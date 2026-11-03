"""Named template sets for manuscript export.

A template set is a directory of slot files, yw2html-style. Each slot is an
optional file; missing slots fall back to the built-in defaults. Placeholders
use ``${Name}`` and ``${Metadata.Key}`` syntax.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from html import escape
from pathlib import Path

# Slots, in the order a compile consults them. All are optional in a set.
SLOTS = (
    "folder",
    "document",
)

PLACEHOLDER_RE = re.compile(r"\$\{([^{}]+)\}")

# Placeholders whose value is markup, not text. A template may put one in
# an element of its own — `<p>${Content}</p>` — as a *styling proxy*: the
# element is not a real paragraph, it is where the writer says how the
# content should be presented. Filling therefore replaces the element
# itself, never just the token, so the document's own blocks are never
# nested inside a <p> (docs/TEMPLATES.md §3.2).
BLOCK_PLACEHOLDERS = ("Content", "Children", "ToC", "Scenes")

# An element whose whole content is one block placeholder, whitespace aside.
_BLOCK_WRAPPER_RE = re.compile(
    r"<(?P<tag>[A-Za-z][A-Za-z0-9]*)\b(?P<attrs>[^>]*)>\s*"
    r"\$\{(?P<key>[^{}]+)\}\s*</(?P=tag)>",
    re.DOTALL)

# Elements that can hold block content as they stand. Any other proxy —
# a <p>, an <h1> — keeps its formatting but not its tag, because a heading
# or a paragraph cannot contain paragraphs.
_BLOCK_ELEMENTS = {
    "div", "section", "article", "nav", "main", "aside", "header", "footer",
    "blockquote", "li", "ul", "ol", "td", "th", "figure", "figcaption",
}

# The fragments that dress the *book* around its documents rather than any
# one document: consulted by a book-level compile, never offered as a
# document's dress (§3 — a header, footer or contents page is chrome).

def config_key(line: str) -> str | None:
    """The normalized name of a config line, or None when it is not one."""
    match = CONFIG_LINE_RE.match(line)
    if match is None:
        return None
    return re.sub(r"\s*\(.*\)\s*$", "", match.group(1)).strip().lower()


def parse_docx_config(text: str) -> dict[str, str]:
    """The `key: value` lines of a config block, by normalized name.

    Comments and blank lines are skipped; a key appearing twice keeps the
    later value, so a fragment's own block can correct the set's file.
    """
    config: dict[str, str] = {}
    for line in text.split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = CONFIG_LINE_RE.match(line)
        key = config_key(line)
        if key is not None:
            config[key] = match.group(2).strip()
    return config


BODY_RE = re.compile(r"<body[^>]*>(.*)</body\s*>", re.IGNORECASE | re.DOTALL)
SPAN_RE = re.compile(r"</?span[^>]*>", re.IGNORECASE)


def strip_editor_html(text: str) -> str:
    """Take the fragment out of whatever whole document carries it.

    A fragment is edited in Lekhak's own editor, which is an HTML
    editor: saving writes a full Qt document — ``<html><head>`` with a
    stylesheet, a ``<body>`` with a font, and a ``<span>`` around any
    run the writer touched. All of that is furniture; the fragment is
    what is inside the body, with no spans on it. Writers paste from a
    browser or a word processor too, and pasted junk lands here as
    happily as our own — one rule, no per-source handling.

    Plain fragments (no ``<body>``) pass through untouched apart from
    the span removal, which is a no-op without spans. Deliberately not
    an HTML parser: this only ever removes wrappers, so it cannot
    corrupt the markup it does not recognise.
    """
    match = BODY_RE.search(text)
    if match:
        text = match.group(1)
    return SPAN_RE.sub("", text)


def split_config_block(text: str) -> tuple[dict[str, str], str]:
    """Peel a leading print-config block off a fragment (§3.1).

    The block must open with a comment line — a fragment never begins
    with '#' otherwise — and runs through the comment and `key: value`
    lines that follow. Returns the parsed config and the fragment with
    the block removed: the config is print handling, so it is stripped
    from every export rather than printed as stray text.
    """
    block, rest = _peel_head(text)
    return parse_docx_config(block), rest


def _peel_head(text: str) -> tuple[str, str]:
    """The head lines of a config block, and the fragment under them."""
    lines = text.split("\n")
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines) or not lines[index].lstrip().startswith("#"):
        return "", text
    end = index
    while end < len(lines):
        line = lines[end]
        if (not line.strip() or line.lstrip().startswith("#")
                or config_key(line) is not None):
            end += 1
            continue
        break
    return "\n".join(lines[index:end]), "\n".join(lines[end:])


def split_head(text: str) -> tuple[str, str]:
    """The head of a template file, and the fragment under it (§3.1).

    The head is the config block — comments and `key: value` lines — that
    the sidebar's config box owns and the compile strips. A file whose
    first line is the Docx marker is nothing *but* head: that is what
    makes it configuration rather than a fragment, and its fragment half
    is empty by definition.
    """
    if text.split("\n", 1)[0].strip() == DOCX_MARKER:
        return text, ""
    return _peel_head(text)


def join_head(head: str, fragment: str) -> str:
    """Put a file back together from its head and its fragment.

    The inverse of split_head for what the sidebar writes: the head as
    the config box holds it, the fragment as the editor holds it. A head
    that neither opens with the Docx marker nor with a comment gets a
    bare '#' line over it — split_head only recognises a block that
    does, and a nudge must open the block rather than comment the first
    typed key out of it.
    """
    head = head.strip("\n")
    if not head.strip():
        return fragment
    first = head.split("\n", 1)[0]
    if first.strip() != DOCX_MARKER and not first.lstrip().startswith("#"):
        head = "#\n" + head
    if not fragment:
        return head            # a Docx-marker file is nothing but head
    return head + "\n" + fragment


def set_template_flag(text: str, on: bool) -> str:
    """The file with its `Template:` head line written or removed (§1.2).

    The whole file in, the whole file out: the flag belongs to the head
    and the head belongs to the file, so this is the one place that
    decides where the line lands — beside the config it sits with,
    never commented out, never dropped into the fragment.
    """
    head, fragment = split_head(text)
    kept = [line for line in head.split("\n")
            if config_key(line) != TEMPLATE_FLAG_KEY]
    if on:
        return join_head("\n".join(kept).strip("\n"), fragment)
    return join_head("\n".join([*kept, "Template: no"]).strip("\n"), fragment)


# The head key that says whether a file is a template at all (sidebar):
# `Template: no` takes the file out of the set — no longer listed, no
# longer read as a fragment, its slot falling back to the built-in. Any
# other value, or no line, keeps it a template.
TEMPLATE_FLAG_KEY = "template"


def write_config_line(text: str, key: str, line: str) -> str:
    """The text with its `key` config line written as `line` (§1.2).

    The preset rows' own writer, the panel's counterpart to
    set_template_flag: every line normalizing to `key` is dropped and
    the new one enters at the end of the block, where it is the parser's
    last word (a later duplicate wins, so a stale twin can no longer
    outvote the pick). Text that does not open a block gets one from
    join_head's own nudge, and everything else — comments, the other
    keys, the fragment under the head — is carried through untouched,
    so a pick rewrites one line and never the file's own words.
    """
    head, fragment = split_head(join_head(text, ""))
    kept = [held for held in head.split("\n") if config_key(held) != key]
    kept.append(line)
    return join_head("\n".join(kept).strip("\n"), fragment)


def drop_config_line(text: str, key: str) -> str:
    """The text with its `key` config line taken away (§1.2).

    The counterpart of write_config_line: a pick that means "nothing
    said" removes its line rather than leaving an empty one behind, so
    the file says what it means and nothing more.
    """
    wanted = config_key(key) or key.strip().lower()
    head, fragment = split_head(join_head(text, ""))
    kept = [held for held in head.split("\n") if config_key(held) != wanted]
    return join_head("\n".join(kept).strip("\n"), fragment)


def is_template_fragment(text: str) -> bool:
    """Whether a file is a template (§1.2): everything is, unless its
    head says `Template: no`."""
    head, _rest = split_head(text)
    if not head.strip():
        return True
    value = parse_docx_config(head).get(TEMPLATE_FLAG_KEY, "yes")
    return value.strip().lower() != "no"


def _read_fragment(path: Path) -> str | None:
    """A file's fragment: editor furniture stripped (§3.2), head
    stripped (§3.1), and None when the file says it is not a template
    (§1.2) — the reader's single entry point for one fragment file."""
    text = strip_editor_html(path.read_text(encoding="utf-8"))
    if not is_template_fragment(text):
        return None
    return split_head(text)[1]


