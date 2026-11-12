"""Book settings in the inspector — the cover, the book template name,
multi-selection export scope — offscreen GUI.

Same contract as the other app suites: QSettings redirected into a temp
dir, windows never closed() (closeEvent would save into a possibly-
closed project and hang offscreen).
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from lekhika.app import (  # noqa: E402
    ExportOptionsDialog, MainWindow,
)
from lekhika.project import Project  # noqa: E402
from lekhika.templates import (  # noqa: E402
    create_template_set, discover_template_sets,
)

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-book-settings-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


def _within(item, document_id):
    if item.data(0, Qt.UserRole) == document_id:
        return item
    for index in range(item.childCount()):
        found = _within(item.child(index), document_id)
        if found:
            return found
    return None


def _find_item(widget, document_id):
    """Search a whole tree widget, or one item's subtree when handed an
    item — the binder keeps its current item selected, so tests clear
    both trees before building an export scope from scratch."""
    if hasattr(widget, "topLevelItemCount"):
        roots = [widget.topLevelItem(i)
                 for i in range(widget.topLevelItemCount())]
    else:
        roots = [widget]
    for root in roots:
        found = _within(root, document_id)
        if found:
            return found
    return None


def _clear_selection(*widgets):
    for widget in widgets:
        for item in widget.selectedItems():
            item.setSelected(False)


class BookSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(
            Path(self.temporary.name) / "Novel", "Novel"
        )
        draft = self.project.documents()[0]
        # A binder folder with prose of its own: it is manuscript like
        # anything else, and it is never offered as a *template* — only
        # folders under templates/ are those (docs/TEMPLATES.md §1.1).
        self.binder_folder = self.project.add_document(
            "Old templates", draft.id, "folder"
        )
        front = self.project.add_document(
            "Generic front", self.binder_folder.id, "chapter"
        )
        self.project.write_content(front.id,
                                   "<p>Generic front text.</p>")

        self.book = self.project.add_document(
            "A Fictional Book", draft.id, "folder"
        )
        self.chapter = self.project.add_document(
            "Chapter One", self.book.id, "chapter"
        )
        self.scene = self.project.add_document(
            "First Light", self.chapter.id, "scene"
        )
        self.project.write_content(self.chapter.id, "<p>Chapter opener.</p>")
        self.project.write_content(self.scene.id,
                                   "<p>First light prose.</p>")

        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        # Never close() the window: closeEvent -> save_current on a closed
        # project raises into a modal QMessageBox and hangs offscreen.
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    # --- inspector sections -------------------------------------------------

    def test_folder_shows_book_settings_and_hides_doc_slot(self):
        self.window._select_document(self.book.id)

        self.assertFalse(self.window.book_template_box.isHidden())
        self.assertFalse(self.window.book_cover_edit.isHidden())
        self.assertTrue(self.window.doc_template_box.isHidden())
        self.assertTrue(self.window.doc_template_label.isHidden())

    def test_prose_selection_shows_doc_template_slot(self):
        self.window._select_document(self.scene.id)

        self.assertFalse(self.window.doc_template_box.isHidden())
        self.assertTrue(self.window.book_template_box.isHidden())
        self.assertTrue(self.window.cover_label.isHidden())
        values = [
            self.window.doc_template_box.itemData(index)
            for index in range(self.window.doc_template_box.count())
        ]
        self.assertIn("", values)          # Default (the set's own slot)
        self.assertIn("no-title", values)      # builtin: no chrome at all

    def test_picking_a_document_slot_persists(self):
        self.window._select_document(self.scene.id)
        box = self.window.doc_template_box

        box.setCurrentIndex(box.findData("no-title"))

        document = next(
            d for d in self.project.documents() if d.id == self.scene.id
        )
        self.assertEqual(document.doc_template, "no-title")

    def test_the_doc_template_box_carries_filenames(self):
        self.window._select_document(self.scene.id)
        box = self.window.doc_template_box

        self.assertTrue(box.isEditable())
        index = box.findData("no-title")
        self.assertGreaterEqual(index, 0)
        self.assertEqual(box.itemText(index), "no-title.html")

    def test_a_filename_typed_before_the_file_exists_saves(self):
        self.window._select_document(self.scene.id)
        box = self.window.doc_template_box
        # Nothing in any set answers this name — the writer is asking
        # for a template they have not created yet, and asks for it the
        # way it will be named on disk (docs/TEMPLATES.md §1.2).
        box.lineEdit().setText("fancynameit-scene.html")
        box.lineEdit().editingFinished.emit()

        document = next(
            d for d in self.project.documents() if d.id == self.scene.id
        )
        self.assertEqual(document.doc_template, "fancynameit-scene")

        # The name keeps its row on the way back: the box still carries
        # the filename, selected, although no file answers it yet — the
        # export falls back to the set's own slot until one does.
        self.window._select_document(self.chapter.id)
        self.window._select_document(self.scene.id)
        box = self.window.doc_template_box
        self.assertEqual(box.currentData(), "fancynameit-scene")
        self.assertEqual(box.currentText(), "fancynameit-scene.html")

    def test_the_default_entry_saves_no_name(self):
        self.window._select_document(self.scene.id)
        box = self.window.doc_template_box
        box.setCurrentIndex(box.findData("no-title"))
        box.setCurrentIndex(0)                     # Default set's own slot

        document = next(
            d for d in self.project.documents() if d.id == self.scene.id
        )
        self.assertEqual(document.doc_template, "")

    def test_picking_a_book_template_persists(self):
        create_template_set("Editorial", self.project.root)
        self.window._select_document(self.book.id)
        box = self.window.book_template_box
        # Automatic, plus every discovered set. A binder folder is never
        # offered — a folder is a template only under templates/ (§1.1),
        # so the chooser names disk sets and nothing else.
        names = [box.itemData(index) for index in range(box.count())]
        self.assertIn("", names)                  # Automatic
        self.assertIn("Editorial", names)
        self.assertNotIn("Old templates", names)

        box.setCurrentIndex(box.findData("Editorial"))

        self.assertEqual(
            self.project.book_settings(self.book.id),
            {"template": "Editorial"},
        )

    def test_cover_round_trips_and_clearing_removes_the_entry(self):
        self.window._select_document(self.book.id)
        self.window.book_cover_edit.setText("/tmp/fake-cover.png")
        self.window._book_settings_changed()
        self.assertEqual(
            self.project.book_settings(self.book.id),
            {"cover": "/tmp/fake-cover.png"},
        )

        # Repopulate through a selection round-trip: the line shows it.
        self.window._select_document(self.scene.id)
        self.window._select_document(self.book.id)
        self.assertEqual(self.window.book_cover_edit.text(),
                         "/tmp/fake-cover.png")

        self.window.book_cover_edit.setText("")
        self.window._book_settings_changed()
        self.assertEqual(self.project.book_settings(self.book.id), {})

    # --- the project total --------------------------------------------------

    def test_every_binder_word_counts_because_templates_are_on_disk(self):
        # Nothing in the binder is template side any more (§1.1): a
        # template is a folder under templates/, holding files rather
        # than binder documents, so the total is simply every document's
        # words — no subtree walked, nothing excluded.
        self.window._refresh_outliner()
        documents = self.project.documents()
        self.assertEqual(
            self.window._project_total,
            sum(d.word_count for d in documents),
        )
        self.assertGreater(self.window._project_total, 0)

    # --- multi-selection and export scope -----------------------------------

    def test_outliner_multi_selection_survives_a_refresh(self):
        outliner = self.window.outliner
        _find_item(outliner, self.chapter.id).setSelected(True)
        _find_item(outliner, self.scene.id).setSelected(True)
        self.assertEqual(len(outliner.selectedItems()), 2)

        self.window.save_current()          # ends in _refresh_outliner()

        selected = {
            item.data(0, Qt.UserRole) for item in outliner.selectedItems()
        }
        self.assertEqual(selected, {self.chapter.id, self.scene.id})

    def test_export_scope_is_the_union_of_both_trees(self):
        _clear_selection(self.window.tree, self.window.outliner)
        _find_item(self.window.tree, self.scene.id).setSelected(True)
        _find_item(self.window.outliner, self.chapter.id).setSelected(True)

        self.assertEqual(
            self.window._gather_export_selection(),
            {self.scene.id, self.chapter.id},
        )
        self.assertTrue(
            self.window._export_summary({self.scene.id}).startswith(
                "Selection"
            )
        )
        self.assertTrue(
            self.window._export_summary(None).startswith("Whole project")
        )

        for widget in (self.window.tree, self.window.outliner):
            for item in widget.selectedItems():
                item.setSelected(False)
        self.assertIsNone(self.window._gather_export_selection())

    def test_export_dialog_shows_scope_and_no_checklist(self):
        sets = discover_template_sets(self.project.root)

        dialog = ExportOptionsDialog(
            "Selection — 1 item(s): First Light", sets, "default"
        )
        try:
            self.assertEqual(dialog.summary_label.text(),
                             "Selection — 1 item(s): First Light")
            self.assertFalse(hasattr(dialog, "documents"))  # checklist gone
            self.assertGreaterEqual(dialog.set_box.count(), 1)
        finally:
            dialog.deleteLater()

    def test_export_with_empty_selection_writes_the_whole_project(self):
        # The binder keeps its current item selected after a switch, so
        # "nothing selected" has to be built: deselect, then export.
        _clear_selection(self.window.tree, self.window.outliner)
        destination = Path(self.temporary.name) / "whole.txt"
        with patch.object(ExportOptionsDialog, "exec",
                          return_value=QDialog.Accepted), \
                patch("lekhika.app.QFileDialog.getSaveFileName",
                      return_value=(str(destination), "")):
            self.window.export("text")

        exported = destination.read_text(encoding="utf-8")
        self.assertIn("Chapter opener.", exported)
        self.assertIn("First light prose.", exported)
        # A binder folder is manuscript like any other: nothing in the
        # binder is template side (§1.1), so the whole project exports
        # the whole binder.
        self.assertIn("Generic front text.", exported)

    def test_selection_export_through_the_window_keeps_book_scope(self):
        # Selecting the book folder: expansion pulls its chapters, and a
        # folder elsewhere in the project stays out — scope, not a mark.
        _clear_selection(self.window.tree, self.window.outliner)
        _find_item(self.window.tree, self.book.id).setSelected(True)
        destination = Path(self.temporary.name) / "book.txt"
        with patch.object(ExportOptionsDialog, "exec",
                          return_value=QDialog.Accepted), \
                patch("lekhika.app.QFileDialog.getSaveFileName",
                      return_value=(str(destination), "")):
            self.window.export("text")

        exported = destination.read_text(encoding="utf-8")
        self.assertIn("Chapter opener.", exported)
        self.assertIn("First light prose.", exported)
        self.assertNotIn("Generic front text.", exported)

    # --- link policy (2.2) --------------------------------------------------

    def test_link_policy_combo_persists_and_clearing_removes_the_entry(self):
        self.window._select_document(self.book.id)
        box = self.window.link_policy_box
        values = [box.itemData(index) for index in range(box.count())]
        self.assertEqual(values, ["", "strip", "plain-text", "keep-as-note"])

        box.setCurrentIndex(box.findData("keep-as-note"))
        self.assertEqual(
            self.project.book_settings(self.book.id),
            {"links": "keep-as-note"},
        )

        box.setCurrentIndex(box.findData(""))
        self.assertEqual(self.project.book_settings(self.book.id), {})

    def test_link_policy_row_is_folder_only(self):
        self.window._select_document(self.book.id)
        self.assertFalse(self.window.link_policy_box.isHidden())
        self.window._select_document(self.scene.id)
        self.assertTrue(self.window.link_policy_box.isHidden())
        self.assertTrue(self.window.link_policy_label.isHidden())

    def test_preferences_offer_the_app_link_default(self):
        from lekhika.app import PreferencesDialog

        # Preferences edits the app-level default only; the project's
        # own setting (per-book, inspector) must not be touched either
        # way — that is the app-defaults vs per-project line (UI-NOTES 4).
        QSettings().remove("link_policy/default")
        with patch.object(PreferencesDialog, "exec",
                          return_value=QDialog.Accepted):
            self.window.preferences()
        self.assertEqual(
            QSettings().value("link_policy/default"), "plain-text")
        self.assertEqual(self.project.get_setting("link_policy"), "")
        QSettings().remove("link_policy/default")

        QSettings().setValue("link_policy/default", "strip")
        dialog = PreferencesDialog(11, {"fonts": False, "paragraphs": False,
                                        "inline": False, "alignment": False})
        try:
            self.assertEqual(dialog.link_default_box.currentData(), "strip")
            # Always editable: it is this installation's default, not a
            # property of whatever book happens to be open.
            self.assertTrue(dialog.link_default_box.isEnabled())
        finally:
            dialog.deleteLater()
            # The app default is global: leave it as found, or later
            # suites inherit "strip" through the export chain.
            QSettings().remove("link_policy/default")

    def test_the_app_link_default_reaches_an_unset_book(self):
        # The dialog stores link_policy/default; an export with no book
        # override and no project setting must actually obey it
        # (docs/PREFERENCES.md), or the row in Preferences is decoration.
        self.project.write_content(
            self.chapter.id,
            "<p>See [[A Fictional Book]] for the rest.</p>")
        destination = Path(self.temporary.name) / "book.html"
        self.project.export_html(destination, {self.chapter.id})
        self.assertIn("See A Fictional Book for the rest.",
                      destination.read_text(encoding="utf-8"))

        QSettings().setValue("link_policy/default", "strip")
        self.addCleanup(QSettings().remove, "link_policy/default")
        self.project.export_html(destination, {self.chapter.id})
        self.assertNotIn("A Fictional Book",
                         destination.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
