# Phase 0 — Foundations (branch `phase-0`)

Gate 0: stack starts with one command; wizard Test succeeds against Ludwig's endpoint;
every capability card shows correct status and badge; create/delete a space creates/removes its folder.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done

## 0. Planning
- [x] Read CLAUDE.md + SPEC.md, create branch `phase-0`
- [x] Write this task list
- [x] `docs/ARCHITECTURE.md` (layout, API contract, data folder, container design)
- [x] `docs/DECISIONS.md` (license checks, Phase 0 scope calls)

## 1. Model + license verification
- [x] Verify licenses on model cards (embeddings, reranker, grounding, STT) and record revisions
- [x] Prove each default loads on CPU with pinned package versions (or switch to fallback)
- [x] `docker/download_models.py` pins exact revisions, downloads into the image
- [x] `THIRD_PARTY_LICENSES.md`

## 2. Backend (`backend/`)
- [x] `pyproject.toml` (uv), ruff, mypy --strict, pytest config
- [x] `app/config.py`: paths, `settings.json` load/save (atomic, mode 600), key redaction
- [x] `app/db/`: numbered SQL migration runner; `global.db` (jobs, request_log); `space.db` full §5 schema
- [x] `app/spaces/`: create / list / get / delete, `space.json` manifest, folder layout
- [x] `app/capabilities/`: YAML manifests (chat, gate, embeddings, reranker, grounding, stt, web_search, tts), registry, locality, status
- [x] Adapters: openai_compatible, azure_openai, anthropic, searxng, brave, tavily, stt endpoint, mock
- [x] Health checks per capability (chat_ping, bundled model checks, search, stt)
- [x] Request log: every outbound endpoint call recorded (host, capability, space, bytes, excerpt; never keys)
- [x] `app/jobs/`: global SQLite queue, worker pool sized by power setting, active-space priority, pause/cancel/reorder
- [x] Calibration on first start → `config/calibration.json`
- [x] `app/api/`: health, setup status, settings, capabilities (+ Test), spaces, jobs, request log, calibration
- [x] `app/main.py`: lifespan, static frontend with SPA fallback
- [x] Tests: unit for every module, integration for every route (mock adapter, no network)

## 3. Frontend (`frontend/`)
- [x] Vite 8 + React 19 + TS + Tailwind + shadcn/ui + TanStack Query + React Router, pnpm 10 pinned
- [x] ESLint, Prettier, Vitest
- [x] Setup wizard: chat endpoint + Test → optional STT endpoint, search key → done
- [x] Spaces list: create, open, delete (confirm)
- [x] Space page shell: sources panel (empty), Chat | Ideation tabs (placeholders)
- [x] Settings: capability cards from manifests (fields, Test, status, local/remote badge), power setting, request log
- [x] Queue view + header "Processing" indicator
- [x] Light/dark mode, works at 1024 px
- [x] Vitest tests for API client and key components

- [x] Saved API key never follows a changed host/provider (data safety fix)

## 4. Containers
- [x] `docker/app.Dockerfile`: frontend build → Python runtime + baked models, non-root when possible
- [x] `docker/searxng/settings.yml` (json on, limiter off, safe engines) + init step that writes it when missing
- [x] `compose.yaml` (GHCR, SearXNG pinned by digest), `compose.dev.yaml`, `.env.example`
- [x] Local smoke: `docker compose -f compose.dev.yaml up --build`, wizard Test against llama-server

## 5. CI / release
- [x] `.github/workflows/ci.yml`: ruff, mypy, pytest, ESLint, typecheck, Vitest, Docker build check
- [x] `.github/workflows/release.yml`: branch `phase-N` → `:phase-N-rc`; tag `phase-N` → `:phase-N` + `:latest`
- [~] CI green on `phase-0`, image `ghcr.io/ludwich/trustbook:phase-0-rc` published and pullable

## 6. Gate
- [x] README (install, `.env`, gate command, grill-me credit placeholder)
- [x] `docs/GATES.md` Gate 0 checklist + how to verify
- [ ] PR `phase-0 → main`
- [ ] `docker image prune -f`
- [ ] Stop and summarise

## Notes for a restarted session
- Gate 0 handover: docs/GATES.md. Known issues: D-019 (Whistle threads), D-015 (search rate limits).
- After Ludwig passes Gate 0: squash-merge PR, tag `phase-0` on main, branch `phase-1`, bump `CALIBRATION_VERSION` when Docling lands.
