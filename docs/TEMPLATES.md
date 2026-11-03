# Templates and export defaults — design

Status: **decided 2026-10-26** (the author's calls in the templates session;
earlier drafts of this file described three systems that no longer exist — the
binder "Templates" document tree, the in-app slot editor, and the folder mark
`is_template`, all deleted). Revised **2026-10-28** for the navigation
redesign: the binder's Templates folder lists the sets that exist on disk, the
Outliner lists one set's own files, a file opens in the regular HTML editor,
one set may carry a per-format folder (§1.3), and a single file can be copied
out into a set of its own. Revised **2026-11-02**: the binder's Templates
folder is gone — the binder is the writer's own tree and nothing else, and
the sets are listed in the Outliner instead (View → *Template Sets*). A line
saying PROPOSED is a choice not yet made — say the word and it changes.

The goal in one sentence: **a project with no templates at all still exports
a readable manuscript** — titles, heading pages, page breaks, A4 — and
everything beyond that is opt-in, visible, and shareable.

The other goal, no less important: **a writer never has to read source code
or guess a filename to find their templates.** Four sets ship with Lekhak —
`default`, `short story`, `Book`, `nothing` — listed together in the Outliner,
duplicable with a right-click, their folders opening on a click, and serving
as the worked examples the documentation points at.

---

## 1. What a template is

**A template is a folder of HTML fragment files with `${...}` placeholders**
— the `peter88213/yw2html` model (the reference already recorded in ROADMAP
1.2), with one addition: DOCX handling (§3.1). Each fragment is a piece of
HTML describing how one shape of content is dressed; the placeholders are
where text and metadata are poured in at export time.

Two kinds of thing hold templates:

- **Files** contain text (fragments, config, a sample `.docx`).
- **Folders** contain files or folders (the manuscript, or a template folder).

That is the whole ontology — like a file manager — and it is what lets a
template folder drop into *any* binder folder and apply downward from there
(§2).

**The baseline still stands.** A template governs wherever one applies; the
baseline is what you get when none does. The baseline is not a floor laid
under a template — it is the *default template* plus the default pagination
(A4, a page per document). The
author's rule: *"if there is a template, we use it; if there isn't, we use
our baseline. Somebody may make a chapter template that contains the titles of
all the documents in it — a scene list of sorts. Another may want a list of
characters. So no scene text. Somebody may want no title, just the scene
text. As long as they can tell us what they want, we're good."*

So a template may legitimately produce something far sparser than the
baseline — titles and no prose, prose and no title, a character list, a scene
list, chrome with an empty body — and that is the template working, not a
bug. What makes those expressible is the value namespace (§3.2):
`${Content}`, `${Children}`, `${ToC}`, `${Title}`, `${Metadata.Key}`. A
fragment the template does **not** define falls back to the *built-in default
fragment* — which is itself just a template, not the baseline's behaviour
layered underneath.

Three axes stay independent of all this, because they are not prose:
**paper** comes from Page setup (1.15), a template that defines pagination
overrides the default, and the default is A4 with a page per document (§4).

## 1.1 Where templates live in the UI

**Templates are folders on disk, seen through two places — never a binder
container.** The old model's binder folder named `Templates` — a database
fiction pointing at nothing — is gone with the system that used it; so is
the `is_template` folder mark that replaced it, a second way of naming a
template that ended up listed nowhere. What stands now:

- **The Outliner lists every set that exists.** View → *Template Sets* shows
  them: the project's own `<project>/templates/` folders and the app
  library's, one row per set, flat: `templates/Book` is a row of its own,
  never `Book > Something`. The four shipped sets come first with
  **`default` on top** — it is what every export answers to when nothing is
  assigned — then the folders under the project's `templates/`, then the app
  library's. The binder holds only the writer's own tree: a template set is
  a folder of fragments, not a document, and a second *Templates* folder in
  the binder only ever said "where are my books?".
- **The shipped three are non-editable and duplicable.** They live in the
  code, so there is nothing on disk to edit; right-click → *Duplicate into
  project…* forks that variant's own files into the project as an editable
  folder. They exist to be read: three working examples of what the
  fragments, the config and the silence rule (§3) do, copyable and editable
  rather than documented from a distance. This is the fix for the defect
  that started the redesign: a writer must never have to read source or
  guess a filename to find the template their exports use.

  What each one demonstrates:

  | Set | What it demonstrates | Open first |
  |---|---|---|
  | `default` | nothing is required: the built-in fragments, an inert config | `print-config.txt` |
  | `short story` | one document dressed on its own, A4 stated | `documents/document.html` |
  | `Book` | a whole book by name: chapters, scenes, a title page, a dedication, a contents page | `README` |
  | `nothing` | silence: one empty body (`nothing.html`), and nothing results | `nothing.html` |
- **A project folder named after a shipped set never takes its slot.**
  `templates/default/` — or `short story`, `Book`, `nothing`, any case — is
  listed as its own row *after* the shipped three, under its folder name,
  so its files stay visible and editable while the top slot stays Lekhak's.
  Listing and naming are separate: the *name* `default` keeps meaning the
  shipped set (§2, §Discovery).
- **Selecting a set lists its files; clicking one opens it in the regular
  editor** (§1.2): the Outliner says which set, then what is in it, and
  the file opens in the same editor prose does — a template file is an
  HTML file, and there is nothing between the two.
- Right-click on a set: **Open folder** (the real folder, in the file
  manager), **Duplicate into project…**. Right-click on one of its files:
  **Copy out as its own template…** (§1.2).

**The files on disk are the truth.** Every one of these is a window onto
them — never a second store.

### 1.2 Navigating one set: the Outliner lists, the editor edits

A set's own listing is the **Outliner**, drawn from the folder itself — the
raw tree of what is on disk:

- **Folders are folders, files are files.** A subfolder appears where it
  sits with its files inside it — `documents/`, `examples/`, and the format
  folders of §1.3 included. Nothing in a folder is hidden: a stray
  `notes.html` or a dropped-in image is a row of its own, because a file the
  writer cannot see is a file they must go to a file manager for.
- **Clicking a file opens it in the editor** — `.html` as HTML, `.txt` as
  plain text, exactly like a document. The file loads as it stands (a full
  Qt document saved by the editor loads the same way — the compile takes
  the body out of it, §3.2) and Save — along with the debounce and every
  switch away — writes it back to its own path. A file the compile never
  reads (a stray config) says so rather than opening
  something the editor would rewrite on save; a file of a shipped set says
  so too, since a set in code has no folder to write to, and offers
  Duplicate instead.
- **Clicking a folder changes nothing** — a folder has no file of its own,
  so the selection and the editor stay where they were.
- **Picking a set in the binder keeps the writer where the files are** —
  the Outliner shows the set, no tab hunt. Picking a manuscript document
  takes the template state away with it, saving the open template file on
  the way.

**The sidebar panel.** While a template file is open, its sidebar is the
template panel — the document metadata steps aside for it, and comes back
the moment a document is picked. The panel holds, in order:

