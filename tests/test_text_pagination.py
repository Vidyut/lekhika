"""BUILD-NOW 9: the phase-1 leftovers, settled.

Text and markdown export are structural — titles and headings as lines,
units separated by blank lines — and deliberately carry no form-feed
pagination (docs/TEMPLATES.md §10, settled 2026-10-22); pagination is
the page-setup formats' job. EPUB needed no such decision: its one
XHTML file per compiled unit already is pages, which this file pins
alongside the no-form-feed rule.
"""

import tempfile
import unittest
import zipfile
from pathlib import Path

from lekhika.project import Project


class TextPaginationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = Project.create(self.root / "Novel", "Novel")
        draft = self.project.documents()[0]
        chapter = self.project.add_document("Chapter One", draft.id, "chapter")
        self.scene = self.project.add_document("First Light", chapter.id,
                                               "scene")
        self.project.write_content(chapter.id, "<p>Chapter opener.</p>")
        self.project.write_content(self.scene.id,
                                   "<p>First light prose.</p>")
        chapter_two = self.project.add_document("Chapter Two", draft.id,
                                                "chapter")
        self.project.write_content(chapter_two.id,
                                   "<p>Second chapter text.</p>")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def _text(self, selected_ids=None) -> str:
        destination = self.root / "book.txt"
        self.project.export_text(destination, selected_ids)
        return destination.read_text(encoding="utf-8")

    def test_text_export_has_no_form_feeds(self):
        exported = self._text()

        self.assertNotIn("\f", exported)
        self.assertIn("Chapter opener.", exported)
        self.assertIn("Second chapter text.", exported)

    def test_a_selection_export_has_no_form_feeds_either(self):
        exported = self._text({self.scene.id})

        self.assertNotIn("\f", exported)
        self.assertIn("First light prose.", exported)

    def test_units_are_separated_by_blank_lines(self):
        exported = self._text()

        # A chapter unit ends with its last scene's prose; the next
        # unit's title follows across the three-newline gap.
        self.assertIn("First light prose.\n\n\nChapter Two", exported)

    def test_markdown_export_has_no_form_feeds(self):
        destination = self.root / "book.md"
        self.project.export_markdown(destination)
        exported = destination.read_text(encoding="utf-8")

        self.assertNotIn("\f", exported)
        self.assertIn("# Chapter One", exported)

    def test_epub_pagination_is_one_file_per_unit(self):
        destination = self.root / "book.epub"
        self.project.export_epub(destination)

        with zipfile.ZipFile(destination) as archive:
            sections = [name for name in archive.namelist()
                        if name.startswith("OEBPS/section-")]

        # Draft (root folder) + two chapter units = one file per unit.
        self.assertEqual(len(sections), 3)


if __name__ == "__main__":
    unittest.main()
