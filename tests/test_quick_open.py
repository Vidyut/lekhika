"""BUILD-NOW 2: Quick Open (Ctrl+Shift+P) — a palette that only navigates.

The acceptance clauses from docs/BUILD-NOW.md: typing three letters of a
scene name jumps to it from anywhere, and the palette never appears with
no project open. Also pins the ranking rule (substring before subsequence,
binder order inside each) and the navigate-only contract.
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from lekhika.app import MainWindow, QuickOpenDialog  # noqa: E402
from lekhika.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-test-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class MatchRankingTests(unittest.TestCase):
    """The pure matcher: ranking and order, no widgets."""

    def test_substring_matches_come_first_in_binder_order(self):
        # The fuzzy hit sits first in binder order; the substring must
        # still win, and binder order decides inside each group.
        entries = [
            ("fuzzy", "a-b-c parade"),
            ("scene", "Midnight abc Society"),
            ("also", "abc def"),
            ("third", "xx abc yy"),
        ]

        ranked = QuickOpenDialog._matches(entries, "abc")

        self.assertEqual(
            [doc_id for doc_id, _ in ranked],
            ["scene", "also", "third", "fuzzy"],
        )

    def test_a_subsequence_catch_only_when_nothing_matches_as_substring(self):
        entries = [("s", "Kettle Lane")]
        self.assertEqual(
            QuickOpenDialog._matches(entries, "ktl"), entries)

    def test_empty_text_lists_everything_in_binder_order(self):
        entries = [("b", "Beta"), ("a", "Alpha"), ("c", "Gamma")]
        self.assertEqual(QuickOpenDialog._matches(entries, "  "), entries)


class QuickOpenDialogTests(unittest.TestCase):
    def setUp(self):
        self.entries = [
            ("train", "The Last Train"),
            ("light", "Morning Light"),
            ("note", "a note"),
        ]

    def test_typing_filters_and_enter_accepts_the_highlight(self):
        dialog = QuickOpenDialog(self.entries)
        dialog.edit.setText("train")

        self.assertEqual(dialog.results.count(), 1)
        dialog.edit.returnPressed.emit()

        self.assertEqual(dialog.chosen_id, "train")
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_down_arrow_walks_the_list_from_the_text_field(self):
        dialog = QuickOpenDialog(self.entries)

        QTest.keyClick(dialog.edit, Qt.Key.Key_Down)

        self.assertEqual(dialog.results.currentRow(), 1)
        QTest.keyClick(dialog.edit, Qt.Key.Key_Up)
        self.assertEqual(dialog.results.currentRow(), 0)

    def test_escape_closes_without_choosing(self):
        dialog = QuickOpenDialog(self.entries)

        dialog.reject()

        self.assertIsNone(dialog.chosen_id)
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)


class QuickOpenJumpTests(unittest.TestCase):
    def setUp(self):
        QSettings().remove("session/lastProject")
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")
        self.draft = self.project.documents()[0]
        self.scene = self.project.add_document(
            "Midnight Train", parent_id=self.draft.id, kind="scene"
        )
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _patch_exec(self, replacement):
        # __dict__ is a mappingproxy; keep the original and put it back.
        original = QuickOpenDialog.exec
        QuickOpenDialog.exec = replacement
        self.addCleanup(
            lambda: setattr(QuickOpenDialog, "exec", original))

    def _fake_pick(self, document_id, accepted=True):
        def fake_exec(self):
            self.chosen_id = document_id
            return accepted
        self._patch_exec(fake_exec)

    def test_a_picked_scene_becomes_the_selection_in_the_editor(self):
        self._fake_pick(self.scene.id)

        self.window.quick_open()

        self.assertEqual(self.window.current_id, self.scene.id)
        self.assertIs(self.window.views.currentWidget(),
                      self.window.editor_page)

    def test_a_rejected_palette_changes_nothing(self):
        before = self.window.current_id
        self._fake_pick(self.scene.id, accepted=False)

        self.window.quick_open()

        self.assertEqual(self.window.current_id, before)

    def test_no_project_never_shows_the_palette(self):
        self.window.project = None

        def fail_exec(self):
            raise AssertionError("palette must not appear with no project")
        self._patch_exec(fail_exec)

        self.window.quick_open()

        self.assertIn("Open a project first",
                      self.window.statusBar().currentMessage())

    def test_the_action_is_disabled_until_a_project_is_open(self):
        self.window._set_project_enabled(False)
        self.assertFalse(self.window.quick_open_action.isEnabled())

        self.window._set_project_enabled(True)
        self.assertTrue(self.window.quick_open_action.isEnabled())
        self.assertEqual(
            self.window.quick_open_action.shortcut().toString(),
            "Ctrl+Shift+P",
        )


if __name__ == "__main__":
    unittest.main()
