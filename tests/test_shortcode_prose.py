"""Shortcodes in prose (BUILD-NOW 10).

${...} inside a document's own text expands once, when an export pulls
the content through the templates — the stored file keeps the literal
markers and the editor shows them, like [[wiki links]] resolving at
export rather than at rest. Conditionals and ${Content} stay out of
prose; unknown keys render empty, as in a slot.
"""

import tempfile
import unittest
from pathlib import Path

from docx import Document as DocxDocument

from lekhika.project import Project


class ProseShortcodeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = Project.create(self.root / "Novel", "Novel")
        draft = self.project.documents()[0]
        self.chapter = self.project.add_document("Chapter One", draft.id,
                                                 "chapter")
        self.scene = self.project.add_document("First Light", self.chapter.id,
                                               "scene")
        self.project.write_content(
            self.scene.id,
            "<p>Shouted ${Metadata.Greeting}, and the title read ${Title}.</p>")
        self.project.set_metadata(self.scene.id, {
            "Greeting": "hello",
            "Scene Title": "A new dawn.",
            "Danger": "a <b> c",
        })

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def _text(self) -> str:
        destination = self.root / "book.txt"
        self.project.export_text(destination)
        return destination.read_text(encoding="utf-8")

    def test_text_export_expands_shortcodes_in_prose(self):
        exported = self._text()

        self.assertIn("Shouted hello, and the title read First Light.",
                      exported)
        self.assertNotIn("${Metadata", exported)

    def test_the_stored_file_keeps_the_literal_markers(self):
        self._text()

        stored = self.project.read_content(self.scene.id)
        self.assertIn("${Metadata.Greeting}", stored)

    def test_binder_values_work_in_prose(self):
        # ${Title} above is the binder name of the scene itself.
        self.assertIn("First Light", self._text())

    def test_content_never_expands_inside_prose(self):
        self.project.write_content(self.scene.id, "<p>A ${Content} B</p>")

        exported = self._text()

        self.assertIn("A  B", exported)   # renders empty, no recursion

    def test_conditionals_stay_out_of_prose(self):
        self.project.write_content(
            self.scene.id, "<p>$if(Greeting)yes$endif $else no $endif</p>")

        exported = self._text()

        self.assertIn("$if(Greeting)yes$endif $else no $endif", exported)

    def test_unknown_keys_render_empty(self):
        self.project.write_content(
            self.scene.id, "<p>[${Metadata.Nope}]</p>")

        self.assertIn("[]", self._text())

    def test_html_export_escapes_the_expanded_value(self):
        self.project.write_content(
            self.scene.id, "<p>Danger: ${Metadata.Danger}</p>")
        destination = self.root / "book.html"
        self.project.export_html(destination)
        exported = destination.read_text(encoding="utf-8")

        self.assertIn("Danger: a &lt;b&gt; c", exported)

    def test_markdown_export_expands(self):
        destination = self.root / "book.md"
        self.project.export_markdown(destination)

        self.assertIn("Shouted hello,", destination.read_text(encoding="utf-8"))

    def test_docx_export_expands(self):
        destination = self.root / "book.docx"
        self.project.export_docx(destination)
        paragraphs = [p.text
                      for p in DocxDocument(destination).paragraphs]

        self.assertTrue(any("Shouted hello," in p for p in paragraphs))

    def test_a_chapter_opener_sees_its_own_number(self):
        self.project.write_content(self.chapter.id,
                                   "<p>Chapter ${ChapterNumber}.</p>")

        self.assertIn("Chapter 1.", self._text())


if __name__ == "__main__":
    unittest.main()
