# Trustbook — Build Specification v1

> **Trustbook — trust your notebook.**
> An open-source (Apache-2.0), local-first research notebook. Users add sources (documents, web pages, YouTube, code folders) into isolated *spaces* and ask questions. Every answer comes only from those sources, with citations a user can verify in one click.

This spec is complete for the POC (Phases 0–4). The builder (Claude Code) works through it without asking questions. Where something is not specified, apply **§15 Decision rules** and log the choice in `docs/DECISIONS.md`. The builder stops only at **gates** (§14).

---

## 1. Locked decisions

| # | Decision |
|---|---|
| 1 | Language: English only. Swedish is backlog. |
| 2 | First users: consultants, one space per customer engagement. |
| 3 | Single user, local, no login. Multi-user/SSO is backlog. |
| 4 | Every space has its own database: one folder per space. Deleting the folder deletes the space. Handover = zip the folder. |
| 5 | Runtime: hybrid. Bundled CPU models run locally; chat LLM (and later TTS) go to a user-configured endpoint. |
| 6 | Own orchestrator API (FastAPI) + own React web UI. Open Notebook is reference only, no code reuse. |
| 7 | Ownership: Ludwig's personal project. License **Apache-2.0**. |
| 8 | Bundled components and models must be MIT / Apache-2.0 / BSD / Unlicense. SearXNG (AGPL-3.0) is allowed only as an **unmodified, separate container**. |
| 9 | Packaging: containers first. OCI images + one `compose.yaml`; runs on Podman, Rancher Desktop, Docker Engine or Docker Desktop. UI at `http://localhost:8484`. |
| 10 | Mounts: user home **read-only** as browse root (`/host`), `~/Trustbook` read-write (`/data`). In-app "Link folder" picker; no symlinks. |
| 11 | Bundled models (baked into image, pinned per tag): embeddings, reranker, grounding checker, Whistle STT. Everything else is an endpoint. Bundling rule: bundle only if small and the core works worse without it. |
| 12 | Web search is core (discover/add sources). SearXNG bundled; Brave or Tavily key optional. Grounded chat never searches. |
| 13 | Strict grounding: six layers (§8). Failing sentences removed with a "Partially verified" badge; whole answer refused if >50% of sentences fail or the first sentence fails. |
| 14 | Sources are snapshots: extracted text always stored in the space; uploads keep originals; linked folders store path only; URLs/YouTube are dated snapshots refreshed on request. |
| 15 | Processing: one global job queue across all spaces, calibration on first start, estimates before big jobs, power setting Low/Normal/High. |
| 16 | Data flow: local/remote badge per capability; only top chunks are sent remotely; request log shows what went out; **no telemetry**. |
| 17 | Ideation tab: grill-me *pattern* (own implementation), "I have an idea" entry only, growing branch map, idea briefs that **never** become sources. |
| 18 | Studio outputs (podcasts, guides, mind maps, flashcards), analyzers, "Suggest ideas", Laya distillation: **out of POC scope**. |
| 19 | Testing: repo ships the test runner, question format and a public smoke set; Ludwig's real golden set stays private, outside the repo. |

---

## 2. Tech stack (exact)

**Backend** (`backend/`)
- Python 3.12, FastAPI, Uvicorn, Pydantic v2, SQLModel (on SQLite), httpx.
- Vector + full-text: **LanceDB** (embedded), one LanceDB dataset per space; full-text index via LanceDB's built-in FTS.
- Job queue: in-process worker pool backed by a **global SQLite** jobs table. No Redis.
- Tooling: `uv` for dependency management, `ruff` (lint + format), `mypy --strict` on `app/`, `pytest`.

**Frontend** (`frontend/`)
- React 19 + TypeScript + **Vite 8** (Rolldown bundler, the default), **pnpm 10** (installed with `npm install -g pnpm@10`, not corepack; pin `"packageManager": "pnpm@10.x"` in `package.json` and use the same version in CI and the Dockerfile).
- Tailwind CSS + shadcn/ui components, TanStack Query, React Router.
- Built to static files and served by the backend (single app container).
- Tooling: ESLint, Prettier, Vitest, Playwright (one end-to-end smoke test).

**Bundled models and tools** (all CPU)

