"""1.15 print geometry: per-book page setup (trim, margins, mirror,
gutter) — storage, scope resolution, the DOCX emission, and the
offscreen dialog that edits it.
"""

import math
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from lekhak.project import (
    DEFAULT_PAGE_SETUP, PAGE_TRIMS, Project, clean_page_setup, trim_id_for,
)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _twips(inches) -> str:
    """Inches as the integer twips DOCX stores — python-docx rounds."""
    return str(int(round(inches * 1440)))



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
    return geometry


def _sections(archive):
    root = ET.fromstring(archive.read("word/document.xml"))
    return root.findall(f".//{W}sectPr")


class PageSetupValidationTests(unittest.TestCase):
    def test_clean_accepts_a_full_geometry(self):
        cleaned = clean_page_setup(_six_by_nine())
        self.assertEqual(cleaned["trim"], "6x9")
        self.assertTrue(cleaned["mirror"])
        self.assertEqual(cleaned["gutter"], 0.25)

    def test_trim_lookup_covers_presets_and_reports_custom(self):
        self.assertEqual(trim_id_for(8.5, 11.0), "letter")
        self.assertEqual(trim_id_for(6.14, 9.21), "6.14x9.21")
        # The Amazon list the panel's trim row offers (1.2) is complete
        # in PAGE_TRIMS: every preset id resolves, KDP's odd trim too.
        self.assertEqual(trim_id_for(5.06, 7.81), "5.06x7.81")
        self.assertEqual(PAGE_TRIMS["5.06x7.81"][1:], (5.06, 7.81))
        self.assertEqual(trim_id_for(5.0, 7.0), "custom")
        self.assertEqual(trim_id_for(11.0, 8.5), "custom")  # swapped

    def test_unknown_trim_name_is_rederived_from_the_inches(self):
        row = _six_by_nine(trim="publisher-5x8")
        self.assertEqual(clean_page_setup(row)["trim"], "6x9")

    def test_non_true_mirror_flag_is_off(self):
        self.assertFalse(clean_page_setup(_six_by_nine(mirror=1))["mirror"])
        self.assertFalse(clean_page_setup(_six_by_nine(mirror="yes"))["mirror"])

    def test_margins_must_leave_a_text_area(self):
        full_height = _six_by_nine(top=7.0, bottom=2.0)  # == height
        self.assertIsNone(clean_page_setup(full_height))
        full_width = _six_by_nine(inside=3.0, outside=2.5, gutter=0.5)
        self.assertIsNone(clean_page_setup(full_width))

    def test_range_and_type_garbage_is_rejected(self):
        self.assertIsNone(clean_page_setup(_six_by_nine(width=0.5)))
        self.assertIsNone(clean_page_setup(_six_by_nine(height=100.0)))
        self.assertIsNone(clean_page_setup(_six_by_nine(top=-1.0)))
        self.assertIsNone(clean_page_setup(_six_by_nine(width="six feet")))
        self.assertIsNone(clean_page_setup(None))
        self.assertIsNone(clean_page_setup(["6x9"]))
        self.assertIsNone(clean_page_setup(_six_by_nine(width=math.nan)))
        self.assertIsNone(clean_page_setup(_six_by_nine(width=math.inf)))


class PageSetupStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def test_blank_returns_none_and_roundtrip_stores(self):
        draft = self.project.documents()[0]
        self.assertIsNone(self.project.page_setup(draft.id))

        self.project.set_page_setup(draft.id, _six_by_nine())
        stored = self.project.page_setup(draft.id)
        self.assertEqual(stored["width"], 6.0)
        self.assertTrue(stored["mirror"])

        self.project.set_page_setup(draft.id, None)
        self.assertIsNone(self.project.page_setup(draft.id))

    def test_folders_are_independent(self):
        draft = self.project.documents()[0]
        other = self.project.add_document("Novellas", draft.id, "folder")
        self.project.set_page_setup(draft.id, _six_by_nine())
        self.assertIsNone(self.project.page_setup(other.id))

    def test_invalid_geometry_refuses_to_store(self):
        draft = self.project.documents()[0]
        with self.assertRaises(ValueError):
            self.project.set_page_setup(draft.id, {"width": 6.0})
        self.assertIsNone(self.project.page_setup(draft.id))

    def test_junk_setting_degrades_and_a_new_set_recovers(self):
        draft = self.project.documents()[0]
        self.project.set_setting("page_setup", "{not json")
        self.assertIsNone(self.project.page_setup(draft.id))
        self.project.set_page_setup(draft.id, _six_by_nine())
        self.assertEqual(self.project.page_setup(draft.id)["width"], 6.0)

    def test_book_settings_replace_does_not_wipe_page_setup(self):
        draft = self.project.documents()[0]
        self.project.set_page_setup(draft.id, _six_by_nine())
        self.project.set_book_settings(draft.id, {"template": "print"})
        self.assertEqual(self.project.page_setup(draft.id)["width"], 6.0)


class PageSetupExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Project.create(Path(self.temporary.name) / "Novel", "Novel")
        self.root = self.project.documents()[0]
        self.chapter = self.project.add_document(
            "Chapter", self.root.id, "chapter")
        self.project.write_content(self.chapter.id, "<p>Body text.</p>")
        self.destination = Path(self.temporary.name) / "book.docx"

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def _export(self):
        self.project.export_docx(self.destination)
        return zipfile.ZipFile(self.destination)

    def test_export_stamps_trim_margins_gutter_twips(self):
        self.project.set_page_setup(self.root.id, _six_by_nine())
        with self._export() as archive:
            sections = _sections(archive)
            self.assertTrue(sections)
            for section in sections:
                page = section.find(f"{W}pgSz")
                margins = section.find(f"{W}pgMar")
                self.assertEqual(page.get(f"{W}w"), _twips(6.0))
                self.assertEqual(page.get(f"{W}h"), _twips(9.0))
                self.assertEqual(margins.get(f"{W}top"), _twips(0.75))
                self.assertEqual(margins.get(f"{W}bottom"), _twips(0.75))
                self.assertEqual(margins.get(f"{W}left"), _twips(0.5))
                self.assertEqual(margins.get(f"{W}right"), _twips(0.25))
                self.assertEqual(margins.get(f"{W}gutter"), _twips(0.25))

    def test_export_adds_mirror_margins_and_clears_them_without(self):
        self.project.set_page_setup(self.root.id, _six_by_nine())
        with self._export() as archive:
            settings = archive.read("word/settings.xml")
        self.assertIn(b"mirrorMargins", settings)

        self.project.set_page_setup(self.root.id, None)
        with self._export() as archive:
            settings = archive.read("word/settings.xml")
        self.assertNotIn(b"mirrorMargins", settings)

    def test_every_section_of_a_page_start_book_gets_the_geometry(self):
        # The mark sits on the *book*, so it also exercises Phase 1's
        # inheritance: the chapters below it resolve to "right" without
        # each carrying the setting. The first document cannot take a
        # section — nothing precedes it — so a prologue goes first.
        prologue = self.project.add_document("Prologue", self.root.id, "chapter")
        self.project.write_content(prologue.id, "<p>Before the mark.</p>")
        self.project.update_document(self.root.id, page_break="right")
        self.project.set_page_setup(self.root.id, _six_by_nine())
        with self._export() as archive:
            sections = _sections(archive)
            # The root folder is an exported unit now, so the geometry is
            # stamped on every section that follows it as well.
            self.assertEqual(len(sections), 3)
            for section in sections:
                page = section.find(f"{W}pgSz")
                self.assertEqual(page.get(f"{W}w"), _twips(6.0))

    def test_every_document_starts_on_a_new_page_by_default(self):
        """Phase 1's baseline, and the author's own ask: with no templates and
        no settings, each exported document begins on its own page — the first
        one excepted, there being nothing above it."""
        root = self.project.documents()[0]
        for index in range(3):
            chapter = self.project.add_document(f"Chapter {index}", root.id,
                                                "chapter")
            self.project.write_content(chapter.id, f"<p>Body {index}.</p>")

        destination = Path(self.temporary.name) / "baseline.docx"
        self.project.export_docx(destination)
        with zipfile.ZipFile(destination) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
        # Every heading unit after the first opens a page — asserted against
        # the units actually printed, not against the fixture's shape. (No
        # automatic title or contents page: the writer designs those.)
        units = len(re.findall(r'w:val="Heading[12]"', xml))
        self.assertGreater(units, 1)
        self.assertEqual(xml.count('w:type="page"'), units - 1)

    def test_continuous_flow_runs_on_without_breaks(self):
        root = self.project.documents()[0]
        for index in range(3):
            chapter = self.project.add_document(f"Chapter {index}", root.id,
                                                "chapter")
            self.project.write_content(chapter.id, f"<p>Body {index}.</p>")
        # Set on the book: the descendants inherit "flow" (Phase 1).
        self.project.update_document(root.id, page_break="flow")

        destination = Path(self.temporary.name) / "flow.docx"
        self.project.export_docx(destination)
        with zipfile.ZipFile(destination) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
        # Continuous flow: no page breaks at all — no automatic title or
        # contents page, and the body runs on as "continuous flow" promises.
        self.assertEqual(xml.count('w:type="page"'), 0)

    def test_chapters_open_recto_once_a_trim_is_set(self):
        """Phase 1: chapters demand a recto opening only when the book has a
        real print trim — a plain A4 export should not sprout blank pages."""
        root = self.project.documents()[0]
        for index in range(2):
            chapter = self.project.add_document(f"Chapter {index}", root.id,
                                                "chapter")
            self.project.write_content(chapter.id, f"<p>Body {index}.</p>")

        destination = Path(self.temporary.name) / "no-trim.docx"
        self.project.export_docx(destination)
        with zipfile.ZipFile(destination) as archive:
            plain = archive.read("word/document.xml").decode("utf-8")
        self.assertEqual(plain.count('w:val="oddPage"'), 0)

        self.project.set_page_setup(root.id, _six_by_nine())
        destination = Path(self.temporary.name) / "trim.docx"
        self.project.export_docx(destination)
        with zipfile.ZipFile(destination) as archive:
            recto = archive.read("word/document.xml").decode("utf-8")
        self.assertGreaterEqual(recto.count('w:val="oddPage"'), 1)

    def test_html_marks_a_new_page_between_units(self):
        root = self.project.documents()[0]
        for index in range(3):
            chapter = self.project.add_document(f"Chapter {index}", root.id,
                                                "chapter")
            self.project.write_content(chapter.id, f"<p>Body {index}.</p>")

        destination = Path(self.temporary.name) / "baseline.html"
        self.project.export_html(destination)
        html = destination.read_text(encoding="utf-8")
        self.assertEqual(html.count("break-before: page;"),
                         html.count("class='unit'") - 1)

    def test_export_without_a_setup_gets_the_a4_baseline(self):
        """Phase 1: no page setup means A4 (unmirrored), not Letter — Letter
        was python-docx's template, never a decision we made."""
        with self._export() as archive:
            settings = archive.read("word/settings.xml")
            page = _sections(archive)[0].find(f"{W}pgSz")
        self.assertNotIn(b"mirrorMargins", settings)
        self.assertAlmostEqual(int(page.get(f"{W}w")) / 1440, 8.27, places=2)

    def test_nearest_ancestor_wins_over_a_parent_book(self):
        book = self.project.add_document("Book Two", self.root.id, "folder")
        scene = self.project.add_document(
            "Scene", book.id, "scene")
        self.project.write_content(scene.id, "<p>Other body.</p>")
        self.project.set_page_setup(self.root.id, _six_by_nine())
        self.project.set_page_setup(book.id, {"trim": "a5", "width": 5.83,
                                              "height": 8.27, "top": 0.5,
                                              "bottom": 0.5, "inside": 0.5,
                                              "outside": 0.5, "mirror": False,
                                              "gutter": 0.0})

        # Whole-project export: binder order reaches the root-level
        # chapter first, so the root's 6×9 governs the one file.
        with self._export() as archive:
            page = _sections(archive)[0].find(f"{W}pgSz")
            self.assertEqual(page.get(f"{W}w"), _twips(6.0))

        # Exporting only the inner book: its own A5 is nearest.
        self.project.export_docx(self.destination, {book.id})
        with zipfile.ZipFile(self.destination) as archive:
            page = _sections(archive)[0].find(f"{W}pgSz")
            self.assertEqual(page.get(f"{W}w"), _twips(5.83))

    def test_landscape_geometry_keeps_the_orientation_flag(self):
        self.project.set_page_setup(self.root.id, _six_by_nine(
            width=9.0, height=6.0, trim="custom"))
        with self._export() as archive:
            page = _sections(archive)[0].find(f"{W}pgSz")
        self.assertEqual(page.get(f"{W}orient"), "landscape")
        self.assertEqual(page.get(f"{W}w"), _twips(9.0))

    def test_defaults_and_labelled_presets_agree(self):
        self.assertEqual(DEFAULT_PAGE_SETUP["trim"], "a4")
        self.assertEqual(PAGE_TRIMS["a4"][1:], (8.27, 11.69))
        self.assertEqual(clean_page_setup(dict(DEFAULT_PAGE_SETUP))["width"],
                         8.27)


if __name__ == "__main__":
    unittest.main()
