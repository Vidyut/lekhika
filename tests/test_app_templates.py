"""Templates as documents, and the roles around them (1.17).

The template is a checkbox, not a role: a document marked as one keeps
its own body as markup and is designed in the sidebar, and it stays out
of the book. The Type dropdown offers only the roles of its own group,
and a field created on one document shows, empty, on every other
document of the kind.

Same test contract as the other GUI suites: QSettings redirected into a
temp dir, windows never closed().
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lekhak.app import MainWindow  # noqa: E402
from lekhak.project import (  # noqa: E402
    TEMPLATE_CONFIG_KEY, TEMPLATE_DOC_PREFIX, TEMPLATE_FILE_PREFIX,
    Project,
)
from lekhak.templates import BUILTIN_PLACEHOLDERS  # noqa: E402
from html import escape  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhak")
_application.setApplicationName("Lekhak")
_settings_dir = tempfile.mkdtemp(prefix="lekhak-templates-settings-")
# QSettings objects created from now on read/write under _settings_dir.
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class TemplateDocumentTests(unittest.TestCase):
    """The template mark, the one-file template and the export."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "Novel"
        self.project = Project.create(self.root, "Novel")
        # The project's own root: create() gives it a .lekhak suffix.
        self.root = self.project.root
        self.folder = self.project.documents()[0]      # Draft, kind "folder"
        self.scene = self.project.add_document(
            "Scene", self.folder.id, "scene")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def _write_template_file(self, name="mine", body="<p>${Content}</p>\n"):
        folder = self.root / "templates"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (name + ".html")
        path.write_text("# a design\nNew Chapter: Recto\n" + body,
                        encoding="utf-8")
        return path

    def test_converting_content_to_template_is_refused(self):
        # FIRST metadata must be Template: yes — no content before.
        self.project.write_content(self.scene.id, "<p>Prose.</p>")
        with self.assertRaises(ValueError):
            self.project.set_template(self.scene.id, True)
        self.assertFalse(self.project.is_template(self.scene.id))

    def test_confirmed_conversion_is_allowed(self):
        # After the popup ("Yes, I am sure"), confirmed=True converts.
        self.project.write_content(self.scene.id, "<p>Prose.</p>")
        self.project.set_template(self.scene.id, True, confirmed=True)
        self.assertTrue(self.project.is_template(self.scene.id))

    def test_template_folder_starts_empty(self):
        # The templates folder itself can be made a template while
        # empty, then children may be added as templates from birth.
        folder = self.project.add_document("Templates", self.folder.id, "folder")
        self.project.set_template(folder.id, True)
        self.assertTrue(self.project.is_template(folder.id))

    def test_the_template_mark_round_trips(self):
        self.project.set_template(self.scene.id, True)
        self.assertTrue(self.project.is_template(self.scene.id))
        self.assertEqual(self.project.metadata(self.scene.id)["Template"],
                         "yes")
        self.assertEqual([d.id for d in self.project.template_documents()],
                         [self.scene.id])

        self.project.set_template(self.scene.id, False)
        self.assertFalse(self.project.is_template(self.scene.id))
        self.assertNotIn("Template", self.project.metadata(self.scene.id))

    def test_a_template_document_stays_out_of_the_export(self):
        # Template starts empty, then markup is written after (no conversion).
        self.project.set_template(self.scene.id, True)
        self.project.write_content(self.scene.id, "<p>Chapter prose.</p>")

        destination = Path(self.temporary.name) / "book.txt"
        self.project.export_text(destination)

        exported = destination.read_text(encoding="utf-8")
        self.assertNotIn("Chapter prose.", exported)

    def test_a_one_file_template_is_found(self):
        self._write_template_file()

        self.assertEqual([path.stem for path in
                          self.project.single_file_templates()], ["mine"])
        self.assertIn("<p>${Content}</p>",
                      self.project.template_fragment("mine"))
        self.assertEqual(self.project.template_config("mine")["new chapter"],
                         "Recto")

    def test_a_template_document_answers_with_its_own_body(self):
        self.project.write_content(self.scene.id, "<p>The markup.</p>")
        self.project.set_metadata(self.scene.id, {
            TEMPLATE_CONFIG_KEY: "# a design\nNew Scene: page-break\n"})

        name = TEMPLATE_DOC_PREFIX + self.scene.id
        self.assertEqual(self.project.template_override(name),
                         self.project.read_content(self.scene.id))
        self.assertEqual(self.project.template_config(name)["new scene"],
                         "page-break")

    def test_the_design_travels_into_the_file_head(self):
        self.project.set_template(self.scene.id, True)
        self.project.set_metadata(self.scene.id, {
            TEMPLATE_CONFIG_KEY: "# a design\nNew Chapter: Recto\n"})
        self.project.write_content(self.scene.id, "<p>Prose.</p>")

        written = self.project.content_path(self.scene.id).read_text(
            encoding="utf-8")
        self.assertIn("template: yes", written)
        self.assertIn("New Chapter: Recto", written)


