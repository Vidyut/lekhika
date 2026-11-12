"""Tracked-change marks in the editor's QTextDocument (2.4).

Two halves of one pair: the live document carries each mark as a
QTextCharFormat with change-id properties (this module), and the stored
HTML carries only the sentinel styles Qt round-trips (project.py).
Accept/reject works on the live half; reconcile_revisions prunes table
rows whose marks an undo removed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor, QTextCharFormat, QTextCursor, QTextDocument, QTextFormat,
    QTextFragment,
)

from .project import REVISION_DELETE_BG, REVISION_INSERT_BG

# Live format properties. UserProperty keys Qt keeps on the document
# while it is open and drops on toHtml() — the stored file never has
# them, which is why re-attach on load goes by span order.
REVISION_ID_PROP = QTextFormat.UserProperty + 41
REVISION_KIND_PROP = QTextFormat.UserProperty + 42

KIND_INSERT = "insert"
KIND_DELETE = "delete"


def revision_char_format(kind: str, revision_id: str,
                         base: QTextCharFormat | None = None) -> QTextCharFormat:
    """A char format carrying one mark: insert = blue underline, delete =
    red strike, both with the sentinel background and the live ids."""
    fmt = QTextCharFormat(base) if base is not None else QTextCharFormat()
    fmt.setProperty(REVISION_ID_PROP, revision_id)
    fmt.setProperty(REVISION_KIND_PROP,
                    1 if kind == KIND_INSERT else 2)
    if kind == KIND_INSERT:
        fmt.setBackground(QColor(REVISION_INSERT_BG))
        fmt.setFontUnderline(True)
    else:
        fmt.setBackground(QColor(REVISION_DELETE_BG))
        fmt.setFontStrikeOut(True)
    return fmt


def _kind_of(fmt: QTextCharFormat) -> str | None:
    value = fmt.intProperty(REVISION_KIND_PROP)
    if value == 1:
        return KIND_INSERT
    if value == 2:
        return KIND_DELETE
    # Loaded documents: the sentinel background is the mark until ids
    # re-attach. The compare is on the colour Qt stored from the CSS.
    background = fmt.background()
    if background.style() != Qt.BrushStyle.NoBrush:
        color = background.color().name().casefold()
        if color == REVISION_INSERT_BG:
            return KIND_INSERT
        if color == REVISION_DELETE_BG:
            return KIND_DELETE
    return None


def _id_of(fmt: QTextCharFormat) -> str:
    value = fmt.property(REVISION_ID_PROP)
    return str(value) if value is not None else ""


def runs(document: QTextDocument, start: int = 0,
         end: int | None = None) -> list[tuple[str, str, int, int]]:
    """Every marked text run in [start, end): (kind, revision_id, position,
    length). Revision ids are empty for marks loaded from disk before
    attach_ids ran."""
    if end is None:
        end = document.characterCount() - 1
    found: list[tuple[str, str, int, int]] = []
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment: QTextFragment = iterator.fragment()
            if fragment.isValid():
                position = fragment.position()
                length = fragment.length()
                if position < end and position + length > start:
                    kind = _kind_of(fragment.charFormat())
                    if kind:
                        clip_start = max(start, position) - position
                        clip_end = min(end, position + length) - position
                        found.append((kind, _id_of(fragment.charFormat()),
                                      position + clip_start,
                                      clip_end - clip_start))
            iterator += 1
        block = block.next()
    return found


def segments(document: QTextDocument, start: int,
             end: int) -> list[tuple[str | None, str, int, int]]:
    """[start, end) split into alternating (kind or None, revision_id,
    position, length) segments — the map a delete-selection pass walks.
    Gaps between marked runs come back as unmarked (kind None)."""
    pieces: list[tuple[str | None, str, int, int]] = []
    cursor_position = start
    for kind, revision_id, position, length in runs(document, start, end):
        if position > cursor_position:
            pieces.append((None, "", cursor_position, position - cursor_position))
        pieces.append((kind, revision_id, position, length))
        cursor_position = position + length
    if cursor_position < end:
        pieces.append((None, "", cursor_position, end - cursor_position))
    return pieces


def _clear_marks(fmt: QTextCharFormat) -> QTextCharFormat:
    """The same format without the mark — what accept leaves behind."""
    fmt.clearBackground()
    fmt.setFontUnderline(False)
    fmt.setFontStrikeOut(False)
    fmt.clearProperty(REVISION_ID_PROP)
    fmt.clearProperty(REVISION_KIND_PROP)
    return fmt


def remove_text(document: QTextDocument, position: int, length: int) -> None:
    cursor = QTextCursor(document)
    cursor.setPosition(position)
    cursor.setPosition(position + length, QTextCursor.KeepAnchor)
    cursor.removeSelectedText()


def apply_accept(document: QTextDocument,
                 revision_ids: set[str]) -> None:
    """Accept: insert runs keep their words and lose their marks, delete
    runs disappear. Reverse position order so earlier offsets stay valid."""
    for kind, revision_id, position, length in reversed(runs(document)):
        if revision_id not in revision_ids:
            continue
        if kind == KIND_DELETE:
            remove_text(document, position, length)
        else:
            cursor = QTextCursor(document)
            cursor.setPosition(position)
            cursor.setPosition(position + length, QTextCursor.KeepAnchor)
            cursor.setCharFormat(_clear_marks(
                QTextCharFormat(cursor.charFormat())))


def apply_reject(document: QTextDocument,
                 revision_ids: set[str]) -> None:
    """Reject: insert runs disappear, delete runs keep their words and
    lose their marks."""
    for kind, revision_id, position, length in reversed(runs(document)):
        if revision_id not in revision_ids:
            continue
        if kind == KIND_INSERT:
            remove_text(document, position, length)
        else:
            cursor = QTextCursor(document)
            cursor.setPosition(position)
            cursor.setPosition(position + length, QTextCursor.KeepAnchor)
            cursor.setCharFormat(_clear_marks(
                QTextCharFormat(cursor.charFormat())))


def char_at(document: QTextDocument,
            position: int) -> tuple[str | None, str, int]:
    """The char at a position: (kind or None, revision_id, position).
    Out-of-range (including the position - 1 callers ask about at
    document start) comes back as (None, "", -1)."""
    if position < 0 or position >= document.characterCount() - 1:
        return None, "", -1
    cursor = QTextCursor(document)
    cursor.setPosition(position)
    cursor.setPosition(position + 1, QTextCursor.KeepAnchor)
    fmt = cursor.charFormat()
    return _kind_of(fmt), _id_of(fmt), position


def find_id(document: QTextDocument, revision_id: str) -> bool:
    """True while any marked run still carries this id — what the erase
    path checks before dropping a table row whose last span it just
    removed."""
    if not revision_id:
        return False
    return any(run[1] == revision_id for run in runs(document))


def attach_ids(document: QTextDocument,
               pending: list) -> int:
    """Re-attach revision ids on load (2.4): pending rows in sequence
    order match the marked spans in document order, per kind. Returns how
    many spans got an id; extra spans keep rendering but stay
    unaddressable until their neighbours are resolved, and the table
    never invents rows for extra spans (reconcile prunes rows, not
    marks)."""
    by_kind: dict[str, list] = {KIND_INSERT: [], KIND_DELETE: []}
    for revision in pending:
        if revision.kind in by_kind:
            by_kind[revision.kind].append(revision)
    used = {KIND_INSERT: 0, KIND_DELETE: 0}
    attached = 0
    for kind, _revision_id, position, length in runs(document):
        bucket = by_kind[kind]
        index = used[kind]
        if index >= len(bucket):
            continue
        revision = bucket[index]
        used[kind] = index + 1
        cursor = QTextCursor(document)
        cursor.setPosition(position)
        cursor.setPosition(position + length, QTextCursor.KeepAnchor)
        cursor.mergeCharFormat(revision_char_format(kind, revision.id))
        attached += 1
    return attached
