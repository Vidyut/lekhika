"""PDF export — a thin wrapper over the DOCX, as 1.6's decision recorded.

The DOCX stays the exact artifact and the single renderer: instead of
growing a second layout engine inside Lekhika, this module hands the
compiled DOCX to the writer's own LibreOffice and moves the result into
place atomically. The handoff goes through LibreOffice's UNO API (a
worker subprocess in ``uno_pdf.py``) rather than a bare
``--convert-to``: the CLI loader never refreshes fields, so the 1.7 TOC
would ship as its cached placeholder, while a UNO ``index.update()``
puts real page numbers in the contents page. Nothing here parses or
understands PDF — we shell out, and when the converter or its scripting
bindings are missing the message says how to fix each.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from .project import Project

# Candidate commands, in order: the deb/snap/tarball all install one of
# these names on PATH. Windows/macOS installs are often NOT on PATH, so
# well-known absolute locations are probed as fallbacks after PATH.
CANDIDATES = ("libreoffice", "soffice")
# Interpreters probed for the `uno` module: the app's own interpreter
# first (venvs created with system site-packages carry it), then the
# system python3 that LibreOffice's bindings were installed for.
UNO_CANDIDATES = (sys.executable, "python3")
_EXTRA_SOFFICE_PATHS = {
    "win32": (
        r"C:\Program Files\LibreOffice\program\soffice.com",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.com",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
    ),
    "darwin": (
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        str(Path.home() / "Applications/LibreOffice.app/Contents/MacOS/soffice"),
    ),
}
_EXTRA_UNO_INTERPRETERS = {
    "win32": (
        r"C:\Program Files\LibreOffice\program\python.exe",
        r"C:\Program Files (x86)\LibreOffice\program\python.exe",
        "python.exe",
    ),
    "darwin": (
        "/Applications/LibreOffice.app/Contents/Resources/python/bin/python3",
        "/Applications/LibreOffice.app/Contents/Frameworks/python3.framework/Versions/Current/bin/python3",
    ),
}
PROBE_TIMEOUT = 10        # --version / import uno are both quick
CONVERT_TIMEOUT = 300     # a full manuscript can take a while
UNO_SCRIPT = Path(__file__).with_name("uno_pdf.py")

# Why the last probes came up empty, kept apart so a converter message
# never mixes in interpreter detail (and vice versa).
_converter_errors: list[str] = []
_uno_errors: list[str] = []


class ConversionError(RuntimeError):
    """An impossible or failed DOCX -> PDF step, with a message meant
    for a writer rather than a log file."""


def _probe_converter(path: str, label: str) -> str | None:
    """Probe one soffice binary for a clean --version; appends to
    _converter_errors on failure, returns the path on success."""
    try:
        result = subprocess.run(
            [path, "--version"], capture_output=True, text=True,
            timeout=PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        _converter_errors.append(f"{label}: {error}")
        return None
    if result.returncode == 0:
        return path
    detail = (result.stderr or result.stdout).strip()
    _converter_errors.append(
        f"{label}: {detail[:300] or 'exit ' + str(result.returncode)}")
    return None


def find_converter() -> list[str] | None:
    """The first LibreOffice that actually runs.

    ``which`` alone lies here: a broken snap shim sat on PATH on the
    author's machine and died before doing any work, so every candidate
    is asked to speak first. PATH is stable within a session, but the
    probe is one ``--version`` per export — re-probing beats caching a
    miss made before the writer installed anything.

    After PATH names, well-known per-platform install locations are
    probed (``_EXTRA_SOFFICE_PATHS``): Windows and macOS installs are
    usually not on PATH, so without them the feature silently degrades
    to "install missing" even though LibreOffice is present.
    """
    _converter_errors.clear()
    for name in CANDIDATES:
        path = shutil.which(name)
        if not path:
            continue
        found = _probe_converter(path, name)
        if found:
            return [found]
    for candidate in _EXTRA_SOFFICE_PATHS.get(sys.platform, ()):
        if not Path(candidate).is_file():
            continue
        found = _probe_converter(candidate, candidate)
        if found:
            return [found]
    return None


def _find_uno_interpreter() -> str | None:
    """The first interpreter that can ``import uno``.

    The worker runs out-of-process precisely because the app's own
    interpreter usually cannot (venvs), so both candidates are probed
    cheaply with ``-c`` before anything is launched. After the
    list in ``UNO_CANDIDATES``, LibreOffice's bundled Python is probed
    per platform (``_EXTRA_UNO_INTERPRETERS``): on Windows and macOS the
    system ``python3`` does not carry ``uno`` — the bundled one does.
    """
    _uno_errors.clear()
    candidates: list[str] = list(UNO_CANDIDATES)
    for extra in _EXTRA_UNO_INTERPRETERS.get(sys.platform, ()):
        if extra not in candidates:
            candidates.append(extra)
    for name in candidates:
        if os.path.isabs(name):
            path = name if Path(name).is_file() else None
        else:
            path = shutil.which(name)
        if not path:
            _uno_errors.append(f"{name}: not found")
            continue
        try:
            result = subprocess.run(
                [path, "-c", "import uno"], capture_output=True, text=True,
                timeout=PROBE_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            _uno_errors.append(f"{path}: {error}")
            continue
        if result.returncode == 0:
            return path
        _uno_errors.append(f"{path}: cannot import the 'uno' module")
    return None


def converter_install_help() -> str:
    """How to install LibreOffice on this system, in one line. The
    platforms outside the author's own Ubuntu are LibreOffice's own
    published routes (winget for Windows, brew cask for macOS); the
    Ubuntu line is the apt package."""
    if sys.platform == "win32":
        return (r"winget install -e --id TheDocumentFoundation.LibreOffice "
                r"(or https://www.libreoffice.org/download/download/)")
    if sys.platform == "darwin":
        return (r"brew install --cask libreoffice "
                r"(or https://www.libreoffice.org/download/download/)")
    return "sudo apt install libreoffice-writer (Debian/Ubuntu)"


def uno_install_help() -> str:
    """How to get a Python that can ``import uno`` on this system. On
    Windows and macOS the bindings ship inside LibreOffice's own Python,
    so there is no separate package — the fix is a reinstall/update."""
    if sys.platform in ("win32", "darwin"):
        return ("the bundled LibreOffice Python should provide it — "
                "reinstall or update LibreOffice from "
                "https://www.libreoffice.org/download/download/")
    return "sudo apt install python3-uno (Debian/Ubuntu)"


def pdf_export_help() -> str:
    """The "?" / menu-tip explanation for PDF export: what to install,
    and what to do by hand if you don't."""
    return (
        "Install LibreOffice if you want Lekhika to create PDF exports "
        "for you. On this system: " + converter_install_help() + "; "
        "the Python UNO bindings come with " + uno_install_help() + ".\n\n"
        "Even without LibreOffice you can export DOCX, open it in Word or "
        "LibreOffice, update the Table of Contents if there is one, and "
        "save it as PDF yourself.\n\n"
        "Amazon KDP accepts DOCX files directly, but we recommend "
        "submitting the PDF you created so pagination, mirrored margins "
        "and page breaks land exactly as you checked them."
    )


