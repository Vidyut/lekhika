"""The outliner follows the selection (docs/UI-NOTES.md 2).

A folder shows its children, a document shows its siblings, nothing selected
shows the project — the corkboard's rule, applied to the tree view.
"""

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
_settings_dir = tempfile.mkdtemp(prefix="lekhak-outliner-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class OutlinerScopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel",
                                      "Novel")
        self.root = self.project.documents()[0]          # the seeded "Draft"
        self.book = self.project.add_document("Book", self.root.id, "folder")
        self.chapter = self.project.add_document("One", self.book.id, "chapter")
        self.scene = self.project.add_document("Scene", self.chapter.id, "scene")
        self.stray = self.project.add_document("Stray", self.root.id, "scene")
        QSettings().remove("session/lastProject")
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _titles(self):
        self.window._refresh_outliner()
        return [self.window.outliner.topLevelItem(index).text(0)
                for index in range(self.window.outliner.topLevelItemCount())]

    def test_a_selected_folder_shows_its_children(self):
        self.window.current_id = self.book.id
        self.assertEqual(self._titles(), ["One"])

    def test_a_selected_document_shows_its_siblings(self):
        self.window.current_id = self.stray.id
        self.assertEqual(self._titles(), ["Book", "Stray"])

    def test_the_root_folder_shows_its_children(self):
        self.window.current_id = self.root.id
        self.assertEqual(self._titles(), ["Book", "Stray"])

    def test_nothing_selected_shows_the_project(self):
        """The project's own level, in binder order — templates included,
        where the writer put them (docs/TEMPLATES.md)."""
        self.window.current_id = None
        self.assertEqual(self._titles(), ["Draft"])

    def test_an_empty_folder_shows_its_contents_not_the_project(self):
        # A childless folder's contents are nothing — not the whole
        # project (the siblings fallback is for documents).
        empty = self.project.add_document("Empty", self.root.id, "folder")
        self.window.current_id = empty.id
        self.assertEqual(self._titles(), [])

    def test_the_scope_follows_a_binder_click_without_a_tab_switch(self):
        # The gap the first pass missed: while the Outliner tab is already
        # showing, moving the binder selection used to leave the old rows
        # up — only a tab change rebuilt them.
        self.window.views.setCurrentWidget(self.window.outliner)
        self.window._select_document(self.book.id)
        self.assertEqual(
            [self.window.outliner.topLevelItem(index).text(0)
             for index in range(self.window.outliner.topLevelItemCount())],
            ["One"])

    # -- the open-time structure prompt -------------------------------------

    def _drifted_project(self):
        """A second, already-open project with a tampered order file."""
        temporary = tempfile.TemporaryDirectory()
        project = Project.create(Path(temporary.name) / "Other", "Other")
        book = project.add_document("Book", kind="folder")
        project.add_document("Scene", book.id, "scene")
        order_file = project.root / "Book" / ".order.json"
        order_file.write_text('{"children": ["Wrong.html"]}',
                              encoding="utf-8")
        return temporary, project

    def test_opening_a_drifted_project_asks_which_is_right(self):
        temporary, project = self._drifted_project()
        try:
            seen: dict = {}

            def fake_dialog(problems):
                seen["problems"] = problems
                return "order"

            original = self.window._drift_dialog
            self.window._drift_dialog = fake_dialog
            try:
                self.window._switch_project(project)
            finally:
                self.window._drift_dialog = original
            self.assertTrue(seen["problems"])
            # The writer chose the order files; the wrong name was ignored
            # and the project opens clean.
            self.assertEqual(project.validate_alignment(), [])
            self.assertIs(self.window.project, project)
        finally:
            self.window.project = None
            project.close()
            temporary.cleanup()

    def test_cancelling_the_drift_prompt_aborts_the_switch(self):
        temporary, project = self._drifted_project()
        try:
            self.window._drift_dialog = lambda problems: None
            self.window._switch_project(project)
            # Still on the original project; the drifted one is untouched.
            self.assertIs(self.window.project, self.project)
            project.open()
            self.assertTrue(project.validate_alignment())
        finally:
            self.window.project = None
            project.close()
            temporary.cleanup()

    def test_opening_with_a_manifest_choice_follows_the_root_order(self):
        import json as _json

        temporary = tempfile.TemporaryDirectory()
        project = Project.create(Path(temporary.name) / "Other", "Other")
        try:
            project.add_document("Alpha", kind="folder")
            manifest = project.root / "manifest.json"
            manifest.write_text(_json.dumps({
                "title": "Other", "version": 1,
                "children": ["Alpha", "Draft"]}), encoding="utf-8")
            self.window._drift_dialog = lambda problems: "manifest"
            self.window._switch_project(project)
            self.assertEqual(
                [d.title for d in project.documents() if d.parent_id is None],
                ["Alpha", "Draft"])
            self.assertEqual(project.validate_alignment(), [])
        finally:
            self.window.project = None
            project.close()
            temporary.cleanup()

    def test_missing_files_ok_removes_their_entries(self):
        temporary = tempfile.TemporaryDirectory()
        project = Project.create(Path(temporary.name) / "Other", "Other")
        try:
            book = project.add_document("Book", kind="folder")
            scene = project.add_document("Scene", book.id, "scene")
            project.write_content(scene.id, "<p>Gone.</p>")
            (project.root / "Book" / "Scene.html").unlink()
            self.window._missing_dialog = lambda missing: "remove"
            self.window._warn_missing_files(project)
            self.assertEqual(project.missing_files(), [])
            self.assertEqual(project.validate_alignment(), [])
        finally:
            self.window.project = None
            project.close()
            temporary.cleanup()

    def test_missing_files_leave_keeps_the_entries(self):
        temporary = tempfile.TemporaryDirectory()
        project = Project.create(Path(temporary.name) / "Other", "Other")
        try:
            book = project.add_document("Book", kind="folder")
            scene = project.add_document("Scene", book.id, "scene")
            project.write_content(scene.id, "<p>Gone.</p>")
            (project.root / "Book" / "Scene.html").unlink()
            self.window._missing_dialog = lambda missing: "leave"
            self.window._warn_missing_files(project)
            self.assertEqual(project.missing_files(), ["Book/Scene.html"])
        finally:
            self.window.project = None
            project.close()
            temporary.cleanup()

    def test_missing_files_recheck_closes_when_fixed(self):
        temporary = tempfile.TemporaryDirectory()
        project = Project.create(Path(temporary.name) / "Other", "Other")
        try:
            book = project.add_document("Book", kind="folder")
            scene = project.add_document("Scene", book.id, "scene")
            project.write_content(scene.id, "<p>Gone.</p>")
            gone = project.root / "Book" / "Scene.html"
            gone.unlink()

            def fake_dialog(missing):
                # The writer put the file back, then clicked Recheck.
                self.assertEqual(missing, ["Book/Scene.html"])
                gone.write_text("<p>Back.</p>", encoding="utf-8")
                return "recheck"

            self.window._missing_dialog = fake_dialog
            self.window._warn_missing_files(project)
            self.assertEqual(project.missing_files(), [])
            self.assertIn(scene.id, [d.id for d in project.documents()])
        finally:
            self.window.project = None
            project.close()
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