class TemplateInspectorTests(unittest.TestCase):
    """The inspector: the roles, the fields and the template switch."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "Novel"
        self.project = Project.create(self.root, "Novel")
        # The project's own root: create() gives it a .lekhak suffix.
        self.root = self.project.root
        self.folder = self.project.documents()[0]      # Draft, kind "folder"
        self.scene = self.project.add_document(
            "Scene", self.folder.id, "scene")
        self.other = self.project.add_document(
            "Other", self.folder.id, "scene")
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _types(self):
        return [self.window.type_box.itemText(index)
                for index in range(self.window.type_box.count())]

    def _table_keys(self):
        table = self.window.metadata_table
        return [table.item(row, 1).text()
                for row in range(table.rowCount())
                if table.item(row, 1) and table.item(row, 1).text()]

    def test_the_type_box_offers_only_its_own_groups_roles(self):
        self.window._select_document(self.folder.id)
        self.assertIn("Folder", self._types())
        self.assertNotIn("Document", self._types())

        self.window._select_document(self.scene.id)
        self.assertIn("Document", self._types())
        self.assertNotIn("Folder", self._types())

    def test_a_new_type_joins_its_own_group(self):
        self.window._select_document(self.scene.id)

        self.window.type_add_edit.setText("Part")
        self.window.type_add_button.click()

        self.assertIn("Part", self.project.known_types("document"))
        self.assertNotIn("Part", self.project.known_types("folder"))
        self.assertEqual(self.project.metadata(self.scene.id)["Type"],
                         "Part")

    def test_a_field_created_on_one_document_shows_empty_on_the_other(self):
        self.project.set_metadata(self.scene.id, {"POV": "Mira"})

        self.window._select_document(self.other.id)

        self.assertIn("POV", self._table_keys())
        row = self._table_keys().index("POV")
        self.assertEqual(self.window.metadata_table.row_value(row), "")

    def test_the_template_tick_swaps_the_sidebar(self):
        # No inspector switch any more; selecting a template document
        # still swaps the sidebar for its design panel.
        self.window._select_document(self.scene.id)
        self.assertFalse(self.window.template_panel.isVisibleTo(self.window))
        # The switch is gone: content cannot be flipped by a click.
        self.assertIsNone(self.window.template_flag_box)

        self.project.set_template(self.scene.id, True)
        self.window._reload_tree(self.scene.id)
        self.window._select_document(self.scene.id)

        self.assertTrue(self.project.is_template(self.scene.id))
        self.assertTrue(self.window.template_panel.isVisibleTo(self.window))
        self.assertFalse(self.window.metadata_section.isVisibleTo(self.window))

        self.project.set_template(self.scene.id, False)
        self.window._reload_tree(self.scene.id)
        self.window._select_document(self.scene.id)

        self.assertFalse(self.project.is_template(self.scene.id))
        self.assertFalse(self.window.template_panel.isVisibleTo(self.window))
        self.assertTrue(self.window.metadata_section.isVisibleTo(self.window))

    def test_the_design_reaches_the_document_and_the_file(self):
        self.window._select_document(self.scene.id)
        self.project.set_template(self.scene.id, True)
        self.window._reload_tree(self.scene.id)
        self.window._select_document(self.scene.id)

        self.window.template_config_box.setPlainText(
            "# a design\nNew Chapter: Recto\n")
        self.window.save_current()

        self.assertIn("New Chapter: Recto",
                      self.project.metadata(self.scene.id)[
                          TEMPLATE_CONFIG_KEY])
        written = self.project.content_path(self.scene.id).read_text(
            encoding="utf-8")
        self.assertIn("template: yes", written)
        self.assertIn("New Chapter: Recto", written)

    def test_the_apply_dropdown_lists_the_templates_the_project_knows(self):
        folder = self.root / "templates"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "mine.html").write_text("<p>${Content}</p>\n",
                                          encoding="utf-8")
        self.project.set_template(self.other.id, True)

        self.window._select_document(self.scene.id)

        data = [self.window.doc_template_box.itemData(index)
                for index in range(self.window.doc_template_box.count())]
        self.assertIn(TEMPLATE_FILE_PREFIX + "mine", data)
        self.assertIn(TEMPLATE_DOC_PREFIX + self.other.id, data)

    def test_the_apply_list_refreshes_when_the_box_opens(self):
        # A template dropped into templates/ while this document is open
        # must not wait for the next selection to show up.
        self.window._select_document(self.scene.id)
        folder = self.root / "templates"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "late.html").write_text("<p>${Content}</p>\n",
                                          encoding="utf-8")

        self.window.doc_template_box.showPopup()

        data = [self.window.doc_template_box.itemData(index)
                for index in range(self.window.doc_template_box.count())]
        self.assertIn(TEMPLATE_FILE_PREFIX + "late", data)

    def test_picking_a_one_file_template_saves_its_name(self):
        folder = self.root / "templates"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "mine.html").write_text("<p>${Content}</p>\n",
                                          encoding="utf-8")
        self.window._select_document(self.scene.id)

        box = self.window.doc_template_box
        box.setCurrentIndex(box.findData(TEMPLATE_FILE_PREFIX + "mine"))

        document = next(d for d in self.project.documents()
                        if d.id == self.scene.id)
        self.assertEqual(document.doc_template, TEMPLATE_FILE_PREFIX + "mine")

    def test_picking_a_template_document_saves_its_name(self):
        self.project.set_template(self.other.id, True)
        self.window._select_document(self.scene.id)

        box = self.window.doc_template_box
        box.setCurrentIndex(box.findData(TEMPLATE_DOC_PREFIX + self.other.id))

        document = next(d for d in self.project.documents()
                        if d.id == self.scene.id)
        self.assertEqual(document.doc_template,
                         TEMPLATE_DOC_PREFIX + self.other.id)

    def test_copying_the_default_templates_puts_them_in_the_project(self):
        # Nothing selected: historic landing is templates/default.
        self.window.current_id = None
        self.window._clear_binder_selection()
        self.window.copy_defaults_button.click()

        copied = self.root / "templates" / "default"
        self.assertTrue(copied.is_dir())
        self.assertTrue((copied / "document.html").exists())
        # And the writer lands in the copy: the button used to write the
        # folder and leave the view on the shipped, read-only set.
        landed = self.window.current_template_set
        self.assertIsNotNone(landed)
        self.assertEqual(landed.directory, copied)

    def test_copying_defaults_to_a_folder_places_them_inside_it(self):
        self.window._select_document(self.folder.id)
        self.window.copy_defaults_button.click()

        copied = self.root / "Draft" / "default"
        self.assertTrue(copied.is_dir())
        self.assertTrue((copied / "document.html").exists())
        self.assertEqual(self.window.current_template_set.directory, copied)

    def test_copying_to_a_subfolder_warns_about_parent_documents(self):
        # Sub-folder copy warns that parent documents won't be dressed.
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        sub = self.project.add_document("Sub", self.folder.id, "folder")
        self.window._reload_tree()
        self.window._select_document(sub.id)
        with patch.object(QMessageBox, "question",
                         return_value=QMessageBox.StandardButton.No,
                         ) as ask:
            self.window.copy_defaults_button.click()
        ask.assert_called_once()
        # No copy happened.
        self.assertFalse((self.root / "Draft" / "Sub" / "default").is_dir())

    def test_copying_defaults_next_to_a_document_places_them_beside_it(self):
        self.window._select_document(self.scene.id)
        self.window.copy_defaults_button.click()

        copied = self.root / "Draft" / "default"
        self.assertTrue(copied.is_dir())
        self.assertTrue((copied / "document.html").exists())
        # The scene itself is untouched.
        self.assertTrue((self.root / "Draft" / "Scene.html").exists())
        self.assertEqual(self.window.current_template_set.directory, copied)

    def test_a_set_copied_into_a_folder_is_tracked_and_never_exported(self):
        # The DB tracks templates copied into folders (is_template mark),
        # so the slot files read as format, never manuscript.
        real = self.project.add_document(
            "Real", self.folder.id, "scene")
        self.project.write_content(real.id, "<p>Real prose</p>")
        self.window._select_document(self.folder.id)
        self.window.copy_defaults_button.click()

        marked = [d for d in self.project.documents()
                  if d.path.startswith("Draft/default")
                  and self.project.is_template(d.id)]
        self.assertTrue(marked, "slot files carry the Template mark")
        exported = [d.path for d in self.project._export_documents()]
        self.assertIn("Draft/Real.html", exported)
        self.assertFalse(any(path.startswith("Draft/default")
                             for path in exported))


class TemplateSelectorTests(unittest.TestCase):
    """Both template rows speak the selected Type (1.18).

    A Book folder asks for a "Book template" and means the set named
    after Book; a chapter document under it means the Chapter face of
    the set its folder chose. Both are shown, never stored, until the
    writer changes the row.
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "Novel"
        self.project = Project.create(self.root, "Novel")
        self.root = self.project.root
        self.folder = self.project.documents()[0]      # Draft, kind "folder"
        self.scene = self.project.add_document(
            "Scene", self.folder.id, "scene")
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _set_type(self, document_id, name, group):
        self.project.add_type(group, name)
        self.project.set_metadata(document_id, {"Type": name})

    def _template_set(self, name, *files):
        folder = self.root / "templates" / name
        folder.mkdir(parents=True, exist_ok=True)
        for file in files:
            (folder / file).write_text("<p>${Content}</p>\n",
                                       encoding="utf-8")

    def _document(self, document_id):
        return next(d for d in self.project.documents()
                    if d.id == document_id)

    def _show(self, document_id):
        """Select a document, after one the panel is not already on.

        The inspector refreshes on a selection *change* (a project opens
        with its first folder already selected), so a selection that
        changes nothing would read the panel's previous state.
        """
        elsewhere = (self.scene.id if document_id == self.folder.id
                     else self.folder.id)
        self.window._select_document(elsewhere)
        self.window._select_document(document_id)

    def test_each_row_is_named_after_its_own_type(self):
        self._show(self.folder.id)
        self.assertEqual(self.window.book_template_label.text(),
                         "Folder template")

        self._show(self.scene.id)
        self.assertEqual(self.window.doc_template_label.text(),
                         "Document template")

        self._set_type(self.folder.id, "Book", "folder")
        self._show(self.folder.id)
        self.assertEqual(self.window.book_template_label.text(),
                         "Book template")

    def test_changing_the_type_retitles_the_row_at_once(self):
        self.project.add_type("folder", "Book")
        self._show(self.folder.id)

        self.window.type_box.setCurrentIndex(
            self.window.type_box.findText("Book"))

        self.assertEqual(self.project.type_of(self._document(self.folder.id)),
                         "Book")
        self.assertEqual(self.window.book_template_label.text(),
                         "Book template")

    def test_a_folder_defaults_to_the_set_named_after_its_type(self):
        self._template_set("Book", "chapter.html")
        self._set_type(self.folder.id, "Book", "folder")

        self._show(self.folder.id)

        box = self.window.book_template_box
        self.assertEqual(box.currentData(), "Book")
        # Stored, not merely shown: naming a set after the Type is the
        # assignment, so the export reads the same answer (1.18).
        self.assertEqual(
            self.project.book_settings(self.folder.id).get("template", ""),
            "Book")

        # And it stays a choice: picking a set stores that one.
        self.assertGreater(box.count(), 1)
        box.setCurrentIndex(1)
        self.assertEqual(
            self.project.book_settings(self.folder.id)["template"],
            box.itemData(1))

    def test_a_stored_choice_outranks_a_matching_name(self):
        self._template_set("Book", "chapter.html")
        self._template_set("Other", "chapter.html")
        self._set_type(self.folder.id, "Book", "folder")
        self.project.set_book_settings(self.folder.id, {"template": "Other"})

        self._show(self.folder.id)

        self.assertEqual(self.window.book_template_box.currentData(), "Other")

    def test_no_matching_name_stores_nothing(self):
        self._template_set("Other", "chapter.html")
        self._set_type(self.folder.id, "Diary", "folder")

        self._show(self.folder.id)

        self.assertEqual(self.window.book_template_box.currentData(), "")
        self.assertEqual(
            self.project.book_settings(self.folder.id).get("template", ""), "")

    def test_a_document_defaults_to_the_face_named_after_its_type(self):
        self._template_set("Mine", "Chapter.html")
        self.project.set_book_settings(self.folder.id, {"template": "Mine"})
        self._set_type(self.scene.id, "Chapter", "document")

        self._show(self.scene.id)

        self.assertEqual(self.window.doc_template_box.currentData(), "Chapter")
        self.assertEqual(self.window.doc_template_label.text(),
                         "Chapter template")
        # Stored, so the export wears the same face the row shows (1.18).
        self.assertEqual(self._document(self.scene.id).doc_template, "Chapter")

    def test_a_document_takes_the_face_of_the_set_its_parent_chose(self):
        self._template_set("Chosen", "Note.html")
        self._template_set("Elsewhere", "Chapter.html")
        self._set_type(self.scene.id, "Chapter", "document")

        # The parent chose nothing, so nothing is derived from a set.
        self._show(self.scene.id)
        self.assertEqual(self.window.doc_template_box.currentData(), "")

        # The chosen set carries no Chapter face: the set's own slot
        # stays, even though another set offers a Chapter one.
        self.project.set_book_settings(self.folder.id, {"template": "Chosen"})
        self._show(self.scene.id)
        self.assertEqual(self.window.doc_template_box.currentData(), "")

    def test_a_stored_face_outranks_a_matching_name(self):
        self._template_set("Mine", "Chapter.html", "Note.html")
        self.project.set_book_settings(self.folder.id, {"template": "Mine"})
        self._set_type(self.scene.id, "Chapter", "document")
        self.project.update_document(self.scene.id, doc_template="Note")

        self._show(self.scene.id)

        self.assertEqual(self.window.doc_template_box.currentData(), "Note")


