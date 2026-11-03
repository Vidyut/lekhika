"""Where templates are, and what selecting one does (docs/TEMPLATES.md §1.1).

The binder's Templates area *is* the list of sets, the Outliner shows the
files of the set that is selected, and a file opens in the regular editor —
a fragment is an HTML file and this is an HTML editor (§1.2). Everything
here holds those to the one truth — the folders under ``templates/`` — and
to the flatness rule the author set ("it should appear in the Templates
folder. Not Templates > Default"): a duplicated set is a sibling of the one
it came from, never its child.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lekhak.app import (
    TEMPLATE_FILE_KIND,
    TEMPLATE_FOLDER_KIND,
    TEMPLATE_GROUP_KIND,
    TEMPLATE_PATH_ROLE,
    TEMPLATE_SET_KIND,
    TEMPLATE_SET_ROLE,
    MainWindow,
)
from lekhak.project import (
    AMAZON_TRIMS,
    KDP_MARGIN_BANDS,
    KDP_MARGIN_MIN,
    PAGE_TRIMS,
    Project,
)
from lekhak.templates import (
    TEMPLATE_CONFIG_NOTE,
    TEMPLATE_NAMING_NOTE,
    TemplateSet,
    copy_template_set,
    create_template_set,
    discover_template_sets,
)

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhak")
_application.setApplicationName("Lekhak")
_settings_dir = tempfile.mkdtemp(prefix="lekhak-template-nav-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class TemplateNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(
            Path(self.temporary.name) / "Novel", "Novel")
        self.draft = self.project.documents()[0]
        # Hermetic: discovery includes the app library under
        # XDG_CONFIG_HOME, which must not leak into these expectations.
        self.xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(
            Path(self.temporary.name) / "config")
        QSettings().remove("session/lastProject")
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        if self.xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self.xdg
        self.project.close()
        self.temporary.cleanup()

    # --- helpers -----------------------------------------------------------

    def _items(self):
        return self.window._all_items()

    def _kind_of(self, item):
        return item.data(0, Qt.UserRole + 1)

    def _of_kind(self, kind):
        return [item for item in self._items() if self._kind_of(item) == kind]

    def _outliner_items(self):
        result = []

        def collect(item):
            result.append(item)
            for index in range(item.childCount()):
                collect(item.child(index))

        for index in range(self.window.outliner.topLevelItemCount()):
            collect(self.window.outliner.topLevelItem(index))
        return result

    def _outliner_of_kind(self, kind):
        return [item for item in self._outliner_items()
                if self._kind_of(item) == kind]

    def _select_set(self, template_set):
        """Open a template set.

        The sets are listed in the Outliner, not in the binder: they are
        folders of fragments on disk rather than documents, and the
        binder is the writer's own tree.
        """
        self.window._open_template_set(template_set)

    def _outliner_rows(self, column=0):
        return [self.window.outliner.topLevelItem(index).text(column)
                for index in range(self.window.outliner.topLevelItemCount())]

    def _outliner_item(self, name, column=0):
        return next(
            item for index in range(self.window.outliner.topLevelItemCount())
            if (item := self.window.outliner.topLevelItem(index)).text(column)
            == name)

    def _pick(self, item):
        self.window.tree.setCurrentItem(item)

    # --- the sets are listed in the Outliner, not the binder ---------------

    def test_the_sets_are_not_rows_in_the_binder(self):
        # The binder is the writer's own tree (docs/STORAGE.md): a
        # template set is a folder of fragments, not a document, and a
        # second "Templates" folder in the binder only ever confused the
        # writer about where the books were.
        names = [item.text(0) for item in self._of_kind(TEMPLATE_SET_KIND)]
        self.assertEqual(names, [])
        self.assertEqual([item.text(0) for item in self._of_kind(
            TEMPLATE_GROUP_KIND)], [])

    def test_every_discovered_set_is_listed_in_the_outliner(self):
        self.window.show_template_sets()
        names = [item.text(0) for item in self._outliner_of_kind(
            TEMPLATE_SET_KIND)]
        self.assertEqual(names, [s.name for s in discover_template_sets(
            self.project.root)])
        self.assertEqual(names, ["default", "short story", "nothing"])

    def test_the_templates_folder_is_not_a_row_of_its_own(self):
        # The area is the project's templates/ folder on disk, not
        # another thing in the binder.
        create_template_set("Mine", self.project.root)
        self.project.sync_with_disk()
        self.assertEqual([d for d in self.project.documents()
                          if (d.path or "") == "templates"], [])
        self.window._reload_tree()
        self.assertEqual([item.text(0) for item in self._of_kind(
            TEMPLATE_SET_KIND)], [])

    def test_a_book_of_the_writers_own_called_templates_is_theirs(self):
        # A folder the writer made, with the same name as the templates
        # root, is not hijacked by it.
        mine = self.project.add_document("Templates", None, "folder")
        self.project.sync_with_disk()
        again = next(d for d in self.project.documents() if d.id == mine.id)
        self.assertNotEqual(again.path, "templates")
        self.assertTrue((self.project.root / again.path).is_dir())

    def test_a_project_without_a_templates_folder_gets_no_area(self):
        # No scaffolding is created in the database: the sets are a view
        # of the folders on disk, not another store.
        self.assertEqual([d for d in self.project.documents()
                          if (d.path or "") == "templates"], [])

    def test_the_listed_sets_carry_no_document_id(self):
        self.window.show_template_sets()
        for item in self._outliner_of_kind(TEMPLATE_SET_KIND):
            self.assertIsNone(item.data(0, Qt.UserRole))
            self.assertIsInstance(item.data(0, TEMPLATE_SET_ROLE), object)

    # --- selecting a set ---------------------------------------------------

    def test_selecting_a_set_lists_its_files_and_folders_in_the_outliner(self):
        created = create_template_set("Mine", self.project.root)
        self.window._reload_tree()
        self._select_set((created))

        self.assertEqual(self.window.current_template_set.name, "Mine")
        self.assertIsNone(self.window.current_id)
        self.assertIn("folder.html", self._outliner_rows())
        self.assertIn("print-config.txt", self._outliner_rows())
        self.assertIn("documents", self._outliner_rows())
        # ... and the shelf's file sits under it, exactly as on disk.
        shelf = self._outliner_item("documents")
        self.assertEqual(self._kind_of(shelf), TEMPLATE_FOLDER_KIND)
        self.assertEqual([shelf.child(i).text(0)
                          for i in range(shelf.childCount())],
                         ["contents.html", "contents.txt", "no-title.html", "no-title.txt"])
        for index in range(self.window.outliner.topLevelItemCount()):
            self.assertEqual(self._kind_of(
                self.window.outliner.topLevelItem(index)), TEMPLATE_FILE_KIND
                if self.window.outliner.topLevelItem(index).childCount() == 0
                else TEMPLATE_FOLDER_KIND)

    def test_a_shipped_set_lists_the_files_it_ships_with(self):
        self._select_set((discover_template_sets(
            self.project.root)[0]))
        rows = self._outliner_rows()
        self.assertIn("folder.html", rows)
        self.assertIn("documents", rows)

    def test_a_format_folder_is_listed_as_a_folder_of_its_own(self):
        # §1.3: `epub/` is part of the set, so it appears in the set's
        # own listing — nested where it sits — and its file is opened
        # like any other, never something the writer has to go to a file
        # manager for.
        created = create_template_set("Mine", self.project.root)
        (created.directory / "epub").mkdir()
        (created.directory / "epub" / "folder.html").write_text(
            "<p>${Content}</p>", encoding="utf-8")
        self.window._reload_tree()
        self._select_set((created))

        folder = self._outliner_item("epub")
        self.assertEqual(self._kind_of(folder), TEMPLATE_FOLDER_KIND)
        self.assertEqual([folder.child(index).text(0)
                          for index in range(folder.childCount())],
                         ["folder.html"])
        self.assertEqual(self._kind_of(folder.child(0)), TEMPLATE_FILE_KIND)

    # --- the Outliner click opens the file in the editor --------------------

    def test_clicking_a_file_opens_it_in_the_editor(self):
        created = create_template_set("Mine", self.project.root)
        (created.directory / "document.html").write_text(
            "<p>mine ${Content}</p>", encoding="utf-8")
        self.window._reload_tree()
        self._select_set((created))

        self.window._outliner_clicked(self._outliner_item("document.html"), 0)

        # The click did not move the binder (the set is still chosen) and
        # it did not open a document: it opened the file itself.
        self.assertEqual(self.window.current_template_set.name, "Mine")
        self.assertIsNone(self.window.current_id)
        self.assertEqual(self.window.current_template_file,
                         created.directory / "document.html")
        self.assertIn("mine", self.window.editor.toPlainText())
        self.assertIn("${Content}", self.window.editor.toPlainText())
        self.assertIs(self.window.views.currentWidget(),
                      self.window.editor_page)

    def test_a_txt_file_opens_as_plain_text(self):
        created = create_template_set("Mine", self.project.root)
        (created.directory / "notes.txt").write_text(
            "plain ${Title}", encoding="utf-8")
        self.window._reload_tree()
        self._select_set((created))

        self.window._outliner_clicked(self._outliner_item("notes.txt"), 0)

        self.assertEqual(self.window.current_template_file,
                         created.directory / "notes.txt")
        self.assertEqual(self.window.editor.toPlainText(), "plain ${Title}")

    def test_a_file_that_is_not_a_fragment_says_so(self):
        # Anything the compile never reads (a README, a stray config) is
        # not markup: saving it through an HTML editor would rewrite it
        # as a document, so it stays unopened with the reason given.
        created = create_template_set("Mine", self.project.root)
        (created.directory / "README.md").write_text("mine", encoding="utf-8")
        self.window._reload_tree()
        self._select_set((created))

        self.window._outliner_clicked(self._outliner_item("README.md"), 0)

        self.assertIsNone(self.window.current_template_file)
        self.assertIn("not an HTML fragment",
                      self.window.statusBar().currentMessage())

    def test_a_shipped_sets_file_offers_duplicate_instead(self):
        # A set that ships in code has no folder to write to.
        source = discover_template_sets(self.project.root)[0]
        self._select_set((source))

        self.window._outliner_clicked(self._outliner_item("folder.html"), 0)

        self.assertIsNone(self.window.current_template_file)
        self.assertIn("Duplicate",
                      self.window.statusBar().currentMessage())

    def test_clicking_a_folder_never_changes_the_selection(self):
        created = create_template_set("Mine", self.project.root)
        self.window._reload_tree()
        self._select_set((created))

        self.window._outliner_clicked(self._outliner_item("documents"), 0)
        self.assertEqual(self.window.current_template_set.name, "Mine")

    # --- the file is saved back to its own path -----------------------------

    def test_saving_writes_the_file_back_to_disk(self):
        created = create_template_set("Mine", self.project.root)
        path = created.directory / "document.html"
        path.write_text("<p>mine ${Content}</p>", encoding="utf-8")
        self.window._reload_tree()
        self._select_set((created))
        self.window._outliner_clicked(self._outliner_item("document.html"), 0)

        cursor = self.window.editor.textCursor()
        cursor.movePosition(QTextCursor.Start)
        cursor.insertText("rewritten ")
        self.assertTrue(self.window.save_current())

        self.assertIn("rewritten ", path.read_text(encoding="utf-8"))

    def test_picking_a_document_saves_the_open_template_file_first(self):
        created = create_template_set("Mine", self.project.root)
        path = created.directory / "document.html"
        path.write_text("<p>mine ${Content}</p>", encoding="utf-8")
        self.window._reload_tree()
        self._select_set((created))
        self.window._outliner_clicked(self._outliner_item("document.html"), 0)
        cursor = self.window.editor.textCursor()
        cursor.movePosition(QTextCursor.Start)
        cursor.insertText("kept ")

        self.window._select_document(self.draft.id)

        self.assertIn("kept ", path.read_text(encoding="utf-8"))
        self.assertIsNone(self.window.current_template_file)
        self.assertEqual(self.window.current_id, self.draft.id)

    def test_picking_another_file_saves_the_open_one_first(self):
        created = create_template_set("Mine", self.project.root)
        first = created.directory / "document.html"
        first.write_text("<p>one ${Content}</p>", encoding="utf-8")
        self.window._reload_tree()
        self._select_set((created))
        self.window._outliner_clicked(self._outliner_item("document.html"), 0)
        cursor = self.window.editor.textCursor()
        cursor.movePosition(QTextCursor.Start)
        cursor.insertText("kept ")

        self.window._outliner_clicked(
            self._outliner_item("folder.html"), 0)

        self.assertIn("kept ", first.read_text(encoding="utf-8"))
        self.assertEqual(self.window.current_template_file,
                         created.directory / "folder.html")

    # --- the sidebar panel (§1.2) ------------------------------------------

    def _open_fragment(self, relative="# head\nPage size: A4\n<p>${Content}</p>",
                       name="document.html"):
        created = create_template_set("Mine", self.project.root)
        (created.directory / name).write_text(relative, encoding="utf-8")
        self.window._reload_tree()
        self._select_set((created))
        self.window._outliner_clicked(self._outliner_item(name), 0)
        return created, created.directory / name

    def test_opening_a_template_file_swaps_the_metadata_for_the_panel(self):
        _created, _path = self._open_fragment()

        self.assertFalse(self.window.template_panel.isHidden())
        self.assertTrue(self.window.metadata_section.isHidden())
        self.assertTrue(self.window.template_check.isChecked())

    def test_the_config_box_holds_the_files_head_and_the_editor_its_body(self):
        _created, path = self._open_fragment()

        self.assertEqual(self.window.template_config_box.toPlainText(),
                         "# head\nPage size: A4")
        self.assertIn("${Content}", self.window.editor.toPlainText())
        self.assertNotIn("Page size", self.window.editor.toPlainText())
        # The file on disk is untouched by the reading of it.
        self.assertEqual(path.read_text(encoding="utf-8"),
                         "# head\nPage size: A4\n<p>${Content}</p>")

    def test_editing_the_config_box_saves_it_back_to_the_head(self):
        _created, path = self._open_fragment()
        self.window.template_config_box.setPlainText(
            "# head\nPage size: 5,7")

        self.assertTrue(self.window.save_current())

        on_disk = path.read_text(encoding="utf-8")
        # The head is the head of the file — first, and parseable.
        self.assertEqual(on_disk.split("\n")[0], "# head")
        self.assertIn("Page size: 5,7", on_disk)
        # The body is the editor's HTML of it (the compile strips the
        # furniture, §3.2) — the fragment is in there, still tagged.
        self.assertIn("${Content}", on_disk)
        self.assertIn("<p", on_disk)

    def test_the_naming_reminder_is_the_shared_text(self):
        self._open_fragment()

        shown = self.window.template_naming_note.text()
        self.assertIn("name this file according to your folder or "
                      "document TYPE", shown)
        self.assertEqual(shown, TEMPLATE_NAMING_NOTE)

    def test_the_config_note_is_the_shared_text(self):
        self._open_fragment()

        self.assertEqual(self.window.template_config_note.text(),
                         TEMPLATE_CONFIG_NOTE)
        self.assertIn("minimum", self.window.template_config_note.text())
        self.assertIn("original tags", self.window.template_config_note.text())
        self.assertIn("default\nfile.", self.window.template_config_note.text())

    def test_unticking_brings_the_metadata_back_and_clears_the_listing(self):
        _created, path = self._open_fragment()

        self.window.template_check.setChecked(False)

        self.assertFalse(self.window.metadata_section.isHidden())
        # The body of the panel goes with the tick — only the checkbox
        # stays up, so the file can be made a template again.
        self.assertTrue(self.window.template_config_box.isHidden())
        self.assertFalse(self.window.template_check.isHidden())
        self.assertNotIn("document.html", self._outliner_rows())
        on_disk = path.read_text(encoding="utf-8")
        self.assertIn("Template: no", on_disk)
        # The file's body is untouched: it is still the fragment the
        # editor holds, whatever the head now says.
        self.assertIn("${Content}", on_disk)

    def test_the_unticked_file_stays_a_file_of_the_set_on_disk(self):
        created, path = self._open_fragment()

        self.window.template_check.setChecked(False)

        self.assertTrue(path.is_file())
        # And it is not read as a fragment: the built-in shape renders.
        reloaded = TemplateSet.load("Mine", created.directory)
        self.assertNotIn("<p>${Content}</p>", reloaded.slot("document", html=True))

    def test_ticking_it_back_makes_it_a_template_again(self):
        _created, path = self._open_fragment()

        self.window.template_check.setChecked(False)
        self.window.template_check.setChecked(True)

        self.assertTrue(self.window.metadata_section.isHidden())
        self.assertFalse(self.window.template_config_box.isHidden())
        self.assertIn("document.html", self._outliner_rows())
        self.assertNotIn("Template: no", path.read_text(encoding="utf-8"))

    def test_the_css_box_writes_the_sets_style_file(self):
        created, _path = self._open_fragment()
        self.window.template_css_box.setPlainText("p { margin: 0 }")

        self.assertTrue(self.window.save_current())

        style = created.directory / "style.css"
        self.assertEqual(style.read_text(encoding="utf-8"), "p { margin: 0 }")

    def test_an_untouched_css_box_leaves_the_style_file_alone(self):
        created, _path = self._open_fragment()
        style = created.directory / "style.css"
        # A new set is born with the default stylesheet (§3.2), so the
        # box's job is to leave it be, not to mint one.
        before = style.read_text(encoding="utf-8")
        self.assertTrue(before.strip())

        self.assertTrue(self.window.save_current())

        self.assertEqual(style.read_text(encoding="utf-8"), before)

    def test_picking_a_document_brings_the_metadata_back(self):
        self._open_fragment()

        self.window._select_document(self.draft.id)

        self.assertTrue(self.window.template_panel.isHidden())
        self.assertFalse(self.window.metadata_section.isHidden())

    # --- the preset rows (§1.2) --------------------------------------------

    def test_the_trim_row_offers_the_amazon_sizes_and_custom(self):
        _created, _path = self._open_fragment()

        box = self.window.size_preset_box
        offered = [box.itemData(index) for index in range(box.count())]
        # The prompt, then the five Amazon trims in Amazon's own order,
        # then Custom — and every trim is a PAGE_TRIMS id, so a pick
        # writes the numbers Page setup and trim_id_for already know.
        self.assertEqual(offered[0], "")
        self.assertEqual(offered[1:-1], list(AMAZON_TRIMS))
        self.assertEqual(offered[-1], "custom")
        for trim_id in AMAZON_TRIMS:
            self.assertIn(trim_id, PAGE_TRIMS)
        self.assertTrue(box.toolTip().startswith("Writes `Page size"))

    def test_the_margin_row_offers_the_bands_and_custom(self):
        _created, _path = self._open_fragment()

        box = self.window.margin_preset_box
        offered = [box.itemData(index) for index in range(box.count())]
        self.assertEqual(offered[0], "")
        self.assertEqual(offered[1:-1], [value for _label, value
                                         in KDP_MARGIN_BANDS])
        self.assertEqual(offered[-1], "custom")
        # The row explains the whole line it will write.
        self.assertIn(str(KDP_MARGIN_MIN), box.toolTip())

    def test_picking_a_trim_replaces_the_size_line_and_it_saves(self):
        _created, path = self._open_fragment()      # head says Page size: A4
        box = self.window.size_preset_box

        box.setCurrentIndex(box.findData("6x9"))

        head = self.window.template_config_box.toPlainText()
        self.assertEqual(head.count("Page size"), 1)     # replaced, not added
        self.assertIn("Page size (inches - horizontal, vertical): 6,9", head)
        self.assertNotIn("A4", head)
        self.assertTrue(self.window.save_current())
        on_disk = path.read_text(encoding="utf-8")
        self.assertIn("Page size (inches - horizontal, vertical): 6,9",
                      on_disk)
        self.assertNotIn("A4", on_disk)

    def test_picking_a_band_writes_the_whole_kdp_margin_line(self):
        _created, path = self._open_fragment(
            "# head\nMargins (L,T,R,B/I,T,O,B): 0.3, 0.3, 0.3, 0.3\n"
            "<p>${Content}</p>")
        box = self.window.margin_preset_box
        expected = (f"Margins (L,T,R,B/I,T,O,B): 0.5, {KDP_MARGIN_MIN:g}, "
                    f"{KDP_MARGIN_MIN:g}, {KDP_MARGIN_MIN:g}")

        box.setCurrentIndex(box.findData(0.5))      # 151–300 pages

        head = self.window.template_config_box.toPlainText()
        self.assertEqual(head.count("Margins"), 1)
        self.assertIn(expected, head)
        self.assertNotIn("0.3", head)
        self.assertTrue(self.window.save_current())
        self.assertIn(expected, path.read_text(encoding="utf-8"))

    def test_the_custom_boxes_write_only_on_editing_finished(self):
        _created, _path = self._open_fragment("# head\n<p>${Content}</p>")
        box = self.window.size_preset_box

        box.setCurrentIndex(box.findData("custom"))
        # Choosing Custom reveals its boxes and writes nothing itself.
        self.assertFalse(self.window.size_custom_row.isHidden())
        self.assertNotIn("Page size",
                         self.window.template_config_box.toPlainText())

        self.window.size_width_spin.setValue(7.5)
        self.window.size_height_spin.setValue(9.25)
        self.window.size_width_spin.editingFinished.emit()

        self.assertIn(
            "Page size (inches - horizontal, vertical): 7.5,9.25",
            self.window.template_config_box.toPlainText())

    def test_the_custom_margin_boxes_write_the_whole_line(self):
        _created, _path = self._open_fragment("# head\n<p>${Content}</p>")
        box = self.window.margin_preset_box

        box.setCurrentIndex(box.findData("custom"))
        # Choosing Custom reveals its boxes and writes nothing itself.
        self.assertFalse(self.window.margin_custom_row.isHidden())
        self.assertNotIn("Margins",
                         self.window.template_config_box.toPlainText())

        self.window.margin_inside_spin.setValue(0.625)
        self.window.margin_top_spin.setValue(0.5)
        self.window.margin_outside_spin.setValue(0.4)
        self.window.margin_bottom_spin.setValue(0.5)
        self.window.margin_inside_spin.editingFinished.emit()

        self.assertIn("Margins (L,T,R,B/I,T,O,B): 0.625, 0.5, 0.4, 0.5",
                      self.window.template_config_box.toPlainText())

    def test_the_custom_margin_boxes_carry_what_the_head_says(self):
        original = ("# head\nMargins (L,T,R,B/I,T,O,B): 0.5, 0.3, 0.3, 0.3\n"
                    "<p>${Content}</p>")
        _created, path = self._open_fragment(original)

        box = self.window.margin_preset_box
        # Not a KDP line — a band selected over 0.3s would be a claim
        # the file does not make, so the row falls to Custom.
        self.assertEqual(box.currentData(), "custom")
        self.assertFalse(self.window.margin_custom_row.isHidden())
        self.assertEqual(self.window.margin_inside_spin.value(), 0.5)
        self.assertEqual(self.window.margin_top_spin.value(), 0.3)
        self.assertEqual(self.window.margin_outside_spin.value(), 0.3)
        self.assertEqual(self.window.margin_bottom_spin.value(), 0.3)
        # Reading wrote nothing.
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        # And an untouched focus-out writes nothing either: these are
        # the numbers the head already says, so nothing is rewritten.
        before = self.window.template_config_box.toPlainText()
        self.window.margin_top_spin.editingFinished.emit()
        self.assertEqual(self.window.template_config_box.toPlainText(),
                         before)

    def test_a_mirrored_pair_falls_to_custom_inside_and_outside(self):
        _created, _path = self._open_fragment(
            "# head\nMargins: 0.5, 0.25\n<p>${Content}</p>")

        box = self.window.margin_preset_box
        self.assertEqual(box.currentData(), "custom")
        self.assertEqual(self.window.margin_inside_spin.value(), 0.5)
        self.assertEqual(self.window.margin_outside_spin.value(), 0.25)
        # The two a mirrored pair does not name stay at KDP's minimum —
        # the spins' own defaults, so the focus-out guard holds.
        self.assertEqual(self.window.margin_top_spin.value(),
                         KDP_MARGIN_MIN)

    def test_the_selector_greys_while_the_custom_boxes_are_filled(self):
        _created, _path = self._open_fragment()
        application = QApplication.instance()

        application.focusChanged.emit(None, self.window.size_width_spin)
        self.assertFalse(self.window.size_preset_box.isEnabled())
        application.focusChanged.emit(None, self.window.margin_top_spin)
        self.assertFalse(self.window.margin_preset_box.isEnabled())
        # Focus leaving the boxes puts both selectors back — the line
        # has been written on the way out, by editingFinished.
        application.focusChanged.emit(self.window.size_width_spin, None)
        self.assertTrue(self.window.size_preset_box.isEnabled())
        self.assertTrue(self.window.margin_preset_box.isEnabled())

    def test_the_mirror_checkbox_writes_both_ways_and_renames_the_boxes(self):
        _created, path = self._open_fragment("# head\n<p>${Content}</p>")
        box = self.window.margin_mirror_box
        # Nothing said: unchecked, and the boxes name the plain edges —
        # the parser's first and third slots either way.
        self.assertFalse(box.isChecked())
        self.assertEqual(self.window.margin_inside_label.text(), "Left")
        self.assertEqual(self.window.margin_outside_label.text(), "Right")

        box.setChecked(True)

        self.assertEqual(self.window.margin_inside_label.text(),
                         "Inside (spine)")
        self.assertEqual(self.window.margin_outside_label.text(), "Outside")
        self.assertIn("Mirror margins: yes",
                      self.window.template_config_box.toPlainText())
        # The write goes through Save like every other head line.
        self.assertTrue(self.window.save_current())
        self.assertIn("Mirror margins: yes",
                      path.read_text(encoding="utf-8"))

        box.setChecked(False)                  # the other way, too
        self.assertIn("Mirror margins: no",
                      self.window.template_config_box.toPlainText())
        self.assertEqual(self.window.margin_inside_label.text(), "Left")

    def test_a_mirrored_head_selects_the_checkbox_on_open(self):
        original = ("# head\nMirror margins: yes\n"
                    "Margins (L,T,R,B/I,T,O,B): 0.5, 0.3, 0.3, 0.3\n"
                    "<p>${Content}</p>")
        _created, path = self._open_fragment(original)

        self.assertTrue(self.window.margin_mirror_box.isChecked())
        self.assertEqual(self.window.margin_inside_label.text(),
                         "Inside (spine)")
        # Reading wrote nothing.
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_the_rows_follow_the_head_they_open_with(self):
        original = ("# head\nPage size: 6,9\n"
                    "Margins (L,T,R,B/I,T,O,B): 0.5, 0.25, 0.25, 0.25\n"
                    "<p>${Content}</p>")
        _created, path = self._open_fragment(original)

        self.assertEqual(self.window.size_preset_box.currentData(), "6x9")
        self.assertEqual(self.window.margin_preset_box.currentData(), 0.5)
        self.assertTrue(self.window.size_custom_row.isHidden())
        # Reading is not choosing — opening wrote nothing back.
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_a_size_the_amazon_row_does_not_carry_falls_to_custom(self):
        _created, path = self._open_fragment(
            "# head\nPage size: 7,10\n<p>${Content}</p>")

        box = self.window.size_preset_box
        self.assertEqual(box.currentData(), "custom")
        self.assertFalse(self.window.size_custom_row.isHidden())
        self.assertEqual(self.window.size_width_spin.value(), 7.0)
        self.assertEqual(self.window.size_height_spin.value(), 10.0)
        self.assertEqual(path.read_text(encoding="utf-8"),
                         "# head\nPage size: 7,10\n<p>${Content}</p>")

    # --- listing the sets ---------------------------------------------------

    def test_listing_the_sets_lists_them(self):
        self.window.show_template_sets()

        self.assertTrue(self.window.current_template_group)
        self.assertIsNone(self.window.current_template_set)
        self.assertIn("default", self._outliner_rows())
        self.assertIn("short story", self._outliner_rows())

    def test_clicking_a_listed_set_opens_it(self):
        self.window.show_template_sets()
        self.window._outliner_clicked(self._outliner_item("default"), 0)
        self.assertEqual(self.window.current_template_set.name, "default")

    # --- back to prose ------------------------------------------------------

    def test_picking_a_document_leaves_the_template_state_behind(self):
        created = create_template_set("Mine", self.project.root)
        self.window._reload_tree()
        self._select_set((created))
        self.assertEqual(self.window.current_template_set.name, "Mine")

        self.window._select_document(self.draft.id)
        self.assertIsNone(self.window.current_template_set)
        self.assertFalse(self.window.current_template_group)
        self.assertEqual(self.window.current_id, self.draft.id)
        self.assertTrue(self.window.views.currentWidget()
                        in (self.window.editor_page, self.window.corkboard,
                            self.window.outliner))

    def test_a_document_can_be_picked_again_after_the_sets_were_listed(self):
        # The editor was emptied to list the sets, so the binder must not
        # still hold the document highlighted: the writer would click it
        # again and nothing would happen.
        self.window._select_document(self.draft.id)
        self.window.show_template_sets()
        self.assertIsNone(self.window.current_id)

        self.window._select_document(self.draft.id)

        self.assertEqual(self.window.current_id, self.draft.id)
        self.assertFalse(self.window.current_template_group)

    def test_a_folder_never_lands_on_a_template_view(self):
        # Selecting a template first, then a folder: the folder view, not
        # anything a template selection left behind.
        created = create_template_set("Mine", self.project.root)
        self.window._reload_tree()
        self._select_set((created))
        self.window._select_document(self.draft.id)
        self.assertIn(self.window.views.currentWidget(),
                      (self.window.corkboard, self.window.outliner))

    # --- duplication is flat ------------------------------------------------

    def test_a_duplicate_is_a_sibling_on_disk_and_in_the_listing(self):
        source = discover_template_sets(self.project.root)[0]
        created = copy_template_set(source, "default copy",
                                    self.project.root)

        self.assertEqual(created.directory.parent,
                         self.project.root / "templates")
        self.assertFalse((self.project.root / "templates" / "default").exists())

        self.window.show_template_sets()
        names = [item.text(0) for item in self._outliner_of_kind(
            TEMPLATE_SET_KIND)]
        self.assertIn("default copy", names)

    def test_a_duplicate_is_listed_again_without_reloading_by_hand(self):
        source = discover_template_sets(self.project.root)[0]
        self.window._duplicate_template(source, "Second copy")
        self.assertEqual(self.window.current_template_set.name,
                         "Second copy")
        self.window.show_template_sets()
        self.assertIn("Second copy", [item.text(0)
                                      for item in self._outliner_of_kind(
                                          TEMPLATE_SET_KIND)])

    # --- one file out, its own set ------------------------------------------

    def test_a_file_row_carries_the_path_it_will_be_copied_from(self):
        created = create_template_set("Mine", self.project.root)
        self.window._reload_tree()
        self._select_set((created))
        row = self._outliner_item("folder.html")
        self.assertEqual(row.data(0, TEMPLATE_PATH_ROLE), "folder.html")

    def test_one_file_becomes_a_set_of_its_own_beside_its_source(self):
        created = create_template_set("Mine", self.project.root)
        (created.directory / "folder.html").write_text(
            "<p>mine ${Content}</p>", encoding="utf-8")
        self.window._reload_tree()
        self._select_set((created))

        self.window._copy_template_file("folder.html", "Chapter only")

        # That one file and nothing else, and the writer lands in it.
        self.assertEqual(self.window.current_template_set.name,
                         "Chapter only")
        self.window.show_template_sets()
        names = [item.text(0) for item in self._outliner_of_kind(
            TEMPLATE_SET_KIND)]
        self.assertIn("Chapter only", names)
        # Flat: a sibling of the set it came from, never its child —
        # "It should appear in the Templates folder. Not Templates >
        # Default."
        copy_item = next(item for item in self._outliner_of_kind(
            TEMPLATE_SET_KIND) if item.text(0) == "Chapter only")
        self.assertIsNone(copy_item.parent())
        copy_dir = self.project.root / "templates" / "Chapter only"
        self.assertEqual(sorted(path.name for path in copy_dir.iterdir()),
                         ["folder.html"])
        self.assertIn("mine ${Content}",
                      (copy_dir / "folder.html").read_text(encoding="utf-8"))

    def test_one_file_can_be_copied_out_of_a_shipped_set(self):
        # A set with no folder still has files the listing shows; the
        # copy writes out exactly those bytes (§1.1).
        source = discover_template_sets(self.project.root)[0]
        self._select_set((source))
        self.window._copy_template_file("folder.html", "My chapter")

        copy_dir = self.project.root / "templates" / "My chapter"
        self.assertEqual(sorted(path.name for path in copy_dir.iterdir()),
                         ["folder.html"])
        self.assertEqual(
            (copy_dir / "folder.html").read_text(encoding="utf-8"),
            source.slot("folder", html=True))

    # --- template rows never reach an export --------------------------------

    def test_a_selected_template_is_not_an_export_scope(self):
        self.window.show_template_sets()
        self.window.tree.clearSelection()
        self.window.outliner.clearSelection()
        item = self._outliner_item("default")
        item.setSelected(True)
        self.assertIsNone(self.window._gather_export_selection())

    def test_template_files_never_carry_a_document_id(self):
        created = create_template_set("Mine", self.project.root)
        self.window._reload_tree()
        self._select_set((created))
        for index in range(self.window.outliner.topLevelItemCount()):
            self.assertIsNone(
                self.window.outliner.topLevelItem(index).data(0, Qt.UserRole))


if __name__ == "__main__":
    unittest.main()
