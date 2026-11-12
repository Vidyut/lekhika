# Interface fixes, queued

Owner: the interface side (`docs/WORK-SPLIT.md`). Reported by the author, recorded so nothing gets lost between sessions.

## 1. Outliner: show only the selected folder's children — DONE (ab21345)

The corkboard already does this and is right; the outliner still lists the whole project, so even after finding the right folder the writer is scrolling through everything. Selecting a folder should scope the outliner to that folder's children, exactly as it scopes the corkboard.

## 2. Editor and Page are not available for a folder — DONE (c0ac213)

A folder is a container. If the Editor tab is there when a folder is selected, sooner or later somebody types into it — and prose on a container is a formatting headache that only shows up much later, in an export. **Hide both tabs for folders** rather than disabling them: Corkboard and Outliner *are* the folder views, and removing the wrong ones is clearer than graying them out.

## 3. Corkboard cards should read as cards

Today they are plain text with line breaks. They should look like cards — padded, framed, a title line and a synopsis block — but **without relying on theme colours**: invert the contrast against the surrounding area (a light card on dark chrome, or the reverse) so a card can never vanish because a desktop theme we do not control renders our colours wrongly. The same trick is worth using anywhere that must stand out from its background.

## 4. Preferences needs rebuilding, not tidying — DONE

As it stands it is a wall: paragraphs of explanation between form rows, labels inline with prose, subjects interleaved with no structure. The author's direction: **it needs a lot of work — use tabs or accordions, and arrange the settings by subject.** Concretely:

- **Split by subject** — Appearance (interface font), Editor, Links and export defaults, Pasting, AI, Read aloud, Updates, Metadata (the app-level key/value list, `docs/TEMPLATES.md` §3.1).
- **Tabs for the top level, accordions inside a tab** where a subject has more than about six controls. Every subject visible at a glance beats a scroll, and nothing should be more than one click from the top. *(Built as a sidebar list + stacked pages instead of tabs — the same one-click access, and it keeps the filter box and scales past eight subjects.)*
- **One line per control**, in its own column, with the label doing the work.
- **The long explanation lives behind a "?"** — level-1 help, owed to the writer anyway (`docs/TEMPLATES.md` §7). A settings dialog is not the place for three-sentence prose about paste policy. *(The AI and Pasting pages were the last two carrying inline prose; both now fold behind "?")*
- **Defaults shown, not implied**: say what will happen if the writer touches nothing (as the AI section partly does with "empty for local servers").

The author's own sketch of the shape:

- **Interface** — font size, and a **slider** is probably the right control (with a live sample), rather than a spin box.
- **Defaults for new work** — author, default template, default metadata.
- **AI** — behind an accordion; endpoint, model, key, the small-model warning (`docs/AI.md`).
- **Pasting**, **Read aloud**, **Updates** — their own sections or accordions.
- **Metadata** — the app-level key/value list (`docs/TEMPLATES.md` §3.1).

**The distinction to keep straight: Preferences holds app-wide settings and *defaults*; anything per-project stays with the project.** An author set in Preferences is the value a new project starts from, and a fallback for projects that say nothing — but the project's own Author, its metadata and its template association live in the project (the inspector already holds them), because they travel with the manuscript and must survive being shared. Put per-project settings in Preferences and the same dialog would mean different things depending on what is open, which is how settings screens get hated.

## 5. "Section target" should say what it is — DONE (fcd53d5)

The stored field is already `word_target`; only the label hides it. Call it **Word count goal**, with the zero state reading **No goal** (not "No target"), and give it the "?" hover the rest of the inspector is owed: *"A word-count goal for this document. Progress shows on the corkboard card and in the status bar. On a folder it needs the children's words added up to mean anything."*

The last sentence is also the small gap found today: progress uses the document's own count, so a goal set on a folder reads 0% over a subtree. Either make folders use the rollup (it exists) or say plainly that goals are for scenes and chapters.

## 6. Where does the page size live? — say it where it matters

The **Page setup** control sits on the book folder in the inspector, which is the right home and a terrible secret: a writer with a scene open sees no way to change the paper, and nothing on the Page tab says where the setting lives. The author's verdict: *"I don't see a clear route for the user to understand what to do."* Four small fixes:

- **A "?" on the Page setup button**: what it does, that the nearest ancestor folder wins, and that clearing it returns to the built-in default.
- **The Page tab names its fallback as a fallback** — *"A4 (default — set Page setup on the book folder)"* — so an unconfigured preview is honest about being undecided rather than looking like a choice the writer made.
- **Two clearer tab labels** — *Editor* and *Page preview* — because the author of the software had to ask what the difference was.
- **The general rule, which is the point: from the consequence to the setting.** Whenever the app shows the *result* of a setting, it says where that setting lives. A page count, a template name, a cover, a trim, a word-count goal — each should be one hover away from the control that changes it. Most of the confusion in this project has been of that shape: the value is visible, the lever is elsewhere.

