# Phase 0 — Foundations (branch `phase-0`)

Gate 0: stack starts with one command; wizard Test succeeds against Ludwig's endpoint;
every capability card shows correct status and badge; create/delete a space creates/removes its folder.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done

## 0. Planning
- [x] Read CLAUDE.md + SPEC.md, create branch `phase-0`
- [x] Write this task list
- [ ] `docs/ARCHITECTURE.md` (layout, API contract, data folder, container design)
- [ ] `docs/DECISIONS.md` (license checks, Phase 0 scope calls)

## 1. Model + license verification
- [ ] Verify licenses on model cards (embeddings, reranker, grounding, STT) and record revisions
- [ ] Prove each default loads on CPU with pinned package versions (or switch to fallback)
- [ ] `docker/download_models.py` pins exact revisions, downloads into the image
- [ ] `THIRD_PARTY_LICENSES.md`

## 2. Backend (`backend/`)
- [ ] `pyproject.toml` (uv), ruff, mypy --strict, pytest config
- [ ] `app/config.py`: paths, `settings.json` load/save (atomic, mode 600), key redaction
- [ ] `app/db/`: numbered SQL migration runner; `global.db` (jobs, request_log); `space.db` full §5 schema
- [ ] `app/spaces/`: create / list / get / delete, `space.json` manifest, folder layout
- [ ] `app/capabilities/`: YAML manifests (chat, gate, embeddings, reranker, grounding, stt, web_search, tts), registry, locality, status
- [ ] Adapters: openai_compatible, azure_openai, anthropic, searxng, brave, tavily, stt endpoint, mock
- [ ] Health checks per capability (chat_ping, bundled model checks, search, stt)
- [ ] Request log: every outbound endpoint call recorded (host, capability, space, bytes, excerpt; never keys)
- [ ] `app/jobs/`: global SQLite queue, worker pool sized by power setting, active-space priority, pause/cancel/reorder
- [ ] Calibration on first start → `config/calibration.json`
- [ ] `app/api/`: health, setup status, settings, capabilities (+ Test), spaces, jobs, request log, calibration
- [ ] `app/main.py`: lifespan, static frontend with SPA fallback
- [ ] Tests: unit for every module, integration for every route (mock adapter, no network)

## 3. Frontend (`frontend/`)
- [ ] Vite 8 + React 19 + TS + Tailwind + shadcn/ui + TanStack Query + React Router, pnpm 10 pinned
- [ ] ESLint, Prettier, Vitest
- [ ] Setup wizard: chat endpoint + Test → optional STT endpoint, search key → done
- [ ] Spaces list: create, open, delete (confirm)
- [ ] Space page shell: sources panel (empty), Chat | Ideation tabs (placeholders)
- [ ] Settings: capability cards from manifests (fields, Test, status, local/remote badge), power setting, request log
- [ ] Queue view + header "Processing" indicator
- [ ] Light/dark mode, works at 1024 px
- [ ] Vitest tests for API client and key components

## 4. Containers
- [ ] `docker/app.Dockerfile`: frontend build → Python runtime + baked models, non-root when possible
- [ ] `docker/searxng/settings.yml` (json on, limiter off, safe engines) + init step that writes it when missing
- [ ] `compose.yaml` (GHCR, SearXNG pinned by digest), `compose.dev.yaml`, `.env.example`
- [ ] Local smoke: `docker compose -f compose.dev.yaml up --build`, wizard Test against llama-server

## 5. CI / release
- [ ] `.github/workflows/ci.yml`: ruff, mypy, pytest, ESLint, typecheck, Vitest, Docker build check
- [ ] `.github/workflows/release.yml`: branch `phase-N` → `:phase-N-rc`; tag `phase-N` → `:phase-N` + `:latest`
- [ ] CI green on `phase-0`, image `ghcr.io/ludwich/trustbook:phase-0-rc` published and pullable

## 6. Gate
- [ ] README (install, `.env`, gate command, grill-me credit placeholder)
- [ ] `docs/GATES.md` Gate 0 checklist + how to verify
- [ ] PR `phase-0 → main`
- [ ] `docker image prune -f`
- [ ] Stop and summarise
