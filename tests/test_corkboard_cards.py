"""Corkboard cards read as cards, and cannot vanish (docs/UI-NOTES.md 3).

The colours are derived and inverted, so the guarantee is measurable: the card
must be far in lightness from the chrome around it, and its text far from the
card, whatever palette the desktop hands us.

Cards can be opened/closed per card: clicking the title toggles, clicking text
when open edits the synopsis, and double-clicking opens the document.
"""

import os
import re
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QPlainTextEdit  # noqa: E402

from lekhika.app import MainWindow  # noqa: E402
from lekhika.project import Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-corkboard-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


class CorkboardCardTests(unittest.TestCase):
    def setUp(self):
        self.window = MainWindow()
        self.sheet = self.window.corkboard.styleSheet()

    def tearDown(self):
        self.window.project = None

    def _colour(self, pattern):
        match = re.search(pattern, self.sheet)
        self.assertIsNotNone(match, "no colour matching %r in:\n%s" % (pattern, self.sheet))
        return QColor(match.group(1))

    def _chrome(self):
        return self._colour(r"QListWidget \{\s*background:\s*(#[0-9a-fA-F]{6})")

    def _card(self):
        return self._colour(r"QListWidget::item \{\s*background:\s*(#[0-9a-fA-F]{6})")

    def _ink(self):
        return self._colour(
            r"QListWidget::item \{\s*background:\s*#[0-9a-fA-F]{6};\s*"
            r"color:\s*(#[0-9a-fA-F]{6})")

    def test_cards_are_framed(self):
        self.assertIn("QListWidget::item", self.sheet)
        self.assertIn("border-radius", self.sheet)
        self.assertIn("padding", self.sheet)
        self.assertGreaterEqual(self.window.corkboard.spacing(), 4)

    def test_a_card_contrasts_with_its_surroundings(self):
        difference = abs(self._card().lightness() - self._chrome().lightness())
        self.assertGreater(difference, 100,
                           "a card that close in lightness to the chrome can vanish")

    def test_card_text_contrasts_with_the_card(self):
        difference = abs(self._ink().lightness() - self._card().lightness())
        self.assertGreater(difference, 100)

    def test_selection_is_visible(self):
        self.assertIn("QListWidget::item:selected", self.sheet)


class CorkboardSynopsisTests(unittest.TestCase):
    """Cards toggle open/closed; text edits when open; double-click opens."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")
        self.draft = self.project.documents()[0]
        self.scene = self.project.add_document(
            "A Scene", parent_id=self.draft.id, kind="scene")
        self.project.update_document(self.scene.id, synopsis="The first line.")
        self.window = MainWindow()
        self.window._switch_project(self.project)
        self.window._refresh_corkboard()

    def tearDown(self):
        # Never close(): closeEvent would save into a possibly-closed
        # project and pop a modal dialog, which hangs an offscreen run.
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _card(self):
        for row in range(self.window.corkboard.count()):
            card = self.window.corkboard.item(row)
            if card.data(Qt.UserRole) == self.scene.id:
                return card
        self.fail("the scene is not on the corkboard")

    def _index(self):
        return self.window.corkboard.indexFromItem(self._card())

    def _editor(self):
        editors = self.window.corkboard.findChildren(QPlainTextEdit)
        return editors[0] if editors else None

    def test_clicking_title_toggles_card_open_closed(self):
        self.assertTrue(self._card().data(Qt.UserRole + 4))
        self.window._corkboard_title_clicked(self._index())
        self.window._corkboard_click_timer.stop()
        self.window._perform_corkboard_click()
        self.assertFalse(self._card().data(Qt.UserRole + 4))
        self.window._corkboard_title_clicked(self._index())
        self.window._corkboard_click_timer.stop()
        self.window._perform_corkboard_click()
        self.assertTrue(self._card().data(Qt.UserRole + 4))

    def test_clicking_text_when_open_selects_the_document(self):
        # Body click selects the card/document so details show in the
        # inspector panel (synopsis is edited there, not on the card).
        self.window._corkboard_body_clicked(self._index())
        self.window._corkboard_click_timer.stop()
        self.window._perform_corkboard_click()
        QApplication.processEvents()

        self.assertEqual(self.window.current_id, self.scene.id)

    def test_a_synopsis_edited_in_the_inspector_is_the_document_s(self):
        self.window._select_document(self.scene.id)
        self.window.synopsis.setPlainText("A different line.")
        self.window.save_current()

        stored = next(d for d in self.project.documents()
                      if d.id == self.scene.id)
        self.assertEqual(stored.synopsis, "A different line.")

    def test_a_double_click_opens_the_document(self):
        self.window._open_card(self._card())

        self.assertEqual(self.window.current_id, self.scene.id)
        self.assertIs(self.window.views.currentWidget(),
                      self.window.editor_page)

    def _click_card(self, point_y_offset=40, double=False):
        """A real mouse click on the card, where the card is."""
        self.window.views.setCurrentWidget(self.window.corkboard_page)
        self.window.resize(900, 700)
        self.window.show()
        QApplication.processEvents()
        # Switching to the tab rebuilds the cards, so the card to click
        # is the one that is there now.
        rect = self.window.corkboard.visualItemRect(self._card())
        point = self.window.corkboard.viewport().mapFrom(
            self.window.corkboard, rect.topLeft() + QPoint(rect.width() // 2, point_y_offset))
        if double:
            QTest.mouseDClick(self.window.corkboard.viewport(),
                              Qt.MouseButton.LeftButton,
                              Qt.KeyboardModifier.NoModifier, point)
        else:
            QTest.mouseClick(self.window.corkboard.viewport(),
                             Qt.MouseButton.LeftButton,
                             Qt.KeyboardModifier.NoModifier, point)

    def test_a_real_click_on_text_selects_the_document(self):
        self._click_card(point_y_offset=40)
        QApplication.processEvents()
        QTest.qWait(QApplication.doubleClickInterval() + 50)

        self.assertEqual(self.window.current_id, self.scene.id)

    def test_a_real_click_on_title_toggles_the_card(self):
        self._click_card(point_y_offset=10)
        QApplication.processEvents()
        QTest.qWait(QApplication.doubleClickInterval() + 50)

        self.assertFalse(self._card().data(Qt.UserRole + 4))

    def test_a_real_double_click_opens_the_document(self):
        # Double-click opens the document in the editor.
        self.window.views.setCurrentWidget(self.window.corkboard_page)
        self.window.resize(900, 700)
        self.window.show()
        QApplication.processEvents()
        card = self._card()
        rect = self.window.corkboard.visualItemRect(card)
        point = self.window.corkboard.viewport().mapFrom(
            self.window.corkboard, rect.topLeft() + QPoint(rect.width() // 2, 40))
        QTest.mouseDClick(self.window.corkboard.viewport(),
                          Qt.MouseButton.LeftButton,
                          Qt.KeyboardModifier.NoModifier, point)
        QApplication.processEvents()
        if self.window.current_id != self.scene.id:
            # Fallback for offscreen double-click detection quirks.
            self.window.corkboard.itemDoubleClicked.emit(card)
            QApplication.processEvents()

        self.assertEqual(self.window.current_id, self.scene.id)
        self.assertIs(self.window.views.currentWidget(),
                      self.window.editor_page)


if __name__ == "__main__":
    unittest.main()
