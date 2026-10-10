# Phase 3 — Code (branch `phase-3`)

Gate 3 (SPEC §14): on the `requests` repo, 100% of cited file/line ranges exist at the indexed commit; the
secrets scan skips a planted fake secret in a test folder.
Gate 3 (added by Ludwig at Gate 2): at most **1 false refusal across all answerable code questions** in the
smoke + refusal sets, with correct refusals still ≥ 95% and citation precision ≥ 90%. False refusals are
reported per category (docs/code/multi). p95 latency is tracked in GATES.md and flagged above 60 s.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done

## 0. Ludwig's Gate 2 priorities (in order; each logged in DECISIONS.md)
- [ ] P1a Answer checking handles claims about code (code-03, code-05 refused "unsupported" on Strix Halo); never weaken grounding
- [ ] P1b Code retrieval finds ref-near-08 (Digest MD5 default) and ref-near-10 (`codes.too_many_requests`)
- [ ] P2 Eval report: false refusals per category (docs/code/multi); Gate 3 criterion wired into the runner (`--max-false-refusals-code 1`)
- [ ] P3 p95 latency tracked in GATES.md, flagged above 60 s (runner target flag)
- [ ] E2E tests for Gate 2 UI items #3–#12 (badges + drawer, unverified, refusal card, code viewer, secrets report, failed sources, power labels, web search ranking, estimates, gate log), run against the phase-3 image

## 1. Code ingestion (SPEC §7.2, §7.3)
- [ ] tree-sitter chunking for C#, TypeScript, JavaScript, Python: one chunk per function/method/class, prefixed with file path + enclosing symbol; symbols > 1,500 tokens split at statement boundaries; module-level code grouped up to 500 tokens; locator file + start_line + end_line + symbol
- [ ] Fallback chunker stays for other languages (AL, X++, SQL, YAML, …)
- [ ] Licenses: tree-sitter + grammars (MIT) in THIRD_PARTY_LICENSES.md

## 2. Commits and refresh (SPEC §7.5)
- [ ] Record HEAD commit; "Indexed at <short>, folder is N commits ahead. Refresh?" banner (N via commit graph; no GPL git binary)
- [ ] Incremental refresh: re-index only changed files (content hash per file)

## 3. Deterministic code checks (SPEC §8 layer 5)
- [ ] Cited file/line ranges exist at the indexed commit; eval runner metric "cited ranges valid" against the checkout (Gate 3)
- [ ] Secrets: planted fake secret in a generated test folder is skipped (Gate 3), test generated at runtime (never commit a secret-looking string)

## 4. Gate
- [ ] CI green, PR `phase-3 → main`, `:phase-3-rc` published (e2e gate passes)
- [ ] GATES.md Gate 3 checklist incl. per-category false refusals and p95 latency
- [ ] `docker image prune -f`; stop and summarise

## Notes for a restarted session
- Phase 2 merged (`14db9aa`), tagged `phase-2`. Push tags as `refs/tags/phase-N`.
- Never `git commit -a` while agents edit the tree; stage explicit paths.
- ML work outside CI: `UV_PROJECT_ENVIRONMENT=<scratch>/mlvenv uv sync --frozen --extra ml`.
- Build test images from committed HEAD only: `git archive HEAD | docker build -f docker/app.Dockerfile -`.
