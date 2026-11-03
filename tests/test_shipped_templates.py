"""The sets that ship with Lekhak (docs/TEMPLATES.md §1.1): `default`,
`short story`, `Book` and `nothing` — seeded by discovery without a folder,
duplicable into the project with their own files, and standing for the whole
range the system can express: the built-in look with an inert config, a
variant that dresses units and states paper, a whole book dressed by Type
name, and silence (§3).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lekhak.project import Project
from lekhak.templates import (
    BUILTIN_HTML,
    BUILTIN_TEXT,
    SHIPPED_FILES,
    SHIPPED_TEMPLATE_SETS,
    SLOTS,
    TemplateSet,
    copy_template_set,
    discover_template_sets,
)


class ShippedSetsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_the_three_shipped_sets_need_no_folder_anywhere(self):
        self.assertEqual(SHIPPED_TEMPLATE_SETS,
                         ("default", "short story", "Book", "nothing"))
        for name in SHIPPED_TEMPLATE_SETS:
            self.assertIsNone(TemplateSet.builtin(name).directory)
            self.assertFalse(TemplateSet.builtin(name).editable)

    def test_discovery_seeds_them_before_any_folder(self):
        (self.root / "templates" / "Editorial").mkdir(parents=True)
        names = [s.name for s in discover_template_sets(self.root)]
        self.assertEqual(names, ["default", "short story", "Book", "nothing",
                                 "Editorial"])

    def test_a_folder_named_after_a_shipped_set_is_listed_beside_it(self):
        # The `default` rule (§1.1) generalised: the shipped set keeps
        # its slot — the top of the list is always Lekhak's own — and the
        # project's copy appears after the three as a set of its own,
        # under its folder name, so its files are never hidden.
        directory = self.root / "templates" / "Short Story"
        directory.mkdir(parents=True)
        (directory / "folder.html").write_text("<p>${Content}</p>",
                                                encoding="utf-8")
        sets = discover_template_sets(self.root)
        self.assertEqual([s.name for s in sets],
                         ["default", "short story", "Book", "nothing",
                          "Short Story"])
        self.assertIsNone(sets[1].directory)       # the shipped one, still
        self.assertEqual(sets[4].directory, directory)
        self.assertEqual(sets[4].slot("folder"), "<p>${Content}</p>")

    def test_default_is_the_builtin_fragments_with_an_inert_config(self):
        default = TemplateSet.builtin("default")
        # Byte for byte what an account with no template has always had —
        # and every line of its config is a comment, so nothing is said:
        # page setup (1.15) still answers for the paper.
        self.assertEqual(default.html_slots, dict(BUILTIN_HTML))
        self.assertEqual(default.text_slots, dict(BUILTIN_TEXT))
        self.assertEqual(default.docx_config, {})
        self.assertIsNone(default.docx_sample)
        self.assertFalse(default.prints_nothing)

    def test_short_story_dresses_a_single_document_and_declares_a4(self):
        short = TemplateSet.builtin("short story")
        # One live line, deliberately: the whole of §3.1's precedence rule
        # demonstrated in a single config value.
        self.assertEqual(short.docx_config, {"page size": "A4"})
        # Named after the Type such a piece carries, so a document in a
        # folder this set dresses takes the face by name (1.18).
        self.assertIn('class="story"', short.document_slot("document"))
        self.assertEqual(short.document_slot("document", html=False),
                         "${Title}\n${Author}\n\n${Content}")
        self.assertFalse(short.prints_nothing)

    def test_no_export_is_one_empty_file(self):
        silent = TemplateSet.builtin("nothing")
        # The whole set: one file whose body is empty, and its notes. No
        # fragment is emptied to say it, so changing one's mind costs one
        # deletion, not rebuilding a vocabulary.
        self.assertEqual(sorted(SHIPPED_FILES["nothing"]),
                         ["nothing.html"])
        self.assertTrue(SHIPPED_FILES["nothing"]["nothing.html"].endswith(
            "<body></body>"))
        self.assertIn("prints nothing",
                      SHIPPED_FILES["nothing"]["nothing.html"])
        self.assertTrue(silent.prints_nothing)
        # Nothing results from it: every slot answers empty in both modes,
        # and so does a document's own dress.
        for name in SLOTS:
            self.assertEqual(silent.slot(name), "")
            self.assertEqual(silent.slot(name, html=False), "")
        self.assertEqual(silent.document_slot("no-title"), "")
        self.assertEqual(silent.document_slot("no-title", html=False), "")
        self.assertEqual(silent.docx_config, {})

    def test_a_shipped_set_is_named_from_the_shipped_list_only(self):
        with self.assertRaises(ValueError):
            TemplateSet.builtin("mine")

    def test_book_names_every_face_after_the_type_it_dresses(self):
        book = TemplateSet.builtin("Book")
        # The naming convention, as a shipped set: a folder typed Book,
        # chapters typed Chapter, scenes typed Scene, and front matter
        # typed after its own page (docs/MODEL.md).
        faces = book.document_template_faces()
        for type_name in ("Chapter", "Scene", "Title", "Frontmatter"):
            self.assertEqual(faces.get(type_name), f"{type_name}.html")
        self.assertIn('class="part"', book.slot("folder"))
        scene = book.document_slot("Scene")
        self.assertIn('class="scene"', scene)
        # A scene has no heading unless the writer adds one to its
        # Metadata: the PoV line is inside a conditional (2.2).
        self.assertIn("$if(Metadata.PoV)", scene)
        self.assertNotIn("${Title}", scene)
        # The set states no paper: page setup in the inspector answers,
        # which is where the writer sets trim and margins.
        self.assertEqual(book.docx_config, {})
        self.assertTrue(book.css().strip())


class BookSetAssignmentTests(unittest.TestCase):
    """What the Book set is for: type the things, and the names assign
    them (1.18) — the folder its set, each document its face."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "Novel"
        self.project = Project.create(self.root, "Novel")
        self.book = self.project.documents()[0]
        self.chapter = self.project.add_document("Chapter One", self.book.id,
                                                 "chapter")
        self.scene = self.project.add_document("Opening", self.chapter.id,
                                               "scene")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def test_the_faces_reach_an_export_by_name_alone(self):
        # No assignment is made anywhere: the set is used whole, and the
        # folder slot dresses the chapter while the scene face dresses
        # the scene — but only a document whose Type says Scene gets the
        # scene's face, so both cases are checked through the resolver.
        self.project.write_content(self.scene.id, "<p>First light.</p>")
        book = TemplateSet.builtin("Book")
        self.assertIn('class="scene"', book.document_slot("Scene"))
        self.assertIn("${Position}", book.document_slot("Chapter"))
        self.assertIn('class="part"', book.slot("folder"))

        destination = self.root / "book.html"
        self.project.export_html(destination, None, book)
        exported = destination.read_text(encoding="utf-8")
        self.assertIn('class="part"', exported)
        self.assertIn("First light.", exported)

    def test_the_type_alone_dresses_the_export(self):
        # Naming files after Types (1.18) is read at export as well as in
        # the sidebar: nothing was ever stored on these documents, and
        # each one still wears the face its Type is named after.
        self.project.set_metadata(self.chapter.id, {"Type": "Chapter"})
        self.project.set_metadata(self.scene.id, {"Type": "Scene"})
        self.project.write_content(self.chapter.id, "<p>Late prose.</p>")
        self.project.write_content(self.scene.id, "<p>First light.</p>")

        destination = self.root / "typed.html"
        self.project.export_html(destination, None, TemplateSet.builtin("Book"))
        exported = destination.read_text(encoding="utf-8")

        self.assertIn('<section class="scene">', exported)  # Scene's face
        self.assertIn('class="chapter-number"', exported)   # Chapter's
        # A chapter is its number: the title lives on the folder's part
        # page and in the contents page, not above every chapter.
        self.assertNotIn("<h1>Chapter One</h1>", exported)

    def test_a_scene_prints_a_pov_only_when_its_metadata_has_one(self):
        self.project.set_metadata(self.scene.id, {"Type": "Scene"})
        self.project.write_content(self.scene.id, "<p>First light.</p>")
        book = TemplateSet.builtin("Book")

        plain = self.root / "plain.html"
        self.project.export_html(plain, None, book)
        self.assertNotIn('class="pov"', plain.read_text(encoding="utf-8"))

        # Replaces, not merges: the Type stays on, the PoV joins it.
        self.project.set_metadata(self.scene.id,
                                  {"Type": "Scene", "PoV": "Maren"})
        named = self.root / "pov.html"
        self.project.export_html(named, None, book)
        self.assertIn('<p class="pov">Maren</p>',
                      named.read_text(encoding="utf-8"))

    def test_a_stored_face_still_outranks_the_type(self):
        self.project.set_metadata(self.chapter.id, {"Type": "Chapter"})
        self.project.write_content(self.chapter.id, "<p>Late prose.</p>")
        self.project.update_document(self.chapter.id, doc_template="Title")

        destination = self.root / "chosen.html"
        self.project.export_html(destination, None, TemplateSet.builtin("Book"))

        self.assertIn('class="title-page"',
                      destination.read_text(encoding="utf-8"))


