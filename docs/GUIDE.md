# The guide

Lekhika ships with its own manual: a Lekhika project, `src/lekhika/guide/guide.lekhika`, that the app opens for you.

It is **the project's example document as well as the user guide** — a real book, in a real project, that you can browse, search and export.

**There is no separate documentation to keep in step.** The manual is the software: it is inside the package, it opens when the software does, and it changes when the software changes. What does not belong in a manual — notes between versions, the detail that only matters once, the story of how a thing came to be — lives on the blog instead, and the manual is exported there when the writing is done.

- On a **fresh install** the guide is what opens — before anyone is asked to create a project. The software can be looked at before it is used.
- It is **read-only**. It belongs to the software, and an update replaces it. The app opens the guide read-only — the index immutable, every write skipped — so not even the install folder is touched. There is no lock file in it: a marker would travel with every copy of the guide, the writer's own copy included, and lock that too. Nothing you do in the guide changes it.
- You can **export it** to the format you prefer — DOCX, EPUB, PDF, HTML or plain text — and keep it as a help document, or to see how an export is put together.
- You can **work on it**: *File → Save Project As…* copies the whole guide into a folder you choose, and the copy is an ordinary, writable project. That is the way to try the software out on a real book. (The same gesture works for any project: a book you want to keep as it is and work on separately.)

## Writing it

The guide is a regular Lekhika project, so it is written in Lekhika — and there is **one** working copy, so the two of us never edit apart:

| | Where |
|---|---|
| The working guide | `~/Documents/Lekhika Guide.lekhika` — open it in Lekhika (File → Open Project) and write it there. |
| The shipped guide | `src/lekhika/guide/guide.lekhika` — a **snapshot** of the working one, taken when the guide is ready. |

Ship it with:

```bash
python scripts/ship_guide.py            # copy into the package, and check
python scripts/ship_guide.py --check    # check only
```

The check is what a shipped manual has to pass: its records agree (every folder has its `.order.json`, the index and the tree match), it carries no trash (a manual should not ship the things that were thrown away), and no lock file (a marker would travel with every copy of it, the writer's own copy included, and lock that too).

Keep the guide concise and avoid repeating the same information in multiple places. When adding or changing features, update the relevant chapter/section page(s).

## The shape of it

| Part | What it is |
|---|---|
| Main page | `Before you start` (overview): key features for writers, screenshot placeholders, and a map to the sections. |
| Getting started | First steps and the basics of the window/project. |
| Write and edit | Writing, editing, paste, finding/replacing, page view and progress. |
| Organise your book | Binder, outliner/corkboard, types and metadata. |
| Templates and export | Templates, page setup and export formats/options. |
| Bring work in and keep it safe | Importing, backups, snapshots, trash, tracked changes, locking and files-on-disk. |
| Optional tools and settings | Reading aloud, AI, preferences, appearance, spelling and shortcuts. |
| Support | Ways to support the project and where to report issues (last in the binder). |

## The order files matter for the install

A glob's `*` never matches a name that begins with a dot, so the package-data patterns in `pyproject.toml` spell out `.order.json` by hand. A missing pattern is a guide whose folders arrive in the wrong order — and the app's own check notices, as a structure that disagrees with itself.


## Templates

A guide project can carry template sets of its own, under its `templates/` folder, and they can be taken away: with the guide open, right-click a set → **Duplicate into project…** asks which project the copy should go into (the guide itself is read-only), and writes an editable copy there.
