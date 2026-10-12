# Gates

Each phase ends at a gate (SPEC §14). Ludwig verifies it on his laptop with the published `-rc` image and
reports **pass** or **fail**.

## Waiting for Ludwig (overnight run, 2026-10-12): Gate 3 → Gate 3b → Gate 4

Three gates are stacked, each on the one before. Nothing has been merged or tagged, and `main` is untouched. Test
them in this order. The full checklists are in the sections named below; each is also on its branch.

| # | Gate | Branch · PR | Image (`TRUSTBOOK_IMAGE_TAG`) | What to test | Section |
|---|---|---|---|---|---|
| 1 | **Gate 3: Code** (resubmitted after the failed gate) | `phase-3` · #4 → `main` | `phase-3-rc` | Delete `Smoke`, run the gate command (#1), and do checklist #2–#7. **Decide on the 14 citations outside the answer key** (Qwen3.8 reference run: 3 correct, 5 jointly correct, 6 off-question, 0 wrong); the exact cited text is listed for each. Final builder runs: 3 × Qwen3.6, each with precision 100%, 0 false refusals and p95 47.8–50.6 s | "Gate 3 — Code" |
| 2 | **Gate 3b: CLI for agents + transcript windows** | `phase-3b` · #5 → `phase-3` | `phase-3b-rc` | 10 PowerShell steps: the `trustbook` CLI in the container and pip-installed on Windows, JSON and exit codes (2 = not in sources, 3 = failed source), no keys in output, the Claude Code skill, transcript windows, Gate 3 still passing | "Gate 3b" |
| 3 | **Gate 4: Ideation** | `phase-4` · #6 → `phase-3b` | `phase-4-rc` | **Back up your Trustbook folder first** (spaces migrate to schema v6, which older images cannot open). A session in `Smoke` with cited recommendations, accept/answer/skip/dismiss, resume, Finish now mid-session, Export, a full session to Complete, and briefs never reaching chat | "Gate 4 — Ideation" |

**Decisions only you can make:**
- The approval list in Gate 3: accept or reject the 14 citations outside the answer key.
- The pass or fail of each gate.

Nothing else is blocked.

**After a pass:**
1. Gate 3: squash-merge #4 and tag `phase-3`.
2. Then I rebase `phase-3b` onto `main`, retarget #5 to `main`, and wait for your Gate 3b result.
3. Gate 4 follows the same way.

If a gate fails, I fix it on its own branch and rebase the branches stacked on it.

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

### Builder results: final runs (attempt 8, code `9708373`, image `phase-3-rc`)

**Setup:**
- **Machine:** dev workbench, 8 CPUs, no GPU. The chat model ran on a llama-server on the LAN, one model loaded at
  a time.
- **Data:** a fresh `Smoke` space, indexed by the gate command's `--setup` (`requests` at 6e83187 plus the four doc
  pages; 5/5 ingest jobs ended as expected).
- **Questions:** 45, made of 15 smoke questions and the 30-question refusal set. 22 are answerable (13 of them
  code questions) and 23 are not.
