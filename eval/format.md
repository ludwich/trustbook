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
- `refused`: a `refused` event, or a `done` message whose `badge` is `"refused"`.
- `error`: an `error` event, an HTTP error, a broken stream, or a stream that ends without
  `done`/`refused`/`error`.

Citations are taken from the `done` message's sentences (falling back to the streamed `sentence`
events) and **deduplicated per answer** by `chunk_id`: citing the same chunk in three sentences
counts once.

| Metric | Definition |
|---|---|
| **Citation precision** (gate) | Over answerable questions: correct citations / all citations. A citation is correct if its source matches an `expected_sources` entry **and** (when an expected locator applies, see above) its locator matches one. Refused or failed answers add no citations; they show up as false refusals or errors. Also shown per question as `correct/total`. |
| **Correct-refusal rate** (gate) | Unanswerable questions with outcome `refused` / all unanswerable questions (errors count as not refused). |
| **False refusals** | Answerable questions with outcome `refused` / all answerable questions. |
| **Errors** | Questions with outcome `error`. |
| **Answer latency p50 / p95** | Time from sending the message request to the first `done` or `refused` event, over `answered` and `refused` questions. Percentiles use linear interpolation between closest ranks (NumPy's default). |
| **must_contain hit rate** (target with `--min-must-contain`) | Answerable questions with `must_contain` whose answer text contains **all** keywords / answerable questions with `must_contain`. Refused or failed answers count as misses. Per question shown as `found/total`. Catches answers that cite the right passage but stop short of the value asked for (e.g. a constant's name instead of its value). |
| **Expected sources cited** (informational) | Per answerable question: distinct expected sources (as resolved in the space) that were cited / expected sources found in the space. Shows whether a multi-source answer used both sources. |

`--min-precision`, `--min-refusal` and `--min-must-contain` (fractions, `0.9` or `90`) make the runner
exit 1 when a value is below the threshold or cannot be computed (no citations / no unanswerable
questions / no answerable questions with `must_contain`). The report shows the target and
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
