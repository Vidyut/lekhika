"""Page setup lives on a folder (docs/UI-NOTES.md 7).

One paper size per book is a fact of printing, not a preference. A folder may
hold it — even before it has any contents, because a book's trim is decided
before it is written — while a chapter or a document may not, because neither
is a book. Clearing stays allowed anywhere, so stale data can be tidied away.
"""

import tempfile
import unittest
from pathlib import Path

from lekhika.project import Project

SIX_BY_NINE = {"trim": "6x9", "width": 6.0, "height": 9.0,
               "top": 0.5, "bottom": 0.5, "inside": 0.5, "outside": 0.5,
               "mirror": True, "gutter": 0.0}


class PageSetupScopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel",
                                      "Novel")
        self.root = self.project.documents()[0]
        self.chapter = self.project.add_document("Chapter", self.root.id,
                                                 "chapter")
        self.scene = self.project.add_document("Scene", self.chapter.id,
                                              "scene")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def test_a_folder_may_hold_a_page_setup(self):
        self.project.set_page_setup(self.root.id, SIX_BY_NINE)
        self.assertEqual(self.project.page_setup(self.root.id)["width"], 6.0)

    def test_an_empty_folder_may_too(self):
        """A trim is decided before the book is written, so no contents needed."""
        empty = self.project.add_document("Second Book", None, "folder")
        self.project.set_page_setup(empty.id, SIX_BY_NINE)
        self.assertEqual(self.project.page_setup(empty.id)["trim"], "6x9")

    def test_a_chapter_may_not(self):
        with self.assertRaises(ValueError) as caught:
            self.project.set_page_setup(self.chapter.id, SIX_BY_NINE)
        self.assertIn("not to a chapter", str(caught.exception))

    def test_a_document_may_not(self):
        with self.assertRaises(ValueError):
            self.project.set_page_setup(self.scene.id, SIX_BY_NINE)

    def test_clearing_is_allowed_anywhere(self):
        self.project.set_page_setup(self.scene.id, None)      # no error
        self.assertIsNone(self.project.page_setup(self.scene.id))

    def test_a_scene_still_inherits_its_books_paper(self):
        self.project.set_page_setup(self.root.id, SIX_BY_NINE)
        self.assertEqual(
            self.project.effective_page_setup(self.scene.id)["width"], 6.0)


if __name__ == "__main__":
    unittest.main()
