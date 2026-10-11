# Gates

Each phase ends at a gate (SPEC §14). Ludwig verifies it on his laptop with the published `-rc` image and
reports **pass** or **fail**.

---

## Gate 0 — Foundations

**Image:** `ghcr.io/ludwich/trustbook:phase-0-rc` · **PR:** `phase-0 → main`

**Criteria (SPEC §14):** the stack starts with one command; the wizard Test succeeds against Ludwig's endpoint;
every capability card shows the correct status and badge; creating and deleting a space creates and removes its
folder.

### Setup (once)

1. Make a folder, e.g. `C:\Users\<you>\trustbook-gate`, and put two files from the `phase-0` branch in it:
   `compose.yaml` and `.env.example` (renamed to `.env`).
2. Edit `.env` (forward slashes, no `~`):
   ```
   GITHUB_OWNER=ludwich
   TRUSTBOOK_IMAGE_TAG=phase-0-rc
   TRUSTBOOK_PORT=8484
   TRUSTBOOK_BROWSE_ROOT=C:/Users/<you>
   TRUSTBOOK_DATA=C:/Users/<you>/Trustbook
   ```
3. Docker Desktop needs at least 4 GB of memory for its VM (Settings → Resources). The first pull is a few GB.

### Checklist

| # | Check | How to verify | Expected |
|---|---|---|---|
| 1 | Stack starts with one command | `docker compose pull` then `docker compose up -d`; then `docker compose ps -a` | `app` is `running (healthy)` within about a minute, `searxng` is `running`, `searxng-init` has `Exited (0)` |
| 2 | UI loads | Open <http://localhost:8484> | The setup wizard opens |
| 3 | Wizard Test succeeds | Step 1: choose your provider (llama-server: `http://192.168.155.52:8080`, or `http://host.docker.internal:8080` if it runs on the laptop). Press **Load models**, pick the model, press **Test** | Green result: "Connected. The model replied in N s." The first call may take up to ~20 s while the server loads the model. Then **Save**, keep the defaults in step 2, **Finish** |
| 4 | Capability cards: status | Open **Settings** | Chat model: Ready. Gate model: Ready ("Uses the chat model"). Embeddings, Reranker, Grounding checker, Speech-to-text: Ready. Web search: Ready ("SearXNG is running" after its startup check). Text-to-speech is hidden |
| 5 | Capability cards: badges | Same page | Chat model and Gate model: **Remote** for a LAN or cloud address, **Local** for `localhost` / `host.docker.internal`. All bundled models and built-in SearXNG: **Local** |
| 6 | Test on each card | Press **Test** on every card | Every card succeeds. Web search may report 0 results if the search engines rate-limit your IP; that still counts as working |
| 7 | Create space → folder | Spaces → **New space** "Gate test" → open it, then go back | Folder `C:\Users\<you>\Trustbook\spaces\gate-test\` exists with `space.json`, `space.db`, `sources\`, `vectors\`, `ideas\` |
| 8 | Delete space → folder gone | Delete "Gate test" (confirm) | The folder `spaces\gate-test` is gone |
| 9 | Keys stay out of spaces | Look in `C:\Users\<you>\Trustbook\config\settings.json` (if you used a key) and in the space folder | The key appears only in `config\settings.json` |
| 10 | Request log | Settings → **Request log** | One entry per Test you ran against the chat endpoint (host, bytes, excerpt "Reply with OK") and SearXNG; never an API key |
| 11 | Calibration | Settings → **Speed test** | Status done; embedding chunks/s, rerank pairs/s and speech-to-text speed are filled; parse speed is empty ("Document parser arrives in Phase 1") |

Optional: `docker compose down` then `docker compose up -d`. Settings, spaces and the request log are kept,
and calibration does not run again.

### Builder results (on the dev workbench, before handing over)

| Check | Result |
|---|---|
| CI on `phase-0` (ruff, mypy --strict, 216 pytest, ESLint, typecheck, 52 Vitest, image build check) | green |
| Full image build, offline model self-check (`bundled_check`: embeddings, reranker, grounding, STT transcribes the JFK clip exactly) | pass |
| `docker compose -f compose.dev.yaml up` → app healthy, searxng-init exits 0, SearXNG answers JSON | pass |
| Wizard Test against `http://192.168.155.52:8080` (Qwen3-8B, llama-server) | pass (16.9 s cold, 5.1 s warm) |
| All bundled Tests + SearXNG Test in the container | pass |
| Fresh install: **Load models** lists the llama-server models before any model is saved | pass |
| Headless-browser walk of the wizard, spaces, settings and queue at 1024 px and 1440 px, light and dark, against the real stack | pass (3 UI bugs found and fixed) |
| `ghcr.io/ludwich/trustbook:phase-0-rc` pullable anonymously (package is public) | pass |
| Space create/delete creates/removes `spaces/<slug>/` | pass |
| Files in the data folder owned by the host user; `settings.json` mode 600 | pass |

