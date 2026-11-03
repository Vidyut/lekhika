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
import hashlib
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
    FORMATS, TemplateSet, discover_template_sets, named_template_sets,
    parse_docx_config, project_template_root, split_head,
)


# Export formats by slug, for a binder folder whose children are named after
# them (2.1b): ``frontmatter/epub`` and ``frontmatter/docx`` are variants.
FORMAT_NAMES = frozenset(FORMATS)

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
# else.
PROJECT_FURNITURE = frozenset({
    "project.sqlite3", "manifest.json", "backups", "documents", ".lekhak",
})
# The templates area: the folder of template sets at the project's root.
# It is not manuscript — the sets are listed by the template code, and
# they are reached from the Outliner.
TEMPLATES_FOLDER = "templates"

# Kinds are labels, not structure: any item may contain any other, at any
# depth, and several items may share the top level. Folder is a neutral
# container; chapter/scene/note are the writer's shorthand for intended use.
# The physical structure is folders and files; the semantic role lives in
# the protected Type metadata (docs/MODEL.md) and type_of() derives it.
DOCUMENT_KINDS = {"folder", "chapter", "scene", "note"}
# What a filesystem refuses, and the names Windows reserves.
_ILLEGAL_NAME_CHARS = set('<>:"/\\|?*') | {chr(code) for code in range(32)}
_RESERVED_NAME_STEMS = {
    "con", "prn", "aux", "nul",
    *(f"com{n}" for n in range(1, 10)),
    *(f"lpt{n}" for n in range(1, 10)),
}
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
# settings will override this per document later; folders and notes stay out.
EXPORT_KINDS = {"chapter", "scene"}

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
                     "footer", "footer left", "footer right")

# A running-head line that asks for nothing (`Header: none`): unlike an
# empty line — which means "inherit the sample" — it says the page
# carries no head at all, which is what a title page wants.
NO_RUNNING_HEAD = {"none", "no", "off"}

# The running-head/folio vocabulary's one non-string value (§3.1): a
# Header or Footer line whose middle piece is exactly `${Page}` asks for
# the page's own number, which a header cannot spell — it survives the
# value fill as this marker and becomes Word's PAGE field in the part.
# Private-use codepoints, so no writer's prose can collide with it.
PAGE_MARKER = "\ue000page\ue000"

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


def piece_slug(name: str) -> str:
    """The name a piece and a binder item are compared by (2.1b).

    A piece names binder content, and the author's rule is that the name must
    *match* the folder — no nearest-name guessing, because silently including
    the wrong front matter is worse than including none. Matching is by slug,
    so ``Front Matter``, ``front-matter`` and ``front matter`` are one target
    (the line is typed by hand), while ``frontmatter`` stays its own name.
    """
    text = str(name or "").casefold().strip()
    return re.sub(r"[\W_]+", "-", text).strip("-")


def piece_name(name: str, fmt: str | None) -> str:
    """A piece name with the export's format filled in (2.1b).

    ``frontmatter-${type}`` becomes ``frontmatter-docx`` for a DOCX export and
    ``frontmatter-epub`` for an EPUB, so one piece line serves a binder that
    holds one folder per format. Anything else in the name is left alone.
    """
    return str(name or "").replace("${type}", str(fmt or "").strip().lower())


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


