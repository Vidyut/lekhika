"""The guide: the software's own manual, as a project.

It ships with the app and opens where it ships — read-only, so nothing
is copied into the writer's disk and nothing is written back into the
install folder. A writer who wants their own version takes a copy, and
that copy is writable.
"""

from __future__ import annotations

import glob
import tempfile
import tomllib
import unittest
from pathlib import Path

from lekhak import guide
from lekhak.project import Project
from lekhak.templates import SHIPPED_TEMPLATE_SETS, discover_template_sets


class GuideTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_the_guide_ships_with_the_software(self):
        source = guide.source_project()
        self.assertIsNotNone(source)
        # It is a project: the index, the manifest, and the book itself.
        self.assertTrue((source / "project.sqlite3").is_file())
        self.assertTrue((source / "manifest.json").is_file())
        self.assertTrue((source / "The Lekhak Guide").is_dir())

    def test_the_guide_is_not_copied_anywhere(self):
        # A program that leaves files behind is a program people try once.
        self.assertEqual(guide.guide_project(), guide.source_project())

    def test_opening_the_guide_writes_nothing(self):
        source = guide.source_project()
        before = {path: path.stat().st_mtime
                  for path in source.rglob("*") if path.is_file()}
        project = Project(source, read_only=True)
        project.open()
        try:
            self.assertTrue(project.documents())
            # Reading is reading: nothing was written, not the prose, not
            # the index, not the order files.
            project.set_setting("anything", "value")
            project.write_order_files()
            project.write_content(project.documents()[0].id, "<p>No.</p>")
        finally:
            project.close()
        after = {path: path.stat().st_mtime
                 for path in source.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_a_read_only_project_refuses_to_be_written(self):
        source = guide.source_project()
        project = Project(source, read_only=True)
        project.open()
        try:
            book = project.documents()[0]
            before = project.metadata(book.id)
            project.update_document(book.id, title="Something else")
            self.assertEqual(project.metadata(book.id), before)
            self.assertFalse(project.reorder(book.id, None, 0))
        finally:
            project.close()

    def test_the_guide_carries_no_lock_marker(self):
        # No lock file in the shipped guide: a marker would travel with
        # every copy of it — the writer's own copy included — and lock
        # that too. The read-only open is what keeps the guide read-only.
        source = guide.source_project()
        self.assertFalse((source / "The Lekhak Guide" / ".locked").exists())
        project = Project(source, read_only=True)
        project.open()
        try:
            book = project.documents()[0]
            # Locked in the only way that matters: the project is opened
            # read-only, so nothing in it can be written.
            self.assertTrue(project.read_only)
            self.assertFalse(project.is_locked(book.id))
        finally:
            project.close()

    def test_the_guides_own_templates_sit_beside_the_shipped_ones(self):
        # The guide's set now ships with Lekhak — it is the worked
        # example of a set dressed by a manual's own Types — and the
        # read-only guide still carries a folder copy of it, exactly as
        # it carries a copy of every shipped set. The shipped one comes
        # first and has no folder; the copy is listed after it as a set
        # of its own, so a reader of the read-only guide can read its
        # fragments and its CSS too.
        source = guide.source_project()
        sets = discover_template_sets(source)
        names = [template_set.name for template_set in sets]
        self.assertEqual(names[:5], list(SHIPPED_TEMPLATE_SETS))
        shipped = sets[names.index("Guide")]
        self.assertIsNone(shipped.directory)
        self.assertFalse(shipped.editable)
        folder_copy = [t for t in sets if t.name == "Guide"][1]
        self.assertEqual(folder_copy.directory.parent,
                         source / "templates")
        self.assertTrue((folder_copy.directory / "documents"
                         / "section.html").is_file())
        self.assertTrue((folder_copy.directory / "style.css").is_file())

    def test_every_file_of_the_guide_reaches_the_package(self):
        # The package-data patterns are one folder per `*`, and a glob's
        # `*` never crosses a separator: miss a level and that level's
        # pages — and the guide's own face — never reach the wheel, where
        # they cannot be read because they are not there at all.
        source = guide.source_project()
        package = source.parents[1]                  # the lekhak package
        pyproject = package.parent.parent / "pyproject.toml"
        if not pyproject.is_file():
            self.skipTest("no repository around to read it from")
        patterns = tomllib.loads(
            pyproject.read_text(encoding="utf-8")
        )["tool"]["setuptools"]["package-data"]["lekhak"]
        covered = {Path(match) for pattern in patterns
                   for match in glob.glob(str(package / pattern))}
        files = {path for path in source.rglob("*") if path.is_file()}
        missing = sorted(files - covered)
        self.assertEqual(
            missing[:3], [],
            f"{len(missing)} of {len(files)} guide files are not in "
            f"package-data — the wheel leaves them behind.")

    def test_a_copy_is_writable(self):
        # "Save as" is the ordinary gesture: a copy of the guide is an
        # ordinary project, writable, because being read-only is a fact
        # about how a project was opened and not about its files.
        source = guide.source_project()
        project = Project(source, read_only=True)
        project.open()
        try:
            copy = project.copy_to(self.root / "Mine.lekhak")
        finally:
            project.close()
        copy_project = Project(copy)
        try:
            copy_project.open()
            book = copy_project.documents()[0]
            copy_project.update_document(book.id, title="My guide")
            self.assertEqual(copy_project.documents()[0].title, "My guide")
        finally:
            copy_project.close()

    def test_the_shipped_copy_can_be_edited_in_the_development_checkout(self):
        # The guide in the repository is a regular project: the author
        # opens it and writes it in Lekhak, and the build ships it.
        source = guide.source_project()
        project = Project(source)
        project.open()
        try:
            self.assertFalse(project.read_only)
            self.assertTrue(project.documents())
        finally:
            project.close()


if __name__ == "__main__":
    unittest.main()