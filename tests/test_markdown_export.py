"""Clean markdown export — plus the HTML-to-markdown conversion itself.
"""

import tempfile
import unittest
from pathlib import Path

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lekhak.markdown import (  # noqa: E402
    markdown_from_html, markdown_to_html,
)
from lekhak.project import Project  # noqa: E402


class MarkdownFromHtmlTests(unittest.TestCase):
    def test_paragraphs_and_inline_round_trip(self):
        source = "A cold *open*, **hard**.\n\nSecond paragraph."

        html = markdown_to_html(source)

        self.assertEqual(markdown_from_html(html), source)

    def test_becomes_a_newline(self):
        self.assertEqual(markdown_from_html("<p>a<br>b</p>"), "a\nb")

    def test_other_tags_dropped_and_entities_unescaped(self):
        self.assertEqual(
            markdown_from_html(
                '<p><u>plain</u> &amp; <span style="color:red">red</span>'
                " &lt;tag&gt;</p>"),
            "plain & red <tag>")


class ExportMarkdownTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = Project.create(self.root / "Novel", "Novel")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def _build_manuscript(self):
        """Draft (default)
             Novel (folder) / The War (chapter) / First Light (scene)
        """
        draft = self.project.documents()[0]
        folder = self.project.add_document("Novel", draft.id, "folder")
        chapter = self.project.add_document("The War", folder.id, "chapter")
        scene = self.project.add_document(
            "First Light", chapter.id, "scene")
        self.project.write_content(chapter.id, "<p>War prose.</p>")
        self.project.write_content(
            scene.id, "<p>Scene <b>bold</b> and <i>soft</i>.</p>")
        return folder, chapter, scene

    def test_headings_and_prose(self):
        self._build_manuscript()
        destination = self.root / "out.md"

        self.project.export_markdown(destination)

        text = destination.read_text(encoding="utf-8")
        self.assertIn("# The War", text)
        self.assertIn("## First Light", text)
        self.assertIn("Scene **bold** and *soft*.", text)
        # Blocks are blank-line separated, re-importable as-is.
        self.assertIn("War prose.\n\n## First Light", text)

    def test_selection_exports_only_the_picked_folder(self):
        folder, _chapter, _scene = self._build_manuscript()
        other = self.project.add_document("Elsewhere",
                                          self.project.documents()[0].id,
                                          "folder")
        self.project.write_content(other.id, "<p>Not here.</p>")
        destination = self.root / "out.md"

        self.project.export_markdown(destination, {folder.id})

        text = destination.read_text(encoding="utf-8")
        self.assertIn("# The War", text)
        self.assertNotIn("Elsewhere", text)

    def test_wiki_links_become_plain_words(self):
        folder, chapter, _scene = self._build_manuscript()
        self.project.write_content(
            chapter.id,
            "<p>See [[First Light]] and [[Nowhere]].</p>")
        destination = self.root / "out.md"

        self.project.export_markdown(destination)

        text = destination.read_text(encoding="utf-8")
        self.assertIn("See First Light and Nowhere.", text)
        self.assertNotIn("[[", text)

    def test_export_writes_markdown_a_human_can_reread(self):
        # Formerly an import→export→import round trip. Import is
        # archived (lekhak-imports), so the export test stands alone:
        # headings + prose land as readable markdown.
        self._build_manuscript()
        destination = self.root / "out.md"

        self.project.export_markdown(destination)

        text = destination.read_text(encoding="utf-8")
        self.assertIn("# The War", text)
        self.assertIn("## First Light", text)
        self.assertIn("Scene **bold** and *soft*.", text)
        self.assertIn("War prose.\n\n## First Light", text)


if __name__ == "__main__":
    unittest.main()
