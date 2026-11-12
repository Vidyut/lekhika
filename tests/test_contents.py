"""The contents: the headings the export produced (docs/TEMPLATES.md §3.2).

Entry text is the heading's own words, its depth is the heading's level,
and a folder or document that renders no heading is no entry at all —
the rule Word's own table follows, so the print file and the reading
formats agree. No Type is consulted anywhere: a project whose vocabulary
is nothing like ours gets the same contents.
"""

import tempfile
import unittest
import zipfile
from pathlib import Path

from lekhika.project import Project


class ContentsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = Project.create(self.root / "Novel", "Novel")
        book = self.project.documents()[0]
        chapter = self.project.add_document("Chapter One", book.id, "chapter")
        scene = self.project.add_document("Opening", chapter.id, "scene")
        self.project.write_content(scene.id, "<p>Words.</p>")
        self.project.add_document("Chapter Two", book.id, "chapter")
        self.contents = self.project.add_document("Contents", book.id, "scene")
        self.project.update_document(self.contents.id, doc_template="contents")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def _export(self, suffix: str) -> Path:
        destination = self.root / f"book.{suffix}"
        {"html": self.project.export_html,
         "txt": self.project.export_text,
         "epub": self.project.export_epub}[suffix](destination, None) \
            if suffix != "epub" else self.project.export_epub(destination)
        return destination

    def test_the_entries_are_the_headings_nested_by_their_level(self):
        # The headings come from the templates: with the default set,
        # containers and documents both render as h2, so the contents
        # lists each heading as its own entry (nesting follows levels).
        # The root folder is an exported unit now, so it appears too.
        html = self._export("html").read_text()
        self.assertIn("<li>Draft</li>", html)
        self.assertIn("<li>Chapter One</li>", html)
        self.assertIn("<li>Opening</li>", html)
        self.assertIn("<li>Chapter Two</li>", html)

    def test_the_contents_page_does_not_list_itself(self):
        # Its own <h1> stands above the list; the list itself is clean.
        html = self._export("html").read_text()
        self.assertNotIn("<li>Contents", html)

    def test_a_document_with_no_heading_is_not_an_entry(self):
        # `no-title` prints the prose alone. Word's rule is the same: a
        # table of contents lists headings, not documents — so a colophon
        # that should stay out of the contents simply has no heading.
        chapter = [d for d in self.project.documents()
                   if d.title == "Chapter Two"][0]
        hidden = self.project.add_document("Colophon", chapter.id, "scene")
        self.project.update_document(hidden.id, doc_template="no-title")
        self.project.write_content(hidden.id, "<p>Set in Lekhika.</p>")
        html = self._export("html").read_text()
        self.assertIn("Set in Lekhika.", html)
        self.assertNotIn("<li>Colophon", html)

    def test_text_lines_are_lines_not_a_literal_backslash_n(self):
        text = self._export("txt").read_text()
        # All default headings are the same level, so the contents lists
        # them without nesting; the root folder is exported as a unit.
        self.assertIn("Draft", text)
        self.assertIn("Chapter One", text)
        self.assertIn("Opening", text)
        self.assertIn("Chapter Two", text)
        self.assertNotIn("\\n", text)

    def test_the_epub_nav_follows_the_same_rule(self):
        with zipfile.ZipFile(self._export("epub")) as archive:
            nav = next(name for name in archive.namelist()
                       if name.endswith("nav.xhtml"))
            page = archive.read(nav).decode()
        # The root folder is an exported unit now, and with the default
        # headings at one level the entries are listed without nesting.
        self.assertIn("<li><a href='section-1.xhtml'>Draft</a></li>", page)
        self.assertIn("<li><a href='section-2.xhtml'>Chapter One</a></li>", page)
        self.assertIn("Opening</a></li>", page)
        self.assertIn("<li><a href='section-3.xhtml'>Chapter Two</a></li>", page)


if __name__ == "__main__":
    unittest.main()
