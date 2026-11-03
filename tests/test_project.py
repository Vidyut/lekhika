import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from lekhak.project import DeleteError, Project


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def test_create_uses_transparent_layout(self):
        self.assertEqual(self.project.root.suffix, ".lekhak")
        self.assertTrue(self.project.db_path.exists())
        # The book itself is a folder tree on disk (docs/STORAGE.md): the
        # manifest marks the project, and every document is a real file.
        self.assertTrue((self.project.root / "manifest.json").is_file())
        root = self.project.documents()[0]
        self.assertTrue((self.project.root / root.path).is_dir())
        self.assertEqual(self.project.get_setting("title"), "Novel")

    def test_content_round_trip_and_search(self):
        document = self.project.documents()[0]
        self.project.write_content(document.id, "<p>Hello <b>Linux</b> writer</p>")
        self.assertIn("Linux", self.project.read_content(document.id))
        self.assertEqual([d.id for d in self.project.search("linux")], [document.id])

    def test_hierarchy_and_export_order(self):
        root = self.project.documents()[0]
        chapter = self.project.add_document("Chapter One", root.id, "chapter")
        self.project.write_content(chapter.id, "<p>Opening</p>")
        scene = self.project.add_document("Scene A", chapter.id, "scene")
        self.project.write_content(scene.id, "<p>Chapter text</p>")
        destination = Path(self.temporary.name) / "book.txt"
        self.project.export_text(destination)
        exported = destination.read_text(encoding="utf-8")
        self.assertLess(exported.index("Opening"), exported.index("Chapter text"))

    def test_delete_removes_descendant_files(self):
        root = self.project.documents()[0]
        child = self.project.add_document("Child", root.id)
        self.project.delete_document(root.id)
        self.assertEqual(self.project.documents(), [])
        self.assertFalse(self.project.content_path(child.id).exists())

    def test_deleting_a_folder_removes_the_folder_from_disk(self):
        # A container is a directory: deleting it takes the directory
        # with it, not just the file inside it.
        root = self.project.documents()[0]
        folder = self.project.add_document("A Book", root.id, "folder")
        scene = self.project.add_document("A Scene", folder.id, "scene")
        folder_path = self.project.root / folder.path
        scene_path = self.project.root / scene.path
        self.assertTrue(folder_path.is_dir())
        self.project.delete_document(folder.id)
        self.assertFalse(folder_path.exists())
        self.assertFalse(scene_path.exists())
        self.assertEqual(
            [d.id for d in self.project.documents() if d.id in
             (folder.id, scene.id)], [])

    def test_a_file_that_will_not_delete_keeps_the_document(self):
        # A permission problem, a read-only mount, a lock: nothing is
        # deleted, the document stays in the binder, and the reason is
        # reported.
        root = self.project.documents()[0]
        scene = self.project.add_document("Stuck", root.id, "scene")
        path = self.project.root / scene.path
        real_unlink = Path.unlink

        def refuse(self, *args, **kwargs):
            if self == path:
                raise PermissionError(13, "Permission denied")
            return real_unlink(self, *args, **kwargs)

        with mock.patch.object(Path, "unlink", refuse):
            with self.assertRaises(DeleteError) as caught:
                self.project.delete_document(scene.id)
        self.assertTrue(caught.exception.failures)
        # The row is back, and the file is still there.
        self.assertIn(scene.id, [d.id for d in self.project.documents()])
        self.assertTrue(path.exists())

    def test_backup_is_recoverable(self):
        backup = self.project.backup()
        self.assertTrue((backup / "project.sqlite3").exists())
        # The manuscript is backed up as the folder tree it is, so a
        # backup reads like the project.
        self.assertTrue((backup / "manifest.json").is_file())
        root = self.project.documents()[0]
        self.assertTrue((backup / root.path).is_dir())

    def test_custom_backup_folder_is_remembered_and_used(self):
        custom_root = Path(self.temporary.name) / "External Backups"
        self.project.set_backup_folder(custom_root)
        backup = self.project.backup()
        self.assertEqual(backup.parent, custom_root / self.project.root.name)
        self.assertEqual(self.project.backup_directory(), backup.parent)
        self.assertTrue((backup / "project.sqlite3").exists())

    def test_backups_never_live_inside_the_project(self):
        # A project that is deleted or lost must not take its backups
        # with it — the one moment they matter.
        self.assertFalse((self.project.root / "backups").exists())
        self.assertNotIn(self.project.root,
                         self.project.backup_directory().parents)
        backup = self.project.backup()
        self.assertFalse(str(backup).startswith(str(self.project.root)))

    def test_a_new_project_holds_only_the_book(self):
        # Nothing but the manuscript and the index: no documents store, no
        # backups folder (docs/STORAGE.md).
        names = {entry.name for entry in self.project.root.iterdir()}
        self.assertNotIn("documents", names)
        self.assertNotIn("backups", names)
        self.assertIn("manifest.json", names)
        self.assertIn("project.sqlite3", names)

    def test_the_order_file_holds_the_binder_order(self):
        root = self.project.documents()[0]
        chapter = self.project.add_document("Chapter One", root.id, "chapter")
        self.project.add_document("Scene B", chapter.id, "scene")
        self.project.add_document("Scene A", chapter.id, "scene")
        self.project.write_order_files()
        order = json.loads(
            (self.project.root / chapter.path / ".order.json").read_text())
        self.assertEqual(order["children"], ["Scene B.html", "Scene A.html"])

    def test_a_document_keeps_its_metadata_in_its_own_file(self):
        scene = self.project.add_document("Scene", None, "scene")
        self.project.update_document(scene.id, synopsis="What happens here",
                                     word_target=800)
        text = (self.project.root / scene.path).read_text(encoding="utf-8")
        self.assertIn("synopsis: What happens here", text)
        self.assertIn("word_target: 800", text)
        # And the metadata never reaches the prose.
        self.assertNotIn("synopsis", self.project.read_content(scene.id))

    def test_a_renamed_document_is_renamed_on_disk(self):
        scene = self.project.add_document("Old Name", None, "scene")
        self.project.update_document(scene.id, title="New Name")
        self.assertTrue((self.project.root / "New Name.html").is_file())
        self.assertFalse((self.project.root / "Old Name.html").exists())
        self.assertEqual(self.project.documents()[-1].path, "New Name.html")

    def test_a_folder_the_writer_added_by_hand_is_picked_up(self):
        # The tree is the truth: something the writer made in a file
        # manager becomes part of the book.
        folder = self.project.root / "Extra Chapter"
        folder.mkdir()
        (folder / "Scene.html").write_text("<p>Hand written.</p>",
                                           encoding="utf-8")
        added = self.project.sync_with_disk()
        self.assertGreaterEqual(added, 2)
        titles = {d.title for d in self.project.documents()}
        self.assertIn("Extra Chapter", titles)
        self.assertIn("Scene", titles)

    def test_snapshot_restore_preserves_both_versions(self):
        document = self.project.documents()[0]
        self.project.write_content(document.id, "<p>First version</p>")
        snapshot = self.project.create_snapshot(document.id, "First")
        self.project.write_content(document.id, "<p>Second version</p>")
        self.project.restore_snapshot(snapshot.id)
        self.assertIn("First version", self.project.read_content(document.id))
        self.assertEqual(len(self.project.snapshots(document.id)), 2)

    def test_word_target_is_persisted(self):
        document = self.project.documents()[0]
        self.project.update_document(document.id, word_target=2500)
        updated = self.project.documents()[0]
        self.assertEqual(updated.word_target, 2500)

    def test_chapter_and_scene_types(self):
        chapter = self.project.add_document("Chapter Two", kind="chapter")
        scene = self.project.add_document("A Meeting", chapter.id, "scene")
        by_id = {item.id: item for item in self.project.documents()}
        self.assertEqual(by_id[chapter.id].kind, "chapter")
        self.assertEqual(by_id[scene.id].kind, "scene")

    def test_epub_export_has_required_structure(self):
        root = self.project.documents()[0]
        scene = self.project.add_document("Scene One", root.id, "scene")
        self.project.write_content(scene.id, "<p>Once upon a time.</p>")
        destination = Path(self.temporary.name) / "book.epub"
        self.project.export_epub(destination)
        with zipfile.ZipFile(destination) as archive:
            self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
            self.assertIn("OEBPS/content.opf", archive.namelist())
            self.assertIn("Once upon a time", archive.read("OEBPS/section-1.xhtml").decode())

    def test_docx_export_contains_scene_text(self):
        chapter = self.project.documents()[0]
        scene = self.project.add_document("Scene One", chapter.id, "scene")
        self.project.write_content(scene.id, "<p>Exported prose.</p>")
        destination = Path(self.temporary.name) / "book.docx"
        self.project.export_docx(destination)
        from docx import Document as DocxDocument
        output = DocxDocument(destination)
        self.assertIn("Exported prose.", [paragraph.text for paragraph in output.paragraphs])

    def test_metadata_is_available_to_export_templates(self):
        document = self.project.documents()[0]
        self.project.set_metadata(document.id, {"POV": "Maya", "Location": "Delhi"})
        rendered = self.project.render_template(
            document, "POV: ${Metadata.POV}\nPlace: ${Metadata.Location}\n${Content}"
        )
        self.assertIn("POV: Maya", rendered)
        self.assertIn("Place: Delhi", rendered)

    def test_export_can_select_individual_scenes(self):
        from lekhak.templates import TemplateSet

        root = self.project.documents()[0]
        first = self.project.add_document("First", root.id, "scene")
        second = self.project.add_document("Second", root.id, "scene")
        self.project.set_metadata(first.id, {"POV": "Maya"})
        self.project.write_content(first.id, "<p>Include this.</p>")
        self.project.write_content(second.id, "<p>Exclude this.</p>")
        templates = TemplateSet(
            "test", None,
            {"document": "${Metadata.POV}\n${Content}"},
            {"document": "${Metadata.POV}\n${Content}"},
        )
        destination = Path(self.temporary.name) / "selection.txt"
        self.project.export_text(destination, {first.id}, templates)
        exported = destination.read_text(encoding="utf-8")
        self.assertIn("Include this.", exported)
        self.assertNotIn("Exclude this.", exported)
        self.assertIn("Maya", exported)

    def test_project_metadata_round_trips_types_and_options(self):
        self.project.set_project_metadata({
            "Series": ("Series", "text", []),
            "Story date": ("2026-10-20", "date", []),
            "Status": ("Draft", "choice", ["Draft", "Final"]),
        })
        entries = self.project.project_metadata()
        self.assertEqual(entries["Series"], ("Series", "text", []))
        self.assertEqual(entries["Story date"], ("2026-10-20", "date", []))
        self.assertEqual(entries["Status"], ("Draft", "choice", ["Draft", "Final"]))

    def test_set_project_metadata_replaces_and_ignores_blank_keys(self):
        self.project.set_project_metadata({"Series": ("Series", "text", [])})
        self.project.set_project_metadata(
            {"Verse": ("Three", "text", []), "   ": ("x", "text", [])}
        )
        self.assertEqual(list(self.project.project_metadata()), ["Verse"])

    def test_junk_project_metadata_setting_falls_back_to_empty(self):
        self.project.set_setting("project_metadata", "{not json")
        self.assertEqual(self.project.project_metadata(), {})
        self.project.set_setting("project_metadata", '[1, 2, {"value": 5}]')
        self.assertEqual(self.project.project_metadata(), {})
        self.project.set_setting(
            "project_metadata", '{"Series": 5, "Verse": {"value": "Three"}}'
        )
        self.assertEqual(self.project.project_metadata(),
                         {"Verse": ("Three", "text", [])})

    def test_page_break_defaults_persist_and_reject_unknown_values(self):
        root = self.project.documents()[0]
        chapter = self.project.add_document("Chapter", root.id, "chapter")
        self.assertEqual(chapter.page_break, "auto")

        self.project.update_document(chapter.id, page_break="right")
        found = next(d for d in self.project.documents() if d.id == chapter.id)
        self.assertEqual(found.page_break, "right")

        with self.assertRaises(ValueError):
            self.project.update_document(chapter.id, page_break="middle")
        found = next(d for d in self.project.documents() if d.id == chapter.id)
        self.assertEqual(found.page_break, "right")   # unchanged by the refusal

    def test_docx_section_parity_for_every_page_break_value(self):
        root = self.project.documents()[0]
        # A leading document without a mark: the first document in any export
        # cannot open a section (nothing precedes it), so without this the
        # first marked chapter would be skipped rather than sectioned.
        prologue = self.project.add_document("Prologue", root.id, "chapter")
        self.project.write_content(prologue.id, "<p>Opens the book.</p>")
        for index, mark in enumerate(("right", "next-odd", "left", "next-even")):
            chapter = self.project.add_document(f"Chapter {index}", root.id, "chapter")
            self.project.write_content(chapter.id, "<p>Body text.</p>")
            self.project.update_document(chapter.id, page_break=mark)
        plain = self.project.add_document("Plain", root.id, "chapter")
        self.project.write_content(plain.id, "<p>Flows on.</p>")

        destination = Path(self.temporary.name) / "marks.docx"
        self.project.export_docx(destination)

        import zipfile
        with zipfile.ZipFile(destination) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
        # right and next-odd are one demand (recto = odd), likewise left
        # and next-even; "auto" adds no section at all.
        self.assertEqual(xml.count('w:val="oddPage"'), 2)
        self.assertEqual(xml.count('w:val="evenPage"'), 2)

    def test_related_documents_matches_whole_words_in_both_directions(self):
        root = self.project.documents()[0]
        someone = self.project.add_document("Someone", root.id, "note")
        somewhere = self.project.add_document("Somewhere", root.id, "note")
        scene = self.project.add_document("Scene One", root.id, "scene")
        self.project.write_content(scene.id, "<p>Someone went to somewhere.</p>")
        self.project.write_content(somewhere.id, "<p>Someone's home port.</p>")

        mentions, mentioned_by = self.project.related_documents(scene.id)
        # Binder order, case-insensitive: "somewhere" in prose matches Somewhere.
        self.assertEqual([d.id for d in mentions], [someone.id, somewhere.id])
        self.assertEqual(mentioned_by, [])     # nobody's prose says "Scene One"

        mentions, mentioned_by = self.project.related_documents(someone.id)
        self.assertEqual(mentions, [])         # Someone's file is empty
        self.assertEqual([d.id for d in mentioned_by], [somewhere.id, scene.id])

        # Whole word: the title "Rid" must not match inside "Somewhere".
        rid = self.project.add_document("Rid", root.id, "note")
        _mentions, mentioned_by = self.project.related_documents(rid.id)
        self.assertEqual(mentioned_by, [])

    def test_related_scan_reflects_a_rewrite(self):
        root = self.project.documents()[0]
        someone = self.project.add_document("Someone", root.id, "note")
        scene = self.project.add_document("Scene One", root.id, "scene")
        self.project.write_content(scene.id, "<p>Someone went to Somewhere.</p>")
        self.assertEqual(
            [d.id for d in self.project.related_documents(scene.id)[0]],
            [someone.id],
        )

        self.project.write_content(scene.id, "<p>Quiet now.</p>")

        self.assertEqual(self.project.related_documents(scene.id), ([], []))

    def test_date_metadata_type_and_value_are_persisted(self):
        document = self.project.documents()[0]
        self.project.set_metadata(
            document.id, {"Story date": "2026-10-20"}, {"Story date": "date"}
        )
        self.assertEqual(
            self.project.metadata_entries(document.id)["Story date"],
            ("2026-10-20", "date", []),
        )
        self.assertIn(
            "2026-10-20",
            self.project.render_template(document, "${Metadata.Story date}"),
        )

    def test_dropdown_metadata_restricts_and_persists_options(self):
        document = self.project.documents()[0]
        self.project.set_metadata(
            document.id,
            {"Status": "Revised"},
            {"Status": "choice"},
            {"Status": ["Draft", "Revised", "Final"]},
        )
        self.assertEqual(
            self.project.metadata_entries(document.id)["Status"],
            ("Revised", "choice", ["Draft", "Revised", "Final"]),
        )
        self.assertEqual(
            self.project.render_template(document, "${Metadata.Status}"), "Revised"
        )

    def test_seeded_draft_is_a_folder(self):
        self.assertEqual(self.project.documents()[0].kind, "folder")

    def test_folder_kind_is_allowed(self):
        folder = self.project.add_document("Worldbuilding", kind="folder")
        self.assertEqual(folder.kind, "folder")
        with self.assertRaises(ValueError):
            self.project.add_document("Bad", kind="character-sheet")

    def test_parallel_top_level_roots(self):
        manuscript = self.project.add_document("Manuscript", kind="folder")
        characters = self.project.add_document("Characters", kind="folder")
        self.assertIsNone(manuscript.parent_id)
        self.assertIsNone(characters.parent_id)
        roots = [d for d in self.project.documents() if d.parent_id is None]
        self.assertEqual(len(roots), 3)

    def test_arbitrary_nesting_regardless_of_kind(self):
        world = self.project.add_document("Worldbuilding", kind="folder")
        chapter = self.project.add_document("Interlude", world.id, "chapter")
        scene = self.project.add_document("Deep Scene", chapter.id, "scene")
        nested = self.project.add_document("Appendix", scene.id, "folder")
        self.assertEqual(self.project.parent_of(nested.id), scene.id)
        self.assertEqual(
            [d.id for d in self.project.ordered_documents() if d.id in {
                world.id, chapter.id, scene.id, nested.id}],
            [world.id, chapter.id, scene.id, nested.id],
        )

    def test_export_skips_folders_and_notes(self):
        root = self.project.documents()[0]
        self.project.write_content(root.id, "<p>Folder text should not export.</p>")
        chapter = self.project.add_document("Chapter", root.id, "chapter")
        self.project.write_content(chapter.id, "<p>Chapter prose.</p>")
        note = self.project.add_document("Aside", chapter.id, "note")
        self.project.write_content(note.id, "<p>Note text should not export.</p>")
        destination = Path(self.temporary.name) / "book.txt"
        self.project.export_text(destination)
        exported = destination.read_text(encoding="utf-8")
        self.assertIn("Chapter prose.", exported)
        self.assertNotIn("Folder text", exported)
        self.assertNotIn("Note text", exported)

    def test_old_draft_chapter_migrates_to_folder(self):
        import sqlite3
        self.project.close()
        connection = sqlite3.connect(self.project.db_path)
        connection.execute(
            "UPDATE documents SET kind='chapter' "
            "WHERE title='Draft' AND parent_id IS NULL"
        )
        # Forgetting the flag too is what makes this an *old* project: one
        # written before the migration existed carries no record of it.
        connection.execute(
            "DELETE FROM settings WHERE key='draft_folder_migration'")
        connection.commit()
        connection.close()
        self.project.open()
        self.assertEqual(self.project.documents()[0].kind, "folder")

    def test_draft_migration_runs_once(self):
        """After it has run, a root chapter named "Draft" is the writer's
        own and stays a chapter — the seed is not rewritten every open."""
        import sqlite3
        self.project.close()
        connection = sqlite3.connect(self.project.db_path)
        connection.execute(
            "UPDATE documents SET kind='chapter' "
            "WHERE title='Draft' AND parent_id IS NULL"
        )
        connection.commit()
        connection.close()
        self.project.open()
        self.assertEqual(self.project.documents()[0].kind, "chapter")

    def test_backup_pruning_leaves_foreign_directories_alone(self):
        """Pruning may only delete Lekhak's own timestamped backups: the
        backup folder belongs to the writer and can already hold anything."""
        folder = (Path(self.temporary.name) / "External Backups"
                  / self.project.root.name)
        self.project.set_backup_folder(Path(self.temporary.name) / "External Backups")
        self.assertEqual(self.project.backup_directory(), folder)
        folder.mkdir(parents=True)
        theirs = folder / "my own notes"
        theirs.mkdir()
        (theirs / "important.txt").write_text("keep me", encoding="utf-8")

        for _ in range(12):
            self.project.backup(keep=10)

        self.assertTrue((theirs / "important.txt").exists())
        ours = [p for p in folder.iterdir() if p.name != "my own notes"]
        self.assertEqual(len(ours), 10)

    def test_renaming_a_backup_makes_it_permanent(self):
        """The documented way to keep a version forever: rename the folder so
        it no longer carries a timestamp. Pruning then cannot see it, and it
        does not consume one of the ten kept slots either."""
        self.project.backup()
        backups = self.project.backup_directory()
        first = sorted(p for p in backups.iterdir() if p.is_dir())[0]
        kept = backups / "as it was before the rewrite"
        first.rename(kept)

        for _ in range(15):
            self.project.backup(keep=2)

        self.assertTrue(kept.is_dir())
        self.assertTrue((kept / "project.sqlite3").exists())
        stamped = [p for p in backups.iterdir() if p.is_dir() and p != kept]
        self.assertEqual(len(stamped), 2)

    def test_add_document_leaves_no_row_when_the_write_fails(self):
        root = self.project.documents()[0]
        before = len(self.project.documents())

        with mock.patch.object(type(self.project), "write_content",
                               side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.project.add_document("Scene", root.id)

        self.assertEqual(len(self.project.documents()), before)

    def test_split_writes_the_new_file_before_the_rows(self):
        document = self.project.documents()[0]
        self.project.write_content(document.id, "<p>one two three</p>")

        with mock.patch.object(type(self.project), "write_content",
                               side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.project.split_document(
                    document.id, "<p>one two</p>", "<p>three</p>")

        self.assertEqual(len(self.project.documents()), 1)
        self.assertIn("one two three", self.project.read_content(document.id))

    def test_split_prefers_duplication_over_losing_the_second_half(self):
        """If the *last* step fails the split is half-applied: both rows
        exist and the original still holds the whole text. Duplicated
        prose is recoverable; a lost half is not."""
        document = self.project.documents()[0]
        self.project.write_content(document.id, "<p>one two three</p>")
        real = type(self.project).write_content
        calls = []

        def flaky(project, document_id, html):
            calls.append(document_id)
            if len(calls) == 2:
                raise OSError("disk full")
            return real(project, document_id, html)

        with mock.patch.object(type(self.project), "write_content", flaky):
            with self.assertRaises(OSError):
                self.project.split_document(
                    document.id, "<p>one two</p>", "<p>three</p>")

        self.assertEqual(len(self.project.documents()), 2)
        self.assertIn("one two three", self.project.read_content(document.id))

    def test_merge_changes_nothing_when_the_combined_write_fails(self):
        root = self.project.documents()[0]
        first = self.project.add_document("First", root.id, "scene")
        second = self.project.add_document("Second", root.id, "scene")
        self.project.write_content(first.id, "<p>alpha</p>")
        self.project.write_content(second.id, "<p>beta</p>")

        with mock.patch.object(type(self.project), "write_content",
                               side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.project.merge_documents(first.id, second.id)

        titles = {d.id: d.title for d in self.project.documents()}
        self.assertEqual(titles[first.id], "First")
        self.assertEqual(titles[second.id], "Second")
        self.assertIn("alpha", self.project.read_content(first.id))
        # the history has not moved either: nothing happened at all
        self.assertEqual(self.project.snapshots(second.id), [])

    def test_replace_snapshots_exactly_the_documents_it_changes(self):
        root = self.project.documents()[0]
        scene = self.project.add_document("Scene", root.id, "scene")
        self.project.write_content(scene.id, "<p>monsoon monsoon</p>")
        untouched = self.project.add_document("Other", root.id, "scene")
        self.project.write_content(untouched.id, "<p>nothing here</p>")

        counts = self.project.replace("monsoon", "rain")

        self.assertEqual(counts["replacements"], 2)
        self.assertIn("rain", self.project.read_content(scene.id))
        self.assertEqual([s.name for s in self.project.snapshots(scene.id)],
                         ["Before replace"])
        self.assertEqual(self.project.snapshots(untouched.id), [])

        # …and the snapshot really is the way back.
        snapshot = self.project.snapshots(scene.id)[0]
        self.project.restore_snapshot(snapshot.id)
        self.assertIn("monsoon", self.project.read_content(scene.id))


    def test_sibling_rank_is_per_parent_and_survives_reorder(self):
        root = self.project.documents()[0]
        folder_one = self.project.add_document("One", root.id, "folder")
        folder_two = self.project.add_document("Two", root.id, "folder")
        self.project.add_document("A", folder_one.id, "scene")
        self.project.add_document("B", folder_one.id, "scene")
        self.project.add_document("C", folder_two.id, "scene")

        def rank(document_id):
            parent = next(d for d in self.project.documents()
                          if d.id == document_id).parent_id
            siblings = [d for d in self.project.documents()
                        if d.parent_id == parent]
            siblings.sort(key=lambda d: d.position)
            return [d.id for d in siblings].index(document_id) + 1

        c = next(d for d in self.project.documents() if d.title == "C")
        b = next(d for d in self.project.documents() if d.title == "B")
        self.assertEqual(rank(c.id), 1)
        self.assertEqual(rank(b.id), 2)
        # Rank is per parent: C's first-in-its-own-folder must not be
        # affected by how many siblings its uncle has.
        self.project.add_document("D", folder_two.id, "scene")
        self.assertEqual(rank(c.id), 1)
        # A reorder renumbers the ranks that follow it.
        self.project.reorder(b.id, folder_one.id, 0)
        self.assertEqual(rank(b.id), 1)
        self.assertEqual(rank(next(d for d in self.project.documents()
                                   if d.title == "A").id), 2)


class SearchReplaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")
        draft = self.project.documents()[0]
        self.chapter = self.project.add_document("Chapter One", draft.id, "chapter")
        self.scene = self.project.add_document("Scene", self.chapter.id, "scene")
        self.project.write_content(self.scene.id, "<p>Kael opened the airlock twice.</p>")
        self.project.update_document(self.chapter.id, synopsis="Kael waits")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def test_replace_literal_is_case_insensitive_by_default(self):
        counts = self.project.replace("kael", "Vidyut", in_synopses=True)
        self.assertEqual(counts["replacements"], 2)
        self.assertEqual(counts["documents"], 2)
        self.assertIn("Vidyut opened", self.project.plain_text(
            self.project.read_content(self.scene.id)))
        self.assertEqual(self.project.documents()[1].synopsis, "Vidyut waits")

    def test_replace_respects_field_flags(self):
        counts = self.project.replace("Kael", "Vidyut", in_titles=False,
                                      in_synopses=False)
        self.assertEqual(counts["titles"] + counts["synopses"], 0)
        self.assertEqual(counts["replacements"], 1)
        self.assertIn("Kael waits", self.project.documents()[1].synopsis)

    def test_replace_takes_titles_when_asked(self):
        counts = self.project.replace("Chapter One", "Chapter 1", in_titles=True)
        self.assertEqual(counts["titles"], 1)
        self.assertEqual(self.project.documents()[1].title, "Chapter 1")

    def test_literal_replacement_is_verbatim_with_backslashes(self):
        self.project.write_content(self.scene.id, "<p>path a\\b</p>")
        counts = self.project.replace("a\\b", "x\\y")
        self.assertEqual(counts["replacements"], 1)
        self.assertIn("x\\y", self.project.read_content(self.scene.id))

    def test_regex_replace_supports_backreferences(self):
        counts = self.project.replace(r"twice", r"thrice", regex=True)
        self.assertEqual(counts["replacements"], 1)
        self.assertIn("thrice", self.project.plain_text(
            self.project.read_content(self.scene.id)))

    def test_replace_can_target_single_document(self):
        counts = self.project.replace("the", "a", document_ids={self.scene.id})
        self.assertEqual(counts["documents"], 1)

    def test_search_snippets_reports_field_and_context(self):
        hits = self.project.search_snippets("Kael")
        fields = {field for _doc, field, _snippet in hits}
        self.assertEqual(fields, {"content", "synopsis"})
        content_hit = next(s for _d, f, s in hits if f == "content")
        self.assertIn("opened", content_hit)

    def test_search_snippets_field_filter(self):
        hits = self.project.search_snippets("Kael", fields={"synopsis"})
        self.assertTrue(all(field == "synopsis" for _d, field, _s in hits))
        self.assertEqual(len(hits), 1)

    def test_search_snippets_rejects_bad_regex_loudly(self):
        with self.assertRaises(Exception):
            self.project.search_snippets("(", regex=True)

    def test_replace_empty_query_is_noop(self):
        self.assertEqual(self.project.replace("", "x")["replacements"], 0)


class SplitMergeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")
        draft = self.project.documents()[0]
        self.scene_a = self.project.add_document("A", draft.id, "scene")
        self.scene_b = self.project.add_document("B", draft.id, "scene")
        self.scene_c = self.project.add_document("C", draft.id, "scene")
        self.project.write_content(self.scene_a.id, "<p>alpha beta gamma</p>")
        self.project.write_content(self.scene_b.id, "<p>second scene</p>")
        self.project.write_content(self.scene_c.id, "<p>third scene</p>")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def test_split_inserts_sibling_right_after_and_snapshots(self):
        new = self.project.split_document(
            self.scene_a.id, "<p>alpha</p>", "<p>beta gamma</p>")
        self.assertEqual(new.title, "A (2)")
        self.assertEqual(new.kind, "scene")
        self.assertEqual(new.position, self.scene_a.position + 1)
        self.assertEqual(self.project.plain_text(
            self.project.read_content(self.scene_a.id)), "alpha")
        self.assertEqual(self.project.plain_text(
            self.project.read_content(new.id)), "beta gamma")
        names = [s.name for s in self.project.snapshots(self.scene_a.id)]
        self.assertIn("Before split", names)

    def test_split_renumbers_following_siblings_in_binder_order(self):
        self.project.split_document(self.scene_a.id, "<p>alpha</p>", "<p>beta</p>")
        titles = [d.title for d in self.project.documents()]
        self.assertEqual(titles[1:4], ["A", "A (2)", "B"])

    def test_split_title_avoids_collisions(self):
        self.project.split_document(self.scene_a.id, "<p>alpha</p>", "<p>beta</p>")
        new2 = self.project.split_document(
            self.scene_a.id, "<p>alp</p>", "<p>ha beta</p>")
        self.assertEqual(new2.title, "A (3)")

    def test_split_refuses_empty_half(self):
        with self.assertRaises(ValueError):
            self.project.split_document(self.scene_a.id, "<p>alpha beta</p>", "<p></p>")
        with self.assertRaises(ValueError):
            self.project.split_document(self.scene_a.id, "<p></p>", "<p>alpha</p>")

    def test_split_refuses_unknown_document(self):
        with self.assertRaises(KeyError):
            self.project.split_document("nope", "<p>x</p>", "<p>y</p>")

    def test_merge_appends_next_sibling_and_deletes_it(self):
        merged = self.project.merge_documents(self.scene_a.id, self.scene_b.id)
        text = self.project.plain_text(self.project.read_content(self.scene_a.id))
        self.assertIn("alpha beta gamma", text)
        self.assertIn("second scene", text)
        self.assertEqual(merged.word_count, 5)
        remaining = {d.title for d in self.project.documents()}
        self.assertNotIn("B", remaining)
        names = [s.name for s in self.project.snapshots(self.scene_a.id)]
        self.assertIn("Before merge", names)

    def test_merge_keeps_first_title_and_position(self):
        merged = self.project.merge_documents(self.scene_a.id, self.scene_b.id)
        self.assertEqual(merged.title, "A")
        self.assertEqual(merged.id, self.scene_a.id)

    def test_merge_rehomes_second_snapshots(self):
        self.project.create_snapshot(self.scene_b.id, "b history")
        self.project.merge_documents(self.scene_a.id, self.scene_b.id)
        names = [s.name for s in self.project.snapshots(self.scene_a.id)]
        self.assertIn("b history", names)

    def test_merge_requires_immediately_following_sibling(self):
        with self.assertRaises(ValueError):
            self.project.merge_documents(self.scene_a.id, self.scene_c.id)
        with self.assertRaises(ValueError):
            self.project.merge_documents(self.scene_b.id, self.scene_a.id)

    def test_merge_refuses_when_second_has_children(self):
        child = self.project.add_document("child", self.scene_b.id, "note")
        with self.assertRaises(ValueError):
            self.project.merge_documents(self.scene_a.id, self.scene_b.id)
        self.assertIsNotNone(child.id)
        titles = {d.title for d in self.project.documents()}
        self.assertIn("B", titles)

    def test_merge_refuses_same_document_and_unknown_ids(self):
        with self.assertRaises(ValueError):
            self.project.merge_documents(self.scene_a.id, self.scene_a.id)
        with self.assertRaises(KeyError):
            self.project.merge_documents(self.scene_a.id, "nope")

    def test_next_sibling_walks_the_same_parent(self):
        self.assertEqual(self.project.next_sibling(self.scene_a.id).id,
                         self.scene_b.id)
        self.assertEqual(self.project.next_sibling(self.scene_c.id), None)
        self.assertEqual(self.project.next_sibling("nope"), None)

    def test_split_then_merge_round_trips_content(self):
        new = self.project.split_document(
            self.scene_a.id, "<p>alpha</p>", "<p>beta gamma</p>")
        merged = self.project.merge_documents(self.scene_a.id, new.id)
        text = self.project.plain_text(self.project.read_content(self.scene_a.id))
        self.assertIn("alpha", text)
        self.assertIn("beta gamma", text)
        self.assertEqual(merged.word_count, 3)


class WikiLinkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")
        self.root = self.project.documents()[0]
        self.chapter = self.project.add_document(
            "Chapter One", self.root.id, "chapter")
        self.scene_a = self.project.add_document(
            "Scene A", self.chapter.id, "scene")
        self.scene_b = self.project.add_document(
            "Scene B", self.chapter.id, "scene")
        self.project.set_metadata(self.scene_a.id, {"Location": "Mars"})
        self.project.set_metadata(self.scene_b.id, {"Location": "Venus"})

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def test_resolve_link_exact_and_case_insensitive(self):
        self.assertEqual(
            [d.id for d in self.project.resolve_link("Scene A")],
            [self.scene_a.id])
        self.assertEqual(
            [d.id for d in self.project.resolve_link("scene a")],
            [self.scene_a.id])

    def test_resolve_link_substring_offers_candidates_without_winning(self):
        matches = self.project.resolve_link("Scene")
        self.assertEqual(
            {d.id for d in matches},
            {self.scene_a.id, self.scene_b.id})

    def test_resolve_link_broken_and_empty(self):
        self.assertEqual(self.project.resolve_link("Nope"), [])
        self.assertEqual(self.project.resolve_link("   "), [])

    def test_resolve_link_follows_a_retitle(self):
        self.project.update_document(self.scene_a.id, title="Renamed")
        self.assertEqual(
            [d.id for d in self.project.resolve_link("Renamed")],
            [self.scene_a.id])
        self.assertEqual(self.project.resolve_link("Scene A"), [])

    def test_wiki_targets_reports_resolution_per_document(self):
        self.project.write_content(
            self.scene_a.id,
            "<p>See [[Scene B]] and [[Nope]].</p>")
        targets = dict(self.project.wiki_targets(self.scene_a.id))
        self.assertEqual(
            [d.id for d in targets["Scene B"]],
            [self.scene_b.id])
        self.assertEqual(targets["Nope"], [])

    def test_plain_text_strips_wiki_markers_for_counts_and_export(self):
        self.project.write_content(
            self.scene_a.id,
            "<p>See [[Scene B]] and [[Nope]].</p>")
        text = self.project.plain_text(self.project.read_content(self.scene_a.id))
        self.assertNotIn("[[", text)
        self.assertNotIn("]]", text)
        self.assertEqual(text, "See Scene B and Nope.")

    def test_plain_text_stripping_is_idempotent(self):
        self.project.write_content(self.scene_a.id, "<p>See [[Scene B]].</p>")
        once = self.project.plain_text(self.project.read_content(self.scene_a.id))
        self.assertEqual(self.project.plain_text(once), once)

    def test_canonicalize_links_restores_editor_markup_to_wiki_form(self):
        html = (
            '<p>See <a href="lekhak://doc/x"><span style="color:#0000ff;">'
            "Scene B</span></a> and "
            '<a href="lekhak://missing"><span style="color:#c0392b;">'
            "[missing: Nope]</span></a>.</p>"
        )
        canonical = self.project.canonicalize_links(html)
        self.assertIn("[[Scene B]]", canonical)
        self.assertIn("[[Nope]]", canonical)
        self.assertNotIn("<a ", canonical)
        self.assertNotIn("missing:", canonical)

    def test_canonicalize_links_is_idempotent_and_leaves_plain_text_alone(self):
        plain = "<p>Ordinary prose with no links.</p>"
        self.assertEqual(self.project.canonicalize_links(plain), plain)
        once = self.project.canonicalize_links(
            '<p><a href="lekhak://doc/x">Scene B</a></p>')
        self.assertEqual(self.project.canonicalize_links(once), once)


class BookAndTemplateStorageTests(unittest.TestCase):
    """Storage half of 2.1: the TEMPLATES flag, per-folder book settings,
    template listing and cover resolution."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def test_book_settings_roundtrip_blank_removes_and_junk_degrades(self):
        draft = self.project.documents()[0]
        self.assertEqual(self.project.book_settings(draft.id), {})

        self.project.set_book_settings(
            draft.id, {"template": "abc123", "cover": "covers/mh.jpg"})
        self.assertEqual(
            self.project.book_settings(draft.id),
            {"template": "abc123", "cover": "covers/mh.jpg"},
        )

        self.project.set_book_settings(draft.id, {"template": "", "cover": ""})
        self.assertEqual(self.project.book_settings(draft.id), {})

        self.project.set_setting("book_settings", "{not json")
        self.assertEqual(self.project.book_settings(draft.id), {})

    def test_doc_template_set_through_update_document(self):
        draft = self.project.documents()[0]
        chapter = self.project.add_document("Chapter", draft.id, "chapter")
        self.assertEqual(chapter.doc_template, "")
        self.project.update_document(chapter.id, doc_template="no-title")
        found = next(d for d in self.project.documents() if d.id == chapter.id)
        self.assertEqual(found.doc_template, "no-title")

    def test_effective_cover_autodetects_then_honours_explicit_path(self):
        draft = self.project.documents()[0]
        self.assertIsNone(self.project.effective_cover(draft.id))

        images = self.project.root / "images" / draft.id
        images.mkdir(parents=True)
        (images / "cover.png").write_bytes(b"png-bytes")
        self.assertEqual(self.project.effective_cover(draft.id),
                         images / "cover.png")

        self.project.set_book_settings(draft.id, {"cover": "covers/missing.jpg"})
        self.assertIsNone(self.project.effective_cover(draft.id))

        explicit = self.project.root / "covers" / "missing.jpg"
        explicit.parent.mkdir(parents=True)
        explicit.write_bytes(b"jpg-bytes")
        self.assertEqual(self.project.effective_cover(draft.id), explicit)


if __name__ == "__main__":
    unittest.main()
