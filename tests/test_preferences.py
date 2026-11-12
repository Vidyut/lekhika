"""Preferences, category by category (docs/PREFERENCES.md; UI-NOTES 4).

The dialog is a category list beside stacked pages; the tests walk that
list, check every page is real, and drive the settings each category
actually persists — including the two the old wall of prose silently
ignored (autosave delay, reopen-on-start) and the AI prompt behind the
accordion.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QDialog, QLabel, QMessageBox, QPushButton)

from lekhika import ai  # noqa: E402
from lekhika.app import MainWindow, PreferencesDialog, load_paste_prefs  # noqa: E402
from lekhika.preferences import (  # noqa: E402
    CSS_EDIT, diagnostics, update_check_due)
from lekhika.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-prefs-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)

_PASTE_OFF = {"fonts": False, "paragraphs": False,
              "inline": False, "alignment": False}

_EXPECTED_CATEGORIES = [
    "General", "Editor", "Dictionary", "Appearance", "Files & saving",
    "Pasting", "Links", "Placeholders", "AI", "Read aloud",
    "Updates", "Help", "Support Lekhika",
]


class CategoryListTests(unittest.TestCase):
    def setUp(self):
        self.dialog = PreferencesDialog(11, dict(_PASTE_OFF))
        # Conditional categories only show while their box is ticked —
        # turn everything on, so the full list is on screen.
        self.dialog.spell_check_box.setChecked(True)
        self.dialog.ai_enabled_box.setChecked(True)
        self.dialog.tts_enabled_box.setChecked(True)

    def tearDown(self):
        self.dialog.deleteLater()

    def test_every_category_shows_a_real_page(self):
        names = [self.dialog.categories.item(i).text()
                 for i in range(self.dialog.categories.count())]
        self.assertEqual(names, _EXPECTED_CATEGORIES)
        for row in range(self.dialog.categories.count()):
            item = self.dialog.categories.item(row)
            page = self.dialog.pages.widget(item.data(Qt.UserRole))
            self.assertIsNotNone(page, item.text())
            self.assertIsNotNone(page.layout(), item.text())

    def test_clicking_a_category_switches_the_page(self):
        for row in range(self.dialog.categories.count()):
            item = self.dialog.categories.item(row)
            self.dialog.categories.setCurrentItem(item)
            self.assertEqual(self.dialog.pages.currentIndex(),
                             item.data(Qt.UserRole), item.text())

    def test_optional_categories_hide_with_their_boxes(self):
        dialog = PreferencesDialog(11, dict(_PASTE_OFF))
        try:
            def visible(name):
                item = next(
                    dialog.categories.item(row)
                    for row in range(dialog.categories.count())
                    if dialog.categories.item(row).text() == name)
                return not item.isHidden()

            self.assertFalse(visible("Dictionary"))
            self.assertFalse(visible("AI"))
            self.assertFalse(visible("Read aloud"))
            dialog.spell_check_box.setChecked(True)
            dialog.ai_enabled_box.setChecked(True)
            dialog.tts_enabled_box.setChecked(True)
            self.assertTrue(visible("Dictionary"))
            self.assertTrue(visible("AI"))
            self.assertTrue(visible("Read aloud"))
        finally:
            dialog.deleteLater()

    def test_the_filter_narrows_the_category_list(self):
        self.dialog.search_box.setText("AI")
        visible = [self.dialog.categories.item(i).text()
                   for i in range(self.dialog.categories.count())
                   if not self.dialog.categories.item(i).isHidden()]
        self.assertEqual(visible, ["AI"])
        self.dialog.search_box.setText("")
        self.assertFalse(any(
            self.dialog.categories.item(i).isHidden()
            for i in range(self.dialog.categories.count())))

    def test_the_font_slider_carries_a_live_sample(self):
        self.assertEqual(self.dialog.size_slider.minimum(), 8)
        self.assertEqual(self.dialog.size_slider.maximum(), 24)
        self.dialog.size_slider.setValue(18)
        self.assertEqual(self.dialog.size_label.text(), "18 pt")
        self.assertEqual(self.dialog.size_sample.font().pointSize(), 18)
        # The scope line moved into the text that actually grows, so the
        # reader sees the difference the slider makes and what it is
        # (and isn't) changing — and the old static note stays gone.
        self.assertIn("only your view of the software",
                      self.dialog.size_sample.text())
        labels = [label.text()
                  for label in self.dialog.findChildren(QLabel)]
        self.assertFalse(any("travels with a book" in text
                             for text in labels))

    def test_the_backup_folder_is_chosen_not_typed(self):
        # The path is long and easy to get wrong, and a typo would only
        # show up as a backup in the wrong place — or none at all.
        self.dialog.backup_folder_edit.setText("/tmp")
        buttons = [button for button
                   in self.dialog.findChildren(QPushButton)
                   if button.text() == "Choose..."]
        self.assertEqual(len(buttons), 1)
        with patch("lekhika.preferences.QFileDialog.getExistingDirectory",
                   return_value="/media/Backups") as picker:
            buttons[0].click()
        self.assertEqual(picker.call_args.args[2], "/tmp")  # where the field points
        self.assertEqual(self.dialog.backup_folder_edit.text(),
                         "/media/Backups")


class PreferencesApplyTests(unittest.TestCase):
    def setUp(self):
        QSettings().remove("session/lastProject")
        for key in ("editor/autosaveMs", "session/reopenLast",
                    "link_policy/default", "related/enabled", "ai/prompt",
                    "ui/fontSize", "ui/spellSquiggles"):
            QSettings().remove(key)
        self.window = MainWindow()

    def tearDown(self):
        self.window.project = None
        self.window.deleteLater()
        # The link default and the proofreading prompt are global; leave
        # them as found for later suites. The prompt one is load-bearing:
        # the fallback test's last act is setting ai/prompt, and without
        # this removal it shadows the shipped prompt for every file that
        # runs afterwards.
        QSettings().remove("link_policy/default")
        QSettings().remove("related/enabled")
        QSettings().remove("ai/prompt")

    def test_the_categories_persist_where_they_claim_to(self):
        QSettings().setValue("editor/autosaveMs", 1500)
        QSettings().setValue("session/reopenLast", True)
        QSettings().setValue("link_policy/default", "plain-text")
        QSettings().setValue("ai/prompt", "Old prompt.")
        created = []
        original_init = PreferencesDialog.__init__

        def spy(dialog, *args, **kwargs):
            created.append(dialog)
            original_init(dialog, *args, **kwargs)

        def accept_after_edits():
            # The dialog starts from what is stored; the writer then
            # edits the widgets. The apply path must persist what it
            # finds at accept, not what construction began with — which
            # is also what makes a dropped write observable here.
            dialog = created[0]
            dialog.autosave_spin.setValue(2400)
            dialog.reopen_last_box.setChecked(False)
            dialog.link_default_box.setCurrentIndex(
                dialog.link_default_box.findData("keep-as-note"))
            dialog.related_box.setChecked(False)
            dialog.ai_prompt_edit.setPlainText("New contract.")
            return QDialog.Accepted

        with patch.object(PreferencesDialog, "__init__", spy), \
                patch.object(PreferencesDialog, "exec",
                             side_effect=accept_after_edits):
            self.window.preferences()

        self.assertEqual(int(QSettings().value("editor/autosaveMs")), 2400)
        self.assertFalse(
            bool(QSettings().value("session/reopenLast", True)))
        self.assertEqual(QSettings().value("link_policy/default"),
                         "keep-as-note")
        # Absent key reads as the shipped default (on), so a dropped
        # write cannot masquerade as an off switch.
        self.assertFalse(bool(QSettings().value("related/enabled", True)))
        self.assertEqual(QSettings().value("ai/prompt"), "New contract.")
        # The autosave delay is live, not next-open: the running timer
        # picks the new interval up in the same call.
        self.assertEqual(self.window.save_timer.interval(), 2400)

    def test_the_shipped_proofreading_prompt_is_a_fallback(self):
        QSettings().remove("ai/prompt")
        self.assertEqual(ai.proofread_system(), ai.PROOFREAD_SYSTEM)
        QSettings().setValue("ai/prompt", "  Custom contract.  ")
        self.assertEqual(ai.proofread_system(), "Custom contract.")

    def test_the_dialog_edits_the_prompt_and_keeps_the_warning(self):
        dialog = PreferencesDialog(11, dict(_PASTE_OFF))
        try:
            self.assertTrue(dialog.ai_prompt_edit.isHidden())
            self.assertTrue(dialog.prompt_warning.isHidden())
            dialog.prompt_toggle.setChecked(True)
            self.assertFalse(dialog.ai_prompt_edit.isHidden())
            self.assertFalse(dialog.prompt_warning.isHidden())
            self.assertIn("Do not edit unless you know what you are doing",
                          dialog.prompt_warning.text())
        finally:
            dialog.deleteLater()


class EditorStylesTests(unittest.TestCase):
    def test_the_box_offers_every_style_first_with_default(self):
        dialog = PreferencesDialog(
            11, dict(_PASTE_OFF),
            styles={
                "Default": {"family": "serif", "size": 12.0, "weight": 50,
                            "italic": False, "top_margin": 0.0,
                            "right_margin": 0.0, "bottom_margin": 0.0,
                            "left_margin": 0.0, "indent": 0,
                            "text_indent": 0.0},
                "Heading 1": {"size": 22.0, "weight": 75, "italic": False,
                              "top_margin": 0.0, "right_margin": 0.0,
                              "bottom_margin": 0.0, "left_margin": 0.0,
                              "indent": 0, "text_indent": 0.0},
                "Heading 2": {"size": 18.0, "weight": 75, "italic": False,
                              "top_margin": 0.0, "right_margin": 0.0,
                              "bottom_margin": 0.0, "left_margin": 0.0,
                              "indent": 0, "text_indent": 0.0},
            },
            style_names=lambda: ["Default", "Heading 1", "Heading 2"])
        try:
            self.assertEqual(dialog.style_box.currentData(), "Default")
            self.assertTrue(any(
                dialog.style_box.itemData(index) == CSS_EDIT
                for index in range(dialog.style_box.count())))
        finally:
            dialog.deleteLater()

    def test_the_delete_box_lists_saved_styles_only(self):
        names = ["Default", "Mine", "Quote copy"]
        deleted = []

        def delete(name, revert):
            deleted.append((name, revert))
            names.remove(name)
            return True

        dialog = PreferencesDialog(
            11, dict(_PASTE_OFF), styles={"Default": {}, "Mine": {}, "Quote copy": {}},
            style_names=lambda: list(names),
            delete_style=delete,
            style_usage=lambda name: (1, 3))
        try:
            box = dialog.style_box
            box.setCurrentIndex(box.findData("Mine"))
            with patch("lekhika.preferences.QDialog.exec",
                       return_value=QDialog.DialogCode.Accepted):
                dialog._delete_style()
            self.assertEqual(deleted, [("Mine", True)])
            box_vals = [box.itemData(index) for index in range(box.count())]
            self.assertNotIn("Mine", box_vals)
        finally:
            dialog.deleteLater()

    def test_deleting_with_reversion_returns_worn_paragraphs_to_default(self):
        documents = []
        names = ["Default", "Mine"]
        dialog = PreferencesDialog(
            11, dict(_PASTE_OFF),
            styles={"Default": {}, "Mine": {}},
            style_names=lambda: list(names),
            delete_style=lambda name, revert: documents.append(
                (name, revert)) or True,
            style_usage=lambda name: (0, 0))
        try:
            dialog.style_box.setCurrentIndex(
                dialog.style_box.findData("Mine"))
            with patch("lekhika.preferences.QDialog.exec",
                       return_value=QDialog.DialogCode.Accepted):
                dialog._delete_style()
            self.assertEqual(documents, [("Mine", False)])
        finally:
            dialog.deleteLater()


class OptionalMenuTests(unittest.TestCase):
    def setUp(self):
        self.window = MainWindow()
        for key in ("ai/enabled", "tts/enabled"):
            QSettings().remove(key)

    def tearDown(self):
        for key in ("ai/enabled", "tts/enabled"):
            QSettings().remove(key)
        self.window.deleteLater()

    def test_optional_menus_are_gone_by_default(self):
        self.window._refresh_optional_menus()
        self.assertFalse(self.window.ai_menu.isVisible())
        self.assertFalse(self.window.speak_menu.isVisible())

    def test_ticking_the_box_brings_the_menu(self):
        QSettings().setValue("ai/enabled", True)
        QSettings().setValue("tts/enabled", True)
        self.window._refresh_optional_menus()
        self.assertTrue(self.window.ai_menu.isVisible())
        self.assertTrue(self.window.speak_menu.isVisible())


class ReopenGateTests(unittest.TestCase):
    def setUp(self):
        QSettings().remove("session/lastProject")
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(
            Path(self.temporary.name) / "Novel", "Novel")
        QSettings().setValue("session/lastProject", str(self.project.root))
        self.window = MainWindow()

    def tearDown(self):
        self.window.project = None
        self.window.deleteLater()
        QSettings().remove("session/lastProject")
        QSettings().remove("session/reopenLast")
        self.project.close()
        self.temporary.cleanup()

    def test_reopening_honours_the_switch(self):
        QSettings().setValue("session/reopenLast", False)
        self.window.reopen_last_project()
        self.assertIsNone(self.window.project)

        QSettings().setValue("session/reopenLast", True)
        self.window.reopen_last_project()
        self.assertIsNotNone(self.window.project)
        self.assertTrue(Path(self.window.project.root).name.startswith("Novel"))


class PlaceholderPageTests(unittest.TestCase):
    """Placeholders: the project's own ${names}, added where they belong.

    The same fields live in the inspector's metadata table, so the page
    reads the project and writes it — nothing is remembered between the
    two views (docs/TEMPLATES.md §3.2).
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(
            Path(self.temporary.name) / "Novel", "Novel")
        self.document = self.project.documents()[0]
        self.opened = []
        self.dialog = PreferencesDialog(
            11, dict(_PASTE_OFF), project=self.project,
            show_placeholders=lambda: self.opened.append(True))

    def tearDown(self):
        self.dialog.deleteLater()
        self.project.close()
        self.temporary.cleanup()

    def test_the_defaults_are_listed(self):
        names = [self.dialog.builtin_placeholders.item(row).text()
                 for row in range(self.dialog.builtin_placeholders.count())]
        self.assertIn("${Title}", names)
        self.assertIn("${PageNo}", names)
        self.assertIn("${Author}", names)

    def test_a_name_added_here_answers_as_a_placeholder(self):
        self.dialog.placeholder_name_edit.setText("Series")
        self.dialog.placeholder_value_edit.setText("Abrahamha's Strays")
        self.dialog.placeholder_add_button.click()
        self.assertEqual(
            self.project.project_metadata()["Series"][0],
            "Abrahamha's Strays")
        # The point of the page: an export of any document fills it.
        self.assertEqual(
            self.project.render_template(self.document, "${Series}"),
            "Abrahamha's Strays")
        shown = [self.dialog.project_placeholders.item(row).text()
                 for row in range(self.dialog.project_placeholders.count())]
        self.assertIn("${Series} — Abrahamha's Strays", shown)

    def test_a_typed_marker_is_stripped_to_a_writable_name(self):
        self.dialog.placeholder_name_edit.setText("${Series}")
        self.dialog.placeholder_add_button.click()
        self.assertIn("Series", self.project.project_metadata())

    def test_removing_takes_the_name_out_again(self):
        self.dialog.placeholder_name_edit.setText("PoV")
        self.dialog.placeholder_value_edit.setText("Aaxyl")
        self.dialog.placeholder_add_button.click()
        self.dialog.project_placeholders.setCurrentRow(0)
        self.dialog.placeholder_remove_button.click()
        self.assertNotIn("PoV", self.project.project_metadata())
        self.assertEqual(self.dialog.project_placeholders.count(), 0)

    def test_an_existing_key_keeps_its_value_type(self):
        # The inspector can give a key a type and a list of options; a
        # value edited here must not quietly flatten it to plain text.
        self.project.set_project_metadata(
            {"PoV": ("Aameh", "choice", ["Aaxyl", "Aameh"])})
        self.dialog.placeholder_name_edit.setText("PoV")
        self.dialog.placeholder_value_edit.setText("Aaxyl")
        self.dialog.placeholder_add_button.click()
        value, value_type, options = self.project.project_metadata()["PoV"]
        self.assertEqual((value, value_type), ("Aaxyl", "choice"))
        self.assertEqual(options, ["Aaxyl", "Aameh"])

    def test_the_reference_button_opens_it_read_only(self):
        self.dialog.placeholder_reference_button.click()
        self.assertEqual(self.opened, [True])

    def test_the_page_is_inert_without_a_project(self):
        dialog = PreferencesDialog(11, dict(_PASTE_OFF))
        try:
            self.assertFalse(dialog.placeholder_add_button.isEnabled())
            self.assertFalse(dialog.placeholder_remove_button.isEnabled())
            self.assertFalse(dialog.placeholder_reference_button.isEnabled())
            self.assertFalse(dialog.placeholder_note.isHidden())
        finally:
            dialog.deleteLater()

    def test_the_read_only_reference_offers_no_insertion(self):
        window = MainWindow()
        try:
            window.project = self.project
            page = window._placeholder_reference_page(insert=False)
            self.assertNotIn("insert:", page)
            self.assertIn("${Title}", page)
            self.assertIn("Preferences → Placeholders", page)
            # The editor's copy still hands names back for insertion.
            self.assertIn("insert:Title",
                          window._placeholder_reference_page(insert=True))
        finally:
            window.project = None
            window.deleteLater()