| Capability | Default | Fallback if default fails to run or license is not as expected |
|---|---|---|
| Embeddings | `google/embeddinggemma-2` text encoder via sentence-transformers, 768-dim, **BF16/FP32, never FP16** | `BAAI/bge-small-en-v1.5` (MIT) |
| Reranker | `mixedbread-ai/mxbai-rerank-xsmall-v1` | `BAAI/bge-reranker-base` (MIT) |
| Grounding checker | `vectara/hallucination_evaluation_model` (HHEM-2.1-Open) | `cross-encoder/nli-deberta-v3-small` (entailment probability as score) |
| Speech-to-text | Whistle (Cactus Compute, Apache-2.0) via `cactus-needle` | whisper.cpp `base.en` via `pywhispercpp` (MIT) |
| Document parsing | Docling (PDF, DOCX, PPTX, XLSX) | `pypdf` text extraction for PDFs |
| Web extraction | Trafilatura | Crawl4AI (headless Chromium) when Trafilatura returns < 200 chars |
| YouTube | `yt-dlp` captions (manual, then auto) | Download audio → STT |
| Code parsing | `tree-sitter` + grammars for C#, TypeScript, JavaScript, Python | Generic fallback chunker (§7.3) |
| Exact code search | LanceDB FTS on code chunks + `ripgrep` for identifier lookups | — |
| Secrets scan | `gitleaks` binary | — |

The builder **verifies every model's license on its model card** before baking it in, and uses the fallback if it is not MIT/Apache-2.0/BSD. Record the result in `docs/DECISIONS.md` and `THIRD_PARTY_LICENSES.md`.

**Endpoint adapters** (`backend/app/capabilities/adapters/`)
- `openai_compatible` (OpenAI, Ollama, llama-server, vLLM, LM Studio, any `/v1/chat/completions`).
- `azure_openai`.
- `anthropic`.
- Search: `searxng`, `brave`, `tavily`.
- STT endpoint: OpenAI-compatible `/v1/audio/transcriptions`.

---

## 3. Repository layout

```
trustbook/
├── CLAUDE.md                  # builder instructions
├── SPEC.md                    # this file
├── README.md                  # user-facing install + usage
├── LICENSE                    # Apache-2.0
├── THIRD_PARTY_LICENSES.md
├── compose.yaml               # user install (pulls images from GHCR)
├── compose.dev.yaml           # local build for development
├── .env.example               # TRUSTBOOK_PORT, TRUSTBOOK_BROWSE_ROOT, TRUSTBOOK_DATA, TRUSTBOOK_IMAGE_TAG
├── docker/
│   ├── app.Dockerfile         # multi-stage: frontend build → python runtime + baked models
│   ├── searxng/settings.yml   # json format enabled, limiter off, safe engine list
│   └── download_models.py     # pins exact model revisions at build time
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py            # FastAPI app, static frontend, lifespan
│   │   ├── config.py          # paths, settings load/save
│   │   ├── db/                # global.db + per-space db models, migrations
│   │   ├── spaces/            # create, list, delete, manifest
│   │   ├── capabilities/      # registry, manifests, adapters, health checks
│   │   ├── jobs/              # global queue, workers, calibration, estimates
│   │   ├── ingest/            # per source type: upload, folder, url, youtube, search, paste
│   │   ├── parse/             # docling, trafilatura, transcripts, code (tree-sitter)
│   │   ├── chunking/
│   │   ├── index/             # embeddings, LanceDB, FTS
│   │   ├── retrieval/         # hybrid search, RRF, rerank, source filter
│   │   ├── grounding/         # six layers, verifier, code checks, badges
│   │   ├── chat/              # conversations, streaming, overview, suggestions
│   │   ├── ideation/          # grilling engine, branch map, briefs
│   │   ├── security/          # gitleaks integration
│   │   └── api/               # routers
│   └── tests/
├── frontend/
│   └── src/ (pages, components, api client)
├── eval/
│   ├── runner.py              # golden-set runner: citation precision, refusal rate, latency
│   ├── format.md              # question file format
│   └── smoke/                 # public smoke set (§13)
├── docs/
│   ├── DECISIONS.md           # builder's decision log
│   ├── ARCHITECTURE.md
│   └── GATES.md               # gate checklist results
└── .github/workflows/
    ├── ci.yml                 # lint, typecheck, tests (mock LLM) on every push and PR
    └── release.yml            # push to branch phase-N → image :phase-N-rc; tag phase-N → images :phase-N and :latest
```

