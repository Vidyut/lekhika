"""The inspector says what its controls are (docs/UI-NOTES.md §5, §6)."""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QToolBar  # noqa: E402

from lekhak.app import MainWindow  # noqa: E402
from lekhak.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhak")
_application.setApplicationName("Lekhak")
_settings_dir = tempfile.mkdtemp(prefix="lekhak-label-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class InspectorLabelTests(unittest.TestCase):
    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.project = None

    def test_the_control_says_word_count_goal(self):
        labels = [label.text() for label in self.window.findChildren(QLabel)]
        self.assertIn("Word count goal", labels)
        self.assertNotIn("Section target", labels)

    def test_the_zero_state_reads_no_goal(self):
        self.assertEqual(self.window.target.specialValueText(), "No goal")

    def test_it_explains_itself_on_hover(self):
        self.assertTrue(self.window.target.toolTip())

    def test_page_setup_points_at_the_book_folder_on_hover(self):
        # The lever lives on the book folder; the button must say so
        # before anyone hunts for a scene-level setting (UI-NOTES 6).
        tip = self.window.page_setup_button.toolTip()
        self.assertIn("book folder", tip)
        self.assertIn("Clearing", tip)

    def test_the_template_and_cover_rows_name_their_lever(self):
        self.assertIn("template set", self.window.book_template_box.toolTip())
        self.assertIn("book folder", self.window.book_cover_edit.toolTip())

    def test_the_second_tab_is_called_page_preview(self):
        index = self.window.views.indexOf(self.window.page_view)
        self.assertEqual(self.window.views.tabText(index), "Page preview")

    def test_the_toolbar_makes_documents_not_scenes(self):
        # Scene is a role a document can carry (1.17), not the thing the
        # toolbar makes: what it makes is a document.
        toolbar = next(bar for bar in self.window.findChildren(QToolBar)
                       if bar.windowTitle() == "Writing")
        texts = [action.text() for action in toolbar.actions()]
        self.assertIn("New Document", texts)
        self.assertNotIn("New Scene", texts)


class PageFallbackTests(unittest.TestCase):
    def setUp(self):
        QSettings().remove("session/lastProject")
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(
            Path(self.temporary.name) / "Novel", "Novel")
        # A chapter, not the Draft folder: folders hide the text tabs,
        # and this test is about the Page preview tab itself.
        self.chapter = self.project.add_document(
            "One", self.project.documents()[0].id, "chapter")
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.window.deleteLater()
        self.project.close()
        self.temporary.cleanup()

    def test_an_unconfigured_preview_says_it_is_a_fallback(self):
        self.window._select_document(self.chapter.id)
        self.window.views.setCurrentWidget(self.window.page_view)
        self.window._refresh_page_preview()
        message = self.window.statusBar().currentMessage()
        self.assertIn(
            "US Letter (default — set Page setup on the book folder)",
            message)