## 7. One paper size per book — enforce it

**Superseded in shape by the model** (`docs/MODEL.md`): books are folders whose Type is `Book`, page setup hangs off those, and `type_of()` heals existing book folders into visibility. What survives: the engine refusal (`set_page_setup` on a non-folder), the inspector showing the resolved value read-only on chapters/scenes with its owner named, and the mixed-trim export decision (honour each book's stamp, warn when trims differ).

A book with some pages 5 × 7 and some 6 × 9 is not a book. Page setup should therefore be settable **only on a folder**, never on a chapter or a scene:

- **Engine:** `set_page_setup` refuses a target that is not a container, so the rule holds no matter which door is used (inspector, future script, a preset).
- **Interface:** the Page setup button appears on folders only. With a scene or chapter selected the inspector shows the resolved value *read-only* and names its owner — *"Paper: 6 × 9 — from ‘Third Book’. Change it there."* That is item 6's rule again: from the consequence to the setting.
- **Nearest ancestor still wins**, so setting it on the book covers everything inside, and a *series* folder can set a house trim for all its books.

**The case this does not solve:** an export spanning two books with different trims. Today one file gets one paper size and the first book in binder order wins (1.15's recorded edge). Two honest options, and the second is better if it is not expensive:

1. warn in the export dialog — *"this selection spans ‘A’ (5 × 7) and ‘B’ (6 × 9); the file will use A's"*;
2. honour each book's own trim by stamping its sections separately, since DOCX carries a page size per section anyway. Then a two-book file is genuinely two books in one file, which is what the writer selected.

## 8. Insertions: literals and placeholders are different things

Two kinds of "insert date", and they must not be confused in the menus:

- **A literal** — *Insert current date* / *Insert current time*: the text is written into the document once, and it says what the date was when the writer typed it. (Build-now 5 shipped this, and it feeds the `Date` metadata.)
- **A placeholder** — `${Date}`, `${Author}`, any resolved value: it stays `${Date}` in the file and is replaced **at export**, so it says the date of the export. Placeholders work anywhere in the project and are only ever resolved on the way out (`docs/TEMPLATES.md` §3.1).

The Format menu wants a small **Insert** dropdown holding both, side by side and clearly labelled — one row for the literals, one for the placeholder vocabulary — because the difference is invisible until the day it matters.

**Engine gap to close first:** placeholders are substituted in *template slots* today; a `${Date}` written in **prose** is not. `_render_document` fills the slot and passes content through untouched. Until prose is filled at compile time, the placeholder half of this menu would insert text that never changes — so this is a prerequisite, not a nicety.

**Assigned to the interface side** (`docs/WORK-SPLIT.md`): Preferences rebuild, the three items above it, and this rename, while the engine side keeps working on the template spec and the print interior.

Nothing here changes behaviour, which is exactly why it deserves a proper pass instead of a one-dialog patch — it is the screen a writer visits when the software has already confused them.

## 8. Metadata panel reformatted; Type is the role row — DONE

The structural model is written up in `docs/MODEL.md`: folders and files, roles assigned by the protected `Type` metadata, no publish flag (exclusion is Type + a blank template, or the export scope). The inspector's metadata section now reads like Preferences did after its rebuild:

- a **Type** row on top — editable dropdown (novel-writing defaults plus every Type the project already uses; typed values join the list), always populated from `type_of()`, editing the document's own metadata even on folders where the table below shows project fields;
- idiot-proofing: exact match snaps to the existing option; a case-only variant warns ("looks like the existing type…") with a *Keep it as a separate type* checkbox, snapping until ticked;
- the table below is **Custom fields**, its third column renamed **Format** (was "Type" — the collision with the metadata key);
- the Edit Metadata dialog hides the Type row and preserves the stored value; the outliner Kind column, binder tooltips, Quick Open and the related-files list show `type_of`, never the raw physical kind.

## 9. Page start leaves the inspector — DONE

The Page start combo is gone from the inspector: the template owns page starts per kind (`New Chapter: Recto` in the template config, `docs/TEMPLATES.md` §2) and the writer picks the template, so a document-level control would only fight it. What stays:

- the export's auto baseline is untouched — chapters open recto once a print trim is set, every other document starts its own page;
- values stored by older projects are still honoured by export, silently: nothing in the inspector reads or writes them anymore.

When the template's per-kind page starts land (engine side), they take over the baseline as well. The Document template row is unrelated — it picks a rendering slot, not a page start.

## 10. The format bar is truncated; styles only come from a menu — DONE (2026-10-29)

The Editor tab's format bar packed every control into one QToolBar: bold, style, font, size, lists, alignment, clear. A QToolBar cannot wrap, so past the width the bar's tail vanished — alignment sat one narrow window away from invisible, and the button row read as "the few buttons". Two things followed:

- **the bar flows.** A QToolBar was the wrong widget: it wraps nothing. The bar is now a flowing row (`lekhika/flowlayout.py`, height-for-width on), so a narrow window makes the bar taller instead of cutting controls off, and the alignment buttons say which way the caret's paragraph sits — the checked button is the paragraph's own, so a mixed selection checks nothing. Justify joined while it was open.
- **styles add themselves.** The box already *offered* saved styles but every way to make one lived in the right-click menu; the box now ends in `+ New style from paragraph…` and `Update style from paragraph…`, capturing straight into the project's styles. Delete is deliberately not in the box — removing a style is a library operation, not something to click beside prose; the menu keeps it until Preferences grows a styles panel.

Text colour and highlight stay out: a colour button would promise something the compile does not keep (the book's words are plain body text), and a style can carry whatever colour the stylesheet gives it.

## 11. The metadata section, in boxes — DONE (2026-11-02)

The metadata was "undifferentiated text with no clear logic to it": one stack of labels and fields, and the two long boxes — the synopsis and the custom-fields table — free to grow until they overflowed the inspector. It is now four groups, each with a header that folds it away:

- **Basic** — Kind (Type), the lock, the word-count goal, the template set. Open by default: these are the facts a writer checks at a glance.
- **Synopsis** — the synopsis box, capped so it scrolls inside its box.
- **Format / Scene** — Page start, Scene divider, Cover, Page setup, and the unpublished-links policy. Closed by default.
- **Advanced / Custom** — the custom fields table, capped so a long list of fields scrolls, with Add field / Remove selected under it. Closed by default.

The headers are tool buttons, not checkboxes: a fold is something you open, and the arrow says which way it goes. A group takes the height it needs and no more, so the surplus in a tall window stays at the bottom of the inspector instead of opening a gap under one box. The rows themselves are unchanged — the same widgets, the same storage, the same live saves; only the arrangement is new (ROADMAP 1.16).

## 12. Type is a role, the template is a tick — DONE (2026-11-03)

The Type dropdown used to carry the whole vocabulary of the writing life — Book, Chapter, Scene, Part, Note — and the template had nowhere to live, because a template is not a role: it is a thing whose body is markup, not manuscript. Two changes, in the Basic group:

- **Type is a role, and there are two of them to begin with.** A folder is a Folder and a document is a Document, and the dropdown offers only the roles of the *kind* it is showing: a folder's list never shows a document's role, a document's never shows a folder's. Beside the dropdown a **"New type…" box** adds a role: it joins that kind's list in the database, so the documents that follow can pick it. A folder with book settings is still a Folder — "Book" is a role you add, not one the app guesses.
- **The template is a tick.** Beside the Type row, **Template**: ticked, the document's body becomes markup, it stays out of the book, and the sidebar swaps to the template design panel; unticked, the ordinary metadata comes back.

Under the Type row, the **Apply template** dropdown lists every template the project knows — the sets' own slots, the one-file templates in `templates/`, and the documents marked as templates — and beside it the note **"Copy default templates to customize"**, which puts an editable copy of the shipped default set in the project's `templates/` folder (the shipped ones cannot be edited in place).

**Page start has finally left the inspector** (§9), and it left Format / Scene with it (§11): the page start belongs to the template's design panel, where a pick writes the `New Chapter:` / `New Scene:` lines the print config speaks — "whether this page design begins on x kind of page". §9 was right from the start; the code kept the widget anyway, and both landed together in commit `bed28c5`. Values older projects stored are still honoured by the export, silently.

The Outliner's Kind column, the binder tooltip, the related-files list and the link picker now show the *physical* kind — folder, chapter, scene, note. The role is the writer's own label and says nothing about what the thing is; it belongs in the inspector (ROADMAP 1.17).

## 13. A book has one paper, a page has its own margins — DONE (2026-11-03)

Printing has one paper size — a book is one object on one trim — but the pages inside it are not all the same page. A title page wants wider margins, a dedication wants no running head, and neither is a reason to print the whole book differently.

- **The template folder owns the paper.** A template made inside a template folder no longer shows Page size or Mirror margins: those are the folder's to set, and the note above the rows says so. **Margins** and **Page start** stay on the sub-template, because they are about where *this* page sits on that paper and how much of it it uses.
- **Those margins print.** An exported document whose own design states a `Margins` line gets its own DOCX section carrying them; the paper, gutter and mirroring stay the book's.
- **`Header: none` / `Footer: none`** is how a page says it carries no running head. An empty line still means "inherit the sample", which is not the same thing.
- **Standalone beats the book.** A page printed on its own — the Page tab, a one-document export or print — is printed as its own design states it, size included. A book export keeps the book's trim and ignores the page's silently; the export dialog's template set is the override that wins.

The edge boxes' names (Inside/Outside vs Left/Right) follow the mirroring in force: a sub-template's own mirror line stands down with the checkbox, so the book's answer names the boxes.