# 2.2 conditional blocks: $if(Key), $if(Key = Value), $if(Key != Value),
# $else, $endif — evaluated against the same values that fill ${...}.
# The $if(...) piece stops at the first ")", so a condition value
# containing ")" cannot be matched; conditionals live only in template
# slots, never in document prose.
CONDITIONAL_PIECE_RE = re.compile(
    r"(\$if\([^)]*\)|\$else(?![A-Za-z])|\$endif(?![A-Za-z]))")
CONDITIONAL_EXPR_RE = re.compile(r"^(.+?)\s*(!=|=)\s*(.*)$")

BUILTIN_HTML: dict[str, str] = {
    "folder": ('<section class="chapter">\n<h1>${Title}</h1>\n'
                '<div class="content">${Content}</div>\n'
                '<div class="scenes">${Scenes}</div>\n</section>'),
    "document": ('<section class="document">\n<h2>${Title}</h2>\n'
              '<div class="content">${Content}</div>\n</section>'),
}

BUILTIN_TEXT: dict[str, str] = {
    "folder": "${Title}\n\n${Content}\n\n${Scenes}",
    "document": "${Title}\n\n${Content}",
}

# The stylesheet every set starts with (docs/TEMPLATES.md 3.2): HTML and
# EPUB put it in a <style>, so a writer who never opens the CSS box still
# exports a page whose headings can be found and whose prose reads like a
# book. DOCX and PDF never see it — their type comes from the sample .docx
# and the design panel's own lines.
DEFAULT_CSS = """\
/* A book on the screen: a heading you can find, prose you can follow.
   Delete a rule, or all of them, and the export goes back to plain. */
body {
  font-family: Georgia, "Iowan Old Style", "Times New Roman", serif;
  font-size: 100%;
  line-height: 1.55;
  color: #1a1a1a;
  margin: 0 auto;
  max-width: 34em;
  padding: 0 1.25em 3em;
}
h1, h2, h3, h4 {
  font-weight: 600;
  line-height: 1.2;
  margin: 2em 0 0.6em;
}
h1 { font-size: 1.9em; margin-top: 1.2em; }
h2 { font-size: 1.4em; }
h3 { font-size: 1.12em; font-variant: small-caps; letter-spacing: 0.04em; }
h4 { font-size: 1em; font-style: italic; }
p { margin: 0; text-indent: 1.4em; }
h1 + p, h2 + p, h3 + p, h4 + p, p:first-child { text-indent: 0; }
blockquote { margin: 1.2em 1.5em; font-style: italic; }
blockquote p { text-indent: 0; }
hr {
  width: 30%;
  margin: 1.6em auto;
  border: 0;
  border-top: 1px solid #b9b9b9;
}
section { margin: 0 0 2em; }
a { color: #2a4d69; }
@media print {
  body { max-width: none; padding: 0; }
}
"""

EXTENSIONS = {True: ".html", False: ".txt"}

DOCX_MARKER = "Docx"


def is_print_config(text: str) -> bool:
    """Whether a file is print configuration (§3.1): its first line is the
    Docx marker, so it holds no fragment at all — the one reading that
    keeps a config file out of the fragment listings."""
    return text.split("\n", 1)[0].strip() == DOCX_MARKER


def is_document_face(text: str) -> bool:
    """Whether a set file can dress a document (§1.2): a fragment whose
    head is not print configuration and does not say `Template: no`."""
    return is_template_fragment(text) and not is_print_config(text)

CONFIG_LINE_RE = re.compile(r"^\s*([A-Za-z][^:]*?)\s*:\s*(.*?)\s*$")

# The export formats a set may refine file for file (§1.3).
FORMATS = ("html", "epub", "docx", "pdf", "text")

# Document-level templates every set has (2.1): "no-title" is the
# sidebar's "print it without the heading" choice. Sets add their own as
# files under documents/<name>.html|.txt — a news-item preamble, and on.
BUILTIN_DOCUMENT_TEMPLATES: dict[str, dict[bool, str]] = {
    "no-title": {True: "${Content}", False: "${Content}"},
    "contents": {True: "<h1>${Title}</h1>\n<ol>${ToC}</ol>",
                 False: "${Title}\n\n${ToC}"},
}

DOCUMENT_TEMPLATE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]*")


def document_template_stem(text: str) -> str:
    """The template name a file's face stands for (docs/TEMPLATES.md
    §1.2): whitespace trimmed, an optional ``documents/`` shelf prefix
    and one trailing ``.html``/``.txt`` dropped — one name covers both
    variants of a fragment (§3.2) — or ``''`` when what is left could
    not be a name anything asks for.

    The Document template box saves through this, so carrying the
    filename and naming the stem are one act: the writer types the
    file, the compile gets the name, and both read back the same.
    """
    name = text.strip()
    if name[:10].casefold() == "documents/":
        name = name[10:]
    for extension in (EXTENSIONS[True], EXTENSIONS[False]):
        if name.casefold().endswith(extension):
            name = name[:-len(extension)]
            break
    return name if DOCUMENT_TEMPLATE_NAME_RE.fullmatch(name) else ""

# Every value key the compile actually supplies (2.4 slot editor): mirrors
# Project._template_values plus the compile extras and the TOC's Entries.
# Metadata.<key> is open-ended and validated by prefix only. fill() is
# case-sensitive, so validation is too — ${title} renders empty and should
# be flagged, not silently accepted.
TEMPLATE_KEYS = (
    "Title", "Kind", "Synopsis", "WordCount", "Content",
    "ChapterNumber", "SceneNumber", "WordsTotal", "ToC", "Scenes", "Children",
)

# What the slot editor's completer offers, as the exact strings an
# activated item inserts (closing brace included; Metadata. stays open
# for the writer's own key).
TEMPLATE_COMPLETIONS = tuple(f"${{{key}}}" for key in TEMPLATE_KEYS) + (
    "${Metadata.", "$if(", "$else", "$endif",
)


def validate_slot(text: str, *, html: bool = True,
                  known_keys: tuple[str, ...] = ()) -> list[str]:
    """Tier-1 checks for a hand-edited slot (2.4): unknown ${...} keys,
    unbalanced $if/$endif, an orphan $else, stray conditional markers.

    Advisory only — TemplateSet.fill already degrades a bad slot to
    something printable, so saving an issue stays allowed; this is the
    editor's tripwire, not a gate. The ``html`` argument is accepted for
    signature symmetry with the dialog's tier-2 Qt round-trip sniff,
    which lives in the GUI layer so this module stays Qt-free.
    ``known_keys`` lets a caller (the template sheet) widen the
    vocabulary without editing it: a config value may name the project's
    own metadata keys or ``Date``/``Book``, which resolve at export
    through ``docx_config_values`` rather than through a slot.
    """
    issues: list[str] = []
    extra = set(known_keys)
    for key in PLACEHOLDER_RE.findall(text):
        name = key.strip()
        if name.startswith("Metadata."):
            continue
        if name not in TEMPLATE_KEYS and name not in extra:
            issues.append(f"unknown placeholder ${{{name}}}")
    depth = 0
    for piece in CONDITIONAL_PIECE_RE.split(text):
        if piece.startswith("$if("):
            depth += 1
        elif piece == "$endif":
            depth -= 1
            if depth < 0:
                issues.append("$endif without a matching $if")
                depth = 0
        elif piece == "$else" and depth == 0:
            issues.append("$else without a matching $if")
    if depth > 0:
        issues.append(f"{depth} unclosed $if")
    leftover = CONDITIONAL_PIECE_RE.sub("", text)
    stray = sorted({marker for marker in ("$if", "$else", "$endif")
                    if marker in leftover})
    if stray:
        issues.append(
            "stray or malformed marker(s): " + ", ".join(stray)
            + " (missing parentheses?)")
    return issues


