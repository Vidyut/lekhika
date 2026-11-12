"""Editor zoom (2.x): display-only magnification that never reaches a file.

The load-bearing test here is test_zoom_never_reaches_stored_html — the
entire point of a display-layer scale is that saving while zoomed writes
exactly what saving unzoomed writes. The rest pin the places the factor
has to be undone: style signatures, paste, the page projection, and the
session state it is remembered in.

Offscreen Qt, QSettings redirected into a temp dir, windows never
closed() — same rules as test_app_resume, for the same reasons.
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QSettings, QSize, Qt  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lekhika.app import (  # noqa: E402
    ZOOM_ANCHOR_PT, ZOOM_MAX, ZOOM_MIN, MainWindow, scaled_font_sizes,
)
from lekhika.pageview import (  # noqa: E402
    MAX_USER_SCALE, MIN_USER_SCALE, PageCanvas, fit_scale,
)
from lekhika.project import DEFAULT_PAGE_SETUP, Project  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-test-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


def _find_item(tree, document_id):
    def walk(item):
        if item.data(0, Qt.UserRole) == document_id:
            return item
        for index in range(item.childCount()):
            found = walk(item.child(index))
            if found is not None:
                return found
        return None

    for index in range(tree.topLevelItemCount()):
        found = walk(tree.topLevelItem(index))
        if found is not None:
            return found
    return None


class ScaledFontSizesTests(unittest.TestCase):
    """The transform itself: exact, reversible, and a no-op at 1.0."""

    HTML = ('<html><head><style>p { font-size:12pt; }</style></head>'
            '<body style=" font-family:\'Ubuntu Sans\'; font-size:12pt;'
            ' font-weight:400;">'
            '<p><span style=" font-size:28pt;">Title</span> body</p></body>'
            '</html>')

    def test_factor_one_returns_the_string_untouched(self):
        self.assertIs(scaled_font_sizes(self.HTML, 1.0), self.HTML)

    def test_scales_every_size_including_the_body_style(self):
        scaled = scaled_font_sizes(self.HTML, 1.5)
        self.assertIn("font-size:18pt", scaled)      # body
        self.assertIn("font-size:42pt", scaled)      # the span
        self.assertNotIn("font-size:12pt", scaled)

    def test_round_trip_is_byte_identical(self):
        zoomed = scaled_font_sizes(self.HTML, 1.5)
        self.assertEqual(scaled_font_sizes(zoomed, 1.0 / 1.5), self.HTML)

    def test_fractional_sizes_do_not_accumulate_decimals(self):
        scaled = scaled_font_sizes(self.HTML, 1.1)
        self.assertEqual(scaled_font_sizes(scaled, 1.0 / 1.1), self.HTML)


class EditorZoomTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")
        self.draft = self.project.documents()[0]
        self.scene = self.project.add_document("Scene", self.draft.id, "scene")
        self.project.write_content(
            self.scene.id,
            '<p><span style="font-size:12pt;">Hello world, this is a test.'
            "</span></p>")
        QSettings().remove("session/lastProject")
        self.window = MainWindow()
        self.window._switch_project(self.project)
        item = _find_item(self.window.tree, self.scene.id)
        self.assertIsNotNone(item)
        self.window.tree.setCurrentItem(item)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def _display_size(self):
        """What the first fragment renders at: its own size, or the base
        font for text that carries none."""
        size = (self.window.editor.document().firstBlock().begin().fragment()
                .charFormat().fontPointSize())
        return size or self.window.editor.font().pointSizeF()

    # -- the promise ------------------------------------------------------

    def test_zoom_never_reaches_stored_html(self):
        self.window.save_current()
        baseline = self.project.content_path(self.scene.id).read_bytes()

        self.window.set_zoom(1.5)
        self.window.save_current()

        after = self.project.content_path(self.scene.id).read_bytes()
        self.assertEqual(after, baseline)
        self.assertIn(b"font-size:12pt", after)

    def test_zoom_and_save_survives_a_reload(self):
        self.window.save_current()
        before = self._display_size()

        self.window.set_zoom(2.0)
        self.window.save_current()
        item = self.window.tree.currentItem()
        self.window._selection_changed(item, item)      # what a reopen does

        self.assertEqual(self._display_size(), before * 2.0)

    # -- display side -----------------------------------------------------

    def test_zoom_scales_the_live_document(self):
        before = self._display_size()
        self.window.set_zoom(1.5)
        self.assertEqual(self._display_size(), round(before * 1.5, 2))
        self.assertEqual(self.window.editor.display_scale, 1.5)
        self.assertEqual(
            self.window.editor.font().pointSizeF(), ZOOM_ANCHOR_PT * 1.5)

    def test_caret_survives_a_zoom_change(self):
        cursor = self.window.editor.textCursor()
        cursor.setPosition(7)
        self.window.editor.setTextCursor(cursor)

        self.window.set_zoom(2.0)

        self.assertEqual(self.window.editor.textCursor().position(), 7)

    def test_zoom_is_clamped(self):
        self.window.set_zoom(99.0)
        self.assertEqual(self.window.zoom, ZOOM_MAX)
        self.window.set_zoom(0.01)
        self.assertEqual(self.window.zoom, ZOOM_MIN)

    def test_zoom_by_steps_and_resets(self):
        self.window.zoom_by(0.2)
        self.assertAlmostEqual(self.window.zoom, 1.2)
        self.window.reset_zoom()
        self.assertEqual(self.window.zoom, 1.0)

    # -- session state ----------------------------------------------------

    def test_zoom_is_remembered_in_the_project_and_restored(self):
        self.window.set_zoom(1.4)
        self.assertEqual(self.project.get_setting("editor_zoom"), "1.40")

        reopened = Project(self.project.root)
        reopened.open()
        second = MainWindow()
        second._switch_project(reopened)
        try:
            self.assertEqual(second.zoom, 1.4)
            self.assertEqual(second.zoom_label.text(), "140%")
        finally:
            second.project = None
            reopened.close()

    def test_junk_stored_zoom_falls_back_to_100(self):
        self.project.set_setting("editor_zoom", "enormous")
        self.window._load_zoom()
        self.assertEqual(self.window.zoom, 1.0)

    # -- the places the factor must be undone or applied -------------------

    def test_style_apply_scales_and_capture_unscales(self):
        self.window.set_zoom(1.5)
        index = self.window.style_box.findData("Title")
        self.window.apply_paragraph_style(index)

        self.assertEqual(self.window.editor.fontPointSize(), 28.0 * 1.5)

        self.assertTrue(self.window.save_style_from_cursor("Zoomed copy"))
        self.assertEqual(self.window._user_styles["Zoomed copy"]["size"], 28.0)
        # …and matching still works: the paragraph wears Title, zoom or not.
        self.assertEqual(self.window.style_at_cursor(), "Title")

    def test_size_box_value_is_a_document_size(self):
        self.window.set_zoom(1.5)
        self.window.set_font_size("20")
        self.assertEqual(self.window.editor.fontPointSize(), 30.0)

    def test_clear_formatting_lands_on_the_zoomed_anchor(self):
        self.window.set_zoom(1.5)
        self.window.set_font_size("30")
        self.window.clear_formatting()
        self.assertEqual(self.window.editor.fontPointSize(),
                         ZOOM_ANCHOR_PT * 1.5)

    def test_paste_is_brought_into_display_units(self):
        self.window.set_zoom(1.5)
        source = QMimeData()
        source.setHtml('<p><span style="font-size:11pt;">pasted</span></p>')
        source.setText("pasted")
        QApplication.clipboard().setMimeData(source)
        self.window.editor.paste_keep_all()

        self.assertEqual(self._display_size(), 11.0 * 1.5)

    # -- copy and cut leave stored units on the clipboard ------------------

    def test_copy_while_zoomed_pastes_the_original_size(self):
        """The clipboard is a saved layer, not a display one.

        Qt's own copy puts the *live* document on it — sizes already ×
        zoom — and paste multiplies once on the way back in. Without the
        divide in LinkEditor.copy(), a copy-paste at 150 % would grow a
        12 pt passage to 27 pt, and the next save would store 18 pt.
        """
        self.addCleanup(QSettings().remove, "paste/fonts")
        QSettings().setValue("paste/fonts", True)     # keep the sizes
        self.window.set_zoom(1.5)
        self.window.save_current()
        baseline = self.project.content_path(self.scene.id).read_bytes()

        cursor = self.window.editor.textCursor()
        cursor.select(QTextCursor.Document)
        self.window.editor.setTextCursor(cursor)
        self.window.editor.copy()
        self.window.editor.selectAll()
        self.window.editor.textCursor().removeSelectedText()
        self.window.editor.paste_keep_all()

        self.assertEqual(self._display_size(), 12.0 * 1.5)
        self.window.save_current()
        self.assertIn(b"font-size:12pt",
                      self.project.content_path(self.scene.id).read_bytes())
        self.assertNotIn(b"font-size:18pt",
                         self.project.content_path(self.scene.id).read_bytes())
        self.assertNotEqual(self.project.content_path(self.scene.id).read_bytes(),
                            baseline)    # the paste added a second copy

    def test_cut_while_zoomed_leaves_stored_units_on_the_clipboard(self):
        """Qt's own cut never calls the Python copy(); cut() has to."""
        self.addCleanup(QSettings().remove, "paste/fonts")
        QSettings().setValue("paste/fonts", True)
        self.window.set_zoom(1.5)

        self.window.editor.selectAll()
        self.window.editor.cut()
        self.assertEqual(self.window.editor.toPlainText(), "")
        self.window.editor.paste_keep_all()

        self.assertEqual(self._display_size(), 12.0 * 1.5)

    def test_page_view_projects_stored_sizes(self):
        self.window.set_zoom(1.5)
        self.window.views.setCurrentWidget(self.window.page_view)
        self.window._refresh_page_preview()

        projection = self.window.page_view._canvas._document.toHtml()
        self.assertIn("font-size:12pt", projection)
        self.assertNotIn("font-size:18pt", projection)


