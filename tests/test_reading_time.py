"""Reading time: the estimate beside the word count.

It is arithmetic on the count — two hundred and fifty words a minute,
the usual figure for adult prose — and it is always an estimate, so it
is marked as one and it sits next to the words it came from.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lekhak.app import MainWindow  # noqa: E402
from lekhak.project import Project, reading_time  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhak")
_application.setApplicationName("Lekhak")
_settings_dir = tempfile.mkdtemp(prefix="lekhak-reading-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class ReadingTimeTests(unittest.TestCase):
    def test_the_estimate_follows_the_count(self):
        self.assertEqual(reading_time(0), "")
        self.assertEqual(reading_time(1), "1 min")
        self.assertEqual(reading_time(250), "1 min")
        self.assertEqual(reading_time(1000), "4 min")
        self.assertEqual(reading_time(15000), "1 hr")
        self.assertEqual(reading_time(100000), "6 hr 40 min")

    def test_the_status_bar_shows_it_beside_the_words(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(
            Path(self.temporary.name) / "Novel", "Novel")
        self.scene = self.project.add_document(
            "Alpha", self.project.documents()[0].id, "scene")
        self.window = MainWindow()
        self.window._switch_project(self.project)
        try:
            self.window.editor.setPlainText(" ".join(["word"] * 1000))
            self.window._update_word_count()
            shown = self.window.word_count_label.text()
            self.assertIn("1,000 words", shown)
            self.assertIn("~4 min reading", shown)
        finally:
            self.window.project = None
            self.project.close()
            self.temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
