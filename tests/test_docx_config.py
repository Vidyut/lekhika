"""3.1 print handling: the `#`-commented config block (in a fragment
header or as its own `Docx`-marked file), the sample `.docx` route, and
the one rule of precedence — the sample supplies everything, each config
line overrides it, page setup (1.15) applies only where the template was
silent.
"""

import json
import tempfile
import unittest
from pathlib import Path

from lekhika.project import (
    Project,
    TEMPLATE_CONFIG_KEY,
    TEMPLATE_DOC_PREFIX,
    config_geometry,
    config_headings,
    config_page_size,
    page_break_value,
)
from lekhika.templates import (
    TemplateSet,
    create_template_set,
    parse_docx_config,
    split_config_block,
)


class ConfigParsingTests(unittest.TestCase):
    def test_a_key_keeps_its_name_and_loses_its_parenthetical(self):
        config = parse_docx_config(
            "Docx\n"
            "# Sample configuration.\n"
            "Page size (inches - horizontal, vertical): 5,7\n"
            "Margins (L,T,R,B/I,T,O,B): 0.5, 0.3, 0.3, 0.3\n")
        self.assertEqual(config["page size"], "5,7")
        self.assertEqual(config["margins"], "0.5, 0.3, 0.3, 0.3")

    def test_comments_blanks_and_keyless_lines_are_not_config(self):
        config = parse_docx_config(
            "# a comment\n\nnot a config line\n<b>html with a colon:</b> tail\n")
        self.assertEqual(config, {})

    def test_a_repeated_key_keeps_the_later_value(self):
        config = parse_docx_config("Page size: 5,7\nPage size: 6,9\n")
        self.assertEqual(config["page size"], "6,9")

    def test_a_fragment_header_block_is_stripped_from_the_fragment(self):
        """The block rides in the fragment's own header and is stripped on
        export: it is print handling, never printed text."""
        block, rest = split_config_block(
            "# print setup\nFont: Georgia, 11pt\n"
            "<article><p>${Content}</p></article>")
        self.assertEqual(block["font"], "Georgia, 11pt")
        self.assertNotIn("Font:", rest)
        self.assertIn("<article>", rest)

    def test_a_fragment_without_a_leading_comment_is_untouched(self):
        text = "<article><p>${Content}</p></article>"
        block, rest = split_config_block(text)
        self.assertEqual(block, {})
        self.assertEqual(rest, text)

    def test_values_take_the_units_and_shapes_of_the_spec(self):
        self.assertEqual(config_page_size("5,7"), (5.0, 7.0))
        self.assertEqual(config_page_size("6 x 9"), (6.0, 9.0))
        self.assertEqual(config_page_size("A4"), (8.27, 11.69))
        self.assertIsNone(config_page_size("atlas"))
        width, height = config_page_size("127mm, 177.8mm")   # a 5x7 in mm
        self.assertAlmostEqual(width, 5.0)
        self.assertAlmostEqual(height, 7.0)
        # A four-value margins line is L,T,R,B; a pair is the mirrored
        # inside/outside, which is what saying it in two numbers means.
        geometry = config_geometry({"margins": "0.5, 0.3, 0.3, 0.3"})
        self.assertEqual(
            (geometry["inside"], geometry["top"],
             geometry["outside"], geometry["bottom"]),
            (0.5, 0.3, 0.3, 0.3))
        self.assertNotIn("mirror", geometry)
        mirrored = config_geometry({"margins": "0.75, 0.6"})
        self.assertEqual((mirrored["inside"], mirrored["outside"]),
                         (0.75, 0.6))
        self.assertTrue(mirrored["mirror"])
        # The Mirror margins line says the flag outright — either way —
        # and being read last, a hand's `no` beats a pair's implied yes.
        self.assertIs(config_geometry(
            parse_docx_config("# h\nMirror margins: yes\n"))["mirror"], True)
        self.assertIs(config_geometry(
            parse_docx_config("# h\nMargins: 0.75, 0.6\n"
                              "Mirror margins (yes/no): no\n"))["mirror"],
                      False)
        # Silence says nothing: the folder's Page setup still decides.
        self.assertNotIn("mirror",
                         config_geometry(parse_docx_config("# h\n")))
        # Spellings a hand actually types.
        for wording in ("YES", "true", "On", "1"):
            self.assertIs(config_geometry(
                {"mirror margins": wording})["mirror"], True)
        for wording in ("no", "False", "off", "0"):
            self.assertIs(config_geometry(
                {"mirror margins": wording})["mirror"], False)

    def test_page_starts_speak_the_printers_vocabulary(self):
        self.assertEqual(page_break_value("Recto"), "right")
        self.assertEqual(page_break_value("verso"), "left")
        self.assertEqual(page_break_value("page-break"), "page")
        self.assertEqual(page_break_value("continuous"), "flow")
        self.assertEqual(page_break_value("Next Odd"), "next-odd")
        self.assertIsNone(page_break_value("somewhere"))

    def test_a_running_head_line_splits_before_its_values_do(self):
        self.assertEqual(config_headings(",{Title},{Date}"), ["", "{Title}", "{Date}"])
        # An author's name carries a comma of its own; it stays in the
        # piece it belongs to rather than becoming a fourth column.
        self.assertEqual(config_headings("{Author}, ,{BookTitle}"),
                         ["{Author}", "", "{BookTitle}"])


class DocxPrintTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = Project.create(self.root / "Novel", "Novel")
        draft = self.project.documents()[0]
        self.chapter = self.project.add_document(
            "Chapter One", draft.id, "chapter")
        self.project.write_content(self.chapter.id, "<p>Chapter opener.</p>")
        self.scene = self.project.add_document(
            "First Light", self.chapter.id, "scene")
        self.project.write_content(self.scene.id, "<p>First light prose.</p>")
        self.templates = create_template_set("print", self.project.root)

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def write_config(self, lines):
        """A template's own config file: the Docx marker, a comment, and
        whatever the writer is saying. Its name carries no meaning."""
        body = "Docx\n# Sample configuration to present the text as docx.\n"
        body += "\n".join(lines) + "\n"
        (self.templates.directory / "components.txt").write_text(
            body, encoding="utf-8")

    def write_sample(self, width=6.0, height=9.0, text="VENDOR SAMPLE"):
        from docx import Document
        from docx.shared import Inches

        sample = Document()
        section = sample.sections[0]
        section.page_width, section.page_height = Inches(width), Inches(height)
        section.top_margin = section.bottom_margin = Inches(0.75)
        section.left_margin = section.right_margin = Inches(0.5)
        sample.add_paragraph(text)
        sample.save(self.templates.directory / "formatting.docx")

    def load(self):
        return TemplateSet.load("print", self.templates.directory)

    def export(self):
        destination = self.root / "book.docx"
        self.project.export_docx(destination, None, self.load())
        from docx import Document

        return Document(str(destination))

    def test_page_size_and_margins_reach_the_section(self):
        self.write_config([
            "Page size (inches - horizontal, vertical): 5,7",
            "Margins (L,T,R,B/I,T,O,B): 0.5, 0.3, 0.3, 0.3",
        ])
        document = self.export()
        section = document.sections[0]
        self.assertAlmostEqual(section.page_width.inches, 5.0)
        self.assertAlmostEqual(section.page_height.inches, 7.0)
        self.assertAlmostEqual(section.top_margin.inches, 0.3)
        self.assertAlmostEqual(section.bottom_margin.inches, 0.3)
        self.assertAlmostEqual(section.left_margin.inches, 0.5)
        self.assertAlmostEqual(section.right_margin.inches, 0.3)

    def test_commented_config_never_reaches_the_body(self):
        self.write_config(["Page size: 6,9"])
        document = self.export()
        body = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertNotIn("Sample configuration", body)
        self.assertNotIn("Page size", body)

    def set_page_setup(self, **overrides):
        """A book's own trim (1.15), on the exporting folder."""
        geometry = {
            "width": 6.0, "height": 9.0, "top": 0.75, "bottom": 0.75,
            "inside": 0.75, "outside": 0.5,
        }
        geometry.update(overrides)
        self.project.set_setting(
            "page_setup",
            json.dumps({self.project.documents()[0].id: geometry}))

    def test_page_setup_stands_when_the_template_says_nothing(self):
        self.set_page_setup()
        section = self.export().sections[0]
        self.assertAlmostEqual(section.page_width.inches, 6.0)
        self.assertAlmostEqual(section.page_height.inches, 9.0)

    def test_a_config_line_overrides_page_setup(self):
        self.set_page_setup()
        self.write_config(["Page size (inches - horizontal, vertical): 5,7"])
        section = self.export().sections[0]
        # The trim lives in the template (§1) — one source, not a cascade.
        self.assertAlmostEqual(section.page_width.inches, 5.0)
        self.assertAlmostEqual(section.page_height.inches, 7.0)
        # ...and only the line the config made: the margins it did not
        # name are still the book's own.
        self.assertAlmostEqual(section.top_margin.inches, 0.75)

    def test_the_sample_papers_the_export_and_its_prose_is_ignored(self):
        self.write_sample(width=6.0, height=9.0, text="VENDOR SAMPLE TEXT")
        document = self.export()
        section = document.sections[0]
        self.assertAlmostEqual(section.page_width.inches, 6.0)
        self.assertAlmostEqual(section.page_height.inches, 9.0)
        body = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertNotIn("VENDOR SAMPLE TEXT", body)
        self.assertIn("First light prose.", body)

    def test_a_config_line_overrides_the_sample(self):
        self.write_sample(width=6.0, height=9.0)
        self.write_config([
            "Page size (inches - horizontal, vertical): 5,7",
            "Margins (L,T,R,B/I,T,O,B): 0.6, 0.6, 0.4, 0.4",
        ])
        section = self.export().sections[0]
        self.assertAlmostEqual(section.page_width.inches, 5.0)
        self.assertAlmostEqual(section.top_margin.inches, 0.6)
        self.assertAlmostEqual(section.right_margin.inches, 0.4)

    def test_an_unreadable_sample_falls_back_rather_than_failing(self):
        (self.templates.directory / "formatting.docx").write_bytes(b"not a zip")
        document = self.export()          # still an export, still A4
        self.assertAlmostEqual(document.sections[0].page_width.inches,
                               8.27, places=2)

    def test_new_chapter_recto_opens_every_chapter_odd(self):
        from docx.oxml.ns import qn

        self.write_config(["New Chapter: Recto", "New Scene: page-break"])
        second = self.project.add_document(
            "Chapter Two", self.project.documents()[0].id, "chapter")
        self.project.write_content(second.id, "<p>Second chapter.</p>")
        document = self.export()
        types = []
        for section in document.sections[1:]:
            found = section._sectPr.find(qn("w:type"))
            if found is not None:
                types.append(found.get(qn("w:val")))
        self.assertIn("oddPage", types)

    def test_a_chosen_page_start_beats_the_config_default(self):
        self.write_config(["New Chapter: Recto"])
        self.project.update_document(self.chapter.id, page_break="flow")
        second = self.project.add_document(
            "Chapter Two", self.project.documents()[0].id, "chapter")
        self.project.write_content(second.id, "<p>Second chapter.</p>")
        self.project.update_document(second.id, page_break="flow")
        document = self.export()
        # Nothing opened a new section: `flow` is what was chosen, and
        # the config's default was never asked for.
        self.assertEqual(len(document.sections), 1)

    def test_the_font_shorthand_sets_the_bodys_type(self):
        self.write_config(["Font: Georgia, 11pt, 1.5, 1cm"])
        document = self.export()
        style = document.styles["Normal"]
        self.assertEqual(style.font.name, "Georgia")
        self.assertAlmostEqual(style.font.size.pt, 11.0)
        self.assertAlmostEqual(float(style.paragraph_format.line_spacing), 1.5)
        self.assertIsNotNone(style.paragraph_format.first_line_indent)

    def test_a_running_head_and_its_verso_twin(self):
        import zipfile

        self.write_config([
            "Header: ,{BookTitle},{Date}",
            "Header left: {BookTitle}, , {Chapter}",
        ])
        # {Chapter} is not a project value; unknown keys render empty,
        # exactly as in a fragment. ${BookTitle} comes from metadata.
        self.project.set_project_metadata({
            "Author": ("Ada", "text", []),
            "BookTitle": ("Novel", "text", []),
        })
        destination = self.root / "book.docx"
        self.project.export_docx(destination, None, self.load())
        with zipfile.ZipFile(destination) as archive:
            heads = [name for name in archive.namelist()
                     if name.startswith("word/header")]
            settings = archive.read("word/settings.xml").decode()
            head_xml = archive.read(heads[0]).decode()
        self.assertTrue(heads)
        self.assertIn("evenAndOddHeaders", settings)
        self.assertIn("Novel", head_xml)
        text = self.export()
        self.assertIn("Novel", "".join(
            paragraph.text for paragraph in text.sections[0].header.paragraphs))

    def test_a_sample_header_stands_when_the_config_does_not_name_it(self):
        from docx import Document
        from docx.shared import Inches

        sample = Document()
        section = sample.sections[0]
        section.page_width, section.page_height = Inches(6.0), Inches(9.0)
        header = section.header
        header.is_linked_to_previous = False
        header.paragraphs[0].add_run("SAMPLE HEAD")
        sample.save(self.templates.directory / "formatting.docx")

        document = self.export()
        self.assertIn("SAMPLE HEAD", document.sections[0].header.paragraphs[0].text)


