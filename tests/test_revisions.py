"""2.4 tracked changes: the storage half (pending rows, sentinel HTML,
reconcile) and the editor half (typing bursts, erasing, accept/reject,
paste, reload re-attach, the AI replacement path), plus the window's
Track Changes wiring.

Offscreen, same contract as the other app suites: QSettings redirected
into a temp dir, windows never close()ed.
"""

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from lekhika import revisions  # noqa: E402
from lekhika.app import LinkEditor, MainWindow  # noqa: E402
from lekhika.project import (  # noqa: E402
    DELETE_SPAN_RE, INSERT_SPAN_RE, Project,
)

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-revisions-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)

# What Qt actually round-trips for the sentinel marks: a span with the
# sentinel background, plus the underline/line-through decoration. The
# live id properties never reach the string — that is the whole design.
DELETE_HTML = ('<p>Hello <span style=" background-color:#ffd6d6;'
               ' text-decoration:line-through;">old</span> world</p>')
INSERT_HTML = ('<p>Hello <span style=" background-color:#cfe4ff;'
               ' text-decoration:underline;">new</span> world</p>')
BOTH_HTML = ('<p>Hello <span style=" background-color:#cfe4ff;'
             ' text-decoration:underline;">new</span> '
             '<span style=" background-color:#ffd6d6;'
             ' text-decoration:line-through;">old</span> world</p>')


def _press(editor, key, text=""):
    from PySide6.QtGui import QKeyEvent
    editor.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier, text))


def _press_text(editor, text):
    for character in text:
        _press(editor, Qt.Key_A, character)


def _caret(editor, position=None, end=False):
    cursor = QTextCursor(editor.document())
    if end:
        cursor.movePosition(QTextCursor.End)
    else:
        cursor.setPosition(position)
    editor.setTextCursor(cursor)


@contextmanager
def _fail_writes_after(count):
    """Let ``count`` content writes through, then fail every one after.

    An "always fail" patch never reaches the operation under test:
    accept/reject saves once before it resolves anything, and that first
    failure stops the whole path. The marks have to reach the disk
    before the failing write matters.
    """
    original = Project.write_content
    seen = {"count": 0}

    def flaky(self, document_id, html):
        seen["count"] += 1
        if seen["count"] > count:
            raise OSError("disk full")
        return original(self, document_id, html)

    with patch.object(Project, "write_content", flaky):
        yield


class RevisionStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(
            Path(self.temporary.name) / "Novel", "Novel"
        )
        self.draft = self.project.documents()[0]

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def test_rows_list_per_document_in_sequence(self):
        first = self.project.add_revision(self.draft.id, "s1", "insert",
                                          "Author", 1)
        second = self.project.add_revision(self.draft.id, "s1", "delete",
                                           "Author", 2)
        rows = self.project.revisions(self.draft.id)
        self.assertEqual([row.id for row in rows], [first.id, second.id])
        self.assertEqual([row.kind for row in rows], ["insert", "delete"])

    def test_sessions_group_into_passes(self):
        self.project.add_revision(self.draft.id, "s1", "insert", "Vidyut", 1)
        self.project.add_revision(self.draft.id, "s1", "delete", "Vidyut", 2)
        self.project.add_revision(self.draft.id, "s2", "insert", "GPT", 3)
        sessions = self.project.revision_sessions(self.draft.id)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0]["session_id"], "s1")
        self.assertEqual(sessions[0]["count"], 2)
        self.assertEqual(sessions[0]["author"], "Vidyut")
        self.assertEqual(sessions[1]["session_id"], "s2")
        self.assertEqual(sessions[1]["count"], 1)

    def test_accepted_html_keeps_insert_words_and_drops_delete_words(self):
        accepted = Project.accepted_html(BOTH_HTML)
        self.assertNotIn("old", accepted)
        self.assertIn("new", accepted)
        self.assertEqual(INSERT_SPAN_RE.findall(accepted), [])
        self.assertEqual(DELETE_SPAN_RE.findall(accepted), [])

    def test_plain_text_unwraps_wiki_links_and_marks(self):
        # Insert words count, delete words do not, wiki markers unwrap.
        self.assertEqual(Project.plain_text(BOTH_HTML),
                         "Hello new  world")
        self.assertEqual(
            Project.plain_text('<p>[[Chapter One]] '
                               '<span style="background-color:#cfe4ff;">'
                               "said</span></p>"),
            "Chapter One said",
        )

    def test_plain_text_drops_the_qt_css_head(self):
        # Regression: QTextEdit.toHtml() prepends a CSS block whose words
        # must never reach word counts, search or the text export.
        qt_html = (
            '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN">'
            '<html><head><meta name="qrichtext" content="1" />'
            '<style type="text/css">\np, li { white-space: pre-wrap; }\n'
            "hr { height: 1px; border-width: 0; }\n</style></head>"
            "<body style=\" font-family:'Ubuntu Sans';\">"
            "<p>Hello world</p></body></html>"
        )
        self.assertEqual(Project.plain_text(qt_html), "Hello world")

    def test_reconcile_prunes_rows_whose_spans_are_gone(self):
        inserts = [self.project.add_revision(self.draft.id, "s1", "insert",
                                             "Author", sequence)
                   for sequence in (1, 2, 3)]
        delete = self.project.add_revision(self.draft.id, "s1", "delete",
                                           "Author", 4)
        self.project.write_content(self.draft.id, BOTH_HTML)

        self.project.reconcile_revisions(self.draft.id)

        remaining = self.project.revisions(self.draft.id)
        # First insert row (sequence order) and the delete row survive;
        # rows two and three had no spans left to back them.
        self.assertEqual([row.id for row in remaining],
                         [inserts[0].id, delete.id])

    def test_reconcile_never_invents_rows_for_extra_spans(self):
        self.project.write_content(self.draft.id, BOTH_HTML)

        self.project.reconcile_revisions(self.draft.id)

        self.assertEqual(self.project.revisions(self.draft.id), [])

    def test_restore_revisions_puts_the_same_rows_back(self):
        """A failed accept/reject write depends on the *ids* surviving:
        the live document still carries them in its marks, and a fresh
        id would make the next accept look for a mark that cannot exist."""
        rows = [self.project.add_revision(self.draft.id, "s1", "insert",
                                          "Author", sequence)
                for sequence in (1, 2)]

        self.project.remove_revisions([row.id for row in rows])
        self.assertEqual(self.project.revisions(self.draft.id), [])

        self.project.restore_revisions(rows)

        restored = self.project.revisions(self.draft.id)
        self.assertEqual([row.id for row in restored],
                         [row.id for row in rows])
        self.assertEqual([row.sequence for row in restored], [1, 2])
        self.assertEqual([row.kind for row in restored], ["insert", "insert"])


class RevisionEditorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(
            Path(self.temporary.name) / "Novel", "Novel"
        )
        self.draft = self.project.documents()[0]
        self.editor = LinkEditor()
        self.editor.set_tracking(True)
        self.editor.begin_document(self.project, self.draft.id)
        self.editor.setPlainText("Hello world")
        _caret(self.editor, end=True)

    def tearDown(self):
        self.editor.set_tracking(False)
        self.editor.begin_document(None, None)
        self.project.close()
        self.temporary.cleanup()

    def _rows(self):
        return self.project.revisions(self.draft.id)

    def _text(self):
        return self.editor.toPlainText()

    def _accepted(self):
        # Qt's toHtml() prepends a CSS head; the prose starts after it.
        return Project.plain_text(
            self.editor.toHtml().split("</style>")[-1])

    # --- typing -------------------------------------------------------------

    def test_typing_forms_one_insert_change_per_burst(self):
        _press_text(self.editor, "abc")

        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].kind, "insert")
        self.assertEqual(self._text(), "Hello worldabc")
        self.assertIn("abc", self._accepted())

    def test_moving_the_caret_ends_the_burst(self):
        _press_text(self.editor, "a")
        _caret(self.editor, 0)
        _press_text(self.editor, "b")

        self.assertEqual(len(self._rows()), 2)
        self.assertEqual(self._text(), "bHello worlda")

    def test_tracking_off_is_plain_typing(self):
        self.editor.set_tracking(False)

        _press_text(self.editor, "x")

        self.assertEqual(self._rows(), [])
        self.assertEqual(self._text(), "Hello worldx")

    def test_paste_is_one_insert_change(self):
        self.editor.insertPlainText(" pasted words")

        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].kind, "insert")
        self.assertTrue(self._text().endswith(" pasted words"))

    def test_paste_replaces_the_selection_tracked(self):
        cursor = self.editor.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(5, QTextCursor.KeepAnchor)  # "Hello"
        self.editor.setTextCursor(cursor)

        self.editor.insertPlainText("Hi")

        rows = self._rows()
        self.assertEqual([row.kind for row in rows], ["delete", "insert"])
        self.assertTrue(self._text().startswith("Hi"))

    # --- erasing ------------------------------------------------------------

    def test_backspace_marks_a_char_instead_of_removing_it(self):
        _press(self.editor, Qt.Key_Backspace)

        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].kind, "delete")
        # The word stays on screen, struck; only the accepted form drops it.
        self.assertEqual(self._text(), "Hello world")
        self.assertEqual(self._accepted(), "Hello worl")

    def test_erasing_an_inserted_char_removes_it_and_its_row(self):
        _press_text(self.editor, "x")
        self.assertEqual(len(self._rows()), 1)

        _press(self.editor, Qt.Key_Backspace)

        self.assertEqual(self._rows(), [])
        self.assertEqual(self._text(), "Hello world")

    def test_backspace_at_a_paragraph_start_falls_through(self):
        self.editor.setPlainText("ab\ncd")
        # Position 3 = start of the "cd" block (the separator sits at 2).
        _caret(self.editor, 3)

        _press(self.editor, Qt.Key_Backspace)

        # Qt owns the block join; the invisible separator gets no mark.
        self.assertEqual(self._rows(), [])

    def test_typing_over_a_selection_marks_and_replaces(self):
        cursor = self.editor.textCursor()
        cursor.setPosition(6)
        cursor.setPosition(11, QTextCursor.KeepAnchor)  # "world"
        self.editor.setTextCursor(cursor)

        _press_text(self.editor, "there")

        rows = self._rows()
        self.assertEqual([row.kind for row in rows], ["delete", "insert"])
        self.assertEqual(self._text(), "Hello thereworld")

    # --- accept / reject ----------------------------------------------------

    def _build_replace(self):
        """Select " world", type " there" over it: one delete row (the
        old words, struck) plus one insert row (the new words)."""
        cursor = self.editor.textCursor()
        cursor.setPosition(5)
        cursor.setPosition(11, QTextCursor.KeepAnchor)  # " world"
        self.editor.setTextCursor(cursor)
        _press_text(self.editor, " there")
        self.assertEqual(self._text(), "Hello there world")

    def test_accept_keeps_insert_words_and_drops_delete_words(self):
        self._build_replace()
        change_ids = {row.id for row in self._rows()}

        revisions.apply_accept(self.editor.document(), change_ids)
        self.project.remove_revisions(list(change_ids))

        self.assertEqual(self._text(), "Hello there")
        self.assertEqual(self._rows(), [])
        html = self.editor.toHtml()
        self.assertEqual(INSERT_SPAN_RE.findall(html), [])
        self.assertEqual(DELETE_SPAN_RE.findall(html), [])

    def test_reject_removes_insert_words_and_restores_delete_words(self):
        self._build_replace()
        change_ids = {row.id for row in self._rows()}

        revisions.apply_reject(self.editor.document(), change_ids)
        self.project.remove_revisions(list(change_ids))

        self.assertEqual(self._text(), "Hello world")
        self.assertEqual(self._rows(), [])

    # --- the AI replacement path -------------------------------------------

    def test_apply_tracked_replacement_works_without_the_toggle(self):
        self.editor.set_tracking(False)  # AI never needs the toggle on

        self.editor.apply_tracked_replacement(6, 11, "there", "sess-ai",
                                              "GPT")

        rows = self._rows()
        self.assertEqual([row.kind for row in rows], ["delete", "insert"])
        for row in rows:
            self.assertEqual(row.session_id, "sess-ai")
            self.assertEqual(row.author, "GPT")
        # Old words stay struck behind the new ones.
        self.assertEqual(self._text(), "Hello thereworld")

        change_ids = {row.id for row in rows}
        revisions.apply_accept(self.editor.document(), change_ids)
        self.assertEqual(self._text(), "Hello there")

    # --- reload -------------------------------------------------------------

    def test_reload_reattaches_ids_in_sequence_order(self):
        _press_text(self.editor, "a")
        _caret(self.editor, 0)
        _press_text(self.editor, "b")
        rows = self._rows()
        self.assertEqual(len(rows), 2)
        html = self.editor.toHtml()
        self.project.write_content(self.draft.id, html)

        reloaded = LinkEditor()
        reloaded.setHtml(html)
        reloaded.begin_document(self.project, self.draft.id)

        # The stored HTML carries spans but no ids; attach matches rows to
        # spans by kind in sequence order — document order here is b then
        # a, sequence order a then b, and the test pins that convention.
        attached = [run[1] for run in revisions.runs(reloaded.document())]
        self.assertEqual(attached, [rows[0].id, rows[1].id])
        reloaded.begin_document(None, None)


class RevisionWindowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(
            Path(self.temporary.name) / "Novel", "Novel"
        )
        draft = self.project.documents()[0]
        chapter = self.project.add_document("Chapter One", draft.id, "chapter")
        self.scene = self.project.add_document("First Light", chapter.id,
                                               "scene")
        self.project.write_content(self.scene.id, "<p>First light prose.</p>")
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        # Never close() the window (closeEvent would save into a closed
        # project offscreen) — same contract as the other app suites.
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _open_scene_tracked(self):
        self.window._select_document(self.scene.id)
        self.window.track_action.setChecked(True)
        _caret(self.window.editor, end=True)

    def test_track_changes_action_toggles_the_editor(self):
        self.assertTrue(self.window.track_action.isCheckable())

        self.window.track_action.setChecked(True)
        self.assertTrue(self.window.editor.tracking)
        self.window.track_action.setChecked(False)
        self.assertFalse(self.window.editor.tracking)

    def test_accept_all_resolves_rows_and_saves_the_clean_form(self):
        self._open_scene_tracked()
        _press_text(self.window.editor, " extra")
        self.assertEqual(len(self.project.revisions(self.scene.id)), 1)

        self.window.accept_all_changes()

        self.assertEqual(self.project.revisions(self.scene.id), [])
        self.assertTrue(self.window.editor.toPlainText().endswith(" extra"))
        stored = self.project.read_content(self.scene.id)
        self.assertEqual(INSERT_SPAN_RE.findall(stored), [])
        self.assertEqual(DELETE_SPAN_RE.findall(stored), [])

    def test_reject_all_restores_the_original_prose(self):
        self._open_scene_tracked()
        _press_text(self.window.editor, " extra")

        self.window.reject_all_changes()

        self.assertEqual(self.project.revisions(self.scene.id), [])
        self.assertEqual(self.window.editor.toPlainText(),
                         "First light prose.")

    def test_a_failed_save_keeps_marks_and_rows_together(self):
        """A failed write after an accept puts the rows back.

        The marks on disk are the one thing a later save cannot
        reconstruct, so the rows that explain them must survive the
        failure — under their own ids, because the live document still
        carries those ids in whatever marks remain. The first save lands
        (it puts the marks on disk); only the resolve's own write fails.
        """
        self._open_scene_tracked()
        _press_text(self.window.editor, " extra")
        self.assertTrue(self.window.save_current())   # marks reach the disk
        stored = self.project.read_content(self.scene.id)
        self.assertEqual(len(INSERT_SPAN_RE.findall(stored)), 1)

        with patch("lekhika.app.QMessageBox.critical"), \
                _fail_writes_after(1):
            self.window.accept_all_changes()

        rows = self.project.revisions(self.scene.id)
        self.assertEqual(len(rows), 1)
        stored = self.project.read_content(self.scene.id)
        self.assertEqual(len(INSERT_SPAN_RE.findall(stored)), 1)

    def test_a_failed_save_does_not_orphan_a_revision_pass(self):
        """Same contract through the passes dialog."""
        self._open_scene_tracked()
        _press_text(self.window.editor, "a")
        self.assertTrue(self.window.save_current())

        class _StubDialog:
            chosen_accept = True

            def __init__(self, sessions, parent=None):
                self.sessions = sessions

            def exec(self):
                return QDialog.Accepted

            def selected_session(self):
                return self.sessions[0]["session_id"]

        with patch("lekhika.app.RevisionPassesDialog", _StubDialog), \
                patch("lekhika.app.QMessageBox.critical"), \
                _fail_writes_after(1):
            self.window.revision_passes()

        self.assertEqual(len(self.project.revisions(self.scene.id)), 1)
        stored = self.project.read_content(self.scene.id)
        self.assertEqual(len(INSERT_SPAN_RE.findall(stored)), 1)

    def test_revision_passes_dialog_resolves_one_pass(self):
        self._open_scene_tracked()
        _press_text(self.window.editor, "a")
        # A second toggle = a second pass, its own session.
        self.window.track_action.setChecked(False)
        self.window.track_action.setChecked(True)
        _caret(self.window.editor, end=True)
        _press_text(self.window.editor, "b")
        rows = self.project.revisions(self.scene.id)
        self.assertEqual(len(rows), 2)
        first_session = rows[0].session_id
        second_session = rows[1].session_id
        self.assertNotEqual(first_session, second_session)

        class _StubDialog:
            chosen_accept = True

            def __init__(self, sessions, parent=None):
                self.sessions = sessions

            def exec(self):
                return QDialog.Accepted

            def selected_session(self):
                return self.sessions[0]["session_id"]

        with patch("lekhika.app.RevisionPassesDialog", _StubDialog):
            self.window.revision_passes()

        remaining = self.project.revisions(self.scene.id)
        self.assertEqual([row.session_id for row in remaining],
                         [second_session])
        self.assertNotIn(first_session,
                         [row.session_id for row in remaining])


if __name__ == "__main__":
    unittest.main()
