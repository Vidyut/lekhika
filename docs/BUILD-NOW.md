# Build-now list

What gets built **before** the template engine, in order, with the thing that tells us it works. Nothing here depends on the template design, so none of it can be invalidated by the spec review still in progress.

## 0. Folder view: outliner by default, remembered

Clicking a folder currently switches to the corkboard (`_selection_changed`: `setCurrentWidget(self.corkboard)`). It should open the **outliner** by default, and remember whichever view the writer actually uses for folders — per project, as session state beside `editor_zoom`, so a corkboard lover is not fought either. *Acceptance:* selecting a folder shows the outliner; switching to the corkboard with a folder selected and then selecting another folder shows the corkboard; reopening the project keeps that choice.

**Built (2026-10-22).** The folder branch opens `_folder_view_widget()`, which reads `folder_view` from the project settings (default, junk-safe: outliner). A hand click on the corkboard/outliner tab while a folder is selected writes the preference; the programmatic switch fires inside `_selection_changed` while the selection is loading and writes nothing, and document selections never touch it. Five tests in `tests/test_folder_view.py`, one per acceptance clause plus junk and the document guard. Suite 457, exit 0.

## Parallel work for an idle small model

Not code. A tiny local model must not edit this repository — and it must not edit anyone's manuscript either (docs/AI.md). What it *can* usefully produce are **verifiable text artefacts**, where a bad result is obvious in seconds: a synthetic manuscript fixture with known structure (chapters, scenes, front matter, injected typos) to drive the importer and word-count tests, and a corpus of realistic bloopers to exercise the proofreading pass. Structure and detectability matter; prose quality does not.

## 1. File → Open Recent
Projects remembered in `QSettings` (`session/recentProjects`), newest first, capped at eight, entries pruned when the folder is gone, updated on a successful switch only. *Acceptance:* switching projects twice leaves both at the top of the menu, and deleting one on disk removes it from the list rather than offering a broken entry.

**Built (2026-10-22).** `session/recentProjects`, recorded at the tail of `_switch_project` (a cancelled switch records nothing), capped at eight, pruned on every menu build *and* on a click that finds the folder already gone — which reports in the status bar instead of raising. Entries show the folder name without `.lekhak`, disambiguated by parent when two share one. Eight tests in `tests/test_recent_projects.py`. Suite 465, exit 0.

## 2. Quick open — `Ctrl+P`
A one-field palette over document titles in binder order (substring, then fuzzy), Enter selects, Esc closes; it only *navigates*, never edits or deletes. *Acceptance:* typing three letters of a scene name jumps to it from anywhere; the palette never appears with no project open.

**Built (2026-10-22).** `QuickOpenDialog`: one field, list below, substring matches first then subsequence matches, binder order inside each group; Enter activates the highlight, Up/Down walk from the text field, Esc closes, and the dialog has no editing surface at all. Jumps select the binder item and bring the Editor forward for documents (folders stay with the folder-view choice); with no project the palette never appears and the action is disabled with the rest of the project chrome.

*Shortcut deviation:* the spec gave **both** this and Print `Ctrl+P`. Print keeps `Ctrl+P` (every other program owns it); Quick Open takes `Ctrl+Shift+P`. One line each if it should be the other way round.

Ten tests in `tests/test_quick_open.py`. Suite 475, exit 0.

## 3. Selection word count
The status bar already recomputes on cursor moves — add "N words selected" when there is a selection. *Acceptance:* selecting a paragraph shows its count; deselecting restores the document count.

**Built (2026-10-22).** The display already existed (it arrived with the outliner columns); what was missing was the deselect half and any test. `selectionChanged` now refreshes the status bar as well as `cursorPositionChanged` — clearing a selection can leave the position where it was, so moves alone missed it. Two acceptance tests in `tests/test_selection_count.py`. Suite 477, exit 0.

