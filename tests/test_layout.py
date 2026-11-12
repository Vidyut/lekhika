"""The filesystem layout: names, the YAML subset, and the tree round trip.

Order is the thing most worth pinning here. A folder's manifest *is* its
order; nothing sorts children by name, because the author's scene titles run
`1---`, `2---`, … `10---`, which sort as text `1, 10, 2`.
"""

import tempfile
import unittest
from pathlib import Path

from lekhika import layout
from lekhika.layout import Node


class SlugTests(unittest.TestCase):
    def test_titles_from_the_real_binder(self):
        # Real binder titles: numbers, dashes, spaces, apostrophes.
        self.assertEqual(layout.slug("3 - Two People Meet"),
                         "3-Two-People-Meet")
        self.assertEqual(layout.slug("1---A Room"), "1-A-Room")
        self.assertEqual(layout.slug("The daughter of the moon"),
                         "The-daughter-of-the-moon")
        self.assertEqual(layout.slug("3---Chapter 5 for Sector 7, Lowlands"),
                         "3-Chapter-5-for-Sector-7-Lowlands")

    def test_no_spaces_ever(self):
        for title in ("Chapter One", "a  b", " lead", "trail ", "  "):
            self.assertNotIn(" ", layout.slug(title))

    def test_illegal_characters_go(self):
        self.assertEqual(layout.slug("A/B\\C:D*E?F"), "A-B-C-D-E-F")
        self.assertEqual(layout.slug("Scene\twith\ttabs"), "Scene-with-tabs")

    def test_letters_in_any_script_survive(self):
        self.assertEqual(layout.slug("Émile's Ångström"), "Émile-s-Ångström")
        self.assertEqual(layout.slug("अंत"), "अंत")

    def test_edges_and_limits(self):
        self.assertEqual(layout.slug(""), "Untitled")
        self.assertEqual(layout.slug("   "), "Untitled")
        self.assertEqual(layout.slug("...draft..."), "draft")
        self.assertEqual(layout.slug("- - -"), "Untitled")
        self.assertEqual(len(layout.slug("x" * 200)), layout.MAX_LENGTH)

    def test_reserved_names_are_avoided(self):
        self.assertEqual(layout.slug("NUL"), "_NUL")
        self.assertEqual(layout.slug("com1"), "_com1")
        self.assertEqual(layout.slug("comic"), "comic")

    def test_collisions_are_suffixed_case_insensitively(self):
        taken = set()
        names = []
        for title in ("Book", "book", "BOOK"):
            name = layout.unique_name(layout.slug(title), taken)
            taken.add(name.casefold())
            names.append(name)
        self.assertEqual(names, ["Book", "book-1", "BOOK-2"])


