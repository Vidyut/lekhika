"""Minimal markdown <-> HTML conversions (export side).

Extracted from the archived importer module so DOCX/HTML/markdown export
keeps working in 1.0 without shipping the import stack. Only the pure
text transforms live here; no Project access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


def markdown_to_html(text: str) -> str:
    """Minimal markdown → the rich-text HTML Lekhika stores."""
    paragraphs: list[str] = []
    for chunk in re.split(r"\n\s*\n", text.strip()):
        lines = [line.rstrip() for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        joined = "\n".join(lines)
        heading = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", joined)
        if heading:
            paragraphs.append(f"<p><b>{_inline(heading.group(1))}</b></p>")
            continue
        escaped = _inline(joined).replace("\n", "<br>")
        paragraphs.append(f"<p>{escaped}</p>")
    return "\n".join(paragraphs)


def _inline(text: str) -> str:
    escaped = (text.replace("&", "&amp;").replace("<", "&lt;")
               .replace(">", "&gt;"))
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"\*(.+?)\*", r"<i>\1</i>", escaped)
    return escaped


def markdown_from_html(html: str) -> str:
    """Inverse of markdown_to_html's subset: rich-text HTML back to clean markdown prose."""
    html = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.I | re.S)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"</p\s*>", "\n\n", html, flags=re.I)
    html = re.sub(r"<(?:b|strong)>", "**", html, flags=re.I)
    html = re.sub(r"</(?:b|strong)>", "**", html, flags=re.I)
    html = re.sub(r"<(?:i|em)>", "*", html, flags=re.I)
    html = re.sub(r"</(?:i|em)>", "*", html, flags=re.I)
    html = re.sub(r"<[^>]+>", "", html)
    html = (html.replace("&nbsp;", " ").replace("&amp;", "&")
            .replace("&lt;", "<").replace("&gt;", ">"))
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


@dataclass
class MarkdownSplit:
    kind: str                       # "chapter" | "scene"
    title: str
    body: str


def _divider_kind(line: str, chapter_divider: str,
                  scene_divider: str) -> tuple[str | None, str]:
    stripped = line.strip()
    for kind, divider in (("scene", scene_divider),
                          ("chapter", chapter_divider)):
        if not divider or not stripped.startswith(divider):
            continue
        rest = stripped[len(divider):]
        if rest and not rest[0].isspace():
            continue
        return kind, rest.strip()
    return None, ""


def split_markdown(text: str, *, chapter_divider: str = "#",
                   scene_divider: str = "##",
                   opening_title: str = "Opening") -> list[MarkdownSplit]:
    chunks: list[MarkdownSplit] = []
    kind: str | None = None
    title = opening_title
    body: list[str] = []
    for line in text.splitlines():
        found_kind, found_title = _divider_kind(
            line, chapter_divider, scene_divider)
        if found_kind:
            if kind is not None:
                chunks.append(MarkdownSplit(kind, title, "\n".join(body)))
            kind, title, body = found_kind, found_title or "Untitled", []
        elif kind is not None:
            body.append(line)
        else:
            kind, title, body = "scene", opening_title, [line]
    if kind is not None:
        chunks.append(MarkdownSplit(kind, title, "\n".join(body)))
    return chunks
