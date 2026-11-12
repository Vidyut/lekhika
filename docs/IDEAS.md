# Feature decisions

What we build, what we don't, and why. Decide first, then build — this file is the record so nobody has to argue it twice.

**The test a feature has to pass:** it must make writing *faster or safer*. Performance, not ambiance. Word counts are pacing tools and therefore core; anything purely decorative has to justify itself or go.

## Decided: building

- **All the word counts.** Not one number — every number a pacing writer wants: document count, selection count, chapter and folder rollups, project total, per-scene, average scene length, progress against a target, the session's delta, and reading time. If a count can be derived honestly, it gets shown. (Per-section targets and rollups exist; the rest is UI on data we already have.)
- **Word goals.** Small effort, widely wanted, even though the author does not use them himself.
- **Recent documents.** Needed; there is no substitute for it.
- **Current date / current time** — named exactly that, so it is never confused with a *Date* metadata field on a document.
- **Word frequency / repetition report.** Cheap, and exactly the kind of editing feedback a pacing writer uses.
- **Trash, with a way home.** Delete moves the document to a Trash top-level folder instead of unlinking it. It remembers where it came from and offers "put it back" — like a file manager. Drag and drop wherever you like. Writers are attached to their words; letting them visit the afterlife is cheap goodwill.
- **Worldbuilding is just folders — and there is no Manuscript/Worldbuilding split.** A character sheet is a folder with a synopsis and metadata, exactly like a chapter; notes, places and languages are more folders, and they can be exported like anything else when the writer selects them. Three areas, then: **the project folder** (named after the project, holding everything), **Templates**, and **Trash**.

**Trash and Templates are outside the manuscript: never exported, never counted.** Templates are outside by construction now — a template is a folder of files under `templates/`, holding no binder documents at all (2.4b); Trash gets the same treatment when it exists. Everything else — notes, research, characters — counts and exports like any other folder, because it is one: if the writer selected it, they meant it.
- **Auto-linking from filenames.** If a metadata value *exactly* matches a document title, it links to it. Instances of a document's title inside the text become links too, auto-listed in the sidebar for the document you are in; **hover shows the beginning of the target document as a popup**, click goes there. (The link machinery and the related-files scan already exist — this is presentation on top of them.)
- **Inline comments / annotations**, if it does not turn into a project of its own. Tracked changes and notes-as-documents cover part of it today.
- **Importers by format, plus the obvious gaps** — DOCX and ODT. See `docs/IMPORTS.md`: a markdown export from Scrivener is markdown, not a Scrivener file, and should not have its own code path.
- **Cloud sync and secure cloud backup**, as *options*. See below.

## Decided: not building

- **Typewriter scrolling.** Ambiance. We are efficiency-obsessed; the code should deliver performance, not a mood.
- **Themes.** Whatever the desktop or system provides is what renders. Not our business unless somebody says "I can't read this" — and then it is an accessibility fix, not a theme engine.
- **Markdown-first editing.** Most writers do not know markdown, and HTML gives us better formatting options. Markdown stays an import and export format, which is where it is genuinely useful.

## Settled properly: local vs hosted, and the cloud

There is no "local-first rule" and no opinion about what anyone else should do. The position is:

- **Hosted APIs are the capable option** and local Ollama is the modest, inferior one — that is what the evidence says.
- **Local is offered because some authors are twitchy about feeding unpublished work to a model.** That is a legitimate reason to accept a worse tool, not a philosophy.
- **Secure cloud backup or sync is welcome** if it can be done safely and opt-in — a dead laptop is the single largest risk to a manuscript, and no amount of local purity fixes it.
- Nobody's choice is judged either way.

## Open, needing discussion

- **Quick open (`Ctrl+P`).** The question asked was whether it differs from clicking a title in the binder. Clicking *selects and shows*; quick open *finds*. In a 300-document binder the difference is twenty seconds of scrolling versus three letters — but it is only worth building if the binder feels slow in practice.
- **“Keep me honest to my story bible”.** A central document (or set of notes) holding character and worldbuilding context — a story bible — that the app can use to flag where the manuscript contradicts that context or doesn’t fit (e.g. with optional LLM assistance). Useful for longer-form work; needs careful design so it helps without being noisy or overreaching. Draft workflow: `docs/STORY-BIBLE-CHECKS.md`.
- Import UI wording and flow (see `docs/IMPORTS.md`).

## Ceiling note

The AI-facing features scale with the model behind them: a small local one fixes bloopers, a good API is genuinely useful for prose. Everything that depends on a model should be built so it degrades honestly instead of pretending.