def _own_fragments_empty(slots: dict) -> bool:
    """True when a set carries fragments and every one of them is empty.

    A set with no fragments of its own says nothing about silence — it is
    the baseline, and the built-ins answer. A set that *does* carry
    fragments and leaves them all blank is the `no export` shape: one
    empty body is enough, and the writer who deletes it gets every
    built-in fragment back rather than a vocabulary of empty files.
    """
    fragments = [text for text in slots.values() if isinstance(text, str)]
    return bool(fragments) and all(not text.strip() for text in fragments)


def _read_folder(directory: Path) -> tuple[dict, dict, dict, Path | None]:
    """The fragments, print config and sample ``.docx`` of one folder.

    Read the same way for a set's root and for a format folder laid over
    it (§1.3) — one reading of the files, not two: each ``.html``/``.txt``
    file is a fragment, its leading config block stripped and merged, a
    Docx-marker file read whole and winning over those blocks, and the
    first sample-shaped ``.docx`` the sample. A file whose head says
    `Template: no` is not a fragment at all and is skipped whole (§1.2).
    Subfolders — the ``documents/`` shelf and any other folder — belong
    to their own reading: document_slot reads the shelf, for_format reads
    a folder.
    """
    html_slots: dict[str, str] = {}
    text_slots: dict[str, str] = {}
    config: dict[str, str] = {}
    marker_config: dict[str, str] = {}
    sample: Path | None = None
    for path in sorted(Path(directory).iterdir()):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix == ".docx":
            if sample is None:
                sample = path
            continue
        if suffix not in tuple(EXTENSIONS.values()):
            continue
        text = strip_editor_html(path.read_text(encoding="utf-8"))
        if not is_template_fragment(text):
            continue
        if text.split("\n", 1)[0].strip() == DOCX_MARKER:
            # Its own config file: the first line is the marker that
            # says what it is, and it corrects whatever a fragment
            # said — merged after, whatever the filenames sort to.
            marker_config.update(parse_docx_config(text))
            continue
        block, text = split_config_block(text)
        config.update(block)
        mode = suffix == EXTENSIONS[True]
        slots = html_slots if mode else text_slots
        slots[path.stem] = text
    config.update(marker_config)
    return html_slots, text_slots, config, sample