---

## 4. Containers and install

**`compose.yaml`** (what Ludwig runs on his laptop):

```yaml
services:
  app:
    image: ghcr.io/${GITHUB_OWNER:?set GITHUB_OWNER in .env}/trustbook:${TRUSTBOOK_IMAGE_TAG:-latest}
    ports: ["${TRUSTBOOK_PORT:-8484}:8484"]
    volumes:
      - ${TRUSTBOOK_BROWSE_ROOT:?set TRUSTBOOK_BROWSE_ROOT in .env}:/host:ro
      - ${TRUSTBOOK_DATA:?set TRUSTBOOK_DATA in .env}:/data
    environment:
      - TRUSTBOOK_SEARXNG_URL=http://searxng:8080
    depends_on: [searxng]
    restart: unless-stopped
  searxng:
    image: docker.io/searxng/searxng:latest   # pin a digest at build time
    volumes: ["./docker/searxng/settings.yml:/etc/searxng/settings.yml:ro"]
    restart: unless-stopped
```

- The builder pins the SearXNG image to a digest in each release.
- The SearXNG settings file is copied into the app image and written out on first start if missing, so `compose.yaml` works when downloaded alone. Builder resolves this cleanly (for example a tiny init step) and documents it.
- Paths come from a `.env` file next to `compose.yaml` (never rely on `~` expansion, which differs on Windows). `.env.example` documents Windows (`C:/Users/<name>`) and Linux/macOS (`/home/<name>`) values. The README walks through creating `.env`.
- **Gate command on Ludwig's laptop** (Windows, PowerShell or any shell), with `TRUSTBOOK_IMAGE_TAG=phase-N-rc` in `.env`:
  ```
  docker compose pull
  docker compose up -d
  ```
  then open `http://localhost:8484`.
- Image targets `linux/amd64`. Add `linux/arm64` only if it builds without changes.

**Data folder (`/data`, i.e. `~/Trustbook`)**

```
~/Trustbook/
├── config/
│   ├── settings.json        # endpoints, API keys, power setting (never inside a space)
│   └── calibration.json
├── global.db                # job queue, request log
├── clones/                  # reserved for later git cloning
└── spaces/
    └── <space-slug>/
        ├── space.json       # manifest: id, name, description, embedding model + revision, created, schema version
        ├── space.db         # sources, chunks metadata, conversations, citations, gate log, ideation
        ├── vectors/         # LanceDB dataset
        ├── sources/<source-id>/
        │   ├── original.<ext>   # uploads only
        │   ├── snapshot.md      # extracted text
        │   └── map.json         # offsets → page / timestamp / file + line
        └── ideas/<session-id>/brief.md
```

API keys are stored in `config/settings.json` with file permissions 600. Never inside a space.

---

## 5. Data model (per-space `space.db`)

- **sources**: id, type (`upload|folder|url|youtube|search|paste`), title, origin (path/URL), snapshot_at, commit_hash (code), status (`queued|reading|indexing|ready|failed`), failure_reason, failure_next_step, quality_warning, enabled (checkbox), group (`documents|transcripts|web|code`), stats (pages, files, duration).
- **chunks**: id, source_id, ordinal, text, start_offset, end_offset, locator (JSON: page / timestamp / file + start_line + end_line + symbol), token_count.
- **conversations**, **messages** (role, content JSON with sentences and citations, badge, removed_sentences).
- **citations**: message_id, sentence_index, chunk_id, verifier_score.
- **gate_log**: gate, input, decision, confidence, user_correction, created.
- **ideation_sessions**, **ideation_nodes** (id, session_id, parent_id, title, status `tentative|open|resolved|dismissed`, question, recommended_answer, final_answer, answer_source `user|accepted|skipped`, citations).
- **request_log** lives in `global.db`: timestamp, capability, endpoint host, space, bytes/tokens sent, excerpt of what was sent.

Migrations: a simple numbered SQL migration runner; `space.json` records the schema version.

---

## 6. Capability registry

Each capability has a manifest in `backend/app/capabilities/manifests/*.yaml`:

