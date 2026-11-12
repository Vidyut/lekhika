#!/usr/bin/env python3
"""Install (or remove) the Lekhika desktop entry for the current user, Linux.

Writes:
  ~/.local/share/applications/lekhika.desktop
  ~/.local/share/icons/hicolor/scalable/apps/lekhika.svg
  ~/.local/share/icons/hicolor/256x256/apps/lekhika.png

The Exec line prefers, in order: this checkout's .venv console script, a
`lekhika` command already on PATH, then this checkout's .venv interpreter
with `-m lekhika`. Stdlib only — works with a bare python3.

Usage:  python3 scripts/install_linux.py [--uninstall] [--check]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
APPLICATIONS = Path.home() / ".local" / "share" / "applications"
HICOLOR = Path.home() / ".local" / "share" / "icons" / "hicolor"
DESKTOP_FILE = APPLICATIONS / "lekhika.desktop"

DESKTOP_TEMPLATE = """\
[Desktop Entry]
Type=Application
Version=1.4
Name=Lekhika
GenericName=Writing Environment
Comment=Plan and write long-form documents
Exec={exec}
Icon=lekhika
Terminal=false
Categories=Office;WordProcessor;
StartupNotify=true
StartupWMClass=Lekhika
Keywords=writing;novel;book;manuscript;outline;scrivener;
"""


def exec_line() -> str:
    venv_console = ROOT / ".venv" / "bin" / "lekhika"
    if venv_console.exists():
        return quote(str(venv_console))
    on_path = shutil.which("lekhika")
    if on_path:
        return quote(on_path)
    venv_python = ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return f"{quote(str(venv_python))} -m lekhika"
    system_python = shutil.which("python3")
    if system_python:
        return f"{quote(system_python)} -m lekhika"
    raise SystemExit(
        "No Lekhika launcher found. Install the package first:\n"
        f"    python3 -m venv {ROOT / '.venv'} && "
        f"{ROOT / '.venv' / 'bin' / 'pip'} install -e {ROOT}"
    )


def quote(path: str) -> str:
    return f'"{path}"' if " " in path else path


def install() -> None:
    APPLICATIONS.mkdir(parents=True, exist_ok=True)
    (HICOLOR / "scalable" / "apps").mkdir(parents=True, exist_ok=True)
    (HICOLOR / "256x256" / "apps").mkdir(parents=True, exist_ok=True)

    DESKTOP_FILE.write_text(DESKTOP_TEMPLATE.format(exec=exec_line()))
    shutil.copyfile(PACKAGING / "lekhika.svg",
                    HICOLOR / "scalable" / "apps" / "lekhika.svg")
    shutil.copyfile(PACKAGING / "lekhika.png",
                    HICOLOR / "256x256" / "apps" / "lekhika.png")

    validate = shutil.which("desktop-file-validate")
    if validate:
        result = subprocess.run([validate, str(DESKTOP_FILE)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stdout + result.stderr, file=sys.stderr)
            raise SystemExit("desktop file failed validation")

    for command in (
        ["update-desktop-database", str(APPLICATIONS)],
        ["gtk-update-icon-cache", "-f", "-t", str(HICOLOR)],
    ):
        if shutil.which(command[0]):
            subprocess.run(command, capture_output=True)

    print(f"installed {DESKTOP_FILE}")
    print(f"  {DESKTOP_FILE.read_text().splitlines()[6]}")
    print(f"  icon: {HICOLOR / 'scalable' / 'apps' / 'lekhika.svg'}")


def uninstall() -> None:
    removed = []
    for path in (
        DESKTOP_FILE,
        HICOLOR / "scalable" / "apps" / "lekhika.svg",
        HICOLOR / "256x256" / "apps" / "lekhika.png",
    ):
        if path.exists():
            path.unlink()
            removed.append(str(path))
    for command in (
        ["update-desktop-database", str(APPLICATIONS)],
        ["gtk-update-icon-cache", "-f", "-t", str(HICOLOR)],
    ):
        if shutil.which(command[0]):
            subprocess.run(command, capture_output=True)
    print("removed:\n  " + "\n  ".join(removed) if removed
          else "nothing to remove")


def check() -> None:
    problems = []
    if not DESKTOP_FILE.exists():
        problems.append("desktop file missing")
    else:
        line = next((l for l in DESKTOP_FILE.read_text().splitlines()
                     if l.startswith("Exec=")), "")
        target = line.removeprefix("Exec=").strip().strip('"').split(" ")[0]
        if not Path(target).exists():
            problems.append(f"Exec target gone: {target}")
    if not (HICOLOR / "scalable" / "apps" / "lekhika.svg").exists():
        problems.append("scalable icon missing")
    if problems:
        raise SystemExit("stale install:\n  " + "\n  ".join(problems)
                         + "\nre-run without --check to repair")
    print("desktop launcher OK")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--uninstall", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.uninstall:
        uninstall()
    elif args.check:
        check()
    else:
        install()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
