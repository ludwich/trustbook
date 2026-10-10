# Evaluation question format

Question files are YAML. A file is either a list of questions or a mapping with a `questions:`
list. `eval/runner.py --questions` takes files or directories (every `*.yaml` / `*.yml` in a
directory, sorted; a `corpus.yaml` there is ignored) and can be repeated. Question ids must be unique
across all files. Private golden sets use the same format and live **outside** the repo.

```yaml
questions:
  - id: code-01                       # required, unique
    question: What is the default maximum number of redirects?   # required
    type: answerable                  # required: answerable | unanswerable
    category: code                    # optional: docs | code | multi | unanswerable (report only)
    expected_sources:                 # required for answerable; titles or origins (see below)
      - requests
    expected_locators:                # optional; narrows which citations count as correct
      - {file: src/requests/models.py, lines: [104, 104]}
      - {file: src/requests/sessions.py, lines: [484, 488]}
    must_contain: ["30"]              # optional keywords (tracked metric)

  - id: unans-01
    question: How do I open a WebSocket connection with requests?
    type: unanswerable                # expected_sources not needed
```

## Fields

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Unique id (string or number). |
| `question` | yes | Sent verbatim as the chat message. |
| `type` | yes | `answerable`: the space's sources contain the answer. `unanswerable`: they do not; the right outcome is a refusal. |
| `expected_sources` | answerable: yes | Sources that may be cited. Each entry is matched against every source in the space by **title** or by **origin** (see below). |
| `expected_locators` | no | Where in those sources the answer is. See "Locators". |
| `must_contain` | no | Keywords the answer should contain (case-insensitive substring). Counted in the must_contain hit rate. |
| `category` | no | `docs`, `code`, `multi` or `unanswerable`; shown in the report. |

### Matching `expected_sources`

An entry matches a source when any of these holds:

- **Title**: equal to the source title, ignoring case, repeated whitespace, HTML entities, `¶`, and
  dash variants (`—`, `–` and `-` are the same). A URL source's title is the page title as
  Trustbook extracted it; a linked folder's title is the folder name (e.g. `requests`).
- **Origin URL**: the entry and the source origin are both URLs and equal after lower-casing scheme
  and host, dropping the `#fragment` and any trailing `/`.
- **Origin path** (folder sources): the entry is a path (not a URL) and equals the origin or is a
  path-suffix of it, or the reverse. `trustbook-eval/requests` matches `/host/trustbook-eval/requests`.

Listing both a page title and its URL is fine; they resolve to the same source. Entries that match
no source in the space are listed under "Notes" in the report, which catches title typos.

### Locators

Each `expected_locators` entry has exactly one kind:

