# Phase 5 — Hardening (branch `phase-5`)

Scope (Ludwig, 2026-10-13): all of P1 and P2 from `docs/BACKLOG.md` ("Priorities"), correctness first, with his
additions. Stop at Gate 5; no merge or tag without his typed confirmation.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done

## P1: correctness of what the user reads
- [x] G4-3 a risk is attached to the branch it concerns, not the one answered just before
- [x] G4-1 chunks split at headings (documents) and a stored section header for web/paste/upload chunks;
      bump `CHUNKER_VERSION` and the calibration version; the UI says a space needs re-indexing and offers a
      button (never silent) (D-104..D-107); Settings lists outdated spaces with Re-index (SPEC §12, D-114)
- [x] Gate 3 command on the new chunker with Qwen3.6 (image 44b3c3a): pass; precision 94.9% (37/39; both off-key
      citations support their sentence), 0 false refusals, p95 42.7 s
- [x] Follow-ups from that run (D-116, D-117): a duplicated sentence after a JSON fragment (ref-near-05); a risk still attached
      to the answered branch by fallback (1 of 15) and a map label ("n5") in a risk text
- [x] G3-2 ref-near-10: diagnosis (docs/reports/G3-2-ref-near-10.md); Ludwig approved F6, F1, F4, F2 (D-118..D-121,
      review follow-ups) and line 106 in the answer key (GATES.md, Gate 5). Before/after, 20 runs each: refusals
      2 → 2 (`unsupported`), verified 16 → 10, partial 2 → 8 (upper-case names removed by layer 5); report updated
- [x] G3-3 second family: gemma-4-26B-A4B-it-UD-Q4_K_XL (Ludwig's choice), one gate run with both models on
      `2696786`: Qwen3.6 pass (100%, 0/13, p95 49.2 s); Gemma reported 97.2%, 23/23, code false refusals 2/13, p95
      56.5 s; runner waits for a model to load (D-122); MODELS.md row
- [x] Untested gate items made one-step: Gate 3b #8 skill behaviour (docs/cli.md one-liners, `claude -p`), Gate 4 #9 request log (test extended to a
      full session: seed, 4 turns, judge), Gate 4 seed dismiss

## P2: usability and setup
- [x] G3b-2 optional API key for "Other OpenAI-compatible" chat and the speech-to-text endpoint
      (`settings.json` only, never logged or echoed); shown for every endpoint preset (D-111)
- [x] G3b-1 `python -m trustbook_cli` + Git Bash shim, documented in docs/cli.md and SKILL.md
- [x] G3-1 code symbol in the citation viewer header
- [x] G4-2 a reload keeps the Ideation tab
- [x] G4-4 source-list messages and long file names wrap; one line per problem
- [x] CLI nits: `http_status` on a known 401; "loading models" instead of "reading 0%"
- [x] docs/MODELS.md: tested chat models with their gate results (Qwen3.6 recommended; Qwen3.8-27B ~78%
      precision, p95 ~85 s; the second family once run)

## Gate 5 (fast to verify: one command or one click per finding; Gate 3 command once with both models)
- [x] Gate 5 checklist in docs/GATES.md
- [~] PR `phase-5 → main`, `phase-5-rc`; then stop for Ludwig

## Notes for a restarted session
- Phase 4 merged (`66bafd5`), tagged `phase-4`; `:latest` = Phase 4.
- Quality bar: backend ruff/format/mypy --strict/pytest; frontend lint/typecheck/test; cli and eval suites.
- Never `git commit -a` while agents edit the tree; stage explicit paths.
- Build test images from committed HEAD only: `git archive HEAD | docker build -f docker/app.Dockerfile -`.
- Decision numbers: D-104 onward.