class PageCanvasZoomTests(unittest.TestCase):
    """The paper scales as a whole; fit-to-window stays the floor."""

    def setUp(self):
        self.canvas = PageCanvas()
        self.geometry = dict(DEFAULT_PAGE_SETUP)
        self.canvas.set_page("<p>one two three</p>", self.geometry, None)

    def test_user_scale_multiplies_the_fit(self):
        self.canvas.set_user_scale(2.0)
        self.canvas.refit(QSize(1200, 1200))
        self.assertAlmostEqual(
            self.canvas.scale,
            fit_scale(self.geometry, (1200, 1200)) * 2.0, places=5)

    def test_user_scale_is_clamped(self):
        self.canvas.set_user_scale(99.0)
        self.assertEqual(self.canvas.user_scale, MAX_USER_SCALE)
        self.canvas.set_user_scale(0.0001)
        self.assertEqual(self.canvas.user_scale, MIN_USER_SCALE)

    def test_pagination_is_independent_of_zoom(self):
        pages = self.canvas.page_count
        self.canvas.set_user_scale(2.0)
        self.canvas.refit(QSize(1200, 1200))
        self.assertEqual(self.canvas.page_count, pages)


def tearDownModule():
    """Drop the clipboard's Python-owned QMimeData while Qt is still up:
    left there, PySide6 6.12 frees it twice at interpreter exit and the
    process dies with SIGSEGV *after* the tests have all passed."""
    QApplication.clipboard().clear()


if __name__ == "__main__":
    unittest.main()
