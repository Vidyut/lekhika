"""Style-standardise in the Edit menu: quotes and the two-hyphen dash."""

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
_settings_dir = tempfile.mkdtemp(prefix="lekhak-standardise-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class StandardiseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel",
                                      "Novel")
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def test_straight_quotes_become_curly_in_place(self):
        self.window.editor.setPlainText(
            '"Now," she said. It\'s fine.')

        made = self.window._standardise_qdocument(
            self.window.editor.document(), "curly", False)

        self.assertEqual(
            self.window.editor.toPlainText(),
            "“Now,” she said. It’s fine.")

    def test_curly_quotes_become_straight(self):
        self.window.editor.setPlainText("“Now,” ‘she said.’")

        made = self.window._standardise_qdocument(
            self.window.editor.document(), "straight", False)

        self.assertEqual(self.window.editor.toPlainText(), '"Now," \'she said.\'')

    def test_two_hyphens_become_em_dashes(self):
        self.window.editor.setPlainText("a -- b")

        made = self.window._standardise_qdocument(
            self.window.editor.document(), "keep", True)

        self.assertEqual(self.window.editor.toPlainText(), "a — b")
        self.assertEqual(made, 1)

    def test_whole_project_scope_writes_stored_files(self):
        folder = self.window.project.documents()[0]
        scene = self.project.add_document("Scene", folder.id, "scene")
        self.project.write_content(scene.id, '<p>She said "no" -- twice.</p>')

        made = self.window._standardise_offline("curly", True)

        html = self.project.read_content(scene.id)
        self.assertIn("“no”", html)
        self.assertIn("—", html)
