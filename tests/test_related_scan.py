"""Related-file mentions must mean something.

A text title matches by meaning; a bare number matches by coincidence, and a
pasted URL is not a reference to this project. Both were being listed as
"mentions" — the author's own binder has chapters named "2", "3", "4", so
every year and every copied link in the prose produced a phantom relation,
and the Mentions panel became a list of chapters that had nothing to do with
the text.
"""

import tempfile
import unittest
from pathlib import Path

from lekhika.project import Project


class MentionScanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel",
                                      "Novel")
        self.root = self.project.documents()[0]

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def _scene(self, title: str, text: str):
        scene = self.project.add_document(title, self.root.id, "scene")
        self.project.write_content(scene.id, f"<p>{text}</p>")
        return scene

    def _chapter(self, title: str, text: str):
        chapter = self.project.add_document(title, self.root.id, "chapter")
        self.project.write_content(chapter.id, f"<p>{text}</p>")
        return chapter

    def _mentions_of(self, document_id):
        mentions, _ = self.project.related_documents(document_id)
        return [document.id for document in mentions]

    def _mentioned_by(self, document_id):
        _, mentioned_by = self.project.related_documents(document_id)
        return [document.id for document in mentioned_by]

    def test_a_numeric_title_is_not_a_mention(self):
        body = self._scene("Body", "The 2024 season was long and hard.")
        self._scene("2", "Chapter two.")
        self.assertEqual(self._mentions_of(body.id), [])

    def test_a_pasted_url_is_not_a_mention(self):
        body = self._scene("Body", "Source: https://example.com/2024/2/x")
        self._scene("2", "Chapter two.")
        self.assertEqual(self._mentions_of(body.id), [])

    def test_a_text_title_still_matches_by_meaning(self):
        body = self._scene("Body", "Then Someone left the wada.")
        character = self._scene("Someone", "Notes and history.")
        self.assertIn(character.id, self._mentions_of(body.id))

    def test_a_numeric_title_collects_no_mentions_either(self):
        """The reverse direction: a document *called* "2" must not be listed
        as mentioned by everything containing a year or a link."""
        chapter_two = self._scene("2", "Chapter two.")
        self._scene("Body", "The 2024 season. See https://x.example/2/slash")
        _mentions, mentioned_by = self.project.related_documents(chapter_two.id)
        self.assertEqual(mentioned_by, [])

    def test_whole_word_matching_is_unchanged(self):
        body = self._scene("Body", "Somewhere rode out at dawn.")
        rid = self._scene("Rid", "Notes.")
        self.assertNotIn(rid.id, self._mentions_of(body.id))

    def test_a_document_is_never_related_to_itself(self):
        """"Prologue mentioned by Prologue" is the same document twice —
        a self-match is noise no matter how often the prose says its own
        title."""
        prologue = self._scene(
            "Prologue", "Prologue began with rain. The prologue said more.")
        self.assertNotIn(prologue.id, self._mentions_of(prologue.id))
        self.assertNotIn(prologue.id, self._mentioned_by(prologue.id))

    def test_two_documents_sharing_one_name_read_as_one(self):
        """A chapter and a scene both called "Prologue" are one document
        to the writer — listing them as each other's relations is the
        self-match again. Case aside, too."""
        chapter = self._chapter("Prologue", "Rain.")
        scene = self._scene("Prologue", "Prologue began with rain.")
        case_variant = self._scene("prologue", "More rain.")
        self.assertNotIn(chapter.id, self._mentions_of(scene.id))
        self.assertNotIn(scene.id, self._mentioned_by(chapter.id))
        self.assertNotIn(case_variant.id, self._mentions_of(scene.id))
        self.assertNotIn(scene.id, self._mentioned_by(case_variant.id))
        # A differently named document still relates to the scene.
        other = self._scene("Body", "Then Prologue ended.")
        self.assertIn(chapter.id, self._mentions_of(other.id))

    def test_a_chapter_title_is_ignored_in_another_case(self):
        """The lowercase word in prose is not a reference to the chapter.
        Both directions: the chapter must not list the scene, and the
        scene must not list the chapter."""
        prologue = self._chapter("Prologue", "Rain.")
        body = self._scene("Body", "The prologue of the letter dragged on.")
        self.assertNotIn(prologue.id, self._mentions_of(body.id))
        self.assertNotIn(body.id, self._mentioned_by(prologue.id))

    def test_a_chapter_title_matches_when_written_exactly(self):
        """A chapter is mentioned when the writer wrote its title as it
        is titled — same case, whole word."""
        prologue = self._chapter("Prologue", "Rain.")
        body = self._scene("Body", "Prologue opened the book.")
        self.assertIn(prologue.id, self._mentions_of(body.id))
        self.assertIn(body.id, self._mentioned_by(prologue.id))

    def test_a_non_chapter_title_still_matches_by_case_difference(self):
        """Names are vocabulary however the writer typed them: only
        Chapters earned the exact rule."""
        body = self._scene("Body", "Then someone left at dawn.")
        character = self._scene("Someone", "Notes.")
        self.assertIn(character.id, self._mentions_of(body.id))
        self.assertIn(body.id, self._mentioned_by(character.id))

    def test_the_exact_rule_follows_type_not_physical_kind(self):
        """A scene whose Type is Chapter plays by the Chapter rule; a
        chapter whose Type says otherwise does not (docs/MODEL.md)."""
        strict = self._scene("Intro", "Rain.")
        self.project.set_metadata(strict.id, {"Type": "Chapter"})
        body = self._scene("Body", "intro of the letter.")
        self.assertNotIn(body.id, self._mentioned_by(strict.id))

        loose = self._chapter("Opening", "Rain.")
        self.project.set_metadata(loose.id, {"Type": "Scene"})
        body2 = self._scene("Body2", "The opening of the letter.")
        self.assertIn(body2.id, self._mentioned_by(loose.id))


if __name__ == "__main__":
    unittest.main()
