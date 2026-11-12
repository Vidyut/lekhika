import tempfile
import unittest
from pathlib import Path

from lekhak.project import Project, config_geometry
from lekhak.templates import (
    TemplateSet, copy_template_file, create_template_set,
    discover_template_sets, document_template_stem, parse_docx_config,
    validate_slot, write_config_line,
)


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = Project.create(self.root / "Novel", "Novel")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def _chapter_with_scenes(self, titles):
        draft = self.project.documents()[0]
        chapter = self.project.add_document("Chapter One", draft.id, "chapter")
        self.project.write_content(chapter.id, "<p>Chapter opener.</p>")
        scenes = []
        for title in titles:
            scene = self.project.add_document(title, chapter.id, "scene")
            self.project.write_content(scene.id, f"<p>{title} text.</p>")
            scenes.append(scene)
        return chapter, scenes

    def test_builtin_text_export_is_title_and_content(self):
        chapter, scenes = self._chapter_with_scenes(["Alpha", "Beta"])
        destination = self.root / "book.txt"
        self.project.export_text(destination)
        exported = destination.read_text(encoding="utf-8")
        self.assertIn("Chapter One", exported)
        self.assertIn("Chapter opener.", exported)
        self.assertIn("Alpha text.", exported)

    def test_builtin_html_export_wraps_sections(self):
        self._chapter_with_scenes(["Alpha"])
        destination = self.root / "book.html"
        self.project.export_html(destination)
        exported = destination.read_text(encoding="utf-8")
        self.assertIn('class="chapter"', exported)
        self.assertIn("<h2>Chapter One</h2>", exported)
        self.assertIn('class="document"', exported)

    def test_the_children_of_a_container_answer_to_one_word(self):
        # One word, one name: `children`. The older names are not names
        # any more — a template coder cannot afford to be approximate.
        chapter, scenes = self._chapter_with_scenes(["Alpha"])
        set_dir = create_template_set("children", self.project.root).directory
        (set_dir / "folder.txt").write_text(
            "NEW[${children}] OLD[${Scenes}]", encoding="utf-8")
        templates = TemplateSet.load("children", set_dir)

        destination = self.root / "book.txt"
        self.project.export_text(destination, None, templates)
        exported = destination.read_text(encoding="utf-8")

        self.assertIn("NEW[Alpha", exported)
        # The old name is no name at all: it prints nothing.
        self.assertIn("OLD[]", exported)
        # And the editor says so when the file is saved.
        self.assertIn("unknown placeholder ${Scenes}",
                      validate_slot("${Scenes}"))

    def test_a_type_answers_to_its_own_word(self):
        # A type is one exact word, used as it stands: no plurals, no
        # guessing. The type list in the sidebar is the truth.
        chapter, scenes = self._chapter_with_scenes(["Alpha"])
        self.project.set_metadata(scenes[0].id, {"Type": "leaf"})
        set_dir = create_template_set("leaves", self.project.root).directory
        (set_dir / "folder.txt").write_text(
            "ONE[${children-leaf}] OTHER[${children-leaves}]", encoding="utf-8")
        templates = TemplateSet.load("leaves", set_dir)

        destination = self.root / "book.txt"
        self.project.export_text(destination, None, templates)
        exported = destination.read_text(encoding="utf-8")

        self.assertIn("ONE[Alpha", exported)
        # The plural is not the type: it finds nothing.
        self.assertIn("OTHER[]", exported)

    def test_a_doubled_dollar_prints_a_placeholder_rather_than_expanding(self):
        # The escape the docs promise: a document can talk about a
        # placeholder instead of being one.
        chapter, scenes = self._chapter_with_scenes(["Alpha"])
        scene = scenes[0]
        project = self.project
        project.write_content(scene.id, "<p>Write $${Title} here.</p>")

        destination = self.root / "book.html"
        project.export_html(destination, None)
        exported = destination.read_text(encoding="utf-8")

        self.assertIn("Write ${Title} here.", exported)
        self.assertNotIn("$${Title}", exported)

    def test_a_value_is_not_expanded_again_after_it_is_placed(self):
        # The prose carries its own ${...} markers; they are expanded
        # once, when the export lifts the content, and not again when
        # the value is put into the fragment.
        chapter, scenes = self._chapter_with_scenes(["Alpha"])
        scene = scenes[0]
        project = self.project
        project.write_content(scene.id, "<p>$${Title}</p>")

        destination = self.root / "book.html"
        project.export_html(destination, None)
        exported = destination.read_text(encoding="utf-8")

        self.assertIn("${Title}", exported)
        self.assertNotIn("Alpha</p>", exported)

    def test_the_project_name_is_not_the_document_title(self):
        # ${Project} is the project's own name, the same for every
        # document in it; ${Title} is the document being dressed. A
        # project may hold several books, so the two are not the same.
        self.project.set_setting("title", "The Whole Work")
        chapter, scenes = self._chapter_with_scenes(["Alpha"])
        set_dir = create_template_set("named", self.project.root).directory
        (set_dir / "document.txt").write_text(
            "P[${Project}] T[${Title}]", encoding="utf-8")
        templates = TemplateSet.load("named", set_dir)

        destination = self.root / "book.txt"
        self.project.export_text(destination, None, templates)
        exported = destination.read_text(encoding="utf-8")

        self.assertIn("P[The Whole Work]", exported)
        self.assertIn("T[Alpha]", exported)

    def test_a_type_placeholder_lists_only_that_type(self):
        chapter, scenes = self._chapter_with_scenes(["Alpha", "Beta"])
        self.project.set_metadata(scenes[0].id, {"Type": "leaf"})
        self.project.set_metadata(scenes[1].id, {"Type": "Vignette"})
        set_dir = create_template_set("kinds", self.project.root).directory
        (set_dir / "folder.txt").write_text(
            "ALL[${children}] LEAF[${children-leaf}] "
            "VIGNETTE[${children-vignette}]", encoding="utf-8")
        templates = TemplateSet.load("kinds", set_dir)

        destination = self.root / "book.txt"
        self.project.export_text(destination, None, templates)
        exported = destination.read_text(encoding="utf-8")

        self.assertIn("ALL[Alpha", exported)
        self.assertIn("Beta text.] LEAF[", exported)   # both in ALL
        self.assertIn("LEAF[Alpha", exported)
        self.assertNotIn("LEAF[Beta", exported)
        self.assertIn("VIGNETTE[Beta", exported)
        self.assertNotIn("VIGNETTE[Alpha", exported)

    def test_the_children_are_dressed_in_their_own_templates_in_order(self):
        # `${children}` is every child a container holds, each one in the
        # template that applies to it, in binder order — not alphabetical.
        chapter, scenes = self._chapter_with_scenes(["Zulu", "Alpha"])
        for scene in scenes:
            self.project.set_metadata(scene.id, {"Type": "Scene"})
        set_dir = create_template_set("own", self.project.root).directory
        (set_dir / "folder.txt").write_text(
            "PARENT[${children}]", encoding="utf-8")
        (set_dir / "documents" / "Scene.txt").write_text(
            "SCENE<${Title}>", encoding="utf-8")
        templates = TemplateSet.load("own", set_dir)

        destination = self.root / "book.txt"
        self.project.export_text(destination, None, templates)
        exported = destination.read_text(encoding="utf-8")

        self.assertIn("PARENT[SCENE<Zulu>\nSCENE<Alpha>]", exported)

    def test_counters_and_running_word_total(self):
        chapter, scenes = self._chapter_with_scenes(["Alpha", "Beta"])
        set_dir = create_template_set("counters", self.project.root).directory
        (set_dir / "document.txt").write_text(
            "[ch${ChapterNumber}.sc${SceneNumber}.tot${WordsTotal}] ${Title}\n${Content}",
            encoding="utf-8",
        )
        templates = TemplateSet.load("counters", set_dir)
        destination = self.root / "book.txt"
        self.project.export_text(destination, None, templates)
        exported = destination.read_text(encoding="utf-8")
        self.assertIn("[ch1.sc1.", exported)
        self.assertIn("[ch1.sc2.", exported)
        first_total = int(exported.split("tot")[1].split("]")[0])
        second_total = int(exported.rsplit("tot", 1)[1].split("]")[0])
        self.assertGreater(second_total, first_total)

    def test_project_set_shadows_user_name_and_is_discoverable(self):
        create_template_set("my-set", self.project.root)
        names = [s.name for s in discover_template_sets(self.project.root)]
        self.assertIn("default", names)
        self.assertIn("my-set", names)

    def test_create_set_rejects_bad_names(self):
        with self.assertRaises(ValueError):
            create_template_set("../escape", self.project.root)

    def test_project_header_and_footer_wrap_text_export(self):
        set_dir = create_template_set("dressed", self.project.root).directory
        (set_dir / "project_header.txt").write_text("== BEGIN ==", encoding="utf-8")
        (set_dir / "project_footer.txt").write_text("== END ==", encoding="utf-8")
        templates = TemplateSet.load("dressed", set_dir)
        self._chapter_with_scenes(["Alpha"])
        destination = self.root / "book.txt"
        self.project.export_text(destination, None, templates)
        exported = destination.read_text(encoding="utf-8")
        self.assertTrue(exported.startswith("== BEGIN =="))
        self.assertIn("== END ==", exported)

    def test_epub_units_are_chapters_with_embedded_scenes(self):
        chapter, scenes = self._chapter_with_scenes(["Alpha", "Beta"])
        import zipfile
        destination = self.root / "book.epub"
        self.project.export_epub(destination)
        with zipfile.ZipFile(destination) as archive:
            sections = [n for n in archive.namelist() if n.startswith("OEBPS/section-")]
            # The root folder is a unit now; the chapter is its own unit
            # with both scenes embedded inside it.
            self.assertEqual(len(sections), 2)
            pages = [archive.read(n).decode() for n in sections]
        chapter_page = next(p for p in pages if "Alpha text." in p)
        self.assertIn("Beta text.", chapter_page)