class TemplateCreationTests(unittest.TestCase):
    """What making a document inside a template means (1.18)."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "Novel"
        self.project = Project.create(self.root, "Novel")
        self.root = self.project.root
        self.folder = self.project.documents()[0]      # Draft, kind "folder"
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _template_folder(self):
        book = self.project.add_document("Book", self.folder.id, "folder")
        self.project.set_template(book.id, True)
        self.window._reload_tree(book.id)
        return book

    def _made_in(self, parent_id):
        return [document for document in self.project.documents()
                if document.parent_id == parent_id]

    def test_a_document_made_inside_a_template_is_a_template(self):
        book = self._template_folder()

        self.window.add_chapter()

        made = self._made_in(book.id)
        self.assertEqual(len(made), 1)
        self.assertTrue(self.project.is_template(made[0].id))
        # The tick is the writer's: unticking makes it a document again.
        self.project.set_template(made[0].id, False)
        self.assertFalse(self.project.is_template(made[0].id))

    def test_a_document_made_outside_a_template_is_not_one(self):
        self.window._reload_tree(self.folder.id)

        self.window.add_scene()

        made = [document for document in self._made_in(self.folder.id)
                if document.title == "New Document"]
        self.assertEqual(len(made), 1)
        self.assertFalse(self.project.is_template(made[0].id))

    def test_a_sub_template_follows_the_template_folders_paper(self):
        """Trim and mirroring are the folder's; margins and page start
        are the page's own, so a title page can be wider and begin
        where it likes (1.18)."""
        book = self._template_folder()
        self.window.add_chapter()

        note = self.window.template_geometry_note
        self.assertTrue(note.isVisibleTo(self.window))
        self.assertIn("Book", note.text())
        self.assertIn("set on the template folder", note.text())
        self.assertFalse(self.window.size_preset_box.isVisibleTo(self.window))
        self.assertFalse(
            self.window.margin_mirror_box.isVisibleTo(self.window))
        self.assertTrue(
            self.window.margin_preset_box.isVisibleTo(self.window))
        self.assertTrue(self.window.page_start_box.isVisibleTo(self.window))

        # A template outside any template keeps its own geometry rows.
        loose = self.project.add_document("Loose", self.folder.id, "chapter")
        self.project.set_template(loose.id, True)
        self.window._reload_tree(loose.id)

        self.assertFalse(
            self.window.template_geometry_note.isVisibleTo(self.window))
        self.assertTrue(self.window.size_preset_box.isVisibleTo(self.window))
        self.assertTrue(
            self.window.margin_mirror_box.isVisibleTo(self.window))

    def test_a_sub_template_can_set_its_own_margins(self):
        """The margins row must reach the document's design, not only a
        template file's head (1.18) — a band pick is stored at once."""
        book = self._template_folder()
        self.window.add_chapter()
        made = self._made_in(book.id)
        self.assertEqual(len(made), 1)

        box = self.window.margin_preset_box
        box.setCurrentIndex(box.findData(0.75))          # a KDP band

        self.assertIn("Margins", self.window.template_config_box.toPlainText())
        self.assertIn("Margins",
                      self.project.metadata(made[0].id)[TEMPLATE_CONFIG_KEY])

    def test_the_margin_labels_follow_the_books_mirroring(self):
        """A sub-template cannot write the mirroring, so its edge boxes
        are named by the book's (1.18)."""
        self._template_folder()
        self.project.set_page_setup(
            self.folder.id,
            {"trim": "6x9", "width": 6.0, "height": 9.0, "top": 0.5,
             "bottom": 0.5, "inside": 0.5, "outside": 0.5, "mirror": True,
             "gutter": 0.0})
        self.window.add_chapter()

        self.assertEqual(self.window.margin_inside_label.text(),
                         "Inside (spine)")
        self.assertEqual(self.window.margin_outside_label.text(),
                         "Outside")

    def test_the_head_boxes_write_the_three_piece_line(self):
        """Left, middle and right are the config's one `Header:` line
        (1.2) — the same line a hand would have typed into the box."""
        book = self._template_folder()
        self.window.add_chapter()
        made = self._made_in(book.id)
        self.assertEqual(len(made), 1)

        self.window.head_edits[("header", "middle")].setText("${BookTitle}")
        self.window.head_edits[("header", "middle")].editingFinished.emit()

        self.assertIn("Header: , ${BookTitle},",
                      self.window.template_config_box.toPlainText())
        self.assertIn("Header: , ${BookTitle},",
                      self.project.metadata(made[0].id)[TEMPLATE_CONFIG_KEY])

    def test_the_facing_pages_pick_writes_the_verso_line(self):
        """A mirrored book's even pages carry their own head, and the
        pick is what says which line the boxes are speaking for."""
        self._template_folder()
        self.window.add_chapter()
        box = self.window.heads_pages_box
        box.setCurrentIndex(box.findData(" left"))

        self.window.head_edits[("footer", "middle")].setText("${Page}")
        self.window.head_edits[("footer", "middle")].editingFinished.emit()

        self.assertIn("Footer left: , ${Page},",
                      self.window.template_config_box.toPlainText())

    def test_clearing_a_head_takes_its_line_away(self):
        """An absent line means "keep the sample's head", so a cleared row
        leaves nothing behind rather than an empty line."""
        self._template_folder()
        self.window.add_chapter()
        self.window.template_config_box.setPlainText("Header: A, B, C\n")
        self.assertEqual(self.window.head_edits[("header", "left")].text(), "A")

        for piece in ("left", "middle", "right"):
            self.window.head_edits[("header", piece)].setText("")
        self.window.head_edits[("header", "left")].editingFinished.emit()

        self.assertNotIn("Header", self.window.template_config_box.toPlainText())

    def test_the_first_page_pick_writes_the_opening_pages_line(self):
        """`First page` is the pick that speaks for each section's opening
        page (1.2, 3.1) — the head a chapter opener should not wear."""
        self._template_folder()
        self.window.add_chapter()
        box = self.window.heads_pages_box
        box.setCurrentIndex(box.findData(" first page"))

        self.window.head_none_boxes["header"].setChecked(True)

        self.assertIn("Header first page: none",
                      self.window.template_config_box.toPlainText())
        # And the main body's own line is untouched, because the pick
        # says which line the boxes are speaking for.
        self.assertNotIn("\nHeader: none",
                         self.window.template_config_box.toPlainText())

    def test_the_font_row_writes_the_head_font_line(self):
        """The running heads' own type (1.2, 3.1): one `Head font:` line
        for the header and the footer both."""
        self._template_folder()
        self.window.add_chapter()
        family = self.window.head_font_box.itemText(1)

        self.window.head_font_box.setCurrentIndex(1)
        self.window.head_size_box.setValue(8)

        stored = self.window.template_config_box.toPlainText()
        self.assertIn(f"Head font: {family}, 8", stored)
        # And reading it back: a line the head already carries shows in
        # the row rather than being silently written over.
        self.window.template_config_box.setPlainText(
            f"Head font: {family}, 9\n")
        self.assertEqual(self.window.head_size_box.value(), 9)
        self.assertEqual(self.window.head_font_box.currentIndex(), 1)

    def test_a_head_font_this_machine_lacks_still_shows(self):
        """A family the file names but this machine has not got is not
        thrown away by the row: it shows, so editing the size beside it
        cannot silently drop it (1.2)."""
        self._template_folder()
        self.window.add_chapter()
        self.window.template_config_box.setPlainText(
            "Head font: Lekhak Test Face, 9\n")

        self.assertEqual(self.window.head_font_box.currentText(),
                         "Lekhak Test Face")

        self.window.head_size_box.setValue(10)

        self.assertIn("Head font: Lekhak Test Face, 10",
                      self.window.template_config_box.toPlainText())

    def test_a_name_from_the_reference_page_lands_in_the_active_box(self):
        """The reference page is also the way to write a name (3.2): a
        click puts it at the cursor of the box the writer was in."""
        self._template_folder()
        self.window.add_chapter()
        edit = self.window.head_edits[("footer", "middle")]
        edit.setFocus()
        self.window.eventFilter(edit, QEvent(QEvent.Type.FocusIn))

        self.window.insert_placeholder("PageNo")

        self.assertEqual(edit.text(), "${PageNo}")
        self.assertIn("Footer: , ${PageNo},",
                      self.window.template_config_box.toPlainText())

    def test_a_name_from_the_reference_page_falls_to_the_config_box(self):
        """With no head box touched, the config box is the box that had
        the cursor — the page inserts somewhere sensible either way."""
        self._template_folder()
        self.window.add_chapter()
        self.window._head_focus = None

        self.window.insert_placeholder("BookTitle")

        self.assertIn("${BookTitle}",
                      self.window.template_config_box.toPlainText())

    def test_a_none_tick_says_the_pages_carry_no_head(self):
        """`Header: none` is not an empty line (3.1): it takes the head
        away where empty would keep the sample's, and greys the boxes
        that no longer speak."""
        self._template_folder()
        self.window.add_chapter()

        self.window.head_none_boxes["header"].setChecked(True)

        self.assertIn("Header: none", self.window.template_config_box.toPlainText())
        self.assertFalse(self.window.head_edits[("header", "middle")].isEnabled())


