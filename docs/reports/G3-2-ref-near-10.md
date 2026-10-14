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

## Before and after the fixes (2026-10-14)

Ludwig approved F6, F1, F4 and F2 (D-118..D-121; F3 and F5 not implemented), and added line 106 to the
answer key. Same data directory and spaces for both: **CodeOnly** (1,217 chunks, new chunker) and **Smoke**.
Exact question, a fresh conversation per run, 5 runs per cell, Qwen3.6-35B-A3B on Ludwig's llama-server.
Before: image from `4dfae43`. After: image from `dcd38df` (the four fixes; the two review fixes in `d7a662f`
touch only feedback wording and a promotion-slot rule, not this question's path).

| Cell | Before: verified / partial / refused | After: verified / partial / refused | Median / max latency, before → after |
|---|---|---|---|
| CodeOnly, app default (0.2) | 4 / 1 / 0 | 3 / 1 / 1 | 29.6 / 32.4 s → 47.2 / 55.0 s |
| CodeOnly, eval (0, seed 42) | 4 / 0 / 1 | 4 / 1 / 0 | 31.3 / 43.5 s → 39.5 / 65.9 s |
| Smoke, app default | 4 / 0 / 1 | 0 / 5 / 0 | 36.9 / 43.6 s → 36.6 / 70.1 s |
| Smoke, eval | 4 / 1 / 0 | 3 / 1 / 1 | 37.1 / 46.0 s → 56.3 / 64.3 s |
| **Total (20 runs)** | **16 / 2 / 2** | **10 / 8 / 2** | |

- **Refusals:** 2 of 20 before and after, all `unsupported` ("`requests.codes` is not in the cited chunks"); no
  `not_in_sources`. With F6 these now show "Couldn't verify an answer", not "Not in your sources".
- **Citations:** all in the answer key before (28/28) and after (32/32), with or without line 106. After the
  fixes the model cites `_init` (109–125) in 8 of 18 answers (before: never). No run cited line 106.
- **Context (F1, F2):** in every after run `_init` is in the context (promoted as `data_user`) and the table
  chunk 77–85 sits by its fused rank, next to the docstring, instead of last.
- **Why more partial answers:** with `_init` in the context, the model also names the upper-case attributes
  (`TOO_MANY_REQUESTS`, `TOO_MANY`), which `_init` creates with `title.upper()`. That text is in no chunk, so
  layer 5 removes the sentence (5 of the 8 partials); the remaining 3 failed the checker and the judge. This is
  layer 5 working as specified; loosening it for derived names would weaken grounding.
- **Latency:** answers got slower (a longer context; judge calls per run went from 0–3 to 0–4). The shared
  llama-server also had another model loaded during the after runs, so part of the difference may be load.
- **Verdict:** the fixes put the right chunks in front of the model and the refusal reason is now visible, but
  on this question they did not reduce refusals, and they turned full answers into partial ones. Whether to keep
  F1 is Ludwig's call (Gate 5). F6 is independent of the partials.

Raw runs: builder scratchpad `g32/before_*.json`, `g32/after_*.json`, `score_before.txt`, `score_after.txt`.
