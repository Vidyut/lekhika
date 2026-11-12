# Storage: the filesystem *is* the structure

Status: **decided** (author, 2026-10-25). This supersedes the "metadata is SQLite" half of the project-format working agreement in `ROADMAP.md`: metadata moves into the files, and SQLite becomes a cache that can be rebuilt by scanning.

## As built (2026-11-01)

The model is now the running application, not a plan.

- **The tree is on disk, in the author's own names.** A project is a folder with `manifest.json` at its root; every container is a directory; every document is an `.html` file inside it. Names keep their spaces — `A Fictional Book/A Chapter/1---A Meeting.html`.
- **The prose and its metadata live together.** Each file's head carries a YAML frontmatter block (title, synopsis, word target, kind, and the rest); the body is the prose. A file opened in any editor is complete on its own.
- **The database is the index.** `project.sqlite3` holds what the app needs to be fast, and each row carries the `path` of its own file — that column is the join between the two. `Project.sync_with_disk()` rebuilds the index by scanning, and picks up anything the writer added by hand.
- **The sequence is written twice, in two places that cannot lose it.** `.order.json` in each folder records that folder's children by name; `manifest.json` records the root's. Copy a folder anywhere and its order travels with it.
- **`migrate_to_tree.py`** converts a project made before this: it writes the tree from the old database and records the paths.

### As built (2026-11-02)

- **The binder is drawn from the files.** `Project.binder_tree()` walks the tree on disk and joins the index in for what only the index knows — the document's id, and the title the writer gave it. A folder or file the writer added by hand is in the binder from the moment it exists. The icon says what the thing *is* on disk — a directory is a folder, an `.html` file is a document — whatever role the row gives it, so a chapter holding scenes wears a folder's icon.
- **A container's own `index.html` is its prose, not a child of it.** The index never grows a second row for that file, and the binder never shows the same words twice.
- **A lock is a marker file in the tree.** `.locked` inside a folder locks that folder and everything inside it; `.<name>.locked` beside a file locks that file. The tick beside the Type row says whether the marker is there; the marker is the truth, and it travels with a copied folder.
- **A leaf that gains children moves before its child is placed.** The child's path is worked out *after* the promotion from `X.html` to the directory `X/`, so the two halves of one binder cannot end up in two trees. `repair_tree.py` puts an older split back together — it finds the ghost directories (a directory named like a file), moves their contents where they belong, and takes a backup before it touches anything.

The rest of this document is the design record — the reasoning, the decisions and the experiments that led here. Where it describes the planned `.lekhak/` machinery, read it as the proposal that the simpler `manifest.json` + `.order.json` shape replaced.

## Why

- **Extract and nest.** Copy `Book Three/` out and open it as its own project;
drop it back and it is a folder again. A series can live in one collection while a WIP is opened on its own without touching published books.
- **Portability.** Copy a project folder to your editor; it opens, with its templates inside it.
- **Legibility.** A file manager shows the book's shape, and Git shows prose diffs rather than a database blob.
- **The Obsidian property:** any folder can be a project, so the unit of work is whatever the writer carved out.

## Layout

```
My Novel.lekhak/          ← a project: a folder with manifest.json
├── manifest.json                   ← title, format version, root child order
├── project.sqlite3                 ← the index: metadata, search, history
├── Book Three/
│   ├── .order.json                 ← the order of this folder's children
│   ├── Chapter-12.html             ← prose + frontmatter
│   └── Scene-Two-People-Meet.html
├── templates/                      ← the template sets, recognised by name
└── images/
```

The `.lekhak` **suffix is a convention**, not a requirement: any folder containing `manifest.json` is a project.

**Nothing else lives in the project.** No `documents/` store, no `backups/` folder: the first is gone now that each document is a real file in the tree, and the second because a project that is lost must not take its backups with it. A project folder holds the book and the index, and nothing more.

**The database is an index, not the store.** It carries the metadata the app needs to be quick — the word counts, the synopsis, the template assignment, the search index — and each row remembers the path of its own file. The prose and the metadata-in-the-file are the truth; the database is a cache that can be rebuilt by scanning (`Project.sync_with_disk`). A folder or file the writer added by hand is picked up on open and becomes part of the book.

**Backups live outside the project**, in a `Backups/` folder beside it (or wherever the writer says). A backup is a copy of the whole folder tree, so it opens like the project. The manuscript autosaves; a backup is the extra copy, taken when the writer asks for one, when the app closes, and as often as Preferences says otherwise.

