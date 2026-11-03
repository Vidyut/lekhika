# Lekhak roadmap

Feedback and feature requests, triaged against the current MVP. Ordered by
foundational importance, not by glamour. Items reference the code they touch.

## Phase 0 — foundations (DONE)

### 0.1 Binder rework: neutral folders, parallel roots — DONE
**Request:** "better binder — only one top level folder; Chapter/scene format
feels impossible. Folders have no quality of their own (like Scrivener); the
role is in how they are organized and used. Chapter/scene/note stay, as
labels the writer chose — they are not structure."

**Reality check:** the schema already permits multiple roots and arbitrary
depth (`parent_id` nullable, `reorder()` accepts any parent). The limitation
is UI policy plus the rigid `kind` enum:
- `app.py:611` `add_chapter()` hardcodes `parent_id=None` (top level only).
- `app.py:618` `add_scene()` guesses the parent from the current selection.
- `project.py:169` `kind` restricted to `{chapter, scene, note}`.
- `project.py:62` `create()` seeds a single "Draft" chapter at root.
- Export (`project.py:391`) hardcodes "kind in {chapter, scene}".

**Change:**
- `kind` gains `folder` as a first-class value: a container with no content
  of its own by default.
- Any item can be created at any level, under any parent, including parallel
  top-level items (a Manuscript folder beside Characters beside Locations).
- Creation actions become "add folder / add chapter / add scene here" relative
  to the selection, never top-level-only.
- Export participation stops being "kind is chapter or scene" and becomes a
  per-document/export-compile property (see 1.2), defaulting sensibly.

**Implemented (2026-10-20):** `folder` is a first-class kind; kinds are
validated against `DOCUMENT_KINDS` and export uses `EXPORT_KINDS`
(chapter+scene; folders and notes stay out). `Project.create()` seeds
"Draft" as a folder, and a one-time migration flips the old seeded root
chapter. The three toolbar actions were unified into one placement helper:
containers receive children, a scene prefers a chapter/folder parent, a leaf
selection gets a sibling; nothing is forced to the top level, so parallel
roots work. Folder items get a directory icon and selecting one flips the
central view to the corkboard. Per-document compile overrides remain open
until 1.2/2.1.
- "Draft" becomes a folder, not a chapter. Existing `.lekhak` projects must
  migrate without data loss (a kind migration plus a one-time seed rule).

### 0.2 Desktop launchers (Linux / Windows / macOS) — DONE
**Request:** easy to launch from the desktop on every platform.
**Implemented (2026-10-20):** `scripts/install_linux.py` (stdlib only) writes
`~/.local/share/applications/lekhak.desktop` plus hicolor SVG/PNG icons,
validates with `desktop-file-validate`, refreshes the desktop/icon caches,
and supports `--check` / `--uninstall`; Exec prefers the checkout's `.venv`
console script, then PATH, then `python -m lekhak`. `scripts/install_windows.ps1`
creates a Start Menu shortcut (optional `-Desktop`, `-Uninstall`) via
pythonw + `-m lekhak`, icon from `packaging/lekhak.ico`. `scripts/install_macos.sh`
builds a minimal `~/Applications/Lekhak.app` bundle with launcher and (when
iconutil is available) an `.icns`. `packaging/lekhak.svg` is the source of
truth; `scripts/render_icons.py` bakes PNG/ICO/ICNS. `python -m lekhak`
works via `src/lekhak/__main__.py`, and `main()` sets the desktop file
name so GNOME/Wayland match the window to the entry.

## Phase 1 — good software

### 1.1 Search and replace across the project — DONE
**Request:** project-wide search *and* replace.
**Implemented (2026-10-20):** Edit → Replace in Project… (Ctrl+Shift+H).
Preview list shows every match with its document, field (content/title/
synopsis) and surrounding text, capped at 500; double-click jumps to that
document. Options: regular expressions, ignore case, include titles,
include synopses (both opt-in at the API level). Replace asks for
confirmation and reports replacement/document counts. Backed by
`Project.replace()` and `Project.search_snippets()`; literal mode inserts
the replacement verbatim, regex mode supports backreferences.
Matches are searched in stored HTML, so matches interrupted by markup
will not hit.
**Related fix:** `Project.documents()` now returns binder order (tree walk
by position) instead of SQL order by uuid parent id; `ordered_documents()`
is now an alias. Search results, the outliner, corkboard and replace all
iterate in binder order as a result.

### 1.2 Export templates (yw2html model) — DONE

> **Extended 2026-10-26:** the decided templates model — the Templates view,
> bounded discovery, applicability and the export chooser, fragment selection
> by content shape, and the three-way DOCX handling — is `docs/TEMPLATES.md`.
> The slot machinery here (`templates.py`) is the half that survives; "slot"
> is now called a *fragment*.
> **Revised 2026-10-28 (2.4b):** the Templates *view* is gone as a listing —
> the binder's Templates folder lists the sets on disk, the Outliner lists
> one set's files, and the tab edits them.
> **Revised again 2026-10-28 (2.4c):** the tab is gone too — a file opens
> in the regular editor and saves back to its own path.

**Request:** default template; users add more; select per chapter/scene;
metadata tags and formatting can modulate output. Reference: peter88213/yw2html.

**Implemented (2026-10-20):** `lekhak/templates.py` provides named template
sets: directories of optional slot files (`project_header`, `project_footer`,
`chapter`, `chapter_end`, `scene`, `first_scene`, `scene_end`,
`scene_divider`), `.html` and `.txt` variants, missing slots falling back to
built-in defaults. Placeholder syntax is yw2html's `${...}` family:
`${Title}`, `${Content}`, `${Synopsis}`, `${WordCount}`, `${ChapterNumber}`,
`${SceneNumber}`, `${WordsTotal}`, `${Metadata.Key}`. Sets live under
`<project>/templates/<name>/` or `~/.config/lekhak/templates/<name>/`; the
export dialog offers a dropdown, "New set…" (forks the current one) and
"Open folder". A compile walks binder order: a chapter unit is its own text
plus descendant scenes separated by the divider; standalone scenes export on
their own. EPUB emits one page per unit. DOCX keeps structural headings
(header slots do not apply there). Word counts are cached on the document
row at save time, with a one-time backfill for older projects.

### 1.3 Word counts everywhere — DONE
**Request:** counts for scenes, outliner, selected text, selected
chapters/scenes, running totals.
**Implemented (2026-10-20):** `word_count` cached on the document row at
save time with a one-time backfill; `Project.word_rollups()` sums
descendants for container rows; the outliner shows per-item and rollup
counts (own vs total in the tooltip); the status bar shows the current
document, target progress, selected-text count, and the project total;
corkboard cards show count and target progress.

### 1.3a Outliner view with user-chosen columns — DONE
**Request:** add/remove metadata columns in the outliner view.
**Implemented (2026-10-20):** a third central view — a hierarchical table of
the binder in binder order. Columns: Title (always on), Kind, Words,
Target, plus any metadata key used in the project. The picker
(Document → Outliner Columns…) checks columns on and off; the choice is
stored in project settings and survives reopening. Clicking a row selects
that document in the binder; binder selection highlights the row. Word
cells on containers are rollups of everything beneath them. Metadata is
read with one batched query per refresh.

### 1.4 Split and merge scenes — DONE
**Request:** split a scene at the cursor into two; merge a scene with its
sibling.
**Implemented (2026-10-20):**
- `Project.split_document(document_id, before_html, after_html, new_title=)`
  — the caller extracts the two HTML halves with `QTextDocumentFragment`,
  which keeps formatting; the method snapshots ("Before split"), inserts a
  sibling right after the original (same kind, collision-free "Name (2)"
  title) and renumbers the rest. Both halves must contain text.