## 4. Print — `Ctrl+P`
Reuse the PDF path (1.6/1.7): write the PDF to a temp file, hand it to the desktop's handler (or Qt's print dialog if it behaves), delete the temp file afterwards. *Acceptance:* printing a short story opens the system print flow with the same pagination the export produces; the temp file does not survive.

**Built (2026-10-22).** `print_manuscript` compiles the PDF straight into a `lekhak-print-*` temp folder — stored template set, export scope, no dialogs — and hands it to `xdg-open` (or `gio open`); a daemon waiter deletes the folder when the handler's process exits, so the viewer may read the file as long as it likes and nothing is left behind. Every failure path (no project, cancelled save, conversion error, handler that never starts) removes the folder immediately. Seven tests in `tests/test_print.py`. Suite 484, exit 0.

## 5. Insert date/time
An editor command inserting the date (and optionally the time) at the caret; feeds the metadata `Date` value rather than being typed by hand. *Acceptance:* inserts today's date in the project's locale and is undoable as one step.

**Built (2026-10-22).** Edit → Insert Date / Insert Date and Time. The prose gets the system locale's long date (there is no per-project locale setting to read, so the system's is the project's), optionally the short time; the caret lands after the insert so typing continues. The same call merges an ISO ``Date`` row into the document's metadata (`date` type for the inspector's widget, existing rows and their types kept — set_metadata replaces wholesale) and refreshes the inspector. One edit block: a single undo removes it. Folders and no-project refuse with a status message. Seven tests in `tests/test_insert_date.py`. Suite 491, exit 0.

## 6. Session goal
A per-day word target beside the existing per-section targets, with progress in the status bar and the day's delta from the session start. *Acceptance:* a target survives a restart; writing 500 words shows 500 of the goal without re-opening a dialog.

**Built (2026-10-22).** Document → Set Daily Goal… (`Ctrl+G`), 0 clears. Three project settings carry it — `session_goal`, `session_goal_day`, `session_goal_baseline` (project total when today's count began) — so the target and today's progress both survive a restart; a stored day that isn't today re-bases silently from a freshly read total (never `_project_total`, which mid-switch still holds the *previous* project's sum) so each day starts at zero while the target carries over. Status bar shows `500 / 2,000 today · +500 this session` (day progress vs. session delta — two baselines once a goal outlives a restart).

**Counts moved to a permanent status-bar widget** (`word_count_label`): `showMessage` was displaced by the next "Saved" notice and, on timeout, QStatusBar clears to nothing — measured, not assumed — so live goal progress would have vanished after every save. Counts and temporary messages now coexist; `save_current` and `_switch_project` redraw the label after their totals land. Nine tests in `tests/test_session_goal.py`; `tests/test_selection_count.py` reads the label now. Suite 500, exit 0; both mutations (rebase removed, post-save redraw removed) caught.

## 7. DOCX/ODT manuscript import
The gap the author spotted: we export DOCX beautifully and cannot read one. Use the existing import floor — never overwrite, numbered second copies, identical content reported (`docs/IMPORTS.md`) — with `python-docx` reading paragraphs and styles, and the LibreOffice UNO path we already drive for ODT. *Acceptance:* a Word document imports as book → chapter → scene using its heading styles; importing it twice warns "identical"; nothing existing is touched.

**Built (2026-10-22).** File → Import DOCX/ODT File…. `importer.parse_docx` drives python-docx: Heading 1 → chapter (flat under the book), deeper headings → scenes under the chapter in force, prose accumulates into the open container, prose before any heading opens an "Opening" scene under the book; the folder takes the document's Title property, else the filename; a heading is its container's binder title and never its content; bold, italic and underline ride inside their runs, with a paragraph-text fallback for field/hyperlink text. `import_docx` reuses the floor untouched — identical content warns, an insisted copy lands as "Title (2)", prose is never overwritten — and the file-dialog handler wraps it exactly like the four existing importers.

The ODT half converts **once, before the floor runs** (the floor re-runs the import into a scratch project; a LibreOffice launch is far too expensive twice): `odt.convert` drives `uno_odt.py`, a worker mirroring `uno_pdf.py` on pdf.py's seam — `_run_uno` gained a `script=` parameter so the kill-the-process-group-on-timeout rule serves both workers without being written twice. The worker drops content indexes before saving (a TOC's cached entries would arrive as prose-shaped junk lines) and skips `index.update()` (page numbers mean nothing to a reader). Probes and failure messages are pdf's machinery re-worded for import: missing soffice → "install libreoffice-writer", missing bindings → "install python3-uno".

