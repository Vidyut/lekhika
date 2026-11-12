"""2.2: conditional template blocks and [[wiki link]] compile behaviour.

Conditionals live in template slots only; links are resolved at compile
time against the exported set — published targets become real links
wherever the format can carry one, everything else follows the strip
policy (per-book override, else the project default).
"""

import tempfile
import unittest
import zipfile
from pathlib import Path

from lekhak.project import Project
from lekhak.templates import TemplateSet, create_template_set


class ConditionalTemplateTests(unittest.TestCase):
    """$if / $else / $endif in TemplateSet.fill."""

    def test_comparison_picks_the_matching_branch(self):
        filled = TemplateSet.fill(
            "$if(Metadata.Status = draft)DRAFT$else FINAL$endif",
            {"Metadata.Status": "Draft"},
        )
        self.assertEqual(filled, "DRAFT")

    def test_comparison_supports_not_equals_casefold(self):
        filled = TemplateSet.fill(
            "$if(Metadata.Status != FINAL)not final$endif",
            {"Metadata.Status": "draft"},
        )
        self.assertEqual(filled, "not final")

    def test_bare_condition_is_non_empty_existence(self):
        template = "$if(Synopsis)has$else none$endif"
        self.assertEqual(TemplateSet.fill(template, {"Synopsis": ""}), " none")
        self.assertEqual(TemplateSet.fill(template, {"Synopsis": "  "}), " none")
        self.assertEqual(TemplateSet.fill(template, {"Synopsis": "s"}), "has")

    def test_nesting_requires_every_condition(self):
        template = ("$if(Synopsis)A:$if(Metadata.Status = draft)draft"
                    "$else final$endif$else no synopsis$endif")
        self.assertEqual(
            TemplateSet.fill(
                template, {"Synopsis": "s", "Metadata.Status": "draft"}),
            "A:draft")
        self.assertEqual(
            TemplateSet.fill(
                template, {"Synopsis": "", "Metadata.Status": "draft"}),
            " no synopsis")
        self.assertEqual(
            TemplateSet.fill(
                template, {"Synopsis": "s", "Metadata.Status": "final"}),
            "A: final")

    def test_surrounding_text_survives_its_markers(self):
        filled = TemplateSet.fill(
            "Before $if(Title) middle $endif after", {"Title": "T"})
        self.assertEqual(filled, "Before  middle  after")

    def test_stray_markers_degrade_without_leaking(self):
        self.assertNotIn("$endif", TemplateSet.fill("A $endif B", {}))
        self.assertNotIn("$else", TemplateSet.fill("A $else B", {}))
        # An unmatched $if keeps its branch state to the end of the
        # template and still consumes its own marker.
        self.assertNotIn("$if(", TemplateSet.fill("$if(Missing)gone $endif t", {}))

    def test_conditionals_run_through_an_export(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        project = Project.create(root / "Novel", "Novel")
        self.addCleanup(project.close)
        draft = project.documents()[0]
        chapter = project.add_document("Chapter One", draft.id, "chapter")
        project.update_document(chapter.id, synopsis="Opens the book.")
        set_dir = create_template_set("braced", root).directory
        (set_dir / "folder.txt").write_text(
            "$if(Synopsis)[${Synopsis}] $endif${Title}: ${Content}",
            encoding="utf-8",
        )
        templates = TemplateSet.load("braced", set_dir)
        destination = root / "book.txt"
        project.export_text(destination, None, templates)
        exported = destination.read_text(encoding="utf-8")
        self.assertIn("[Opens the book.] Chapter One:", exported)


class WikiLinkCompileTests(unittest.TestCase):
    """Link resolution and strip policies at compile time."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = Project.create(self.root / "Novel", "Novel")
        self._build()

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def _build(self):
        draft = self.project.documents()[0]
        self.book = self.project.add_document("Book", draft.id, "folder")
        self.chapter_one = self.project.add_document(
            "Chapter One", self.book.id, "chapter")
        self.scene = self.project.add_document(
            "First Light", self.chapter_one.id, "scene")
        self.other_book = self.project.add_document(
            "Book Two", draft.id, "folder")
        self.chapter_two = self.project.add_document(
            "Chapter Two", self.other_book.id, "chapter")
        self.project.write_content(
            self.chapter_one.id,
            "<p>See [[Chapter Two]] and [[Missing Scene]]. "
            "Also [[Chapter]].</p>")
        self.project.write_content(
            self.scene.id, "<p>Back to [[First Light]] roots.</p>")
        self.project.write_content(
            self.chapter_two.id, "<p>Forward again.</p>")

    def _export_html(self, selected_ids=None) -> str:
        destination = self.root / "book.html"
        self.project.export_html(destination, selected_ids)
        return destination.read_text(encoding="utf-8")

    def _export_text(self, selected_ids=None) -> str:
        destination = self.root / "book.txt"
        self.project.export_text(destination, selected_ids)
        return destination.read_text(encoding="utf-8")

    def _export_docx_xml(self, selected_ids=None) -> str:
        destination = self.root / "book.docx"
        self.project.export_docx(destination, selected_ids)
        with zipfile.ZipFile(destination) as archive:
            return archive.read("word/document.xml").decode()

    def test_published_link_becomes_an_html_anchor(self):
        exported = self._export_html()
        # The root and book folders are exported units, so Chapter Two is
        # a later unit than before — the link resolves to that unit.
        self.assertIn("<a href='#unit-5'>Chapter Two</a>", exported)
        self.assertIn("<a id='unit-5'></a>", exported)
        self.assertNotIn("[[", exported)

    def test_published_link_points_at_the_right_epub_section(self):
        destination = self.root / "book.epub"
        self.project.export_epub(destination)
        with zipfile.ZipFile(destination) as archive:
            sections = [name for name in archive.namelist()
                        if name.startswith("OEBPS/section-")]
            joined = "".join(archive.read(name).decode() for name in sections)
        self.assertIn("href='section-5.xhtml'", joined)

    def test_unpublished_link_keeps_its_words_by_default(self):
        exported = self._export_html({self.chapter_one.id})
        # Every unresolved link in the sentence survives as plain words;
        # the scene's self-link still resolves (its unit is exported).
        self.assertIn(
            "See Chapter Two and Missing Scene. Also Chapter.", exported)
        self.assertNotIn("[[Chapter Two]]", exported)
        self.assertIn("<a href='#unit-1'>First Light</a>", exported)

    def test_strip_policy_removes_the_words(self):
        self.project.set_book_settings(self.book.id, {"links": "strip"})
        exported = self._export_html({self.chapter_one.id})
        self.assertNotIn("Chapter Two", exported)
        self.assertNotIn("Missing Scene", exported)

    def test_keep_as_note_marks_the_missing_target(self):
        self.project.set_book_settings(
            self.book.id, {"links": "keep-as-note"})
        exported = self._export_html({self.chapter_one.id})
        self.assertIn("Chapter Two (not in this export)", exported)
        self.assertIn("Missing Scene (not in this export)", exported)

    def test_project_default_applies_without_a_book_override(self):
        self.project.set_setting("link_policy", "strip")
        exported = self._export_html({self.chapter_one.id})
        self.assertNotIn("Chapter Two", exported)
        self.assertNotIn("Missing Scene", exported)

    def test_book_override_beats_the_project_default(self):
        self.project.set_setting("link_policy", "strip")
        self.project.set_book_settings(
            self.book.id, {"links": "plain-text"})
        # Project strip alone would have removed the words; the book's
        # plain-text override keeps them.
        exported = self._export_html({self.chapter_one.id})
        self.assertIn("See Chapter Two", exported)

    def test_broken_and_ambiguous_links_never_become_anchors(self):
        # [[Missing Scene]] is broken, [[Chapter]] is ambiguous
        # (substring of both chapter titles) — the editor only ever
        # offers those as picker candidates, so compile keeps the words
        # and makes no link whose text is exactly the ambiguous token.
        exported = self._export_html()
        self.assertIn("Missing Scene", exported)
        self.assertIn("Also Chapter.", exported)
        self.assertNotIn(">Chapter</a>", exported)

    def test_text_export_follows_the_policy(self):
        plain = self._export_text({self.chapter_one.id})
        self.assertIn("Chapter Two", plain)
        self.project.set_book_settings(self.book.id, {"links": "strip"})
        stripped = self._export_text({self.chapter_one.id})
        self.assertNotIn("Chapter Two", stripped)

    def test_docx_published_links_are_real_hyperlinks(self):
        document_xml = self._export_docx_xml()
        self.assertIn(f'w:anchor="d{self.chapter_two.id}"', document_xml)
        self.assertIn(f'w:name="d{self.chapter_two.id}"', document_xml)
        self.assertIn("Chapter Two", document_xml)

    def test_docx_unpublished_link_follows_the_policy(self):
        plain_xml = self._export_docx_xml({self.chapter_one.id})
        self.assertIn("Missing Scene", plain_xml)
        self.assertNotIn(f'w:anchor="d{self.chapter_two.id}"', plain_xml)
        self.project.set_book_settings(self.book.id, {"links": "strip"})
        stripped_xml = self._export_docx_xml({self.chapter_one.id})
        self.assertNotIn("Missing Scene", stripped_xml)

    def test_header_and_footer_links_are_rewritten(self):
        set_dir = create_template_set("linked", self.root).directory
        (set_dir / "project_header.txt").write_text(
            "Jump to [[Chapter Two]].", encoding="utf-8")
        (set_dir / "project_footer.txt").write_text(
            "Missing [[No Such Page]] here.", encoding="utf-8")
        templates = TemplateSet.load("linked", set_dir)
        destination = self.root / "book.txt"
        self.project.export_text(destination, None, templates)
        exported = destination.read_text(encoding="utf-8")
        self.assertIn("Jump to Chapter Two.", exported)
        self.assertIn("Missing No Such Page here.", exported)
        self.assertNotIn("[[", exported)

    def test_html_header_link_points_at_a_unit(self):
        set_dir = create_template_set("linked-html", self.root).directory
        (set_dir / "project_header.html").write_text(
            "<p>Jump to [[Chapter Two]].</p>", encoding="utf-8")
        templates = TemplateSet.load("linked-html", set_dir)
        destination = self.root / "book.html"
        self.project.export_html(destination, None, templates)
        exported = destination.read_text(encoding="utf-8")
        self.assertIn("<a href='#unit-5'>Chapter Two</a>", exported)


if __name__ == "__main__":
    unittest.main()
