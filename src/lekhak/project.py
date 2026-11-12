"""Transparent project storage: the model behind the editor.

A project is a ``.lekhak`` directory holding three things, deliberately
kept separable so a writer can inspect or recover work by hand:

- ``project.sqlite3`` — structure and metadata: the document tree
  (parent + position), titles, synopses, word counts, snapshots and
  per-document key/value metadata. Never prose.
- ``documents/<id>.html`` — one plain HTML file of prose per document.
  The manuscript itself; readable without this codebase.
- ``backups/`` — timestamped copies of both, taken on open and on save.

Flow of a document through the system:

    add_document()   creates the row, then writes an empty content file
    write_content()  atomically replaces that file (temp + fsync + rename)
                     and refreshes the cached word_count
    documents()      walks the tree in binder order (parent, then position)
    delete_document() removes rows first, then files — so a crash leaves
                     orphans on disk, never a row pointing at nothing

Structure is split from prose on purpose: read_content()/write_content()
touch only files, everything else touches only rows, and the two are
reconciled by the document id.

Export path: compile_units() walks the binder in order, renders each
document through a TemplateSet, and the export_* methods assemble those
units into the target format. Snapshots are per-document HTML copies,
taken before destructive operations (split, merge, restore) so any of
them can be reversed.
"""

from __future__ import annotations

import os
import json
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape, unescape
from pathlib import Path

from PySide6.QtCore import QSettings

from .layout import (Node, load_tree as _load_tree, write_tree as _write_tree,
                     slug as _slug, dump_frontmatter)
from .disk_doc import split_document
from .templates import (
    CHILDREN_NAME, PLACEHOLDER_RE, TemplateSet, discover_template_sets,
    is_children_name, named_template_sets, parse_docx_config,
    project_template_root, split_head,
)


IF_CONDITION_RE = re.compile(r"\$if\(\s*([^)]*)\)")


TAG_RE = re.compile(r"<[^>]+>")
# Qt's toHtml() prepends a CSS head; without dropping it, every stored
# document would count ~19 phantom words ("p, li { white-space …") and
# leak the same lines into text exports and search.
STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.I | re.S)
# A pasted link is not a reference to this project. Relevant when scanning
# prose for mentions: URLs are full of digits and slashes that would otherwise
# match short numeric titles.
URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
# Backup directory names this app creates (datetime "%Y%m%d-%H%M%S-%f").
# Pruning matches this exactly so a writer's own folders in a
# writer-chosen backup directory are never mistaken for our old backups.
BACKUP_STAMP_RE = re.compile(r"\d{8}-\d{6}-\d{6}")
# The marker that locks a folder or a document against editing. It is a
# file in the tree, like ``.order.json``: the truth about the lock, and
# it travels with a copied folder.
LOCK_FILE = ".locked"

# The project's own furniture: the things that live in the project folder
# but are not part of the manuscript. The writer's tree is everything
# else. Backups live *outside* the project (backup_directory()), so an
# inside ``backups/`` is never furniture — it is a legacy mistake to
# flag, not to hide. Templates and trash are normal folders on disk,
# visible in the binder like everything else.
PROJECT_FURNITURE = frozenset({
    "project.sqlite3", "manifest.json", "documents", ".lekhak",
})
# The templates area: the folder of template sets at the project's root.
# It is not manuscript — the sets are listed by the template code, and
# they are reached from the Outliner. It is legitimate, editable content
# (export designs), so it is preserved and backed up, never flagged as a
# ghost and never deleted by reconciliation.
TEMPLATES_FOLDER = "templates"
# Deleted items land here, mirroring their original paths, so a restore
# returns them to place. It is a normal binder folder: visible, read
# from disk like everything else, with ``.order.json`` in every folder.
TRASH_FOLDER = "trash"

# validate_alignment() reports this advisory when a legacy inside
# ``backups/`` exists. Backups live outside the project (backup_directory()),
# so this is worth knowing — but it is not a structure disagreement, and
# none of the "which is right" resolutions can fix it, so it never triggers
# the drift prompt on its own. Callers compare against this exact string.
INSIDE_BACKUPS_PROBLEM = (
    "Backups folder inside project — backups should live outside "
    "(see backup_directory()), not inside the project")

# Kinds are labels, not structure: any item may contain any other, at any
# depth, and several items may share the top level. Folder is a neutral
# container; chapter and scene are the writer's shorthand for intended use.
# The physical structure is folders and files; the semantic role lives in
# the protected Type metadata (docs/MODEL.md) and type_of() derives it.
DOCUMENT_KINDS = {"folder", "chapter", "scene"}
# What a filesystem refuses, and the names Windows reserves.
_ILLEGAL_NAME_CHARS = set('<>:"/\\|?*') | {chr(code) for code in range(32)}
_RESERVED_NAME_STEMS = {
    "con", "prn", "aux", "nul",
    *(f"com{n}" for n in range(1, 10)),
    *(f"lpt{n}" for n in range(1, 10)),
}
# Lekhak's own reserved name: a container's own prose is index.html, so a
# document may not take that file name — the two would be one file, and the
# document would be swallowed by the container's page.
LEKHAK_RESERVED_NAMES = {"index"}
# The roles a document may have (1.17): two base ones, as close to the
# truth on disk as a role can be — a folder is a Folder, anything else a
# Document — and every other role the writer adds, which the database
# keeps per group, so a folder's roles never sit in a document's list.
TYPE_GROUPS = ("folder", "document")
BASE_TYPE = {"folder": "Folder", "document": "Document"}
# The metadata key that says a document is a template (1.17): its body
# is markup rather than manuscript, it stays out of the book, and the
# sidebar swaps to the template design panel while it is so marked.
TEMPLATE_KEY = "Template"
# The metadata key holding a template's own design (1.17): the yaml
# lines the design panel edits, written into the file's head.
TEMPLATE_CONFIG_KEY = "Template config"
# How a document names the template it uses (1.17): a set's own slot by
# name, as before; a one-file template and a template document by these
# prefixes, so the three can never be mistaken for one another.
TEMPLATE_FILE_PREFIX = "file:"
TEMPLATE_DOC_PREFIX = "doc:"

# What an imported metadata value looks like, for the field type it gets
# (File → Import Markdown): an ISO date is a Date field, true/false a
# Checkbox, anything else Text. The writer can change the type afterwards.
_DATE_VALUE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T].*)?$")


def _metadata_type(value: str) -> str:
    text = value.strip()
    if text.casefold() in ("true", "false"):
        return "checkbox"
    if _DATE_VALUE.match(text):
        return "date"
    return "text"
# The frontmatter keys Lekhak owns (1.17): anything else in a document's
# head is the document's own — a template's design.
OWN_HEAD_KEYS = {"title", "synopsis", "word_target", "kind", "page_break",
                 "scene_divider", "doc_template", "template"}
# How a document may demand to start on a page (1.6). "right" is recto
# and "next-odd" the literal parity phrasing — both emit the same DOCX
# ODD_PAGE section, as the user chose; likewise left/next-even.
# "auto" inherits (the document, then each ancestor folder) and falls back to
# a new page per document; "flow" runs on regardless; the parity values demand
# a recto/verso start, which is only meaningful once a print trim is set.
PAGE_BREAK_VALUES = ("auto", "flow", "page", "left", "right", "next-odd",
                     "next-even")
# Kinds that contribute prose to a compile by default. Book-level compile
# settings will override this per document later. A folder carries its own
# content (index.html) and may hold children, so when it is in scope it
# contributes to the export like any other document; templates and trash are
# excluded by path.
EXPORT_KINDS = {"chapter", "scene", "folder"}

# Auto-detected cover filenames under images/<folder_id>/ (2.1).
COVER_SUFFIXES = ("jpg", "jpeg", "png", "webp", "gif")

# Print geometry a book folder may carry (1.15): trim id ->
# (label, width, height) in inches. The KDP/IngramSpark print-on-demand
# list plus the international standards and the manuscript default;
# anything else is entered as width/height directly ("custom").
PAGE_TRIMS: dict[str, tuple[str, float, float]] = {
    "letter": ("US Letter (8.5 × 11)", 8.5, 11.0),
    "a4": ("A4 (8.27 × 11.69)", 8.27, 11.69),
    "a5": ("A5 (5.83 × 8.27)", 5.83, 8.27),
    "5x8": ("5 × 8", 5.0, 8.0),
    "5.06x7.81": ("5.06 × 7.81 (KDP standard)", 5.06, 7.81),
    "5.25x8": ("5.25 × 8", 5.25, 8.0),
    "5.5x8.5": ("5.5 × 8.5", 5.5, 8.5),
    "6x9": ("6 × 9", 6.0, 9.0),
    "6.14x9.21": ("6.14 × 9.21 (KDP standard)", 6.14, 9.21),
    "6.69x9.61": ("6.69 × 9.61", 6.69, 9.61),
    "7x10": ("7 × 10", 7.0, 10.0),
    "7.44x9.69": ("7.44 × 9.69", 7.44, 9.69),
    "7.5x9.25": ("7.5 × 9.25", 7.5, 9.25),
    "8x10": ("8 × 10", 8.0, 10.0),
    "8.25x11": ("8.25 × 11", 8.25, 11.0),
}

# 2.4f — the preset rows in a template's config panel (§1.2): the two
# settings a print file must not get wrong, each picked rather than
# typed. AMAZON_TRIMS is the Amazon print-on-demand list a text novel
# actually prints on — the large sizes are left out because those need
# typesetting, which is not this app's job — and every id is a
# PAGE_TRIMS id, so a pick writes the very numbers trim_id_for, Page
# setup and the export all know.
AMAZON_TRIMS: tuple[str, ...] = (
    "5x8", "5.06x7.81", "5.25x8", "5.5x8.5", "6x9",
)

# KDP's inside (gutter) margin by page count — the band's label as the
# writer knows their book, the margin in inches. The matching outside
# minimum is KDP_MARGIN_MIN: what top, outside and bottom must be for
# a book without bleed (Lekhak sets no bleed; full-bleed pages are a
# typesetting job). Choosing a band writes all four through
# config_geometry's own reading — L,T,R,B with inside first.
KDP_MARGIN_BANDS: tuple[tuple[str, float], ...] = (
    ("24–150 pages", 0.375),
    ("151–300 pages", 0.5),
    ("301–500 pages", 0.625),
    ("501–700 pages", 0.75),
    ("701–828 pages", 0.875),
)
KDP_MARGIN_MIN = 0.25

# The paper an export gets when nothing is configured (Phase 1): A4, because
# it is the ISO standard almost everywhere outside the USA and Canada, and
# Letter was never a decision — it was python-docx's template. Real books set
# a trim in Page setup, which overrides this entirely.
DEFAULT_PAGE_SETUP: dict = {
    "trim": "a4",
    "width": 8.27, "height": 11.69,
    "top": 1.0, "bottom": 1.0, "inside": 1.0, "outside": 1.0,
    # Not mirrored: a plain A4 export is not necessarily a bound book, and
    # equal margins make mirroring invisible anyway. A trim set in Page
    # setup carries the book's real mirroring.
    "mirror": False,
    "gutter": 0.0,
}


def trim_id_for(width: float, height: float) -> str:
    """The PAGE_TRIMS id matching these inches, or "custom"."""
    for trim_id, (_label, trim_width, trim_height) in PAGE_TRIMS.items():
        if abs(trim_width - width) < 0.005 and abs(trim_height - height) < 0.005:
            return trim_id
    return "custom"


# The contents placeholder's stand-in while the export is being
# rendered: the entries are the headings the export produces, so the
# value cannot exist until the units do. Chosen to be a string no
# fragment can contain and no writer can type.
TOC_SENTINEL = "\x00lekhak-toc\x00"

_HEADING_RE = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1>", re.I | re.S)


def _toc_entries(units, skip=(), *, max_depth: int = 4) -> list[tuple[int, str, int]]:
    """``(level, text, unit)`` for every heading a compiled export made.

    The contents page is the one document that must not list itself, so
    its unit is skipped, headings and all: once built, its body is
    recognised by ``skip`` (the indexes ``compile_units`` recorded); while
    it is still being built, by the sentinel it carries. ``max_depth``
    caps the heading levels included (Word/Writer-style depth).
    """
    max_depth = max(1, min(int(max_depth or 4), 6))
    entries: list[tuple[int, str, int]] = []
    for index, (_title, body) in enumerate(units, start=1):
        if index in skip or TOC_SENTINEL in (body or ""):
            continue
        for match in _HEADING_RE.finditer(body or ""):
            level = int(match.group(1))
            if level > max_depth:
                continue
            words = re.sub(r"<[^>]+>", "", match.group(2))
            text = re.sub(r"\s+", " ", unescape(words)).strip()
            if text:
                entries.append((level, text, index))
    return entries


def _roman_lower(number: int) -> str:
    values = ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"),
              (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
              (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"))
    result = ""
    for value, symbol in values:
        while number >= value:
            result += symbol
            number -= value
    return result or "0"


def _alpha_lower(number: int) -> str:
    result = ""
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        result = chr(97 + remainder) + result
    return result or "a"


def _toc_marker(style: str, number: int) -> str:
    style = (style or "decimal").strip().lower().replace(" ", "-")
    if style in ("none", "no", "hidden", ""):
        return ""
    if style in ("bullet", "bullets", "disc", "•"):
        return "•"
    if style in ("decimal", "number", "numbers", "1", "1.2.3"):
        return str(number)
    if style in ("lower-alpha", "alpha", "a", "a.b.c", "lower-alpha."):
        return _alpha_lower(number)
    if style in ("upper-alpha", "A", "A.B.C"):
        return _alpha_lower(number).upper()
    if style in ("lower-roman", "roman", "i", "i.ii.iii"):
        return _roman_lower(number)
    if style in ("upper-roman", "I", "I.II.III"):
        return _roman_lower(number).upper()
    return str(number)


def _toc_label(text: str, unit: int, *, links: bool) -> str:
    return (f"<a href='section-{unit}.xhtml'>{text}</a>"
            if links else text)


def _toc_markup(entries, *, links: bool, markers=None) -> str:
    """The entries as ``<li>``s, nested by heading level (§3.2).

    A fragment's own ``<ol>``/``<ul>`` holds the first level — a block
    placeholder keeps a container that can hold blocks — so this opens
    only the lists *below* it. ``markers`` is a list of per-depth marker
    styles (index 0 = first included level): decimal/alpha/roman, bullet,
    or none.
    """
    markers = list(markers or [])

    def style_for(depth: int) -> str:
        index = depth - 1
        if 0 <= index < len(markers):
            return str(markers[index])
        return "none"

    def items(start: int, level: int, depth: int) -> tuple[str, int]:
        out: list[str] = []
        index = start
        counter = 0
        tag = "ul" if style_for(depth) == "bullet" else "ol"
        while index < len(entries) and entries[index][0] >= level:
            item_level, text, unit = entries[index]
            index += 1
            counter += 1
            marker = _toc_marker(style_for(depth), counter)
            prefix = f"{marker}. " if marker and marker != "•" else ""
            label = prefix + _toc_label(text, unit, links=links)
            if index < len(entries) and entries[index][0] > item_level:
                inner, index = items(index, entries[index][0], depth + 1)
                out.append(f"<li>{label}<{tag}>{inner}</{tag}></li>")
            else:
                out.append(f"<li>{label}</li>")
        return "".join(out), index

    if not entries:
        return ""
    return items(0, entries[0][0], 1)[0]


def _toc_text(entries, *, markers=None) -> str:
    """The same entries as plain text: one line each, stepped in by level."""
    markers = list(markers or [])
    if not entries:
        return ""
    base = entries[0][0]
    counters: dict[int, int] = {}

    def style_for(depth: int) -> str:
        index = depth - 1
        if 0 <= index < len(markers):
            return str(markers[index])
        return "none"

    lines: list[str] = []
    for level, text, _unit in entries:
        depth = max(1, level - base + 1)
        counters[level] = counters.get(level, 0) + 1
        for key in [k for k in counters if k > level]:
            counters.pop(key, None)
        marker = _toc_marker(style_for(depth), counters[level])
        if marker == "•":
            prefix = "- "
        elif marker:
            prefix = f"{marker}. "
        else:
            prefix = ""
        indent = "  " * (depth - 1)
        lines.append(f"{indent}{prefix}{text}")
    return "\n".join(lines)


def clean_page_setup(row) -> dict | None:
    """Validated print geometry in inches, or None when the row is
    junk. Shared by storage, scope resolution and the dialog's accept
    path, so "what the dialog allows" and "what export will use" can
    never drift apart. Comparisons reject NaN/inf without a math import
    (every bound is a chain of < comparisons, which NaN fails)."""
    if not isinstance(row, dict):
        return None
    try:
        width = float(row["width"])
        height = float(row["height"])
        top = float(row["top"])
        bottom = float(row["bottom"])
        inside = float(row["inside"])
        outside = float(row["outside"])
        gutter = float(row.get("gutter", 0.0))
    except (KeyError, TypeError, ValueError):
        return None
    if not (1.0 <= width <= 40.0 and 1.0 <= height <= 40.0):
        return None
    if not all(0.0 <= margin <= 40.0
               for margin in (top, bottom, inside, outside, gutter)):
        return None
    if top + bottom >= height or inside + outside + gutter >= width:
        return None
    trim = str(row.get("trim", "custom"))
    if trim != "custom" and trim not in PAGE_TRIMS:
        trim = trim_id_for(width, height)
    return {
        "trim": trim,
        "width": width, "height": height,
        "top": top, "bottom": bottom,
        "inside": inside, "outside": outside,
        "mirror": row.get("mirror") is True,
        "gutter": gutter,
    }


def _apply_page_geometry(document, geometry: dict) -> None:
    """Put a book's print geometry (1.15) on every section of an open
    python-docx document: trim in twips, inside margin on the left of
    the recto (LTR books — page 1's left edge is the spine), gutter
    beside it, and mirror margins in settings.xml when asked. All
    sections are rewritten, so the ODD/EVEN_PAGE breaks of 1.6 can
    never drift onto a different paper size. A document opened from a
    sample file may already carry the mirroring — and may carry one the
    config then took away — so the flag is brought *to* the answer
    rather than merely appended to it (§3.1)."""
    from docx.enum.section import WD_ORIENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches

    landscape = geometry["width"] > geometry["height"]
    for section in document.sections:
        section.page_width = Inches(geometry["width"])
        section.page_height = Inches(geometry["height"])
        section.top_margin = Inches(geometry["top"])
        section.bottom_margin = Inches(geometry["bottom"])
        section.left_margin = Inches(geometry["inside"])
        section.right_margin = Inches(geometry["outside"])
        section.gutter = Inches(geometry["gutter"])
        section.orientation = (WD_ORIENT.LANDSCAPE if landscape
                               else WD_ORIENT.PORTRAIT)
    settings = document.settings.element
    mirrored = settings.find(qn("w:mirrorMargins"))
    if geometry["mirror"] and mirrored is None:
        settings.append(OxmlElement("w:mirrorMargins"))
    elif not geometry["mirror"] and mirrored is not None:
        settings.remove(mirrored)


def _apply_section_margins(section, geometry: dict) -> None:
    """One section's margins (1.18): a page's own design may ask for
    wider ones — a title page, a dedication — while the book's paper,
    gutter and mirroring stay the book's."""
    from docx.shared import Inches

    section.top_margin = Inches(geometry["top"])
    section.bottom_margin = Inches(geometry["bottom"])
    section.left_margin = Inches(geometry["inside"])
    section.right_margin = Inches(geometry["outside"])
    section.gutter = Inches(geometry["gutter"])


# 3.1 print handling — the config vocabulary an export reads. The keys
# are the writer's own (the parenthetical in "Page size (inches -
# horizontal, vertical)" is documentation and already stripped by
# parse_docx_config), and each one overrides what the sample .docx
# supplied. Anything not here is inert: the config says only what it
# says, and a shipped template's own config file documents every key.
DOCX_CONFIG_KEYS = (
    "page size", "margins", "mirror margins",
    "header", "header left", "header right",
    "footer", "footer left", "footer right",
    "new chapter", "new scene", "font",
    "title style", "chapter style", "scene style", "body style",
)

# The running-head half of the vocabulary: the lines a single page's own
# design may say for its own section (1.18).
RUNNING_HEAD_KEYS = ("header", "header left", "header right",
                     "header first page", "footer", "footer left",
                     "footer right", "footer first page")

# A running-head line that asks for nothing (`Header: none`): unlike an
# empty line — which means "inherit the sample" — it says the page
# carries no head at all, which is what a title page wants.
NO_RUNNING_HEAD = {"none", "no", "off"}

# The running-head/folio vocabulary's one non-string value (§3.1): a
# Header or Footer line whose middle piece is exactly `${PageNo}` asks
# for the page's own number, which a header cannot spell — it survives
# the value fill as this marker and becomes Word's PAGE field in the
# part. Private-use codepoints, so no writer's prose can collide with it.
PAGE_MARKER = "\ue000page\ue000"

# The spellings that ask for the folio. `PageNo` is the name the panel
# inserts and the reference page lists; `Page` is what the first
# templates wrote, kept working rather than broken (§3.2).
PAGE_NAMES = ("${PageNo}", "${Page}")

# How a printer may spell a page start (1.6's vocabulary — "recto" is
# what a printer says for "right"), normalized to hyphens first.
PAGE_BREAK_ALIASES = {
    "recto": "right", "odd": "right", "verso": "left", "even": "left",
    "page-break": "page", "new-page": "page", "newpage": "page",
    "continuous": "flow", "run-on": "flow", "no-break": "flow",
}


def page_break_value(text: str) -> str | None:
    """A ``New Chapter: Recto`` line in the page-start vocabulary, or
    None when it names no start we know (in which case nothing changes)."""
    word = re.sub(r"[\s_-]+", "-", str(text).strip().lower())
    if not word:
        return None
    if word in PAGE_BREAK_ALIASES:
        return PAGE_BREAK_ALIASES[word]
    return word if word in PAGE_BREAK_VALUES else None


def config_length(text) -> float | None:
    """One length from a print-config line, in inches: a bare number is
    inches, an ``mm``/``cm``/``in``/``pt`` suffix changes the unit
    (§3.1). Unreadable or out of range returns None, so a mistyped line
    is silently absent rather than silently enormous."""
    value = str(text).strip().lower().replace('"', "").replace(" ", "")
    factor = 1.0
    for suffix, step in (("mm", 1.0 / 25.4), ("cm", 1.0 / 2.54),
                         ("in", 1.0), ("pt", 1.0 / 72.0)):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            factor = step
            break
    try:
        number = float(value)
    except ValueError:
        return None
    inches = number * factor
    return inches if 0.0 <= inches <= 40.0 else None


def config_points(text) -> float | None:
    """A ``Font: ... 11pt ...`` size in points, or None when it says
    nothing usable. Bounded, because a size nobody meant is worse than
    no size at all."""
    value = str(text).strip().lower()
    if value.endswith("pt"):
        value = value[:-2].strip()
    try:
        number = float(value)
    except ValueError:
        return None
    return number if 1.0 <= number <= 300.0 else None


def config_length_list(text) -> list[float] | None:
    """A comma/space separated run of lengths — the margins line — or
    None when any of them cannot be read."""
    pieces = [piece for piece in re.split(r"[,;\s/]+", str(text).strip())
              if piece]
    if not pieces:
        return None
    lengths: list[float] = []
    for piece in pieces:
        length = config_length(piece)
        if length is None:
            return None
        lengths.append(length)
    return lengths


def config_page_size(text) -> tuple[float, float] | None:
    """``Page size (inches - horizontal, vertical): 5,7`` — two numbers,
    ``5x7`` (a trim id or its plain spelling), or a size named from
    PAGE_TRIMS ("A4", "Letter")."""
    value = str(text).strip()
    cross = re.fullmatch(
        r"(\d+(?:[.,]\d+)?)\s*(?:x|×|\*)\s*(\d+(?:[.,]\d+)?)\s*(mm|cm)?",
        value, re.IGNORECASE)
    if cross:
        factor = {"mm": 1.0 / 25.4, "cm": 1.0 / 2.54}.get(
            (cross.group(3) or "").lower(), 1.0)
        width, height = float(cross.group(1)) * factor, float(cross.group(2)) * factor
        if 0.0 < width <= 40.0 and 0.0 < height <= 40.0:
            return width, height
        return None
    numbers = config_length_list(value)
    if numbers is not None and len(numbers) == 2:
        width, height = numbers
        if width > 0.0 and height > 0.0:
            return width, height
        return None
    name = re.sub(r"[^a-z0-9]", "", value.lower())
    for trim, (_label, width, height) in PAGE_TRIMS.items():
        if re.sub(r"[^a-z0-9]", "", trim) == name:
            return width, height
    return None


def config_geometry(config: dict) -> dict:
    """The ``Page size``, ``Margins`` and ``Mirror margins`` lines as
    an overlay in inches.

    A four-value margins line is L,T,R,B; a two-value one is the
    mirrored pair (inside, outside) §3.1 describes — saying it mirrored
    is what a two-value line means. ``Mirror margins: yes|no`` says the
    flag outright either way (a pair only implies yes), and being read
    last, an explicit line wins over the implied one. Anything
    unreadable is simply not said, as everywhere in the config.
    """
    overlay: dict = {}
    size = config_page_size(config.get("page size", ""))
    if size is not None:
        overlay["width"], overlay["height"] = size
        overlay["trim"] = trim_id_for(*size)
    margins = config_length_list(config.get("margins", ""))
    if margins is not None and len(margins) in (2, 4):
        if len(margins) == 4:
            left, top, right, bottom = margins
            overlay["inside"], overlay["top"] = left, top
            overlay["outside"], overlay["bottom"] = right, bottom
        else:
            overlay["inside"], overlay["outside"] = margins
            overlay["mirror"] = True
    mirror = str(config.get("mirror margins", "")).strip().lower()
    if mirror in {"yes", "true", "on", "1"}:
        overlay["mirror"] = True
    elif mirror in {"no", "false", "off", "0"}:
        overlay["mirror"] = False
    return overlay


def config_margins(config: dict) -> dict | None:
    """Only the margins a print config states (§3.1), in inches: the
    per-page half of config_geometry, without the paper it prints on.
    A page's own design may widen its margins while the book keeps its
    trim (1.18)."""
    overlay = config_geometry(config)
    margins = {key: overlay[key]
               for key in ("top", "bottom", "inside", "outside")
               if key in overlay}
    return margins or None


def config_headings(text) -> list[str]:
    """A running-head/footer line's three pieces — left, middle, right
    — split *before* the placeholders are filled, so an author called
    `Last, First` never becomes four pieces of header. Fewer than three
    pieces pad to a shorter line; extra commas stay with the right, the
    piece whose values carry them most often."""
    if text is None:
        return []
    pieces = [piece.strip() for piece in str(text).split(",", 2)]
    while len(pieces) < 3:
        pieces.append("")
    return pieces


def _names_children(text: str) -> bool:
    """Whether a fragment asks for the children itself — ``${children}``,
    a ``children-<type>`` group, or a ``$if(children...)`` guard. A
    fragment that never names the family has the children appended after
    it, so a container's prose cannot be lost by silence (§3)."""
    names = list(PLACEHOLDER_RE.findall(text or ""))
    names += [condition.split("=")[0].strip()
              for condition in IF_CONDITION_RE.findall(text or "")]
    return any(is_children_name(name) for name in names)


def _brace_aliases(text: str) -> str:
    """``{Series}`` — the spelling the author's own config example uses
    — read as ``${Series}``: one namespace, written however the writer
    already writes it (§3.1)."""
    return re.sub(r"(?<!\$)\{([A-Za-z][A-Za-z0-9 ._-]*)\}", r"${\1}", text)


def _head_part(section, noun: str, which: str):
    """One running part of a section: the header or footer of the page
    set asked for — ``odd`` (recto), ``even`` (verso) or ``first`` (the
    section's opening page, Word's own third slot)."""
    if noun == "header":
        return (section.first_page_header if which == "first"
                else section.header if which == "odd"
                else section.even_page_header)
    return (section.first_page_footer if which == "first"
            else section.footer if which == "odd"
            else section.even_page_footer)


def _first_page_flag(section) -> None:
    """Turn on a section's *different first page* (§3.1). Until the flag
    is set, the part written into is real but unused, so the line that
    speaks for the opening page has to set it — and only that line does:
    a plain `Header:` must not change what the first page carries."""
    try:
        section.different_first_page_header_footer = True
    except (AttributeError, ValueError):
        # No flag in this document's section properties: the sample's own
        # paper won the argument, and a head is not worth an exception.
        pass


def _head_font(config: dict) -> tuple[str | None, float | None] | None:
    """The `Head font:` line read out — family and points, either of
    which may be missing — or None when the file says nothing about the
    type of its running heads, in which case the sample's own stands.
    Same one-line grammar as `Font:` (§3.1), read one piece at a time."""
    raw = str(config.get("head font", "")).strip()
    if not raw:
        return None
    pieces = [piece.strip() for piece in raw.split(",")]
    name = pieces[0] or None
    size = config_points(pieces[1]) if len(pieces) > 1 else None
    if name is None and size is None:
        return None
    return (name, size)


def _style_head_run(run, font) -> None:
    """A running head's own type: the `Head font:` answer, on the run —
    the piece, its tab and the folio alike, so the folio is not set in
    the body's size while the head beside it is not."""
    if not font or run is None:
        return
    from docx.shared import Pt

    name, size = font
    if name:
        run.font.name = name
    if size is not None:
        run.font.size = Pt(size)


def open_docx_sample(path: Path | None):
    """The sample `.docx` as an open document with its body cleared
    (§3.1 option 3): its text is ignored, its formatting is not. Every
    section but the last is dropped with the prose, so the remainder's
    page setup, styles, headers and footers are what the export writes
    into. None when there is no sample, or it will not open — a file we
    cannot read is not a template, and exporting must still happen."""
    if path is None:
        return None
    candidate = Path(path)
    if not candidate.is_file():
        return None
    from docx import Document as DocxDocument
    from docx.oxml.ns import qn

    try:
        document = DocxDocument(str(candidate))
    except Exception:  # noqa: BLE001 — a corrupt or mislabelled file
        return None
    body = document.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)
    return document


def sample_geometry(document) -> dict | None:
    """The paper an open sample document is set up on, as a page-setup
    row (1.15's shape), or None when its section is broken or absent."""
    from docx.oxml.ns import qn

    try:
        section = document.sections[-1]
    except IndexError:
        return None
    try:
        width = section.page_width.inches
        height = section.page_height.inches
    except (AttributeError, TypeError):
        return None
    geometry = {
        "width": width, "height": height, "trim": trim_id_for(width, height),
        "top": section.top_margin.inches, "bottom": section.bottom_margin.inches,
        "inside": section.left_margin.inches,
        "outside": section.right_margin.inches,
        "gutter": section.gutter.inches if section.gutter is not None else 0.0,
        "mirror": (
            document.settings.element.find(qn("w:mirrorMargins")) is not None),
    }
    return geometry


def paragraph_style(document, name) -> str | None:
    """``name`` if it is a paragraph style of this document — what a
    ``Title style:`` (or a sample's own missing \"Heading 1\") asks for
    — else None, so an unreadable mapping degrades to no style rather
    than to an exception in the middle of an export."""
    if not name:
        return None
    from docx.enum.style import WD_STYLE_TYPE

    try:
        style = document.styles[str(name)]
    except KeyError:
        return None
    except ValueError:
        return None  # a look-alike that is a character style, not ours
    return str(name) if style.type == WD_STYLE_TYPE.PARAGRAPH else None


# Export-format word -> template-variant folder titles to try, in order
# (2.1): a matched template's "ebook" subfolder applies to EPUB exports,
# and the plain format word is the natural second guess.
FORMAT_VARIANT_NAMES = {
    "text": ("text", "txt", "plain"),
    "html": ("html", "web"),
    "docx": ("docx", "print"),
    "epub": ("ebook", "epub"),
}

COVER_MEDIA_TYPES = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "webp": "image/webp", "gif": "image/gif",
}

# 2.2 strip policies, for links whose target is not in the current export:
# "strip" removes the link's words from the output entirely, "plain-text"
# leaves the words but no link, "keep-as-note" leaves the words plus a
# marker naming the missing target. Published links (target in the export)
# always become real links where the format can carry one.
LINK_POLICIES = ("strip", "plain-text", "keep-as-note")
DEFAULT_LINK_POLICY = "plain-text"

