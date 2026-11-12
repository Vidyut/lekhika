"""Template slots: the pure half.

validate_slot, slot paths, atomic write and the Qt round-trip sniff — the
machinery a template set is actually made of, and what exports run on.

The in-app slot editor that used to live here was removed: it reached
templates only through a dialog two menus deep, and a writer could not see
or duplicate the shipped default from it. Templates are reached through the
Templates view instead (docs/TEMPLATES.md); this file covers what a template
*is*, not the UI for it.
"""

import tempfile
import unittest
from pathlib import Path

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from lekhika.app import _roundtrip_issues  # noqa: E402
from lekhika.templates import (  # noqa: E402
    BUILTIN_HTML, slot_file_path, validate_slot, write_slot,
)

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")


class ValidateSlotTests(unittest.TestCase):
    def test_known_placeholders_are_clean(self):
        text = ("${Title}\n${Content}\n${ChapterNumber} ${SceneNumber} "
                "${WordsTotal} ${WordCount} ${Kind} ${Synopsis} "
                "${ToC} ${children}")
        self.assertEqual(validate_slot(text), [])

    def test_unknown_placeholder_is_flagged(self):
        self.assertIn("unknown placeholder ${Bogus}",
                      validate_slot("Hello ${Bogus}"))

    def test_metadata_prefix_is_allowed(self):
        self.assertEqual(
            validate_slot("${Metadata.Author} ${Metadata.World}"), [])

    def test_keys_are_case_sensitive(self):
        # fill() looks values up exactly, so ${title} renders empty and
        # the editor must say so.
        self.assertIn("unknown placeholder ${title}",
                      validate_slot("${title}"))

    def test_conditionals_balance(self):
        self.assertEqual(
            validate_slot("$if(Key)yes$else$endif"), [])

        def has(needle, text):
            return any(needle in issue for issue in validate_slot(text))

        self.assertTrue(has("unclosed $if", "$if(Key)yes"))
        self.assertTrue(has("$endif without a matching $if", "yes$endif"))
        self.assertTrue(has("$else without a matching $if", "yes$else"))
        self.assertTrue(has("unclosed $if", "$if(A)$if(B)nested"))

    def test_stray_marker_without_parentheses_is_flagged(self):
        issues = validate_slot("$if Key)oops")
        self.assertTrue(any("stray or malformed" in issue
                            for issue in issues))

    def test_builtin_slots_validate_clean(self):
        for slot, text in BUILTIN_HTML.items():
            self.assertEqual(validate_slot(text), [], slot)


class SlotPathAndWriteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_slot_file_path_shapes(self):
        self.assertEqual(
            slot_file_path(self.directory, "folder", document=False,
                           html=True),
            self.directory / "folder.html")
        self.assertEqual(
            slot_file_path(self.directory, "no-title", document=True,
                           html=False),
            self.directory / "documents" / "no-title.txt")

    def test_write_slot_is_atomic_and_creates_documents_dir(self):
        path = write_slot(self.directory, "no-title", document=True, html=True,
                          text="${Content}<!--custom-->")

        self.assertEqual(path, self.directory / "documents" / "no-title.html")
        self.assertEqual(path.read_text(encoding="utf-8"),
                         "${Content}<!--custom-->")
        # No leftover temp files from the atomic write.
        self.assertEqual(
            [p.name for p in path.parent.iterdir() if p.name.startswith(".")],
            [])


class RoundtripSniffTests(unittest.TestCase):
    def test_wellformed_html_keeps_placeholders(self):
        self.assertEqual(
            _roundtrip_issues('<h1>${Title}</h1>\n${Content}'), [])

    def test_unterminated_comment_swallows_a_placeholder(self):
        issues = _roundtrip_issues("<p>before</p><!-- ${Title}")
        self.assertTrue(any("${Title}" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
