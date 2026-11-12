"""What is wrong with the guide as it stands: coverage and page shape.

    python scripts/guide_audit.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from guide_inventory import (  # noqa: E402
    CHAPTER, FINISHED, _key, _same_page, _text, inventory,
)
from lekhika.project import Project  # noqa: E402

WORKING = Path.home() / "Documents" / "Lekhika Guide.lekhika"


def main() -> int:
    problems: list[str] = []
    project = Project(WORKING)
    project.open()
    try:
        documents = project.documents()
        chapter = next((d for d in documents if d.title == CHAPTER), None)
        if chapter is None:
            print("no inventory chapter")
            return 1
        for section_title, bullets in inventory():
            folder_title = section_title.split(". ", 1)[-1]
            folder = next((d for d in documents
                           if d.parent_id == chapter.id
                           and _key(d.title) == _key(folder_title)), None)
            if folder is None:
                problems.append(f"no folder for {folder_title}")
                continue
            siblings = [d for d in documents if d.parent_id == folder.id]
            for name, body in bullets:
                hit = next((d for d in siblings
                            if _key(d.title) == _key(name)
                            or _same_page(d.title, name)), None)
                if hit is None:
                    problems.append(f"no page :: {folder_title} :: {name}")
                elif not FINISHED.search(
                        _text(project.read_content(hit.id))):
                    problems.append(f"unfinished :: {folder_title} :: {name}")
        # duplicates inside one folder
        by_parent: dict[str, list] = {}
        for doc in documents:
            by_parent.setdefault(doc.parent_id, []).append(doc)
        for children in by_parent.values():
            seen = list(children)
            while seen:
                seed = seen.pop(0)
                for other in list(seen):
                    if _key(seed.title) == _key(other.title):
                        problems.append(
                            f"twice named: {seed.title!r} / {other.title!r}")
                        seen.remove(other)
        # shape of the files on disk
        root = WORKING / "The Lekhika Guide" / CHAPTER
        for page in sorted(root.rglob("*.html")):
            if page.name == "index.html":
                continue
            text = page.read_text(encoding="utf-8")
            if re.search(r"\*\*|`", text):
                problems.append(f"raw markdown :: {page.name}")
            if re.search(r"(?<![$])\$\{|\[\[", text):
                problems.append(f"unescaped syntax :: {page.name}")
    finally:
        project.close()
    print("\n".join(problems) if problems else "clean")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())