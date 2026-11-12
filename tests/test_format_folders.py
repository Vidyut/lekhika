"""One set, several formats (docs/TEMPLATES.md §1.3).

A set is a folder on disk, and a folder inside it named for an export
format — ``epub/``, ``pdf/``, ``docx/``, ``html/``, ``text/`` — holds the
same layout as the set's root and shadows the root's file of that name
*for that format alone*. Whatever the folder does not carry still comes
from the root, which is the whole reason a set with one template exports
everything: the folder refines, it never replaces.

That is the layout the author set for a set that dresses a printed book
and an ebook differently — mirrored margins, inserted blank pages and a
page-numbered ToC belong in ``docx/`` and ``pdf/``, and an EPUB has no
use for any of them — while the two formats keep one continuity of
formatting because they are one folder.
"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from docx import Document as DocxDocument

from lekhika.project import Project
from lekhika.templates import DOCX_MARKER, TemplateSet, create_template_set


class FormatFolderTests(unittest.TestCase):
    """Which set an export gets — the model, without an export in it."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project_root = self.root / "Novel"
        self.project_root.mkdir()
        self.set = create_template_set("Book", self.project_root)
        self.directory = self.set.directory

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative: str, text: str) -> Path:
        path = self.directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def config(self, relative: str, *lines: str) -> Path:
        return self.write(relative, "\n".join((DOCX_MARKER, *lines)) + "\n")

    def load(self) -> TemplateSet:
        return TemplateSet.load("Book", self.directory)

    def test_a_set_without_a_format_folder_answers_as_itself(self):
        templates = self.load()
        self.assertEqual(list(templates.formats), [])
        for fmt in ("epub", "pdf", "docx", "html", "text"):
            self.assertIs(templates.for_format(fmt), templates, fmt)
        self.assertIs(templates.for_format(None), templates)

    def test_a_format_folder_shadows_one_file_for_its_format_alone(self):
        self.write("folder.html", "<p>root chapter</p>")
        self.write("epub/folder.html", "<p>epub chapter</p>")
        templates = self.load()

        self.assertEqual(templates.for_format("epub").slot("folder"),
                         "<p>epub chapter</p>")
        # ... and no other format is touched by it: one refinement, one
        # format, one folder — everything else keeps the set's own file.
        self.assertEqual(templates.for_format("html").slot("folder"),
                         "<p>root chapter</p>")
        self.assertEqual(templates.for_format("docx").slot("folder"),
                         "<p>root chapter</p>")
        self.assertEqual(templates.slot("folder"), "<p>root chapter</p>")

    def test_a_file_the_folder_does_not_carry_falls_through(self):
        self.write("document.html", "<p>root scene</p>")
        self.write("epub/folder.html", "<p>epub only</p>")
        templates = self.load()

        self.assertEqual(templates.for_format("epub").slot("folder"),
                         "<p>epub only</p>")
        self.assertEqual(templates.for_format("epub").slot("document"),
                         "<p>root scene</p>")

    def test_both_modes_are_readable_from_a_format_folder(self):
        # The one rule for telling files apart (§3) holds inside the
        # folder exactly as it does at the root: .html dresses markup
        # exports, .txt the plain ones.
        self.write("epub/folder.html", "<p>epub html</p>")
        self.write("text/folder.txt", "text mode")
        templates = self.load()

        self.assertEqual(templates.for_format("epub").slot("folder"),
                         "<p>epub html</p>")
        self.assertEqual(templates.for_format("text").slot("folder",
                                                           html=False),
                         "text mode")
        self.assertEqual(templates.for_format("epub").slot("folder",
                                                           html=False),
                         templates.slot("folder", html=False))

    def test_print_config_is_format_over_root_key_by_key(self):
        self.config("print-config.txt", "# root", "Page size: 5,7",
                    "Font: Georgia, 11pt")
        self.config("pdf/print-config.txt", "Page size: A4")
        templates = self.load()

        merged = templates.for_format("pdf").docx_config
        self.assertEqual(merged["page size"], "A4")      # the folder wins
        self.assertEqual(merged["font"], "Georgia, 11pt")  # the root is kept
        self.assertEqual(templates.for_format("docx").docx_config["page size"],
                         "5,7")

    def test_a_format_folder_may_carry_its_own_sample_docx(self):
        DocxDocument().save(str(self.directory / "root-sample.docx"))
        (self.directory / "pdf").mkdir()
        DocxDocument().save(str(self.directory / "pdf" / "print-sample.docx"))
        templates = self.load()

        self.assertEqual(templates.docx_sample.name, "root-sample.docx")
        self.assertEqual(templates.for_format("pdf").docx_sample.name,
                         "print-sample.docx")
        self.assertEqual(templates.for_format("docx").docx_sample.name,
                         "root-sample.docx")

    def test_the_document_shelf_is_format_scoped_with_fall_through(self):
        self.write("documents/news.html", "<p>root news</p>")
        self.write("documents/aside.html", "<p>root aside</p>")
        self.write("epub/documents/news.html", "<p>epub news</p>")
        templates = self.load()

        self.assertEqual(
            templates.for_format("epub").document_slot("news", html=True),
            "<p>epub news</p>")
        self.assertEqual(
            templates.for_format("html").document_slot("news", html=True),
            "<p>root news</p>")
        # A dress the folder says nothing about is still the set's own.
        self.assertEqual(
            templates.for_format("epub").document_slot("aside", html=True),
            "<p>root aside</p>")

    def test_an_empty_format_folder_changes_nothing(self):
        # An override that states nothing is not a gap: the folder exists
        # for the writer to fill, and until then every answer is the
        # root's — a format folder is never a requirement.
        (self.directory / "pdf").mkdir()
        templates = self.load()

        self.assertIn("pdf", templates.formats)
        self.assertEqual(templates.for_format("pdf").slot("folder"),
                         templates.slot("folder"))
        self.assertEqual(templates.for_format("pdf").docx_config,
                         templates.docx_config)

    def test_a_shipped_set_has_no_folder_to_refine_with(self):
        # Its files are code; a writer refines one format by duplicating
        # the set first, and the duplicate is a folder like any other.
        builtin = TemplateSet.builtin("default")
        self.assertEqual(builtin.formats, {})
        self.assertIs(builtin.for_format("epub"), builtin)

    def test_an_unknown_or_blank_format_is_the_set_itself(self):
        self.write("epub/folder.html", "<p>epub</p>")
        templates = self.load()
        self.assertIs(templates.for_format("docbook"), templates)
        self.assertIs(templates.for_format(""), templates)
        self.assertIs(templates.for_format(None), templates)


