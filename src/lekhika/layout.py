"""The filesystem layout: writing a project out as a folder tree, and reading
it back (docs/STORAGE.md).

This is the storage migration's core, built *beside* the existing database
rather than replacing it — nothing in the running application calls this yet.
It exists so the tree can be written, read back, and compared before anything
real depends on it.

It works on plain :class:`Node` values, not on the database, so it can be
tested without a project and without Qt.

Frontmatter and manifests use a documented **subset of YAML** — keys, scalars,
simple lists, and one level of nesting by indentation. No anchors, aliases,
multi-document streams or tags; no dependency (docs/STORAGE.md).
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# Nothing on the three target filesystems will take these; Windows also
# reserves a handful of device names, and a file called NUL.txt is a headache.
ILLEGAL_EXTRA = set('<>:"/\\|?*')
RESERVED_NAMES = {"con", "prn", "aux", "nul",
                  *(f"com{n}" for n in range(1, 10)),
                  *(f"lpt{n}" for n in range(1, 10))}
MAX_LENGTH = 80

MACHINERY = ".lekhika"
PROJECT_MANIFEST = f"{MACHINERY}/project.yaml"
FOLDER_MANIFEST = f"{MACHINERY}/folder.yaml"
INDEX_NAME = "index.html"
DOCUMENT_SUFFIX = ".html"
FORMAT_VERSION = 1


# --- names -------------------------------------------------------------------

def slug(title: str) -> str:
    """The filename for a title: no spaces, nothing a filesystem refuses.

    Letters and digits survive in any script, and so do ``-`` and ``_``;
    spaces and everything else collapse to a single hyphen; the ends trim; the
    length caps; an empty title becomes ``Untitled``. Titles in the author's
    binder look like ``3 - Two People Meet`` and ``1---A Room``, which
    come out as ``3-One-meets-Another`` and ``1-Someone-s-room``.
    """
    characters = []
    for character in unicodedata.normalize("NFC", title).strip():
        # Combining marks are not separators: Devanagari and European
        # diacritics would otherwise be hyphenated mid-word (अंत, café).
        if (character.isalnum() or character in "-_"
                or unicodedata.category(character).startswith("M")):
            characters.append(character)
        else:
            characters.append("-")
    name = "".join(characters)
    while "--" in name:
        name = name.replace("--", "-")
    name = name.strip("-._")[:MAX_LENGTH].rstrip("-._")
    if not name:
        name = "Untitled"
    if name.split(".")[0].casefold() in RESERVED_NAMES:
        name = f"_{name}"
    return name


def unique_name(name: str, taken: set[str]) -> str:
    """``Book``, then ``Book-1``, ``Book-2`` — compared case-insensitively,
    because macOS and Windows filesystems are (docs/STORAGE.md).

    Numbering is quiet and meant to stay invisible: a writer with chapters
    named for their point of view (``MMC``, ``FMC``, ``MMC``…) never meets the
    suffix, because the binder shows the *title* from the frontmatter and only
    the file carries the number.
    """
    if name.casefold() not in taken:
        return name
    number = 1
    while f"{name}-{number}".casefold() in taken:
        number += 1
    return f"{name}-{number}"


STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.I | re.S)
MARKUP_RE = re.compile(r"<[^>]+>")
ENTITY_RE = re.compile(r"&[a-zA-Z#0-9]+;")
INVISIBLE_RE = re.compile(r"[\u200b-\u200f\ufeff]")


def has_prose(html: str, title: str = "") -> bool:
    """Does this document carry words of its own, beyond its own title?

    Two false positives are common enough to matter, both found in the
    author's real project:

    - Qt writes an *empty* document as a full HTML file whose only text is its
      own stylesheet (``p, li { white-space: pre-wrap; }``), which survives any
      tag-stripping that leaves ``<style>`` alone;
    - a container's content is often just its title, typed once as a heading
      (``<p><b>How the Light Breaks</b></p>``).

    Neither is prose. Strip style blocks, then markup, entities and
    zero-width characters, and compare what is left — letters and digits only
    — against the title the same way, so `1. Rebirth in Ruin` matches its own
    heading whatever punctuation either side carries.
    """
    text = INVISIBLE_RE.sub("", ENTITY_RE.sub(" ", MARKUP_RE.sub(
        " ", STYLE_BLOCK_RE.sub(" ", html or ""))))
    text = re.sub(r"[^\w]+", "", text, flags=re.UNICODE).casefold()
    if not text:
        return False
    heading = re.sub(r"[^\w]+", "", title or "", flags=re.UNICODE).casefold()
    return text != heading


# --- the documented YAML subset ----------------------------------------------

def _unescape(text: str) -> str:
    """Reverse :func:`_scalar`'s escapes: ``\\\\``, ``\\"`` and ``\\n``."""
    out: list[str] = []
    index = 0
    while index < len(text):
        if text[index] == "\\" and index + 1 < len(text):
            out.append({"n": "\n", '"': '"', "\\": "\\"}.get(
                text[index + 1], text[index + 1]))
            index += 2
        else:
            out.append(text[index])
            index += 1
    return "".join(out)


