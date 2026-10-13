# Phase 5 — Hardening (branch `phase-5`)

Scope (Ludwig, 2026-10-13): all of P1 and P2 from `docs/BACKLOG.md` ("Priorities"), correctness first, with his
additions. Stop at Gate 5; no merge or tag without his typed confirmation.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done

## P1: correctness of what the user reads
- [x] G4-3 a risk is attached to the branch it concerns, not the one answered just before
- [~] G4-1 chunks split at headings (documents) and a stored section header for web/paste/upload chunks;
      bump `CHUNKER_VERSION` and the calibration version; the UI says a space needs re-indexing and offers a
      button (never silent)
- [~] G3-2 ref-near-10: diagnosis done (docs/reports/G3-2-ref-near-10.md): verification refusals, not retrieval;
      **waiting for Ludwig** to choose fixes. **Diagnose first** and report to Ludwig what retrieval misses, before any change. Never
      weaken a grounding layer; never widen the answer key without his approval
- [ ] G3-3 second model family (non-Qwen): propose 2–3 GGUF candidates for llama-server on Strix Halo (Vulkan)
      with size and speed; **Ludwig chooses before anything runs**. Results reported next to Qwen3.6, not
      pass/fail. The gate command runs both models in one command
- [x] Untested gate items made one-step: Gate 3b #8 skill behaviour (docs/cli.md one-liners, `claude -p`), Gate 4 #9 request log (test extended to a
      full session: seed, 4 turns, judge), Gate 4 seed dismiss
- [ ] Real-model check of the new risk schema (G4-3, D-108) with Qwen3.6: risks name the right branch

## P2: usability and setup
- [x] G3b-2 optional API key for "Other OpenAI-compatible" chat and the speech-to-text endpoint
      (`settings.json` only, never logged or echoed); shown for every endpoint preset (D-111)
- [x] G3b-1 `python -m trustbook_cli` + Git Bash shim, documented in docs/cli.md and SKILL.md
- [x] G3-1 code symbol in the citation viewer header
- [x] G4-2 a reload keeps the Ideation tab
- [x] G4-4 source-list messages and long file names wrap; one line per problem
- [x] CLI nits: `http_status` on a known 401; "loading models" instead of "reading 0%"
- [ ] docs/MODELS.md: tested chat models with their gate results (Qwen3.6 recommended; Qwen3.8-27B ~78%
      precision, p95 ~85 s; the second family once run)

## Gate 5 (fast to verify: one command or one click per finding; Gate 3 command once with both models)
- [ ] Gate 5 checklist in docs/GATES.md, PR `phase-5 → main`, `phase-5-rc`

## Notes for a restarted session
- Phase 4 merged (`66bafd5`), tagged `phase-4`; `:latest` = Phase 4.
- Quality bar: backend ruff/format/mypy --strict/pytest; frontend lint/typecheck/test; cli and eval suites.
- Never `git commit -a` while agents edit the tree; stage explicit paths.
- Build test images from committed HEAD only: `git archive HEAD | docker build -f docker/app.Dockerfile -`.
- Decision numbers: D-104 onward.
