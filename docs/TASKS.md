# Phase 2 — Trust and flow (branch `phase-2`)

Gate 2: correct-refusal rate ≥ 95% on unanswerable questions, with citation precision still ≥ 90%.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done

## 0. Gate 1 follow-ups (Ludwig's findings, do first; each logged in DECISIONS.md)
- [ ] G1 Power "High" label shows the real (capped) worker count
- [ ] G2 Indexing one large source uses all cores: parallel parsing/chunking per file, concurrent embedding streams (tuned by calibration), batched LanceDB writes
- [ ] G3 Code answers include definitions of referenced module-level constants; `must_contain` becomes a tracked metric in the eval report
- [ ] G4 Secrets warning shown once and clickable: report (file, line, rule ID) viewable in the UI
- [ ] G5 Refusal card: no repeated title; suggestion is a readable topic
- [ ] G6 Code viewer: exact highlight, monospace, line numbers, no flash of the file top
- [ ] G7 Failed sources: unticked, not tickable, not counted
- [ ] G8 Web search: `language=en` sent to SearXNG (config/params only), bare homepages demoted for specific queries
- [ ] E2E tests for what Ludwig did not verify at Gate 1: PDF upload (incl. OCR warning), overview + one-time hint, checkbox scoping
- [x] Backlog items written to docs/BACKLOG.md (not built)

## 1. Grounding layers 4–6 (SPEC §8)
- [ ] Layer 4: sentence verification with the grounding checker (max score over cited chunks ≥ verify_threshold 0.5); failing sentences removed; one regeneration if more than half fail; then the refusal rule
- [ ] Refusal rule: refuse if > 50% of sentences fail or the first sentence fails
- [ ] Layer 5: deterministic code checks (cited file/line ranges exist in the indexed snapshot; every backticked identifier occurs in its cited chunks)
- [ ] Grounding disabled → badge "Unverified"
- [ ] Badges: Verified · Partially verified (N removed; drawer "Unverified, not from your sources") · Refused · Unverified
- [ ] Layer 6: eval runner records scores; private-set support; threshold tuning report

## 2. Source viewer (SPEC §9, §12)
- [ ] Full viewer: documents by page, transcripts with timestamps, web by section, code with line numbers + commit

## 3. Processing, cost and flow (SPEC §11)
- [ ] Estimates before adds above 2 minutes ("About N files, roughly M min on this PC") → confirm/cancel
- [ ] Queue view: ETA from calibration, pause / reorder / cancel across spaces; header "about M min left"

## 4. Gate logging (SPEC §5, §6)
- [ ] `gate` capability used as a fixed-choice router; every decision logged to `gate_log` (input, decision, confidence, user correction)

## 5. Evaluation and tuning
- [ ] Runner: per-question top rerank score and verifier scores in a JSON sidecar; threshold sweep table
- [ ] Tune refuse/verify thresholds on the smoke set + Ludwig's private set; record in GATES.md

## 6. Gate
- [ ] CI green, PR `phase-2 → main`, `:phase-2-rc` published (e2e gate passes)
- [ ] Smoke set: refusal ≥ 95%, precision ≥ 90%; GATES.md checklist
- [ ] `docker image prune -f`; stop and summarise

## Notes for a restarted session
- Phase 1 merged (`65d07c7`), tagged `phase-1`. Push tags as `refs/tags/phase-N`.
- Never `git commit -a` while agents edit the tree; stage explicit paths.
- ML work outside CI: use a separate venv (`UV_PROJECT_ENVIRONMENT=<scratch>/mlvenv uv sync --extra ml`) so
  agents' `uv run` doesn't fight over backend/.venv.
