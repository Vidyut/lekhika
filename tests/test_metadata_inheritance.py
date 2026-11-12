"""Metadata inheritance: a field set on a folder is the value for
everything inside it.

Nearest first, and the first non-empty value wins: the document's own,
then the nearest folder that has one, then the next one up. An empty
field means *inherit*, not *blank it*.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lekhak.project import Project
from lekhak.templates import TemplateSet, create_template_set


class MetadataInheritanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(
            Path(self.temporary.name) / "Novel", "Novel")
        self.book = self.project.documents()[0]
        self.part = self.project.add_document("Part One", self.book.id,
                                              "folder")
        self.chapter = self.project.add_document("Chapter One", self.part.id,
                                                 "chapter")
        self.scene = self.project.add_document("Alpha", self.chapter.id,
                                               "scene")
        self.under = self.project.add_document("Beta", self.part.id, "scene")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def test_a_folders_value_is_what_its_children_answer_with(self):
        self.project.set_metadata(self.part.id, {"Status": "Revised"})

        inherited = self.project.metadata_inherited(self.scene.id)

        self.assertEqual(inherited["Status"], "Revised")
        self.assertEqual(
            self.project.metadata_inherited(self.under.id)["Status"],
            "Revised")

    def test_the_nearest_value_wins(self):
        self.project.set_metadata(self.part.id, {"Status": "Revised"})
        self.project.set_metadata(self.chapter.id, {"Status": "Done"})

        self.assertEqual(
            self.project.metadata_inherited(self.scene.id)["Status"], "Done")
        # A sibling that is not inside the chapter keeps the folder's.
        self.assertEqual(
            self.project.metadata_inherited(self.under.id)["Status"],
            "Revised")

    def test_an_empty_field_inherits_rather_than_blanking(self):
        self.project.set_metadata(self.part.id, {"Status": "Revised"})
        self.project.set_metadata(self.scene.id, {"Status": ""})

        self.assertEqual(
            self.project.metadata_inherited(self.scene.id)["Status"],
            "Revised")

    def test_a_template_prints_the_inherited_value(self):
        self.project.set_metadata(self.part.id, {"Status": "Revised"})
        set_dir = create_template_set("inherited", self.project.root).directory
        (set_dir / "folder.txt").write_text(
            "Status[${Status}]", encoding="utf-8")
        templates = TemplateSet.load("inherited", set_dir)

        destination = self.project.root.parent / "book.txt"
        self.project.export_text(destination, None, templates)
        exported = destination.read_text(encoding="utf-8")

        self.assertIn("Status[Revised]", exported)


if __name__ == "__main__":
    unittest.main()
