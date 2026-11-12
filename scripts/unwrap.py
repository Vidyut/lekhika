"""Unwrap hard-wrapped prose so the text flows.

Markdown paragraphs that are wrapped at seventy-odd columns render the
same but cannot be reflowed: paste them into a word processor and the
breaks stay, and every later edit fights them. This joins each
paragraph into one line, leaving the structure alone — headings, lists,
tables, code blocks and rules exactly as they are, and a list item's
continuation lines joined into the item.

    python scripts/unwrap.py README.md docs/*.md
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HEADING = re.compile(r"^\s*#{1,6}\s")
RULE = re.compile(r"^\s*([-*_]\s*){3,}$")
TABLE = re.compile(r"^\s*\|")
ITEM = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)$")
QUOTE = re.compile(r"^\s*>")


def unwrap(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    block: list[str] = []
    in_code = False

    def flush() -> None:
        """Write the current block as one line."""
        if not block:
            return
        first = block[0]
        item = ITEM.match(first)
        if item:
            indent, marker, text = item.group(1), item.group(2), item.group(3)
            rest = " ".join(part.strip() for part in block[1:])
            out.append(f"{indent}{marker} {(text + ' ' + rest).strip()}")
        elif QUOTE.match(first):
            text = first.strip()
            rest = " ".join(part.strip() for part in block[1:])
            out.append((text + " " + rest).strip())
        else:
            out.append(" ".join(part.strip() for part in block))
        block.clear()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            in_code = not in_code
            out.append(line)
            continue
        if in_code:
            out.append(line)
            continue
        if not stripped:
            flush()
            out.append("")
            continue
        if (HEADING.match(line) or RULE.match(line) or TABLE.match(line)):
            flush()
            out.append(line)
            continue
        if ITEM.match(line):
            # A list item starts a block; its continuation lines follow it.
            flush()
            block.append(line)
            continue
        block.append(line)
    flush()
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def main(argv: list[str]) -> int:
    for name in argv:
        path = Path(name)
        text = path.read_text(encoding="utf-8")
        changed = unwrap(text)
        if changed != text:
            path.write_text(changed, encoding="utf-8")
            print(f"unwrapped {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
