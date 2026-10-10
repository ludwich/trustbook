# Trustbook architecture

Trustbook is one app container (FastAPI backend + built React UI) plus an unmodified SearXNG container.
`SPEC.md` is the source of truth; this file records how the code is organised and the API contract
the backend and frontend share. Non-trivial choices are logged in `docs/DECISIONS.md`.

## Runtime layout

```
browser ──HTTP──▶ app container :8484
                   ├── FastAPI  /api/*            (backend/app)
                   ├── static UI  /*  (SPA fallback to index.html)
                   ├── worker pool (in-process, global SQLite queue)
                   ├── bundled CPU models (baked at /opt/trustbook/models, offline)
                   └── outbound: user-configured endpoints only (chat, gate, stt, search)
                             └── searxng container :8080 (internal network only)
```

Container paths:

| Path | Purpose | Env var |
|---|---|---|
| `/data` | `~/Trustbook` (read-write) | `TRUSTBOOK_DATA` (default `/data`; dev default `~/Trustbook`) |
| `/host` | user home, read-only browse root | `TRUSTBOOK_BROWSE_ROOT` (default `/host`) |
| `/opt/trustbook/models` | baked models + `models.lock.json` | `TRUSTBOOK_MODELS_DIR` |
| `/app/static` | built frontend | `TRUSTBOOK_STATIC_DIR` |
| — | SearXNG base URL | `TRUSTBOOK_SEARXNG_URL` (default `http://searxng:8080`) |
| — | image tag shown in UI | `TRUSTBOOK_VERSION` |
| `/etc/searxng` (init step) | where `searxng-init` writes `settings.yml` | `TRUSTBOOK_SEARXNG_CONFIG_DIR` |