### Known issues (not gate blockers)

- STT speed in containers whose CPU limit is smaller than the CPUs they can see (e.g. `--cpus`, nested LXC) is
  much slower than it should be (D-019). On Docker Desktop this does not apply.
- Web search results depend on public engines; from some IPs DuckDuckGo and Brave rate-limit (D-015).

---

## Gate 1 — Grounded chat

**Image:** `ghcr.io/ludwich/trustbook:phase-1-rc` · **PR:** `phase-1 → main`

**Criterion (SPEC §14):** citation precision ≥ 90% on the smoke set using Ludwig's chat endpoint.

### Setup (once)

1. In `.env` set `TRUSTBOOK_IMAGE_TAG=phase-1-rc`, then `docker compose pull` and `docker compose up -d`.
   The image is bigger now (document parser, browser): about 3 GB to download, about 12 GB of free disk.
   Give Docker Desktop at least **8 GB of memory** (Settings → Resources).
2. Put the smoke corpus under your browse root (the folder must be named `requests`):
   ```
   git clone --depth 1 --branch v2.34.2 https://github.com/psf/requests C:/Users/<you>/trustbook-eval/requests
   ```
   (Commit `6e83187b`. Without git: `py eval/smoke/fetch_corpus.py C:/Users/<you>/trustbook-eval/requests`
   from a checkout of this repo.)
3. Wait until **Settings → Speed test** says done (first start, about 1–3 minutes).

### Checklist

| # | Check | How to verify | Expected |
|---|---|---|---|
| 1 | Gate 0 finding F1 fixed | Settings → Speed test → "This computer" line and Indexing | "uses N of N CPUs · T model threads · BF16" (BF16 if your CPU supports it). Indexing well above the 4 chunks/s you measured at Gate 0 |
| 2 | F2, F3 | Same section; power setting High | Speech-to-text reads "N× faster than real time"; High shows at most 8 workers |
| 3 | **Smoke set (gate)** | `docker compose exec app python /app/eval/runner.py --base-url http://127.0.0.1:8484 --space Smoke --setup /app/eval/smoke/corpus.yaml --questions /app/eval/smoke/questions.yaml --report /data/gate1-report.md --min-precision 0.9` | Exit code 0 and "Citation precision … pass" in `C:/Users/<you>/Trustbook/gate1-report.md`. The first run indexes the corpus (about 3,600 passages: roughly 5–25 min depending on the PC; progress is printed). Rerunning skips sources that are already there |
| 4 | Answers in the UI | Open space **Smoke** → ask "How many redirects does requests follow by default?" | A streamed answer with numbered citations; clicking one opens the source viewer at the highlighted lines of `src/requests/...` |
| 5 | Refusal | Ask "Does requests support WebSocket connections?" | "Not in your sources" with a suggestion, badge **Refused**, no LLM answer |
| 6 | Source types | In a new space: upload a PDF, add a web page, a YouTube link, paste text, search the web and add a result | Each reaches **Ready** (or **Failed** with a reason and next step); a scanned/OCR'd PDF shows a warning; a LAN address is refused |
| 7 | Overview and hint | The new space's chat tab | With a remote endpoint: "About N tokens will be sent…" + **Write overview** (nothing is sent before you click); afterwards 2–4 cited sentences and three suggested questions. The one-time hint can be dismissed and stays dismissed |
| 8 | Checkboxes | Untick a source or a whole group, ask again | "Using X of Y sources" changes; answers never cite unticked sources |

Report the chat model you used together with the report file.

### Builder results (dev workbench, 8 CPUs, before handing over)

Chat endpoint: llama-server at `192.168.155.52:8080`, model `Qwen3.6-35B-A3B-UD-Q4_K_XL`. Corpus indexed in
19 min (requests repo + 4 doc pages; BF16, 7 threads).

