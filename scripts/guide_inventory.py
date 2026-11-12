"""Rebuild the guide's inventory from docs/FEATURES.md.

The guide carries two things: chapters written by hand, and an
inventory — every feature of Lekhak, one page each — generated from
``docs/FEATURES.md``. This script rebuilds the second part, so the two
never drift apart: each section of that file is a folder in the guide,
and each bullet is a page in it.

A page that is still a cut line — what the script wrote when it wrote
the page, and nothing more — is brought up to its source line. A page
finished in any other way, whether the writer wrote into it or an older
bullet already said it all, is left alone: its text is the writer's.

    python scripts/guide_inventory.py
"""

from __future__ import annotations

import html as _html
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from lekhak.project import Project  # noqa: E402

WORKING = Path.home() / "Documents" / "Lekhak Guide.lekhak"
FEATURES = REPO / "docs" / "FEATURES.md"
CHAPTER = "Every feature"

ELLIPSIS = "…"

BULLET_RE = re.compile(r"^-\s+(.*)$")
LEAD_RE = re.compile(r"^\*\*(.+?)\*\*")
MARKDOWN = re.compile(r"\*\*")
SPLIT = re.compile(r"[.:—]")
SECTION_RE = re.compile(r"^##\s+(.*)$")
STOPS = re.compile(f"[.\\s{ELLIPSIS}]+$")
SHAPING = re.compile(f"[\\s.*{ELLIPSIS}_`]+")
FINISHED = re.compile("[.!?:\"’”" + ELLIPSIS + ")]$")


def html(text: str) -> str:
    """The bullet as the page should carry it.

    The app's own syntaxes are kept as the reader should see them: a
    placeholder is escaped so an export prints the marker rather than
    expanding it, and a wiki link is written as entities so the link
    pass leaves it standing. Markup in the line is written as markup,
    not left as asterisks on the page.
    """
    text = _html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = text.replace("${", "$${")
    text = text.replace("[[", "&#91;[").replace("]]", "]&#93;")
    return f"<p>{text}</p>"


