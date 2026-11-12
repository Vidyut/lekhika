"""The Page view (3.2): the current document on real, printed pages.

QTextEdit cannot paginate live — with word wrap on it re-stamps the
document's pageSize to the viewport on every relayout, so the editing
widget will never hold a paper size. The projection owns its own
QTextDocument instead: the editor's HTML laid out at the book's
text-column size (page setup, 1.15), painted page by page — pasteboard,
paper, margins, page numbers — the way the export will print it.

Layout coordinates for such a document are page-stacked: page i's
content lives at y in [i * col_h, (i + 1) * col_h), verified against
QTextDocumentLayout. Dblclick jumps back to the editor at the same
character position: both documents are built from the same HTML, so a
character offset maps.

The geometry helpers at the top are pure functions (canvas pixels only,
no Qt objects in or out beyond tuples/QRectF) so tests can pin them
without a widget.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, QSizeF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTextDocument
from PySide6.QtGui import QAbstractTextDocumentLayout
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

# Canvas furniture, in canvas pixels: gap between stacked pages, outer
# padding around the first/last page.
PAGE_GAP = 14.0
PAGE_PAD = 20.0
# Fit-to-window never upscales past 1:1 (text stays crisp) and never
# downscales past this (below it the page is unreadable anyway).
MIN_SCALE = 0.15
MAX_SCALE = 1.0
# Manual zoom on top of fit-to-window (Ctrl+scroll, the View menu). It is
# a canvas scale, not a font size: the page keeps its real geometry and
# the pagination stays exactly what the export will print.
SCALE_STEP = 0.1
MIN_USER_SCALE = 0.25
MAX_USER_SCALE = 4.0
# The projection lays out at this many pixels per inch; fit_scale puts
# the scale on top of that for the current viewport.
DPI = 96.0

PASTEBOARD = QColor(0x38, 0x38, 0x3a)
COLUMN_LINE = QColor(0xc8, 0xc8, 0xc8)
PAGE_NUMBER = QColor(0x8a, 0x8a, 0x8a)


def page_pixels(geometry: dict, dpi: float = DPI) -> tuple:
    """Paper and text column in pixels: (page_w, page_h, col_w, col_h).
    The column is the paper minus margins minus gutter — the box prose
    can occupy on every page, identical for recto and verso."""
    page_w = geometry["width"] * dpi
    page_h = geometry["height"] * dpi
    col_w = (geometry["width"] - geometry["inside"]
             - geometry["outside"] - geometry["gutter"]) * dpi
    col_h = (geometry["height"] - geometry["top"]
             - geometry["bottom"]) * dpi
    return page_w, page_h, col_w, col_h


def content_origin(geometry: dict, page_index: int, dpi: float = DPI) -> tuple:
    """Where the text column starts on one page: (left, top) in
    pixels.

    Mirrored books alternate: recto (even pages) puts the inside margin
    and gutter on the left, verso flips it. Unmirrored keeps the
    export's fixed convention — inside on the left — on every page.
    Assumes the document itself starts recto, which is what export
    decides per book (recorded edge)."""
    if geometry["mirror"] and page_index % 2 == 1:
        left = geometry["outside"]
    else:
        left = geometry["inside"] + geometry["gutter"]
    return left * dpi, geometry["top"] * dpi


def page_count(total_height: float, col_height: float) -> int:
    """Pages needed to hold total_height of laid-out content (at
    least one — an empty document is still a page)."""
    if col_height <= 0:
        return 1
    return max(1, math.ceil(total_height / col_height))


def fit_scale(geometry: dict, viewport: tuple,
              pad: float = PAGE_PAD) -> float:
    """Scale that fits one full page into the viewport, capped at 1:1
    and at MIN_SCALE below."""
    page_w, page_h, _col_w, _col_h = page_pixels(geometry)
    del _col_h
    available_w = max(1.0, viewport[0] - 2 * pad)
    available_h = max(1.0, viewport[1] - 2 * pad)
    scale = min(MAX_SCALE, available_w / page_w, available_h / page_h)
    return max(MIN_SCALE, scale)


def canvas_extent(geometry: dict, count: int, scale: float,
                  pad: float = PAGE_PAD, gap: float = PAGE_GAP) -> QSize:
    """Fixed size of the whole canvas: every page stacked with gaps,
    padded, at the given scale."""
    page_w, page_h, _col_w, _col_h = page_pixels(geometry)
    width = 2 * pad + page_w * scale
    height = (2 * pad + count * page_h * scale
              + max(0, count - 1) * gap * scale)
    return QSize(int(math.ceil(width)), int(math.ceil(height)))


def page_rects(geometry: dict, count: int, scale: float,
               pad: float = PAGE_PAD, gap: float = PAGE_GAP) -> list:
    """One paper QRectF per page in canvas coordinates at `scale`,
    stacked in a single column."""
    page_w, page_h, _col_w, _col_h = page_pixels(geometry)
    stride = (page_h + gap) * scale
    return [
        QRectF(pad, pad + index * stride, page_w * scale, page_h * scale)
        for index in range(count)
    ]


class PageCanvas(QWidget):
    """Paints one document's pages; hit-testing lives here too."""

    jump_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._document = QTextDocument(self)
        self._document.setDocumentMargin(0.0)
        self._geometry = None            # None until a page is set
        self._scale = 1.0
        self._user_scale = 1.0      # manual zoom; fit-to-window is the floor
        self._count = 1
        self._cache_extent = QSize(0, 0)

    @property
    def page_count(self) -> int:
        return self._count

    @property
    def geometry(self) -> dict | None:
        return self._geometry

    @property
    def scale(self) -> float:
        return self._scale

    @property
    def user_scale(self) -> float:
        return self._user_scale

    def set_user_scale(self, scale: float) -> None:
        """Set the manual zoom factor (applied on the next refit)."""
        self._user_scale = min(max(round(scale, 2), MIN_USER_SCALE),
                               MAX_USER_SCALE)

    def set_page(self, html: str, geometry: dict | None,
                 font: QFont | None = None) -> None:
        """Rebuild the projection. geometry None (or junk) means no
        setup — the default Letter box applies, same as export."""
        from .project import DEFAULT_PAGE_SETUP, clean_page_setup

        cleaned = clean_page_setup(geometry) if geometry else None
        self._geometry = cleaned or dict(DEFAULT_PAGE_SETUP)
        if font is not None:
            self._document.setDefaultFont(font)
        self._document.setHtml(html)
        _page_w, _page_h, col_w, col_h = page_pixels(self._geometry)
        self._document.setPageSize(QSizeF(col_w, col_h))
        self._count = self._document.pageCount()

    def refit(self, viewport_size: QSize) -> None:
        """Scale to the viewport and resize the canvas. Scroll
        position is PageView's business — it saves and restores around
        this."""
        if self._geometry is None:
            return
        self._scale = fit_scale(
            self._geometry,
            (max(1, viewport_size.width()), max(1, viewport_size.height())),
        ) * self._user_scale
        extent = canvas_extent(self._geometry, self._count, self._scale)
        self._cache_extent = extent
        self.setFixedSize(extent)
        self.update()

    # -- painting ---------------------------------------------------
    def paintEvent(self, _event) -> None:
        if self._geometry is None:
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.fillRect(self.rect(), PASTEBOARD)
            for index, paper in enumerate(page_rects(
                    self._geometry, self._count, self._scale)):
                painter.fillRect(paper.translated(2, 2),
                                 QColor(0, 0, 0, 90))
                painter.fillRect(paper, Qt.GlobalColor.white)
                self._paint_page(painter, paper, index)
        finally:
            painter.end()

    def _paint_page(self, painter, paper: QRectF, index: int) -> None:
        geometry = self._geometry
        left, top = content_origin(geometry, index)
        _page_w, _page_h, col_w, col_h = page_pixels(geometry)
        column = QRectF(paper.x() + left * self._scale,
                        paper.y() + top * self._scale,
                        col_w * self._scale, col_h * self._scale)

        painter.save()
        painter.setClipRect(column)
        painter.translate(column.x(),
                          column.y() - index * col_h * self._scale)
        painter.scale(self._scale, self._scale)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self._document.documentLayout().draw(
            painter, QAbstractTextDocumentLayout.PaintContext())
        painter.restore()

        # The text-box outline: where prose may go (an illustration
        # placement reads this line first).
        pen = QPen(COLUMN_LINE, 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(column)

        # Page number in the bottom margin, fixed screen size.
        painter.setPen(PAGE_NUMBER)
        number_font = QFont(self._document.defaultFont())
        number_font.setPixelSize(11)
        painter.setFont(number_font)
        bottom_margin = geometry["bottom"] * DPI
        painter.drawText(
            QRectF(paper.x(), paper.bottom() - bottom_margin * 0.8,
                   paper.width(), bottom_margin * 0.6),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            str(index + 1),
        )

    # -- hit-testing ------------------------------------------------
    def char_position(self, x: float, y: float) -> int:
        """Character offset under a canvas point, or -1 off-paper.
        Page i's content sits at layout y = i * col_h + local."""
        if self._geometry is None:
            return -1
        rects = page_rects(self._geometry, self._count, self._scale)
        _page_w, _page_h, _col_w, col_h = page_pixels(self._geometry)
        for index, paper in enumerate(rects):
            if not (paper.left() <= x <= paper.right()
                    and paper.top() <= y <= paper.bottom()):
                continue
            left, top = content_origin(self._geometry, index)
            layout_x = (x - paper.x()) / self._scale - left
            layout_y = (index * col_h
                        + (y - paper.y()) / self._scale - top)
            position = self._document.documentLayout().hitTest(
                QPointF(layout_x, layout_y),
                Qt.HitTestAccuracy.FuzzyHit)
            return int(position)
        return -1

    def mouseDoubleClickEvent(self, event) -> None:
        position = self.char_position(event.position().x(),
                                      event.position().y())
        if position >= 0:
            self.jump_requested.emit(position)


class PageScrollArea(QScrollArea):
    """Scroll area whose canvas re-fits whenever the visible viewport
    changes size."""

    def __init__(self, canvas: PageCanvas, parent=None):
        super().__init__(parent)
        self._canvas = canvas
        self.setWidgetResizable(False)
        self.setWidget(canvas)
        # The viewport shows beyond a canvas narrower than the tab;
        # it must read as the same pasteboard, or the dark stage ends
        # in a light strip.
        self.viewport().setStyleSheet(
            f"background-color: {PASTEBOARD.name()};")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit()

    def fit(self) -> None:
        """Re-scale to the viewport, preserving the relative scroll
        position across the size change."""
        bar_v = self.verticalScrollBar()
        bar_h = self.horizontalScrollBar()
        ratio_v = bar_v.value() / max(1, bar_v.maximum())
        ratio_h = bar_h.value() / max(1, bar_h.maximum())
        before = self._canvas.size()
        self._canvas.refit(self.viewport().size())
        if before != self._canvas.size():
            bar_v.setValue(int(ratio_v * bar_v.maximum()))
            bar_h.setValue(int(ratio_h * bar_h.maximum()))

    def zoom_by(self, delta: float) -> None:
        self._canvas.set_user_scale(self._canvas.user_scale + delta)
        self.fit()

    def wheelEvent(self, event) -> None:
        """Ctrl+wheel zooms the paper; plain wheel keeps scrolling."""
        if event.modifiers() & Qt.ControlModifier:
            self.zoom_by(SCALE_STEP if event.angleDelta().y() > 0
                         else -SCALE_STEP)
            event.accept()
            return
        super().wheelEvent(event)


class PageView(QWidget):
    """The widget the Editor-tab stack hosts: scroll area + canvas.

    app sets it up with set_page(html, geometry, font); a dblclick on
    the paper emits jump_requested with the character offset for the
    editor."""

    jump_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._canvas = PageCanvas()
        self._area = PageScrollArea(self._canvas)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._area)
        self._canvas.jump_requested.connect(self.jump_requested)

    @property
    def page_count(self) -> int:
        return self._canvas.page_count

    @property
    def scale(self) -> float:
        return self._canvas.scale

    @property
    def geometry(self) -> dict | None:
        return self._canvas.geometry

    def set_page(self, html: str, geometry: dict | None,
                 font: QFont | None = None) -> None:
        self._canvas.set_page(html, geometry, font)
        self._area.fit()

    def set_user_scale(self, scale: float) -> None:
        """Manual zoom for the paper — app drives it from the editor's."""
        self._canvas.set_user_scale(scale)
        self._area.fit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._area.fit()