def _scalar(value) -> str:
    """A value, quoted only when it has to.

    One rule decides "has to": **a string is quoted unless :func:`_value`
    would read it back as that exact string.** Text that would otherwise
    be typed — ``007``, ``2026``, ``true``, ``6.10`` — is text to the
    writer and has to stay text (docs/STORAGE.md; a round trip may never
    rewrite what a document says). The earlier form quoted only a
    hand-picked set of words, so a count of ``007`` came back as ``7``.

    Values the model genuinely holds as numbers or booleans are written
    bare instead, so they come back as the type they are rather than as
    their str() rendering.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    text = "" if value is None else str(value)
    if isinstance(value, (int, float)):
        return text
    needs_quotes = (
        text == ""
        or text != text.strip()
        or "\n" in text
        or ": " in text
        or text.startswith(("-", "#", '"', "'", "&", "*", "!", "|", ">", "%", "@", "`"))
        or _value(text) != text
    )
    if needs_quotes:
        escaped = (text.replace("\\", "\\\\").replace('"', '\\"')
                   .replace("\n", "\\n"))
        return f'"{escaped}"'
    return text


def _value(raw: str):
    """The value a scalar stands for: unquoted numbers and booleans become
    their Python types, everything else stays text.

    Floats are included because the model holds real ones — print
    geometry is inches (``6.14``), and a round trip that returned
    ``"6.14"`` would compare unequal to what was written. Anything that
    does not parse stays text, which is the safe direction: a string the
    writer meant as text is never guessed into a number, because
    :func:`_scalar` quotes exactly those.
    """
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return _unescape(raw[1:-1])
    if raw.lstrip("-").isdigit():
        return int(raw)
    if raw.casefold() in {"true", "false"}:
        return raw.casefold() == "true"
    if raw.casefold() in {"null", "~", ""}:
        return ""
    try:
        number = float(raw)
    except ValueError:
        return raw
    # "nan"/"inf" parse but are not measurements; a hand-written one stays
    # the text it is rather than becoming a number nothing can compare.
    return number if math.isfinite(number) else raw


def dump_frontmatter(values: dict) -> str:
    """A ``---`` block: scalars, one level of nesting, lists of scalars."""
    lines = ["---"]
    for key, value in values.items():
        if isinstance(value, dict):
            lines.append(f"{key}:")
            for sub_key, sub_value in value.items():
                lines.append(f"  {sub_key}: {_scalar(sub_value)}")
        elif isinstance(value, (list, tuple)):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {_scalar(item)}")
        else:
            lines.append(f"{key}: {_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a file into (frontmatter, the rest).

    A file with no ``---`` block is all content, and its frontmatter is empty —
    a document edited by hand or written by another tool still loads.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    after = text.find("\n", end + 1)
    rest = text[after + 1:] if after != -1 else ""
    values: dict = {}
    current = None
    for line in text[3:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - "):
            bucket = values.get(current)
            if not isinstance(bucket, list):
                bucket = []
                values[current] = bucket
            bucket.append(_value(line[4:]))
        elif line.startswith("  "):
            bucket = values.get(current)
            if not isinstance(bucket, dict):
                bucket = {}
                values[current] = bucket
            key, _, value = line.strip().partition(":")
            bucket[key.strip()] = _value(value)
        else:
            key, _, value = line.partition(":")
            current = key.strip()
            values[current] = _value(value) if value.strip() else None
    return values, rest


# --- the tree ----------------------------------------------------------------

@dataclass
class Node:
    """One binder item, as the filesystem will hold it."""

    id: str
    title: str
    kind: str = "document"          # physical: "folder" or "document"
    type: str = ""                  # the role, from Type metadata
    content: str = ""               # prose HTML, frontmatter excluded
    synopsis: str = ""
    word_target: int = 0
    metadata: dict = field(default_factory=dict)
    # The one field the flat database had and an earlier tree had nowhere
    # to put (scripts/storage_roundtrip.py reports it): the book's print
    # geometry (1.15), a property of a *folder*, so it belongs in
    # folder.yaml, which travels with the folder — copy a book out and it
    # keeps its trim and margins. (The old template mark lived here too;
    # the mark is gone, and a manifest still carrying it is read as
    # simply not marking anything.)
    page_setup: dict | None = None
    children: list["Node"] = field(default_factory=list)

    @property
    def is_container(self) -> bool:
        """A container is a directory, whatever its role: a filesystem item
        cannot be a file and a directory at once (docs/STORAGE.md)."""
        return self.kind == "folder" or bool(self.children)


def _document_text(node: Node) -> str:
    """Frontmatter plus prose — the file's whole content."""
    frontmatter = {
        "title": node.title,
        "id": node.id,
        "type": node.type,
        "synopsis": node.synopsis,
        "word_target": node.word_target,
    }
    if node.metadata:
        frontmatter["metadata"] = dict(node.metadata)
    return dump_frontmatter(frontmatter) + node.content


