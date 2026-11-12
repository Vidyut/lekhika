"""BUILD-NOW 4: Print (Ctrl+P) — the export's PDF path, aimed at a printer.

Acceptance from docs/BUILD-NOW.md: printing opens the system print flow
with the same pagination the export produces (the stored template set and
export scope, no dialogs), and the temp file does not survive — the folder
lives exactly as long as the desktop handler needs it.
"""

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lekhika import app as app_module  # noqa: E402
from lekhika.app import MainWindow, print_via_desktop  # noqa: E402
from lekhika.project import Project  # noqa: E402
from lekhika.templates import create_template_set  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-test-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class PrintTests(unittest.TestCase):
    def setUp(self):
        QSettings().remove("session/lastProject")
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")
        self.draft = self.project.documents()[0]
        self.scene = self.project.add_document(
            "Scene One", parent_id=self.draft.id, kind="scene"
        )
        self.project.write_content(self.scene.id, "<p>Words here.</p>")
        self.window = MainWindow()
        self.window._switch_project(self.project)
        # A controlled home for the print folder, so cleanup is observable.
        self.print_area = Path(self.temporary.name) / "print-area"

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _print_with(self, exporter):
        """Run print_manuscript with export and the handler replaced."""
        handler = MagicMock()
        with patch.object(app_module.pdf, "export_pdf", exporter), \
                patch.object(app_module, "print_via_desktop", handler), \
                patch.object(app_module, "QMessageBox") as boxes, \
                patch.object(app_module.tempfile, "mkdtemp",
                             lambda **_kw: str(self.print_area)):
            # mkdtemp is asked to create; do it for real so rmtree has
            # something to remove.
            self.print_area.mkdir(exist_ok=True)
            self.window.print_manuscript()
        return handler, boxes

    def test_print_dresses_with_the_most_applicable_template(self):
        captured = {}

        def fake_export(project, destination, raw_ids, templates):
            captured["project"] = project
            captured["destination"] = Path(destination)
            captured["raw_ids"] = raw_ids
            captured["templates"] = templates
            Path(destination).write_bytes(b"%PDF-fake")

        # A second set, so "the default" is a real choice and not the
        # only entry in the list by coincidence.
        create_template_set("fancy", self.project.root)
        handler, boxes = self._print_with(fake_export)

        self.assertIs(captured["project"], self.project)
        self.assertEqual(captured["raw_ids"],
                         self.window._gather_export_selection())
        # Nothing is assigned anywhere, so print answers with the default
        # (§2) — and never with a set some earlier export happened to
        # leave behind: that stored pick is retired (§2.1).
        self.assertEqual(captured["templates"].name, "default")
        self.assertEqual(self.project.get_setting("template_set"), "")
        self.assertTrue(captured["destination"].exists())
        # The handler saw the same file export_pdf produced.
        handler.assert_called_once_with(captured["destination"])
        boxes.critical.assert_not_called()
        self.assertIn("print flow",
                      self.window.statusBar().currentMessage())

    def test_an_assigned_folder_dresses_its_own_print(self):
        captured = {}

        def fake_export(project, destination, raw_ids, templates):
            captured["templates"] = templates
            Path(destination).write_bytes(b"%PDF-fake")

        # The persistent mechanism (§2.1): a template assigned to a
        # folder is the answer for that folder's documents, so print
        # takes it without being asked — in place of the stored pick the
        # export dialog used to leave lying around.
        create_template_set("fancy", self.project.root)
        self.project.set_book_settings(self.draft.id, {"template": "fancy"})
        self._print_with(fake_export)

        self.assertEqual(captured["templates"].name, "fancy")

    def test_a_failed_conversion_reports_and_leaves_no_folder(self):
        def explode(*_args):
            raise RuntimeError("no LibreOffice here")

        handler, boxes = self._print_with(explode)

        handler.assert_not_called()
        boxes.critical.assert_called_once()
        self.assertFalse(self.print_area.exists())

    def test_no_project_never_starts_the_flow(self):
        exporter = MagicMock()
        self.window.project = None

        with patch.object(app_module.pdf, "export_pdf", exporter), \
                patch.object(app_module, "print_via_desktop") as handler:
            self.window.print_manuscript()

        exporter.assert_not_called()
        handler.assert_not_called()
        self.assertIn("Open a project first",
                      self.window.statusBar().currentMessage())

    def test_a_cancelled_save_cancels_print(self):
        exporter = MagicMock()
        self.window.save_current = lambda: False

        with patch.object(app_module.pdf, "export_pdf", exporter):
            self.window.print_manuscript()

        exporter.assert_not_called()

    def test_print_action_is_disabled_until_a_project_is_open(self):
        fresh = MainWindow()
        self.addCleanup(lambda: setattr(fresh, "project", None))
        self.assertFalse(fresh.print_action.isEnabled())

    def test_the_folder_outlives_the_handler_and_then_is_gone(self):
        self.print_area.mkdir()
        pdf_path = self.print_area / "lekhika-print.pdf"
        pdf_path.write_bytes(b"%PDF-fake")
        shell = shutil.which("sh")

        process = print_via_desktop(
            pdf_path, command=[shell, "-c", "sleep 0.4"])

        # While the handler runs, the file must still be readable.
        self.assertTrue(pdf_path.exists())
        process.wait(timeout=10)
        deadline = time.time() + 5
        while self.print_area.exists() and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(self.print_area.exists())

    def test_a_handler_that_never_starts_cleans_up(self):
        self.print_area.mkdir()
        pdf_path = self.print_area / "lekhika-print.pdf"
        pdf_path.write_bytes(b"%PDF-fake")

        with patch.object(app_module.subprocess, "Popen",
                          side_effect=OSError("no handler")):
            with self.assertRaises(OSError):
                print_via_desktop(pdf_path)

        self.assertFalse(self.print_area.exists())


if __name__ == "__main__":
    unittest.main()
