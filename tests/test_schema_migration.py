"""The two flags of the binder-template systems, retired (docs/TEMPLATES.md).

``is_templates_root`` marked a binder *templates area* (2.1); the mark that
replaced it, ``is_template``, flagged a folder as a template. Both systems
are gone: a template is a folder under ``<project>/templates/``, holding
files rather than binder documents. So an old project opens with both
columns dropped, and nothing else about it moves — the flags were the whole
of those systems, and the new model reads neither.

An old SQLite that cannot drop a column (pre-3.35) keeps it instead; the
migration tolerates that, so both outcomes are asserted here: on a
current SQLite the columns are gone, and on an old one the project still
opens with the leftover column never selected.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from lekhika.project import Project


class _NoDropConnection(sqlite3.Connection):
    """A connection from before SQLite 3.35: DROP COLUMN is syntax."""

    def execute(self, sql, *args, **kwargs):
        if isinstance(sql, str) and "DROP COLUMN" in sql.upper():
            raise sqlite3.OperationalError('near "COLUMN": syntax error')
        return super().execute(sql, *args, **kwargs)


class _OldSQLite:
    """``lekhika.project``'s view of the sqlite3 module, aged down.

    Every name the project module borrows forwards to the real module;
    only ``connect`` differs, handing back the connection above.
    """

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def connect(self, *args, **kwargs):
        kwargs.setdefault("factory", _NoDropConnection)
        return self._real.connect(*args, **kwargs)


@contextmanager
def _old_sqlite():
    with mock.patch("lekhika.project.sqlite3", _OldSQLite(sqlite3)):
        yield


class DeadTemplateFlagTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = Project.create(self.root / "Novel", "Novel")
        self.path = self.project.root
        self._build_shape()
        self._downgrade_to_old_schema()

    def tearDown(self):
        self.project.close()
        self.temporary.cleanup()

    def _build_shape(self):
        draft = self.project.documents()[0]
        area = self.project.add_document("Templates", draft.id, "folder")
        page = self.project.add_document("Title page", area.id, "chapter")
        self.project.write_content(page.id, "<p>Title page text.</p>")
        book = self.project.add_document("Book", draft.id, "folder")
        self.draft, self.area, self.book = draft, area, book

    def _downgrade_to_old_schema(self):
        """Rewrite the just-created database the way the old app left it:
        both flags present, one of them set on the old area folder."""
        self.project.db.execute(
            "ALTER TABLE documents ADD COLUMN is_templates_root"
            " INTEGER NOT NULL DEFAULT 0")
        self.project.db.execute(
            "ALTER TABLE documents ADD COLUMN is_template"
            " INTEGER NOT NULL DEFAULT 0")
        self.project.db.execute(
            "UPDATE documents SET is_templates_root=1, is_template=1"
            " WHERE id=?", (self.area.id,))
        self.project.db.commit()

    def _reopen(self) -> Project:
        self.project.close()
        project = Project(self.path)
        project.open()
        return project

    @staticmethod
    def _columns(project) -> set:
        return {row[1] for row in project.db.execute(
            "PRAGMA table_info(documents)")}

    def _check_flags_gone(self, project) -> None:
        columns = self._columns(project)
        self.assertNotIn("is_templates_root", columns)
        self.assertNotIn("is_template", columns)

    def test_both_flags_are_dropped_and_the_documents_stay(self):
        project = self._reopen()
        try:
            self._check_flags_gone(project)
            titles = {d.title for d in project.documents()}
            self.assertEqual(titles, {"Draft", "Templates", "Title page",
                                      "Book"})
            self.assertEqual(project.read_content(
                next(d.id for d in project.documents()
                     if d.title == "Title page")),
                "<p>Title page text.</p>")
        finally:
            project.close()

    def test_a_folder_that_was_marked_is_ordinary_now(self):
        # The flag was the only thing that made it a template; with it
        # dropped the folder is manuscript, and exports say so — templates
        # are folders under templates/ (§1.1), which hold files, not
        # binder documents that could ever reach an export.
        project = self._reopen()
        try:
            destination = self.root / "book.txt"
            project.export_text(destination)
            self.assertIn("Title page text.",
                          destination.read_text(encoding="utf-8"))
        finally:
            project.close()

    def test_the_migration_runs_once(self):
        project = self._reopen()
        try:
            # A second open changes nothing: the columns are gone, so the
            # branch cannot fire again and no error is raised trying.
            self._check_flags_gone(project)
        finally:
            project.close()
        again = Project(self.path)
        again.open()
        try:
            self._check_flags_gone(again)
        finally:
            again.close()

    def test_a_brand_new_project_is_never_given_the_columns(self):
        # The schema no longer declares them, so a fresh database has
        # neither — the migration exists only for projects that predate
        # this, and never adds back what it just removed.
        fresh = Project.create(self.root / "Fresh", "Fresh")
        try:
            self._check_flags_gone(fresh)
        finally:
            fresh.close()

    def test_an_old_database_without_sqlite_drop_still_opens(self):
        # Pre-3.35 SQLite cannot drop a column (the migration's own
        # fallback): the project must open anyway, with the leftover
        # column simply never selected.
        with _old_sqlite():
            project = self._reopen()
            try:
                self.assertIn("is_template", self._columns(project))
                rows = project.documents()
                self.assertTrue(rows)
                self.assertEqual(
                    {d.title for d in rows},
                    {"Draft", "Templates", "Title page", "Book"})
            finally:
                project.close()


if __name__ == "__main__":
    unittest.main()
