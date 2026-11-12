# Reading copy and print copy

Two DOCX exports, two jobs. Conflating them is how interior files get rejected by print-on-demand.

## Reading copy (the default DOCX)

For readers, editors, agents, and reading on screen. A **cover page is allowed**, and mirrored margins are unnecessary — single-sided reading wants equal margins. This is what the DOCX export already produces.

## Print interior

For print-on-demand (Amazon KDP et al.), the interior must satisfy the printer's rules, and they are strict:

- **No full-colour content images** anywhere in the interior — a printer rejects the file. Image plates belong in a book *with images*: its own template, its own bleed and paper decisions.
- **Mirrored margins** with a real trim and gutter (1.15), recto/verso page starts (phase 1 already opens chapters recto when a trim is set).
- **No cover in the interior.** The cover is a separate PDF, uploaded separately. The interior starts at the title page.
- Fonts embedded, no restricted colours, no comments or tracked changes left unresolved.

So "print" is a *distinct export*, selected deliberately, not a checkbox on the reading one.

## The cover: supported, not designed by us

We do not become an art studio. What Lekhak can honestly do, as an optional future piece, is the mechanical part of print-on-demand cover preparation:

- place the cover image and the back image, with a **sensible spine width for the book's page count and paper**;
- margins per the trim and bleed the vendor asks for;
- **overlay a grid** so the writer can see whether anything important is being trimmed off;
- upscale (with a quality warning) or downscale the artwork as needed;
- let the writer place text fields where they want them, if that proves tractable;
- export the result as a PDF ready to upload.

It is edited and exported **separately from the interior** — two artefacts, two files — and it is explicitly optional: a future update, provided because Lekhak would like to cover the whole process, not because cover design is its competence.

## Status

Nothing here is built. Reading copy exists (DOCX export). Print interior is the template engine's `print`/`docx` format branch plus the page-setup work already shipped; the cover tool is the optional piece after it.
