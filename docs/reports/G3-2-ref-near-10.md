# G3-2: why ref-near-10 is refused (diagnosis, 2026-10-13)

Diagnosis only; nothing was changed. Image built from `79a052a` (retrieval, grounding, chat and chunking are
unchanged since). Chat: Qwen3.6-35B-A3B on Ludwig's llama-server. `requests` at 6e83187. Two spaces:
**CodeOnly** (the `requests` folder only, 1,045 chunks) and **Smoke** (the folder plus the 4 doc pages).

Question: "ref-near-10" (`requests.codes`, the names for HTTP 429).

## Where the answer is (`src/requests/status_codes.py`, as indexed)

| Chunk | Lines | Holds | In the answer key |
|---|---|---|---|
| Module docstring | 1–21 | `requests.codes[...]`, names usable as attributes | yes (1–19) |
| `_codes` table, part 6 | 77–85 | `429: ("too_many_requests", "too_many")`; the word `codes` does not occur (the header says `_codes`) | yes (85) |
| `codes = LookupDict(...)` | 106 | the public name | **no** |
| `_init` | 109–125 | the `setattr(codes, …)` loop | yes (109–114) |

## Retrieval for the exact question (identical over repeats and in both spaces)

| Chunk | Vector rank | Full-text rank | Fused | Rerank (of 40) | In the answer context |
|---|---|---|---|---|---|
| Docstring | 6 | 1 | 2 | **1**, score 0.5085 | yes, 1st of 8 |
| Table (429 row) | **1** | 2 | **1** | **32**, score 0.0174 | yes, only via the D-046 guard, **last of 8** |
| `codes = …` (106) | 9 | 7 | 5 | 13, 0.0402 | no |
| `_init` | 29 | 8 | 12 | 18, 0.0336 | no (cut by the 8-chunk cap, not by tokens: ~1.9k of 6k used) |

- Pre-answer refusal (rerank < 0.15): never fires (top score 0.51). Gate router: "sources", 0.95, every time.
- D-077 promotion expands only the first 3 ranked chunks, so the table chunk gets no parent or neighbours.
- Paraphrases: vector and full-text search rank the table chunk 1st or 2nd every time, so the miss is not
  lexical. The **reranker** buries it: 0.017 as indexed, 0.008 without its header, 0.20 with one docstring
  line prepended.

## Where it fails: verification, not retrieval

| Runs (fresh conversation each) | Answered | Refused `unsupported` | Regenerated |
|---|---|---|---|
| CodeOnly, exact question, app default (0.2) | 9/12 | 3 | 12/12 |
| CodeOnly, exact, eval settings (0, seed 42) | 3/3 | 0 | 3/3 |
| CodeOnly, 2 paraphrases | 1/2 | 1 | 1 |
| Smoke, exact (3 app default, 2 eval) | 4/5 | 1 | 1 |

**No run was refused as `not_in_sources`.** The model always finds `too_many_requests` / `too_many`. The
refusals are verification refusals (`unsupported`):
1. The model cites only the table chunk for a sentence that also says `requests.codes`; layer 5 rejects it
   because `requests.codes` is not in the cited chunk (3 runs). Checked directly: the table chunk alone never
   passes such a sentence; the table plus any of the docstring, `_init` or line 106 passes.
2. The model cites docstring and table, layer 5 passes, and the code judge vetoes: its evidence lines held only
   line 85, even after the D-085 follow-up (2 runs).

**Why it looked like retrieval in the UI:** the refusal card shows the same title and suggestion for
`unsupported` as for `not_in_sources` (`backend/app/chat/pipeline.py`), and the reason is not stored with the
message. The stored diagnostics do tell them apart (a pre-answer refusal has 0 answer calls).

## Root causes
1. **Main (verification):** no single context chunk holds both `codes` and the 429 row, so a correct sentence
   needs two citations; Qwen3.6 often gives one. When it gives both, the judge's evidence lines sometimes
   leave out the docstring.
2. **Contributing (retrieval):** the reranker scores the table chunk near zero. It survives only through the
   guard, comes last and is never expanded; the bridging `_init` chunk is cut by the 8-chunk cap.
3. **Reporting:** one shared "Not in your sources" card hides which layer refused.

## Candidate fixes (none implemented; awaiting Ludwig)

| # | Fix | Layer | Weakens grounding? | Answer key |
|---|---|---|---|---|
| F1 | When a data-table chunk is in context (including via the guard), promote the same-file chunk that uses it (`_init` uses `_codes`), within the D-077 budgets | retrieval / context | no: adds a verbatim chunk, still verified | unchanged |
| F2 | Place guard-kept chunks by fused rank, so the table sits next to the docstring | context order | no: the refusal threshold still uses rerank scores | unchanged |
| F3 | Rerank data-table chunks with a non-citable docstring prefix (0.017 → ~0.20) | reranker input | **risk**: the refusal threshold uses these scores; only safe if the threshold keeps the verbatim score | unchanged |
| F4 | Prompt/feedback: when a sentence uses names from two chunks, cite both | prompt | no: citations are still checked as given | unchanged |
| F5 | Accept an anchor that is in a cited chunk but missing from the judge's lines | judge | **yes** (weakens D-085); not recommended | unchanged |
| F6 | Give `unsupported` its own refusal card and store the refusal reason with the message | UI / API | no | unchanged |

Any fix that leads the model to cite line 106 (`codes = LookupDict`) would need the answer key widened, which
needs Ludwig's approval.

Raw traces and scripts: builder scratchpad `g32/` (not committed).
