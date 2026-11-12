"""Locking: a folder or a document that is not written to.

A writer with a series has published books they still want to read
while they work on the next one. A lock says "this is not being
changed": the text opens, it can be read and referenced, and even typed
in — but the file on disk stays exactly as it was.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from lekhika.project import Project


class LockTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(
            Path(self.temporary.name) / "Novel.lekhika", "Novel")
        self.root = self.project.documents()[0]
        self.book = self.project.add_document("Book One", self.root.id,
                                              "folder")
        self.chapter = self.project.add_document("Chapter One", self.book.id,
                                                 "chapter")
        self.scene = self.project.add_document("A Scene", self.chapter.id,
                                                "scene")
        self.project.write_content(self.scene.id, "<p>The prose.</p>")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def test_locking_a_folder_drops_a_marker_inside_it(self):
        self.project.set_locked(self.book.id, True)
        marker = self.project.root / self.book.path / ".locked"
        self.assertTrue(marker.is_file())

    def test_a_locked_folder_locks_everything_inside_it(self):
        self.project.set_locked(self.book.id, True)
        self.assertTrue(self.project.is_locked(self.book.id))
        self.assertTrue(self.project.is_locked(self.chapter.id))
        self.assertTrue(self.project.is_locked(self.scene.id))
        self.assertEqual(self.project.lock_holder(self.scene.id),
                         "Book One")

    def test_unlocking_takes_the_marker_away(self):
        self.project.set_locked(self.book.id, True)
        self.project.set_locked(self.book.id, False)
        self.assertFalse(self.project.is_locked(self.book.id))
        self.assertFalse(self.project.is_locked(self.scene.id))
        self.assertFalse((self.project.root / self.book.path / ".locked")
                         .exists())

    def test_a_document_can_be_locked_on_its_own(self):
        self.project.set_locked(self.scene.id, True)
        self.assertTrue(self.project.is_locked(self.scene.id))
        self.assertFalse(self.project.is_locked(self.chapter.id))
        self.assertEqual(self.project.lock_holder(self.scene.id), "A Scene")

    def test_the_marker_is_hidden_from_the_binder(self):
        # A dotfile is machinery, not a document: it must never show as
        # a scene.
        self.project.set_locked(self.book.id, True)
        folder = self.project.root / self.book.path
        visible = {entry.name for entry in folder.iterdir()
                   if not entry.name.startswith(".")}
        self.assertNotIn(".locked", visible)
        # And the project scan must not make a row for it either.
        before = len(self.project.documents())
        self.project.sync_with_disk()
        self.assertEqual(len(self.project.documents()), before)

    def test_locked_ids_covers_the_whole_locked_branch(self):
        self.project.set_locked(self.book.id, True)
        locked = self.project.locked_ids()
        self.assertIn(self.book.id, locked)
        self.assertIn(self.chapter.id, locked)
        self.assertIn(self.scene.id, locked)
        self.assertNotIn(self.root.id, locked)

    def test_a_renamed_document_keeps_its_lock(self):
        self.project.set_locked(self.scene.id, True)
        self.project.update_document(self.scene.id, title="Another Name")
        self.assertTrue(self.project.is_locked(self.scene.id))

    def test_the_lock_survives_a_copy_of_the_folder(self):
        self.project.set_locked(self.book.id, True)
        copy = self.project.root.parent / "copy"
        shutil.copytree(self.project.root / self.book.path, copy)
        self.assertTrue((copy / ".locked").is_file())