- **A big *Template* checkbox.** Everything in `templates/` is a template
  until you say otherwise; unticking says otherwise. It writes
  `Template: no` into the file's head, the metadata comes back, and the
  file leaves the set's listing — Outliner included — while staying
  exactly where it is on disk. It is never deleted and never edited
  beyond that one line: tick the box again and the file is a template
  again. (The flag is only honoured because the head is where all the
  file's own settings live — see the note below.)
- **The print/docx config box**, with the note above it, verbatim:
  *If your books are formatted similarly across mediums, you don't need
  separate print templates. You can simply add any minor formatting here
  (at a minimum add your page size). Whatever is in this box is saved
  back to the head of the file, where the export reads it. If you edit it
  and need to refer to the original tags, you can look at the default
  file.* The box **is** the file's head (§3.1) — comment lines and
  `key: value` lines — pulled out of the file when it opens and saved
  back to the top of the file where it belongs, so the box and the file
  can never disagree. *Look at the default file* opens this set's
  `print-config.txt` beside it.
- **The preset rows**, *Page size* and *Margins*, above that box — the
  two lines a print file must not get wrong, picked rather than typed.
  **Page size** offers the Amazon trims a text novel prints on (5 × 8,
  5.06 × 7.81, 5.25 × 8, 5.5 × 8.5, 6 × 9 — the large sizes are left
  out; those need typesetting, which is not this app's job) and *Custom
  size…*, which reveals two boxes for your own measurement; either one
  writes `Page size (inches - horizontal, vertical): W,H` — the same
  line Page setup speaks, in the numbers `trim_id_for` already knows.
  **Margins** offers KDP's inside (gutter) margin by page count (24–150
  pages → 0.375 in … 701–828 → 0.875 in) and writes it with 0.25 in
  top, outside and bottom — the whole table KDP prints a novel against,
  with no bleed variant because Lekhak sets no bleed — plus *Custom
  margins…*, four boxes (inside, top, outside, bottom) writing the same
  four-number line, carrying whatever the head already says. A
  **Mirror margins** checkbox sits above those boxes: ticked it writes
  `Mirror margins: yes`, unticked `no` — this file's own say, which the
  export's geometry follows (§3.1) — and the two edge boxes name
  themselves to match, *Inside (spine)* / *Outside* while ticked and
  *Left* / *Right* while not: the same two slots either way, because
  the config reads position, not vocabulary. Neither row
  owns state: a pick rewrites its one line in the config box below, the
  box holds the head, and the file saves as any keystroke would; opening
  a file simply selects what its head already says — a line that is a
  band's whole KDP row selects the band, anything else falls to its
  custom boxes. And while a row's custom boxes are being filled, that
  row's selector stands down — grey — until focus leaves them, so a
  preset one keypress away cannot throw away what is being typed.
- **A CSS text area**, which reads and writes the set's own `style.css`
  (§3.2) — the stylesheet HTML and EPUB exports carry. Empty means no
  stylesheet, and an untouched box writes no file at all.
- **The naming reminder**: *Remember to name this file according to your
  folder or document TYPE in the manuscript metadata?* — the same
  sentence this documentation and the template pages use, because a
  file's name is its job: `folder.html` dresses folders, `document.html` dresses documents
  scenes — the export's own names line up with the Types the manuscript
  metadata shows — and any other name dresses whatever the writer asks
  for by that name (§3). The inspector's **Document template** box
  carries those same filenames, editable: a name can be typed before
  its file exists, and until a file answers it the export falls back to
  the set's own slot for that document's shape.

**Copy out, one file at a time.** *Copy out as its own template…* writes
just that one file into a new folder in `templates/` — a sibling of the set
it came from, never inside it — at the path it had, so a file taken from
`epub/` still refines EPUB exports of the new set. Everything the file does
not say falls back to Lekhak's built-ins, the same rule that lets a set
carry a single template and still export every format (§1.3). A taken name
or a missing file refuses cleanly, leaving no half-made folder.

### 1.3 One set, several formats

A set may hold a **subfolder named for an export format** — `html`, `epub`,
`docx`, `pdf`, `text` — carrying the same layout as the set's root:
fragments, the `documents/` shelf, a print config, a sample `.docx`. When
that format is exported, the folder's copy of a file shadows the root's;
whatever the folder does **not** carry still comes from the root, and then
from the built-ins. The folder refines, it never replaces.

```
templates/Book/
├── folder.html             ← every format that has no folder.html of its own
├── print-config.txt        ← … and every format with no config of its own
├── documents/news.html
├── epub/folder.html        ← the EPUB's folder, and only ever the EPUB's
├── pdf/print-config.txt    ← margins, ToC, blank pages for the printed file
├── pdf/formatting.docx     ← its own sample, if the print look needs one
└── docx/print-config.txt   ← … and a different one for DOCX itself
```

Why the split lands where it does: **DOCX and PDF never read fragments** —
they read the print config and the sample `.docx` (§3.1) — so a printed book
and an ebook differ by `docx/`/`pdf/` config on one side and `epub/`
fragments on the other. That is the case this layout exists for: mirrored
margins, inserted blank pages and a page-numbered contents page belong in a
printed file and an EPUB has no use for any of them, while both keep one
continuity of formatting because they are one folder.

- **Each export asks its own format at entry**: `export_epub` → `epub/`,
  `export_docx` → `docx/`, text → `text/`, HTML → `html/`. A PDF is this
  same DOCX compile converted by LibreOffice, and it asks for `pdf/` — only
  the converter in between is indifferent to which template it got.
- **A set named in a folder's Book template refines itself too**, so an
  assignment never silently drops the format folder; §2's first rule is
  unchanged.
- **`${Format}` says the format being written** — `pdf` in a PDF's running
  head, `docx` in a DOCX's.
- **An empty format folder changes nothing.** It exists for the writer to
  fill, and until then every answer is the root's: a format folder is never
  a requirement, which is why a set with one template still exports
  everything.
- **Markdown is deliberately not one of them.** That export carries no
  template chrome at all (2.3), so a `markdown/` folder would promise
  something no file can deliver.
- A file inside a format folder is listed inside it — the root's print
  config and a format's own config are both files, each editable as the
  file it is.

### Discovery, exactly

Discovery is **bounded on purpose** — the author's constraint: *"Nobody is
going to go on an infinite document hunt each time a view is refreshed."*

1. `<project>/templates/` — listed automatically, one level of subfolders:
   each subfolder is a template. This is the project's templates root.
2. The **app library** (`$XDG_CONFIG_HOME/lekhak/templates`, else
   `~/.config/lekhak/templates`) — the writer's own sets, shared between
   projects, listed the same way.
3. The three **shipped sets** — `default`, `short story`, `no export` —
   always present at the top, from the code, not from disk (§1.1).

That is the whole of it: no walk outside these roots, no scan of the
binder, no hunt for familiar filenames, and no mark to set. A folder
outside `templates/` is an ordinary folder; making it a template is moving
or copying it there — or pressing **Duplicate**, which puts it there for
you.

**The name is a different question from the listing.** Names are what an
assignment stores (`book_settings["template"]`, §2), so a shipped set keeps
its own: a project folder called `default` is listed as itself but never
answers to that name, while a folder called `Default` — a different name —
is listed *and* answerable. The export dialog's picker is the exception,
because it picks the set itself rather than a name: there the folder is a
row like any other.

## 2. Which template an export uses

**Explicit metadata always wins.** `doc_template` on a document, or a
template name in `book_settings["template"]` on a folder, is the answer for
that item — no other rule is consulted.

**Otherwise: every applicable template is presented, and the writer picks.**
There is no precedence lattice. Templates apply from where they sit (a
template in folder F applies to F and everything under it), and when
more than one applies to the export's scope, the export dialog's chooser
lists **all applicable templates first** (applicability ordered nearest
first), **pre-selects the most applicable**, then the rest of the project's
templates below it. The writer chooses.

**The pick is one-shot.** It lives in that export only: never written to
settings, never remembered next time. An override that lingered would govern
a later export the writer had forgotten about. If a look should stick, it is
assigned to the folder or document — that is the persistent mechanism, and
the chooser deliberately is not a second one.

**A choice made for you — say the word and it changes:** applicability is
computed from the export's scope selection (what the binder/outliner has
selected, gathered before the dialog opens). Empty selection means the whole
project, not an error.

