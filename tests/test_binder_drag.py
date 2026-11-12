"""A drag moves the files, not just the rows (bug report, 2026-11-03).

The binder showed the move and the database half of it, but the folder
tree on disk never changed — so the next reload, which reads the files
as the truth (docs/STORAGE.md), put everything back where it was. The
two disagreed from then on.

The widget-level move below is the shape a real drop leaves in the
model: a removal plus an insertion. Nothing here calls the sweep by
hand, so these tests fail if the signal is no longer heard.
"""

import json
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
_settings_dir = tempfile.mkdtemp(prefix="lekhika-drag-settings-")
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


class BinderDragTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel",
                                      "Novel")
        self.draft = self.project.documents()[0]   # the project's own Draft
        self.astray = self.project.add_document("astray", self.draft.id,
                                                "folder")
        self.chapter = self.project.add_document("chapter", self.astray.id,
                                                 "scene")
        self.templates = self.project.add_document("Templates", self.draft.id,
                                                   "folder")
        self.project.write_order_files()
        QSettings().remove("session/lastProject")
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _move(self, document_id, new_parent_id, position=0):
        """Do what a drop does to the widget, then let the sweep run."""
        item = _find_item(self.window.tree, document_id)
        tree = item.treeWidget()
        holder = item.parent()
        if holder is None:
            tree.takeTopLevelItem(tree.indexOfTopLevelItem(item))
        else:
            holder.takeChild(holder.indexOfChild(item))
        if new_parent_id is None:
            tree.insertTopLevelItem(position, item)
        else:
            _find_item(self.window.tree, new_parent_id).insertChild(position,
                                                                    item)
        _application.processEvents()

    def _path(self, document_id):
        return next(d.path for d in self.project.documents()
                    if d.id == document_id)

    def _order(self, relative):
        return json.loads((self.project.root / relative).read_text())["children"]

    def test_a_drag_into_another_folder_moves_the_files(self):
        self._move(self.astray.id, self.templates.id)

        moved = self.project.root / "Draft" / "Templates" / "astray"
        self.assertTrue(moved.is_dir(), "the folder did not move on disk")
        self.assertFalse((self.project.root / "Draft" / "astray").exists())
        self.assertTrue((moved / "chapter.html").is_file(),
                        "the children did not come along")

        rows = {d.id: d for d in self.project.documents()}
        self.assertEqual(rows[self.astray.id].parent_id, self.templates.id)
        self.assertEqual(rows[self.astray.id].path, "Draft/Templates/astray")
        self.assertEqual(rows[self.chapter.id].path,
                         "Draft/Templates/astray/chapter.html")

        self.assertEqual(self._order("Draft/.order.json"), ["Templates"])
        self.assertEqual(self._order("Draft/Templates/.order.json"),
                         ["astray"])

        # Reload, as the writer would: the binder reads the files, so it
        # must now show the move instead of taking it back.
        self.window._reload_tree()
        item = _find_item(self.window.tree, self.astray.id)
        self.assertEqual(item.parent().data(0, Qt.UserRole),
                         self.templates.id)

    def test_a_drag_that_would_collide_is_refused(self):
        taken = self.project.root / "Draft" / "Templates" / "astray"
        taken.mkdir(parents=True)
        (taken / "index.html").write_text("<p>another astray</p>")
        self.project.sync_with_disk()

        self._move(self.astray.id, self.templates.id)

        self.assertTrue((self.project.root / "Draft" / "astray").is_dir())
        self.assertEqual(self.project.parent_of(self.astray.id),
                         self.draft.id)
        self.assertEqual(self._path(self.astray.id), "Draft/astray")
        # The binder snaps back to the files rather than keeping a move
        # that was never written.
        item = _find_item(self.window.tree, self.astray.id)
        self.assertEqual(item.parent().data(0, Qt.UserRole), self.draft.id)

    def test_a_drag_to_the_top_level_is_written(self):
        self._move(self.astray.id, None)

        self.assertTrue((self.project.root / "astray").is_dir())
        self.assertIsNone(self.project.parent_of(self.astray.id))
        self.assertEqual(self._path(self.astray.id), "astray")
        self.assertEqual(self._order("manifest.json")[0], "astray")

        self.window._reload_tree()
        item = _find_item(self.window.tree, self.astray.id)
        self.assertIsNone(item.parent())

    def test_a_reorder_within_a_folder_rewrites_the_order_file(self):
        self.project.add_document("second", self.draft.id, "folder")
        self.project.write_order_files()
        self.window._reload_tree()
        self.assertEqual(self._order("Draft/.order.json"),
                         ["astray", "Templates", "second"])

        second = next(d for d in self.project.documents()
                      if d.title == "second")
        self._move(second.id, self.draft.id, position=0)

        self.assertEqual(self._order("Draft/.order.json"),
                         ["second", "astray", "Templates"])


if __name__ == "__main__":
    unittest.main()