| Run | Answer key | Citation precision | Correct refusals | False refusals | p50 / p95 latency |
|---|---|---|---|---|---|
| 1 | as first written | 87.5% (21/24) | 3/3 | 0/12 | 24.8 s / 34.7 s |
| 2 | + 3 hand-verified passages | 92.0% (23/25) | 3/3 | 0/12 | 19.5 s / 31.2 s |
| 3 | + 2 hand-verified passages | **100.0% (24/24)** | 3/3 | 0/12 | 24.8 s / 50.3 s |

Every citation flagged as wrong in runs 1–2 was checked by hand. Each one stated the answer (a test asserting
session cookies are not persisted, the Quickstart line on dropping `Authorization` off-host, the
`get_adapter` prefix tests). So the answer key was widened, not the system; details are in
`eval/smoke/verification.md`. No grounding threshold or prompt was changed between runs.

Other checks:

| Check | Result |
|---|---|
| **Exact gate command (#3) on the published `phase-1-rc` image**, pulled anonymously and started with `compose.yaml` | **100.0% (23/23)**, refusals 3/3, exit 0 |
| CI on `phase-1` (ruff, mypy --strict, pytest, eval tests, ESLint, typecheck, Vitest, image build check) | green |
| Release pipeline: Playwright e2e (paste → ask → cited answer, real models, mock LLM) runs against the image before it is pushed | pass (GitHub runner and a fresh local container) |
| Every source type in the container: Docling PDF (11 pages, OCR warning), web page, JavaScript-only page via headless Chromium, YouTube captions, audio upload via Whistle, LAN URL refused | pass |
| Secret scan on the `requests` folder: 4 test TLS private keys (`tests/certs/**.key`) skipped, reported as file, line and rule ID (`private-key`) only; source shows "4 files skipped" | pass |
| Calibration v4 on the workbench: indexing 10.8 chunks/s (synthetic), ranking 18.4 pairs/s, BF16 for both, 7 threads | pass |

### Known limits (not gate blockers)

- Indexing cost grows with passage length: real code passages (~700 tokens) index at about 3.5/s on the
  workbench versus 10.8/s for the calibration text. Phase 2 adds time estimates before big adds.
- Doc-page questions use `requests.readthedocs.io/en/stable/`, built from v2.34.2 today. If a new requests
  release moves `stable`, re-check `verification.md`.
- The headless-browser fallback cannot run Chromium's sandbox inside the container (Docker's default
  seccomp). It renders only public pages, and private addresses are blocked (D-025).

---

## Gate 2 — Trust and flow

**Image:** `ghcr.io/ludwich/trustbook:phase-2-rc` · **PR:** `phase-2 → main`

**Criterion (SPEC §14):** correct-refusal rate ≥ 95% on unanswerable questions, with citation precision still
≥ 90%.

### Setup

1. In `.env` set `TRUSTBOOK_IMAGE_TAG=phase-2-rc`, then `docker compose pull` and `docker compose up -d`.
   The speed test runs again (version 5: it now also picks parallel indexing streams). Wait for it in
   **Settings → Speed test**.
2. The smoke corpus from Gate 1 stays where it is (`trustbook-eval/requests`). The `Smoke` space from Gate 1 can
   be reused, but delete it and let the runner rebuild it: Phase 2 no longer indexes generated files (the
   `.ai`/`.svg` logos were 93% of the old index).
3. Private golden set (optional, recommended): put the YAML file(s) under your data folder, for example
   `C:/Users/<you>/Trustbook/eval-private/`, which the container sees as `/data/eval-private/`. Nothing there is
   ever indexed or uploaded.

### Checklist

