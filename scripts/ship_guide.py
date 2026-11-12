"""Ship the guide: copy the working guide into the package.

The guide is written in Lekhika, in one place: ``~/Documents/Lekhika
Guide.lekhika``. This script takes that copy and puts it where the
software ships it (``src/lekhika/guide/guide.lekhika``), so the packaged
guide is a snapshot of the working one and never a second file the two
of us edit apart.

Run it when the guide is ready to ship, and check what it says: a guide
whose records disagree is not ready.

    python scripts/ship_guide.py            # copy and check
    python scripts/ship_guide.py --check    # check only
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from lekhika.project import Project  # noqa: E402

WORKING = Path.home() / "Documents" / "Lekhika Guide.lekhika"
SHIPPED = REPO / "src" / "lekhika" / "guide" / "guide.lekhika"


def check(source: Path) -> list[str]:
    """What would be wrong with the guide as it stands."""
    problems: list[str] = []
    if not source.is_dir():
        return [f"There is no guide at {source}."]
    project = Project(source)
    try:
        project.open()
        problems.extend(project.validate_alignment())
        if (source / "trash").is_dir():
            problems.append(
                "The guide has a trash: a shipped manual should not carry "
                "the things that were thrown away.")
        trash_row = project.db.execute(
            "SELECT id FROM documents WHERE path='trash'").fetchone()
        if trash_row is not None:
            problems.append(
                "The guide has a trash folder in its index: a row with no "
                "folder behind it is the kind of disagreement the app "
                "warns about when the guide is opened.")
        if any(path.name == ".locked" for path in source.rglob(".locked")):
            problems.append(
                "The guide carries a lock file: it would travel with every "
                "copy of it and lock the writer's copy too.")
    finally:
        project.close()
    return problems


def drop_the_trash(source: Path) -> None:
    """Take the trash out of the guide, row and folder alike.

    A shipped manual has thrown nothing away, and an empty trash folder
    is a thing the writer's binder does not need. The row goes as well:
    a row whose folder is not there is the one disagreement the app
    warns about when the guide opens.
    """
    project = Project(source)
    try:
        project.open()
        with project.db:
            project.db.execute("DELETE FROM documents WHERE path='trash'")
        project.write_order_files()
    finally:
        project.close()
    shutil.rmtree(source / "trash", ignore_errors=True)


def checkpoint(source: Path) -> None:
    """Fold the write-ahead log back into the database file.

    The guide is written while the app is running, so its database keeps
    recent commits in ``project.sqlite3-wal``. A copy of the database
    alone is a copy from before those commits, and copying the ``-shm``
    index beside it is worse — that index records which log frames the
    last reader had already applied, and a stale one makes the copy look
    like drift in the app. Checkpoint first, then copy the one file that
    holds everything.
    """
    project = Project(source)
    try:
        project.open()
        project.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        project.close()


def main(argv: list[str]) -> int:
    # The trash goes first: a manual has thrown nothing away, and a row
    # whose folder is missing is the one thing the app warns about when
    # the guide opens.
    drop_the_trash(WORKING)
    problems = check(WORKING)
    for problem in problems:
        print(f"• {problem}")
    if problems:
        print("\nNot ready to ship.")
        return 1
    if "--check" in argv:
        print("\nThe guide is ready to ship.")
        return 0
    checkpoint(WORKING)
    if SHIPPED.exists():
        shutil.rmtree(SHIPPED)
    shutil.copytree(WORKING, SHIPPED,
                    ignore=shutil.ignore_patterns("*-wal", "*-shm"))
    # What ships is what has to open cleanly: check the copy, not the
    # working guide it came from.
    problems = check(SHIPPED)
    for problem in problems:
        print(f"• {problem}")
    if problems:
        print("\nShipped, but the copy does not agree with itself.")
        return 1
    print(f"\nShipped to {SHIPPED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
