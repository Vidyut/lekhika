# Scrivener behavior, recovered

What Scrivener actually does, read out of its binary, and what Lekhak does about
each entry. This is the ledger behind the roadmap items that copy a Scrivener habit
on purpose: match the parts writers depend on, and be explicit about the parts we
deliberately do better — or not at all.

## How this was read

- Target: `Scrivener.exe`, Scrivener 3 for Windows, from a throwaway Wine prefix
  (`scrivener-test`, a copy of the author's own install). The live install and the
  live projects were never used. SHA-256
  `8e2047ecad6b2afe9efc0c5bc5b8973c2d4a1a1e220a8792e74440bfe3693b71`.
- Method: reverse-engineering tooling for binary structure — PE imports, symbols,
  disassembly — plus plain `strings` for the app's own vocabulary: 830 internal
  class names (MSVC RTTI descriptors), 1,088 Qt actions and slots, and the help
  text Scrivener ships inside itself.
- Raw lists: `/tmp/opencode/rea/classes.txt` and `/tmp/opencode/rea/moc-slots.txt`
  (session working files).
- Nothing here is Scrivener's source. Every line is a recovered name or a string the
  binary carries — evidence, not a copy. Ghidra-level decompilation of the compile
  machinery is a follow-up pass, tracked under "Open questions".

## Compile pipeline — the recovered model

Compile is a two-stage machine. The dialog (`SCRCompileDialog`) walks the binder and
builds a content model (`SCRCompileContent`); then a **compile format** — shipped as
data under Qt resources — decides how that content becomes a file:

    :/CompileFormat/SectionLayouts    /Separators        /Styles          /Replacements
    :/CompileFormat/Processing        /PageSettings      /MetaData        /HTML
    :/CompileFormat/LaTeX            /ScriptSettings    /RTFCompatibility
    :/CompileFormat/FootnotesComments /TablesAndLists   /Transformations
    :/CompileFormat/Css /Pdf /PdfSettings /DocumentTitleLinks /EBookToc /Statistics

- **Section layouts** are per-document compile rules (text layout, separators, page
  settings, styles). Documents are matched to layouts by **section type**
  (`SCRSectionTypeModel`, `SCRAssignSectionLayoutsDialog`); `As-Is` documents compile
  verbatim, as do layouts with "Use default separators" ticked.
- **Separators**, from Scrivener's own help text: *"Separator before sections"* (when
  the layout changes), *"Separator between sections"* (when it does not), *"Blank line
  separator"* (scene breaks inside a document).
- **Replacements** rewrite compiled text (`SCRCompileReplacements`,
  `:/CompileFormat/Replacements`), with an internal clipboard format
  `application/scrivener.replacements`.
- **Writers** close the pipe: `SCRMmdWriter` (MultiMarkdown), `QFnWriter` (Fountain),
  `QTextDocumentWriter` (ODF/RTF class), `QZipWriter` (the DOCX/EPUB containers), and
  generator strings "Scrivener for Windows ePub Writer" / "HTML Writer". PDF runs
  through `SCRCompileFormatPdf` and Qt print support.

**Lekhak decision — match the shape, not the bugs.** The exporter already walks
binder → template → writer (1.2, 2.1). The gap against this model is *per-document
compile rules* and *section types* as a binder property; that is roadmap 2.1's next
layer, and this map is its spec.

## Indent, and the prefix-tab class of bug

This is the machinery behind the export problems that started this project.
Scrivener's first-line-indent story does **not** live in the document; it lives at
compile time, in `SCRCompileFormatTextLayout`, as an explicit choice:

    DoNotFlatten   FlattenAfterBlankLines   FlattenAfterHeadings   FlattenAfterSeparators

- The UI toggles it (`onFlattenFirstIndentToggled`); the editor exposes an indent
  ruler — `increase/decreaseFirstLineIndent()`, `…HangingIndent()`,
  `decreaseFirstLineAndHangingIndent()`, `SCRIndentationDialog`,
  `setFirstIndentPosition(qreal)`.