Verified by hand with real LibreOffice, not committed: docx→odt→docx round trip through the worker (0.8 s, headings and bold intact, workdir cleaned) and LibreOffice's own `idxexample.odt`. The committed suite fakes `_run_uno` per the house rule in `tests/test_pdf.py` — it never launches LibreOffice. Nineteen tests in `tests/test_docx_import.py`. Suite 519, exit 0; mutations caught (heading map demoted, conversion moved inside the double-run floor, chapter stacking copied from the markdown loop).

**Quirk noticed, not touched:** `import_markdown_file` stacks each chapter under the *previous* one (`chapter_id or folder.id` runs for chapters too), so a three-chapter markdown import nests A→B→C instead of sitting flat under the book. The docx importer deliberately does not copy that; the markdown path is its own fix, out of scope here.

## 8. AI: local-model default
Per `docs/AI.md`: the Preferences wording and warning, the `ollama pull` instruction, a "minimal edits only" proofreading prompt, and — separately — a deterministic `hunspell`/`aspell` typo underliner with no model involved. *Acceptance:* with Ollama running and a small model named, Proofread Selection lands exactly one tracked change per edit; with no model configured, the AI menu explains how to set one up instead of failing silently.

**Built (2026-10-22).** Proofread Selection now sends `ai.PROOFREAD_SYSTEM` — the shipped "bloopers, not prose" prompt: fix typos, spelling, grammar, doubled words and lines, stray spaces and punctuation, the *minimal edit* only, never polish, rephrase or restructure, return the corrected text alone. The empty-model refusal (`ai.NO_MODEL_MESSAGE`) is the shortest setup path, not a shrug: install Ollama, `ollama pull qwen2.5:7b`, Preferences → Model, endpoint already right — and both AI menu items show it, the proofread item additionally carrying a "Bloopers, not prose" tooltip. Preferences got the local-model how-to (install, `ollama pull`, default endpoint) and the AI.md read-this-first warning verbatim in spirit: a model that fits an ordinary computer is very small and error-prone; bloopers yes, unsupervised edits never, prompt tuning expected.

One more explanation the live run demanded: a thinking model that hits its token cap mid-reasoning (qwen3.5:2b, 3,983 tokens of `reasoning`, empty `content`, `finish: length`) used to surface as "AI returned nothing"; `complete()` now says which model burned the reply thinking and names a non-thinking replacement.

The underliner is a separate, model-free module, `spellcheck.py`: aspell in batch mode first, hunspell as fallback, one process spawn per refresh and a session word cache, ranges in UTF-16 code units because that is what QTextDocument positions are, and a missing or failing checker flags *nothing* rather than burying the page in false positives. The View menu gained Spell-check Squiggles (default off — a fresh project lights up with invented names before there is a project dictionary to feed), red wavy `ExtraSelections` that cannot be undone, saved or exported, a 500 ms debounce on typing, a direct refresh on document load, and a toggle that refuses politely — back to off, status bar naming aspell/hunspell — when the machine has neither.

Verified by hand against the live Ollama on this machine, not committed: phi4-mini through the real window landed exactly one AI delete/insert pair for one selection ("teh quic brown fox jumpd over teh lasy dogg." → minimal corrections, tracked, never in place), and the qwen3.5:2b thinking run is what produced the explanation above. Twenty-one tests in `tests/test_ai_local.py` and `tests/test_spellcheck.py`; the suite still spawns no network and no checker process — same house rule as `tests/test_pdf.py`. Suite 540, exit 0; mutations caught (proofread prompt unshipped, `ollama pull` removed from the no-model message, squiggle verdict filter inverted, squiggles on by default, thinking-reply explanation removed).