# DOCX-internal link markers: \x02<doc_id>\x03<text>\x04 survives
# plain_text (neither unescape nor TAG_RE touches control characters), so
# export_docx can split the plain-text runs into real w:hyperlink runs
# anchored at each heading's bookmark.
LINK_TOKEN_RE = re.compile(r"\x02([^\x03\x04]+)\x03([^\x04]*)\x04")

# Tracked-change sentinel spans (2.4). Qt's toHtml round-trips CSS on
# spans but drops custom format properties, so the marks ride on a
# sentinel background colour the compile pass recognises; the live ids
# re-attach by span order on load (see Revision.sequence).
REVISION_INSERT_BG = "#cfe4ff"
REVISION_DELETE_BG = "#ffd6d6"
DELETE_SPAN_RE = re.compile(
    r'<span\b[^>]*background-color:\s*' + REVISION_DELETE_BG
    + r'[^>]*>.*?</span>', re.I | re.S)
INSERT_SPAN_RE = re.compile(
    r'(<span\b[^>]*background-color:\s*' + REVISION_INSERT_BG
    + r'[^>]*>)(.*?)(</span>)', re.I | re.S)


_SPELLED_UNITS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
)
_SPELLED_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty",
                 "seventy", "eighty", "ninety")


def _spelled_position(number: int) -> str:
    """``one``, ``twenty-one`` — a number as prose spells it (3.2).

    Hundreds are spelled out; beyond 999 the leading part is read as a
    count of hundreds (``ten hundred``), which is not how a book of that
    length numbers its chapters.
    """
    if number < 20:
        return _SPELLED_UNITS[number]
    if number < 100:
        tens, unit = divmod(number, 10)
        return _SPELLED_TENS[tens] + (f"-{_SPELLED_UNITS[unit]}" if unit else "")
    hundreds, rest = divmod(number, 100)
    text = f"{_SPELLED_UNITS[hundreds]} hundred"
    return f"{text} {_spelled_position(rest)}" if rest else text


def _template_numbers(chapter_number: int, scene_number: int,
                      words_total: int) -> dict[str, str]:
    """Counter placeholders for one rendered document."""
    return {
        "ChapterNumber": str(chapter_number),
        "SceneNumber": str(scene_number),
        "WordsTotal": str(words_total),
    }


@dataclass(frozen=True)
class Document:
    """One node of the binder tree, as stored in SQLite.

    ``parent_id`` is None for a top-level item; ``position`` is the rank
    among siblings under that parent, and is what binder order follows.
    ``kind`` is a label (folder/chapter/scene/note), not structure — any
    kind may contain any other. ``doc_template`` names the
    document-level rendering slot picked in the sidebar. A folder is a
    template only by being one under ``templates/`` (docs/TEMPLATES.md):
    there is no mark, because the disk is the truth.
    """
    id: str
    parent_id: str | None
    title: str
    synopsis: str
    position: int
    word_target: int
    kind: str
    word_count: int = 0
    page_break: str = "auto"
    scene_divider: str = ""
    doc_template: str = ""
    path: str = ""


@dataclass(frozen=True)
class Snapshot:
    """A named, restorable copy of one document's content and synopsis."""
    id: str
    document_id: str
    name: str
    created_at: str


@dataclass
class DiskNode:
    """One thing on disk, as the binder shows it.

    The tree the binder draws is the folder tree itself
    (docs/STORAGE.md): ``path`` is where the thing lives, ``is_folder``
    says whether it is a directory or an ``.html`` file, and
    ``document_id`` is the row the index keeps for it — the join between
    the two. A node with no row is something the writer put on disk by
    hand; the index catches up with it, and until then it is shown but
    not editable.
    """
    path: str
    title: str
    is_folder: bool
    document_id: str | None = None
    kind: str = "scene"
    children: list["DiskNode"] = field(default_factory=list)


class DeleteError(Exception):
    """A file could not be removed, so nothing was deleted.

    The failures are ``(path, reason)`` pairs: what would not go, and
    why. The document is still in the binder — the writer decides what
    to do about it.
    """

    def __init__(self, failures: list[tuple[Path, str]]):
        self.failures = failures
        super().__init__(
            "; ".join(f"{path}: {reason}" for path, reason in failures))


@dataclass(frozen=True)
class Revision:
    """One pending tracked change (2.4).

    A row exists exactly while the mark is unresolved; accept/reject
    deletes it. ``session_id`` groups the changes of one revision pass,
    ``sequence`` is the per-document creation order — the order spans
    re-attach in on load, since Qt's HTML round-trip drops the live
    format properties that carried the ids.
    """
    id: str
    document_id: str
    session_id: str
    kind: str  # "insert" or "delete"
    author: str
    created_at: str
    sequence: int


def clean_name(name: str) -> str:
    """Turn a title into a name the filesystem accepts.

    Spaces and letters in any script survive — the writer's own names
    are the point, so "A Fictional Book" stays that. Only characters a
    filesystem refuses (``/``, ``:``, control codes…) collapse to a
    hyphen; the ends are trimmed of dots and spaces (Windows dislikes
    them); a reserved device name is prefixed; an empty name becomes
    "Untitled".

    ``index`` is reserved by Lekhak rather than by the filesystem: a
    container's own prose is its ``index.html``, so a document of that
    name is written as ``_index.html`` — the title still reads "index"
    in the binder, and the container's page keeps its own file.
    """
    text = (name or "").strip()
    kept = ["-" if (character in _ILLEGAL_NAME_CHARS) else character
            for character in text]
    cleaned = "".join(kept).strip().rstrip(". ")
    if not cleaned:
        cleaned = "Untitled"
    stem = cleaned.split(".")[0].casefold()
    if stem in _RESERVED_NAME_STEMS or stem in LEKHAK_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    if len(cleaned) > 120:
        cleaned = cleaned[:120].rstrip(". ")
    return cleaned or "Untitled"


# Reading time: the average speed of an adult reading prose. It is an
# estimate, and the app says so by showing it beside the count it comes
# from — a writer can disagree with the number and still read the words.
WORDS_PER_MINUTE = 250


def reading_time(words: int) -> str:
    """How long a piece of prose takes to read, as a short phrase.

    Two hundred and fifty words a minute is the usual figure for adult
    prose. Under a minute reads better as "under a minute" than as
    "0 min", and an empty document has no reading time at all.
    """
    if words <= 0:
        return ""
    minutes = max(1, round(words / WORDS_PER_MINUTE))
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} hr {rest} min" if rest else f"{hours} hr"