| # | Check | How to verify | Expected |
|---|---|---|---|
| 1 | **Refusal + precision (gate)** | `docker compose exec app python /app/eval/runner.py --base-url http://127.0.0.1:8484 --space Smoke --setup /app/eval/smoke/corpus.yaml --questions /app/eval/smoke/questions.yaml --questions /app/eval/smoke/refusal.yaml --report /data/gate2-report.md --min-precision 0.9 --min-refusal 0.95` (add `--questions /data/eval-private` for your set) | Exit code 0; in `Trustbook/gate2-report.md` correct-refusal ≥ 95% and precision ≥ 90% (pass). Also note "False refusals". `gate2-report.json` holds the per-question scores for tuning |
| 2 | G2 indexing speed | The `--setup` step above (fresh `Smoke` space) | `requests` indexes in minutes, not 29 min; Speed test shows "Indexing code" and "N streams" |
| 3 | Badges | Ask in `Smoke`: "What are the default values of pool_connections, pool_maxsize and pool_block?" | Answer badged **Verified** (10, 10, False). Some answers show **Partially verified**: open "Unverified, not from your sources" to see removed statements |
| 4 | Unverified | Settings → Grounding checker → turn off; ask again; turn it back on | Badge **Unverified** with the tooltip "Answer checking is turned off in Settings" |
| 5 | Refusal card (G5) | Ask "Does requests support WebSocket connections?" | Title "Not in your sources", subtitle a readable sentence quoting your question, no repetition |
| 6 | Source viewer (G6) | Click a citation into `src/requests/…` | Monospace, line numbers, exact highlighted lines, no flash of the top of the file |
| 7 | Secrets report (G4) | In `Smoke`, the `requests` source row | One "4 files skipped: possible secrets" button → report with file, line, rule ID (`private-key`), no values |
| 8 | Failed sources (G7) | Add a URL on your LAN, e.g. `http://192.168.1.1/` | Fails with the local-network message; its checkbox is unticked and disabled; not counted |
| 9 | Power labels (G1) | Settings → Power | High shows the real (capped) worker count, at most 8 |
| 10 | Web search (G8) | Search the web for "microsoft ab 731 certificate" | Relevant pages above bare homepages; no Swedish homepage on top |
| 11 | Estimates | Link a big folder (e.g. a large repo) | Before indexing: "About N files, roughly M min on this PC" with Add / Cancel; the header shows "about M min left" while it runs |
| 12 | Gate log | Ask "hello", then "Brainstorm ideas for a retry feature"; open the space's ⋮ menu → Gate log | "hello" gets a short reply without citations; the brainstorm answer shows the Ideation hint; both appear in the log and a decision can be corrected |
| 13 | Gate 1 items you deferred | PDF upload with OCR warning, overview + one-time hint, checkbox scoping | Covered by end-to-end tests (`frontend/e2e/`), run against this image before it was published; spot-check in the UI if you like |

### Builder results (dev workbench, before handing over)

Chat endpoint: llama-server `192.168.155.52:8080`, `Qwen3.6-35B-A3B-UD-Q4_K_XL`. 45 questions: the 15 smoke questions
plus `eval/smoke/refusal.yaml` (20 hard unanswerable + 10 answerable near-misses, each verified by hand,
`eval/smoke/refusal-verification.md`). Thresholds refuse 0.15 / verify 0.5 (unchanged; the sweep showed no
gain from moving them).

| Run | Change | Precision | Correct refusals | False refusals |
|---|---|---|---|---|
| A | layers 4–5 as first built | 95.7% (smoke only) | 3/3 | 3/12 (smoke) |
| B | + cited chunks scored together, qualified names by part (D-042); refuse 0 to collect sweep data | 91.2% | 23/23 | 6/22 |
| C | + focused premise, literal check (D-044) | 100% | 23/23 | 7/22 |
| D | + restated code facts (D-044) | 100% | 23/23 | 6/22 |
| **E** | + facts first in a capped window, literal check for value claims only | **97.4% (38/39)** | **23/23 (100%)** | **3/22** |

Run E, smoke questions: 11/12 answerable answered, all citations correct; 3/3 unanswerable refused.

**Remaining false refusals:**
- code-05 is a claim about what code does ("joins the username and password with a colon"). The prose-trained
  checker can't verify that from `b64encode(b":".join(...))`. It is a known limit, with Phase 3 options in
  `docs/BACKLOG.md`.
- ref-near-08 and ref-near-10: the model itself said the chunks don't answer (retrieval). Not a verification
  issue.

The run is cautious by design: grounding correctness comes first.

Other checks:

| Check | Result |
|---|---|
| All 7 end-to-end specs (smoke, PDF + OCR warning, overview + hint, checkbox scoping ×2, badges + gate chitchat) against the release image, real models + mock LLM | pass (3.3 min incl. calibration) |
| Gate router with a reasoning model: `think=False` for the gate call (D-043) | ~1 s per gate call; correct decisions |
| Estimates on `requests`: predicted 80 s vs real 79 s (8 CPUs) | pass |
| Indexing `requests` folder | 19 min (Phase 1) → ~1.3 min (generated files skipped, 4 streams) |
| CI on `phase-2` (ruff, mypy --strict, pytest, eval tests, ESLint, typecheck, Vitest, image build) | see PR checks |

