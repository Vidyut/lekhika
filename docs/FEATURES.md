# Features of Lekhak

A plain-language list of what Lekhak does today. Grouped by what you are trying to do, not by the menu it lives under. If a feature is listed here, it works in the current release; the "Not built yet" section at the end is honest about what does not.

Lekhak is one window, a binder on the left, the page in the middle, and an inspector on the right. Almost everything you do is a right-click or a menu away.

## 1. The guide

Lekhak ships with its own manual, and a fresh install opens it: you can see what the software does before you are asked to create a project. It is the project's example document as well as the user guide — a real book, in a real project.

- It browses and searches like any other, and **File → Export…** gives it to you as DOCX, EPUB, PDF, HTML, Markdown or plain text: keep it as a help document, or look at it to see how an export is put together.
- It is **read-only**. It belongs to the software, and an update replaces it. **File → Save Project As…** copies it into a folder you choose, and that copy is yours to edit — the way to try the software out on a real book.
- Nothing is copied into your disk and nothing is written back into the folder the guide came from.

## 2. Your book, in a tree

- Folders and documents — nested however you like, dragged into order. Folders hold anything; the physical structure is just files and folders on disk.
- A **Type** on every document (Folder, Document, or a name of your own). The Type is what a template dresses, and what a search or filter keys on. It is not the same as the kind: a document's kind says what it is (folder or document), but its Type can be "Leaf", "Poem", "Interview", or anything you name.
- **Quick Open** (Ctrl+Shift+P): type a few letters and jump to any document by title.
- **Find in Project** (Ctrl+Shift+F): search the whole book for a word or phrase and jump from result to result.
- **Replace in Project** (Ctrl+Shift+H): the same search, with a replacement.
- **Standardise** (Edit menu): a pass over the manuscript to normalise things like smart quotes or spacing.
- A **trash** folder holds anything you delete, so nothing is gone until you empty it.
- **Outliner columns** (Document menu): choose which details show next to each row — Type, word count, status, POV and so on.
- **Bulk Edit**: select several documents in the binder or the outliner and change their Type or their metadata in one go.
- **Expand all / Collapse all** (outliner): open or close the whole tree at once.

## 3. The editor

- A comfortable rich-text editor: paragraph styles, fonts, sizes, bold, italics, underline, alignment, lists, headings.
- **Zoom** that makes the letters bigger without touching your document's formatting (Ctrl++ / Ctrl+-, Ctrl+0 to reset).
- **Focus mode** (F11): the binder and the inspector step aside so you see only the words.
- **Insert Date / Insert Date and Time / Insert Page Break** (Ctrl+Enter).
- **Paste** in three flavours: plain text, normal, and "keep all formatting" (fonts and colours, alignment).
- **Insert Image…** (Edit menu): inserts an image at the caret with alignment (inline, left, right, centre). The image is copied into the project so the document travels with it.
- **Undo and Redo** the usual way.
- **Split at Cursor** (Ctrl+K) and **Merge with Next Sibling** to reshape the manuscript as you go.
- **Snapshots** (Document menu): take a snapshot before a big change, restore it if the change was a mistake.
- **Word count** on every document, every folder, and the whole project.
- The editor remembers where you were: the last document and cursor position reopen with the project.

## 4. Page view and pagination

- **Page View** (a tab beside the editor) shows the manuscript paginated, the way it will print — paper size, margins and all.
- Page breaks you insert by hand, and the ones the template and page setup produce, both show up here.
- **Print** (File menu, Ctrl+P) sends the same PDF to your system print flow, using the template set for the current scope.

## 5. Metadata

- Every document carries **metadata**: Status, POV, dates, and any field you name. Create a field once and it appears, empty, on every document of that kind — the catalogue names the fields, the documents hold the values.
- **Inheritance**: set a field on a folder and everything inside it inherits the value. An empty field means "inherit", not "blank it".
- **Protected Type field**: a document's role (Folder, Document, or a name of your own). Templates and the UI read this, never the raw folder/file kind.
- **Project-level metadata** (the inspector when nothing is selected):
overall setup for the whole book — Author, synopsis, and your own fields. Every document can read them.
- **Edit Metadata** (Document menu) opens the full editor for the selected document.
- **Outliner Columns** lets you show metadata columns in the binder, so you can scan a folder's documents by status or POV.
- **Media** (inspector): attach images to a document; they are copied into the project and shown as thumbnails.

## 6. The outliner and the corkboard

