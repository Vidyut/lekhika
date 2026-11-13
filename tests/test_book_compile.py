from __future__ import annotations

import re
import tempfile
import unittest
import zipfile
from pathlib import Path

from lekhika.project import Project
from lekhika.templates import TemplateSet, create_template_set


class BookCompileTests(unittest.TestCase):
    """Compile behaviour that outlived the binder-document template
    system (docs/TEMPLATES.md): project chrome, document-level slots,
    and cover discovery."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = Project.create(self.root / "Novel", "Novel")
        self._build_structure()

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def _build_structure(self):
        """Draft
             A Fictional Book (book) / Chapter One / First Light
             Random / Chapter Two
        """
        draft = self.project.documents()[0]
        self.book = self.project.add_document(
            "A Fictional Book", draft.id, "folder")
        chapter = self.project.add_document(
            "Chapter One", self.book.id, "chapter")
        self.scene = self.project.add_document(
            "First Light", chapter.id, "scene")
        self.project.write_content(chapter.id, "<p>Chapter opener.</p>")
        self.project.write_content(self.scene.id,
                                   "<p>First light prose.</p>")

        self.other = self.project.add_document("Random", draft.id, "folder")
        other_chapter = self.project.add_document(
            "Chapter Two", self.other.id, "chapter")
        self.project.write_content(other_chapter.id,
                                   "<p>Random chapter text.</p>")
        self.draft = draft

    def _export_text(self, selected_ids=None, templates=None) -> str:
        destination = self.root / "book.txt"
        self.project.export_text(destination, selected_ids, templates)
        return destination.read_text(encoding="utf-8")

    def _give_cover(self, folder, payload: bytes) -> Path:
        images = self.project.root / "images" / folder.id
        images.mkdir(parents=True)
        cover = images / "cover.png"
        cover.write_bytes(payload)
        return cover

    def _epub_cover_bytes(self, destination: Path) -> bytes | None:
        with zipfile.ZipFile(destination) as archive:
            names = archive.namelist()
            opf = archive.read("OEBPS/content.opf").decode()
        self.assertIn("properties='cover-image'", opf)
        self.assertIn("name='cover'", opf)
        if "OEBPS/images/cover.png" not in names:
            return None
        with zipfile.ZipFile(destination) as archive:
            return archive.read("OEBPS/images/cover.png")

    def test_selection_export_drops_project_chrome_whole_keeps_it(self):
        set_dir = create_template_set("dressed", self.project.root).directory
        (set_dir / "project_header.txt").write_text("== BEGIN ==",
                                                    encoding="utf-8")
        (set_dir / "project_footer.txt").write_text("== END ==",
                                                    encoding="utf-8")
        templates = TemplateSet.load("dressed", set_dir)

        selected = self._export_text({self.scene.id}, templates)
        self.assertNotIn("== BEGIN ==", selected)
        self.assertNotIn("== END ==", selected)

        whole = self._export_text(None, templates)
        self.assertTrue(whole.startswith("== BEGIN =="))
        self.assertIn("== END ==", whole)

    def test_document_template_override_uses_named_slot(self):
        self.project.update_document(self.scene.id, doc_template="no-title")
        exported = self._export_text({self.scene.id})
        self.assertIn("First light prose.", exported)
        self.assertNotIn("First Light", exported)

    def test_custom_document_template_slot_from_the_set(self):
        set_dir = create_template_set("news", self.project.root).directory
        documents_dir = set_dir / "documents"
        documents_dir.mkdir(exist_ok=True)
        (documents_dir / "news.html").write_text(
            "<p class='preamble'>Breaking news.</p>${Content}",
            encoding="utf-8",
        )
        templates = TemplateSet.load("news", set_dir)
        self.assertIn("news", templates.document_template_names())
        self.project.update_document(self.scene.id, doc_template="news")
        destination = self.root / "book.html"
        self.project.export_html(destination, {self.scene.id}, templates)
        exported = destination.read_text(encoding="utf-8")
        self.assertIn("Breaking news.", exported)
        self.assertIn("First light prose.", exported)

    def test_a_picked_folder_supplies_its_own_cover(self):
        self._give_cover(self.book, b"mh-bytes")
        self._give_cover(self.other, b"random-bytes")
        destination = self.root / "picked.epub"
        self.project.export_epub(destination, {self.book.id})
        self.assertEqual(self._epub_cover_bytes(destination), b"mh-bytes")

    def test_whole_project_export_takes_the_first_books_cover(self):
        self._give_cover(self.book, b"mh-bytes")
        self._give_cover(self.other, b"random-bytes")
        destination = self.root / "whole.epub"
        self.project.export_epub(destination)
        self.assertEqual(self._epub_cover_bytes(destination), b"mh-bytes")

    def _opf(self, destination: Path) -> str:
        return zipfile.ZipFile(destination).read("OEBPS/content.opf").decode()

    def test_the_opf_names_the_author_the_project_carries(self):
        # The DOCX title page has always printed the project's Author;
        # a shelf and a catalogue read dc:creator from an EPUB, so the
        # same metadata rides it — and silence is not an error either.
        self.project.set_project_metadata(
            {"Author": ("Vidyut Gore", "text", [])})
        destination = self.root / "author.epub"

        self.project.export_epub(destination)

        self.assertIn("<dc:creator>Vidyut Gore</dc:creator>",
                      self._opf(destination))

    def test_no_author_means_no_creator_element(self):
        destination = self.root / "no-author.epub"

        self.project.export_epub(destination)

        self.assertNotIn("<dc:creator>", self._opf(destination))

    def test_a_folder_holding_the_export_supplies_the_cover(self):
        # Nothing is picked but the scene; the cover lives on the book
        # folder around it, which the export still resolves to (§5).
        self._give_cover(self.book, b"mh-bytes")
        destination = self.root / "scene.epub"
        self.project.export_epub(destination, {self.scene.id})
        self.assertEqual(self._epub_cover_bytes(destination), b"mh-bytes")

    def test_an_explicit_cover_path_beats_the_conventional_one(self):
        conventional = self._give_cover(self.book, b"conventional")
        wanted = self.project.root / "chosen.jpg"
        wanted.write_bytes(b"chosen-bytes")
        self.project.set_book_settings(self.book.id, {
            "cover": "chosen.jpg",
        })
        self.assertEqual(self.project.effective_cover(self.book.id), wanted)
        self.assertNotEqual(self.project.effective_cover(self.book.id),
                            conventional)


    def _opf(self, destination: Path) -> str:
        return zipfile.ZipFile(destination).read("OEBPS/content.opf").decode()

    def _running_parts(self, destination: Path) -> list[tuple]:
        with zipfile.ZipFile(destination) as archive:
            doc = archive.read("word/document.xml").decode()
            rels = archive.read("word/_rels/document.xml.rels").decode()
            found = []
            for sect in re.findall(r"<w:sectPr.*?</w:sectPr>", doc, re.DOTALL):
                for kind, parity, rid in re.findall(
                        r'<w:(header|footer)Reference[^>]*w:type="([^"]+)"'
                        r'[^>]*r:id="([^"]+)"', sect):
                    target = re.search(rf'Id="{rid}"[^>]*Target="([^"]+)"',
                                      rels).group(1)
                    part = archive.read(f"word/{target}").decode()
                    text = "".join(
                        re.findall(r"<w:t[^>]*>([^<]*)</w:t>", part))
                    if "PAGE" in "".join(re.findall(
                            r"<w:instrText[^>]*>([^<]*)</w:instrText>",
                            part)):
                        text = "PAGE"
                    found.append((parity, kind, text.strip()))
            return found

    def test_verso_and_recto_heads_and_a_folio(self):
        # A mirrored pair of heads is the book's own furniture (§3.1):
        # the series on the verso, the book's name on the recto, and a
        # PAGE field for the folio -- three parts, not one overwrit-
        # ing the others, and present on every section of the export.
        self.project.set_project_metadata(
            {"Author": ("A Writer", "text", []),
             "Series": ("The Series", "text", [])})
        set_dir = create_template_set("headed", self.project.root).directory
        (set_dir / "print-config.txt").write_text(
            "Docx\nHeader left: ${Metadata.Series}, ,\n"
            "Header right: , ,${BookTitle}\nFooter: ,, ${Page}\n")
        destination = self.root / "headed.docx"

        self.project.export_docx(
            destination, None, TemplateSet.load("headed", set_dir))

        parts = self._running_parts(destination)
        self.assertIn(("even", "header", "The Series"), parts)
        self.assertIn(("default", "header", "Novel"), parts)
        self.assertIn(("default", "footer", "PAGE"), parts)
        # And no head carries anything else on any section.
        for parity, kind, text in parts:
            if kind == "header":
                self.assertIn(text, ("The Series", "Novel"),
                              f"a head carried {text!r}")

    def test_a_recto_head_named_by_a_key_nothing_answers_says_nothing(self):
        # `${Metadata.Book}` is not a key: the namespace carries `Book`.
        # An all-empty line is skipped by design, so a misnamed head
        # leaves the recto empty rather than writing a blank.
        set_dir = create_template_set("misnamed", self.project.root).directory
        (set_dir / "print-config.txt").write_text(
            "Docx\nHeader right: , ,${Metadata.Book}\n")
        destination = self.root / "misnamed.docx"

        self.project.export_docx(
            destination, None, TemplateSet.load("misnamed", set_dir))

        heads = [part for part in self._running_parts(destination)
                 if part[1] == "header"]
        self.assertEqual(heads, [])

    def test_a_first_page_line_takes_the_head_off_the_opening_page(self):
        # A chapter opener wearing a running head looks wrong (§3.1), so
        # the `first page` pair say what each section's opening page
        # carries: here, no head at all, while every other page keeps one.
        set_dir = create_template_set("openers", self.project.root).directory
        (set_dir / "print-config.txt").write_text(
            "Docx\nNew Chapter: Recto\nHeader: ,${BookTitle},\n"
            "Header first page: none\n")
        destination = self.root / "openers.docx"

        self.project.export_docx(
            destination, None, TemplateSet.load("openers", set_dir))

        parts = self._running_parts(destination)
        self.assertIn(("default", "header", "Novel"), parts)
        # The opening page's part is written and empty, and the section
        # is flagged to use it — the two halves of the one answer.
        self.assertIn(("first", "header", ""), parts)
        with zipfile.ZipFile(destination) as archive:
            doc = archive.read("word/document.xml").decode()
        self.assertIn("<w:titlePg", doc)

    def test_a_plain_head_line_leaves_the_opening_page_alone(self):
        # The flip side: without the `first page` line the flag is never
        # set, so the sample's own title page stays the sample's.
        set_dir = create_template_set("plain", self.project.root).directory
        (set_dir / "print-config.txt").write_text(
            "Docx\nHeader: ,${BookTitle},\n")
        destination = self.root / "plain.docx"

        self.project.export_docx(
            destination, None, TemplateSet.load("plain", set_dir))

        with zipfile.ZipFile(destination) as archive:
            doc = archive.read("word/document.xml").decode()
        self.assertNotIn("<w:titlePg", doc)

    def test_a_container_never_loses_its_children_by_silence(self):
        # `${children}` says *where* a chapter's scenes go (§3), it does
        # not gate them: a fragment that never names the family has them
        # appended, because a template must not swallow manuscript.
        set_dir = create_template_set("silent", self.project.root).directory
        (set_dir / "folder.html").write_text("<h1>${Title}</h1>",
                                            encoding="utf-8")
        destination = self.root / "silent.html"

        self.project.export_html(
            destination, None, TemplateSet.load("silent", set_dir))

        exported = destination.read_text(encoding="utf-8")
        self.assertIn("First light prose.", exported)
        # Without the placeholder the scenes follow the fragment; with
        # it they sit where the writer put them.
        self.assertLess(exported.find("<h1>Chapter One</h1>"),
                        exported.find("First light prose."))

    def test_a_page_designs_first_page_line_speaks_for_its_own_section(self):
        # A title page wearing a design of its own (1.18): its
        # `Header first page: none` belongs to the section it opened,
        # not to the file — the chapters after it keep the book's head
        # and their own opening pages unflagged.
        set_dir = create_template_set("plain", self.project.root).directory
        (set_dir / "print-config.txt").write_text(
            "Docx\nPage size: A4\nNew Chapter: Recto\nHeader: ,${BookTitle},\n")
        design = self.project.root / "templates" / "front.html"
        design.write_text("Docx\nHeader first page: none\n${Content}",
                          encoding="utf-8")
        self.project.update_document(self.scene.id, doc_template="front")
        destination = self.root / "front.docx"

        self.project.export_docx(
            destination, None, TemplateSet.load("plain", set_dir))

        with zipfile.ZipFile(destination) as archive:
            doc = archive.read("word/document.xml").decode()
        sections = re.findall(r"<w:sectPr.*?</w:sectPr>", doc, re.DOTALL)
        flagged = [index for index, sect in enumerate(sections)
                   if "<w:titlePg" in sect]
        self.assertEqual(len(flagged), 1)
        # The chapters after it carry the book's head and no first-page
        # part of their own.
        parts = self._running_parts(destination)
        self.assertIn(("default", "header", "Novel"), parts)
        self.assertIn(("first", "header", ""), parts)
        self.assertEqual(sum(1 for part in parts if part[0] == "first"), 1)

    def test_the_head_font_line_sets_the_running_heads_type(self):
        # `Head font:` is the running heads' own type (§3.1): the folio
        # is usually smaller than the body, and this is the line that
        # says so — on the pieces and on the PAGE field alike.
        set_dir = create_template_set("heads", self.project.root).directory
        (set_dir / "print-config.txt").write_text(
            "Docx\nHeader: ,${BookTitle},\nFooter: ,${PageNo},\n"
            "Head font: Georgia, 8\n")
        destination = self.root / "heads.docx"

        self.project.export_docx(
            destination, None, TemplateSet.load("heads", set_dir))

        with zipfile.ZipFile(destination) as archive:
            doc = archive.read("word/document.xml").decode()
            rels = archive.read("word/_rels/document.xml.rels").decode()
            for rid in re.findall(r'w:(?:header|footer)Reference[^>]*'
                                  r'r:id="([^"]+)"', doc):
                target = re.search(rf'Id="{rid}"[^>]*Target="([^"]+)"',
                                   rels).group(1)
                part = archive.read(f"word/{target}").decode()
                self.assertIn('w:ascii="Georgia"', part)
                # 8pt is 16 half-points in Word's own units.
                self.assertIn('<w:sz w:val="16"/>', part)

    def test_page_no_names_the_folio_and_page_keeps_working(self):
        # `${PageNo}` is the name the panel inserts and the reference
        # lists; `${Page}` is what the first templates wrote, and a
        # template that says it still gets its number (§3.2).
        self.project.write_content(
            self.scene.id, "<p>First light.</p><p>${PageNo}</p>")
        destination = self.root / "folio-no.docx"

        self.project.export_docx(destination, {self.scene.id})

        with zipfile.ZipFile(destination) as archive:
            doc = archive.read("word/document.xml").decode()
        self.assertIn('<w:instrText xml:space="preserve"> PAGE </w:instrText>',
                      doc)
        self.assertNotIn("${PageNo}", doc)
        self.assertNotIn("PageNo", doc)

    def test_a_page_placeholder_in_the_text_positions_the_folio(self):
        # `${Page}` is a position, not a count (3.1): it becomes Word's
        # own PAGE field where the writer put it, so the number stays
        # right as the text reflows. It never appears as literal text.
        self.project.write_content(
            self.scene.id, "<p>First light prose.</p><p>${Page}</p>")
        destination = self.root / "folio.docx"

        self.project.export_docx(destination, {self.scene.id})

        with zipfile.ZipFile(destination) as archive:
            doc = archive.read("word/document.xml").decode()
        self.assertIn('<w:instrText xml:space="preserve"> PAGE </w:instrText>',
                      doc)
        self.assertNotIn("${Page}", doc)

    def test_the_page_placeholder_prints_nothing_where_there_are_no_pages(self):
        # HTML has no fixed pages, so the placeholder leaves no trace —
        # not even the private-use marker it resolves to for Word.
        self.project.write_content(
            self.scene.id, "<p>First light prose.</p><p>${Page}</p>")
        destination = self.root / "folio.html"

        self.project.export_html(destination, {self.scene.id})

        page = destination.read_text(encoding="utf-8")
        self.assertNotIn("${Page}", page)
        self.assertNotIn("\ue000", page)

    def test_a_document_export_sets_no_book_title(self):
        # A lone document is not a book (3.1): `${BookTitle}` is blank
        # rather than the document's own name wearing the book's part,
        # and an all-empty head line says nothing at all.
        set_dir = create_template_set("lonesome", self.project.root).directory
        (set_dir / "print-config.txt").write_text(
            "Docx\nHeader: , ,${BookTitle}\n", encoding="utf-8")
        destination = self.root / "lone.docx"

        self.project.export_docx(destination, {self.scene.id},
                                 TemplateSet.load("lonesome", set_dir))

        heads = [part for part in self._running_parts(destination)
                 if part[1] == "header"]
        self.assertEqual(heads, [])

    def test_a_folder_export_wears_the_folders_book_title(self):
        set_dir = create_template_set("titled", self.project.root).directory
        (set_dir / "print-config.txt").write_text(
            "Docx\nHeader: , ,${BookTitle}\n", encoding="utf-8")
        self.project.set_metadata(
            self.book.id, {"BookTitle": "A Fictional Book"}, {}, {})
        destination = self.root / "titled.docx"

        self.project.export_docx(destination, {self.book.id},
                                 TemplateSet.load("titled", set_dir))

        parts = self._running_parts(destination)
        self.assertIn(("default", "header", "A Fictional Book"), parts)


if __name__ == "__main__":
    unittest.main()
