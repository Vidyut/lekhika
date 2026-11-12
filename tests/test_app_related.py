"""1.13 auto-linked related files: the inspector's scan section — offscreen.

Same test contract as the other GUI suites: QSettings redirected into a
temp dir, windows never closed().
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lekhika.app import MainWindow  # noqa: E402
from lekhika.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-related-settings-")
# QSettings objects created from now on read/write under _settings_dir.
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class RelatedFilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")
        self.folder = self.project.documents()[0]          # Draft, kind "folder"
        self.someone = self.project.add_document(
            "Someone", parent_id=self.folder.id, kind="scene"
        )
        self.scene = self.project.add_document(
            "Scene One", parent_id=self.folder.id, kind="scene"
        )
        self.project.write_content(
            self.scene.id, "<p>Someone went to Somewhere.</p>"
        )
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()
        # The off switch is installation-wide; later suites must find
        # the shipped default, not this test's leftover.
        QSettings().remove("related/enabled")

    def _group(self, label: str):
        tree = self.window.related_tree
        return next(
            (tree.topLevelItem(index) for index in range(tree.topLevelItemCount())
             if tree.topLevelItem(index).text(0) == label),
            None,
        )

    def test_section_hidden_until_a_scan_finds_something(self):
        # After the switch the Draft folder is selected and mentions nothing.
        self.assertTrue(self.window.related_section.isHidden())

        self.window._select_document(self.scene.id)

        self.assertFalse(self.window.related_section.isHidden())

    def test_mentions_group_lists_the_named_documents(self):
        self.window._select_document(self.scene.id)

        group = self._group("Mentions")
        self.assertIsNotNone(group)
        children = [group.child(index).text(0)
                    for index in range(group.childCount())]
        self.assertEqual(children, ["Someone  (Scene)"])

    def test_clicking_a_related_document_jumps_there(self):
        self.window._select_document(self.scene.id)
        child = self._group("Mentions").child(0)

        self.window._related_clicked(child, 0)

        self.assertEqual(self.window.current_id, self.someone.id)
        # The character sheet's own section runs the other way.
        group = self._group("Mentioned by")
        self.assertIsNotNone(group)
        self.assertEqual(group.child(0).data(0, Qt.UserRole), self.scene.id)

    def test_group_headers_carry_no_jump_target(self):
        self.window._select_document(self.scene.id)
        group = self._group("Mentions")

        self.window._related_clicked(group, 0)

        self.assertEqual(self.window.current_id, self.scene.id)

    def test_the_preference_turns_the_whole_scan_off(self):
        self.window._select_document(self.scene.id)
        self.assertFalse(self.window.related_section.isHidden())

        QSettings().setValue("related/enabled", False)
        self.window._refresh_related()
        self.assertTrue(self.window.related_section.isHidden())
        self.assertEqual(self.window.related_tree.topLevelItemCount(), 0)

        # A fresh selection must not resurrect it.
        self.window._select_document(self.someone.id)
        self.window._select_document(self.scene.id)
        self.assertTrue(self.window.related_section.isHidden())
        self.assertEqual(self.window.related_tree.topLevelItemCount(), 0)

    def test_document_without_mentions_hides_the_section(self):
        self.window._select_document(self.scene.id)
        self.assertFalse(self.window.related_section.isHidden())

        self.window._select_document(self.someone.id)
        # Someone's text names nothing, so only "Mentioned by" survives…
        self.assertFalse(self.window.related_section.isHidden())
        self.assertIsNone(self._group("Mentions"))
        self.assertIsNotNone(self._group("Mentioned by"))

        # …and back on the (textless) Draft folder the section collapses.
        self.window._select_document(self.folder.id)
        self.assertTrue(self.window.related_section.isHidden())


if __name__ == "__main__":
    unittest.main()
