# Phase 1 — Grounded chat (branch `phase-1`)

Gate 1: citation precision ≥ 90% on the smoke set using Ludwig's chat endpoint.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done

## 0. Gate 0 follow-ups (Ludwig's findings, do first)
- [ ] F1 Slow bundled models in Docker Desktop/WSL2 (4 chunks/s, 8.9 pairs/s on 32 cores): measure torch thread use inside the container (main vs worker threads), fix, log the cause in DECISIONS.md
- [ ] F2 Speed test wording: show STT as "N× faster than real time"
- [ ] F3 Cap "High" power workers at a sensible maximum (RAM per worker)

## 1. Sources and ingestion (SPEC §7.1)
- [ ] Source model + API: list, add, enable/disable checkbox, groups (documents, transcripts, web, code), delete, refresh
- [ ] Upload: store original → Docling (PDF, DOCX, PPTX, XLSX) / text / audio → STT; pypdf fallback
- [ ] Paste text
- [ ] URL: fetch → Trafilatura → Crawl4AI fallback (< 200 chars) → dated snapshot
- [ ] YouTube: yt-dlp captions (manual, then auto) → else audio → STT; timestamps
- [ ] Web search: SearXNG / Brave / Tavily results → user ticks → URL sources
- [ ] Link folder: picker over `/host` (read-only), ignore rules, documents parsed (code chunking in Phase 3)
- [ ] Snapshots: `snapshot.md`, `map.json` (offsets → page / timestamp / file + line), originals for uploads
- [ ] Bake Docling models; bump `CALIBRATION_VERSION` with parse speed measured
- [ ] Voice-activity check before STT (D-008)

## 2. Chunking and index (SPEC §7.2, §2)
- [ ] Heading-aware document chunker (target 500, max 800, overlap 80; locator = pages)
- [ ] Transcript chunker (speaker turns ≤ ~500 tokens, never mid-sentence; locator = timestamps)
- [ ] Web chunker (locator = section heading + offset)
- [ ] LanceDB per space (`vectors/`), embeddings, FTS index

## 3. Processing UX (SPEC §7.5)
- [ ] Status flow queued → reading → indexing → ready | failed, progress
- [ ] Failure cause + next step; quality warnings (Whistle transcript, OCR, < 300 chars)

## 4. Retrieval and grounding layers 1–3 (SPEC §8)
- [ ] Enabled-sources filter, vector top 40 + FTS top 40 → RRF (k = 60) → rerank → top 8 (≤ ~6,000 tokens)
- [ ] Layer 1: no web tool in the chat pipeline
- [ ] Layer 2: refuse before generating if top rerank score < refuse_threshold (0.15)
- [ ] Layer 3: forced citations JSON; sentences without chunk_ids dropped; JSON schema → JSON mode + validation + one retry

## 5. Chat (SPEC §9)
- [ ] Conversations + messages persisted; streaming at sentence granularity
- [ ] Inline numbered citations → source viewer at passage (basic viewer; full viewer is Phase 2)
- [ ] "Show sources used" chip; "Using X of Y sources"
- [ ] Space overview + three suggested questions (auto on first ready source, refresh on request, per-space toggle, token estimate when remote)
- [ ] One-time hint

## 6. Frontend
- [ ] Sources panel: add-source menu (upload, folder, URL, YouTube, search, paste), groups with checkboxes, statuses, warnings
- [ ] Chat view with streaming, citations, chips; overview card
- [ ] Playwright e2e: create space → paste text → ask → cited answer (mock LLM)

## 7. Evaluation (SPEC §13)
- [ ] `eval/runner.py`: citation precision, correct-refusal rate, latency p50/p95, Markdown report; `--questions` any path
- [ ] `eval/format.md`
- [ ] `eval/smoke/`: psf/requests at a pinned commit + docs; 15 questions (5 docs, 5 code, 2 multi-source, 3 unanswerable), each verified by hand

## 8. Gate
- [ ] Licenses for new deps (Docling, Trafilatura, Crawl4AI, yt-dlp, LanceDB, …) in THIRD_PARTY_LICENSES.md
- [ ] CI green, PR `phase-1 → main`, `:phase-1-rc` published
- [ ] Run smoke set against Ludwig's endpoint; record citation precision in GATES.md
- [ ] `docker image prune -f`; stop and summarise

## Notes for a restarted session
- Phase 0 merged (`a60f774`), tagged `phase-0`. Local branch `phase-0` deleted; push tags as `refs/tags/phase-N`
  (a branch and tag share the name).
