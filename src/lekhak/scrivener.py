"""Import a Scrivener 3 project (.scriv package) into a project. (2.3)

Flow of a scriv import:

    read <name>.scrivx ─ Binder → DraftFolder subtree only
                                        │
        BinderItem walk: Folder → folder, Text → scene, in binder order
                                        │
        Files/Data/<UUID>/content.rtf → rtf_to_html → stored content
        Files/Data/<UUID>/synopsis.txt → synopsis metadata

Only the DraftFolder ("Manuscript") subtree comes through — Research,
Trash and the rest of the binder are reference material, and
Scrivener's own compile leaves them out for the same reason. The .scriv
is a folder package; the binder XML inside carries the same base name.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .project import Project

# RTF destination groups that hold no prose. Skipped whole, at any
# nesting depth, including the {\*...} internal destinations.
_RTF_DROP = {
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "themedata",
    "generator", "listtable", "listoverridetable", "latentstyles",
    "datastore", "header", "footer", "headerl", "headerr", "footerl",
    "footerr", "revtbl", "fldinst", "xmlnstbl", "colorschememapping",
}

# Named characters Scrivener emits instead of raw symbols.
_RTF_NAMED = {
    "emdash": "—", "endash": "–", "lquote": "‘", "rquote": "’",
    "ldquo": "“", "rdquo": "”", "bullet": "•", "nbsp": " ",
    "tab": "\t",
}

_HEX_ESCAPE = re.compile(r"([0-9a-fA-F]{2})")
_WORD = re.compile(r"([a-zA-Z]+)(-?\d*) ?")

# Control *symbols*: a backslash and one character, standing for a
# character rather than a formatting instruction. Scrivener writes a
# non-breaking space as ``\~`` — often several times in a sentence —
# and an importer that does not know the symbol drops it, which glues
# the words around it together: ``she'd\~expected\~trouble`` comes out
# as ``she'dexpectedtrouble``. Lekhak keeps these as ordinary spaces,
# the way it already keeps ``\nbsp``.
_RTF_SYMBOLS = {
    "~": " ",      # non-breaking space
    "_": "-",      # non-breaking hyphen
    ".": " ",      # no-width space
}


@dataclass
class ScrivItem:
    """One binder item, mapped to Lekhak's vocabulary."""

    kind: str                       # "folder" | "scene"
    title: str
    content: str
    synopsis: str
    children: list["ScrivItem"] = field(default_factory=list)


@dataclass
class ScrivProject:
    """The DraftFolder subtree of a Scrivener 3 project."""

    title: str
    draft: ScrivItem


def _skip_group(text: str, start: int) -> int:
    """Index just past the balanced group whose '{' is at ``start``."""
    depth = 0
    i = start
    n = len(text)
    while i < n:
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _escape(text: str, bold: bool, italic: bool) -> str:
    text = (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))
    if bold:
        text = f"<b>{text}</b>"
    if italic:
        text = f"<i>{text}</i>"
    return text


def _skip_fallback(rtf: str, index: int, uc: int) -> int:
    """Where the fallback characters after a ``\\uN`` escape end.

    RTF writes a Unicode character as ``\\u8220`` followed by ``uc``
    fallback characters, for readers that do not know the escape. The
    fallback is a *character*, not a number of source characters: for a
    character outside ASCII, Scrivener writes the fallback as an escape
    of its own — ``\\u8220\\'93`` — where the fallback is the one
    character ``\\'93`` and the source is four characters long.

    Counting source characters instead leaves the tail of the escape in
    the prose, and every smart quote in the manuscript reads as
    ``'93`` — which is exactly what the first Scrivener import did.
    """
    if index >= len(rtf):
        return index
    if rtf[index] != "\\":
        # Plain characters: the fallback is as long as the writer said.
        return index + uc
    if rtf[index + 1:index + 2] == "'":
        # \'xx — one character, four characters of source.
        return index + 4
    if rtf[index + 1:index + 2].isalpha():
        match = _WORD.match(rtf, index + 1)
        if match:
            return match.end()
    # Any other control symbol: one character.
    return index + 2


