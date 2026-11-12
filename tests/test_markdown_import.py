"""Markdown import — Lekhak's own export, and Scrivener's, metadata intact.

The point of the importer is that a writer's metadata is data, not
decoration: every key becomes a field or fills one, and nothing but the
export's own bookkeeping is dropped.
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lekhak.project import Project  # noqa: E402


class MarkdownImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = Project.create(self.root / "Novel", "Novel")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def _titled(self, project=None):
        project = project or self.project
        return {document.title: document for document in project.documents()}

    def test_lekhak_export_round_trip_keeps_structure_and_metadata(self):
        draft = self.project.documents()[0]
        folder = self.project.add_document("Novel", draft.id, "folder")
        chapter = self.project.add_document("The War", folder.id, "chapter")
        scene = self.project.add_document("First Light", chapter.id, "scene")
        self.project.write_content(chapter.id, "<p>War prose.</p>")
        self.project.write_content(scene.id, "<p>Scene <b>bold</b>.</p>")
        self.project.set_metadata(
            scene.id,
            {"Type": "Scene", "PoV": "Noir", "Date": "2430-07-04"},
            {"Date": "date"})
        self.project.update_document(scene.id, synopsis="A cold clinic.")
        exported = self.root / "out.md"
        self.project.export_markdown(exported)
        text = exported.read_text(encoding="utf-8")
        self.assertIn("PoV: Noir", text)
        self.assertIn("Synopsis: A cold clinic.", text)

        other = Project.create(self.root / "Second", "Second")
        try:
            summary = other.import_markdown([exported])
            documents = self._titled(other)
            # Folders contribute to the export now, so the root and the
            # "Novel" folder travel with the chapter and scene.
            self.assertEqual(summary["documents"], 4)
            self.assertIn("The War", documents)
            copy = documents["First Light"]
            self.assertEqual(other.type_of(copy), "Scene")
            # A scene with no Parent lands under the chapter above it.
            parent = next(d for d in other.documents()
                          if d.id == copy.parent_id)
            self.assertEqual(parent.title, "The War")
            self.assertEqual(other.metadata(copy.id)["PoV"], "Noir")
            self.assertEqual(other.metadata_entries(copy.id)["Date"][0],
                             "2430-07-04")
            self.assertEqual(other.metadata_entries(copy.id)["Date"][1],
                             "date")
            self.assertEqual(
                next(d for d in other.documents()
                     if d.id == copy.id).synopsis, "A cold clinic.")
            self.assertIn("PoV", summary["fields"])
            self.assertNotIn("Type", summary["fields"])
        finally:
            other.close()

    def test_scrivener_export_keeps_every_writer_key(self):
        source = self.root / "marginally.md"
        source.write_text(
            "Title: Marginally Human\n"
            "Author: Vidyut Gore\n"
            "Copyright: Copyright 2026, Vidyut Gore.\n"
            "\n"
            "===DOCUMENT===\n"
            "\n"
            "Type: Chapter\n"
            "ID: 2\n"
            "Position: 3\n"
            "ParentPosition: 1\n"
            "Level1: Prologue\n"
            "Title: Prologue\n"
            "Status: Done\n"
            "Synopsis: The opening.\n"
            "\n"
            "===TEXT===\n"
            "\n"
            "# Prologue #\n"
            "\n"
            "===DOCUMENT===\n"
            "\n"
            "Type: Scene\n"
            "ID: 3\n"
            "Parent: Prologue\n"
            "Title: Ekta Dorms clinic\n"
            "PoV: Aaxyl\n"
            "Date: 2430-07-04\n"
            "Status: Done\n"
            "Label: Looks done\n"
            "Keywords: Hersh, Clinic\n"
            "Reviewed: true\n"
            "Synopsis: Two orphans meet.\n"
            "\n"
            "===TEXT===\n"
            "\n"
            '"Blink if you understand me."\n',
            encoding="utf-8")

        summary = self.project.import_markdown([source])
        documents = self._titled(self.project)
        self.assertEqual(summary["documents"], 2)
        chapter = documents["Prologue"]
        scene = documents["Ekta Dorms clinic"]
        self.assertEqual(chapter.kind, "chapter")
        self.assertEqual(scene.parent_id, chapter.id)
        self.assertEqual(scene.kind, "scene")
        self.assertEqual(scene.synopsis, "Two orphans meet.")
        held = self.project.metadata(scene.id)
        self.assertEqual(held["PoV"], "Aaxyl")
        self.assertEqual(held["Status"], "Done")
        self.assertEqual(held["Label"], "Looks done")
        self.assertEqual(held["Keywords"], "Hersh, Clinic")
        # Scrivener's bookkeeping shapes the tree; it never becomes a field.
        for structural in ("ID", "Position", "ParentPosition", "Level1",
                           "Hierarchy", "Parent"):
            self.assertNotIn(structural, held)
        # A value's shape decides the field type it is created with.
        self.assertEqual(
            self.project.metadata_entries(scene.id)["Reviewed"][1],
            "checkbox")
        self.assertEqual(
            self.project.metadata_entries(scene.id)["Date"][1], "date")
        self.assertIn("PoV", summary["fields"])
        self.assertEqual(
            self.project.project_metadata()["Author"][0], "Vidyut Gore")
        # The prose arrives (the repeated title heading is not prose).
        self.assertNotIn("<b>Prologue</b>",
                         self.project.read_content(scene.id))


if __name__ == "__main__":
    unittest.main()