class PlaceholderReferenceTests(unittest.TestCase):
    """Document > Placeholder Reference (3.2): the ${...} vocabulary as a
    page — every built-in with one line, then this project's own metadata
    grouped by the Type that carries it."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "Novel"
        self.project = Project.create(self.root, "Novel")
        self.root = self.project.root
        self.folder = self.project.documents()[0]
        self.chapter = self.project.add_document(
            "Chapter One", self.folder.id, "chapter")
        self.scene = self.project.add_document(
            "Opening", self.chapter.id, "scene")
        self.project.set_metadata(self.chapter.id,
                                  {"Type": "Chapter", "PoV": "Maren"})
        self.project.set_metadata(self.scene.id,
                                  {"Type": "Scene", "Location": "Harbour"})
        self.project.set_project_metadata({"Author": ("A. Writer", "text", [])})
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def test_every_built_in_is_published_with_one_line(self):
        page = self.window._placeholder_reference_page()

        for name, meaning in BUILTIN_PLACEHOLDERS:
            self.assertIn(f"${{{name}}}", page)
            self.assertIn(escape(meaning), page)

    def test_the_projects_metadata_is_published_under_its_type(self):
        page = self.window._placeholder_reference_page()

        self.assertIn("<h2>Project</h2>", page)
        self.assertIn("${Author}", page)
        self.assertIn("<h2>Type Chapter</h2>", page)
        self.assertIn("${PoV}", page)
        self.assertIn("on 1 Chapter document", page)
        self.assertIn("${Location}", page)

    def test_the_dialog_shows_the_page_and_closes_when_asked(self):
        self.window.show_placeholder_reference()

        dialog = self.window.placeholder_dialog
        self.assertEqual(dialog.windowTitle(), "Placeholder Reference")
        self.assertIn("Placeholders", dialog.browser.toPlainText())
        self.assertTrue(dialog.isVisible())

        dialog.close()
        self.assertFalse(dialog.isVisible())


if __name__ == "__main__":
    unittest.main()
