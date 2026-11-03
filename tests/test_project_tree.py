"""The Project adapter for the filesystem layout (docs/STORAGE.md).

`Project.to_nodes()` turns the binder into layout Nodes; `write_tree` writes
the folder tree and `read_tree` reads it back. This is the migration's
foundation: a project must survive the round trip exactly — every id, title,
role, synopsis, target, metadata key and word of prose — before anything is
trusted to it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lekhak.layout import has_prose
from lekhak.project import Project


def _flatten(nodes):
    """Every node, depth-first, in binder order."""
    out = []
    for node in nodes:
        out.append(node)
        out.extend(_flatten(node.children))
    return out


class ProjectTreeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name) / "Series.lekhak"
        self.project = Project.create(root, "Series")
        # A book folder -> a chapter with its own prose and two scenes,
        # plus an empty chapter and a loose scene with metadata — the shapes
        # the author's real binder mixes together.
        self.book = self.project.add_document("Book One", kind="folder")
        self.chapter = self.project.add_document(
            "Chapter One", self.book.id, "chapter")
        self.project.write_content(self.chapter.id, "<p>Chapter words.</p>")
        self.project.update_document(self.chapter.id,
                                     synopsis="Where it begins.")
        self.scene_a = self.project.add_document(
            "1---One leaves", self.chapter.id, "scene")
        self.project.write_content(self.scene_a.id, "<p>One left.</p>")
        self.project.set_metadata(self.scene_a.id, {"PoV": "One", "Count": "3"})
        self.scene_b = self.project.add_document(
            "10---Someone follows", self.chapter.id, "scene")
        self.project.write_content(self.scene_b.id, "<p>Someone followed.</p>")
        self.empty = self.project.add_document(
            "Research", self.book.id, "folder")
        self.loose = self.project.add_document("Loose scene", kind="scene")
        self.project.write_content(self.loose.id, "<p>On its own.</p>")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def test_containers_are_folders_and_leaves_are_documents(self):
        nodes = {node.title: node for node in _flatten(self.project.to_nodes())}
        self.assertEqual(nodes["Book One"].kind, "folder")
        # A chapter with scenes is a directory whatever its stored kind.
        self.assertEqual(nodes["Chapter One"].kind, "folder")
        self.assertEqual(nodes["Research"].kind, "folder")
        # A scene is a leaf document.
        self.assertEqual(nodes["1---One leaves"].kind, "document")
        self.assertEqual(nodes["Loose scene"].kind, "document")

    def test_type_becomes_the_nodes_own_role(self):
        nodes = {node.title: node for node in _flatten(self.project.to_nodes())}
        # Two base roles now (1.17): a folder is a Folder, everything
        # else a Document until the writer says otherwise. The physical
        # kind still travels beside it.
        self.assertEqual(nodes["Book One"].type, "Folder")
        self.assertEqual(nodes["Chapter One"].type, "Document")
        self.assertEqual(nodes["1---One leaves"].type, "Document")
        self.assertEqual(nodes["1---One leaves"].kind, "document")
        # Type is the node's field, so it must not also sit in metadata.
        self.assertNotIn("Type", nodes["1---One leaves"].metadata)

    def test_order_follows_binder_order_not_names(self):
        nodes = {node.title: node for node in _flatten(self.project.to_nodes())}
        self.assertEqual([child.title for child in nodes["Chapter One"].children],
                         ["1---One leaves", "10---Someone follows"])

    def test_write_then_read_back_is_exact(self):
        before = _flatten(self.project.to_nodes())
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "tree"
            stats = self.project.write_tree(destination)
            after = _flatten(self.project.read_tree(destination)[0])

            # Folders: the seeded Draft, Book One, Chapter One, Research.
            # Documents (leaves): the two scenes and the loose scene. One
            # container (the chapter) has prose of its own -> one index.html.
            self.assertEqual(stats["folders"], 4)
            self.assertEqual(stats["documents"], 3)
            self.assertEqual(stats["indexes"], 1)

            self.assertEqual(len(before), len(after))
            for old, new in zip(before, after):
                container = old.kind == "folder" or bool(old.children)
                expected = (old.content if not container
                            or has_prose(old.content, old.title) else "")
                self.assertEqual(new.id, old.id, old.title)
                self.assertEqual(new.title, old.title)
                self.assertEqual(new.type, old.type, old.title)
                self.assertEqual(new.synopsis, old.synopsis, old.title)
                self.assertEqual(new.word_target, old.word_target, old.title)
                self.assertEqual(new.metadata, old.metadata, old.title)
                self.assertEqual(new.content, expected, old.title)

    def test_metadata_value_types_are_text(self):
        """A metadata value that looks like a number comes back as text, so
        the model's str -> str contract holds."""
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "tree"
            self.project.write_tree(destination)
            nodes = {node.title: node
                     for node in _flatten(self.project.read_tree(destination)[0])}
            self.assertEqual(nodes["1---One leaves"].metadata["Count"], "3")

    def test_the_root_order_lives_in_the_project_manifest(self):
        """The root has no folder.yaml, so its order is in project.yaml."""
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "tree"
            self.project.write_tree(destination)
            nodes, _project = self.project.read_tree(destination)
            self.assertEqual([node.title for node in nodes],
                             ["Draft", "Book One", "Loose scene"])

    def test_container_synopsis_survives_without_an_index(self):
        """A container with no prose of its own still keeps its synopsis and
        metadata: they live in folder.yaml, not only in index.html."""
        self.project.update_document(self.empty.id, synopsis="Scratch work.")
        self.project.set_metadata(self.empty.id, {"Status": "Draft"})
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "tree"
            self.project.write_tree(destination)
            # No prose -> no index.html for this container.
            self.assertFalse(
                (destination / "Book-One/Research/index.html").exists())
            nodes = {node.title: node
                     for node in _flatten(self.project.read_tree(destination)[0])}
            self.assertEqual(nodes["Research"].synopsis, "Scratch work.")
            self.assertEqual(nodes["Research"].metadata, {"Status": "Draft"})

    def test_print_geometry_travels_with_the_folder(self):
        """A field the flat schema had and an earlier tree had no home for
        (scripts/storage_roundtrip.py): folder-scoped, so it lives in
        folder.yaml — which is what makes a copied book a book with its
        own trim (docs/STORAGE.md). The template mark that used to travel
        beside it is gone: a template is a folder under templates/ now."""
        self.project.set_page_setup(self.book.id, {
            "trim": "6.14x9.21", "width": 6.14, "height": 9.21,
            "top": 1.0, "bottom": 1.0, "inside": 1.0, "outside": 1.0,
            "mirror": True, "gutter": 0.25,
        })
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "tree"
            self.project.write_tree(destination)
            manifest = (destination / "Book-One" / ".lekhak" / "folder.yaml"
                        ).read_text(encoding="utf-8")
            self.assertIn("page_setup:", manifest)
            self.assertNotIn("is_template", manifest)   # the mark is gone

            nodes = {node.title: node
                     for node in _flatten(self.project.read_tree(destination)[0])}
            self.assertEqual(nodes["Book One"].page_setup, {
                "trim": "6.14x9.21", "width": 6.14, "height": 9.21,
                "top": 1.0, "bottom": 1.0, "inside": 1.0, "outside": 1.0,
                "mirror": True, "gutter": 0.25,
            })
            # A folder that never had any stays plainly unset, not falsy junk.
            self.assertIsNone(nodes["Research"].page_setup)

    def test_a_hand_broken_geometry_still_loads(self):
        """Validation is clean_page_setup's job, not the loader's: junk is
        rejected where everything else rejects it, never dropped on the way
        in (which would hide it from the writer who put it there)."""
        from lekhak.layout import Node, load_tree, write_tree
        folder = Node(id="f1", title="Book", kind="folder",
                      page_setup={"width": "nonsense"},
                      children=[Node(id="d1", title="Scene", kind="document")])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "tree"
            write_tree(root, [folder], title="T")
            nodes = load_tree(root)[0]
            self.assertEqual(nodes[0].page_setup, {"width": "nonsense"})
            from lekhak.project import clean_page_setup
            self.assertIsNone(clean_page_setup(nodes[0].page_setup))


