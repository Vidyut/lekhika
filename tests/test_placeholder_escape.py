"""The escape in the editor: the writer sees what they meant.

A document that talks about a placeholder — `${Title}` — should read that
way in the editor, while the file keeps the escape that protects it from
the export. The round trip has to be lossless: open, save, and the file
is byte for byte what it was.
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
from lekhak.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhak")
_application.setApplicationName("Lekhak")
_settings_dir = tempfile.mkdtemp(prefix="lekhak-escape-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class PlaceholderEscapeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(
            Path(self.temporary.name) / "Novel", "Novel")
        self.folder = self.project.documents()[0]
        self.scene = self.project.add_document(
            "Alpha", self.folder.id, "scene")
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def test_the_editor_shows_the_placeholder_and_the_file_keeps_the_escape(self):
        self.project.write_content(self.scene.id, "<p>Write $${Title} here.</p>")

        self.window._select_document(self.scene.id)

        # What the writer wrote is what they see.
        self.assertIn("Write ${Title} here.", self.window.editor.toPlainText())
        self.assertNotIn("$$", self.window.editor.toPlainText())

        self.window.save_current()

        # The file keeps the escape, so the export leaves it alone.
        self.assertIn("$${Title}", self.project.read_content(self.scene.id))

        destination = self.project.root.parent / "out.html"
        self.project.export_html(destination, None)
        exported = destination.read_text(encoding="utf-8")
        self.assertIn("Write ${Title} here.", exported)
        self.assertNotIn("Write Alpha here.", exported)

    def test_a_saved_placeholder_keeps_its_escape(self):
        self.project.write_content(self.scene.id, "<p>Write $${Title} here.</p>")
        self.window._select_document(self.scene.id)

        self.window.save_current()

        # The file is rewritten in the editor's own HTML (as every save
        # is), but the escape is still there.
        saved = self.project.read_content(self.scene.id)
        self.assertIn("$${Title}", saved)
        self.assertNotIn(">${Title}", saved)


if __name__ == "__main__":
    unittest.main()