@dataclass(frozen=True)
class TemplateSet:
    """A resolved set of slot templates: user files over built-in defaults."""

    name: str
    directory: Path | None
    html_slots: dict[str, str]
    text_slots: dict[str, str]
    # 3.1 print handling, resolved once at load: the merged `key: value`
    # config (fragment header blocks first, then the Docx-marker file,
    # which corrects them) and the sample ``.docx`` whose formatting an
    # export inherits. Both default to "nothing said" — the built-in set
    # has neither, and DOCX then falls back to page setup (1.15), else
    # A4. Only ``.docx`` counts as a sample: a ``.odt`` would need
    # LibreOffice to be read, and this module never runs a process.
    docx_config: dict = field(default_factory=dict)
    docx_sample: Path | None = None
    # 1.3: the set this one becomes for each export format that has a
    # folder of its own, resolved once at load. An overlay's directory is
    # that folder, and `under` is the set it lies over — the place a file
    # the format folder does not carry still lives, so a format answers
    # first and the root next rather than either replacing the other.
    formats: dict = field(default_factory=dict)
    under: TemplateSet | None = None
    # 2.1b: what the book is made of, in order — ``content`` for the
    # manuscript, any other name for binder content the template pulls in
    # (front matter, a preview chapter, a dedication). Stated on one config
    # line and read here once; the assembly step is the only reader.
    # §3: a set that carries fragments of its own and leaves every one of
    # them empty produces nothing — no content, no placeholders, no output.
    # The shipped `no export` is one such file, not a vocabulary of emptied
    # slots, so a writer who changes their mind deletes it and gets every
    # built-in fragment back.
    own_empty: bool = False

    @classmethod
    def builtin(cls, name: str = "default") -> TemplateSet:
        """The named set that ships with Lekhak (§1.1), file for file.

        No folder, so the Templates view offers it non-editable with
        Duplicate; the slots and config come from `SHIPPED_FILES`, the
        very map a duplicate is written from — a writer who duplicates
        "short story" and opens the folder finds exactly what the
        shipped entry already was, not a pleasant surprise.
        """
        if name not in SHIPPED_TEMPLATE_SETS:
            raise ValueError(f"No shipped template set named {name!r}")
        html_slots, text_slots = _shipped_slots(name)
        config = _shipped_config(name)
        return cls(name, None, html_slots, text_slots, config, None,
                   own_empty=_own_fragments_empty(_shipped_own_slots(name)))

    @classmethod
    def load(cls, name: str, directory: Path) -> TemplateSet:
        """Load a set from a directory; missing slots keep built-in defaults.

        Every ``.html``/``.txt`` file in the folder is a fragment (§3) —
        "one rule tells the files apart". The built-in names are a
        vocabulary the compile consults by content shape, not a
        requirement: a set owning only ``short_story.html`` is a complete
        template the moment a document asks for it. Anything else in the
        folder (a README, a sample ``.docx``) is support material and is
        ignored here, as is the ``documents/`` shelf — that one is read
        per document by document_slot().

        Print handling (§3.1) is resolved here too: a leading config
        block is stripped from each fragment and merged, the Docx-marker
        file is read whole (its filename carries no meaning) and wins
        over fragment blocks, and the first sample-shaped file becomes
        docx_sample. The stripped config never reaches an export.

        A subfolder named for an export format (§1.3) is read as a second
        copy of this layout and laid over the root's, one folder per
        format, so ``pdf/print-config.txt`` says what a printed PDF takes
        and the root's says what every format without a folder takes. The
        overlay is pre-merged here so an export has one decision to make —
        which format it is — and never a cascade of lookups.
        """
        directory = Path(directory)
        html, text, config, sample = _read_folder(directory)
        base = cls(name, directory, {**BUILTIN_HTML, **html},
                   {**BUILTIN_TEXT, **text}, config, sample,
                   own_empty=_own_fragments_empty({**html, **text}))
        overlays: dict[str, TemplateSet] = {}
        for fmt in FORMATS:
            folder = directory / fmt
            if not folder.is_dir():
                continue
            fmt_html, fmt_text, fmt_config, fmt_sample = _read_folder(folder)
            overlays[fmt] = cls(
                name, folder,
                {**base.html_slots, **fmt_html},
                {**base.text_slots, **fmt_text},
                {**base.docx_config, **fmt_config},
                base.docx_sample if fmt_sample is None else fmt_sample,
                under=base,
                own_empty=_own_fragments_empty(
                    {**html, **text, **fmt_html, **fmt_text}),
            )
        if not overlays:
            return base
        return replace(base, formats=overlays)

    def for_format(self, fmt: str | None) -> TemplateSet:
        """The set as one export format sees it (§1.3).

        A folder named for the format shadows the set's own files for that
        format alone — ``epub/chapter.html`` dresses an EPUB, the root's
        ``chapter.html`` dresses everything else — so one set carries one
        continuity of formatting across formats while each format keeps
        what suits it: a page-numbered ToC and mirrored margins for DOCX
        and PDF, neither for an EPUB.

        No folder for this format, and the answer is the set itself,
        which is why a set with a single template still exports
        everything: the folder is an override, never a requirement.
        """
        if not fmt:
            return self
        return self.formats.get(fmt, self)

    def css(self) -> str:
        """The set's stylesheet: ``style.css`` in this set's own folder.

        A format folder carries its own when it has one (§1.3 — the
        overlay's directory is that folder), and the root's answers next,
        so one file dresses every format that does not override it.
        HTML and EPUB exports embed it; DOCX/PDF take typography from
        page setup and the sample ``.docx``, where CSS has no say. A
        shipped set has no folder and answers empty.
        """
        if self.directory is not None:
            path = self.directory / "style.css"
            if path.is_file():
                return path.read_text(encoding="utf-8")
        if self.under is not None:
            return self.under.css()
        # A shipped set has no folder, so its stylesheet ships in the map
        # the duplicate is written from — the copy answers the same bytes.
        return SHIPPED_FILES.get(self.name, {}).get("style.css", "")

    @property
    def editable(self) -> bool:
        return self.directory is not None

    @property
    def prints_nothing(self) -> bool:
        """True when every fragment this set carries is empty — nothing
        in, nothing out (§3: a template may legitimately produce something
        far sparser than the baseline, and silence is where that ends).
        This is what the shipped `no export` set is: DOCX and text write
        empty files, HTML and EPUB keep their wrappers with no content
        inside them."""
        if self.own_empty:
            return True
        return all(not text.strip()
                   for text in (*self.html_slots.values(),
                                *self.text_slots.values()))

    def slot(self, name: str, *, html: bool = True) -> str:
        # A set that produces nothing answers every slot with nothing, so
        # no exporter needs to know why (§3).
        if self.prints_nothing:
            return ""
        slots = self.html_slots if html else self.text_slots
        return slots.get(name, "")

    def render(self, name: str, values: dict[str, str], *, html: bool = True) -> str:
        """Fill one named slot of this set."""
        return self.fill(self.slot(name, html=html), values, html=html)

    @staticmethod
    def fill(template: str, values: dict[str, str], *,
             html: bool = True) -> str:
        """Fill an arbitrary template string. Scalar values are escaped in
        html mode; ``content``, ``children`` and ``entries`` are raw, as
        the caller has already decided. $if/$else/$endif blocks (2.2) are
        evaluated first and the dead branches removed.

        A block placeholder standing alone in an element is filled by
        replacing that element: the element is the writer's styling proxy,
        not a real paragraph, and letting the content's own blocks nest
        inside it would produce ``<p><p>``. Its attributes survive — the
        same tag when the tag can hold blocks, a ``<div>`` carrying the
        attributes when it cannot (§3.2).
        """
        template = TemplateSet.conditionals(template, values)
        template = TemplateSet.replace_block_proxies(template, values)
        raw_keys = {"content", "children", "entries"}

        def substitute(match: re.Match) -> str:
            key = match.group(1).strip()
            value = values.get(key, "")
            if html and key.lower() not in raw_keys:
                return escape(value)
            return value

        return PLACEHOLDER_RE.sub(substitute, template)

    @staticmethod
    def replace_block_proxies(template: str, values: dict[str, str]) -> str:
        """Replace an element that is nothing but a block placeholder.

        ``<p>${Content}</p>`` becomes the content's own blocks; a proxy
        that says something about presentation — ``<p class="first">``,
        ``<div style="...">`` — keeps that formatting. Only the three
        markup-valued placeholders qualify: a scalar like ``${Title}``
        stays inline where the template put it.
        """
        def replace(match: re.Match) -> str:
            key = match.group("key").strip()
            if key not in BLOCK_PLACEHOLDERS:
                return match.group(0)
            value = values.get(key, "")
            tag = match.group("tag").lower()
            attributes = match.group("attrs").strip()
            if tag in _BLOCK_ELEMENTS:
                # A container that can hold blocks is doing real work —
                # a <div> for styling, an <ol> for the entries' <li>s —
                # so it stays, with or without attributes.
                opening = f"<{tag} {attributes}>" if attributes else f"<{tag}>"
                return f"{opening}{value}</{tag}>"
            if attributes:
                # A <p>/<h1> cannot contain paragraphs: keep what the
                # writer said about presentation, drop the impossible tag.
                return f"<div {attributes}>{value}</div>"
            return value          # a bare text-only proxy: content stands alone

        return _BLOCK_WRAPPER_RE.sub(replace, template)

    @staticmethod
    def conditionals(template: str, values: dict[str, str]) -> str:
        """Evaluate $if/$else/$endif blocks (2.2); the result is the
        template with dead branches and every marker consumed.

        A condition is either a bare key — true when its value is
        non-empty — or `Key = Value` / `Key != Value`, compared
        case-insensitively against the raw (unescaped) value. Nesting
        works. An unmatched $if keeps its branch state to the end of the
        template; a stray $else/$endif is dropped. A hand-edited slot
        therefore degrades to something printable, never to leaked
        markers.
        """
        active = [True]
        else_taken: list[bool] = []
        result: list[str] = []
        for piece in CONDITIONAL_PIECE_RE.split(template):
            if piece.startswith("$if("):
                condition = TemplateSet._condition_holds(piece[4:-1], values)
                active.append(condition)
                else_taken.append(condition)
            elif piece == "$else":
                if else_taken:
                    active[-1] = not else_taken[-1]
                    else_taken[-1] = True
            elif piece == "$endif":
                if else_taken:
                    else_taken.pop()
                    active.pop()
            elif all(active):
                result.append(piece)
        return "".join(result)

    @staticmethod
    def _condition_holds(expr: str, values: dict[str, str]) -> bool:
        match = CONDITIONAL_EXPR_RE.match(expr.strip())
        if not match:
            return bool(values.get(expr.strip(), "").strip())
        key = match.group(1).strip()
        expected = match.group(3).strip()
        actual = values.get(key, "").strip()
        equal = actual.casefold() == expected.casefold()
        return equal if match.group(2) == "=" else not equal

    def document_slot(self, name: str, *, html: bool = True) -> str | None:
        """A document-level template (§3): the file
        ``documents/<name>.html|.txt`` of this set, then a root fragment
        of that name — the writer's own vocabulary, so a set owning only
        ``short_story.html`` dresses short stories the moment the writer
        names it — else the built-in ``no-title``; None when the set has
        no
        such template, so the caller falls back to the ordinary
        chapter/scene slot the document's shape would take.

        Only real files count: a name matching a built-in slot the set
        does not carry is not this document's answer, it is the shape's.

        Format-scoped (§1.3): on a set laid over another for one format,
        that format's own shelf answers here first and the root's falls
        through — so a dress can differ per format without the two ever
        being different sets.
        """
        if self.prints_nothing:
            return ""
        name = document_template_stem(name)
        if not name:
            return None
        if self.directory:
            shelf = self.directory / "documents" / (name + EXTENSIONS[html])
            if shelf.is_file():
                fragment = _read_fragment(shelf)
                if fragment is not None:
                    return fragment
            root = self.directory / (name + EXTENSIONS[html])
            if root.is_file():
                fragment = _read_fragment(root)
                if fragment is not None:
                    return fragment
        else:
            # A set that ships in code: its own map answers before the
            # built-in does — `nothing` owns an empty `documents/no-title`,
            # and must be silent for it like for everything else.
            files = SHIPPED_FILES.get(self.name, {})
            for relative in (f"documents/{name}", name):
                if relative + EXTENSIONS[html] in files:
                    return files[relative + EXTENSIONS[html]]
        if self.under is not None:
            # 1.3: a format folder answers first and the set it lies over
            # next, so `epub/documents/news.html` dresses an EPUB and the
            # root's `documents/news.html` still dresses every other
            # format — the shelf's own files are format-scoped too.
            return self.under.document_slot(name, html=html)
        builtin = BUILTIN_DOCUMENT_TEMPLATES.get(name)
        return builtin[html] if builtin else None

    def document_template_faces(self) -> dict[str, str]:
        """Every document-level template this set offers, as
        ``name -> filename`` — the face the sidebar carries
        (docs/TEMPLATES.md §1.2): each name shown as the file it stands
        for, ``.html`` where a set carries that variant (both variants
        of one name share it, §3.2) and ``.txt`` where plain text is all
        there is. Built-ins always answer — ``no-title`` has no file of
        its own, so it stands as no-title.html — and chrome is left out: a
        header, footer or contents page dresses the book, never a
        document, and so are the set's own slot files at its root — the
        shape's dress is the "Default" row, not a name to pick. For a set
        that ships in code the shipped map is its files (§1.1), the same
        list a duplicate is written out from.
        """
        html: set[str] = set(BUILTIN_DOCUMENT_TEMPLATES)
        text: set[str] = set()
        entries: list[tuple[Path, str, bool]] = []
        if self.directory:
            paths = [(path, True) for path in sorted(self.directory.iterdir())
                     if path.is_file()]
            shelf = self.directory / "documents"
            if shelf.is_dir():
                paths += [(path, False) for path in sorted(shelf.iterdir())
                          if path.is_file()]
            for path, at_root in paths:
                try:
                    entries.append(
                        (path, path.read_text(encoding="utf-8"), at_root))
                except (OSError, UnicodeDecodeError):
                    continue        # unreadable: not a face this box can offer
        else:
            for relative, content in SHIPPED_FILES.get(self.name, {}).items():
                path = Path(relative)
                entries.append((path, content, path.parent == Path(".")))
        for path, content, at_root in entries:
            suffix = path.suffix.lower()
            if suffix not in EXTENSIONS.values():
                continue            # a README or a stylesheet is not a fragment
            if at_root and path.stem in SLOTS:
                # `folder.html` and `document.html` at the set's root are
                # the shape's own dress, offered as "Default (set's own
                # slot)" — listing them here too would be the same thing
                # twice, under two names that dress differently.
                continue
            if not is_document_face(content):
                continue            # print configuration, or `Template: no`
            (html if suffix == EXTENSIONS[True] else text).add(path.stem)
        faces = {stem: stem + EXTENSIONS[True] for stem in html}
        faces.update({stem: stem + EXTENSIONS[False]
                      for stem in text if stem not in html})
        return faces

    def document_template_names(self) -> list[str]:
        """Names the sidebar can offer: every key of the list above, so
        asking for a name and asking for its file are one question
        (docs/TEMPLATES.md §1.2) — a writer-named fragment is pickable
        without reading the filesystem, under the name it is found by."""
        return sorted(self.document_template_faces())


