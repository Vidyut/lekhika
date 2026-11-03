from __future__ import annotations

import re
import tempfile
import unittest
import zipfile
from pathlib import Path

from lekhak.project import Project
from lekhak.templates import TemplateSet, create_template_set


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
            "Header right: , ,${Book}\nFooter: ,, ${Page}\n")
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


if __name__ == "__main__":
    unittest.main()