### Known limits

- Claims describing code behaviour often can't be verified and get removed or refused (above).
- Latency: verification adds a grounding-checker pass. One retry when most sentences fail adds a second LLM
  call. p50 is about 20 s and p95 about 45 s on the workbench with your server.
- A web-page chunk's heading label is the heading where the chunk starts. A citation can therefore show the
  previous section's heading (seen once in run E).

---

## Gate 3 — Code

**Image:** `ghcr.io/ludwich/trustbook:phase-3-rc` · **PR:** `phase-3 → main`

**Criteria:**
- **SPEC §14:** on the `requests` repo, 100% of cited file/line ranges exist at the indexed commit, and the
  secrets scan skips a planted fake secret in a test folder.
- **Added by Ludwig at Gate 2:** at most **1 false refusal across all answerable code questions** (smoke +
  refusal sets), with correct refusals ≥ 95% and citation precision ≥ 90%.
- **Tracked:** false refusals per category, and p95 latency (flagged above 60 s).

### Setup
1. In `.env` set `TRUSTBOOK_IMAGE_TAG=phase-3-rc`, then `docker compose pull` and `docker compose up -d`. Let the
   speed test finish.
2. **Delete the `Smoke` space** so the runner re-indexes `requests` with the new code chunker (tree-sitter).

### Checklist

| # | Check | How to verify | Expected |
|---|---|---|---|
| 1 | **Gate command** | `docker compose exec app python /app/eval/runner.py --base-url http://127.0.0.1:8484 --space Smoke --setup /app/eval/smoke/corpus.yaml --questions /app/eval/smoke/questions.yaml --questions /app/eval/smoke/refusal.yaml --code-root /host/trustbook-eval/requests --report /data/gate3-report.md --min-precision 0.9 --min-refusal 0.95 --max-false-refusals-code 1 --min-ranges-valid 1.0 --max-p95 60` (add `--questions /data/eval-private` for your set) | Exit code 0. In `Trustbook/gate3-report.md`: cited ranges valid 100%, false refusals on code questions ≤ 1, refusals ≥ 95%, precision ≥ 90%, p95 ≤ 60 s, plus the per-category false refusal rows |
| 2 | Planted secret (SPEC Gate 3) | Make a folder under your home folder, e.g. `trustbook-eval/secrets-test/`, with a file `deploy.pem` containing a private-key block (`-----BEGIN RSA PRIVATE KEY-----` / any base64 line / `-----END RSA PRIVATE KEY-----`) and a normal `README.md`. Link the folder | Source ready with "1 file skipped: possible secrets". The report shows `deploy.pem`, the line and `private-key`, but no key text. Asking about the key is refused |
| 3 | Code chunks | Ask in `Smoke`: "What does `should_strip_auth` check?" and open a citation | The viewer header shows the file, the symbol (e.g. `SessionRedirectMixin.should_strip_auth`) and the exact lines |
| 4 | Code claims | Ask: "How does requests encode the username and password for Basic auth?" | A verified (or partially verified) answer naming the colon join and base64, citing `src/requests/auth.py` |
| 5 | Commits ahead | `git -C <your home>/trustbook-eval/requests commit --allow-empty -m test` (or pull new commits) | Within seconds the `requests` source shows "Indexed at 6e83187, folder is 1 commits ahead. Refresh?"; Refresh → "0 files changed, N unchanged", much faster than the first index |
| 6 | Code judge setting | Settings → Grounding checker → "Check code claims with the chat model" | On by default; switching it off makes code answers stricter (more refusals) |
| 7 | Gate 2 UI items #3–#12 | Already covered by end-to-end tests, run against this image before it was published (`frontend/e2e/gate2-*.spec.ts`) | Spot-check any you like |

Undo #5 with `git -C …/requests reset --hard HEAD~1`.

### Builder results (dev workbench, 8 CPUs, before handing over)

Chat endpoint: Ludwig's llama-server, `Qwen3.6-35B-A3B-UD-Q4_K_XL`. 45 questions (15 smoke + 30 refusal set). Fresh
`Smoke` space, re-indexed in 80 s (tree-sitter: 993 chunks).

