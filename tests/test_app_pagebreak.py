"""The Page start row is gone from the inspector — UI-NOTES 9, and for
good in 1.17.

The template's design panel owns page starts now: the design says where
its own pages begin, in the `New Chapter:` / `New Scene:` lines the
print-config vocabulary speaks. What this file guards: the inspector
does not get a second place to say it, the design panel's pick writes
those lines, and a value stored by an older project still loads and
saves without the inspector reading or clobbering it.

Same test contract as the other GUI suites: QSettings redirected into a
temp dir, windows never closed().
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from lekhika.app import MainWindow  # noqa: E402
from lekhika.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-pagebreak-settings-")
# QSettings objects created from now on read/write under _settings_dir.
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class PageStartRowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")
        self.folder = self.project.documents()[0]          # Draft, kind "folder"
        self.scene = self.project.add_document(
            "Scene", parent_id=self.folder.id, kind="scene"
        )
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def test_the_inspector_has_no_page_start_row(self):
        # The page start belongs to the template's design panel (1.17),
        # so the metadata section says nothing about it.
        self.assertFalse(hasattr(self.window, "page_break_box"))
        self.assertIs(self.window.page_start_label.parent(),
                      self.window.template_panel)

    def test_the_design_panel_has_the_page_start_pick(self):
        self.assertTrue(hasattr(self.window, "page_start_box"))
        labels = [label.text() for label in self.window.findChildren(QLabel)]
        self.assertIn("Page start", labels)

    def test_a_stored_page_break_survives_a_save(self):
        # An older project's own value is still the document's, and the
        # inspector no longer reads or writes it.
        self.project.update_document(self.scene.id, page_break="right")
        self.window._select_document(self.scene.id)
        self.window.save_current()

        found = next(d for d in self.project.documents() if d.id == self.scene.id)
        self.assertEqual(found.page_break, "right")

    def test_the_pick_writes_the_designs_own_lines(self):
        self.window._select_document(self.scene.id)
        self.window.template_config_box.setPlainText("# a design\n")
        self.window.page_start_box.setCurrentIndex(
            self.window.page_start_box.findData("Recto"))

        head = self.window.template_config_box.toPlainText()
        self.assertIn("New Chapter: Recto", head)
        self.assertIn("New Scene: Recto", head)

    def test_automatic_takes_the_lines_away(self):
        self.window._select_document(self.scene.id)
        self.window.template_config_box.setPlainText(
            "# a design\nNew Chapter: Recto\n")
        self.window.page_start_box.setCurrentIndex(
            self.window.page_start_box.findData(""))

        head = self.window.template_config_box.toPlainText()
        self.assertNotIn("New Chapter", head)


if __name__ == "__main__":
    unittest.main()
