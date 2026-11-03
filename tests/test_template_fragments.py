"""A fragment on disk is whatever the editor saved (docs/TEMPLATES.md §1.2).

Files are the truth and any editor may write them — Lekhak's own HTML
editor most of all, which saves a full Qt document (``<html><head>`` with
a stylesheet, a ``<body>`` with a font, ``<span>`` around touched runs).
The compile reads a fragment out of all that: body content, no spans. One
rule, no per-source handling, because writers paste from browsers and word
processors just as happily as they save from Lekhak.
"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from lekhak.project import Project
from lekhak.templates import (
    BUILTIN_HTML,
    TemplateSet,
    create_template_set,
    file_paths,
    is_template_fragment,
    join_head,
    parse_docx_config,
    set_template_flag,
    split_head,
    strip_editor_html,
)


class StripEditorHtmlTests(unittest.TestCase):
    def test_a_bare_fragment_passes_through(self):
        text = "<h1>${Title}</h1>\n<p>${Content}</p>"
        self.assertEqual(strip_editor_html(text), text)

    def test_a_full_document_becomes_its_body(self):
        text = (
            "<html><head><meta name=\"qrichtext\" content=\"1\" />"
            "<style type=\"text/css\">p { margin: 0px }</style></head>"
            "<body style=\" font-family:'Sans Serif'; font-size:9pt;\">"
            "<p>${Content}</p></body></html>")
        self.assertEqual(strip_editor_html(text), "<p>${Content}</p>")

    def test_every_span_is_stripped_outright(self):
        # God hates spans. The block proxy a writer types is exactly
        # this shape, and it must still read as bare.
        self.assertEqual(
            strip_editor_html("<p><span>${Content}</span></p>"),
            "<p>${Content}</p>")

    def test_pasted_browser_and_word_junk_is_handled_by_the_same_rule(self):
        text = (
            "<html><body><p class=\"MsoNormal\"><span lang=\"EN-GB\" "
            "style=\"font-size:12pt\">Hello <span><b>world</b></span>"
            "</span></p></body></html>")
        self.assertEqual(
            strip_editor_html(text),
            "<p class=\"MsoNormal\">Hello <b>world</b></p>")

    def test_body_content_survives_case_and_attributes(self):
        text = "<BODY class=\"x\"><p>${Title}</p></BODY  >"
        self.assertIn("<p>${Title}</p>", strip_editor_html(text))

    def test_a_document_with_no_body_is_only_despanned(self):
        # Half a document (a fragment wrapped in <html> by hand): there
        # is no body to take, so the markup stands and spans go.
        text = "<html><p><span>${Children}</span></p></html>"
        self.assertEqual(strip_editor_html(text),
                         "<html><p>${Children}</p></html>")


class ReadingAFileWithEditorJunkTests(unittest.TestCase):
    """The strip is applied where files are read, not where they are
    written: a file saved by the editor fills like the fragment it is."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temporary.name)
        self.created = create_template_set("Mine", self.project_root)

    def tearDown(self):
        self.temporary.cleanup()

    def _write(self, relative, text):
        path = self.created.directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_a_slot_saved_by_the_editor_still_fills(self):
        self._write("folder.html", (
            "<html><head><style>p { margin: 0px }</style></head>"
            "<body style=\" font-size:9pt;\"><p><span>Once "
            "${ChapterNumber}: ${Content}</span></p></body></html>"))
        template = TemplateSet.load("Mine", self.created.directory)

        self.assertEqual(
            template.slot("folder", html=True),
            "<p>Once ${ChapterNumber}: ${Content}</p>")

    def test_a_document_shelf_file_saved_by_the_editor_still_fills(self):
        self._write("documents/no-title.html", (
            "<html><body><p><span>${Content}</span></p></body></html>"))
        template = TemplateSet.load("Mine", self.created.directory)

        self.assertEqual(
            template.document_slot("no-title", html=True),
            "<p>${Content}</p>")

    def test_fill_of_a_stripped_fragment_leaves_no_editor_furniture(self):
        self._write("folder.html", (
            "<html><body style=\"font-size:9pt;\"><p class=\"MsoNormal\">"
            "<span>${Content}</span></p></body></html>"))
        template = TemplateSet.load("Mine", self.created.directory)

        filled = template.render("folder", {"Content": "<p>hello</p>"})

        self.assertIn("<p>hello</p>", filled)
        self.assertNotIn("<span>", filled)
        self.assertNotIn("<html>", filled)