- **Outliner**: the binder as a table — titles, types, word counts, metadata — for when you want the shape of the book rather than the words.
- **Corkboard**: index cards for every document under the selected folder, with synopsis and word count. Rearrange by dragging.
- Both views read the same tree; nothing is duplicated.

## 7. Search and links

- **Find / Replace in Section** (Ctrl+F): the usual search inside the current document, with replace.
- **Find in Project / Replace in Project**: search across the whole book.
- **Wiki links** with `[[Title]]` to any section. Hover to preview, click to jump.
- **Mentions / Mentioned by**: the inspector shows which documents mention the selected one, and which mention it. Toggle this scan in Preferences.
- **Related files**: documents that link to each other are listed, so you can follow a thread through the book.

## 8. Snapshots, backups and versions

- **Snapshots** (Document menu): a named point-in-time copy of one document, restorable later.
- **Save and Back Up** (Ctrl+S): saves and makes a full-project backup.
- **Choose Backup Folder**: where backups live.
- **Restore Snapshot**: bring back an earlier version of a document.
- Backups you rename are kept forever; the ordinary ones roll off.
- **Trash**: deleted documents land here first and can be restored.
- The project is plain files on disk — open the folder and read it, copy it, back it up yourself. No proprietary lock-in.

## 9. Tracked changes and revisions

- **Tracked changes**: suggestions — yours or an AI's — arrive as changes you accept or reject, one by one or all at once in a section. Nothing rewrites your manuscript behind your back.
- **Accept All Changes in Section / Reject All Changes in Section**.
- **Revision passes** (Edit menu): named passes over the manuscript, so you can work through a set of edits deliberately.
- **Revision author** setting in Preferences, so the tracked changes carry your name.

## 10. Word counts, goals and progress

- **Word count** on every document, folder and the project, shown in the binder and the status bar.
- **Per-section word goals** and a **daily goal** if you like watching a number climb.
- **Session goal**: a target for this sitting.
- **Reading time**: an estimate beside the word count — about 250 words a minute, and marked as an estimate.
- The status bar clears honestly when nothing is selected.

## 11. Templates and export

- **Templates** decide how an export looks. A template is a folder of fragments (HTML) plus a print-config file. You edit them in the Templates view (View menu).
- **Five shipped sets**: `default` (the ordinary book), `short story` (a unit-dressed collection on A4), `Book` (one page per kind of thing, named after the Type it dresses), `Guide` (a manual: a shaded heading page and a section page, named after the two Types a guide uses), and `no-export` (prints nothing). They live in the code, so there is nothing on disk to edit; right-click → Duplicate into project to get an editable copy.
- **Template Sets** (View menu): pick which set dresses an export.
- **CSS editor** (Templates view): a box that writes the set's `style.css` — the stylesheet that dresses the HTML and EPUB exports.
- **Per-folder template assignment**: a folder can name its own set, so different parts of the book can look different.
- **Document template** (inspector): a document can name its own face, overriding the set.
- **Format folders**: a subfolder named `epub`, `html`, `docx`, `pdf` or `text` overrides the parent set for that format only.
- **Export formats**: plain text, HTML, DOCX, PDF, EPUB, Markdown.
- **Scope**: export the whole project, a folder, or a selection — folders expand, and the template dialog appears for the chosen scope.
- **Import Markdown** (File menu): bring a markdown export back in (see below).

## 12. Import

- **File → Import Markdown**: reads Lekhak's own export and Scrivener's markdown export. Every document becomes a folder or document; the tree is rebuilt from the file.
- **Metadata survives**: every key in the imported YAML becomes a metadata field — created if the project does not have it, filled if it does. `Type` is stored but does not create a new field; `Template` and `Template config` marks are never imported.
- **Value shape picks the field type**: an ISO date becomes a Date field, true/false becomes a Checkbox, anything else Text. You can change the type afterwards.
- **Project-level keys** (Author, Title, Copyright and the rest) fill the project's own metadata only when it has no value yet — your existing settings are never overwritten.
- A document without a parent in the file lands under the folder before it, the order the export wrote.
- The import lands under the container you selected (or the top of the tree when a leaf or nothing is selected). It never overwrites anything you already have.

## 13. Read aloud

- **Read Aloud** (F8), **Pause / Resume** (F9), **Stop** (Speak menu).
- Uses the system's voice (SAPI on Windows, NSSpeech on macOS, espeak on Linux). Off by default; tick it in Preferences to make the menu appear.
- **Hear a sample** (Preferences → Read aloud): try the voice before you keep it.

## 14. AI assistant

