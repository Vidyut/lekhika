"""1.12 metadata panel: the inspector's inline rows — offscreen GUI.

Same test contract as the other GUI suites: QSettings redirected into a
temp dir, windows never closed() (closeEvent would save into a possibly-
closed project and hang offscreen).
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QComboBox, QDateEdit,
)

from lekhak.app import (  # noqa: E402
    PROJECT_METADATA_TARGET, MainWindow, MetadataDialog,
)
from lekhak.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhak")
_application.setApplicationName("Lekhak")
_settings_dir = tempfile.mkdtemp(prefix="lekhak-metadata-settings-")
# QSettings objects created from now on read/write under _settings_dir.
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class MetadataPanelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")
        self.folder = self.project.documents()[0]          # Draft, kind "folder"
        self.scene = self.project.add_document(
            "Scene", parent_id=self.folder.id, kind="scene"
        )
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        # Never close() the window: closeEvent -> save_current on a closed
        # project raises into a modal QMessageBox and hangs offscreen.
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def test_document_selection_shows_its_own_rows(self):
        self.project.set_metadata(self.scene.id, {"POV": "Maya"})

        self.window._select_document(self.scene.id)

        table = self.window.metadata_table
        self.assertEqual(self.window._metadata_target, self.scene.id)
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.item(0, 0).text(), "POV")
        self.assertEqual(table.item(0, 1).text(), "Maya")

    def test_the_lock_is_a_tick_of_its_own_not_a_custom_field(self):
        # The lock is a marker file in the tree (docs/STORAGE.md), not one
        # of the writer's fields: the table's rows are the writer's own,
        # and the tick lives beside the Type row.
        self.window._select_document(self.scene.id)

        table = self.window.metadata_table
        names = [table.item(row, 0).text() for row in range(table.rowCount())]
        self.assertNotIn("Locked", names)
        self.assertTrue(self.window.lock_box.isVisibleTo(self.window))
        self.assertFalse(self.window.lock_box.isChecked())

    def test_the_lock_tick_follows_the_selected_document(self):
        self.project.set_locked(self.scene.id, True)

        self.window._select_document(self.scene.id)

        self.assertTrue(self.window.lock_box.isChecked())

    def test_ticking_the_lock_writes_the_marker(self):
        self.window._select_document(self.scene.id)

        self.window.lock_box.setChecked(True)

        self.assertTrue(self.project.is_locked(self.scene.id))
        self.assertTrue((self.project.root / self.scene.path).with_name(
            "." + Path(self.scene.path).name + ".locked").is_file())

    def test_unticking_the_lock_takes_the_marker_away(self):
        self.project.set_locked(self.scene.id, True)
        self.window._select_document(self.scene.id)

        self.window.lock_box.setChecked(False)

        self.assertFalse(self.project.is_locked(self.scene.id))

    def test_folder_selection_shows_project_metadata(self):
        self.project.set_project_metadata({"Series": ("Series", "text", [])})
        # Select away first: the panel refreshes on a selection CHANGE,
        # and the folder was already selected after the project switch.
        self.window._select_document(self.scene.id)

        self.window._select_document(self.folder.id)

        table = self.window.metadata_table
        self.assertEqual(self.window._metadata_target, PROJECT_METADATA_TARGET)
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.item(0, 0).text(), "Series")
        self.assertEqual(table.item(0, 1).text(), "Series")

    def test_nothing_selected_targets_the_project(self):
        self.project.set_project_metadata({"Series": ("Series", "text", [])})

        self.window.current_id = None
        self.window._populate_metadata_table()

        self.assertEqual(self.window._metadata_target, PROJECT_METADATA_TARGET)
        self.assertEqual(self.window.metadata_table.rowCount(), 1)

    def test_live_edit_persists_a_document_field(self):
        self.window._select_document(self.scene.id)

        self.window.metadata_table.add_field()
        self.window.metadata_table.item(0, 0).setText("POV")
        self.window.metadata_table.item(0, 1).setText("Maya")

        self.assertEqual(
            self.project.metadata_entries(self.scene.id)["POV"],
            ("Maya", "text", []),
        )

    def test_live_edit_persists_a_project_field(self):
        self.window._select_document(self.folder.id)

        self.window.metadata_table.add_field()
        self.window.metadata_table.item(0, 0).setText("Series")
        self.window.metadata_table.item(0, 1).setText("Series")

        self.assertEqual(
            self.project.project_metadata()["Series"],
            ("Series", "text", []),
        )

    def test_clearing_a_key_never_drops_the_field(self):
        # Mid-rename: the key commits empty first, the value still held.
        # A named-subset write at that instant would delete the field
        # from storage — the write waits for the row to name itself.
        self.project.set_metadata(self.scene.id, {"POV": "Maya"})
        self.window._select_document(self.scene.id)

        self.window.metadata_table.item(0, 0).setText("")

        self.assertEqual(
            self.project.metadata_entries(self.scene.id)["POV"],
            ("Maya", "text", []),
        )

        self.window.metadata_table.item(0, 0).setText("Hero")

        entries = self.project.metadata_entries(self.scene.id)
        self.assertNotIn("POV", entries)
        self.assertEqual(entries["Hero"], ("Maya", "text", []))

    def test_add_field_does_not_disturb_existing_rows(self):
        self.project.set_metadata(self.scene.id, {"POV": "Maya", "Location": "Delhi"})
        self.window._select_document(self.scene.id)

        self.window.metadata_table.add_field()

        self.assertEqual(
            set(self.project.metadata_entries(self.scene.id)),
            {"POV", "Location"},
        )

    def test_remove_selected_persists_the_deletion(self):
        self.project.set_metadata(self.scene.id, {"POV": "Maya"})
        self.window._select_document(self.scene.id)

        self.window.metadata_table.selectAll()
        self.window.metadata_table.remove_selected()

        self.assertEqual(self.project.metadata_entries(self.scene.id), {})

    def test_legacy_dropdown_rows_load_as_plain_text(self):
        self.project.set_metadata(
            self.scene.id, {"Status": "Draft"}, {"Status": "choice"},
            {"Status": ["Draft", "Final"]},
        )
        self.window._select_document(self.scene.id)

        type_box = self.window.metadata_table.cellWidget(0, 2)
        self.assertIsInstance(type_box, QComboBox)
        self.assertEqual(type_box.currentData(), "text")
        self.assertEqual(self.window.metadata_table.item(0, 1).text(), "Draft")

    def test_metadata_widgets_disabled_without_a_project(self):
        window = MainWindow()

        self.assertFalse(window.metadata_table.isEnabled())
        self.assertFalse(window.metadata_add_button.isEnabled())
        self.assertFalse(window.metadata_remove_button.isEnabled())
        window.project = None

    def test_dialog_edits_the_same_rows_through_the_shared_table(self):
        entries = {"POV": ("Maya", "text", [])}

        dialog = MetadataDialog(entries)

        self.assertEqual(dialog.values_types_options(), (
            {"POV": "Maya"},
            {"POV": "text"},
            {"POV": []},
        ))
        dialog.table.add_field()
        self.assertEqual(dialog.table.rowCount(), 2)
        dialog.deleteLater()

    def test_edit_metadata_target_follows_the_selection(self):
        self.assertEqual(self.window._selection_metadata_target(),
                         PROJECT_METADATA_TARGET)          # Draft folder after switch
        self.window._select_document(self.scene.id)
        self.assertEqual(self.window._selection_metadata_target(), self.scene.id)
        self.window.current_id = None
        self.assertEqual(self.window._selection_metadata_target(),
                         PROJECT_METADATA_TARGET)          # outliner root
        bare = MainWindow()
        self.assertIsNone(bare._selection_metadata_target())
        bare.project = None

    def test_editor_tree_selecting_a_folder_switches_the_panel(self):
        self.project.set_metadata(self.scene.id, {"POV": "Maya"})
        self.project.set_project_metadata({"Series": ("Series", "text", [])})
        self.window._select_document(self.scene.id)
        self.assertEqual(self.window.metadata_table.item(0, 0).text(), "POV")

        self.window._select_document(self.folder.id)

        self.assertEqual(self.window._metadata_target, PROJECT_METADATA_TARGET)
        self.assertEqual(self.window.metadata_table.item(0, 0).text(), "Series")

    def test_checkbox_type_stores_yes_or_no(self):
        self.project.set_metadata(
            self.scene.id, {"Trapped": "yes"}, {"Trapped": "checkbox"}
        )
        self.window._select_document(self.scene.id)

        value_widget = self.window.metadata_table.cellWidget(0, 1)
        self.assertTrue(value_widget.isChecked())

        value_widget.setChecked(False)
        self.assertEqual(
            self.project.metadata_entries(self.scene.id)["Trapped"],
            ("no", "checkbox", []),
        )

    def test_textarea_type_keeps_newlines_and_commits_on_focus_out(self):
        self.project.set_metadata(
            self.scene.id, {"Beat sheet": "one\ntwo"},
            {"Beat sheet": "textarea"},
        )
        self.window._select_document(self.scene.id)

        editor = self.window.metadata_table.cellWidget(0, 1)
        editor.setPlainText("one\ntwo\nthree")
        # Typing alone must not save per keystroke: the commit fires when
        # the caret leaves the widget (the line-edit editingFinished
        # equivalent). Send that event directly — offscreen windows do
        # not reliably move real focus, but the filter sees the same
        # event either way.
        self.assertEqual(
            self.project.metadata_entries(self.scene.id)["Beat sheet"][0],
            "one\ntwo",
        )

        QApplication.sendEvent(editor, QEvent(QEvent.FocusOut))
        self.assertEqual(
            self.project.metadata_entries(self.scene.id)["Beat sheet"],
            ("one\ntwo\nthree", "textarea", []),
        )

    def test_type_combo_offers_the_four_types(self):
        self.window._select_document(self.scene.id)
        self.window.metadata_table.add_field()

        type_box = self.window.metadata_table.cellWidget(0, 2)
        values = [type_box.itemData(index) for index in range(type_box.count())]
        self.assertEqual(values, ["text", "textarea", "checkbox", "date"])

    def test_switching_a_row_to_checkbox_round_trips_the_value(self):
        self.project.set_metadata(self.scene.id, {"Done": "maybe"})
        self.window._select_document(self.scene.id)

        type_box = self.window.metadata_table.cellWidget(0, 2)
        type_box.setCurrentIndex(type_box.findData("checkbox"))

        value_widget = self.window.metadata_table.cellWidget(0, 1)
        self.assertFalse(value_widget.isChecked())      # "maybe" is not yes
        value_widget.setChecked(True)
        self.assertEqual(
            self.project.metadata_entries(self.scene.id)["Done"],
            ("yes", "checkbox", []),
        )


    def test_column_order_puts_value_next_to_the_field(self):
        table = self.window.metadata_table
        headers = [table.horizontalHeaderItem(i).text() for i in range(3)]
        # "Format", not "Type": Type is the protected role key
        # (docs/MODEL.md) and must not share a word with the widget column.
        self.assertEqual(headers, ["Field", "Value", "Format"])

    def test_scrivener_long_date_opens_in_the_picker_as_a_real_date(self):
        self.project.set_metadata(
            self.scene.id, {"Date": "Wednesday, January 7, 2443"},
            {"Date": "date"},
        )
        self.window._select_document(self.scene.id)

        editor = self.window.metadata_table.cellWidget(0, 1)
        self.assertIsInstance(editor, QDateEdit)
        self.assertEqual(editor.date().toString(Qt.ISODate), "2443-01-07")
        self.assertEqual(
            self.window.metadata_table.row_value(0), "2443-01-07"
        )


class MetadataBoxesTests(unittest.TestCase):
    """1.16: the inspector's metadata in four collapsible groups.

    The author's complaint: the metadata was "undifferentiated text with no
    clear logic to it". The groups give it that logic; the fold and the
    height caps keep the long ones from pushing the editor off-screen.
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")
        self.folder = self.project.documents()[0]          # Draft, kind "folder"
        self.scene = self.project.add_document(
            "Scene", parent_id=self.folder.id, kind="scene"
        )
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _fold(self, title):
        for fold in self.window.metadata_folds:
            if fold.header.text() == title:
                return fold
        self.fail(f"no group titled {title}")

    def _widgets(self, fold):
        return self._widgets_in(fold.body_layout)

    def _widgets_in(self, layout):
        found = []
        for index in range(layout.count()):
            item = layout.itemAt(index)
            if item.widget() is not None:
                found.append(item.widget())
            elif item.layout() is not None:
                found.extend(self._widgets_in(item.layout()))
        return found

    def test_the_panel_reads_as_four_groups_in_order(self):
        titles = [fold.header.text() for fold in self.window.metadata_folds]
        self.assertEqual(titles, ["Basic", "Synopsis", "Format / Scene",
                                   "Advanced / Custom"])

    def test_the_facts_land_in_their_groups(self):
        basic = self._fold("Basic")
        self.assertIn(self.window.type_box, self._widgets(basic))
        self.assertIn(self.window.target, self._widgets(basic))
        self.assertIn(self.window.doc_template_box, self._widgets(basic))
        self.assertEqual(self._widgets(self._fold("Synopsis")),
                         [self.window.synopsis])
        format_fold = self._fold("Format / Scene")
        self.assertIn(self.window.scene_divider_edit,
                      self._widgets(format_fold))
        self.assertIn(self.window.book_cover_edit, self._widgets(format_fold))
        self.assertIn(self.window.page_setup_button,
                      self._widgets(format_fold))
        self.assertIn(self.window.metadata_table,
                      self._widgets(self._fold("Advanced / Custom")))

    def test_the_long_fields_are_capped(self):
        # Capped, not shrunk: a long synopsis or field list scrolls inside
        # its box instead of pushing the editor off-screen.
        self.assertLessEqual(self.window.synopsis.maximumHeight(), 120)
        self.assertLessEqual(self.window.metadata_table.maximumHeight(), 220)

    def test_folding_a_group_hides_its_rows_and_unfolding_brings_them_back(self):
        # The Format / Scene rows are the folder's (a scene has none of
        # them), so the fold is checked on the folder.
        self.window._select_document(self.folder.id)
        fold = self._fold("Format / Scene")
        self.assertFalse(fold.header.isChecked())
        self.assertFalse(self.window.page_setup_button.isVisibleTo(self.window))

        fold.header.setChecked(True)

        self.assertTrue(self.window.page_setup_button.isVisibleTo(self.window))
        fold.header.setChecked(False)
        self.assertFalse(self.window.page_setup_button.isVisibleTo(self.window))

    def test_the_synopsis_still_saves_from_inside_its_group(self):
        self.window._select_document(self.scene.id)

        self.window.synopsis.setPlainText("Maya waits on the bridge.")

        # The signal still reaches the save timer after the re-parent.
        self.assertTrue(self.window.save_timer.isActive())
        self.window.save_current()
        text = (self.project.root / self.scene.path).read_text(encoding="utf-8")
        self.assertIn("synopsis: Maya waits on the bridge.", text)


