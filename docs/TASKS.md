# Phase 3b — CLI for agents and transcript windows (branch `phase-3b`)

Gate 3 passed (Ludwig, 2026-10-12): #4 squash-merged into `main`, tagged `phase-3`; its task list is in git
history. `phase-3b` is rebased onto `main` and PR #5 targets `main`. Gate 3 findings G3-1..G3-3 are in
docs/BACKLOG.md. `phase-4` (#6 → `phase-3b`) is stacked on this branch.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done

## Status: Gate 3b passed (Ludwig, 2026-10-13; #8 waived), #5 squash-merged, tagged `phase-3b`
- [x] CLI package `cli/` (D-087), JSON contract + exit codes (D-088), folder paths (D-089), skill (D-090),
  review fixes (SSE line splitting, eval extra and exit codes, no tracebacks, partial adds reported)
- [x] Transcript windows, non-citable (D-091); golden prompts unchanged for the eval corpus
- [x] Release: sub-phase images and the CLI end-to-end test (D-092); CLI installed into the app venv
- [x] Quality bar (backend, eval, cli, frontend) green; local image: CLI e2e PASS, failed URL → exit 3
- [x] CI + `phase-3b-rc` (4b3cb21); PR #5 phase-3b → main (rebased after Gate 3); Gate 3b in GATES.md — waiting for Ludwig

### Original Phase 3b scope
- `trustbook` CLI (in the image: `docker compose exec app trustbook …`; pip-installable from the repo), talks to the HTTP API (default http://127.0.0.1:8484, `--base-url`), reuses the eval runner's client
- Commands: spaces list|create|delete; sources add folder|url|file|paste|search; sources list|status|refresh|remove; ask "<q>" --space X; queue; gate-log; eval
- `--json` everywhere with a stable documented schema (ask: answer, verdict, citations with source/file/lines/text, removed statements); exit codes 0 ok · 2 refused · 3 failed source · 1 error; `--wait` on adds (progress on stderr)
- Same grounding as the UI (cannot bypass the six layers or refusal); never prints API keys
- docs/cli.md + Claude Code skill skills/trustbook/SKILL.md (refusal = "not in sources", never retry around it)
- Tests: integration per command (mock LLM); e2e add folder → ask → cited JSON
- Gate 3b: Windows PowerShell CLI add folder + ask → cited JSON; Claude Code on Ludwig's laptop uses the skill
- Backlog: MCP server wrapping the same client
- Transcripts in parent/child retrieval: ~1–2k-token windows with speakers + timestamps as parent context; citations
  only to the child's own timestamps (D-077 slice for transcripts)

## Notes for a restarted session
- Quality bar script (stops on first failure): scratchpad `qbar.sh`; never chain checks through `| tail`.
- Phase 3 merged (`e5c2991`), tagged `phase-3`. Push tags as `refs/tags/phase-N`.
- Never `git commit -a` while agents edit the tree; stage explicit paths.
- ML work outside CI: `UV_PROJECT_ENVIRONMENT=<scratch>/mlvenv uv sync --frozen --extra ml`.
- Build test images from committed HEAD only: `git archive HEAD | docker build -f docker/app.Dockerfile -`.

## Phase 4 (branch `phase-4`, stacked on phase-3b): Ideation — built, Gate 4 waiting
- [x] Backend: schema v6, map, prompt, engine (per-sentence grounded recommendations), service, API + SSE, brief,
  estimate, shared `api/grounded.py`, isolation guards (D-093..D-101)
- [x] Frontend: Ideation tab (entry, question card, branch map, finish/export/continue, resume, running state; D-102)
- [x] Real-model check (Qwen3.6): recommendations cite sources, briefs never in chat (D-103)
- [x] Quality bar green; Playwright 25/25 incl. `ideation.spec.ts`; CLI e2e on the image
- [ ] CI + `phase-4-rc`; PR phase-4 → phase-3b; Gate 4 in GATES.md (written)
- Backlog: `trustbook ideas` CLI command; "Suggest ideas" (SPEC §16, out of scope)
