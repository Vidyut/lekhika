# AI in Lekhika — ways to connect, and what to expect

Lekhika talks to any **OpenAI-compatible** `/chat/completions` endpoint (2.4's client): an endpoint, a model name, optionally a key, all in Preferences. Two ways to use it.

## 1. A hosted model (the usual path)

Point Preferences at the endpoint you already pay for. Fast, capable, and it means sending your text to a third party — which is a choice only the writer can make, so it is never made for them.

## 2. A local model (for writers who will not upload, or who have the hardware)

Run a model on your own machine; nothing leaves the computer. Install [Ollama](https://ollama.com), then load the model you choose:

```
ollama pull <model-name>
```

The default host and port are almost always correct — Lekhika expects `http://localhost:11434/v1`. Check it in Preferences if the connection misbehaves.

**Read this before you rely on it.** A local model that fits on an ordinary computer is a *very small* model, and it is offered for exactly that reason: it fits on almost any computer. The smaller the model, the more error-prone it is and the less it can usefully do. The smallest useful tier may manage nothing more than typos and simple bloopers — and that is not a small thing: fixing typos across a 100,000-word book is nothing to sneeze at. It is not to be trusted with anything unsupervised, and not for anything exotic.

## How the app should behave (decided 2026-10-21)

**Do not trust the writer with the model's quirks; do the idiotproofing in the app.** When the configured model is small, Lekhika feeds the work in **paragraphs, or three or four sentences at a time, sequentially** — not the whole chapter — because we *know* long input is where it breaks. The writer presses one button; the chunking is not their problem.

- **"Click here to proofread."** The shipped prompt fixes typos, spelling, grammar, doubled lines, stray spaces and the like. The writer never sees it unless they want to.
- **The prompt is editable, but folded away** behind a collapsible section whose heading recommends leaving it alone unless you know the eccentricities of small models. Sensible default, escape hatch, no footgun.
- **Every result is a tracked change**, always (§ below) — the model cannot write to the manuscript. This is structural and applies to every model, local or hosted, tiny or enormous. *No AI edits the text directly, ever.*
- **Expect tuning.** Prompt wording and chunk size for small models will take trial and error; the shipped defaults are a starting point, not a verdict.

## Dictionaries, and why writers hate them

Spell-check squiggles go blind-making in fiction: invented names and worldbuilding jargon light up the whole page and the writer stops seeing any of it. A model, even a small one, tends to *remember* the words it has seen, which is the better half of the problem.

So: a dictionary pass is worth having — for real typos, deterministically and offline — but it should be fed. When a model proofreads, ask it to return the **entities** it noticed (people, places, made-up terms) so they can be added to the project's dictionary alongside the squiggle pass. Then the two halves complement each other: the dictionary catches spelling, the model catches grammar and stops the dictionary from crying wolf.

The whole feature is worth building even though small models will do a mediocre job, because anyone using a larger model or a hosted API gets genuinely good results from the same code.

## What protects the manuscript

- **Every AI result arrives as a tracked change**, never a direct write —
accept or reject, per change (2.4). The model cannot alter your prose behind your back; that is structural, not a promise.
- **Tracking is not required for the AI, but reviewing is the point.** The AI menu items land as "AI" revision passes precisely so the writer stays the filter.
- **Small models rewrite.** They smooth voice, "tidy" deliberate fragments and restructure sentences. The proofreading prompt therefore asks for minimal edits only, and the UI should say *bloopers, not prose*.
- **Keep selections short.** A few paragraphs, not a chapter: context is small and long selections drift.

## What each tool is actually good at

| Task | Right tool |
|---|---|
| Spelling, doubled words, obvious typos | a dictionary (`hunspell`/`aspell`) — deterministic, tiny, offline, never hallucinates |
| Sentence-level grammar and punctuation | a model, through tracked changes |
| Plot, structure, "is this any good" | a large model, as advice only |

A dictionary plus a tiny model is a better deal than either alone, and the dictionary half costs nothing in trust.

## Chunking is our job, not the writer's

For a small model, never hand the writer a whole chapter and hope: feed it a paragraph at a time, or three or four sentences where paragraphs are long, **sequentially**, and assemble the result as tracked changes. We know small models degrade past a short context — so asking the user to judge the right size is asking them to know something we already know. Idiotproof where it can be.

## One button, and a prompt behind a fold

The everyday path is a single action — *Proofread this* — whose prompt is ours (typos, spelling, grammar, doubled words and lines, stray spaces, punctuation), not a form to fill in. The prompt lives in a **collapsible section, editable, with a strong note: do not change this unless you know the eccentricities of small models.** Experienced users can tune it; nobody has to.

## Dictionaries, jargon, and what the model is for

A spell dictionary helps, but writers ignore red squiggles once worldbuilding names light up the whole page — blindness, not vigilance. Models tend to *remember* invented words, which is why the grammar pass earns its keep. A useful future step: have the model extract entities (character, place, term) and feed them both to the dictionary and to later prompts. Expect tuning — this is trial and error, and the ceiling scales with the model: a small local one fixes bloopers; a good API fixes prose.

## No AI writes anything

Two rules, both absolute. **No AI edits the manuscript** except through tracked changes the writer accepts (structural, 2.4). **No AI edits Lekhika's own code** — not a local model, not a hosted one, however capable. Advice is welcome; commits are a human act.

## Warning text to show in Preferences

> **Local models are small.** The one that fits on almost any computer is a
> very small model: expect more errors and less capability the smaller it
> gets. The smallest may only fix typos and simple mistakes — still worth
> having for a long manuscript. Do not trust it unsupervised, and review
> every change. For anything beyond basic fixes, use a larger model or your
> own judgement.