def write_tree(root: Path, nodes: list[Node], *, title: str = "",
               settings: dict | None = None) -> dict:
    """Write a whole project as a folder tree; returns what it wrote.

    The project manifest carries the *top level's* order as well as the
    title and settings: the root has no ``folder.yaml`` of its own, and
    without it the top level would be the only one sorted by name.
    """
    root = Path(root)
    (root / MACHINERY).mkdir(parents=True, exist_ok=True)
    stats = {"folders": 0, "documents": 0, "indexes": 0}
    top_names = _write_level(root, nodes, stats)
    (root / PROJECT_MANIFEST).write_text(
        dump_frontmatter({"title": title, "format": FORMAT_VERSION,
                          "children": top_names,
                          "settings": dict(settings or {})}),
        encoding="utf-8")
    return stats


def _write_level(parent: Path, nodes: list[Node], stats: dict) -> list[str]:
    """One folder's children, in the order given — names first, so a folder's
    manifest can record exactly what its siblings were called."""
    taken: set[str] = set()
    plan: list[tuple[Node, str]] = []
    for node in nodes:
        name = unique_name(slug(node.title), taken)
        taken.add(name.casefold())
        plan.append((node, name))

    written: list[str] = []
    for node, name in plan:
        if node.is_container:
            folder = parent / name
            folder.mkdir(parents=True, exist_ok=True)
            stats["folders"] += 1
            child_names = _write_level(folder, node.children, stats)
            if has_prose(node.content, node.title):
                (folder / INDEX_NAME).write_text(_document_text(node),
                                                 encoding="utf-8")
                stats["indexes"] += 1
            # The folder's own frontmatter: everything that is not prose
            # lives here when there is no index.html to carry it, so a
            # container's synopsis, target and metadata never vanish.
            manifest_values: dict = {"id": node.id, "title": node.title,
                                     "type": node.type}
            if node.synopsis:
                manifest_values["synopsis"] = node.synopsis
            if node.word_target:
                manifest_values["word_target"] = node.word_target
            if node.metadata:
                manifest_values["metadata"] = dict(node.metadata)
            if node.page_setup:
                # A flat map of inches, so one level of nesting — exactly
                # what the YAML subset offers. It lives here rather than
                # in project.yaml so a folder copied out on its own is
                # still a book with its own trim (docs/STORAGE.md).
                manifest_values["page_setup"] = dict(node.page_setup)
            manifest_values["children"] = child_names
            manifest = folder / FOLDER_MANIFEST
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(dump_frontmatter(manifest_values),
                                encoding="utf-8")
            # The manifest records the *name* of each child, no trailing
            # slash: the slash is for a human reading a tree, and load_tree
            # matches against real directory entries (docs/STORAGE.md).
            written.append(name)
        else:
            (parent / f"{name}{DOCUMENT_SUFFIX}").write_text(
                _document_text(node), encoding="utf-8")
            stats["documents"] += 1
            written.append(f"{name}{DOCUMENT_SUFFIX}")
    return written


def load_tree(root: Path) -> tuple[list[Node], dict]:
    """Read a folder tree back into nodes, and the project manifest with it.

    The top level's order lives in the project manifest, not a
    ``folder.yaml`` — the root has none — so it is passed down here.
    """
    root = Path(root)
    manifest = root / PROJECT_MANIFEST
    project = {}
    top_order: list[str] | None = None
    if manifest.exists():
        project, _rest = parse_frontmatter(manifest.read_text(encoding="utf-8"))
        children = project.get("children")
        if isinstance(children, list):
            top_order = [str(name).rstrip("/") for name in children]
    return _load_level(root, top_order), project