```yaml
id: chat
label: Chat model
kind: endpoint            # endpoint | bundled | bundled_with_endpoint_override
required: true
contract: openai_chat     # adapter contract
locality: from_endpoint   # bundled → local; endpoint → local if host is localhost/127.0.0.1/host.docker.internal, else remote
ui_fields: [preset, base_url, api_key, model, api_version]
health_check: chat_ping   # send "Reply with OK", expect a reply within 30 s
```

**POC capabilities**

| id | kind | required | notes |
|---|---|---|---|
| `chat` | endpoint | yes | All generation: answers, overview, suggestions, ideation. JSON schema output; if unsupported, JSON mode + validation + one retry. |
| `gate` | endpoint (defaults to the `chat` config) | no | Router with a fixed-choice output. Logged to `gate_log`. |
| `embeddings` | bundled | — | Changing it requires re-indexing; never automatic. |
| `reranker` | bundled | — | |
| `grounding` | bundled, swappable | — | If disabled, every answer is badged "Unverified". |
| `stt` | bundled_with_endpoint_override | — | Whistle default; endpoint enables "Re-transcribe". |
| `web_search` | bundled (SearXNG) with Brave/Tavily override | — | |
| `tts` | endpoint | no | Manifest only, no POC feature uses it. Hidden in UI until studio outputs ship. |

Settings UI renders from manifests: one card per capability with fields, a **Test** button, and a local/remote badge. A capability without a working config is greyed out wherever it's needed, with "Set up in Settings".

---

## 7. Ingestion

### 7.1 Per source type
- **Upload**: store original → parse (Docling / text / audio → STT) → snapshot.
- **Link folder**: in-app picker over `/host` (read-only). Walk recursively, respect `.gitignore` and a default ignore list (`node_modules`, `bin`, `obj`, `dist`, `.git`, `venv`, `.venv`, `__pycache__`, binaries, files > 5 MB). Documents → parse; code files → §7.3. If `.git` exists, record HEAD commit. Store path only.
- **URL**: fetch → Trafilatura → Crawl4AI fallback → dated snapshot.
- **YouTube**: yt-dlp captions → else audio → STT. Snapshot with timestamps.
- **Web search**: query SearXNG (or override) → results list (title, URL, snippet) → user ticks → each becomes a URL source.
- **Paste text**: snapshot directly.

### 7.2 Chunking
- **Documents**: heading-aware; target 500 tokens, max 800, overlap 80; locator = page(s).
- **Transcripts**: group consecutive speaker turns up to ~500 tokens; never split mid-sentence; locator = start/end timestamp (+ speaker if known).
- **Web**: same as documents; locator = section heading + offset.
- **Code (tree-sitter languages)**: one chunk per function/method/class; prefix with file path and enclosing symbol; split symbols > 1,500 tokens at statement boundaries; module-level code grouped up to 500 tokens; locator = file + start_line + end_line + symbol.

### 7.3 Fallback code chunker
For other languages (AL, X++, SQL, YAML, …): split on blank-line-separated blocks and top-level brace or `procedure`/`trigger` keywords, target 400 tokens, max 800; locator = file + lines.

### 7.4 Secrets
Before indexing any folder: run `gitleaks detect --no-git --source <folder> --report-format json`. Files with findings are skipped; the source shows "N files skipped: possible secrets" with a report listing **file path, line and rule ID only — never the secret value**.

### 7.5 Processing UX
- Status flow `queued → reading → indexing → ready | failed`, progress for long jobs.
- Failures give cause and next step (scanned PDF without text, blocked site, no captions and no STT, etc.).
- Quality warnings: transcript produced by Whistle, OCR text, extracted text < 300 chars.
- Code sources show their commit; if the linked folder's HEAD differs, show "Indexed at <short>, folder is N commits ahead. Refresh?" (N via `git rev-list --count`).
- Refresh re-indexes only changed files (content hash per file).

---

## 8. Retrieval and strict grounding

**Retrieval**
1. Filter to the space's **enabled** sources.
2. Vector search top 40 + FTS search top 40 → Reciprocal Rank Fusion (k = 60).
3. Rerank the top 40 → keep the top 8 (max ~6,000 tokens of context).

