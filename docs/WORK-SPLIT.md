# Work split: who owns what

Two writers, one repository. The rules first, then the areas, then the to-do. Read this before editing anything.

## Rules

1. **`git pull --rebase origin main` before you start and before you push.** A rejected push means somebody else pushed; rebase, re-run the suite, push again. Never force-push.
2. **The suite must be green before a commit lands** —
`PYTHONPATH=src QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests`. A feature without tests is not finished.
3. **One writer at a time in a shared file.** `src/lekhak/app.py`, `README.md` and `ROADMAP.md` are shared: say so before you edit, or work in a new module and let the other side wire it in.
4. **Never overwrite someone else's uncommitted work.** If `git status` shows changes you did not make, stop and ask.
5. **No AI edits text or code except through the mechanisms the app already has** (tracked changes for manuscripts; a human reviewing a diff for code). Advice is welcome; commits are a human act.
6. Small commits, one idea each, message in the repo's style (`area: what changed`, from the user's point of view where possible).

## When a writer is cut off mid-task

Being interrupted is ordinary — a provider limit, a crash, a person going to bed. It is not a discipline failure, and the fix is not "be more careful": it is to make interruption *cheap*. Three habits do that.

- **Small, green, committed steps.** Work lands in coherent increments so a cut-off costs at most the current step. A `wip:` commit is better than a dirty tree: it is recoverable, and a dirty tree blocks the other writer's `pull --rebase` (tonight it did exactly that).
- **Prefer appending to in-place rewriting.** A new module plus a small wiring change survives interruption — the half comes in reviewable, and the tree stays green. A wholesale rewrite of an existing file does not: cut off halfway, it breaks everything that builds against it. This is the strongest argument for doing the Preferences rebuild as a new module rather than by editing `app.py` in place.
- **Branch for anything atomic.** `git switch -c name/feature`, commit there, merge when green. Main stays green whether or not the writer comes back.

**If you find a dirty tree you did not make** — the recovery, in order:

```bash
git diff -- <file> > /tmp/opencode/<who>-<file>.diff    # keep the work
cp <file> /tmp/opencode/<who>-<file>.partial            # keep the file
git checkout -- <file>                                  # restore green
# verify: the suite must pass before anything else happens
git apply /tmp/opencode/<who>-<file>.diff               # if it is wanted back
```

That is what was done for MiMo's half-finished Preferences refactor: the patch and the file are in `/tmp/opencode/mimo/`, the tree is green, and nothing was lost by restoring it.

## Decide → script → run

Once a change is decided, write it as a script that carries its own safety envelope, and run that:

```
back up the files it will touch
apply the change (with assertions, so a moved anchor aborts rather than mangles)
add or update its tests
run the suite
  green → commit, pull --rebase, push
  red   → restore the backup, exit non-zero
```

**Capture the suite's status; never pipe it.** `python -m unittest … | tail` reports *tail's* success, so a red suite sails through the envelope and gets committed and pushed — which happened once (commit `cc98373`, fixed on top by `a539903`). Write the output to a file, keep `$?`, then show the tail:

```bash
.venv/bin/python -m unittest discover -s tests > /tmp/suite.log 2>&1
status=$?
tail -4 /tmp/suite.log
[ "$status" -eq 0 ] || { restore; exit 1; }
```

Then an interruption — a provider limit, a crash, the person going to bed — cannot leave work half-applied or a tree red, because the script either finishes the job or undoes it. It also removes the conversation from the critical path: the decision is the only thing that needs a human, and it can be made long before the script runs.

Tonight's record, in this order: one **abort** (the tests failed because my expectations were wrong, not the patch — the tree was restored untouched) and three **landings** (the inspector label, the outliner scope, the folder tabs).

Recurring tooling belongs in `scripts/`; one-off actions live in `/tmp/opencode/do-*.sh` with the file they patched backed up beside them.

## Areas

**Engine — opencode.** The parts that decide what an export *is*:

- `src/lekhak/project.py` — storage, compile scope, export pipeline, page geometry, counts, snapshots, imports' safety floor.
- `src/lekhak/templates.py` and the new template engine modules.
- `src/lekhak/pageview.py`, `src/lekhak/pdf.py`, `uno_pdf.py`.
- `docs/` — every design document, including this one.
- Release chores: tagging, the blog page, the tester round.

**Interface and features — MiMo.** The parts a writer touches:

- `src/lekhak/app.py` (UI wiring), and new self-contained UI modules.
- `src/lekhak/importer.py`, `odt.py`, `uno_odt.py`, `spellcheck.py`, `scrivener.py`, `ywriter.py`.
- New feature modules and their tests (`open_recent`, `quick_open`, `print`, session goal, and whatever comes next).
- `docs/BUILD-NOW.md` as it works through the list.

**Shared, serialised:** `app.py`, `README.md`, `ROADMAP.md`. When the template engine needs UI (the export dialog's override row, the inspector, the Templates area), the engine lands first with tests; then either side does the wiring in a single pass, announced, with the other holding off `app.py` until it is pushed.

## To-do

| # | Item | Owner | State |
|---|---|---|---|
| 1 | Template engine — discovery, `Type`/title/format matching, slot-driven render, DOCX config/reference/generated, per-export override, Templates area, help levels, update check (`docs/TEMPLATES.md` §9) | engine, then UI | **gated on the author's read of the spec** |
| 2 | Print interior (no colour images, mirror+recto, separate cover PDF) and the optional cover-prep tool (`docs/PRINT.md`) | engine, then UI | trim/mirror/recto already ship; the print branch and cover do not exist |
| 3 | Import consolidation by *format*, the Merge/Skip/Keep/Abort walkthrough, and labels/status/keywords/custom metadata (`docs/IMPORTS.md`) | importers (MiMo), floor + docs (engine) | floor ships; consolidation does not |
| 4 | Trash with a way home; project folder seeded under the project's name | engine (move/restore API) + UI | not started |
| 5 | All the word counts (averages, reading time, day deltas), word-frequency report, auto-linking from exact filename/metadata matches with hover previews, inline comments | UI mostly | not started |
| 6 | Release: tag `v0.1.0`, blog page refresh (install, gift link, screenshots), tester round | engine | pending |
