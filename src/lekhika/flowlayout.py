"""A layout that wraps its items onto as many rows as the width allows.

Qt's canonical FlowLayout example, trimmed to what this application
needs. The Editor tab's format bar needs it (2.4h): a QToolBar cannot
wrap, so a format bar carrying every control lost its tail past the
editor's width — alignment was one narrow window away from invisible,
and the author of the app found the bar cut off at his own window. A
flowing row keeps every control on screen whatever the window does,
and costs vertical space only when there really is no room.

Used with the container's height-for-width turned on (see the Editor
tab's build), so a narrow window makes the bar taller rather than
clipping anything.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout


class FlowLayout(QLayout):
    """Arrange widgets left to right, starting a new row whenever the
    next item would not fit — text wrapping, for controls.

    A QLayout like any other: add and take items the way the box
    layouts do, and `hasHeightForWidth` tells the widget holding it
    that its height depends on the width it is given.
    """

    def __init__(self, parent=None, margin: int = 0, spacing: int = 4):
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        self._spacing = spacing
        self._items: list = []

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        # Nothing here asks for more than its size: the flow decides
        # the rest, and the container spans the width it is given.
        return Qt.Orientations()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        margins = self.contentsMargins()
        usable = max(0, width - margins.left() - margins.right())
        return (self._flow(QRect(margins.left(), margins.top(), usable, 0),
                           test_only=True)
                + margins.top() + margins.bottom())

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        margins = self.contentsMargins()
        inner = rect.adjusted(margins.left(), margins.top(),
                              -margins.right(), -margins.bottom())
        self._flow(inner, test_only=False)

    def sizeHint(self) -> QSize:
        # One row is the comfortable size, but every item must fit:
        # the smallest ask is the widest single item.
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size + self._contents_margins()

    def minimumSize(self) -> QSize:
        return self.sizeHint()

    def _contents_margins(self) -> QSize:
        margins = self.contentsMargins()
        return QSize(margins.left() + margins.right(),
                     margins.top() + margins.bottom())

    def _flow(self, rect: QRect, test_only: bool) -> int:
        """Place every item within rect's width, or — when test_only —
        measure the height the placement wants without touching a
        thing. Returns the height used, so heightForWidth can answer
        without a geometry pass."""
        x = rect.x()
        row_y = rect.y()
        row_height = 0
        for item in self._items:
            size = item.sizeHint()
            if x + size.width() > rect.right() and x > rect.x():
                x = rect.x()                  # this item starts a new row
                row_y += row_height + self._spacing
                row_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, row_y), size))
            x += size.width() + self._spacing
            row_height = max(row_height, size.height())
        return row_y + row_height - rect.y()
