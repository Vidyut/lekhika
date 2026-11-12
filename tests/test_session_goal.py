"""BUILD-NOW 6: Session goal — a per-day word target with live progress.

Acceptance from docs/BUILD-NOW.md: a target survives a restart; writing
500 words shows 500 of the goal without re-opening a dialog. The counts
moved to a permanent status-bar widget in the same slice: a timed
showMessage clears to nothing once its timeout runs, so progress beside
"Saved" would otherwise vanish mid-sentence.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate, QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QInputDialog  # noqa: E402

from lekhika.app import MainWindow  # noqa: E402
from lekhika.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-test-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)

_TODAY = QDate.currentDate().toString(Qt.ISODate)


class SessionGoalTests(unittest.TestCase):
    def setUp(self):
        QSettings().remove("session/lastProject")
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")
        self.chapter = self.project.add_document(
            "Chapter One", parent_id=None, kind="chapter"
        )
        self.project.set_setting("last_document", self.chapter.id)
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _set_goal(self, value, accepted=True):
        with patch.object(QInputDialog, "getInt",
                          return_value=(value, accepted)):
            self.window.set_session_goal()

    def _write(self, words):
        self.window.editor.setPlainText(" ".join(["word"] * words))
        self.window.save_current()

    def test_goal_progress_appears_after_writing_without_a_dialog(self):
        self._set_goal(500)

        self._write(500)

        label = self.window.word_count_label.text()
        self.assertIn("500 / 500 today", label)
        self.assertIn("+500 this session", label)

    def test_the_target_survives_a_restart(self):
        self._set_goal(1000)

        self.window.project = None        # close without closeEvent
        fresh = MainWindow()
        fresh._switch_project(self.project)
        try:
            self.assertEqual(self.project.get_setting("session_goal"), "1000")
            self.assertIn("/ 1,000 today", fresh.word_count_label.text())
        finally:
            fresh.project = None

    def test_no_goal_means_no_goal_clutter(self):
        self.window._update_word_count()

        label = self.window.word_count_label.text()
        self.assertNotIn("today", label)
        self.assertNotIn("this session", label)

    def test_a_new_day_rebases_the_count_but_keeps_the_target(self):
        self._set_goal(500)
        self._write(300)
        self.assertIn("300 / 500 today", self.window.word_count_label.text())

        self.project.set_setting("session_goal_day", "2000-01-01")
        self.window._update_word_count()

        self.assertIn("0 / 500 today", self.window.word_count_label.text())
        self.assertEqual(self.project.get_setting("session_goal_day"), _TODAY)
        self.assertEqual(self.project.get_setting("session_goal"), "500")

    def test_cancelling_the_dialog_changes_nothing(self):
        self._set_goal(500)

        self._set_goal(999, accepted=False)

        self.assertEqual(self.project.get_setting("session_goal"), "500")

    def test_zero_clears_the_goal(self):
        self._set_goal(500)

        self._set_goal(0)

        self.assertNotIn("today", self.window.word_count_label.text())

    def test_counts_survive_a_save_notice(self):
        self._set_goal(500)
        self._write(500)
        # "Saved" is a timed showMessage; the label has to hold the
        # counts through it instead of waiting for the timeout to clear
        # the bar to nothing.
        self.assertIn("Saved", self.window.statusBar().currentMessage())
        self.assertIn("500 / 500 today", self.window.word_count_label.text())

    def test_status_message_and_label_coexist(self):
        self._set_goal(500)   # ends with a temporary message

        self.assertIn("Daily goal: 500 words",
                      self.window.statusBar().currentMessage())
        self.assertIn("today", self.window.word_count_label.text())

    def test_no_project_refuses_the_dialog(self):
        self.window.project = None

        with patch.object(QInputDialog, "getInt") as get_int:
            self.window.set_session_goal()

        get_int.assert_not_called()
        self.assertIn("Open a project first",
                      self.window.statusBar().currentMessage())


if __name__ == "__main__":
    unittest.main()