## 9. Phase-1 leftovers
Text export pagination (form feed or not) and the EPUB note that per-unit files already are pages.

**Built (2026-10-22).** Settled both. Text and markdown export stay structural — titles and headings as lines, units separated by blank lines — with no form feeds, deliberately: pagination is the page-setup formats' job (PDF/DOCX, page setup / PRINT.md), and a `.txt` carrying form feeds is neither a good reading copy nor a printable one. The decision now lives where the writer will find it: `export_text` and `export_markdown` docstrings, and the TEMPLATES.md §10 ledger line flipped from PROPOSED to settled with the date. EPUB needed no decision and no code: its one XHTML file per compiled unit already *is* pages — now said in the `export_epub` docstring and the ledger line, and pinned by a test that counts the section files against the units. Five tests in `tests/test_text_pagination.py`. Suite 545, exit 0; mutations caught (form feeds injected into the text export, the blank-line unit gap collapsed).

## 10. Shortcodes in prose
The author's follow-up to the shortcuts/shortcodes lists: `${…}` markers inside a document's own text should render when the templates pull the content for export — not live in the editor, not in the stored file. *Acceptance:* a scene whose prose says `${Metadata.Scene Title}` exports as "A new dawn."; the document on disk still says `${Metadata.Scene Title}`; conditionals and `${Content}` stay out of prose.

**Built (2026-10-22).** `Project.expand_shortcodes` runs at the four places an export lifts a document's own prose — `_render_document` (own-prose path only; a chapter's assembled body is already-rendered child output and is never re-scanned), the chapter-opener prose in `compile_units`, `export_markdown`'s per-document body, and `export_docx` after the plain-text strip. The vocabulary is the slots': sidebar `${Metadata.Key}` rows against that document, binder values, compile counters — everything `_template_values` carries except `${Content}`, which would mean the prose itself. Conditionals stay in slots (prose gets values, not control flow); unknown keys render empty; html exports escape the expanded values. PDF inherits through DOCX. Ten tests in `tests/test_shortcode_prose.py`. Suite 555, exit 0; mutations caught (scene-prose expansion skipped, escaping removed, markdown expansion skipped).

Still unbuilt from TEMPLATES.md §3.1, and now said so in the doc: metadata's inheritance chain (document → ancestor folders → project → application preferences) — prose and slots resolve only the document's own rows today.

## Print copies, and covers (near-term: the author needs book 1 and 2 on KDP)

Two different DOCX jobs, and they must not be confused:

| | Reading / submission copy | **Print copy** |
|---|---|---|
| Cover | may carry one | **must not** — the print copy is the *inner* book; the cover is a separate PDF the printer wraps |
| Margins | simple | mirrored, per the book's trim |
| Images | any | **no full-colour content images** unless the book is genuinely an illustrated one (bleed, plates); colour content gets a print job rejected |
| Output | DOCX, for editors and readers | the inner-block PDF to upload, plus a separate cover PDF |

The cover creator is **optional and a later update** — it is not Lekhak's core competence. What it should do when it arrives, because the author asked for a full pipeline rather than an art studio: place cover, back and a sensibly proportioned spine for the book's trim, lay out margins per the printer's template, **overlay a grid so nothing important is provably cropped**, scale images to the required size with a quality warning when upscaling, allow text fields where the template permits, and export a print-ready PDF. Cover and inner copy are edited and exported **separately**.

This is why the print copy belongs after the template engine: it needs the DOCX config/reference machinery (trim, mirrored margins, running heads, per-kind page starts) before it can be anything but a guess.

## Then, after the spec review
The template engine — discovery, `Type`/format matching, the slot-driven DOCX render, the config/reference/generate forms, per-export override (`docs/TEMPLATES.md` §9). This is deliberately last: it is the part where an early wrong decision would cascade.
