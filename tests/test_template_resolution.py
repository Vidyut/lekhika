"""Which template an export uses (docs/TEMPLATES.md §2, §2.1).

Three rules, each with its own test bed: explicit metadata always wins
(a folder naming a template dresses its own subtree), the chooser orders
applicability nearest first with the most applicable pre-selected, and
the pick is one-shot — never written to settings, so no later export can
be governed by a look the writer had forgotten about.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog

from lekhika.app import ExportOptionsDialog, MainWindow
from lekhika.project import Project
from lekhika.templates import TemplateSet, create_template_set

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-template-chooser-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class TemplateResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        # Hermetic: discovery includes the app library under
        # XDG_CONFIG_HOME, which must not leak into these expectations.
        self.xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(self.root / "config")
        self.project = Project.create(self.root / "Novel", "Novel")
        self._build_manuscript()

    def tearDown(self):
        if self.xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self.xdg
        self.project.close()
        self.temporary.cleanup()

    def _build_manuscript(self):
        """Draft
             Book / Chapter One / First Light
             Stray (a scene outside any book)
        """
        self.draft = self.project.documents()[0]
        self.book = self.project.add_document(
            "A Fictional Book", self.draft.id, "folder")
        self.chapter = self.project.add_document(
            "Chapter One", self.book.id, "chapter")
        self.scene = self.project.add_document(
            "First Light", self.chapter.id, "scene")
        self.project.write_content(self.chapter.id, "<p>Chapter opener.</p>")
        self.project.write_content(self.scene.id, "<p>First light prose.</p>")
        self.stray = self.project.add_document(
            "Stray", self.draft.id, "scene")
        self.project.write_content(self.stray.id, "<p>Stray prose.</p>")

    def _set(self, name: str, marker: str) -> TemplateSet:
        """A set whose chapter and scene fragments shout their own name,
        so an export says which template dressed it without any parsing.
        Both modes are written: these tests read the text export."""
        created = create_template_set(name, self.project.root)
        for suffix, template in ((".html", f"<p>{marker}</p>${{Content}}${{children}}"),
                                 (".txt", f"{marker}\n${{Content}}\n${{children}}")):
            (created.directory / ("folder" + suffix)).write_text(
                template, encoding="utf-8")
            (created.directory / ("document" + suffix)).write_text(
                template, encoding="utf-8")
        return TemplateSet.load(name, created.directory)

    def _export(self, selected_ids=None, templates=None) -> str:
        destination = self.root / "book.txt"
        self.project.export_text(destination, selected_ids,
                                 templates or TemplateSet.builtin())
        return destination.read_text(encoding="utf-8")

    # --- explicit metadata always wins (§2) ---------------------------------

    def test_an_assigned_folder_dresses_its_own_subtree(self):
        self._set("Editorial", "EDITORIAL")
        self.project.set_book_settings(self.book.id, {"template": "Editorial"})

        exported = self._export()

        self.assertIn("EDITORIAL\nChapter opener.", exported)
        self.assertIn("EDITORIAL\nFirst light prose.", exported)
        # The rest of the project keeps the export's own choice: an
        # assignment governs where it sits, never the whole compile.
        self.assertIn("Stray prose.", exported)
        self.assertNotIn("EDITORIAL\nStray", exported)

    def test_the_nearest_assignment_wins(self):
        self._set("Outer", "OUTER")
        self._set("Inner", "INNER")
        inner = self.project.add_document(
            "Imprint", self.book.id, "folder")
        chapter = self.project.add_document(
            "Chapter Two", inner.id, "chapter")
        self.project.write_content(chapter.id, "<p>Second opener.</p>")
        self.project.set_book_settings(self.book.id, {"template": "Outer"})
        self.project.set_book_settings(inner.id, {"template": "Inner"})

        exported = self._export()

        self.assertIn("OUTER\nChapter opener.", exported)
        self.assertIn("INNER\nSecond opener.", exported)

    def test_an_assignment_naming_nothing_falls_back_not_empty(self):
        self._set("Editorial", "EDITORIAL")
        self.project.set_book_settings(self.book.id, {"template": "Penguin"})

        exported = self._export()

        # Never an empty export: the name matches nothing, so the
        # export's own set dresses everything (§2's last rule).
        self.assertIn("Chapter opener.", exported)
        self.assertNotIn("EDITORIAL", exported)

    def test_the_resolved_set_is_not_poisoned_across_branches(self):
        """A deep assignment caches its own branch only: an ancestor it
        decided nothing about must still resolve for a sibling."""
        self._set("Inner", "INNER")
        inner = self.project.add_document("Imprint", self.book.id, "folder")
        chapter = self.project.add_document(
            "Chapter Two", inner.id, "chapter")
        self.project.write_content(chapter.id, "<p>Second opener.</p>")
        self.project.set_book_settings(inner.id, {"template": "Inner"})
        sibling = self.project.add_document(
            "Annex", self.book.id, "scene")
        self.project.write_content(sibling.id, "<p>Annex prose.</p>")

        # Resolve the deep one first, so its chain fills the cache.
        resolve = self.project._template_resolver(TemplateSet.builtin())
        self.assertEqual(resolve(chapter.id).name, "Inner")
        self.assertEqual(resolve(sibling.id).name, "default")
        self.assertEqual(resolve(self.chapter.id).name, "default")

    # --- applicability and the chooser's ordering (§2, §2.1) ----------------

    def test_nothing_assigned_preselects_the_default(self):
        self._set("Editorial", "EDITORIAL")
        self._set("Other", "OTHER")
        ordered, preselected, warnings = self.project.template_choices(None)

        # Nothing applies nearer than the project itself, so the list is
        # discovery's own order with the default on top and answering.
        self.assertEqual(preselected.name, "default")
        self.assertEqual([s.name for s in ordered],
                         ["default", "short story", "Book", "Guide",
                          "no-export", "Editorial", "Other"])
        self.assertEqual(warnings, [])

    def test_an_assignment_in_scope_comes_first_and_is_preselected(self):
        self._set("Editorial", "EDITORIAL")
        self._set("Other", "OTHER")
        self.project.set_book_settings(self.book.id, {"template": "Editorial"})

        ordered, preselected, _warnings = self.project.template_choices(
            {self.scene.id})

        self.assertEqual(preselected.name, "Editorial")
        self.assertEqual(ordered[0].name, "Editorial")
        # Everything discovery found is still offered below it: the
        # ordering is the explanation, not a filter.
        self.assertEqual({s.name for s in ordered},
                         {"default", "short story", "Book", "Guide",
                          "no-export", "Editorial", "Other"})

    def test_nearest_first_between_two_assignments(self):
        self._set("Outer", "OUTER")
        self._set("Inner", "INNER")
        inner = self.project.add_document("Imprint", self.book.id, "folder")
        chapter = self.project.add_document(
            "Chapter Two", inner.id, "chapter")
        self.project.write_content(chapter.id, "<p>Second opener.</p>")
        self.project.set_book_settings(self.book.id, {"template": "Outer"})
        self.project.set_book_settings(inner.id, {"template": "Inner"})

        ordered, preselected, _warnings = self.project.template_choices(
            {chapter.id})

        self.assertEqual(preselected.name, "Inner")
        self.assertEqual([s.name for s in ordered][:2], ["Inner", "Outer"])

    def test_a_missing_assignment_warns_and_keeps_the_default(self):
        self._set("Editorial", "EDITORIAL")
        self.project.set_book_settings(self.book.id, {"template": "Penguin"})

        ordered, preselected, warnings = self.project.template_choices(
            {self.scene.id})

        self.assertEqual(preselected.name, "default")
        self.assertIn("default", [s.name for s in ordered])
        self.assertEqual(len(warnings), 1)
        self.assertIn("Penguin", warnings[0])
        self.assertIn("has no match", warnings[0])

    def test_the_default_is_always_reachable(self):
        self._set("Editorial", "EDITORIAL")
        ordered, _preselected, _warnings = self.project.template_choices(
            {self.scene.id})
        self.assertIn("default", [s.name for s in ordered])

    def test_a_whole_project_export_still_sees_folder_assignments(self):
        self._set("Editorial", "EDITORIAL")
        self.project.set_book_settings(self.book.id, {"template": "Editorial"})

        _ordered, preselected, _warnings = self.project.template_choices(None)

        self.assertEqual(preselected.name, "Editorial")


class ExportChooserTests(unittest.TestCase):
    """The dialog end of §2.1: the row shows the answer, an override says
    it is one-shot and where the persistent place is, and a missing
    assignment is a line rather than a modal.

    Same contract as the other offscreen suites: QSettings redirected
    into a temp dir, windows never closed().
    """

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(self.root / "config")
        self.project = Project.create(self.root / "Novel", "Novel")
        self.draft = self.project.documents()[0]
        self.book = self.project.add_document(
            "A Fictional Book", self.draft.id, "folder")
        self.chapter = self.project.add_document(
            "Chapter One", self.book.id, "chapter")
        self.project.write_content(self.chapter.id, "<p>Chapter opener.</p>")
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

    def _dialog(self, scope=None) -> ExportOptionsDialog:
        sets, preselected, warnings = self.project.template_choices(scope)
        return ExportOptionsDialog("Scope", sets, preselected.name, warnings)

    def test_the_row_shows_the_answer_and_an_override_says_so(self):
        create_template_set("Editorial", self.project.root)
        self.project.set_book_settings(self.book.id, {"template": "Editorial"})

        dialog = self._dialog({self.chapter.id})
        try:
            # The answer, first and pre-selected — recomputed here, never
            # remembered from an earlier export.
            self.assertEqual(dialog.set_box.currentText(), "Editorial")
            names = [dialog.set_box.itemText(i)
                     for i in range(dialog.set_box.count())]
            self.assertEqual(names[0], "Editorial")
            self.assertIn("default", names)
            self.assertTrue(dialog.override_note.isHidden())

            dialog.set_box.setCurrentIndex(dialog.set_box.findText("default"))
            # The override says what it is, and names the place where it
            # would stick rather than merely refusing to persist.
            self.assertFalse(dialog.override_note.isHidden())
            self.assertIn("this export only", dialog.override_note.text())
            self.assertIn("Book template", dialog.override_note.text())
        finally:
            dialog.deleteLater()

    def test_a_missing_assignment_is_a_line_not_a_modal(self):
        self.project.set_book_settings(self.book.id, {"template": "Penguin"})

        dialog = self._dialog({self.chapter.id})
        try:
            self.assertFalse(dialog.warning_label.isHidden())
            self.assertIn("Penguin", dialog.warning_label.text())
            self.assertIn("has no match", dialog.warning_label.text())
            # The export still has an answer: the default, pre-selected.
            self.assertEqual(dialog.set_box.currentText(), "default")
        finally:
            dialog.deleteLater()

    def test_the_pick_is_one_shot(self):
        create_template_set("Editorial", self.project.root)
        chosen = self.project.resolve_template("Editorial")
        destination = Path(self.temporary.name) / "one.txt"
        with patch.object(ExportOptionsDialog, "exec",
                          return_value=QDialog.Accepted), \
                patch.object(ExportOptionsDialog, "selected_template_set",
                             return_value=chosen), \
                patch("lekhika.app.QFileDialog.getSaveFileName",
                      return_value=(str(destination), "")):
            self.window.export("text")

        # Nothing was written. The choice lived in that dialog and died
        # with it, so the next export answers from applicability again —
        # an override that lingered would govern an export the writer had
        # forgotten about (§2.1).
        self.assertEqual(self.project.get_setting("template_set"), "")
        _sets, preselected, _warnings = self.project.template_choices(None)
        self.assertEqual(preselected.name, "default")


if __name__ == "__main__":
    unittest.main()