## Documents

One file per document. Prose is HTML as it is today; everything that is not prose lives in a **YAML frontmatter block** at the top of the file:

```html
---
title: 3 - Two People Meet
id: 5f3c…            # stable across renames
type: Scene
synopsis: One finally meets Another, and it goes badly.
word_target: 1200
metadata:
  PoV: Narrator
  Status: Draft
---
<p>…the prose…</p>
```

- **A writer's own field is in the file (built 2026-11-12).** `metadata:` above is written and kept: `Type`, `PoV`, a `Series` — every field the writer's own table holds — travels in the head of the file, so a document opened in any editor is complete on its own. Value types and option lists stay in the database, since they are editing affordances rather than content, and the head's own keys (`title`, `kind`, `page_break`, …) are not repeated inside the block.
- **Disagreements are reported, never silently fixed.** When a file's head and the project's record disagree, Lekhak reports it on detection while it is fresh. Metadata drift is resolved per document and per field (choose file or project for each, or accept all from one side). Title/identity drift is handled separately: the writer chooses which title is right, is told the consequences (automated links may no longer resolve naturally; manual or hardcoded wiki links may break), and may opt in to a project-wide find/replace update of hardcoded `[[wiki links]]` only — the rest is fixed by hand.
- **The title is the truth**; the filename is a slug derived from it. Titles are free text and filenames are not, so the two cannot be the same thing.
- **`id` is stable and permanent** — the thing that makes a rename harmless:
covers keep finding their folder, snapshots and revisions keep their document, and an external move looks like a move rather than a delete plus a create. *Recommended; amend if you'd rather have no ids at all and accept that renames lose history and covers.*
- **Frontmatter never reaches an export.** It is stripped on the way out and restored on the way in — the same display-layer rule that already governs binder numbers and resolved links (working agreements, "Manuscripts are precious").
- **YAML subset.** Keys, scalars, simple lists, nested maps by indentation.
No anchors, aliases, multi-document streams or tags. A hand-rolled parser keeps the two-dependency agreement intact; if the subset ever pinches, we move to PyYAML and record the reason then.

## Folders

**Anything with children is a directory, whatever its role.** A filesystem item cannot be a file and a directory at once, and real binders contain chapters that hold both their own prose *and* their scenes — the dry run against the author's project found exactly that. So a document with children becomes a directory, and its own words, if it has any, become **`index.html`** inside it. A container with no prose of its own has no index file. (Whether a container's own `index.html` exports is a question of role, settled by `Type`, not by where it sits.)

`.order.json` inside each folder holds **the order of its children** as a list of names. It lives *inside* the folder because a copied folder must carry its sequence with it. The project's own root order lives in `manifest.json`, beside the title.

**`index.html` is a reserved name, in every folder.** It is the container's own prose — a part of the parent, not one of the children — so it leads the folder's order file, and the order applies to the rest. A document *titled* "index" may exist: it is written as `_index.html`, and the title still reads "index" in the binder. Without that, the two would be one file and the document would be swallowed by the container's page.

**A conflict is reported, never rewritten.** If a folder's `index.html` says in its own head that it is some *other* document — a writer made a document called "index" before the name was reserved — the two meanings collide. The file is read as the folder's page (the rule above), the writer is told which folder and which title, and **nothing is changed**. Lekhak never rewrites a file it did not write on purpose: a wrong guess at the writer's intent loses their words, and a silent fix turns a working file into a troubleshooting session.

Order is a list of **names**, not numeric prefixes: reordering rewrites one small file instead of renaming every document.

**Order is never derived from filenames, and nothing sorts children by name.** The order file *is* the order: the app writes it from the binder order, and every reorder rewrites it. Sorting by name would scramble real binders — the author's scene titles run `1---`, `2---`, … `10---`, which as text sort `1, 10, 2` — and a filesystem's own enumeration order is arbitrary on top of that. New documents append; the writer drags to reorder; the numbers in a title stay decoration, never mechanism.

**Why the order lives in the folder as well as the manifest.** Copy, move or extract a folder anywhere and its sequence travels with it. If `manifest.json` is lost, the project's title and overview go with it but no root order does — every folder still knows its own sequence, so a re-opened project is arranged exactly as it was. If a single `.order.json` is lost, only that folder falls back to a scan; nothing else moves.