| Run | Change | Precision | Refusals | Code false refusals | p95 |
|---|---|---|---|---|---|
| Gate 2 (Ludwig) | Phase 2 | 100% | 23/23 | 4/13 | 51 s |
| 3a | code judge + agreement guard + tree-sitter | 86.4% | 23/23 | 1/13 | 60.7 s |
| 3b | + fused-top guard, FTS without line numbers, citation pruning, section labels | crashed: out of memory (fixed, D-054) | | | |
| **3c** | + replicas released after indexing, small checker batches | **92.3% (36/39)** | **23/23** | **1/13** | **41.0 s** |

**Run 3c in detail:**
- False refusals: docs 0/10, code 1/10, multi 0/2.
- The one false refusal is ref-near-10 (`codes.too_many_requests`): the model said the cited chunks don't name
  429. The 747-token status table is one chunk.
- Cited ranges valid: 25/25 (100%).
- Wrong citations (3): two over-citations on docs-04 (Quickstart chunks that mention `.netrc` in passing) and a
  test chunk on code-05 that calls `_basic_auth_str`. All three support their sentence, but not the question.

Other checks:

| Check | Result |
|---|---|
| 18/18 end-to-end specs on the release image, incl. every Gate 2 UI item #3–#12, the planted secret (#7), the code viewer (#6) and the code-judge toggle | pass (3.2 min) |
| Planted secrets (AWS-key shape + private key, built at runtime) with real gitleaks 8.30.1 | skipped; report has file, line and rule only; the secret never appears in snapshot, chunks, API, report or logs |
| Code claims (code judge, real model) | 17/18 as expected; all 9 false claims failed (semicolon, SHA-1, pool_block True, …) |
| Memory during the run | 6.3 GB, no OOM |
| CI on `phase-3` | see PR checks |

### Round 2b: parent context and latency (D-077..D-080; to be filled at the gate)

Every run's report has a "Latency and judge calls" section (`eval/runner.py`, defined in `eval/format.md`); its
numbers go in this table. N is the answers that reached generation; answers refused before any answer call are
listed apart. "Over 60 s" is flagged in the report only when the computed p95 is over 60 s (at most 2 of 45 over
60 s guarantee p95 ≤ 60 s; 3 can still give a p95 under 60 s).

| Run | Model, temperature | Parent context / outline | Precision | Refusals | Code FR | Latency p50 / p95 | Over 60 s | Judge calls / answer: mean / p95 / max (0 / 1 / 2 / ≥3) | Judge s p50 / p95 (share) | Answer calls mean / max | Regenerations | First-call prompt tokens p50 / p95 | Promoted / outline tokens p95 | Outline-only failures (regenerated) | Promoted citations (correct) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| pre-gate on | Qwen3.6, 0 / seed 42, changed questions only | on / on | | | | | | | | | | | | | |
| pre-gate off | Qwen3.6, 0 / seed 42, changed questions only | off / off | | | | | | | | | | | | | |
| 1 | Qwen3.6, 0 / seed 42 | on / on | | | | | | | | | | | | | |
| 2 | Qwen3.6, 0 / seed 42 | on / on | | | | | | | | | | | | | |
| real-use | Qwen3.6, 0.2 (app default) | on / on | | | | | | | | | | | | | |
| reference | Qwen3.8-27B, 0 / seed 42 | on / on | | | | | | | | | | | | | |

**How to verify:** each row's numbers are in that run's report under "Latency and judge calls" (and the
`latency` block of its sidecar, version 4). The switches are `retrieval.parent_context` / `retrieval.outline`
(`PUT /api/settings` with `{"retrieval": {...}}`, no restart); the run's sidecar records them as
`retrieval_settings`. `--compare` lists questions whose outline alone changed separately ("outline only").
**If the computed p95 is over 60 s,** the levers are, in order and never a threshold (D-080): outline off if
outline-only failures caused regenerations; `EXPANSION_TOKENS` 900 → 500 and `EXPAND_TOP` 3 → 2; parent context
off (recorded in D-077).

**Pre-gate integration check (merged slices A and B; one check, not a gate run, see D-077 "Integration check").**
This was run live in `trustbook-g3` on a scratch copy of the Smoke space, re-chunked with `p3-treesitter-6`. It
used Qwen3.6 at temperature 0 with seed 42. Each question ran twice, in opposite order: run 1 was OFF then ON, run
2 was ON then OFF. "Promoted / outline" is the ON context. "prompt" is the estimated first-call prompt tokens
(characters / 4). Citations are the union over all four answers; every one is a child's own lines.