- A compile transformation `ConvertFormattingPreserveTabsAndIndent` preserves tabs
  and indents inside Preserve-Formatting blocks; HTML exports emit
  `<span class="Scrivener-tab-span">` and `<span class="Scrivener-converted-space">`
  for tabs and runs of spaces.
- Screenplay conversion reuses the machinery: *"Convert Preserve Formatting blocks to
  dual dialogue tables"*, *"Convert tables and lists to MultiMarkdown"*.

**Lekhak decision (corrected 2026-10-31).** Indent *values* are first-class
stored prose property: paragraph first-line, left, right and hanging survive the
round trip (Qt block formats carry them already). The *flatten* decision is a
property of a **style inside the template** — exactly where Scrivener's own
Styles page puts it — not of a document or a book setting. A tab is a tab in the
exported RTF/DOCX/EPUB, or a named style — never a magic span whose meaning
depends on which writer ran. `Scrivener-tab-span` is the bug class to avoid.

## Binder, collections, and the rest of the vocabulary

- Binder icons recovered: `Draft`, `Group`, `Text`, `TextGroup` — Scrivener's four
  item shapes; Lekhak's folder/scene icons cover the same ground (0.1).
- `SCRCollection*` — collections: named, persistent document sets with a search bar
  and per-collection color. Lekhak has search (1.1) but no collections: a coverage
  gap, not a design difference.
- `addAndEditComment()`, `addAndEditFootnote()` — comments and footnotes both exist
  in Scrivener; Lekhak has neither, and tracked changes do not cover the need.
- `addMetaData()`, `addMetaDataListItem()` — custom metadata, matching 1.12/1.14.
- Elsewhere in the binary: a MathML equation editor, TTS, hunspell spellcheck, and
  SQLite (Qt6Sql) — noted for the files-first/SQLite-as-cache storage decision of
  2.5, which stands.

## Deep pass (Ghidra), what it settled

Imported the binary into Ghidra 12.1.4 through REA: **35,553 procedures, 38,539
strings**, symbols otherwise stripped (functions read as `FUN_*`; navigation is by
string and RTTI xrefs).

- The enum names `FlattenAfterHeadings` / `FlattenAfterSeparators` /
  `FlattenAfterBlankLines` sit at `0x140f03ea8/c0/d8` and `DoNotFlatten` at
  `0x140eff7a0`; `SCRCompileFormatTextLayout`'s RTTI descriptor is at
  `0x14118ada8`.