class FlattenPromotedBooksTests(unittest.TestCase):
    """The one-time fold of a promoted book's same-named wrapper chapter."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(
            Path(self.temporary.name) / "Corpus.lekhak", "Corpus")
        # The old import's shape: a book folder wrapping a same-named
        # chapter that holds the real chapters, with the title-page prose on
        # the wrapper.
        self.book = self.project.add_document("Novelette A", kind="folder")
        self.wrapper = self.project.add_document(
            "Novelette A", self.book.id, "chapter")
        self.project.write_content(self.wrapper.id, "<p>A shell of prose.</p>")
        self.scene = self.project.add_document(
            "A-beat", self.wrapper.id, "scene")
        self.project.write_content(self.scene.id, "<p>Beat.</p>")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def test_the_fold_removes_the_wrapper_and_hoists_children(self):
        folded = self.project.flatten_promoted_books()
        self.assertEqual(folded, 1)
        documents = self.project.documents()
        self.assertNotIn(self.wrapper.id, {d.id for d in documents})
        # The folder carries the wrapper's words…
        self.assertIn("shell of prose", self.project.read_content(self.book.id))
        # …and now holds the scene directly.
        self.assertEqual(
            [d.title for d in documents if d.parent_id == self.book.id],
            ["A-beat"])

    def test_the_fold_is_idempotent(self):
        self.project.flatten_promoted_books()
        self.assertEqual(self.project.flatten_promoted_books(), 0)

    def test_a_book_not_wrapped_is_left_alone(self):
        # A normal book: chapters sit directly under the folder already.
        book = self.project.add_document("Book Two", kind="folder")
        self.project.add_document("Prologue", book.id, "chapter")
        self.assertEqual(self.project.flatten_promoted_books(), 1)  # only Novelette A
        self.assertEqual(
            [d.title for d in self.project.documents()
             if d.parent_id == book.id], ["Prologue"])


class UnwrapFolderTests(unittest.TestCase):
    """Dissolving a grouping folder (an imported Scrivener shelf)."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(
            Path(self.temporary.name) / "Corpus.lekhak", "Corpus")
        self.shelf = self.project.add_document("Manuscript", kind="folder")
        self.book_a = self.project.add_document("Book A", self.shelf.id, "folder")
        self.book_b = self.project.add_document("Book B", self.shelf.id, "folder")
        self.loose = self.project.add_document("Loose", kind="scene")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def test_children_move_up_and_the_folder_goes(self):
        moved = self.project.unwrap_folder(self.shelf.id)
        self.assertEqual(moved, 2)
        documents = self.project.documents()
        self.assertNotIn(self.shelf.id, {d.id for d in documents})
        top = [d.title for d in documents if d.parent_id is None]
        # The seeded Draft, the loose scene, then the two books in order.
        self.assertEqual(top, ["Draft", "Loose", "Book A", "Book B"])

    def test_unwrapping_an_empty_folder_just_removes_it(self):
        empty = self.project.add_document("Empty", kind="folder")
        self.assertEqual(self.project.unwrap_folder(empty.id), 0)
        self.assertNotIn(empty.id, {d.id for d in self.project.documents()})


if __name__ == "__main__":
    unittest.main()
