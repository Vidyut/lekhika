"""Discovery: which sets a project offers (docs/TEMPLATES.md §1.1).

The sets are the folders on disk — one under the project's ``templates/``
root, one under the app library — plus the three that ship in code. This
is the only listing now that the Templates tab is an editor: the binder
shows the same sets, so these tests hold the list the binder is drawn
from.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from lekhak.templates import (
    TemplateSet,
    discover_template_sets,
    named_template_sets,
)


class DiscoveryTests(unittest.TestCase):
    """Discovery as §1.1 defines it: the project's templates/ folder, one
    level of subfolders, with the shipped sets on top."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        # Hermetic: the app library lives under XDG_CONFIG_HOME, and a
        # writer's real one must not leak into these expectations.
        self.xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(self.root / "config")

    def tearDown(self):
        if self.xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self.xdg
        self.temporary.cleanup()

    def _names(self):
        return [s.name for s in discover_template_sets(self.root)]

    def test_an_empty_project_has_exactly_the_shipped_sets(self):
        # §1.1: three sets ship with Lekhak and need no folder anywhere,
        # so an empty project still offers all of them, default first.
        self.assertEqual(self._names(),
                         ["default", "short story", "Book", "nothing"])

    def test_a_default_folder_is_listed_beside_the_shipped_default(self):
        # §1.1 flat, and §1.1's other half: the folder is a row of its
        # own, after the shipped three, under its folder name — so its
        # files are never hidden — while the top slot stays Lekhak's own,
        # the answer when nobody picked a set.
        directory = self.root / "templates" / "default"
        directory.mkdir(parents=True)
        (directory / "folder.html").write_text("<p>${Title}${Content}</p>",
                                                encoding="utf-8")
        sets = discover_template_sets(self.root)

        self.assertEqual(self._names(),
                         ["default", "short story", "Book", "nothing", "default"])
        self.assertIsNone(sets[0].directory)          # shipped, not the folder
        self.assertEqual(sets[0].slot("folder"),
                         TemplateSet.builtin("default").slot("folder"))
        owned = sets[4]
        self.assertEqual(owned.directory, directory)
        self.assertEqual(owned.slot("folder"), "<p>${Title}${Content}</p>")

    def test_the_name_default_always_resolves_to_the_shipped_set(self):
        # The name is what an assignment stores (§2), so it must keep
        # meaning Lekhak's own set — the folder is listed and pickable,
        # but it never answers to the name the shipped set already owns.
        directory = self.root / "templates" / "default"
        directory.mkdir(parents=True)
        (directory / "folder.html").write_text("<p>mine</p>",
                                                encoding="utf-8")

        named = named_template_sets(self.root)
        self.assertIsNone(named["default"].directory)
        self.assertEqual(named["default"].slot("folder"),
                         TemplateSet.builtin("default").slot("folder"))
        # The folder is still there, under its own directory — listed as
        # itself, and addressable by any name that is not the shipped one.
        self.assertIn(directory,
                      [s.directory for s in discover_template_sets(self.root)])

    def test_a_folder_shaped_like_a_shipped_name_keeps_its_own_name(self):
        # A capitalised `Default` is a different name entirely: the
        # shipped set holds `default`, and the folder is listed and
        # addressable as `Default` — one listing, two answers, no shadow.
        directory = self.root / "templates" / "Default"
        directory.mkdir(parents=True)

        self.assertEqual(self._names(),
                         ["default", "short story", "Book", "nothing", "Default"])
        named = named_template_sets(self.root)
        self.assertIsNone(named["default"].directory)
        self.assertEqual(named["Default"].directory, directory)

    def test_the_projects_own_folder_shadows_the_libraries_of_the_same_name(self):
        # The rule that did not change: the project's own folder shadows
        # the user's one of the same name — only a shipped set's slot is
        # beyond a folder's reach.
        (self.root / "config" / "lekhak" / "templates" / "Editorial"
         ).mkdir(parents=True)
        (self.root / "config" / "lekhak" / "templates" / "Editorial"
         / "folder.html").write_text("<p>library</p>", encoding="utf-8")
        (self.root / "templates" / "Editorial").mkdir(parents=True)
        (self.root / "templates" / "Editorial" / "folder.html").write_text(
            "<p>project</p>", encoding="utf-8")

        named = named_template_sets(self.root)
        self.assertEqual(named["Editorial"].slot("folder"), "<p>project</p>")

    def test_subfolders_beside_the_shipped_sets_are_listed_after_them(self):
        (self.root / "templates" / "Editorial").mkdir(parents=True)
        (self.root / "templates" / "Print").mkdir(parents=True)
        # The shipped sets keep the top, one slot each; a project's own
        # folders follow in their own order.
        self.assertEqual(self._names(), ["default", "short story", "Book",
                                         "nothing", "Editorial", "Print"])

    def test_files_in_templates_are_not_templates(self):
        (self.root / "templates").mkdir(parents=True)
        (self.root / "templates" / "notes.txt").write_text("x",
                                                           encoding="utf-8")
        self.assertEqual(self._names(),
                         ["default", "short story", "Book", "nothing"])


if __name__ == "__main__":
    unittest.main()
