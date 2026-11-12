"""The wraparound cover (KDP print copies, docs/BUILD-NOW.md).

Geometry, estimation, and the single-page PDF itself — composed by
the typesetter, not by a word processor."""

import tempfile
import unittest
from pathlib import Path

from lekhak.cover import (compute_sizes, estimate_pages, compose_cover_pdf,
                          spine_width_inches)
from lekhak.project import Project


def _project(tmp: str) -> tuple[Project, object]:
    project = Project.create(Path(tmp) / "Novel", "Novel")
    draft = project.documents()[0]
    chapter = project.add_document("Chapter One", draft.id, "chapter")
    scene = project.add_document("Opening", chapter.id, "scene")
    project.write_content(scene.id, "<p>The river gave back its names. </p>" * 120)
    return project, draft


class EstimateTests(unittest.TestCase):
    def test_pages_estimate_scales_with_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            project, draft = _project(tmp)
            documents = project._export_documents({draft.id})
            pages = estimate_pages(project, documents)

            self.assertGreater(pages, 1)
            self.assertLess(pages, 700)
            self.assertEqual(pages, estimate_pages(project, documents))

    def test_spine_width_is_kdp_thickness(self):
        import math
        self.assertEqual(
            spine_width_inches(10), max(0.1, 10 * 0.002252))


class GeometryTests(unittest.TestCase):
    def test_wraparound_geometry(self):
        sizes = compute_sizes((152.4, 228.6), 10)

        self.assertAlmostEqual(
            sizes["page_height"], 228.6 + 2 * sizes["bleed"], places=2)
        self.assertAlmostEqual(
            sizes["page_width"],
            152.4 * 2 + sizes["spine"] + 2 * sizes["bleed"], places=4)
        left, right = sizes["zones"]["front"]
        self.assertAlmostEqual(right - left, 152.4, places=2)


class ComposeTests(unittest.TestCase):
    def test_one_page_pdf_with_cover_and_grid(self):
        from PySide6.QtPdf import QPdfDocument
        from PySide6.QtWidgets import QApplication

        with tempfile.TemporaryDirectory() as tmp:
            project, draft = _project(tmp)
            art = Path(tmp) / "art.png"
            from PIL import Image
            Image.new("RGB", (1830, 2740), color=(90, 120, 90)).save(art)
            out = Path(tmp) / "cover.pdf"

            compose_cover_pdf(
                project, {draft.id}, (152.4, 228.6), art, out,
                page_count=10)

            application = QApplication.instance() or QApplication([])
            doc = QPdfDocument()
            doc.load(str(out))
            self.assertEqual(doc.pageCount(), 1)

    def test_partial_cover_carries_only_blank_cover(self):
        from PySide6.QtPdf import QPdfDocument
        from PySide6.QtWidgets import QApplication

        with tempfile.TemporaryDirectory() as tmp:
            project, draft = _project(tmp)
            out = Path(tmp) / "cover.pdf"

            compose_cover_pdf(
                project, {draft.id}, (152.4, 228.6), None, out, page_count=5)

            application = QApplication.instance() or QApplication([])
            doc = QPdfDocument()
            doc.load(str(out))
            self.assertEqual(doc.pageCount(), 1)