def libreoffice_status_text() -> str:
    """One detected-system line for the Preferences "Links & export"
    page: what was actually found here, and the install command for
    whatever is missing (so the advice matches the machine, not the
    author's)."""
    converter = find_converter()
    if converter is None:
        return f"LibreOffice not found. Install: {converter_install_help()}"
    if _find_uno_interpreter() is None:
        return (f"LibreOffice found ({converter[0]}), but Python UNO "
                f"bindings not found. Install: {uno_install_help()}")
    return f"LibreOffice found ({converter[0]}), Python UNO found."


def _converter_missing_message() -> str:
    message = (
        f"PDF export needs LibreOffice installed. Install: "
        f"{converter_install_help()}"
    )
    if _converter_errors:
        message += ("\n\nDetails from the failed probe:\n"
                    + "\n".join(_converter_errors))
    return message


def _uno_missing_message() -> str:
    message = (
        "LibreOffice is installed, but no Python could load its scripting "
        f"bindings (the 'uno' module), which PDF export drives. Install: "
        f"{uno_install_help()}"
    )
    if _uno_errors:
        message += "\n\nDetails from the failed probe:\n" + "\n".join(_uno_errors)
    return message


def _run_uno(interpreter: str, docx: Path, pdf_path: Path,
             soffice: str, workdir: Path,
             script: Path = UNO_SCRIPT) -> subprocess.CompletedProcess:
    """The seam tests stand on: everything past here is LibreOffice.

    The worker runs as its own session leader because it starts LibreOffice
    as a child. On a timeout, killing just the worker would leave soffice
    alive — and convert() would then delete the profile directory out from
    under the survivor, so the *next* export fails for reasons nothing in
    its own run explains. Killing the whole process group takes both
    (Unix: ``os.killpg``; Windows: ``terminate`` then ``kill`` on the
    worker — ``os.killpg`` does not exist there).

    ``script`` defaults to the PDF worker; ODT import drives the same
    seam with its own worker (``uno_odt.py``), so the kill-on-timeout
    rule holds for both without being written twice.
    """
    popen_kwargs: dict = {
        "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        # so the group can be killed as one
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(
        [interpreter, str(script), str(docx), str(pdf_path),
         soffice, str(workdir)],
        **popen_kwargs,
    )
    try:
        stdout, stderr = process.communicate(timeout=CONVERT_TIMEOUT)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)   # pid is the pgid here
            except ProcessLookupError:
                pass                     # already gone; still drain the pipes
        process.communicate()
        raise
    return subprocess.CompletedProcess(process.args, process.returncode,
                                       stdout, stderr)


