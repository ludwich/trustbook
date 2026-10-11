# Phase 3 — Code (branch `phase-3`)

Gate 3 (SPEC §14): on the `requests` repo, 100% of cited file/line ranges exist at the indexed commit; the
secrets scan skips a planted fake secret in a test folder.
Gate 3 (added by Ludwig at Gate 2): at most **1 false refusal across all answerable code questions** in the
smoke + refusal sets, with correct refusals still ≥ 95% and citation precision ≥ 90%. False refusals are
reported per category (docs/code/multi). p95 latency is tracked in GATES.md and flagged above 60 s.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done

## Overnight plan (Ludwig, 2026-10-11 evening; authorised to work without waiting)
1. Gate 3: 4 runs on `trustbook:p3h` (93b4419). Pass → GATES.md, push phase-3, confirm `phase-3-rc`, leave PR #4
   OPEN (no merge, no tag, main untouched). Fail → keep fixing (no key widening; 3 passing Qwen3.6 runs incl.
   real-use at 0.2 + Qwen3.8-27B comparison).
2. Stacked branches, never merged until Ludwig passes each gate:
   a. `phase-3b` from phase-3: CLI for agents + Claude Code skill (below) + transcripts in parent/child retrieval
      (1–2k-token windows, speakers + timestamps, citations to the child only). PR phase-3b → phase-3,
      `phase-3b-rc`, Gate 3b in GATES.md.
   b. `phase-4` from phase-3b: Phase 4 (Ideation) per SPEC §14. PR phase-4 → phase-3b, `phase-4-rc`, Gate 4.
   If phase-3 changes after Gate 3 review: rebase the stacked branches.
3. CI green on every push; DECISIONS/TASKS current; never weaken grounding.
   Status 22:00: attempt 3 on p3h, run 1 FAIL (code-04 + ref-near-10), run 2 PASS, real-use PASS, Qwen3.8 reference
   precision 77.8% (comparison only). Fix D-082 (feedback after a failed rescue, judge evidence for every name);
   rerun all 4 on the next image.
   Attempt 4 (p3i, 36d17db): runs 1, 2 and real-use PASS (code false refusals 0/1/0, precision 100%, p95 42.7/42.6/44.6 s);
   Qwen3.8 reference 79.4% precision, p95 84.5 s. Run 2's one refusal was a double-backtick parse bug, fixed in D-083,
   so the final runs are on the next image (p3j).
4. Stop when Phase 4 is done or blocked on a decision only Ludwig can make. Summary at the top of docs/GATES.md:
   what waits for him (Gate 3 → 3b → 4) and how to test each.

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
  - [x] chunking (D-056): data literals split by element into named ~150-token parts; `locator.kind`;
    document files chunked on whole lines (line locators claim only lines the chunk holds); `CHUNKER_VERSION` 4
  - [x] retrieval (D-059–062): content-ordered ties, callee definitions, agreement guard; chat (D-063/064): judge confirms checker passes
  - [x] attempt-2 measurement: run 1 passed; run 2 (2 code FR), real-use (p95 66 s) and Qwen3.8 reference failed
  - [x] round 2a (D-068..D-076): anchor matching (dotted names, plural acronyms, inline markup), confirmation veto checks the full
    cited chunks, targeted retry, failed_sentences diagnostics
  - [x] round 2b (Ludwig, D-077..D-081): general parent/child retrieval — retrieve on small chunks, expand to capped parent context
    for the prompt (docs: enclosing section with heading path; code: enclosing class / file outline + imports;
    transcripts later: ~1–2k-token window with speakers + timestamps); citations always the child's exact
    lines/timestamps, never the parent; replace the D-032 constant lookup and the callee lookup where possible
    (DECISION); fixes ref-near-03 (release heading outside the chunk); CHUNKER_VERSION/calibration bump if chunks change
    - [x] slice A (D-077 data, D-078): parent keys + structure records at chunk time, `p3-treesitter-6`, space
      migration 005, D-032 constants / D-058 literal parts through the module family (`FileRows`); parity run
    - [x] slice B (D-077 expansion, D-079, D-080): `retrieval/parents.py` (promotions + code outline, caps
      120/300 tokens; fitting keeps the expanded class line last), `retrieval.parent_context` / `retrieval.outline`
      switches, outline section after the chunks with its own rule (system message unchanged), regeneration text
      in the user message, grounding isolation tests, diagnostics (`judge_calls`, timings, tokens, expansions)
    - [x] merge A + B (service passes one `FileRows` to the lookups and the expansion); retrieval-only on/off diff
      over 45 questions; seeded on/off run of the changed questions (D-077 integration check)
    - [x] D-069 claim replay keeps false passes at 0 (also outline-only and promoted-sibling claims); D-078 parity
      numbers filled
- [~] Latency: judge calls per answer in diagnostics + eval report (D-080, done in slice B); judge calls/answer and
  p95 in GATES.md (round 2b table added, to fill); p95 ≤ 60 s with Qwen3.6 (strict judge now also runs on prose
  and borderline passes): 3 Qwen3.6 runs (one real-use) + 1 Qwen3.8-27B run
- [ ] F4 Run the full gate command 3× on the workbench; all 3 must pass; report all 3 in GATES.md
- [x] F5 Eval runs use temperature 0 + fixed seed (per-request generation override used only by the runner; SPEC's 0.2 default unchanged)
- [~] F6 Runner `--chat-endpoint` / `--chat-model` overrides (done); model name in the report header (done); gate run with a stronger reference model (Qwen3.8-27B-UD-Q4_K_XL, chosen by Ludwig) alongside Qwen3.6, both in GATES.md

- [~] A (runner `--app-default` done) One of the 3 workbench gate runs uses the app default (temperature 0.2, no seed), labelled "real-use" in GATES.md
- [x] B e2e + eval assert JOB status (done/failed) and the Queue UI state for every source type (paste, upload, URL, YouTube, search, folder); URL/YouTube success opt-in via `E2E_INTERNET=1` (D-067)
- [x] C Calibration version bumped now (v6) and tied to CHUNKER_VERSION + embedding revision so chunking/embedding changes always re-calibrate (D-057, `Calibration.pipeline`)
- [ ] D docs/MODELS.md (per-model Gate 3 results + recommended minimum chat model); README memory note (Docker Desktop/WSL memory, Trustbook's use while indexing)

## 4. Gate
- [x] CI green, PR `phase-3 → main` (#4), `:phase-3-rc` published from `92a8b12` (e2e gate passes)
- [x] GATES.md Gate 3 checklist incl. per-category false refusals and p95 latency
- [ ] `docker image prune -f`; stop and summarise (Gate 3 attempt 1 failed; fixing)

## Phase 3b (branch `phase-3b`, after the Gate 3 runs pass): CLI for agents + transcript windows
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
- Phase 2 merged (`14db9aa`), tagged `phase-2`. Push tags as `refs/tags/phase-N`.
- Never `git commit -a` while agents edit the tree; stage explicit paths.
- ML work outside CI: `UV_PROJECT_ENVIRONMENT=<scratch>/mlvenv uv sync --frozen --extra ml`.
- Build test images from committed HEAD only: `git archive HEAD | docker build -f docker/app.Dockerfile -`.
