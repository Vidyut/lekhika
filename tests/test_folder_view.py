"""BUILD-NOW 0: folders open in a remembered view, outliner by default.

The three acceptance points from docs/BUILD-NOW.md: a fresh project shows
the outliner; a hand switch to the corkboard is remembered for the next
folder selection; and reopening the project keeps that choice.
"""

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
_settings_dir = tempfile.mkdtemp(prefix="lekhak-test-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


def _find_item(tree, document_id):
    def walk(item):
        if item.data(0, Qt.UserRole) == document_id:
            return item
        for index in range(item.childCount()):
            found = walk(item.child(index))
            if found is not None:
                return found
        return None

    for index in range(tree.topLevelItemCount()):
        found = walk(tree.topLevelItem(index))
        if found is not None:
            return found
    return None


class FolderViewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")
        self.draft = self.project.documents()[0]
        self.folder_a = self.project.add_document(
            "Part One", parent_id=self.draft.id, kind="folder"
        )
        self.folder_b = self.project.add_document(
            "Part Two", parent_id=self.draft.id, kind="folder"
        )
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        # Never close(): closeEvent would save into a possibly-closed
        # project and pop a modal dialog, which hangs an offscreen run.
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _select(self, document_id):
        item = _find_item(self.window.tree, document_id)
        self.assertIsNotNone(item, "binder item missing")
        self.window.tree.setCurrentItem(item)

    def _click_tab(self, widget):
        # A hand click: the programmatic switch inside _selection_changed
        # runs while loading, but setCurrentWidget from here does not.
        self.window.views.setCurrentWidget(widget)

    def test_folder_selection_opens_the_outliner_by_default(self):
        self._select(self.folder_a.id)

        self.assertIs(self.window.views.currentWidget(), getattr(self.window, 'outliner_page', self.window.outliner))
        self.assertEqual(self.project.get_setting("folder_view"), "")

    def test_a_hand_switch_to_the_corkboard_is_remembered(self):
        self._select(self.folder_a.id)
        self._click_tab(self.window.corkboard)
        self.assertEqual(self.project.get_setting("folder_view"), "corkboard")

        # Another folder while the corkboard is showing stays put…
        self._select(self.folder_b.id)
        self.assertIs(self.window.views.currentWidget(), self.window.corkboard)

        # …and from the Editor tab the remembered view comes back.
        # (Re-selecting the item that is already current fires no
        # selection signal, so the other folder is chosen first.)
        self._click_tab(self.window.editor_page)
        self._select(self.folder_a.id)
        self.assertIs(self.window.views.currentWidget(), self.window.corkboard)

    def test_reopening_the_project_keeps_the_choice(self):
        self._select(self.folder_a.id)
        self._click_tab(self.window.corkboard)

        # A fresh window reopens the same project, as a relaunch would.
        self.window.project = None
        reopened = MainWindow()
        self.addCleanup(lambda: setattr(reopened, "project", None))
        reopened._switch_project(self.project)

        self.assertEqual(self.project.get_setting("folder_view"), "corkboard")
        self.assertIs(reopened.views.currentWidget(), reopened.corkboard)

    def test_junk_stored_choice_falls_back_to_the_outliner(self):
        self.project.set_setting("folder_view", "banana")

        self._select(self.folder_a.id)

        self.assertIs(self.window.views.currentWidget(), getattr(self.window, 'outliner_page', self.window.outliner))

    def test_a_document_selection_never_records_the_preference(self):
        self._select(self.folder_a.id)
        self._click_tab(self.window.corkboard)

        chapter = self.project.add_document(
            "Chapter One", parent_id=self.draft.id, kind="chapter"
        )
        self.window._reload_tree()
        self._select(chapter.id)
        self._click_tab(getattr(self.window, 'outliner_page', self.window.outliner))

        self.assertEqual(self.project.get_setting("folder_view"), "corkboard")


if __name__ == "__main__":
    unittest.main()
