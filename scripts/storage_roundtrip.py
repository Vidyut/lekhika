#!/usr/bin/env python3
"""Round-trip check: does the folder tree hold everything the database does?

Read-only against the project you point it at (docs/STORAGE.md step 2). It
copies the project into a temporary directory first, opens *that*, writes the
tree, reads it back and compares every document field by field — so the
author's real manuscript is never opened for writing, never migrated, never
touched.

Two questions are answered, and they are different ones:

1. **Does what comes back equal what went in?** A field that is written and
   read back differently is a bug in the layout.
2. **Is anything in the database that the layout has no home for?** Nothing
   fails this — it is a list of what a migration would silently drop, which
   is exactly the list that has to be empty (or consciously accepted) before
   the flat pool is retired.

    python3 scripts/storage_roundtrip.py "/path/to/Project.lekhika"
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from lekhika.layout import has_prose
from lekhika.project import Project, clean_page_setup

# Session state about the writer's machine, not about the manuscript: it
# travels with the app, not with a copied folder. Listed so the report can
# say "not carried, deliberately" rather than "not carried, noticed late".
SESSION_SETTINGS = {
    "editor_zoom", "last_cursor", "last_document",
    "draft_folder_migration", "word_count_backfill",
}

# One-time flags that only mean something to the database that set them.
FLAG_SETTINGS = {"templates_area_created"}


def flatten(nodes):
    """Every node, depth-first, in binder order."""
    out = []
    for node in nodes:
        out.append(node)
        out.extend(flatten(node.children))
    return out


def copy_project(source: Path, destination: Path) -> None:
    """A working copy: the database and the prose, without the bulk.

    ``backups/`` and ``documents``' siblings are left behind on purpose —
    neither is read by the migration, and a project's backups can be large.

    The write-ahead log and its shared-memory file come along if the
    project has them: the database is opened in WAL mode, so a copy of the
    main file alone can be missing everything committed since the last
    checkpoint, and a comparison against stale data is worse than no
    comparison at all.
    """
    for suffix in ("", "-wal", "-shm"):
        sidecar = source / f"project.sqlite3{suffix}"
        if sidecar.exists():
            shutil.copy2(sidecar, destination / f"project.sqlite3{suffix}")
    documents = source / "documents"
    if documents.exists():
        shutil.copytree(documents, destination / "documents")


def integrity(project: Project) -> str:
    """``PRAGMA integrity_check`` — the answer before any comparison.

    A corrupt index makes index-backed queries return phantom rows, which
    reads as a bug in the layout rather than in the database (the
    author's project reported 334 documents against a table holding 332,
    and one folder listed as its own child three times). Comparing
    against that would blame the wrong thing, so the check comes first.
    """
    return str(project.db.execute("PRAGMA integrity_check").fetchone()[0])


def database_rows(project: Project) -> tuple[dict, dict, list[str]]:
    """What the database holds, keyed for comparison.

    Returns (documents by id, metadata by id, the ordered root-to-leaf id
    path of every document) so the tree can be walked against the binder's
    own order rather than against a sorted list.
    """
    documents = {document.id: document for document in project.documents()}
    metadata = project.metadata_entries_all()
    order: list[str] = []

    def walk(parent_id) -> None:
        children = sorted((d for d in documents.values()
                           if d.parent_id == parent_id),
                          key=lambda item: item.position)
        for child in children:
            order.append(child.id)
            walk(child.id)

    walk(None)
    return documents, metadata, order


def compare(project: Project, nodes) -> tuple[list[str], list[str]]:
    """Field-by-field: what the database says against what came back."""
    stored, metadata, order = database_rows(project)
    returned = flatten(nodes)
    problems: list[str] = []

    if len(returned) != len(order):
        problems.append(
            f"count: {len(order)} documents in the database, "
            f"{len(returned)} read back from the tree")
    if [node.id for node in returned] != order:
        problems.append("binder order or identity differs after the round trip")

    by_id = {node.id: node for node in returned}
    for document_id in order:
        document = stored[document_id]
        node = by_id.get(document_id)
        if node is None:
            problems.append(f"missing: {document.title!r} ({document_id[:8]})")
            continue
        label = f"{document.title!r}"
        if node.title != document.title:
            problems.append(f"{label}: title {document.title!r} -> {node.title!r}")
        if node.synopsis != document.synopsis:
            problems.append(f"{label}: synopsis changed")
        if node.word_target != document.word_target:
            problems.append(f"{label}: word_target "
                            f"{document.word_target} -> {node.word_target}")
        if node.type != project.type_of(document):
            problems.append(f"{label}: type {project.type_of(document)!r} "
                            f"-> {node.type!r}")

        # The folder-scoped field the flat schema had and an earlier tree
        # had nowhere to put: it is checked like any other field now,
        # which is the point of giving it a home — "carried" that nothing
        # verifies is just a claim. (The template mark that used to travel
        # beside it is gone: a template is a folder under templates/.)
        expected_geometry = clean_page_setup(
            project._page_setup_map().get(document_id))
        if expected_geometry != node.page_setup:
            problems.append(f"{label}: page_setup {expected_geometry} "
                            f"-> {node.page_setup}")

        # Type is the node's own field, so it is not in metadata on either
        # side; everything else must survive as text, key for key.
        rows = metadata.get(document_id, {})
        expected = {key: str(value[0]) for key, value in rows.items()
                    if key != "Type"}
        if node.metadata != expected:
            problems.append(f"{label}: metadata {expected} -> {node.metadata}")

        # A container's words live in index.html, and only when they are
        # words: a title typed as a heading is not prose (layout.has_prose).
        container = document.kind == "folder" or bool(
            [d for d in stored.values() if d.parent_id == document_id])
        content = project.read_content(document_id)
        if container and not has_prose(content, document.title):
            content = ""
        if node.content != content:
            problems.append(f"{label}: content changed "
                            f"({len(content)} -> {len(node.content)} bytes)")

    # Anything the layout never wrote is not a difference in the round trip;
    # it is a field with nowhere to go. Reported below, not here.
    return problems, order


def uncaptured(project: Project) -> list[str]:
    """Database fields the layout has no home for — the silent-drop list.

    A round trip can only compare what it carries, so this reads the schema
    directly: a column nobody writes is invisible to the comparison above,
    and would be lost by a migration without anything failing.
    """
    findings: list[str] = []
    columns = {row[1] for row in project.db.execute(
        "PRAGMA table_info(documents)")}
    # What Project.to_nodes / layout.Node actually carry.
    carried = {"id", "parent_id", "title", "synopsis", "position",
               "word_target", "kind"}
    for column in sorted(columns - carried):
        if column in {"word_count", "created_at", "updated_at"}:
            # word_count is recomputed on save; the timestamps are the
            # database's own bookkeeping. Neither belongs in a file.
            continue
        row = project.db.execute(
            f"SELECT count(*) FROM documents "
            f"WHERE {column} NOT IN ('', 'auto', '0')").fetchone()
        count = int(row[0])
        findings.append(f"documents.{column}: not carried "
                        f"({count} row(s) would lose it)")

    metadata_columns = {row[1] for row in project.db.execute(
        "PRAGMA table_info(document_metadata)")}
    for column in sorted(metadata_columns
                         - {"document_id", "key", "value"}):
        row = project.db.execute(
            f"SELECT count(*) FROM document_metadata "
            f"WHERE {column} NOT IN ('', 'text', '[]')").fetchone()
        count = int(row[0])
        findings.append(f"document_metadata.{column}: not carried "
                        f"({count} row(s) would lose it)")

    # "template_set" is absent on purpose: the export pick is one-shot
    # (docs/TEMPLATES.md §2.1) and open() retires the old key, so a
    # project cannot carry a preference the writer never made.
    carried_settings = {"title", "link_policy",
                        "project_metadata", "page_setup"}
    for (key,) in project.db.execute(
            "SELECT key FROM settings ORDER BY key"):
        if key in carried_settings or key in SESSION_SETTINGS:
            continue
        if key in FLAG_SETTINGS:
            continue
        findings.append(f"settings.{key}: not carried")
    return findings


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    source = Path(sys.argv[1]).expanduser()
    if not (source / "project.sqlite3").exists():
        raise SystemExit(f"no project.sqlite3 in {source}")

    # Everything happens on copies. The point of this script is that the
    # real project can be pointed at without a second thought.
    with tempfile.TemporaryDirectory(prefix="lekhika-roundtrip-") as temporary:
        scratch = Path(temporary)
        work = scratch / source.name
        work.mkdir()
        copy_project(source, work)

        project = Project(work)
        project.open()
        try:
            check = integrity(project)
            before = {document.id: (document.title, document.synopsis,
                                    document.word_target)
                      for document in project.documents()}
            tree = scratch / "tree"
            stats = project.write_tree(tree)
            nodes, manifest = project.read_tree(tree)
            problems, order = compare(project, nodes)
            missing = uncaptured(project)
        finally:
            project.close()

        # The working copy must not have drifted either: a comparison that
        # changed the data it was checking would be worthless.
        reopened = Project(work)
        reopened.open()
        try:
            after = {document.id: (document.title, document.synopsis,
                                   document.word_target)
                     for document in reopened.documents()}
        finally:
            reopened.close()

    print(f"project: {source}")
    print(f"documents: {len(order)}  "
          f"folders: {stats['folders']}  documents: {stats['documents']}  "
          f"index.html: {stats['indexes']}")
    print(f"project title in manifest: {manifest.get('title')!r}")

    print("\n=== database integrity ===")
    if check == "ok":
        print("ok")
    else:
        # Not a failure of the round trip: a comparison against a corrupt
        # index would report phantom rows as layout bugs, so this is said
        # first and the exit code follows it.
        print(f"{check}")
        print("  run REINDEX on a copy first — the layout cannot be judged "
              "through a bad index")

    print("\n=== round trip: database -> tree -> database ===")
    if problems:
        print(f"{len(problems)} problem(s):")
        for problem in problems[:40]:
            print(f"  {problem}")
        if len(problems) > 40:
            print(f"  … and {len(problems) - 40} more")
    else:
        print("every document matches: id, title, type, synopsis, target, "
              "metadata, content and order")
    if before != after:
        print("  the working copy changed while it was being compared")

    print("\n=== fields the layout has nowhere to put ===")
    if missing:
        print(f"{len(missing)} — a migration would drop these silently:")
        for item in missing:
            print(f"  {item}")
    else:
        print("none: everything in the database has a home in the tree")

    return 1 if problems or before != after or check != "ok" else 0


if __name__ == "__main__":
    raise SystemExit(main())
