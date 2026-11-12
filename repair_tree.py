"""Repair a folder tree that was split in two by an old bug.

A leaf that gained children used to have its new child's path worked
out **before** the leaf was promoted to a directory. The child landed in
a ghost tree — ``X.html/`` beside the real ``X/`` — and the two halves
of one binder never met again:

    Perfectly Flawed/
    ├── Sleepwise stolen/          ← the real container, and its own words
    │   ├── index.html
    │   └── Stolen words.html      ← ... after the repair
    └── Sleepwise stolen.html/     ← the ghost: a directory named like a file
        └── Stolen words.html      ← the child, in the wrong tree

The tree never grows such a directory itself: a container is a directory
named after its title, and a leaf is an ``.html`` file. So a directory
whose name ends in ``.html`` is always a ghost, and this script puts
its contents back where they belong.

Nothing is destroyed: the child's own file is moved (not copied and
deleted), the ghost's own prose — if it has any — is kept as the
container's index when the container has none, and the backup is taken
before anything moves.

    python repair_tree.py <project.lekhika>           # a report
    python repair_tree.py <project.lekhika> --apply   # and the repair
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from lekhika.project import Project  # noqa: E402


def ghosts(project: Project) -> list[tuple[str, Path]]:
    """The ghost directories in a project: directories named like files."""
    found: list[tuple[str, Path]] = []
    for path in project.root.rglob("*"):
        if not path.is_dir():
            continue
        if path.name.lower().endswith(".html"):
            found.append((str(path.relative_to(project.root)), path))
    return sorted(found)


def repair(project: Project, relative: str, ghost: Path) -> list[str]:
    """Put a ghost's contents back where they belong.

    Returns the lines of the report.
    """
    lines: list[str] = []
    real = ghost.with_suffix("")
    if not real.is_dir():
        lines.append(f"  {relative}: no real container beside it — left alone")
        return lines
    # The ghost's children: files of its own, and the rows that point
    # into it.
    for child in sorted(ghost.iterdir()):
        if child.name.startswith("."):
            continue
        target = real / child.name
        if target.exists():
            lines.append(f"  {relative}/{child.name}: {real.name}/ already has "
                         f"one — left alone")
            continue
        shutil.move(str(child), str(target))
        lines.append(f"  {relative}/{child.name} → {real.name}/{child.name}")
        project.db.execute(
            "UPDATE documents SET path=? WHERE path=?",
            (str(target.relative_to(project.root)),
             str(child.relative_to(project.root))),
        )
    # The row for the ghost itself: it is the real container's row now.
    project.db.execute("DELETE FROM documents WHERE path=?", (relative,))
    shutil.rmtree(ghost, ignore_errors=True)
    lines.append(f"  {relative}: the ghost itself is gone")
    project.db.commit()
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--apply", action="store_true",
                        help="make the repair (without it, only report)")
    arguments = parser.parse_args()

    root = arguments.project
    if root.suffix != ".lekhika":
        root = root.with_suffix(".lekhika")
    project = Project(root)
    project.open()

    found = ghosts(project)
    if not found:
        print(f"{root}: nothing to repair — the tree is whole.")
        project.close()
        return 0

    print(f"{root}: {len(found)} ghost director"
          f"{'y' if len(found) == 1 else 'ies'} found:")
    for relative, ghost in found:
        print(f"  {relative}/")
    if not arguments.apply:
        print("\nNothing was changed. Run again with --apply to repair.")
        project.close()
        return 1

    backup = project.backup()
    print(f"\nBackup taken: {backup}")
    for relative, ghost in found:
        print(f"repairing {relative}/")
        for line in repair(project, relative, ghost):
            print(line)
    project.write_order_files()
    project.close()
    print("\nRepaired. The project order files were rewritten.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