class HelpPageTests(unittest.TestCase):
    """Help: what a bug report needs, and where the pointers go."""

    def setUp(self):
        self.opened = []
        self.dialog = PreferencesDialog(
            11, dict(_PASTE_OFF),
            open_guide=lambda: self.opened.append(True))

    def tearDown(self):
        self.dialog.deleteLater()

    def test_the_version_is_named_on_the_page(self):
        from lekhika import __version__
        labels = [label.text() for label in self.dialog.findChildren(QLabel)]
        self.assertIn(f"Lekhika {__version__}", labels)

    def test_the_guide_button_opens_the_guide(self):
        self.dialog.open_manual_button.click()
        self.assertEqual(self.opened, [True])

    def test_without_a_window_the_guide_button_is_disabled(self):
        dialog = PreferencesDialog(11, dict(_PASTE_OFF))
        try:
            self.assertFalse(dialog.open_manual_button.isEnabled())
        finally:
            dialog.deleteLater()

    def _page(self, name):
        for row in range(self.dialog.categories.count()):
            item = self.dialog.categories.item(row)
            if item.text() == name:
                return self.dialog.pages.widget(item.data(Qt.UserRole))
        raise AssertionError(f"no {name} page")

    def test_the_support_page_is_last_and_its_links_go_where_they_say(self):
        self.assertEqual(
            self.dialog.categories.item(
                self.dialog.categories.count() - 1).text(),
            "Support Lekhika")
        page = self._page("Support Lekhika")
        with patch("lekhika.preferences.QDesktopServices.openUrl") as opened:
            for button in page.findChildren(QPushButton):
                if button.text():
                    button.click()
        urls = [call.args[0].toString() for call in opened.call_args_list]
        self.assertIn("https://paypal.me/VidyutG", urls)          # a gift
        self.assertIn("https://vidyut.net/category/books/", urls)  # the books
        self.assertIn("https://vidyut.net/newsletter-signup/", urls)
        self.assertIn("https://github.com/Vidyut/lekhika/issues/new", urls)

    def test_copying_the_details_puts_them_on_the_clipboard(self):
        self.dialog._copy_diagnostics()
        self.assertEqual(QGuiApplication.clipboard().text(),
                         diagnostics(self.dialog.project))
        self.assertEqual(self.dialog.copy_details_button.text(),
                         "Copied to the clipboard")

    def test_the_block_carries_the_shape_of_the_book_not_the_book(self):
        # A bug report needs the version and the machine, never the
        # manuscript or the folder the writer's name is in.
        temporary = tempfile.TemporaryDirectory()
        project = Project.create(Path(temporary.name) / "Novel", "Novel")
        project.write_content(project.documents()[0].id,
                              "<p>Secret prose of the manuscript.</p>")
        try:
            from lekhika import __version__
            text = diagnostics(project)
            self.assertIn(f"Lekhika {__version__}", text)
            self.assertIn("documents", text)
            self.assertNotIn("Secret prose", text)
            self.assertNotIn(str(project.root), text)
            self.assertNotIn(str(Path.home()), text)
        finally:
            project.close()
            temporary.cleanup()