class ShippedDuplicateTests(unittest.TestCase):
    """Duplicating is how a writer reads these sets: what lands on disk has
    to be exactly what the shipped entry already was."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def files(self, created):
        return sorted(path.relative_to(created.directory).as_posix()
                      for path in created.directory.rglob("*")
                      if path.is_file())

    def test_duplicating_default_writes_its_config_notes_and_example(self):
        created = copy_template_set(TemplateSet.builtin("default"),
                                    "default copy", self.root)
        self.assertEqual(created.directory,
                         self.root / "templates" / "default copy")
        config = (created.directory / "print-config.txt").read_text(
            encoding="utf-8")
        self.assertTrue(config.startswith("Docx"))
        self.assertEqual(created.docx_config, {})     # still all comments
        sample = created.directory / "examples" / "formatting.docx"
        self.assertIn("examples/formatting.docx", self.files(created))
        from docx import Document

        section = Document(str(sample)).sections[0]
        self.assertAlmostEqual(section.page_width.inches, 8.27, places=1)

    def test_duplicating_short_story_writes_its_own_fragments(self):
        created = copy_template_set(TemplateSet.builtin("short story"),
                                    "my stories", self.root)
        self.assertEqual(created.slot("folder"),
                         TemplateSet.builtin("short story").slot("folder"))
        self.assertEqual(created.docx_config, {"page size": "A4"})
        # The default's fragments still answer wherever the variant is
        # silent: load falls back exactly as it does for a folder.
        # The example sample rides along (§3.1 option 3)...
        self.assertTrue(
            (created.directory / "examples" / "formatting.docx").is_file())

    def test_duplicating_no_export_writes_its_one_file_and_its_notes(self):
        created = copy_template_set(TemplateSet.builtin("nothing"),
                                    "quiet", self.root)
        # The copy is the shipped entry, not a seeded vocabulary: one empty
        # file and the notes, nothing else (§3).
        self.assertEqual(self.files(created), ["nothing.html"])
        self.assertTrue(created.prints_nothing)
        self.assertEqual(created.slot("folder"), "")
        self.assertNotIn("examples/formatting.docx", self.files(created))

    def test_a_folder_copy_is_recursive(self):
        # The `documents/` shelf and examples/ used to be lost here, which
        # made Duplicate a liar about what a set contains.
        source = copy_template_set(TemplateSet.builtin("default"),
                                   "original", self.root)
        forked = copy_template_set(source, "forked", self.root)
        self.assertEqual(self.files(forked), self.files(source))
        self.assertTrue(
            (forked.directory / "examples" / "formatting.docx").is_file())


class ShippedExportTests(unittest.TestCase):
    """What each shipped set actually writes (§3's silence rule included)."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = Project.create(self.root / "Novel", "Novel")
        draft = self.project.documents()[0]
        self.book = draft.id
        self.chapter = self.project.add_document(
            "Chapter One", draft.id, "chapter")
        self.project.write_content(self.chapter.id, "<p>Chapter opener.</p>")
        self.scene = self.project.add_document(
            "First Light", self.chapter.id, "scene")
        self.project.write_content(self.scene.id, "<p>First light prose.</p>")
        # A second chapter: the first is above any page start there is to
        # speak of, so chapter-openers are only visible from the second.
        self.chapter_two = self.project.add_document(
            "Chapter Two", self.book, "chapter")
        self.project.write_content(self.chapter_two.id,
                                   "<p>Second opener.</p>")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def set_book_page_setup(self):
        """A real print trim on the book (1.15) — 6 x 9, not A4."""
        self.project.set_setting(
            "page_setup",
            json.dumps({self.book: {
                "width": 6.0, "height": 9.0, "top": 0.75, "bottom": 0.75,
                "inside": 0.75, "outside": 0.5}}))

    def export_docx(self, name):
        destination = self.root / f"{name}.docx"
        self.project.export_docx(destination, None,
                                 TemplateSet.builtin(name))
        from docx import Document

        return Document(str(destination))

    def test_the_shipped_default_exports_exactly_as_the_builtin(self):
        # Shipped `default` is not a look-alike of the builder: it is it.
        with_default = self.export_docx("default")
        self.set_book_page_setup()
        section = with_default.sections[0]
        self.assertAlmostEqual(section.page_width.inches, 8.27, places=2)
        body = "\n".join(p.text for p in with_default.paragraphs)
        self.assertIn("Chapter One", body)
        self.assertIn("First light prose.", body)

    def test_its_inert_config_leaves_the_book_trim_alone(self):
        self.set_book_page_setup()
        section = self.export_docx("default").sections[0]
        # Every config line is a comment, so page setup still answers.
        self.assertAlmostEqual(section.page_width.inches, 6.0)
        self.assertAlmostEqual(section.top_margin.inches, 0.75)

    def test_short_story_puts_its_a4_in_front_of_the_book_trim(self):
        self.set_book_page_setup()
        section = self.export_docx("short story").sections[0]
        self.assertAlmostEqual(section.page_width.inches, 8.27, places=2)
        self.assertAlmostEqual(section.page_height.inches, 11.69, places=2)

    def test_a4_stays_an_export_and_never_becomes_a_print_book(self):
        # No page setup anywhere: `short story` states A4, and A4 is where
        # an export starts — stating it does not promote chapters to
        # recto (the honed trim_stated rule: a trim other than the
        # baseline's own, not any trim at all).
        document = self.export_docx("short story")
        self.assertEqual(len(document.sections), 1)
        self.assertFalse(any(
            section.start_type is not None
            and ("ODD" in str(section.start_type)
                 or "EVEN" in str(section.start_type))
            for section in document.sections))

    def test_the_book_trim_still_takes_chapters_recto(self):
        # Page setup (1.15) has always said so, and the shipped default —
        # which says nothing — leaves that answer standing (§3.1).
        self.set_book_page_setup()
        document = self.export_docx("default")
        self.assertTrue(any(
            section.start_type is not None
            and "ODD" in str(section.start_type)
            for section in document.sections))

    def test_no_export_writes_an_empty_document(self):
        document = self.export_docx("nothing")
        self.assertEqual([p.text for p in document.paragraphs if p.text.strip()],
                         [])

    def test_no_export_writes_an_empty_text_file(self):
        destination = self.root / "quiet.txt"
        self.project.export_text(destination, None,
                                 TemplateSet.builtin("nothing"))
        self.assertEqual(destination.read_text(encoding="utf-8"), "")

    def test_no_export_keeps_the_html_wrapper_with_nothing_inside(self):
        destination = self.root / "quiet.html"
        self.project.export_html(destination, None,
                                 TemplateSet.builtin("nothing"))
        text = destination.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("<!doctype html>"))
        self.assertIn("<title>", text)
        self.assertNotIn("Chapter One", text)
        self.assertNotIn("First light prose.", text)

    def test_no_export_leaves_an_epub_with_an_empty_body(self):
        destination = self.root / "quiet.epub"
        self.project.export_epub(destination, None,
                                 TemplateSet.builtin("nothing"))
        import zipfile

        with zipfile.ZipFile(destination) as archive:
            names = archive.namelist()
            # The archive is whole — package document and nav — because
            # silence empties content, not structure.
            self.assertIn("OEBPS/content.opf", names)
            self.assertIn("OEBPS/nav.xhtml", names)
            pages = [name for name in names
                     if name.startswith("OEBPS/section-")]
            self.assertTrue(pages)
            text = b"".join(archive.read(name)
                            for name in pages).decode("utf-8")
        # Every page still exists, titled, with an empty body: the
        # wrappers are the format's, not the template's to supply.
        self.assertIn("<body></body>", text)
        self.assertNotIn("First light prose.", text)


if __name__ == "__main__":
    unittest.main()