class FrontmatterTests(unittest.TestCase):
    def test_round_trip_with_nesting_and_types(self):
        values = {
            "title": "Scene: the meeting",
            "id": "abc123",
            "type": "Scene",
            "word_target": 1200,
            "synopsis": "",
            "metadata": {"PoV": "One", "Status": "Draft", "Count": 3},
        }
        block = layout.dump_frontmatter(values)
        parsed, rest = layout.parse_frontmatter(block + "<p>prose</p>")
        self.assertEqual(parsed["title"], "Scene: the meeting")
        self.assertEqual(parsed["word_target"], 1200)
        self.assertEqual(parsed["metadata"], {"PoV": "One", "Status": "Draft",
                                              "Count": 3})
        self.assertEqual(rest, "<p>prose</p>")

    def test_lists_round_trip(self):
        block = layout.dump_frontmatter({"children": ["a.html", "b/", "c.html"]})
        parsed, _rest = layout.parse_frontmatter(block)
        self.assertEqual(parsed["children"], ["a.html", "b/", "c.html"])

    def test_text_that_looks_typed_stays_text(self):
        """A value the writer meant as text must never come back typed.

        The old writer quoted a hand-picked list of words, so a count of
        ``007`` survived but ``2026`` became an int and ``007`` became 7 —
        metadata rewritten by a round trip nobody asked for
        (docs/STORAGE.md, "Manuscripts are precious").
        """
        values = {"metadata": {"Count": "007", "Word": "2026",
                               "Ratio": "6.10", "Flag": "true",
                               "Version": "1.0", "Id": "-3"}}
        parsed, _rest = layout.parse_frontmatter(layout.dump_frontmatter(values))
        self.assertEqual(parsed["metadata"], values["metadata"])

    def test_real_numbers_and_booleans_keep_their_type(self):
        """Print geometry is inches and a mirror flag: 6.14 must come back
        as 6.14 and True as True, not as their str() rendering."""
        values = {"page_setup": {"width": 6.14, "gutter": 0.0,
                                 "mirror": True, "trim": "6.14x9.21"}}
        parsed, _rest = layout.parse_frontmatter(layout.dump_frontmatter(values))
        self.assertEqual(parsed["page_setup"], values["page_setup"])

    def test_an_int_still_reads_as_an_int(self):
        parsed, _rest = layout.parse_frontmatter(
            layout.dump_frontmatter({"word_target": 1200}))
        self.assertEqual(parsed["word_target"], 1200)
        self.assertIsInstance(parsed["word_target"], int)

    def test_a_file_without_frontmatter_is_all_content(self):
        parsed, rest = layout.parse_frontmatter("<p>just prose</p>")
        self.assertEqual(parsed, {})
        self.assertEqual(rest, "<p>just prose</p>")

    def test_hand_written_frontmatter_is_tolerated(self):
        text = ("---\n"
                "# my own note\n"
                "title: Hand written\n"
                "type: Chapter\n"
                "metadata:\n"
                "  Status: Final\n"
                "---\n"
                "<p>body</p>")
        parsed, rest = layout.parse_frontmatter(text)
        self.assertEqual(parsed["title"], "Hand written")
        self.assertEqual(parsed["metadata"], {"Status": "Final"})
        self.assertEqual(rest, "<p>body</p>")