def _brace_aliases(text: str) -> str:
    """``{Series}`` — the spelling the author's own config example uses
    — read as ``${Series}``: one namespace, written however the writer
    already writes it (§3.1)."""
    return re.sub(r"(?<!\$)\{([A-Za-z][A-Za-z0-9 ._-]*)\}", r"${\1}", text)


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
    """
    text = (name or "").strip()
    kept = ["-" if (character in _ILLEGAL_NAME_CHARS) else character
            for character in text]
    cleaned = "".join(kept).strip().rstrip(". ")
    if not cleaned:
        cleaned = "Untitled"
    if cleaned.split(".")[0].casefold() in _RESERVED_NAME_STEMS:
        cleaned = f"_{cleaned}"
    if len(cleaned) > 120:
        cleaned = cleaned[:120].rstrip(". ")
    return cleaned or "Untitled"


class Project:
    """Transparent project storage with atomic document writes."""

    def __init__(self, root: Path):
        self.root = Path(root)
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

    def open(self, create: bool = False) -> None:
        """Open the database, creating the schema and applying migrations.

        Migrations run inline here: new columns are added with a default,
        and one-time fixes are guarded by a settings flag so they fire
        exactly once per project. Any future schema change follows the
        same pattern — add the column, guard the backfill, never rewrite
        user content.
        """
        if not create and not self.db_path.exists():
            raise FileNotFoundError(f"Not a Lekhak project: {self.root}")
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
        """Store a project-level key (title, backup folder, view prefs)."""
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

    def find_child_folder(self, title: str, parent_id: str | None = None) -> Document | None:
        """The child folder of ``parent_id`` with this title, or None.

        Case-insensitive and stripped, because a folder name is a name, not a
        key — but only a *lookup*: callers decide what a collision means
        (imports ask; see ``place_imported_folder``).
        """
        needle = title.strip().casefold()
        if not needle:
            return None
        for document in self.documents():
            if (document.parent_id == parent_id and document.kind == "folder"
                    and document.title.strip().casefold() == needle):
                return document
        return None

    def unique_title(self, title: str, parent_id: str | None = None) -> str:
        """"Book", then "Book (2)", "Book (3)" — the file-manager convention.

        Only the importers use this, and only for containers (folders and
        chapters): an import must never land on top of an existing same-named
        container, and when the writer chooses "keep separate" this is what
        keeps both. Documents keep their titles — repeating a scene title is
        ordinary, and imports have no business renaming prose.
        """
        taken = {document.title.strip().casefold() for document in self.documents()
                 if document.parent_id == parent_id}
        base = title.strip() or "Untitled"
        if base.casefold() not in taken:
            return base
        number = 2
        while f"{base} ({number})".casefold() in taken:
            number += 1
        return f"{base} ({number})"

    def subtree_fingerprint(self, document_id: str) -> str:
        """A crude content fingerprint: every descendant's depth, title and
        plain text, in binder order.

        Crude is the point (author's rule): imports compare folders by name
        and, when the names line up, by what is inside — enough to tell "the
        same file again" from "a different draft with the same folder names".
        """
        by_parent: dict[str | None, list[Document]] = {}
        for document in self.documents():
            by_parent.setdefault(document.parent_id, []).append(document)
        digest = hashlib.sha256()

        def walk(parent_id: str | None, depth: int) -> None:
            for document in sorted(by_parent.get(parent_id, []),
                                   key=lambda d: d.position):
                digest.update(f"{depth}\x1f{document.title}\x1f".encode("utf-8"))
                digest.update(
                    self.plain_text(self.read_content(document.id)).encode("utf-8"))
                digest.update(b"\x1e")
                walk(document.id, depth + 1)

        walk(document_id, 0)
        return digest.hexdigest()

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
        """
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
        self.db.execute(
            "UPDATE documents SET path=? WHERE id=?",
            (str(wanted.relative_to(self.root)), document_id),
        )
        self.db.commit()

    def reorder(self, document_id: str, parent_id: str | None, position: int) -> bool:
        """Place a document at a position under a parent, file and row together.

        Returns False and writes nothing if the file cannot go there —
        a name already taken in the destination folder, most often.
        The folder tree is the truth (docs/STORAGE.md), so a parent
        change means the file moves first and the index follows; the
        two are never left disagreeing.
        """
        if parent_id == document_id or self._is_descendant(parent_id, document_id):
            raise ValueError("A document cannot contain itself")
        row = self.db.execute(
            "SELECT parent_id FROM documents WHERE id=?", (document_id,)
        ).fetchone()
        if row is None:
            return True
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
        prefix = old_relative + "/"
        updates: list[tuple[str, str]] = []
        for item in self.db.execute("SELECT id, path FROM documents").fetchall():
            if not item[1]:
                continue
            if item[1] == old_relative:
                updates.append((new_relative, item[0]))
            elif item[1].startswith(prefix):
                updates.append((new_relative + "/" + item[1][len(prefix):], item[0]))
        with self.db:
            self.db.executemany(
                "UPDATE documents SET path=? WHERE id=?", updates)
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

        The two go together or not at all. The rows are removed first —
        a crash between the two leaves orphaned files on disk (harmless,
        and the backup still has them) rather than a live row pointing at
        a file that no longer exists. But if a file turns out to be
        undeletable (a permission, a read-only mount, a lock), the rows
        are put back and :class:`DeleteError` says so: the document stays
        in the binder, and the writer is told why.
        """
        descendants = self._descendant_ids(document_id)
        # The rows are remembered so they can be put back if a file
        # refuses to go, and the places to clear are gathered before the
        # rows go: once they are gone, the paths are gone with them.
        rows: list[tuple] = []
        places: list[Path] = []
        for item_id in descendants:
            row = self.db.execute(
                "SELECT id,parent_id,title,synopsis,position,word_target,"
                "kind,word_count,page_break,scene_divider,doc_template,path,"
                "created_at,updated_at FROM documents WHERE id = ?",
                (item_id,),
            ).fetchone()
            if row is None:
                continue
            rows.append(tuple(row))
            if not row[11]:
                continue
            path = self.root / str(row[11])
            # A container is a directory: the folder goes, and with it
            # whatever it held (docs/STORAGE.md).
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
            # Nothing is deleted after all: the rows go back, so the
            # document is still there for the writer to deal with.
            self._restore_rows(rows)
            raise DeleteError(failures)
        self.write_order_files()

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

        The order file is the sequence of a folder's children, by name
        (docs/STORAGE.md). It travels with the folder, so a copy of the
        folder keeps its order — and if the database is ever lost, the
        tree can still be read back exactly as it was.
        """
        documents = self.documents()
        children: dict[str | None, list[Document]] = {}
        for document in documents:
            children.setdefault(document.parent_id, []).append(document)
        for document in documents:
            if not document.path:
                continue
            folder = self.root / document.path
            if not folder.is_dir():
                continue
            names = []
            for child in sorted(children.get(document.id, []),
                                key=lambda d: d.position):
                if not child.path:
                    continue
                names.append(Path(child.path).name)
            (folder / ".order.json").write_text(
                json.dumps({"children": names}, ensure_ascii=False, indent=2)
                + "\n", encoding="utf-8")
        # The root's own order lives in the project manifest.
        names = []
        for child in sorted(children.get(None, []), key=lambda d: d.position):
            if child.path:
                names.append(Path(child.path).name)
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
            added += 1
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
                                and (entry.name in PROJECT_FURNITURE
                                     or entry.name == TEMPLATES_FOLDER))]
        except OSError:
            return []
        order = self._order_names(folder, relative)
        rank = {name: index for index, name in enumerate(order)}

        def key(entry: Path) -> tuple[int, int, str]:
            name = entry.name
            if name in rank:
                return (0, rank[name], name.casefold())
            return (1, 0, name.casefold())

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

        The project's own furniture — the database, the manifest, the
        backups — is not part of the manuscript, so it is not walked,
        and neither is the templates area: the sets are listed by the
        template code, not as manuscript. The furniture is skipped at
        the root only: a writer's own book called "documents" is theirs.
        """
        skip = {"backups", "documents", ".lekhak"}

        def walk(folder: Path):
            try:
                entries = sorted(folder.iterdir(), key=lambda p: p.name.casefold())
            except OSError:
                return
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                if folder == self.root and entry.name in skip:
                    continue
                if entry.is_dir():
                    if entry.name == TEMPLATES_FOLDER and folder == self.root:
                        continue
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
        if marker is None:
            return
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

    def set_template(self, document_id: str, on: bool) -> None:
        """Mark a document as a template, or take the mark away.

        The mark is the document's own metadata (and the file's head
        beside it) and nothing else: it says what the document *is*, and
        every export asks. The row is written on its own, so replacing
        the document's metadata elsewhere never unmakes the template.
        """
        if on:
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

    def _export_documents(self, selected_ids: set[str] | None = None,
                          exclude_ids: set[str] | None = None
                          ) -> list[Document]:
        """The chapters and scenes to compile, in binder order.

        Folder ids in ``selected_ids`` expand to their contents — picking
        a book folder exports the book. None selects the whole
        manuscript. ``exclude_ids`` removes a subtree even from that
        whole-manuscript case, which a selection cannot express when the
        root itself is selected: 2.1b uses it so a piece the template
        places elsewhere is not printed inside the content as well.
        """
        documents = self.ordered_documents()
        parent_map = {d.id: d.parent_id for d in documents}
        excluded = exclude_ids or set()
        export_docs: list[Document] = []
        for document in documents:
            if document.kind not in EXPORT_KINDS:
                continue
            if self.is_template(document.id):
                continue        # a template is markup, not manuscript
            current: str | None = document.id
            covered = selected_ids is None
            drop = False
            while current is not None:
                if current in excluded:
                    drop = True
                    break
                if selected_ids is not None and current in selected_ids:
                    covered = True
                current = parent_map.get(current)
            if covered and not drop:
                export_docs.append(document)
        return export_docs

    def _template_values(self, document: Document, content: str | None = None,
                         extra: dict[str, str] | None = None) -> dict[str, str]:
        if content is None:
            content = self.read_content(document.id)
        values = {
            "Title": document.title,
            "Kind": document.kind,
            "Synopsis": document.synopsis,
            "WordCount": str(document.word_count),
            "Content": content,
            "Author": (self.project_metadata().get("Author", ("", "text", []))[0]
                       if "Author" in self.project_metadata() else ""),
        }
        for key, value in self.metadata(document.id).items():
            values[f"Metadata.{key}"] = value
        if extra:
            values.update(extra)
        return values

    def render_template(self, document: Document, template: str,
                        *, html: bool = False) -> str:
        """Fill a one-off template string with ``${...}`` placeholders."""
        from .templates import PLACEHOLDER_RE

        values = self._template_values(document)
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
                          extra: dict[str, str] | None = None) -> str:
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
        values = self._template_values(document, content="", extra=extra)
        values.pop("Content", None)

        def substitute(match: re.Match) -> str:
            key = match.group(1).strip()
            value = values.get(key, "")
            return escape(value) if html else value

        return PLACEHOLDER_RE.sub(substitute, text)

    def _nearest_chapter_ancestor(self, document_id: str,
                                  parent_map: dict[str, str | None],
                                  kinds: dict[str, str]) -> str | None:
        current = parent_map.get(document_id)
        while current is not None:
            if kinds.get(current) == "chapter":
                return current
            current = parent_map.get(current)
        return None

    def _render_document(self, document: Document, templates: TemplateSet,
                         html: bool, *, slot: str | None = None,
                         content: str | None = None,
                         extra: dict[str, str] | None = None,
                         scope: dict | None = None,
                         fmt: str = "html") -> str:
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
                                          extra=extra)
        else:
            body = content
        values = self._template_values(document, content=body, extra=extra)
        # Contents page / chapter scenes: filled when the template asks.
        values["ToC"] = getattr(self, "_toc_str", "")
        values.setdefault("Scenes", (extra or {}).get("Scenes", ""))
        if document.doc_template:
            override = self.template_override(document.doc_template, html=html)
            if override is None:
                override = templates.document_slot(document.doc_template, html=html)
            if override is not None:
                return templates.fill(override, values, html=html)
        if slot is None:
            slot = "folder" if document.kind == "chapter" else "document"
        return templates.render(slot, values, html=html)

    def _compile_scope(self, selected_ids: set[str] | None,
                       fmt: str,
                       exclude_ids: set[str] | None = None) -> dict:
        """Everything one compile's links need to know (2.2): the exported
        document ids, each document's unit index (shared by a chapter and
        its scenes, 0-based), the ancestor chain used to look up per-book
        settings, an exact-title resolution index over the whole binder,
        and the project-wide strip policy.

        Split out of compile_units so header/footer strings — compiled
        outside the unit list — run through the same machinery.
        """
        export_docs = self._export_documents(selected_ids, exclude_ids)
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
                      *, html: bool = True, fmt: str = "html",
                      exclude_ids: set[str] | None = None
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
        scope = self._compile_scope(selected_ids, fmt, exclude_ids)
        export_docs = scope["export_docs"]
        ordered = scope["ordered"]
        chapter_scenes = scope["chapter_scenes"]

        # Contents page support: a document whose template contains ${ToC}
        # is filled with the book's table of contents. Build the list
        # up front so it is available even when the contents page sits
        # in front matter, before the units are rendered.
        _child_scenes = set()
        for _scenes in chapter_scenes.values():
            for _s in _scenes:
                _child_scenes.add(_s.id)
        _unit_docs = [d for d in ordered
                      if d.kind == "chapter" or d.id not in _child_scenes]
        _toc_titles = [d.title for d in _unit_docs]
        if fmt == "epub":
            _toc_str = "<ol>" + "".join(
                f"<li><a href='section-{i}.xhtml'>{escape(t)}</a></li>"
                for i, t in enumerate(_toc_titles, 1)) + "</ol>"
        elif fmt == "html":
            _toc_str = "<ol>" + "".join(
                f"<li>{escape(t)}</li>" for t in _toc_titles) + "</ol>"
        elif fmt == "text":
            _toc_str = "\\n".join(_toc_titles)
        else:
            _toc_str = ""
        self._toc_str = _toc_str

        units: list[tuple[str, str]] = []
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
                    scene_rendered.append(self._render_document(
                        scene, scene_set, html, slot="document", extra=extra,
                        scope=scope, fmt=fmt))
                    words_total += scene.word_count
                extra = _template_numbers(chapter_number, 0, words_total)
                extra["Scenes"] = "\n".join(scene_rendered)
                body = self._render_document(
                    document, book, html, slot="folder",
                    content=own if own.strip() else "",
                    extra=extra, scope=scope, fmt=fmt)
                words_total += document.word_count
                units.append((document.title, body))
            else:
                scene_number += 1
                scene_set = set_for(document.id)
                extra = _template_numbers(chapter_number, scene_number,
                                          words_total)
                body = self._render_document(
                    document, scene_set, html, extra=extra,
                    scope=scope, fmt=fmt)
                words_total += document.word_count
                units.append((document.title, body))
        return units

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
        templates = (templates or TemplateSet.builtin()).for_format("text")
        if templates.prints_nothing:
            # `no export` (§3): no fragment carries content, so neither
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

        The round-trip counterpart of importer.markdown_from_html's
        sibling import: no template chrome and no book-settings pages —
        chapters as '# Title', scenes as '## Title', prose converted
        back from the stored HTML with inline bold and italic kept — so
        a file this writes imports back as the same structure. Wiki
        links become plain words (the text-fallthrough of the 2.2 link
        pass); resolved targets inside the export do not become real
        markdown links, which have no notion of a sibling document.
        Form feeds likewise stay out (BUILD-NOW 9): markdown is
        structural too.
        """
        # Local import: importer imports Project at module level.
        from .importer import markdown_from_html

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

        for document in ordered:
            heading = "# " if document.kind == "chapter" else "## "
            add(heading + document.title)
            add(body_of(document))
            for scene in scenes_of.get(document.id, []):
                add("## " + scene.title)
                add(body_of(scene))
        content = "\n\n".join(blocks)
        self._atomic_write(Path(destination), content.rstrip() + "\n")

    def export_html(self, destination: Path, selected_ids: set[str] | None = None,
                    templates: TemplateSet | None = None) -> None:
        """Write the compiled manuscript as a single HTML file."""
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
        """What heads an export (Phase 1): the selected containers' names, the
        document's own name when one document is all that was chosen, or the
        project's title for a whole-project export."""
        if not selected_ids:
            return [self.get_setting("title", "Lekhak")]
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
        return [document.title for document in roots]

    @staticmethod
    def _add_toc_field(output) -> None:
        """The Word TOC field, placed after the title page (Phase 1) so the
        contents do not stand in front of the book's own opening pages."""
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        paragraph = output.add_paragraph()
        run = paragraph.add_run()
        begin = OxmlElement("w:fldChar")
        begin.set(qn("w:fldCharType"), "begin")
        instruction = OxmlElement("w:instrText")
        instruction.set(qn("xml:space"), "preserve")
        instruction.text = 'TOC \\o "1-3" \\z \\u'
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
        `${Page}`): the same field shape the toc writer uses, so a folio
        numbers itself instead of spelling "Page" down the margin. An
        optional prefix carries the tab that centres the piece — fields
        are runs, and the run before them holds the tab."""
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
        book = titles[0] if titles else str(self.get_setting("title", "Lekhak"))
        values["Book"] = book
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
                              geometry: dict, sections=None) -> None:
        """``Header``/``Footer`` lines as running heads and folios
        (§3.1): one line, three pieces — left, middle, right — set on
        tab stops at the middle and the end of the text width, filled
        through the same `${...}` namespace a fragment uses. A line's
        ``left`` variant is the verso head and switches on Word's
        odd/even headers, which is what mirroring a hand-built book
        means in practice; ``right`` is the recto one. An all-empty line
        says nothing, so the sample's own head stands (§3.1: the config
        overrides only the lines it names). A piece that is exactly
        ``${Page}`` becomes a PAGE field, so the folio numbers the
        pages rather than spelling "Page" on every one of them.

        ``sections`` limits the pass to one page's own section (1.18);
        the default is every section of the file. ``Header: none`` is
        the one line that means *nothing there* rather than *inherit*:
        a title page or a dedication carries no running head.
        """
        from docx.enum.text import WD_TAB_ALIGNMENT
        from docx.shared import Inches

        if sections is None:
            sections = output.sections
        else:
            sections = list(sections)
        width = (geometry["width"] - geometry["inside"] - geometry["outside"])

        def line(part, pieces):
            """One line in one running part: everything already in it is
            sample prose (or an older answer), and the writer's three
            pieces take its place. A ``${Page}`` piece arrives still a
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
                    Project._add_page_field(keep, prefix)
                    continue
                keep.add_run(prefix + piece)

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
            for suffix, which in (("", "odd"), (" left", "even"),
                                   (" right", "odd")):
                key = noun + suffix
                if key not in config:
                    continue
                said = str(config[key] or "").strip()
                if not said:
                    continue            # an empty line says nothing
                if said.lower() in NO_RUNNING_HEAD:
                    # `Header: none` — this page carries no head. The
                    # plain key stands for the pair, so both parts go.
                    for section in sections:
                        if noun == "header":
                            pair = (section.header,
                                    section.even_page_header)
                        else:
                            pair = (section.footer,
                                    section.even_page_footer)
                        for part in pair:
                            part.is_linked_to_previous = False
                            line(part, [])
                    continue
                pieces = []
                for piece in config_headings(config[key]):
                    # The page request is recognised on the *raw* piece:
                    # filling first would collapse `${Page}` to nothing
                    # (an unknown key renders empty), and a folio would
                    # silently become a blank middle tab.
                    spelled = _brace_aliases(piece).strip()
                    if spelled == "${Page}":
                        pieces.append(PAGE_MARKER)
                        continue
                    pieces.append(TemplateSet.fill(
                        _brace_aliases(piece), values, html=False).strip())
                if not any(pieces):
                    continue            # an empty line says nothing
                even_odd = even_odd or which == "even"
                parts = (which == "odd")
                for section in sections:
                    if noun == "header":
                        part = section.header if parts else section.even_page_header
                    else:
                        part = section.footer if parts else section.even_page_footer
                    part.is_linked_to_previous = False
                    line(part, pieces)
        if even_odd:
            output.settings.element.get_or_add_evenAndOddHeaders()

    @staticmethod
    def _apply_print_font(output, config: dict) -> None:
        """``Font: Georgia, 11pt, 1, 1cm`` — the shorthand §3.1 gives
        (font, size, line spacing, indent) — set on the body style the
        manuscript is actually written in, overriding the sample's own
        line by line: one config line, one override. Pieces are read one
        at a time, so a shorter line (`Font: Georgia`) still says what
        it says and the rest keeps the sample's answer."""
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

    def export_docx(self, destination: Path, selected_ids: set[str] | None = None,
                    templates: TemplateSet | None = None, *,
                    fmt: str = "docx") -> None:
        """DOCX keeps structural headings (title as Heading 1/2) and plain
        body text — the structure is not the fragments' here, but its
        *typography* is the template's to say (§3.1). A title page comes
        first, then a Word TOC field (page numbers only), which Word or
        LibreOffice fills in on field update; every document then starts a
        page of its own, where the print config does not say otherwise.

        Print handling, the one rule (§3.1): the template's sample
        ``.docx`` supplies everything — paper, styles, running heads —
        each config line overrides it, and page setup (1.15), else the
        A4 baseline, stands only where the template said nothing. The
        set used is the export's own (the chooser's answer, §2.1): one
        file carries one paper, so a subtree's other assignment dresses
        its prose but not this file's trim.

        ``fmt`` is the format this file is being written *for* (§1.3): a
        DOCX export says "docx", and PDF — this same compile converted by
        LibreOffice — says "pdf", so one set may hold a ``docx/`` folder
        and a ``pdf/`` folder that differ in margins, blank pages or ToC
        style while everything else about it stays shared.
        """
        from docx import Document as DocxDocument
        from docx.enum.section import WD_SECTION_START
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        templates = (templates or TemplateSet.builtin()).for_format(fmt)
        if templates.prints_nothing:
            # `no export` (§3): the set defines no content anywhere, so
            # neither does the file — no title page, no contents field,
            # no headings, no prose. An empty document, saved properly.
            self._save_docx(DocxDocument(), destination)
            return
        config = dict(templates.docx_config or {})
        scope = self._compile_scope(selected_ids, fmt)
        sample = open_docx_sample(templates.docx_sample)
        output = sample if sample is not None else DocxDocument()
        geometry, stated = self._docx_geometry(config, sample, scope)
        # The `Font` line is the config's one claim on the typography of
        # the body (§3.1), and it lands before anything is written into
        # the document so the sample's own answer is what it overrides.
        self._apply_print_font(output, config)
        output.core_properties.title = self.get_setting("title", "Lekhak")
        # Title page (Phase 1 baseline): the writing's own name — the selected
        # container, or the project's title for a whole-project export — with
        # the author only if the project actually carries one (a collection of
        # protest fliers has no author, and that is not our business). Every
        # export gets one, including a single selected document. A sample
        # may own different style names for it, and the config says which.
        #
        author = ""
        entries = self.project_metadata()
        if "Author" in entries:
            author = (entries["Author"][0] or "").strip()
        assembled = scope["export_docs"]
        toc_at = 0
        # Recto chapter openers are a print-book behaviour: page setup already
        # gave them to a book with a trim (1.15), and a template that states
        # a trim other than the baseline's own A4 is stating the same thing.
        # A4 is where an export starts, not a book's paper.
        trim_stated = stated and geometry["trim"] != DEFAULT_PAGE_SETUP["trim"]
        bookmark_id = 1
        first = True
        body_style = paragraph_style(output, config.get("body style"))
        # A sample may name its own styles for the headings (§3.1: a style
        # mapping is the one thing that cannot be inferred, so the config
        # says it); an unreadable mapping — or a sample missing the
        # built-in — degrades to no style rather than to an exception.
        chapter_style = (paragraph_style(output, config.get("chapter style"))
                         or paragraph_style(output, "Heading 1"))
        scene_style = (paragraph_style(output, config.get("scene style"))
                       or paragraph_style(output, "Heading 2"))
        containers = {parent for parent in scope["parent_map"].values() if parent}
        explicit = scope.get("explicit_breaks", set())
        # Pages that carry a section of their own (1.18): index into
        # output.sections — recorded as an index, because every later
        # add_section() appends and a Section object would follow the
        # sentinel rather than the page it was taken for.
        layout: list[tuple[int, dict | None, dict]] = []
        for position, document in enumerate(assembled):
            # Baseline (Phase 1): every exported document starts a fresh
            # page — the first needs no break, there being nothing above it.
            # The print config's `New Chapter`/`New Scene` default sits
            # under a page start somebody actually chose, and over this
            # baseline; a template that stated paper puts chapters recto
            # the same way a book's own page setup does.
            start = scope.get("breaks", {}).get(document.id, "page")
            # The design this page wears (1.17/1.18): its page start and
            # its own margins and running heads, which speak for its own
            # section while the book's paper stays the book's.
            own = (self.template_config(document.doc_template)
                   if document.doc_template else {})
            page_margins = config_margins(own)
            page_heads = {key: value for key, value in own.items()
                          if key in RUNNING_HEAD_KEYS}
            key = ("new chapter" if document.kind == "chapter"
                   else "new scene" if document.kind == "scene" else None)
            if document.id not in explicit and key is not None:
                demanded = page_break_value(config.get(key, ""))
                if demanded is None:
                    # The template this document wears may state a page
                    # start of its own (1.17): the design's say, under
                    # the print config's and over the baseline.
                    demanded = page_break_value(own.get(key, ""))
                if demanded is not None:
                    start = demanded
                elif (start == "page" and trim_stated
                          and (document.kind == "chapter"
                               or document.id in containers)):
                    start = "right"
            page_section = len(output.sections) - 1
            if not first:
                # 1.6 page starts: a marked document opens a new section with
                # the demanded parity; Word itself inserts the blank page when
                # the flow lands on the wrong side. "right" and "next-odd" are
                # the same demand (recto = odd in an LTR book) and emit the
                # same section type, likewise left/next-even — the values are
                # the writer's vocabulary, not seven behaviours.
                if start in ("right", "next-odd"):
                    output.add_section(WD_SECTION_START.ODD_PAGE)
                elif start in ("left", "next-even"):
                    output.add_section(WD_SECTION_START.EVEN_PAGE)
                elif page_margins or page_heads:
                    # A page whose design says something about its own
                    # margins or heads needs a section to say it in.
                    output.add_section(
                        WD_SECTION_START.CONTINUOUS if start == "flow"
                        else WD_SECTION_START.NEW_PAGE)
                elif start == "page":
                    output.add_page_break()
                page_section = len(output.sections) - 1
            if page_margins or page_heads:
                layout.append((page_section, page_margins, page_heads))
            first = False
            heading = output.add_paragraph(
                document.title,
                style=(chapter_style if document.kind == "chapter"
                       else scene_style))
            # 2.2: every heading carries a bookmark, so a published
            # [[link]] becomes a real Word-internal hyperlink.
            bookmark = OxmlElement("w:bookmarkStart")
            bookmark.set(qn("w:id"), str(bookmark_id))
            bookmark.set(qn("w:name"), f"d{document.id}")
            finish = OxmlElement("w:bookmarkEnd")
            finish.set(qn("w:id"), str(bookmark_id))
            p_element = heading._p
            p_element.insert(1 if p_element.pPr is not None else 0, bookmark)
            p_element.append(finish)
            bookmark_id += 1
            # Contents page: a document whose template carries ${ToC} gets
            # the Word table-of-contents field here instead of its prose.
            _tmpl = (templates.document_slot(document.doc_template, html=True)
                     if document.doc_template
                     else templates.slot("folder" if document.kind == "chapter"
                                         else "document", html=True))
            if "${ToC}" in (_tmpl or ""):
                self._add_toc_field(output)
                continue
            content = self._prepare_links(
                self.read_content(document.id), source_id=document.id,
                scope=scope, fmt=fmt, html=True)
            plain = self.expand_shortcodes(
                document, self.plain_text(content), html=False)
            for paragraph_text in plain.split("\n"):
                if paragraph_text.strip():
                    self._docx_paragraph(output, paragraph_text.strip(),
                                         style=body_style)
        # (contents page removed: no mandated content — the writer
        #  designs it with a binder document and a template.)
        # Paper and running heads: the sample's, corrected by the config,
        # or page setup (1.15), or A4 — in that order, and one source
        # rather than a cascade (§1 "The trim lives in the template").
        _apply_page_geometry(output, geometry)
        values = self.docx_config_values(selected_ids, fmt=fmt)
        self._apply_print_headings(output, config, values, geometry)
        # A page's own design, section by section (1.18): the book's
        # paper with wider margins — a title page's, a dedication's —
        # and its own running heads, `Header: none` included.
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
                                           sections=[sections[index]])
        self._save_docx(output, destination)

    @staticmethod
    def _save_docx(output, destination) -> None:
        """Write an open document to ``destination`` atomically: a temp
        file in the same directory, then os.replace — the same contract
        document prose uses, so an interrupted export never leaves half a
        file where a real one was going."""
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
    def _docx_paragraph(output, text: str, style: str | None = None) -> None:
        """One plain-text DOCX paragraph, with link tokens (2.2) split
        out into w:hyperlink runs anchored at the target heading's
        ``d<doc_id>`` bookmark. python-docx has no hyperlink API, so the
        elements are built by hand. ``style`` is the print config's
        ``Font``/``Body style`` answer when the template said one."""
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn

        paragraph = output.add_paragraph(style=style)
        position = 0
        for match in LINK_TOKEN_RE.finditer(text):
            if match.start() > position:
                paragraph.add_run(text[position:match.start()])
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
        if position < len(text):
            paragraph.add_run(text[position:])

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
            nav_items.append(f"<li><a href='{filename}'>{escape(title)}</a></li>")
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
        manifest.append(cover_item)
        nav = ("<?xml version='1.0' encoding='utf-8'?><html xmlns='http://www.w3.org/1999/xhtml' "
            "xmlns:epub='http://www.idpf.org/2007/ops'><head><title>Contents</title></head>"
            f"<body><nav epub:type='toc'><h1>Contents</h1><ol>{''.join(nav_items)}</ol></nav></body></html>")
        opf = ("<?xml version='1.0' encoding='utf-8'?><package xmlns='http://www.idpf.org/2007/opf' "
            "version='3.0' unique-identifier='book-id'><metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>"
            f"<dc:identifier id='book-id'>{uuid.uuid4().urn}</dc:identifier><dc:title>{title}</dc:title>"
            f"<dc:language>en</dc:language>{creator}{cover_meta}</metadata><manifest>"
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

    def backup(self, keep: int = 10) -> Path:
        """Copy the project into a timestamped folder outside itself, then prune.

        Checkpoints the WAL first so the copied database is self-contained,
        and keeps only the newest ``keep`` backups. The copy is the folder
        tree as it stands, so a backup opens like the project.
        """
        self.db.execute("PRAGMA wal_checkpoint(FULL)")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_directory = self.backup_directory()
        backup_directory.mkdir(parents=True, exist_ok=True)
        destination = backup_directory / stamp
        destination.mkdir(parents=True)
        shutil.copy2(self.db_path, destination / self.db_path.name)
        # The manuscript is copied as the folder tree it is, so a backup
        # reads like the project (docs/STORAGE.md).
        for entry in self.root.iterdir():
            if entry.name in (self.db_path.name, "backups"):
                continue
            if entry.is_dir():
                shutil.copytree(entry, destination / entry.name,
                                ignore=shutil.ignore_patterns(".*"))
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
