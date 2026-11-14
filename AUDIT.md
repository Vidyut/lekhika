# Audit of Lekhika

An independent static review of the Lekhika source, run by an AI auditor that had never seen the code before and then checked line by line against the repository by the author. Every finding below was verified in the code before anything changed; the three places where the auditor's own wording did not match the code are set out under [Corrections](#corrections).

This is a code review, not a penetration test and not a security certification. Nothing was run against a live install, no dependency was checked against known CVEs, and no dynamic or fuzz testing was done. It says what was read and what was fixed — not that no bug can exist.

Reviewed on 14 November 2026, against the source at v1.0.5 plus the six audit commits named below. Scope: `src/lekhika/` (app, project, layout, disk_doc, ai, preferences, pdf, uno_pdf, tts, spellcheck, templates, revisions, cover, pageview, markdown, markdown_import, flowlayout, guide), `scripts/`, `pyproject.toml` and `.github/workflows/release-build.yml`. `revisions.py`, `markdown.py`, `flowlayout.py` and `guide.py` were read and came back clean.

## Summary

| Area | Verdict |
|---|---|
| Storage and data integrity | No silent data loss found: writes are atomic, and a disagreement between the files and the index is always put to the writer |
| Network and telemetry | No hidden calls. Three opt-in routes, all listed below; nothing else in the app opens a socket |
| Credentials | An API key is never sent over a cleartext connection to a remote host |
| Subprocesses | No `shell=True` anywhere; every external program is started with an argument list |
| Updates | No automatic download or execution of binaries; installing is a click, and only a git checkout is ever pulled |
| Diagnostics | Carries no manuscript text, no project path and no document title |
| Release pipeline | Six targets built from public tags and smoke-tested; macOS packaging fixed, one re-tag outstanding |

## What Lekhika is

Filesystem-as-truth with a SQLite index. A project is a directory of UTF-8 HTML documents, `.order.json` order manifests and a `project.sqlite3` index; each document carries a YAML frontmatter block with its title, synopsis, word target and metadata. Nothing is locked in a proprietary container: open the folder and read it.

## The boundaries that matter

### Network: three routes, all opt-in

- **AI** (`ai.py`) — only when the writer enables AI and names an endpoint. The default is `http://localhost:11434/v1`, a local Ollama. A key is never sent over plaintext `http://` to anything but localhost (`_sends_key_in_the_clear()`), and the endpoint scheme must be `http://` or `https://`.
- **Text to speech** (`tts.py`) — only when the writer configures an external TTS endpoint and asks for a voice. The same cleartext rule applies to the key, and to the text being read.
- **Update check** (`preferences.py`) — one GET to the public GitHub tags API. The default mode is "check only when I ask"; the daily automatic check has to be chosen, and "never check" makes no network call at all.

There is no telemetry, no analytics, no crash reporting, no account and no licence check. `grep -rln "urllib.request" src/lekhika/` returns exactly these three modules.

### The AI never writes to the manuscript

AI output reaches the editor only as tracked changes: `_ai_continue_scene()`, `_ai_prompt()` and `_ai_proofread_selection()` all call `LinkEditor.apply_tracked_replacement()`, which writes revision spans backed by rows in the `revisions` table. Nothing joins the manuscript until the writer accepts it, and AI stays off until it is switched on.

### Writes are atomic

`Project._atomic_write()` and the template slot writer create a temporary file in the target directory, write it, flush it, `fsync()` it and `os.replace()` it into place. A crash mid-save leaves the previous file intact.

### Storage, backups and drift

- SQLite runs in WAL mode with `PRAGMA foreign_keys = ON`.
- A read-only project opens its index through a `?immutable=1` URI (`Path.as_uri()` on Windows), so it never writes a journal into the install folder.
- `Project.backup()` checkpoints the WAL, copies the project into a timestamped folder **outside** the project, and prunes only folders matching its own stamp pattern (`\d{8}-\d{6}-\d{6}`) — the backup folder belongs to the writer, so anything else in it is left alone.
- When the files on disk and the index disagree, Lekhika takes a backup and then asks which side is right. Structure, title and metadata drift are three separate questions, and none of them is answered silently.

