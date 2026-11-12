"""The binder is the folder tree on disk (docs/STORAGE.md).

The files are the truth and the database is the index over them, so the
binder is drawn from the files: a folder the writer made by hand shows
up, a container's own index is its prose rather than a second document,
and the icon says what each thing is rather than what role its row
gives it.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyle  # noqa: E402

from lekhika.app import FOLDER_ROLE, MainWindow  # noqa: E402
from lekhika.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-tree-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class BinderTreeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(
            Path(self.temporary.name) / "Novel.lekhika", "Novel")
        self.root = self.project.documents()[0]
        self.chapter = self.project.add_document("A Chapter", self.root.id,
                                                  "chapter")
        self.scene = self.project.add_document("A Scene", self.chapter.id,
                                                "scene")
        self.project.write_content(self.scene.id, "<p>The prose.</p>")
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    # --- helpers -----------------------------------------------------------

    def _items(self):
        return self.window._all_items()

    def _find(self, document_id):
        for item in self._items():
            if item.data(0, Qt.UserRole) == document_id:
                return item
        return None

    def _pixels(self, icon):
        return bytes(icon.pixmap(16, 16).toImage().constBits())

    def _folder_pixels(self):
        return self._pixels(self.window.style().standardIcon(
            QStyle.SP_DirIcon))

    def _file_pixels(self):
        return self._pixels(self.window.style().standardIcon(
            QStyle.SP_FileIcon))

    # --- the tree is the files ---------------------------------------------

    def test_a_folder_the_writer_added_by_hand_shows_in_the_binder(self):
        (self.project.root / "A New Folder").mkdir()
        (self.project.root / "A New Folder" / "A New Scene.html").write_text(
            "<p>New words.</p>", encoding="utf-8")

        self.window._reload_tree()

        folder = next((item for item in self._items()
                       if item.text(0) == "A New Folder"), None)
        self.assertIsNotNone(folder)
        self.assertIsNotNone(folder.data(0, Qt.UserRole))
        children = [folder.child(index).text(0)
                    for index in range(folder.childCount())]
        self.assertEqual(children, ["A New Scene"])

    def test_a_file_the_writer_added_by_hand_shows_in_the_binder(self):
        (self.project.root / "Hand Written.html").write_text(
            "<p>Hand written.</p>", encoding="utf-8")

        self.window._reload_tree()

        item = next((item for item in self._items()
                     if item.text(0) == "Hand Written"), None)
        self.assertIsNotNone(item)
        self.assertIsNotNone(item.data(0, Qt.UserRole))

    def test_a_containers_own_index_is_not_a_second_document(self):
        # The container's index.html is its prose, not a child of it: the
        # binder shows the container once, not the container and an
        # "index" beside it.
        self.project.write_content(self.chapter.id, "<p>Chapter words.</p>")

        self.window._reload_tree()

        names = [item.text(0) for item in self._items()]
        self.assertIn("A Chapter", names)
        self.assertNotIn("index", names)
        chapter = self._find(self.chapter.id)
        self.assertEqual([chapter.child(index).text(0)
                          for index in range(chapter.childCount())],
                         ["A Scene"])

    def test_a_row_whose_file_has_gone_is_still_shown(self):
        # An empty document is a thing the writer can see and mend; a
        # vanished one is a mystery.
        (self.project.root / self.scene.path).unlink()

        self.window._reload_tree()

        item = self._find(self.scene.id)
        self.assertIsNotNone(item)
        self.assertEqual(item.text(0), "A Scene")

    # --- the icon says what the thing is -----------------------------------

    def test_the_icon_says_what_the_thing_is_not_what_its_row_says(self):
        # A chapter is a container on disk: a directory, so a folder's
        # icon. A scene is a file, so a document's.
        self.window._reload_tree()

        chapter = self._find(self.chapter.id)
        scene = self._find(self.scene.id)
        self.assertTrue(chapter.data(0, FOLDER_ROLE))
        self.assertFalse(scene.data(0, FOLDER_ROLE))
        self.assertEqual(self._pixels(chapter.icon(0)), self._folder_pixels())
        self.assertEqual(self._pixels(scene.icon(0)), self._file_pixels())

    def test_a_scene_that_holds_scenes_is_still_a_folder(self):
        # Kind is a label, not structure (docs/STORAGE.md): a scene with
        # children is a directory on disk, and says so.
        self.project.add_document("Nested", self.scene.id, "scene")

        self.window._reload_tree()

        item = self._find(self.scene.id)
        self.assertTrue(item.data(0, FOLDER_ROLE))
        self.assertEqual(self._pixels(item.icon(0)), self._folder_pixels())

    # --- the tree is not split in two --------------------------------------

    def test_a_scene_that_gains_children_keeps_them_in_one_tree(self):
        # A leaf that becomes a container moves from X.html to the
        # directory X/. The child's path must be worked out after that
        # move, or it lands in a ghost tree beside it — X.html/ — and
        # the two halves of one binder never meet again.
        scene = self.project.add_document("Outer", self.root.id, "scene")
        nested = self.project.add_document("Inner", scene.id, "scene")

        scene = next(d for d in self.project.documents() if d.id == scene.id)
        inner = next(d for d in self.project.documents() if d.id == nested.id)
        self.assertEqual(scene.path, "Draft/Outer")
        self.assertEqual(inner.path, "Draft/Outer/Inner.html")
        self.assertTrue((self.project.root / inner.path).is_file())
        self.assertFalse((self.project.root / "Draft/Outer.html").exists())

    def test_a_nested_scene_stays_inside_its_parents_folder(self):
        # Formerly "the import keeps ..." — the behaviour belongs to
        # Project.add_document, not to any importer: a leaf that gains
        # a child moves to a directory and keeps its prose as index.html.
        outer = self.project.add_document("Outer", self.root.id, "scene")
        self.project.write_content(outer.id, "<p>The scene's own words.</p>")
        nested = self.project.add_document("Nested", outer.id, "scene")

        outer = next(d for d in self.project.documents() if d.id == outer.id)
        nested = next(d for d in self.project.documents() if d.id == nested.id)
        self.assertEqual(nested.path, f"{outer.path}/Nested.html")
        self.assertTrue((self.project.root / nested.path).is_file())
        self.assertFalse((self.project.root / f"{outer.path}.html").exists())


class ContentPathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(
            Path(self.temporary.name) / "Novel.lekhika", "Novel")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def test_a_row_that_names_a_directory_reads_its_index(self):
        # The row may name a directory where the tree would have had a
        # file; the prose is the directory's own index either way, and
        # reading it must not raise.
        ghost = self.project.root / "A Folder.html"
        ghost.mkdir()
        (ghost / "index.html").write_text("<p>Words.</p>", encoding="utf-8")
        folder = self.project.add_document("A Folder", None, "folder")
        self.project.db.execute("UPDATE documents SET path=? WHERE id=?",
                                ("A Folder.html", folder.id))
        self.project.db.commit()

        self.assertEqual(self.project.read_content(folder.id), "<p>Words.</p>")

    def test_a_row_that_names_a_file_finds_the_directory_beside_it(self):
        # The row says "X.html" where the tree says "X/": the container
        # is the directory beside it, and its prose is that directory's
        # own index.
        folder = self.project.add_document("A Folder", None, "folder")
        (self.project.root / folder.path / "index.html").write_text(
            "<p>Words.</p>", encoding="utf-8")
        self.project.db.execute("UPDATE documents SET path=? WHERE id=?",
                                (f"{folder.path}.html", folder.id))
        self.project.db.commit()

        self.assertEqual(self.project.read_content(folder.id), "<p>Words.</p>")

    def test_a_missing_file_reads_as_empty_rather_than_raising(self):
        folder = self.project.add_document("A Folder", None, "folder")
        self.assertEqual(self.project.read_content(folder.id), "")


if __name__ == "__main__":
    unittest.main()
