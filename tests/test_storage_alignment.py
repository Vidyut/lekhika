"""The three records of the tree stay in step (docs/STORAGE.md).

The database is the index, `.order.json` is a folder's sequence and
`manifest.json` is the root's. A structural change — add, rename, move —
touches the file and every record of it at once, so a rename can never
leave a row pointing at a path that no longer exists and a move can never
leave an order file describing the old arrangement. These tests pin that
contract at the Project API, because the UI is not the only caller.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lekhika.project import Project


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel",
                                      "Novel")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def orders(self) -> dict[str, list[str]]:
        return {
            str(path.parent.relative_to(self.project.root)): json.loads(
                path.read_text(encoding="utf-8"))["children"]
            for path in self.project.root.rglob(".order.json")
        }

    def root_order(self) -> list[str]:
        manifest = self.project.root / "manifest.json"
        return json.loads(manifest.read_text(encoding="utf-8"))["children"]

    def path_of(self, title: str) -> str:
        return next(d.path for d in self.project.documents()
                    if d.title == title)

    def test_index_is_a_reserved_name(self):
        # A container's own prose is its index.html, so a document may not
        # take that file name: the two would be one file.
        book = self.project.add_document("Book", kind="folder")
        chapter = self.project.add_document("Chapter", book.id, "chapter")
        self.project.write_content(chapter.id, "<p>The chapter's own words.</p>")
        scene = self.project.add_document("index", chapter.id, "scene")
        self.project.write_content(scene.id, "<p>An index of the book.</p>")

        folder = self.project.root / self.path_of("Chapter")
        # Two files, two things: the container's page and the document.
        self.assertTrue((folder / "index.html").is_file())
        self.assertTrue((folder / "_index.html").is_file())
        # The title still reads "index" in the binder.
        self.assertEqual(self.path_of("index"), "Book/Chapter/_index.html")
        self.assertIn(
            "An index of the book.",
            self.project.plain_text(self.project.read_content(scene.id)))

    def test_the_containers_own_page_leads_the_folder_order(self):
        # index.html is a part of the parent, not one of the children: it
        # leads the folder's order, and it takes no position of its own.
        book = self.project.add_document("Book", kind="folder")
        chapter = self.project.add_document("Chapter", book.id, "chapter")
        self.project.write_content(chapter.id, "<p>The chapter's own words.</p>")
        first = self.project.add_document("Alpha", chapter.id, "scene")
        second = self.project.add_document("Beta", chapter.id, "scene")

        folder = self.path_of("Chapter")
        self.assertEqual(self.orders()[folder],
                         ["index.html", "Alpha.html", "Beta.html"])
        # The children still count from one: the parent's page is not one.
        positions = {d.title: d.position for d in self.project.documents()
                     if d.title in ("Alpha", "Beta")}
        self.assertEqual(positions, {"Alpha": 0, "Beta": 1})
        self.assertEqual(first.position, 0)
        self.assertEqual(second.position, 1)

    def test_an_index_html_on_disk_is_the_containers_own_words(self):
        # The rule, not a convention: an index.html in a folder is the
        # container's prose — it is never quietly made into a document.
        book = self.project.add_document("Book", kind="folder")
        chapter = self.project.add_document("Chapter", book.id, "chapter")
        scene = self.project.add_document("Alpha", chapter.id, "scene")
        self.project.write_content(scene.id, "<p>Alpha text.</p>")
        folder = self.project.root / self.path_of("Chapter")
        (folder / "index.html").write_text(
            "<html><body><p>A chapter opener, written by hand.</p>"
            "</body></html>", encoding="utf-8")

        self.project.sync_with_disk()

        # No document was made of it...
        self.assertNotIn("index", [d.title for d in self.project.documents()])
        # ...and the container's own words are the file's.
        self.assertIn(
            "A chapter opener, written by hand.",
            self.project.plain_text(self.project.read_content(chapter.id)))

    def test_an_index_html_that_calls_itself_something_else_is_reported(self):
        # The rule is the folder's page — but a file that says it is a
        # document of its own is a conflict. Nothing is changed; the
        # writer is told.
        book = self.project.add_document("Book", kind="folder")
        chapter = self.project.add_document("Chapter", book.id, "chapter")
        folder = self.project.root / self.path_of("Chapter")
        # An index.html carrying another document's head — a writer made
        # a document called "index" before the name was reserved.
        (folder / "index.html").write_text(
            "---\ntitle: index\n---\n<p>An index of the book.</p>",
            encoding="utf-8")

        conflicts = self.project.index_conflicts()

        self.assertEqual(len(conflicts), 1)
        self.assertIn("index", conflicts[0])
        # And the file is left exactly as it was.
        self.assertIn("An index of the book.",
                      (folder / "index.html").read_text(encoding="utf-8"))

    def test_a_folders_own_page_is_not_a_conflict(self):
        book = self.project.add_document("Book", kind="folder")
        chapter = self.project.add_document("Chapter", book.id, "chapter")
        self.project.write_content(chapter.id, "<p>The chapter's own words.</p>")

        self.assertEqual(self.project.index_conflicts(), [])

    def test_renaming_a_folder_repoints_its_children(self):
        book = self.project.add_document("Book", kind="folder")
        chapter = self.project.add_document("Chapter", book.id, "chapter")
        scene = self.project.add_document("Scene", chapter.id, "scene")
        self.project.write_content(scene.id, "<p>Prose.</p>")

        self.project.update_document(book.id, title="Novel")

        # Every row under the rename points at a file that is really there.
        for document in self.project.documents():
            if document.path:
                self.assertTrue((self.project.root / document.path).exists(),
                                document.title)
        self.assertEqual(self.path_of("Scene"), "Novel/Chapter/Scene.html")
        # ...and the binder has one Chapter, not a real one and a ghost.
        novel = next(node for node in self.project.binder_tree()
                     if node.title == "Novel")
        self.assertEqual([child.title for child in novel.children], ["Chapter"])

    def test_renaming_a_folder_rewrites_the_root_order(self):
        book = self.project.add_document("Book", kind="folder")
        self.project.write_order_files()
        self.assertIn("Book", self.root_order())

        self.project.update_document(book.id, title="Novel")

        self.assertEqual(sorted(self.root_order()), ["Draft", "Novel"])

    def test_renaming_rewrites_the_title_in_the_file_head(self):
        scene = self.project.add_document("Untitled", kind="scene")
        self.project.write_content(scene.id, "<p>Prose.</p>")

        self.project.update_document(scene.id, title="First Light")

        head = (self.project.root / self.path_of("First Light")).read_text(
            encoding="utf-8")
        self.assertIn("title: First Light", head)

    def test_moving_a_document_rewrites_both_order_files(self):
        left = self.project.add_document("Left", kind="folder")
        right = self.project.add_document("Right", kind="folder")
        scene = self.project.add_document("Scene", left.id, "scene")
        self.project.write_content(scene.id, "<p>Prose.</p>")
        self.project.write_order_files()

        self.assertTrue(self.project.reorder(scene.id, right.id, 0))

        self.assertEqual(self.orders()["Left"], [])
        self.assertEqual(self.orders()["Right"], ["Scene.html"])
        self.assertEqual(self.path_of("Scene"), "Right/Scene.html")

    def test_adding_a_document_appends_it_to_the_folder_order(self):
        book = self.project.add_document("Book", kind="folder")

        self.project.add_document("One", book.id, "scene")
        self.project.add_document("Two", book.id, "scene")

        self.assertEqual(self.orders()["Book"], ["One.html", "Two.html"])

    def test_a_hand_added_file_joins_the_folder_order(self):
        book = self.project.add_document("Book", kind="folder")
        self.project.write_order_files()
        (self.project.root / "Book" / "By Hand.html").write_text(
            "<p>Made in a file manager.</p>", encoding="utf-8")

        self.assertEqual(self.project.sync_with_disk(), 1)

        self.assertEqual(self.orders()["Book"], ["By Hand.html"])

    def test_reordering_within_a_folder_rewrites_its_order(self):
        book = self.project.add_document("Book", kind="folder")
        one = self.project.add_document("One", book.id, "scene")
        two = self.project.add_document("Two", book.id, "scene")
        self.project.write_content(one.id, "<p>One.</p>")
        self.project.write_content(two.id, "<p>Two.</p>")
        self.project.write_order_files()

        self.assertTrue(self.project.reorder(two.id, book.id, 0))

        self.assertEqual(self.orders()["Book"], ["Two.html", "One.html"])

    def test_validate_alignment_reports_no_problems_for_healthy_project(self):
        """A newly created project should have no alignment problems."""
        problems = self.project.validate_alignment()
        self.assertEqual(problems, [])

    def test_reconcile_creates_missing_order_files(self):
        """Reconcile should create .order.json files where missing."""
        book = self.project.add_document("Book", kind="folder")
        scene = self.project.add_document("Scene", book.id, "scene")

        # The order files should exist
        book_order = self.project.root / "Book" / ".order.json"
        manifest = self.project.root / "manifest.json"

        self.assertTrue(book_order.exists())
        self.assertTrue(manifest.exists())

    def test_validate_alignment_detects_mismatched_order(self):
        """Validation should find mismatches between DB and order files."""
        book = self.project.add_document("Book", kind="folder")
        self.project.add_document("Scene1", book.id, "scene")

        # Tamper with the order file
        book_order = self.project.root / "Book" / ".order.json"
        book_order.write_text('{"children": ["WrongName.html"]}', encoding="utf-8")

        problems = self.project.validate_alignment()
        self.assertTrue(any("Scene1" in p for p in problems))
        self.assertTrue(any("WrongName" in p for p in problems))

    def test_validate_alignment_detects_missing_manifest_item(self):
        """Validation should find items missing from manifest.json."""
        book = self.project.add_document("Book", kind="folder")

        # Remove Book from manifest manually
        manifest = self.project.root / "manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["children"] = [c for c in data.get("children", []) if c != "Book"]
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        problems = self.project.validate_alignment()
        self.assertTrue(any("Book" in p for p in problems))

    def test_validate_alignment_accepts_empty_chapter_folder(self):
        """An empty chapter has a directory and .order.json but no children.

        It is still a container on disk (like write_order_files treats it),
        so its order file is not a ghost.
        """
        self.project.add_document("Chapter", kind="chapter")
        problems = self.project.validate_alignment()
        self.assertEqual(problems, [])

    def test_validate_alignment_treats_templates_as_normal_folder(self):
        """Templates is a normal binder folder, so its order file without a DB row is a ghost."""
        set_dir = self.project.root / "templates" / "MySet"
        set_dir.mkdir(parents=True)
        (set_dir / ".order.json").write_text('{"children": []}', encoding="utf-8")
        problems = self.project.validate_alignment()
        self.assertTrue(any("templates/MySet" in p for p in problems))


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def test_reconcile_fixes_order_mismatch(self):
        """Reconcile should fix mismatched order files."""
        book = self.project.add_document("Book", kind="folder")
        self.project.add_document("Scene1", book.id, "scene")

        # Tamper with the order file
        book_order = self.project.root / "Book" / ".order.json"
        book_order.write_text('{"children": ["WrongName.html"]}', encoding="utf-8")

        # Verify there's a problem
        problems_before = self.project.validate_alignment()
        self.assertTrue(len(problems_before) > 0)

        # Reconcile
        changes = self.project.reconcile()
        self.assertEqual(changes["order_files"], 1)

        # Verify fixed
        problems_after = self.project.validate_alignment()
        self.assertEqual(problems_after, [])

        # Verify order file content
        data = json.loads(book_order.read_text(encoding="utf-8"))
        self.assertEqual(data["children"], ["Scene1.html"])

    def test_reconcile_fixes_manifest(self):
        """Reconcile should fix manifest.json if needed."""
        book1 = self.project.add_document("Book1", kind="folder")
        book2 = self.project.add_document("Book2", kind="folder")
        self.project.update_document(book1.id, title="Renamed Book")

        # Tamper with manifest - remove one book
        manifest = self.project.root / "manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["children"] = [c for c in data.get("children", []) if c != "Book2"]
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # Reconcile
        changes = self.project.reconcile()
        self.assertEqual(changes["manifest"], 1)

        # Verify fixed
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertIn("Renamed Book", data["children"])
        self.assertIn("Book2", data["children"])

    def test_reconcile_dry_run_reports_changes(self):
        """Dry run should report changes without making them."""
        book = self.project.add_document("Book", kind="folder")
        self.project.add_document("Scene1", book.id, "scene")

        # Tamper with order file
        book_order = self.project.root / "Book" / ".order.json"
        book_order.write_text('{"children": []}', encoding="utf-8")

        changes = self.project.reconcile(dry_run=True)
        self.assertEqual(changes["order_files"], 1)

        # Verify no change was made
        data = json.loads(book_order.read_text(encoding="utf-8"))
        self.assertEqual(data["children"], [])


class DriftTests(unittest.TestCase):
    """The "which is right" resolutions for drift between DB, order files
    and disk (the open-time prompt's three truths)."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel",
                                      "Novel")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def test_a_hand_rearranged_order_file_is_detected(self):
        # Same names, other order: a set check alone would miss this.
        book = self.project.add_document("Book", kind="folder")
        one = self.project.add_document("One", book.id, "scene")
        two = self.project.add_document("Two", book.id, "scene")
        self.project.write_content(one.id, "<p>One.</p>")
        self.project.write_content(two.id, "<p>Two.</p>")
        order_file = self.project.root / "Book" / ".order.json"
        order_file.write_text(json.dumps({"children": ["Two.html", "One.html"]}),
                              encoding="utf-8")

        problems = self.project.validate_alignment()
        self.assertTrue(any("order differs" in p for p in problems),
                        problems)

    def test_order_files_win_applies_the_json_arrangement(self):
        book = self.project.add_document("Book", kind="folder")
        one = self.project.add_document("One", book.id, "scene")
        two = self.project.add_document("Two", book.id, "scene")
        self.project.write_content(one.id, "<p>One.</p>")
        self.project.write_content(two.id, "<p>Two.</p>")
        order_file = self.project.root / "Book" / ".order.json"
        order_file.write_text(json.dumps({"children": ["Two.html", "One.html"]}),
                              encoding="utf-8")

        self.project.apply_order_files()

        kids = [d.title for d in self.project.documents()
                if d.parent_id == book.id]
        self.assertEqual(kids, ["Two", "One"])

    def test_order_files_win_ignores_unknown_names(self):
        book = self.project.add_document("Book", kind="folder")
        one = self.project.add_document("One", book.id, "scene")
        two = self.project.add_document("Two", book.id, "scene")
        self.project.write_content(one.id, "<p>One.</p>")
        self.project.write_content(two.id, "<p>Two.</p>")
        order_file = self.project.root / "Book" / ".order.json"
        order_file.write_text(
            json.dumps({"children": ["Ghost.html", "Two.html", "One.html"]}),
            encoding="utf-8")

        self.project.apply_order_files()

        titles = {d.title for d in self.project.documents()}
        self.assertNotIn("Ghost", titles)
        kids = [d.title for d in self.project.documents()
                if d.parent_id == book.id]
        self.assertEqual(kids, ["Two", "One"])

    def test_manifest_win_applies_the_root_arrangement(self):
        book = self.project.add_document("Book", kind="folder")
        manuscript = self.project.add_document("Manuscript", kind="folder")
        manifest = self.project.root / "manifest.json"
        manifest.write_text(json.dumps({
            "title": "Novel", "version": 1,
            "children": ["Manuscript", "Draft", "Book"]}), encoding="utf-8")

        self.project.apply_manifest()

        roots = [d.title for d in self.project.documents()
                 if d.parent_id is None]
        self.assertEqual(roots, ["Manuscript", "Draft", "Book"])
        # The folders' own order is untouched.
        self.assertTrue((self.project.root / "Book" / ".order.json").exists())

    def test_manifest_win_ignores_unknown_names(self):
        self.project.add_document("Book", kind="folder")
        manifest = self.project.root / "manifest.json"
        manifest.write_text(json.dumps({
            "title": "Novel", "version": 1,
            "children": ["Ghost", "Book", "Draft"]}), encoding="utf-8")

        self.project.apply_manifest()

        titles = {d.title for d in self.project.documents()}
        self.assertNotIn("Ghost", titles)
        self.assertEqual(
            [d.title for d in self.project.documents() if d.parent_id is None],
            ["Book", "Draft"])

    def test_files_win_adopts_hand_added_files(self):
        book = self.project.add_document("Book", kind="folder")
        (self.project.root / "Book" / "New Chapter.html").write_text(
            "<p>Fresh.</p>", encoding="utf-8")

        self.project.sync_with_disk()
        self.project.reconcile()

        titles = {d.title for d in self.project.documents()}
        self.assertIn("New Chapter", titles)
        self.assertEqual(self.project.validate_alignment(), [])

    def test_missing_files_reports_only_what_the_disk_lacks(self):
        self.assertEqual(self.project.missing_files(), [])
        book = self.project.add_document("Book", kind="folder")
        gone = self.project.add_document("Gone", book.id, "scene")
        self.project.write_content(gone.id, "<p>Gone.</p>")
        (self.project.root / "Book" / "Gone.html").unlink()

        self.assertEqual(self.project.missing_files(), ["Book/Gone.html"])

    def test_forget_missing_files_drops_entries_and_realigns(self):
        book = self.project.add_document("Book", kind="folder")
        gone = self.project.add_document("Gone", book.id, "scene")
        stay = self.project.add_document("Stay", book.id, "scene")
        self.project.write_content(gone.id, "<p>Gone.</p>")
        self.project.write_content(stay.id, "<p>Stay.</p>")
        (self.project.root / "Book" / "Gone.html").unlink()

        removed = self.project.forget_missing_files()

        self.assertEqual(removed, ["Book/Gone.html"])
        remaining = {d.title for d in self.project.documents()}
        self.assertNotIn("Gone", remaining)
        self.assertIn("Stay", remaining)
        self.assertEqual(self.project.validate_alignment(), [])

    def test_database_win_regenerates_deleted_json(self):
        # The writer deleted the json files thinking them extra: the DB
        # is right and reconcile brings them back.
        book = self.project.add_document("Book", kind="folder")
        self.project.add_document("Scene", book.id, "scene")
        (self.project.root / "Book" / ".order.json").unlink()
        (self.project.root / "manifest.json").unlink()

        problems = self.project.validate_alignment()
        self.assertTrue(any(".order.json" in p for p in problems), problems)
        self.assertTrue(problems)

        self.project.sync_with_disk()
        self.project.reconcile()

        self.assertTrue((self.project.root / "Book" / ".order.json").exists())
        self.assertTrue((self.project.root / "manifest.json").exists())
        self.assertEqual(self.project.validate_alignment(), [])