### Subprocesses

Every external program — LibreOffice, the UNO worker, Piper, aspell, hunspell — is started with an argument list; there is no `shell=True` anywhere in `src/` or `scripts/`. LibreOffice conversion runs against a private profile in a scratch directory and in its own process group, so a timed-out conversion cannot leave a process behind or disturb a desktop session of the writer's own.

### Updates

The modes are "check automatically", "check only when I ask" (the default) and "never". A check only ever reads the public tags API and says what it found. An install is applied only for a git checkout, only when the working tree is clean, and only as `git pull --ff-only`. A packaged install is never rewritten behind the writer's back: the release is named and the update is the writer's own command.

### Diagnostics

`preferences.diagnostics()` produces the block a bug report needs: version, install kind, Python and Qt versions, whether LibreOffice was found, and the settings folder — with the home folder shortened to `~`. It carries no manuscript text, no project path and no document title.

### Release pipeline

`release-build.yml` builds from public source tags for Windows (x64, arm64), Linux (x64, arm64, plus AppImages) and macOS (Intel, Apple Silicon), and smoke-tests each build on its own runner before it is packaged.

## Fixes made in response to the review

- **Binder context menu** (`app.py`) — every right-click raised `NameError: doc_id`; the selected row's id is now bound before the menu is built.
- **Trash restore** (`app.py`, `project.py`) — see [Corrections](#corrections).
- **Drift-dialog labels** (`app.py`) — text taken from the project was interpolated into the dialog unescaped; it is escaped now.
- **Debug print in the save path** (`app.py`) — removed.
- **AI server settings** (`ai.py`) — save and load now agree on one stored shape (dictionaries, with the older list form still readable), remote-model detection takes the API key, both endpoints refuse a scheme that is not http(s), and the Ollama installer opens the download page when the script cannot run.
- **The AI request no longer freezes the app** (`app.py`) — the request runs on a worker thread and the answer comes back through a signal, so it lands on the UI thread where the editor is; the writer can keep writing while the model thinks. The reply is inserted as a tracked change at the caret as it stands when the answer arrives; a reply for a scene the writer has left is not inserted anywhere but goes to the clipboard; one request at a time. The "Ask the AI" popup, which raised `AttributeError` on every use, works again.
- **Word target** (`layout.py`) — a hand-edited value now reads as 0 instead of failing; an unused constant is gone.
- **Table of contents and reconciliation** (`project.py`) — ToC labels are escaped for strict XHTML; the shadowed duplicate export check is gone; the read-only SQLite URI is URI-encoded; a `.lekhika` suffix is appended rather than replacing a dotted suffix; `reconcile()` now writes the order exactly as the binder does, including a leading `index.html` and list order; `effective_cover()` cannot climb out of the project; `copy_to()` checkpoints the WAL first.
- **Update review** (`preferences.py`) — `_preferred_download_asset()` raised `NameError` for every release with assets, so the check always fell back to the generic releases page with no download link; the Piper progress handler is the one that was written rather than a fresh lambda; the dead `apply_update()` is gone (v1.0.5 removed the Auto mode that called it).
- **External runners** (`tts.py`, `disk_doc.py`) — the Piper tarball is unpacked with a data filter (3.12+) or, before that, a name check, so a member cannot write outside the folder it belongs in; the TTS API route follows the chat client's rule — http(s) endpoints only, and no key (nor the text being read) over a cleartext remote connection; a failed write or rename no longer leaves a `.tmp` file behind that the binder would read as a document.
- **Release build** (`.github/workflows/release-build.yml`) — the smoke test no longer continues on error, so a bundle that crashes on start stops the release instead of publishing green; the bundle is found by what PyInstaller actually produced (macOS makes a `.app`, Linux and Windows a folder — the prune step died on macOS, where `dist/Lekhika` does not exist); archives use `zip -ry` so the symlinks a macOS bundle needs survive.
- **Scripts** (`scripts/`) — the Linux launcher parses its `Exec=` line with `shlex`, so a path with spaces is read as one path; the macOS installer takes its version from `pyproject.toml` (it was hardcoded at 0.1.0); the blog uploader accepts only http(s) sites and says so plainly for a non-numeric category or tag id; the icon renderer explains `--icns` without a path instead of raising `IndexError`.
- **Covers, preview, templates and imports** (`cover.py`, `pageview.py`, `uno_pdf.py`, `templates.py`, `project.py`, `markdown_import.py`) — the writer's cover art is flattened to an RGB PNG before ReportLab draws it (a CMYK JPEG fails and an RGBA PNG inverts); the page preview resolves relative image paths from the project folder, and a bottom margin too small to hold the page number no longer draws one into the page's own edge; LibreOffice's profile log is read with `errors="replace"`, so a Windows codepage log cannot hide the real conversion error; the writer's own template sets live where the platform keeps user settings (Windows app-data, macOS Application Support, XDG on Linux), with the old Linux folder still used where it is the only one; imported Markdown is read as `utf-8-sig`, so a file with a UTF-8 BOM keeps its frontmatter fence and document header.

Six commits carry these changes: `6e54ca8`, `0458954`, `a87a83d`, `9f4ccd6`, `2799f4b`, `99c2c3a`. The test suite went from 1068 to 1092 tests, all passing.

## Corrections

Three statements in the auditor's report did not match the code. They are recorded here rather than quietly dropped:

1. **"Updated `_restore_selected()` to pass the trash-relative Path rather than the document UUID to `restore_from_trash()`."** — `restore_from_trash()` was dead code: the `.deleted.json` marker its row repair read was never written by anything, so no restore could ever have succeeded through it. The function was removed, and `_restore_selected()` now restores through the same `reorder` operation as dragging an item out of the trash — the file, its rows and the folders it vacates move together, so nothing comes back half-restored. One edge the old code did not consider: if the item's own folder is still in the trash, the folder is restored first.
2. **"Normalized stored DB paths to POSIX separators."** — not done, and deliberately not claimed. The sweep would touch every path column, and the path that actually broke on Windows — the read-only SQLite URI — was fixed with `Path.as_uri()` instead. The sweep stays on the list of known limits below.
3. **"The app makes two kinds of outbound request — the LLM endpoint the writer configured and this update check."** — three: the external TTS endpoint is a third route, and it is listed above.

Two claims were checked and rejected outright: the README's PowerShell flags are correctly spaced at HEAD and in the history, and `item(row)` cannot become `None` between `currentRow()` and `item()` because no event loop runs in between.

## Known limits

Honest things this review did not settle:

- **Model detection in Preferences still waits.** Choosing a server in the AI preferences asks it what models it has, on the UI thread — a few seconds at most, not the two minutes a completion could take, but it is a wait.
- **Stored paths are not normalised to POSIX separators on Windows.** Deferred; see Corrections.
- **No dependency vulnerability scan.** The four runtime dependencies (PySide6, python-docx, Pillow, reportlab) were not checked against known CVEs.
- **No dynamic testing.** Everything here is source reading and the unit suite. The release pipeline itself was not run from a tag; the macOS packaging fix has not yet been proved by a real release, so one re-tag is outstanding.
- **Per-field date display formats** are not yet configurable; dates are stored canonically as ISO and shown in one chosen format.

## Checking this yourself

```bash
grep -rn "shell=True" src/ scripts/                 # nothing
grep -rln "urllib.request" src/lekhika/             # ai.py, tts.py, preferences.py
grep -rn "os.replace" src/lekhika/                  # every write swaps a finished file in
grep -rn "immutable=1" src/lekhika/                 # the read-only open
```

Then read `src/lekhika/project.py` (storage) and `src/lekhika/app.py` (the UI's write paths) — they are where the guarantees above live.