def convert(docx: Path, destination: Path) -> None:
    """Convert a DOCX to a PDF at ``destination``.

    The scratch outdir lives beside the destination rather than in
    ``/tmp``: the final move is an ``os.replace``, which only works
    within one filesystem, and exports land on the writer's disk. The
    worker keeps LibreOffice's private profile inside that same scratch,
    so a run never talks to (or deadlocks on) a LibreOffice the writer
    already has open.
    """
    command = find_converter()
    if command is None:
        raise ConversionError(_converter_missing_message())
    interpreter = _find_uno_interpreter()
    if interpreter is None:
        raise ConversionError(_uno_missing_message())
    workdir = Path(tempfile.mkdtemp(
        prefix=".lekhika-pdf-", dir=str(destination.parent)))
    try:
        produced = workdir / (docx.stem + ".pdf")
        try:
            result = _run_uno(interpreter, docx, produced, command[0], workdir)
        except subprocess.TimeoutExpired as error:
            raise ConversionError(
                f"LibreOffice did not finish within {CONVERT_TIMEOUT} seconds; "
                "gave up."
            ) from error
        if result.returncode != 0 or not produced.is_file():
            detail = (result.stderr or result.stdout).strip()[-1000:]
            raise ConversionError(
                "LibreOffice failed to convert the document:\n"
                + (detail or f"exit code {result.returncode}")
            )
        os.replace(produced, destination)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def export_pdf(project: Project, destination: Path,
               selected_ids: set[str] | None = None,
               templates=None) -> None:
    """Export as PDF by compiling a scratch DOCX and converting it.

    The orchestration lives here rather than in project.py because it
    manages temporary files and a subprocess; the project layer stays
    free of process concerns and keeps writing DOCX.
    """
    workdir = Path(tempfile.mkdtemp(
        prefix=".lekhika-docx-", dir=str(destination.parent)))
    try:
        scratch = workdir / "manuscript.docx"
        # The compile is a DOCX file, but the *template* is PDF's own:
        # a set may give `pdf/` its margins and its ToC while `docx/`
        # keeps another (§1.3), and only the converter in between is
        # indifferent to which one it got.
        project.export_docx(scratch, selected_ids, templates, fmt="pdf")
        convert(scratch, destination)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
