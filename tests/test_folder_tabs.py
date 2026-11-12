"""Editor and Page are not offered for a folder (docs/UI-NOTES.md 3)."""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lekhak.app import MainWindow  # noqa: E402
from lekhak.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhak")
_application.setApplicationName("Lekhak")
_settings_dir = tempfile.mkdtemp(prefix="lekhak-tabs-settings-")
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


class FolderTabTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel",
                                      "Novel")
        self.root = self.project.documents()[0]
        self.book = self.project.add_document("Book", self.root.id, "folder")
        self.scene = self.project.add_document("Scene", self.book.id, "scene")
        self.project.write_content(self.scene.id, "<p>Text.</p>")
        QSettings().remove("session/lastProject")
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _select(self, document_id):
        self.window.tree.setCurrentItem(_find_item(self.window.tree, document_id))

    def _text_tabs(self):
        return (self.window.views.isTabVisible(0), self.window.views.isTabVisible(1))

    def _folder_tabs(self):
        return (self.window.views.isTabVisible(2), self.window.views.isTabVisible(3))

    def test_a_folder_offers_no_text_tabs(self):
        self._select(self.book.id)
        self.assertEqual(self._text_tabs(), (False, False))
        self.assertTrue(all(self._folder_tabs()))

    def test_a_document_offers_them_again(self):
        self._select(self.book.id)
        self._select(self.scene.id)
        self.assertEqual(self._text_tabs(), (True, True))

    def test_selecting_a_folder_leaves_a_folder_view_showing(self):
        self._select(self.scene.id)
        self._select(self.book.id)
        self.assertIn(self.window.views.currentWidget(),
                      (self.window.corkboard, getattr(self.window, 'outliner_page', self.window.outliner)))


if __name__ == "__main__":
    unittest.main()
