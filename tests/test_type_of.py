"""The protected Type role (docs/MODEL.md): type_of, default_type, the
two base roles, and the per-group catalogue the dropdown reads (1.17)."""

import tempfile
import unittest
from pathlib import Path

from lekhak.project import Project


class TypeOfTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel")
        self.draft = self.project.documents()[0]

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def test_two_base_roles_read_as_folder_and_document(self):
        chapter = self.project.add_document("Ch", self.draft.id, "chapter")
        scene = self.project.add_document("Sc", chapter.id, "scene")
        note = self.project.add_document("Nt", chapter.id, "note")
        self.assertEqual(self.project.default_type(self.draft), "Folder")
        self.assertEqual(self.project.default_type(chapter), "Document")
        self.assertEqual(self.project.default_type(scene), "Document")
        self.assertEqual(self.project.default_type(note), "Document")

    def test_book_settings_do_not_promote_a_folder(self):
        # The role is what the writer says it is now (1.17): a folder
        # with a book template is still a folder, and "Book" is a role
        # one adds rather than one the app guesses.
        self.project.set_book_settings(self.draft.id, {"template": "x"})
        self.assertEqual(self.project.default_type(self.draft), "Folder")
        self.assertEqual(self.project.type_of(self.draft), "Folder")

    def test_stored_type_wins_over_the_default(self):
        chapter = self.project.add_document("Ch", self.draft.id, "chapter")
        self.project.set_metadata(chapter.id, {"Type": "Short story"})
        self.assertEqual(self.project.type_of(chapter), "Short story")

    def test_blank_stored_type_falls_back(self):
        chapter = self.project.add_document("Ch", self.draft.id, "chapter")
        self.project.set_metadata(chapter.id, {"Type": "   "})
        self.assertEqual(self.project.type_of(chapter), "Document")

    def test_defaults_are_not_seeded_into_storage(self):
        # A stored default would shadow a later heal, so creation writes
        # no Type at all.
        scene = self.project.add_document("Sc", self.draft.id, "scene")
        self.assertNotIn("Type", self.project.metadata(scene.id))
        self.assertEqual(self.project.type_of(scene), "Document")

    def test_type_values_lists_what_the_project_uses(self):
        chapter = self.project.add_document("Ch", self.draft.id, "chapter")
        self.project.set_metadata(chapter.id, {"Type": "Weird"})
        other = self.project.add_document("Sc", chapter.id, "scene")
        self.project.set_metadata(other.id, {"Type": "Weird"})
        # Only explicitly set values count; derived defaults are not
        # stored, so they never pad the dropdown's project list.
        self.assertEqual(self.project.type_values(), ["Weird"])

    def test_a_group_offers_only_its_own_roles(self):
        chapter = self.project.add_document("Ch", self.draft.id, "chapter")
        self.project.set_metadata(chapter.id, {"Type": "Scene"})
        self.project.set_metadata(self.draft.id, {"Type": "Book"})
        self.assertEqual(self.project.known_types("document"), ["Document", "Scene"])
        self.assertEqual(self.project.known_types("folder"), ["Folder", "Book"])

    def test_a_new_role_is_kept_for_the_documents_that_follow(self):
        self.assertEqual(self.project.add_type("document", "Part"), "Part")
        self.assertIn("Part", self.project.known_types("document"))
        # A folder's list is its own: the new role never crosses over.
        self.assertNotIn("Part", self.project.known_types("folder"))

    def test_a_new_role_is_kept_once(self):
        self.project.add_type("document", "Part")
        self.project.add_type("document", "part")
        self.assertEqual(
            [name for name in self.project.known_types("document")
             if name.casefold() == "part"], ["Part"])

    def test_a_field_known_to_one_document_shows_on_the_group(self):
        chapter = self.project.add_document("Ch", self.draft.id, "chapter")
        scene = self.project.add_document("Sc", chapter.id, "scene")
        self.project.set_metadata(scene.id, {"POV": "Mira"})
        self.assertIn("POV", self.project.known_fields("document"))
        self.assertNotIn("POV", self.project.known_fields("folder"))

    def test_an_empty_field_name_is_kept_for_the_group(self):
        # Only filled values are stored, so the name itself has to be
        # remembered or no other document would ever show the field.
        self.project.add_field("document", "POV")
        self.assertIn("POV", self.project.known_fields("document"))
        self.assertNotIn("POV", self.project.known_fields("folder"))


if __name__ == "__main__":
    unittest.main()