def _text(html_text: str) -> str:
    """What a page says, without its tags."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_text)).strip()


def _norm(text: str) -> str:
    """A page's text and its source line, said the same way.

    Markup is dropped rather than compared: the page carries ``<b>``
    where the bullet carries ``**``, and spaces the comparison does not
    need to be told about.
    """
    text = _html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"[\s`*_]", "", text)


def _key(text: str) -> str:
    """The page a title names, past everything that is only shaping.

    Markup, stops and case are not the difference between two pages of
    the inventory: ``**Every document carries metadata**``, the same
    title cut with an ellipsis and the same one cut with three dots are
    one page counted twice — twice written, twice listed.
    """
    text = _html.unescape(re.sub(r"<[^>]+>", "", text))
    return SHAPING.sub("", text).casefold()


def _same_page(first: str, second: str) -> bool:
    """Whether two titles are naming the same inventory page.

    One title may be the head of the other: ``A Type`` (where a line
    begins) and the page's name cut from the full sentence are the same
    row, cut differently.
    """
    here, there = _key(first), _key(second)
    return bool(here and there) and (here.startswith(there)
                                     or there.startswith(here))


def is_the_script_s_own_page(current: str, source: str) -> bool:
    """Whether this page is a line still waiting for its ending.

    The script writes a page as its bullet — one line, cut to fit. A
    page that ends short of its full stop was never finished, however
    it got there: cut short when the page was written, or left behind
    by a bullet rewritten since. A page that ends in its punctuation
    has been finished — by the source line or by the writer — and is
    not touched.
    """
    if _norm(current) == _norm(source):
        return False                      # already says the whole line
    return not FINISHED.search(_text(current))


def inventory() -> list[tuple[str, list[tuple[str, str]]]]:
    sections: list[tuple[str, list[tuple[str, str]]]] = []
    title = ""
    bullets: list[tuple[str, str]] = []
    for line in FEATURES.read_text(encoding="utf-8").splitlines():
        section = SECTION_RE.match(line)
        if section:
            if title:
                sections.append((title, bullets))
            title = section.group(1).strip()
            bullets = []
            continue
        bullet = BULLET_RE.match(line)
        if bullet and title:
            text = bullet.group(1).strip()
            lead = LEAD_RE.match(text)
            if lead:
                name = lead.group(1)
            else:
                name = SPLIT.split(text, 1)[0]
            # The name is a name: style inside the line is not part of
            # it, so a highlighted word never reaches a file name.
            name = MARKDOWN.sub("", name).strip().rstrip(".").strip()
            # A title is a name, not a sentence: the long ones are cut.
            if len(name) > 46:
                name = name[:45].rstrip() + "..."
            body = LEAD_RE.sub(lambda match: match.group(1), text)
            bullets.append((name, body))
    if title:
        sections.append((title, bullets))
    return sections


def main() -> int:
    project = Project(WORKING)
    project.open()
    try:
        book = project.documents()[0]
        chapter = next((d for d in project.documents()
                        if d.title == CHAPTER), None)
        if chapter is None:
            chapter = project.add_document(CHAPTER, book.id, "chapter")
            project.set_metadata(chapter.id, {"Type": "chapter"})
            project.write_content(chapter.id, html(
                "Every feature of Lekhak has a page of its own here, one "
                "line each. The pages are there to be written into: open "
                "this guide in Lekhak and expand the ones you would know "
                "more about."))
        made = 0
        repaired = 0
        for section_title, bullets in inventory():
            folder_title = section_title.split(". ", 1)[-1]
            # Under the inventory chapter, wherever else in the guide a
            # page or a chapter may share the name: the manual's own
            # "Metadata" is not the inventory's folder. (This used to
            # suffix a colliding name instead, which merely renamed the
            # folder for as long as nobody looked for the old one.)
            folder = next((d for d in project.documents()
                           if d.parent_id == chapter.id
                           and _key(d.title) == _key(folder_title)), None)
            if folder is None:
                folder = project.add_document(folder_title, chapter.id,
                                               "folder")
                project.set_metadata(folder.id, {"Type": "folder"})
                project.write_content(folder.id, html(
                    f"The {folder_title.split(' — ')[0].lower()} of Lekhak."))
                # Lines whose names are set: only a folder-scoped page can
            # be another line's cut. A page elsewhere in the guide —
            # the manual's own "Preferences" under Settings/ — is not
            # this line's page, and does not stand in for it.
            names_in_section = {_key(name) for name, _ in bullets}
            for name, body in bullets:
                siblings = [d for d in project.documents()
                            if d.parent_id == folder.id]
                # The name first, exactly: two pages in a folder may
                # share a start ("Templates", "Template Sets") and only
                # one of them is this line's page.
                existing = next((d for d in siblings
                                 if _key(d.title) == _key(name)), None)
                if existing is None:
                    # Cut differently by an older run: the same name,
                    # stopping short of or past its cut — but never a
                    # page named by another line, which is a different
                    # page that merely starts the same way.
                    other_lines = names_in_section - {_key(name)}
                    existing = next(
                        (d for d in siblings
                         if _key(d.title) not in other_lines
                         and _same_page(d.title, name)), None)
                if existing is not None:
                    # Finish the line only where the line is unfinished;
                    # a page written into stays as written.
                    if is_the_script_s_own_page(
                            project.read_content(existing.id), body):
                        project.write_content(existing.id, html(body))
                        repaired += 1
                else:
                    page = project.add_document(name, folder.id, "scene")
                    project.write_content(page.id, html(body))
                    project.set_metadata(page.id, {"Type": "scene"})
                    made += 1
        print(f"inventory: {made} new pages, {repaired} brought up to "
              f"their line, {len(project.documents())} documents in all")
    finally:
        project.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())