"""BUILD-NOW 3: "N words selected" in the status bar.

The behaviour predates the build-now list (it arrived with the outliner
columns); this file pins its two acceptance clauses: selecting a paragraph
shows its count, deselecting restores the document count.
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lekhika.app import MainWindow  # noqa: E402
from lekhika.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-test-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class SelectionCountTests(unittest.TestCase):
    def setUp(self):
        QSettings().remove("session/lastProject")
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")
        self.draft = self.project.documents()[0]
        self.chapter = self.project.add_document(
            "Chapter One", parent_id=self.draft.id, kind="chapter"
        )
        self.project.write_content(
            self.chapter.id,
            "<p>One two three four five</p><p>Six seven eight</p>",
        )
        # Land on the chapter, not the empty Draft folder.
        self.project.set_setting("last_document", self.chapter.id)
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _select_first_paragraph(self):
        cursor = self.window.editor.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(23, QTextCursor.MoveMode.KeepAnchor)   # "One … five"
        self.window.editor.setTextCursor(cursor)

    def test_selecting_a_paragraph_shows_its_count(self):
        self._select_first_paragraph()

        message = self.window.word_count_label.text()

        self.assertIn("5 selected", message)
        self.assertIn("8 words", message)          # the document count stays

    def test_deselecting_restores_the_document_count_alone(self):
        self._select_first_paragraph()
        self.assertIn("selected", self.window.word_count_label.text())

        cursor = self.window.editor.textCursor()
        cursor.clearSelection()
        self.window.editor.setTextCursor(cursor)

        message = self.window.word_count_label.text()
        self.assertIn("8 words", message)
        self.assertNotIn("selected", message)


if __name__ == "__main__":
    unittest.main()