Privacy env set in the image: `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1`,
`NEEDLE_TELEMETRY=0`, `DO_NOT_TRACK=1`, `OTEL_SDK_DISABLED=true`; `HF_MODULES_CACHE=/tmp/trustbook-hf-modules`
(HHEM's remote code is copied there because the models folder is read-only).

`TRUSTBOOK_MOCK_LLM=1` adds a `mock` chat preset that answers deterministically without network
(used by tests and the Playwright e2e test only; never set in `compose.yaml`).

## Data folder

Exactly as SPEC §4. `config/settings.json` is written atomically (temp file + rename) and chmod 600.
API keys exist only there. Space folders never contain keys.

## Backend modules (`backend/app`)

| Module | Responsibility |
|---|---|
| `config.py` | Paths from env, `Settings` pydantic model, load/save `settings.json`, key redaction helpers |
| `db/` | Numbered SQL migration runner; `global.db` (jobs, request_log, meta); `space.db` (full SPEC §5 schema) |
| `spaces/` | Slugify, create / list / get / rename / delete, `space.json` manifest |
| `capabilities/manifests/*.yaml` | One manifest per capability (SPEC §6) |
| `capabilities/registry.py` | Loads manifests, merges with saved config, computes status + locality |
| `capabilities/adapters/` | `openai_compatible`, `azure_openai`, `anthropic`, `searxng`, `brave`, `tavily`, `stt_openai`, `mock` |
| `capabilities/bundled.py` | Bundled model providers (embeddings, reranker, grounding, STT); lazy import of torch etc.; availability from `models.lock.json` |
| `capabilities/health.py` | Health checks (`chat_ping`, bundled model checks, search check, STT check) |
| `capabilities/http.py` | Shared outbound httpx client that writes every request to `request_log` |
| `jobs/` | Queue repository, worker pool (size from power setting), handler registry, calibration |
| `api/` | Routers, one per area |
| `main.py` | App factory, lifespan (migrations, worker pool, first-start calibration), static files |

Rules:
- Every outbound call to a configured endpoint goes through `capabilities/http.py`, which logs:
  timestamp, capability, endpoint host, space id, bytes sent, tokens sent (if known), excerpt (first 500 chars
  of the request body). Headers are never logged. API keys never appear in logs or responses.
- Bundled models are loaded lazily and cached per process; tests inject fakes.
- No network calls in tests: adapters take an injectable `httpx` transport.

## Locality rule

`bundled` → `local`. Endpoint → `local` if host is `localhost`, `127.0.0.1`, `::1` or `host.docker.internal`,
otherwise `remote` (LAN addresses count as remote: the data leaves this machine).

## API contract (all JSON, prefix `/api`)

Errors use FastAPI's `{"detail": "..."}` with proper status codes.

### Health and setup
- `GET /api/health` → `{"status":"ok","version":str}`
- `GET /api/setup` → `{"completed":bool,"chat_ready":bool}`
- `POST /api/setup/complete` → `{"completed":true,"chat_ready":bool}` (409 if chat is not configured)

### Settings
- `GET /api/settings` → `Settings`
- `PUT /api/settings` body `{"power"?: "low"|"normal"|"high"}` → `Settings`

```
Settings = {
  "power": "low"|"normal"|"high",
  "setup_completed": bool,
  "workers": int,                       // effective worker count for the power setting
  "thresholds": {"refuse": 0.15, "verify": 0.5}
}
```

### Capabilities
- `GET /api/capabilities` → `Capability[]` (hidden ones included with `hidden: true`; UI filters)
- `GET /api/capabilities/{id}` → `Capability`
- `PUT /api/capabilities/{id}` body `CapabilityConfigUpdate` → `Capability`
- `POST /api/capabilities/{id}/test` body `CapabilityConfigUpdate | {}` → `TestResult`
  Tests the saved config merged with the (optional, unsaved) body, so the wizard can test before saving.
  A missing `api_key` in the body means "use the saved key". The result is stored as `last_test`
  only when no draft body was given.
- `POST /api/capabilities/{id}/models` body `CapabilityConfigUpdate | {}` → `{"models":[str]}`
  Lists model ids the endpoint offers (OpenAI-compatible/Anthropic `GET /models`; Azure returns `[]`).
  Used by the wizard to suggest a model name. Errors → `502 {"detail": plain-English reason}`.

```
Capability = {
  "id": "chat"|"gate"|"embeddings"|"reranker"|"grounding"|"stt"|"web_search"|"tts",
  "label": str,
  "description": str,
  "kind": "endpoint"|"bundled"|"bundled_with_endpoint_override",
  "required": bool,
  "hidden": bool,
  "contract": str,
  "fields": Field[],               // rendered by the UI; depends on preset/provider/mode
  "presets": Preset[],             // chat/gate/stt endpoint presets; [] otherwise
  "providers": Option[],           // web_search: searxng|brave|tavily; [] otherwise
  "config": CapabilityConfig,
  "locality": "local"|"remote"|null,      // null when not configured
  "status": "ready"|"not_configured"|"error"|"disabled",
  "status_detail": str,                   // short plain-English reason
  "last_test": TestResult|null,
  "bundled": {"model_id":str,"revision":str,"license":str,"present":bool,"fallback":bool}|null
}
Field = {"name":str,"label":str,"type":"text"|"url"|"password"|"select"|"toggle",
         "required":bool,"placeholder"?:str,"help"?:str,"options"?:Option[]}
Option = {"value":str,"label":str}
Preset = {"id":str,"label":str,"adapter":"openai_compatible"|"azure_openai"|"anthropic"|"mock",
          "base_url":str|null,"needs_api_key":bool}
CapabilityConfig = {
  "enabled": bool,
  "preset": str|null, "base_url": str|null, "model": str|null, "api_version": str|null,
  "api_key_set": bool, "api_key_hint": str|null,      // e.g. "…a1b2"; never the key
  "use_chat": bool|null,                              // gate only: reuse the chat config
  "provider": "searxng"|"brave"|"tavily"|null,        // web_search only
  "mode": "bundled"|"endpoint"|null                    // stt only
}
CapabilityConfigUpdate = { any subset of: enabled, preset, base_url, model, api_version,
  api_key (string; "" clears), use_chat, provider, mode }
TestResult = {"ok":bool,"message":str,"latency_ms":int|null,"tested_at":str}   // ISO 8601 UTC
```

Chat presets: `openai`, `azure_openai`, `anthropic`, `ollama` (`http://host.docker.internal:11434/v1`),
`llama_server` (`http://host.docker.internal:8080/v1`), `lm_studio` (`http://host.docker.internal:1234/v1`),
`vllm`, `custom` (any OpenAI-compatible URL), and `mock` only when `TRUSTBOOK_MOCK_LLM=1`.

Status rules: endpoint capability without base_url/model (or key when the preset needs one) → `not_configured`;
last saved-config test failed → `error`; `enabled:false` → `disabled`; bundled model files missing → `error`;
otherwise `ready`. `gate` with `use_chat:true` mirrors chat. `grounding` disabled → `disabled`
(answers get the "Unverified" badge). `tts` is `hidden`.

### Spaces
- `GET /api/spaces` → `Space[]` (newest first)
- `POST /api/spaces` body `{"name":str (1–80 chars),"description"?:str (≤500)}` → `201 Space`
- `GET /api/spaces/{space_id}` → `Space`
- `PATCH /api/spaces/{space_id}` body `{"name"?,"description"?}` → `Space` (folder slug never changes)
- `DELETE /api/spaces/{space_id}` → `204` (cancels its jobs, removes the folder)
- `PUT /api/active-space` body `{"space_id":str|null}` → `204` (its jobs get queue priority)

```
Space = {"id":str,"slug":str,"name":str,"description":str,"created_at":str,
         "schema_version":int,"embedding":{"model":str,"revision":str},
         "folder":str,          // container path, e.g. /data/spaces/acme-audit
         "source_count":int}
```

### Jobs (global queue)
- `GET /api/jobs` → `{"jobs":Job[],"summary":QueueSummary}` (unfinished jobs + last 50 finished)
- `POST /api/jobs/{job_id}/cancel|pause|resume` → `Job`
- `POST /api/jobs/reorder` body `{"job_ids":[str]}` → `204` (new order for queued jobs)

```
Job = {"id":str,"kind":str,"title":str,"space_id":str|null,"space_name":str|null,
       "status":"queued"|"running"|"paused"|"done"|"failed"|"cancelled",
       "progress":float|null,"position":int,"error":str|null,
       "created_at":str,"started_at":str|null,"finished_at":str|null}
QueueSummary = {"queued":int,"running":int,"paused":int,"eta_seconds":int|null,"workers":int}
```

### Request log
- `GET /api/request-log?limit=100&before_id=` → `RequestLogEntry[]` newest first
- `DELETE /api/request-log` → `204`

```
RequestLogEntry = {"id":int,"timestamp":str,"capability":str,"endpoint_host":str,
                   "space_id":str|null,"space_name":str|null,"bytes_sent":int,
                   "tokens_sent":int|null,"excerpt":str}
```

### Calibration
- `GET /api/calibration` → `{"status":"pending"|"running"|"done"|"failed","results":Calibration|null}`
- `POST /api/calibration/run` → `202 Job`

```
Calibration = {"version":int,"measured_at":str,"cpu_count":int,      // version 3
               "embedding_chunks_per_s":float|null,"rerank_pairs_per_s":float|null,
               "parse_pages_per_s":float|null,"stt_realtime_factor":float|null,
               "notes":[str],
               "cpu":{"model":str|null,"logical":int,"usable":int,"physical":int|null,"bf16":bool}|null,
               "runtime":{"torch_threads":int|null,"embedding_dtype":"float32"|"bfloat16",
                          "reranker_dtype":"float32"|"bfloat16"}|null}
// stt_realtime_factor = processing seconds per audio second (UI shows "N× faster than real time").
// runtime is chosen by measurement and applied to the bundled models at startup (DECISIONS D-020).
```

## Phase 1: sources, index, retrieval and chat

### Modules

| Module | Responsibility |
|---|---|
| `ingest/` | One function per source type that turns an origin into a `Snapshot` (markdown text + offset map), plus the job handler that runs parse → chunk → embed → index and moves `status` along |
| `parse/` | Docling (PDF, DOCX, PPTX, XLSX) with pypdf fallback, plain text/Markdown, Trafilatura + headless-browser fallback, yt-dlp captions, audio → STT (with a voice-activity check) |
| `chunking/` | Document/web chunker (heading-aware, target 500, max 800, overlap 80 tokens), transcript chunker, fallback code chunker (SPEC §7.3) |
| `index/` | `SpaceIndex`: LanceDB dataset in `<space>/vectors/`, table `chunks` (chunk_id, source_id, text, vector), vector + FTS search filtered by source ids |
| `retrieval/` | Enabled-source filter → vector top 40 + FTS top 40 → RRF (k = 60) → rerank top 40 → top 8 within ~6,000 tokens |
| `chat/` | Conversations, prompt, forced-citation JSON (layer 3), refusal before generation (layer 2), SSE streaming, overview + suggested questions |
| `security/` | gitleaks scan of linked folders before indexing (pulled forward from Phase 3, DECISIONS D-024) |

Token counts use the embedding model's tokenizer when the ML runtime is present, else `ceil(chars / 4)`.

### Snapshot files (SPEC §4)

`sources/<source_id>/snapshot.md` is the extracted text. `map.json` maps character offsets in
`snapshot.md` to where they came from:

```
{"version": 1, "kind": "pages"|"timestamps"|"sections"|"files",
 "segments": [{"start": int, "end": int,            // offsets in snapshot.md
               "page"?: int, "start_s"?: float, "end_s"?: float, "speaker"?: str,
               "heading"?: str, "file"?: str, "start_line"?: int, "end_line"?: int}]}
```

A chunk's `locator` (JSON in `chunks.locator`) is derived from the segments it overlaps:
`{"pages":[3,4]}`, `{"start_s":61.2,"end_s":95.0,"speaker":null}`, `{"heading":"Install","offset":1234}`,
`{"file":"src/requests/api.py","start_line":12,"end_line":58,"symbol":null}`.

### Sources API
- `GET /api/spaces/{sid}/sources` → `Source[]` (creation order)
- `POST /api/spaces/{sid}/sources/paste` body `{"title"?: str, "text": str}` → `201 Source`
- `POST /api/spaces/{sid}/sources/upload` multipart `files` (one or more) → `201 Source[]`
- `POST /api/spaces/{sid}/sources/url` body `{"url": str}` → `201 Source` (YouTube URLs become `youtube` sources)
- `POST /api/spaces/{sid}/sources/folder` body `{"path": str}` (relative to the browse root) → `201 Source`
- `POST /api/spaces/{sid}/web-search` body `{"query": str, "limit"?: int}` → `{"results": [{"title","url","snippet"}]}`
- `POST /api/spaces/{sid}/sources/from-search` body `{"items": [{"url": str, "title": str}]}` → `201 Source[]` (type `search`)
- `PATCH /api/spaces/{sid}/sources/{source_id}` body `{"enabled"?: bool, "title"?: str}` → `Source`
- `PUT /api/spaces/{sid}/sources/enabled` body `{"source_ids": [str], "enabled": bool}` → `Source[]` (group checkboxes)
- `POST /api/spaces/{sid}/sources/{source_id}/refresh` → `Source` (url, youtube, folder: new dated snapshot)
- `DELETE /api/spaces/{sid}/sources/{source_id}` → `204` (cancels its job, removes its folder, chunks and vectors)
- `GET /api/spaces/{sid}/sources/{source_id}/snapshot` → `{"markdown": str, "map": Map}`
- `GET /api/spaces/{sid}/sources/{source_id}/original` → the uploaded file
- `GET /api/spaces/{sid}/chunks/{chunk_id}` → `{"id","source_id","source_title","text","start_offset","end_offset","locator"}`
- `GET /api/browse?path=` → `{"path": str, "parent": str|null, "entries": [{"name","path","is_dir","size"}]}`
  (paths relative to the read-only browse root; `..` and symlinks out of it are refused)

```
Source = {"id","type":"upload"|"folder"|"url"|"youtube"|"search"|"paste","title","origin":str|null,
          "group":"documents"|"transcripts"|"web"|"code",
          "status":"queued"|"reading"|"indexing"|"ready"|"failed","progress":float|null,
          "failure_reason":str|null,"failure_next_step":str|null,"quality_warning":str|null,
          "enabled":bool,"snapshot_at":str|null,"commit_hash":str|null,
          "stats":{"pages"?,"files"?,"duration_s"?,"chunks"?,"chars"?,"skipped_secrets"?},
          "created_at":str,"job_id":str|null}
```

Groups: uploads of audio, and YouTube → `transcripts`; url/search → `web`; folder code files → `code`;
everything else → `documents`. A folder with both documents and code becomes one source in the `code`
group if most files are code, else `documents`.

### Chat API
- `GET /api/spaces/{sid}/chat/status` → `{"enabled_sources": int, "ready_sources": int, "total_sources": int, "chat_ready": bool}`
  ("Using X of Y sources": X = enabled and ready, Y = ready)
- `GET /api/spaces/{sid}/conversations` → `[{"id","title","created_at","updated_at"}]` newest first
- `POST /api/spaces/{sid}/conversations` body `{"title"?: str}` → `201 Conversation`
- `DELETE /api/spaces/{sid}/conversations/{cid}` → `204`
- `GET /api/spaces/{sid}/conversations/{cid}/messages` → `Message[]`
- `POST /api/spaces/{sid}/conversations/{cid}/messages` body `{"content": str}` → `text/event-stream`:

| event | data |
|---|---|
| `status` | `{"stage": "retrieving"|"generating"|"checking"}` |
| `sentence` | `{"index": int, "text": str, "citations": Citation[]}` |
| `refused` | `{"reason": "not_in_sources"|"no_sources"|"invalid_output", "message": str, "suggestion": str|null}` |
| `done` | `{"message": Message}` |
| `error` | `{"message": str}` |

```
Citation = {"n": int, "chunk_id": str, "source_id": str, "source_title": str, "locator": object}
Message  = {"id","role":"user"|"assistant","created_at",
            "content": {"text"?: str,                               // user messages; refusal message
                        "suggestion"?: str|null,                    // refusals only
                        "sentences"?: [{"text": str, "citations": Citation[]}]},
            "badge": null|"verified"|"partially_verified"|"refused"|"unverified",
            "removed_sentences": [str], "sources_used": [{"source_id","title"}]}
```

Phase 1 has no sentence verifier (layer 4 is Phase 2): `badge` is `"refused"` for refusals, else `null`.
Every stream ends with `done` (also after `refused`, carrying the persisted refusal message) or `error`.
Refresh applies to `url`, `youtube`, `search` and `folder` sources. The browse root itself cannot be linked.
Citation numbers `n` are per answer, in order of first use.

Chat pipeline (layers 1–3): no search or scraping code is reachable from `chat/` (layer 1). The top
rerank score below `refuse_threshold` → `refused/not_in_sources` before any LLM call (layer 2). The LLM
gets numbered chunks `[c1]…[c8]`, temperature 0.2, and must return
`{"sentences":[{"text":"…","chunk_ids":["c1"]}]}` via JSON schema, else JSON mode + validation + one
retry (layer 3). Sentences without valid chunk ids are dropped. If none remain, the answer is refused
(`invalid_output`). A model that says the chunks don't answer the question returns `{"sentences": []}`,
which becomes `not_in_sources`.

### Overview API
- `GET /api/spaces/{sid}/overview` → `Overview`
- `POST /api/spaces/{sid}/overview/refresh` → `202 Job`
- `PUT /api/spaces/{sid}/overview` body `{"enabled": bool}` → `Overview`

```
Overview = {"enabled": bool, "status": "none"|"pending"|"ready"|"failed",
            "sentences": [{"text","citations":Citation[]}], "questions": [str],
            "generated_at": str|null, "stale": bool,          // sources changed since
            "estimate": {"tokens": int, "remote": bool}|null}  // shown when the chat endpoint is remote
```

The overview is generated automatically when the first source becomes ready (if enabled, default on),
with the same forced-citation format, plus exactly three suggested questions.

### Hints
`Settings` gains `"hints_dismissed": [str]`. `PUT /api/settings` accepts `{"dismiss_hint": str}`.
The chat hint id is `chat_grounding`.

## Frontend (`frontend/`)

Vite 8 + React 19 + TypeScript, Tailwind + shadcn/ui, TanStack Query, React Router. Built to
`frontend/dist`, copied into the image at `/app/static`. In dev, Vite proxies `/api` to `http://localhost:8484`.

Routes: `/setup` (wizard; shown until `setup.completed`), `/` (spaces list), `/spaces/:id` (space shell,
tabs Chat | Ideation), `/settings` (capability cards, power, request log), `/queue`.
Header: app name, "Processing: N jobs, about M min left" indicator (links to `/queue`), Settings link,
light/dark toggle.

## Commands (used by CI and the Dockerfile)

```
# backend
cd backend && uv sync --frozen
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict app && uv run pytest
uv run uvicorn app.main:app --host 0.0.0.0 --port 8484

# frontend (pnpm 10, pinned in package.json "packageManager")
cd frontend && pnpm install --frozen-lockfile
pnpm lint && pnpm typecheck && pnpm test && pnpm build      # → frontend/dist
```

## Containers

- `docker/app.Dockerfile`: stage 1 builds the frontend; stage 2 installs backend deps with uv
  (CPU torch) and runs `docker/download_models.py` to bake pinned model revisions into
  `/opt/trustbook/models`; runtime sets `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`.
- SearXNG settings: the app image contains `docker/searxng/settings.yml`. A one-shot `searxng-init`
  service (same app image, `python -m app.searxng_init`) writes it into a shared named volume with a
  generated `secret_key` if missing; `searxng` waits for it (`service_completed_successfully`).
  So `compose.yaml` works when downloaded alone.
- Entry point drops privileges to the owner of `/data` when that owner is not root, so files on a
  Linux host belong to the user (Windows/macOS bind mounts are unaffected).