class FormatExportTests(unittest.TestCase):
    """The same folders, seen from the four exporters."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = Project.create(self.root / "Novel", "Novel")
        draft = self.project.documents()[0]
        chapter = self.project.add_document("Chapter One", draft.id, "chapter")
        self.project.write_content(chapter.id, "<p>Chapter opener.</p>")
        scene = self.project.add_document("Alpha", chapter.id, "scene")
        self.project.write_content(scene.id, "<p>Alpha text.</p>")
        self.set = create_template_set("Book", self.project.root)
        self.directory = self.set.directory

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def write(self, relative: str, text: str) -> Path:
        path = self.directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def load(self) -> TemplateSet:
        return TemplateSet.load("Book", self.directory)

    def test_html_takes_the_root_fragment_and_epub_its_own(self):
        self.write("folder.html", '<article class="root">${Content}</article>')
        self.write("epub/folder.html",
                   '<article class="epub">${Content}</article>')
        templates = self.load()

        html_file = self.root / "book.html"
        self.project.export_html(html_file, None, templates)
        exported = html_file.read_text(encoding="utf-8")
        self.assertIn('class="root"', exported)
        self.assertNotIn('class="epub"', exported)

        epub_file = self.root / "book.epub"
        self.project.export_epub(epub_file, None, templates)
        with zipfile.ZipFile(epub_file) as archive:
            pages = "\n".join(
                archive.read(name).decode("utf-8")
                for name in archive.namelist() if name.endswith(".xhtml"))
        self.assertIn('class="epub"', pages)
        self.assertNotIn('class="root"', pages)

    def test_docx_and_pdf_can_take_different_print_config(self):
        self.write("print-config.txt", f"{DOCX_MARKER}\n# root\nPage size: 5,7")
        self.write("pdf/print-config.txt", f"{DOCX_MARKER}\nPage size: A4")
        templates = self.load()

        docx_file = self.root / "book.docx"
        self.project.export_docx(docx_file, None, templates)
        pdf_compile = self.root / "book-as-pdf.docx"
        self.project.export_docx(pdf_compile, None, templates, fmt="pdf")

        from docx import Document
        self.assertAlmostEqual(
            Document(str(docx_file)).sections[0].page_width.inches, 5.0)
        self.assertAlmostEqual(
            Document(str(pdf_compile)).sections[0].page_width.inches, 8.27,
            places=1)

    def test_a_text_export_can_have_its_own_fragment(self):
        self.write("folder.txt", "ROOT ${Content}")
        self.write("text/folder.txt", "TEXT-ONLY ${Content}")
        templates = self.load()

        destination = self.root / "book.txt"
        self.project.export_text(destination, None, templates)
        exported = destination.read_text(encoding="utf-8")
        self.assertIn("TEXT-ONLY", exported)
        self.assertNotIn("ROOT", exported)

    def test_the_running_head_names_the_format_it_is_for(self):
        # `${Format}` in a header is filled per export, so a PDF says pdf
        # and a DOCX says docx — the same template, two answers.
        self.assertEqual(
            self.project.docx_config_values(None)["Format"], "docx")
        self.assertEqual(
            self.project.docx_config_values(None, fmt="pdf")["Format"], "pdf")

    def test_a_named_set_refines_itself_for_the_export_too(self):
        # The resolver's other route: a folder naming a set in book
        # settings gets that set's own format folder, never the root's.
        self.write("folder.html", '<article class="root">${Content}</article>')
        self.write("epub/folder.html",
                   '<article class="epub">${Content}</article>')
        templates = self.load()

        resolved = self.project._template_resolver(templates, "epub")
        documents = self.project.documents()
        chosen = resolved(next(d.id for d in documents if d.kind == "scene"))
        self.assertEqual(chosen.slot("folder"),
                         '<article class="epub">${Content}</article>')
        resolved_html = self.project._template_resolver(templates, "html")
        chosen_html = resolved_html(
            next(d.id for d in documents if d.kind == "scene"))
        self.assertEqual(chosen_html.slot("folder"),
                         '<article class="root">${Content}</article>')


if __name__ == "__main__":
    unittest.main()
