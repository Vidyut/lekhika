# How Lekhak thinks about your project

## Folders and documents

A project has exactly two kinds of thing in it: **folders** and **documents**.
Folders group. Documents hold your words.

Lekhak does not call them anything else. Because writers structure books,
series and research differently, we do not ship categories like "chapter" or
"note" baked into the structure. A folder is a folder. What it *is* — a book,
a chapter, Part One, a pile of research, a wiki of characters and locations —
is decided by you, in the panel on the right. The software will format and
publish exactly the structure you built.

## Roles come from Type

Select anything in the binder and the inspector (the panel on the right)
shows a **Type** row. It is always there, always filled — before you set
anything it says what the item physically is (`Folder` or `Document`), or
what it was created as (`Chapter`, `Scene`, `Note`). Set it to whatever the
item plays in your book:

- a folder holding your book — **Type: Book**
- a chapter — **Type: Chapter** (on the chapter's document, or on a folder
  that contains the chapter's scenes)
- a scene — **Type: Scene**
- front matter, back matter, author profile, glossary, bibliography — any
  words you like: **Type: Front Matter**, **Type: Glossary**, …

Type is free text. The names above are suggestions. Templates and exports
use whatever Type names you give them — to pick a template, choose a heading
style, start a chapter on a new page, or leave something out of the book
entirely.

## Keeping something out of the printed book

Any document can be printed or not, as you wish. There is no special kind of
item that refuses to print. Two ways to leave something out:

- **Give it a Type like "Do not print"**, and add a blank template for that
  Type to your template set — it renders as nothing.
- **Or simply don't select it** when exporting. The Export Options dialog's
  scope is whatever you have selected; select nothing and it is the whole
  project.

## Do what feels intuitive

If you're writing a book, create a folder for the book and start writing
documents inside it. If you think a chapter should have separate scenes in
it, create folders for the chapters and write or move scenes into them. Keep
research, character notes and worldbuilding in their own folders if that
helps you — we recommend keeping material to publish separate from references
and research, but don't worry: you can always organize folders as you need
them when a need for clarity or reorganization arises.

Do what feels intuitive for you, and the software will help you write and
publish exactly like that.

---

## For builders

The physical kinds are labels — `folder`, `chapter`, `scene`, `note` — and
any kind may hold any other; the *role* is the protected metadata key
`Type` (already reserved in `docs/TEMPLATES.md` §2), and its vocabulary is
the writer's, matching opportunistically against template names per §2's
chain.

- **`Project.type_of(document)`** (project.py) is the single source of truth:
  stored `Type` metadata if set, else the derived default — **Folder** for a
  folder, **Document** for anything else. Two base roles, as close to the
  truth on disk as a role can be (1.17); every other role is one the writer
  adds. Derivation *is* the migration: old projects need no backfill.
- **Roles and metadata fields are kept per group.** `type_group(document)`
  says whether a document is a folder or a document, and `known_types(group)`
  is what that group's dropdown offers — the base role, the roles added to
  it, and the roles already stored on the group's documents. `known_fields(group)`
  does the same for metadata field names: a field named on one scene shows,
  empty, on every other scene, and only filled values are stored. Both
  catalogues live in the `settings` table (`known_types`, `known_fields`), so
  no schema change was needed.
- The inspector's Type row (app.py) is an **editable dropdown** offering only
  that group's roles, plus free text, and a **"New type…" box** beside it
  that adds a role to the group. It edits the **document's own** metadata —
  folders included, even though the custom-fields table below a folder
  selection shows project metadata (§3.1). It shows `type_of` defaults
  and is always populated; emptying it returns the item to its derived
  default. A derived default is never written to storage — only an
  explicit choice is stored — so a later heal is never shadowed by a
  stale seed. Idiot-proofing: an exact match snaps to the existing option
  (nothing is added); a case-only variant ('book' vs 'Book') warns and
  offers "Keep it as a separate type", snapping to the existing option
  until the checkbox is ticked.
- The metadata table's third column is **Format** (the value widget:
  Text/Text area/Checkbox/Date) — renamed from "Type" to end the collision
  with the metadata key. The Edit Metadata dialog hides the Type row and
  preserves the stored value on save; the inspector's Type row owns it.
- **A template is not a role.** It is a **tick** in the inspector: the
  metadata key `Template` (`yes`), with the design itself in `Template
  config`. A template document's body is markup rather than manuscript, so
  it stays out of the export and the sidebar shows the template design panel
  instead of the metadata. The mark travels into the file's head as
  `template: yes`.
- **No publish flag exists.** Exclusion is the template mark, a blank
  template slot, or the export scope selection (`ExportOptionsDialog`).
- The Outliner's "Kind" column, the binder tooltip, the related-files list
  and the link picker show the **physical kind** — the role is the writer's
  own label and lives in the inspector.
- Page setup, book templates and covers hang off the folder that carries
  the book settings, not off a role: a folder with book settings is still a
  Folder, and "Book" is a role the writer adds. Project default otherwise.
  Export stamps each book's own paper and warns on mixed trims (author
  decision, 2026-10-23).
- **Templates live on disk** (`<project>/templates/`, one folder per set,
  §1.1 of TEMPLATES.md): no flag, no binder mark — the folder is the truth.
  A **one-file template** is a single file with its yaml in the head
  (`templates/<name>.html`), listed and applied like a set's slot. A
  **template document** is a document marked as a template; its own body is
  the markup.
