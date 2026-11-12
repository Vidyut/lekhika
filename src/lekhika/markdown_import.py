"""Read markdown back into documents: Lekhika's own export, or Scrivener's.

Two dialects, one shape. Every document comes out as title, kind, the
title of its parent document (if the file says), its synopsis, its
metadata and its prose.

* **Lekhika** — an optional YAML fence above each heading, ``#`` for a
  chapter and ``##`` for a scene. Written by ``Project.export_markdown``,
  so export → import is a round trip.
* **Scrivener** — ``===DOCUMENT===`` blocks of ``Key: Value`` lines,
  then ``===TEXT===`` and the prose; the title lives in the block, not
  in a heading.

Metadata is the reason the import exists: a writer's fields are data
they built with effort, so no key is dropped here. Structural keys
(``Parent``, ``Position``, ``Level1`` …) shape the tree instead of
becoming fields; everything else is handed on for the caller to create
as a field or fill into one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .layout import parse_frontmatter

_HEADING = re.compile(r"^(#{1,2})\s+(.+?)(?:\s+#+)?\s*$")
_DOCUMENT = re.compile(r"(?m)^===DOCUMENT===[ \t]*$")

# Scrivener's own bookkeeping: these describe the export, not the book.
_SCREVENER_STRUCTURAL = {
    "id", "hierarchy", "position", "parentposition", "level1", "level2",
    "parent", "title", "type", "synopsis",
}
# File-head keys Scrivener writes above the first document: project facts.
_SCREVENER_PROJECT = {"title", "author", "copyright", "base header level"}

_KINDS = {"chapter": "chapter", "scene": "scene", "folder": "folder",
          "part": "chapter"}


@dataclass
class ImportedDocument:
    """One document as the file describes it, before it is created."""
    title: str
    kind: str = "scene"
    parent: str = ""
    synopsis: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    body: str = ""                    # markdown, not HTML


@dataclass
class ImportedFile:
    """What one markdown file holds: its documents and its project facts."""
    documents: list[ImportedDocument] = field(default_factory=list)
    project_metadata: dict[str, str] = field(default_factory=dict)


def parse_markdown(text: str) -> ImportedFile:
    """Parse either dialect. Scrivener's is recognised by its separators."""
    if _DOCUMENT.search(text):
        return _parse_scrivener(text)
    return _parse_lekhika(text)


# -- Lekhika's export -------------------------------------------------------

def _is_fence(lines: list[str], start: int, end: int) -> bool:
    """A ``---`` pair is frontmatter only if everything inside is a
    ``key: value`` line (or its indented/list continuation). Prose
    separated by ``---`` rules must not be eaten by the metadata pass.
    """
    saw_key = False
    for line in lines[start + 1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith(("  - ", "\t- ", "  ")) and saw_key:
            continue
        if ":" not in line:
            return False
        saw_key = True
    return saw_key


def _parse_lekhika(text: str) -> ImportedFile:
    lines = text.splitlines()
    result = ImportedFile()
    pending: dict = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip() == "---":
            closing = next(
                (j for j in range(index + 1, len(lines))
                 if lines[j].strip() == "---"), None)
            if closing is not None and _is_fence(lines, index, closing):
                pending, _rest = parse_frontmatter(
                    "\n".join(lines[index:closing + 1]) + "\n")
                index = closing + 1
                continue
        heading = _HEADING.match(line)
        if heading:
            start = index + 1
            end = start
            while end < len(lines):
                if _HEADING.match(lines[end]):
                    break
                if lines[end].strip() == "---":
                    closing = next(
                        (j for j in range(end + 1, len(lines))
                         if lines[j].strip() == "---"), None)
                    # A rule inside prose is prose; only a fence that
                    # belongs to the next document ends this one.
                    if (closing is not None
                            and _is_fence(lines, end, closing)):
                        break
                end += 1
            metadata = _clean(pending)
            pending = {}
            result.documents.append(ImportedDocument(
                title=_title(heading.group(2)),
                kind="chapter" if len(heading.group(1)) == 1 else "scene",
                parent=str(metadata.pop("Parent", "") or ""),
                synopsis=str(metadata.pop("Synopsis", "") or ""),
                metadata=metadata,
                body="\n".join(lines[start:end]).strip(),
            ))
            index = end
            continue
        index += 1
    return result


# -- Scrivener's export -----------------------------------------------------

def _key_values(text: str) -> dict[str, str]:
    """``Key: Value`` lines, blank lines ignored, later keys win."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        values[key.strip()] = value.strip()
    return values


def _parse_scrivener(text: str) -> ImportedFile:
    chunks = _DOCUMENT.split(text)
    result = ImportedFile()
    head = _key_values(chunks[0]) if chunks else {}
    for key, value in head.items():
        if key.casefold() in _SCREVENER_PROJECT and value:
            result.project_metadata[key] = value
    for chunk in chunks[1:]:
        block, _, body = chunk.partition("===TEXT===")
        meta = _key_values(block)
        title = meta.pop("Title", "").strip()
        body = _strip_leading_heading(body, title)
        if not title:
            first = re.search(r"(?m)^#+\s+(.+?)\s*$", body)
            title = _title(first.group(1)) if first else ""
        kind = _KINDS.get(meta.pop("Type", "").strip().casefold(), "scene")
        synopsis = meta.pop("Synopsis", "")
        parent = meta.pop("Parent", "")
        metadata = {
            key: value for key, value in meta.items()
            if key.casefold() not in _SCREVENER_STRUCTURAL and value
        }
        result.documents.append(ImportedDocument(
            title=title,
            kind=kind,
            parent=parent,
            synopsis=synopsis,
            metadata=_clean(metadata),
            body=body.strip(),
        ))
    return result


def _strip_leading_heading(body: str, title: str) -> str:
    """Scrivener repeats the title as a heading in the body; drop it."""
    lines = body.strip().splitlines()
    if not lines:
        return ""
    heading = _HEADING.match(lines[0])
    if heading and (_title(heading.group(2)).casefold()
                    == title.casefold()):
        return "\n".join(lines[1:]).strip()
    return body.strip()


# -- shared -----------------------------------------------------------------

def _title(raw: str) -> str:
    return raw.strip().strip("#").strip()


def _clean(values: dict) -> dict[str, str]:
    """Frontmatter values as text; empty and missing ones dropped.

    ``parse_frontmatter`` yields Python types for numbers and booleans and
    ``None`` for an empty value; the metadata table stores text, so a
    ``True`` arrives as ``"true"`` and ``None`` is not stored at all.
    """
    cleaned: dict[str, str] = {}
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, (int, float)):
            text = str(value)
        else:
            text = str(value)
        if text.strip():
            cleaned[str(key).strip()] = text
    return cleaned