"""3.2 Page view: the geometry that maps documents onto paper, the
projection canvas, and the tab that hosts it — offscreen.

Same GUI-suite contract as the others: QSettings redirected into a temp
dir, windows never closed().
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lekhika.app import MainWindow  # noqa: E402
from lekhika.pageview import (  # noqa: E402
    MAX_SCALE, MIN_SCALE, PageView, canvas_extent, content_origin,
    fit_scale, page_count, page_pixels, page_rects,
)
from lekhika.project import Project, clean_page_setup  # noqa: E402

_application = QApplication.instance() or QApplication([])
_application.setOrganizationName("Lekhika")
_application.setApplicationName("Lekhika")
_settings_dir = tempfile.mkdtemp(prefix="lekhika-pageview-settings-")
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, _settings_dir)


def _six_by_nine(**overrides):
    geometry = {
        "trim": "6x9",
        "width": 6.0, "height": 9.0,
        "top": 0.75, "bottom": 0.75,
        "inside": 0.5, "outside": 0.25,
        "mirror": True,
        "gutter": 0.25,
    }
    geometry.update(overrides)
    return clean_page_setup(geometry)


def _paragraphs(count):
    body = "".join(
        f"<p>Paragraph {i} with enough words to flow across the page.</p>"
        for i in range(count)
    )
    return f"<html><body>{body}</body></html>"


def _double_click(canvas, point):
    """Duck-typed mouse event — canvas.mouseDoubleClickEvent only reads
    event.position()."""
    canvas.mouseDoubleClickEvent(SimpleNamespace(position=lambda: point))


class PageGeometryTests(unittest.TestCase):
    def test_page_pixels_split_paper_from_column(self):
        page_w, page_h, col_w, col_h = page_pixels(_six_by_nine())
        self.assertEqual((page_w, page_h), (576.0, 864.0))
        self.assertEqual(col_w, (6.0 - 0.5 - 0.25 - 0.25) * 96)
        self.assertEqual(col_h, (9.0 - 0.75 - 0.75) * 96)

    def test_recto_puts_inside_on_the_left_and_verso_flips(self):
        left, top = content_origin(_six_by_nine(), 0)
        self.assertEqual(left, (0.5 + 0.25) * 96)   # inside + gutter
        self.assertEqual(top, 0.75 * 96)

        left_v, _top = content_origin(_six_by_nine(), 1)
        self.assertEqual(left_v, 0.25 * 96)         # outside

    def test_unmirrored_books_keep_inside_on_the_left_every_page(self):
        geometry = _six_by_nine(mirror=False)
        self.assertEqual(content_origin(geometry, 1)[0],
                         content_origin(geometry, 0)[0])

    def test_page_count_holds_at_least_one_page(self):
        self.assertEqual(page_count(0, 720), 1)
        self.assertEqual(page_count(720, 720), 1)
        self.assertEqual(page_count(721, 720), 2)
        self.assertEqual(page_count(1440, 720), 2)
        self.assertEqual(page_count(100, 0), 1)

    def test_fit_scale_caps_at_one_and_floors_at_minimum(self):
        geometry = _six_by_nine()
        self.assertEqual(fit_scale(geometry, (4000, 4000)), MAX_SCALE)
        small = fit_scale(geometry, (300, 300))
        self.assertLess(small, 1.0)
        self.assertGreaterEqual(small, MIN_SCALE)
        self.assertEqual(fit_scale(geometry, (5, 5)), MIN_SCALE)

    def test_page_rects_stack_with_the_gap(self):
        geometry = _six_by_nine()
        rects = page_rects(geometry, 3, 0.5)
        self.assertEqual(len(rects), 3)
        self.assertEqual(rects[0].top(), 20.0)          # PAGE_PAD
        stride = (geometry["height"] * 96 + 14.0) * 0.5
        self.assertEqual(rects[1].top(), 20.0 + stride)
        extent = canvas_extent(geometry, 3, 0.5)
        self.assertEqual(extent.height(), rects[2].bottom() + 20.0)
        self.assertEqual(extent.width(), rects[0].right() + 20.0)


class EffectivePageSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def test_inherits_from_the_nearest_book(self):
        book = self.project.documents()[0]
        scene = self.project.add_document("Scene", book.id, "scene")
        self.project.set_page_setup(book.id, _six_by_nine())

        effective = self.project.effective_page_setup(scene.id)
        self.assertEqual(effective["width"], 6.0)
        self.assertEqual(self.project.effective_page_setup(book.id)["width"],
                         6.0)

    def test_walks_past_a_junk_row_to_the_next_book(self):
        outer = self.project.documents()[0]
        inner = self.project.add_document("Book", outer.id, "folder")
        scene = self.project.add_document("Scene", inner.id, "scene")
        self.project.set_page_setup(outer.id, _six_by_nine())
        stored = json.loads(self.project.get_setting("page_setup"))
        stored[inner.id] = {"width": "wide"}
        self.project.set_setting("page_setup", json.dumps(stored))

        effective = self.project.effective_page_setup(scene.id)
        self.assertEqual(effective["width"], 6.0)

    def test_none_when_no_book_has_a_setup(self):
        scene = self.project.add_document(
            "Scene", self.project.documents()[0].id, "scene")
        self.assertIsNone(self.project.effective_page_setup(scene.id))

        self.project.set_setting("page_setup", "{not json")
        self.assertIsNone(self.project.effective_page_setup(scene.id))


class PageCanvasTests(unittest.TestCase):
    def setUp(self):
        self.view = PageView()
        self.view.resize(700, 600)
        self.view.show()

    def tearDown(self):
        self.view.hide()

    def test_long_content_paginates_and_fits_the_viewport(self):
        self.view.set_page(_paragraphs(40), _six_by_nine())
        _application.processEvents()
        self.assertGreater(self.view.page_count, 1)
        self.assertGreaterEqual(self.view.scale, MIN_SCALE)
        self.assertLessEqual(self.view.scale, MAX_SCALE)
        canvas = self.view._canvas
        self.assertEqual(canvas.size(),
                         canvas_extent(self.view.geometry,
                                       self.view.page_count,
                                       self.view.scale))

    def test_no_geometry_falls_back_to_a4(self):
        self.view.set_page(_paragraphs(2), None)
        self.assertEqual(self.view.geometry["width"], 8.27)
        self.assertEqual(self.view.geometry["height"], 11.69)

    def test_hit_test_maps_the_first_page_and_rejects_off_paper(self):
        self.view.set_page(_paragraphs(40), _six_by_nine())
        _application.processEvents()
        canvas = self.view._canvas
        paper = page_rects(self.view.geometry, self.view.page_count,
                           self.view.scale)[0]
        left, top = content_origin(self.view.geometry, 0)
        position = canvas.char_position(
            paper.x() + (left + 10) * self.view.scale,
            paper.y() + (top + 5) * self.view.scale)
        self.assertGreaterEqual(position, 0)
        self.assertLess(position, 5)
        self.assertEqual(canvas.char_position(1, 1), -1)

    def test_second_page_hits_later_characters(self):
        self.view.set_page(_paragraphs(40), _six_by_nine())
        _application.processEvents()
        canvas = self.view._canvas
        rects = page_rects(self.view.geometry, self.view.page_count,
                           self.view.scale)
        self.assertGreaterEqual(len(rects), 2)
        left, top = content_origin(self.view.geometry, 1)
        position = canvas.char_position(
            rects[1].x() + (left + 10) * self.view.scale,
            rects[1].y() + (top + 5) * self.view.scale)
        self.assertGreater(position, 0)

    def test_doubleclick_emits_a_jump_with_the_position(self):
        self.view.set_page(_paragraphs(40), _six_by_nine())
        _application.processEvents()
        fired = []
        self.view.jump_requested.connect(fired.append)
        paper = page_rects(self.view.geometry, self.view.page_count,
                           self.view.scale)[0]
        _double_click(self.view._canvas,
                      QPointF(paper.x() + 60, paper.y() + 40))
        self.assertEqual(len(fired), 1)
        self.assertIsInstance(fired[0], int)

        _double_click(self.view._canvas, QPointF(1, 1))
        self.assertEqual(len(fired), 1)   # nothing off-paper


class PageTabTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")
        self.folder = self.project.documents()[0]
        self.scene = self.project.add_document(
            "Scene", self.folder.id, "scene")
        self.project.write_content(self.scene.id, _paragraphs(40))
        self.window = MainWindow()
        self.window._switch_project(self.project)

    def tearDown(self):
        self.window.project = None
        self.project.close()
        self.temporary.cleanup()

    def test_page_tab_sits_second_and_refreshes_on_show(self):
        self.assertEqual(self.window.views.tabText(1), "Page preview")
        self.window._select_document(self.scene.id)
        self.window.views.setCurrentWidget(self.window.page_view)
        self.assertGreater(self.window.page_view.page_count, 1)
        self.assertIn("Page view",
                      self.window.statusBar().currentMessage())

    def test_geometry_comes_from_the_books_effective_setup(self):
        self.project.set_page_setup(self.folder.id, _six_by_nine())
        self.window._select_document(self.scene.id)
        self.window.views.setCurrentWidget(self.window.page_view)
        self.assertEqual(self.window.page_view.geometry["width"], 6.0)

    def test_jump_switches_to_the_editor_and_positions_the_cursor(self):
        self.window._select_document(self.scene.id)
        self.window.views.setCurrentWidget(self.window.page_view)
        self.window._page_jump(7)
        self.assertIs(self.window.views.currentWidget(), self.window.editor_page)
        self.assertEqual(self.window.editor.textCursor().position(), 7)

    def test_refresh_stays_quiet_on_other_tabs(self):
        self.window._select_document(self.scene.id)
        self.window._refresh_page_preview()      # Editor tab showing
        self.assertNotIn("Page view",
                         self.window.statusBar().currentMessage())


if __name__ == "__main__":
    unittest.main()
