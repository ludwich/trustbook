# Public smoke set

SPEC §13: 15 questions (5 docs, 5 code, 2 multi-source, 3 unanswerable) over a public corpus:

- **Code**: [`psf/requests`](https://github.com/psf/requests) (Apache-2.0), tag **v2.34.2**, commit
  **`6e83187b8feb273ed4c6cdab5efd8d54901dfab3`**, linked as a folder source titled `requests`.
- **Docs**: four pages of the requests documentation for the same release, added as URL sources:
  - https://requests.readthedocs.io/en/stable/user/quickstart/
  - https://requests.readthedocs.io/en/stable/user/advanced/
  - https://requests.readthedocs.io/en/stable/user/authentication/
  - https://requests.readthedocs.io/en/stable/community/faq/

  Read the Docs has no per-release build for v2.34.x, so these use `stable`, which is built from
  commit `6e83187b` (the pages say "Requests 2.34.2 documentation"). If a new requests release moves
  `stable`, re-check `verification.md` before using the set for a gate.

| File | Purpose |
|---|---|
| `corpus.yaml` | Sources for `runner.py --setup` |
| `questions.yaml` | The 15 questions (format: `eval/format.md`) |
| `verification.md` | The exact passage that answers each question, and the searches that show the unanswerable ones are absent |
| `fetch_corpus.py` | Downloads requests at the pinned commit (run once) |
| `refusal.yaml` | 30 refusal-tuning questions for Gate 2 (20 unanswerable, 10 answerable near-misses) |
| `refusal-verification.md` | The searches behind each unanswerable question and the passage behind each near-miss |

Nothing here is private; private golden sets stay outside the repo and are passed with `--questions`.

## 1. Fetch the corpus (once)

The folder must sit under Trustbook's browse root (`TRUSTBOOK_BROWSE_ROOT` in `.env`, mounted
read-only at `/host`) at `trustbook-eval/requests`, and must be named `requests`.

```sh
# Linux / macOS (browse root = your home folder, for example)
python3 eval/smoke/fetch_corpus.py ~/trustbook-eval/requests
```

```powershell
# Windows: replace C:\Users\ludwig with the TRUSTBOOK_BROWSE_ROOT from .env
py eval\smoke\fetch_corpus.py C:\Users\ludwig\trustbook-eval\requests
```

The script needs only Python 3.12. With `git` on PATH it shallow-fetches exactly the pinned commit
(Trustbook then records the commit). Without git it downloads the GitHub tarball of that commit.
Re-running is a no-op. Expect Trustbook to skip a few test certificate files under `tests/certs/`
as possible secrets: they contain private keys by design. No question depends on them.

## 2. Run the smoke set (Gate 1)

Prerequisites: Trustbook is running (`http://localhost:8484`), setup is complete with your chat
endpoint, and the corpus is in place. The runner needs Python 3.12 with the `trustbook-cli` package
(its API client, `cli/`) and `PyYAML`; the backend environment has both:

```sh
uv run --project backend python eval/runner.py \
  --base-url http://localhost:8484 \
  --space "requests smoke" \
  --setup eval/smoke/corpus.yaml \
  --questions eval/smoke/questions.yaml \
  --report gate-1-smoke.md \
  --min-precision 0.9
```

Without uv: from the repository root, `pip install "./cli[eval]"` (the CLI with its `eval` extra:
`httpx` and `PyYAML`), then `python eval/runner.py …` with the same arguments, or
`trustbook eval …` (it adds `--base-url` from `TRUSTBOOK_URL` and maps the exit codes, see
`docs/cli.md`).

What happens:

1. `--setup` creates the space "requests smoke" if needed and adds the folder and the four URLs
   (sources that already exist with the same origin are skipped, so re-runs are safe). It waits until
   every source is ready or failed and prints progress. Indexing the folder takes a few minutes.
2. Each question is asked in a new conversation (titled `eval: <id>`, so you can open them in the UI).
3. The Markdown report is printed and written to `gate-1-smoke.md`. Paste it into `docs/GATES.md`.

Exit code: 0 = done, 1 = a `--min-*` threshold was missed, 2 = Trustbook unreachable or bad input.

