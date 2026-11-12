#!/usr/bin/env python3
"""Bake packaging/lekhika.svg into the icon artifacts Lekhika ships.

Produces:
  packaging/lekhika.png   — 256 px PNG (hicolor fallback)
  packaging/lekhika.ico   — Windows ICO (16…256, PNG-packed)
  --icns PATH            — macOS .icns (needs iconutil, macOS only)

Requires PySide6 (Lekhika's own dependency). Runs headless.
Re-run this whenever packaging/lekhika.svg changes.
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "packaging" / "lekhika.svg"
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
ICONSET_SIZES = {
    16: ["16x16", "16x16@2x"],
    32: ["32x32", "32x32@2x"],
    64: ["64x64"],
    128: ["128x128", "128x128@2x"],
    256: ["256x256", "256x256@2x"],
    512: ["512x512", "512x512@2x"],
}


def render_png(renderer: QSvgRenderer, size: int) -> bytes:
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def write_ico(path: Path, pngs: dict[int, bytes]) -> None:
    """ICO container with PNG-compressed images (supported since Vista)."""
    entries = [pngs[size] for size in sorted(pngs)]
    sizes = sorted(pngs)
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    directory = b""
    for size, data in zip(sizes, entries):
        dim = 0 if size >= 256 else size
        directory += struct.pack(
            "<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset
        )
        offset += len(data)
    path.write_bytes(header + directory + b"".join(entries))


def write_icns(path: Path, renderer: QSvgRenderer) -> None:
    if os.name != "posix" or sys.platform != "darwin":
        raise SystemExit("--icns requires macOS (iconutil)")
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Lekhika.iconset"
        iconset.mkdir()
        for size, names in ICONSET_SIZES.items():
            data = render_png(renderer, size)
            for name in names:
                (iconset / f"{name}.png").write_bytes(data)
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(path)],
            check=True,
        )


def main() -> int:
    if not SVG.exists():
        raise SystemExit(f"missing {SVG}")
    renderer = QSvgRenderer(str(SVG))
    if not renderer.isValid():
        raise SystemExit("SVG failed to load")

    icns_path = None
    if "--icns" in sys.argv:
        icns_path = Path(sys.argv[sys.argv.index("--icns") + 1])

    png_256 = render_png(renderer, 256)
    (ROOT / "packaging" / "lekhika.png").write_bytes(png_256)
    write_ico(ROOT / "packaging" / "lekhika.ico",
              {size: render_png(renderer, size) for size in ICO_SIZES})
    print("wrote packaging/lekhika.png and packaging/lekhika.ico")

    if icns_path:
        write_icns(icns_path, renderer)
        print(f"wrote {icns_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