class UpdateModeTests(unittest.TestCase):
    """Four modes, and consent is the default (docs/TEMPLATES.md §8)."""

    def setUp(self):
        QSettings().remove("update/mode")
        self.dialog = PreferencesDialog(11, dict(_PASTE_OFF))

    def tearDown(self):
        self.dialog.deleteLater()
        QSettings().remove("update/mode")

    def test_the_modes_are_auto_check_manual_nothing(self):
        box = self.dialog.update_mode_box
        self.assertEqual([box.itemText(row) for row in range(box.count())],
                         ["Auto", "Check", "Manual", "Nothing"])

    def test_the_default_is_the_button_because_that_is_consent(self):
        self.assertEqual(self.dialog.update_mode_box.currentData(), "manual")

    def test_the_page_recommends_regular_checks_at_least(self):
        texts = [label.text()
                 for label in self.dialog.pages.findChildren(QLabel)]
        self.assertTrue(any("recommend" in text and "Check" in text
                            for text in texts), texts)


class UpdateScheduleTests(unittest.TestCase):
    """Auto and Check look once a day; Manual and Nothing never do."""

    def tearDown(self):
        QSettings().remove("update/mode")
        QSettings().remove("update/lastCheck")

    def test_nothing_is_asked_without_consent(self):
        QSettings().remove("update/lastCheck")
        for mode in ("manual", "never"):
            QSettings().setValue("update/mode", mode)
            self.assertFalse(update_check_due(), mode)

    def test_auto_and_check_look_once_a_day(self):
        for mode in ("auto", "check"):
            QSettings().setValue("update/mode", mode)
            QSettings().remove("update/lastCheck")
            self.assertTrue(update_check_due(), mode)
            QSettings().setValue(
                "update/lastCheck", datetime.now().isoformat())
            self.assertFalse(update_check_due(), mode)
            QSettings().setValue(
                "update/lastCheck",
                (datetime.now() - timedelta(days=1, minutes=1)).isoformat())
            self.assertTrue(update_check_due(), mode)

    def test_the_older_name_for_manual_still_reads(self):
        QSettings().setValue("update/mode", "ask")
        self.assertFalse(update_check_due())

    def test_a_pre_release_is_not_newer_than_its_version(self):
        from lekhika.preferences import numeric_version
        self.assertEqual(numeric_version("v1.0.0"), numeric_version("1.0.0"))
        self.assertFalse(numeric_version("1.1.0-rc1") > numeric_version("1.1.0"))

    def test_the_version_the_check_compares_is_the_packaged_one(self):
        from lekhika import __version__
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        self.assertIn(f'version = "{__version__}"', pyproject.read_text())


