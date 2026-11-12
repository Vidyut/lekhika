"""BUILD-NOW 5: Insert Date / Insert Date and Time.

Acceptance from docs/BUILD-NOW.md: inserts today's date in the locale and
is undoable as one step — plus the second half of the item, feeding the
document's Date metadata so the writer never types the same day twice.
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate, QLocale, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lekhika.app import MainWindow  # noqa: E402
from lekhika.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-test-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)

_TODAY = QDate.currentDate()
_PROSE_DATE = QLocale.system().toString(_TODAY, QLocale.FormatType.LongFormat)
_ISO_DATE = _TODAY.toString(Qt.ISODate)


class InsertDateTests(unittest.TestCase):
    def setUp(self):
        QSettings().remove("session/lastProject")
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")
        self.draft = self.project.documents()[0]
        self.chapter = self.project.add_document(
            "Chapter One", parent_id=self.draft.id, kind="chapter"
        )
        self.project.write_content(self.chapter.id, "<p>Start of the story.</p>")
        self.project.set_setting("last_document", self.chapter.id)
        self.window = MainWindow()
        self.window._switch_project(self.project)
        # Caret at the end of the existing sentence.
        cursor = self.window.editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.window.editor.setTextCursor(cursor)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def test_insert_puts_the_locales_date_at_the_caret(self):
        before = self.window.editor.toPlainText()

        self.window.insert_date()

        after = self.window.editor.toPlainText()
        self.assertTrue(after.startswith(before + _PROSE_DATE),
                        f"expected {before!r} + {_PROSE_DATE!r}, got {after!r}")

    def test_one_undo_takes_it_back_out(self):
        before = self.window.editor.toPlainText()

        self.window.insert_date()
        self.window.editor.undo()

        self.assertEqual(self.window.editor.toPlainText(), before)

    def test_the_date_metadata_is_fed_and_siblings_survive(self):
        self.project.set_metadata(
            self.chapter.id, {"Mood": "pensive"},
            {"Mood": "text"}, {"Mood": []})

        self.window.insert_date()

        entries = self.project.metadata_entries(self.chapter.id)
        self.assertEqual(entries["Date"][0], _ISO_DATE)
        self.assertEqual(entries["Date"][1], "date")
        self.assertEqual(entries["Mood"][0], "pensive")   # replaced set, kept row

    def test_the_inspector_shows_the_new_field(self):
        self.window.insert_date()

        keys = [
            self.window.metadata_table.item(row, 1).text()
            for row in range(self.window.metadata_table.rowCount())
            if self.window.metadata_table.item(row, 1)
        ]
        self.assertIn("Date", keys)

    def test_date_and_time_appends_the_short_time(self):
        self.window.insert_date(with_time=True)

        text = self.window.editor.toPlainText()
        self.assertIn(_PROSE_DATE, text)
        # Anything after the date is the time part, e.g. ", 20:45".
        self.assertTrue(text.endswith(_PROSE_DATE) or
                        text.split(_PROSE_DATE, 1)[1].startswith(", "))

    def test_a_folder_refuses_and_changes_nothing(self):
        self.window.tree.setCurrentItem(
            next(item for item in self.window._all_items()
                 if item.data(0, Qt.UserRole) == self.draft.id))
        before = self.window.project.metadata_entries(self.draft.id)

        self.window.insert_date()

        self.assertIn("Folders have no text",
                      self.window.statusBar().currentMessage())
        self.assertEqual(self.window.project.metadata_entries(self.draft.id),
                         before)

    def test_nothing_open_says_so_instead_of_raising(self):
        self.window.project = None
        self.window.insert_date()

        self.assertIn("Open a document first",
                      self.window.statusBar().currentMessage())


if __name__ == "__main__":
    unittest.main()
