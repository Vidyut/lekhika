"""A failed save must never cost the writer their text.

save_current() returns False when the write fails, and every path that
would destroy or outlive the in-memory copy stops there: the document
switch that reloads from disk, the project switch that closes the old
project, the close that discards the editor, the backup that would
announce success. Each test forces the write to fail and asserts the
caller did nothing.

QMessageBox is patched throughout: the failure dialogs are modal, and a
modal dialog in an offscreen run hangs the suite.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from lekhika.app import MainWindow  # noqa: E402
from lekhika.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-save-settings-")
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


class SaveFailureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")
        self.draft = self.project.documents()[0]
        self.scene = self.project.add_document("Scene", self.draft.id, "scene")
        self.other = self.project.add_document("Other", self.draft.id, "scene")
        self.project.write_content(self.scene.id, "<p>Seed text.</p>")
        self.project.write_content(self.other.id, "<p>Other text.</p>")
        QSettings().remove("session/lastProject")
        self.window = MainWindow()
        self.window._switch_project(self.project)
        self.window.tree.setCurrentItem(_find_item(self.window.tree, self.scene.id))

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _failing_write(self):
        return patch.object(type(self.project), "write_content",
                            side_effect=OSError("disk full"))

    # -- the contract -----------------------------------------------------

    def test_save_current_reports_failure(self):
        with patch("lekhika.app.QMessageBox.critical"), self._failing_write():
            self.assertFalse(self.window.save_current())

    def test_save_current_reports_success_normally(self):
        self.assertTrue(self.window.save_current())

    def test_nothing_to_save_is_success(self):
        self.window.current_id = None
        self.assertTrue(self.window.save_current())

    # -- the destructive paths --------------------------------------------

    def test_a_failed_save_cancels_a_document_switch(self):
        self.window.editor.setPlainText("unsaved words")
        original = self.window.tree.currentItem()

        with patch("lekhika.app.QMessageBox.critical"), self._failing_write():
            self.window.tree.setCurrentItem(
                _find_item(self.window.tree, self.other.id))

        self.assertEqual(self.window.current_id, self.scene.id)
        self.assertIs(self.window.tree.currentItem(), original)
        # the only copy of the new text is still on screen
        self.assertIn("unsaved words", self.window.editor.toPlainText())

    def test_a_failed_save_cancels_a_project_switch(self):
        elsewhere = Project.create(Path(self.temporary.name) / "Second", "Second")
        try:
            with patch("lekhika.app.QMessageBox.critical"), self._failing_write():
                self.window._switch_project(elsewhere)
            self.assertIs(self.window.project, self.project)
            # the old project stays open rather than being closed under it
            self.assertIsNotNone(self.project.connection)
        finally:
            elsewhere.close()

    def test_a_failed_save_cancels_the_backup(self):
        with patch("lekhika.app.QMessageBox.critical"), \
                patch.object(type(self.project), "backup") as backup, \
                self._failing_write():
            self.window.manual_save()

        backup.assert_not_called()

    def test_close_is_refused_when_the_save_fails(self):
        event = QCloseEvent()
        with patch("lekhika.app.QMessageBox.critical"), \
                patch("lekhika.app.QMessageBox.warning",
                      return_value=QMessageBox.No), self._failing_write():
            self.window.closeEvent(event)

        self.assertFalse(event.isAccepted())
        self.assertIsNotNone(self.project.connection)

    def test_close_proceeds_when_the_writer_insists(self):
        event = QCloseEvent()
        with patch("lekhika.app.QMessageBox.critical"), \
                patch("lekhika.app.QMessageBox.warning",
                      return_value=QMessageBox.Yes), self._failing_write():
            self.window.closeEvent(event)

        self.assertTrue(event.isAccepted())

    def test_export_cancels_when_the_save_fails(self):
        with patch("lekhika.app.QMessageBox.critical"), \
                patch("lekhika.app.QFileDialog.getSaveFileName") as chooser, \
                self._failing_write():
            self.window.export("text")

        chooser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