class HeadRoundTripTests(unittest.TestCase):
    """The sidebar's config box and the editor hold one file between
    them (§1.2): split_head separates the two, join_head puts them back
    where they belong, and neither may move a line to the other's half
    on the way."""

    def test_a_head_and_its_fragment_come_back_together(self):
        text = "# comment\nPage size: A4\n<section>${Content}</section>"
        head, fragment = split_head(text)

        self.assertEqual(head, "# comment\nPage size: A4")
        self.assertEqual(fragment, "<section>${Content}</section>")
        self.assertEqual(join_head(head, fragment), text)

    def test_a_file_with_no_head_is_all_fragment(self):
        head, fragment = split_head("<p>${Title}</p>")

        self.assertEqual(head, "")
        self.assertEqual(fragment, "<p>${Title}</p>")
        self.assertEqual(join_head(head, fragment), "<p>${Title}</p>")

    def test_a_docx_marker_file_is_all_head(self):
        text = "Docx\nPage size: 5,7"
        head, fragment = split_head(text)

        self.assertEqual(head, text)
        self.assertEqual(fragment, "")
        self.assertEqual(join_head(head, fragment), text)

    def test_typed_keys_are_saved_as_keys_not_as_a_comment(self):
        # join_head must open the block, not comment its first line out:
        # a bare 'Page size: A4' head comes back parseable.
        text = join_head("Page size: A4", "<p>a</p>")
        head, fragment = split_head(text)

        self.assertEqual(parse_docx_config(head), {"page size": "A4"})
        self.assertEqual(fragment, "<p>a</p>")

    def test_an_empty_head_adds_nothing_to_the_fragment(self):
        self.assertEqual(join_head("", "<p>a</p>"), "<p>a</p>")
        self.assertEqual(join_head("   \n", "<p>a</p>"), "<p>a</p>")


class TemplateFlagTests(unittest.TestCase):
    """The sidebar's big checkbox, in the file (§1.2): `Template: no`
    in the head takes a file out of the set — unlisted, unread as a
    fragment — while the bytes stay exactly where they are."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temporary.name)
        self.created = create_template_set("Mine", self.project_root)

    def tearDown(self):
        self.temporary.cleanup()

    def _write(self, relative, text):
        path = self.created.directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_unticking_writes_the_flag_into_the_head(self):
        text = set_template_flag("<p>${Content}</p>", False)

        self.assertTrue(is_template_fragment(text) is False)
        head, fragment = split_head(text)
        self.assertEqual(parse_docx_config(head), {"template": "no"})
        self.assertEqual(fragment, "<p>${Content}</p>")

    def test_ticking_takes_the_flag_out_again(self):
        unticked = set_template_flag("# c\nTemplate: no\n<p>a</p>", True)

        self.assertEqual(unticked, "# c\n<p>a</p>")
        self.assertTrue(is_template_fragment(unticked))

    def test_the_flag_lives_with_the_config_not_in_the_fragment(self):
        # A file already carrying config keeps its config: the flag line
        # joins the head instead of displacing anything.
        text = set_template_flag("# c\nPage size: A4\n<p>a</p>", False)
        head, fragment = split_head(text)

        self.assertEqual(parse_docx_config(head),
                         {"template": "no", "page size": "A4"})
        self.assertEqual(fragment, "<p>a</p>")

    def test_a_file_without_a_head_is_a_template(self):
        self.assertTrue(is_template_fragment("<p>a</p>"))
        self.assertTrue(is_template_fragment("# nothing but a comment\n<p>a</p>"))

    def test_an_unticked_file_leaves_the_listing(self):
        self._write("stray.html", "<p>not a template</p>")
        self._write("side.html", set_template_flag("<p>side</p>", False))
        template = TemplateSet.load("Mine", self.created.directory)

        listing = file_paths(template)
        self.assertNotIn("side.html", listing)
        self.assertIn("folder.html", listing)
        # The file is still on disk — in the folder, not deleted.
        on_disk = (self.created.directory / "side.html").read_text(
            encoding="utf-8")
        self.assertIn("<p>side</p>", on_disk)
        self.assertIn("Template: no", on_disk)

    def test_an_unticked_slot_falls_back_to_the_builtin(self):
        self._write("document.html", set_template_flag("<p>mine</p>", False))
        template = TemplateSet.load("Mine", self.created.directory)

        # The set still carries scene.html as a file, but not as a slot:
        # the built-in shape renders instead.
        self.assertNotIn("<p>mine</p>", template.slot("document", html=True))
        self.assertEqual(
            template.slot("document", html=True),
            BUILTIN_HTML["document"])

    def test_an_unticked_document_template_is_not_a_document_dress(self):
        self._write("documents/short_story.html",
                    set_template_flag("<p>${Content}</p>", False))
        template = TemplateSet.load("Mine", self.created.directory)

        self.assertNotIn("<p>${Content}</p>",
                         (template.document_slot("short_story", html=True) or ""))

    def test_the_flag_round_trips_through_the_sidebar_boxes(self):
        # open: head to the config box, fragment to the editor; save:
        # the two joined back. The flag is in the head, so it survives.
        original = set_template_flag("# keep\n<p>${Content}</p>", False)
        head, fragment = split_head(original)

        self.assertEqual(parse_docx_config(head), {"template": "no"})
        self.assertEqual(join_head(head, fragment), original)


class CssTests(unittest.TestCase):
    """style.css as the set's own file (§3.2): the CSS box in the
    sidebar reads and writes one file, and exports ask the set for it."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temporary.name)
        self.created = create_template_set("Mine", self.project_root)
        self.directory = self.created.directory

    def tearDown(self):
        self.temporary.cleanup()

    def test_a_set_without_a_style_file_answers_empty(self):
        (self.directory / "style.css").unlink()
        template = TemplateSet.load("Mine", self.directory)
        self.assertEqual(template.css(), "")

    def test_the_root_style_file_is_the_sets_css(self):
        (self.directory / "style.css").write_text(
            "p { margin: 0 }", encoding="utf-8")

        template = TemplateSet.load("Mine", self.directory)

        self.assertEqual(template.css(), "p { margin: 0 }")

    def test_a_format_folders_css_wins_for_that_format_only(self):
        (self.directory / "style.css").write_text(
            "p { margin: 0 }", encoding="utf-8")
        (self.directory / "epub").mkdir()
        (self.directory / "epub" / "style.css").write_text(
            "p { margin: 9 }", encoding="utf-8")

        template = TemplateSet.load("Mine", self.directory)

        self.assertEqual(template.css(), "p { margin: 0 }")
        self.assertEqual(template.for_format("epub").css(), "p { margin: 9 }")
        # And the format without a folder of its own still gets the root.
        self.assertEqual(template.for_format("pdf").css(), "p { margin: 0 }")

    def test_a_new_set_starts_with_the_default_stylesheet(self):
        template = TemplateSet.load("Mine", self.directory)
        self.assertIn("h1 {", template.css())

    def test_a_shipped_set_ships_its_stylesheet(self):
        # No folder to read, so the stylesheet comes from the same map a
        # duplicate writes: the copy and the shipped set dress alike.
        self.assertIn("h1 {", TemplateSet.builtin().css())
        self.assertEqual(TemplateSet.builtin("nothing").css(), "")