**Six grounding layers**
1. **No web tool in chat.** The chat pipeline has no access to search or scraping.
2. **Refuse before generating** if the top reranker score < `refuse_threshold` (initial **0.15**, configurable, tuned at Gate 2). Reply: "Not in your sources" + suggestion to add sources about the question's topic.
3. **Forced citations.** The LLM returns JSON `{"sentences":[{"text":"…","chunk_ids":["c12","c40"]}]}`. Sentences without chunk_ids are dropped.
4. **Sentence verification.** Score each sentence against its cited chunks with the grounding checker; pass if max score ≥ `verify_threshold` (initial **0.5**). Failing sentences are removed; one regeneration attempt if more than half fail; then apply the refusal rule.
5. **Deterministic code checks.** Cited file/line ranges must exist at the indexed commit; every backticked identifier in a sentence must occur in its cited chunks (exact string match). Failure = sentence fails.
6. **Evaluation** via `eval/runner.py` (§13).

**Badges**: Verified · Partially verified (N statements removed; collapsed drawer "Unverified, not from your sources") · Refused · Unverified (grounding checker disabled).

**Refusal rule**: refuse the whole answer if > 50% of sentences fail or the first sentence fails.

**Prompt rules for the chat LLM**: answer only from the numbered chunks; if they don't contain the answer, say so; no outside knowledge; temperature 0.2.

---

## 9. Chat UX
- Answers stream (sentences are verified before display; stream at sentence granularity after verification).
- Inline numbered citations; clicking opens the **source viewer** at the highlighted passage (code: cited lines + commit).
- "Show sources used" chip under each answer.
- Chat box shows "Using X of Y sources".
- **Space overview**: generated automatically when the first source becomes ready and refreshed (on request) after more are added: a short grounded summary + **three suggested questions**. Toggle per space. Shows estimated tokens when the chat endpoint is remote.
- One-time hint: "Answers only come from your ticked sources. Ideas and brainstorming live in the Ideation tab."

---

## 10. Ideation tab
- Entry: "I have an idea" text box.
- Engine: **own implementation** of the grill-me pattern (interview one question at a time; each question has a recommended answer). Do not copy third-party prompt text; credit the pattern to Matt Pocock's grill-me in README.
- Recommendations cite the space where retrieval supports them (same retrieval + verification); otherwise labelled "suggestion".
- Users answer, accept the recommendation, or skip.
- **Growing branch map** beside the chat: seeded with 3–4 tentative branches (dashed), new branches added from answers, resolved = solid, dismissible seeds; progress as "X resolved, Y open"; complete when no open branches.
- "Finish now" writes the brief at any time; open branches become open questions. Sessions are saved and resumable.
- **Brief** (`ideas/<session>/brief.md`): idea, Q&A (accepted recommendations marked), cited anchors, open risks, open questions, map (Mermaid mindmap block). Export as Markdown. Briefs are **never** indexed as sources.

---

## 11. Processing, cost and data flow
- **Global queue**: workers = 1 (Low), 2 (Normal, default), max(2, cores − 2) (High). Active space's jobs get priority.
- **Calibration** (first start, ~10 s): embedding throughput (chunks/s), parse speed (pages/s), STT real-time factor. Stored in `calibration.json`.
- **Estimates** before adds above 2 minutes estimated: "About 4,200 files, roughly 25 min on this PC" → confirm or cancel.
- **Header indicator**: "Processing: N jobs, about M min left" → queue view across spaces with pause, reorder, cancel.
- **Remote token estimates** before the overview and ideation sessions when the endpoint is remote.
- **Request log** view in Settings.
- **No telemetry**, no update checks.

---

## 12. UI screens
1. **Setup wizard** (first start): chat endpoint (required) with Test → optional STT endpoint, search key → done.
2. **Spaces list**: create (name + optional description), open, delete (confirm, removes folder).
3. **Space**: left = sources panel (add source menu, groups with checkboxes, statuses); centre = chat; tabs **Chat | Ideation**.
4. **Source viewer** (drawer/panel).
5. **Settings**: capability cards from manifests, power setting, request log, re-index outdated spaces.
6. **Queue view**.

Design: clean, calm, information-dense; light and dark mode; responsive down to 1024 px (laptop-first).

---

