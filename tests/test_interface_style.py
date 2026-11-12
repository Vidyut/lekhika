"""The interface's row theory (UI-NOTES 3): manuscript, proxy, ambience."""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lekhak.app import MainWindow  # noqa: E402
from lekhak.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhak")
_application.setApplicationName("Lekhak")
_settings_dir = tempfile.mkdtemp(prefix="lekhak-style-settings-")
# QSettings objects created from now on read/write under _settings_dir.
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class InterfaceStyleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel",
                                      "Novel")
        self.draft = self.project.documents()[0]
        scene = self.project.add_document("Scene", self.draft.id, "scene")
        self.project.write_content(scene.id, "<p>Real prose.</p>")
        empty = self.project.add_document("Fresh", self.draft.id, "scene")
        template = self.project.add_document("Design", self.draft.id, "scene")
        self.project.set_template(template.id, True)
        self.documents = {d.title: d for d in self.project.documents()}
        self.draft = self.documents["Draft"]
        self.window = MainWindow()

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def test_row_kinds(self):
        self.window.project = self.project
        self.assertEqual(self.window._row_kind(self.documents["Scene"]),
                         "manuscript")
        self.assertEqual(self.window._row_kind(self.documents["Fresh"]),
                         "ambience")
        self.assertEqual(self.window._row_kind(self.documents["Design"]),
                         "proxy")
        self.assertEqual(self.window._row_kind(self.draft), "manuscript")

    def test_outliner_rows_carry_tints_and_serif(self):
        self.window._switch_project(self.project)
        self.window.current_id = self.draft.id
        self.window._refresh_outliner()
        rows = {self.window.outliner.topLevelItem(i).text(0):
                self.window.outliner.topLevelItem(i)
                for i in range(self.window.outliner.topLevelItemCount())}
        self.assertIsNone(
            rows["Scene"].data(0, Qt.ItemDataRole.BackgroundRole))
        self.assertIsNotNone(
            rows["Design"].data(0, Qt.ItemDataRole.BackgroundRole))
        for row in rows.values():
            self.assertIn("Serif", row.font(0).family())
        self.assertFalse(rows["Scene"].font(0).bold())


if __name__ == "__main__":
    unittest.main()
