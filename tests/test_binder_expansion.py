"""The binder rests collapsed (bug report, 2026-10-22).

Opening every folder on load buried the structure under its contents. Only the
path to the current document opens; a sibling stays shut until asked.
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QTreeWidgetItem  # noqa: E402

from lekhika.app import MainWindow  # noqa: E402
from lekhika.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-binder-settings-")
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


class BinderExpansionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel",
                                      "Novel")
        self.root = self.project.documents()[0]
        self.book = self.project.add_document("Book", self.root.id, "folder")
        self.chapter = self.project.add_document("One", self.book.id, "chapter")
        self.scene = self.project.add_document("Scene", self.chapter.id, "scene")
        self.other = self.project.add_document("Other Book", self.root.id,
                                               "folder")
        self.other_scene = self.project.add_document("Loose", self.other.id,
                                                     "scene")
        QSettings().remove("session/lastProject")
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _item(self, document_id):
        return _find_item(self.window.tree, document_id)

    def test_load_leaves_folders_collapsed(self):
        self.window._reload_tree()
        self.assertFalse(self._item(self.book.id).isExpanded())
        self.assertFalse(self._item(self.other.id).isExpanded())

    def test_the_path_to_the_selection_is_open(self):
        self.window.tree.setCurrentItem(self._item(self.scene.id))
        self.assertTrue(self._item(self.book.id).isExpanded())
        self.assertTrue(self._item(self.chapter.id).isExpanded())

    def test_a_sibling_folder_stays_shut(self):
        self.window.tree.setCurrentItem(self._item(self.scene.id))
        self.assertFalse(self._item(self.other.id).isExpanded())

    def test_selecting_in_the_other_book_moves_the_opening(self):
        self.window.tree.setCurrentItem(self._item(self.scene.id))
        self.window.tree.setCurrentItem(self._item(self.other_scene.id))
        self.assertTrue(self._item(self.other.id).isExpanded())


class BinderReorderTests(unittest.TestCase):
    """A drag renumbers the book's children — and the synthetic Templates
    area must not abort that sweep (report, 2026-10-29: Frontmatter dragged
    to the front of its book showed in the tree but not in the database, so
    the order slid back on the next reload)."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel",
                                      "Novel")
        self.book = self.project.add_document("Book", None, "folder")
        self.front = self.project.add_document("Frontmatter", self.book.id,
                                               "folder")
        self.one = self.project.add_document("One", self.book.id, "chapter")
        self.two = self.project.add_document("Two", self.book.id, "chapter")
        QSettings().remove("session/lastProject")
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _book_children_in_tree(self):
        book_item = self._find(self.book.id)
        return [book_item.child(index).text(0)
                for index in range(book_item.childCount())]

    def _find(self, document_id):
        return _find_item(self.window.tree, document_id)

    def test_a_row_without_a_document_id_does_not_break_the_sweep(self):
        # A row of furniture carries no document id, and reaching
        # reorder() with None raised ValueError — killing the sweep
        # before the book's children were renumbered. The guard stays
        # whatever the binder holds.
        furniture = QTreeWidgetItem(["Furniture"])
        furniture.setData(0, Qt.UserRole, None)
        self.window.tree.addTopLevelItem(furniture)

        self.window._tree_moved()                 # must not raise

        for document in self.project.documents():
            self.assertIsNotNone(document.id)

    def test_a_drag_above_the_first_chapter_persists(self):
        book_item = self._find(self.book.id)
        book_item.insertChild(1, book_item.takeChild(0))    # One, Front, Two

        self.window._tree_moved()

        self.assertEqual(self._book_children_in_tree(),
                         ["One", "Frontmatter", "Two"])
        # The database agrees, which is the half that used to be lost.
        positions = {d.title: d.position for d in self.project.documents()
                     if d.parent_id == self.book.id}
        self.assertEqual(positions, {"One": 0, "Frontmatter": 1, "Two": 2})

    def test_the_drag_survives_a_reload(self):
        book_item = self._find(self.book.id)
        book_item.insertChild(0, book_item.takeChild(book_item.childCount() - 1))
        self.window._tree_moved()

        reopened = Project(self.project.root)
        reopened.open()
        reopened_book = next(d for d in reopened.documents()
                             if d.title == "Book")
        titles = [d.title for d in reopened.documents()
                  if d.parent_id == reopened_book.id]
        reopened.close()

        self.assertEqual(titles, self._book_children_in_tree())


if __name__ == "__main__":
    unittest.main()
