"""Placeholders as an export-time vocabulary (docs/TEMPLATES.md §3.2).

They are not a template feature: the same ${...} resolves in a fragment and
in the manuscript, once, when an export lifts the text. These tests pin the
names the reference page publishes — position and its spellings, and the
one-namespace rule where a metadata key answers under its own name.
"""

import tempfile
import unittest
from pathlib import Path

from lekhak.project import Project, _spelled_position


class PositionPlaceholderTests(unittest.TestCase):
    """``${Position}``, ``${Position-spell}`` and ``${Position-depth:N}``.

    Position is the document's place among its siblings; the book folder
    above it is not a numbered level, so a chapter there is 1, 2, 3 and a
    scene inside it is 2.1.
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = Project.create(self.root / "Novel", "Novel")
        self.draft = self.project.documents()[0]
        self.project.add_document("Chapter One", self.draft.id, "chapter")
        self.chapter = self.project.add_document("Chapter Two",
                                                 self.draft.id, "chapter")
        self.first = self.project.add_document("Opening", self.chapter.id,
                                               "scene")
        self.second = self.project.add_document("Dusk", self.chapter.id,
                                                "scene")
        line = "<p>${Position} / ${Position-spell} / ${Position-depth:2}</p>"
        self.project.write_content(self.first.id, line)
        self.project.write_content(self.second.id, line)

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def _text(self) -> str:
        destination = self.root / "book.txt"
        self.project.export_text(destination)
        return destination.read_text(encoding="utf-8")

    def test_position_is_the_place_among_siblings(self):
        # First scene of the second chapter: 1 among its siblings, and its
        # path from the book is the chapter's 2 then its own 1.
        self.assertIn("1 / one / 2.1", self._text())

    def test_the_second_sibling_counts_on(self):
        self.assertIn("2 / two / 2.2", self._text())

    def test_front_matter_does_not_shift_the_chapter_count(self):
        # A title page at the top of the same folder is another Type, so
        # the chapters still count 1, 2, 3 rather than 2, 3, 4 (3.2).
        title = self.project.add_document("Title Page", self.draft.id, "note")
        self.project.set_metadata(title.id, {"Type": "Title"})
        self.project.reorder(title.id, self.draft.id, 0)

        self.assertIn("1 / one / 2.1", self._text())

    def test_a_chapter_prints_its_own_number(self):
        # The chapter is the second child of the folder, and the folder is
        # the book the export dresses — not a numbered level above it.
        self.project.write_content(self.chapter.id,
                                   "<p>${Position} ${Position-depth:2}</p>")
        exported = self._text()

        self.assertIn("2 2", exported)
        self.assertNotIn("1.2", exported)


class SpelledPositionTests(unittest.TestCase):
    def test_numbers_read_as_prose(self):
        self.assertEqual(_spelled_position(1), "one")
        self.assertEqual(_spelled_position(13), "thirteen")
        self.assertEqual(_spelled_position(21), "twenty-one")
        self.assertEqual(_spelled_position(40), "forty")
        self.assertEqual(_spelled_position(105), "one hundred five")


class MetadataNamespaceTests(unittest.TestCase):
    """A metadata key answers under its own name as well as its
    ``Metadata.`` alias, in prose as in a fragment."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = Project.create(self.root / "Novel", "Novel")
        folder = self.project.documents()[0]
        self.scene = self.project.add_document("Opening", folder.id, "scene")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def _text(self) -> str:
        destination = self.root / "book.txt"
        self.project.export_text(destination)
        return destination.read_text(encoding="utf-8")

    def test_a_bare_key_is_the_metadata_key(self):
        self.project.set_metadata(self.scene.id, {"Uncle": "Robert"})
        self.project.write_content(self.scene.id, "<p>${Uncle}</p>")

        self.assertIn("Robert", self._text())

    def test_the_metadata_spelling_still_works(self):
        self.project.set_metadata(self.scene.id, {"Uncle": "Robert"})
        self.project.write_content(self.scene.id, "<p>${Metadata.Uncle}</p>")

        self.assertIn("Robert", self._text())

    def test_project_metadata_answers_where_the_document_is_silent(self):
        self.project.set_project_metadata({"Uncle": ("Robert", "text", [])})
        self.project.write_content(self.scene.id, "<p>${Uncle}</p>")

        self.assertIn("Robert", self._text())

    def test_the_documents_own_value_wins(self):
        self.project.set_project_metadata({"Uncle": ("Robert", "text", [])})
        self.project.set_metadata(self.scene.id, {"Uncle": "Bob"})
        self.project.write_content(self.scene.id, "<p>${Uncle}</p>")

        exported = self._text()
        self.assertIn("Bob", exported)
        self.assertNotIn("Robert", exported)

    def test_a_built_in_name_wins_over_a_metadata_key(self):
        self.project.set_project_metadata({"Title": ("Wrong", "text", [])})

        self.assertIn("Opening", self._text())

    def test_metadata_is_grouped_by_the_type_carrying_it(self):
        self.project.set_metadata(self.scene.id, {"PoV": "Maren"})
        self.project.set_project_metadata({"Author": ("A. Writer", "text", [])})
        chapter = self.project.add_document("Chapter", self.scene.parent_id,
                                            "chapter")
        self.project.set_metadata(chapter.id, {"Type": "Chapter", "PoV": "Ivo"})

        groups = self.project.metadata_placeholders()

        self.assertEqual(groups["Project"], {"Author": 1})
        self.assertEqual(groups["Document"], {"PoV": 1})
        self.assertEqual(groups["Chapter"], {"PoV": 1})


if __name__ == "__main__":
    unittest.main()