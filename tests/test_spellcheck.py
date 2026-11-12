"""BUILD-NOW 8 (spell half): the deterministic underliner.

The engine and the checker are patched — no process spawns in the
committed suite, same house rule as pdf's converter — except the one
guarded test that runs the real aspell when this machine has it.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lekhika import spellcheck  # noqa: E402
from lekhika.app import MainWindow  # noqa: E402
from lekhika.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-spell-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class MisspellingsTests(unittest.TestCase):
    def setUp(self):
        spellcheck.reset()
        self.cache: dict[str, bool] = {}

    def tearDown(self):
        spellcheck.reset()

    def _fake_checker(self, unknown):
        """The two halves the suite refuses to spawn: engine present,
        verdicts canned. `unknown` None simulates a failing checker."""
        engine = patch.object(spellcheck, "find_engine",
                              return_value=["fake"])
        checker = patch.object(spellcheck, "_unknown", return_value=unknown)
        engine_mock = engine.start()
        checker_mock = checker.start()
        self.addCleanup(engine.stop)
        self.addCleanup(checker.stop)
        return engine_mock, checker_mock

    def test_rejected_words_come_back_as_ranges(self):
        text = "teh adn foolow"
        self._fake_checker({"teh", "adn", "foolow"})

        ranges = spellcheck.misspellings(text, self.cache)

        self.assertEqual([text[start:end] for start, end in ranges],
                         ["teh", "adn", "foolow"])

    def test_dictionary_words_stay_unflagged(self):
        text = "the wind rose"
        self._fake_checker(set())

        ranges = spellcheck.misspellings(text, self.cache)

        self.assertEqual(ranges, [])

    def test_words_are_cached_and_the_checker_runs_once(self):
        text = "teh wind"
        _, checker = self._fake_checker({"teh"})

        first = spellcheck.misspellings(text, self.cache)
        second = spellcheck.misspellings(text, self.cache)

        self.assertEqual(checker.call_count, 1)
        self.assertEqual(first, second)
        self.assertEqual([text[start:end] for start, end in second], ["teh"])

    def test_a_broken_checker_flags_nothing_and_caches_nothing(self):
        _, checker = self._fake_checker(None)

        self.assertEqual(spellcheck.misspellings("teh wind", self.cache), [])
        self.assertEqual(spellcheck.misspellings("teh wind", self.cache), [])

        self.assertEqual(checker.call_count, 2)   # retried, never trusted

    def test_no_engine_means_no_squiggles(self):
        with patch.object(spellcheck, "find_engine", return_value=None):
            self.assertEqual(spellcheck.misspellings("teh", self.cache), [])

    def test_empty_text_never_reaches_the_checker(self):
        _, checker = self._fake_checker({"teh"})

        self.assertEqual(spellcheck.misspellings("", self.cache), [])

        checker.assert_not_called()

    def test_offsets_are_utf16_units(self):
        text = "\U0001f600 teh"   # 😀 = two UTF-16 units, one code point
        self._fake_checker({"teh"})

        ranges = spellcheck.misspellings(text, self.cache)

        self.assertEqual(ranges, [(3, 6)])

    def test_the_word_list_teaches_the_pass_its_words(self):
        # The writer's own list is consulted without the engine ever
        # hearing about the words in it.
        _, checker = self._fake_checker({"teh", "Virelia"})
        with tempfile.TemporaryDirectory() as tmp:
            list_file = Path(tmp) / "dictionary.txt"
            list_file.write_text("Virelia\n", encoding="utf-8")
            spellcheck.set_word_list(list_file)

            ranges = spellcheck.misspellings("teh Virelia", self.cache)

        self.assertEqual(
            ["teh Virelia"[start:end] for start, end in ranges], ["teh"])
        unseen = checker.call_args[0][1]
        self.assertNotIn("Virelia", unseen)

    def test_editing_the_word_list_teaches_the_next_pass(self):
        _, checker = self._fake_checker({"teh"})
        with tempfile.TemporaryDirectory() as tmp:
            list_file = Path(tmp) / "dictionary.txt"
            list_file.write_text("", encoding="utf-8")
            spellcheck.set_word_list(list_file)
            self.assertEqual(
                len(spellcheck.misspellings("teh", self.cache)), 1)

            list_file.write_text("teh\n", encoding="utf-8")
            moment = list_file.stat().st_mtime + 2
            os.utime(list_file, (moment, moment))
            self.assertEqual(spellcheck.misspellings("teh", self.cache), [])

    def test_entity_candidates_harvest_capitalised_unknowns(self):
        # Virelia appears in title/synopsis/prose; Kael only in POV
        # metadata. The system dictionary would flag both, so both
        # are candidates — The/This sentence starters are not.
        with tempfile.TemporaryDirectory() as tmp:
            project = Project.create(Path(tmp) / "Novel.lekhika", "Novel")
            draft = project.documents()[0]
            chapter = project.add_document("Chapter One", draft.id, "chapter")
            scene = project.add_document("First Light", chapter.id, "scene")
            project.write_content(
                scene.id, "<p>Virelia rose. Virelia stayed. The wind rose.</p>")
            project.update_document(scene.id, synopsis="Virelia watch")
            project.set_metadata(scene.id, {"POV": "Kael"},
                                 {"POV": "text"}, {"POV": []})
            try:
                with patch.object(spellcheck, "_unknown",
                                  return_value={"Virelia", "Kael"}):
                    candidates = dict(spellcheck.entity_candidates(project))
                self.assertEqual(candidates.get("Virelia"), 3)
                self.assertEqual(candidates.get("Kael"), 1)
                self.assertNotIn("The", candidates)
            finally:
                project.close()

    @unittest.skipUnless(shutil.which("aspell"), "aspell not installed")
    def test_the_real_dictionary_passes_ordinary_words(self):
        text = "teh hello foolow its"

        ranges = spellcheck.misspellings(text, self.cache)

        self.assertEqual([text[start:end] for start, end in ranges],
                         ["teh", "foolow"])


class SquiggleWindowTests(unittest.TestCase):
    def setUp(self):
        QSettings().remove("ui/spellSquiggles")
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(
            Path(self.temporary.name) / "Book", "Book"
        )
        draft = self.project.documents()[0]
        chapter = self.project.add_document("Chapter", draft.id, "chapter")
        self.scene = self.project.add_document("Scene", chapter.id, "scene")
        self.project.write_content(self.scene.id, "<p>teh wind rose.</p>")
        self.window = MainWindow()
        self.window._switch_project(self.project)
        self.window._select_document(self.scene.id)
        self._fake_checker({"teh"})

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()
        QSettings().remove("ui/spellSquiggles")

    def _fake_checker(self, unknown):
        """Engine present, verdicts canned — the window tests need the
        same refusal to spawn as the unit tests above."""
        engine = patch.object(spellcheck, "find_engine",
                              return_value=["fake"])
        checker = patch.object(spellcheck, "_unknown", return_value=unknown)
        engine.start()
        checker.start()
        self.addCleanup(engine.stop)
        self.addCleanup(checker.stop)

    def test_squiggles_start_disabled(self):
        # Off until asked for: a fresh project would light up with
        # invented names before there is a dictionary to feed it.
        self.assertFalse(self.window.spell_enabled)
        self.assertFalse(self.window.spell_action.isChecked())

    def test_enabling_marks_and_disabling_clears(self):
        self.window.spell_action.setChecked(True)
        selections = self.window.editor.extraSelections()
        self.window.spell_action.setChecked(False)

        self.assertEqual(len(selections), 1)
        self.assertFalse(QSettings().value(
            "ui/spellSquiggles", False, type=bool))
        self.assertEqual(self.window.editor.extraSelections(), [])

    def test_without_an_engine_the_toggle_refuses_and_says_so(self):
        no_engine = patch.object(spellcheck, "find_engine",
                                 return_value=None)
        no_engine.start()
        self.addCleanup(no_engine.stop)
        self.window.spell_action.setChecked(True)

        self.assertFalse(self.window.spell_action.isChecked())
        self.assertFalse(self.window.spell_enabled)
        self.assertIn("No spell checker",
                      self.window.statusBar().currentMessage())

    def test_a_load_refreshes_and_typing_schedules_a_refresh(self):
        self.window.spell_action.setChecked(True)
        self.assertEqual(len(self.window.editor.extraSelections()), 1)

        # The load path bypasses _content_changed's debounce and
        # refreshes directly, so a switch never shows stale marks.
        self.window._select_document(self.scene.id)
        self.assertEqual(len(self.window.editor.extraSelections()), 1)

        # Typing schedules instead of running: the checker is a
        # process spawn, so it waits for the pause.
        self.window.editor.setPlainText("teh wind")
        self.assertTrue(self.window.spell_timer.isActive())

        QTest.qWait(700)
        self.assertEqual(len(self.window.editor.extraSelections()), 1)


if __name__ == "__main__":
    unittest.main()
