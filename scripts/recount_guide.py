"""Recount the shipped guide's pages: the cached word_count must agree
with the file it came from (the rule Project.write_content keeps).

Only the manual's own pages are recounted -- the files under "The
Lekhika Guide/". The rows that point into templates/ are template
fragments the app shows in the binder, not pages of the manual.

    python scripts/recount_guide.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from lekhika.disk_doc import split_document  # noqa: E402
from lekhika.project import Project  # noqa: E402

GUIDE = REPO / "src" / "lekhika" / "guide" / "guide.lekhika"
MANUAL = "The Lekhika Guide"


def main() -> int:
    db = GUIDE / "project.sqlite3"
    connection = sqlite3.connect(db)
    changed = 0
    try:
        rows = connection.execute(
            "SELECT id, title, word_count, path FROM documents").fetchall()
        for document_id, title, stored, path in rows:
            if not path or not path.startswith(MANUAL):
                continue
            file = GUIDE / path
            if file.is_dir():
                file = file / "index.html"
            if not file.is_file():
                continue
            _metadata, prose = split_document(file.read_text(encoding="utf-8"))
            count = len(Project.plain_text(prose).split())
            if count != stored:
                connection.execute(
                    "UPDATE documents SET word_count=? WHERE id=?",
                    (count, document_id))
                changed += 1
                print(f"{title}: {stored} -> {count}")
        connection.commit()
    finally:
        connection.close()
    print(f"{changed} page(s) recounted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
