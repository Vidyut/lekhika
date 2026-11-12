"""1.6's PDF route: a thin wrapper over the DOCX, driven through UNO.

The probes and the orchestration are stubbed (a shell script answering
--version, a fake interpreter, a fake _run_uno) so the suite never
needs LibreOffice; the real conversion is a manual smoke — it depends
on system packages (libreoffice-writer + python3-uno) the suite must
not require. The last test checks the window dispatches Export PDF…
through this module.
"""

import os
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from lekhika import pdf  # noqa: E402
from lekhika.app import ExportOptionsDialog, MainWindow  # noqa: E402
from lekhika.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-pdf-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)

_VERSION_STUB = """#!/bin/bash
if [ "$1" = "--version" ]; then
    echo "LibreOffice 99.9.9"
    exit 0
fi
exit 1
"""

_BROKEN_STUB = """#!/bin/bash
if [ "$1" = "--version" ]; then
    echo "snap-confine is packaged without necessary permissions" >&2
    exit 1
fi
exit 1
"""


def _make_stub(directory: Path, name: str, script: str) -> str:
    path = directory / name
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


class FakeProject:
    """export_docx is the only method export_pdf touches."""

    def __init__(self):
        self.calls = []

    def export_docx(self, destination, selected_ids, templates, fmt="docx"):
        self.calls.append((destination, selected_ids, templates, fmt))
        destination.write_bytes(b"docx placeholder")


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_find_converter_returns_the_working_candidate(self):
        _make_stub(self.root, "libreoffice", _VERSION_STUB)

        with patch.dict(os.environ, {"PATH": str(self.root)}):
            command = pdf.find_converter()

        self.assertIsNotNone(command)
        self.assertTrue(command[0].endswith("libreoffice"))

    def test_find_converter_is_none_without_a_converter(self):
        # PATH points nowhere in particular (not even /usr/bin): on a
        # machine that does have LibreOffice this must still be None.
        with patch.dict(os.environ, {"PATH": str(self.root)}):
            command = pdf.find_converter()

        self.assertIsNone(command)

    def test_broken_converter_is_probed_and_explained(self):
        _make_stub(self.root, "libreoffice", _BROKEN_STUB)

        # Stub alone on PATH: no healthy candidate can rescue the probe.
        with patch.dict(os.environ, {"PATH": str(self.root)}):
            found = pdf.find_converter()
            message = pdf._converter_missing_message()

        self.assertIsNone(found)
        self.assertIn("sudo apt install libreoffice-writer", message)
        self.assertIn("snap-confine", message)   # the real probe detail

    def test_uno_interpreter_probe_picks_the_working_one(self):
        bad = _make_stub(self.root, "bad-python", "#!/bin/bash\nexit 1\n")
        good = _make_stub(self.root, "good-python", "#!/bin/bash\nexit 0\n")

        with patch.object(pdf, "UNO_CANDIDATES", (bad, good)):
            found = pdf._find_uno_interpreter()

        self.assertEqual(found, good)

    def test_uno_interpreter_none_explains_the_missing_bindings(self):
        bad = _make_stub(self.root, "bad-python", "#!/bin/bash\nexit 1\n")

        with patch.object(pdf, "UNO_CANDIDATES", (bad, "definitely-not-python")):
            found = pdf._find_uno_interpreter()
            message = pdf._uno_missing_message()

        self.assertIsNone(found)
        self.assertIn("python3-uno", message)


class ConvertTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        _make_stub(self.root, "libreoffice", _VERSION_STUB)
        # The UNO probes are environment facts (system packages); the
        # suite replaces them so a machine without python3-uno passes.
        self.env = patch.dict(os.environ, {"PATH": str(self.root)})
        self.interpreter = patch.object(
            pdf, "_find_uno_interpreter", return_value="/fake/python3")
        self.env.start()
        self.interpreter.start()

    def tearDown(self):
        self.interpreter.stop()
        self.env.stop()
        self.temporary.cleanup()

    @staticmethod
    def _fake_uno(wrote=None, returncode=0, stderr="", boom=None):
        def run(interpreter, docx, pdf_path, soffice, workdir):
            if boom is not None:
                raise boom
            if wrote is not None:
                wrote.append((interpreter, docx, pdf_path, soffice, workdir))
            if returncode == 0:
                pdf_path.write_bytes(b"%PDF-1.4 uno")
            return subprocess.CompletedProcess([], returncode,
                                               stdout="", stderr=stderr)
        return run

    def test_convert_moves_the_pdf_into_place_and_cleans_up(self):
        source = self.root / "book.docx"
        source.write_bytes(b"docx")
        destination = self.root / "out" / "final.pdf"
        destination.parent.mkdir()
        wrote = []

        with patch.object(pdf, "_run_uno", side_effect=self._fake_uno(wrote)):
            pdf.convert(source, destination)

        self.assertEqual(destination.read_bytes(), b"%PDF-1.4 uno")
        interpreter, scratch_docx, scratch_pdf, soffice, workdir = wrote[0]
        self.assertEqual(interpreter, "/fake/python3")
        self.assertEqual(scratch_docx, source)
        self.assertTrue(soffice.endswith("libreoffice"))
        # The worker's out.pdf landed in its scratch, then was replaced.
        self.assertEqual(scratch_pdf.parent, workdir)
        # No .lekhika-pdf-* scratch directory left beside the destination.
        leftovers = [p.name for p in destination.parent.iterdir()
                     if p.name.startswith(".lekhika-")]
        self.assertEqual(leftovers, [])

    def test_timeout_kills_the_workers_whole_process_group(self):
        """The worker starts LibreOffice as a child: a timeout has to take
        the group, or soffice survives and convert() then deletes the
        profile directory out from under it, breaking the next export."""
        class FakeProcess:
            args = ["py", "uno_pdf.py"]
            pid = 4242
            returncode = None

            def communicate(self, timeout=None):
                if timeout is not None:
                    raise subprocess.TimeoutExpired("uno_pdf.py", timeout)
                return "", ""

        killed = []
        with patch.object(pdf.subprocess, "Popen",
                          return_value=FakeProcess()) as spawned, \
                patch.object(pdf.os, "killpg",
                             side_effect=lambda pid, sig: killed.append((pid, sig))):
            with self.assertRaises(subprocess.TimeoutExpired):
                pdf._run_uno("py", self.root / "in.docx",
                             self.root / "out.pdf", "soffice", self.root)

        self.assertTrue(spawned.call_args.kwargs["start_new_session"],
                        "the worker must lead its own session to be killable as one")
        self.assertEqual(killed, [(4242, signal.SIGKILL)])

    def test_convert_reports_the_workers_stderr(self):
        source = self.root / "book.docx"
        source.write_bytes(b"docx")

        with patch.object(pdf, "_run_uno",
                          side_effect=self._fake_uno(returncode=1,
                                                     stderr="no-uno: nope")):
            with self.assertRaises(pdf.ConversionError) as caught:
                pdf.convert(source, self.root / "book.pdf")

        self.assertIn("no-uno: nope", str(caught.exception))

    def test_convert_turns_a_timeout_into_a_readable_error(self):
        source = self.root / "book.docx"
        source.write_bytes(b"docx")

        with patch.object(pdf, "_run_uno",
                          side_effect=self._fake_uno(
                              boom=subprocess.TimeoutExpired("uno", 300))):
            with self.assertRaises(pdf.ConversionError) as caught:
                pdf.convert(source, self.root / "book.pdf")

        self.assertIn("300 seconds", str(caught.exception))

    def test_export_pdf_compiles_a_scratch_docx_then_converts(self):
        destination = self.root / "novel.pdf"
        project = FakeProject()

        with patch.object(pdf, "_run_uno", side_effect=self._fake_uno()):
            pdf.export_pdf(project, destination, {"id-1"}, "templates")

        self.assertEqual(destination.read_bytes(), b"%PDF-1.4 uno")
        self.assertEqual(len(project.calls), 1)
        scratch, ids, templates, fmt = project.calls[0]
        self.assertEqual(ids, {"id-1"})
        self.assertEqual(templates, "templates")
        # The compile is a DOCX file but the template is PDF's: a set
        # may give `pdf/` its own margins and ToC (§1.3).
        self.assertEqual(fmt, "pdf")
        self.assertFalse(scratch.exists())     # temp docx cleaned up
        leftovers = [p.name for p in self.root.iterdir()
                     if p.name.startswith(".lekhika-")]
        self.assertEqual(leftovers, [])


class UnoScriptTests(unittest.TestCase):
    """Cheap contract tests on the worker's entry point — no LibreOffice
    needed: wrong arity answers before it ever imports uno."""

    def test_usage_error_without_arguments(self):
        result = subprocess.run(
            [sys.executable, str(pdf.UNO_SCRIPT)],
            capture_output=True, text=True, timeout=30,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("usage: uno_pdf.py", result.stderr)

    def test_no_uno_message_when_the_interpreter_cannot_import_it(self):
        if self._uno_in(sys.executable):
            self.skipTest("this interpreter has uno; the message cannot fire")
        result = subprocess.run(
            [sys.executable, str(pdf.UNO_SCRIPT), "a.docx", "b.pdf",
             "soffice", "/tmp"],
            capture_output=True, text=True, timeout=30,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no-uno", result.stderr)

    @staticmethod
    def _uno_in(interpreter):
        probe = subprocess.run([interpreter, "-c", "import uno"],
                               capture_output=True, timeout=30)
        return probe.returncode == 0


class ExportPdfMenuTests(unittest.TestCase):
    """The window's Export PDF… reaches pdf.export_pdf with the scope
    and template set the shared dialog produced."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(
            Path(self.temporary.name) / "Novel", "Novel"
        )
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def test_export_dispatches_through_the_pdf_wrapper(self):
        destination = Path(self.temporary.name) / "book.pdf"
        with patch.object(ExportOptionsDialog, "exec",
                          return_value=QDialog.Accepted), \
                patch("lekhika.app.QFileDialog.getSaveFileName",
                      return_value=(str(destination), "")), \
                patch("lekhika.app.pdf.export_pdf") as export_pdf:
            self.window.export("pdf")

        export_pdf.assert_called_once()
        project, target, _ids, _templates = export_pdf.call_args.args
        self.assertIs(project, self.project)
        self.assertEqual(target, destination)


if __name__ == "__main__":
    unittest.main()
