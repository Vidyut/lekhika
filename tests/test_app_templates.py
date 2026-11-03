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

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lekhak.app import MainWindow  # noqa: E402
from lekhak.project import (  # noqa: E402
    TEMPLATE_CONFIG_KEY, TEMPLATE_DOC_PREFIX, TEMPLATE_FILE_PREFIX,
    Project,
)

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
        self.project.write_content(self.scene.id, "<p>Chapter prose.</p>")
        self.project.set_template(self.scene.id, True)

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
        return [table.item(row, 0).text()
                for row in range(table.rowCount())
                if table.item(row, 0)]

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
        self.window._select_document(self.scene.id)
        self.assertFalse(self.window.template_panel.isVisibleTo(self.window))

        self.window.template_flag_box.setChecked(True)

        self.assertTrue(self.project.is_template(self.scene.id))
        self.assertTrue(self.window.template_panel.isVisibleTo(self.window))
        self.assertFalse(self.window.metadata_section.isVisibleTo(self.window))

        self.window.template_flag_box.setChecked(False)

        self.assertFalse(self.project.is_template(self.scene.id))
        self.assertFalse(self.window.template_panel.isVisibleTo(self.window))
        self.assertTrue(self.window.metadata_section.isVisibleTo(self.window))

    def test_the_design_reaches_the_document_and_the_file(self):
        self.window._select_document(self.scene.id)
        self.window.template_flag_box.setChecked(True)

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
        self.window.copy_defaults_button.click()

        copied = self.root / "templates" / "default"
        self.assertTrue(copied.is_dir())
        self.assertTrue((copied / "document.html").exists())


if __name__ == "__main__":
    unittest.main()