def user_template_root() -> Path:
    from os import environ

    base = environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(base) / "lekhak" / "templates"


def project_template_root(project_root: Path) -> Path:
    return Path(project_root) / "templates"


def discover_template_sets(project_root: Path) -> list[TemplateSet]:
    """Every set Lekhak offers: the ones that ship with it, plus each set
    directory under the user and project template roots, in that order.

    The shipped three keep their slots for good (§1.1): a directory called
    ``default`` — or ``short story``, or ``no export``, any case — is
    listed **after** them as a set of its own, under its folder name, so
    nothing on disk is ever hidden and the project's own copy has a home
    in the listing. What it never does is take the shipped set's place:
    the default slot stays Lekhak's, the answer when nobody picked
    anything. Naming a set is separate — see ``named_template_sets``.

    A folder not named after a shipped set is simply added; two roots
    carrying the same folder name resolve the way they always have, the
    project's own shadowing the user's.
    """
    sets = [TemplateSet.builtin(name) for name in SHIPPED_TEMPLATE_SETS]
    # Only folders, keyed by their name: a shipped set is never in here,
    # so no folder can be written over its slot.
    index: dict[str, int] = {}
    for root in (user_template_root(), project_template_root(project_root)):
        if not root.is_dir():
            continue
        for candidate in sorted(root.iterdir()):
            if not candidate.is_dir():
                continue
            key = candidate.name.casefold()
            loaded = TemplateSet.load(candidate.name, candidate)
            if key in index:
                sets[index[key]] = loaded     # the project's own shadows the user's
            else:
                index[key] = len(sets)
                sets.append(loaded)
    return sets


def named_template_sets(project_root: Path) -> dict[str, TemplateSet]:
    """Discovery keyed by the name an assignment stores (§2).

    ``book_settings["template"]`` and the export dialog's warnings both
    speak in names, so the shipped sets keep theirs: a project folder that
    has one of those names (``default``, ``short story``, ``no export``)
    is listed as itself but never answers to the name, because the name
    already means the set Lekhak ships — that is what makes the default
    slot always the shipped one. Any other name behaves as discovery
    does: the project's folder shadows the user's of the same name.
    """
    named: dict[str, TemplateSet] = {}
    for candidate in discover_template_sets(project_root):
        current = named.get(candidate.name)
        if current is not None and current.directory is None:
            continue        # a shipped set keeps its name
        named[candidate.name] = candidate
    return named


def _shipped_paths(name: str) -> set[str]:
    """The files a shipped set consists of, written out by duplication:
    every built-in slot in both modes, the document shelf, and whatever
    the shipped map overrides or adds (§1.1 — the copy *is* the shipped
    entry, so the two cannot be a different list).

    A set whose own fragments are all empty is the exception: it is copied
    as itself — its one empty file and its notes — because seeding the
    built-ins would turn silence into a vocabulary to empty again (§3).
    """
    names = set(SHIPPED_FILES.get(name, {}))
    if _own_fragments_empty(_shipped_own_slots(name)):
        return names
    for stem in set(BUILTIN_HTML) | set(BUILTIN_TEXT):
        names.add(stem + EXTENSIONS[True])
        names.add(stem + EXTENSIONS[False])
    for document in BUILTIN_DOCUMENT_TEMPLATES:
        names.add(f"documents/{document}{EXTENSIONS[True]}")
        names.add(f"documents/{document}{EXTENSIONS[False]}")
    return names


def file_paths(templates: TemplateSet) -> list[str]:
    """Every file of the set, as a path relative to its root (posix).

    A set with a folder is read from disk; a set that ships in code has
    no folder, so it answers with its own definition — the files a
    Duplicate of it writes. Either way this is the folder's own truth:
    a stray ``notes.txt`` or a nested ``examples/`` appears exactly
    where it is, never grouped or hidden.

    A file whose head says `Template: no` is not listed (§1.2): the
    checkbox in the sidebar takes it out of the set without touching a
    byte of its content, so it stays in the folder — support material
    beside the templates, not gone.
    """
    if templates.directory is not None:
        root = Path(templates.directory)
        if not root.is_dir():
            return []
        listed = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() in tuple(EXTENSIONS.values()):
                try:
                    if not is_template_fragment(
                            path.read_text(encoding="utf-8")):
                        continue
                except UnicodeDecodeError:
                    continue          # not text; cannot claim to be a template
            listed.append(path.relative_to(root).as_posix())
        return sorted(listed)
    return sorted(_shipped_paths(templates.name))