## Stored versus indexed

| Stored (the truth) | Indexed (rebuildable) |
|---|---|
| prose, frontmatter, folder manifests | search index, word counts |
| templates, images | revisions and snapshots |
| backups | anything derivable by scanning |

**Styles use delta capture (builder rule).** `capture_paragraph_signature` keeps character keys (family/size/weight/italic) only when uniform across the block; a character save keeps only what is uniform across the selection *minus the paragraph base*. `Default`/`default.css` stays file-as-source and reconciles like prose files; absolute capture copies the base into every style and drifts, so delta capture is the contract.

**History does not travel with an extracted folder.** The prose travels, and so do tracked changes (their marks live in the HTML), but snapshot and revision history stays with the project it happened in. A folder split off today starts with no history of its own.

## Projects, and opening any folder

- `manifest.json` marks a project: title, format version, root child order.
- **Opening a folder with no marker offers to make it a project** — write the manifest, index what is there, and let the areas appear as they are needed.
- **Converting an old project is the same code path** plus one step: read the old database and write the tree from it (`migrate_to_tree.py`). One implementation, not a converter and an opener.

## Nesting

A project marker is honoured **only at the root you opened**. Nested inside another project, a project is a folder: its project settings are ignored, its `.lekhak/` is machinery rather than content, and its documents are simply content — counted, exported, reordered. Its own `Templates/` still governs its subtree, by the nearest-wins rule, which is what lets ten books carry ten formats inside one collection.

**Locks.** On open, check `.lekhak/lock` in the ancestors *and* immediate descendants: warn that the same files are open at another level, and allow an **override**, because a crashed session must not leave a project permanently unopenable. No arbitration beyond that.

## The areas