def rtf_to_html(rtf: str) -> str:
    """Minimal RTF → the rich-text HTML Lekhak stores.

    Only what the editor keeps: paragraphs (``\\par``), bold, italic,
    line breaks and the handful of named characters Scrivener writes.
    Destination groups — font tables, colour tables, pictures, the
    generator banner — are dropped whole; unknown control words are
    skipped. Not a general RTF engine: Scrivener writes a narrow
    dialect, and the writer polishes in the editor.
    """
    paragraphs: list[str] = []
    para: list[str] = []
    saved: list[tuple[bool, bool]] = []   # (bold, italic) per open group
    bold = italic = False
    uc = 1                                # fallback chars after \uN

    def close_para() -> None:
        nonlocal para
        text = "".join(para).strip()
        if text:
            paragraphs.append(f"<p>{text}</p>")
        para = []

    i, n = 0, len(rtf)
    while i < n:
        char = rtf[i]
        if char == "{":
            marker = rtf[i + 1:i + 3]
            if marker == "\\*":
                i = _skip_group(rtf, i)
                continue
            j = i + 1
            if j < n and rtf[j] == "\\":
                match = _WORD.match(rtf, j + 1)
                if match and match.group(1) in _RTF_DROP:
                    i = _skip_group(rtf, i)
                    continue
            saved.append((bold, italic))
            i += 1
        elif char == "}":
            if saved:
                bold, italic = saved.pop()
            i += 1
        elif char == "\\":
            i += 1
            if i >= n:
                break
            char = rtf[i]
            if char in "{}\\":
                para.append(_escape(char, bold, italic))
                i += 1
            elif char == "'":
                digits = _HEX_ESCAPE.match(rtf, i + 1)
                if digits:
                    byte = bytes([int(digits.group(1), 16)])
                    para.append(_escape(
                        byte.decode("cp1252", errors="replace"),
                        bold, italic))
                    i = digits.end()
                else:
                    i += 1
            elif char in _RTF_SYMBOLS:
                para.append(_escape(_RTF_SYMBOLS[char], bold, italic))
                i += 1
            else:
                match = _WORD.match(rtf, i)
                if not match:
                    i += 1
                    continue
                word, arg = match.group(1), match.group(2)
                i = match.end()
                if word == "par":
                    close_para()
                elif word == "line":
                    para.append("<br>")
                elif word == "b":
                    bold = arg != "0"
                elif word == "i":
                    italic = arg != "0"
                elif word == "uc" and arg:
                    uc = max(0, int(arg))
                elif word == "u" and arg:
                    code = int(arg)
                    para.append(_escape(chr(code + 65536 if code < 0
                                             else code), bold, italic))
                    i = _skip_fallback(rtf, i, uc)   # one character, however long its source
                elif word in _RTF_NAMED:
                    para.append(_escape(_RTF_NAMED[word], bold, italic))
                # everything else — font ids, codepages, cell marks —
                # is formatting this app does not store.
        elif char in "\r\n":
            i += 1
        else:
            j = i
            while j < n and rtf[j] not in "\\{}\r\n":
                j += 1
            para.append(_escape(rtf[i:j], bold, italic))
            i = j
    close_para()
    return "\n".join(paragraphs)


def _read_item(element: ET.Element, base: Path) -> ScrivItem:
    raw_type = element.get("Type") or "Text"
    title = (element.findtext("Title") or "").strip() or "Untitled"
    data = base / "Files" / "Data" / (element.get("UUID") or "")
    content = ""
    synopsis = ""
    if data.is_dir():
        rtf = data / "content.rtf"
        if rtf.is_file():
            # RTF is nominally cp1252 with hex escapes for the rest;
            # latin-1 maps every byte and never fails.
            content = rtf_to_html(
                rtf.read_bytes().decode("latin-1", errors="replace"))
        notes = data / "synopsis.txt"
        if notes.is_file():
            synopsis = notes.read_text(
                encoding="utf-8", errors="replace").strip()
    children_element = element.find("Children")
    children = []
    if children_element is not None:
        children = [_read_item(child, base)
                    for child in children_element.findall("BinderItem")]
    kind = "folder" if raw_type in ("Folder", "DraftFolder") else "scene"
    return ScrivItem(kind, title, content, synopsis, children)


def parse_scriv(path: Path) -> ScrivProject:
    """Parse a Scrivener 3 package; only the DraftFolder subtree.

    The binder XML sits beside the package's own name
    (``my-novel.scriv/my-novel.scrivx``). Text items
    keep their own prose; nested Folders become folders around them.
    """
    path = Path(path)
    if not path.is_dir():
        raise ValueError(f"{path} is not a folder; a Scrivener 3 "
                         "project is a .scriv package directory")
    scrivx = next(
        (candidate for candidate in path.glob("*.scrivx")
         if candidate.is_file()), None)
    if scrivx is None:
        raise ValueError(f"No .scrivx binder found inside {path}")
    root = ET.fromstring(scrivx.read_text(encoding="utf-8"))
    binder = root.find("Binder")
    draft_element = None
    if binder is not None:
        draft_element = next(
            (item for item in binder.findall("BinderItem")
             if item.get("Type") == "DraftFolder"), None)
    if draft_element is None:
        raise ValueError(f"No DraftFolder (Manuscript) in {scrivx.name}")
    draft = _read_item(draft_element, path)
    return ScrivProject(draft.title or path.stem, draft)


def import_scriv(project: Project, path: Path, *, title: str = "") -> str:
    """Import one Scrivener 3 project as a book tree; returns the root id.

    The root takes the DraftFolder's own title ("Manuscript") unless the
    caller names it; folders and scenes nest exactly as the binder had
    them, prose lands as stored HTML, and each item's synopsis.txt
    becomes the document's synopsis.
    """
    parsed = parse_scriv(path)
    root_title = title.strip() or parsed.draft.title or Path(path).stem
    root = project.add_document(
        project.unique_title(root_title, None), None, parsed.draft.kind)

    def add_children(items: list[ScrivItem], parent_id: str) -> None:
        for item in items:
            item_title = (item.title if item.kind == "scene"
                          else project.unique_title(item.title, parent_id))
            document = project.add_document(item_title, parent_id, item.kind)
            if item.content.strip():
                project.write_content(document.id, item.content)
            if item.synopsis:
                project.set_metadata(document.id,
                                     {"synopsis": item.synopsis})
            add_children(item.children, document.id)

    add_children(parsed.draft.children, root.id)
    return root.id