def _set_directory(name: str, project_root: Path) -> Path:
    """Validate a set's name and make its folder (§1.1) — always a sibling
    of the other sets in ``<project>/templates/``, never inside one."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]*", name):
        raise ValueError(f"Invalid template set name: {name!r}")
    directory = project_template_root(project_root) / name
    if directory.exists():
        raise FileExistsError(f"Template set already exists: {directory}")
    directory.mkdir(parents=True)
    return directory


def create_template_set(name: str, project_root: Path) -> TemplateSet:
    """Copy the built-in defaults into a new editable set in the project."""
    directory = _set_directory(name, project_root)
    for slot in SLOTS:
        (directory / (slot + ".html")).write_text(BUILTIN_HTML[slot], encoding="utf-8")
        (directory / (slot + ".txt")).write_text(BUILTIN_TEXT[slot], encoding="utf-8")
    # The document shelf comes too: a new set stands for a complete one,
    # and `no-title` is the sidebar's "without the heading" choice — same
    # bytes the unowned fallback answers with, so exports do not move.
    shelf = directory / "documents"
    shelf.mkdir()
    for document, variants in BUILTIN_DOCUMENT_TEMPLATES.items():
        (shelf / (document + EXTENSIONS[True])).write_text(
            variants[True], encoding="utf-8")
        (shelf / (document + EXTENSIONS[False])).write_text(
            variants[False], encoding="utf-8")
    # And the print-config file, so a new set teaches its own keys (§3.1)
    # the way the shipped default does. Every line in it is a comment,
    # so it changes no export: it is the manual, in the place the sheet
    # writes to.
    (directory / "print-config.txt").write_text(
        SHIPPED_FILES[SHIPPED_TEMPLATE_SETS[0]]["print-config.txt"],
        encoding="utf-8")
    # And the stylesheet, so a new set has an emphasis anchor of its own
    # to change rather than an empty box to fill from nothing.
    (directory / "style.css").write_text(DEFAULT_CSS, encoding="utf-8")
    return TemplateSet.load(name, directory)


def copy_template_set(source: TemplateSet, name: str, project_root: Path) -> TemplateSet:
    """Fork an existing set into a new editable one.

    A set with a folder is copied file for file. A shipped set (§1.1) has
    no folder to copy, so its own files — the fragments of that variant,
    its config, its notes — are written out instead: what lands on disk is
    what the Templates view showed, ready to edit.
    """
    if source.directory is None and source.name in SHIPPED_FILES:
        if source.own_empty:
            # A set that prints nothing is one empty file and its notes.
            # Seeding the built-ins first would make the duplicate a full
            # vocabulary of fragments that all have to be emptied again.
            directory = _set_directory(name, project_root)
        else:
            directory = create_template_set(name, project_root).directory
        _write_shipped_files(source.name, directory)
        return TemplateSet.load(name, directory)
    created = create_template_set(name, project_root)
    if source.directory:
        # Every file, subfolders too: the `documents/` shelf and any
        # examples/ folder are part of the set (§1.1 — duplicating is how
        # a writer gets these exact files).
        for path in source.directory.rglob("*"):
            if not path.is_file():
                continue
            target = created.directory / path.relative_to(source.directory)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        return TemplateSet.load(name, created.directory)
    return created


def _write_shipped_files(name: str, directory: Path) -> None:
    """Write a shipped set's own files into ``directory``: the fragments of
    that variant, its config, its notes, and the example sample when it has
    one. What lands on disk is what the shipped entry already was."""
    for relative, text in _shipped_files(name).items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    if name in SHIPPED_EXAMPLE_SAMPLE:
        try:
            write_example_sample(directory / "examples" / "formatting.docx")
        except ImportError:          # no python-docx: the set still ships
            pass


def _shipped_text(templates: TemplateSet, relative: str) -> str:
    """What one file of a set that ships in code contains — the same
    bytes a duplicate of it writes (§1.1), looked up the way the listing
    shows them: the shipped map first, then the built-in answer a
    fragment or a shelf file falls back to."""
    files = SHIPPED_FILES.get(templates.name, {})
    if relative in files:
        return files[relative]
    path = Path(relative)
    if path.suffix not in tuple(EXTENSIONS.values()):
        raise ValueError(
            f"{relative} is not a template — duplicate the set to get it")
    html = path.suffix == EXTENSIONS[True]
    if path.parent == Path("documents"):
        text = templates.document_slot(path.stem, html=html)
    else:
        text = templates.slot(path.stem, html=html)
    if text is None:
        raise ValueError(f"No such file in this set: {relative}")
    return text


def copy_template_file(source: TemplateSet, relative: str, name: str,
                       project_root: Path) -> TemplateSet:
    """Copy one file of a set out into a new set of its own (§1.1).

    Just that one file, and the new folder holds nothing else: everything
    the file does not say falls back to Lekhak's built-ins — the same
    rule that lets a set carry a single template and still export every
    format. It lands at the path it had, so a file taken from ``epub/``
    refines EPUB exports of the new set rather than quietly becoming
    every format's answer.

    The new set is a sibling of the one it came from — in
    ``<project>/templates/``, never inside it — and a failure leaves no
    half-made folder behind.
    """
    parts = Path(relative.replace("\\", "/")).parts
    if not parts or parts[0].startswith("/") or ".." in parts:
        raise ValueError(f"Not a file of the set: {relative!r}")
    relative = "/".join(parts)
    directory = _set_directory(name, project_root)
    try:
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.directory is not None:
            original = source.directory / relative
            if not original.is_file():
                raise FileNotFoundError(f"No such file in this set: {relative}")
            shutil.copy2(original, target)
        else:
            target.write_text(_shipped_text(source, relative), encoding="utf-8")
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return TemplateSet.load(name, directory)


# The sidebar's own words (docs/TEMPLATES.md §1.2), one source for the
# panel, the README this module writes into a set, and the documentation:
# what the config box is for, and the reminder that says how a file is
# asked for at all. Written once — quoted verbatim wherever it appears,
# so a writer who has seen one has seen them all.
TEMPLATE_CONFIG_NOTE = """If your books are formatted similarly across mediums, you don't need
separate print templates. You can simply add any minor formatting here
(at a minimum add your page size). Whatever is in this box is saved
back to the head of the file, where the export reads it. If you edit it
and need to refer to the original tags, you can look at the default
file."""

TEMPLATE_NAMING_NOTE = """Remember to name this file according to your folder or document TYPE in
the manuscript metadata? The export's own names already follow it:
chapter.html dresses chapters, scene.html scenes, documents/document.html
documents that name no face of their own, documents/no-title.html a
document printed without its heading. Any other file dresses
whatever asks for it by name — pick or type its filename in the
Document template box; a name no file answers saves just the same, and
the export falls back to the set's own slot. The names an export looks
up are project_header, project_footer, toc_page, chapter, chapter_end,
scene, first_scene, scene_end and scene_divider — any other name is
yours to choose, and a name nothing asks for costs nothing. Metadata is
separate: ${Title}, ${WordCount} and ${Metadata.AnyKey} are filled in
from the document at export, whatever this file is called."""


SET_README = """This folder is one template set: the instructions for turning your project
into an exported book.

Each .html file here is a fragment — the instructions for one kind of thing.
chapter.html says what a chapter looks like, scene.html what a scene looks
like, project_header.html what the title matter looks like. Edit them to
change your export; delete a file to fall back to the built-in default.

The names an export looks up by itself are: project_header, project_footer,
toc_page, chapter, chapter_end, scene, first_scene, scene_end, scene_divider.
A chapter made of scenes uses the chapter and scene fragments; a whole book
takes the header, footer and contents.

""" + TEMPLATE_NAMING_NOTE + """

Placeholders you can use inside a fragment — ${Title}, ${Content}, ${Author},
${Date}, ${Metadata.AnyKey} — are filled in when you export.

The .txt files are the same fragments for plain-text export. Edit either; they
do not have to agree.

