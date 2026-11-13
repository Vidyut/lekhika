"""Built-in Lekhika guide project shipped with the application.

The guide opens read-only from its installed location and can be exported or saved as a copy.
"""

from __future__ import annotations

from pathlib import Path


def source_project() -> Path | None:
    """The guide as it ships, or None when the installation does not carry
    it — an incomplete installation still works."""
    path = Path(__file__).resolve().parent / "guide" / "guide.lekhika"
    return path if path.is_dir() else None


def guide_project() -> Path | None:
    """Return the shipped guide project path, or None if unavailable."""
    return source_project()