**Reserved names, and only names.** Reserved: the format tags `docx`, `pdf`,
`epub`, `html` (with the aliases 2.1 already uses — `ebook`, `print`, `web`,
`text`/`txt`/`plain`), and the built-in value names. The folder name
`Templates` no longer means anything — discovery is by location alone (§1.1),
so a folder called `Templates` is an ordinary folder. A *set's* name is
reserved only against the shipped three: `default`, `short story` and
`no export` keep meaning Lekhak's own sets (§1.1, §Discovery).

The **built-in value names** are reserved in the same sense: `Title`, `Content`,
`ChapterNumber`, `PagesTotal` and the rest always resolve to the built-in value,
so a metadata key cannot shadow one — it warns instead (§3.2). Two of them are
load-bearing in a way the others are not: **the built-in default fragments are
little more than `${Title}` and `${Content}` wrapped in markup**, so if those
names could be redefined, our defaults would die with them. That is the whole
reason to protect them, and the whole extent of the protection: lowercase
spellings of the same words remain the writer's, as does every other name they
care to invent.

**Matching is opportunistic; it never invents anything.** A format that has
no override in the chosen template falls back to that template's html
fragments: `Format: pdf` with no pdf handling simply uses the same
presentation. A template is always HTML — it decides how the content is
presented — and a format only chooses which presentation applies.

**If an explicitly assigned template names something that does not exist**,
warn and fall back — a line in the export dialog ("'Penguin' has no match —
using default"), not a modal, and never an empty export.

## 2.1 The chooser, exactly

One book has to come out in different clothes: the book template for the
reading copy, an "editor submission" template for the version you send out —
both DOCX, from the same project. The export dialog carries the selector.

- **The row always shows the answer.** The selector is pre-populated with
  the pre-selected applicable template (§2), recomputed every time the
  dialog opens — never remembered, never a stored setting.
- **Applicable first, then everything else.** Templates that apply to this
  scope, nearest first; then the rest of the project's templates; the
  shipped default always reachable at the bottom as the fallback. The
  ordering *is* the explanation: nearest applies, the rest are offered.
- **Changing it is an override, and says so.** Choosing a different entry
  shows, in these terms: *"This applies to this export only. To make it
  stick, assign the template to the folder (Book template) or to the
  document."* — naming the persistent place rather than merely refusing to
  persist.
- **Existing templates only.** The selector lists what discovery found
  (§1); nothing is created or edited here. Choosing a look is not authoring
  one.
- **One-off, not preserved.** The choice lives in the dialog and is gone
  when it closes. It is not written to project settings.
- **Granularity:** the same selector is available at chapter and scene level
  in the export scope, so a single chapter can go out under a different
  template than the rest while the override is in play.

## 3. Anatomy of a template folder

**Say only what you need.** A template is not a form to fill in. If every
scene is a title and some prose, then title and prose are all you touch —
no format folders, nothing else. Reach for the rest only when something
must be presented differently, and prefer a conditional in one fragment
over a second template for a small variation:

```
$if(Format = pouchebook)$else…$endif      ← one decision, one file
if epub or html: the ordinary back matter
if docx or pdf: the print back matter, where clickable links
                are replaced by QR codes
```

The rest of the template is identical, so a whole separate DOCX template for
that one change would be duplication with a maintenance bill. Make a separate
format template when the *presentation* genuinely differs — a document size,
margins, fonts, running heads or page numbers: things HTML and EPUB cannot
express at all.

A template is a **folder of fragment files**. Everything in it is optional
except the fragments you want used; a fragment the export has nothing to fill
is simply not used.

```
templates/                       ← the project templates root (§1.1)
├── Book/                        ← a set: named on a book folder, it dresses that whole book (§2)
│   ├── folder.html              ← a fragment: how a folder is dressed
│   ├── document.html            ← how a document is dressed
│   ├── contents.html            ← a fragment: a contents page, carrying ${ToC}
│   ├── print-config.txt         ← config + notes in the head
│   ├── sample-page.docx         ← a sample the writer dropped in (§3.1)
│   ├── print.html               ← its own print behaviour rides in the header (§3.1)
│   ├── epub/                    ← §1.3: what only the EPUB takes
│   │   └── folder.html
│   └── pdf/                     ← §1.3: what only the printed file takes
│       └── print-config.txt
├── Research Article/            ← another set, beside it — never inside Book
└── short story copy/            ← a Duplicate of a shipped set, editable
```

A set named after a shipped one (`default`, `short story`, `nothing`) sits
in this root like any other — listed after the shipped three, under its
folder name, and never taking the shipped set's slot (§1.1).

**Fragments are selected by content shape, not by a fixed hierarchy.** A
template is a bag of fragments and export uses whichever ones there is
something to fill:

- a **chapter folder containing only scenes** → the chapter and scene
  fragments are used;
- the same template **dropped at book level** → the whole book is dressed
  (header, footer, contents);
- a writer of **short stories** may own only document fragments, and that is
  a complete template.

**One rule tells the files apart:** a file with an `.html` extension is a
fragment; anything else is support material (a README, a sample `.docx`, a
config). Fragment names are the writer's — the built-in names (`chapter`,
`folder`, `document`, …) are
a vocabulary, not a requirement, and a template with only `short_story.html`
dresses short stories the moment the writer tells an export to use it.

**Silence is a valid answer.** A set that carries fragments of its own and
leaves every one of them empty prints nothing: DOCX and plain text write
empty files, HTML and EPUB keep their wrappers with nothing inside them.
The shipped **`no export`** set is exactly that, and it says so with one
file — `document.html`, whose body is empty:

```html
<body></body>
```

One file is the whole statement. Silence is a property of the set, not of
each shape it might dress, so nothing enumerates the vocabulary and nothing
is emptied to say "nothing". Delete that file and the set owns no fragments
again, so every built-in fragment answers: changing one's mind costs one
deletion. A duplicate of `no export` is therefore that one file and its
notes, never a seeded folder of fragments to empty again. It is the
sparsest end of the range quoted at the top of §1 made real, proof that
"nothing" needs no special case, and a starting folder for a workflow that
only compiles inside Lekhak.

