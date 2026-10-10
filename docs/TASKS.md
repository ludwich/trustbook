# Phase 1 — Grounded chat (branch `phase-1`)

Gate 1: citation precision ≥ 90% on the smoke set using Ludwig's chat endpoint.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done

## 0. Gate 0 follow-ups (Ludwig's findings, do first)
- [x] F1 Slow bundled models in Docker Desktop/WSL2 (4 chunks/s, 8.9 pairs/s on 32 cores): measure torch thread use inside the container (main vs worker threads), fix, log the cause in DECISIONS.md
- [x] F2 Speed test wording: show STT as "N× faster than real time"
- [x] F3 Cap "High" power workers at a sensible maximum (RAM per worker)

## 1. Sources and ingestion (SPEC §7.1)
- [x] Source model + API: list, add, enable/disable checkbox, groups (documents, transcripts, web, code), delete, refresh
- [x] Upload: store original → Docling (PDF, DOCX, PPTX, XLSX) / text / audio → STT; pypdf fallback
- [x] Paste text
- [x] URL: fetch → Trafilatura → Crawl4AI fallback (< 200 chars) → dated snapshot
- [x] YouTube: yt-dlp captions (manual, then auto) → else audio → STT; timestamps
- [x] Web search: SearXNG / Brave / Tavily results → user ticks → URL sources
- [x] Link folder: picker over `/host` (read-only), ignore rules, documents parsed (code chunking in Phase 3)
- [x] Snapshots: `snapshot.md`, `map.json` (offsets → page / timestamp / file + line), originals for uploads
- [x] Bake Docling models; bump `CALIBRATION_VERSION` with parse speed measured
- [x] Voice-activity check before STT (D-008)

## 2. Chunking and index (SPEC §7.2, §2)
- [x] Heading-aware document chunker (target 500, max 800, overlap 80; locator = pages)
- [x] Transcript chunker (speaker turns ≤ ~500 tokens, never mid-sentence; locator = timestamps)
- [x] Web chunker (locator = section heading + offset)
- [x] LanceDB per space (`vectors/`), embeddings, FTS index

## 3. Processing UX (SPEC §7.5)
- [x] Status flow queued → reading → indexing → ready | failed, progress
- [x] Failure cause + next step; quality warnings (Whistle transcript, OCR, < 300 chars)

## 4. Retrieval and grounding layers 1–3 (SPEC §8)
- [x] Enabled-sources filter, vector top 40 + FTS top 40 → RRF (k = 60) → rerank → top 8 (≤ ~6,000 tokens)
- [x] Layer 1: no web tool in the chat pipeline
- [x] Layer 2: refuse before generating if top rerank score < refuse_threshold (0.15)
- [x] Layer 3: forced citations JSON; sentences without chunk_ids dropped; JSON schema → JSON mode + validation + one retry

## 5. Chat (SPEC §9)
- [x] Conversations + messages persisted; streaming at sentence granularity
- [x] Inline numbered citations → source viewer at passage (basic viewer; full viewer is Phase 2)
- [x] "Show sources used" chip; "Using X of Y sources"
- [x] Space overview + three suggested questions (auto on first ready source, refresh on request, per-space toggle, token estimate when remote)
- [x] One-time hint

## 6. Frontend
- [x] Sources panel: add-source menu (upload, folder, URL, YouTube, search, paste), groups with checkboxes, statuses, warnings
- [x] Chat view with streaming, citations, chips; overview card
- [x] Playwright e2e: create space → paste text → ask → cited answer (mock LLM) — runs in release.yml against the built image before push

## 7. Evaluation (SPEC §13)
- [x] `eval/runner.py`: citation precision, correct-refusal rate, latency p50/p95, Markdown report; `--questions` any path
- [x] `eval/format.md`
- [x] `eval/smoke/`: psf/requests at a pinned commit + docs; 15 questions (5 docs, 5 code, 2 multi-source, 3 unanswerable), each verified by hand

## 8. Gate
- [x] Licenses for new deps (Docling, Trafilatura, Crawl4AI, yt-dlp, LanceDB, …) in THIRD_PARTY_LICENSES.md
- [x] CI green, PR `phase-1 → main` (#2), `:phase-1-rc` published (e2e gate passed in release.yml)
- [x] Run smoke set against Ludwig's endpoint; record citation precision in GATES.md
- [x] `docker image prune -f`; stop and summarise — **waiting for Ludwig's Gate 1 verdict**

- [x] Calibration: exclusive model lock (D-030), parse speed measured (version 4)
- [x] Smoke-set dry runs against Ludwig's llama-server (Qwen3.6-35B-A3B): 87.5% → 92.0% → 100% after hand-verified key fixes

- [x] Exact gate command on the published `phase-1-rc`: 100% (23/23), refusals 3/3

## Notes for a restarted session
- Phase 0 merged (`a60f774`), tagged `phase-0`. Local branch `phase-0` deleted; push tags as `refs/tags/phase-N`
  (a branch and tag share the name).