- `Project.merge_documents(first_id, second_id)` — target must be the
  immediately following sibling and a leaf; snapshots first ("Before
  merge"), re-homes the second document's snapshots onto the first so its
  history survives, concatenates the bodies, deletes the second (metadata
  cascades away, content file unlinked).
- `Project.next_sibling()` helper.
- UI: Document → Split at Cursor (`Ctrl+K`) and Merge with Next Sibling,
  plus binder context-menu entries (right-click loads the document first).
  Merge confirms with an explicit warning about dropped metadata.

### 1.8 Interface font size in preferences — DONE
**Request:** font-size picker in Preferences — **for the interface only**
(menus, binder, corkboard, outliner, synopsis, status bar); the editor
text keeps the existing zoom (View → Zoom In/Out, Ctrl+0).
**Implemented (2026-10-20):** Edit → Preferences… (`Ctrl+,`) opens a dialog
with a 8–24 pt interface-size picker and a note that the editor keeps its
own size. The choice persists to `QSettings` (`ui/fontSize`, org/app
"Lekhak") and is applied in `main()` *before* the window is built, so a
fresh launch starts at the right size; the dialog applies it live too.

The two footguns this had to dodge, both confirmed against a running
session rather than assumed:
- **Live propagation.** `QApplication.setFont` alone does **not** reach
  widgets that already exist — a live change left the binder at 9 pt.
  `preferences()` therefore also calls `setFont` on the window, which
  cascades to existing children. Tree items carry no per-item font, so
  they follow.
- **The editor must never inherit it.** The editor is pinned with an
  explicit `setFont` to 12 pt at construction (`reset_zoom`'s number),
  and an explicit font ignores the cascade — so the tree moves and the
  editor does not. This matters because `toHtml()` round-trips the body
  font size: if the interface size reached the editor it would be written
  into stored document content. `setHtml` still applies each document's
  own stored size on load, so existing documents are unaffected.
Verified: set 20 pt → tree 20 / editor 12 in the same session, and across
a simulated relaunch.

**Display-only editor zoom, and the leak it replaced (2026-10-21):** the
old View → Zoom called `QTextEdit.zoomIn/zoomOut`, which moves only the
document's *default* font. Every stored span carries its own size, so
nothing visibly zoomed — and the body CSS was rewritten to the zoomed
number and saved. Auditing the author's real project found seven
documents already stamped `body … font-size:14pt` by it (repaired to the
12 pt anchor; originals kept in `~/Writing/LEKHAK/zoom-leak-repair-20261021`).
The replacement is a display factor (`ZOOM_ANCHOR_PT`,
`scaled_font_sizes()`): the editor loads stored HTML × zoom, saves ÷ zoom,
and each place that converts between live formats and stored ones undoes
it — style capture (`_capture_signature`), pasted fragments
(`_scale_scratch`) — while style application, the size box and Clear
multiply it. The load-bearing test is that zooming and saving leaves the
stored file **byte-identical**. Zoom is session state about this
manuscript (`editor_zoom`, beside `last_document`/`last_cursor`, 1.9),
driven by Ctrl+scroll over the text, the View menu, and a readout; the
Page view multiplies its canvas scale by the same factor, so paper and
type scale together and pagination stays what the export will print.
The format bar itself moved into the Editor tab in the same pass: bold,
italic, style, font, size, lists, alignment, Clear and the zoom readout
are editor-scoped, so they now hide with the tab instead of sitting in
the window chrome above the sidebars (it stays a real `QToolBar`, so
focus mode's sweep still hides it). Tests `tests/test_zoom.py` (20).

**The clipboard is a saved layer too (2026-10-22):** Qt's own `copy()`
exports the live document, so the clipboard carried sizes already × zoom —
and `_scale_scratch` multiplied them a second time on the way back in.
With Preferences → paste "keep fonts" on at 150 %, a 12 pt passage copied
inside a zoomed document pasted at 27 pt and was *stored* as 18 pt after
the next save. `LinkEditor.copy()` now divides the factor out before the
clipboard sees the HTML — the same rule `save_current()` follows, so a
paste puts it back exactly once — and `cut()` is copy plus a delete,
because Qt's own cut builds its mime data itself and never calls the
Python override. Nothing selected leaves the clipboard alone.
Tests `tests/test_zoom.py` (22).

### 1.5 Internal links — DONE
**Request:** link to internal documents — a picker, and Obsidian-style
`[[wiki]]` links that resolve by title and open the page on click.
**Implemented (2026-10-20):** `[[Title]]` typed in the editor is resolved on
load into a clickable link and stored again as plain `[[Title]]`, so links
survive a retitle and stay readable in the file. Resolution order: exact
title, then case-insensitive, then substring (substring only ever *offers*
matches for a picker, never auto-opens one). A broken target renders as a red
`[missing: Title]` and is counted in the status bar. Document → Link to
Section… (Ctrl+L) picks a document and inserts `[[Title]]` at the cursor.
**Related fixes:** resolved-link chrome is canonicalised back to `[[Title]]`
in `Project.canonicalize_links()` before every write, so the resolved markup
never reaches disk (a save would otherwise freeze the resolution and break
retitle self-healing). Internal link clicks are handled by a `LinkEditor`
QTextEdit subclass emitting a signal, since plain QTextEdit has no link-click
support. Palette link colour is read via `.color().name()` — `palette().link()`
returns a QBrush. `plain_text()` strips `[[ ]]`, keeping link markers out of
word counts and exports.

### 1.6 DOCX page control — DONE
**Request:** mark chapters/scenes as starting on a right or left page; insert
a blank page when the flow lands on the wrong side.
**Add:** a per-document `page_break` property (`auto`, `left`, `right`,
`next-odd`, `next-even`); DOCX export emits the right
`WD_SECTION_START` / page break.
**PDF is explicitly deferred, not merely missing.** Decision (2026-10-20):
no bundled PDF engine, no LibreOffice or pandoc detection, no new
dependency. The DOCX export is the exact artifact — anyone who needs a
PDF takes that DOCX through whatever converter they already have. This
makes DOCX fidelity the priority for this item and for 1.7's page
numbers; a future PDF export, if it ever happens, is a thin wrapper over
this DOCX, not a parallel renderer.
**Superseded 2026-10-21 — PDF landed, on exactly those terms.** The
author chose the LibreOffice route knowing the deferral's own words.
Implemented (`6e2e81c`): File → Export PDF… runs the normal DOCX
compile into a scratch dir and converts it with the first LibreOffice
on PATH that answers `--version` (deb/snap/tarball names both
`libreoffice` and `soffice`; a found-but-broken candidate — the snap
shim on this machine — is reported with its real stderr plus the install
hint). The first live run (author installed the deb, snap removed —
LibreOffice 26.2.6.3) exposed the CLI's real limit: `--convert-to`
never refreshes fields, so the TOC shipped as its cached placeholder
text, and `w:dirty` on the field didn't move the headless loader
either. Second commit (`0f4c2eb`) drives LibreOffice over a private
socket through a standalone worker (`uno_pdf.py`, stdlib + uno only,
run by whichever Python carries python3-uno — probed, because the
app's venv cannot import it) that calls `index.update()` before
`writer_pdf_Export`; two separate probe messages cover "no
LibreOffice" and "no python3-uno". Private profile, atomic
`os.replace`, scratch cleaned in `finally`. Live smoke: 34-page
letter PDF in 0.9s, TOC with true page numbers (Scene 1.2 → 3,
Chapter 2 → 7 …). Tests: 12 in `tests/test_pdf.py` against stubs
(suite 348 — still no LibreOffice needed). **Open edges:** depends on
`python3-uno` beside LibreOffice (probed; the error says the fix —
`sudo apt install python3-uno`); no page-size / mirror-margin presets
behind the export (none exist for DOCX either — see 1.6's scope
note).
**Implemented (2026-10-20):** `page_break` is a documents column (guarded
migration, default `auto`) carried on `Document`, written through
`update_document`, chosen in the inspector's "Page start" combo and saved
with the document. DOCX export opens a new section per marked document:
`right`/`next-odd` emit ODD_PAGE, `left`/`next-even` emit EVEN_PAGE — the
user chose to keep all five values as the writer's vocabulary even though
each pair is one demand (recto = odd in an LTR book), and Word inserts the
blank page itself when the flow lands wrong. `auto` adds no section, so
existing exports are byte-identical until something is marked. Scope
decision (2026-10-20): page presets (paper size, mirror margins, footer
page numbers, gutter) were NOT part of this item — no spec for them exists
in the repo; capture separately if wanted. Tests: 2 project-level (parity
counts in the DOCX XML, persistence + rejection) and 4 offscreen GUI tests
in `tests/test_app_pagebreak.py`.

### 1.7 Table of contents — DONE
**Request:** TOC with links for ebook, links and/or page numbers for PDF,
page numbers only for DOCX.

**Implemented (2026-10-20):** the compile already knows the unit list, so
the TOC is a view of it. EPUB: every set gains a `toc_page` slot; when
non-empty it is rendered as the first content page with `${Entries}` —
one internal link per unit — on top of the spec-required nav document.
DOCX: export inserts a real Word TOC field (`TOC \o "1-3" \z \u`, no
`\h`, so it shows page numbers only), populated by Word or LibreOffice on
field update. **PDF (2026-10-21, verified in the `0f4c2eb` smoke):**
page numbers land — `soffice --convert-to` alone left the placeholder
in the PDF, so the UNO worker updates every document index explicitly
before exporting; a 34-page smoke shows chapters and scenes in the
contents with their real pages. Scene headings appear too (the field
is `\o "1-3"` and scenes are Heading 2 — same as the DOCX contract).
**Links (2026-10-21):** LibreOffice emits the annotations itself —
every contents entry becomes a `/Link` annot with a direct page
destination, byte-identical with and without `\h` on the field, so the
DOCX contract (page numbers only) stands and the PDF gets clickable
entries anyway. End-to-end smoke through `pdf.export_pdf`: nine entries,
printed numbers equal to link targets, each heading present on its
target page.

### 1.9 Resume where you left off — DONE
**Request:** "When the software is started, resume from where the cursor was
when it was closed the last time."
**Model:** on close, remember the open document id and the cursor position
(character offset in the plain text) per project; on project open, select
that document and put the caret back (clamped to length if the text moved).
Stored in project settings — it is session state about this manuscript, not
app-wide state, so it travels with the project.
**Implemented (2026-10-20):** `save_current()` writes `last_document` +
`last_cursor` on every save path (debounce, document switch, close — so a
crash still resumes); `_switch_project` selects the remembered document
(fallback: first binder item, stale cursor not applied) and `_restore_cursor`
clamps the offset (hand-edited junk settings leave the caret at start).
Scope confirmed with author: startup also reopens the project itself —
app-level `QSettings` key `session/lastProject`, written on successful
switch, read by `MainWindow.reopen_last_project()`; missing/moved/corrupt
project falls back to the empty window with no dialog.
Tests: `tests/test_app_resume.py` (9 offscreen GUI tests; QSettings
redirected to a temp dir so the suite never touches real config).

**Save-failure contract and write-order hardening (2026-10-21):** a
failed write no longer costs the writer their text. `save_current()`
returns False (after its dialog) and every caller that would destroy or
outlive the editor stops there: a document switch (the tree highlight is
put back), a project switch, close (which now asks "Quit anyway?" and
ignores the close on No), the Ctrl+S backup, exports, split, merge,
snapshot, restore, replace-in-project, the revision passes and the four
imports. Tests `tests/test_app_save.py` (9). Project's own write order
fails toward the harmless state: `add_document` writes the file before
the row (never a row without content; the orphan is unlinked),
`split_document` writes the new half and the rows before touching the
original (worst case duplicated prose, never a lost half),
`merge_documents` combines before re-homing history (a failed merge
changes nothing at all), and `replace` plans the whole change set first,
snapshots exactly the documents it will change, then writes — its only
rollback path. Backup pruning deletes only directories matching our own
`%Y%m%d-%H%M%S-%f` stamp, because the backup folder is the writer's and
may already hold anything. The "Draft" seed migration now runs once
(settings flag): the query cannot tell the seed from a chapter the
writer named "Draft", so repeating it on every open was rewriting their
deliberate choice. PDF export timeouts kill the worker's whole process
group, so LibreOffice cannot outlive the cleanup that deletes its
profile and break the next export. The AI client refuses a key — and the
manuscript text travelling with it — over plain http to a non-local
endpoint. The suite's exit-139 segfault was two test modules handing a
Python-owned `QMimeData` to the clipboard (PySide6 6.12 frees it twice at
interpreter exit); both now clear the clipboard in `tearDownModule`.

**The revision passes, second look (2026-10-22).** The rows were deleted
and never put back when the write that follows them failed, leaving the
stored marks on disk with nothing left to accept or reject them — colour
and strikethrough nobody can act on again. The rows still go first (they
must: `save_current()` runs `reconcile_revisions`, which counts the marks
left in the stored HTML and prunes pending rows by sequence order, so
resolving the earlier of two passes would otherwise eat the later one's
row), but a failed write now restores them through
`Project.restore_revisions` — the *same* ids, because the live document
still carries those ids in its surviving marks. Two tests in
`tests/test_revisions.py` pin both paths (accept-all and the passes
dialog). Suite 452, exit 0.

**Imports cannot hurt the project (2026-10-21).** Imports never overwrite:
a second import of a name that is already there lands as a numbered copy
(`Book`, `Book (2)` — `Project.unique_title`, containers only, since scenes
may legitimately repeat a title), and identical content is reported *before*
it is duplicated — compared by folder names and the prose inside
(`Project.subtree_fingerprint`), so a re-export with new file bytes still
reads as the same work. The comparison runs in a throwaway project, so
refusing an import leaves the real project untouched with nothing to clean
up. The merge/skip/overwrite walkthrough (sizes, checkbox-gated overwrite,
abort-and-clean-up) is designed in `docs/IMPORTS.md` and deferred; tests
`tests/test_import_safety.py` (10) pin the floor.

**Keeping a version forever (2026-10-21).** Renaming a backup folder — to
`11Oct26 before the rewrite`, say — makes it permanent: pruning matches only
our own `%Y%m%d-%H%M%S-%f` names, and renamed folders also stop consuming the
kept slots, so a rolling set plus any number of kept ones coexist in the same
directory. Documented in the README; pinned by a test. A Backups list with a
*Protect this one* button (the same rename, with a label field) is designed,
not built.
Suite 443, exit 0.

### 1.10 Paste that doesn't mangle formatting, with preferences — DONE
**Request:** "default behaviour should copy stuff like bold/italics, but not
paragraph formatting or font… paste in something and line break turns into
two line breaks, indents change, line spacing changes, font changes… and if
you do ctrl+shift+V, you lose your bold/italics!… Formatting is imported as
intended to continue the project. So overall para, font, line spacing,
indent, etc don't change — unless user preferences WANT them to change. But
any bold, italics, etc come through… alignment and inline formatting should
survive (alignment is rarely an accident)."
**Model:** paste goes through our own HTML sanitizer (override
`insertFromMimeData`), not Qt's raw import. Default policy: keep inline
character formatting (bold/italic/underline) and alignment; drop font
family/size, paragraph indents, line spacing and block margins; collapse the
double-line-break noise that clipboard HTML carries. Preferences gain a
checkbox section — font, paragraph formatting, alignment, inline formatting
— plus a "keep everything" path for pasting a fully formatted passage on
purpose (which feeds 1.11). Ctrl+Shift+V stays plain-text paste.
**Implemented (2026-10-20):** `LinkEditor.insertFromMimeData` scrubs every
HTML paste through `sanitize_pasted_html()` — a scratch document rebuilt
read-then-write: each fragment gets a fresh char format holding only the
kept properties, each block a fresh block format (alignment kept by
default; margins/indent/line-height only when asked), and blank-paragraph
runs collapse (leading/trailing runs go, interior runs shrink to one) —
except when paragraph formatting is kept, which leaves structure alone.
Anchors ride along whatever the boxes say (a link is content, not skin);
image objects pass through untouched. Policy lives in `QSettings` keys
`paste/fonts|paragraphs|inline|alignment`, read live by
`load_paste_prefs()`; `PreferencesDialog` gains the four checkboxes. Edit
menu gains Cut/Copy/Paste plus "Paste as Plain Text" (Ctrl+Shift+V — Qt
has no plain-paste API, so `paste_as_plain_text()` recovers the words even
from an HTML-only clipboard) and "Paste and Keep All Formatting"
(`paste_keep_all()`, the deliberate full-fidelity path feeding 1.11).
Tests: `tests/test_app_paste.py` (31 offscreen tests; QSettings redirected
to a temp dir, same contract as the resume tests).

### 1.11 Paragraph styles from real formatting — DONE
**Request:** "paste beautifully formatted text, right click, update paragraph
preset or create new preset… now you can save it as a template and change
your whole document accordingly." (Called "styles" in Word, "classes" in
CSS — user settled on paragraph preset/style.)
**Model:** named paragraph styles stored per project (name → block+character
format signature). Right-click in the editor: "Create style from paragraph…"
/ "Update style '<name>'". The toolbar's six hardcoded entries
(`apply_paragraph_style`) become the seed list, user styles join them and
replace them. Scope check before building: this is formatting metadata only
— never a second way to store prose.
**Implemented (2026-10-20):** `SEED_STYLES` holds the six built-ins as
signatures (size/weight/italic + full margins/indent — the complete margins
are deliberate: switching away from Quote now actually resets the 32 pt
instead of leaking it, which the old parallel arrays left behind). A
signature captured by `capture_paragraph_signature()` records the block and
the character format at the caret — underline, family, line-height and
alignment only when present, so a plain paragraph still matches the seed it
wears — and `style_signature_formats()` rebuilds formats from one, writing
only the keys carried (seeds therefore never touch alignment, underline or
family). Storage is one JSON string in the existing project-settings table
(`paragraph_styles`): `save_style_from_cursor` / `update_style_from_cursor` /
`remove_style` write it, `_load_styles` merges user styles over seeds (an
override keeps its seed's place in the toolbar combo), junk settings fall
back to seeds. Editor right-click extends Qt's own menu with Create / Update
/ Delete; Update prefills the style `style_at_cursor()` detects by
signature. Tests: `tests/test_app_styles.py` (11 offscreen tests).

### 1.12 Metadata panel on the right — DONE
**Request:** "panel on the right for metadata — overall metadata setup on
outliner type pages and document specific metadata on chapter/scene/note
pages."
**Model:** the right-hand inspector (currently Synopsis + Section target)
grows a metadata section: project-level fields when a folder/outliner root
is selected, per-document fields on chapters/scenes/notes — the same
`document_metadata` rows `MetadataDialog` edits, inline instead of modal.
Project-level metadata needs a home first (project settings or a table) —
decide at build time, don't invent early.
**Implemented (2026-10-20):** the dialog's Field/Type/Options/Value grid
was extracted into `MetadataTable`, so the inspector hosts exactly the rows
the dialog edits — the dialog keeps Save/Cancel, the panel saves live.
Project-level metadata went to a single JSON string in the existing
settings table (`project_metadata`) — the channel paragraph styles use —
not a new table or a magic document id: no schema, no migration, junk
degrades to no fields. Selection decides the target: `current_id` on
chapters/scenes/notes, `PROJECT_METADATA_TARGET` on folders and with
nothing selected (uuid-hex ids cannot collide); Edit Metadata… follows the
same rule, so menu and panel always edit one target. Live writes are
replace-the-whole-set, guarded against the mid-edit windows inline saving
creates: a row that lost its key while holding a value blocks the write
(rename safety), population runs signal-quiet, and an edit always targets
the id captured when the table filled, never `current_id` at fire time.
Known edges (refine later): the 4-column grid is cramped at the default
270 px splitter width; folder documents' own metadata rows stay in storage
and in search but no longer have UI (the request moves folders to
project-level fields); project-level fields are not searchable.
**Follow-up (2026-10-20):** column order is now Field | Value | Type —
"seeing name:value next to each other is efficient" — with the Value
column stretching while Field/Type size to content. The Dropdown type
and its options column ("Allowed options", briefly renamed "Choices")
are removed at the author's request: no field in their corpus wants a
fixed vocabulary, and a stored legacy "choice" row loads as Text and
heals on the next save. Dates open as a real picker even for
Scrivener's long format ("Wednesday, January 7, 2443" parses via a
second format and normalises to ISO on the next save).

### 1.13 Auto-linked related files (title mentions) — DONE
**Request:** "auto-linking/tagging — if a file contains words that are an
EXACT match for another file, it gets linked in metadata as a related file,
for eg. character or location names. Handy for checking continuity, updating
character sheets, or providing an AI additional info about the
characters/locations in a scene. So if a scene has 'Someone went to Somewhere'
and there are files someone.html and somewhere.html, they show up as related
files in the metadata without needing the writer to do it specifically."
**Model:** computed, never stored. A project-level scan matches whole-word
occurrences of document titles (case-insensitive — the example matches
"Someone" against a file named someone) in each document's plain text; the
result is shown as a "Related files" list in the metadata panel (1.12),
clickable to jump. Shown in both directions where it's cheap: this scene
mentions these characters/locations, and (on a character sheet) which
documents mention it — the continuity view. No new tables, no write path,
nothing to go stale: titles are the vocabulary and the scan runs on view.
Scope check before building: do not turn mentions into stored links or
editable relations — that would be a second source of truth for prose.
**Implemented (2026-10-20):** `Project.related_documents(id)` returns the
two directions from one pass — titles found in this document's plain text
("Mentions") and documents whose text contains this document's title
("Mentioned by") — with word-character boundaries so "Rid" never matches
inside "Somewhere", casefolded both sides, self excluded, every kind's title
in the vocabulary. Plain texts are cached against file mtime+size on the
Project instance, so the scan re-parses only what was written and a stale
string cannot outlive a save. The inspector shows a collapsible
"Related files" tree (hidden with no matches), and clicking a child jumps
like a wiki link — save first, then switch. Nothing is stored: retitle or
rewrite and the next view is correct.

### 1.14 Metadata value types: text area and checkbox — DONE
**Request:** "type of input text area — so metadata has text, date, drop
down selector, and text area. Maybe people may want checkboxes?"
**Add:** two more value types beside the existing text/date/dropdown:
a multi-line text area, and a checkbox that stores "yes"/"no". The
storage column was already free-form, so nothing about the project
format changes; this is a panel feature only.
**Implemented (2026-10-20):** the Type combo in the shared metadata
table (inspector and Edit Metadata dialog alike) offers Text, Text
area, Checkbox and Date — Dropdown, part of 1.12, was removed the same
day as useless to the author. The text area is an inline
QPlainTextEdit with a taller row that commits when the caret leaves the
widget — the line edit's editingFinished equivalent — so live-save in
the inspector never runs per keystroke. The checkbox stores "yes"/"no"
rather than booleans because the value is what a template predicate
compares and what a transparent project file shows a plain-text editor;
"true"/"1" are accepted on load and normalised on the next save.
Switching a row's type carries the current value across where it still
makes sense and falls back to the neutral default where it does not.

### 1.15 Print geometry: page setup per book — DONE
**Request:** "We need size, margins and mirror/gutter — whatever print
on demand needs," escalating a narrower "page size only" ask after the
author named the interior checklist (KDP/IngramSpark ask every line of
it before they will print).
**Add:** per-folder print geometry — trim size, four margins, mirrored
margins and gutter — stored with the book and stamped onto every DOCX
export of it. Decisions the author answered directly: paper size,
margins and mirror/gutter together (the whole POD checklist, not size
alone); a docx→PDF thin wrapper for the converter (recorded under 1.6).
**Model:** geometry lives in its own settings key (`page_setup`, one
JSON dict keyed by folder id) rather than inside `book_settings`,
whose writer replaces the whole row — a template tweak must not be
able to wipe the print geometry. One dictionary validates every path
(`clean_page_setup`): the dialog's accept, storage's set, and the
export's read all call it, so an entry the dialog allows can never be
refused later and vice versa. Trims are the KDP/IngramSpark list plus
A4/A5 and the manuscript standards; anything else is entered as
width/height (marked Custom). Scope resolution mirrors 2.2's link
policy — nearest ancestor folder of the exported documents; one file
carries one paper size, so a mixed multi-book export takes the first
book in binder order (recorded edge, tested).
**Implemented (2026-10-21):** the inspector's **Page setup** button
shows the current geometry as its label ("Page setup — 6 × 9, mirrored,
gutter 0.25 in") and opens a dialog: trim combo two-way-bound to the
width/height spins, five margins in inches (1/16 steps), mirror
checkbox, and a "Use default (no override)" that clears the entry —
which restores python-docx's Letter template exactly as before the
slice. Export stamps every section (1.6's ODD/EVEN_PAGE sections
included): trim and margins in twips, inside margin on the recto's
spine edge, gutter beside it, `w:mirrorMargins` in settings.xml when
mirrored, landscape flag when width exceeds height. The PDF route of
1.6 inherits the size through LibreOffice unchanged — a 6×9 docx
converts to a 432 pt × ~649 pt PDF (LibreOffice's own rounding),
verified live.

**Baseline export behaviour, phase 1 of `docs/TEMPLATES.md` (2026-10-21).**
An export with no configuration at all now comes out readable: **A4** paper
(`DEFAULT_PAGE_SETUP`; Letter was python-docx's template, never a decision —
and the A4 baseline is deliberately *unmirrored*, since a bare A4 export is
not necessarily a bound book), and **every exported document starts on a new
page** — the author's own ask, "no point mind-reading users". The first
document is exempt (nothing precedes it, and a leading break would render a
blank first page). Page starts are now resolved **once**, in
`_compile_scope`, so all exporters agree: an explicit setting wins
nearest-first (document → chapter → book → series), and `auto` — the stored
default — inherits and then falls back to a new page. The inspector's
vocabulary grew to carry that honestly: *Automatic (new page)*, *Continuous
flow*, *New page*, and the four parity demands, `project.py` accepting
`flow` and `page` alongside them. Verified on the author's own project: the
Second Book subtree exports at 8.27 in with four breaks (folder heading,
chapter, each scene). **The rest of the phase landed the same night:** DOCX
gains a **title page** — the selected container's name (the project's title
for a whole-project export, the document's own name for a single selection),
with the author printed only when the project metadata actually carries an
`Author` — then the **TOC field after it**, then the body, so the contents no
longer stand in front of the book's opening; chapters and other containers
**open recto automatically once a real print trim is set** (a plain A4 export
sprouts no blank pages, asserted by test); and the HTML export marks
`break-before: page` between units, inline so a user template's own CSS keeps
the last word. Still to come: the same breaks in EPUB (inherent per unit) and
form-feed consideration for text, plus the template-discovery phases
(`docs/TEMPLATES.md` §9). Suite 452, exit 0 (tests kept being added after this record was written; this
is the verified count at release). A comparison against other
writing software, with an easy batch queued, is in `docs/IDEAS.md`.

### 1.16 The metadata section, in boxes — OPEN (outlined 2026-11-01)

**Request:** "Right now the metadata is just undifferentiated text with no clear
logic to it. We could organize it better, create collapsible sections to
prevent overflow, create height limited sections for where text boxes pull
metadata from templates and it is long..."

**Model:** one metadata section (1.12), its rows sorted into collapsible
groups — the common facts visible without scrolling, the long ones unable to
push the editor off-screen. The grouping agreed on 2026-11-01:

- **Basic** — always open: Kind | Word target | Template set.
- **Synopsis** — collapsible, height-limited: the synopsis box scrolls instead
  of growing.
- **Format / Scene** — collapsible: Page start | Scene divider | Cover.
- **Advanced / Custom** — collapsible: custom metadata keys, height-capped so
  a long value never pushes the editor off-screen.

**Why it was still open:** the outline lived only in chat; the Nov 1
session was pulled into the binder/storage rewrite before any of it was
built. The section in `app.py` was still one flat `QVBoxLayout` (2191-2226)
with an uncapped synopsis box.

**Implemented (2026-11-02):** the inspector's metadata section is four
groups, each with a header that folds it away (`Fold` in `app.py`): **Basic**
(Type, lock, word-count goal, template set), **Synopsis**, **Format / Scene**
(Page start, Scene divider, Cover, Page setup, unpublished links) and
**Advanced / Custom** (the field table with its Add/Remove buttons). Basic
and Synopsis open by default, the other two closed. The synopsis box is
capped at 120 px and the field table at 220 px, so a long text scrolls
inside its box instead of stretching the panel, and a group takes the
height it needs and no more — the surplus in a tall window stays at the
bottom of the inspector instead of opening a gap under one box. The rows
themselves are unchanged: the same widgets, the same storage, the same live
saves; only the arrangement is new. Suite 903, exit 0.

**Relations:** serves 2.1d's Assumption H — the metadata bar is the home for
document/book facts; the 2.4c/2.4d open edge ("features replacing metadata")
lands in the same section.

**Still open:** Page start itself — `docs/UI-NOTES.md` §9 says it left the
inspector, but `app.py` still shows it for every selected document and
still saves it, so one of the two is wrong. It sits in Format / Scene until
that is settled. — **settled by 1.17: the row is gone and the page start
belongs to the template's design panel.**

### 1.17 Type is a role, the template is a tick — DONE (2026-11-03)

**Request:** the author's own words, in three messages. "The page start feels
like it belongs in a template... I was thinking that since we already have
the 'Type' dropdown, we could optimize it to handle templates as well... We
can just pack the yaml in the header and be done with it. One document, one
file. Less things to break. When type is not template, below it, there will
be a dropdown to apply template which lists all the templates the database
knows." Then the refinement: "we should not keep templates in type. So the
original plan of having a checkbox for template is better than adding it to
type. Document role is the correct type model. However, we provide only two
roles initially, that are as close to the truth on disk as possible — folder
and document — with a text box next to the dropdown to add new types." Then
the lock: "'Copy default templates to customize'... Only types for folders
should appear for the type box for folders. Only types set for documents
should appear for documents... And same with metadata. New metadata entries
created for documents should show for all documents. New metadata entries
created for folders should show for all folders... if a user adds metadata
{POV} for one scene. There should be empty metadata field for {POV} in other
scenes... The DB maintains this whole metadata panel business. Tracking known
templates, known types, metadata fields by type, etc."

**Model:** Type is a document *role*, with two base values close to the truth
on disk — **Folder** and **Document** — and the template is a separate mark, a
checkbox. The database keeps the catalogues the panel reads: roles per group
(folder / document) and metadata field names per group. A template is one
file with its yaml in the head.

**Implemented (2026-11-03):**

- **The Type row** offers the roles of its own group only — a folder's
  dropdown never shows a document's role and the other way round. Beside it
  a **"New type…" box** adds one: the name joins that group's catalogue in
  the database, so every document of that kind that follows can pick it.
- **Two base roles.** `default_type` returns Folder for a folder and Document
  for everything else; a folder with book settings is no longer promoted to
  "Book" — "Book" is a role the writer adds. The *physical* kind still
  travels beside the role, and the Outliner's Kind column, the binder
  tooltip, the related-files list and the link picker show it: the role is
  the writer's own label and says nothing about what the thing is.
- **The Chapter rule (1.13) follows the role** as `docs/MODEL.md` says, but a
  chapter keeps the rule until the writer gives it another role
  (`Project._chapter_exact`) — the two base roles would otherwise silently
  loosen it for every chapter.
- **The per-group metadata catalogue.** A field created on one scene shows,
  empty, on every other scene; the same holds for folders. The catalogue
  names the fields, the documents hold the values, and only filled values
  are stored. It lives in the `settings` table, so no schema change was
  needed.
- **The template tick** sits beside the Type row: ticked, the document is a
  template — its body is markup rather than manuscript, it stays out of the
  book, and the sidebar swaps to the template design panel; unticked, the
  ordinary metadata comes back. The mark is the document's own metadata
  (`Template`) and travels into the file's head as `template: yes`.
- **The apply-template dropdown** below the Type row lists every template the
  project knows — the sets' own slots, the one-file templates in
  `templates/`, and the documents marked as templates. Beside it, the note
  **"Copy default templates to customize"** puts an editable copy of the
  shipped default set in the project's `templates/` folder.
- **One-file templates**: a template is one file with its yaml in the head
  (`templates/<name>.html`), discovered, listed and applied; the design panel
  edits that head, and the export reads the page start it states.
- **Page start moved** from the inspector's Format / Scene group into the
  design panel, where a pick writes the `New Chapter:` / `New Scene:` lines
  the print-config vocabulary speaks. This settles 1.16's open contradiction:
  `docs/UI-NOTES.md` §9 recorded the decision at commit `bed28c5` and the
  live widget landed in the same commit, so the docs and the code disagreed
  from the start. The row is now really gone; values older projects stored
  are still honoured by the export, silently.

Suite 923, exit 0.

**Relations:** 1.16 (the boxes — the Type row, the tick and the apply-template
row live in Basic), 1.13 (the Chapter rule), 1.2 and 1.12 (the template design
panel), 1.6 (page starts), `docs/MODEL.md` (the role model),
`docs/UI-NOTES.md` §12.

**Still open:** a one-file template is listed and applied but is not opened in
the editor; a template document does not appear in the Outliner's Templates
listing; custom metadata values stay in the database — they are not written
into the file's head (pre-existing, not 1.17's doing).

## Phase 2 — books, variants, and conditional content

### 2.1 Book-level compile settings — DONE

> **Superseded in part, 2026-10-26:** the *binder* half of this design — the
> TEMPLATES area flag, templates-as-binder-folders, format-variant folders
> matched by title, `book_settings["template"]` as a folder id — is **deleted**
> (`1b4005d`; `docs/TEMPLATES.md`, status *decided*). It rendered nothing: the
> author's project had a template root nested in a template root, 0-word
> template pages, and no book assigning a template. What survives: compile
> scope rules, counters, cover discovery (rehomed onto the export's own
> folders), the export dialog's scope summary and set dropdown. The
> replacement model: templates are folders of HTML fragments on disk,
> discovered under `<project>/templates/`, listed in the binder's Templates
> folder with the three shipped sets duplicable — items 3–12 of TEMPLATES §9
> (the *mark* that briefly replaced the area flag is itself deleted; 2.4b).

**Request:** "if book is MH/TI/PF, front/back matter is this, that or the
other — set on the book folder level; if an image is present in the folder it
is the book image."
**Model:** the request grew through follow-up Q&A into the anatomy below
(decisions the author answered directly: the doc templates are files in the
template set's `documents/` directory; a format variant replaces the basic
content rather than adding to it; the export dialog offers a set dropdown
while the four File-menu entries keep picking the format).

- **A TEMPLATES area, not a book flag.** Any folder can be flagged as the
  special templates root. It renders below the regular folders in binder and
  outliner wherever it sits (display-lift), and its subtree never exports as
  manuscript — template pages reach output only through compile settings.
- **A template is a direct child of a templates root** ("default",
  "A Fictional Book", …). Its subfolders are format variants, matched by
  title: epub → `("ebook", "epub")`, docx → `("docx", "print")`,
  html → `("html", "web")`, text → `("text", "txt", "plain")`. A matching
  variant folder *replaces* the template's basic content for that export. A
  child folder titled "Back matter" (casefold/strip) marks the pages that go
  after the book; everything else in the template is front matter.
- **Association** — `book_settings["template"]` (an explicit folder id) >
  title match against the book folder > `default`, applied when a folder is
  directly selected. Chrome fires only when the folder is in the raw export
  selection, or on a whole-project export where the override/match holds; a
  chapter- or scene-only selection gets document-level treatment only, and
  the project header/footer/toc_page wrap only a whole-project compile.
  Nested dressed folders bookend inward (wrapper outside, book inside).
- **Document templates** are named slots in the set: `documents/<name>.html`
  (or `.txt`) plus the builtin `bare` = `${Content}` — "nothing, not even
  title". Stored per document in the `doc_template` column; a name the set
  does not have falls back to the default slot rendering.
- **Counters** never credit template pages: no chapter/scene numbers, no
  contribution to `${WordsTotal}` — front matter is not manuscript.
- **Cover** — explicit `book_settings["cover"]` path, else auto-detect
  `images/<folder_id>/cover.{jpg,jpeg,png,webp,gif}`; EPUB embeds the first
  dressed book that resolves one (`properties="cover-image"`).
- The originally drafted **expose-keys metadata allowlist** (which metadata
  keys a book publishes) is deferred — see open edges.

**Implemented (2026-10-20):** three commits — storage (`de405b7`), compile
(`e09a78f`), window (`87f5a3e`). Columns `is_templates_root` and
`doc_template` arrive through the guarded-migration pattern; book settings
live as one JSON string in the existing settings table (blank removes).
Compile side: `_export_documents` expands folder selections but excludes
template subtrees, `_chrome_books`/`_variant_folder`/`_template_pages`/
`_assemble_with_book_chrome` dress the export, `compile_units(fmt=…)`
carries template-aware counters, and all four exporters honour the scope
rules (EPUB adds the cover, DOCX breaks pages around injected template
pages). Window side: the inspector shows the TEMPLATES flag, book-template
combo (Automatic = match by name), cover row and — on chapters/scenes/notes
— the document-template slot combo; binder and outliner render the lifted
template area in italic; both trees allow multi-select and export gathers
the union of their selections (empty = whole project, replacing the old
"Nothing selected" warning); `ExportOptionsDialog` now shows a scope summary
plus the set dropdown instead of the per-document checklist;
`_project_total` skips template words. Tests: `tests/test_book_compile.py`
and `tests/test_app_booksettings.py` (12 each, suite 202).
**Open edges:** expose-keys deferred; the DOCX TOC field still lands before
front matter (page breaks work, ordering not yet considered); "Back matter"
is a title convention, not a flag; outliner word *tooltips* (project
rollups) still count template words under a regular folder even though the
project total does not; the binder keeps its current item selected, so a
whole-project export needs an explicit deselect (the scope summary makes
this visible); a stored template/slot id that no longer exists shows as
Automatic/Default and heals on the next actual change.

### 2.1a Per-document section flag — REMOVED (2026-10-31)
**Built, tested, then removed the same day.** `layout.py` gained a `section`
(`body`/`front`/`back`) fact with folder inheritance and eleven tests, on the
theory that 2.1's "a folder titled *Back matter*" convention needed a flag.
The composition model in 2.1b settled it the other way: front and back matter
are **template decisions over ordinary binder folders**, so nothing would ever
read a per-document section — a stored instruction no part of the program
follows, and one that teaches the wrong design besides. Storage, tests and the
`docs/STORAGE.md` entry were reverted; the record stays here so the idea is not
re-proposed. (One thing the work did establish, which 2.1 still wants:
`page_break`, inclusion via the no-export template, PoV/Location as title
substitutions, and indent flattening inside the template's styles are each
already owned by an existing mechanism.)

### 2.1b The template composes the book: ordered pieces around a content slot
(captured 2026-10-31, author's examples — planning only, no code yet)

**Request, in the author's words.** Templates decide what to include and
exclude, as an ordered sequence of pieces with the manuscript somewhere in it:

```
Book                    Book                      Book
  frontmatter             frontmatter-epub          Frontmatter-title
  content                 content                   frontmatter-dedication
  backmatter              backmatter-epub           frontmatter-TOC
                                                    content
                                                    Backmatter-book2-preview-scene
```

or, for a single document, just `Scene / Title / Content`. Pieces are named
freely (`Frontmatter-title`, `Backmatter-book2-preview-scene`), may be
format-specific (`frontmatter-epub`), and any number may appear — so front and
back matter are the writer's composition, not two fixed slots.

**How a piece resolves (author, 2026-10-31).** By default an export publishes
the binder in binder order — that is the whole of the default behaviour, and it
already works. A piece *names binder content to pull in*: `frontmatter` includes
the front matter, and a piece may name a specific sub-folder or file instead
(`Backmatter-book2-preview-scene`). Pieces are therefore references interleaved
with the manuscript, not new storage: the writer's binder stays the source of
the words, and the template says what of it this book is made of and in what
order. Front and back matter are ordinary binder folders that the default
binder-order publish leaves out and a template can include by name.

**The name must match — that is the idiotproofing (author, 2026-10-31).**
Anything a template includes must have a binder folder whose name matches the
piece's slug: `frontmatter` needs a folder called `frontmatter`, and
`frontmatter-${type}` needs literal `frontmatter-docx` / `frontmatter-epub`
folders, one per format, so a DOCX or EPUB export picks the right front matter
by name rather than by a suffix the exporter guessed. Inside an included folder
nothing is constrained: scenes and chapters can be called anything and the walk
simply goes through them in binder order. So the strictness is only at the
seam where a template points at the binder — exactly where a silent
near-miss would put the wrong page in the book.

Built so far: `piece_slug` (casefold, runs of punctuation/space → one hyphen,
so `Front Matter`, `front-matter` and `front matter` are one target while
`frontmatter` stays distinct), `piece_name` (fills `${type}` with the export
format) and `Project.resolve_pieces`, which returns `content` / `document` /
`absent` per piece in the order written. The binder's own organisation is free
— `frontmatter-epub` and `frontmatter/epub` both work, the second read as a
path — so the strictness is the *name*, not the layout. A missing piece is
absent, and the assembly skips it **silently**: no export ever prints an error,
which is the author's rule, so "absent" exists for previews and tests rather
than for the writer's screen. 23 tests in `tests/test_template_pieces.py`.

**Assembly built (2026-10-31).** `Project.assemble_parts` returns the ordered
parts every exporter will consume: `content` (the manuscript, selection or whole
book) and one part per included folder, compiled the same way so a folder
expands to its chapters and scenes in binder order. Absent pieces are dropped
without a word; a piece the selection already contains is taken out of the
content part, so selecting everything prints each page once, where the template
put it. `compile_units`/`_compile_scope`/`_export_documents` gained an
`exclude_ids` argument for exactly that (a selection cannot express exclusion
when the root itself is selected).

**Variants inside a folder are automatic — the recommended shape** (author,
2026-10-31). A template that simply includes `frontmatter` needs no `${type}`
when the folder holds `frontmatter/epub` and `frontmatter/docx`: the walk keeps
the child named for the export and drops the other variants. Pages sitting
directly in the folder are direct content and appear in every format; a folder
with no variant subfolder is used whole in every format, which is the old
behaviour unchanged. `${type}` remains for flat naming, but adding a format
should mean adding a folder, never editing the line.

**What this supersedes.**
- The **two chrome slots** (`project_header`, `project_footer`, `toc_page`) —
  exactly two, rendered by name lookup. The model needs an ordered list.
- 2.1's open edge *"Back matter is a title convention, not a flag"*: with this
  model there is nothing to flag — a piece in the template is the mechanism.
- **2.1a's per-document `section`** (`body`/`front`/`back`): with the template
  composing front and back matter, a document-level section has no reader.
  The storage stays on the branch (committed, tested, harmless) but should be
  dropped unless a real reader appears — a writer's own closing pages are
  simply documents last in the binder order.

**Current state against the model (evidence, not assumption).**
- `text` and `html` exporters render `project_header` + body + `project_footer`
  in the right order, by name.
- `epub` wraps `project_header` and `project_footer` around **every unit's
  page** — a non-empty header repeats on every page.
- `docx` (and therefore `pdf`) **ignores both fragments entirely**, writing its
  own baseline title page and then the TOC field.
- No mechanism expresses an *order* of pieces, or a format-specific piece
  beyond the existing `epub/` and `pdf/` folders (§1.3).

**Order of work (planned).**
1. **Storage — DONE (2026-10-31).** One config line, `Pieces: frontmatter,
   content, backmatter`, read into `TemplateSet.pieces` (templates.py) and
   parsed by `parse_pieces`; nine tests in `tests/test_template_pieces.py`
   pin the default, the order written, the format-folder override and
   inheritance, the blank fallback and kept duplicates. The config block is
   the set's existing manifest, so no second file format was introduced.
   Resolution rule for the assembly step: `content` is the manuscript, every
   other name is binder content matched by title, and a name that matches
   nothing is skipped.
2. **One assembly step** that every exporter calls: it yields the ordered
   parts — pieces before `content`, the units, pieces after — with format
   variants resolved. The exporters stop knowing about chrome.
3. **Placement per writer — DONE (2026-10-31).** `text`/`html` concatenate
   the parts in order; `epub` gives each piece's units their own XHTML page
   instead of repeating chrome; `docx`/`pdf` write the parts as paragraphs in
   order with the TOC field placed at the content boundary — after front
   matter, before the manuscript. A set that states Pieces also **drops the
   automatic DOCX title page**: content is never mandated, and the writer's
   front matter carries whatever a title page should say (the shipped sets
   should gain a title-page piece — still the inference to confirm). A set
   that states no Pieces keeps the old path exactly, chrome slots and title
   page included.
4. **Then** the per-format variants the examples show (`frontmatter-epub`)
   fall out of the same resolution the `epub/` folder already uses.

**Not yet observed, and it composes with this (author, 2026-10-31).** The UI
pass covered Section layouts, separators, styles, text layout and document title
links, but not the format's **Transformations**, **Replacements**, Footnotes &
Comments, Statistics, Tables, Page Settings and Compatibility pages.
Transformations and Replacements *change the text* (and can add it), so they are
a processing stage over assembled output rather than a property of one piece:
step 2 must therefore leave a seam between "assembly" and "the exporter writes
it", and the screenshots will settle whether replacements reach template pieces
or only the content slot. Steps 1–2 do not depend on the answer.

**Decided (author, 2026-10-31) — these two were mine to get wrong.**

- **The look is the *styles* system, shared by the editor and by templates.**
  A piece is styled; a format differs by a different stylesheet, a specific
  override, or a different piece (`frontmatter-epub`) — never by an exporter
  inventing formatting. So front/back matter keeps its styles, and "make Word
  match arbitrary HTML" is not the model: DOCX honours the template's named
  paragraph and character styles, as the body already does. Only the pieces
  need to differ between outputs; the formatting may be identical.
- **No mandated content, so there is no automatic title page.** How would the
  app know a title page wants a title alone, or a title and author, or a series
  too? A title page is *a page the writer designs* — literal words ("A Fictional Book
  Human, by Vidyut Gore") or placeholders, their call. The Phase-1 automatic
  DOCX title page therefore goes. *Inference to confirm:* the **shipped**
  templates gain a title-page piece carrying the title/author placeholders, so
  the default export still looks like something while the app mandates nothing.

**Assumptions still open (the rest are settled: A, D, E, F, G are as written).**
- D: EPUB pieces become their own pages; the spec nav document is unchanged.
- E: a piece naming binder content resolves by **title**, the way wiki links,
  template association and book selection already do (not by id, not by path).
- F: a piece naming something that does not exist is simply absent — the same
  rule a fragment with nothing to fill follows — rather than an error.
- G: "the front matter" is a binder folder the template names; there is still
  no per-document flag, and the default export keeps publishing plain binder
  order for a project that has no template at all.
- A: piece order lives in an explicit manifest, not in filename order.

**Not done tonight, deliberately:** no exporter was touched. Every change above
depends on the ordering model in (1), and building it against the two-slot
abstraction would have been thrown away.

### 2.1c Page size: the topmost level of the export decides
(captured 2026-10-31, author's direction — planning only)

A book template at 6×9 with scene templates at A4 exports as 6×9 throughout: a
book never contains A4 pages. The individual A4 templates matter when *they*
are the export — selecting a scene alone exports at A4. So geometry resolves
from the topmost level of what is being exported, and nested assignments dress
their own prose without changing the export's paper.

*Assumption E:* this is already what `_docx_geometry` does for a whole-project
compile (2.1's "one file carries one paper"), and the work is to verify the
document-only case uses that document's own geometry, plus a test pinning both.

### 2.1d Compile settings, consolidated: one place per fact, not nine panels
(captured 2026-10-31, author's direction after the full Format Designer tour)

Scrivener puts nine pages in a per-format designer. Most of what they hold is
useful; the *panels* are not the feature, and Lekhak should not reproduce them.
The author's verdict, in order: **page setup is the one handled nicely and it
already lives as a compact control in the inspector's metadata bar — that is the
shape to copy; the replacements table does not really work well and is not worth
copying; everything else is useful but should arrive more efficiently than as a
panel each.**

The rule this settles: **a compile setting appears once, beside the fact it
changes**, never as a page in a designer. Where each observed panel's
functionality lands:

| Scrivener panel | What it really holds | Where it goes in Lekhak |
| --- | --- | --- |
| Page Settings | trim, margins, headers/footers, facing pages | **Already the metadata-bar Page setup control** (2.1's geometry + mirrored heads/folios). Extend that control, don't add a panel. |
| Transformations | dumb quotes, italics↔underline, ordinals, indent-per-level | **Template manifest switches** — they change text, so they belong to the template that owns the words, not to a global page. |
| Replacements | a regex find/replace table | **Dropped as a feature** (author: does not work well). If a real need appears, a literal-only two-column list in the manifest — no regex, no case/whole-word matrix. |
| Statistics | which text counts, footnotes/comments, spaces | **The existing count surfaces** (status bar, outliner rollups) gain the counting rule; nothing new to open. |
| Tables | stitch adjacent tables, width | **Output-format detail, decided automatically** — DOCX behaviour with no switch unless a real case appears. |
| Footnotes & Comments | numbering, marker style, comment export | **Template/format defaults** (one section of the manifest), since DOCX already emits real footnotes. |
| Compatibility | flatten footnotes, list indents, hyperlink styling, highlight colours | **Automatic** — these are "make Word behave" fixes with one right answer; ship them on. |

What this means for 2.1b's order of work: the processing seam is real but
**small** — one place where the assembled parts pass through the manifest's
transformations before an exporter writes them. It is not a settings subsystem,
and there is no per-format designer to build.

*Assumption H:* the inspector's metadata bar is the home for *document/book*
facts (page setup already is), while template-wide processing lives in the
template's manifest — so the two never compete for the same switch.


### 2.2 Conditional content and link stripping — DONE
**Request:** "only internal links to documents getting published should be
available to publish; all other internal links stripped."
**Model:** resolved at compile time, not at write time — the editor keeps
storing bare `[[wiki]]` markers and its `lekhak://` anchors unchanged, so a
link's meaning depends on what the current export contains (the same
manuscript can be whole-project for EPUB and a single chapter for a draft).

- **Resolution is exact-title only**, matching what the editor never
  auto-opens: a casefold-equal title resolves (first in binder order on a
  duplicate), a substring-only or ambiguous target is treated like a broken
  one — picker candidates are the editor's job, not compile's.
- **Published links** (target in the exported set) become real links wherever
  the format can carry one: `#unit-N` fragment anchors in single-file HTML
  (one invisible anchor per unit), `section-N.xhtml` in EPUB, Word-internal
  `w:hyperlink`/`w:bookmarkStart` pairs in DOCX (python-docx has no hyperlink
  API, so the elements are built by hand; every heading gets a `d<id>`
  bookmark). Plain text keeps the words — a text file cannot link.
- **Unpublished links** (broken, ambiguous or outside the set) follow a strip
  policy: `strip` removes the link's words from the output entirely,
  `plain-text` (the default) keeps the words with no link, `keep-as-note`
  keeps them plus " (not in this export)". The policy is per **source**
  document: the first ancestor folder with `book_settings["links"]` wins, else
  the project's `link_policy` setting (a Preferences-dialog row; inert when no
  project is open). Published links ignore the policy — a link to a published
  document is never stripped.
- **Header/footer slots** run through the same link pass, against this
  compile's document set, with the global policy (project chrome belongs to no
  one book). Selection exports have no project chrome, so this only matters
  for whole-project compiles.
- **Conditional blocks** live in template slots only, never in document prose:
  `$if(Key)`, `$if(Key = Value)`, `$if(Key != Value)`, `$else`, `$endif`,
  evaluated in `TemplateSet.fill()` against the same values that fill
  `${...}`. Conditions compare case-insensitively; a bare key is true when
  its value is non-empty; the operators need surrounding spaces so keys may
  contain them; nesting works. A hand-edited slot degrades to something
  printable — an unmatched `$if` keeps its branch state to the end, a stray
  `$else`/`$endif` is dropped, markers never leak into output.

**Implemented (2026-10-20):** two commits — core (`4bb615f`), window
(`07c2c0b`). `TemplateSet.conditionals` plus `_compile_scope`/`_prepare_links`/`rewrite_wiki_links` in `project.py`
(scope extracted from `compile_units` so header/footer strings run through the
same machinery), thread-through in `compile_units`/`_render_document`, unit
anchors in `export_html`, bookmarks and the token-splitting run builder in
`export_docx` (`\x02<id>\x03<text>\x04` survives `plain_text`). Window: the
inspector's folder book section gains an "Unpublished links" combo
(Automatic = project default), Preferences gains the project-wide default
row. Tests: `tests/test_content_rules.py` (14) plus three app tests, suite
225.
**Open edges:** a `$if(...)` condition value containing `)` cannot be matched
(the piece parser stops at the first one); conditionals do not apply to DOCX
(the DOCX path has no template slots); an epub/html link to a *scene* lands on
its chapter's unit/section top — DOCX gets scene-level precision via the
scene's own heading; header/footer links always use the global policy, never a
book's; the exact-title-only rule means a deliberately shortened
`[[Chapter]]` for "Chapter Two" is an unresolved link and follows the policy
rather than silently picking a target.

### 2.3 Importers and full round-trip export — DONE
**Request:** "eventually, importer/exporter." Then: "I can give you a huge
dump from Scrivener… books + WIPs (so you can also test behaviour under
load…) Or we can do just one book at a time." And: "Don't import the old
export. I can give you a fresh export (with latest edits)."
**Model:** start with the author's fresh Scrivener multi-document markdown
dump (the `===DOCUMENT===` block format with Type/Parent/Position headers —
already proven by the earlier Astray migration audit, 0 warnings), one book
at a time, structured as chapters/scenes/kinds the way the dump declares.
The 2026-10 archived exports in `Writing/Scrivener/*.md` are stale and are
not a source. Serves two purposes at once: the author's real corpus in
Lekhak for daily writing, and a load fixture for exports/word counts under
real volume. Further targets: plain Markdown import (pandoc-free), yWriter 7
`.yw7`, Scrivener `.scriv` XML as stretch. Export already covers txt/html/
docx/epub; PDF and a clean Markdown export join it.
**Partial (2026-10-20): Scrivener dump import is done.**
`lekhak/importer.py` parses the `===DOCUMENT===` format (blank-line header
fields, multi-line Synopsis values recognised by a known-key whitelist,
markdown-ish bodies converted to the rich text Lekhak stores: paragraphs,
`*em*`, `**strong**`, `#` headings) and places documents with an
ancestor-stack walk — parent-by-title is safe because the dump is
depth-first binder order, verified against the full 326-document corpus
with zero orphans. Books appear only as parent titles (folders are never
in the dump); the import rebuilds every book as its own folder — the
splitting is the app's job, per the author's correction on first use
("I don't know how to split it when I import… I thought you would do
it"), not a book-by-book picker. Duplicate chapter titles across books
cannot cross-wire because each book is placed by its own stack pass.
**Shelf handling:** a generic container whose direct children are all
chapters (Scrivener's "Manuscript": 8 works stored as chapter-level
docs) is not a book — each child is, and the author's reference
structure shows A Fictional Book and Terminally-Immortal inside
Manuscript beside them too. Since folder blocks are absent from the
dump, that containment is an assumption, recorded here as such: with
exactly one shelf in the dump, the shelf becomes a wrapper folder and
every book imports inside it, matching the author's screenshot; with
none or several, books import at the top level where the dump cannot
misplace them. Real books are never mistaken for shelves because each
has a book-level scene (a title page) among its children — verified
against all three top-level containers of the real corpus.
Scrivener's unset sentinels ("No Status", "No Label", PoV "None") are
dropped rather than stored; every real value (PoV, Location, Date, Quote,
Source, Keywords, custom outline keys) lands in document_metadata, and
Synopsis lands in the synopsis column — the meta fields are headers and
never leak into prose (asserted by test). The dump's Date column
imports typed as `date` so it opens in the panel's picker directly, not
as text ("dates changed to dates directly"). Whole-library import of the
author's real dump: 3 books, 330 documents, 406k words in ~0.15s — the
2.6MB file is not too big. File menu: "Import Scrivener Dump…" into the
open project. Still open in this item: nothing — PDF
joined the export kinds on 2026-10-21 (1.6), so the round trip covers
text/html/docx/epub/markdown/pdf.
**Imports land in the open project only (2026-10-21):** the four
"…into New Project…" menu variants (dump, markdown, yWriter, Scrivener)
were removed. They forked the writer's session and quietly owned a new
folder for the sake of a source program's habits, which is not this
app's job: whoever wants a fresh project makes one first
(File → New Project) and imports into it. `test_app_import` loses the
test that covered the deleted path; suite 413.
**Design note for the Markdown import (author's idea, 2026-10-20):**
"insert specific strings as dividers for the splitter — [this string]
begins a chapter, [that string] begins a scene." The importer should
not impose a heading convention: the writer names the divider strings
(defaults `#` for a chapter, `##` for a scene) in a small import dialog
and the file splits on those, pandoc-free, everything between dividers
becoming the document's prose. Same placement stack as the dump import
once the blocks exist.
**Markdown import implemented (2026-10-20):** one commit (`d5e50af`).
File → "Import Markdown File…"; the dialog names
the book title (default: file stem) and both divider strings (either may
be emptied to drop that level). A divider must start the stripped line
and be followed by whitespace — `##nope` is prose — and the scene
divider is tested first so the more specific string wins when both
match; a level-3 `###` heading is prose under the default `#`/`##`
dividers, exact strings by design. Prose before the first divider
becomes the book's opening scene titled after the book, so a cold open
is never dropped. Bodies reuse `markdown_to_html`; placement is book →
chapter → scene, scenes before any chapter landing under the book.
Tests: `tests/test_markdown_import.py` (16), suite 293.
**Open edges (markdown import):** the opening scene shares the book's
title, so `[[Book]]` resolves to the folder (first in binder order) —
harmless, but a deliberate link to the opening scene needs another
title; setext headings (`Title` over `===`) are not dividers; no
front-matter/synopsis extraction — a file is prose and structure only.
**Markdown export implemented (2026-10-20):** one commit (`b8ec0a4`).
File → "Export Markdown…": clean structure only — `#` chapter / `##`
scene headings, prose converted back from stored HTML with bold and
italic kept (`markdown_from_html` mirrors `markdown_to_html`'s subset),
wiki links as plain words via the 2.2 text fallthrough. No template
chrome, no book-settings pages, no template-set dialog — the inverse of
the import, deliberately not a fifth chrome-aware format. Full-corpus
smoke: the author's dump exports to 2.4M chars in 0.04s and re-imports
in 0.08s with 46 chapters / 280 scenes intact. Tests:
`tests/test_markdown_export.py` (8), suite 301.
**Open edges (markdown export):** resolved `[[links]]` become plain
words, not real markdown links; a standalone top-level scene exports as
`##`, which on re-import parents under the preceding chapter rather
than back at book level; book-settings numbers/chrome are absent by
design — clean markdown is prose and structure only.
**yWriter 7 import implemented (2026-10-20):** one commit (`53c5076`).
File → "Import yWriter Project…": the `.yw7`
single-XML format (root `YWRITER7`, scene prose inline in
`SceneContent`) parsed with no dependency, chapter order from the
`CHAPTERS` list and scene order from each chapter's `ScID` references;
Notes/ToDo/unused chapters and scenes skipped the way yWriter's own
compile skips them; empty normal chapters kept as structure; scene
`Desc` becomes the synopsis; utf-8 tried first, utf-16 (yWriter for
iOS) as fallback. Scene HTML is stored as written — Qt reads that
dialect directly. Tests: `tests/test_ywriter_import.py` (10), suite
311.
**Open edges (yWriter import):** no real `.yw7` from the author's
library was available to smoke-test against — the format is verified
against PyWriter's reference reader and synthetic fixtures, so a
file that trips the parser will show up as a failed import with the
XML error quoted; part-level chapters (`SectionStart`) import flat as
chapters rather than as folders; character/location/item reference
data and chapter Descs are not imported; yWriter 5 side-car RTF files
are not read (6+ inline content only).
**Scrivener 3 import implemented (2026-10-20):** one commit
(`54a6c18`). File → "Import Scrivener Project…" / "…into New
Project…": the `.scriv` folder package read directly — `.scrivx`
binder walked down the DraftFolder subtree only (Research, Trash and
the rest stay out, as in Scrivener's own compile), Folder→folder and
Text→scene in binder order, `Files/Data/<UUID>/content.rtf` through a
new minimal RTF converter (paragraphs, bold, italic, line breaks,
cp1252 hex escapes, `\uN` with uc fallback skips, Scrivener's named
characters, destination groups dropped whole), `synopsis.txt` as the
document's synopsis. Smoke on the author's real
`my-novel.scriv`: 50 folders, 325 scenes, **408k words in
0.46s** — the same corpus size the dump import reports, a cross-check
between the two paths — exporting to 2.4MB of clean markdown in 0.05s.
Tests: `tests/test_scrivener_import.py` (16), suite 327.
**Open edges (Scrivener import):** only the DraftFolder subtree is
read — a manuscript kept in a top-level binder Folder (as in the
author's `Someone-trilogy.scriv`, whose DraftFolder is empty and whose
prose sits in "Recovered Files") must be moved under the Manuscript
in Scrivener first; adjacent RTF runs with the same style are wrapped
per run rather than merged (renders identically, cosmetically noisy
HTML); RTF read as latin-1 with errors replaced — exotic codepages
beyond cp1252 hex escapes may garble; snapshots, notes.rtf,
content.styles and compile-format metadata are not imported.

### 2.4 Tracked edits with accept/reject, and AI as a source of them — DONE
**Request:** "eventually, AI integration and tracked edits to
accept/reject."

**Layer 1 — tracked changes (no AI required).** Word-style revision marks
inside the editor: insertions and deletions carry a change id, an author
label (Preferences → "Change author label", default "Author"), and a
timestamp. Rendering is a pair of sentinel styles Qt round-trips — a
blue-tinted underline for inserts, a red-tinted strike for deletes —
because `toHtml()` keeps a background colour but drops custom format
properties; the live change ids ride those custom properties while the
document is open, and re-attach to the marks **by span order per kind**
after a reload (`attach_ids`). Storage is the marked-up HTML plus
pending-only rows in a `revisions` table; accept deletes the row with the
mark, reject deletes the insert or unmarks the deletion. A session
("revision pass") groups changes and is created per tracking toggle-on;
the Revision Passes dialog accepts or rejects one whole pass, and Accept
All / Reject All cover the document. Word counts, search and every export
see the **accepted** rendering (`plain_text` → `accepted_html`) — the
stored file keeps its marks until the writer resolves them.

**Layer 2 — AI integration.** A provider-agnostic client (`ai.py`, urllib
only, no extra dependency): any OpenAI-compatible `/chat/completions`
endpoint, Ollama's localhost server as the default, endpoint/model/key in
Preferences (app settings, never the project). First uses shipped:
**AI → Continue Scene** (the section's tail, capped, at the caret) and
**AI → Proofread Selection**. Both land through
`apply_tracked_replacement` as one tracked pair under an "AI" byline and
their own session — never a direct write, and the tracking toggle does not
gate them, so accept/reject stays the writer's call.

**Implemented (2026-10-20):** three commits — core (`c0a5ec5`), AI layer
(`ba0beac`), ROADMAP. Storage and marks: `revisions` table + sentinel
constants + `accepted_html`/`reconcile_revisions` in `project.py`, the
live-mark half in a new `revisions.py` (`runs`/`segments`/`apply_accept`/
`apply_reject`/`attach_ids`). Editor: `LinkEditor` gained the tracking
state machine — typing bursts coalesce into one insert until the caret
leaves the end, backspacing a plain char strikes it in place, erasing
already-marked text removes it and its row, pastes are one change,
paragraph-mark joins at block boundaries stay with Qt untracked. Window:
Track Changes (Ctrl+Shift+T), Accept/Reject All in Section, Revision
Passes…; reconcile runs on every save; AI menu + Preferences AI section.
Also fixed en route: `plain_text` leaked Qt's CSS head, adding ~19
phantom words per stored document. Tests: `tests/test_revisions.py` (24)
plus `tests/test_ai.py` (9, canned loopback endpoint), suite 258.
**Open edges:** deleting a paragraph mark (block join) is untracked —
the separator is invisible and Qt owns the merge; bursts split on caret
moves, not on punctuation or pauses; marks left without rows (undo
removed them behind the table's back) keep rendering but are
unaddressable until neighbours resolve — the table never invents rows,
only prunes; per-change granularity keeps its data (ids on every row)
but ships no list yet, so the passes dialog is document+pass only; undo
can desync rows from marks until the next save's reconcile heals it; the
live status-bar word count still counts struck words until the next
save recomputes; restoring a snapshot taken with pending marks restores
the marked-up prose and reconcile prunes the orphaned rows; AI context
is the section tail, not the export pipeline (compile-aware context and
the remaining uses — synopsis generation, binder Q&A — wait on a later
pass); no streaming or cancel; the API key sits in QSettings plaintext.

### 2.4a Template-slot editor — REMOVED (2026-10-26)

**Request:** (captured 2026-10-20, queued "after 2.4") an in-app editor
for the export template slots, with the placeholder vocabulary offered as
a dropdown and the slot checked for mistakes.

**Built 2026-10-20, removed 2026-10-26** on the author's call: *"it
doesn't show any code to begin with and is more confusing than useful."*
It also failed the thing it existed for — a dialog two menus deep was the
only place a writer could see the shipped default at all, and it could not
duplicate it from there either.

Gone: `TemplateSlotEditorDialog`, `edit_template_set`, the
Document → Edit Template Set… action, and the six imports only they used.
Kept, because exports run on it: `validate_slot`, `TEMPLATE_KEYS`,
`TEMPLATE_COMPLETIONS`, `slot_file_path`, `write_slot` in `templates.py`
(stays Qt-free) and `_roundtrip_issues` in `app.py`. The dialog's tests
went with it; `tests/test_template_slots.py` is 12 tests over the pure
half.

Replaced by the **Templates folder in the binder** (docs/TEMPLATES.md §1.1):
it lists the sets that exist on disk, the shipped three on top and
duplicable, and picking one lists its files in the Outliner. *(That listing
itself moved on: 2.4b, then 2.4c — the sheet with it.)*

**Edges carried forward:** validation does not parse bare keys inside
`$if(...)` conditions, and a second `$else` inside one `$if` is tolerated
silently (`fill()` flips the branch twice).

### 2.4b Template navigation redesign, format folders, and the mark's deletion — DONE (2026-10-28)

> **Revised again later the same day:** the sheet described below is gone.
> A template file is a plain HTML file, opened in the regular editor and
> saved back to its own path; the compile strips the editor's furniture
> (body taken out of a full Qt document, `<span>` tags removed outright).
> See 2.4c.

**Request:** the Templates view could not answer the question a writer
actually asks — *"where are my templates, and which one did I just get?"* —
and the `is_template` folder mark was a second, competing concept of
"template" that ended up listed nowhere worth looking at. Author's calls:
the disk is the truth, files are editable in any text editor, *"we already
have the outliner for listing, we don't need another tab for listing"*, and
flat under `templates/` — *"It should appear in the Templates folder. Not
Templates > Default."*

**The navigation** (`app.py`, `sheet_view.py`, `template_sheet.py`): the
binder's `Templates` folder lists the sets that exist — the project's own
`<project>/templates/` directory, a synthetic row when it has none — one
flat row per set, the three shipped sets first with `default` on top, then
the project's folders, then the app library's. Picking a set lists its files
in the Outliner as the raw folder tree (subfolders where they sit, every
file a row, the sheet area each maps to) and opens the Templates tab on its
sheet; picking a manuscript document takes both away. The tab is editing
only: a placeholder when nothing is picked, the sheet when a set is. Files
can be edited in any text editor; nothing on screen is a second store.

**Per-format subfolders** (`templates.py`): a set may carry `html/`, `epub/`,
`docx/`, `pdf/` or `text/` — `FORMATS` — refining the root for that format's
exports, falling back to the root and then the built-ins, so a set with one
template still exports everything. A PDF asks `pdf/` of the DOCX compile
LibreOffice converts; markdown stays template-free. `for_format`,
`document_slot`, `compile_units`, `_prepare_links`, `docx_config_values`
and `_template_resolver` all take the format at entry.

**Copy out, one file at a time** (`templates.py`, `app.py`): a file in a
set's listing becomes a new folder in `templates/` — a sibling, never
inside, at the path it had — and everything else it does not say falls back
to built-ins. Whole-folder duplication is unchanged.

**The `default` slot**: `discover_template_sets` seeds only the shipped
three, so a project folder named `default` (or `short story`, or `no
export`, any case) is listed *after* them as a row of its own, under its
folder name — its files never hidden, the top slot still Lekhak's.
`named_template_sets` keeps the *name* `default` meaning the shipped set,
while the export dialog's picker (which carries the set itself) can still
pick the folder; the book-template combo dedupes identical names.

**The mark is deleted** end to end: the `is_template` column (the migration
now drops it *and* the old `is_templates_root`, guarded for SQLite < 3.35,
with nothing to migrate — no part of the new model reads either), plus
`set_template`, `template_folders`, the export exclusion, the italic cues
in both trees, the inspector's Template checkbox, the binder's
"Mark as template" actions, `layout.Node.is_template` and the `folder.yaml`
key. Word counts and whole-project export simply include the whole binder
now: a template is a folder of *files* under `templates/`, so manuscript and
templates cannot collide. `tests/test_template_mark.py` → 
`tests/test_schema_migration.py` (5 tests, including the no-drop fallback).
Suite 841, lint at the pre-existing baseline (333), every file parses as
Python 3.9.

**Edges carried forward:** a set's format folder must carry the same layout
as the root — nothing validates that a `docx/` config is complete, so a
half-filled one falls back per file rather than failing loudly; and the
Outliner's file rows carry no per-file actions beyond Copy out (no inline
rename or delete — those belong to the file manager, which is where the
files are the truth anyway).

### 2.4c The sheet deleted: a template file is a file — DONE (2026-10-28)

**Request:** *"I'm still seeing the old code editor. Just treat the files
like normal html"*, and on the structured sheet that had replaced the old
slot editor: *"This looks harder than a text editor"*, then *"Don't
overthink it. html is our friend."* The sheet of 2.4b — row/area mapping,
field groups, unsaved-flag, per-area editors — was that idea's second
implementation and is deleted with the first; both are in git
(`24899b2`).

**What stands** (`app.py`, `templates.py`): a file of a set opens in the
regular editor — `.html` as HTML, `.txt` as plain text — and writes back to
its own path: Save, the autosave debounce, and every switch away (to a
document, another file, another set, another project, or quitting) go
through `save_current`'s one write path. The Outliner keeps the whole set
in view beside the editor, so clicking through files is the navigation.
A file the compile never reads (a `README.md`) refuses with the reason
rather than opening something the editor would rewrite on save; a file of
a shipped set refuses too — a set in code has no folder to write to — and
offers Duplicate.

**The compile is what tolerates** (`templates.py`): `strip_editor_html`
takes the body out of a whole document and strips `<span>` tags outright,
applied wherever set files are read. The editor saves a full Qt document
with spans around touched runs; writers paste from browsers and Word. One
rule, no per-source handling, and nothing tidied on the write side — what
lands on disk stays readable by any editor, exactly as the author framed
the tolerance: *"God I hate spans. We just strip them out in the compile."*

**Deleted**: `template_sheet.py`, `sheet_view.py`, the Templates tab and
its placeholder, `TEMPLATE_ROW_ROLE` and the Area column, `Edit template`
from the set's context menu, and `tests/test_template_sheet.py` +
`tests/test_sheet_view.py`; `tests/test_template_navigation.py` rewritten
around the editor, `tests/test_template_fragments.py` added for the strip.
Suite 791 passed / 1 skipped; lint 329 (below the 333 baseline); every
file parses as Python 3.9.

**Open edge:** the metadata replacement the author has in mind ("some
features replacing metadata which I will tell you once the world returns
to normalcy in the editor") is pending — the editor is now normal, so this
is the slot it goes in.

### 2.4d The template sidebar panel: checkbox, config box, CSS — DONE (2026-10-28)

**Request** (dictated in order): *"a big Template checkbox"* — unticking it
*"brings the metadata back and the file stops showing in the templates"*; a
*docx/print config text box pulled from the file's head with a note on top*:
*"if your books are formatted similarly across mediums, you don't need
separate print templates. You can simply add any minor formatting here (at
a minimum add your page size). If you edit it and need to refer to the
original tags, you can look at the default file"* — *"any text in the box
gets saved to the head where it belongs"*; a CSS text area: *"We can also
add a text area for css in the sidebar. I forgot to write that."*; and the
naming reminder: *"remember to name this file according to your document
structure for it to work correctly"* — one text, *"written once and reused
in documentation, template pages, and to understand metadata"*.

**What stands** (`app.py`, `templates.py`, `project.py`): while a template
file is open, the sidebar swaps the document metadata for a panel
(`metadata_section` / `template_panel`, one show/hide). The big checkbox
writes `Template: no` into the file's head and back out again — the file
leaves `file_paths`, `_read_folder` and `document_slot` (the built-in slot
renders instead) while staying on disk, the metadata returns, and only the
checkbox stays up so the file can be made a template again. The config box
**is** the file's head: `split_head` peels it on open, `join_head` puts it
back on Save (a typed head without a `#` opener is given a bare one, so the
block is always re-readable), and the flag lives in the same head. The CSS
box reads and writes the set's `style.css` — written only when it moved, so
an untouched box mints no file — and `TemplateSet.css()` answers it with a
format folder's own file winning (§1.3). Exports carry it: `export_html`
embeds a `<style>` after the title, `export_epub` ships `OEBPS/style.css`
with a link in every section and a manifest item; no stylesheet means no
`<style>` and no link, so existing exports are byte-identical. The two
shared texts live once as `TEMPLATE_CONFIG_NOTE` / `TEMPLATE_NAMING_NOTE`
and are quoted verbatim by the panel, the docs (§1.2, §3.1, §3.2) and the
`README` a set is created with; the config note's *look at the default
file* is a button opening the set's `print-config.txt`.

**Gates:** suite 824 passed / 1 skipped; lint 329 (unchanged, below the
333 baseline — profile identical before and after); every file parses as
Python 3.9. Tests: head round-trips and the flag (`test_template_fragments`),
CSS fallbacks and HTML/EPUB embedding (same), panel behaviour — swap, head
box, untick/tick, CSS box, naming text (`test_template_navigation`).

**Still open:** the metadata replacement itself (2.4c's edge) — the panel
now occupies exactly the slot it goes in.

### 2.4e The Document template box carries the filename; the reminder names Types — DONE (2026-10-28)

**Request** (dictated, then asked exactly): the naming reminder becomes
*"Remember to name this file according to your folder or document TYPE in
the manuscript metadata?"*, with example filenames *"book.html, file.html,
chapter.html, scene.html, fancynameit-scene.html..."*; and *"In templates,
the binder should carry directly the filename, so it can be edited.
Truth."* Asked which piece should carry it, the answer: **the Document
template box** — not the binder rows. Asked whether Type should pick the
file at compile, **no — the note is advice only**: *"it might prevent
setting the type if a template hasn't been created"* and *"We already have
sensible defaults for whatever doesn't get a match"*.

**What stands** (`templates.py`, `app.py`): `TEMPLATE_NAMING_NOTE` rewords
once — panel, docs (§1.2) and the set README still quote it verbatim. The
Document template box is editable, wired to `editingFinished` exactly as
the Type box is: every entry now shows the filename it stands for
(`bare.html`; `.txt` only where a set carries no html variant, `.html`
where it does), and whatever is typed saves as the name the compile
asks for, through `document_template_stem()` — extension dropped, an
optional `documents/` shelf prefix dropped, and a string no name could
bear saving as nothing, which *is* `Default (set's own slot)`. A stored
name with no file behind it keeps its own selected row, so a template can
be asked for before it exists; until a file answers it, `_render_document`'s
untouched fallback is the sensible default. The box's whole vocabulary
comes from `document_template_faces()` (stem → real filename, chrome and
non-fragments left out, shipped sets answering from `SHIPPED_FILES`, the
map a duplicate is written from); `document_template_names()` is its sorted
keys, and `document_slot()` now also answers a filename directly.

**Gates:** suite **833 passed / 1 skipped** (six stem/faces/slot unit
tests in `DocumentTemplateFaceTests`, three GUI box tests: filenames
carried, a filename typed before the file exists, Default saving no
name); lint **329** — byte-equal to the baseline's per-file profile (the
`test_templates.py` I001 predates this work, identical at HEAD); every
file parses as Python 3.9.

### 2.4f Template-panel preset rows: Amazon trim + KDP margin band — DONE (2026-10-28)

**Request** (dictated with KDP's own tables): *"We could further
idiotproof this by offering the most critical setting as a kind of
preset... Instead of setting page dimensions manually, we could offer a
dropdown?"*, then narrowed four times: the desktop **template panel's
config area** only (*"the template panel config area"*); **these Amazon
sizes only** — *"I don't think we need large sizes — those usually need
typesetting, which is beyond our app"*; *"there can be a box for custom.
Takes out all the guesswork."*; page-count ranges first *"as a config
comment"*, then retracted — *"You just need the trim size... No need for
the comment even"*; and the KDP margin table with *"And this is for
margins"* — both questions later answered as **recommended**: a full
KDP margin line per band, and **no bleed** variant (Lekhak sets none).

**What stands** (`project.py`, `templates.py`, `app.py`): `PAGE_TRIMS`
gains `5.06x7.81` so the Amazon list is complete there too;
`AMAZON_TRIMS` (five ids, all `PAGE_TRIMS` keys) and `KDP_MARGIN_BANDS`
(five page-count bands → inside margin) with `KDP_MARGIN_MIN = 0.25`
hold the tables beside it. `write_config_line(text, key, line)` is the
panel's counterpart to `set_template_flag`: it drops every line
normalizing to the key — a stale twin cannot outvote the pick — appends
the new one where the parser reads it last, and carries comments, other
keys and the fragment through untouched (a headless text gains
join_head's own `#` nudge; a `Docx` marker stays first).

The panel gains two rows above the config box (and below its note):
**Page size** — the five Amazon trims plus *Custom size…*, which
reveals two `Page setup`-style spin boxes (3–14 in, 1/16 step); a pick
writes `Page size (inches - horizontal, vertical): W,H` in `trim_id_for`'s
own numbers, Custom writes theirs on `editingFinished`, prefilled from
the head so a focus-out never overwrites 6,9 with 3,3. **Margins** —
the five bands, each writing `Margins (L,T,R,B/I,T,O,B): inside, 0.25,
0.25, 0.25` in the four-value spelling `config_geometry` reads back.
Both rows are stateless: they write the config box, the box arms the
normal save, and `_prefill_preset_rows` on open *selects* what the head
already says — an Amazon trim selects itself, any other real size falls
to Custom carrying its numbers, an exact band's gutter selects that
band, signals blocked throughout so reading never writes. Body-visibility
loops re-apply the Custom mode after showing the body (`isHidden` truth:
the mode, not the loop).

**Then, for whoever wants them** (same day, at the author's word):
`Custom margins…` joins the Margins row — four boxes (inside, top,
outside, bottom) carrying whatever the head says, writing the same
four-number line on `editingFinished`; a mirrored pair fills
inside/outside, the two it does not name staying at KDP's minimum.
Prefill now selects a band only for a line that *is* the whole KDP row;
any other readable margins line falls to Custom carrying its numbers.
And the selector goes grey while its own custom boxes hold the focus
(`focusChanged` → `_preset_focus_changed`) — the boxes are what is
speaking, and focus leaving them re-enables the selector after
editingFinished has written the line.

**Gates:** suite **850 passed / 1 skipped** — six `ConfigLineWriteTests`
unit cases (replace, append, duplicate collapse, headless, `Docx`
marker, geometry round-trip) and eleven panel GUI tests (the two rows'
offerings; a trim pick replacing + saving; a band pick writing the whole
line + saving; Custom on `editingFinished` for both rows; prefill
following the head — band, custom carry, mirrored pair; a non-Amazon
size falling to Custom; the selector greying in its row) plus
`trim_id_for(5.06, 7.81)`; lint **329 = baseline** (zero new flags);
every file parses as Python 3.9.

### 2.4g The mirror checkbox, and the `Mirror margins` config line — DONE (2026-10-28)

**Request** (asked first, then proposed): *"Inside/outside is applicable
only when margins are mirrored? Or Left/right? Maybe add a checkbox for
mirrored. If checked, L/R turns to I/O."* The reading held: a
four-value line is always four slots — first means binding side, third
fore-edge (`config_geometry` maps them to `inside`/`outside`
unconditionally) — so *inside/outside* is the true word only on a
mirrored layout, where even pages swap (`pageview` flips verso), and
*left/right* on a plain one. The numbers never move; only the names do.

**What stands** (`project.py`, `app.py`, `templates.py`): the config
gains a key, `Mirror margins: yes|no` — until now the flag a two-value
pair *implied* was the only way a head could say mirrored at all, and
"not mirrored" could not be said; `config_geometry` reads the line
after the pair (explicit beats implied) and the merge turns it into the
same `geometry["mirror"]` the exports already honour (`w:mirrorMargins`
in settings.xml, the verso flip in the page preview). The panel row
gains the checkbox writing it both ways through `write_config_line`
(ticked `yes`, unticked `no`, no debounce wait — a toggle is a
decision), and the two edge boxes are labels that follow it: *Inside
(spine)* / *Outside* ticked, *Left* / *Right* not, via
`_refresh_margin_labels`; prefill checks the box from the head's own
line, signals blocked. The shipped `print-config.txt` sample and
`PRINTING_NOTES` document the key; §1.2 and §3.1 quote it.

**Gates:** suite **852 passed / 1 skipped** — unit cases for the line's
two ways (yes/no beat a pair's implication, silence stays silent,
spellings a hand types) in `test_docx_config`, two panel GUI tests (the
checkbox writing both ways and renaming its boxes through Save, a
mirrored head selecting itself on open); lint **329 = baseline**; every
file parses as Python 3.9.

### 2.4h The format bar flows; the style box makes styles — DONE (2026-10-29)

**Request**: a proper rich-text editor "instead of those few buttons",
then the real symptom — *"in my interface, I only see until list —
getting truncated by end of area and needs to wrap?"* — and *"a way to
add styles to the box. As in, for the user to add styles. Not just
pre-defined styles from us."* Text colour was asked for and then
withdrawn by the author himself: *"Then take the colours out. They
could be added in the css if the user marks them with a style."*

**What stands** (`flowlayout.py`, `app.py`): the complaint was Qt's
own fault — a QToolBar cannot wrap, so a full format bar lost its tail
past the editor's width. The Editor tab's bar is a plain widget with a
`FlowLayout` (new module, margins honoured, `hasHeightForWidth` with
the container's policy flipped so a narrow window grows the bar instead
of clipping), and every control is a `QToolButton` over its own
`QAction` — the action keeps the slot, the checkable state and the
tooltip. Focus mode's chrome sweep names the container (it is no
longer a `QToolBar`). The alignment buttons are checkable and follow
the caret (`cursorPositionChanged` → `_refresh_alignment_buttons`, also
called on the click so a block-format change that moves no caret still
answers), Justify joining Left/Centre/Right; a mixed selection checks
nothing, which is the truthful answer.

Making a style no longer needs the menu: the style box ends in `+ New
style from paragraph…` and `Update style from paragraph…`, routed
through the existing capture/persist machinery (`_create_style_dialog`,
`save_style_from_cursor`, `update_style_from_cursor`) with
`_style_selection_tracked` remembering the last real pick so Update
re-captures the right style, and `_restore_style_box` returning the box
to the caret's own style after a command. Delete is *not* in the box
(removing a style is a library operation — the author's own word — and
the right-click menu keeps it till Preferences grows a styles panel).
The commands are custom data (`NEW_STYLE_COMMAND`/`UPDATE_STYLE_COMMAND`),
never names, so no future style could collide. No colour: the compile
is plain body text, and colour belongs in a style the CSS can give.

**Gates:** suite **862 passed / 1 skipped** — a new `test_flowlayout`
suite (wrap by width, item-to-next-row, height-for-width moves nothing,
item removal), six new style-bar GUI tests (New persisting and
restoring the box, a cancelled New storing nothing, Update
re-capturing the shown style, alignment setting the paragraph,
alignment showing the paragraph's own — clicked and followed —), plus
the style-box expectations rewritten for seeds + separator + commands;
lint **329 = baseline** (two of my own flags caught in the run and
fixed); every file parses as Python 3.9.

### 2.4i EPUB names the author — DONE (2026-10-29)

While exporting *A Fictional Book* as a 6 × 9 book (a new project
imported from the Scrivener compile in `~/Writing/LEKHAK/`, with a
simple `6x9 book` template set — `Page size: 6,9` and KDP's 0.625 in
gutter for a ~430-page novel — in its `print-config.txt`), the
verification pass found the DOCX title page carrying the project's
Author but the EPUB OPF carrying only identifier, title and language.
A shelf and a catalogue read `dc:creator`. One fix in `export_epub`:
the OPF now writes `<dc:creator>` when `project_metadata()` carries an
Author (silence stays legal — a collection of fliers has no author),
mirroring the DOCX title page's own rule, with two tests (author
present, author absent) beside the cover tests.

**Gates:** suite **864 passed / 1 skipped**; lint **329 = baseline**;
3.9 parse clean.

### 2.5 Where metadata lives: files first, SQLite as the cache — DECIDED (2026-10-29)

**The author's own words:** *"We should be able to read simple folders.
Make them editable or not, etc. Metadata could be stored in sqlite3 —
if that is best — I didn't make the decision though. But the folders
and their text should be reasonably accessible to humans without being
able to read random keys and map them to their work."*

**The call (mine, as invited):** metadata stays **in the files**, and
SQLite holds only what a human never opens — revisions, snapshots,
search/count caches, the lock. Reasons, in the author's own priorities:

- **Copy-a-book is the mandate.** A completed book is *copied as a
  folder* into another project (2026-10-29, working agreements). If its
  Type, page setup, child order or scene metadata lived only in
  SQLite, the copy would arrive naked — the mandate cannot be honoured
  by a database that stays behind.
- **Read-only books are a filesystem fact.** A published book "may not
  be written in any conditions"; the strongest enforcement is the
  disk's own, and it guards files, not tables.
- **Human access without a decoder.** The book folder must read as a
  book: slug-named HTML files whose contents are the prose, plus a
  small frontmatter a person can read. Today's store — `documents/
  <uuid>.html` with every fact in the DB — is exactly the "random keys"
  he refuses to map.

So `docs/STORAGE.md` (decided 2026-10-25) is the target, confirmed:
folders are directories with `.lekhak/folder.yaml` (id, Type, page
setup, child order), documents are slug-named HTML with frontmatter
(title, id, type, synopsis, target, metadata keys), SQLite is a
rebuildable cache. Nothing about titles is stored twice-and-differently:
a rename rewrites one file; the `id` keeps history, covers and wiki
links finding their document ("an external move looks like a move
rather than a delete plus a create").

**The gap, named:** the live code still runs the superseded half —
SQLite owns structure, prose is flat `<uuid>.html`. Nothing in this
decision changes code today; it says which way the storage build goes
when it is scheduled, and it is why the queued items are ordered the
way they are (per-book folders, then read-only books, then the cache).

### 2.6 The binder is the tree on disk, and a lock is a file in it — DONE (2026-11-02)

**What the writer reported (2026-11-02):** imported documents looked
empty, folders and documents wore the same icon, and two *Templates*
folders sat in the binder — one empty at the top, one with the sets at
the bottom, neither behaving.

**Three causes, three fixes.**

- **The import split one binder into two trees.** A leaf that gained
  children was promoted from `X.html` to the directory `X/` *after* its
  new child's path had been worked out, so the child landed in a ghost
  directory `X.html/` beside the real `X/`. The two halves never met
  again, and the writer saw a document that read as empty because its
  words were in the other tree. The child's path is now worked out
  after the promotion, and `repair_tree.py` puts an older split back
  together — it finds the ghost directories, moves their contents where
  they belong, and takes a backup before it touches anything.
- **The binder was drawn from the database, not from the files.**
  `Project.binder_tree()` walks the tree on disk and joins the index in
  for the ids and titles, so a folder the writer made by hand is in the
  binder at once, and the icon says what the thing *is* on disk rather
  than what role its row gives it.
- **Two *Templates* folders.** A template set is a folder of fragments,
  not a document: the binder holds the writer's own tree and nothing
  else. The sets are listed in the Outliner instead (View → *Template
  Sets*), and the project's `templates/` folder — the one reserved name
  that stays — is no longer a row of its own.

**Also fixed:** a container's own `index.html` is its prose, not a child
of it. The index no longer grows a second row for that file, so the
binder stops showing the same words twice, once as the folder and once
as a document called "index".

**The lock is the same idea made concrete.** A lock is a marker file in
the tree — `.locked` inside a folder, `.<name>.locked` beside a file —
so it travels with a copied folder and cannot be lost with the
database. The tick beside the Type row says whether the marker is there;
a locked folder locks everything inside it. A locked document still
opens, still reads, and can still be typed in: it is simply never
written, and the writer is told rather than losing the words quietly.

## Phase 3 — wish-fulfilling fairy software
### 3.1 Text to speech — DONE
**Request:** "Read the current scene or selection aloud."
**Implemented (2026-10-20):** one commit (`4fd24b5`). Speak menu:
Read Aloud (F8) speaks the selection, else the whole scene; Pause /
Resume (F9); Stop. Qt's `QTextToSpeech` behind a thin `SpeechReader`
(`src/lekhak/tts.py`) — the `speechd` engine when the system has it
(speech-dispatcher + espeak-ng are installed on the author's machine:
420 voices), any other engine as fallback, a status-bar message when
none exists. Built lazily on first use so the speechd handshake never
taxes startup. Preferences holds the voice picker and a rate slider
(−1.0…1.0 shown as a ×multiplier); document switches and window close
stop the engine. Live smoke: the real engine connects and speaks.
Tests: `tests/test_tts.py` (9), suite 336.
**Open edges (read aloud):** the whole-scene fallback reads the
accepted rendering including resolved link text; pause support depends
on the backend (speechd's varies by version — a failed pause is
silent); no per-sentence highlighting or click-to-resume-from-here;
the engine choice is not exposed in the UI (QSettings `tts/engine`,
default `speechd`); voice list is all 420 system voices with no
language filter.

### 3.2 Everything else in the wish list
Anything above that didn't earn a slot gets collected here as it arrives.

**Instructions and GitHub release (captured 2026-10-20, not scheduled).**
Author: "At some point, we should write instructions for it and release
it onto github I guess." A README (what Lekhak is, install, first
project, the export formats and what each is for), a short user guide
for the menus the roadmap's features created, and a public repo push —
decide license, repo name and what to do about the test fixtures that
point at the author's corpus paths (the suite itself is portable; ad-hoc
smokes reference `/home/vidyut/Writing` and must not leak).

**Page view — realistic paged canvas — DONE**
**Request:** "we should also be able to offer a 'page view' so the
writer knows what goes on the page and where — this will be needed for
books with illustrations. But since I don't do them (yet), there is no
hurry." (Captured 2026-10-21, scheduled the same day once 1.15 shipped.)
**Model:** the investigation the capture called for settled the
architecture by failing: QTextEdit cannot paginate live — with word
wrap on, every relayout re-stamps the document's pageSize to the
viewport, and with NoWrap nothing wraps (both measured, not guessed).
So the view is a projection: the Page tab owns its own QTextDocument,
built from the editor's live HTML and laid out at the book's
text-column size, painted sheet by sheet — pasteboard, paper, shadow,
text-box outline, page number — with layout coordinates page-stacked at
exactly the column height (verified against QTextDocumentLayout).
Geometry comes from a new `Project.effective_page_setup` — the export
walk, nearest book ancestor, junk rows skipped — so the preview and
the printed file can never disagree; no setup anywhere means the same
default Letter box export uses. Mirrored books alternate recto/verso
per sheet with the gutter on the inside; fit-to-window scaling runs
0.15–1.0×. Dblclick on the paper jumps to the editor at the same
character offset (both sides share one HTML source, so offsets map).
**Implemented (2026-10-21):** `src/lekhak/pageview.py` (pure geometry
helpers, `PageCanvas`, `PageScrollArea`, `PageView`), the Project
helper, and the second tab between Editor and Corkboard — refresh on
tab show and selection change, status line reporting pages and trim.
Tests `tests/test_pageview.py` (18), suite 394. Live visual smoke:
rendered sheets inspected — margins, styles, page numbers all correct.
**Open edges:** the projection is read-only — typing happens in the
editor (dblclick goes there); fonts are the editor's, not the export
template's, so the preview's page count can differ from the PDF's until
template typography is parsed for preview; a document is assumed to
start recto (page 1 = right-hand sheet) — parity across a multi-scene
book isn't modeled; template header/footer slots and 1.6 page-start
markers don't shift the sheets; zoom is fit × the session's display
factor (1.8) — Ctrl+scroll on the paper, no separate page-view setting;
no word/page estimates yet (the status line counts sheets).

**Book cover generator (captured 2026-10-20, not scheduled).** Full-wrap
cover from user-provided images: front, back, spine. The value is doing
the parts writers get wrong and waste days on — spine width computed from
page count × paper type (Amazon's white vs cream constants differ),
bleed added outside the trim, a check that spine lettering and back-cover
blurbs stay inside the safe zone, and a PDF at the exact dimensions
KDP/IngramSpark accept. Front/back/spine laid out from a template per
trim size; user drops in their artwork and text. Depends on 1.6 (page
geometry + realistic page count — still uncaptured; see its scope note)
and, since 2026-10-21, only on that: the PDF writer now exists
(LibreOffice wrapper). Whether the cover should ride the same wrapper
or draw at exact trim size with Qt (a cover is images and a few text
boxes, not manuscript layout, so a second renderer costs far less there)
is a decision for when this gets scheduled.

---

## Working agreements

- The project format stays transparent: **the filesystem is the structure** —
  documents are HTML files carrying YAML frontmatter, folders are directories
  with a hidden `.lekhak/folder.yaml`, and SQLite is a rebuildable cache — all
  per `docs/STORAGE.md`, decided 2026-10-25. That decision supersedes the
  "metadata is SQLite" clause, which was **never the author's**: it arrived in
  `7cbc683` (2026-10-20, an AI-drafted roadmap pass, "Codex's MVP baseline")
  and was never a mandate — the author's recollection ("I can't recall even
  thinking that") is correct and is recorded here so no later hand treats it
  as agreeing to it. Backups are copies. No further change to this contract
  without an explicit decision recorded here.
- **A completed book is read-only in fact** (author, 2026-10-29): it lives in
  its own folder, and no routine write path touches a published one — the
  filesystem may enforce it (permissions), not merely convention. A book is
  *copied*, never moved, when it goes to another project or another person:
  the book is a folder, so sharing it is a folder copy (`docs/STORAGE.md`
  "extract and nest"). This is the reason the structure lives on disk: a
  database cannot express "do not touch this".
- Content writes stay atomic (`os.replace` after fsync). Any new writer
  follows the same rule.
- Codex may pick up any item directly; claim it in a commit message
  (`claim: 1.4 split/merge`) so work isn't duplicated.
- **Broad compatibility, low friction.** Python floor is **3.9** — any
  common distro/system interpreter, no exotic versions. Two runtime
  dependencies (PySide6, python-docx), no environment or tooling the user
  has to assemble; `pipx install .` or `pip install --user .` is the
  install path. No new dependency unless it is genuinely unavoidable, and
  a floor bump needs a recorded reason here. The floor is held by
  `str.removeprefix` (3.9) and the lazy `from __future__ import
  annotations` unions; keep new annotations under that import and prefer
  3.9-safe stdlib. Verify with
  `ast.parse(src, feature_version=(3, 9))` across `src/ scripts/ tests/`.
- **Manuscripts are precious.** Nothing may rewrite, prefix or reformat a
  document's stored title or content as a side effect of a display,
  numbering or import/export feature. An interchange concern (binder
  numbers, TOC markers) is shown on the way out and stripped on the way in;
  it never lands in the stored manuscript.
- **Destruction is confirmed, and never by name alone.** No content is
  deleted, overwritten or merged, and no target is chosen by a *title* match,
  without an explicit confirmation that names exactly what will be lost and
  which item (its title and its place in the binder) is meant. A Yes/No
  dialog is not enough — it gets clicked through; the confirmation carries a
  checkbox that has to be ticked before the destructive button is live.
  Where several items share a name, the writer picks which one: the app
  never guesses on their behalf. Writing into a folder that already exists
  is allowed — behind that confirmation, never from a bare paste.
- **Ship as few conventions as possible.** A convention is something the writer
  must learn, remember and obey, so every one we add takes away room to do
  exactly what they want. The app supplies defaults and a sensible vocabulary,
  then stays out of the way: no `Metadata.` prefix to remember, no case rules
  in placeholder names, no page-size picker loose inside a book, no policing of
  a determined writer's eccentricities. Where a convention is genuinely needed
  — `Templates` as a reserved folder name is the clearest case — it is said
  once, in one place, and only then.
- **Lean over clever, and every line must earn its place.** Before
  implementing a feature, check both sides: what it costs (a new mechanism,
  a new stored role, a new regex in a write path, a new code path that can
  touch content) and what it buys. A cosmetic or low-value feature that
  needs a lot of code is bad code regardless of how well it is written —
  8000 lines to make the interface sparkly is a no. If the cost is real,
  stop and say "I don't advise this because…" with the specific risk or
  bloat, and let the decision be made explicitly rather than by default —
  this applies even to essential features, where the answer is to explain
  the trade-off and get an opinion rather than to proceed silently. Prefer
  the representation that has no way to corrupt data over the one that
  merely looks tidier.
- **Comment the why, not the what.** Every load-bearing or non-obvious
  block says what it does *and why it is done that way*, so that a future
  change to the "why" makes the comment visibly stale rather than silently
  wrong. The test: can a reader tell, at the point of use, whether these
  lines are safe to remove? If not, the comment is missing. This is not
  decoration — uncommented invariants get "tidied up" and break in ways
  that cost someone their work.
