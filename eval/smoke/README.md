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
endpoint, and the corpus is in place. The runner needs Python 3.12 with `httpx` and `PyYAML`; the
backend environment has both:

```sh
uv run --project backend python eval/runner.py \
  --base-url http://localhost:8484 \
  --space "requests smoke" \
  --setup eval/smoke/corpus.yaml \
  --questions eval/smoke/questions.yaml \
  --report gate-1-smoke.md \
  --min-precision 0.9
```

Without uv: `pip install httpx pyyaml`, then `python eval/runner.py …` with the same arguments.

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