| Kind | YAML | Matches a citation locator when |
|---|---|---|
| File | `{file: path, lines: [start, end]}` (`lines` optional; an int means one line) | `locator.file` path-matches `file` (equal or path-suffix either way, `\` = `/`) and, if `lines` is given, `[start_line, end_line]` overlaps `[start, end]` (inclusive). A citation without `start_line` fails a `lines` check. |
| Page | `{page: 3}` or `{pages: [3, 4]}` | `locator.pages` shares a page. |
| Heading | `{heading: "Quickstart"}` | `locator.heading` equals it (normalised like titles) or one of its parts split on `>`, `/`, `»`, `\|`. |
| Time | `{start_s: 60, end_s: 95}` | `[locator.start_s, locator.end_s]` overlaps. |

Optional `source: <entry>` on a locator restricts it to citations of that source.

A locator only applies to citations whose locator is of the **same kind** (and, with `source:`, from
that source). If no expected locator applies to a citation, the source check alone decides. So a
multi-source question can pin the code citations to line ranges and still accept any citation of
the documentation page.

## Metrics (as computed by `eval/runner.py`)

Each question is asked in a **new conversation**. The runner reads the SSE stream from
`POST /api/spaces/{sid}/conversations/{cid}/messages` until `done` or `error` (a `refused` event
marks the outcome; the stream is read on to `done`). Outcomes:

- `answered`: a `done` event without a refusal.
- `refused`: a `refused` event, or a `done` message whose `badge` is `"refused"`. The gate's
  fixed chitchat reply (a `done` message with `text`, no sentences and badge `null`) also counts as
  `refused` (reason `chitchat`): no answer was given.
- `error`: an `error` event, an HTTP error, a broken stream, or a stream that ends without
  `done`/`refused`/`error`.

Citations are taken from the `done` message's sentences (falling back to the streamed `sentence`
events) and **deduplicated per answer** by `chunk_id`: citing the same chunk in three sentences
counts once.

| Metric | Definition |
|---|---|
| **Citation precision** (gate) | Over answerable questions: correct citations / all citations. A citation is correct if its source matches an `expected_sources` entry **and** (when an expected locator applies, see above) its locator matches one. Refused or failed answers add no citations; they show up as false refusals or errors. Also shown per question as `correct/total`. |
| **Correct-refusal rate** (gate) | Unanswerable questions with outcome `refused` / all unanswerable questions (errors count as not refused). |
| **False refusals** | Answerable questions with outcome `refused` / all answerable questions. Also shown per category: `docs`, `code`, `multi`, and `other` (no category, or any other value). |
| **False refusals on code questions** (target with `--max-false-refusals-code N`) | Refused / all answerable questions that are **code questions**: `category: code`, or any question with an expected **file** locator whose file is code. A file is code unless its extension is a documentation or text one (`md markdown mdx rst txt text adoc asciidoc html htm pdf docx pptx xlsx odt rtf`); a name without an extension (`Makefile`) is code. So a `multi` question pinned to `src/requests/auth.py` counts, one pinned only to `HISTORY.md` or a docs heading does not. The target is a count: exit 1 when more than `N` are refused, or when the set has no code questions. |
| **Errors** | Questions with outcome `error`. |
| **Answer latency p50 / p95** (target with `--max-p95 SECONDS`) | Time from sending the message request to the first `done` or `refused` event, over `answered` and `refused` questions. Percentiles use linear interpolation between closest ranks (NumPy's default). A p95 above 60 s is flagged "**above 60 s**" in the report even without `--max-p95`. |
| **Cited ranges valid** (with `--code-root PATH`; target with `--min-ranges-valid`) | Over every citation (deduplicated per answer, all answered questions) whose locator has a `file`: the file exists under `PATH` (a checkout of the corpus at the indexed commit; paths that leave `PATH` count as missing) and, when the citation has line numbers, `1 <= start_line <= end_line <=` the file's line count. Invalid ones are listed under "Cited ranges" with the reason. Gate 3 wants 100%. |
| **must_contain hit rate** (target with `--min-must-contain`) | Answerable questions with `must_contain` whose answer text contains **all** keywords / answerable questions with `must_contain`. Refused or failed answers count as misses. Per question shown as `found/total`. Catches answers that cite the right passage but stop short of the value asked for (e.g. a constant's name instead of its value). |
| **Expected sources cited** (informational) | Per answerable question: distinct expected sources (as resolved in the space) that were cited / expected sources found in the space. Shows whether a multi-source answer used both sources. |

`--min-precision`, `--min-refusal`, `--min-must-contain` and `--min-ranges-valid` (fractions, `0.9` or
`90`) make the runner exit 1 when a value is below the threshold or cannot be computed (no citations /
no unanswerable questions / no answerable questions with `must_contain` / no file citations).
`--max-false-refusals-code N` (a count) and `--max-p95 SECONDS` exit 1 when the value is higher or
cannot be computed. `--min-ranges-valid` needs `--code-root`. The report shows the target and
pass/**FAIL** for each one given.

## Corpus file (`--setup`)

```yaml
space_description: "optional, used when the space is created"
sources:
  - folder: trustbook-eval/requests      # path relative to the browse root
  - url: https://requests.readthedocs.io/en/stable/user/quickstart/
```

The runner creates the space (named by `--space`) if missing, adds each source unless one with the
same origin already exists (URL or path match as above), then polls `GET /api/spaces/{sid}/sources`
until every corpus source is `ready` or `failed`, printing progress. After `--setup-timeout`
(default 3600 s) it continues anyway and notes the unfinished sources in the report.

## Scores sidecar (`--report out.md` → `out.json`)

With `--report`, the runner also writes a JSON file next to the report (`out.md` → `out.json`;
another name gets `.json` appended). It holds **ids and numbers only**, never question or answer
text, and nothing is written anywhere else, so a private set stays wherever you keep it.

```json
{"version": 1, "date": "2026-10-10T12:00:00Z", "api_version": "phase-2-rc", "space_id": "…",
 "thresholds": {"refuse": 0.15, "verify": 0.5},          // GET /api/settings; null if unavailable
 "questions": [{"id": "code-01", "type": "answerable", "category": "code",
                "outcome": "answered", "refusal_reason": null, "badge": "partially_verified",
                "top_rerank": 0.83, "sentence_scores": [0.97, 0.12],
                "sentence_methods": ["checker", null], "regenerated": false,
                "removed_sentences": 1, "citations": 2, "citations_correct": 2, "latency_s": 4.1}],
 "false_refusals": {"docs": {"num": 0, "den": 5, "value": 0.0}, "code": {…}, "multi": {…}, "other": {…}},
 "false_refusals_code": {"num": 1, "den": 17, "value": 0.06},
 "cited_ranges": {"num": 48, "den": 48, "value": 1.0},     // only with --code-root
 "sweep": [{"refuse": 0.05, "verify": 0.3, "correct_refusal": {"num": 3, "den": 3, "value": 1.0},
            "answered": {…}, "precision_proxy": {…}}]}
```

`top_rerank`, `sentence_scores`, `sentence_methods` and `regenerated` come from the answer's
`diagnostics` (docs/ARCHITECTURE.md, "Grounding layers 4–5" and "Code claim verification").
`sentence_scores` follows the model's cited sentences, removed ones included: the grounding
checker's best score over the cited chunks, `0.0` when a deterministic code check failed (it fails
at any threshold), `null` when the checker is off. `sentence_methods` (same order) says what passed
each sentence: `checker`, `code_judge` (it failed the checker and passed the code judge; its score
is still the checker's) or `null` (failed, or checker off). They are `null` for answers from an
API without them.

## Threshold sweep

The report's "Threshold sweep" table re-decides every question for refuse thresholds 0.05–0.40
(step 0.05) and verify thresholds 0.3–0.7 (step 0.1) **from the recorded scores, without asking
again**. The row of the run's own thresholds is marked **(run)**. A question's estimated outcome
at `(refuse r, verify v)`:

1. `error` stays an error (not refused, not answered).
2. **Refused** if `top_rerank < r` (layer 2).
3. Otherwise, if sentence scores were recorded: **refused** if the first score is below `v`, or more
   than half of them are (the refusal rule; `null` scores pass, and so do sentences whose
   `sentence_methods` entry is `code_judge`: the judge's decision does not depend on `v`). If not, **refused** when the run
   refused it for a reason the scores can't re-decide (anything but `unsupported`: `no_sources`,
   `invalid_output`, `chitchat`, or `not_in_sources` from a model that found nothing); else
   **answered**.
4. Without sentence scores (refused before generating, or no diagnostics), the run's outcome stands.

Columns:

| Column | Estimate |
|---|---|
| Correct refusals | Unanswerable questions estimated `refused` / unanswerable questions. |
| Answered | Answerable questions estimated `answered` / answerable questions. |
| Precision proxy | Over answerable questions that were answered in the run **and** are estimated `answered`: citations (deduplicated per answer) of the kept sentences whose score is `>= v` (or `null`), judged like citation precision. |

Limits (where a row is only a bound):

- **Refuse thresholds below the run's.** A question refused before generating has no answer to
  score, so it stays refused even where `top_rerank >= r`. Correct refusals are then an upper bound
  and answered rates a lower bound. For a full sweep, run once with a low refuse threshold (set
  `thresholds.refuse` in `config/settings.json`, then restart Trustbook).
- **Verify thresholds below the run's.** Sentences the run removed are known only by score, not by
  citations, so they can make a refused answer "answered" but add nothing to the precision proxy.
  An answer refused as `unsupported` and "answered" at a lower `v` contributes no citations.
- **Regeneration** is not replayed: the scores are those of the final attempt.
- Kept sentences are matched to their scores by the run's verify threshold (scores `>= verify` or
  `null`, in order). If that does not line up, their scores count as unknown (`null`, included).