class StartupUpdateTests(unittest.TestCase):
    """Auto installs, Check only tells, and Manual does not even look."""

    FOUND = {"current": "1.0.0", "latest": "1.0.1", "page": ""}

    def setUp(self):
        QSettings().remove("session/lastProject")
        QSettings().remove("update/mode")
        QSettings().remove("update/lastCheck")
        self.window = MainWindow()

    def tearDown(self):
        self.window.project = None
        self.window.deleteLater()
        QSettings().remove("update/mode")
        QSettings().remove("update/lastCheck")

    def _start(self, mode, installed=False):
        QSettings().setValue("update/mode", mode)
        QSettings().remove("update/lastCheck")

        class Immediate:
            """Run the worker where it starts, so no event loop is needed."""

            def __init__(self, target, **_):
                self.target = target

            def start(self):
                self.target()

        with patch("lekhika.app.threading.Thread", Immediate), \
                patch("lekhika.app.newer_release",
                      return_value=self.FOUND) as probe, \
                patch("lekhika.app.apply_update",
                      return_value=installed) as install:
            self.window.start_update_check()
        return self.window.statusBar().currentMessage(), probe, install

    def test_manual_does_not_look_at_the_network(self):
        message, probe, install = self._start("manual")
        self.assertEqual(message, "")
        self.assertFalse(probe.called)
        self.assertFalse(install.called)

    def test_check_tells_without_installing(self):
        message, _, install = self._start("check")
        self.assertIn("1.0.1 is available", message)
        self.assertFalse(install.called)

    def test_auto_installs_and_says_what_to_do(self):
        message, _, install = self._start("auto", installed=True)
        self.assertIn("was installed", message)
        self.assertIn("restart", message)
        install.assert_called_once_with("1.0.0", "1.0.1")

    def test_auto_that_cannot_install_only_tells(self):
        message, _, install = self._start("auto", installed=False)
        self.assertIn("1.0.1 is available", message)
        install.assert_called_once()


if __name__ == "__main__":
    unittest.main()