class CopyTemplateFileTests(unittest.TestCase):
    """One file out into a set of its own (§1.1).

    The author set both halves directly: the copy appears in the
    Templates folder as a *sibling* — "Not Templates > Default" — and it
    holds just that one file, everything else falling back to Lekhak's
    built-ins, which is what lets a set carry a single template and still
    export every format (§1.3).
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project_root = self.root / "Novel"
        self.project_root.mkdir()
        self.templates_root = self.project_root / "templates"
        self.source = create_template_set("Book", self.project_root)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative: str, text: str) -> Path:
        path = self.source.directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def files(self, created) -> list[str]:
        return sorted(path.relative_to(created.directory).as_posix()
                      for path in created.directory.rglob("*")
                      if path.is_file())

    def test_the_copy_is_a_sibling_holding_only_that_file(self):
        self.write("folder.html", "<p>mine</p>")
        created = copy_template_file(self.source, "folder.html",
                                     "Chapter only", self.project_root)

        self.assertEqual(created.directory.parent, self.templates_root)
        self.assertNotEqual(created.directory, self.source.directory)
        self.assertEqual(self.files(created), ["folder.html"])

    def test_the_copy_says_its_own_file_and_falls_back_for_the_rest(self):
        self.write("folder.html", "<p>mine</p>")
        created = copy_template_file(self.source, "folder.html",
                                     "Chapter only", self.project_root)

        self.assertEqual(created.slot("folder"), "<p>mine</p>")
        # Nothing else was copied, so everything else is the built-in
        # answer — one template is a complete set, not a broken one.
        self.assertEqual(created.slot("document"),
                         TemplateSet.builtin().slot("document"))
        self.assertEqual(created.document_slot("no-title", html=True),
                         TemplateSet.builtin().document_slot("no-title", html=True))

    def test_a_file_from_a_format_folder_keeps_its_format(self):
        # Copied where it stood: `epub/` copied out refines EPUB exports
        # of the new set rather than quietly becoming every format's.
        self.write("epub/folder.html", "<p>epub only</p>")
        created = copy_template_file(self.source, "epub/folder.html",
                                     "Epub chapter", self.project_root)
    
        self.assertEqual(self.files(created), ["epub/folder.html"])
        self.assertEqual(created.for_format("epub").slot("folder"),
                         "<p>epub only</p>")
        self.assertEqual(created.slot("folder"),
                         TemplateSet.builtin().slot("folder"))

    def test_a_shelf_file_can_be_copied_out_too(self):
        created = copy_template_file(self.source, "documents/no-title.html",
                                     "Bare dress", self.project_root)
        self.assertEqual(self.files(created), ["documents/no-title.html"])
        self.assertEqual(created.document_slot("no-title", html=True),
                         TemplateSet.builtin().document_slot("no-title", html=True))

    def test_a_name_that_is_taken_refuses_without_touching_it(self):
        create_template_set("Taken", self.project_root)
        self.write("folder.html", "<p>mine</p>")
        with self.assertRaises(FileExistsError):
            copy_template_file(self.source, "folder.html", "Taken",
                               self.project_root)
        # The set that was there is untouched by the refused copy.
        self.assertNotEqual(
            TemplateSet.load("Taken", self.templates_root / "Taken")
            .slot("folder"), "<p>mine</p>")

    def test_a_file_that_is_not_there_leaves_no_half_made_set(self):
        with self.assertRaises(FileNotFoundError):
            copy_template_file(self.source, "notes.html", "Nope",
                               self.project_root)
        self.assertFalse((self.templates_root / "Nope").exists())

    def test_a_path_outside_the_set_is_refused(self):
        for relative in ("../outside.html", "/etc/passwd", ""):
            with self.assertRaises(ValueError, msg=relative):
                copy_template_file(self.source, relative, "Bad",
                                   self.project_root)
        self.assertFalse((self.templates_root / "Bad").exists())


class BlockProxyTests(unittest.TestCase):
    """A block placeholder in an element of its own (docs/TEMPLATES.md §3.2).

    The element is a styling proxy, not a paragraph: filling replaces it,
    so the document's own blocks are never nested inside a <p>.
    """

    CONTENT = "<p>One.</p><p>Two.</p>"

    def fill(self, template, **values):
        values.setdefault("Content", self.CONTENT)
        values.setdefault("ToC", "<li><a href='#a'>A</a></li>")
        return TemplateSet.fill(template, values)

    def test_a_bare_p_proxy_is_replaced_by_the_contents_own_blocks(self):
        self.assertEqual(self.fill("<section>\n<p>${Content}</p>\n</section>"),
                         "<section>\n" + self.CONTENT + "\n</section>")

    def test_a_p_proxy_keeps_its_formatting_but_not_its_tag(self):
        # A <p> cannot hold paragraphs, so the attributes move to a <div>.
        self.assertEqual(
            self.fill('<p class="first">${Content}</p>'),
            '<div class="first">' + self.CONTENT + "</div>")

    def test_a_div_proxy_is_kept_as_it_stands(self):
        self.assertEqual(
            self.fill('<div class="content">${Content}</div>'),
            '<div class="content">' + self.CONTENT + "</div>")

    def test_whitespace_inside_the_proxy_is_ignored(self):
        self.assertEqual(
            self.fill("<div>\n  ${Content}\n</div>"),
            "<div>" + self.CONTENT + "</div>")

    def test_entries_is_a_block_placeholder_too(self):
        self.assertEqual(
            self.fill("<ol>${ToC}</ol>"),
            "<ol><li><a href='#a'>A</a></li></ol>")

    def test_a_heading_is_a_real_element_not_a_proxy(self):
        # <h1>${Title}</h1>: Title is text, so the text is replaced and the
        # heading — with whatever formatting it carries — stays.
        self.assertEqual(self.fill("<h1>${Title}</h1>", Title="One"),
                         "<h1>One</h1>")

    def test_a_scalar_in_a_element_is_not_a_proxy(self):
        # ${Title} is text, not markup: its element is a real paragraph.
        self.assertEqual(self.fill('<p class="x">${Title}</p>', Title="Chapter"),
                         '<p class="x">Chapter</p>')

    def test_content_mixed_with_text_is_an_inline_substitution(self):
        self.assertEqual(self.fill("<p>Before ${Content} after</p>"),
                         "<p>Before " + self.CONTENT + " after</p>")

    def test_the_shipped_chapter_fragment_fills_without_nesting(self):
        filled = TemplateSet.fill(
            '<section class="chapter">\n<h1>${Title}</h1>\n'
            '<div class="content">${Content}</div>\n</section>',
            {"Title": "One", "Content": self.CONTENT})
        self.assertIn('<div class="content"><p>One.</p><p>Two.</p></div>', filled)
        self.assertNotIn("<p><p>", filled)


class WordCountTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def test_word_count_cached_on_write(self):
        document = self.project.documents()[0]
        self.project.write_content(document.id, "<p>One two three</p>")
        self.assertEqual(self.project.documents()[0].word_count, 3)

    def test_backfill_runs_once_for_old_projects(self):
        import sqlite3
        document = self.project.documents()[0]
        self.project.write_content(document.id, "<p>Four five six seven</p>")
        self.project.close()
        connection = sqlite3.connect(self.project.db_path)
        connection.execute("UPDATE documents SET word_count=0")
        connection.execute("DELETE FROM settings WHERE key='word_count_backfill'")
        connection.commit()
        connection.close()
        self.project.open()
        self.assertEqual(self.project.documents()[0].word_count, 4)

    def test_word_rollups_sum_descendants(self):
        draft = self.project.documents()[0]
        chapter = self.project.add_document("Chapter", draft.id, "chapter")
        scene_a = self.project.add_document("A", chapter.id, "scene")
        scene_b = self.project.add_document("B", chapter.id, "scene")
        self.project.write_content(scene_a.id, "<p>one two</p>")
        self.project.write_content(scene_b.id, "<p>one two three four</p>")
        rollups = self.project.word_rollups()
        self.assertEqual(rollups[scene_a.id], 2)
        self.assertEqual(rollups[chapter.id], 6)
        self.assertEqual(rollups[draft.id], 6)

    def test_metadata_entries_all_matches_per_document_reads(self):
        draft = self.project.documents()[0]
        chapter = self.project.add_document("Chapter", draft.id, "chapter")
        self.project.set_metadata(draft.id, {"POV": "Maya"})
        self.project.set_metadata(chapter.id, {"Status": "Draft"}, {"Status": "choice"},
                                  {"Status": ["Draft", "Final"]})
        everything = self.project.metadata_entries_all()
        self.assertEqual(everything[draft.id], self.project.metadata_entries(draft.id))
        self.assertEqual(everything[chapter.id], self.project.metadata_entries(chapter.id))
        self.assertEqual(self.project.metadata_keys(), ["POV", "Status"])


class FragmentVocabularyTests(unittest.TestCase):
    """Fragment selection by content shape, and the writer's own names
    (docs/TEMPLATES.md §3): a file with an .html extension is a fragment,
    the built-in names are the vocabulary the compile consults by shape,
    and any other name dresses a document that asks for it."""

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

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def _set(self, name="written"):
        return create_template_set(name, self.project.root)

    def test_every_html_file_is_a_fragment(self):
        created = self._set()
        (created.directory / "short_story.html").write_text(
            "<article>${Title}: ${Content}</article>", encoding="utf-8")
        # Anything that is not .html/.txt is support material (§3), so a
        # README or a sample the writer dropped in is never consulted.
        (created.directory / "README").write_text("notes", encoding="utf-8")
        (created.directory / "sample-page.docx").write_bytes(b"PK-fake")

        loaded = TemplateSet.load("written", created.directory)

        self.assertEqual(loaded.slot("short_story"),
                         "<article>${Title}: ${Content}</article>")
        self.assertEqual(loaded.slot("README"), "")
        self.assertEqual(loaded.slot("sample-page"), "")
        # The vocabulary the compile consults by shape is still there.
        self.assertIn("<section", loaded.slot("folder"))

    def test_a_document_asks_for_a_writer_named_fragment(self):
        created = self._set()
        (created.directory / "short_story.html").write_text(
            "<article><h2>${Title}</h2>${Content}</article>",
            encoding="utf-8")
        templates = TemplateSet.load("written", created.directory)
        self.project.update_document(self.scene.id,
                                     doc_template="short_story")

        destination = self.root / "book.html"
        self.project.export_html(destination, {self.scene.id}, templates)
        exported = destination.read_text(encoding="utf-8")

        self.assertIn("<article><h2>First Light</h2>", exported)
        self.assertIn("First light prose.", exported)

    def test_the_documents_shelf_wins_over_a_root_fragment(self):
        created = self._set()
        (created.directory / "short_story.html").write_text(
            "root", encoding="utf-8")
        shelf = created.directory / "documents"
        shelf.mkdir(exist_ok=True)
        (shelf / "short_story.html").write_text("shelf", encoding="utf-8")

        loaded = TemplateSet.load("written", created.directory)

        self.assertEqual(loaded.document_slot("short_story"), "shelf")

    def test_a_root_fragment_reaches_a_document_without_a_shelf(self):
        created = self._set()
        (created.directory / "short_story.html").write_text(
            "root", encoding="utf-8")
        loaded = TemplateSet.load("written", created.directory)

        self.assertEqual(loaded.document_slot("short_story"), "root")

    def test_chrome_is_not_offered_as_a_documents_dress(self):
        created = self._set()
        (created.directory / "short_story.html").write_text(
            "story", encoding="utf-8")
        loaded = TemplateSet.load("written", created.directory)

        names = loaded.document_template_names()
        self.assertIn("short_story", names)      # the writer's, pickable
        self.assertIn("no-title", names)             # the built-in shelf
        self.assertNotIn("project_header", names)
        self.assertNotIn("toc_page", names)

    def test_a_template_of_document_fragments_alone_is_complete(self):
        """A writer of short stories may own only document fragments, and
        that dresses everything: the shape rule picks chapter and scene,
        and the first scene takes its own fragment when there is one."""
        created = self._set()
        for suffix in (".html", ".txt"):
            (created.directory / ("folder" + suffix)).write_text(
                "CHAPTER<${Content}>${children}", encoding="utf-8")
            (created.directory / ("document" + suffix)).write_text(
                "SCENE<${Content}>", encoding="utf-8")
        templates = TemplateSet.load("written", created.directory)
        second = self.project.add_document(
            "Second Light", self.chapter.id, "scene")
        self.project.write_content(second.id, "<p>Second light prose.</p>")

        destination = self.root / "book.txt"
        self.project.export_text(destination, None, templates)
        exported = destination.read_text(encoding="utf-8")

        self.assertIn("CHAPTER<", exported)
        self.assertIn("SCENE<First light prose.>", exported)
        self.assertIn("SCENE<Second light prose.>", exported)

    def test_the_assigned_set_supplies_the_documents_named_fragment(self):
        """The practical flow (§3): the folder is assigned a template and
        a document inside asks for one of its fragments by name — no
        export-time choice needed, because the assignment is what dresses
        the folder."""
        created = self._set()
        (created.directory / "short_story.html").write_text(
            "<article>${Content}</article>", encoding="utf-8")
        self.project.set_book_settings(self.project.documents()[0].id,
                                       {"template": "written"})
        self.project.update_document(self.scene.id, doc_template="short_story")

        destination = self.root / "book.html"
        self.project.export_html(destination)          # whole project
        exported = destination.read_text(encoding="utf-8")

        self.assertIn("<article><p>First light prose.</p></article>",
                      exported)

    def test_a_fragment_nobody_asks_for_costs_nothing(self):
        created = self._set()
        (created.directory / "unused.html").write_text(
            "SHOULD NOT APPEAR", encoding="utf-8")
        templates = TemplateSet.load("written", created.directory)

        destination = self.root / "book.txt"
        self.project.export_text(destination, None, templates)

        self.assertNotIn("SHOULD NOT APPEAR",
                         destination.read_text(encoding="utf-8"))


class DocumentTemplateFaceTests(unittest.TestCase):
    """The Document template box's own vocabulary (§1.2): a name carries
    its filename, one name covers both variants of a fragment, and what
    the writer types saves as the name the compile asks for."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = Project.create(self.root / "Novel", "Novel")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def _set(self):
        return create_template_set("written", self.project.root)

    def test_a_filename_and_its_stem_are_one_name(self):
        self.assertEqual(document_template_stem("folder.html"), "folder")
        self.assertEqual(document_template_stem("folder.txt"), "folder")
        self.assertEqual(document_template_stem("Bare.HTML"), "Bare")
        self.assertEqual(document_template_stem(" scene "), "scene")
        self.assertEqual(document_template_stem("fancy name.html"),
                         "fancy name")

    def test_a_shelf_path_is_the_same_name(self):
        self.assertEqual(document_template_stem("documents/no-title.html"),
                         "no-title")

    def test_names_that_nothing_could_ask_for_are_empty(self):
        self.assertEqual(document_template_stem(""), "")
        self.assertEqual(document_template_stem("Default (set's own slot)"),
                         "")
        self.assertEqual(document_template_stem("a.b.html"), "")
        self.assertEqual(document_template_stem("no/slash.html"), "")

    def test_a_slot_is_asked_for_by_filename_or_by_name(self):
        created = self._set()
        templates = TemplateSet.load("written", created.directory)

        self.assertEqual(templates.document_slot("folder.html"),
                         templates.document_slot("folder"))
        # A name no set carries answers nothing, whatever its face —
        # the caller falls back to the document's own default slot.
        self.assertIsNone(templates.document_slot("news.html"))
        self.assertIsNone(templates.document_slot("documents/news.html"))

    def test_faces_carry_the_real_file_of_each_name(self):
        created = self._set()
        (created.directory / "news.txt").write_text("news", encoding="utf-8")
        (created.directory / "notes.md").write_text("notes",
                                                    encoding="utf-8")
        templates = TemplateSet.load("written", created.directory)

        faces = templates.document_template_faces()
        # Plain text is the face only when it is all the set has.
        self.assertEqual(faces.get("news"), "news.txt")
        # The shelf keeps each name as the file it is.
        self.assertEqual(faces.get("no-title"), "no-title.html")
        # The set's own slot files are the "Default" row, not faces.
        self.assertNotIn("folder", faces)
        self.assertNotIn("document", faces)
        # Print configuration, chrome and files that are not fragments
        # are not faces either.
        self.assertNotIn("print-config", faces)
        self.assertNotIn("project_header", faces)
        self.assertNotIn("toc_page", faces)
        self.assertNotIn("notes", faces)
        # The names list is this same vocabulary, sorted.
        self.assertEqual(templates.document_template_names(),
                         sorted(faces))

    def test_a_shipped_set_answers_with_the_same_faces(self):
        faces = TemplateSet.builtin().document_template_faces()
        self.assertEqual(faces.get("no-title"), "no-title.html")


