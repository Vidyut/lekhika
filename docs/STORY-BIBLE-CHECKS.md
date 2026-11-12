# Story bible consistency checks (workflow)

A practical approach: check against a **story bible** without dumping the whole manuscript + bible into one huge prompt. Use scene-level context, character arcs, and citations back to exact source text.

## Goals
- Catch contradictions/drift between manuscript and bible (characters, settings, rules/timeline/relationships) without being naggy.
- Keep prompts small and reliable (works better with local models).
- Always show the **exact source text** that triggered a flag (cite scenes/chapter positions).
- Be incremental and opt-in.

## Data model
| Item | Purpose | Notes |
|---|---|---|
| **Bible entries** | Canon facts/constraints | Chunk into small fact cards (character, location, faction, system/rules, timeline). Prefer writer-editable. |
| **Entities & links** | Who/what appears | Reuse existing name recognition, `[[wiki links]]`, mentions/related files and metadata. |
| **Scene index** | Position/order | Known from binder/order files (chapter/scene sequence). Needed for “up to now / future” and cross-chapter lookup. |
| **Character arcs** | Per-entity timeline | For each scene where entity appears/affected, add a short entity-relevant note with `scene_id/chapter_id/position`. Built from synopsis + extracted facts. |
| **Continuity ledger** | Extracted events/facts | Compact entries (who/what/when/where/changed) with pointers to source text/scene. Indexed for retrieval (RAG/keyword). |
| **Book/chapter summaries** | Global continuity snapshot | Hierarchical summaries updated incrementally; used for broader checks and context assembly. |

## Indexing / extraction (background, incremental)
1. **Chunk bible** into fact cards and tag by entities/keywords.
2. **Scan scenes in order**: use existing links/names/metadata to identify entities per scene.
3. **Build character arcs**: for each (entity, scene) pair, generate a short entity-relevant arc entry (what happens/changes to them) linked to that scene.
4. **Extract ledger entries**: pull concrete events/facts/promises/constraints introduced/changed per scene with exact-text pointers.
5. **Maintain summaries**: update chapter and book summaries incrementally when scenes/ledger change (cache aggressively).
6. **Embed/index for retrieval**: index ledger entries and fact cards (embeddings or simple text search) to find candidates quickly.

## Runtime checks

### 1) Scene-level (fast, on-demand)
For the current scene:
- Assemble context: relevant bible fact cards + each entity’s arc up to current scene index + (optionally) chapter summary.
- Check scene prose against that bundle.
- Return flags with **exact quoted passages** from scene and cited bible/arc entries. Writer can accept/ignore.

### 2) Continuity / manuscript-level (slower, on-demand)
For broader checks:
- Query ledger/RAG for candidates related to entities/timeline in question (e.g. same character across distant chapters).
- Compare against bible fact cards and arc history.
- For any candidate, **fetch exact source text** from those scenes (never rely only on retrieved summaries).
- Report with pointers to specific scenes/chapters and side-by-side quotes.

## UX principles
- **Cite everything:** every flag links to exact source text in manuscript and/or bible entry.
- **Soft warnings:** suggestions, not blocks. Confidence/why shown.
- **Opt-in & scoped:** per-scene check first; global scan separate and background-friendly.
- **Local-friendly:** prefer small models, incremental indexing, caching, and on-demand runs.
- **Explainable:** “up to ch4 / future after ch4” context when relevant to avoid timeline confusion.

## Implementation notes / open questions
- **Relevance detection:** name/mention matching + links is a good start; may need simple heuristics or entity tagging.
- **Arc freshness:** decide when to regenerate arc entries (on scene edit/save) and how to handle retcons.
- **Ledger schema:** keep minimal (entity ids, scene/chapter ids, offsets/ranges, fact text, source quotes, tags) to support fast lookup and exact retrieval.
- **Token control:** hierarchy (book/chapter/arc + facts + single scene detail) to stay within context without losing long-range awareness.
- **False positives:** require exact citations + allow easy ignore/teaching; track dismissed flags per entity/scene.
- **Timeline/sequence:** use scene order from binder as the “time/order” axis for “past vs future” in a scene’s context.