def _load_level(folder: Path, order: list[str] | None = None) -> list[Node]:
    """One folder: the manifest's order first, then anything new on disk
    (appended, in name order), because the manifest is the order and files may
    be added by other hands (docs/STORAGE.md).

    ``order`` overrides the folder's own manifest for the project root; every
    other level reads its ``folder.yaml``.
    """
    manifest = folder / FOLDER_MANIFEST
    if order is None:
        order = []
        if manifest.exists():
            values, _rest = parse_frontmatter(manifest.read_text(encoding="utf-8"))
            children = values.get("children")
            if isinstance(children, list):
                # A trailing slash (an older writer marked directories that
                # way) is not part of the name; strip it before matching.
                order = [str(name).rstrip("/") for name in children]

    # index.html is the container's own prose, not one of its children.
    entries = {entry.name: entry for entry in folder.iterdir()
               if not entry.name.startswith(".") and entry.name != INDEX_NAME}
    names = [name for name in order if name in entries]
    names += sorted(name for name in entries if name not in names)

    nodes: list[Node] = []
    for name in names:
        entry = entries[name]
        if entry.is_dir():
            # The folder's own manifest carries its title, id and role; the
            # directory name is only ever a slug of the title. Its own
            # synopsis, target and metadata live there too, with index.html's
            # frontmatter as the fallback for a folder that predates that.
            own = _manifest_values(entry)
            children = _load_level(entry)
            index = entry / INDEX_NAME
            index_values, content = ({}, "")
            if index.exists():
                index_values, content = parse_frontmatter(
                    index.read_text(encoding="utf-8"))

            values = _folder_values(own, index_values)
            nodes.append(Node(
                id=str(values.get("id") or name),
                title=_named(values, "title", name),
                kind="folder",
                type=str(values.get("type") or ""),
                content=content,
                synopsis=str(values.get("synopsis") or ""),
                word_target=int(values.get("word_target") or 0),
                metadata=_string_map(values.get("metadata")),
                page_setup=_page_setup(values.get("page_setup")),
                children=children,
            ))
        elif entry.suffix == DOCUMENT_SUFFIX:
            values, content = parse_frontmatter(entry.read_text(encoding="utf-8"))
            nodes.append(Node(
                id=str(values.get("id") or entry.stem),
                title=_named(values, "title", entry.stem),
                kind="document",
                type=str(values.get("type") or ""),
                content=content,
                synopsis=str(values.get("synopsis") or ""),
                word_target=int(values.get("word_target") or 0),
                metadata=_string_map(values.get("metadata")),
            ))
    return nodes


def _named(values: dict, key: str, fallback: str) -> str:
    """A field's text, falling back to the file's name only when the key is
    *absent*.

    The distinction is the whole point. A file with no frontmatter at all —
    written by another tool, or by hand — has no title, and its name is the
    only name it has. But an **empty** title is a deliberate one: a writer
    who left the binder title blank keeps it blank (docs/STORAGE.md).

    Reading ``or name`` collapsed the two, so a blank title came back as the
    filename — and worse, as the filename's disambiguation number. Two
    blank titles became ``Untitled`` and ``Untitled-1``, handing the writer
    exactly the slug they are never supposed to see.
    """
    value = values.get(key)
    return fallback if value is None else str(value)


def _string_map(raw) -> dict:
    """Metadata is text: a numeric-looking value that the YAML subset read as
    a number comes back as its text, so the round trip is exact."""
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _page_setup(raw) -> dict | None:
    """Print geometry as a flat map of scalars, or None if it is not one.

    Validation is deliberately *not* here: the range and margin rules
    belong to ``project.clean_page_setup``, which the dialog and the
    export already share. This layer only has to hand back exactly what
    was stored — a folder whose geometry a hand editor broke should load
    and then fail validation where everything else fails it, not be
    silently dropped on the way in.
    """
    if not isinstance(raw, dict) or not raw:
        return None
    return {str(key): value for key, value in raw.items()}


def _folder_values(own: dict, index: dict) -> dict:
    """A folder's fields, the manifest winning over its ``index.html``.

    The manifest is what a *copy* of the folder carries, so it is the
    authority; ``index.html`` frontmatter only fills what it leaves out
    (docs/STORAGE.md). "Leaves out" means empty as well as absent — a
    folder written before these fields lived in the manifest has them only
    here, and an empty manifest ``synopsis`` is not a deliberate choice.

    **The title is the exception, and it is the one that matters.** An empty
    title *is* a decision: a writer who left the binder title blank keeps it
    blank, and it must never be borrowed from the directory name — the name
    is a slug, carries the disambiguation number when two folders collide,
    and the writer is not supposed to see it (docs/STORAGE.md). So a keyed
    title is kept even when it is empty; only a manifest with no title at
    all falls through to the index.
    """
    values = dict(index)
    values.update({key: value for key, value in own.items()
                   if value not in (None, "", {}, [])})
    if "title" in own:
        values["title"] = own["title"]
    return values


def _manifest_values(folder: Path) -> dict:
    """A folder's own manifest: id, title, role, and the order it kept.

    It is inside the folder so that a copied folder is a valid project, and it
    holds the *title* because the directory name is only a slug of it.
    """
    manifest = folder / FOLDER_MANIFEST
    if not manifest.exists():
        return {}
    values, _rest = parse_frontmatter(manifest.read_text(encoding="utf-8"))
    return values if isinstance(values, dict) else {}
