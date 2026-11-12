"""The Editor tab's flowing format bar (2.4h) — offscreen.

A QToolBar cannot wrap, so a format bar wider than the Editor tab lost
its tail — the alignment buttons sat one narrow window away from
invisible. The FlowLayout wraps instead; these hold that: width decides
how many rows, an item that does not fit starts the next row, and
heightForWidth measures without moving anything.
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# No `noqa: E402` on these: the project's ruff config does not enable
# E402, so imports-after-code are not errors here at all.
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from lekhika.flowlayout import FlowLayout

_application = QApplication.instance() or QApplication([])


class FlowLayoutTests(unittest.TestCase):
    def _host(self, items: int = 4, width: int = 60):
        host = QWidget()
        layout = FlowLayout(host, margin=2, spacing=4)
        for _ in range(items):
            label = QLabel("x" * width)
            label.setFixedWidth(width)
            layout.addWidget(label)
        return host, layout

    def test_width_decides_how_many_rows(self):
        host, _layout = self._host()

        one_row = host.heightForWidth(1200)
        many_rows = host.heightForWidth(80)

        self.assertLess(one_row, many_rows)          # narrow wraps
        self.assertGreater(many_rows, one_row * 2)   # every row shows

    def test_an_item_that_does_not_fit_starts_the_next_row(self):
        _host, layout = self._host(items=2, width=60)
        layout.setGeometry(QRect(0, 0, 90, 400))

        first = layout.itemAt(0).geometry()
        second = layout.itemAt(1).geometry()

        self.assertEqual(first.x(), layout.contentsMargins().left())
        self.assertGreater(second.y(), first.y())

    def test_height_for_width_moves_nothing(self):
        host, layout = self._host()
        layout.setGeometry(QRect(0, 0, 2000, 50))
        before = layout.itemAt(0).geometry()

        host.heightForWidth(80)

        self.assertEqual(layout.itemAt(0).geometry(), before)

    def test_items_can_be_taken_out_again(self):
        _host, layout = self._host(items=2)
        self.assertEqual(layout.count(), 2)

        self.assertIsNotNone(layout.takeAt(0))

        self.assertEqual(layout.count(), 1)
        self.assertIsNone(layout.takeAt(5))


if __name__ == "__main__":
    unittest.main()