class Project:
    """Transparent project storage with atomic document writes."""

    def __init__(self, root: Path, *, read_only: bool = False):
        self.root = Path(root)
        # A read-only project is one Lekhak may look at but never write
        # to: the guide that ships with the software, opened from the
        # install folder. Nothing here assumes the rights we were not
        # given — the files are read, the index is opened immutable, and
        # every write is skipped rather than attempted.
        self.read_only = read_only
        # Where a project's prose used to live, before the folder tree
        # (docs/STORAGE.md). Kept as a name because old projects still
        # have those files, and a row without a path still reads from
        # there.
        self.documents_dir = self.root / "documents"
        self.db_path = self.root / "project.sqlite3"
        self.connection: sqlite3.Connection | None = None
        # id -> ((mtime_ns, size), plain text): the related-files scan
        # re-reads every document on view; see _plain_texts().
        self._plain_text_cache: dict[str, tuple[tuple[int, int], str]] = {}

    @classmethod
    def create(cls, root: Path, title: str = "Untitled Project") -> "Project":
        """Make a new project directory and seed it with an empty Draft folder."""
        root = Path(root)
        if root.suffix != ".lekhak":
            root = root.with_suffix(".lekhak")
        if root.exists() and any(root.iterdir()):
            raise FileExistsError(f"Project directory is not empty: {root}")
        root.mkdir(parents=True, exist_ok=True)
        project = cls(root)
        project.open(create=True)
        project.set_setting("title", title)
        project.add_document("Draft", kind="folder")
        # The project manifest: the root's own order, and the title.
        (root / "manifest.json").write_text(
            json.dumps({"title": title, "version": 1, "children": []},
                       ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        project.write_order_files()
        return project

    def copy_to(self, destination: Path) -> Path:
        """Save this project under a new name: a copy of the folder.

        Everything a project is — its tree, its index, its images, its
        templates — is copied as it stands, so the copy opens as a
        project of its own and is yours to change. The trash is left
        behind: a new book does not inherit what was thrown away.

        Read-only projects are copied too (the guide is): the copy is an
        ordinary project, writable, because being read-only is a fact
        about how a project was opened and not about its files.
        """
        destination = Path(destination)
        if destination.suffix != ".lekhak":
            destination = destination.with_suffix(".lekhak")
        if destination.exists() and any(destination.iterdir()):
            raise FileExistsError(
                f"Project directory is not empty: {destination}")
        destination.mkdir(parents=True, exist_ok=True)
        for path in self.root.iterdir():
            if path.name == "trash":
                continue
            if path.is_dir():
                shutil.copytree(path, destination / path.name)
            else:
                shutil.copy2(path, destination / path.name)
        return destination

    def open(self, create: bool = False) -> None:
        """Open the database, creating the schema and applying migrations.

        Migrations run inline here: new columns are added with a default,
        and one-time fixes are guarded by a settings flag so they fire
        exactly once per project. Any future schema change follows the
        same pattern — add the column, guard the backfill, never rewrite
        user content.

        A read-only project is opened as it is: no journal to write, no
        schema to create, no migration to run. The index is opened
        immutable, so a write fails loudly instead of happening quietly.
        """
        if not create and not self.db_path.exists():
            raise FileNotFoundError(f"Not a Lekhak project: {self.root}")
        if self.read_only:
            self.connection = sqlite3.connect(
                f"file:{self.db_path}?immutable=1", uri=True)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            return
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                parent_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                synopsis TEXT NOT NULL DEFAULT '',
                position INTEGER NOT NULL,
                word_target INTEGER NOT NULL DEFAULT 0,
                word_count INTEGER NOT NULL DEFAULT 0,
                kind TEXT NOT NULL DEFAULT 'scene',
                page_break TEXT NOT NULL DEFAULT 'auto',
                scene_divider TEXT NOT NULL DEFAULT '',
                doc_template TEXT NOT NULL DEFAULT '',
                path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS document_parent_order
                ON documents(parent_id, position);
            CREATE TABLE IF NOT EXISTS snapshots (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                html TEXT NOT NULL,
                synopsis TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS snapshot_document_date
                ON snapshots(document_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS revisions (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                session_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                author TEXT NOT NULL,
                created_at TEXT NOT NULL,
                sequence INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS revision_document_sequence
                ON revisions(document_id, sequence);
            CREATE TABLE IF NOT EXISTS document_metadata (
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                value_type TEXT NOT NULL DEFAULT 'text',
                options_json TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY(document_id, key)
            );
            """
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(documents)")}
        if "word_target" not in columns:
            self.connection.execute(
                "ALTER TABLE documents ADD COLUMN word_target INTEGER NOT NULL DEFAULT 0"
            )
        if "kind" not in columns:
            self.connection.execute(
                "ALTER TABLE documents ADD COLUMN kind TEXT NOT NULL DEFAULT 'scene'"
            )
        if "word_count" not in columns:
            self.connection.execute(
                "ALTER TABLE documents ADD COLUMN word_count INTEGER NOT NULL DEFAULT 0"
            )
        if "page_break" not in columns:
            self.connection.execute(
                "ALTER TABLE documents ADD COLUMN page_break TEXT NOT NULL DEFAULT 'auto'"
            )
        if "scene_divider" not in columns:
            self.connection.execute(
                "ALTER TABLE documents ADD COLUMN scene_divider TEXT NOT NULL DEFAULT ''"
            )
        for dead in ("is_templates_root", "is_template"):
            # Both flags of the binder-template systems that are gone
            # (docs/TEMPLATES.md): the old *area* flag, and the mark that
            # replaced it. A folder under ``templates/`` is a template by
            # location now, so neither flag means anything — drop it
            # rather than leave a column nothing reads. Their data needed
            # no migrating: nothing in the new model consumes it, so an
            # old project simply loses a flag it was carrying.
            if dead not in columns:
                continue
            try:
                self.connection.execute(
                    f"ALTER TABLE documents DROP COLUMN {dead}"
                )
            except sqlite3.OperationalError:
                # Old SQLite (< 3.35) cannot drop a column. Nothing
                # selects it, so a leftover one costs one dead integer
                # per row and breaks nothing.
                pass
            columns.discard(dead)
        if "doc_template" not in columns:
            # Document-level rendering template from the sidebar (2.1):
            # a named slot in the template set's documents/ directory.
            self.connection.execute(
                "ALTER TABLE documents ADD COLUMN doc_template"
                " TEXT NOT NULL DEFAULT ''"
            )
        if "path" not in columns:
            # Where the document's own file lives, relative to the project
            # root (docs/STORAGE.md). The folder tree on disk is the
            # truth; the database row carries the path so the two can be
            # joined — the row knows its metadata, the path knows its
            # place in the book.
            self.connection.execute(
                "ALTER TABLE documents ADD COLUMN path TEXT NOT NULL DEFAULT ''"
            )
        metadata_columns = {
            row[1] for row in self.connection.execute("PRAGMA table_info(document_metadata)")
        }
        if "value_type" not in metadata_columns:
            self.connection.execute(
                "ALTER TABLE document_metadata ADD COLUMN value_type TEXT NOT NULL DEFAULT 'text'"
            )
        if "options_json" not in metadata_columns:
            self.connection.execute(
                "ALTER TABLE document_metadata ADD COLUMN options_json TEXT NOT NULL DEFAULT '[]'"
            )
        # Migration: earlier versions seeded "Draft" as a chapter; it is a
        # neutral folder now. Only touch the seeded root, never user content.
        # The query cannot tell the seed from a chapter somebody *named*
        # "Draft", so it runs once and says so — otherwise it would rewrite
        # that writer's deliberate choice on every single open.
        if self.get_setting("draft_folder_migration") != "1":
            self.connection.execute(
                "UPDATE documents SET kind='folder' "
                "WHERE title='Draft' AND parent_id IS NULL AND kind='chapter'"
            )
            self.set_setting("draft_folder_migration", "1")
        if self.get_setting("word_count_backfill") != "1":
            for row in self.connection.execute("SELECT id FROM documents").fetchall():
                count = len(self.plain_text(self.read_content(row[0])).split())
                self.connection.execute(
                    "UPDATE documents SET word_count=? WHERE id=?", (count, row[0])
                )
            self.set_setting("word_count_backfill", "1")
        # The export dialog's template pick is one-shot now
        # (docs/TEMPLATES.md §2.1): it lives in that export only and is
        # never remembered, so the setting older versions stored for it
        # is retired rather than read. Deleting it here — unconditionally,
        # an absent row costs nothing — keeps an old project's leftover
        # from resurfacing as a preference nobody set.
        self.connection.execute("DELETE FROM settings WHERE key='template_set'")
        self.connection.commit()

    def close(self) -> None:
        """Release the database connection. Safe to call twice."""
        if self.connection:
            self.connection.close()
            self.connection = None

    @property
    def db(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("Project is not open")
        return self.connection

    def set_setting(self, key: str, value: str) -> None:
        """Store a project-level key (title, backup folder, view prefs).

        A read-only project keeps nothing: the guide must not be changed
        by being looked at, and its install folder must not be written to
        just because it was opened.
        """
        if self.read_only:
            return
        self.db.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.db.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        """Read a project-level key, returning ``default`` if unset."""
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_backup_folder(self, folder: Path | None) -> None:
        """Point backups at a folder outside the project.

        Backups never live inside the project: a project that is deleted
        or lost takes its backups with it otherwise, which is the one
        moment they are needed. None means the default — a folder beside
        the project.
        """
        self.set_setting("backup_folder",
                         str(Path(folder).expanduser().resolve()) if folder else "")

    def backup_directory(self) -> Path:
        """Where backups land: the configured folder, else one beside the
        project.

        Either way it is outside the project, so deleting the project
        cannot delete the backups — the one moment they matter. The
        default is a ``Backups`` folder beside the project, holding one
        folder per project.
        """
        configured = self.get_setting("backup_folder")
        if configured:
            return Path(configured).expanduser() / self.root.name
        return self.root.parent / "Backups" / self.root.name

    def add_document(self, title: str, parent_id: str | None = None,
                     kind: str = "scene") -> Document:
        """Create a document as the last child of ``parent_id``.

        Appends at the end of the sibling order (position = max + 1). The
        empty content file is written *before* the row: an orphan file for
        an id nothing references is invisible, whereas a row whose file is
        missing reads as an empty document — so if storage fails, this is
        the harmless of the two states.

        The file is a real file in the folder tree (docs/STORAGE.md): a
        folder is a directory, a document is an ``.html`` file inside it,
        and the row remembers where it went (``path``).
        """
        if kind not in DOCUMENT_KINDS:
            raise ValueError(f"Unknown document kind: {kind}")
        document_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        row = self.db.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM documents "
            "WHERE parent_id IS ?",
            (parent_id,),
        ).fetchone()
        position = int(row[0])
        if parent_id:
            # A parent that gains children is a container, whatever its
            # stored kind (docs/STORAGE.md: kind is a label, not
            # structure). On disk that means a directory.
            #
            # This happens **before** the new document's own path is
            # worked out. A leaf that becomes a container moves from
            # ``X.html`` to the directory ``X/``, so a path worked out
            # against the old name would land the child in a ghost tree
            # — ``X.html/`` beside the real ``X/`` — and the two halves
            # of one binder would never meet again.
            self._promote_to_container(parent_id)
        relative = self._new_relative_path(title, parent_id, kind)
        is_container = kind in ("folder", "chapter")
        if is_container:
            # A container is a directory on disk (docs/STORAGE.md), with
            # its own order file: the sequence of what is inside it.
            folder = self.root / relative
            folder.mkdir(parents=True, exist_ok=True)
            (folder / ".order.json").write_text(
                json.dumps({"children": []}, ensure_ascii=False) + "\n",
                encoding="utf-8")
        else:
            self.write_content(document_id, "", relative=relative)
        try:
            self.db.execute(
                "INSERT INTO documents(id,parent_id,title,position,kind,"
                "created_at,updated_at,path) VALUES(?,?,?,?,?,?,?,?)",
                (document_id, parent_id, title, position, kind, now, now,
                 relative),
            )
            self.db.commit()
        except BaseException:
            if is_container:
                shutil.rmtree(self.root / relative, ignore_errors=True)
            else:
                self.content_path(document_id, relative=relative).unlink(
                    missing_ok=True)
            raise
        # No Type written here: derived defaults stay out of storage so a
        # later heal (book settings promoting a folder) is never shadowed
        # by a stale seed. The Type row shows type_of() either way, and
        # an explicit choice is stored by the inspector (docs/MODEL.md).
        # The new item is last, and the folder's order file says so.
        self._write_order_for(parent_id)
        return Document(document_id, parent_id, title, "", position, 0, kind, 0,
                        path=relative)

    def _promote_to_container(self, document_id: str) -> None:
        """Make a document that has gained children a folder on disk.

        Kind is a label, not structure (docs/STORAGE.md): a scene may
        hold scenes. On disk a container is a directory, so the leaf's
        file becomes the directory's own ``index.html`` — its prose
        travels with it, nothing is lost.

        A directory of that name may already be there: a writer's own
        folder, or a second document that wanted the same name. The
        leaf's words move into it as its index when it has no index of
        its own; when it already has one, the leaf's file is left alone
        on disk — nothing is destroyed — and the row points at the
        folder, which is where the children live.
        """
        row = self.db.execute(
            "SELECT path FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        if row is None or not row[0]:
            return
        relative = str(row[0])
        if not relative.lower().endswith(".html"):
            return
        leaf = self.root / relative
        folder = leaf.with_suffix("")
        if not folder.exists():
            if not leaf.exists():
                return
            folder.mkdir(parents=True, exist_ok=True)
            leaf.rename(folder / "index.html")
        elif leaf.exists() and not (folder / "index.html").exists():
            # The directory is there and has no index of its own: the
            # leaf's prose becomes it.
            leaf.rename(folder / "index.html")
        if not (folder / ".order.json").is_file():
            (folder / ".order.json").write_text(
                json.dumps({"children": []}, ensure_ascii=False) + "\n",
                encoding="utf-8")
        self.db.execute(
            "UPDATE documents SET path=? WHERE id=?",
            (str(folder.relative_to(self.root)), document_id),
        )
        self.db.commit()

    def _container_dir(self, document_id: str) -> Path:
        """The directory a document's children live in.

        A container is a directory (docs/STORAGE.md). A leaf that gains
        children is promoted to one before anything is added to it, so
        by then its path *is* the directory; the fallback covers a leaf
        whose file has gone missing, so the children still land
        somewhere sensible.
        """
        row = self.db.execute(
            "SELECT path FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        relative = str(row[0] or "") if row is not None else ""
        if not relative:
            return self.root
        path = self.root / relative
        if path.is_dir():
            return path
        if path.suffix.lower() == ".html":
            sibling = path.with_suffix("")
            if sibling.is_dir():
                return sibling
        return path

    def _new_relative_path(self, title: str, parent_id: str | None,
                           kind: str) -> str:
        """Where a new document's file goes, relative to the project root.

        A container is a directory named after its title; a leaf is an
        ``.html`` file inside its parent folder. "Container" means the
        kind that holds children — a folder, or a chapter, which is a
        container whatever its stored role (docs/STORAGE.md). The name is
        made unique among its siblings, so two documents called
        "Prologue" both live.
        """
        base = self._container_dir(parent_id) if parent_id else self.root
        stem = clean_name(title)
        is_container = kind in ("folder", "chapter")
        name = stem if is_container else f"{stem}.html"
        taken = {entry.name for entry in base.iterdir()} if base.is_dir() else set()
        candidate = name
        counter = 1
        while candidate.casefold() in {t.casefold() for t in taken}:
            suffix = f"-{counter}" if is_container else f"-{counter}.html"
            candidate = stem + suffix
            counter += 1
        parent_path = base.relative_to(self.root)
        if str(parent_path) in (".", ""):
            return candidate
        return str(parent_path / candidate)

    def documents(self) -> list[Document]:
        """Every document in binder order: parents before children, siblings by position.

        This is the canonical ordering the whole app follows — the binder
        tree, the outliner, export and search all iterate this list rather
        than querying the database themselves.
        """
        rows = self.db.execute(
            "SELECT id,parent_id,title,synopsis,position,word_target,kind,"
            "word_count,page_break,scene_divider,doc_template,path"
            " FROM documents"
        ).fetchall()
        by_parent: dict[str | None, list[Document]] = {}
        for row in rows:
            document = Document(**dict(row))
            by_parent.setdefault(document.parent_id, []).append(document)
        ordered: list[Document] = []

        def visit(parent_id: str | None) -> None:
            for document in sorted(by_parent.get(parent_id, []),
                                   key=lambda d: d.position):
                ordered.append(document)
                visit(document.id)

        visit(None)
        return ordered

    def update_document(self, document_id: str, *, title: str | None = None,
                        synopsis: str | None = None,
                        word_target: int | None = None,
                        page_break: str | None = None,
                        scene_divider: str | None = None,
                        doc_template: str | None = None) -> None:
        """Set the given properties on a document; None means leave alone.

        Title, synopsis, word target, page start and the document-level
        template only — prose is never touched here, it goes through
        write_content().

        A read-only project keeps what it has: the guide is the software's
        manual, and looking at it must not change a word of it.
        """
        if self.read_only:
            return
        fields: list[str] = []
        values: list[str] = []
        if title is not None:
            fields.append("title=?")
            values.append(title)
        if synopsis is not None:
            fields.append("synopsis=?")
            values.append(synopsis)
        if word_target is not None:
            fields.append("word_target=?")
            values.append(str(max(0, word_target)))
        if page_break is not None:
            if page_break not in PAGE_BREAK_VALUES:
                raise ValueError(f"Unknown page start: {page_break}")
            fields.append("page_break=?")
            values.append(page_break)
        if scene_divider is not None:
            fields.append("scene_divider=?")
            values.append(scene_divider)
        if doc_template is not None:
            fields.append("doc_template=?")
            values.append(doc_template)
        if not fields:
            return
        fields.append("updated_at=?")
        values.append(datetime.now(timezone.utc).isoformat())
        values.append(document_id)
        self.db.execute(
            f"UPDATE documents SET {', '.join(fields)} WHERE id=?", values
        )
        self.db.commit()
        if title is not None:
            # The title is the name of the thing, so the file is renamed
            # with it (docs/STORAGE.md): the folder tree the writer sees
            # in a file manager says what the binder says.
            self._rename_on_disk(document_id, title)
        if any(field is not None for field in
               (synopsis, word_target, page_break, scene_divider,
                doc_template)):
            # The file carries the metadata too, so it stays in step with
            # what the inspector shows.
            self._refresh_metadata_on_disk(document_id)

    def _refresh_metadata_on_disk(self, document_id: str) -> None:
        """Rewrite a document's file so its head carries the current
        metadata. The prose is untouched: only the head is rebuilt.
        """
        path = self.content_path(document_id)
        if not path.is_file():
            return
        text = path.read_text(encoding="utf-8")
        _metadata, prose = split_document(text)
        try:
            self.write_content(document_id, prose)
        except OSError:
            pass

    def identity_drift(self) -> list[tuple[str, str, str]]:
        """Title/identity divergence between file head, project and path.

        Returns ``(document_id, file_title, project_title)`` when the
        file's head states a title different from the project's record.
        Filename/path mismatches are handled by sync/reconcile; this
        reports identity disagreements the writer must resolve, never
        silently fixed.
        """
        from .disk_doc import read_document

        drift: list[tuple[str, str, str]] = []
        for document in self.documents():
            path = self.content_path(document.id)
            if not path.is_file():
                continue
            try:
                head, _prose = read_document(path)
            except OSError:
                continue
            file_title = head.get("title")
            if not isinstance(file_title, str):
                continue
            file_title = file_title.strip()
            proj_title = (document.title or "").strip()
            if file_title and file_title != proj_title:
                drift.append((document.id, file_title, proj_title))
        return drift

    def metadata_drift(self) -> list[tuple[str, str, str, str, str]]:
        """Fields a file names differently from the project's own record.

        The same contract as the manifest and the .order.json files
        (docs/STORAGE.md): the writer's fields are kept in the database
        *and* written into each document's head, so a file edited outside
        Lekhak can disagree with the project. A disagreement is reported
        and the writer says which side is right — never silently fixed.

        Only a *value* the file states counts. A key the head does not
        mention at all is not a disagreement: it is a head written before
        the field existed, and the next save writes it. Entries are
        ``(document_id, title, key, file_value, project_value)``.
        """
        from .disk_doc import read_document

        drift: list[tuple[str, str, str, str, str]] = []
        for document in self.documents():
            path = self.content_path(document.id)
            if not path.is_file():
                continue
            try:
                head, _prose = read_document(path)
            except OSError:
                continue
            stated = head.get("metadata")
            if not isinstance(stated, dict):
                continue
            held = self.metadata(document.id)
            for key, value in stated.items():
                if key not in held or str(held[key]) == str(value):
                    continue
                drift.append((document.id, document.title, str(key),
                              str(value), str(held[key])))
        return drift

    def replace_wiki_link_title(self, old_title: str, new_title: str) -> int:
        """Project-wide opt-in find/replace for hardcoded wiki links.

        Rewrites ``[[Old Title]]`` (and ``[[Old Title|...]]`` if present)
        text in every document's content. Returns number of documents
        changed. Automated/id-based links are not affected.
        """
        if not old_title or not new_title or old_title == new_title:
            return 0
        import re as _re
        changed = 0
        pattern = _re.compile(
            r"\[\[" + _re.escape(old_title) + r"(\|[^\]]*)?\]\]")
        for document in self.documents():
            content = self.read_content(document.id)
            if not content:
                continue
            new_content, count = pattern.subn(
                lambda m: f"[[{new_title}{m.group(1) or ''}]]", content)
            if count:
                self.write_content(document.id, new_content)
                changed += 1
        return changed

    def apply_file_metadata(self, document_id: str, key: str) -> bool:
        """Take the file's word for one field: the head's value wins.

        The other answer — the project is right — is the ordinary save:
        ``set_metadata`` writes the head from the database. Field types
        and option lists are the project's, so the value changes and
        nothing else does.
        """
        from .disk_doc import read_document

        path = self.content_path(document_id)
        if not path.is_file():
            return False
        head, _prose = read_document(path)
        stated = head.get("metadata")
        if not isinstance(stated, dict) or key not in stated:
            return False
        entries = self.metadata_entries(document_id)
        self.set_metadata(
            document_id,
            {name: value for name, (value, _t, _o) in entries.items()}
            | {key: str(stated[key])},
            {name: value_type for name, (_v, value_type, _o) in entries.items()},
            {name: options for name, (_v, _t, options) in entries.items()})
        return True

    def _rename_on_disk(self, document_id: str, title: str) -> None:
        """Rename a document's file (and folder) to match its new title.

        The name on disk is the title, so the two never drift apart. A
        name already taken in the same folder gets a number, and the
        database is told the new path.
        """
        row = self.db.execute(
            "SELECT path FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        if row is None or not row[0]:
            return
        relative = str(row[0])
        current = self.root / relative
        if not current.exists():
            return
        parent = current.parent
        stem = clean_name(title)
        if current.is_dir():
            wanted = parent / stem
        else:
            wanted = parent / f"{stem}.html"
        if wanted == current:
            return
        if wanted.exists():
            return          # a sibling already has that name; leave it be
        try:
            current.rename(wanted)
        except OSError:
            return
        # A document's lock marker travels with it.
        marker = self.lock_marker(document_id)
        if marker is not None and marker.exists():
            try:
                marker.rename(wanted.with_name("." + wanted.name + LOCK_FILE))
            except OSError:
                pass
        new_relative = str(wanted.relative_to(self.root))
        # The rename carries the whole subtree, so every row beneath it is
        # repointed; then the folder that held it re-writes its order file
        # (the name it lists has changed), and the file's head is brought
        # up to the new title, because the file is the truth.
        self._repoint_paths(relative, new_relative)
        parent_id = self.parent_of(document_id)
        self._write_order_for(parent_id)
        self._refresh_metadata_on_disk(document_id)

    def _repoint_paths(self, old_relative: str, new_relative: str) -> None:
        """Move every row under a renamed or moved path to its new one.

        A directory rename or move carries its whole subtree, so the rows
        beneath it would otherwise keep pointing at files that are no
        longer there — each one a vanished document, and the binder grows
        a ghost beside the real file that replaced it.
        """
        prefix = old_relative + "/"
        updates: list[tuple[str, str]] = []
        for item in self.db.execute("SELECT id, path FROM documents").fetchall():
            if not item[1]:
                continue
            if item[1] == old_relative:
                updates.append((new_relative, item[0]))
            elif item[1].startswith(prefix):
                updates.append((new_relative + "/" + item[1][len(prefix):],
                                item[0]))
        with self.db:
            self.db.executemany(
                "UPDATE documents SET path=? WHERE id=?", updates)

    def index_conflicts(self) -> list[str]:
        """Folders whose ``index.html`` says it belongs to another document.

        The rule: a folder's ``index.html`` is the container's own words
        (docs/STORAGE.md). When the file carries a head naming a
        *different* document — a writer made a document called "index"
        before the name was reserved — the two meanings collide.

        Nothing is changed here, and nothing is changed anywhere else:
        the file is read as the container's page, the writer is told, and
        the file is left exactly as it is. Rewriting it would be the
        app's guess at the writer's intent, and a guess that is wrong
        loses their words.
        """
        problems: list[str] = []
        for document in self.documents():
            if not document.path:
                continue
            folder = self.root / document.path
            if not folder.is_dir():
                continue
            page = folder / "index.html"
            if not page.is_file():
                continue
            try:
                metadata, _prose = split_document(
                    page.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            title = str(metadata.get("title") or "").strip()
            if not title:
                continue        # a page with no name of its own: no conflict
            if title.casefold() == document.title.strip().casefold():
                continue        # the container's own page, as it should be
            problems.append(
                f"{document.path}/index.html — the file calls itself "
                f"“{title}”, but a folder's index.html is the folder's own "
                f"words (“{document.title}”).")
        return problems

    def _write_order_for(self, parent_id: str | None) -> None:
        """Rewrite one folder's order file — the manifest, for the root.

        The order file is the sequence of a folder's children by name
        (docs/STORAGE.md), so every structural change rewrites the one
        folder it touched rather than the whole project. Doing this here,
        where the rows change, is what keeps the three records — the
        database, the folder's ``.order.json``, the root's
        ``manifest.json`` — in step even when a caller forgets.
        """
        if parent_id is None:
            folder = self.root
        else:
            row = self.db.execute(
                "SELECT path FROM documents WHERE id=?", (parent_id,)
            ).fetchone()
            if row is None or not row[0]:
                return
            folder = self.root / str(row[0])
            if not folder.is_dir():
                return
        names = [
            Path(row[0]).name
            for row in self.db.execute(
                "SELECT path FROM documents WHERE parent_id IS ? "
                "ORDER BY position", (parent_id,))
            if row[0]
        ]
        if parent_id is not None:
            if (folder / "index.html").is_file():
                # A folder's own prose leads its order: it is the parent's
                # page, not one of the children, so it takes no position
                # of its own (docs/STORAGE.md).
                names.insert(0, "index.html")
            (folder / ".order.json").write_text(
                json.dumps({"children": names}, ensure_ascii=False, indent=2)
                + "\n", encoding="utf-8")
            return
        # The root's own order lives in the project manifest, beside the
        # title and format version it must not lose.
        manifest = self.root / "manifest.json"
        existing: dict = {}
        if manifest.is_file():
            try:
                loaded = json.loads(manifest.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    existing = loaded
            except (OSError, ValueError):
                existing = {}
        existing.setdefault("title", self.get_setting("title") or self.root.stem)
        existing.pop("tree", None)     # an older shape; "children" is the one
        existing["children"] = names
        manifest.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

    def reorder(self, document_id: str, parent_id: str | None, position: int) -> bool:
        """Place a document at a position under a parent, file and row together.

        Returns False and writes nothing if the file cannot go there —
        a name already taken in the destination folder, most often, and
        always for a read-only project: the guide is not rearranged by
        being looked at.
        The folder tree is the truth (docs/STORAGE.md), so a parent
        change means the file moves first and the index follows; the
        two are never left disagreeing.
        """
        if self.read_only:
            return False
        if parent_id == document_id or self._is_descendant(parent_id, document_id):
            raise ValueError("A document cannot contain itself")
        row = self.db.execute(
            "SELECT parent_id FROM documents WHERE id=?", (document_id,)
        ).fetchone()
        if row is None:
            return True
        old_parent = row[0]
        old_rel = None
        if row[0] != parent_id:
            path_row = self.db.execute(
                "SELECT path FROM documents WHERE id=?", (document_id,)
            ).fetchone()
            old_rel = str(path_row[0]) if path_row and path_row[0] else None
        if row[0] != parent_id and not self._move_file(document_id, parent_id):
            return False
        # The order of these three UPDATEs is load-bearing. First close the
        # gap the moved item leaves behind (decrement everything above it
        # under its old parent), then open a slot at the destination
        # (increment everything at or after it under the new parent), then
        # place the item. Reordering them leaves duplicate or gapped
        # positions, which would silently break sibling order on reload.
        with self.db:
            self.db.execute(
                "UPDATE documents SET position=position-1 WHERE parent_id IS "
                "(SELECT parent_id FROM documents WHERE id=?) AND position > "
                "(SELECT position FROM documents WHERE id=?)",
                (document_id, document_id),
            )
            self.db.execute(
                "UPDATE documents SET position=position+1 WHERE parent_id IS ? AND position>=?",
                (parent_id, position),
            )
            self.db.execute(
                "UPDATE documents SET parent_id=?,position=?,updated_at=? WHERE id=?",
                (parent_id, position, datetime.now(timezone.utc).isoformat(), document_id),
            )
        # Both folders the move touched write their sequence, so a drag or
        # a move is on disk before the next open (docs/STORAGE.md).
        self._write_order_for(old_parent)
        if parent_id != old_parent:
            self._write_order_for(parent_id)
        # A move out of trash (a drag-restore) leaves the vacated mirror
        # parents behind. Prune them up to the trash root — but only
        # now, after the parent update, so the prune never cascades into
        # the just-moved row. The order files follow the rows.
        if old_rel and (old_rel == TRASH_FOLDER
                        or old_rel.startswith(TRASH_FOLDER + "/")):
            self._prune_empty_trash_parents(Path(old_rel).parent)
            self.write_order_files()
        return True

    def _move_file(self, document_id: str, new_parent_id: str | None) -> bool:
        """Move a document's file under a new parent on disk.

        The row and every row beneath it are given the path the move
        gives them, so the index keeps pointing at the files it was
        indexing. False means nothing moved and the caller must leave
        the database alone: the destination is unknown, or it already
        holds that name.
        """
        row = self.db.execute(
            "SELECT path FROM documents WHERE id=?", (document_id,)
        ).fetchone()
        if row is None or not row[0]:
            return True                      # no file of its own to move
        old_relative = str(row[0])
        current = self.root / old_relative
        if not current.exists():
            return True                      # a row whose file has gone
        if new_parent_id is None:
            destination = self.root
        else:
            parent = self.db.execute(
                "SELECT path FROM documents WHERE id=?", (new_parent_id,)
            ).fetchone()
            if parent is None or not parent[0]:
                return False                 # the destination is not known
            destination = self.root / str(parent[0])
        if not destination.is_dir():
            return False
        wanted = destination / current.name
        if wanted == current:
            return True
        if wanted.exists():
            return False                     # that name is taken there
        marker = self.lock_marker(document_id)
        try:
            shutil.move(str(current), str(wanted))
        except OSError:
            return False
        # A container's lock marker rides inside the folder and has
        # already come along; a document's sits beside its file.
        if marker is not None and marker.exists():
            try:
                marker.rename(wanted.with_name("." + wanted.name + LOCK_FILE))
            except OSError:
                pass
        new_relative = str(wanted.relative_to(self.root))
        self._repoint_paths(old_relative, new_relative)
        return True

    def parent_of(self, document_id: str) -> str | None:
        """The id of this document's parent, or None if it is top-level."""
        row = self.db.execute(
            "SELECT parent_id FROM documents WHERE id=?", (document_id,)
        ).fetchone()
        return row[0] if row else None

    def _is_descendant(self, candidate: str | None, ancestor: str) -> bool:
        current = candidate
        while current:
            if current == ancestor:
                return True
            row = self.db.execute(
                "SELECT parent_id FROM documents WHERE id=?", (current,)
            ).fetchone()
            current = row[0] if row else None
        return False

    def delete_document(self, document_id: str) -> None:
        """Delete a document, its rows and its files, together.

        First delete trashes it: ``trash/<path>`` mirrors its original
        path, rows stay so the trash folder itself is a normal binder
        folder you can open, drag to restore, or delete again. Second
        delete from trash erases it and its row — and prunes empty
        parent folders up to the trash root, so no empty clutter stays.

        If a file turns out to be undeletable (a permission, a
        read-only mount, a lock), nothing changes: the rows and the
        files stay where they were and :class:`DeleteError` says so.
        """
        row0 = self.db.execute(
            "SELECT path FROM documents WHERE id=?", (document_id,)
        ).fetchone()
        path0 = str(row0[0]) if row0 and row0[0] else ""
        # Legacy fallback: a row without a path lives at documents/<id>.html.
        if not path0:
            fallback = self.content_path(document_id)
            if fallback.exists():
                path0 = str(fallback.relative_to(self.root))
        if path0 and (path0 == TRASH_FOLDER or
                      path0.startswith(TRASH_FOLDER + "/")):
            return self._permanent_delete(document_id)

        descendants = self._descendant_ids(document_id)
        rows: list[tuple] = []
        for item_id in descendants:
            row = self.db.execute(
                "SELECT id,parent_id,title,synopsis,position,word_target,"
                "kind,word_count,page_break,scene_divider,doc_template,path,"
                "created_at,updated_at FROM documents WHERE id = ?",
                (item_id,),
            ).fetchone()
            if row is not None:
                rows.append(tuple(row))
        if not rows:
            return

        top = next((r for r in rows if r[0] == document_id), None)
        top_rel = str(top[11]) if top and top[11] else None
        top_path = self.root / top_rel if top_rel else None
        if top_rel is None:
            # No tree path: restore the flat-pool fallback file.
            fallback = self.content_path(document_id)
            if fallback.exists():
                top_rel = str(fallback.relative_to(self.root))
                top_path = fallback

        old_parent_id = top[1] if top else None  # parent_id column

        # Move the top-level item on disk first, keeping every row.
        failures: list[tuple[Path, str]] = []
        moved: list[tuple[Path, Path]] = []
        if top_path and top_path.exists():
            try:
                relative = top_path.relative_to(self.root)
                destination = self.root / TRASH_FOLDER / relative
                if destination.exists():
                    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
                    destination = destination.with_name(
                        f"{destination.name}.{stamp}")
                    # Re-derive the final relative path (stamp changes it).
                    top_rel = str(destination.relative_to(self.root))
                destination.parent.mkdir(parents=True, exist_ok=True)
                if top_path.is_dir():
                    shutil.move(str(top_path), str(destination))
                else:
                    shutil.move(str(top_path), str(destination))
                moved.append((top_path, destination))
            except OSError as error:
                failures.append((top_path, str(error)))
        if failures:
            for original, trashed in moved:
                try:
                    shutil.move(str(trashed), str(original))
                except OSError:
                    pass
            raise DeleteError(failures)

        trash_top_rel = str(top_rel) if top_rel else ""
        trash_ids = {document_id}
        if top_rel:
            trash_path = f"{TRASH_FOLDER}/{top_rel}"
            # Ensure trash parents mirror the original parent chain.
            parent_rel = str(Path(top_rel).parent)
            mirror_parent_id = None
            if parent_rel not in (".", ""):
                mirror_parent_id = self._ensure_folder_under_trash(
                    *Path(parent_rel).parts)
            else:
                mirror_parent_id = self._ensure_trash_folder()
            if moved:
                trash_path = str(moved[0][1].relative_to(self.root))
            with self.db:
                # Re-point the whole subtree, preserving sibling order
                # inside it; only the top item changes parent.
                for row in rows:
                    old_path = row[11]
                    if old_path:
                        if old_path == top_rel:
                            new_path = trash_path
                        elif old_path.startswith(top_rel + "/"):
                            new_path = (
                                trash_path + old_path[len(top_rel):])
                        else:
                            new_path = old_path
                        self.db.execute(
                            "UPDATE documents SET path=? WHERE id=?",
                            (new_path, row[0]))
                self.db.execute(
                    "UPDATE documents SET parent_id=? WHERE id=?",
                    (mirror_parent_id, document_id))
        # Records of the source folder and trash mirrors stay in step.
        self._write_order_for(old_parent_id)
        if top_rel:
            self._write_order_for(mirror_parent_id)
        self.write_order_files()

    def _ensure_trash_folder(self) -> str | None:
        """The id of the trash root folder, created on first use."""
        row = self.db.execute(
            "SELECT id FROM documents WHERE path=?", (TRASH_FOLDER,)
        ).fetchone()
        if row is not None:
            return str(row[0])
        folder_path = self.root / TRASH_FOLDER
        folder_path.mkdir(parents=True, exist_ok=True)
        (folder_path / ".order.json").write_text(
            json.dumps({"children": []}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        document_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        row = self.db.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM documents "
        ).fetchone()
        with self.db:
            self.db.execute(
                "INSERT INTO documents(id,parent_id,title,position,kind,"
                "created_at,updated_at,path) VALUES(?,?,?,?,?,?,?,?)",
                (document_id, None, TRASH_FOLDER,
                 int(row[0]), "folder", now, now, TRASH_FOLDER),
            )
        self._write_order_for(None)
        return document_id

    def _ensure_folder_under_trash(self, *parts: str) -> str | None:
        """Folder id mirroring ``parts`` under trash/ (created as needed)."""
        parent_id = self._ensure_trash_folder()
        relative = TRASH_FOLDER
        for part in parts:
            relative = f"{relative}/{part}"
            row = self.db.execute(
                "SELECT id FROM documents WHERE path=?", (relative,)
            ).fetchone()
            if row is not None:
                parent_id = str(row[0])
                continue
            folder_path = self.root / relative
            folder_path.mkdir(parents=True, exist_ok=True)
            if not (folder_path / ".order.json").exists():
                (folder_path / ".order.json").write_text(
                    json.dumps({"children": []}, ensure_ascii=False) + "\n",
                    encoding="utf-8")
            position_row = self.db.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM documents "
                "WHERE parent_id IS ?", (parent_id,)
            ).fetchone()
            document_id = uuid.uuid4().hex
            now = datetime.now(timezone.utc).isoformat()
            with self.db:
                self.db.execute(
                    "INSERT INTO documents(id,parent_id,title,position,kind,"
                    "created_at,updated_at,path) VALUES(?,?,?,?,?,?,?,?)",
                    (document_id, parent_id, part,
                     int(position_row[0]), "folder", now, now, relative),
                )
            parent_id = document_id
        return parent_id

    def _permanent_delete(self, document_id: str) -> None:
        """Second delete from trash: erase files and rows, prune empties."""
        descendants = self._descendant_ids(document_id)
        top_row = self.db.execute(
            "SELECT path FROM documents WHERE id=?", (document_id,)
        ).fetchone()
        top_rel = str(top_row[0]) if top_row and top_row[0] else ""
        saved_rows: list[tuple] = []
        descendant_rows: list[tuple] = []
        places: list[Path] = []
        for item_id in descendants:
            rows = self.db.execute(
                "SELECT id,parent_id,title,synopsis,position,word_target,"
                "kind,word_count,page_break,scene_divider,doc_template,path,"
                "created_at,updated_at FROM documents WHERE id = ?",
                (item_id,),
            ).fetchone()
            if rows is None:
                continue
            saved_rows.append(tuple(rows))
            descendant_rows.append((item_id, str(rows[0]) if False else str(rows[11] or "")))
        # Top-level only: erasing a folder takes its contents with it.
        for item_id, rel in descendant_rows:
            if rel:
                path = self.root / rel
                if path.exists() and not any(
                    path != other and other in path.parents
                    for other in places
                ):
                    places.append(path)
        with self.db:
            self.db.execute("DELETE FROM documents WHERE id=?", (document_id,))
        failures: list[tuple[Path, str]] = []
        for path in places:
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
            except OSError as error:
                failures.append((path, str(error)))
        if failures:
            self._restore_rows(saved_rows)
            raise DeleteError(failures)
        self._prune_empty_trash_parents(
            (Path(top_rel).parent if top_rel else Path("")))
        self.write_order_files()

    def _prune_empty_trash_parents(self, parent_rel: Path) -> None:
        """Delete empty folders up the trash tree to the trash root."""
        trash_root = self.root / TRASH_FOLDER
        current = self.root / parent_rel if str(parent_rel) else trash_root
        if trash_root not in current.parents and current != trash_root:
            return
        while current != trash_root and trash_root in current.parents:
            try:
                names = [entry.name for entry in current.iterdir()
                         if not entry.name.startswith(".")]
            except OSError:
                return
            if names:
                return  # Not empty: real content still lives here.
            relative = str(current.relative_to(self.root))
            # Its .order.json is binder metadata, not content.
            order_file = current / ".order.json"
            try:
                order_file.unlink(missing_ok=True)
            except OSError:
                pass
            row = self.db.execute(
                "SELECT id FROM documents WHERE path=?", (relative,)
            ).fetchone()
            with self.db:
                if row is not None:
                    self.db.execute(
                        "DELETE FROM documents WHERE id=?", (row[0],))
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent

    def trash_dir(self) -> Path:
        """Where deleted files rest, mirroring their original paths."""
        return self.root / TRASH_FOLDER

    def list_trash(self) -> list[Path]:
        """Relative paths in trash, restored or not."""
        root = self.trash_dir()
        if not root.is_dir():
            return []
        # Bookkeeping files are not trash: an emptied trash still holds
        # its own .order.json, which is nothing a writer could restore.
        return sorted(path.relative_to(root) for path in root.rglob("*")
                      if path.is_file()
                      and not path.name.endswith(".deleted.json")
                      and path.name != ".order.json")

    def restore_from_trash(self, relative: str | Path) -> bool:
        """Move a trashed file/folder back to its original path.

        Returns True when something was restored. Rows remembered
        beside the trashed files are re-inserted along with it.
        """
        relative = Path(relative)
        trashed = self.trash_dir() / relative
        if not trashed.exists():
            return False
        original = self.root / relative
        original.parent.mkdir(parents=True, exist_ok=True)
        if original.exists():
            stamp = datetime.now().strftime("%Y%m%d%H%M%S")
            original = original.with_name(f"{original.name}.{stamp}")
        shutil.move(str(trashed), str(original))
        # Re-insert the rows beside the restored files.
        meta_path = self.trash_dir() / (str(relative) + ".deleted.json")
        if meta_path.is_file():
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                rows = [tuple(row) for row in data.get("rows", [])]
                if rows:
                    self._restore_rows(rows)  # type: ignore[arg-type]
                meta_path.unlink()
            except (OSError, ValueError):
                pass
        self.write_order_files()
        return True

    def empty_trash(self) -> None:
        """Permanently delete everything in trash.

        The files go, and so do their rows: an emptied trash must not
        leave ghosts in the binder, pointing at files that are no
        longer there. The trash folder itself stays — it is a normal
        folder in the tree, and it is empty now.
        """
        root = self.trash_dir()
        rows = self.db.execute(
            "SELECT id FROM documents WHERE path LIKE ?",
            (TRASH_FOLDER + "/%",),
        ).fetchall()
        with self.db:
            for row in rows:
                self.db.execute("DELETE FROM documents WHERE id=?", (row[0],))
        if root.is_dir():
            shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        (root / ".order.json").write_text(
            json.dumps({"children": []}, ensure_ascii=False) + "\n",
            encoding="utf-8")

    def _restore_rows(self, rows: list[tuple]) -> None:
        """Put deleted rows back exactly as they were."""
        with self.db:
            for row in rows:
                self.db.execute(
                    "INSERT OR REPLACE INTO documents(id,parent_id,title,"
                    "synopsis,position,word_target,kind,word_count,"
                    "page_break,scene_divider,doc_template,path,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    row,
                )

    def write_order_files(self) -> None:
        """Write every folder's ``.order.json`` from the binder order.

        A read-only project is left exactly as it is: its order files are
        already right, and the install folder is not ours to write to.

        The order file is the sequence of a folder's children, by name
        (docs/STORAGE.md). It travels with the folder, so a copy of the
        folder keeps its order — and if the database is ever lost, the
        tree can still be read back exactly as it was.
        """
        if self.read_only:
            return
        for document in self.documents():
            if document.path and (self.root / document.path).is_dir():
                self._write_order_for(document.id)
        self._write_order_for(None)

    def next_sibling(self, document_id: str) -> Document | None:
        """The document following this one under the same parent, if any."""
        row = self.db.execute(
            "SELECT parent_id,position FROM documents WHERE id=?", (document_id,)
        ).fetchone()
        if row is None:
            return None
        sibling = self.db.execute(
            "SELECT id,parent_id,title,synopsis,position,word_target,kind,"
            "word_count,page_break,scene_divider,doc_template,path"
            " FROM documents WHERE parent_id IS ? AND position>? "
            "ORDER BY position LIMIT 1", (row[0], row[1]),
        ).fetchone()
        return Document(**dict(sibling)) if sibling else None

    def split_document(self, document_id: str, before_html: str,
                       after_html: str, *, new_title: str | None = None) -> Document:
        """Split a section into two at the cursor.

        The caller extracts the two HTML halves (Qt document fragments keep
        the formatting); this method snapshots the original, inserts a
        sibling right after it and writes both halves.
        """
        row = self.db.execute(
            "SELECT parent_id,title,position,kind FROM documents WHERE id=?",
            (document_id,),
        ).fetchone()
        if row is None:
            raise KeyError(document_id)
        if not self.plain_text(before_html):
            raise ValueError("Nothing before the cursor to keep.")
        if not self.plain_text(after_html):
            raise ValueError("Nothing after the cursor to split off.")
        parent_id, title, position, kind = row
        self.create_snapshot(document_id, "Before split")

        if new_title is None:
            taken = {
                r[0] for r in self.db.execute(
                    "SELECT title FROM documents WHERE parent_id IS ?",
                    (parent_id,),
                ).fetchall()
            }
            number = 2
            while f"{title} ({number})" in taken:
                number += 1
            new_title = f"{title} ({number})"

        new_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        # Only one of these three steps can fail, so the order decides what
        # a failure leaves behind: the new file first (an orphan is
        # invisible), then the rows, then the original's file last — which
        # is what keeps its previous text intact until everything else has
        # landed. Worst case is duplicated text, never lost text, and the
        # snapshot above still holds the pre-split version.
        self.write_content(new_id, after_html)
        try:
            with self.db:
                self.db.execute(
                    "UPDATE documents SET position=position+1 "
                    "WHERE parent_id IS ? AND position>?",
                    (parent_id, position),
                )
                self.db.execute(
                    "INSERT INTO documents(id,parent_id,title,position,kind,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (new_id, parent_id, new_title, position + 1, kind, now, now),
                )
        except BaseException:
            self.content_path(new_id).unlink(missing_ok=True)
            raise
        self.write_content(document_id, before_html)
        return Document(new_id, parent_id, new_title, "", position + 1, 0, kind, 0)

    def merge_documents(self, first_id: str, second_id: str) -> Document:
        """Append the next sibling's content onto this document and delete it.

        `second` must immediately follow `first` under the same parent and
        must have no sub-documents. `first` is snapshotted before the merge;
        `second`'s snapshots are re-homed under `first` so its history
        survives. `second`'s metadata is dropped (the row cascade removes it).
        """
        if first_id == second_id:
            raise ValueError("A document cannot merge with itself.")
        for doc_id in (first_id, second_id):
            if not self.db.execute(
                "SELECT 1 FROM documents WHERE id=?", (doc_id,)
            ).fetchone():
                raise KeyError(doc_id)
        following = self.next_sibling(first_id)
        if following is None or following.id != second_id:
            raise ValueError(
                "Merge works with the immediately following sibling only."
            )
        if self.db.execute(
            "SELECT 1 FROM documents WHERE parent_id=? LIMIT 1", (second_id,)
        ).fetchone():
            raise ValueError(
                "The following section contains sub-documents; move them out first."
            )
        self.create_snapshot(first_id, "Before merge")
        combined = self._join_html(self.read_content(first_id),
                                   self.read_content(second_id))
        # The combined text lands before anything else moves: if it cannot
        # be written, the merge simply did not happen. The history is
        # re-homed before the row goes, because deleting the row cascades
        # and would take the second document's snapshots with it.
        self.write_content(first_id, combined)
        with self.db:
            self.db.execute(
                "UPDATE snapshots SET document_id=? WHERE document_id=?",
                (first_id, second_id),
            )
        self.delete_document(second_id)
        row = self.db.execute(
            "SELECT id,parent_id,title,synopsis,position,word_target,kind,"
            "word_count,page_break,scene_divider,doc_template,path"
            " FROM documents WHERE id=?", (first_id,),
        ).fetchone()
        return Document(**dict(row))

    @staticmethod
    def _body_content(html: str) -> str:
        match = re.search(r"<body[^>]*>(.*)</body>", html, flags=re.S | re.I)
        return match.group(1) if match else html

    @classmethod
    def _join_html(cls, first: str, second: str) -> str:
        body = cls._body_content(first) + cls._body_content(second)
        if re.search(r"<body[^>]*>.*</body>", first, flags=re.S | re.I):
            return re.sub(
                r"(<body[^>]*>).*(</body>)",
                lambda m: m.group(1) + body + m.group(2),
                first, count=1, flags=re.S | re.I,
            )
        return f"<html><body>{body}</body></html>"

    def _descendant_ids(self, document_id: str) -> list[str]:
        result = [document_id]
        for row in self.db.execute(
            "SELECT id FROM documents WHERE parent_id=?", (document_id,)
        ):
            result.extend(self._descendant_ids(row[0]))
        return result

    def content_path(self, document_id: str, *, relative: str | None = None) -> Path:
        """Where a document's prose lives on disk.

        A document's file is a real file in the folder tree
        (docs/STORAGE.md): the row's ``path`` says where. A row without
        one — an old project, or a document created before the tree
        existed — falls back to ``documents/<id>.html``, where the prose
        has always lived.

        A container's own prose (a chapter's title page) is an
        ``index.html`` inside its folder, the convention the tree writer
        uses (docs/STORAGE.md).
        """
        if relative is None:
            row = self.db.execute(
                "SELECT path FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            relative = str(row[0]) if row is not None and row[0] else ""
        if relative:
            path = self.root / relative
            if path.suffix.lower() != ".html":
                return path / "index.html"
            if path.is_dir():
                # The row names a directory. Whatever the row calls it,
                # a container's prose is its own index — the same place
                # it would be if the row had said so.
                return path / "index.html"
            if not path.exists():
                # The row says "X.html" where the tree says "X/": the
                # container is the directory beside it.
                sibling = path.with_suffix("")
                if sibling.is_dir():
                    return sibling / "index.html"
            return path
        return self.documents_dir / f"{document_id}.html"

    def read_content(self, document_id: str) -> str:
        """A document's stored HTML, or "" if its file is missing.

        The metadata is lifted out: what comes back is the document's
        prose, so a word count or an export never counts the metadata
        (docs/STORAGE.md).

        Missing is treated as empty rather than an error so a project with
        a lost file still opens and can be repaired from a backup.
        """
        path = self.content_path(document_id)
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8")
        _metadata, prose = split_document(text)
        return prose

    def write_content(self, document_id: str, html: str, *,
                      relative: str | None = None) -> None:
        """Replace a document's prose file and refresh its cached word count.

        This is the only path that writes manuscript text, and it is
        atomic. The word count is derived here rather than stored
        separately, so the cached number can never disagree with the file
        that produced it.
        """
        # Writing to a temp file then os.replace so a crash mid-write can
        # never leave a half-written manuscript: the real file is only ever
        # swapped in whole. fsync first so the temp file's contents are on
        # disk before the rename — without it the rename can land on an
        # empty file after a power loss. Both steps are load-bearing; if
        # this is ever "simplified", the atomicity guarantee goes with it.
        if self.read_only:
            return          # the guide is read; it is not changed
        path = self.content_path(document_id, relative=relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = self._with_metadata(document_id, html)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{document_id}-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        except BaseException:
            Path(temporary_name).unlink(missing_ok=True)
            raise
        count = len(self.plain_text(html).split())
        self.db.execute(
            "UPDATE documents SET updated_at=?, word_count=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), count, document_id),
        )
        self.db.commit()
        if path.name.casefold() == "index.html":
            # A container's own prose leads its folder's order, so the
            # order file has to say so (docs/STORAGE.md).
            self._write_order_for(document_id)

    def _with_metadata(self, document_id: str, html: str) -> str:
        """The document's HTML with its metadata in the head.

        A file that carries its own metadata is self-contained: a writer
        can open it in any editor and see the synopsis, the word target
        and the rest, without Lekhak and without the database. The
        database still holds the same values, so the app is fast; the
        file is the truth (docs/STORAGE.md).
        """
        row = self.db.execute(
            "SELECT title,synopsis,word_target,kind,page_break,scene_divider,"
            "doc_template FROM documents WHERE id=?", (document_id,)
        ).fetchone()
        if row is None:
            return html
        metadata = {
            "title": row[0],
            "synopsis": row[1],
            "word_target": row[2],
            "kind": row[3],
            "page_break": row[4],
            "scene_divider": row[5],
            "doc_template": row[6],
        }
        metadata = {key: value for key, value in metadata.items()
                    if value not in (None, "", 0)}
        if self.is_template(document_id):
            # A template's own design (1.17): the yaml in the head is the
            # template's, and it travels with the file.
            metadata["template"] = "yes"
            for key, value in self.config_lines(
                    self.metadata(document_id).get(TEMPLATE_CONFIG_KEY, "")
            ).items():
                if key not in OWN_HEAD_KEYS:
                    metadata[key] = value
        # The writer's own fields travel with the file too (docs/STORAGE.md):
        # a `Type`, a `PoV`, a `Series` is in the head, not only in the
        # database, so the file is the whole document it claims to be. They
        # sit under `metadata:` rather than beside the head's own keys, which
        # is the shape the format promises and the one that cannot collide
        # with a key Lekhak owns. Value types and option lists stay behind:
        # they are editing affordances, not content.
        own = {key: value for key, value in self.metadata(document_id).items()
               if key not in OWN_HEAD_KEYS
               and key not in {TEMPLATE_KEY, TEMPLATE_CONFIG_KEY}
               and str(value).strip()}
        if own:
            metadata["metadata"] = own
        if not metadata:
            return html
        head = dump_frontmatter(metadata)
        if "<head>" in html.lower():
            return re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + "\n" + head,
                          html, count=1, flags=re.I)
        return head + html

    @staticmethod
    def accepted_html(html: str) -> str:
        """The manuscript as accept-all would leave it (2.4).

        Delete-marked text removed, insert-marked text kept with its
        words and without the mark. Word counts, search and every export
        see this — the stored file keeps its marks until the writer
        accepts or rejects (manuscripts-precious: nothing rewrites the
        stored form as a side effect).
        """
        html = DELETE_SPAN_RE.sub("", html)
        return INSERT_SPAN_RE.sub(r"\2", html)

    @staticmethod
    def plain_text(html: str) -> str:
        """Strip markup for word counts, search and plain-text export.

        Also unwraps ``[[wiki links]]`` to their bare title, so link
        markers are never counted as words or leaked into an export, and
        renders pending tracked changes (2.4) as accept-all would: the
        words of an insertion, never the words of a deletion.
        """
        html = Project.accepted_html(html)
        html = STYLE_RE.sub(" ", html)
        text = re.sub(r"<(br|/p|/div|/h[1-6])\b[^>]*>", "\n", html, flags=re.I)
        text = re.sub(r"\[\[([^\[\]]+)\]\]", r"\1", text)
        return unescape(TAG_RE.sub("", text)).strip()

    WIKI_LINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")

    def resolve_link(self, target: str) -> list[Document]:
        """Documents a `[[link]]` target points at.

        Exact (case-insensitive) title match wins outright; substring
        matches are returned too, but only ever as picker candidates — a
        caller must not auto-open more than one result. Empty means broken.
        """
        needle = target.strip().casefold()
        if not needle:
            return []
        documents = self.documents()
        exact = [d for d in documents if d.title.casefold() == needle]
        if exact:
            return exact
        # Substring only as a fallback, and only title-inside-target so a
        # short token cannot swallow an unrelated long title.
        return [
            d for d in documents
            if needle in d.title.casefold() or d.title.casefold() in needle
        ]

    def wiki_targets(self, document_id: str) -> list[tuple[str, list[Document]]]:
        """Each distinct `[[target]]` in a document, with what it resolves to."""
        html = self.read_content(document_id)
        seen: list[str] = []
        results: list[tuple[str, list[Document]]] = []
        for match in self.WIKI_LINK_RE.finditer(html):
            target = match.group(1).strip()
            if target and target not in seen:
                seen.append(target)
                results.append((target, self.resolve_link(target)))
        return results

    LINK_HREF_RE = re.compile(
        r'<a\s[^>]*?href=["\']lekhak://[^"\']*["\'][^>]*>(.*?)</a>',
        re.I | re.S,
    )

    def canonicalize_links(self, html: str) -> str:
        """Editor HTML back to stored form, so resolved links never hit disk.

        The editor resolves `[[Title]]` into `<a>` elements on load (a
        broken target becoming `[missing: Title]`). Saving that markup
        would freeze the resolution forever: a later retitle could not heal
        it, and the file would stop reading as plain `[[Title]]`. So each
        link anchor is rewritten back to `[[Title]]`, using the anchor's
        own visible text as the target.
        """

        def restore(match: re.Match) -> str:
            text = unescape(TAG_RE.sub("", match.group(1))).strip()
            if text.startswith("[missing: ") and text.endswith("]"):
                text = text[len("[missing: "):-1].strip()
            return f"[[{text}]]" if text else ""

        return self.LINK_HREF_RE.sub(restore, html)

    def _chapter_exact(self, document: Document) -> bool:
        """Whether a document plays by the Chapter rule (1.13): the
        exact, case-sensitive match.

        The rule follows the role (docs/MODEL.md): a document whose role
        is Chapter plays by it, and a chapter keeps playing by it until
        the writer gives it another role — the two base roles (1.17)
        would otherwise silently loosen the rule for every chapter.
        """
        role = self.type_of(document)
        return role == "Chapter" or (
            role == BASE_TYPE["document"] and document.kind == "chapter")

    def related_documents(self, document_id: str) -> tuple[list[Document], list[Document]]:
        """Whole-word title matches around one document (1.13), both ways.

        ``mentions``: other documents whose titles appear in this one's
        text — the scene's characters and locations. ``mentioned_by``:
        other documents whose text contains this one's title — the
        continuity view on a character sheet.

        Computed fresh from titles and prose on every call: nothing is
        stored, so a retitle or an edit heals itself and no relation can
        go stale. Whole-word phrase match (word-character boundaries,
        case-insensitive) so "Rid" never matches inside "Somewhere". Three
        rules on top of that:

        - **Never itself, and never a duplicate name.** A document is not
          related to the same document; and two documents sharing one
          name — a chapter folder and its scene both called "Prologue" —
          read as one document to the writer, so a title equal to this
          one's (case aside) is skipped in both directions.
        - **A Chapter's title must match exactly, case included.** Chapter
          titles are often common words — the lowercase "prologue" in
          "the prologue of the letter" is not a reference to the chapter
          *Prologue*, while writing "Prologue" as it is titled is. The
          rule keys on whose title is being matched: ``mentions`` matches
          the other documents' titles, ``mentioned_by`` matches this one's
          — so a Type of Chapter anywhere earns the strict rule.
          Everything else keeps the case-insensitive match: names are
          vocabulary however the writer typed them.
        """
        documents = self.documents()
        target = next((d for d in documents if d.id == document_id), None)
        if target is None:
            return [], []
        # Templates are furniture, not manuscript: a side file named
        # chapter.html must not claim to mention every Chapter, and a
        # template selected in the binder has no "mentioned by" to show.
        if target.path and (target.path == "templates" or target.path.startswith("templates/")):
            return [], []
        texts = self._plain_texts()
        own_text = texts.get(document_id, "")
        own_title = target.title.strip()
        own_key = own_title.casefold()
        own_exact = self._chapter_exact(target)
        mentions: list[Document] = []
        mentioned_by: list[Document] = []
        for document in documents:
            if document.id == document_id:
                continue
            if document.path and (document.path == "templates" or document.path.startswith("templates/")):
                continue
            title = document.title.strip()
            if title and title.casefold() == own_key:
                continue
            if title and self._mentions_phrase(own_text, title):
                # An exact match is a subset of the loose one, so the
                # strict re-check runs only on candidates that already
                # matched — one type_of per hit, not per document.
                if (not self._chapter_exact(document)
                        or self._mentions_phrase(own_text, title, exact=True)):
                    mentions.append(document)
            other_text = texts.get(document.id, "")
            if own_title and self._mentions_phrase(other_text, own_title):
                if (not own_exact
                        or self._mentions_phrase(other_text, own_title, exact=True)):
                    mentioned_by.append(document)
        return mentions, mentioned_by

    def _plain_texts(self) -> dict[str, str]:
        """Every document's plain text, cached against file mtime and size.

        The related-files scan (1.13) needs all texts on every selection
        change; stat is microseconds while re-parsing is not. Keyed on
        mtime+size, so a save invalidates its own entry — the file stays
        the only source of truth and a stale string cannot survive a
        write. Entries for deleted documents may linger unread; they cost
        nothing and the cache dies with this Project instance.
        """
        texts: dict[str, str] = {}
        for document in self.documents():
            path = self.content_path(document.id)
            try:
                stat = path.stat()
                stamp = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                texts[document.id] = ""
                continue
            cached = self._plain_text_cache.get(document.id)
            if cached is None or cached[0] != stamp:
                try:
                    raw = path.read_text(encoding="utf-8")
                except OSError:
                    raw = ""
                # The metadata in the file's head is not prose: it must
                # not count as words or answer a wiki link.
                _metadata, prose = split_document(raw)
                cached = (stamp, self.plain_text(prose))
                self._plain_text_cache[document.id] = cached
            texts[document.id] = cached[1]
        return texts

    @staticmethod
    def _mentions_phrase(text: str, phrase: str, *, exact: bool = False) -> bool:
        r"""Whole-word ``phrase`` inside ``text``.

        (?<!\w)/(?!\w) rather than \b so titles that start or end with
        punctuation still bind correctly, and both sides are casefolded
        so a match survives every case difference the writer typed —
        unless ``exact`` is set, which keeps the writer's case: a Chapter
        is mentioned only when its title appears as it is written, not by
        a common word in another case ("the prologue of the letter" is no
        reference to the chapter *Prologue*). See related_documents.

        Two things are deliberately *not* mentions:

        - **A phrase with no letters in it.** A text title matches by
          meaning; a bare number matches by coincidence — a year, a page
          number, an index — so a chapter called "2" would be "mentioned" by
          every "2024" in the book. Meaningless matches are worse than none.
        - **Anything inside a URL.** A link copied from the internet is not a
          reference to this project, and URLs are thick with the digits and
          slashes that make the previous case so noisy.
        """
        if not text or not phrase or not any(ch.isalpha() for ch in phrase):
            return False
        haystack = URL_RE.sub(" ", text)
        if exact:
            pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"
            return re.search(pattern, haystack) is not None
        pattern = r"(?<!\w)" + re.escape(phrase.casefold()) + r"(?!\w)"
        return re.search(pattern, haystack.casefold()) is not None

    def search(self, query: str) -> list[Document]:
        """Documents whose title, synopsis or body text contains ``query``.

        Case-insensitive substring match over stored text. For previews
        with surrounding context, see search_snippets().
        """
        needle = query.casefold().strip()
        if not needle:
            return []
        return [
            document for document in self.documents()
            if needle in document.title.casefold()
            or needle in document.synopsis.casefold()
            or needle in self.plain_text(self.read_content(document.id)).casefold()
        ]

    @staticmethod
    def _compile_query(query: str, *, regex: bool, ignore_case: bool) -> re.Pattern:
        pattern = query if regex else re.escape(query)
        return re.compile(pattern, re.IGNORECASE if ignore_case else 0)

    def search_snippets(self, query: str, *, regex: bool = False,
                        ignore_case: bool = True, context: int = 40,
                        limit: int = 500,
                        fields: set[str] | None = None) -> list[tuple[Document, str, str]]:
        """Every match with its field and surrounding text, for previews.

        `fields` filters to any of "content", "title", "synopsis"; None means
        all three.
        """
        if not query:
            return []
        compiled = self._compile_query(query, regex=regex, ignore_case=ignore_case)
        results: list[tuple[Document, str, str]] = []
        for document in self.documents():
            candidates = [
                ("content", self.plain_text(self.read_content(document.id))),
                ("title", document.title),
                ("synopsis", document.synopsis),
            ]
            for field, text in candidates:
                if fields is not None and field not in fields:
                    continue
                for match in compiled.finditer(text):
                    start = max(0, match.start() - context)
                    end = min(len(text), match.end() + context)
                    prefix = "…" if start > 0 else ""
                    suffix = "…" if end < len(text) else ""
                    results.append((document, field, f"{prefix}{text[start:end]}{suffix}"))
                    if len(results) >= limit:
                        return results
        return results

    def replace(self, query: str, replacement: str, *,
                document_ids: set[str] | None = None,
                regex: bool = False, ignore_case: bool = True,
                in_titles: bool = False, in_synopses: bool = False) -> dict:
        """Replace across the project. Returns a count dict.

        In literal mode the replacement is inserted verbatim; in regex mode
        it is a normal re.sub pattern (backreferences work). The search runs
        against stored HTML, so matches interrupted by markup will not hit —
        the same limitation every plain exporter has.
        """
        if not query:
            return {"replacements": 0, "documents": 0, "titles": 0, "synopses": 0}
        compiled = self._compile_query(query, regex=regex, ignore_case=ignore_case)
        targets = [
            document for document in self.documents()
            if document_ids is None or document.id in document_ids
        ]
        counts = {"replacements": 0, "documents": 0, "titles": 0, "synopses": 0}
        insert = (lambda _match: replacement) if not regex else replacement
        # Plan first, write second. Replace spans many files with no
        # transaction across them, so the only rollback path is a
        # snapshot — and planning first means a document that turns out
        # not to change gets no snapshot and no write.
        planned = []
        for document in targets:
            html = self.read_content(document.id)
            replaced, count = compiled.subn(insert, html)
            new_title = None
            title_count = 0
            if in_titles and compiled.search(document.title):
                new_title, title_count = compiled.subn(insert, document.title)
            new_synopsis = None
            synopsis_count = 0
            if in_synopses and compiled.search(document.synopsis):
                new_synopsis, synopsis_count = compiled.subn(
                    insert, document.synopsis)
            if count or title_count or synopsis_count:
                planned.append((document, replaced, count, new_title,
                                title_count, new_synopsis, synopsis_count))
        for document, *_rest in planned:
            self.create_snapshot(document.id, "Before replace")
        for (document, replaced, count, new_title, title_count,
             new_synopsis, synopsis_count) in planned:
            if count:
                self.write_content(document.id, replaced)
                counts["replacements"] += count
            if title_count:
                self.update_document(document.id, title=new_title)
                counts["titles"] += title_count
                counts["replacements"] += title_count
            if synopsis_count:
                self.update_document(document.id, synopsis=new_synopsis)
                counts["synopses"] += synopsis_count
                counts["replacements"] += synopsis_count
            counts["documents"] += 1
        return counts

    def metadata(self, document_id: str) -> dict[str, str]:
        rows = self.db.execute(
            "SELECT key,value FROM document_metadata WHERE document_id=? ORDER BY key",
            (document_id,),
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def metadata_inherited(self, document_id: str) -> dict[str, str]:
        """The values a document answers with: its own, and what it
        inherits from the folders above it.

        Nearest first, and the first non-empty value wins: the
        document's own, then the nearest folder that has one, then the
        next one up. An empty field means *inherit*, not *blank it* —
        which is what makes a folder the place to set a field once for
        everything inside it.
        """
        values: dict[str, str] = {}
        by_id = {document.id: document for document in self.documents()}
        current = by_id.get(document_id)
        while current is not None:
            for key, value in self.metadata(current.id).items():
                if value.strip() and not values.get(key, "").strip():
                    values[key] = value
            current = by_id.get(current.parent_id) if current.parent_id else None
        return values

    def metadata_inherited_all(self) -> dict[str, dict[str, str]]:
        """Every document's effective metadata, in one pass.

        The same rule as :meth:`metadata_inherited` — its own value, then
        the nearest folder that has one, then the next one up — for the
        places that need the whole project at once (the outliner).
        """
        entries = self.metadata_entries_all()
        by_id = {document.id: document for document in self.documents()}
        resolved: dict[str, dict[str, str]] = {}

        def resolve(document_id: str) -> dict[str, str]:
            if document_id in resolved:
                return resolved[document_id]
            values: dict[str, str] = {}
            document = by_id.get(document_id)
            if document is not None:
                for key, entry in entries.get(document_id, {}).items():
                    if entry[0].strip():
                        values[key] = entry[0]
                if document.parent_id:
                    for key, value in resolve(document.parent_id).items():
                        if not values.get(key, "").strip():
                            values[key] = value
            resolved[document_id] = values
            return values

        for document in self.documents():
            resolve(document.id)
        return resolved

    def default_type(self, document: Document) -> str:
        """The role a document shows with nothing stored (1.17).

        Two base roles, as close to the truth on disk as a role can be:
        a folder is a Folder and everything else a Document. A role the
        writer adds (Scene, Part, Book…) is stored on the document and
        kept in that group's catalogue, so it is offered again.
        """
        return BASE_TYPE[self.type_group(document)]

    def type_group(self, document: Document) -> str:
        """Which role group a document belongs to (1.17): a folder's
        roles are the folder's, a document's are the document's, and
        the two lists never meet."""
        return "folder" if document.kind == "folder" else "document"

    def container_ids(self) -> set[str]:
        """Ids that hold children — a container whatever its physical kind.

        The filesystem model (docs/STORAGE.md): anything with children is a
        folder, so a chapter holding scenes is a container to the interface
        even though its stored kind is "chapter".
        """
        documents = self.documents()
        parents = {document.parent_id for document in documents
                   if document.parent_id}
        return {document.id for document in documents
                if document.kind == "folder" or document.id in parents}

    def sync_with_disk(self) -> int:
        """Bring the database into step with the folder tree on disk.

        The folder tree is the truth (docs/STORAGE.md); the database is
        the index over it. A folder or file the writer added by hand —
        in a file manager, in a backup restored onto disk — becomes a
        row here, so it shows in the binder and can be written in. A row
        whose file has gone keeps its row but loses its file, which
        reads as an empty document rather than a mystery.

        A container's own ``index.html`` is its prose, not a document of
        its own: the row for the container already stands for that file,
        so the index never grows a second row for it.

        Returns how many rows were added.
        """
        known = {
            str(row[0]): row[1]
            for row in self.db.execute("SELECT path,id FROM documents")
            if row[0]
        }
        added = 0
        touched: set[str | None] = set()
        for folder, relative in self._disk_tree():
            if relative in known:
                continue
            if Path(relative).name.lower() == "index.html":
                continue        # a container's own prose, not a child
            title = Path(relative).stem if relative.endswith(".html") \
                else Path(relative).name
            parent_relative = str(Path(relative).parent)
            parent_id = None
            if parent_relative not in (".", ""):
                parent_id = known.get(parent_relative)
            kind = "folder" if folder.is_dir() else "scene"
            if not folder.is_dir():
                kind = self._kind_from_file(folder) or kind
            document_id = self._insert_row(title, parent_id, kind, relative)
            known[relative] = document_id
            touched.add(parent_id)
            added += 1
        # A file added by hand is a row now, so its folder's order file
        # lists it too: the tree, the order and the index agree before
        # anything is reordered.
        for parent_id in touched:
            self._write_order_for(parent_id)
        # A row that points at a container's own index is a leftover of
        # an older index: the container's row stands for that file, so
        # the second row is dropped — otherwise the binder shows the
        # same words twice, once as the folder and once as a document.
        for relative, document_id in list(known.items()):
            if Path(relative).name.lower() != "index.html":
                continue
            parent = str(Path(relative).parent)
            if parent in (".", ""):
                continue
            if parent in known:
                self._forget_row(document_id)
                known.pop(relative, None)
        return added

    def _forget_row(self, document_id: str) -> None:
        """Drop a row from the index, leaving what is on disk alone.

        Used where the index has a row the tree does not need: the
        container's own ``index.html`` is already spoken for by the
        container's row, and two rows for one file is a binder that
        shows the same words twice.
        """
        with self.db:
            self.db.execute("DELETE FROM documents WHERE id=?", (document_id,))

    def binder_tree(self) -> list[DiskNode]:
        """The project as the binder shows it: the folder tree, on disk.

        The tree is walked on disk, not read out of the database
        (docs/STORAGE.md): the files are the truth and the database is
        the index over them. The index is joined in for what only it
        knows — the document's id, and the title the writer gave it.

        A row whose file has gone is still shown, in its place in the
        tree: an empty document is a thing the writer can see and mend,
        a vanished one is a mystery.
        """
        documents = self.documents()
        if any(not document.path for document in documents):
            # A project made before the tree existed: its documents live
            # in the old store, and the database is still the only place
            # the structure is written down.
            return self._tree_from_database(documents)
        by_path = {document.path: document for document in documents}
        nodes: dict[str, DiskNode] = {}      # document id -> its node

        def walk(folder: Path, relative: str) -> list[DiskNode]:
            children: list[DiskNode] = []
            for entry in self._ordered_entries(folder, relative):
                child_relative = (str(Path(relative) / entry.name)
                                  if relative else entry.name)
                if entry.is_dir():
                    node = DiskNode(path=child_relative, title=entry.name,
                                    is_folder=True)
                    node.children = walk(entry, child_relative)
                    children.append(node)
                elif entry.suffix.lower() == ".html":
                    if entry.name.lower() == "index.html":
                        # A container's own prose, not a child of it.
                        continue
                    children.append(DiskNode(path=child_relative,
                                             title=entry.stem,
                                             is_folder=False))
            return children

        roots = walk(self.root, "")

        def join(node: DiskNode) -> None:
            document = by_path.get(node.path)
            if document is not None:
                node.document_id = document.id
                node.title = document.title
                node.kind = document.kind
                nodes[document.id] = node
            for child in node.children:
                join(child)

        for node in roots:
            join(node)
        # A row whose file has gone keeps its place in the tree.
        parent_ids = {document.parent_id for document in documents
                      if document.parent_id}
        for document in documents:
            if document.id in nodes or not document.path:
                continue
            node = DiskNode(path=document.path, title=document.title,
                            is_folder=(document.kind == "folder"
                                       or document.id in parent_ids),
                            document_id=document.id, kind=document.kind)
            parent = nodes.get(document.parent_id or "")
            if parent is not None:
                parent.children.append(node)
            else:
                roots.append(node)
        return roots

    def _tree_from_database(self, documents: list[Document]) -> list[DiskNode]:
        """The tree as the database records it.

        For a project that has no folder tree of its own — one made
        before the tree existed — the database is the only place the
        structure is written down, so it is what the binder shows.
        """
        by_parent: dict[str | None, list[Document]] = {}
        for document in documents:
            by_parent.setdefault(document.parent_id, []).append(document)
        parent_ids = {document.parent_id for document in documents
                      if document.parent_id}

        def build(parent_id: str | None) -> list[DiskNode]:
            nodes: list[DiskNode] = []
            for document in sorted(by_parent.get(parent_id, []),
                                   key=lambda d: d.position):
                node = DiskNode(
                    path=document.path, title=document.title,
                    is_folder=(document.kind == "folder"
                               or document.id in parent_ids),
                    document_id=document.id, kind=document.kind)
                node.children = build(document.id)
                nodes.append(node)
            return nodes

        return build(None)

    def _ordered_entries(self, folder: Path, relative: str) -> list[Path]:
        """A folder's children, in the order the tree records.

        The order file is the sequence on disk (docs/STORAGE.md); the
        manifest holds the root's. Anything the order file does not
        name — something the writer added by hand — comes after, in
        name order, so the tree is never left out of what is there.

        The project's own furniture is skipped at the root only: a
        writer's own book called "documents" is theirs, wherever it
        stands.
        """
        try:
            entries = [entry for entry in folder.iterdir()
                       if not entry.name.startswith(".")
                       and not (not relative
                                and entry.name in PROJECT_FURNITURE)]
        except OSError:
            return []
        order = self._order_names(folder, relative)
        rank = {name: index for index, name in enumerate(order)}
        in_templates = relative == "templates" or relative.startswith("templates/")
        template_rank = {"title-page.html": 0, "title.html": 0,
                         "book.html": 1, "frontmatter.html": 2,
                         "folder.html": 3, "contents.html": 3,
                         "chapter.html": 4, "scene.html": 5,
                         "document.html": 6}

        def key(entry: Path) -> tuple[int, int, str]:
            name = entry.name
            if name in rank:
                return (0, rank[name], name.casefold())
            prio = template_rank.get(name.casefold(), 10) if in_templates else 10
            return (1, prio, name.casefold())

        return sorted(entries, key=key)

    def _order_names(self, folder: Path, relative: str) -> list[str]:
        """The names a folder's order file gives, in order."""
        if relative:
            path = folder / ".order.json"
            if not path.is_file():
                return []
        else:
            path = self.root / "manifest.json"
            if not path.is_file():
                return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, dict):
            return []
        names = data.get("children", [])
        if not isinstance(names, list):
            return []
        return [str(name) for name in names]

    def _insert_row(self, title: str, parent_id: str | None, kind: str,
                    path: str) -> str:
        """Add a row for something already on disk.

        The file is not created — it is there, which is why this is not
        ``add_document``. Its position follows the binder order, and the
        folder's own order file if it has one.
        """
        document_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        position = self._next_position(parent_id, path)
        self.db.execute(
            "INSERT INTO documents(id,parent_id,title,position,kind,"
            "created_at,updated_at,path) VALUES(?,?,?,?,?,?,?,?)",
            (document_id, parent_id, title, position, kind, now, now, path),
        )
        self.db.commit()
        return document_id

    def _next_position(self, parent_id: str | None, path: str) -> int:
        """Where a new row goes among its siblings.

        The folder's order file has the answer when it has one: the
        sequence the writer arranged is the sequence kept. Otherwise the
        row goes at the end.
        """
        row = self.db.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM documents "
            "WHERE parent_id IS ?", (parent_id,)
        ).fetchone()
        end = int(row[0])
        parent_path = str(Path(path).parent)
        if parent_path in (".", ""):
            return end
        order_file = self.root / parent_path / ".order.json"
        if not order_file.is_file():
            return end
        try:
            names = json.loads(order_file.read_text(encoding="utf-8")).get(
                "children", [])
        except (OSError, ValueError, AttributeError):
            return end
        name = Path(path).name
        if name not in names:
            return end
        index = names.index(name)
        # index.html leads the folder's order but is the parent's own
        # page, not a child: it takes no position, so the children count
        # from zero as they always did.
        index -= sum(1 for entry in names[:index]
                     if entry.casefold() == "index.html")
        # Everything at or after this name shifts down one.
        for sibling in self.db.execute(
            "SELECT id,position FROM documents WHERE parent_id IS ? "
            "AND position >= ?", (parent_id, index)
        ).fetchall():
            self.db.execute(
                "UPDATE documents SET position=? WHERE id=?",
                (int(sibling[1]) + 1, sibling[0]))
        return index

    def _kind_from_file(self, path: Path) -> str:
        """The kind a document's own head says it is."""
        try:
            metadata, _ = split_document(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        kind = str(metadata.get("kind") or "").strip()
        return kind if kind in DOCUMENT_KINDS else ""

    def _disk_tree(self):
        """Every folder and document file in the project, on disk.

        The project's own furniture — the database, the manifest — is
        not part of the manuscript, so it is not walked. Templates and
        trash are normal folders read from disk like any other: they
        show in the binder and are editable in the editor; only the
        metadata box and the export differ. The furniture is skipped
        at the root only: a writer's own book called "documents" is
        theirs. An inside ``backups/`` is never walked either, but
        unlike furniture it is flagged by validate_alignment(): it
        should not exist inside the project at all.
        """
        skip = {"documents", ".lekhak", "backups"}

        template_rank = {"title-page.html": 0, "title.html": 0,
                         "book.html": 1, "frontmatter.html": 2,
                         "folder.html": 3, "contents.html": 3,
                         "chapter.html": 4, "scene.html": 5,
                         "document.html": 6}

        def walk(folder: Path):
            try:
                try:
                    rel = str(folder.relative_to(self.root))
                except Exception:
                    rel = ""
                in_templates = rel == "templates" or rel.startswith("templates/")
                entries = sorted(
                    folder.iterdir(),
                    key=lambda x: ((template_rank.get(x.name.casefold(), 10) if in_templates else 10),
                                   x.name.casefold()))
            except OSError:
                return
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                if folder == self.root and entry.name in skip:
                    continue
                if entry.is_dir():
                    yield entry, str(entry.relative_to(self.root))
                    yield from walk(entry)
                elif entry.name.endswith(".html"):
                    if entry.name.lower() == "index.html":
                        continue    # a container's own prose, not a child
                    yield entry, str(entry.relative_to(self.root))

        yield from walk(self.root)

    def lock_marker(self, document_id: str) -> Path | None:
        """Where a document's lock marker lives, or None if it has none.

        A container carries the marker **inside** the folder, so a copy of
        the folder is still locked. A document carries it beside the file
        and hidden, so the folder around it stays readable.
        """
        row = self.db.execute(
            "SELECT path FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        if row is None or not row[0]:
            return None
        path = self.root / str(row[0])
        if path.is_dir():
            return path / LOCK_FILE
        return path.with_name("." + path.name + LOCK_FILE)

    def set_locked(self, document_id: str, locked: bool) -> None:
        """Lock a document, or unlock it.

        Locking drops the marker file; unlocking takes it away. Nothing
        else changes: the writing stays exactly as it was, which is the
        point of a lock on a book that is already published.
        """
        marker = self.lock_marker(document_id)
        if marker is None or self.read_only:
            return          # a read-only project keeps no markers
        if locked:
            try:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("Locked.\n", encoding="utf-8")
            except OSError:
                pass
        else:
            marker.unlink(missing_ok=True)

    def is_locked(self, document_id: str) -> bool:
        """True when the document is locked — its own marker, or an
        ancestor's. A locked folder locks everything inside it.
        """
        return self.lock_holder(document_id) is not None

    def has_own_lock(self, document_id: str) -> bool:
        """True when the document carries a lock marker of its own."""
        marker = self.lock_marker(document_id)
        return marker is not None and marker.exists()

    def lock_holder(self, document_id: str) -> str | None:
        """The title of the thing that locks this document, if any.

        A locked folder locks everything inside it, so the holder may be
        an ancestor rather than the document itself.
        """
        current = document_id
        while current:
            marker = self.lock_marker(current)
            if marker is not None and marker.exists():
                row = self.db.execute(
                    "SELECT title FROM documents WHERE id = ?", (current,)
                ).fetchone()
                return row[0] if row else current
            current = self.parent_of(current)
        return None

    def locked_ids(self) -> set[str]:
        """Every document that is locked — its own marker, or an
        ancestor's.

        One pass down the tree: a locked folder locks everything inside
        it, so the answer is inherited as it goes.
        """
        documents = self.documents()
        children: dict[str | None, list[Document]] = {}
        for document in documents:
            children.setdefault(document.parent_id, []).append(document)
        locked: set[str] = set()

        def visit(document: Document, inherited: bool) -> None:
            here = inherited or self.has_own_lock(document.id)
            if here:
                locked.add(document.id)
            for child in children.get(document.id, []):
                visit(child, here)

        for root in children.get(None, []):
            visit(root, False)
        return locked

    def type_of(self, document: Document) -> str:
        """The protected Type role of a document (docs/MODEL.md).

        Stored Type metadata wins; otherwise the derived default, which is
        also the whole migration for pre-Type projects: a folder with book
        settings or page setup reads as Book, everything else as what it
        physically is. Templates and the UI ask here, never the raw kind.
        """
        stored = self.metadata(document.id).get("Type", "").strip()
        return stored or self.default_type(document)

    def metadata_keys(self) -> list[str]:
        """Every metadata key used anywhere in the project, sorted."""
        rows = self.db.execute(
            "SELECT DISTINCT key FROM document_metadata ORDER BY key"
        ).fetchall()
        return [row[0] for row in rows]

    def metadata_placeholders(self) -> dict[str, dict[str, int]]:
        """Which metadata keys this project actually carries, by the Type
        that carries them: "Project" for the project's own fields, else the
        document's Type, each with how many documents set it. A key two
        Types use appears under both (docs/TEMPLATES.md 3.2 — the
        placeholder reference offers what is real, not what is possible).
        """
        grouped: dict[str, dict[str, int]] = {}
        for key, entry in self.project_metadata().items():
            if entry and entry[0].strip():
                grouped.setdefault("Project", {})[key] = 1
        types = {document.id: self.type_of(document)
                 for document in self.documents()}
        for document_id, entries in self.metadata_entries_all().items():
            for key, entry in entries.items():
                if key == "Type" or not entry[0].strip():
                    continue
                counts = grouped.setdefault(types.get(document_id, "Document"), {})
                counts[key] = counts.get(key, 0) + 1
        ordered: dict[str, dict[str, int]] = {}
        if "Project" in grouped:
            ordered["Project"] = dict(sorted(grouped.pop("Project").items()))
        for name, keys in sorted(grouped.items()):
            ordered[name] = dict(sorted(keys.items()))
        return ordered

    def required_metadata_fields(self) -> dict[str, list[str]]:
        """Mandatory field names, by Type.

        Marking a field required in one document makes it required for
        every document and folder of that Type — stored per Type so the
        rule follows the writer's vocabulary, not a global guess. Value:
        ``{type_name: [field, ...]}``. A list-shaped legacy value is read
        as applying to every type (never written that way).
        """
        raw = self.get_setting("required_metadata")
        try:
            stored = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            stored = {}
        if isinstance(stored, list):
            # legacy/global shape: treat as no per-type rules rather than
            # mis-apply; UI will re-save in the typed shape.
            return {}
        if not isinstance(stored, dict):
            return {}
        result: dict[str, list[str]] = {}
        for type_name, names in stored.items():
            if not isinstance(type_name, str) or not isinstance(names, list):
                continue
            cleaned: list[str] = []
            seen: set[str] = set()
            for name in names:
                if isinstance(name, str) and name.strip():
                    key = name.strip()
                    if key.casefold() not in seen:
                        seen.add(key.casefold())
                        cleaned.append(key)
            if cleaned:
                result[type_name] = cleaned
        return result

    def required_fields_for_type(self, type_name: str) -> list[str]:
        """Field names mandatory for this Type (case-insensitive match)."""
        wanted = (type_name or "").casefold()
        for name, fields in self.required_metadata_fields().items():
            if name.casefold() == wanted:
                return list(fields)
        return []

    def set_type_required(self, type_name: str, field: str,
                          required: bool) -> None:
        """Mark a field required or not for every document of this Type."""
        field = (field or "").strip()
        type_name = (type_name or "").strip()
        if not field or not type_name:
            return
        stored = self.required_metadata_fields()
        # keep the original-cased key if present
        key = next((name for name in stored if name.casefold() == type_name.casefold()),
                   type_name)
        fields = stored.get(key, [])
        lower = field.casefold()
        fields = [name for name in fields if name.casefold() != lower]
        if required:
            fields.append(field)
        if fields:
            stored[key] = fields
        else:
            stored.pop(key, None)
        self.set_setting("required_metadata",
                         json.dumps(stored, ensure_ascii=False))

    def missing_required_metadata(self, selected_ids: set[str] | None = None
                                 ) -> list[tuple[str, str, str]]:
        """(document title, Type, field) for every mandatory field left empty.

        Only documents in the export scope are checked. A value set on the
        document, on a folder above it, or as a project-wide field counts
        as filled.
        """
        by_type = self.required_metadata_fields()
        if not by_type:
            return []
        exported = self._export_documents(selected_ids)
        missing: list[tuple[str, str, str]] = []
        for document in exported:
            doc_type = self.type_of(document)
            required = self.required_fields_for_type(doc_type)
            if not required:
                continue
            values = self.metadata_inherited(document.id)
            for name in required:
                if str(values.get(name, "")).strip():
                    continue
                project_value = self.project_metadata().get(name)
                if project_value is not None:
                    held = (project_value[0]
                            if isinstance(project_value, tuple)
                            else str(project_value))
                    if str(held).strip():
                        continue
                missing.append((document.title, doc_type, name))
        return missing

    def _require_metadata_for_export(self, selected_ids: set[str] | None) -> None:
        missing = self.missing_required_metadata(selected_ids)
        if not missing:
            return
        lines = "\n".join(
            f"- {title} ({dtype}): {field}"
            for title, dtype, field in missing[:20])
        more = "" if len(missing) <= 20 else f"\n… and {len(missing) - 20} more"
        raise ValueError(
            "Some fields marked as required are empty:\n"
            f"{lines}{more}\n\n"
            "Fill them, or mark the fields as not required, then export again.")

    def _toc_config(self, document_id: str) -> dict:
        """Contents settings for the document that holds ${ToC}.

        Stored on that document (its own metadata/head) so each contents
        page can be configured independently. Depth is capped at 4; each
        included level has a marker style (decimal, alpha, roman, bullet,
        none). Defaults keep the previous behaviour: depth 4, decimal.
        """
        meta = self.metadata(document_id)
        try:
            depth = int(str(meta.get("ToC depth", "4")).strip() or "4")
        except (TypeError, ValueError):
            depth = 4
        depth = max(1, min(depth, 4))
        markers: list[str] = []
        for level in range(1, depth + 1):
            style = str(meta.get(f"ToC level {level}", "none")).strip()
            markers.append(style or "none")
        return {"depth": depth, "markers": markers}

    def _require_metadata_for_export(self, selected_ids: set[str] | None) -> None:
        missing = self.missing_required_metadata(selected_ids)
        if not missing:
            return
        lines = "\n".join(
            f"- {title}: {field}" for title, field in missing[:20])
        more = "" if len(missing) <= 20 else f"\n… and {len(missing) - 20} more"
        raise ValueError(
            "Some fields marked as required are empty:\n"
            f"{lines}{more}\n\n"
            "Fill them, or mark the fields as not required, then export again.")

    def type_values(self) -> list[str]:
        """Every distinct Type value used in the project, sorted.

        The inspector's Type dropdown seeds itself from these, so a
        value typed once — typo included — is selectable next time
        rather than re-typed (docs/MODEL.md).
        """
        rows = self.db.execute(
            "SELECT DISTINCT value FROM document_metadata "
            "WHERE key='Type' AND TRIM(value) != '' ORDER BY value"
        ).fetchall()
        return [row[0] for row in rows]

    def known_types(self, group: str) -> list[str]:
        """The roles this group offers (1.17).

        Its base role, every role the writer has added to it, and every
        role already stored on one of the group's documents — an older
        project's own roles stay offered rather than vanishing.
        """
        names: list[str] = []
        seen: set[str] = set()

        def offer(name: str) -> None:
            name = (name or "").strip()
            if not name or name.casefold() in seen:
                return
            seen.add(name.casefold())
            names.append(name)

        offer(BASE_TYPE.get(group, BASE_TYPE["document"]))
        for name in self._catalogue("known_types", group):
            offer(name)
        for document in self.documents():
            if self.type_group(document) != group:
                continue
            offer(self.metadata(document.id).get("Type", ""))
        return names

    def add_type(self, group: str, name: str) -> str:
        """Remember a new role for a group (1.17), and return it as kept.

        The catalogue is what the dropdown reads, so a role added here
        is offered to every document of the group that follows — storing
        it on one document alone would offer it to that one only.
        """
        name = (name or "").strip()
        if not name:
            return ""
        catalogue = self._catalogue("known_types", group)
        for held in catalogue:
            if held.casefold() == name.casefold():
                return held
        catalogue.append(name)
        self._set_catalogue("known_types", group, catalogue)
        return name

    def known_fields(self, group: str) -> list[str]:
        """The metadata field names this group offers (1.17).

        A field created on one scene shows, empty, on every other
        scene: the catalogue names the fields, the documents hold the
        values, and only filled values are stored.
        """
        names: list[str] = []
        seen: set[str] = set()

        def offer(name: str) -> None:
            name = (name or "").strip()
            if not name or name.casefold() in seen:
                return
            seen.add(name.casefold())
            names.append(name)

        for name in self._catalogue("known_fields", group):
            offer(name)
        for document in self.documents():
            if self.type_group(document) != group:
                continue
            for key in self.metadata(document.id):
                if key in ("Type", TEMPLATE_KEY, TEMPLATE_CONFIG_KEY):
                    continue
                offer(key)
        if group == "folder":
            for key in self.project_metadata():
                offer(key)
        return names

    def add_field(self, group: str, name: str) -> str:
        """Remember a field name for a group (1.17), and return it."""
        name = (name or "").strip()
        if not name:
            return ""
        catalogue = self._catalogue("known_fields", group)
        for held in catalogue:
            if held.casefold() == name.casefold():
                return held
        catalogue.append(name)
        self._set_catalogue("known_fields", group, catalogue)
        return name

    def _catalogue(self, key: str, group: str) -> list[str]:
        """One group's list from a catalogue kept in the settings table
        (the project_metadata precedent): no schema change, and a junk
        or hand-edited value degrades to an empty list."""
        raw = self.get_setting(key)
        try:
            stored = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            stored = {}
        if not isinstance(stored, dict):
            stored = {}
        values = stored.get(group)
        if not isinstance(values, list):
            return []
        return [str(value) for value in values if isinstance(value, str)]

    def _set_catalogue(self, key: str, group: str, values: list[str]) -> None:
        raw = self.get_setting(key)
        try:
            stored = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            stored = {}
        if not isinstance(stored, dict):
            stored = {}
        stored[group] = list(values)
        self.set_setting(key, json.dumps(stored, ensure_ascii=False))

    def word_rollups(self) -> dict[str, int]:
        """Word count per document, containers summing all descendants."""
        documents = self.documents()
        by_id = {d.id: d for d in documents}
        children: dict[str | None, list[str]] = {}
        for document in documents:
            children.setdefault(document.parent_id, []).append(document.id)
        rollup: dict[str, int] = {}

        def visit(document_id: str) -> int:
            total = by_id[document_id].word_count
            for child_id in children.get(document_id, []):
                total += visit(child_id)
            rollup[document_id] = total
            return total

        for document in documents:
            if document.id not in rollup:
                visit(document.id)
        return rollup

    def metadata_entries(self, document_id: str) -> dict[str, tuple[str, str, list[str]]]:
        """One document's metadata: key -> (value, value_type, options).

        ``value_type`` is "text", "textarea", "checkbox" or "date";
        ``options`` belongs to the removed Dropdown type and is now
        always an empty list — legacy "choice" rows keep their stored
        value but load in the panel as text.
        """
        rows = self.db.execute(
            "SELECT key,value,value_type,options_json FROM document_metadata "
            "WHERE document_id=? ORDER BY key", (document_id,),
        ).fetchall()
        return {row[0]: (row[1], row[2], json.loads(row[3])) for row in rows}

    def metadata_entries_all(self) -> dict[str, dict[str, tuple[str, str, list[str]]]]:
        """Every document's metadata in one query, keyed by document id."""
        rows = self.db.execute(
            "SELECT document_id,key,value,value_type,options_json "
            "FROM document_metadata ORDER BY document_id,key"
        ).fetchall()
        result: dict[str, dict[str, tuple[str, str, list[str]]]] = {}
        for row in rows:
            result.setdefault(row[0], {})[row[1]] = (row[2], row[3], json.loads(row[4]))
        return result

    def project_metadata(self) -> dict[str, tuple[str, str, list[str]]]:
        """Project-level metadata: the inspector's "overall setup" fields
        shown when a folder or nothing at all is selected (1.12).

        Stored as one JSON string in the existing settings table — the
        channel paragraph styles already use — rather than a new table or
        a magic document id: no schema change, no migration, and a junk or
        hand-edited value degrades to no fields instead of failing.
        Entry shape matches metadata_entries: key -> (value, value_type,
        options).
        """
        raw = self.get_setting("project_metadata")
        try:
            stored = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            stored = {}
        if not isinstance(stored, dict):
            stored = {}
        entries: dict[str, tuple[str, str, list[str]]] = {}
        for key, row in stored.items():
            if not isinstance(key, str) or not isinstance(row, dict):
                continue
            value = row.get("value")
            options = row.get("options")
            entries[key] = (
                str(value) if value is not None else "",
                str(row.get("value_type", "text")),
                [str(option) for option in options] if isinstance(options, list) else [],
            )
        return entries

    def set_project_metadata(self, entries: dict[str, tuple[str, str, list[str]]]) -> None:
        """Replace project-level metadata with exactly ``entries`` (same
        replace-not-merge contract as set_metadata)."""
        payload = {
            key.strip(): {
                "value": value,
                "value_type": value_type,
                "options": options,
            }
            for key, (value, value_type, options) in entries.items()
            if key.strip()
        }
        self.set_setting(
            "project_metadata",
            json.dumps(payload, ensure_ascii=False, indent=1),
        )

    def book_settings(self, document_id: str) -> dict[str, str]:
        """Compile settings for one book folder (2.1).

        One JSON string in the existing settings table — the
        project_metadata precedent — so there is no schema change and junk
        degrades to no settings. Keys: "template" (the *name* of a
        template assigned to this folder — the explicit pick that wins
        over applicability, docs/TEMPLATES.md §2), "cover" (explicit
        image path; empty means auto-detect images/<folder_id>/cover.*)
        and "links" (2.2 strip policy for this book's documents; empty
        means follow the project default).
        """
        raw = self.get_setting("book_settings")
        try:
            stored = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            stored = {}
        if not isinstance(stored, dict):
            stored = {}
        row = stored.get(document_id)
        if not isinstance(row, dict):
            return {}
        return {
            key: str(row[key])
            for key in ("template", "cover", "links")
            if isinstance(row.get(key), str)
        }

    def set_book_settings(self, document_id: str,
                          settings: dict[str, str] | None) -> None:
        """Replace one folder's book settings; None or all-blank removes
        the entry, so the folder falls back to automatic matching."""
        raw = self.get_setting("book_settings")
        try:
            stored = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            stored = {}
        if not isinstance(stored, dict):
            stored = {}
        cleaned = {
            key: str(value).strip()
            for key, value in (settings or {}).items()
            if key in ("template", "cover", "links") and str(value).strip()
        }
        if cleaned:
            stored[document_id] = cleaned
        else:
            stored.pop(document_id, None)
        self.set_setting(
            "book_settings",
            json.dumps(stored, ensure_ascii=False, indent=1),
        )

    def page_setup(self, document_id: str) -> dict | None:
        """Print geometry for one book folder (1.15), or None — no
        override, exports keep python-docx's Letter defaults.

        A separate settings key from book_settings (not a row inside
        it): set_book_settings replaces its whole row, and a template
        tweak must not be able to wipe the print geometry. One JSON
        dict keyed by folder id, same shape as project_metadata; junk
        degrades to None through clean_page_setup."""
        raw = self.get_setting("page_setup")
        try:
            stored = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            return None
        if not isinstance(stored, dict):
            return None
        return clean_page_setup(stored.get(document_id))

    def set_page_setup(self, document_id: str, values: dict | None) -> None:
        """Replace one folder's print geometry; None removes the entry.
        An invalid dict raises ValueError rather than silently storing
        something export would then refuse to read."""
        # Paper belongs to a book, not to a paragraph (docs/UI-NOTES.md 7):
        # a folder may hold it even before it has contents, a chapter may not,
        # because a chapter is not a book. Clearing is allowed anywhere, so
        # stale data can always be tidied away.
        if values is not None:
            document = next((d for d in self.documents()
                             if d.id == document_id), None)
            if document is None:
                raise KeyError(document_id)
            if document.kind != "folder":
                raise ValueError(
                    "Page setup belongs to a folder, not to a chapter or a "
                    "document. Set it on the folder that holds this one, or "
                    "use a template that carries the trim."
                )
        cleaned = clean_page_setup(values) if values is not None else None
        if values is not None and cleaned is None:
            raise ValueError(f"invalid page setup: {values!r}")
        raw = self.get_setting("page_setup")
        try:
            stored = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            stored = {}
        if not isinstance(stored, dict):
            stored = {}
        if cleaned is None:
            stored.pop(document_id, None)
        else:
            stored[document_id] = cleaned
        self.set_setting(
            "page_setup",
            json.dumps(stored, ensure_ascii=False, indent=1),
        )

    def effective_page_setup(self, document_id: str) -> dict | None:
        """Print geometry in force for one document (1.15/3.2): the
        nearest ancestor folder's stored setup. None means no override
        anywhere; callers fall back to the default Letter box, as
        export does. A page printed on its own goes through
        standalone_page_setup (1.18) instead, which lets the page's own
        design have the last word."""
        raw = self.get_setting("page_setup")
        try:
            stored = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            return None
        if not isinstance(stored, dict):
            return None
        parents = {document.id: document.parent_id
                   for document in self.documents()}
        current: str | None = document_id
        while current is not None:
            if current in stored:
                cleaned = clean_page_setup(stored[current])
                if cleaned is not None:
                    return cleaned
            current = parents.get(current)
        return None

    def standalone_page_setup(self, document_id: str) -> dict | None:
        """Print geometry for one page printed on its own (1.18).

        The nearest book folder's setup seeds it as usual, but a
        standalone page is printed as its own design states it: a
        template outside the selected set may carry a Page size, and
        there that size is the truth. A book export keeps the book's
        trim and ignores it — silently, as the design's owner asked.
        """
        document = next((d for d in self.documents()
                         if d.id == document_id), None)
        if document is None:
            return None
        base = self.effective_page_setup(document_id)
        overlay = config_geometry(self.template_config(document.doc_template))
        if not overlay:
            return base
        candidate = dict(base or DEFAULT_PAGE_SETUP)
        candidate.update(overlay)
        return clean_page_setup(candidate)

    def resolve_template(self, name: str) -> TemplateSet | None:
        """A discovered template by name (docs/TEMPLATES.md §2), or None
        when the name matches nothing.

        None is a real answer, not a failure: an assignment naming a
        template that has since been deleted falls back to the default
        with a line in the export dialog, never an empty export. The
        names are ``named_template_sets``' — a project folder that shares
        a shipped set's name never answers to it, so ``default`` is
        always Lekhak's own (§1.1).
        """
        name = (name or "").strip()
        if not name:
            return None
        return named_template_sets(self.root).get(name)

    def _scope_folders(
            self, selected_ids: set[str] | None,
    ) -> list[tuple[int, Document]]:
        """The folders this export's scope covers, each with its depth
        from the project root — what applicability is computed from (§2).

        A whole-project export is every folder; a selection is each
        selected folder plus the ancestors that dress it, because a
        template assigned to a folder applies downward to everything
        under it.
        """
        documents = self.documents()
        by_id = {d.id: d for d in documents}
        depths: dict[str, int] = {}

        def depth_of(document_id: str) -> int:
            if document_id not in depths:
                parent = by_id.get(by_id[document_id].parent_id)
                depths[document_id] = (
                    0 if parent is None else depth_of(parent.id) + 1)
            return depths[document_id]

        found: dict[str, tuple[int, Document]] = {}

        def include(document: Document) -> None:
            if document.kind == "folder":
                found.setdefault(document.id, (depth_of(document.id), document))
            parent = (by_id.get(document.parent_id)
                      if document.parent_id else None)
            if parent is not None:
                include(parent)

        if selected_ids is None:
            for document in documents:
                if document.kind == "folder":
                    found[document.id] = (depth_of(document.id), document)
        else:
            for document_id in selected_ids:
                document = by_id.get(document_id)
                if document is not None:
                    include(document)
        return list(found.values())

    def template_choices(
            self, selected_ids: set[str] | None = None,
    ) -> tuple[list[TemplateSet], TemplateSet, list[str]]:
        """What the export chooser offers, ordered (§2, §2.1).

        Applicable templates first — every folder in the export's scope
        naming one in book_settings, nearest (deepest) first — then the
        rest of the project's templates, with the default always
        reachable as the fallback. Returns that list, the pre-selected
        entry (the most applicable, else the default), and the warnings:
        assignments naming templates that no longer exist, which fall
        back rather than exporting empty.

        Nothing here persists (§2.1's one-shot rule): the choice lives in
        the dialog that consumes this and is gone when it closes. A look
        that should stick is assigned to the folder, which is what the
        ordering above is computed from.
        """
        discovered = discover_template_sets(self.root)
        by_name = named_template_sets(self.root)
        default = discovered[0] if discovered else TemplateSet.builtin()

        applicable: list[tuple[int, int, TemplateSet]] = []
        warnings: list[str] = []
        named: set[str] = set()
        for depth, folder in self._scope_folders(selected_ids):
            name = self.book_settings(folder.id).get("template", "").strip()
            if not name or name in named:
                continue
            named.add(name)
            found = by_name.get(name)
            if found is None:
                warnings.append(
                    f"{folder.title}: '{name}' has no match — using default")
            else:
                applicable.append((depth, len(applicable), found))
        applicable.sort(key=lambda entry: (-entry[0], entry[1]))

        ordered = [entry[2] for entry in applicable]
        listed = {s.name for s in ordered}
        ordered += [s for s in discovered if s.name not in listed]
        if default.name not in {s.name for s in ordered}:
            ordered.append(default)     # always reachable as the fallback
        return ordered, (ordered[0] if ordered else default), warnings

    # -- templates as documents, and one-file templates (1.17) --------------

    def is_template(self, document_id: str) -> bool:
        """Whether a document is a template (1.17): its body is markup
        rather than manuscript, so it stays out of the book and the
        sidebar shows the template design panel instead."""
        value = self.metadata(document_id).get(TEMPLATE_KEY, "")
        return value.strip().lower() in ("yes", "true", "1")

    def set_template(self, document_id: str, on: bool, *, _force: bool = False, confirmed: bool = False) -> None:
        """Mark a document as a template, or take the mark away.

        The mark is the document's own metadata (and the file's head
        beside it) and nothing else: it says what the document *is*, and
        every export asks. The row is written on its own, so replacing
        the document's metadata elsewhere never unmakes the template.

        Turning the mark ON is allowed only when the document is empty
        of content and of other metadata: the FIRST metadata of a new
        folder/document must be Template: yes. A document that already
        holds prose or any other field is refused (ValueError) — it
        cannot be converted. The normal way to make templates once per
        project is to create the templates folder first, with Template:
        yes as its first field, and put the templates inside it. A
        folder with children that already hold content is refused for
        the same reason: a template folder starts empty and its
        children are templates from birth.
        """
        if on:
            if self.is_template(document_id):
                return
            if _force or confirmed:
                # Internal trusted copy (default template set shipped
                # with Lekhak), or a user-confirmed conversion after the
                # warning popup — not an accidental click.
                self.db.execute(
                    "INSERT INTO document_metadata"
                    "(document_id,key,value,value_type,options_json) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(document_id,key) DO UPDATE SET value=excluded.value",
                    (document_id, TEMPLATE_KEY, "yes", "text", "[]"),
                )
                self.db.commit()
                return
            # Refuse to convert content to template.
            try:
                body = self.read_content(document_id).strip()
            except Exception:
                body = ""
            if body:
                raise ValueError(
                    "Cannot convert to template: the document already "
                    "contains content. Templates start empty — the first "
                    "metadata of a new folder/document must be "
                    "Template: yes, before any prose is written.")
            other = {k: v for k, v in self.metadata(document_id).items()
                     if k not in (TEMPLATE_KEY, TEMPLATE_CONFIG_KEY, "Type")}
            # Only allow if no other meaningful values.
            if any(str(v).strip() not in ("", "0") for v in other.values()):
                raise ValueError(
                    "Cannot convert to template: the document already "
                    "holds metadata. Templates start empty — the first "
                    "metadata must be Template: yes; remove the existing "
                    "fields or start a new empty folder/document.")
            # Folder: refuse if any descendant holds content already.
            children = [d for d in self.documents()
                        if d.parent_id == document_id]
            if children:
                for child in children:
                    try:
                        child_body = self.read_content(child.id).strip()
                    except Exception:
                        child_body = ""
                    if child_body:
                        raise ValueError(
                            "Cannot convert to template: the folder already "
                            "contains content. Templates start empty — "
                            "create the templates folder first with "
                            "Template: yes, before adding content.")
                    child_meta = self.metadata(child.id)
                    if any(str(v).strip() not in ("", "0")
                           for k, v in child_meta.items()
                           if k not in (TEMPLATE_KEY, "Type")):
                        raise ValueError(
                            "Cannot convert to template: the folder's "
                            "children already hold metadata. Templates "
                            "start empty.")
            self.db.execute(
                "INSERT INTO document_metadata"
                "(document_id,key,value,value_type,options_json) VALUES(?,?,?,?,?) "
                "ON CONFLICT(document_id,key) DO UPDATE SET value=excluded.value",
                (document_id, TEMPLATE_KEY, "yes", "text", "[]"),
            )
        else:
            self.db.execute(
                "DELETE FROM document_metadata WHERE document_id=? AND key=?",
                (document_id, TEMPLATE_KEY),
            )
        self.db.commit()

    def template_documents(self) -> list[Document]:
        """The documents that are templates (1.17)."""
        return [document for document in self.documents()
                if self.is_template(document.id)]

    def single_file_templates(self) -> list[Path]:
        """The one-file templates in the project's templates folder (1.17).

        A template is one file with its yaml in the head — no folder of
        slots beside it — so these sit directly under templates/, where
        the sets' own folders sit.
        """
        root = project_template_root(self.root)
        if not root.is_dir():
            return []
        return [path for path in sorted(root.iterdir())
                if path.is_file() and path.suffix.lower() in (".html", ".txt")]

    def template_fragment(self, name: str, *, html: bool = True) -> str | None:
        """A one-file template's fragment (1.17), or None when no such
        template answers to the name."""
        for path in self.single_file_templates():
            if path.stem != name or (path.suffix.lower() == ".html") != html:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                return None
            return split_head(text)[1]
        return None

    def template_config(self, name: str) -> dict[str, str]:
        """The design of the template a name stands for (1.17), by key.

        A one-file template keeps its design in its own head; a template
        document keeps it in its metadata, written into the file's head
        beside it. Keys come back normalized, the way the print
        config's keys are read.
        """
        name = (name or "").strip()
        if not name:
            return {}
        if name.startswith(TEMPLATE_DOC_PREFIX):
            document = next(
                (d for d in self.documents()
                 if d.id == name[len(TEMPLATE_DOC_PREFIX):]), None)
            if document is None:
                return {}
            return parse_docx_config(
                self.metadata(document.id).get(TEMPLATE_CONFIG_KEY, ""))
        for path in self.single_file_templates():
            if path.stem != name:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                return {}
            return parse_docx_config(split_head(text)[0])
        return {}

    def template_override(self, name: str, *, html: bool = True) -> str | None:
        """The fragment of the template a document names (1.17), or None.

        A template document answers first — its own body is the markup —
        then a one-file template; the set's own slot answers when
        neither does, which the caller asks for separately.
        """
        name = (name or "").strip()
        if not name:
            return None
        if name.startswith(TEMPLATE_DOC_PREFIX):
            document = next(
                (d for d in self.documents()
                 if d.id == name[len(TEMPLATE_DOC_PREFIX):]), None)
            if document is None:
                return None
            return self.read_content(document.id)
        if name.startswith(TEMPLATE_FILE_PREFIX):
            return self.template_fragment(name[len(TEMPLATE_FILE_PREFIX):],
                                          html=html)
        return self.template_fragment(name, html=html)

    @staticmethod
    def config_lines(text: str) -> dict[str, str]:
        """The `key: value` lines of a config block, keys as written."""
        lines: dict[str, str] = {}
        for line in (text or "").split("\n"):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, separator, value = line.partition(":")
            if not separator or not key.strip():
                continue
            lines[key.strip()] = value.strip()
        return lines

    def _template_resolver(self, fallback: TemplateSet, fmt: str = "html"):
        """The set that renders each document (§2's first rule).

        Explicit metadata always wins: the nearest ancestor folder naming
        a template in book_settings dresses its own subtree, and
        everything else uses the set the export chose. Returns a function
        from document id to its set, resolving a chain once and caching
        only as far as the folder that decided — an ancestor above that
        one may yet be decided nearer to a different branch.

        Every set is asked for the export's own format first (§1.3): a
        named set refines itself for this format exactly as the chosen
        one does, so an assignment never silently drops the refinement.
        """
        by_id = {d.id: d for d in self.documents()}
        fallback = fallback.for_format(fmt)
        by_name = {name: s.for_format(fmt)
                   for name, s in named_template_sets(self.root).items()}
        resolved: dict[str, TemplateSet] = {}

        def resolve(document_id: str) -> TemplateSet:
            if document_id in resolved:
                return resolved[document_id]
            chain: list[Document] = []
            current = by_id.get(document_id)
            while current is not None:
                chain.append(current)
                current = (by_id.get(current.parent_id)
                           if current.parent_id else None)
            chosen, decided_at = fallback, None
            for folder in chain:            # nearest first
                name = self.book_settings(folder.id).get(
                    "template", "").strip()
                found = by_name.get(name) if name else None
                if found is not None:
                    chosen, decided_at = found, folder.id
                    break
            for node in chain:
                resolved[node.id] = chosen
                if node.id == decided_at:
                    break                   # the rest resolves on its own walk
            return chosen

        return resolve

    def _cover_folders(self, selected_ids: set[str] | None) -> list[Document]:
        """Folders whose cover may dress this export, in binder order (§5).

        The folders the export actually resolves to: any folder picked
        directly, plus every folder holding an exported document. With no
        selection it is every folder in the manuscript, so a whole-project
        export finds the first book with a cover.
        """
        documents = self.ordered_documents()
        by_id = {d.id: d for d in documents}
        wanted: dict[str, None] = {}
        if selected_ids:
            for document_id in selected_ids:
                folder = by_id.get(document_id)
                if folder is not None and folder.kind == "folder":
                    wanted.setdefault(folder.id, None)
        for document in self._export_documents(selected_ids):
            current = by_id.get(document.parent_id) if document.parent_id else None
            while current is not None:
                wanted.setdefault(current.id, None)
                current = by_id.get(current.parent_id) if current.parent_id else None
        return [by_id[document_id] for document_id in wanted]

    def effective_cover(self, document_id: str) -> Path | None:
        """The book's cover image (2.1): an explicit path from book
        settings if that file exists, otherwise the conventional
        images/<folder_id>/cover.* — the stand-in for "an image present
        in the folder"."""
        raw = self.book_settings(document_id).get("cover", "").strip()
        if raw:
            path = Path(raw)
            if not path.is_absolute():
                path = self.root / path
            return path if path.is_file() else None
        directory = self.root / "images" / document_id
        if directory.is_dir():
            for suffix in COVER_SUFFIXES:
                candidate = directory / f"cover.{suffix}"
                if candidate.is_file():
                    return candidate
        return None

    def set_metadata(self, document_id: str, values: dict[str, str],
                     value_types: dict[str, str] | None = None,
                     options: dict[str, list[str]] | None = None) -> None:
        """Replace a document's metadata with exactly ``values``.

        Not a merge: keys absent from ``values`` are removed. Callers that
        want to preserve existing keys read them first with
        metadata_entries() and pass the combined dict back.

        The template's own mark and design are the exception (1.17):
        they say what the document *is*, not what its metadata table
        holds, so a caller replacing the set does not silently unmake a
        template. Passing them explicitly replaces them as asked.
        """
        cleaned = {key.strip(): value for key, value in values.items() if key.strip()}
        value_types = dict(value_types or {})
        options = dict(options or {})
        for key in (TEMPLATE_KEY, TEMPLATE_CONFIG_KEY):
            if key in cleaned:
                continue
            row = self.db.execute(
                "SELECT value,value_type,options_json FROM document_metadata "
                "WHERE document_id=? AND key=?", (document_id, key)).fetchone()
            if row is None:
                continue
            cleaned[key] = row[0]
            value_types.setdefault(key, row[1] or "text")
            try:
                options.setdefault(key, json.loads(row[2] or "[]"))
            except (TypeError, ValueError):
                options.setdefault(key, [])
        with self.db:
            self.db.execute("DELETE FROM document_metadata WHERE document_id=?", (document_id,))
            self.db.executemany(
                "INSERT INTO document_metadata"
                "(document_id,key,value,value_type,options_json) VALUES(?,?,?,?,?)",
                [
                    (
                        document_id, key, value, value_types.get(key, "text"),
                        json.dumps(options.get(key, []), ensure_ascii=False),
                    )
                    for key, value in cleaned.items()
                ],
            )
        # Keep the file's head in step (docs/STORAGE.md): a metadata
        # edit that only touches the DB would leave the file carrying
        # yesterday's title/synopsis/etc. until the next prose save.
        # _with_metadata rebuilds the head from the DB, so rewriting
        # the prose unchanged is enough. Custom metadata that lives
        # only in the DB (POV, Loc…) intentionally stays out of the
        # head — the file carries the subset it can read back.
        self._refresh_metadata_on_disk(document_id)

    def create_snapshot(self, document_id: str, name: str) -> Snapshot:
        """Copy a document's current content and synopsis into the snapshot store.

        Called before any operation that rewrites a document (split,
        merge, restore) so the previous state stays reachable.
        """
        snapshot_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        row = self.db.execute(
            "SELECT synopsis FROM documents WHERE id=?", (document_id,)
        ).fetchone()
        if row is None:
            raise KeyError(document_id)
        self.db.execute(
            "INSERT INTO snapshots(id,document_id,name,html,synopsis,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (snapshot_id, document_id, name, self.read_content(document_id), row[0], created_at),
        )
        self.db.commit()
        return Snapshot(snapshot_id, document_id, name, created_at)

    def snapshots(self, document_id: str) -> list[Snapshot]:
        """A document's snapshots, newest first. Content is read separately."""
        rows = self.db.execute(
            "SELECT id,document_id,name,created_at FROM snapshots "
            "WHERE document_id=? ORDER BY created_at DESC", (document_id,)
        ).fetchall()
        return [Snapshot(**dict(row)) for row in rows]

    def restore_snapshot(self, snapshot_id: str) -> str:
        """Write a snapshot's content back over its document, then return the id.

        Snapshots the state being replaced first, so a restore is itself
        reversible — you can always get back to what you just left.
        """
        row = self.db.execute(
            "SELECT document_id,html,synopsis FROM snapshots WHERE id=?", (snapshot_id,)
        ).fetchone()
        if row is None:
            raise KeyError(snapshot_id)
        document_id, html, synopsis = row
        # Preserve the state being replaced so restore is itself reversible.
        self.create_snapshot(document_id, "Before snapshot restore")
        self.write_content(document_id, html)
        self.update_document(document_id, synopsis=synopsis)
        return document_id

    # --- tracked changes (2.4) ---------------------------------------------
    # Pending rows only: accept/reject deletes the row, the mark in the
    # HTML is the other half of the pair, and reconcile_revisions prunes
    # whichever half an undo left behind.

    def add_revision(self, document_id: str, session_id: str, kind: str,
                     author: str, sequence: int) -> Revision:
        revision = Revision(
            id=uuid.uuid4().hex,
            document_id=document_id,
            session_id=session_id,
            kind=kind,
            author=author,
            created_at=datetime.now(timezone.utc).isoformat(),
            sequence=sequence,
        )
        self.db.execute(
            "INSERT INTO revisions"
            "(id,document_id,session_id,kind,author,created_at,sequence)"
            " VALUES (?,?,?,?,?,?,?)",
            (revision.id, revision.document_id, revision.session_id,
             revision.kind, revision.author, revision.created_at,
             revision.sequence),
        )
        self.db.commit()
        return revision

    def revisions(self, document_id: str) -> list[Revision]:
        """One document's pending changes, in creation order."""
        rows = self.db.execute(
            "SELECT id,document_id,session_id,kind,author,created_at,sequence"
            " FROM revisions WHERE document_id=? ORDER BY sequence",
            (document_id,),
        ).fetchall()
        return [Revision(*row) for row in rows]

    def revision_sessions(self, document_id: str) -> list[dict]:
        """Pending revision passes for one document, oldest first — what
        the accept/reject dialog lists. Each entry carries the session id,
        the author, the timestamp of its first change and its size."""
        rows = self.db.execute(
            "SELECT session_id, author, MIN(created_at), COUNT(*)"
            " FROM revisions WHERE document_id=?"
            " GROUP BY session_id ORDER BY MIN(created_at)",
            (document_id,),
        ).fetchall()
        return [
            {"session_id": row[0], "author": row[1], "created_at": row[2],
             "count": row[3]}
            for row in rows
        ]

    def remove_revisions(self, revision_ids: list[str]) -> None:
        """Delete pending rows (the change was accepted or rejected)."""
        if not revision_ids:
            return
        self.db.executemany(
            "DELETE FROM revisions WHERE id=?", [(rid,) for rid in revision_ids])
        self.db.commit()

    def restore_revisions(self, rows: list[Revision]) -> None:
        """Put rows back after a failed write, under their own ids.

        Accept/reject deletes the rows and then writes the resolved text;
        if that write fails, the marks are still on disk and the rows are
        the only thing that can ever address them. Same ids, not fresh
        ones: the live document still carries the old ids in its marks,
        and a new id would make the next accept look for a mark that
        cannot exist.
        """
        for revision in rows:
            self.db.execute(
                "INSERT INTO revisions"
                "(id,document_id,session_id,kind,author,created_at,sequence)"
                " VALUES (?,?,?,?,?,?,?)",
                (revision.id, revision.document_id, revision.session_id,
                 revision.kind, revision.author, revision.created_at,
                 revision.sequence),
            )
        self.db.commit()

    def reconcile_revisions(self, document_id: str) -> None:
        """Drop pending rows whose marks no longer exist (2.4).

        Undo/redo can remove marked text without telling the revisions
        table; after a save the stored HTML is the truth. Count the
        insert and delete spans, keep the first N rows of each kind in
        sequence order, drop the rest. The reverse mismatch — marks with
        no rows — leaves those spans rendering but unaddressable until
        the next accept/reject pass; the table never invents rows.
        """
        html = self.read_content(document_id)
        limits = {
            "insert": len(INSERT_SPAN_RE.findall(html)),
            "delete": len(DELETE_SPAN_RE.findall(html)),
        }
        seen = {"insert": 0, "delete": 0}
        doomed: list[str] = []
        for revision in self.revisions(document_id):
            seen[revision.kind] += 1
            if seen[revision.kind] > limits.get(revision.kind, 0):
                doomed.append(revision.id)
        self.remove_revisions(doomed)

    def ordered_documents(self) -> list[Document]:
        # documents() already walks the tree in binder order
        return self.documents()

    def _export_documents(self, selected_ids: set[str] | None = None
                          ) -> list[Document]:
        """The chapters and scenes to compile, in binder order.

        Folder ids in ``selected_ids`` expand to their contents — picking
        a book folder exports the book. None selects the whole
        manuscript. Templates are format, not content, so the
        ``templates/`` subtree is never manuscript even though it is a
        normal binder folder.
        """
        documents = self.ordered_documents()
        parent_map = {d.id: d.parent_id for d in documents}
        export_docs: list[Document] = []
        for document in documents:
            if document.kind not in EXPORT_KINDS:
                continue
            if self.is_template(document.id):
                continue        # a template is markup, not manuscript
            if document.path and document.path.split("/")[0].casefold() == TEMPLATES_FOLDER:
                continue        # templates handle the format, not the content
            if document.path and document.path.split("/")[0].casefold() == TRASH_FOLDER:
                continue        # trashed content is not manuscript either
            current: str | None = document.id
            covered = selected_ids is None
            while current is not None:
                if selected_ids is not None and current in selected_ids:
                    covered = True
                current = parent_map.get(current)
            if covered:
                export_docs.append(document)
        return export_docs

    def _type_name(self, document_id: str, kind: str) -> str:
        """A document's Type from its id and kind — type_of without a row."""
        stored = self.metadata(document_id).get("Type", "").strip()
        return stored or BASE_TYPE["folder" if kind == "folder" else "document"]

    def _type_position(self, document_id: str, parent_id: str | None,
                       type_name: str) -> int:
        """Its place among the siblings that share its Type, counting from 1.

        Numbering follows meaning, not raw order (3.2): a title page, a
        dedication and a contents page above the first chapter do not push
        the chapters to 2, 3, 4 — they are other Types. With no Type stored
        every sibling shares the default Document/Folder, so this is plain
        sibling order.
        """
        rows = self.db.execute(
            "SELECT d.id, d.kind, m.value FROM documents d "
            "LEFT JOIN document_metadata m ON m.document_id = d.id "
            "AND m.key = 'Type' WHERE d.parent_id IS ? ORDER BY d.position",
            (parent_id,)).fetchall()
        wanted = type_name.casefold()
        count = 0
        for row_id, kind, stored in rows:
            group = "folder" if kind == "folder" else "document"
            name = (stored or "").strip() or BASE_TYPE[group]
            if name.casefold() == wanted:
                count += 1
            if row_id == document_id:
                return count
        return 1

    def _position_path(self, document: Document) -> list[int]:
        """One-based positions from the book down to this document (3.2).

        Each level is the document's place among its siblings of the same
        Type; the outermost folder is the book the export dresses, so it is
        not a numbered level — a chapter directly inside it is 1, 2, 3, not
        1.1, 1.2, and a scene inside that chapter is 1.1.
        """
        levels = [(document.id, document.parent_id, document.kind)]
        while levels[-1][1] is not None:
            row = self.db.execute(
                "SELECT id, parent_id, kind FROM documents WHERE id=?",
                (levels[-1][1],)).fetchone()
            if row is None:
                break
            levels.append((row[0], row[1], row[2]))
        if len(levels) > 1 and levels[-1][2] == "folder":
            levels.pop()
        path = [self._type_position(document_id, parent_id,
                                    self._type_name(document_id, kind))
                for document_id, parent_id, kind in levels]
        path.reverse()
        return path

    def _template_values(self, document: Document, content: str | None = None,
                         extra: dict[str, str] | None = None,
                         fmt: str = "", pages_total: int | None = None) -> dict[str, str]:
        if content is None:
            content = self.read_content(document.id)
        path = self._position_path(document)
        values = {
            "Title": document.title,
            "Position": str(path[-1]),
            "Position-spell": _spelled_position(path[-1]),
            "Kind": document.kind,
            "Synopsis": document.synopsis,
            "WordCount": str(document.word_count),
            "Content": content,
            # The project's own name, which is not the document's title:
            # a project may hold several books, and ${Title} is whatever
            # document is being dressed.
            "Project": self.get_setting("title", self.root.stem),
        }
        # ${Parent}: the immediate container's title. Empty at the root.
        parent = None
        if document.parent_id:
            parent = next((d for d in self.documents()
                           if d.id == document.parent_id), None)
        values["Parent"] = parent.title if parent is not None else ""
        # ${Format}: the format being written, empty outside an export.
        values["Format"] = fmt
        values["EstimatedPages"] = str(pages_total) if pages_total else ""
        # ${PageNo}: the page's own number, positioned where the writer
        # puts it — a Header/Footer line, or a paragraph of the
        # manuscript. We never count pages here; the marker survives the
        # text and becomes Word's PAGE field in the DOCX printer (3.1).
        # Formats without fixed pages (HTML, EPUB, text) print nothing.
        # ${Page} is the spelling the first templates used, and stays.
        values["PageNo"] = values["Page"] = (
            PAGE_MARKER if fmt in ("docx", "pdf") else "")
        # ${Position-depth:N} is the path cut to its N most specific
        # numbers, so one name answers for a chapter (1) and a scene
        # (1.1). Depths past the path's own length print the whole path.
        for depth in range(1, max(4, len(path)) + 1):
            values[f"Position-depth:{depth}"] = ".".join(
                str(number) for number in path[-depth:])
        # One namespace (docs/TEMPLATES.md 3.2): a metadata key answers
        # under its own name and its Metadata. alias. The document's
        # non-empty value wins over the project's — an empty field means
        # "inherit", not "blank it" — and a built-in name wins over both.
        metadata = {key: entry[0] for key, entry in self.project_metadata().items()
                    if entry and entry[0].strip()}
        # The document's own value, then the nearest folder that has one,
        # then the next one up: a field set on a folder is the value for
        # everything inside it.
        for key, value in self.metadata_inherited(document.id).items():
            if value.strip():
                metadata[key] = value
        for key, value in metadata.items():
            values.setdefault(key, value)
            values[f"Metadata.{key}"] = value
        if extra:
            values.update(extra)
        return values

    def render_template(self, document: Document, template: str,
                        *, html: bool = False, fmt: str = "",
                        pages_total: int | None = None) -> str:
        """Fill a one-off template string with ``${...}`` placeholders."""
        from .templates import PLACEHOLDER_RE

        values = self._template_values(document, fmt=fmt,
                                      pages_total=pages_total)
        if not html:
            values["Content"] = self.plain_text(values["Content"])

        def substitute(match: re.Match) -> str:
            key = match.group(1).strip()
            value = values.get(key, "")
            if html and key.lower() != "content":
                return escape(value)
            return value

        return PLACEHOLDER_RE.sub(substitute, template)

    def expand_shortcodes(self, document: Document, text: str, *,
                          html: bool,
                          extra: dict[str, str] | None = None,
                          fmt: str = "",
                          pages_total: int | None = None) -> str:
        """Expand ``${...}`` shortcodes inside a document's own prose.

        Compile-time only: the stored file keeps the literal markers and
        the editor shows them — the pull happens once, when an export
        lifts the content, exactly as [[wiki links]] resolve at export
        rather than at rest. The vocabulary is the slots' own (sidebar
        ``${Metadata.Key}`` rows included, through _template_values);
        ``${Content}`` never expands inside prose — it would mean the
        prose itself — and conditionals stay out as documented: prose
        gets values, not control flow. Unknown keys render empty,
        exactly as in a slot.
        """
        from .templates import PLACEHOLDER_RE

        if "${" not in text:
            return text
        values = self._template_values(document, content="", extra=extra,
                                      fmt=fmt, pages_total=pages_total)
        values.pop("Content", None)

        def substitute(match: re.Match) -> str:
            key = match.group(1).strip()
            value = values.get(key, "")
            return escape(value) if html else value

        # `$$` is a literal `$`: the escape that lets a document talk
        # about a placeholder instead of being one. Each part between the
        # escapes is expanded on its own, so `$${Name}` prints `${Name}`
        # and never reaches the expander.
        parts = text.split("$$")
        for index, part in enumerate(parts):
            parts[index] = PLACEHOLDER_RE.sub(substitute, part)
        return "$".join(parts)

    def _nearest_chapter_ancestor(self, document_id: str,
                                  parent_map: dict[str, str | None],
                                  kinds: dict[str, str]) -> str | None:
        current = parent_map.get(document_id)
        while current is not None:
            if kinds.get(current) == "chapter":
                return current
            current = parent_map.get(current)
        return None

    def _nearest_container_ancestor(self, document_id: str,
                                   parent_map: dict[str, str | None],
                                   kinds: dict[str, str],
                                   export_ids: set[str]) -> str | None:
        """The nearest exported container above this document.

        A container unit (chapter or folder) renders its children inside
        itself; a child whose nearest exported container is in scope
        belongs to that container's unit rather than standing alone.
        """
        current = parent_map.get(document_id)
        while current is not None:
            if current in export_ids and kinds.get(current) in ("chapter", "folder"):
                return current
            current = parent_map.get(current)
        return None

    def _render_document(self, document: Document, templates: TemplateSet,
                         html: bool, *, slot: str | None = None,
                         content: str | None = None,
                         extra: dict[str, str] | None = None,
                         scope: dict | None = None,
                         fmt: str = "html",
                         pages_total: int | None = None,
                         children: dict[str, list[str]] | None = None) -> str:
        if content is None:
            # The document's own stored prose: links rewritten for this
            # export, markup stripped in text mode, shortcodes expanded.
            # An assembled body (content=) arrives the other way round —
            # prepared part by part by its caller and already rendered —
            # so running these over it would eat the fragments' own
            # characters ("<" in a scene fragment, for one) and rewrite
            # links that were resolved when each part was built.
            body = self.read_content(document.id)
            if scope is not None:
                body = self._prepare_links(body, source_id=document.id,
                                           scope=scope, fmt=fmt, html=html)
            if not html:
                body = self.plain_text(body)
            body = self.expand_shortcodes(document, body, html=html,
                                          extra=extra, fmt=fmt,
                                          pages_total=pages_total)
        else:
            body = content
        values = self._template_values(document, content=body, extra=extra,
                                      fmt=fmt, pages_total=pages_total)
        # Contents page / the children a container holds: filled when the
        # template asks. The name is `Documents` because that is what they
        # are — English plurals of a Type would be guesswork, and the
        # children of a folder need not be scenes. `Scenes` and
        # `Children` are the older names for the same value, kept so a
        # template written before the rename keeps working.
        values["ToC"] = getattr(self, "_toc_str", "")
        children_value = (extra or {}).get("children", "")
        # The children family: `children` is all of them, `children-<type>`
        # is the ones of one kind — the word the writer gave it. Only the
        # word itself is kept: a qualifier is matched against the types
        # that are here, so there is no table of plurals to maintain.
        # One word, one name: nothing else is reserved, so a field of the
        # writer's own called "Scenes" is theirs again.
        values["children"] = children_value
        for name, rendered in (children or {}).items():
            values[CHILDREN_NAME + "-" + name.casefold()] = "\n".join(rendered)
        silent = templates.prints_nothing

        def placed(text: str, raw: str) -> str:
            """A container's children are never lost (§3): `${children}`
            says *where* they go, and a fragment that never names the
            family has them appended after itself. The placeholder
            positions the children; it does not gate them, so a chapter
            cannot swallow its scenes by saying nothing."""
            if silent or not children_value.strip() or _names_children(raw):
                return text
            return text + children_value

        if document.doc_template:
            override = self.template_override(document.doc_template, html=html)
            if override is None:
                override = templates.document_slot(document.doc_template, html=html)
            if override is not None:
                return placed(templates.fill(override, values, html=html),
                              override)
        # Nothing stored: this document's set may carry a face named after
        # the document's own Type (1.18) — the naming convention, read at
        # export as well as in the sidebar, so a project typed by hand
        # dresses the same way whether or not a row was ever opened.
        type_face = self.type_of(document).casefold()
        derived = next(
            (stem for stem in templates.document_template_names()
             if stem.casefold() == type_face), "")
        if derived:
            override = templates.document_slot(derived, html=html)
            if override is not None:
                return placed(templates.fill(override, values, html=html),
                              override)
        if slot is None:
            slot = "folder" if document.kind == "chapter" else "document"
        return placed(templates.render(slot, values, html=html),
                      templates.slot(slot, html=html))

    def _compile_scope(self, selected_ids: set[str] | None,
                       fmt: str) -> dict:
        """Everything one compile's links need to know (2.2): the exported
        document ids, each document's unit index (shared by a chapter and
        its scenes, 0-based), the ancestor chain used to look up per-book
        settings, an exact-title resolution index over the whole binder,
        and the project-wide strip policy.

        Split out of compile_units so header/footer strings — compiled
        outside the unit list — run through the same machinery.
        """
        export_docs = self._export_documents(selected_ids)
        export_ids = {d.id for d in export_docs}
        all_docs = self.documents()
        parent_map = {d.id: d.parent_id for d in all_docs}
        kinds = {d.id: d.kind for d in all_docs}
        chapter_scenes: dict[str, list[Document]] = {}
        ordered: list[Document] = []
        for document in export_docs:
            chapter_id = self._nearest_chapter_ancestor(document.id, parent_map, kinds)
            if document.kind == "scene" and chapter_id in export_ids:
                chapter_scenes.setdefault(chapter_id, []).append(document)
            else:
                ordered.append(document)
        doc_unit: dict[str, int] = {}
        for index, document in enumerate(ordered):
            doc_unit[document.id] = index
            if document.kind == "chapter":
                for scene in chapter_scenes.get(document.id, []):
                    doc_unit[scene.id] = index
        titles: dict[str, list[Document]] = {}
        for document in all_docs:
            titles.setdefault(document.title.casefold(), []).append(document)
        try:
            settings = json.loads(self.get_setting("book_settings"))
        except (TypeError, ValueError):
            settings = {}
        if not isinstance(settings, dict):
            settings = {}
        try:
            page_setup = json.loads(self.get_setting("page_setup"))
        except (TypeError, ValueError):
            page_setup = {}
        if not isinstance(page_setup, dict):
            page_setup = {}
        policy = self.get_setting("link_policy")
        # Page starts (Phase 1): resolved once, here, so every exporter agrees.
        # An explicit setting wins nearest-first (document, then ancestors);
        # what is left over is a new page per document, which is the baseline
        # the author asked for — "no point mind-reading users".
        by_id = {document.id: document for document in all_docs}
        containers = {document.parent_id for document in all_docs}

        def has_trim(document) -> bool:
            """True when a real print trim governs this document — the
            condition the author set for recto chapter openers."""
            current: str | None = document.id
            while current is not None:
                if current in page_setup and clean_page_setup(page_setup[current]):
                    return True
                current = parent_map.get(current)
            return False

        breaks: dict[str, str] = {}
        explicit_breaks: set[str] = set()
        for document in export_docs:
            value = "auto"
            current: str | None = document.id
            while current is not None:
                candidate = by_id[current].page_break if current in by_id else "auto"
                if candidate in PAGE_BREAK_VALUES and candidate != "auto":
                    value = candidate
                    break
                current = parent_map.get(current)
            if value == "auto":
                # A book's chapters (and its other containers) open recto once
                # a print trim is set; everything else simply starts a page.
                value = ("right" if has_trim(document)
                         and (document.kind == "chapter"
                              or document.id in containers)
                         else "page")
            else:
                # Somebody said so — a document's own page start or an
                # ancestor folder's. Names here, for the print config's
                # `New Chapter:` default to sit *under* rather than over.
                explicit_breaks.add(document.id)
            breaks[document.id] = value
        # The link-policy fallback chain: the book's own setting (above),
        # then the app-level default from Preferences → Links & export,
        # then the shipped constant — so a fresh install needs no stored
        # value and an invalid hand-edit heals (UI-NOTES 4; docs/PREFERENCES.md).
        app_default = str(QSettings().value(
            "link_policy/default", DEFAULT_LINK_POLICY))
        return {
            "export_docs": export_docs,
            "breaks": breaks,
            "export_ids": export_ids,
            "ordered": ordered,
            "chapter_scenes": chapter_scenes,
            "parent_map": parent_map,
            "doc_unit": doc_unit,
            "titles": titles,
            "book_settings": settings,
            "page_setup": page_setup,
            # Which page starts were *chosen* (document or folder) rather
            # than defaulted: the print config's `New Chapter`/`New Scene`
            # default applies to the defaulted ones only (§3.1).
            "explicit_breaks": explicit_breaks,
            "global_policy": policy if policy in LINK_POLICIES
            else (app_default if app_default in LINK_POLICIES
                  else DEFAULT_LINK_POLICY),
        }

    def _link_policy(self, scope: dict, source_id: str | None) -> str:
        """Strip policy in force for one source document (2.2): the first
        ancestor folder with a book-level "links" override wins, else the
        project default."""
        settings = scope["book_settings"]
        current = source_id
        while current is not None:
            row = settings.get(current)
            if isinstance(row, dict) and row.get("links") in LINK_POLICIES:
                return str(row["links"])
            current = scope["parent_map"].get(current)
        return scope["global_policy"]

    def _page_setup_for_scope(self, scope: dict) -> dict | None:
        """Print geometry in force for one export (1.15).

        The nearest ancestor folder with a page setup, walking the
        exported documents in binder order — same walk as _link_policy.
        One file can carry only one paper size, so a multi-book export
        takes the first book in scope that has one (recorded edge)."""
        setups = scope["page_setup"]
        if not isinstance(setups, dict):
            return None
        for document in scope["export_docs"]:
            current = document.id
            while current is not None:
                if current in setups:
                    cleaned = clean_page_setup(setups[current])
                    if cleaned is not None:
                        return cleaned
                current = scope["parent_map"].get(current)
        return None

    def _prepare_links(self, content: str, *, source_id: str | None,
                       scope: dict, fmt: str, html: bool) -> str:
        """Rewrite ``[[wiki]]`` markers for one export (2.2).

        A link whose exact title is in the exported set becomes a real
        link where the format can carry one — a fragment anchor for html,
        a section file for epub, a token pair for docx — and plain words
        for text. Everything else (broken, ambiguous, out of the set —
        matching the editor, which never auto-opens a substring
        candidate) follows the source's strip policy. Runs on raw read
        content, before plain_text; a second pass finds no markers and is
        a no-op. The compile-time content pass also renders pending
        tracked changes (2.4) as accept-all would first, so exports never
        print revision marks.
        """
        content = self.accepted_html(content)
        if not self.WIKI_LINK_RE.search(content):
            return content

        def replace(match: re.Match) -> str:
            target = match.group(1).strip()
            text = escape(target) if html else target
            documents = scope["titles"].get(target.casefold())
            resolved = documents[0] if documents else None
            if resolved is not None and resolved.id in scope["export_ids"]:
                unit = scope["doc_unit"][resolved.id] + 1
                if fmt == "html":
                    return f"<a href='#unit-{unit}'>{text}</a>"
                if fmt == "epub":
                    return f"<a href='section-{unit}.xhtml'>{text}</a>"
                if fmt in ("docx", "pdf"):
                    return f"\x02{resolved.id}\x03{target}\x04"
                return text
            policy = self._link_policy(scope, source_id)
            if policy == "strip":
                return ""
            if policy == "keep-as-note":
                suffix = (" (not in this export)" if not html
                          else escape(" (not in this export)"))
                return f"{text}{suffix}"
            return text

        return self.WIKI_LINK_RE.sub(replace, content)

    def rewrite_wiki_links(self, text: str,
                           selected_ids: set[str] | None = None, *,
                           fmt: str = "html", html: bool = True) -> str:
        """Link pass for strings compiled outside compile_units — the
        project header/footer slots (2.2). No markers, no scope build;
        with markers, this compile's whole link set is consulted, with the
        global policy (header/footer belongs to no book)."""
        if not text or not self.WIKI_LINK_RE.search(text):
            return text
        scope = self._compile_scope(selected_ids, fmt)
        return self._prepare_links(text, source_id=None, scope=scope,
                                   fmt=fmt, html=html)

    def compile_units(self, selected_ids: set[str] | None = None,
                      templates: TemplateSet | None = None,
                      *, html: bool = True, fmt: str = "html", _toc: bool = True
                      ) -> list[tuple[str, str]]:
        """Compile the manuscript into ordered ``(title, body)`` units.

        A unit is one chapter (its own text plus its descendant scenes, in
        binder order, separated by the scene-divider slot) or one standalone
        scene that belongs to no chapter. Every body is fully rendered
        through the template set, with each document's sidebar template
        overriding its chapter/scene slot when the set provides one.

        ``templates`` is the export's chosen set — the floor under
        everything (§2). Explicit metadata wins over it at compile time:
        a folder naming a template in book_settings dresses its own
        subtree, resolved per document by _template_resolver, so an
        assignment governs a book even inside a whole-project export.

        ``fmt`` picks the book chrome (2.1): template pages selected by
        the firing books — by direct folder selection, or by name match on
        a whole-project compile — are assembled around their books'
        documents before units are built, and they stay out of the
        chapter/scene numbering and the running word total.
        """
        templates = (templates or TemplateSet.builtin()).for_format(fmt)
        set_for = self._template_resolver(templates, fmt)
        scope = self._compile_scope(selected_ids, fmt)
        export_docs = scope["export_docs"]
        ordered = scope["ordered"]
        chapter_scenes = scope["chapter_scenes"]

        # ${EstimatedPages}: an estimate from the text, since a printed page
        # count needs the rendered document (LibreOffice for PDF). The
        # same figure cover.py uses for the spine, so the two agree.
        total_words = sum(d.word_count for d in export_docs)
        pages_total = max(1, -(-total_words // 250)) if total_words else 0

        # Contents page support: a document whose template contains ${ToC}
        # is filled with the book's table of contents. Build the list
        # up front so it is available even when the contents page sits
        # in front matter, before the units are rendered.
        # Contents: a document whose fragment carries ${ToC} is filled
        # with the contents of this export. The entries are the *headings
        # the export itself produces*, nested by their level — the rule
        # Word's own table follows — so a folder or document appears
        # because its fragment renders a heading, and the heading's own
        # words are the entry (docs/TEMPLATES.md §3.2). Nothing of that
        # can be known before the units are rendered, so the placeholder
        # starts as a sentinel and is replaced below; the contents page
        # itself carries the same sentinel, which is how it is kept out of
        # its own list.
        self._toc_str = TOC_SENTINEL

        # (title, body, owning document id) — the id lets a contents page
        # read its own ToC settings (depth, markers) from its head/metadata.
        unit_rows: list[tuple[str, str, str]] = []
        chapter_number = 0
        scene_number = 0
        words_total = 0
        for document in ordered:
            if document.kind == "chapter":
                chapter_number += 1
                scene_number = 0
                # The chapter dresses itself, and everything it wraps:
                # its own fragment, the divider between its scenes and
                # its closing. A scene inside may still be dressed by a
                # nearer assignment of its own.
                book = set_for(document.id)
                parts: list[str] = []
                own = self._prepare_links(
                    self.read_content(document.id), source_id=document.id,
                    scope=scope, fmt=fmt, html=html)
                if not html:
                    own = self.plain_text(own)
                own = self.expand_shortcodes(
                    document, own, html=html,
                    extra=_template_numbers(chapter_number, 0, words_total))
                divider = (document.scene_divider or "").strip()
                scene_rendered: list[str] = []
                by_name: dict[str, list[str]] = {}
                for index, scene in enumerate(
                        chapter_scenes.get(document.id, [])):
                    scene_number += 1
                    if index > 0 and divider:
                        if html:
                            from html import escape as _esc
                            scene_rendered.append(
                                '<p class="scene-divider">' + _esc(divider) + '</p>')
                        else:
                            scene_rendered.append(divider)
                    scene_set = set_for(scene.id)
                    extra = _template_numbers(
                        chapter_number, scene_number,
                        words_total + scene.word_count)
                    rendered = self._render_document(
                        scene, scene_set, html, slot="document", extra=extra,
                        scope=scope, fmt=fmt, pages_total=pages_total)
                    scene_rendered.append(rendered)
                    # The children, grouped by the word the writer gave
                    # them: their Type, and their kind. A template can
                    # ask for one group alone.
                    for name in (self.type_of(scene), scene.kind):
                        by_name.setdefault(name.casefold(), []).append(rendered)
                    words_total += scene.word_count
                extra = _template_numbers(chapter_number, 0, words_total)
                extra["children"] = "\n".join(scene_rendered)
                body = self._render_document(
                    document, book, html, slot="folder",
                    content=own if own.strip() else "",
                    extra=extra, scope=scope, fmt=fmt, children=by_name,
                    pages_total=pages_total)
                words_total += document.word_count
                unit_rows.append((document.title, body, document.id))
            else:
                # A folder, a scene that stands alone, or any other exported
                # document: one unit, in order. A folder keeps its container
                # face so its own heading/content can reach the contents.
                scene_number += 1
                scene_set = set_for(document.id)
                extra = _template_numbers(chapter_number, scene_number,
                                          words_total)
                slot = "folder" if document.kind == "folder" else None
                body = self._render_document(
                    document, scene_set, html, slot=slot, extra=extra,
                    scope=scope, fmt=fmt, pages_total=pages_total)
                words_total += document.word_count
                unit_rows.append((document.title, body, document.id))
        units = [(title, body) for title, body, _did in unit_rows]
        self._toc_units = {index for index, (_title, body)
                           in enumerate(units, start=1)
                           if TOC_SENTINEL in body}
        if not _toc or not self._toc_units:
            return units            # no contents page in this export

        # Per-ToC configuration: each contents page reads its depth cap and
        # marker styles from the document that holds ${ToC} (stored in that
        # document's head/metadata), so different contents pages can differ.
        def _render_toc_for(doc_id: str, source_units: list[tuple[str, str]]):
            config = self._toc_config(doc_id)
            entries = _toc_entries(source_units, max_depth=config["depth"])
            if html:
                return _toc_markup(entries, links=fmt == "epub",
                                   markers=config["markers"])
            return _toc_text(entries, markers=config["markers"])

        if html:
            structure = None
        else:
            # A text render carries no headings: the same set's html side
            # is where the structure lives.
            structure = self.compile_units(selected_ids, templates,
                                           html=True, fmt=fmt, _toc=False)

        result: list[tuple[str, str]] = []
        first_toc = ""
        for (title, body, doc_id) in unit_rows:
            if TOC_SENTINEL in body:
                source = units if html else (structure or units)
                toc = _render_toc_for(doc_id, source)
                if not first_toc:
                    first_toc = toc
                body = body.replace(TOC_SENTINEL, toc)
            result.append((title, body))
        self._toc_str = first_toc or ""
        return result



    def import_from_project(self, source: Path | str, selected_ids: set[str] | None = None,
                            parent_id: str | None = None) -> int:
        """Import documents/folders from another Lekhak project.

        Copies the selected items (and their descendants) into this project
        under ``parent_id`` (or the root), preserving structure, order,
        content, metadata, type, template status and template configuration.
        Referenced media/cover assets stored under the source project are
        copied and rewritten to the new document IDs where possible.
        Returns the number of documents/folders imported.
        """
        source = Path(source)
        src = Project(source, read_only=True)
        src.open()
        try:
            all_docs = {d.id: d for d in src.documents()}
            if selected_ids is None:
                selected_ids = {d.id for d in src.documents()}
            # include descendants of selected items
            children = {}
            for d in src.documents():
                children.setdefault(d.parent_id, []).append(d)
            wanted: set[str] = set()

            def add_tree(doc_id: str):
                if doc_id in wanted or doc_id not in all_docs:
                    return
                wanted.add(doc_id)
                for ch in sorted(children.get(doc_id, []), key=lambda x: x.position):
                    add_tree(ch.id)

            for sid in list(selected_ids):
                add_tree(sid)
            ordered = [d for d in src.documents() if d.id in wanted]
            id_map: dict[str, str] = {}
            count = 0

            def rewrite_asset_refs(content: str, old_id: str, new_id: str) -> str:
                if not content:
                    return content
                # common forms: images/<old_id>/..., src='images/<old_id>/...'
                content = content.replace(f"images/{old_id}/", f"images/{new_id}/")
                content = content.replace(f"images\\{old_id}\\", f"images/{new_id}/")
                return content

            for doc in ordered:
                old_parent = doc.parent_id
                new_parent = parent_id
                if old_parent and old_parent in id_map:
                    new_parent = id_map[old_parent]
                elif old_parent is None:
                    new_parent = parent_id
                new = self.add_document(doc.title, new_parent, doc.kind)
                id_map[doc.id] = new.id

                # copy per-document/folder media folder (images/<id>/...)
                src_media = source / "images" / doc.id
                if src_media.is_dir():
                    dst_media = self.root / "images" / new.id
                    try:
                        if dst_media.exists():
                            shutil.rmtree(dst_media)
                        shutil.copytree(src_media, dst_media)
                    except Exception:
                        pass

                content = src.read_content(doc.id)
                if content:
                    content = rewrite_asset_refs(content, doc.id, new.id)
                    # also rewrite refs to any other imported item's assets
                    for oid, nid in list(id_map.items()):
                        if oid != doc.id:
                            content = rewrite_asset_refs(content, oid, nid)
                    self.write_content(new.id, content)

                self.update_document(
                    new.id,
                    synopsis=doc.synopsis or "",
                    word_target=getattr(doc, "word_target", 0) or 0,
                    page_break=getattr(doc, "page_break", "auto") or "auto",
                    scene_divider=getattr(doc, "scene_divider", "") or "",
                    doc_template=getattr(doc, "doc_template", "") or "",
                )
                try:
                    entries = src.metadata_entries(doc.id)
                    if entries:
                        values = {k: v for k, (v, _t, _o) in entries.items()}
                        types = {k: t for k, (_v, t, _o) in entries.items()}
                        options = {k: o for k, (_v, _t, o) in entries.items()}
                        self.set_metadata(new.id, values, types, options)
                except Exception:
                    pass
                try:
                    if src.is_template(doc.id):
                        self.set_template(new.id, True, _force=True)
                except Exception:
                    pass
                try:
                    tval = src.type_of(doc) if hasattr(src, "type_of") else ""
                    if tval:
                        self.set_type(new.id, tval)
                except Exception:
                    pass
                count += 1

            # second pass: rewrite content refs for ids mapped after earlier docs were written
            for doc in ordered:
                new_id = id_map.get(doc.id)
                if not new_id:
                    continue
                content = self.read_content(new_id)
                if not content:
                    continue
                rewritten = content
                for oid, nid in id_map.items():
                    rewritten = rewrite_asset_refs(rewritten, oid, nid)
                if rewritten != content:
                    self.write_content(new_id, rewritten)

            # copy covers/book settings for imported folders
            try:
                raw = self.get_setting("book_settings")
                stored = json.loads(raw) if raw else {}
                if not isinstance(stored, dict):
                    stored = {}
            except Exception:
                stored = {}
            for old_id, new_id in id_map.items():
                old_doc = all_docs.get(old_id)
                if old_doc is None or old_doc.kind != "folder":
                    continue
                bs = src.book_settings(old_id)
                if not bs:
                    # still ensure auto cover folder copied if exists (done above)
                    continue
                bs = dict(bs)
                cover = bs.get("cover", "")
                if cover:
                    cpath = Path(cover)
                    if not cpath.is_absolute():
                        cpath = source / cpath
                    if cpath.is_file():
                        try:
                            # keep under images/<new_id>/cover.<ext>
                            ext = cpath.suffix.lower().lstrip('.') or 'jpg'
                            dst_dir = self.root / "images" / new_id
                            dst_dir.mkdir(parents=True, exist_ok=True)
                            dst = dst_dir / f"cover.{ext}"
                            shutil.copy2(cpath, dst)
                            # prefer auto-detect path form
                            bs["cover"] = ""
                        except Exception:
                            pass
                # remap template name? name is portable, keep as-is
                cleaned = {k: v for k, v in bs.items() if v}
                if cleaned:
                    stored[new_id] = cleaned
            self.set_setting("book_settings", json.dumps(stored, ensure_ascii=False, indent=1))

            # preserve relative order
            by_new_parent = {}
            for doc in ordered:
                new_id = id_map.get(doc.id)
                if not new_id:
                    continue
                old_parent = doc.parent_id
                new_parent = parent_id
                if old_parent and old_parent in id_map:
                    new_parent = id_map[old_parent]
                by_new_parent.setdefault(new_parent, []).append(new_id)
            for p, ids in by_new_parent.items():
                for pos, did in enumerate(ids):
                    self.db.execute("UPDATE documents SET position=? WHERE id=?", (pos, did))
            self.db.commit()
            self.sync_with_disk()
            return count
        finally:
            src.close()



    def export_text(self, destination: Path, selected_ids: set[str] | None = None,
                    templates: TemplateSet | None = None) -> None:
        """Write the compiled manuscript as plain text.

        The same compiled units as every other export, with markup
        stripped; header/footer slots are joined with blank lines.

        Pagination decision (BUILD-NOW 9, settled 2026-10-22): no form
        feeds, deliberately. A .txt is structural — titles and headings
        as lines, units separated by blank lines — and pagination is the
        page-setup formats' job (PDF/DOCX, page setup / PRINT.md). Form
        feeds would make the file neither a good reading copy nor a
        printable one.
        """
        self._require_metadata_for_export(selected_ids)
        templates = (templates or TemplateSet.builtin()).for_format("text")
        if templates.prints_nothing:
            # `no-export` (§3): no fragment carries content, so neither
            # does the file — an empty one, written properly.
            self._atomic_write(Path(destination), "")
            return
        units = self.compile_units(selected_ids, templates, html=False,
                                   fmt="text")
        # Project chrome wraps a whole-manuscript compile only: a
        # selection exports "with only the document level template" (2.1).
        if selected_ids is None:
            header = templates.render("project_header", {}, html=False)
            footer = templates.render("project_footer", {}, html=False)
            header = self.rewrite_wiki_links(
                header, selected_ids, fmt="text", html=False)
            footer = self.rewrite_wiki_links(
                footer, selected_ids, fmt="text", html=False)
        else:
            header, footer = "", ""
        body = "\n\n\n".join(body for _title, body in units if body.strip())
        content = "\n".join(part for part in (header, body, footer) if part.strip())
        self._atomic_write(Path(destination), content.rstrip() + "\n")

    def export_markdown(self, destination: Path,
                        selected_ids: set[str] | None = None) -> None:
        """Write the manuscript as clean markdown (2.3).

        No template chrome and no book-settings pages —
        chapters as '# Title', scenes as '## Title', prose converted
        back from the stored HTML with inline bold and italic kept. Wiki
        links become plain words (the text-fallthrough of the 2.2 link
        pass); resolved targets inside the export do not become real
        markdown links, which have no notion of a sibling document.
        Form feeds likewise stay out (BUILD-NOW 9): markdown is
        structural too.

        Each document's metadata rides above its heading as a YAML
        fence, in the format the document files already use. Metadata is
        work the writer did, so the export carries it and
        ``import_markdown`` reads it back — export then import is a real
        round trip, not a flatten.
        """
        self._require_metadata_for_export(selected_ids)
        from .layout import dump_frontmatter
        from .markdown import markdown_from_html

        scope = self._compile_scope(selected_ids, "markdown")
        ordered = scope["ordered"]
        scenes_of = scope["chapter_scenes"]

        def body_of(document: Document) -> str:
            content = self._prepare_links(
                self.read_content(document.id), source_id=document.id,
                scope=scope, fmt="markdown", html=False)
            return self.expand_shortcodes(
                document, markdown_from_html(content), html=False)

        blocks: list[str] = []

        def add(text: str) -> None:
            text = text.strip()
            if text:
                blocks.append(text)

        def metadata_head(document: Document) -> str:
            values = {
                key: value
                for key, value in sorted(self.metadata(document.id).items())
                if key not in (TEMPLATE_KEY, TEMPLATE_CONFIG_KEY)
                and value.strip()
            }
            if document.synopsis.strip():
                values = {"Synopsis": document.synopsis, **values}
            return dump_frontmatter(values) if values else ""

        for document in ordered:
            heading = "# " if document.kind == "chapter" else "## "
            add(metadata_head(document) + heading + document.title)
            add(body_of(document))
            for scene in scenes_of.get(document.id, []):
                add(metadata_head(scene) + "## " + scene.title)
                add(body_of(scene))
        content = "\n\n".join(blocks)
        self._atomic_write(Path(destination), content.rstrip() + "\n")

    def import_markdown(self, paths: list[Path],
                        parent_id: str | None = None) -> dict:
        """Create documents from markdown files (File → Import Markdown).

        Reads Lekhak's own export and Scrivener's, so "export the project,
        import it here" is a real round trip. Every metadata key the file
        carries lands in the metadata table: a field the project does not
        know yet is created, an existing one is filled. Structural keys
        build the tree (``Parent`` names the document above, when that
        document came in the same import).

        Returns a summary for the caller to show: how many documents were
        created, which fields were created, how many values were filled,
        and which project-level keys were kept as they were because the
        project already had a value for them.
        """
        from .markdown import markdown_to_html
        from .markdown_import import parse_markdown

        target = parent_id or next(
            (d.id for d in self.documents() if d.parent_id is None), None)
        documents_created = 0
        fields_created: list[str] = []
        values_filled = 0
        project_kept: list[str] = []
        by_title: dict[str, str] = {}
        for path in paths:
            parsed = parse_markdown(
                Path(path).read_text(encoding="utf-8"))
            if parsed.project_metadata:
                entries = self.project_metadata()
                for key, value in parsed.project_metadata.items():
                    held = entries.get(key)
                    if held is not None and held[0].strip():
                        project_kept.append(key)
                        continue
                    entries[key] = (value, _metadata_type(value), [])
                self.set_project_metadata(entries)
            # The flat markdown carries no tree beyond chapter/scene, so a
            # scene with no Parent lands under the chapter before it —
            # the order the export wrote.
            last_chapter: str | None = None
            for item in parsed.documents:
                parent = by_title.get(item.parent.casefold(), target)
                if (item.kind == "scene" and not item.parent
                        and last_chapter is not None):
                    parent = last_chapter
                document = self.add_document(
                    item.title or "Imported", parent, item.kind)
                self.write_content(
                    document.id, markdown_to_html(item.body))
                if item.synopsis.strip():
                    self.update_document(
                        document.id, synopsis=item.synopsis)
                if item.metadata:
                    entries = self.metadata(document.id)
                    rows = self.metadata_entries(document.id)
                    types: dict[str, str] = {}
                    group = self.type_group(document)
                    known = {name.casefold()
                             for name in self.known_fields(group)}
                    for key, value in item.metadata.items():
                        folded = key.casefold()
                        if folded in (TEMPLATE_KEY.casefold(),
                                      TEMPLATE_CONFIG_KEY.casefold()):
                            continue    # a mark, not metadata to import
                        # Type is the protected role the panel already
                        # knows; every other key may need a new field.
                        name = (key if folded == "type"
                                else self.add_field(group, key))
                        if (folded != "type" and name not in rows
                                and name.casefold() not in known):
                            fields_created.append(name)
                        types[name] = (rows[name][1] if name in rows
                                       else _metadata_type(value))
                        entries[name] = value
                        values_filled += 1
                    self.set_metadata(document.id, entries, types)
                by_title[item.title.casefold()] = document.id
                if item.kind == "chapter":
                    last_chapter = document.id
                documents_created += 1
        return {
            "documents": documents_created,
            "fields": sorted(set(fields_created)),
            "values": values_filled,
            "kept": sorted(set(project_kept)),
        }

    def export_html(self, destination: Path, selected_ids: set[str] | None = None,
                    templates: TemplateSet | None = None) -> None:
        """Write the compiled manuscript as a single HTML file."""
        self._require_metadata_for_export(selected_ids)
        templates = (templates or TemplateSet.builtin()).for_format("html")
        units = self.compile_units(selected_ids, templates, html=True,
                                   fmt="html")
        header, footer = "", ""
        if selected_ids is None:
            header = templates.render("project_header", {}, html=True)
            footer = templates.render("project_footer", {}, html=True)
            header = self.rewrite_wiki_links(
                header, selected_ids, fmt="html", html=True)
            footer = self.rewrite_wiki_links(
                footer, selected_ids, fmt="html", html=True)
        title = escape(self.get_setting("title", "Lekhak"))
        # 2.2: one invisible anchor per unit, so a published [[link]] has
        # somewhere to point inside this single file.
        # Phase 1: every unit after the first begins a printed page. Inline
        # rather than in a stylesheet, so a user template's own CSS is never
        # fighting ours for the last word.
        def wrapped(item: tuple[int, tuple[str, str]]) -> str:
            index, (_title, body) = item
            style = "" if index == 1 else " style='break-before: page;'"
            return f"<a id='unit-{index}'></a><div class='unit'{style}>{body}</div>"

        anchored = "".join(wrapped(item) for item in enumerate(units, start=1))
        # The set's own stylesheet (§3.2), in <style> after the title:
        # it is the writer's CSS, so it stands before the header that may
        # well use its classes. A set with no style.css writes no <style>
        # at all — the export stays exactly what it was.
        css = templates.css()
        css_style = f"<style>{css}</style>" if css.strip() else ""
        content = (
            "<!doctype html><meta charset='utf-8'>"
            f"<title>{title}</title>{css_style}{header}"
            f"{anchored}{footer}"
        )
        self._atomic_write(Path(destination), content)

    def export_root_titles(self, selected_ids: set[str] | None) -> list[str]:
        """The *book* an export dresses (Phase 1), for `${BookTitle}`: the
        selected containers' names, or the project's own title for a
        whole-project export.

        Only a folder is a book. A lone chapter or document answers
        nothing rather than wearing its own name as one: an empty
        `${BookTitle}` prints a blank (or lets the sample's head stand),
        which is honest, where a document's title masquerading as the
        book's would not be.
        """
        if not selected_ids:
            return [self.get_setting("title", "")]
        ordered = self.documents()
        parents = {document.id: document.parent_id for document in ordered}

        def inside_the_selection(document_id: str) -> bool:
            current = parents.get(document_id)
            while current is not None:
                if current in selected_ids:
                    return True
                current = parents.get(current)
            return False

        roots = [document for document in ordered
                 if document.id in selected_ids and not inside_the_selection(document.id)]
        if not roots:
            roots = [document for document in ordered if document.id in selected_ids]
        return [document.title for document in roots
                if document.kind == "folder"]

    def _document_template_is_toc(self, document, templates) -> bool:
        """True when this unit is the contents page (carries ${ToC})."""
        try:
            doc_template = (getattr(document, "doc_template", "") or "").strip().lower()
            if doc_template in {"contents", "toc"}:
                return True
            slot = templates.document_slot(document.doc_template, html=True) if document.doc_template else templates.slot("folder" if getattr(document, "kind", "") == "folder" else "document", html=True)
            return "${ToC}" in (slot or "") or "${toc}" in (slot or "").lower()
        except Exception:
            return False

    @staticmethod
    def _add_toc_field(output, depth: int = 3) -> None:
        """The Word TOC field, placed where a contents unit wants it.

        Depth follows the contents/ToC configuration (capped at 4).
        """
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        try:
            depth = int(depth)
        except Exception:
            depth = 3
        depth = max(1, min(depth, 4))
        paragraph = output.add_paragraph()
        run = paragraph.add_run()
        begin = OxmlElement("w:fldChar")
        begin.set(qn("w:fldCharType"), "begin")
        instruction = OxmlElement("w:instrText")
        instruction.set(qn("xml:space"), "preserve")
        instruction.text = f'TOC \\o "1-{depth}" \\z \\u'
        separate = OxmlElement("w:fldChar")
        separate.set(qn("w:fldCharType"), "separate")
        placeholder = OxmlElement("w:t")
        placeholder.text = "Update this field to build the table of contents."
        end = OxmlElement("w:fldChar")
        end.set(qn("w:fldCharType"), "end")
        for element in (begin, instruction, separate, placeholder, end):
            run._r.append(element)

    @staticmethod
    @staticmethod
    def _add_page_field(paragraph, prefix: str = "") -> None:
        """Word's PAGE field into a running part's paragraph (3.1's
        `${PageNo}`): the same field shape the toc writer uses, so a folio
        numbers itself instead of spelling "Page" down the margin. An
        optional prefix carries the tab that centres the piece — fields
        are runs, and the run before them holds the tab. The run is
        handed back, so the caller can set the head's own type on it."""
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        run = paragraph.add_run(prefix)
        begin = OxmlElement("w:fldChar")
        begin.set(qn("w:fldCharType"), "begin")
        instruction = OxmlElement("w:instrText")
        instruction.set(qn("xml:space"), "preserve")
        instruction.text = " PAGE "
        separate = OxmlElement("w:fldChar")
        separate.set(qn("w:fldCharType"), "separate")
        placeholder = OxmlElement("w:t")
        placeholder.text = "1"          # what a reader shows before update
        end = OxmlElement("w:fldChar")
        end.set(qn("w:fldCharType"), "end")
        for element in (begin, instruction, separate, placeholder, end):
            run._r.append(element)
        return run

    def docx_config_values(self, selected_ids: set[str] | None, *,
                           fmt: str = "docx") -> dict[str, str]:
        """The values a print-config line may name (§3.1): project
        metadata under its bare name *and* its ``Metadata.`` alias (3.2's
        one namespace), the book the export is titled after, today's
        date, and the format — so `${Format}` in a running head tests
        the same way it does anywhere else."""
        from datetime import date

        values: dict[str, str] = {}
        for key, entry in self.project_metadata().items():
            text = (entry[0] or "") if entry else ""
            values[key] = text
            values[f"Metadata.{key}"] = text
        titles = self.export_root_titles(selected_ids)
        book = titles[0] if titles else ""
        values["BookTitle"] = book
        values["Title"] = book
        values["Date"] = date.today().isoformat()
        values["Format"] = fmt
        return values

    def _docx_geometry(self, config: dict, sample,
                       scope: dict) -> tuple[dict, bool]:
        """The paper this file will print on (§3.1's one rule): the
        template's sample supplies everything, each config line overrides
        it, and page setup (1.15) — else the A4 baseline — applies only
        when the template said nothing. Returns the geometry and whether
        *the template* stated paper, which is what recto chapter openers
        respond to: a trim is a trim whenever it arrived.

        A scope of one document is a page printed on its own, and there
        the page's own design has the last word (1.18): whatever it
        states as size is the truth.

        Each layer is validated by the same ``clean_page_setup`` the page
        setup dialog's accept path uses, so a line nobody meant cannot
        reach the page — the rejected layer is dropped as a whole and
        what stood before it remains.
        """
        geometry = dict(self._page_setup_for_scope(scope) or DEFAULT_PAGE_SETUP)
        stated = False
        layer = sample_geometry(sample) if sample is not None else None
        if layer:
            candidate = dict(geometry)
            candidate.update(layer)
            cleaned = clean_page_setup(candidate)
            if cleaned is not None:
                geometry, stated = cleaned, True
        overlay = config_geometry(config)
        if overlay:
            candidate = dict(geometry)
            candidate.update(overlay)
            cleaned = clean_page_setup(candidate)
            if cleaned is not None:
                geometry, stated = cleaned, True
        # A page printed on its own (1.18) is printed as its own design
        # states it: a template outside the selected set may carry a
        # Page size, and there that size is the truth. A book export
        # keeps the book's trim and ignores it silently.
        pages = scope.get("export_docs") or []
        if len(pages) == 1:
            own = config_geometry(
                self.template_config(pages[0].doc_template))
            if "width" in own:
                candidate = dict(geometry)
                candidate.update(own)
                cleaned = clean_page_setup(candidate)
                if cleaned is not None:
                    geometry, stated = cleaned, True
        return geometry, stated

    @staticmethod
    def _apply_print_headings(output, config: dict, values: dict,
                              geometry: dict, sections=None,
                              head_font=None) -> None:
        """``Header``/``Footer`` lines as running heads and folios
        (§3.1): one line, three pieces — left, middle, right — set on
        tab stops at the middle and the end of the text width, filled
        through the same `${...}` namespace a fragment uses. A line's
        ``left`` variant is the verso head and switches on Word's
        odd/even headers, which is what mirroring a hand-built book
        means in practice; ``right`` is the recto one. An all-empty line
        says nothing, so the sample's own head stands (§3.1: the config
        overrides only the lines it names). A piece that is exactly
        ``${PageNo}`` becomes a PAGE field, so the folio numbers the
        pages rather than spelling "Page" on every one of them.

        ``sections`` limits the pass to one page's own section (1.18);
        the default is every section of the file. ``Header: none`` is
        the one line that means *nothing there* rather than *inherit*:
        a title page or a dedication carries no running head. The
        ``first page`` pair say the same about each section's *opening*
        page — the head a chapter opener should not wear — which Word
        keeps apart from its other pages by a flag on the section.

        ``head_font`` is the ``Head font:`` line already read out
        (``_head_font``): the per-page pass inherits the file's answer,
        so a page with a head of its own wears the same type as the
        rest.
        """
        from docx.enum.text import WD_TAB_ALIGNMENT
        from docx.shared import Inches

        if sections is None:
            sections = output.sections
        else:
            sections = list(sections)
        width = (geometry["width"] - geometry["inside"] - geometry["outside"])
        if head_font is None:
            head_font = _head_font(config)

        def line(part, pieces):
            """One line in one running part: everything already in it is
            sample prose (or an older answer), and the writer's three
            pieces take its place. A ``${PageNo}`` piece arrives still a
            marker, because the folio it stands for cannot be a string:
            it is swapped for Word's own PAGE field here."""
            paragraphs = list(part.paragraphs) or [part.add_paragraph()]
            keep = paragraphs[0]
            for extra in paragraphs[1:]:
                extra._element.getparent().remove(extra._element)
            for run in list(keep.runs):
                run._element.getparent().remove(run._element)
            stops = keep.paragraph_format.tab_stops
            stops.clear_all()
            stops.add_tab_stop(Inches(width / 2), WD_TAB_ALIGNMENT.CENTER)
            stops.add_tab_stop(Inches(width), WD_TAB_ALIGNMENT.RIGHT)
            for index, piece in enumerate(pieces):
                prefix = "\t" if index else ""
                if piece == PAGE_MARKER:
                    run = Project._add_page_field(keep, prefix)
                else:
                    run = keep.add_run(prefix + piece)
                _style_head_run(run, head_font)

        # Odd/even alternation on before the first part is written: until
        # Word's evenAndOddHeaders setting exists, a section's recto and
        # verso parts resolve against each other, and the recto head
        # landed in the verso one — the series on every page.
        if any(key in config for key in ("header left", "footer left")):
            output.settings.element.get_or_add_evenAndOddHeaders()
        even_odd = False
        for noun in ("header", "footer"):
            # One line, three slots: the plain key is the odd (recto)
            # page's own, `left` the verso one — mirroring is what the
            # two halves are *for*, so the pair only means something
            # when both are said. `right` is accepted as the plain key's
            # spelling and writes the same part, never a third one.
            # `first page` is the section's opening page, which Word
            # keeps apart from all of them.
            for suffix, which in (("", "odd"), (" left", "even"),
                                   (" right", "odd"),
                                   (" first page", "first")):
                key = noun + suffix
                if key not in config:
                    continue
                said = str(config[key] or "").strip()
                if not said:
                    continue            # an empty line says nothing
                if said.lower() in NO_RUNNING_HEAD:
                    # `Header: none` — this page carries no head. The
                    # plain key stands for the pair, so both parts go;
                    # `first page` speaks for the opening page alone.
                    for section in sections:
                        if which == "first":
                            _first_page_flag(section)
                            part = _head_part(section, noun, which)
                            part.is_linked_to_previous = False
                            line(part, [])
                            continue
                        for part in (_head_part(section, noun, "odd"),
                                     _head_part(section, noun, "even")):
                            part.is_linked_to_previous = False
                            line(part, [])
                    continue
                pieces = []
                for piece in config_headings(config[key]):
                    # The page request is recognised on the *raw* piece:
                    # filling first would collapse `${PageNo}` to nothing
                    # (an unknown key renders empty), and a folio would
                    # silently become a blank middle tab.
                    spelled = _brace_aliases(piece).strip()
                    if spelled in PAGE_NAMES:
                        pieces.append(PAGE_MARKER)
                        continue
                    pieces.append(TemplateSet.fill(
                        _brace_aliases(piece), values, html=False).strip())
                if not any(pieces):
                    continue            # an empty line says nothing
                even_odd = even_odd or which == "even"
                for section in sections:
                    if which == "first":
                        _first_page_flag(section)
                    part = _head_part(section, noun, which)
                    part.is_linked_to_previous = False
                    line(part, pieces)
        if even_odd:
            output.settings.element.get_or_add_evenAndOddHeaders()

    @staticmethod
    def _apply_print_font(output, config: dict) -> None:
        """``Font: Georgia, 11pt, 1, 1cm`` — the shorthand §3.1 gives
        (font, size, line spacing, indent) — set on the body style the
        manuscript is actually written in, overriding the sample's own
        line by line: one config line, one override. The line's pieces
        are read one at a time, so a shorter line (`Font: Georgia`) still
        says what it says and the rest keeps the sample's answer."""
        from docx.shared import Inches, Pt

        raw = str(config.get("font", "")).strip()
        if not raw:
            return
        target = (paragraph_style(output, config.get("body style"))
                  or paragraph_style(output, "Normal"))
        if target is None:
            return
        style = output.styles[target]
        pieces = [piece.strip() for piece in raw.split(",")]
        if pieces[0]:
            style.font.name = pieces[0]
        if len(pieces) > 1:
            size = config_points(pieces[1])
            if size is not None:
                style.font.size = Pt(size)
        if len(pieces) > 2 and pieces[2]:
            try:
                spacing = float(pieces[2])
            except ValueError:
                spacing = None
            if spacing is not None and 0.0 < spacing <= 10.0:
                style.paragraph_format.line_spacing = spacing
        if len(pieces) > 3 and pieces[3]:
            indent = config_length(pieces[3])
            if indent is not None and indent > 0.0:
                style.paragraph_format.first_line_indent = Inches(indent)


    def _docx_write_html(self, output, html: str, *, body_style=None,
                         styles: dict | None = None, doc_id: str | None = None,
                         bookmark_id: list[int] | None = None) -> None:
        """Render a unit's HTML (from its template fragment) into DOCX.

        Headings h1-h6 become Heading 1-6 so the TOC field sees them.
        Paragraphs, lists, breaks and basic inline formatting are emitted.
        A bookmark for the unit is placed on its first block so internal
        links (d<doc_id>) still resolve.
        """
        from html.parser import HTMLParser
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        styles = styles or {}
        bookmark_id = bookmark_id if bookmark_id is not None else [1]
        first_block = [True]

        def add_bookmark(paragraph):
            if not doc_id or not first_block[0]:
                return
            bid = str(bookmark_id[0]); bookmark_id[0] += 1
            bm = OxmlElement("w:bookmarkStart")
            bm.set(qn("w:id"), bid); bm.set(qn("w:name"), f"d{doc_id}")
            end = OxmlElement("w:bookmarkEnd"); end.set(qn("w:id"), bid)
            p = paragraph._p
            p.insert(1 if p.pPr is not None else 0, bm)
            p.append(end)
            first_block[0] = False

        class DocxBuilder(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.inline = []
                self.list_stack = []
                self.href = None
                self.current_para = None

            def _ensure_para(self, style=None):
                if self.current_para is None:
                    self.current_para = output.add_paragraph(style=style)
                    add_bookmark(self.current_para)
                return self.current_para

            def _close_para(self):
                self.current_para = None

            def _add_text_run(self, para, text):
                if text == "":
                    return
                # split link tokens \x02id\x03text\x04 (2.2 internal links)
                import re as _re
                pos = 0
                for m in _re.finditer(r"\x02([^\x03]+)\x03([^\x04]*)\x04", text):
                    if m.start() > pos:
                        self._add_plain_run(para, text[pos:m.start()])
                    self._add_hyperlink_run(para, m.group(1), m.group(2))
                    pos = m.end()
                if pos < len(text):
                    self._add_plain_run(para, text[pos:])

            def _add_plain_run(self, para, text):
                parts = text.split(PAGE_MARKER)
                for i, part in enumerate(parts):
                    if i:
                        Project._add_page_field(para)
                    if not part:
                        continue
                    run = para.add_run(part)
                    if "b" in self.inline: run.bold = True
                    if "i" in self.inline: run.italic = True
                    if "u" in self.inline: run.underline = True

            def _add_hyperlink_run(self, para, target_id, label):
                hyperlink = OxmlElement("w:hyperlink")
                hyperlink.set(qn("w:anchor"), f"d{target_id}")
                run = OxmlElement("w:r")
                rPr = OxmlElement("w:rPr")
                rStyle = OxmlElement("w:rStyle"); rStyle.set(qn("w:val"), "Hyperlink")
                rPr.append(rStyle)
                run.append(rPr)
                t = OxmlElement("w:t"); t.set(qn("xml:space"), "preserve")
                t.text = label
                run.append(t)
                hyperlink.append(run)
                para._p.append(hyperlink)

            def _add_run(self, text):
                if not text:
                    return
                para = self.current_para
                if para is None:
                    para = self._ensure_para(style=body_style)
                self._add_text_run(para, text)

            def handle_starttag(self, tag, attrs):
                tag = tag.lower()
                attrs_d = dict(attrs)
                if tag in ("h1","h2","h3","h4","h5","h6"):
                    self._close_para()
                    style = styles.get(tag) or f"Heading {tag[1]}"
                    self.current_para = output.add_paragraph(style=style)
                    add_bookmark(self.current_para)
                elif tag == "p":
                    self._close_para()
                    # page-break-before style hint
                    style_attr = attrs_d.get("style", "")
                    self.current_para = output.add_paragraph(style=body_style)
                    add_bookmark(self.current_para)
                    if "page-break-before" in style_attr:
                        try:
                            self.current_para.paragraph_format.page_break_before = True
                        except Exception:
                            pass
                elif tag == "br":
                    para = self.current_para or self._ensure_para(style=body_style)
                    para.add_run().add_break()
                elif tag in ("ul","ol"):
                    self._close_para()
                    self.list_stack.append(tag)
                elif tag == "li":
                    self._close_para()
                    lstyle = "List Bullet" if (self.list_stack and self.list_stack[-1]=="ul") else "List Number"
                    self.current_para = output.add_paragraph(style=lstyle)
                    add_bookmark(self.current_para)
                elif tag in ("strong","b"):
                    self.inline.append("b")
                elif tag in ("em","i"):
                    self.inline.append("i")
                elif tag == "u":
                    self.inline.append("u")
                elif tag == "a":
                    self.inline.append("a")
                    self.href = attrs_d.get("href")
                elif tag == "blockquote":
                    self._close_para()
                    self.current_para = output.add_paragraph(style="Quote")
                    add_bookmark(self.current_para)
                elif tag in ("div","section","article","nav","header","footer","main","figure","figcaption"):
                    self._close_para()
                elif tag == "hr":
                    self._close_para()
                    output.add_paragraph("—")

            def handle_endtag(self, tag):
                tag = tag.lower()
                if tag in ("h1","h2","h3","h4","h5","h6","p","li","blockquote"):
                    self._close_para()
                elif tag in ("ul","ol"):
                    if self.list_stack: self.list_stack.pop()
                    self._close_para()
                elif tag in ("strong","b"):
                    if "b" in self.inline: self.inline.remove("b")
                elif tag in ("em","i"):
                    if "i" in self.inline: self.inline.remove("i")
                elif tag == "u":
                    if "u" in self.inline: self.inline.remove("u")
                elif tag == "a":
                    if "a" in self.inline: self.inline.remove("a")
                    self.href = None
                elif tag in ("div","section","article","nav","header","footer","main","figure","figcaption"):
                    self._close_para()

            def handle_data(self, data):
                if data is None:
                    return
                self._add_run(data)

        # normalize: strip style/head wrappers not useful here
        DocxBuilder().feed(html or "")


    def export_docx(self, destination: Path, selected_ids: set[str] | None = None,
                    templates: TemplateSet | None = None, *,
                    fmt: str = "docx") -> None:
        """Export as DOCX, rendering template fragments (structure/headings)
        rather than role-stamped headings and plain-text body.

        Page/section layout, running heads and page setup still follow the
        print config and template rules; a ${ToC} unit inserts a Word TOC
        field whose depth follows that unit's contents configuration.
        """
        self._require_metadata_for_export(selected_ids)
        from docx import Document as DocxDocument
        from docx.enum.section import WD_SECTION_START
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        templates = (templates or TemplateSet.builtin()).for_format(fmt)
        if templates.prints_nothing:
            self._save_docx(DocxDocument(), destination)
            return
        config = dict(templates.docx_config or {})
        scope = self._compile_scope(selected_ids, fmt)
        sample = open_docx_sample(templates.docx_sample)
        output = sample if sample is not None else DocxDocument()
        geometry, stated = self._docx_geometry(config, sample, scope)
        self._apply_print_font(output, config)
        output.core_properties.title = self.get_setting("title", "Lekhak")

        entries = self.project_metadata()
        author = ""
        if "Author" in entries:
            author = (entries["Author"][0] or "").strip()
        assembled = scope["export_docs"]
        trim_stated = stated and geometry["trim"] != DEFAULT_PAGE_SETUP["trim"]
        first = True
        body_style = paragraph_style(output, config.get("body style"))
        containers = {parent for parent in scope["parent_map"].values() if parent}
        explicit = scope.get("explicit_breaks", set())
        layout: list[tuple[int, dict | None, dict]] = []
        bookmark_id = [1]
        set_for = self._template_resolver(templates, fmt)

        # Render per-unit HTML like compile_units so DOCX follows template
        # fragments/headings, while keeping document/section/page handling.
        chapter_scenes = scope["chapter_scenes"]
        ordered = scope["ordered"]
        # Build a quick map from doc id to rendered content for assembled docs.
        # Use compile_units to get final rendered HTML bodies in order? Easier:
        # render each assembled document the same way compile_units would for
        # standalone/folder units and for chapter units (including children).
        rendered_by_id: dict[str, str] = {}
        chapter_number = 0
        scene_number = 0
        words_total = 0
        total_words = sum(d.word_count for d in assembled)
        pages_total = max(1, -(-total_words // 250)) if total_words else 0
        self._toc_str = TOC_SENTINEL
        # pre-render assembled units in document order
        # chapters: render folder slot with children (as compile_units)
        # others: render as unit
        # We need to follow compile_units logic for chapters inside selection.
        # For simplicity and correctness, render via a subset approach:
        # For each document in assembled, if it's a chapter in ordered context
        # render as compile_units would when iterating ordered; otherwise unit.
        # But assembled may be arbitrary selection. Fall back: if chapter has
        # scenes in scope, render as chapter unit; else render as plain unit.
        for document in ordered:
            if document.kind == "chapter":
                chapter_number += 1
                scene_number = 0
                book = set_for(document.id)
                own = self._prepare_links(
                    self.read_content(document.id), source_id=document.id,
                    scope=scope, fmt=fmt, html=True)
                own = self.expand_shortcodes(
                    document, own, html=True,
                    extra=_template_numbers(chapter_number, 0, words_total))
                divider = (document.scene_divider or "").strip()
                scene_rendered = []
                by_name = {}
                for index, scene in enumerate(chapter_scenes.get(document.id, [])):
                    scene_number += 1
                    if index > 0 and divider:
                        from html import escape as _esc
                        scene_rendered.append(
                            '<p class="scene-divider">' + _esc(divider) + '</p>')
                    scene_set = set_for(scene.id)
                    extra = _template_numbers(
                        chapter_number, scene_number, words_total + scene.word_count)
                    rendered = self._render_document(
                        scene, scene_set, True, slot="document", extra=extra,
                        scope=scope, fmt=fmt, pages_total=pages_total)
                    scene_rendered.append(rendered)
                    for name in (self.type_of(scene), scene.kind):
                        by_name.setdefault(name.casefold(), []).append(rendered)
                    words_total += scene.word_count
                extra = _template_numbers(chapter_number, 0, words_total)
                extra["children"] = "\n".join(scene_rendered)
                body = self._render_document(
                    document, book, True, slot="folder",
                    content=own if own.strip() else "",
                    extra=extra, scope=scope, fmt=fmt, children=by_name,
                    pages_total=pages_total)
                words_total += document.word_count
                rendered_by_id[document.id] = body
            else:
                scene_number += 1
                scene_set = set_for(document.id)
                extra = _template_numbers(chapter_number, scene_number, words_total)
                slot = "folder" if document.kind == "folder" else None
                body = self._render_document(
                    document, scene_set, True, slot=slot, extra=extra,
                    scope=scope, fmt=fmt, pages_total=pages_total)
                words_total += document.word_count
                rendered_by_id[document.id] = body

        for position, document in enumerate(assembled):
            start = scope.get("breaks", {}).get(document.id, "page")
            own_cfg = (self.template_config(document.doc_template)
                       if document.doc_template else {})
            page_margins = config_margins(own_cfg)
            page_heads = {key: value for key, value in own_cfg.items()
                          if key in RUNNING_HEAD_KEYS}
            key = ("new chapter" if document.kind == "chapter"
                   else "new scene" if document.kind == "scene" else None)
            if document.id not in explicit and key is not None:
                demanded = page_break_value(config.get(key, ""))
                if demanded is None:
                    demanded = page_break_value(own_cfg.get(key, ""))
                if demanded is not None:
                    start = demanded
                elif (start == "page" and trim_stated
                          and (document.kind == "chapter"
                               or document.id in containers)):
                    start = "right"
            page_section = len(output.sections) - 1
            if not first:
                if start in ("right", "next-odd"):
                    output.add_section(WD_SECTION_START.ODD_PAGE)
                elif start in ("left", "next-even"):
                    output.add_section(WD_SECTION_START.EVEN_PAGE)
                elif page_margins or page_heads:
                    output.add_section(
                        WD_SECTION_START.CONTINUOUS if start == "flow"
                        else WD_SECTION_START.NEW_PAGE)
                elif start == "page":
                    output.add_page_break()
                page_section = len(output.sections) - 1
            if page_margins or page_heads:
                layout.append((page_section, page_margins, page_heads))
            first = False

            html_body = rendered_by_id.get(document.id, "")
            # Contents/ToC unit: insert Word TOC field (depth from this unit).
            if "${ToC}" in html_body or self._document_template_is_toc(document, templates):
                toc_depth = self._toc_config(document.id)["depth"]
                self._add_toc_field(output, depth=toc_depth)
                continue

            if html_body.strip():
                self._docx_write_html(output, html_body, body_style=body_style,
                                      doc_id=document.id, bookmark_id=bookmark_id)
            else:
                # Keep a bookmarkable empty anchor so links don't break badly.
                para = output.add_paragraph(style=body_style)
                bid = str(bookmark_id[0]); bookmark_id[0] += 1
                bm = OxmlElement("w:bookmarkStart")
                bm.set(qn("w:id"), bid); bm.set(qn("w:name"), f"d{document.id}")
                end = OxmlElement("w:bookmarkEnd"); end.set(qn("w:id"), bid)
                p = para._p
                p.insert(1 if p.pPr is not None else 0, bm)
                p.append(end)

        _apply_page_geometry(output, geometry)
        values = self.docx_config_values(selected_ids, fmt=fmt)
        self._apply_print_headings(output, config, values, geometry)
        head_font = _head_font(config)
        sections = output.sections
        for index, margins, heads in layout:
            if index >= len(sections):
                continue
            page = dict(geometry)
            if margins:
                page.update(margins)
            cleaned = clean_page_setup(page) or geometry
            if margins:
                _apply_section_margins(sections[index], cleaned)
            if heads:
                self._apply_print_headings(output, heads, values, cleaned,
                                           sections=[sections[index]],
                                           head_font=head_font)
        self._save_docx(output, destination)



    @staticmethod
    def _save_docx(output, destination) -> None:
        """Write an open document to ``destination`` atomically."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}-", dir=destination.parent)
        os.close(fd)
        try:
            output.save(temporary_name)
            os.replace(temporary_name, destination)
        except BaseException:
            Path(temporary_name).unlink(missing_ok=True)
            raise

    @staticmethod
    def _docx_page_break_flags(html: str) -> list[bool]:
        flags: list[bool] = []
        for match in re.finditer(r"<p\b[^>]*>(.*?)</p>", html, flags=re.I | re.S):
            inner = match.group(1)
            text = re.sub(r"<[^>]+>", "", inner)
            if not text.strip():
                continue
            opening = match.group(0).split(">", 1)[0]
            flags.append("page-break-before" in opening)
        return flags

    @staticmethod
    def _docx_paragraph(output, text: str, style: str | None = None) -> None:
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        paragraph = output.add_paragraph(style=style)
        for index, segment in enumerate(text.split(PAGE_MARKER)):
            if index:
                Project._add_page_field(paragraph)
            position = 0
            for match in LINK_TOKEN_RE.finditer(segment):
                if match.start() > position:
                    paragraph.add_run(segment[position:match.start()])
                hyperlink = OxmlElement("w:hyperlink")
                hyperlink.set(qn("w:anchor"), f"d{match.group(1)}")
                run = OxmlElement("w:r")
                props = OxmlElement("w:rPr")
                color = OxmlElement("w:color")
                color.set(qn("w:val"), "0563C1")
                underline = OxmlElement("w:u")
                underline.set(qn("w:val"), "single")
                props.append(color)
                props.append(underline)
                run.append(props)
                node = OxmlElement("w:t")
                node.set(qn("xml:space"), "preserve")
                node.text = match.group(2)
                run.append(node)
                hyperlink.append(run)
                paragraph._p.append(hyperlink)
                position = match.end()
            if position < len(segment):
                paragraph.add_run(segment[position:])


    def export_epub(self, destination: Path, selected_ids: set[str] | None = None,
                    templates: TemplateSet | None = None) -> None:
        """Write the compiled manuscript as an EPUB 3 archive.

        One XHTML page per compiled unit, plus the nav document the spec
        requires and, when the template set defines a non-empty toc_page
        slot, a visible contents page as the first content page. The
        OPF names the book and — when the project metadata carries one —
        the author, which is what a shelf and a catalogue read.

        The per-unit files *are* the pagination (BUILD-NOW 9): a reader
        turns pages by unit, so the form-feed question that text export
        had to settle does not arise here.
        """
        self._require_metadata_for_export(selected_ids)
        templates = (templates or TemplateSet.builtin()).for_format("epub")
        units = self.compile_units(selected_ids, templates, html=True,
                                   fmt="epub")
        if selected_ids is None:
            header = templates.render("project_header", {}, html=True)
            footer = templates.render("project_footer", {}, html=True)
            header = self.rewrite_wiki_links(
                header, selected_ids, fmt="epub", html=True)
            footer = self.rewrite_wiki_links(
                footer, selected_ids, fmt="epub", html=True)
        else:
            header, footer = "", ""
        # The cover is a book property (§5): the first folder this export
        # resolves to that has one wins — one EPUB, one cover. A folder
        # picked directly counts, and so does every folder holding an
        # exported document; a single-document export with no folder
        # around it has no cover to find.
        cover_path = None
        for folder in self._cover_folders(selected_ids):
            candidate = self.effective_cover(folder.id)
            if candidate is not None:
                cover_path = candidate
                break
        cover_item = cover_meta = cover_entry = ""
        if cover_path is not None:
            suffix = cover_path.suffix.lstrip(".").casefold()
            media = COVER_MEDIA_TYPES.get(suffix)
            if media:
                href = f"images/cover.{suffix}"
                cover_item = (f"<item id='cover-image' href='{href}'"
                              f" media-type='{media}' properties='cover-image'/>")
                cover_meta = "<meta name='cover' content='cover-image'/>"
                cover_entry = (f"OEBPS/{href}", cover_path.read_bytes())
        manifest, spine, nav_items, pages = [], [], [], []
        # EPUB needs a cover *page* in the spine, not just the metadata
        # image — readers that ignore properties='cover-image' need the
        # visible first page. Generated here, not by any template set,
        # so the astray set (which has no cover slot) still ships a cover.
        if cover_path is not None and cover_entry:
            _href = cover_entry[0].removeprefix("OEBPS/")
            cover_page = (
                "<?xml version='1.0' encoding='utf-8'?>"
                "<html xmlns='http://www.w3.org/1999/xhtml' "
                "xmlns:epub='http://www.idpf.org/2007/ops'>"
                "<head><title>Cover</title></head><body epub:type='cover'>"
                f"<img src='{_href}' alt='Cover'/></body></html>")
            pages.append(("cover.xhtml", cover_page))
            manifest.append("<item id='cover-page' href='cover.xhtml' media-type='application/xhtml+xml'/>")
            spine.insert(0, "<itemref idref='cover-page'/>")
        # The set's stylesheet (§3.2) ships as one file every page links
        # — the EPUB way — rather than repeated in each head.
        css = templates.css()
        css_link = ("<link rel='stylesheet' type='text/css' "
                    "href='style.css'/>") if css.strip() else ""
        section_names = [f"section-{index}.xhtml" for index in range(1, len(units) + 1)]
        for index, ((title, body), filename) in enumerate(zip(units, section_names), start=1):
            item_id = f"section-{index}"
            pages.append((filename, "<?xml version='1.0' encoding='utf-8'?>"
                "<html xmlns='http://www.w3.org/1999/xhtml'><head>"
                f"<title>{escape(title)}</title>{css_link}</head><body>"
                f"{header}{body}{footer}</body></html>"))
            manifest.append(f"<item id='{item_id}' href='{filename}' media-type='application/xhtml+xml'/>")
            spine.append(f"<itemref idref='{item_id}'/>")
        # The nav follows the same rule as a contents page — the headings
        # the export produced, nested by their level (§3.2) — so a
        # reader's table and a printed one cannot disagree.
        nav_items = [_toc_markup(_toc_entries(units, skip=self._toc_units),
                                 links=True)]
        # (contents page removed: no mandated content — the writer
        #  designs it with a binder document and a template.)
        if css.strip():
            manifest.append("<item id='style' href='style.css' "
                            "media-type='text/css'/>")
        title = escape(self.get_setting("title", "Lekhak"))
        # The author rides the OPF when the project carries one — the
        # same project-metadata "Author" the DOCX title page prints
        # (a collection of fliers has no author, and that is not our
        # business). Readers and library catalogues read dc:creator.
        author = ""
        entries = self.project_metadata()
        if "Author" in entries:
            author = (entries["Author"][0] or "").strip()
        creator = (f"<dc:creator>{escape(author)}</dc:creator>"
                   if author else "")
        lang_value = (entries.get("Language", ("",))[0] or "").strip()
        if not lang_value:
            try:
                from PySide6.QtCore import QLocale
                lang_value = QLocale.system().name().split("_")[0]
            except Exception:
                lang_value = "en"
        language = escape(lang_value)
        manifest.append(cover_item)
        nav = ("<?xml version='1.0' encoding='utf-8'?><html xmlns='http://www.w3.org/1999/xhtml' "
            "xmlns:epub='http://www.idpf.org/2007/ops'><head><title>Contents</title></head>"
            f"<body><nav epub:type='toc'><h1>Contents</h1><ol>{''.join(nav_items)}</ol></nav></body></html>")
        opf = ("<?xml version='1.0' encoding='utf-8'?><package xmlns='http://www.idpf.org/2007/opf' "
            "version='3.0' unique-identifier='book-id'><metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>"
            f"<dc:identifier id='book-id'>{uuid.uuid4().urn}</dc:identifier><dc:title>{title}</dc:title>"
            f"<dc:language>{language}</dc:language>{creator}{cover_meta}</metadata><manifest>"
            "<item id='nav' href='nav.xhtml' media-type='application/xhtml+xml' properties='nav'/>"
            f"{''.join(manifest)}</manifest><spine>{''.join(spine)}</spine></package>")
        container = ("<?xml version='1.0' encoding='UTF-8'?><container version='1.0' "
            "xmlns='urn:oasis:names:tc:opendocument:xmlns:container'><rootfiles>"
            "<rootfile full-path='OEBPS/content.opf' media-type='application/oebps-package+xml'/>"
            "</rootfiles></container>")
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}-", dir=destination.parent)
        os.close(fd)
        try:
            with zipfile.ZipFile(temporary_name, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
                archive.writestr("META-INF/container.xml", container)
                archive.writestr("OEBPS/content.opf", opf)
                archive.writestr("OEBPS/nav.xhtml", nav)
                for filename, page in pages:
                    archive.writestr(f"OEBPS/{filename}", page)
                if css.strip():
                    archive.writestr("OEBPS/style.css", css)
                if cover_entry:
                    archive.writestr(*cover_entry)
            os.replace(temporary_name, destination)
        except BaseException:
            Path(temporary_name).unlink(missing_ok=True)
            raise

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        except BaseException:
            Path(temporary_name).unlink(missing_ok=True)
            raise

    def _project_changed_since(self, when: float) -> bool:
        """Best-effort check: has anything in the project changed since `when`?

        Used to avoid creating identical timestamped backups when nothing
        changed. Checks the database and files in the project tree.
        """
        try:
            paths = [self.db_path]
            for entry in self.root.rglob("*"):
                if not entry.is_file():
                    continue
                # skip sqlite side files except main db; also skip nothing else critical
                if entry.name in {self.db_path.name + "-wal", self.db_path.name + "-shm"}:
                    continue
                paths.append(entry)
            for p in paths:
                try:
                    if p.stat().st_mtime > when:
                        return True
                except OSError:
                    return True
            return False
        except Exception:
            return True

    def backup(self, keep: int = 10, *, force: bool = False) -> Path | None:
        """Copy the project into a timestamped folder outside itself, then prune.

        Checkpoints the WAL first so the copied database is self-contained,
        and keeps only the newest ``keep`` backups. The copy is the folder
        tree as it stands, so a backup opens like the project. Templates
        and trash are included (normal binder folders, with rows pointing
        into them); a legacy inside ``backups/`` is not.

        A read-only project is not backed up: nothing in it changed, and
        the copy would land beside the software's own files — in the
        install folder, where it does not belong. None says so.
        """
        if self.read_only:
            return None
        self.db.execute("PRAGMA wal_checkpoint(FULL)")
        backup_directory = self.backup_directory()
        backup_directory.mkdir(parents=True, exist_ok=True)
        # Avoid creating a new backup when nothing changed since the last
        # timestamped backup, unless explicitly forced.
        if not force:
            try:
                existing = sorted(
                    (p for p in backup_directory.iterdir()
                     if p.is_dir() and BACKUP_STAMP_RE.fullmatch(p.name)),
                    reverse=True,
                )
                if existing:
                    latest = existing[0]
                    # if nothing in project changed after latest backup was made
                    if not self._project_changed_since(latest.stat().st_mtime):
                        self.set_setting("last_backup", datetime.now(timezone.utc).isoformat())
                        return latest
            except Exception:
                pass
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        destination = backup_directory / stamp
        destination.mkdir(parents=True)
        shutil.copy2(self.db_path, destination / self.db_path.name)
        # The manuscript is copied as the folder tree it is, so a backup
        # reads like the project (docs/STORAGE.md).
        for entry in self.root.iterdir():
            if entry.name in (self.db_path.name, "backups",
                              self.db_path.name + "-wal",
                              self.db_path.name + "-shm"):
                continue
            if entry.is_dir():
                shutil.copytree(entry, destination / entry.name)
            else:
                shutil.copy2(entry, destination / entry.name)
        # Prune only our own backups. The backup folder is the writer's to
        # choose, so it can already hold anything — other tools' folders,
        # their own — and a directory that is not one of our timestamps is
        # not ours to delete.
        backups = sorted(
            (path for path in backup_directory.iterdir()
             if path.is_dir() and BACKUP_STAMP_RE.fullmatch(path.name)),
            reverse=True,
        )
        for old in backups[keep:]:
            shutil.rmtree(old)
        self.set_setting("last_backup", datetime.now(timezone.utc).isoformat())
        return destination

    # --- the filesystem layout (docs/STORAGE.md) ---------------------------
    # The tree writer and reader, beside the database rather than replacing
    # it: the database is still the source of truth, and these turn it into
    # the folder tree a file manager (and another machine) can read.

    def to_nodes(self) -> list[Node]:
        """The binder as layout Nodes, in binder order (docs/STORAGE.md).

        A container is a folder whatever its role, and a leaf is a
        document: the adapter the tree writer goes through. Type becomes the
        node's own field (so the frontmatter does not repeat it), everything
        else the writer keeps stays in ``metadata``.

        Print geometry is a *project*-level map keyed by folder id, so it
        is looked up here and attached to the folder that owns it: the
        tree stores it beside the folder it describes rather than in one
        list at the top, which is what lets a copied book keep its own trim
        (docs/STORAGE.md).
        """
        documents = self.documents()
        metadata = self.metadata_entries_all()
        geometry = self._page_setup_map()
        children: dict[str | None, list[Document]] = {}
        for document in documents:
            children.setdefault(document.parent_id, []).append(document)

        def build(document: Document) -> Node:
            kids = sorted(children.get(document.id, []),
                          key=lambda item: item.position)
            extra = {key: value[0]
                     for key, value in metadata.get(document.id, {}).items()
                     if key != "Type"}
            return Node(
                id=document.id,
                title=document.title,
                kind=("folder" if document.kind == "folder" or kids
                      else "document"),
                type=self.type_of(document),
                content=self.read_content(document.id),
                synopsis=document.synopsis,
                word_target=document.word_target,
                metadata=extra,
                page_setup=clean_page_setup(geometry.get(document.id)),
                children=[build(child) for child in kids],
            )

        return [build(document)
                for document in sorted(children.get(None, []),
                                       key=lambda item: item.position)]

    def _page_setup_map(self) -> dict:
        """The raw ``page_setup`` setting as a dict: folder id -> geometry.

        Read as text and coerced rather than trusted — a hand-edited
        setting must not raise in an adapter, and junk simply means "no
        geometry recorded for that folder".
        """
        try:
            stored = json.loads(self.get_setting("page_setup") or "{}")
        except (TypeError, ValueError):
            stored = {}
        return stored if isinstance(stored, dict) else {}

    def write_tree(self, destination: Path, *,
                   settings: dict | None = None) -> dict:
        """Write the whole project out as a folder tree (docs/STORAGE.md).

        The migration's writer. The database stays the source of truth; this
        produces the tree it describes and returns what it wrote. App
        settings that describe the *project* travel in project.yaml — the
        link policy, the project-level metadata — while print geometry
        travels inside each folder that has it. The export's template pick
        does not travel either: it is one-shot (§2.1) and no longer stored,
        so carrying it would resurrect a preference the writer never made.
        """
        carried = dict(settings or {})
        value = self.get_setting("link_policy")
        if value:
            carried.setdefault("link_policy", value)
        project_metadata = self.get_setting("project_metadata")
        if project_metadata:
            carried.setdefault("project_metadata", project_metadata)
        return _write_tree(
            Path(destination), self.to_nodes(),
            title=self.get_setting("title", self.root.stem),
            settings=carried,
        )

    def read_tree(self, root: Path) -> tuple[list[Node], dict]:
        """Read a folder tree back into nodes and its project manifest."""
        return _load_tree(Path(root))

    def flatten_promoted_books(self) -> int:
        """Fold a promoted book's same-named wrapper chapter into its folder.

        The old Scrivener import made a shelf work's own block a chapter
        *child* of the book folder, wrapping every book in a redundant level
        — which is why a chapter-with-scenes read as a document. The folder
        *is* the book now (docs/STORAGE.md): the wrapper's words become the
        folder's own prose, its synopsis and metadata move across, and its
        children become the folder's own.

        Idempotent: after the fold no folder has a single same-named chapter
        child, so a second run does nothing. Returns how many were folded.
        """
        documents = self.documents()
        children: dict[str | None, list[Document]] = {}
        for document in documents:
            children.setdefault(document.parent_id, []).append(document)
        folded = 0
        for document in documents:
            if document.kind != "folder":
                continue
            kids = children.get(document.id, [])
            if (len(kids) != 1 or kids[0].kind != "chapter"
                    or kids[0].title != document.title):
                continue
            wrapper = kids[0]
            content = self.read_content(wrapper.id)
            if content:
                self.write_content(document.id, content)
            if wrapper.synopsis:
                self.update_document(document.id, synopsis=wrapper.synopsis)
            metadata = self.metadata(wrapper.id)
            if metadata:
                self.set_metadata(document.id, metadata)
            moved = sorted(children.get(wrapper.id, []),
                           key=lambda item: item.position)
            with self.db:
                for position, child in enumerate(moved):
                    self.db.execute(
                        "UPDATE documents SET parent_id=?, position=? "
                        "WHERE id=?",
                        (document.id, position, child.id))
                self.db.execute("DELETE FROM documents WHERE id=?",
                                (wrapper.id,))
            self.content_path(wrapper.id).unlink(missing_ok=True)
            folded += 1
        return folded

    def unwrap_folder(self, document_id: str) -> int:
        """Move a folder's children up to its parent, then delete the folder.

        Dissolves one grouping level without touching its contents: an
        imported Scrivener shelf ("Manuscript"), a series folder the writer
        wants gone. Children keep their order and land after the parent's
        existing siblings; the folder's own prose is not carried up (a
        grouping folder has none worth keeping — fold it with
        flatten_promoted_books first if it does). Returns how many children
        moved; an empty folder is simply removed. Idempotent: a second call
        finds nothing.
        """
        documents = self.documents()
        document = next((d for d in documents if d.id == document_id), None)
        if document is None:
            return 0
        moved = sorted((d for d in documents if d.parent_id == document_id),
                       key=lambda item: item.position)
        row = self.db.execute(
            "SELECT COALESCE(MAX(position), -1) FROM documents "
            "WHERE parent_id IS ?", (document.parent_id,)).fetchone()
        start = int(row[0]) + 1
        with self.db:
            for offset, child in enumerate(moved):
                self.db.execute(
                    "UPDATE documents SET parent_id=?, position=? WHERE id=?",
                    (document.parent_id, start + offset, child.id))
            self.db.execute("DELETE FROM documents WHERE id=?", (document_id,))
        self.content_path(document_id).unlink(missing_ok=True)
        return len(moved)

    def validate_alignment(self) -> list[str]:
        """Check that manifest.json, .order.json files and DB are in sync.

        Returns a list of problems found, or an empty list if everything is aligned.
        This is a read-only check that never modifies the project.

        A disagreement is a real prompt, not a silent fix-up: the caller
        is expected to ask the writer which side is right (see
        :meth:`apply_manifest` and :meth:`apply_order_files`).
        """
        problems: list[str] = []

        # 1. Check DB integrity first
        integrity = self.db.execute("PRAGMA integrity_check").fetchone()
        if integrity[0] != "ok":
            problems.append(f"Database integrity check failed: {integrity[0]}")

        # 2. Check that folders with .order.json match DB folders.
        #    A folder here is any document backed by a directory on disk
        #    (folder, chapter, or a leaf promoted to a container) — the
        #    same test write_order_files() uses. Templates and trash are
        #    normal folders, so they are checked like any other. Only a
        #    legacy inside ``backups/`` and ``.lekhak/`` are not
        #    binder folders.
        system_trees = {"backups", ".lekhak"}

        def _is_system(path_str: str) -> bool:
            return path_str.split("/")[0].casefold() in system_trees

        # Backups should never exist inside the project at all — they
        # live outside (backup_directory()), so an inside folder is an
        # error to report, not something to silently skip.
        if (self.root / "backups").exists():
            problems.append(INSIDE_BACKUPS_PROBLEM)

        folders_with_order = {
            str(path.parent.relative_to(self.root))
            for path in self.root.rglob(".order.json")
            if path.parent != self.root
            and not _is_system(str(path.parent.relative_to(self.root)))
        }
        folders_in_db = {
            document.path
            for document in self.documents()
            if document.path and (self.root / document.path).is_dir()
        }

        missing_in_db = folders_with_order - folders_in_db
        for folder_path in sorted(missing_in_db):
            problems.append(f"Folder {folder_path} has .order.json but no DB row")

        # A folder the DB knows must have its order file: the writer may
        # have deleted it, or a folder was copied in from outside without
        # one. (An unreadable or malformed one is flagged in check 4.)
        for document in self.documents():
            if (document.path and (self.root / document.path).is_dir()
                    and not _is_system(document.path)):
                order_file = self.root / document.path / ".order.json"
                if not order_file.exists():
                    problems.append(
                        f"Folder {document.path} has a DB row but no .order.json")

        # 3. Check manifest.json children match root-level DB items
        manifest_children = set()
        manifest_ordered: list[str] = []
        manifest_path = self.root / "manifest.json"
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("children"), list):
                    manifest_ordered = [str(item) for item in data["children"]]
                    manifest_children = set(manifest_ordered)
            except (json.JSONDecodeError, OSError):
                problems.append("manifest.json is unreadable")

        root_items_in_db = {
            d.path.split("/")[0] if "/" in d.path else d.path
            for d in self.documents()
            if not d.parent_id and d.path
        }

        # Filter out system folders (first component, case-insensitive)
        user_root_items = {
            item
            for item in root_items_in_db
            if item.casefold() not in system_trees
        }

        if manifest_children != user_root_items:
            missing_in_manifest = user_root_items - manifest_children
            extra_in_manifest = manifest_children - user_root_items
            for item in sorted(missing_in_manifest):
                problems.append(f"Root item {item} missing from manifest.json")
            for item in sorted(extra_in_manifest):
                problems.append(f"Item {item} in manifest.json but not in DB")
        else:
            # Same items, different sequence — a hand-rearranged manifest
            # (or a DB reordered without writing). Either way it is a
            # disagreement to ask about, not to smooth over.
            db_root_ordered = [
                d.path.split("/")[0] if "/" in d.path else d.path
                for d in sorted(
                    (d for d in self.documents()
                     if not d.parent_id and d.path),
                    key=lambda d: d.position)
                if d.path.split("/")[0].casefold() not in system_trees
            ]
            if manifest_ordered != db_root_ordered:
                problems.append(
                    "Root order in manifest.json differs from the database")

        # 4. Check that every folder's .order.json matches its DB children
        for order_path in self.root.rglob(".order.json"):
            if order_path.name == "manifest.json":
                continue
            if _is_system(str(order_path.parent.relative_to(self.root))):
                continue  # trash/backups trees are not binder folders

            parent_path = str(order_path.parent.relative_to(self.root))
            try:
                order_data = json.loads(order_path.read_text(encoding="utf-8"))
                if not isinstance(order_data, dict) or not isinstance(order_data.get("children"), list):
                    problems.append(f"{order_path.relative_to(self.root)}: invalid format")
                    continue
                order_ordered = [str(item) for item in order_data["children"]]
                # index.html is the folder's own page, not a child: it
                # leads the order but is no child of the binder.
                order_children = {c for c in order_ordered if c != "index.html"}
            except (json.JSONDecodeError, OSError):
                problems.append(f"{order_path.relative_to(self.root)}: unreadable")
                continue

            # Find parent ID
            parent_id = None
            for doc in self.documents():
                if doc.path == parent_path:
                    parent_id = doc.id
                    break

            if parent_id:
                siblings = sorted(
                    (d for d in self.documents() if d.parent_id == parent_id),
                    key=lambda d: d.position)
                db_ordered = [
                    d.path.split("/")[-1] if "/" in d.path else d.path
                    for d in siblings
                    if d.path and d.path.split("/")[-1] not in (".locked", "index.html")
                ]
                db_children = {
                    d.path.split("/")[-1] if "/" in d.path else d.path
                    for d in self.documents()
                    if d.parent_id == parent_id
                }
                # Filter out system files and index.html
                db_children = {c for c in db_children if c not in (".locked", "index.html")}

                if order_children != db_children:
                    for child in sorted(db_children - order_children):
                        problems.append(
                            f"{order_path.relative_to(self.root)}: {child} missing from order file")
                    for child in sorted(order_children - db_children):
                        if child != "index.html":  # index.html is special
                            problems.append(
                                f"{order_path.relative_to(self.root)}: {child} extra in order file")
                elif [c for c in order_ordered if c != "index.html"] != db_ordered:
                    problems.append(
                        f"{order_path.relative_to(self.root)}: order differs from the database")

        return problems

    def reconcile(self, *, dry_run: bool = False) -> dict[str, int]:
        """Bring all synchronization files into alignment with the database.

        Rewrites .order.json files and manifest.json to match the DB state.
        Returns a dict with counts of changes made (or what would be changed
        in dry_run mode).

        Use with caution: this rewrites synchronization files. It is the
        responsibility of the caller to ensure a backup exists if needed.
        """
        changes = {"order_files": 0, "manifest": 0}

        # Find all containers: any document backed by a directory on
        # disk, the same test write_order_files() uses. This covers
        # empty folders and empty chapters alike.
        containers = [
            d
            for d in self.documents()
            if d.path and (self.root / d.path).is_dir()
        ]

        for container in containers:
            if not container.path:
                continue
            folder_path = self.root / container.path
            if not folder_path.is_dir():
                continue

            order_path = folder_path / ".order.json"

            # Get children from DB
            children = [
                d.path.split("/")[-1] if "/" in d.path else d.path
                for d in self.documents()
                if d.parent_id == container.id
            ]

            # Read existing order
            try:
                existing = json.loads(order_path.read_text(encoding="utf-8"))
                old_children = set(existing.get("children", []))
            except (json.JSONDecodeError, OSError):
                old_children = set()

            new_children = set(children)

            if old_children != new_children and not dry_run:
                order_path.write_text(
                    json.dumps({"children": children}, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
                changes["order_files"] += 1
            elif old_children != new_children:
                changes["order_files"] += 1  # Would change

        # Root children, excluding system trees (matched on the first
        # path component). Templates and trash are normal folders and
        # appear in the manifest like any other.
        _system = {"backups", "images", ".lekhak"}
        root_children = [
            d.path.split("/")[-1] if "/" in d.path else d.path
            for d in self.documents()
            if not d.parent_id
            and d.path
            and d.path.split("/")[0].casefold() not in _system
        ]

        manifest_path = self.root / "manifest.json"
        old_manifest = {}
        try:
            old_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

        old_children = set(old_manifest.get("children", []))
        new_children = set(root_children)

        if (old_children != new_children or
                old_manifest.get("title") != self.get_setting("title")) and not dry_run:
            manifest_path.write_text(
                json.dumps({
                    "title": self.get_setting("title") or self.root.stem,
                    "version": 1,
                    "children": root_children
                }, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
            changes["manifest"] = 1
        elif old_children != new_children or old_manifest.get("title") != self.get_setting("title"):
            changes["manifest"] = 1  # Would change

        return changes

    def _apply_order(self, parent_id: str | None,
                     ordered_names: list[str]) -> None:
        """Set one folder's sibling positions from a written sequence.

        Names the DB does not know under this parent are ignored — an
        order source never invents a document or drops one — and
        children the sequence does not name keep their relative order
        at the end. Only positions change, never parents.
        """
        siblings = sorted(
            (d for d in self.documents() if d.parent_id == parent_id),
            key=lambda d: d.position)
        by_basename: dict[str, Document] = {}
        for document in siblings:
            if document.path:
                by_basename.setdefault(Path(document.path).name, document)
        placed: list[Document] = []
        seen: set[str] = set()
        for name in ordered_names:
            document = by_basename.get(name)
            if document is None or document.id in seen:
                continue
            seen.add(document.id)
            placed.append(document)
        for document in siblings:
            if document.id not in seen:
                placed.append(document)
        with self.db:
            for position, document in enumerate(placed):
                self.db.execute(
                    "UPDATE documents SET position=? WHERE id=?",
                    (position, document.id))

    def _read_order_children(self, path: Path) -> list[str] | None:
        """A written sequence ("children"), or None when the file is
        missing or unreadable — no sequence to follow means no change."""
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("children"), list):
                return [str(item) for item in data["children"]]
        except (json.JSONDecodeError, OSError):
            pass
        return None

    def apply_manifest(self) -> None:
        """Make the database follow manifest.json (the root order).

        One of the three truths: the sequence the writer arranged in
        the manifest — for example with a text editor — becomes the
        binder's root order. Unknown names are ignored; root items the
        manifest does not name keep their relative order at the end.
        Nothing else is rewritten here; the caller reconciles after.
        """
        ordered = self._read_order_children(self.root / "manifest.json")
        if ordered is not None:
            self._apply_order(None, ordered)

    def apply_order_files(self) -> None:
        """Make the database follow the .order.json files.

        The other order truth: every folder's own sequence — a writer
        who rearranged inside a folder by editing its .order.json sees
        that arrangement. A name moved into a different folder's list
        matches nothing there and is left alone. Nothing else is
        rewritten here; the caller reconciles after.
        """
        for document in self.documents():
            if not document.path or not (self.root / document.path).is_dir():
                continue
            ordered = self._read_order_children(
                self.root / document.path / ".order.json")
            if ordered is not None:
                self._apply_order(document.id, ordered)

    def forget_missing_files(self) -> list[str]:
        """Drop rows whose files are gone, then rewrite the order files.

        The "OK" answer to the missing-files warning: the records agree,
        the file does not exist — so the entry comes out of the project.
        Legacy rows with no path are kept (they are not disk-managed).
        Returns the paths whose entries were removed.
        """
        removed = []
        for document in self.documents():
            if document.path and not (self.root / document.path).exists():
                self._forget_row(document.id)
                removed.append(document.path)
        if removed:
            self.reconcile()
        return removed

    def missing_files(self) -> list[str]:
        """Project-relative paths the database expects but the disk lacks.

        Not a resolution and not a disagreement between records — the
        records agree, the file is simply gone (deleted elsewhere, a
        folder copied without it). Legacy rows with no path are not
        disk-managed and are not reported. The trash root is not
        reported either: an empty trash is a folder the app makes when
        something is thrown away, and its absence is not lost writing.
        The caller warns; it never silently heals this.
        """
        return sorted(
            document.path for document in self.documents()
            if document.path and document.path != TRASH_FOLDER
            and not (self.root / document.path).exists())