Nothing here is precious: make a copy (Duplicate in the Templates view, or
copy the folder) before changing anything you might want back.
"""


# ---------------------------------------------------------------------------
# The sets that ship with Lekhak (docs/TEMPLATES.md §1.1): three ways to see
# what the system can do, listed above everything else, non-editable because
# there is no folder to edit until they are duplicated into the project.
# One file map per set: what a duplicate is written out as is exactly what
# the shipped entry already is, so a writer who duplicates and inspects sees
# the variant's names rather than a surprise.
SHIPPED_TEMPLATE_SETS = ("default", "short story", "nothing")

DEFAULT_CONFIG = """Docx
# Sample configuration for DOCX and PDF. Every line below is a comment, so
# this set changes nothing: converting a project to it keeps the export as
# it is (A4, a page per document). Uncomment a line to use it. Each line
# overrides the sample .docx, which overrides Page setup (TEMPLATES.md 3.1).
#
# Paper: W,H in inches (millimetres write as 127mm); margins: L,T,R,B or a
# mirrored inside,outside pair.
# Page size (inches - horizontal, vertical): 6,9
# Margins: 0.75, 0.6
# Mirror margins: yes
#
# Where chapters and scenes start: Recto, page-break.
# New Chapter: Recto
# New Scene: page-break
#
# Running heads: left, middle, right -- one piece each.
# Header: ,{Title},
# Footer: ,,Page {Date}
#
# Type: family, size, line spacing, first-line indent.
# Font: Georgia, 11pt, 1.15, 1cm
#
# What the book is made of: `content` is the manuscript; other names are
# binder content pulled in, in this order. Nothing is sorted.
# Pieces: frontmatter, content, backmatter
#
# Printing from Lekhak (DOCX and PDF)
# ===================================
#
# A template tells the exporter how your book is printed in three ways, in
# escalating order — pick whichever is least work for you.
#
# 1. THIS CONFIG FILE (print-config.txt). Key: value lines, # for comments,
#    and a first line that reads `Docx` — that marker is what makes the file
#    configuration instead of a fragment. Its lines override everything below.
#
# 2. A SEPARATE TEMPLATE. Give the print version a set of its own and assign
#    it: Book template in the inspector, or Template in Export Options for a
#    single export.
#
# 3. A SAMPLE .docx. Drop any Word file with real page setup and styles into
#    this folder — a vendor's formatting sample is exactly what this is for —
#    and it supplies paper, styles, headers and footers. Lekhak keeps its
#    formatting and ignores its text. There is one to try in examples/: move
#    it up into this folder and it becomes your export's paper.
#
# Precedence, one rule: the sample supplies everything, each config line
# overrides it, and Page setup in the inspector applies only where the
# template is silent. With nothing at all you get A4 and a page per document.
#
# Lines the config understands
# ----------------------------
#
#   Page size (inches - horizontal, vertical): 5,7      also A4, or 5x5
#   Margins (L,T,R,B/I,T,O,B): 0.5, 0.3, 0.3, 0.3       four, or a mirrored pair
#   Mirror margins: yes       or no — even pages swap inside and outside
#   New Chapter: Recto        also: page-break, verso, flow, next-even
#   New Scene: page-break
#   Header: left, middle, right     and Header left / Header right for a
#   Footer: left, middle, right     mirrored book's verso and recto heads
#   Header: none                    this page carries no running head —
#   Footer: none                    a title page, a dedication — where an
#                                   empty line would keep the sample's own
#   Font: Georgia, 11pt, 1.15, 1cm  name, size, line spacing, indent
#   Title style: Book Title         Chapter style / Scene style / Body style
#
# Units: inches by default, with mm / cm / in / pt suffixes. Placeholders in
# a value are the same as in a fragment (${Title}, ${Date}, ${Author}, any
# metadata key); {Title} with single braces is accepted as its alias.
#
# About the other files
# ---------------------
#
# .html and .txt files in this folder are fragments — how one kind of thing
# is dressed. Everything else (this file, print-config.txt, examples/) is
# support material the exporter never prints.
#
# Nothing here is precious: Duplicate this set in the Templates view before
# changing anything you might want back.
#
"""

SHORT_STORY_CONFIG = """Docx
# Short stories print on A4 with a page per document. This one live line
# is what the whole precedence rule looks like in practice: choosing this
# template puts its trim in front of Page setup (docs/TEMPLATES.md 3.1).
Page size: A4
# A gift-book size, in case a short-story collection goes to print:
# Page size (inches - horizontal, vertical): 5,5
# Margins (L,T,R,B/I,T,O,B): 0.75, 0.75, 0.75, 0.75
#
# Printing from Lekhak (DOCX and PDF)
# ===================================
#
# A template tells the exporter how your book is printed in three ways, in
# escalating order — pick whichever is least work for you.
#
# 1. THIS CONFIG FILE (print-config.txt). Key: value lines, # for comments,
#    and a first line that reads `Docx` — that marker is what makes the file
#    configuration instead of a fragment. Its lines override everything below.
#
# 2. A SEPARATE TEMPLATE. Give the print version a set of its own and assign
#    it: Book template in the inspector, or Template in Export Options for a
#    single export.
#
# 3. A SAMPLE .docx. Drop any Word file with real page setup and styles into
#    this folder — a vendor's formatting sample is exactly what this is for —
#    and it supplies paper, styles, headers and footers. Lekhak keeps its
#    formatting and ignores its text. There is one to try in examples/: move
#    it up into this folder and it becomes your export's paper.
#
# Precedence, one rule: the sample supplies everything, each config line
# overrides it, and Page setup in the inspector applies only where the
# template is silent. With nothing at all you get A4 and a page per document.
#
# Lines the config understands
# ----------------------------
#
#   Page size (inches - horizontal, vertical): 5,7      also A4, or 5x5
#   Margins (L,T,R,B/I,T,O,B): 0.5, 0.3, 0.3, 0.3       four, or a mirrored pair
#   Mirror margins: yes       or no — even pages swap inside and outside
#   New Chapter: Recto        also: page-break, verso, flow, next-even
#   New Scene: page-break
#   Header: left, middle, right     and Header left / Header right for a
#   Footer: left, middle, right     mirrored book's verso and recto heads
#   Header: none                    this page carries no running head —
#   Footer: none                    a title page, a dedication — where an
#                                   empty line would keep the sample's own
#   Font: Georgia, 11pt, 1.15, 1cm  name, size, line spacing, indent
#   Title style: Book Title         Chapter style / Scene style / Body style
#
# Units: inches by default, with mm / cm / in / pt suffixes. Placeholders in
# a value are the same as in a fragment (${Title}, ${Date}, ${Author}, any
# metadata key); {Title} with single braces is accepted as its alias.
#
# About the other files
# ---------------------
#
# .html and .txt files in this folder are fragments — how one kind of thing
# is dressed. Everything else (this file, print-config.txt, examples/) is
# support material the exporter never prints.
#
# Nothing here is precious: Duplicate this set in the Templates view before
# changing anything you might want back.
#
"""

# One map per shipped set: relative path -> file text. A `.html`/`.txt`
# entry in the root is a fragment slot, a file opening with the `Docx`
# marker is print configuration, and anything else — including nested
# paths and the `documents/` shelf — is written out as it stands.
NOTHING_HTML = """# nothing — a template that prints nothing
# =========================================
#
# This set is one file: nothing.html, whose body is empty.
#
#     (an empty body element)
#
# That is the whole template. It carries no content, so an export using it
# prints nothing — DOCX and plain text write empty files, HTML and EPUB keep
# their wrappers with nothing inside them.
#
# Why one file and not one empty file per slot: silence is a property of the
# set, not of each shape it might dress. A set whose own fragments are all
# empty prints nothing, whatever slot the compile asks for, so there is
# nothing to enumerate. To make it output again, change the template assigned
# to that folder, or give nothing.html a body — the set then owns a
# non-empty fragment and every slot answers normally. Nothing was emptied to
# make this set, so nothing is lost by using it.
#
# Assign it to a folder to keep that folder out of an export, or use it as a
# starting point for a print-only or compilation-only workflow. Duplicated
# copies are yours.
#
<body></body>"""

SHIPPED_FILES: dict[str, dict[str, str]] = {
    "default": {
        "print-config.txt": DEFAULT_CONFIG,
        "style.css": DEFAULT_CSS,
    },
    "short story": {
        # One document template, not a chapter with scenes: a single
        # document dressed on its own. It is named `document` because that
        # is the Type such a piece carries (docs/MODEL.md), so a document
        # in a folder this set dresses takes the face by name (1.18).
        "documents/document.html": (
            '<article class="story">\n<h1>${Title}</h1>\n'
            '<p class="author">${Author}</p>\n'
            '<div class="content">${Content}</div>\n</article>'),
        "documents/document.txt": "${Title}\n${Author}\n\n${Content}",
        "print-config.txt": SHORT_STORY_CONFIG,
        "style.css": DEFAULT_CSS,
    },
    "nothing": {
        # One empty body, not an emptied vocabulary (§3). This file (and
        # the notes beside it) is the whole set, so it prints nothing —
        # give it a body, or reassign the folder, and it answers normally.
        "nothing.html": NOTHING_HTML,
    },
}

# Sets whose duplication writes an example formatting file (§3.1 option 3)
# into examples/ — deliberately in a subfolder, because discovery reads the
# folder's own files only: an example that changed the paper of every
# project that duplicated the default would be a silent import. The writer
# promotes it by moving it up a level, which is the whole lesson.
SHIPPED_EXAMPLE_SAMPLE = ("default", "short story")


def _shipped_files(name: str) -> dict[str, str]:
    return SHIPPED_FILES.get(name, {})


def _shipped_slots(name: str) -> tuple[dict[str, str], dict[str, str]]:
    """The set's own fragments over the built-in ones — the same rule
    ``load`` applies to a folder, applied to the files a duplicate gets."""
    html_slots, text_slots = dict(BUILTIN_HTML), dict(BUILTIN_TEXT)
    for relative, content in _shipped_files(name).items():
        if "/" in relative:
            continue                                  # the documents/ shelf
        suffix = Path(relative).suffix
        if suffix not in tuple(EXTENSIONS.values()):
            continue
        if content.split("\n", 1)[0].strip() == DOCX_MARKER:
            continue                                  # print configuration
        slots = html_slots if suffix == EXTENSIONS[True] else text_slots
        slots[Path(relative).stem] = strip_editor_html(content)
    return html_slots, text_slots


def _shipped_own_slots(name: str) -> dict[str, str]:
    """The fragment files a shipped set carries itself, body stripped.

    The same reading a folder gets, so a shipped `<body></body>` is the
    empty fragment it looks like rather than markup nobody strips.
    """
    own: dict[str, str] = {}
    for relative, content in _shipped_files(name).items():
        if "/" in relative:
            continue                                  # the documents/ shelf
        if Path(relative).suffix not in tuple(EXTENSIONS.values()):
            continue
        if content.split("\n", 1)[0].strip() == DOCX_MARKER:
            continue                                  # print configuration
        own[Path(relative).stem] = strip_editor_html(content)
    return own


def _shipped_config(name: str) -> dict[str, str]:
    """The shipped set's print configuration, parsed by exactly the parser
    a folder's own files go through — no second reading of the syntax."""
    config: dict[str, str] = {}
    for content in _shipped_files(name).values():
        if content.split("\n", 1)[0].strip() == DOCX_MARKER:
            config.update(parse_docx_config(content))
    return config