- **Continue Scene**: ask the model to continue from the cursor.
- **Proofread Selection**: typos, grammar, doubled words and stray spaces, applied as tracked changes so you review them rather than have them silently rewritten. Works with a local model (Ollama) or a hosted one.
- **Prompt…**: ask the model anything, and the answer comes back as a tracked insertion at the caret.
- **Set it up in Preferences**: **Detect local servers** finds an Ollama on this machine, **Add API…** stores a hosted endpoint, and **Install Ollama + phi4-mini…** puts a small local model in place for you.
- Everything the AI produces lands via the tracked-changes path — never straight into the manuscript.
- Off by default; tick "AI integration" in Preferences and set an endpoint and model.

## 15. Spellcheck and dictionary

- **Spell-check squiggles** in the editor (red wavy underlines). Toggle in Preferences or from the status bar.
- **System dictionary** plus your own word list. Words you mark as correct are added to a per-project dictionary.
- **Dictionary harvest**: Lekhak can scan the project and offer the words it does not know, so you can accept or reject them in batches.

## 16. Preferences and appearance

- **Themes**: System, Light, Rose, Earth, Dark and Night rose — fixed palettes, no picker. The accent colour is built into each theme.
- **Interface font size**.
- **Author** for the whole project, overridable per document.
- **Paste behaviour**: which formatting survives a paste.
- **Mentions scan**: toggle the Mentions / Mentioned by panel.
- **Bug reports**: the Help page names the version and shows where the program and its settings live, and **Copy system details** puts the version, system, install kind and settings folder on the clipboard, ready to paste into a new GitHub issue. It copies nothing from the manuscript and no project path. The same page opens the guide, the code, and the blog.
- **Update check**: four settings. **Auto** looks for a new version once a day at start and installs what it finds (below a new major version, on a git checkout with no local edits); **Check** looks the same way and leaves the install to you; **Manual**, the default, does either only when you press the buttons; **Nothing** makes no network call at all. A look that happens on its own is reported in the status bar, never in a dialog.

## 17. Page setup

- **Page setup** in the inspector: paper size, margins, mirror margins, new-document starts (recto, page break, flow) for each type, headers and footers (three pieces on tab stops, odd/even switching).
- **Divider between child documents**: the separator printed between the children of a folder — `* * *`, or nothing at all.
- **Print-config.txt** in a template overrides page setup; the sample .docx supplies the rest. Page setup applies only where the template is silent.
- **Page size** accepts inches, millimetres, named sizes (A4) or `5x5`.

## 18. Project files and data

- A project is a folder: `project.sqlite3`, a tree of plain text/HTML files, `images/`, `templates/`, `trash/`. Open the folder and read it.
- **Images** live under the project and are referenced by the manuscript.
- **Cover** (inspector, folders only): the image the export uses for the book's cover. Left alone, Lekhak finds `images/…/cover.*` for you.
- **Recent projects** menu, populated on demand so a deleted project never offers a broken entry.
- **Backup folder** setting, per project.

## Not built yet

Honest list of what the app does not do today, so nobody is surprised:

- **Importing anything but markdown.** There is no importer for Word, LibreOffice, yWriter, HTML or Scrivener's own `.scriv` format. The road in is markdown — export from your other tool and import it here. Metadata, fields and the tree all survive that route.
- **AI beyond continue and proofread.** No rewriting and no summarising, and no back-and-forth conversation — **Prompt…** is one question in and one answer out. AI output always arrives as tracked changes.
- **More template sets beyond the ones shipped.** You can duplicate and edit the shipped ones, or build your own from scratch; nothing is downloaded.
- **Cloud sync, collaboration, or a web version.** Lekhak is a single-user desktop app; your files are yours on your disk.
- **Windows and macOS cold runs are proven less.** PDF via LibreOffice, text-to-speech (SAPI / NSSpeech) and the one-click AI install have been exercised on Linux; the code paths exist for the other platforms but have not been run there.
- **A built-in cover designer.** Lekhak does not draw covers; supply an image and use your own tool for the rest.
- **Reading statistics beyond word counts.** No time-on-task, no chapter-level pacing graphs.
- **Mobile or tablet.** Desktop only.

## Where to find things

- Menus: File, Edit, View, Document, Speak (if enabled), AI (if enabled).
- The inspector is on the right: book template, page setup, metadata, mentions, related files.
- Preferences: Edit → Preferences (Ctrl+,).
- Templates: View → Template Sets.
- The roadmap of what comes next: [ROADMAP.md](ROADMAP.md).
