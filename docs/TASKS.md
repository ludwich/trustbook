# Phase 3 — Code (branch `phase-3`)

Gate 3 (SPEC §14): on the `requests` repo, 100% of cited file/line ranges exist at the indexed commit; the
secrets scan skips a planted fake secret in a test folder.
Gate 3 (added by Ludwig at Gate 2): at most **1 false refusal across all answerable code questions** in the
smoke + refusal sets, with correct refusals still ≥ 95% and citation precision ≥ 90%. False refusals are
reported per category (docs/code/multi). p95 latency is tracked in GATES.md and flagged above 60 s.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done

## 0. Ludwig's Gate 2 priorities (in order; each logged in DECISIONS.md)
- [x] P1a Answer checking handles claims about code (code-03, code-05 refused "unsupported" on Strix Halo); never weaken grounding
- [x] P1b Code retrieval finds ref-near-08 (Digest MD5 default) and ref-near-10 (`codes.too_many_requests`)
- [x] P2 Eval report: false refusals per category (docs/code/multi); Gate 3 criterion wired into the runner (`--max-false-refusals-code 1`)
- [x] P3 p95 latency tracked in GATES.md, flagged above 60 s (runner target flag)
- [x] E2E tests for Gate 2 UI items #3–#12 (badges + drawer, unverified, refusal card, code viewer, secrets report, failed sources, power labels, web search ranking, estimates, gate log), run against the phase-3 image

## 1. Code ingestion (SPEC §7.2, §7.3)
- [x] tree-sitter chunking for C#, TypeScript, JavaScript, Python: one chunk per function/method/class, prefixed with file path + enclosing symbol; symbols > 1,500 tokens split at statement boundaries; module-level code grouped up to 500 tokens; locator file + start_line + end_line + symbol
- [x] Fallback chunker stays for other languages (AL, X++, SQL, YAML, …)
- [x] Licenses: tree-sitter + grammars (MIT) in THIRD_PARTY_LICENSES.md

## 2. Commits and refresh (SPEC §7.5)
- [x] Record HEAD commit; "Indexed at <short>, folder is N commits ahead. Refresh?" banner (N via commit graph; no GPL git binary)
- [x] Incremental refresh: re-index only changed files (content hash per file)

## 3. Deterministic code checks (SPEC §8 layer 5)
- [x] Cited file/line ranges exist at the indexed commit; eval runner metric "cited ranges valid" against the checkout (Gate 3)
- [x] Secrets: planted fake secret in a generated test folder is skipped (Gate 3), test generated at runtime (never commit a secret-looking string)

## 3b. Gate 3 failure fixes (Ludwig, 2026-10-11)
- [x] F1 BLOCKING: bare `raise` in ingest handler `finally` failed every successful job (fixed `2a4924a`, job-status test)
- [~] F2 Wrong citations from function-level chunks (code-02, docs-04, ref-near-05): fix chunk boundaries/retrieval; never widen the key — list believed-correct citations in GATES.md for approval
- [~] F3 code-05 answers again; split oversized table chunks (ref-near-10); ref-near-07
- [ ] F4 Run the full gate command 3× on the workbench; all 3 must pass; report all 3 in GATES.md
- [ ] F5 Eval runs use temperature 0 + fixed seed (per-request generation override used only by the runner; SPEC's 0.2 default unchanged)
- [ ] F6 Runner `--chat-endpoint` / `--chat-model` overrides; model name in the report header; gate run with a stronger reference model (Qwen3.5-122B-A10B) alongside Qwen3.6, both in GATES.md

- [ ] A One of the 3 workbench gate runs uses the app default (temperature 0.2, no seed), labelled "real-use" in GATES.md
- [ ] B e2e + eval assert JOB status (done/failed) and the Queue UI state for every source type (paste, upload, URL, YouTube, search, folder)
- [ ] C Calibration version bumped now (v6) and tied to CHUNKER_VERSION + embedding revision so chunking/embedding changes always re-calibrate
- [ ] D docs/MODELS.md (per-model Gate 3 results + recommended minimum chat model); README memory note (Docker Desktop/WSL memory, Trustbook's use while indexing)

## 4. Gate
- [x] CI green, PR `phase-3 → main` (#4), `:phase-3-rc` published from `92a8b12` (e2e gate passes)
- [x] GATES.md Gate 3 checklist incl. per-category false refusals and p95 latency
- [ ] `docker image prune -f`; stop and summarise (Gate 3 attempt 1 failed; fixing)

## Queued AFTER Gate 3 passes — Phase 3b: CLI for agents (do not mix into Gate 3 fixes)
- `trustbook` CLI (in the image: `docker compose exec app trustbook …`; pip-installable from the repo), talks to the HTTP API (default http://127.0.0.1:8484, `--base-url`), reuses the eval runner's client
- Commands: spaces list|create|delete; sources add folder|url|file|paste|search; sources list|status|refresh|remove; ask "<q>" --space X; queue; gate-log; eval
- `--json` everywhere with a stable documented schema (ask: answer, verdict, citations with source/file/lines/text, removed statements); exit codes 0 ok · 2 refused · 3 failed source · 1 error; `--wait` on adds (progress on stderr)
- Same grounding as the UI (cannot bypass the six layers or refusal); never prints API keys
- docs/cli.md + Claude Code skill skills/trustbook/SKILL.md (refusal = "not in sources", never retry around it)
- Tests: integration per command (mock LLM); e2e add folder → ask → cited JSON
- Gate 3b: Windows PowerShell CLI add folder + ask → cited JSON; Claude Code on Ludwig's laptop uses the skill
- Backlog: MCP server wrapping the same client

## Notes for a restarted session
- Quality bar script (stops on first failure): scratchpad `qbar.sh`; never chain checks through `| tail`.
- Phase 2 merged (`14db9aa`), tagged `phase-2`. Push tags as `refs/tags/phase-N`.
- Never `git commit -a` while agents edit the tree; stage explicit paths.
- ML work outside CI: `UV_PROJECT_ENVIRONMENT=<scratch>/mlvenv uv sync --frozen --extra ml`.
- Build test images from committed HEAD only: `git archive HEAD | docker build -f docker/app.Dockerfile -`.