class TypeRowTests(unittest.TestCase):
    """The protected Type row (docs/MODEL.md): always filled, never a
    table row, snaps typos, and keeps folder/Type separate from the
    project fields the table shows on folders."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel")
        self.folder = self.project.documents()[0]          # Draft
        self.scene = self.project.add_document(
            "Scene", parent_id=self.folder.id, kind="scene"
        )
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def test_type_row_shows_the_derived_default_and_hides_it_in_the_table(self):
        self.window._select_document(self.scene.id)

        # Two base roles now (1.17): a scene is a Document until the
        # writer says otherwise.
        self.assertEqual(self.window.type_box.currentText(), "Document")
        # Type is never a table row.
        keys = [self.window.metadata_table.item(row, 0).text()
                for row in range(self.window.metadata_table.rowCount())]
        self.assertNotIn("Type", keys)

    def test_type_never_appears_as_a_table_row_even_if_stored(self):
        self.project.set_metadata(self.scene.id, {"Type": "Chapter"})

        self.window._select_document(self.scene.id)

        self.assertEqual(self.window.type_box.currentText(), "Chapter")
        keys = [self.window.metadata_table.item(row, 0).text()
                for row in range(self.window.metadata_table.rowCount())]
        self.assertNotIn("Type", keys)

    def test_setting_type_persists_to_the_document(self):
        self.window._select_document(self.scene.id)

        self.window.type_box.setCurrentText("Short story")
        self.window._save_metadata()

        self.assertEqual(
            self.project.metadata(self.scene.id)["Type"], "Short story")

    def test_clearing_type_returns_to_the_derived_default(self):
        self.project.set_metadata(self.scene.id, {"Type": "Short story"})
        self.window._select_document(self.scene.id)

        self.window.type_box.setCurrentText("")
        self.window._save_metadata()

        self.assertNotIn("Type", self.project.metadata(self.scene.id))
        self.assertEqual(self.project.type_of(self.scene), "Document")

    def test_default_type_is_not_written_to_storage(self):
        self.window._select_document(self.scene.id)

        self.window._save_metadata()   # nothing changed; Scene is derived

        self.assertNotIn("Type", self.project.metadata(self.scene.id))

    def test_exact_match_snaps_to_the_existing_option(self):
        self.project.set_metadata(self.scene.id, {"Type": "Part"})
        self.window._select_document(self.scene.id)

        # setText, not setCurrentText: typing a free value. "Part" is the
        # document's own role and lives at findText("Part") — a working
        # snap must move the index there, so a no-op fails.
        self.window.type_box.lineEdit().setText("Part")
        self.window._type_changed()    # what editingFinished runs

        self.assertEqual(self.window.type_box.currentIndex(),
                         self.window.type_box.findText("Part"))
        # "Part" is not this scene's derived default, so it is written to
        # storage — _write_document_type keeps defaults out on purpose.

    def test_case_variant_snaps_and_offers_the_checkbox(self):
        self.project.set_metadata(self.scene.id, {"Type": "Scene"})
        self.window._select_document(self.scene.id)

        self.window.type_box.lineEdit().setText("scene")
        self.window._type_changed()

        # Snapped to the existing spelling; the warning offers the variant.
        self.assertEqual(self.window.type_box.currentText(), "Scene")
        self.assertFalse(self.window.type_warning.isHidden())
        self.assertFalse(self.window.type_keep_box.isHidden())
        self.assertEqual(
            self.project.metadata(self.scene.id)["Type"], "Scene")

        # Ticking restores the typed variant and saves it.
        self.window.type_keep_box.setChecked(True)
        self.assertEqual(self.window.type_box.currentText(), "scene")
        self.assertEqual(
            self.project.metadata(self.scene.id)["Type"], "scene")

    def test_dropdown_lists_types_the_project_already_uses(self):
        self.project.set_metadata(self.scene.id, {"Type": "Weird"})
        self.other = self.project.add_document(
            "Other", parent_id=self.folder.id, kind="scene")
        # add_document doesn't rebuild the binder; without this the
        # selection can't find the new item and the panel stays put.
        self.window._reload_tree()
        self.window._select_document(self.other.id)

        items = [self.window.type_box.itemText(i)
                 for i in range(self.window.type_box.count())]
        self.assertIn("Weird", items)
        # "Scene" is the selected doc's value, consumed into `seen` and so
        # deliberately not repeated as its own dropdown entry.

    def test_folder_type_is_the_folders_own_not_the_projects(self):
        self.window._select_document(self.folder.id)

        self.assertEqual(self.window.type_box.currentText(), "Folder")
        self.window.type_box.setCurrentText("Book")
        self.window._save_metadata()

        self.assertEqual(self.project.metadata(self.folder.id)["Type"],
                         "Book")
        # The custom-fields table on a folder is the project's; the Type
        # row never lands there.
        self.assertNotIn("Type", self.project.project_metadata())
        self.assertEqual(self.project.type_of(self.folder), "Book")

    def test_a_folder_reads_as_a_folder_whatever_its_book_settings(self):
        # The role is what the writer says it is (1.17): book settings
        # do not make a folder a Book any more — "Book" is a role one
        # adds, and it stays a folder's own.
        self.project.set_book_settings(self.folder.id, {"template": "x"})
        # Changing book settings fires no selection signal, and the
        # folder may already be current — repopulate to pick up the heal.
        self.window._reload_tree()
        self.window._select_document(self.folder.id)
        self.window._populate_metadata_table()

        self.assertEqual(self.window.type_box.currentText(), "Folder")
        self.assertNotIn("Book", [
            self.window.type_box.itemText(index)
            for index in range(self.window.type_box.count())])

    def test_type_row_is_hidden_with_no_document_selected(self):
        # _switch_project auto-selects the first binder item; clear it so
        # the panel is genuinely showing the project's own fields.
        self.window.tree.setCurrentItem(None)
        self.window.current_id = None
        self.window._populate_metadata_table()

        self.assertTrue(self.window.type_box.isHidden())
        self.assertTrue(self.window.type_label.isHidden())


if __name__ == "__main__":
    unittest.main()