class ConfigLineWriteTests(unittest.TestCase):
    """write_config_line — the preset rows' writer (§1.2): one key, one
    line at the block's end where the parser reads it last, and every
    other word of the file carried through untouched."""

    def test_an_existing_line_is_replaced_not_doubled(self):
        text = write_config_line(
            "# head\nPage size: A4\n<p>${Content}</p>",
            "page size", "Page size: 6,9")

        self.assertEqual(text, "# head\nPage size: 6,9\n<p>${Content}</p>")

    def test_a_key_the_file_has_not_said_is_appended_at_the_end(self):
        text = write_config_line("# head\nHeader: ,{Title},", "margins",
                                 "Margins (L,T,R,B/I,T,O,B): 0.5, 0.25, "
                                 "0.25, 0.25")

        lines = text.split("\n")
        self.assertEqual(lines[0], "# head")
        self.assertIn("Header: ,{Title},", lines)
        self.assertEqual(lines[-1],
                         "Margins (L,T,R,B/I,T,O,B): 0.5, 0.25, 0.25, 0.25")

    def test_two_lines_saying_the_key_leave_exactly_the_new_one(self):
        # A later duplicate wins the parser, so a stale twin could never
        # be outranked by a pick — losing both is what makes the pick
        # the last word.
        text = write_config_line("# a\nPage size: 6,9\n# note\nPage size: A4",
                                 "page size", "Page size: 5,8")

        self.assertEqual(text.count("Page size"), 1)
        self.assertIn("Page size: 5,8", text)
        self.assertIn("# a", text)
        self.assertIn("# note", text)        # the file's own words stay

    def test_a_headless_text_gains_a_block(self):
        self.assertEqual(write_config_line("Page size: A4", "page size",
                                           "Page size: 5,8"),
                         "#\nPage size: 5,8")
        self.assertEqual(write_config_line("", "page size",
                                           "Page size: 5,8"),
                         "#\nPage size: 5,8")

    def test_a_docx_marker_file_keeps_its_marker_first(self):
        text = write_config_line("Docx\nFoo: 1", "page size",
                                 "Page size: 5,8")

        self.assertEqual(text.split("\n")[0], "Docx")
        self.assertIn("Foo: 1", text)
        self.assertTrue(text.split("\n")[-1].startswith("Page size"))

    def test_a_written_pair_reads_back_as_the_same_geometry(self):
        head = write_config_line(
            "# presets", "page size",
            "Page size (inches - horizontal, vertical): 5.06,7.81")
        head = write_config_line(
            head, "margins",
            "Margins (L,T,R,B/I,T,O,B): 0.5, 0.25, 0.25, 0.25")

        overlay = config_geometry(parse_docx_config(head))
        self.assertEqual(overlay["trim"], "5.06x7.81")
        self.assertEqual(
            (overlay["inside"], overlay["top"],
             overlay["outside"], overlay["bottom"]),
            (0.5, 0.25, 0.25, 0.25))


if __name__ == "__main__":
    unittest.main()