- **`templates/`** (the project's own root) holds the template sets: one folder each, flat in this root, listed in the binder's Templates folder with the shipped sets on top (`docs/TEMPLATES.md` §1.1). It is a folder of *files*, not documents — no binder rows, no flags, no jump rows — and the shipped sets need no folder at all, living in the code and duplicable into the project. A folder named `templates` anywhere else is an ordinary folder: discovery is by this one location, not by a magic name.
- **`Trash`** is a **root-level area**: your deletes go there, mirrored into the same structure, and emptying it prunes the directories it emptied. The **name** is recognised *anywhere* to mean "not manuscript", so a nested project's discards never count or export; the area lists any nested `Trash/` as jump rows so nothing is hidden. It is not renamable, restoring means moving something out (with **Rename** as the default and **Overwrite** behind a checkbox that promises nothing about backups), and manuscript folders are never pruned — only explicit deletion removes them.

## Filenames: no spaces, enforced on sight

**Author's decision (2026-10-25):** *"Lekhak filenames must not contain spaces"* — and the app enforces it rather than merely preferring it. Anybody who can take an HTML document out of a project folder and edit it can write a compliant filename; the alternative is a tree that every other tool reads differently from the way Lekhak does.

- **On open, and on any rescan, non-compliant names are normalised**: spaces and illegal characters become a single hyphen, runs collapse, ends trim. Files **and folders** — a policy that stops at the last path separator is not a policy — and because the walk is top-down, renaming a folder needs no second pass for its children.
- **Files from outside are accepted, never refused.** Enforcement is a rename, not a rejection: bring in whatever a file manager or another editor made, and Lekhak tidies the names on sight.
- **Guards:** only while we hold the project lock; a single pass before any editing begins; never on a name that is already compliant; collisions get `-1`, `-2`, compared case-insensitively because macOS and Windows filesystems are; and every change is **reported** in the session summary, so no rename is a mystery. A per-project setting can switch normalisation off for someone deliberately syncing a project with another tool.
- **Slug rules:** the writer's own names survive. Spaces and letters in any script stay; only characters a filesystem refuses (`/`, `:`, control codes…) become `-`; the ends are trimmed of dots and spaces; a 120-character cap; a title with nothing in it gives its *file* the name `Untitled`; Windows-illegal characters and reserved names (`CON`, `NUL`, `COM1`…) are avoided, and so is `index` — Lekhak's own reserved name, since a container's own prose is its `index.html` (§Folders). A name already taken in the same folder gets a number — `Epilogue.html` and `Epilogue-1.html` — and both read `Epilogue` in the binder.
- **The title is never touched.** It lives in the frontmatter; normalisation changes filenames only, and the writer's words are not rewritten (working agreements, "Manuscripts are precious"). In particular the collision suffix never reaches it: two documents titled `Epilogue` are `Epilogue.html` and `Epilogue-1.html` on disk and both read `Epilogue` in the binder, and a title the writer left blank stays blank rather than becoming `Untitled`. Only a file with no frontmatter at all borrows its title from its name — that one was written by another hand, and its name is all it has.

## Backups, locks, and edits from outside

- **Backups** are full-tree copies, kept **outside** the project — content and templates travel, and a backup can never contain backups. The default is a `Backups/` folder beside the project; Preferences sets the folder and the frequency for new projects, and one project may override both. The manuscript autosaves on its own, so a backup is the extra copy: on an explicit Ctrl+S, when the app closes, and on the hourly setting if the writer wants it.
- **Edits from outside are expected**: rescan when the project regains focus, and if a file changed on disk while it was open here, offer **Reload**, **Keep mine** or **Keep both** — never a silent reload, which would discard what is in the editor.
- **A lock is a marker file in the tree.** `.locked` inside a folder locks that folder and everything inside it; `.<name>.locked` beside a file locks that file. A locked document still opens, still reads, and can still be typed in — it is simply never written, and the writer is told so rather than losing the words quietly. Unlocking takes the marker away; nothing else changes.

## Risks to keep in view

- Case-insensitive filesystems (hence the collision suffix and casefolded comparison).
- Cloud-sync tools and hidden directories.
- Frontmatter leaking into an export if any new writer forgets to strip it.
- Lock blind spots across nesting, and stale locks.
- Folder renames cascading through children, and anything still keyed by path — which is what the stable `id` exists to prevent.

## Build order

1. This spec, and the `ROADMAP.md` amendment it requires.
2. **The dry-run migration tool** — read-only, run against the author's real project, reporting the tree it would write before anything is touched.
3. Loader and index rebuild, then the writer: create, rename, move, reorder, delete — atomic renames wherever the filesystem allows.
4. Trash, the areas, and the per-level lift.
5. Extract, nest and absorb — which by then is "copy a folder, open it".
6. The example/documentation project, authored as markdown in the repo.

---

## Built overnight (2026-10-25, unattended shift)

Verified: `641 tests, exit 0`. Nothing in the running application changed — this is all groundwork, deliberately beside the database.

- **`docs/STORAGE.md`** (this file) — the decided model, plus the `ROADMAP.md` amendment the format-contract clause required.
- **`scripts/storage_layout_dryrun.py`** — read-only: prints the tree a migration would write, from an existing project's database. Run against the author's real project it reports **55 folders, 283 documents, 46 of them carrying their own prose as `index.html`, 282 names normalised by the no-spaces rule, 8 collisions needing a suffix, 0 problems.** *The 46 was wrong when written, and is stale now.* `f0803c4` (2026-10-25) showed those are Qt's stylesheet-only empty documents and titles typed once as headings — neither is prose. Re-run 2026-10-26: **51 folders, 281 documents, 0 index.html**, and the 0 was checked *against the files* with an independent HTML text extraction rather than through `has_prose` alone, because a wrong `has_prose` would make the round-trip gate agree with the writer and pass while dropping prose. No container in the author's project holds a sentence of its own, so none needs an `index.html`.
- **`src/lekhak/layout.py`** and **`tests/test_layout.py`** (20 tests) — the core: name rules, the YAML-subset frontmatter and manifests, `write_tree` and `load_tree`, and the order guarantees below.

### Assumptions made, for the author to confirm or overturn

1. **A folder's title lives in the database row, and its name on disk.** Found while building: a directory name is only a slug, so the real title has nowhere else to live. The row's `title` holds it, and the file's frontmatter holds it too, so the file is complete on its own.
2. **Combining marks survive in filenames.** A mark is not a space: `अंत` stays `अंत` and `café` stays `café` rather than being hyphenated mid-word.
3. **Values are quoted whenever unquoting them would change them** (amended 2026-10-26). The earlier rule — "numeric-looking values are not quoted, so `Count: 3` reads back as the number 3" — quoted only a hand-picked list of words, and silently retyped `007` to `7`. A *string* is now quoted unless the parser would read it back as that exact string; numbers and booleans the model genuinely holds are written bare and come back as their type. So `Count: "3"` is the text 3, `word_target: 3` is the number 3, and a hand-written bare `3` still reads as a number (YAML's own habit).
4. **`index.html` is not a child.** It is the container's own prose, so the scan skips it when listing children — otherwise a chapter's text appeared as one of its scenes.
5. **Files added from outside append**, in name order after the manifest's children, rather than being inserted somewhere clever. The manifest is the order; new things go last.
6. **The dry-run script still carries its own copy of the slug function** rather than importing `lekhak.layout.slug` — a tidy-up for the next pass, now that there is one place for it.

### What the order guarantees actually are

- `write_tree` writes each folder's children **in the order given**, and records exactly those names in the manifest.
- `load_tree` reads the manifest first and consults the directory only for names it does not already know.
- Tests pin both: titles that would sort `1, 10, 2` stay in binder order, and one test **edits a manifest by hand and watches the tree follow it** — the proof that filenames are never consulted for order.
- A document renamed by hand keeps its identity, because the `id` lives in the frontmatter.

### Next, in order

1. **An adapter on `Project`: build `Node`s from the database, and read a tree back into the model — DONE (see below).**
2. **The migration writer: `write_tree` from the real database, then read it back and compare every document before the flat pool is retired — DONE (`scripts/storage_roundtrip.py`, below).**
3. Writer operations in the new model: create, rename, move, reorder, delete —
atomic renames wherever the filesystem allows.
4. Trash and the areas; extract and nest; then the example project.

### The adapter and the gate (2026-10-26)

`Project.to_nodes()` is the adapter: a container is a folder whatever its stored kind, a leaf is a document, `Type` becomes the node's own field and everything else the writer keeps stays in `metadata`. `Project.write_tree()` writes the binder out; `read_tree()` reads it back. The two helpers `flatten_promoted_books()` and `unwrap_folder()` exist because the old import wrapped a book in a same-named chapter — the folder *is* the book now.

**`scripts/storage_roundtrip.py` is the gate.** It copies the project to a temporary directory (the real one is never opened for writing), writes the tree, reads it back and compares every document field by field — id, title, type, synopsis, target, metadata, content and binder order. It then does something the round trip *cannot*: it reads the schema directly and lists fields the layout has **nowhere to put**, because a column nobody writes is invisible to a comparison and would be dropped by a migration with nothing failing.

It runs `PRAGMA integrity_check` first, for a reason worth keeping: a corrupt index makes index-backed queries return phantom rows, which reads as a bug in the layout rather than in the database.

**Result on the author's project (2026-10-26): the round trip is exact** — all 332 documents match, and `integrity_check` is `ok`. Four fields remain unhomed, all at **0 rows**: `page_break`, `doc_template`, `value_type`, `options_json`.

Two things were found by building this, both fixed:

- **The author's project had a corrupt `document_parent_order` index.** `integrity_check` failed with *"wrong # of entries"*; `count(*)` said 334 rows against a table holding 332, `Templates` listed four children of which three were the same id, and `_descendant_ids()` — which feeds `delete_document` — returned that id three times. Repaired with `REINDEX` on 2026-10-26 after a backup (`backups/<stamp>-pre-reindex`, named outside the pruning stamp so pruning never touches it). The table was never wrong; only the index was. Two orphaned `.html` files on disk are the other half of the same event, and are exactly what `delete_document`'s own documented row-before-file order predicts a crash would leave.
- **The frontmatter writer turned text into numbers.** `_scalar` quoted a hand-picked set of words, so `Count: 007` read back as `7` and `Word: 2026` as `2026` — metadata rewritten by a round trip nobody asked for. The rule is now one that cannot miss: *a string is quoted unless `_value` would read it back as that exact string*, and `_value` parses floats so real numbers (print geometry is inches) keep their type. The corpus had no leading-zero values, which is why the first gate run still reported an exact round trip.

**`is_templates_root` and `page_setup` now have a home** (the two non-zero gaps the gate first reported): both are folder-scoped, so both live in `folder.yaml` — which is what makes a copied book a book with its own trim. `page_setup` is validated by `clean_page_setup`, never by the loader: a hand-broken geometry loads and then fails where everything else fails it, rather than being silently dropped where the writer would never see it. `project_metadata` rides in `project.yaml`'s `settings`. *(Both flags have since been deleted outright — the second decision below.)*

Suite 669, exit 0; lint at the pre-existing baseline (337); every file parses as Python 3.9. Mutations caught: the manifest write of `templates_root`, the old quote rule, float parsing removed, and `to_nodes` dropping `templates_root` — the last reported by the gate as `'Templates': templates_root True -> False` and `'Default': …`, exit 1.

### Decided: templates are folders on disk, found by location or a mark (2026-10-26)

> **Revised 2026-10-28 — see the next section:** the mark never earned its
> keep and is gone; discovery is by location alone.

The binder-document template system (`is_templates_root`, the `Templates` binder folder, `book_settings["template"]` as a folder id) is **deleted** (`1b4005d`) — it rendered nothing: the author's project had `Default` nested inside `Templates` with both flagged as roots, 0 words, and no book assigning a template, so every export was already driven by the on-disk slot files in `<project>/templates/`. The decided model (`docs/TEMPLATES.md`): a template is a folder of HTML fragments on disk; discovery lists `<project>/templates/` automatically and any other folder **only when explicitly marked**; the Templates view (not a binder container) shows the shipped default with Duplicate. The column did not simply vanish: its *meaning* moved down a level. `is_template` marks the template folder itself, and a migration on open marks each old area's direct children, zeroes the area flags and drops the old column (guarded for SQLite < 3.35) — an area nested inside an area becomes a template of the outer one and keeps its own children. `folder.yaml` writes `is_template` in place of `templates_root`, so a copied template stays a template.

### Revised: the mark is gone, the disk is the whole answer (2026-10-28)

That mark was a *second* concept of "template" competing with the folder on disk, and it ended up listed nowhere worth looking at (the author's call, `docs/TEMPLATES.md` §1.1). Deleted end to end: the column — the migration now drops **both** `is_templates_root` and `is_template`, guarded for SQLite < 3.35, with no data to migrate since nothing in the new model reads either — plus `set_template`, `template_folders`, the export exclusion, the italic cues in both trees, the inspector checkbox and the binder's mark actions. `folder.yaml` no longer writes `is_template`; an old manifest still carrying one reads as simply not marking anything.

What is left is one rule: **a template is a folder under `<project>/templates/`**, holding files rather than binder documents, so manuscript and templates cannot collide and word counts and whole-project export need no exclusion at all. A folder outside `templates/` becomes a template by being moved there. Same session, two more decisions that live in the same folder: a **subfolder named for a format** (`html`, `epub`, `docx`, `pdf`, `text`) refines a set's root for that format's exports (`docs/TEMPLATES.md` §1.3), and a single file can be **copied out** of a set as a new set of its own (§1.2). The shipped sets keep their slots forever: a project folder called `default` is listed after them as a row of its own and never answers to the shipped name.

### Decided: the writer never meets a slug (author, 2026-10-26)

**Collisions are a filesystem problem, and the filesystem solves them.** Duplicate titles are numbered *in the filename only* — the binder keeps showing the title the writer typed, duplicates and all.

The author's project makes this the common case rather than the exception: **16 titles appear more than once** — nine `Untitled`, five `A Place, Sector 7`, four each of `Epilogue` and `1---A Place, Sector 7`, three `Prologue`, and both `Epilogue` and `epilogue`, which collide case-insensitively on macOS and Windows. All of them get distinct files and identical binder titles.

**A blank title stays blank.** If the writer put nothing in the binder title it stays empty (or `Untitled`, whichever they typed); it is never filled in from the filename. A document with no frontmatter *at all* is the one case that still takes its title from its file — that file was written by another hand, and its name is the only name it has.

This was a live bug, not a policy question. The reader fell back to the filename whenever the title was *empty* as well as absent, so two blank titles came back as `Untitled` and `Untitled-1`: the disambiguation number leaking into the binder, which is precisely the slug a writer is not meant to see. `_named()` now distinguishes absent from empty, and `_folder_values()` keeps a keyed-but-empty manifest title instead of dropping it as unwritten.

### Still open, and the author's to decide

`page_break`, `doc_template`, `document_metadata.value_type` and `options_json` have no home in the tree. All four are at 0 rows in the author's project today, so nothing is at risk *now* — but 1.6 and 2.1 both put real values in the first two, and 1.14 puts real ones in the last two, so the moment a writer uses those features the gate will start reporting rows that a migration would drop.

### Assumptions made, for the author to confirm or overturn