- **Answer key:** unchanged; nothing was widened.
- **Run settings:**
  - Runs 1 and 2: temperature 0 and seed 42 (the runner's default).
  - Real-use: `--app-default`, the app's own setting of temperature 0.2 with no seed.
  - Reference: `--chat-endpoint http://192.168.155.52:8080 --chat-model Qwen3.8-27B-UD-Q4_K_XL`, otherwise like
    run 1.
- **Nondeterminism:** llama-server is not deterministic even at temperature 0 with a fixed seed (identical prompts
  gave different completions, D-077). Each run is therefore one sample, and runs 1 and 2 differ.

| Run | Model | Precision | Correct refusals | False refusals docs / code / multi | Code false refusals (≤ 1) | p50 / p95 | Over 60 s | Cited ranges valid | Result |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Qwen3.6-35B-A3B UD-Q4_K_XL, 0 / seed 42 | **100% (38/38)** | **23/23** | 0/10 · 0/10 · 0/2 | **0/13** | 21.1 s / **48.6 s** | 1/45 | 26/26 | **pass** |
| 2 | Qwen3.6-35B-A3B UD-Q4_K_XL, 0 / seed 42 | **100% (36/36)** | **23/23** | 0/10 · 0/10 · 0/2 | **0/13** | 19.9 s / **47.8 s** | 0/45 | 27/27 | **pass** |
| real-use | Qwen3.6-35B-A3B UD-Q4_K_XL, 0.2 (app default) | **100% (39/39)** | **23/23** | 0/10 · 0/10 · 0/2 | **0/13** | 19.0 s / **50.6 s** | 1/45 | 26/26 | **pass** |
| reference | Qwen3.8-27B UD-Q4_K_XL (dense), 0 / seed 42 | 77.4% (48/62) | 23/23 | 0/10 · 0/10 · 0/2 | 0/13 | 35.0 s / 85.3 s | 11/44 | 45/45 | comparison only: fails precision and p95 |

The reference run has 44 timed answers: ref-near-05 got a 500 "proxy error" from the model server, which is not an answer. Its first attempt also failed: the server did not reply within 30 s while loading the model. It was re-run alone on the same data.

**The questions you named at the failed gate.** All three were answered in all three Qwen3.6 runs, and every
citation was inside the answer key:

| Question | Run 1 | Run 2 | Real-use |
|---|---|---|---|
| code-05 (Basic auth encoding) | verified, 77.7 s (regenerated, 9 judge calls) | verified, 46.0 s | verified, 45.5 s |
| ref-near-10 (`requests.codes` 429 names) | verified, 47.8 s (regenerated) | partially verified, 58.1 s (regenerated) | verified, 21.3 s |
| ref-near-07 (SOCKS DNS on the proxy) | verified, 25.3 s | verified, 25.0 s | verified, 33.5 s |

**Memory:** the container peaked at **6.91 GiB** during setup and run 1. Across attempts the peak was 5.0–7.1 GiB
and there were no OOMs. The README now says 8 GB minimum and 10 GB recommended, and shows how to set the WSL 2
limit (`.wslconfig`).

### Latency and judge calls (round 2b, D-077..D-080)

Every run's report has a "Latency and judge calls" section (`eval/runner.py`, defined in `eval/format.md`). Notes on
the table:
- N = 42: 3 unanswerable questions (`ref-unans-04`, `-05`, `-11`) were refused before any answer call.
- Parent context and the outline were **on** in every run (the defaults).

| Run | Judge calls / answer: mean / p50 / p95 / max | Answers with 0 / 1 / 2 / ≥3 judge calls | Judge s p50 / p95 (share of answer time) | Answer calls mean / max | Regenerations | First-call prompt tokens p50 / p95 | Promoted / outline tokens p95 | Outline-only failures | Promoted citations |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.95 / 1 / 3 / 9 | 20 / 14 / 4 / 4 | 1.8 s / 8.2 s (10.8%) | 1.05 / 2 | 2/42 | 3160 / 4416 | 217 / 217 | 0 | 0 |
| 2 | 0.93 / 1 / 3 / 4 | 20 / 13 / 3 / 6 | 1.9 s / 8.0 s (9.5%) | 1.05 / 2 | 2/42 | 3160 / 4416 | 217 / 217 | 0 | 0 |
| real-use | 0.93 / 1 / 3 / 4 | 20 / 11 / 6 / 5 | 2.4 s / 8.2 s (10.5%) | 1.02 / 2 | 1/42 | 3160 / 4416 | 217 / 217 | 0 | 0 |
| reference | 1.20 / 1 / 4 / 6 | 20 / 8 / 4 / 9 | 6.1 s / 33.8 s (22.1%) | 1.05 / 2 | 1/41 | 3136 / 4348 | 226 / 217 | 0 | 0 |

**Answers over 60 s:**
- Run 1, code-05: 77.7 s. Generation took 49.8 s in 2 calls (regenerated), verification 25.3 s, of which the judge
  took 21.9 s in 9 calls.
- Real-use, docs-03: 65.4 s. Generation took 52.3 s in 2 calls (regenerated), and the judge 7.8 s in 2 calls.

Both are regenerations, and at most 2 of 45 answers over 60 s keep p95 ≤ 60 s. None of the levers in D-080 was
needed.

**How to verify:** each row is in that run's report under "Latency and judge calls", and in the `latency` block of
its sidecar JSON.

### Citations outside the answer key: for your approval

The three Qwen3.6 runs have **no** citation outside the answer key (precision 100%), so there is nothing to approve
for them. The Qwen3.8-27B reference run's citations outside the key are listed below.
- **How they were checked:** each was reviewed strictly against its sentence and the source at 6e83187.
- **Effect on the score:** they count as wrong in its precision. The key was not changed.
- **Your decision:** whether to accept these as correct is yours to make.

**Summary:** 14 citations. **3 correct** on their own, **5 jointly correct** with the sentence's other citations, **6 correct but about something the question did not ask**, and **0 wrong**. The same review on attempt 7 found 3 wrong ones. One was the `__bool__` citation that D-086 fixed. The other two became joint because the sentences now also cite the chunk that completes them.

#### CORRECT (3)

- **`code-02 [4]`** · `docs/user/quickstart.rst:369-404` (lines 390-402 matter)
  · Sentence: "The quickstart shows that a 404 response triggers raise_for_status() to raise requests.exceptions.HTTPError, while a 200 response makes raise_for_status() return None."
  · Cited: `>>> bad_r = requests.get('https://httpbin.org/status/404')` … `>>> bad_r.raise_for_status()` … `requests.exceptions.HTTPError: 404 Client Error` … `since our ``status_code`` for ``r`` was ``200``, when we call ``raise_for_status()`` we get::` / `>>> r.raise_for_status()` / `None`
  · Reason: The chunk states both outcomes exactly. The sentence is on-question because `ok` calls `raise_for_status()`, as the previous sentence states, citing in-key [1].

- **`ref-near-04 [2]`** · `tests/test_requests.py:2165-2172`
  · Sentence: "The test test_unconsumed_session_response_closes_connection confirms that closing a stream=True session response via contextlib.closing sets response.raw.closed to True even when response._content_consumed is False."
  · Cited: `s = requests.session()` / `with contextlib.closing(s.get(httpbin("stream/4"), stream=True)) as response:` / `assert response._content_consumed is False` / `assert response.raw.closed`
  · Reason: Every element is stated. It answers the question: closing releases the connection even when the body was never read.

- **`ref-near-04 [3]`** · `tests/test_requests.py:2159-2163`
  · Sentence: "The test test_response_context_manager confirms that exiting a with block on a stream=True response sets response.raw.closed to True."
  · Cited: `with requests.get(httpbin("stream/4"), stream=True) as response:` / `assert response.raw.closed`
  · Reason: Stated exactly. The sentence does not claim a Session. It is on-question because leaving the `with` block releases the connection.

#### JOINT (5)

- **`docs-04 [2]`** · `src/requests/utils.py:231-280` (lines 239-243 matter)
  · Sentence: "In the source code, if the NETRC environment variable is set, Requests uses only that path; otherwise it iterates over the NETRC_FILES tuple (".netrc", "_netrc") prefixed with the home directory."
  · Cited: `netrc_file = os.environ.get("NETRC")` / `if netrc_file is not None:` / `    netrc_locations = (netrc_file,)` / `else:` / `    netrc_locations = (f"~/{f}" for f in NETRC_FILES)`
  · Reason: This chunk gives the NETRC-only rule and the `~/` prefix. The tuple values come only from [3], so the sentence is supported by **[2]+[3]**.

- **`docs-04 [3]`** · `src/requests/utils.py:71-146` (line 78 matters)
  · Sentence: (same as `docs-04 [2]`)
  · Cited: `NETRC_FILES: Final = (".netrc", "_netrc")`
  · Reason: This chunk only defines the tuple. The env-var rule and the home prefix come from [2], so the sentence is supported by **[2]+[3]**.

- **`code-02 [2]`** · `src/requests/models.py:835-843`
  · Sentence: "Response.__bool__ and Response.__nonzero__ both simply return self.ok, so a Response object is truthy whenever the status code is less than 400."
  · Cited: `def __bool__(self) -> bool:` / `"""Returns True if :attr:`status_code` is less than 400.` … `return self.ok`
  · Reason: This chunk covers `__bool__`, which is what makes a Response truthy, and the "< 400" rule. `__nonzero__` comes only from [3], so the sentence is supported by **[2]+[3]**. Note: the sentence is about truthiness and is only next to the question's subject, which is how `ok` is computed.

- **`code-02 [3]`** · `src/requests/models.py:845-853`
  · Sentence: (same as `code-02 [2]`)
  · Cited: `def __nonzero__(self) -> bool:` … `return self.ok`
  · Reason: This chunk covers `__nonzero__` only. The `__bool__` method, and with it truthiness, comes from [2], so the sentence is supported by **[2]+[3]**.

- **`ref-near-01 [3]`** · `src/requests/models.py:912-973` (lines 937, 949 matter)
  · Sentence: "iter_lines() passes that chunk_size to iter_content(), which uses it as the size for each read from the raw stream (self.raw.stream(chunk_size, ...) or self.raw.read(chunk_size))."
  · Cited: `yield from self.raw.stream(chunk_size, decode_content=True)` … `chunk = self.raw.read(chunk_size)`
  · Reason: This chunk gives the read size. The handoff `self.iter_content(chunk_size=chunk_size, ...)` is in the in-key **[1]** (`models.py:1008-1009`). The sentence is supported by **[1]+[3]**. In a7 this pair was WRONG; the in-key [1] is now cited.

#### OFF-QUESTION (6)

- **`docs-03 [5]`** · `src/requests/sessions.py:752-829` (lines 793-799 matter)
  · Sentence: "Cookies received in responses are automatically extracted and stored in the session's cookie jar via extract_cookies_to_jar."
  · Cited: `# Persist cookies` … `extract_cookies_to_jar(self.cookies, request, r.raw)`
  · Reason: Supported, but the sentence is about cookies the server sets. The question asks about cookies passed to one request, which are not persisted.

- **`docs-03 [6]`** · `tests/test_requests.py:390-394`
  · Sentence: "Cookies set by a response (e.g., via a /cookies/set endpoint) are persisted in the session and sent on subsequent requests, as shown by test_cookie_sent_on_redirect."
  · Cited: `s.get(httpbin("cookies/set?foo=bar"))` / `r = s.get(httpbin("redirect/1"))` / `assert "Cookie" in r.json()["headers"]`
  · Reason: Supported, but the sentence is about server-set cookies, not cookies passed per request.

- **`docs-05 [5]`** · `HISTORY.md:360-412` (lines 360-366 matter)
  · Sentence: "Requests added Brotli compression support in version 2.26.0 (2021-07-13)."
  · Cited: `2.26.0 (2021-07-13)` … `- Requests now supports Brotli compression, if either the `brotli` or` / `` `brotlicffi` package is installed. (#5783)``
  · Reason: The version and date are stated. The question asks which encodings are decoded and what to install, and the sentence gives a version history fact instead.

- **`code-01 [3]`** · `docs/user/quickstart.rst:552-567` (lines 565-566 matter)
  · Sentence: "If a request exceeds this limit, a TooManyRedirects exception is raised."
  · Cited: "If a request exceeds the configured number of maximum redirections, a `TooManyRedirects` exception is raised."
  · Reason: Supported, and also by the in-key [1] (`sessions.py:484-485`). It only restates the question's premise; the default limit is given in the previous sentence.

- **`code-05 [3]`** · `src/requests/auth.py:85-89`
  · Sentence: "The `HTTPBasicAuth` class declares its `username` and `password` attributes with the type `bytes | str`."
  · Cited: `username: bytes | str` / `password: bytes | str`
  · Reason: The type annotations are stated exactly, but they say nothing about the encoding, which is latin1 then base64 in `_basic_auth_str`.

- **`code-05 [4]`** · `tests/test_requests.py:554-570`
  · Sentence: "The test `test_set_basicauth` verifies that for inputs such as `("user", "pass")`, `("имя".encode(), "пароль".encode())`, `(42, 42)`, and `(None, None)`, the prepared request's `Authorization` header equals `_basic_auth_str(username, password)`."
  · Cited: `("user", "pass"),` / `("имя".encode(), "пароль".encode()),` / `(42, 42),` / `(None, None),` … `assert p.headers["Authorization"] == _basic_auth_str(username, password)`
  · Reason: Every input and the assertion are stated, but the sentence names only the helper, not the encoding the question asks about.

#### WRONG (0)

None.


#### All 14 at a glance

| id | [n] | location | verdict |
|---|---|---|---|
| docs-03 | [5] | src/requests/sessions.py:793-799 | OFF-QUESTION |
| docs-03 | [6] | tests/test_requests.py:390-394 | OFF-QUESTION |
| docs-04 | [2] | src/requests/utils.py:239-243 | JOINT (with [3]) |
| docs-04 | [3] | src/requests/utils.py:78 | JOINT (with [2]) |
| docs-05 | [5] | HISTORY.md:360-366 | OFF-QUESTION |
| code-01 | [3] | docs/user/quickstart.rst:565-566 | OFF-QUESTION |
| code-02 | [2] | src/requests/models.py:835-843 | JOINT (with [3]) |
| code-02 | [3] | src/requests/models.py:845-853 | JOINT (with [2]) |
| code-02 | [4] | docs/user/quickstart.rst:390-402 | CORRECT |
| code-05 | [3] | src/requests/auth.py:85-89 | OFF-QUESTION |
| code-05 | [4] | tests/test_requests.py:557-560, 570 | OFF-QUESTION |
| ref-near-01 | [3] | src/requests/models.py:937, 949 | JOINT (with in-key [1]) |
| ref-near-04 | [2] | tests/test_requests.py:2165-2172 | CORRECT |
| ref-near-04 | [3] | tests/test_requests.py:2159-2163 | CORRECT |

**Counts:** CORRECT 3 · JOINT 5 · OFF-QUESTION 6 · WRONG 0.

### Attempt history (round 2c, after the failed Gate 3)

Each attempt ran all four runs on a fresh image from the branch head. Every fix is logged in `docs/DECISIONS.md`;
none loosened a threshold, a layer or the answer key.

| Attempt | Image (commit) | Runs 1 / 2 / real-use: code false refusals | Precision | p95 | Qwen3.8 reference | What it found → fix |
|---|---|---|---|---|---|---|
| 3 | p3h (93b4419) | **2** / 0 / 0 (code-04, ref-near-10) | — | — | 77.8% | a failed rescue's feedback did not say where a name is; the judge named lines for only one excerpt → **D-082** |
| 4 | p3i (36d17db) | 0 / **1** / 0 | 100% | 42.7 / 42.6 / 44.6 s | 79.4%, p95 84.5 s | ref-near-10 used ``double backticks``, read as two spans → **D-083** |
| 5 | p3j (cabb3de) | 0 / 0 / 0 (run 2: 1 docs, ref-near-07) | 97.7 / 100 / 100% | 50.0 / 46.1 / 42.1 s | 79.0%, p95 86.6 s | the reference cited `Response.__bool__` without backticks to a chunk without it, and layer 5 checked only backticked names → **D-084** |
| 6 | p3k (75fc3f8) | **1** / 0 / 0 (ref-near-10) | 100 / 100 / 97.4% | 41.1 / 43.1 / 41.2 s | 79.0%, p95 89.2 s | the judge said "supported" but its lines lacked `requests.codes`, which a cited chunk holds → **D-085** (ask once for those lines) |
| 7 | p3l (eae63e0) | 0 / 0 / 0 (no false refusals at all) | 100 / 100 / 100% | 41.5 / 44.5 / 47.7 s | 77.8%, p95 88.7 s | the citation choice dropped the `__bool__` chunk after verification; its guard was an old copy of layer 5 → **D-086** |
| **8** | **p3m (9708373)** | **0 / 0 / 0 (no false refusals at all)** | **100 / 100 / 100%** | **48.6 / 47.8 / 50.6 s** | 77.4%, p95 85.3 s; 0 wrong citations | final |

Attempt 6 passed the criteria. The fixes after it addressed the question you named (ref-near-10) and a citation
defect found in the reference run.

**Claim replay after each grounding change** (trustbook-g3, Qwen3.6, as recorded in D-082..D-085):
- 0 of 41 base false claims passed, with forced confirmation and with the real checker.
- 0 of 33 promoted-sibling and outline-only false claims passed.
- True claims passed: 20 of 21 forced, 19 of 21 real, and 4 of 4 extra.

### Earlier builder runs (before round 2)

Before the failed gate (attempt 1 was your Gate 3 run):
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

### Known limits
- **Prose rescue.** A correct paraphrase of technical prose that HHEM scores below 0.5 (ref-near-07 in attempt 5,
  run 2: 0.36) fails. The judge rescues only sentences that cite code (D-064), because for prose the deterministic
  rescue gates cannot tell a negated or role-swapped claim from a true one. Expect an occasional docs false refusal.
- **Answer key and other supporting places.** Precision counts only citations into places the answer key lists. A
  model that also cites supporting tests or implementation (Qwen3.8-27B) scores lower without being wrong; see
  "Citations outside the answer key".
- **Nondeterminism.** llama-server answers differ from run to run even at temperature 0 with seed 42. A question
  that passes in one run can regenerate or come out partially verified in the next (ref-near-10: verified,
  partially verified, verified).
- **Refresh estimates.** Refresh time estimates still assume a full re-read (incremental refresh is much faster).
