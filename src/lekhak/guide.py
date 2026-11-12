"""The Lekhak guide: the app's own manual, as a project.

The guide is a Lekhak project that ships with the software
(``guide/guide.lekhak``), so it is written in Lekhak and read in Lekhak.
It is a project like any other: browsable in the binder, and exportable —
DOCX, EPUB, PDF, HTML, Markdown — for reading without the app at all.

It is the project's example document as well as the user guide: a real
book, in a real project, that a writer can look at, export, and (with
*File → Save Project As…*) copy somewhere to work on as their own.

It opens read-only, and a fresh install opens it before asking anyone to
create a project: the software can be looked at before it is used.
"""

from __future__ import annotations

from pathlib import Path


def source_project() -> Path | None:
    """The guide as it ships, or None when the installation does not carry
    it — an incomplete installation still works."""
    path = Path(__file__).resolve().parent / "guide" / "guide.lekhak"
    return path if path.is_dir() else None


def guide_project() -> Path | None:
    """The guide, where it ships.

    None when the installation does not carry it: the app then simply has
    no guide to open.

    It is opened where it lives, read-only. Nothing is copied into the
    writer's disk — a program that leaves files behind is a program
    people try once — and nothing is written back into the install
    folder, whatever the permissions there happen to allow.
    """
    return source_project()