def write_example_sample(path: Path) -> Path:
    """Write the example formatting file (§3.1 option 3): A4 with the
    baseline's own margins, fake prose in every shape the example
    demonstrates, and a last line saying what it is for.

    Deliberately A4 with no fiction in its geometry: a writer who moves it
    up a level gets the mechanism and no surprise — the page setup, the
    headings and the body are their only changes to make.
    """
    from docx import Document
    from docx.shared import Inches

    # A4 with the baseline's margins (DEFAULT_PAGE_SETUP's numbers, spelled
    # out so this module never has to import the project model).
    document = Document()
    section = document.sections[0]
    section.page_width = Inches(8.27)      # A4, 210 mm
    section.page_height = Inches(11.69)    # A4, 297 mm
    section.top_margin = Inches(1.0)
    section.bottom_margin = Inches(1.0)
    section.left_margin = Inches(1.0)
    section.right_margin = Inches(1.0)
    document.add_paragraph("Formatting sample", style="Title")
    document.add_paragraph("Lekhak — what a sample .docx is for",
                            style="Subtitle")
    document.add_page_break()
    document.add_paragraph(
        "This file exists to be edited, and only its formatting survives an "
        "export: Lekhak takes its page setup, its styles and its running "
        "heads and throws this text away.")
    document.add_heading("Chapter One", level=1)
    document.add_paragraph(
        "Fake prose for a fake book: the harbour emptied at six, the gulls "
        "took the roofs, and somewhere below a door closed on the night "
        "shift. None of this is your writing, and none of it will be read "
        "by anybody who receives your export.")
    document.add_paragraph(
        "Every chapter heading here is a Heading 1 and every scene heading "
        "a Heading 2, which is what the Word table of contents looks for. "
        "Change the font, the margins, the header, the lot — then move this "
        "file one folder up and export, and what you see is what ships.")
    document.add_heading("A scene", level=2)
    document.add_paragraph(
        "Shorter paragraph, smaller heading, same idea: the structure of "
        "this file is the structure Lekhak inherits, minus every word.")
    document.add_paragraph(
        "Print-config.txt in the folder beside this one is the other route: "
        "short lines that override whatever this file says. Precedence — "
        "sample, then config lines, then Page setup — is in docs/TEMPLATES.md "
        "§3.1.")
    parent = Path(path).parent
    parent.mkdir(parents=True, exist_ok=True)
    document.save(str(path))
    return Path(path)


def materialise_builtin_set(project_root: Path, name: str = "Default") -> TemplateSet:
    """Write the built-in defaults out as an editable set inside the project.

    The defaults live in this module as strings, which suits the machine and is
    invisible to the writer. Materialising them is what puts a template in
    front of somebody: real files, in their project, beside their book. No
    code calls it on project open any more — the shipped default is visible
    and duplicable in the Templates view (docs/TEMPLATES.md §1.1) — so it
    exists for a writer who wants real files to edit.
    """
    directory = project_template_root(project_root) / name
    if directory.is_dir():
        return TemplateSet.load(name, directory)
    created = create_template_set(name, project_root)
    documents = created.directory / "documents"
    documents.mkdir(parents=True, exist_ok=True)
    (documents / "no-title.html").write_text(
        BUILTIN_DOCUMENT_TEMPLATES["no-title"][True], encoding="utf-8")
    # No extension: §3's one rule says a file that is neither .html nor .txt
    # is support material, and a README with .txt would be a fragment named
    # "README" in every sidebar.
    (created.directory / "README").write_text(SET_README, encoding="utf-8")
    return created


def slot_file_path(directory: Path, name: str, *, document: bool,
                   html: bool) -> Path:
    """Where one slot of a set lives on disk (2.4): ``<set>/<slot>.html``
    or ``<set>/documents/<name>.html`` for document-level templates."""
    base = Path(directory) / "documents" if document else Path(directory)
    return base / (name + EXTENSIONS[html])


def write_slot(directory: Path, name: str, *, document: bool, html: bool,
               text: str) -> Path:
    """Atomic slot write (2.4): temp file + os.replace, the same contract
    document content uses — a crash mid-save never leaves a half-written
    template."""
    path = slot_file_path(directory, name, document=document, html=html)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    return path