**The book is the binder.** An export publishes the binder in binder order, and
that is the whole of it: the order on screen is the order on the page. Front
matter, the manuscript and back matter are all ordinary binder items — folders
and documents — placed where they belong, and each is dressed by its own
template (or by the set's shape template when it has none of its own). There is
no composition line and no "before the content / after the content" machinery:
if a title page, a dedication, a glossary or a preview chapter belongs in the
book, it is a document in the binder, in the place it should appear. A name
that matches nothing is simply not there, and the export says nothing about it.

**Format variants in subfolders still work (§1.3).** A folder may carry
`epub/` and `docx/` children; the walk keeps the child named for the export
and drops the other variants, so pages sitting directly in the folder (a
dedication, a title page) appear in every format while a preview chapter under
`backmatter/epub/` ships only in the EPUB. `${type}` is still there for a
writer who prefers flat names (`frontmatter-epub`), but the parent-and-
subfolders shape is the one that scales — add a format by adding a folder.

Inside an included folder nothing is constrained: chapters and scenes may be
named anything and the walk goes through them in binder order.

**What is not there is simply not there.** Whatever the template names must
exist; if it does not, the piece is absent and the export says nothing about
it — no error, no warning, no half-page. That is deliberate: an export is the
book, not a validation report. The binder is where the name has to be right,
which is why the name is visible there.

**Placeholder names stay exact** (§3.2): `${Title}` is one value, and
`${title}` is the writer's own key.

- **The html fragments are authoritative**: CSS and metadata live in them,
  and every format is derived from them unless it has its own handling.

### 3.1 DOCX and print differences

What HTML cannot express — page size, margins, running heads, page numbers,
per-kind page starts — is the print side of a template. There are **three
ways to say it, in escalating order**, and the writer picks whichever is
least work for them:

**Option 1 — minor differences: the config block, in the fragment's own
header or in its own file.** A `#`-commented `key: value` block riding in
the fragment's HTML header, **stripped during export**, or as a small
separate file in the template folder (its first line is the `Docx` marker
that says what it is; the filename carries no meaning). Same syntax either
way — this is the author's example, refined:
```
Docx
# Sample configuration to present the text as docx, edit as needed.
Page size (inches - horizontal, vertical): 5,7
Margins (L,T,R,B/I,T,O,B): 0.5, 0.3, 0.3, 0.3
# Header layouts
# If mirrored
Header left: {Series}, ,{Author}
Header right (Left, Middle, Right): , ,{Book}
# If not mirrored
Header: ,{Title},{Date}
# Same with footer
New Chapter: Recto
New Scene: page-break
Font: Georgia, 11pt, 1, 1cm
```

`#` starts a comment; units are inches, or millimetres with an `mm`
suffix; margins are four values (left/top/right/bottom) or a mirrored pair
(inside/outside), and `Mirror margins: yes|no` says the mirroring outright
— the flag a pair only implies, and the last word over an implied one.
`New Chapter` / `New Scene` take the page-start
vocabulary. `Font` takes the shorthand: font, size, line-spacing, indent.

**What a line can say is a fixed vocabulary** — `Page size`, `Margins`,
`Mirror margins`, `Header` with its `left`/`right` twins and likewise
`Footer`, `New Chapter`,
`New Scene`, `Font`, and the style mappings (`Title style`, `Chapter style`,
`Scene style`, `Body style`). Placeholders in those lines take the same
`${…}` namespace as everywhere else; the single-brace spelling the example
above uses (`{Series}`, `{Book}`) is accepted as its alias, so an author's
own line needs no rewriting. The panel's preset rows (§1.2) write the
`Page size`, `Margins` and `Mirror margins` lines from choices rather
than measurements — they only rewrite their own line in the config box,
which still holds the whole head below them. A line naming nothing in it
is inert rather than an error: nothing is hidden, and a shipped template's
config file documents every key in its own comments. Three rules fall out
of "each config line overrides it":

- `New Chapter` / `New Scene` set the default for a start **nobody chose** —
  a document's own page start, or an ancestor folder's, still wins.
- A style mapping that names no style the export has (a sample without
  `Book Title`) applies no style; the built-in one is the fallback.
- An all-empty header/footer line says nothing, so the sample's own head
  stands rather than being blanked by an empty answer.

**The sidebar's config box is exactly this block.** It is the head of the
file, not a copy of it (§1.2): opening a template file peels the head off
into the box and hands the fragment to the editor, and Save writes the two
back to their own places — head first, fragment under it. A head typed
without its `#` opener is given one on the way out, so what the box saves
is always a block the next read recognises. The `Template` flag (§1.2)
lives in the same head, which is why unticking the checkbox and the config
box can never fight over one file.

**Option 2 — a separate template.** When the print version varies
significantly, give it a completely separate template and assign it. That
is what the chooser (§2.1) and the explicit assignment are for.

**Option 3 — a sample `.docx` dropped in.** A formatting file the writer
already has — the vendor's own formatting sample — dropped into the template
folder. Its **text is ignored**; only its formatting is used: page setup,
styles, headers and footers become the export's. The author's words: *"this
last makes a writer's life very easy, because they can simply download an
Amazon formatting sample file and drop it here to instantly have a
compatible export."* Style mappings are the one thing that cannot be
inferred, so the config block names them explicitly (`Title style: Book
Title`), and **each config line overrides what the sample supplies** — delete
the line and the sample's version stands. A `.docx` and a `.odt` are the
same offer in two containers; LibreOffice converts between them and we
already drive LibreOffice for PDF (1.6).

Our end of that offer, precisely: the sample is opened and its body is
discarded in the same breath — every section but the last goes with it — so
what remains to write into is its page setup, its styles and its running
heads. Its own headers and footers stand until a config line names that part.
A file that will not open (a `.odt`, or anything mislabelled) is not a
template: the export still happens, on the fallback below. The first sample
in the folder is the one.

**An example to try ships with the shipped sets.** Duplicating `default` or
`short story` writes `examples/formatting.docx` — A4, this baseline's own
margins, fake prose demonstrating titles, chapter headings and scene
headings. It sits in a subfolder on purpose: discovery reads a folder's own
files only, so an example cannot silently become every project's paper.
Move it up a level and it is the export's paper — the lesson, bought for
the price of a drag.

**Precedence, one rule:** the sample file supplies everything; each config
line overrides it; the template's html fragments supply the structure. When
something cannot be set in the writer's editor — recto starts are a classic
(Google Docs, we are looking at you) — it goes in the config, and nothing is
hidden: the config says only what it says.

**Third-party templates come along for free.** Because we lift only what we
understand and ignore the rest, a downloaded "free book template" — or a
vendor's KDP formatting template for a particular trim — can be dropped in
as the sample: we take its page size, margins and named styles, ignore its
sample prose and its irrelevant styles, and the writer tweaks anything
glitchy through the config. Knowing how to format a book stops being a
prerequisite for having one formatted.

**Missing print handling ⇒ the html fragments are adapted**, as today:
html/text render them; DOCX/PDF take structure from the fragments and their
typography from page setup (1.15) and book settings.

### 3.2 Values: what a fragment can ask for, and where the answer comes from

Fragments are strings with `${Value}` placeholders and `$if(Value)…$endif`
conditionals (2.2). Conditionals read the same namespace, so
`$if(Metadata.Status = Final)` works anywhere a `${…}` does.

**Shortcodes in prose, too (built 2026-10-22).** A document's own text
may carry `${…}` markers; they expand once, at compile time, when an
export pulls the content — the stored manuscript keeps the literal
markers and the editor shows them, exactly like `[[wiki links]]`
resolving at export rather than at rest. The vocabulary is the fragments'
own (`${Metadata.Key}` from the sidebar included, resolved against that
document — the inheritance chain below is still unbuilt); `${Content}`
never expands inside prose, and conditionals stay in fragments: prose gets
values, not control flow. Unknown keys render empty, as in a fragment.
HTML escaping applies to expanded values, as in a fragment.

| Value | Meaning |
|---|---|
| `${Title}`, `${Kind}`, `${Synopsis}`, `${WordCount}` | the unit being rendered |
| `${Content}` | the unit's prose (raw, not escaped) |
| `${Children}`, `${ToC}` | nested units / TOC entries (raw) |
| `${Position}` | the unit's place among the siblings that share its Type, counting from 1 — the chapter number a chapter fragment wants. No Type set means plain sibling order, so a homogeneous folder numbers 1, 2, 3 either way; front matter of another Type never shifts it |
| `${Position-spell}` | the same number spelled out: `one`, `two`, `twenty-one` |
| `${Position-depth:N}` | positions from the book down, cut to N numbers: a chapter `2`, its scenes `2.1`, `2.2`. The book folder itself is not a numbered level |
| `${ChapterNumber}`, `${SceneNumber}`, `${WordsTotal}` | counters; template and heading pages never take numbers (2.1) |
| `${Anything}` | built-in values first, then any metadata key — see *one namespace* below; `${Metadata.Key}` still works as the explicit spelling |
| `${Parent}` | PROPOSED: the immediate container's title |
| `${Book}` | PROPOSED: the nearest ancestor that is "the book being dressed" — a folder with a page setup or a template association; failing that, the export's title-page container |
| `${Format}` | the format being written (`docx`, `pdf`, `epub`, `html`, `text`) — the value that makes `$if(Format = docx)` possible, so a small difference never needs a second template. Resolved today in print-config values; not yet in fragments |
| `${Page}`, `${PagesTotal}` | PROPOSED: page numbers, where the format has pages (below) |

**Metadata inherits down the binder, and outward from the writer.** A value
resolves nearest-first: the document's own metadata, then each ancestor
folder upward, then the project's, then the **application preference** list
below — and the first **non-empty** value wins, so an empty field on a scene
deliberately shows the chapter's or the book's value rather than blanking it.
This is what makes `${Metadata.Author}` in a scene's page footer, or
`${Metadata.Status}` on a single scene, work without repeating anything:
folder metadata is metadata for everything inside the folder.

**Built 2026-11-04:** the document's own metadata and the project's, a
non-empty document value winning and an empty one inheriting. Ancestor
folders and the preference list are still open (ROADMAP 1.20).

**What is special: exactly two things.** `${…}` is a value and `$if(…)` /
`$else` / `$endif` are conditionals. Everything else is literal text, and this
is load-bearing rather than incidental: html fragments are full of CSS, and
`p { font-size: 12pt; }` passes through the parser untouched because a bare
brace means nothing. No `{{ }}`, no `{% %}` — if a template arrived from Jinja,
Handlebars or Mustache it will not work, and it should not look like it might.

The only sequence a writer cannot type literally is `${` itself. Give it an
escape — `$$` is a literal `$`, so `$${Name}` prints as `${Name}` — because
"how do I write it without the app eating it" is a question that arrives the
first week, not the first year.

**One namespace (author's direction, 2026-10-21).** There is no `Metadata.`
tier to remember. A placeholder is matched against the built-in values first
and the resolved metadata second, so `${Author}`, `${Status}` and `${PoV}` work
exactly like `${Title}` — and `${Metadata.Author}` keeps working, because it is
what existing templates already say. Conditionals read the same namespace, so
`$if(Status = Final)` replaces `$if(Metadata.Status = Final)`.

Implementation is a *flattening*, not new syntax: each resolved metadata entry
is published under both its own name and its `Metadata.` alias, so the parser
never learns a second rule.

**Names are exact, and may contain spaces** — the author's call: case
insensitivity anywhere is bloat, saved by not having it. A name is trimmed at
its ends and then matched as written, which is what the code already does
(`PLACEHOLDER_RE` accepts spaces, and the lookup strips the ends), so the only
engine change is publishing resolved metadata under its bare name as well as
its `Metadata.` alias.

```
${Author} wrote ${Book title} as a part of the ${Series} series.
```

Case therefore matters: `${author}` is a different name from `${Author}`, and
as a typo it resolves to nothing. The safety net is validation at the point
of writing — an unknown key reported while the fragment is being edited,
which is where that belongs — and an autocomplete vocabulary that offers the
project's real metadata keys, so the exact spelling is a pick rather than a
memory test. (The in-app slot editor that used to do this was removed — see
ROADMAP 2.4a — the check belongs in the new fragment editing path, not in a
dialog of its own.)

**The convention that falls out of that** (the author's example): built-in
values are CamelCase, so the lowercase spelling of any of them is free for the
writer's own use. `${Title}` is the binder title; `${title}` is *their* `title`
key — set on a document, a chapter, a book or the project, and inherited
downward like any other metadata. So a chapter named `3 - Two People Meet` can
print under a `title` the writer chose to give it, without a mechanism, a
prefix rule, or a second syntax: one namespace and exact matching already do
it.

Nothing is set by default, so an unset `${title}` resolves to nothing — and
the check says so *while the template is being written*, which is when that
is cheap to fix. The one collision worth a warning remains a
metadata key named exactly `Title`, where the built-in wins.

**The reference is in the app (built 2026-11-04).** *Document > Placeholder
Reference* — and the *Placeholders...* button in the template panel — opens a
page listing every built-in value with one line of meaning, then the metadata
keys this project actually carries, grouped by the Type carrying them
(`Project`, `Type Chapter`, `Type Scene`). It is what the writer needs at the
moment of writing a `${...}`, and it is generated from the live project, so a
key exists on the page because some document already sets it. Scrivener's
precedent is its *Help > List of All Placeholders*; ours adds the project's
own vocabulary, which is the part no general list can know.

**The names follow Scrivener's numbering family** where it has one
(`docs/SCRIVENER-BEHAVIOR.md`): `<$n>` → `${Position}`, `<$w>` (word numbers)
→ `${Position-spell}`, `<$hn>` (hierarchical) → `${Position-depth:N}`. The
tags differ in spelling because `${...}` is the syntax that already exists;
the concepts are the same ones writers have met before.

**Application-level metadata (Preferences).** A key/value list in
Preferences applies to every export of every project: set `Author = …` once
if you are always the same writer, or an imprint if you only publish ebooks.
Stored in the app's `QSettings` (`metadata/…`), **never in a project** — so
sharing a manuscript does not carry your identity with it, and a project,
folder or document that sets the same key simply overrides it. `${Author}`
is always `Author`: the key name is a convention templates may rely on. Like
every metadata value it is used at compile time only; it never reaches stored
manuscript content (§2.4's display rule).

**Two kinds of placeholder, and they fill differently (§3.2's rule).**
Scalars are text: `${Title}` is replaced inside whatever element holds it,
element kept — `<h1>${Title}</h1>` stays an `<h1>`. The three **block
placeholders** — `${Content}`, `${Children}`, `${ToC}` — are *markup*:
their value is a document's own blocks, so the element around one is a
**styling proxy**, not a paragraph. `<p>${Content}</p>` therefore has its
`<p>` replaced at fill time, and the new content wears that element's
formatting, because leaving it would nest the document's paragraphs inside
a `<p>` — invalid HTML, and a lie about the structure. Filling keeps the
writer's formatting; it does not keep an element the content contradicts.

**A fragment may be a whole document; the compile takes the body (§1.2).**
Files are the truth and any editor may write them, Lekhak's own most of
all — it saves a full Qt document, `<html><head>` with a stylesheet, a
`<body>` with a font, and a `<span>` around any run the writer touched.
None of that is the fragment: when a file is read, the body's content is
taken and **`<span>` tags are stripped outright** — they are wrappers,
never the writer's intent, and stripping them is also what makes
`<p><span>${Content}</span></p>` read as the bare block proxy it is.
Pasted browser or word-processor junk is handled by the same one rule, so
the tolerance lives in the compile and never in what is written to disk:
a fragment stays readable by any editor, and the editor's furniture never
reaches an export.

**The set's stylesheet is `style.css`, and the CSS box is that file (§1.2).**
One file at the root of the set — a format folder's own `style.css` wins for
that format alone (§1.3) — read into the sidebar's CSS text area when a
template file opens and written back from it on Save, but only when the box
has actually moved: an untouched box mints no file. HTML exports embed it as
a `<style>` after the title, ahead of the header that may use its classes;
EPUB ships it once as `OEBPS/style.css` with a `<link>` in every section and
the contents page, and a manifest item for it. A set with no stylesheet
writes no `<style>` and no link at all, so every existing export stays
byte-identical. DOCX and PDF take their typography from page setup and the
sample `.docx` (§3.1), where CSS has no say.

**Page numbers are format-specific**, because pages are: DOCX/PDF insert real
Word fields (`PAGE`, `NUMPAGES`) so Word and LibreOffice keep them correct as
the text reflows; HTML can use CSS `@page` margin counters when printed;
EPUB has no page numbers (readers define their own) and ignores `${Page}`.

## 4. The baseline

What an export looks like with no template at all — and what the shipped
default fragments encode, so a template can replace every line of it
wholesale:

| Rule | Value |
|---|---|
| Paper | **A4** (210 × 297 mm) when the book has no page setup |
| Title page | the selected container's title; the author **only** if project metadata has an `Author` |
| Author key | project metadata `Author`, and nothing else — no fallback, nothing invented. A collection of protest fliers has no author, and that is not our business |
| Containers | each container on the way to a document prints its name as its own heading page |
| Heading levels | by depth in the export: the container chain gets H1, H2, H3… in order, documents one level deeper |
| Documents | every exported document starts on a **new page**, and prints its own title (also when it is the only thing selected) |
| Chapters | plain page break, **recto** (odd page) when the book has a real print trim set (an explicit page setup, not the A4 fallback) |
| TOC | a document whose template carries `${ToC}` — placed wherever the writer puts it |
| Running heads / page numbers | DOCX/PDF: page number bottom-centre, book title top-outer once page setup exists — **new work** |
| Cover | a cover page in every format when a cover resolves (see §5); **DOCX cover is new work** |
| Numbering | none. Titles are the writer's; the baseline invents no "Chapter 1" |
| Counters | title/heading pages contribute no words and take no numbers (2.1's rule, generalised) |

A4, not Letter: A4 is the ISO 216 standard used essentially everywhere except
the USA and Canada (and common-not-standard in Mexico and the Philippines).
Letter is the outlier; A4 is the democratic choice, and for real books the
trim (5×8, 6×9, A5…) overrides it anyway. The current Letter fallback came
from python-docx's template, not from a decision.

## 5. Covers

**Discovery**, in order: `book_settings["cover"]` (explicit path, chosen in
the inspector) → `images/<folder_id>/cover.{jpg,jpeg,png,webp,gif}`.

**Emission**: cover image is page 1, unnumbered, in every format —
EPUB (already), HTML (first block), DOCX/PDF (new: a page with zeroed margins
and the image scaled to the trim width, aspect preserved). PROPOSED: the image
is inserted **as-is**, scaled to fit the trim width, never cropped or padded;
cover *preparation* (crop to exact ratio, EPUB-typical 1600×2400) is a later
convenience, not a gate. The writer decides; we do not police aspect ratios.

A cover only applies when the export resolves to a book folder; a
single-document export has none.

## 6. Workflows this has to support

**The hobbyist (zero configuration).** Write in folders, export, get A4 with
title pages, heading pages and page breaks. Nothing to learn. This is the
baseline and it is always at least this good.

**The author making a book.** Right-click the shipped default in the
binder's Templates folder → Duplicate into project…, edit the fragments in
any text editor, drop in a sample `.docx` from their vendor (§3.1), set page
setup for the trim, pick a cover. Export gets the TOC, running heads, cover
and recto chapters.

**Someone sharing a look.** Zip a template folder and send it. The recipient
drops it in their project's `templates/` folder and it is there — listed in
the binder, files in the Outliner, opened in the editor like any HTML file.
Their exports can use it. No import step, no database rows, no app-specific
container format.

**The binder's Templates folder** is the window onto all of this (§1.1): the
shipped three on top with Duplicate, every set on disk listed as its own row.
It never becomes a second, divergent store: the files on disk are the truth.

## 7. Help, in two levels

**Level 1 — the hovering question mark.** Every non-obvious control gets a
"?" whose tooltip says what the thing does *and what it costs*: the page-start
combo ("Recto: starts on a right-hand page, inserting a blank page if the
flow lands wrong — only meaningful with a print trim set"), the template
chooser, the cover row, `blog_settings`-style rows, the export scope summary.
In-app, offline, no manual needed for the common path.
This is the level that has to be *abundant*: a writer should never have to
guess what a control does in order to avoid breaking their book.

**Level 2 — the manual, with the source chosen in Preferences.** A proper
instruction document, read either:

- **Online** (default): <https://vidyut.info/downloads/lekhak/> — the live
  version, which keeps improving without an app release;
- **Offline**: the packaged copy, shipped with the app (`docs/`, readable
  with no network) — for writers who prefer to stay offline;
- and the choice is remembered in Preferences (`help/source`).

Both open from Help → *Manual*, and from the binder's Templates folder and
the export dialog's template chooser. The manual opens with the honest caveat: *Lekhak is
new; the manual is incomplete and still being written — the online copy is
the more current of the two.* The user decides what to do with that.

**Packaging requirement:** the offline manual must actually ship —
`pyproject.toml` needs the docs in the wheel/sdist (or `scripts/install`
copies them), or "offline" silently means "not there". Phase 6 covers it.

## 8. Updates

The default is **take the fixes**: you already installed Lekhak, so when the
author pushes a repair, you get it. The one thing that changes that behaviour
is a breaking change, which is rare and never silent.

| Preference `update/mode` | Behaviour |
|---|---|
| `auto` (**default**) | check at most once a day on start; if newer, apply it (git checkouts), and say so in the update log |
| `ask` | check, then show what changed and apply only on a click |
| `never` | no network calls at all |

- **Check**: one GET to the public GitHub API for the newest commit on
  `main`, compared with `__version__` and the installed commit. Offline is
  silent; being current is silent.
- **Apply, git checkout**: `git pull --ff-only`, and only when the working
  tree is clean — the writer's own edits are never stashed, merged or
  overwritten; a dirty tree means we report and stop. The commits being taken
  are listed by subject and date, and the log records what was taken.
  **We promise no way back.** An earlier commit can be checked out if it ever
  matters, but a rollback restores code only: template files you have edited,
  settings, and anything a project has already written into a newer shape are
  not restored by it. The log exists so the situation is knowable, not so it
  is undoable.
- **Apply, pipx/pip/zip**: we do not rewrite an install behind the writer's
  back. The update becomes one click that runs and *shows* the exact command
  (`pipx upgrade lekhak`, …) and its output in the log.
- **Breaking changes**: if the incoming range is flagged breaking (or the
  version's major part moves), `auto` behaves like `ask`: the breaking items
  are shown first, with a link to the notes.
- **Nothing happens without a trace.** Every check and apply is written to
  Help → *Update log*: timestamp, URL or command, and result. As this is
  written, the app makes two kinds of outbound request — the LLM endpoint the
  writer configured (2.4) and this update check — and the manual says so in
  one sentence. Restarting is what picks up a new version, and the log says
  that too.

The honest caveat from §7 applies here as well: this is a fresh project, so
"newer" means "the author has been fixing things", not a supported release
channel.

## 9. Implementation order

1. **Baseline behaviour** (§4): A4 fallback, title page (+author), heading
   pages by depth, new page per document, recto-with-trim, DOCX TOC placement.
   Touches `_compile_scope` (breaks + title-page list), the four exporters,
   the inspector's page-start combo (add `page`, rename `auto`), tests.
   *Shipped 2026-10-21* — but note what it means for later: the DOCX title
   page, heading levels and breaks are still **code**. Once templates land
   they have to be the output of the built-in default fragments, so a template
   can replace them wholesale (§1) — a scene-list chapter, a character list, a
   document with no title at all. Until then the baseline is hard-coded and a
   template cannot change those three things.
2. **Delete the binder-document template system** — **done 2026-10-26**
   (`1b4005d`): nothing in it survived the decided model, and no export
   depended on it (the author's project was checked: zero books assign a
   template folder, and its template pages have no content).
   `_chrome_books`, `_template_pages`, `_variant_folder`,
   `_assemble_with_book_chrome`, `templates_roots()`,
   `ensure_templates_area()`, `set_templates_root()`,
   `Node.templates_root`, `book_settings["template"]` as a folder id,
   `book_template_box` reading binder folders, the `Templates` binder
   folder, and the `is_templates_root` layout column. The column's
   *meaning* moved down a level rather than vanishing: a migration marks
   each old area's direct children as templates (an area nested in an
   area becomes a template of the outer one, keeping its own), zeroes
   the area flags, and drops the old column — so `template_folders()`
   is the same name returning marked folders, and EPUB cover discovery
   rehomes onto `_cover_folders()` (folders picked directly plus folders
   holding exported documents, §5). *(That mark lived one day of design:
   item 12 deleted it, and the migration now drops both columns.)*
3. **The Templates view** (§1.1) — **done 2026-10-26** (`129102d`), **superseded
   by item 9 below**: it was a fifth tab that both listed the sets and edited
   them, and a projection of folders that exist — re-listing from disk on
   every show, so there was no sync to keep and nothing to drift. Shipped
   default first and bold, non-editable because there is nothing on disk to
   edit, right-click → Duplicate into project…. The parts that did not
   survive: a project's own `templates/default/` taking the shipped set's
   slot (item 11), and the folder *mark* — "folder in the binder" rows, the
   binder's "Mark as template" (item 12). What survived is the tab itself,
   now an editor rather than a second listing (the author: *"we already have
   the outliner for listing"*).
4. **Discovery** (§Discovery) — **done 2026-10-26** (`129102d`), **revised by
   items 10–12 below**: project-root `templates/` automatic, one level of
   subfolders; no unbounded scan, and the refresh still costs one directory
   listing per root. The explicit-mark source is gone (item 12), and so is
   the rule that a directory named `default` *replaced* the shipped entry —
   it is now listed beside it as a set of its own (item 11).
5. **Applicability and the chooser** (§2, §2.1) — **done 2026-10-27**:
   - `Project.template_choices(scope)` orders the chooser's list —
     applicable first (every folder in the export's scope naming a template
     in `book_settings`, deepest first), then the rest of the project's
     templates with the default always reachable — and returns the
     pre-selected most applicable plus the warnings for names that match
     nothing ("has no match — using default", a line in the dialog, never a
     modal, never an empty export).
   - `Project.resolve_template` and `_template_resolver` enforce the first
     rule at compile time: a folder naming a template dresses its own
     subtree inside *any* export, nearest assignment wins, everything
     unassigned keeps the export's choice. The resolver caches a chain only
     as far as the folder that decided, so one branch's assignment cannot
     answer for a sibling.
   - The dialog (§2.1) is recomputed every time it opens, shows the answer
     pre-selected, and says what an override is in the specified words —
     naming "Book template" as the persistent place.
   - **One-shot is enforced, not merely promised**: the `template_set`
     setting is retired (deleted on open, dropped from `write_tree` and
     from the round trip's carried settings), and Print — which used to
     borrow whichever set the last export happened to leave behind — now
     answers with the most applicable template for its scope.
   - **Boundary, closed by item 12**: the chooser offers sets discovered
     *on disk*; the binder folder marked as a template that used to be
     listed but not offered is gone with the mark itself, so there is one
     concept left — a folder of files under `templates/` (STORAGE.md) —
     and the chooser and the listing name the same things.
6. **Fragment selection by content shape** (§3) — **done 2026-10-27**
   (`7c1e977`): one rule tells the files apart — every `.html`/`.txt` file
   in a template folder is a fragment. The built-in names (`folder`,
   `document`) are the vocabulary the
   compile consults by shape (chapter folder of scenes → chapter + scene;
   book level → header/footer/toc), not a requirement on the folder. Any
   other name dresses a document the moment the writer picks it in the
   sidebar: `document_slot` resolves `documents/<name>` shelf → root
   fragment → built-in `no-title`, so a set of document fragments alone is a
   complete template; chrome slots are never offered as a document's dress.
   A file that is neither `.html` nor `.txt` (README, sample `.docx`) is
   support material and ignored. Also fixed here: a text export used to
   strip a fragment's own characters (the assembled body was re-run
   through `plain_text`); all three prose passes are now gated behind the
   document's own text.
7. **DOCX handling** (§3.1) — **done 2026-10-28**:
   - The block parses and is **stripped**: a fragment's leading
     `#`-commented block is removed at load and merged into the set's
     config, a file whose first line is the `Docx` marker is *only*
     config (never a fragment), and its keys correct the fragment ones.
     The syntax is the author's example, parentheticals and all —
     `Page size (inches - horizontal, vertical): 5,7` — and the value
     vocabulary is the fixed list §3.1 now spells out; unknown keys are
     inert rather than fatal.
   - The sample-`.docx` route: opened, its body and every section but the
     last dropped, so page setup, styles and running heads are what the
     export writes into; a file that will not open is not a template and
     the export still happens.
   - **Precedence enforced**: sample → every config line on top → page
     setup (1.15) only where the template said nothing, else A4 — each
     layer through `clean_page_setup`, so junk is dropped as a whole
     instead of reaching the page. `New Chapter`/`New Scene` are the
     default for a start nobody chose; `Font` and the style mappings land
     on the body and the headings; `Header`/`Footer` lines become real
     running heads (three pieces on tab stops, `left` switching Word's
     odd/even headers on) — PDF comes along free, because it still routes
     through `export_docx`. Tests: `tests/test_docx_config.py` (21).
8. **Three shipped template sets** (§1.1, §3, §3.1) — **done 2026-10-28**:
   `default`, `short story` and `no export` ship in the code and are seeded
   by discovery, so they need no folder: listed under the top slot, bold
   only where it is the default, non-editable (nothing on disk to edit) and
   **Duplicate**-able, each forking *its own* files into the project —
   one file map per set (`SHIPPED_FILES`) serves as both the shipped entry
   and what a duplicate writes, so a writer who copies `short story` and
   opens the folder finds that variant's fragments and its `Page size: A4`
   line, not a surprise. `default` keeps the built-in fragments byte for
   byte with an all-comment config (inert, so page setup still answers) and
   carries its example sample under `examples/` — §3.1's option 3
   demonstrated without silently becoming any project's paper;
   `short story` dresses units as `<article class="story">` with no scene
   divider and one live config line; `no export` is one file whose body is
   empty (`<body></body>`), which made `TemplateSet.prints_nothing` a rule
   rather than an observation (§3): DOCX writes an empty document, text an
   empty file, HTML and EPUB keep their wrappers. A set directory named
   after any of the three is listed *after* them as a set of its own
   (item 11) — the shipped slot is beyond a folder's reach.
   Also fixed here, because duplicating is how writers *read* these sets:
   a directory copy is now recursive (the `documents/` shelf and
   `examples/` used to be lost), and `copy_template_set` on a shipped
   source writes its config, its notes and that example sample.
9. **Navigation redesign: the binder lists, the Outliner lists one set, the
   tab edits** (§1.1, §1.2) — **done 2026-10-28**, **superseded by item 13
   below** (the tab is deleted; a file opens in the regular editor): the
   binder's `Templates`
   folder is the project's `templates/` directory (a synthetic row when the
   project has none), one flat row per set with the three shipped first,
   `default` on top; picking a set opens its files in the Outliner as the
   raw folder tree and switches to the Templates tab, and picking a
   manuscript document takes that state away. The tab lost its listing —
   *"we already have the outliner for listing"* — and became an editor:
   placeholder when nothing is picked, sheet when a set is. Right-click on
   a set: Edit template, Open folder, Duplicate into project…; on a file:
   Copy out as its own template… (item 11).
10. **Per-format subfolders** (§1.3) — **done 2026-10-28**, **the sheet
    half superseded by item 13**: `html`, `epub`,
    `docx`, `pdf`, `text` subfolders refine a set's root; each export asks
    its own format at entry and falls back to the root, then the built-ins,
    so a set with one template still exports everything. A PDF asks `pdf/`
    of the same DOCX compile LibreOffice converts; markdown stays
    template-free. A format folder's files are listed inside it, and the
    print config inside one is the file, not the root's field groups.
11. **Copy out, one file at a time** (§1.2) — **done 2026-10-28**: one file
    becomes one new set in `templates/` — its path kept, so a file taken
    from `epub/` still refines EPUB — with everything else falling back to
    built-ins. Duplicating a whole folder stays as item 8 left it.
12. **The `default` slot, and the folder mark deleted** (§1.1, §Discovery) —
    **done 2026-10-28**: `discover_template_sets` seeds only the three
    shipped sets and lists folders after them, so `templates/default/` is a
    row of its own under its folder name; `named_template_sets` keeps the
    *name* `default` meaning the shipped set, while the export dialog's
    picker (which carries the set itself, not a name) can still pick the
    folder. The mark `is_template` — the second concept of "template" — is
    gone end to end: the column (dropped by the migration, with
    `is_templates_root`), `set_template`, `template_folders`, the export
    exclusion, the italic cues, the inspector checkbox and the binder's
    mark actions. Word counts and whole-project export now simply include
    everything in the binder; a template cannot be there at all, because a
    template is a folder of files under `templates/`.
13. **The tab is gone; a fragment is a file (§1.1, §1.2)** — **done
    2026-10-28**, superseding the editing half of items 9–10: the Templates
    tab, its placeholder, its sheet and the row/area mapping are deleted
    ("This looks harder than a text editor"). A file of a set opens in the
    regular editor — `.html` as HTML, `.txt` as plain text — and Save (and
    the debounce, and every switch) writes it back to its own path; the
    compile strips the editor's furniture instead of the write side
    tidying it (§3.2): the body out of a full Qt document, and `<span>`
    tags stripped outright. Right-click on a set lost *Edit template* —
    selecting the set is what lists its files — and a file of a shipped
    set says Duplicate rather than opening something unwritable. What
    survives of the sheet is in git (`24899b2`), and the metadata
    replacement the author has in mind is item 17's.
14. **Covers in every format** (§5), including the DOCX cover page.
15. **Help — level 1** (§7): hovering "?" on every non-obvious control.
16. **Help — level 2** (§7): the manual, its online/offline choice in
    Preferences, packaged docs, Help menu and the caveat line.
17. **Update check** (§8): the read-only check, the Preferences toggle, and
    the one-click pull for git checkouts.
18. **Preferences metadata** (§3.2): the app-level key/value list that applies
    to every export of every project.
19. Reserved: cover preparation; one-click updating for non-git installs.

---

### The trim lives in the template — and only there (author's direction, 2026-10-21)

Page size and margins are a **template** concern and nothing else. A dropped-in
KDP template or a downloaded free book template already carries its trim, so
"produce a print-ready book" is "drop in the template and export". There is no
per-book paper setting to hunt for and no cascade of fallbacks to reason about
— the author's warning, verbatim: *"Or you'll end up with too much conditional
stuff."*

- **With a template:** its page size and margins apply — the config block's
  `Page size` and `Margins` lines, or the sample `.docx`'s own page setup
  (§3.1).
- **Without one:** the A4 baseline (§4).

That is the whole rule. A book that needs different paper uses a different
template.

**One source, not a cascade.** This is the third time this design has shed a
layer: per-document, then per-folder-with-inheritance, then template *plus*
per-book override, and now template alone. Each round removed a rule the writer
would have had to know. Where a choice can live in one place, that is where it
goes.

**Safeguards are deliberate, not accidental.** Word will happily let a document
carry several page styles, so mixed paper inside one file is *possible*; if
somebody builds that on purpose, that is their headache — the author's words,
and the right call. What the app owes is that nobody arrives there by accident:
hence the folder-only guard for the low-level override (1.15's mechanism, kept
but no longer a route we advertise, `docs/UI-NOTES.md` 7) and the export
warning when a selection spans books with different trims.

**And the boundary of our responsibility is the picker.** Offering paper
selection per document or per chapter would be *our* mistake; that is the
accident being prevented. Beyond it, the template belongs to the writer: if
they build differently sized pages into their book template to get a mixed-size
file, then they are a determined person using their own tools to do what they
want, and who are we to stand in the way.

## 10. Open, small

- Text export pagination: **settled 2026-10-22 — no form feeds.** Text
  and markdown stay structural (titles and headings as lines, units
  separated by blank lines); pagination belongs to the page-setup
  formats (PDF/DOCX, page setup / PRINT.md). EPUB needed no decision:
  one XHTML file per compiled unit already is pages.
- A container that has prose of its own prints that prose on its heading page
  rather than dropping it — PROPOSED (nothing is ever silently discarded).
- Running heads: only once page setup exists, or always? PROPOSED: only with
  page setup (a "book"), so a stray export of notes does not sprout headers.
- Update check frequency: PROPOSED daily, silently skipping when offline or
  already current.