class PerPagePrintTests(unittest.TestCase):
    """A page's own design speaks for its own section (1.18): wider
    margins for a title page, no running head for a dedication — while
    the book keeps one paper size."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = Project.create(self.root / "Novel", "Novel")
        self.draft = self.project.documents()[0]
        self.chapter = self.project.add_document("Chapter One",
                                                 self.draft.id, "chapter")
        self.project.write_content(self.chapter.id, "<p>Chapter opener.</p>")
        self.scene = self.project.add_document("First Light",
                                               self.chapter.id, "scene")
        self.project.write_content(self.scene.id, "<p>First light prose.</p>")
        self.templates = create_template_set("print", self.project.root)

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def load(self):
        return TemplateSet.load("print", self.templates.directory)

    def write_config(self, lines):
        body = "Docx\n# Sample configuration to present the text as docx.\n"
        body += "\n".join(lines) + "\n"
        (self.templates.directory / "components.txt").write_text(
            body, encoding="utf-8")

    def design_a_page(self, lines):
        """A template document — the design a scene wears (1.17)."""
        page = self.project.add_document("Title Page", self.draft.id, "scene")
        self.project.set_template(page.id, True)
        self.project.set_metadata(
            page.id, {TEMPLATE_CONFIG_KEY: "\n".join(lines) + "\n"})
        self.project.update_document(
            self.scene.id, doc_template=TEMPLATE_DOC_PREFIX + page.id)
        return page

    def export(self):
        destination = self.root / "book.docx"
        self.project.export_docx(destination, None, self.load())
        from docx import Document

        return Document(str(destination))

    def test_a_pages_own_margins_print_on_its_own_pages(self):
        self.write_config(
            ["Margins (L,T,R,B/I,T,O,B): 0.5, 0.75, 0.5, 0.75"])
        self.design_a_page(
            ["Margins (L,T,R,B/I,T,O,B): 1.25, 1, 1, 1"])

        sections = self.export().sections
        # The chapter kept the book's margins; the scene wore its own.
        self.assertAlmostEqual(sections[0].top_margin.inches, 0.75)
        self.assertAlmostEqual(sections[1].top_margin.inches, 1.0)
        self.assertAlmostEqual(sections[1].left_margin.inches, 1.25)
        # ...and the book's paper, not a paper of its own.
        self.assertAlmostEqual(sections[1].page_width.inches,
                               sections[0].page_width.inches)

    def test_a_page_can_say_it_carries_no_running_head(self):
        self.write_config(["Header: ,{BookTitle},", "Footer: {BookTitle} folio"])
        self.project.set_project_metadata({"BookTitle": ("Novel", "text", [])})
        self.design_a_page(["Header: none"])

        document = self.export()
        self.assertIn("Novel",
                      document.sections[0].header.paragraphs[0].text)
        # `none` is not "inherit": that page has no head at all.
        self.assertEqual(document.sections[1].header.paragraphs[0].text, "")
        # ...and only the head it named: the folio still stands.
        self.assertIn("Novel folio",
                      document.sections[1].footer.paragraphs[0].text)

    def test_a_page_printed_alone_takes_its_own_designs_paper(self):
        self.design_a_page(
            ["Page size (inches - horizontal, vertical): 5,7"])
        self.project.set_setting(
            "page_setup",
            json.dumps({self.draft.id: {
                "trim": "6x9", "width": 6.0, "height": 9.0, "top": 0.5,
                "bottom": 0.5, "inside": 0.5, "outside": 0.5,
                "mirror": True, "gutter": 0.0}}))

        # In the book, the trim is the book's: the page's size is
        # silently ignored.
        self.assertAlmostEqual(self.project.effective_page_setup(
            self.scene.id)["width"], 6.0)
        self.assertAlmostEqual(self.export().sections[0].page_width.inches,
                               6.0)

        # Printed on its own, the page states the truth.
        self.assertAlmostEqual(self.project.standalone_page_setup(
            self.scene.id)["width"], 5.0)
        destination = self.root / "page.docx"
        self.project.export_docx(destination, {self.scene.id}, self.load())
        from docx import Document

        self.assertAlmostEqual(
            Document(str(destination)).sections[0].page_width.inches, 5.0)

if __name__ == "__main__":
    unittest.main()