class CssExportTests(unittest.TestCase):
    """Where the set's stylesheet lands in an export (§3.2): inside the
    HTML file, and as one linked file in the EPUB — and nowhere when the
    set has no style.css, so every existing export stays byte-identical."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project_root = self.root / "Novel"
        self.project_root.mkdir()
        self.project = Project.create(self.project_root, "Novel")
        self.chapter = self.project.add_document(
            "Chapter One", None, "chapter")
        self.project.write_content(self.chapter.id, "<p>Hello.</p>")
        self.created = create_template_set("Mine", self.project_root)
        self.directory = self.created.directory

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def load(self):
        return TemplateSet.load("Mine", self.directory)

    def test_html_export_embeds_the_stylesheet(self):
        (self.directory / "style.css").write_text(
            "p { margin: 0 }", encoding="utf-8")
        destination = self.root / "book.html"

        self.project.export_html(destination, None, self.load())

        exported = destination.read_text(encoding="utf-8")
        self.assertIn("<style>p { margin: 0 }</style>", exported)
        self.assertLess(exported.index("<style>"),
                        exported.index("unit-1"))

    def test_html_export_without_css_writes_no_style_tag(self):
        (self.directory / "style.css").unlink()
        destination = self.root / "book.html"

        self.project.export_html(destination, None, self.load())

        self.assertNotIn("<style>", destination.read_text(encoding="utf-8"))

    def test_epub_ships_the_stylesheet_as_one_linked_file(self):
        (self.directory / "style.css").write_text(
            "p { margin: 0 }", encoding="utf-8")
        destination = self.root / "book.epub"

        self.project.export_epub(destination, None, self.load())

        with zipfile.ZipFile(destination) as archive:
            names = archive.namelist()
            css = archive.read("OEBPS/style.css").decode("utf-8")
            sections = "\n".join(
                archive.read(name).decode("utf-8")
                for name in names if name.startswith("OEBPS/section-"))
            opf = archive.read("OEBPS/content.opf").decode("utf-8")
        self.assertEqual(css, "p { margin: 0 }")
        self.assertIn("href='style.css'", sections)
        self.assertIn("id='style' href='style.css'", opf)

    def test_epub_without_css_has_neither_link_nor_file(self):
        (self.directory / "style.css").unlink()
        destination = self.root / "book.epub"

        self.project.export_epub(destination, None, self.load())

        with zipfile.ZipFile(destination) as archive:
            names = archive.namelist()
        self.assertNotIn("OEBPS/style.css", names)

    def test_a_format_folders_css_reaches_that_formats_export(self):
        (self.directory / "html").mkdir()
        (self.directory / "html" / "style.css").write_text(
            "p { margin: 7 }", encoding="utf-8")
        destination = self.root / "book.html"

        self.project.export_html(destination, None, self.load())

        self.assertIn("<style>p { margin: 7 }</style>",
                      destination.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