## 13. Testing
- **CI (`ci.yml`)**: ruff, mypy, pytest (unit + integration with a **mock LLM adapter** returning deterministic JSON), ESLint, Vitest, Docker build check. No real endpoints in CI.
- **Eval runner** (`eval/runner.py`): input = question file(s) + a space; metrics = citation precision, correct-refusal rate, answer latency p50/p95. Output Markdown report to `docs/GATES.md`-ready format.
- **Question format** (`eval/format.md`, YAML): `question`, `type: answerable|unanswerable`, `expected_sources` (source titles), `expected_locators` (optional), `must_contain` (optional keywords).
- **Public smoke set** (`eval/smoke/`): corpus = the `psf/requests` repository (Apache-2.0) at a pinned commit + its documentation pages. 15 questions: 5 docs, 5 code, 2 multi-source, 3 unanswerable. Builder writes them and verifies each by hand against the corpus.
- **Private golden set**: runner accepts any path via `--questions`; nothing private lives in the repo.
- **Playwright e2e**: create space → paste text source → ask → see a cited answer (mock LLM).

---

## 14. Phases and gates

Git flow (never commit directly to `main`):

1. The builder works on branch `phase-N` and pushes regularly.
2. When the phase is done and CI is green, the builder opens a pull request `phase-N → main`, confirms `release.yml` has published `ghcr.io/ludwich/trustbook:phase-N-rc`, writes the gate checklist into `docs/GATES.md`, then **stops**.
3. Ludwig runs the gate command (§4) on his laptop and reports pass or fail.
4. On **pass**: the builder merges the PR (squash), tags `phase-N` on `main` (publishing `:phase-N` and `:latest`), and starts branch `phase-N+1`. On **fail**: the builder fixes on the same branch and the loop repeats.

Bootstrap exception: the very first commit (adding `SPEC.md`, `CLAUDE.md`, `LICENSE`) goes to `main` directly, before branch protection is turned on.

### Phase 0 — Foundations
Repo skeleton, CI, release pipeline, Dockerfile with baked models, compose, SearXNG config, settings + capability registry + adapters, setup wizard with Test, spaces CRUD, calibration, global queue skeleton, request log.
**Gate 0:** stack starts with one command; wizard Test succeeds against Ludwig's endpoint; every capability card shows correct status and badge; create/delete a space creates/removes its folder.

### Phase 1 — Grounded chat
All six source types, parsing, chunking, snapshots, processing UX (§7.5), hybrid retrieval, grounding layers 1–3, chat with streaming and citations, source checkboxes and groups, space overview + suggested questions, one-time hint, eval runner + smoke set.
**Gate 1:** citation precision ≥ 90% on the smoke set using Ludwig's chat endpoint.

### Phase 2 — Trust and flow
Grounding layers 4–6 (verifier, removal drawer, refusal rule, badges), source viewer, estimates + power setting + queue view, gate logging, private-set support in the runner, threshold tuning.
**Gate 2:** correct-refusal rate ≥ 95% on unanswerable questions, with citation precision still ≥ 90%.

### Phase 3 — Code
Link-folder code ingestion with tree-sitter + fallback chunker, gitleaks, commit recording and "ahead" banner, incremental refresh, deterministic code checks.
**Gate 3:** on the `requests` repo, 100% of cited file/line ranges exist at the indexed commit; secrets scan correctly skips a planted fake secret in a test folder.

### Phase 4 — Ideation
Ideation tab, grilling engine, growing branch map, resumable sessions, briefs + export.
**Gate 4:** a full session on a sample idea produces a brief with cited anchors and open questions; "Finish now" mid-session works; briefs never appear in chat citations.

---

## 15. Decision rules (when the spec is silent)
Priority order: **grounding correctness > data safety > simplicity > performance > features.**
- Prefer fewer moving parts (no extra services, no extra containers).
- If a dependency or model fails, use the listed fallback; if none is listed, choose the most widely used MIT/Apache option.
- Never weaken a grounding layer to make a test pass.
- Never send data to a remote endpoint that the user hasn't configured; never in tests.
- Never commit secrets, real customer data or private golden sets.
- UI copy: short, plain English, no jargon.
- Log every non-trivial choice in `docs/DECISIONS.md` (date, decision, reason, alternatives).

---

## 16. Out of scope for the POC
Studio outputs (podcasts, guides, FAQ, briefing, mind maps, flashcards), analyzers (dead code, security, versions, tests, ADR, C4, changelog), "Suggest ideas", deep research agent, Laya distillation, tags, saved selections, space templates, git host cloning, symbol graph, Swedish, Teams client, redaction, multi-user/SSO, auto-update.