- Every name is referenced from two code regions only: `0x1409250f0` (4,497
  instructions, 592 blocks, 524 strings — the compile-format property machinery)
  and `0x140933580` (7,270 instructions, 613 blocks). Both are too large for
  whole-function decompilation (Ghidra's response buffer), so the enum's *integer
  values* and the flatten *rule* live inside these two and need windowed assembly
  reads — which stay available on the warm session for a specific question.
- The moc slot `onFlattenFirstIndentToggled` is at `0x140f38966` in the moc tables
  — again the dialog side, not the compile pipeline.

## Runtime pass (observed), 2026-10-31

A compile session in `scrivener-test` (strace, 964k lines) on the scratch corpus —
open, compile DOCX + RTF + EPUB, quit. The trace also caught the copied prefix's
"reopen last project" setting, so this pass actually ran on the author's live
*A Fictional Book* from the live prefix's point of view: no prose was written
(lock file created and removed cleanly, `binder.backup` regenerated,
`Settings/ui.ini` rewritten), but it is why the house rule below exists.

**The observed compile sequence**

1. Compile reads format definitions from the project itself:
   `<project>.scriv/Settings/Compile Formats/*.scrformat` — compile formats are
   *per-project data*, not application data.
2. Composition injects, per document: a bookmark around each scene
   (`{\*\bkmkstart <scene-slug>}` … `end`), PoV and Location as **character
   styles** above the scene (`\cs25 PoV`, `\cs26 Loc`, Cinzel, coloured), custom
   scene separators (the author's `~*~*~*`), `\sect\sectd` section breaks around
   front matter, mirror margins (`\facingp\margmirror`) and book-title/author
   running heads with page-number restarts.
3. RTF out through a LibreOffice-dialect writer (`\ltrch\loch`, `\*\cs25`) — the
   same engine family as Lekhak's LibreOffice-backed PDF path.
4. DOCX out through a **two-stage pipeline**: compose temp RTF in
   `%TEMP%\Scrivener\<guid>.rtf`, then `Aspose.exe` (129 invocations) converts it
   into the final `.docx`.

**The indent contract — this is the prefix-tab answer**

- Source scenes: bare `\fi` / `\li` control words (resets, no first-line indent).
- Output RTF: `\fi360` (0.25") on 131 body paragraphs — the indent is **injected
  by the compile format's text layout**, never stored in the document.
- Output DOCX for that format: `<w:ind w:firstLine="360"/>` as **direct paragraph
  formatting** on 3,689 paragraphs; the `Normal` style itself carries no indent;
  chapter headings come from a real `Heading2` style (with tab stops from the RTF
  leg). Direct-versus-style is Scrivener's design choice for an *injected* indent,
  not a rule.
- Leading tabs were zero in this output *because this format injected the indent*,
  not because tabs are forbidden. The contract to preserve and test is the round
  trip: what the document contains is what the output contains; only the format's
  declared layout is added. A literal tab a writer typed comes back as a tab. The
  "prefix tab" symptom is a compile-time re-encode of leading whitespace — the same
  family as the HTML writer's `Scrivener-tab-span` — and the Aspose temp-RTF leg is
  the new suspect for where it creeps in.

**Lekhak decisions this settles**

- Compile-time injection, not document-time storage: the first-line indent,
  separators, PoV/Location prefixes and bookmarks are added while composing the
  output, never written back into a document. Where each is *decided* was
  corrected later the same day: styling and flattening in the template (per
  document type), inclusion by the no-export template, page breaks by the
  existing setting, PoV/Location by title substitutions, and only the book
  section as a per-document fact. Golden tests per format.
- DOCX: an injected indent may be style or direct formatting — our choice on
  robustness grounds; headings as styles; heading tab stops only where the layout
  needs them; and literal writer tabs round-trip untouched. RTF: LibreOffice
  dialect so LO, Word and any converter read identical structure.
- Compile formats live in the book (Scrivener) versus templates in the app
  (Lekhak today) — a decision worth making consciously when 2.1 lands, not by
  inheritance.
- The DOCX path's Aspose dependency is a portability trap for anyone else's
  export: Lekhak's python-docx direct write keeps the output inspectable, and the
  temp-RTF leg is worth avoiding entirely.

**Where Lekhak deliberately differs**

- Scrivener formats every injection separately — labels, PoV/Location, separators
  each with their own per-format styling — and the seams between those injections
  are where the bugs live (the tab-span and prefix-tab family are both seam
  artifacts). Lekhak's one-template model holds the whole book's design in one
  place; that stays, and the golden tests guard the seams that do remain (where the
  export walk injects PoV/Location and separators).
- What to borrow anyway: their previews render **Lorem ipsum** sample text instead
  of a bare content placeholder, so the writer judges real typography instead of a
  label. Lekhak's template preview should sample prose the same way.

## UI pass (observed), 2026-10-31 — the author's own screenshots

**Per document (binder + inspector).** The binder doubles as an outliner with
columns: Title, Total Words, **Include in Compile** (a checkbox column), PoV.
The inspector carries General Metadata (Created/Modified), the **Include in
Compile** checkbox, a **Section type** dropdown (N/A / Front Matter / Heading /
Scene — Heading and Scene are their per-document roles, the others are book
regions), then Custom Metadata fields (Date, Location, Quote, PoV, Source).
The bottom bar holds Label / Status / Keywords.

**Compile Overview (one compilation).** "Compile For:" picks the writer; the
formats list splits **Scrivener Formats** (built-in) from **Project Formats**
(the author's own — AS-Manuscript, MH - Modern for html, Modern Astray-Test,
News quotes, Paperback copies). Section Layouts preview live (font dropdown,
sample rendering; the As-Is layout says formatting follows the editor but with
a chosen font). The right pane sets scope: the compile group, "Treat compile
group as complete manuscript", "Include text of containing group", a per-document
list with Title + Section Type columns and include checkboxes, and **"Add front
matter" / "Add back matter"** rows pointing at their own formats.

**Format Designer (per format).** A Section Layouts table where each layout
tickes what it carries — **Title / Metadata / Synopsis / Notes / Text** — and
is "Assigned to:" a section type; their file holds Section Text, Section-pov-loc,
New Page, ToC. Layout tabs are Formatting / Title Options / New Pages / Prefix /
Suffix / Settings:
- Title Options: "Insert title as run-in head", Title Case, and **Title
  Prefix / Title Suffix** rich text with per-field Case — theirs reads
  `¶ <$custom:PoV>: :` prefix and `<$custom:Location>` suffix, in Small Caps.
- New Pages: "Pad top of page with N blank lines", "Number of opening words to
  make uppercase (max 10)", small caps, "uppercase even when not after a page
  break".
- Prefix: "Section prefix:" rich text, "Place prefix after title".
- The left rail lists the same per-format pages the binary's resources name:
  Styles, Text Layout, Transformations, Replacements, Statistics, Tables,
  Footnotes & Comments, Page Settings, Compatibility.

**Pages still unobserved (author's note, 2026-10-31).** Screenshots exist for
Section layouts, separators, styles, text layout and document title links only.
Missing, and the first three matter to the walk because they *change or add
text*: **Transformations** (case, quotes, italics→underline and similar),
**Replacements** (a find/replace table, which can insert as well as correct) and
**Footnotes & Comments** (their stripping rules — related to the "Delete text of
this style" switch above). Statistics, Tables, Page Settings and Compatibility
are presentation/tooling and can wait. Consequence for 2.1b: processing runs
over *assembled* output, so assembly and writing must be separate steps; whether
replacements also reach template pieces is what the screenshots will tell.

**The remaining pages, now observed (2026-10-31) — and what they cost.**

- **Transformations** (4 switches): smart→dumb punctuation; italics→underlines;
  underlines→italics; superscript ordinals in titles, synopses & metadata; plus
  "Add indent per outline level" in points (0.000 in this format).
- **Replacements**: a table of Replace / With / Reg Exp / Case-Sensitive / Whole
  Word. The author's own rows show the failure: source and target are the same
  placeholder class (`!fig($@)` → `<$n:figure:$@>`), a half-migrated scheme, and
  the verdict is that they *do not really work well*. Not a design to copy.
- **Statistics**: exclude front matter / back matter; include all text, or only
  main text / synopses / notes; count footnotes; count comments and annotations;
  don't count spaces in character counts.
- **Tables**: stitch adjacent tables, blank row between them, restrict stitched
  width to page width.
- **Footnotes & Comments**: override font; indent footnotes to match text;
  period+space instead of superscript markers; footnote/endnote format and
  numbering; export comments as margin comments; enclosing markers `[ ]`.
- **Page Settings**: use project page settings; Page Setup…; default paper
  6×9; margins 0.75/0.75/0.7/0.5; *Headers and Footers* tab with different first
  page, page numbers counting first pages, **"main body header and footer
  starts: After front matter"**, facing pages, different header/footer after
  page breaks, different for back matter, none on single pages; header font EB
  Garamond Italic 12, footer EB Garamond 11.
- **Compatibility**: flatten footnotes/comments to regular text; Word-compatible
  list indents; hyperlinks coloured and underlined; exact highlight colours.

Two facts here are worth keeping beyond the panels: **front matter is a real
compile region that headers and footers key off** ("starts after front matter"),
and **page geometry already belongs to the format**, not to each document — which
is 2.1c from the other side.

**The remaining four pages, and where the flatten enum actually lives.**

- **Separators**: "Default Separators" for Folders and Text Files, then a
  per-layout list (Heading, Section Text, Section-pov-loc, New Page, ToC) each
  with *"Separator before sections"* (a preset — Page Break — plus a rich-text
  field) and *"Separator between sections"* (Single Return), an *"Override
  separator after"* switch, *"Ignore indents when centering custom
  separators"*, and a custom separator font. Scrivener's separators are a
  property of the **layout** (the section type), with defaults per physical kind.
- **Styles** — the page that explains the prefix-tab family: every style in the
  format (Attribution, Block Quote, Body, Caption, Centered Text, Code Block,
  Heading 1–3, Quote, Subtitle) carries its own switches: *"Delete text of this
  style"*, **"Flatten first indent"**, "Single paragraph only", **"Flatten next
  indent"**, "Match right indent to left", title prefix/suffix, paragraph
  prefix/suffix, an override name — plus a sample-paragraph preview ("Sample
  text so that you can see what your paragraph style looks like…"). The
  recovered `DoNotFlatten`/`FlattenAfter*` vocabulary is *this*, per style, in
  the format — an indent is flattened by a style rule at compile time.
  "Delete text of this style" is also how annotations get stripped on export.
- **Text Layout**: near-empty in this format — "Document Suffix:" and "Before
  back matter" — because the layout-specific typography lives in Section
  Layouts.
- **Document Title Links**: how a linked document's *title* is rendered in
  links, ebook tables of contents and endnote subheadings — prefix/suffix use,
  an override prefix separator, and suffix enclosing markers. Relevant to
  `[[Link]]` resolution and the 1.7 TOC, not to the compile walk.

**What Lekhak takes from this.** Section types (= existing `type:`,
chapter/scene/note); front/back matter as compile regions (`section:`); the
author's own formats as first-class templates (already true); the opener
conventions and run-in heads as template features; and — from these pages — the
flatten switches and separator text as *template/style* properties, with the
walk deciding only where a separator or break lands.

**Where Lekhak deliberately differs (revised with this evidence).** PoV/Location
are rendered by one template's substitutions (`${PoV}: ${Loc}`), not by a
prefix/suffix dialog per layout — the same seam, one file. Front/back matter are
regions the walk resolves, not separate compile groups the writer appends.

**The design law (author, 2026-10-31).** Lekhak's template is *one HTML
document with placeholders*. The writer puts whatever they want wherever they
want and styles it however they want; the export walk replaces the placeholders
it finds values for and leaves the rest alone. There are no per-injection
format dialogs — nothing that styles "the label line" or "the PoV line"
separately — because that separation is where Scrivener's bugs live. The
compile work is deliberately *behavior*, not styling. Appearance lives in the
template; the walk decides when and where, never how it looks. The author's
corollaries, each of which deleted a candidate rule:

- **Templates are per document *type*, not per document** (2026-10-31): what a
  template sets applies to every document assigned to it.
- **Inclusion** is the no-export template — a body that is empty — which
  applies downward from a folder like any other assignment.
- **Page breaks** are the existing `page_break` document setting; there is
  nothing to add.
- **Flatten switches and separator text** belong to the template's styles and
  content — Scrivener's own Styles page shows "Flatten first indent"/"Flatten
  next indent" living per style inside the format.

Two more corollaries the author added the same day, both of which deleted
something else:

- **The look is the *styles* system, shared by the editor and by templates**
  (2026-10-31). A piece is styled; one output differs from another by a
  stylesheet, an override, or a different piece — never by an exporter
  inventing formatting.
- **Content is never mandated** (2026-10-31). A title page is a page the writer
  designs — literal words or placeholders, their call — so no exporter writes
  one on its own. That killed the Phase-1 automatic DOCX title page.

A per-document `section` flag was built and then removed the same day (roadmap
2.1a): front and back matter turned out to be template decisions over ordinary
binder folders, so nothing would have read it. Recording this matters more than
the code: four of the five rules first proposed were mechanisms Lekhak already
had, and the ledger is where that stays settled.

## Placeholder vocabulary — what Scrivener calls things

From Scrivener 3's *Help > List of All Placeholders* (a third-party
transcription of it, the free-scrivener-themes compile guide, checked
2026-11-04). Scrivener's syntax is `<$tag>`; Lekhak's is `${Name}`. The
family worth borrowing is the numbering one:

| Scrivener | Meaning | Lekhak |
|---|---|---|
| `<$n>` | Arabic number in the current stream: 1, 2, 3 | `${Position}` |
| `<$w>` / `<$t>` / `<$W>` | the same number as words, lower / title / upper case: one / One / ONE | `${Position-spell}` (lowercase) |
| `<$hn>` | hierarchical number: 1.1, 1.2, 2 | `${Position-depth:N}` |
| `<$hn_levelN>`, `<$hn_0>` | start the hierarchy at a level / at zero | — (the depth states the cut instead) |
| `<$rst>`, `<$rst_X>` | restart a numbering stream | — (nothing to restart: the binder is the stream) |
| `<$n:name>`, `<$t:name>` | a named stream, e.g. figures | — (a Type face plus `${Position}` is the cheap version) |
| `<$r>` / `<$R>`, `<$l>` / `<$L>` | roman and alphabetical numbering | — |
| `<$title>`, `<$levelN_title>` | the current document's title, a title at an outline level | `${Title}`; levels unbuilt |
| `<$custom:FieldName>` | custom metadata | `${FieldName}` / `${Metadata.FieldName}` |
| `<$projecttitle>`, `<$draftname>` | project title, draft folder title | `${Book}` in print-config; the project `Title` setting |
| `<$author>`, `<$surname>`, ... | the writing machine's user name, from the system account | project metadata `Author` — deliberately not the OS account |
| `<$date>`, `<$date:HH:mm:ss>` | today's date, with a format | `${Date}` in print-config only; formats unbuilt |
| `<$wc>`, `<$cc>`, `<$doccount>` | counts, with rounded variants (`<$wc50>`) | `${WordCount}` for the document; project counts unbuilt |
| `<$p>`, `<$p-r>`, `<$pagecount>` | page numbers in heads and feet | `${Page}`/`${PagesTotal}` PROPOSED; DOCX fields today |
| `<$toc>` | ebook table of contents | `${ToC}` |
| `<$include>`, `<$img:...>` | insert a linked document / an image | `[[links]]` and image paths, resolved by the walk |

**What the comparison settles.** Scrivener numbers by *stream*: a section
layout owns a stream and `<$rst>` restarts it, which is how "1 again in part
2" is expressed there. Lekhak's binder already gives every document one
unambiguous place among its siblings, so `${Position}` is the stream's
number with nothing to configure, and `${Position-depth:N}` is the
hierarchical form. `<$w>` is why `${Position-spell}` exists at all: roman
numerals on front matter are a style, word numbers ("Chapter Two") are prose.

**Renaming across the manuscript.** Scrivener calls its project-wide find and
replace *Project Replace* — the tool a writer actually reaches for when a
character's name changes. Lekhak has the same thing under the same idea (the
project-wide replace), and a `${Uncle}` that stands in for the name is
replaced either way, once, at export.

**What it does not settle:** named streams, restart points, roman and
alphabetical forms, and a localisable spelling of the number. Those stay in
`docs/TEMPLATES.md` §3.2 as the open half.

## Open questions (evidence we do not have yet)

- The exact flatten-indent rule — which paragraphs flatten against what. The
  logged compile session's output files (DOCX/RTF for the same manuscript) answer
  it observationally and stay the next step; windowed assembly on the warm Ghidra
  session is the fallback for a specific sub-question.
- The enum's integer values (switch tables inside the two regions above).
- How section types are assigned when a binder folder carries no explicit type.
- The `.scrivx` schema points that matter to the exporter (1.3's open edges:
  snapshots, `notes.rtf`, `content.styles`, compile-format metadata).

---