class ProseTests(unittest.TestCase):
    """What counts as words of its own — the two false positives from the
    author's real project, and the case that should still count."""

    QT_EMPTY = ('<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN">\n'
                '<html><head><style type="text/css">\n'
                'p, li { white-space: pre-wrap; }\n'
                'hr { height: 1px; border-width: 0; }\n'
                '</style></head><body style="font-size:12pt;">'
                '<p style="-qt-paragraph-type:empty;"><br /></p></body></html>')

    def test_an_empty_document_is_not_prose(self):
        """Qt writes a whole file whose only text is its own stylesheet."""
        self.assertFalse(layout.has_prose(self.QT_EMPTY, "Manuscript"))

    def test_a_title_typed_as_a_heading_is_not_prose(self):
        self.assertFalse(layout.has_prose("<p><b>How the Light Breaks</b></p>",
                                          "How the Light Breaks"))

    def test_a_numbered_title_matches_its_own_heading(self):
        self.assertFalse(layout.has_prose("<p><b>\\1. Rebirth in Ruin</b></p>",
                                          "1. Rebirth in Ruin"))

    def test_real_words_are_prose(self):
        self.assertTrue(layout.has_prose("<p>The monsoon came late.</p>",
                                         "The monsoon"))
        self.assertTrue(layout.has_prose(
            "<p><b>Prologue</b></p><p>Then the rain came.</p>", "Prologue"))

    def test_a_title_only_container_gets_no_index_html(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "P.lekhika"
            node = Node(id="x", title="Prologue", kind="folder",
                        content="<p><b>Prologue</b></p>",
                        children=[Node(id="y", title="Scene", content="<p>t</p>")])
            stats = layout.write_tree(root, [node])
            self.assertEqual(stats["indexes"], 0)


class TreeRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "Project.lekhika"
        # Deliberately *not* in alphabetical order, so a test can prove that
        # nothing sorts by name: these would sort 1, 10, 2.
        self.scenes = [
            Node(id="id-m", title="2---The monsoon", content="<p>m</p>",
                 type="Scene"),
            Node(id="id-a", title="10---A Letter Arrives", content="<p>a</p>",
                 type="Scene"),
            Node(id="id-z", title="1---One leaves", content="<p>z</p>",
                 type="Scene", metadata={"PoV": "One"}),
        ]
        self.chapter = Node(id="ch-1", title="Chapter One", kind="folder",
                            type="Chapter", content="<p>chapter words</p>",
                            children=self.scenes)
        self.notes = Node(id="note-1", title="Research notes", kind="folder",
                          type="Research")
        self.book = Node(id="book-1", title="A Book", kind="folder",
                         type="Book",
                         children=[self.chapter, self.notes])

    def tearDown(self):
        self.temporary.cleanup()

    def _write(self):
        return layout.write_tree(self.root, [self.book], title="Test")

    def test_write_then_read_back(self):
        stats = self._write()
        self.assertEqual(stats["folders"], 3)      # book, chapter, notes
        self.assertEqual(stats["documents"], 3)
        self.assertEqual(stats["indexes"], 1)      # the chapter has prose

        nodes, project = layout.load_tree(self.root)
        self.assertEqual(project["title"], "Test")
        self.assertEqual(len(nodes), 1)
        book = nodes[0]
        self.assertEqual(book.title, "A Book")
        self.assertEqual(book.type, "Book")
        self.assertEqual(book.id, "book-1")
        self.assertEqual([child.title for child in book.children],
                         ["Chapter One", "Research notes"])

    def test_order_is_preserved_not_alphabetical(self):
        self._write()
        book = layout.load_tree(self.root)[0][0]
        chapter = book.children[0]
        self.assertEqual([scene.title for scene in chapter.children],
                         ["2---The monsoon", "10---A Letter Arrives",
                          "1---One leaves"])
        # …which is emphatically not what sorting the filenames would give.
        names = sorted(layout.slug(scene.title) for scene in self.scenes)
        self.assertNotEqual(
            names,
            [layout.slug(scene.title) for scene in chapter.children])

    def test_a_container_with_prose_keeps_it_as_index_html(self):
        self._write()
        index = (self.root / "A-Book/Chapter-One/index.html")
        self.assertTrue(index.exists())
        chapter = layout.load_tree(self.root)[0][0].children[0]
        self.assertEqual(chapter.content, "<p>chapter words</p>")

    def test_a_container_without_prose_has_no_index(self):
        self._write()
        self.assertFalse((self.root / "A-Book/Research-notes/"
                          "index.html").exists())

    def test_metadata_and_ids_survive(self):
        self._write()
        chapter = layout.load_tree(self.root)[0][0].children[0]
        scene = [scene for scene in chapter.children
                if scene.title == "1---One leaves"][0]
        self.assertEqual(scene.metadata, {"PoV": "One"})
        self.assertEqual(scene.id, "id-z")

    def test_the_manifest_is_the_order(self):
        """Reorder the manifest by hand and the tree follows — proof that
        filenames are not consulted for order."""
        self._write()
        manifest = self.root / "A-Book/Chapter-One/.lekhika/folder.yaml"
        text = manifest.read_text(encoding="utf-8")
        text = text.replace("  - 1-One-leaves.html\n", "")
        text = text.replace("children:\n",
                            "children:\n  - 1-One-leaves.html\n")
        manifest.write_text(text, encoding="utf-8")

        chapter = layout.load_tree(self.root)[0][0].children[0]
        self.assertEqual([scene.title for scene in chapter.children],
                         ["1---One leaves", "2---The monsoon",
                          "10---A Letter Arrives"])

    def test_a_file_added_from_outside_lands_last(self):
        self._write()
        folder = self.root / "A-Book/Chapter-One"
        (folder / "By-hand.html").write_text(
            "---\ntitle: By hand\ntype: Scene\n---\n<p>typed elsewhere</p>",
            encoding="utf-8")

        chapter = layout.load_tree(self.root)[0][0].children[0]
        self.assertEqual(chapter.children[-1].title, "By hand")
        self.assertEqual(chapter.children[-1].content, "<p>typed elsewhere</p>")

    def test_a_renamed_file_keeps_its_identity(self):
        """The id lives in the frontmatter, so a rename by hand loses nothing."""
        self._write()
        folder = self.root / "A-Book/Chapter-One"
        (folder / "1-One-leaves.html").rename(folder / "Renamed-by-hand.html")

        chapter = layout.load_tree(self.root)[0][0].children[0]
        renamed = [scene for scene in chapter.children
                   if scene.title == "1---One leaves"]
        self.assertEqual(len(renamed), 1)
        self.assertEqual(renamed[0].id, "id-z")
        self.assertEqual(renamed[0].metadata, {"PoV": "One"})

    def test_colliding_titles_in_one_folder_get_suffixed(self):
        scene = [scene for scene in self.scenes
                if scene.title == "1---One leaves"][0]
        twin = Node(id="id-z2", title="1---One leaves", content="<p>again</p>")
        chapter = Node(id="ch-2", title="Chapter", kind="folder",
                       children=[scene, twin])
        layout.write_tree(self.root, [chapter])
        names = sorted(entry.name for entry in
                       (self.root / "Chapter").iterdir()
                       if entry.suffix == ".html")
        self.assertEqual(names, ["1-One-leaves-1.html", "1-One-leaves.html"])

    def test_a_numbered_file_never_numbers_its_title(self):
        """The suffix is a filesystem detail. Two documents the writer titled
        the same stay titled the same (docs/STORAGE.md): the binder shows the
        frontmatter, and the writer never meets the slug."""
        twin = Node(id="id-dup", title="1---One leaves", content="<p>again</p>")
        chapter = Node(id="ch-3", title="Chapter", kind="folder",
                       children=[self.scenes[2], twin])
        layout.write_tree(self.root, [chapter])
        back = layout.load_tree(self.root)[0][0].children
        self.assertEqual([node.title for node in back],
                         ["1---One leaves", "1---One leaves"])
        self.assertEqual([node.id for node in back], ["id-z", "id-dup"])

    def test_a_blank_title_stays_blank(self):
        """A writer who left the title blank keeps it blank. It is never
        filled in from the file's name — and in particular never from the
        *numbered* name, which once made two blank titles read back as
        ``Untitled`` and ``Untitled-1``, handing the writer the slug."""
        blank_doc = Node(id="id-blank", title="", content="<p>x</p>")
        blank_twin = Node(id="id-blank2", title="", content="<p>y</p>")
        blank_folder = Node(id="ch-blank", title="", kind="folder",
                            children=[blank_doc, blank_twin])
        layout.write_tree(self.root, [blank_folder])
        # The files themselves are named, because they have to be.
        folder = self.root / "Untitled"
        self.assertEqual(sorted(entry.name for entry in folder.iterdir()
                                if entry.suffix == ".html"),
                         ["Untitled-1.html", "Untitled.html"])
        back = layout.load_tree(self.root)[0][0]
        self.assertEqual(back.title, "")
        self.assertEqual([node.title for node in back.children], ["", ""])

    def test_repeated_point_of_view_titles_are_normal(self):
        """Fiction is actually written this way: chapters titled for whose
        head the reader is inside. MMC, FMC, MMC, FMC — two titles for a
        whole book, repeated endlessly. So duplicates are the common case,
        not a defect, and the writer must never be made to feel one.

        The risk with repeated titles is not the filename, it is identity:
        six chapters sharing two titles must stay six distinct chapters, in
        the writer's order, each holding its own child. Only the directories
        differ.
        """
        povs = ["MMC", "FMC"] * 3
        book = Node(id="bk", title="The Book", kind="folder", children=[
            Node(id=f"ch-{i}", title=pov, kind="folder", type="Chapter",
                 children=[Node(id=f"sc-{i}", title=pov, kind="document",
                                content=f"<p>scene {i}</p>")])
            for i, pov in enumerate(povs)])
        layout.write_tree(self.root, [book])

        back = layout.load_tree(self.root)[0][0].children
        self.assertEqual([node.title for node in back], povs)
        self.assertEqual([node.id for node in back],
                         [f"ch-{i}" for i in range(6)])
        # Each chapter kept its own child, not a neighbour's.
        self.assertEqual([node.children[0].id for node in back],
                         [f"sc-{i}" for i in range(6)])
        # Only the directories were numbered, six names for two titles.
        self.assertEqual(sorted(entry.name for entry
                                in (self.root / "The-Book").iterdir()
                                if entry.is_dir()
                                and not entry.name.startswith(".")),
                         ["FMC", "FMC-1", "FMC-2",
                          "MMC", "MMC-1", "MMC-2"])

    def test_a_file_with_no_frontmatter_is_named_after_its_file(self):
        """The other half of the rule: absent is not empty. A file another
        tool wrote has no title at all, so its name is the only name it has."""
        bare = self.root / "Bare-Scene.html"
        self.root.mkdir(parents=True, exist_ok=True)
        bare.write_text("<p>words</p>", encoding="utf-8")
        back = layout.load_tree(self.root)[0][0]
        self.assertEqual(back.title, "Bare-Scene")


if __name__ == "__main__":
    unittest.main()
