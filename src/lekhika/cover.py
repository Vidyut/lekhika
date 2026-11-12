"""Wraparound cover composer (KDP print copies, docs/BUILD-NOW.md).

The inner copy is a separate file. This page — the outer sheet's one
page — is one wide page: back, spine and front, trim plus bleed, with
a tidy margin grid drawn over it so a writer can see at a glance what
will survive the trim. The sheet is typeset (reportlab), not carried
by a word processor: a cover is a placed rectangle, and the geometry
must be exact to the millimetre.

The spine needs the final page count, and nothing can know it before
the manuscript exists. What can: the words. The count is estimated
from the selected text at a fixed words-per-page for the trim the
book uses (a fair centre of the range; every layout shifts it a
little, and KDP's own tolerance absorbs a few pages either way).
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

# Words per page for the estimate, and KDP's per-page thickness.
WORDS_PER_PAGE = 250
PAGE_THICKNESS_INCHES = 0.002252     # per page, black and white
BLEED_INCHES = 0.125                 # KDP per-side bleed


def estimate_pages(project: object, documents: list) -> int:
    """Pages the inner copy would reach, from the text alone.

    Whole-document word counts divided by words-per-page, rounded up.
    An estimate — a packed layout or a loose one shifts the spine a
    little either way, and KDP's tolerance absorbs a few pages of it.
    """
    total_words = sum(
        len(project.plain_text(project.read_content(document.id)).split())
        for document in documents)
    return max(1, math.ceil(total_words / WORDS_PER_PAGE))


def spine_width_inches(page_count: int) -> float:
    """Spine for KDP's per-page thickness rule."""
    return max(0.1, page_count * PAGE_THICKNESS_INCHES)


def compute_sizes(trim: tuple[float, float], page_count: int) -> dict:
    """The sheet's numbers, named — so a preview or a test can look."""
    bleed = BLEED_INCHES * 25.4
    spine = spine_width_inches(page_count) * 25.4
    page_width = trim[0] * 2 + spine + 2 * bleed
    page_height = trim[1] + 2 * bleed
    safe = 6.0
    # zones in millimetres, measured from the sheet's top-left
    back_start = bleed
    back_end = bleed + trim[0]
    spine_end = back_end + spine
    sheet_end = page_width - bleed
    return {
        "page_width": page_width,
        "page_height": page_height,
        "bleed": bleed,
        "spine": spine,
        "trim": trim,
        "zones": {
            "back": (back_start, back_end),
            "spine": (back_end, spine_end),
            "front": (spine_end, sheet_end),
        },
        # safe zone: inside the trim by 6 mm on each side
        "safe": (back_start + safe, bleed + safe,
                 sheet_end - safe, page_height - bleed - safe),
    }


MM_TO_POINTS = 72 / 25.4  # mm in points (72 dpi)


def _draw_grid(image, page_width: float, page_height: float,
               zones: dict, safe: tuple[float, float, float, float]) -> None:
    """Light margin grid over the wraparound sheet."""
    from PIL import Image, ImageDraw
    dpi = 72
    width = max(1, int(round(page_width * dpi / 25.4)))
    height = max(1, int(round(page_height * dpi / 25.4)))
    art = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(art)

    def px(mm: float) -> int:
        return int(round(mm * 72 / 25.4))

    for edge in ("back", "spine", "front"):
        left, right = zones[edge]
        draw.line([(px(left), 0), (px(left), height)],
                  fill=(255, 90, 90), width=1)
        draw.line([(px(right), 0), (px(right), height)],
                  fill=(255, 90, 90), width=1)
    left, top, right, bottom = safe
    draw.rectangle([px(left), px(top), px(right), px(bottom)],
                   outline=(255, 90, 90), width=1)
    art.save(image)


def compose_cover_pdf(project, selected_ids,
                      trim: tuple[float, float],
                      cover: Path | None,
                      output: Path,
                      page_count: int | None = None) -> Path:
    """Compose the wraparound cover as a one-page PDF.

    The cover art fills the whole sheet (bleed to bleed); a light
    grid showing the trim edges, spine seams and safe zone is drawn
    over it. The PDF carries exactly one page — this, not document
    flow, is what a cover wrap is.
    """
    documents = project._export_documents(selected_ids)
    wanted = page_count or estimate_pages(project, documents)
    sizes = compute_sizes(trim, wanted)
    page_width, page_height = sizes["page_width"], sizes["page_height"]
    spine = sizes["spine"]

    from reportlab.pdfgen import canvas

    art_path = Path(cover) if cover else None
    with tempfile.TemporaryDirectory() as temp:
        grid = Path(temp) / "grid.png"
        _draw_grid(image=grid, page_width=page_width,
                   page_height=page_height, zones=sizes["zones"],
                   safe=sizes["safe"])
        document = canvas.Canvas(str(output), pagesize=(
            page_width * MM_TO_POINTS, page_height * MM_TO_POINTS))
        if art_path is not None and art_path.is_file():
            document.drawImage(
                str(art_path), 0, 0,
                page_width * MM_TO_POINTS, page_height * MM_TO_POINTS,
                preserveAspectRatio=False, mask=None)
        document.drawImage(
            str(grid), 0, 0,
            page_width * MM_TO_POINTS, page_height * MM_TO_POINTS,
            preserveAspectRatio=False, mask=None)
        document.showPage()
        document.save()
    return output
