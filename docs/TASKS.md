# Phase 4 — Ideation (branch `phase-4`)

Gates 3 and 3b passed (2026-10-12 and 2026-10-13) and are merged into `main` (tags `phase-3`, `phase-3b`).
`phase-4` is rebased onto `main`; PR #6 targets `main`. All SPEC §14 phases are done; the next phase waits
for Ludwig (proposal: `docs/BACKLOG.md`, "Priorities").

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done

## Status: Gate 4 passed (Ludwig, 2026-10-13; #9 and seed dismiss not tested), #6 squash-merged, tagged `phase-4`
- [x] Backend: schema v6, map, prompt, engine (per-sentence grounded recommendations), service, API + SSE, brief,
  estimate, shared `api/grounded.py`, isolation guards (D-093..D-101)
- [x] Frontend: Ideation tab (entry, question card, branch map, finish/export/continue, resume, running state; D-102)
- [x] Real-model check (Qwen3.6): recommendations cite sources, briefs never in chat (D-103)
- [x] Quality bar green; Playwright 25/25 incl. `ideation.spec.ts`; CLI e2e on the image
- [x] CI + `phase-4-rc` (rebuilt from the rebased branch head); PR #6 phase-4 → main; Gate 4 in GATES.md — waiting for Ludwig
- Backlog: `trustbook ideas` CLI command; "Suggest ideas" (SPEC §16, out of scope)

## Notes for a restarted session
- Quality bar script (stops on first failure): scratchpad `qbar.sh`; never chain checks through `| tail`.
- Phase 3b merged (`e5ee9c4`), tagged `phase-3b`. Push tags as `refs/tags/phase-N`.
- Never `git commit -a` while agents edit the tree; stage explicit paths.
- ML work outside CI: `UV_PROJECT_ENVIRONMENT=<scratch>/mlvenv uv sync --frozen --extra ml`.
- Build test images from committed HEAD only: `git archive HEAD | docker build -f docker/app.Dockerfile -`.