**Gate 1**: citation precision ≥ 90%. **Gate 2** adds `--min-refusal 0.95` (correct-refusal rate on
the three unanswerable questions; with three questions that means all three must be refused).
`--min-must-contain 0.8` also tracks whether answers contain the expected values (`must_contain`
keywords), e.g. `10` rather than only the name `DEFAULT_POOLSIZE`.

## Reading the report

- "Wrong citations" lists each citation that did not match: the cited source, its locator, and what
  was expected. Check the passage in the UI before deciding whether the answer or the expectation is
  wrong; if the expectation is wrong, fix `questions.yaml` and `verification.md` together.
- "Notes" lists `expected_sources` entries that match no source in the space. A page title listed
  there is harmless as long as the same page's URL entry matches (Trustbook may extract a slightly
  different title than the HTML `<title>`).

## Gate 2: refusal threshold tuning (`refusal.yaml`)

Three unanswerable questions are too few to tune `refuse_threshold` and `verify_threshold` (SPEC §8):
one miss already drops the correct-refusal rate to 67%. `refusal.yaml` adds 30 questions over the
**same corpus** (no extra setup):

- **20 unanswerable** (`ref-unans-*`): on-topic and plausible, but the answer is not in the corpus.
  Most are traps where a nearby fact *is* there (the docs' Retry example, the Keep-Alive section,
  the certifi section, HISTORY entries for other Python versions or CVEs, third-party libraries
  that are only linked).
- **10 answerable near-misses** (`ref-near-*`): the answer is in the corpus but hinges on a small
  detail, often right next to one of the traps. They count towards false refusals, citation
  precision and `must_contain`, so a threshold that simply refuses everything shows up as a failure.

Run both files together (ids are unique across them):

```sh
uv run --project backend python eval/runner.py \
  --base-url http://localhost:8484 \
  --space "requests smoke" \
  --setup eval/smoke/corpus.yaml \
  --questions eval/smoke/questions.yaml \
  --questions eval/smoke/refusal.yaml \
  --report gate-2-refusal.md \
  --min-precision 0.9 --min-refusal 0.95
```

That is 23 unanswerable questions (≥ 95% means at most one may be answered) and 22 answerable ones.
Use the report's "Threshold sweep" table to pick the thresholds; for a full sweep, run once with a low
`thresholds.refuse` first (see `eval/format.md`, "Threshold sweep"). Passing the folder
`--questions eval/smoke` also loads `refusal.yaml`; for the Gate 1 set alone, pass
`eval/smoke/questions.yaml` as above.

## Gate 3: code questions, cited ranges and latency

Gate 3 runs the same two files against the same space, after Trustbook has re-indexed the corpus with
the Phase 3 code chunker: add `--refresh` to the first run on a new image (it re-ingests every corpus
source already in the space and checks each new ingest job), or start from a new space. The corpus
folder from step 1 is also the checkout the cited ranges are checked against (`--code-root`):

```sh
uv run --project backend python eval/runner.py \
  --base-url http://localhost:8484 \
  --space "requests smoke" \
  --setup eval/smoke/corpus.yaml \
  --questions eval/smoke/questions.yaml \
  --questions eval/smoke/refusal.yaml \
  --report gate-3.md \
  --min-precision 0.9 --min-refusal 0.95 \
  --max-false-refusals-code 1 \
  --code-root ~/trustbook-eval/requests --min-ranges-valid 1.0 \
  --max-p95 60
```

On Windows, pass the same folder as `--code-root C:\Users\ludwig\trustbook-eval\requests` (the
`TRUSTBOOK_BROWSE_ROOT` folder plus `trustbook-eval\requests`).

The report adds:

- **False refusals per category** (`docs`, `code`, `multi`, `other`) and **false refusals on code
  questions**: `category: code` plus every question with an expected file locator on a code file
  (for example a `multi` question pinned to `src/requests/auth.py`). Gate 3 allows at most one
  (`--max-false-refusals-code 1`) across the smoke and refusal sets.
- **Cited ranges valid**: every citation with a file must name a file that exists in the checkout,
  with a line range inside it (`--min-ranges-valid 1.0` = 100%). Failures are listed with the
  reason under "Cited ranges".
