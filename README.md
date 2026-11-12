# Lekhika

Lekhika is a **free, open-source** writing app for books and long-form projects. Plan, write and organise chapters and scenes, then export a finished manuscript — while your work stays in ordinary files on your computer.

The idea is simple: **just write.**

*If Lekhika earns a place in your writing life, you're welcome to [send a gift](https://paypal.me/VidyutG) — entirely optional, and the software is free and open source either way.*

## Key features for writers

- **Free and open source.** No account, no subscription, no lock-in.
- **Automatic updates (opt-in).** Choose how updates are checked and installed in Preferences, or turn checks off entirely.
- **Easy exports, highly customisable.** Export to DOCX, EPUB, PDF, HTML or plain text with simple defaults, or use templates, page setup and placeholders for more control — including exports suitable for uploading to platforms such as Amazon KDP.
- **Files as truth.** Projects are stored as readable files and folders on disk, with structure and metadata preserved, so your work stays accessible and under your control.
- **Better paste for book formatting.** Control what formatting is kept when you paste, so bringing text in does not mangle your manuscript.
- **Dictionary that includes in-world terms.** Spell-check uses your system dictionary plus a project word list, and project scan helps you add names and other project-specific terms in batches, so that you can trust your spell-check.
- **Read aloud (TTS).** Hear your prose to catch rhythm, clarity and mistakes.
- **AI assistance, on your terms.** Optional local or hosted AI for continue, proofread and prompt — with output arriving as tracked changes you review, and AI is never allowed to alter your manuscript without your approval. All the ease without the risk.
- **Tracked changes and organisation.** Review edits, work in chapters and scenes, use the binder, outliner, corkboard, metadata, search/links, and word counts/goals to manage your manuscript.

## What's inside

- **A binder that thinks like a book.** Chapters, scenes, notes, folders — nested however you like, rearranged by dragging. A real corkboard and an outliner when you want to see the shape of the thing instead of the words.
- **Word counts everywhere.** Every scene, every chapter, every folder, the whole project, and how much you've written today. Targets and progress help you pace the work.
- **A comfortable editor.** Rich text with paragraph styles, fonts, alignment, lists, focus mode, and zoom that makes text larger without changing document formatting.
- **Snapshots and backups.** Take snapshots before big changes, restore when needed, and keep full-project backups (with flexible scheduling/frequency options).
- **Links and related files.** `[[Wiki links]]`, mentions/related files, and project-wide search and replace to move through and maintain a large manuscript.
- **Metadata that behaves.** Status, POV, dates and your own fields; use metadata and placeholders in exports.
- **Templates and export.** Export the whole project or a selection with template sets, format-specific overrides, page setup and placeholders.
- **Bring your work over.** Import markdown or from another Lekhika project (including structure/metadata and referenced assets where applicable), without overwriting existing work.
- **A guide that comes with the software.** Lekhika opens its own manual on a fresh install. It is read-only; use *File → Save Project As…* to copy it into an editable project.

## There's more in the works

- **Ready-made book formatting:** download a free book template or one of Amazon's templates, add it to Lekhika, and let the software try to match the formatting.
- **Print-ready manuscripts:** interiors that satisfy print-on-demand rules, plus help preparing a cover — spine, bleed and all — without either of us becoming a graphic designer.
- **AI-assisted editing:** catch typos, mistakes and bloopers, with tracked changes so you review suggestions rather than have an AI quietly rewrite your work. Works with a small local model if you'd rather not send your writing anywhere.
- **More import sources:** markdown is the road in today, which covers Scrivener and anything else that exports markdown. Word documents and the tools whose exports need converting are next.

Those are goals, not promises about what already works.

## Your writing stays yours

Your book is saved in ordinary files rather than locked into a proprietary format — you can open the folder and read it. Lekhika also makes backups and lets you restore earlier versions, and it asks before it deletes or overwrites anything. Ever.

## Getting it running

Lekhika will run on almost any Python you already have (3.9 or newer). If you haven't got Python, download it from [python.org](https://www.python.org/downloads/) — and on Windows, tick "Add python.exe to PATH" while you're installing it.

**First, get the code.** In a terminal:

```bash
git clone https://github.com/Vidyut/lekhika.git   # download it
cd lekhika                                        # go into that folder
```

No Git? On the [GitHub page](https://github.com/Vidyut/lekhika) click **Code → Download ZIP**, unzip it, and open a terminal in the folder you unzipped. Either way, every command below runs from inside that folder.

**Now set it up.** This keeps Lekhika's bits in a private folder inside the checkout — nothing system-wide, no administrator rights needed.

On Linux or Mac:

```bash
python3 -m venv .venv              # make a small private Python for Lekhika
.venv/bin/pip install --upgrade pip    # installers do the fetching: keep yours current
.venv/bin/pip install -e .         # install Lekhika into it
```

On Windows (in PowerShell):

```powershell
py -3 -m venv .venv
.venv\Scripts\pip install --upgrade pip
.venv\Scripts\pip install -e .
```

Worth doing once in a while even if you installed long ago: `pip` is the thing that fetches everything else, so its own security updates matter more than most. (Lekhika itself does not use pip, `uv`, or `certifi` while it runs.)

**Finally, make it start like a normal app.** One command, and Lekhika will show up in your applications menu.

On Linux:

```bash
python3 scripts/install_linux.py
```

On Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
```

On Mac:

```bash
bash scripts/install_macos.sh
```

If you'd rather just have a look first, you can start it right away without any of that: `.venv/bin/lekhika` on Linux and Mac, `.venv\Scripts\lekhika` on Windows.

Screenshots, and the same steps with pictures, are on the [Lekhika page at vidyut.info](https://vidyut.info/downloads/lekhika/).

It's still an early release, so expect rough edges; the roadmap is in [ROADMAP.md](ROADMAP.md).

**Lekhika is free and open source, licensed under the GPL.** Use it, modify it, share it. If you'd like to say thank you, [send a gift](https://paypal.me/VidyutG) — optional, and it stays free either way.

Source code: [GitHub](https://github.com/Vidyut/lekhika).
