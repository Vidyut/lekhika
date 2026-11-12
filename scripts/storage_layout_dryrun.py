#!/usr/bin/env python3
"""Dry run: what the filesystem-as-truth layout would write (docs/STORAGE.md).

Read-only. Opens an existing project's database, walks the binder, and reports
the tree the migration would create: slashed names made safe, collisions,
duplicate and empty titles, and the folders that would be written. Nothing is
created, moved or renamed — this exists so the shape can be judged before any
of it is real.

    python3 scripts/storage_layout_dryrun.py "/path/to/Project.lekhika"
"""

from __future__ import annotations

import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

from lekhika.layout import has_prose as _has_prose

# Characters no target filesystem will take, plus the ones Windows reserves.
ILLEGAL = set('<>:"/\\|?*') | {chr(code) for code in range(32)}
# Reserved device names on Windows; a file called NUL.txt is a headache.
RESERVED = {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)),
            *(f"lpt{n}" for n in range(1, 10))}
MAX_LENGTH = 80


def slug(title: str) -> str:
    """A filename for a title: hyphens, no surprises (docs/STORAGE.md).

    Letters and digits survive in any script, `-` and `_` survive; spaces and
    everything illegal collapse to a single hyphen; the ends are trimmed; the
    length is capped; an empty title becomes Untitled.
    """
    kept = []
    for character in unicodedata.normalize("NFC", title).strip():
        if character.isalnum() or character in "-_":
            kept.append(character)
        else:
            kept.append("-")
    name = "".join(kept)
    while "--" in name:
        name = name.replace("--", "-")
    name = name.strip("-._")
    name = name[:MAX_LENGTH].rstrip("-._")
    if not name:
        name = "Untitled"
    if name.split(".")[0].casefold() in RESERVED:
        name = f"_{name}"
    return name


def load(project: Path):
    database = project / "project.sqlite3"
    if not database.exists():
        raise SystemExit(f"no project.sqlite3 in {project}")
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
    wanted = ["id", "parent_id", "title", "kind", "position"]
    if "doc_template" in columns:      # absent in databases older than 2.1
        wanted.append("doc_template")
    rows = connection.execute(
        f"SELECT {', '.join(wanted)} FROM documents").fetchall()
    connection.close()
    return rows


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    project = Path(sys.argv[1])
    rows = load(project)

    children: dict[str | None, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        children[row["parent_id"]].append(row)

    problems: list[str] = []
    renamed: list[tuple[str, str]] = []
    duplicates: list[str] = []
    files = folders = indexes = 0
    content_dir = project / "documents"
    prose_cache: dict[str, bool] = {}

    def has_prose(document_id: str, title: str = "") -> bool:
        """Does this row carry words of its own, not just children?

        Delegates to the real rule so the report and the migration can never
        disagree (docs/STORAGE.md): Qt's stylesheet text and a title typed as
        a heading are not prose.
        """
        if document_id not in prose_cache:
            path = content_dir / f"{document_id}.html"
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            prose_cache[document_id] = _has_prose(text, title)
        return prose_cache[document_id]

    manifests: list[tuple[str, list[str]]] = []

    def walk(parent_id: str | None, path: str) -> None:
        """Depth-first, assigning a filename per child in its own folder.

        Children are visited in the database's own sibling order — the order
        the writer sees in the binder — and the manifest records exactly that,
        because filenames must never be the source of order
        (docs/STORAGE.md).
        """
        nonlocal files, folders, indexes
        made: list[str] = []
        taken: dict[str, str] = {}
        for row in sorted(children.get(parent_id, []),
                          key=lambda item: item["position"]):
            # A container is a directory, whatever its kind — a chapter with
            # scenes cannot also be a file. Its own words become index.html.
            is_container = (row["kind"] == "folder"
                            or bool(children.get(row["id"])))
            name = slug(row["title"])
            extension = "" if is_container else ".html"
            candidate = f"{name}{extension}"
            key = candidate.casefold()
            if key in taken:
                number = 2
                while f"{name}-{number}{extension}".casefold() in taken:
                    number += 1
                duplicates.append(
                    f"{path}/{candidate} ← {row['title']!r} (a second one)")
                candidate = f"{name}-{number}{extension}"
                key = candidate.casefold()
            taken[key] = row["title"]
            if candidate != f"{row['title']}{extension}":
                renamed.append((f"{path}/{candidate}", row["title"]))
            if not row["title"].strip():
                problems.append(f"empty title at {path} ({row['id'][:8]})")
            made.append(candidate + ("/" if is_container else ""))
            here = f"{path}/{candidate}"
            if is_container:
                folders += 1
                note = ""
                if has_prose(row["id"], row["title"]):
                    indexes += 1
                    note = "  + index.html (its own prose)"
                print(f"{here}/   [{row['kind']}]{note}  {row['title']!r}")
            else:
                files += 1
                print(f"{here}   [{row['kind']}]  {row['title']!r}")
            walk(row["id"], here)
        if made:
            manifests.append((path or "/", made))

    print(f"project: {project}")
    print(f"documents: {len(rows)}\n")
    print("=== the tree it would write (folders shown with a trailing /) ===")
    walk(None, "")
    print()
    print(f"would write: {folders} folders, {files} documents "
          f"({indexes} of the folders also carry their own prose as index.html)")
    print(f"names changed by the slug rule: {len(renamed)}")
    for path, title in renamed[:15]:
        print(f"  {title!r}  ->  {path}")
    if len(renamed) > 15:
        print(f"  … and {len(renamed) - 15} more")
    print(f"collisions needing a suffixed name: {len(duplicates)}")
    for item in duplicates[:10]:
        print(f"  {item}")
    print(f"problems: {len(problems)}")
    for item in problems[:10]:
        print(f"  {item}")
    print()
    print("=== would-be folder.yaml, shallowest folders (order straight from the database)")
    shown = 0
    for path, names in manifests:
        if shown >= 4 or path.count("/") > 2:
            continue
        print(f"{path}/.lekhika/folder.yaml")
        for name in names[:8]:
            print(f"  - {name}")
        if len(names) > 8:
            print(f"  … and {len(names) - 8} more, still in order")
        print()
        shown += 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