| Question | Promoted / outline tokens | Run 1 OFF | Run 1 ON | Run 2 ON | Run 2 OFF | Citations |
|---|---|---|---|---|---|---|
| ref-near-03 | 0 / 0 | verified, 22.4 s, judge 2, prompt 2901 | verified, 17.5 s, judge 2, prompt 2901 | verified, 34.0 s, judge 1, prompt 2901 | verified, 13.4 s, judge 2, prompt 2901 | HISTORY.md:159-199 |
| ref-near-10 | 0 / 20 | refused (regen), 20.6 s, judge 1, prompt 2005 | verified, 17.5 s, judge 1, prompt 2077 | refused (regen), 32.5 s, judge 1, prompt 2077 | verified (regen), 31.7 s, judge 2, prompt 2005 | status_codes.py:77-85 |
| code-02 | 1 / 113 | verified, 34.8 s, judge 3, prompt 1468 | verified, 41.0 s, judge 2, prompt 1821 | verified, 27.3 s, judge 2, prompt 1821 | verified, 41.8 s, judge 2, prompt 1468 | models.py:859-872 |
| code-05 | 1 / 119 | verified, 37.5 s, judge 5, prompt 2854 | verified, 41.5 s, judge 4, prompt 3048 | verified, 37.1 s, judge 4, prompt 3048 | verified, 37.3 s, judge 3, prompt 2854 | auth.py:34-75, auth.py:111-113 |
| docs-04 | 0 / 0 | verified, 22.1 s, judge 1, prompt 3540 | verified (regen), 74.0 s, judge 3, prompt 3540 | refused (regen), 82.6 s, judge 4, prompt 3540 | verified, 14.9 s, judge 1, prompt 3540 | authentication.rst:36-67, web "netrc Authentication" |
| ref-near-07 | 0 / 0 | verified, 16.9 s, judge 2, prompt 4348 | verified, 21.8 s, judge 1, prompt 4348 | verified, 22.9 s, judge 1, prompt 4348 | refused (regen), 39.5 s, judge 2, prompt 4348 | web "SOCKS", advanced.rst:661-703 |
| multi-02 | 0 / 0 | verified, 52.2 s, judge 2, prompt 2651 | partial, 43.2 s, judge 1, prompt 2651 | partial, 48.5 s, judge 3, prompt 2651 | partial, 41.5 s, judge 3, prompt 2651 | web "Transport Adapters", advanced.rst:968-1003, sessions.py:870-881 |
| code-01 | 2 / 209 | verified, 24.2 s, judge 1, prompt 3087 | verified, 23.8 s, judge 1, prompt 3638 | verified, 13.0 s, judge 1, prompt 3638 | verified, 24.3 s, judge 1, prompt 3087 | sessions.py:442-503 |
| code-03 | 0 / 113 | verified, 28.0 s, judge 1, prompt 2872 | verified, 36.7 s, judge 2, prompt 3013 | verified, 28.3 s, judge 2, prompt 3013 | verified, 25.4 s, judge 1, prompt 2872 | adapters.py:201-221, adapters.py:70-82 |
| code-04 | 1 / 116 | verified, 40.3 s, judge 2, prompt 3628 | verified, 42.9 s, judge 2, prompt 3832 | verified, 46.2 s, judge 2, prompt 3832 | verified, 36.6 s, judge 2, prompt 3628 | sessions.py:154-184, sessions.py:309-332, web "Custom Headers" |

Totals over the 20 answers per arm:
- **OFF:** p50 29.9 s, max 52.2 s, 0 answers over 60 s, 1.95 judge calls per answer (max 5).
- **ON:** p50 35.4 s, max 82.6 s, 2 answers over 60 s, 2.0 judge calls per answer (max 4).
- Each arm regenerated 3 times. No answer cited a promoted chunk, and `outline_only` was 0 for every answer.

Both answers over 60 s are docs-04, which gets no expansion. Its OFF and ON prompts are byte-identical, and four
direct calls with that prompt gave four different completions. The same holds for ref-near-03, ref-near-07 and
multi-02, so their OFF/ON differences are noise from llama-server, not effects of the expansion.

Claim replay (D-077): there were 0 false passes out of 41 base false claims, OFF and ON, with the real checker and
with forced confirmation. There were also 0 out of 33 outline-only and promoted-sibling false claims.

### Known limits
- Big data tables (e.g. `status_codes.py`) are single chunks, and answers about one entry can be missed
  (ref-near-10).
- Refresh time estimates still assume a full re-read (incremental refresh is much faster).
