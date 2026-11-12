"""Reading and writing a document file on disk: prose plus its metadata.

A Lekhika document is one ``.html`` file whose head carries a YAML
frontmatter block and whose body carries the prose. The file is the
truth: a writer can read it in any editor, and any tool can produce one.
Nothing here needs the database.

The metadata block is read from either of two places, because both are
in the wild:

* at the very top of the file, before ``<html>`` — the form
  ``lekhika-web`` writes and the form most other tools expect;
* inside the ``<head>`` element, between ``---`` fences — the form a
  writer's own editor produces, and the one Lekhika writes.

Whichever it is, writing always puts it at the top of the file, so
there is one canonical shape and a round trip never moves the writer's
prose around.
"""

from __future__ import annotations

import re
from pathlib import Path

from .layout import dump_frontmatter, parse_frontmatter

DOCUMENT_SUFFIX = ".html"

_HEAD_RE = re.compile(r"<head[^>]*>(.*?)</head>", re.IGNORECASE | re.DOTALL)


def is_document(path: Path) -> bool:
    """True when the path is a document file we can open."""
    return path.suffix.lower() == DOCUMENT_SUFFIX and path.is_file()


def read_document(path: Path) -> tuple[dict, str]:
    """A document's metadata and its prose, as they stand on disk.

    A file with no frontmatter is still a document: its metadata is
    empty and the whole file is prose.
    """
    text = path.read_text(encoding="utf-8")
    return split_document(text)


def split_document(text: str) -> tuple[dict, str]:
    """Split a document's text into (metadata, prose).

    The frontmatter is lifted out of whichever place it sits — the top
    of the file, or the ``<head>`` — and the prose is what remains.
    """
    if text.startswith("---"):
        metadata, rest = parse_frontmatter(text)
        if metadata:
            return metadata, rest
    match = _HEAD_RE.search(text)
    if match:
        inner = match.group(1).strip()
        if inner.startswith("---"):
            metadata, _ = parse_frontmatter(inner)
            if metadata:
                # The head held nothing but the metadata, so it is
                # replaced by an empty head: the prose around it is
                # untouched.
                prose = text[:match.start()] + "<head></head>" + text[match.end():]
                return metadata, prose
    return {}, text


def write_document(path: Path, metadata: dict, prose: str) -> None:
    """Write a document back: metadata at the top, prose after it.

    The write is atomic — a temporary file beside it, then a rename —
    so a crash mid-save cannot leave half a scene behind.
    """
    head = dump_frontmatter(metadata) if metadata else ""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(head + prose, encoding="utf-8")
    temporary.replace(path)


def merge_metadata(path: Path, changes: dict) -> dict:
    """The document's metadata with ``changes`` applied.

    Only keys the writer actually set are written; an unset field is
    left as the file already had it, so a hand-written value survives a
    round trip through the app.
    """
    metadata, _ = read_document(path)
    for key, value in changes.items():
        if value in (None, "", 0):
            metadata.pop(key, None)
        else:
            metadata[key] = value
    return metadata


def body_of(path: Path) -> str:
    """The prose of a document file — what the editor shows and saves."""
    _, prose = read_document(path)
    return prose


def title_of(path: Path) -> str:
    """A document's name without its extension."""
    return path.stem
