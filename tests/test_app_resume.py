"""1.9 session resume: last document + caret come back on project open.

These are the only tests that build real Qt widgets. They run offscreen;
QSettings is redirected into a temp dir so the suite never touches the
user's real Lekhika configuration. Windows are never closed() — closeEvent
would save into a possibly-closed project and pop a modal dialog, which
would hang an offscreen run.
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lekhika.app import MainWindow  # noqa: E402
from lekhika.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-test-settings-")
# QSettings objects created from now on read/write under _settings_dir.
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


def _find_item(tree, document_id):
    def walk(item):
        if item.data(0, Qt.UserRole) == document_id:
            return item
        for index in range(item.childCount()):
            found = walk(item.child(index))
            if found is not None:
                return found
        return None

    for index in range(tree.topLevelItemCount()):
        found = walk(tree.topLevelItem(index))
        if found is not None:
            return found
    return None


class SessionResumeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")
        self.draft = self.project.documents()[0]
        self.chapter = self.project.add_document(
            "Chapter One", parent_id=self.draft.id, kind="chapter"
        )
        self.project.write_content(
            self.chapter.id, "<p>Hello world, this is a test.</p>"
        )
        QSettings().remove("session/lastProject")
        # The guide is copied into the data folder; keep that inside the
        # test's own temporary directory.
        self.previous_data = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = str(Path(self.temporary.name) / "data")
        self.window = MainWindow()

    def tearDown(self):
        # Never close() the window: closeEvent -> save_current on a closed
        # project raises into a modal QMessageBox and hangs offscreen.
        self.window.project = None
        self.project.close()
        if self.previous_data is None:
            os.environ.pop("XDG_DATA_HOME", None)
        else:
            os.environ["XDG_DATA_HOME"] = self.previous_data
        self.temporary.cleanup()

    def _select_chapter(self):
        item = _find_item(self.window.tree, self.chapter.id)
        self.assertIsNotNone(item, "chapter missing from binder tree")
        self.window.tree.setCurrentItem(item)

    def _set_cursor(self, position):
        cursor = self.window.editor.textCursor()
        cursor.setPosition(position)
        self.window.editor.setTextCursor(cursor)

    def test_save_writes_document_and_cursor(self):
        self.window._switch_project(self.project)
        self._select_chapter()
        self._set_cursor(5)

        self.window.save_current()

        self.assertEqual(self.project.get_setting("last_document"), self.chapter.id)
        self.assertEqual(self.project.get_setting("last_cursor"), "5")

    def test_switch_restores_document_and_cursor(self):
        self.project.set_setting("last_document", self.chapter.id)
        self.project.set_setting("last_cursor", "5")

        self.window._switch_project(self.project)

        self.assertEqual(self.window.current_id, self.chapter.id)
        self.assertEqual(self.window.editor.textCursor().position(), 5)

    def test_cursor_clamped_to_text_end(self):
        self.project.set_setting("last_document", self.chapter.id)
        self.project.set_setting("last_cursor", "999999")

        self.window._switch_project(self.project)

        self.assertEqual(self.window.current_id, self.chapter.id)
        self.assertEqual(
            self.window.editor.textCursor().position(),
            self.window.editor.document().characterCount() - 1,
        )

    def test_junk_cursor_value_leaves_caret_at_start(self):
        # Transparent format: settings may be hand-edited. A value that
        # isn't a number must not raise, just leave the caret alone.
        self.project.set_setting("last_document", self.chapter.id)
        self.project.set_setting("last_cursor", "banana")

        self.window._switch_project(self.project)

        self.assertEqual(self.window.current_id, self.chapter.id)
        self.assertEqual(self.window.editor.textCursor().position(), 0)

    def test_deleted_last_document_falls_back_to_first_item(self):
        self.project.set_setting("last_document", "deadbeef")
        self.project.set_setting("last_cursor", "5")

        self.window._switch_project(self.project)

        # Fallback selects the first binder item (the Draft folder) and
        # the stale cursor is not applied to a document it never described.
        self.assertEqual(self.window.current_id, self.draft.id)
        self.assertEqual(self.window.editor.textCursor().position(), 0)

    def test_switch_project_records_last_project_path(self):
        self.window._switch_project(self.project)

        self.assertEqual(
            QSettings().value("session/lastProject", "", type=str),
            str(self.project.root),
        )

    def test_reopen_last_project_restores_session(self):
        self.project.set_setting("last_document", self.chapter.id)
        self.project.set_setting("last_cursor", "5")
        QSettings().setValue("session/lastProject", str(self.project.root))

        window = MainWindow()
        window.reopen_last_project()

        self.assertIsNotNone(window.project)
        self.assertEqual(window.project.root, self.project.root)
        self.assertEqual(window.current_id, self.chapter.id)
        self.assertEqual(window.editor.textCursor().position(), 5)
        window.project = None  # leave it open; the setUp project owns cleanup

    def test_reopen_ignores_missing_project(self):
        # A project that has been deleted: the guide opens instead, so a
        # fresh install shows the software rather than an empty window.
        QSettings().setValue(
            "session/lastProject", str(Path(self.temporary.name) / "gone.lekhika")
        )

        self.window.reopen_last_project()

        self.assertIsNotNone(self.window.project)
        self.assertEqual(self.window.project.root.name, "guide.lekhika")

    def test_reopen_falls_back_when_project_wont_open(self):
        # Path exists but isn't a Lekhika project (no database): startup
        # must land on the empty window, not a dialog or a crash.
        impostor = Path(self.temporary.name) / "Impostor.lekhika"
        impostor.mkdir()
        QSettings().setValue("session/lastProject", str(impostor))

        self.window.reopen_last_project()

        self.assertIsNone(self.window.project)
        self.assertIsNone(self.window.current_id)

    def test_reopen_without_recorded_project_opens_the_guide(self):
        # Nothing was open last time: the guide is what a fresh install
        # shows, so the software can be looked at before it is used.
        self.window.reopen_last_project()

        self.assertIsNotNone(self.window.project)
        self.assertEqual(self.window.project.root.name, "guide.lekhika")
        # Read-only: the manual is the software's, not the writer's.
        self.assertTrue(self.window._project_is_read_only())


if __name__ == "__main__":
    unittest.main()