- **p95 latency** is flagged "above 60 s" whenever it is; `--max-p95 60` also makes it a target. Drop
  `--max-p95` if latency is only tracked, not gated.

Gate 3 also keeps Gate 2's correct refusals ≥ 95% and citation precision ≥ 90%.

## Gate 3 rework: comparable runs, real-use run, reference model

Since the Gate 3 rework (D-055), every run sends `temperature 0, seed 42` with each question so that
runs can be compared. The report header says so, and also notes that some endpoints, such as
llama-server with several parallel slots, do not honour a seed. `--setup` also checks every source's
**ingest job**: a source that is ready while its job failed is reported as a "job/source mismatch",
and the run exits 1 (see `eval/format.md`, "Corpus file").

Jobs that ended before the run started are only noted, because they may come from an older image.
A space indexed by `phase-3-rc` has a failed job on every source (F1), even though each source was
indexed correctly. So the first run after pulling a new `-rc` image takes `--refresh`. It re-ingests
all five corpus sources (the four URLs are fetched again) and checks every new job on that image.
Later runs on the same image can leave `--refresh` out. Their report then says "N ended before this
run, not re-checked", and those jobs were already checked by the first run.

```sh
# The Gate 3 command above, three times (F4); keep each report and its .json sidecar.
# The first run re-ingests and checks every source on the image under test.
uv run --project backend python eval/runner.py … --refresh --report gate-3-run1.md
uv run --project backend python eval/runner.py … --report gate-3-run2.md

# One of the three is the real-use run: the app's own temperature 0.2, no seed (A).
uv run --project backend python eval/runner.py … --app-default --report gate-3-real-use.md

# The same gate with a stronger reference model (F6); the chat settings are restored afterwards.
uv run --project backend python eval/runner.py … \
  --chat-endpoint http://192.168.155.52:8080/v1 --chat-model Qwen3.8-27B-UD-Q4_K_XL \
  --report gate-3-reference.md

# Which differences come from the model, and which from the pipeline?
uv run --project backend python eval/runner.py --compare gate-3-run1.json gate-3-reference.json
```

`--chat-endpoint` takes any OpenAI-compatible base URL, saved as the `custom` preset for the run.
`--chat-model` takes the model name. The report header names the model and endpoint host actually
used. If your saved chat settings have an API key, an endpoint on another server is refused:
Trustbook would delete the key (keys never travel to another host), and the runner could not restore
it. `--compare` lists the questions whose outcome differs and whether their retrieval was the same
(then the model made the difference) or different (then the pipeline or the index did).

## Gate 3 with a second model family, in one command (G3-3)

`--also-model NAME` asks the same questions again with a comparison model after the main run, in the
same space and with the same settings, and adds its numbers to the same report: a side-by-side row per
model (precision, correct refusals, false refusals docs / code / multi, p50 / p95, cited ranges valid)
and a section with the comparison model's metrics and questions. They are **reported, not a
criterion**: the pass/fail verdicts and the exit code come from the main model only. The saved chat
settings are restored afterwards, also after an error or Ctrl-C.

```sh
uv run --project backend python eval/runner.py \
  --base-url http://localhost:8484 \
  --space "requests smoke" \
  --setup eval/smoke/corpus.yaml \
  --questions eval/smoke/questions.yaml \
  --questions eval/smoke/refusal.yaml \
  --report gate-3.md \
  --min-precision 0.9 --min-refusal 0.95 \
  --max-false-refusals-code 1 \
  --code-root ~/trustbook-eval/requests --min-ranges-valid 1.0 \
  --max-p95 60 \
  --also-model <second-model> --also-endpoint http://192.168.155.52:8080/v1
```

In the container (the Gate 3 command in `docs/GATES.md`), add the same two options:
`docker compose exec app python /app/eval/runner.py … --max-p95 60 --also-model <second-model>`.
Leave out `--also-endpoint` when the comparison model is served by the same endpoint as the main
model (for example a llama-server router or llama-swap that loads models by name). `--also-model` can
be repeated. A comparison model whose endpoint test fails, or a Ctrl-C during the comparison, still
writes the main report with the comparison marked `failed` or `interrupted` (see `eval/format.md`,
"Comparison models in the same run").
