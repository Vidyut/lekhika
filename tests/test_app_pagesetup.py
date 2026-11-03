"""1.15 print geometry in the window: the inspector's Page setup
button and its dialog — offscreen.

Same contract as the other GUI suites: QSettings redirected into a temp
dir, windows never closed().
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lekhak.app import MainWindow, PageSetupDialog  # noqa: E402
from lekhak.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhak")
_application.setApplicationName("Lekhak")
_settings_dir = tempfile.mkdtemp(prefix="lekhak-pagesetup-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


def _geometry(**overrides):
    geometry = {
        "trim": "6x9",
        "width": 6.0, "height": 9.0,
        "top": 0.75, "bottom": 0.75,
        "inside": 0.5, "outside": 0.25,
        "mirror": True,
        "gutter": 0.25,
    }
    geometry.update(overrides)
    return geometry


class PageSetupDialogTests(unittest.TestCase):
    def test_prefill_reads_the_saved_geometry(self):
        dialog = PageSetupDialog(_geometry())
        self.assertEqual(dialog.trim_box.currentData(), "6x9")
        self.assertEqual(dialog.width_spin.value(), 6.0)
        self.assertTrue(dialog.mirror_box.isChecked())
        self.assertEqual(dialog.geometry(), _geometry())

    def test_blank_geometry_fills_the_a4_default(self):
        """Phase 1: an unconfigured export is A4, not python-docx's Letter."""
        dialog = PageSetupDialog(None)
        self.assertEqual(dialog.trim_box.currentData(), "a4")
        self.assertEqual(dialog.width_spin.value(), 8.27)
        self.assertFalse(dialog.mirror_box.isChecked())

    def test_picking_a_trim_fills_the_inches(self):
        dialog = PageSetupDialog(None)
        dialog.trim_box.setCurrentIndex(dialog.trim_box.findData("a5"))
        self.assertEqual(dialog.width_spin.value(), 5.83)
        self.assertEqual(dialog.height_spin.value(), 8.27)
        self.assertEqual(dialog.geometry()["trim"], "a5")

    def test_editing_the_inches_switches_the_trim_to_custom(self):
        dialog = PageSetupDialog(None)
        dialog.width_spin.setValue(7.0)
        self.assertEqual(dialog.trim_box.currentData(), "custom")
        # ... and staying on a preset keeps its id.
        dialog.width_spin.setValue(8.27)
        self.assertEqual(dialog.trim_box.currentData(), "a4")

    def test_margins_without_a_text_area_are_refused(self):
        dialog = PageSetupDialog(_geometry())
        dialog.inside_spin.setValue(4.0)
        dialog.outside_spin.setValue(4.0)   # + 0.25 gutter > 6 in width
        dialog.accept()
        self.assertNotEqual(dialog.result(), 1)
        self.assertTrue(dialog.hint.text())
        self.assertFalse(dialog.cleared)

        dialog.inside_spin.setValue(0.5)
        dialog.outside_spin.setValue(0.25)
        dialog.accept()
        self.assertEqual(dialog.result(), 1)

    def test_use_default_marks_the_setup_cleared(self):
        dialog = PageSetupDialog(_geometry())
        dialog._use_default()
        self.assertTrue(dialog.cleared)
        self.assertEqual(dialog.result(), 1)


class PageSetupInspectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")
        self.folder = self.project.documents()[0]
        self.window = MainWindow()
        self.window._switch_project(self.project)
        # The tree is built once at switch, so every document the tests
        # select must already exist in it.
        self.chapter = self.project.add_document(
            "Chapter", self.folder.id, "chapter")
        self.window._reload_tree(select_id=self.folder.id)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _reselect_folder(self):
        """_select_document only fires when the current item really
        changes, so bounce off the chapter to force a folder refresh."""
        self.window._select_document(self.chapter.id)
        self.window._select_document(self.folder.id)

    def test_button_shows_the_default_before_any_setup(self):
        self.window._select_document(self.folder.id)
        self.assertEqual(self.window.page_setup_button.text(),
                         "Page setup — Default (A4)")

    def test_button_summarises_saved_geometry_after_selection(self):
        self.project.set_page_setup(self.folder.id, _geometry())
        self._reselect_folder()
        text = self.window.page_setup_button.text()
        self.assertIn("6 × 9", text)
        self.assertIn("mirrored", text)
        self.assertIn("gutter 0.25", text)

    def test_button_is_shown_only_for_folders(self):
        self.window._select_document(self.folder.id)
        self.assertFalse(self.window.page_setup_button.isHidden())

        self.window._select_document(self.chapter.id)
        self.assertTrue(self.window.page_setup_button.isHidden())

    def test_saving_through_the_project_updates_the_button(self):
        self.window._select_document(self.folder.id)
        self.project.set_page_setup(self.folder.id, _geometry())
        self._reselect_folder()
        self.assertIn("6 × 9", self.window.page_setup_button.text())

        self.project.set_page_setup(self.folder.id, None)
        self._reselect_folder()
        self.assertEqual(self.window.page_setup_button.text(),
                         "Page setup — Default (A4)")


if __name__ == "__main__":
    unittest.main()
