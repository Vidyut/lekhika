"""BUILD-NOW 1: File → Open Recent.

Newest first, eight at most, recorded only by a switch that completed, and
pruned when the folder is gone — from docs/BUILD-NOW.md: "deleting one on
disk removes it from the list rather than offering a broken entry".
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lekhika.app import MainWindow  # noqa: E402
from lekhika.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-test-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class RecentProjectsTests(unittest.TestCase):
    def setUp(self):
        QSettings().remove("session/recentProjects")
        QSettings().remove("session/lastProject")
        self.temporary = tempfile.TemporaryDirectory()
        self.alpha = Project.create(Path(self.temporary.name) / "Alpha", "Alpha")
        self.beta = Project.create(Path(self.temporary.name) / "Beta", "Beta")
        self.window = MainWindow()

    def tearDown(self):
        # Never close(): closeEvent would save into a possibly-closed
        # project and pop a modal dialog, which hangs an offscreen run.
        self.window.project = None
        self.alpha.close()
        self.beta.close()
        self.temporary.cleanup()

    def _menu_actions(self):
        self.window._populate_recent_menu()
        return list(self.window.recent_menu.actions())

    def test_switching_twice_leaves_both_at_the_top(self):
        self.window._switch_project(self.alpha)
        self.window._switch_project(self.beta)

        self.assertEqual(
            self.window._recent_projects(),
            [str(self.beta.root), str(self.alpha.root)],
        )
        actions = self._menu_actions()
        self.assertEqual([a.text() for a in actions], ["Beta", "Alpha"])

    def test_the_list_is_capped_at_eight_newest_first(self):
        for index in range(10):
            path = str(Path(self.temporary.name) / f"p{index}.lekhika")
            self.window._record_recent(path)

        entries = self.window._recent_projects()
        self.assertEqual(len(entries), 8)
        self.assertEqual(entries[0], str(Path(self.temporary.name) / "p9.lekhika"))
        self.assertNotIn(str(Path(self.temporary.name) / "p0.lekhika"), entries)

    def test_a_deleted_folder_is_pruned_when_the_menu_builds(self):
        gone = str(Path(self.temporary.name) / "Deleted")
        QSettings().setValue(
            "session/recentProjects", [str(self.alpha.root), gone])

        actions = self._menu_actions()

        self.assertEqual([a.text() for a in actions], ["Alpha"])
        self.assertEqual(self.window._recent_projects(), [str(self.alpha.root)])

    def test_an_empty_list_offers_a_disabled_placeholder(self):
        QSettings().setValue("session/recentProjects", [str(Path(self.temporary.name) / "Gone")])

        actions = self._menu_actions()

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].text(), "No recent projects")
        self.assertFalse(actions[0].isEnabled())

    def test_a_cancelled_switch_records_nothing(self):
        self.window._switch_project(self.alpha)
        before = self.window._recent_projects()

        self.window.save_current = lambda: False      # failed save
        self.window._switch_project(self.beta)

        self.assertEqual(self.window._recent_projects(), before)
        self.assertIs(self.window.project, self.alpha)

    def test_clicking_an_entry_opens_that_project(self):
        QSettings().setValue(
            "session/recentProjects", [str(self.beta.root)])
        actions = self._menu_actions()

        actions[0].trigger()

        # _open_path makes its own instance; the project it opens is the
        # one named by the entry, not the fixture's object.
        self.assertEqual(self.window.project.root, self.beta.root)

    def test_clicking_a_folder_deleted_after_the_menu_built_prunes_it(self):
        gone = str(Path(self.temporary.name) / "Vanished")
        QSettings().setValue("session/recentProjects", [gone, str(self.alpha.root)])

        self.window._open_path(gone)                 # no dialog, no crash

        self.assertEqual(self.window._recent_projects(), [str(self.alpha.root)])
        self.assertIn("no longer exists", self.window.statusBar().currentMessage())

    def test_duplicate_folder_names_are_disambiguated(self):
        first = str(Path(self.temporary.name) / "One" / "Same.lekhika")
        second = str(Path(self.temporary.name) / "Two" / "Same.lekhika")
        Path(first).mkdir(parents=True, exist_ok=True)
        Path(second).mkdir(parents=True, exist_ok=True)
        QSettings().setValue("session/recentProjects", [first, second])

        labels = [a.text() for a in self._menu_actions()]

        self.assertEqual(labels, ["Same — One", "Same — Two"])


if __name__ == "__main__":
    unittest.main()
