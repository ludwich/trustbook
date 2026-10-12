# Decision log

Format: date · decision · reason · alternatives considered. Priority order (SPEC §15):
grounding correctness > data safety > simplicity > performance > features.

## 2026-10-10 — Phase 0

### D-001 Bundled model licenses verified on model cards
- **Decision:** keep the SPEC defaults; all four model cards state a permissive license.
  | Capability | Model | Revision | License (card) |
  |---|---|---|---|
  | Embeddings | `google/embeddinggemma-2` | `914f7f89142e` | Apache-2.0 |
  | Reranker | `mixedbread-ai/mxbai-rerank-xsmall-v1` | `b5c6e9da73ab` | Apache-2.0 |
  | Grounding | `vectara/hallucination_evaluation_model` (HHEM-2.1-Open) | `de9a5e6ba9ba` | Apache-2.0 (base `google/flan-t5-base`, Apache-2.0) |
  | STT | `Cactus-Compute/whistle` via `cactus-needle` 3.2.0 | `ca5287601bef` | Apache-2.0 (engine `Cactus-Compute/needle3`, Apache-2.0) |
- **Reason:** SPEC §2 requires card-level verification. Note: EmbeddingGemma **2** is Apache-2.0, unlike
  EmbeddingGemma 1 (`embeddinggemma-300m`), which uses the Gemma license and is gated.
- **Alternatives:** the SPEC fallbacks (all MIT/Apache-2.0) remain wired in case a default fails to run.

### D-002 Phase 0 bakes only the four bundled models; Docling arrives in Phase 1
- **Decision:** the Phase 0 image contains embeddings, reranker, grounding and STT models. Docling (and its
  layout models) is added in Phase 1 with the parsers.
- **Reason:** Phase 0 scope is foundations; parsing is Phase 1. Keeps the first image smaller.

### D-003 LAN endpoints count as "remote"
- **Decision:** locality is `local` only for `localhost`, `127.0.0.1`, `::1`, `host.docker.internal`.
  Any other host, including private LAN IPs, is `remote`.
- **Reason:** SPEC §6 rule; data that leaves the machine is shown as remote (data safety).

### D-004 SearXNG settings via a one-shot init service using the app image
- **Decision:** `compose.yaml` has a `searxng-init` service (same app image) that writes `settings.yml`
  (with a freshly generated `secret_key`) into a named volume if missing; SearXNG waits for it.
- **Reason:** SPEC §4 requires `compose.yaml` to work when downloaded alone. If SearXNG started first it
  would write its own default settings (JSON off, limiter on) and the app could not fix them without a race.
  No extra image, SearXNG stays unmodified.
- **Alternatives:** app writes the file and SearXNG `depends_on` the app (inverts the SPEC dependency and
  needs a health check); bind-mount from the data folder (same race).

### D-005 Container drops privileges to the owner of `/data`
- **Decision:** entrypoint runs as root only long enough to read the owner of `/data`; if it is a non-root
  uid, the app runs as that uid/gid (via `setpriv`), else it keeps running as root.
- **Reason:** on Linux hosts files in `~/Trustbook` stay owned by the user (handover = zip the folder);
  Windows/macOS bind mounts are unaffected.

### D-006 CI Docker build check skips model download
- **Decision:** `ci.yml` builds the image with `--build-arg BAKE_MODELS=0`; `release.yml` builds the full
  image with baked models.
- **Reason:** avoid downloading ~2 GB of models twice on every push; the release build exercises the full path.

### D-007 Playwright e2e test lands in Phase 1
- **Decision:** the SPEC e2e scenario (paste text → cited answer) needs Phase 1 features; Playwright is set up then.

### D-008 Whistle STT runs pinned, offline and with telemetry off
- **Decision:** bake the Whistle weights (`Cactus-Compute/whistle@ca5287601bef`) and the Needle engine
  (`libneedle3.so` extracted from `Cactus-Compute/needle3@8354babbfb92`, wheel `cactus_needle-3.3.0`) into the
  image. Load them with `needle.Whistle(weights=<path>)` and `NEEDLE3_LIB_PATH`. Set `NEEDLE_TELEMETRY=0`
  and `DO_NOT_TRACK=1` in the image.
- **Reason:** by default `cactus-needle` downloads the engine and weights unpinned from `main` and re-fetches a
  `config.json` (a download counter) on every load. Its module-level `needle.transcribe` also sends telemetry
  to a third-party endpoint. SPEC forbids telemetry and network calls the user did not configure. The
  `Whistle` class methods never call telemetry; offline use was verified with no network namespace.
- **Also:** Whistle is not thread-safe, so STT calls are serialised behind a lock. On non-speech audio it can
  invent a short phrase ("Thank you."), so Phase 1 adds a voice-activity check before STT.

### D-009 Licenses of transitive dependencies
- **Decision:** direct dependencies must be MIT, Apache-2.0, BSD or Unlicense. Transitive dependencies with
  equivalent permissive licenses (ISC, PSF-2.0, HPND/MIT-CMU, 0BSD) or file-level weak copyleft used
  unmodified (MPL-2.0, e.g. `certifi`) are accepted and listed in `THIRD_PARTY_LICENSES.md`.
- **Reason:** `transformers`/`sentence-transformers` cannot load EmbeddingGemma 2 without Pillow (HPND) and
  torchvision, and `httpx` needs `certifi` (MPL-2.0). There is no allowlisted alternative, and these
  licenses impose no obligations beyond attribution for unmodified use.
- **Alternatives:** dropping to the fallback embedding model would not remove Pillow/certifi.

### D-010 Embeddings run in FP32 by default
- **Decision:** FP32 everywhere (never FP16). BF16 is ~2.7× faster on CPUs with native BF16, but most laptops
  lack it. It may become an opt-in setting later.
- **Model notes:** text-only load (`vision_config`/`audio_config` = None, 271M params). The full 1.49 GB
  weights file must still be downloaded because it is a single unsharded file. Reranker scores are already
  0..1 (sentence-transformers applies a sigmoid). HHEM's tokenizer comes from a pinned, tokenizer-only copy of
  `google/flan-t5-base`, because its remote code otherwise fetches it unpinned.

### D-011 Pinned ML runtime set
- `torch 2.14.1+cpu`, `torchvision 0.29.1+cpu`, `transformers 5.19.0`, `sentence-transformers 6.1.0`,
  `tokenizers 0.23.3`, `huggingface-hub 1.33.0`, `safetensors 0.8.0`, `numpy 2.5.3`, `pillow 12.3.0`,
  `cactus-needle 3.2.0`. All four models were verified together in one process with no network access
  (RSS ~2.7 GB, so the app container needs at least 4 GB RAM).

### D-012 Frontend contract interpretations
- **Decision:** the backend returns every field a capability can have, and the UI hides the ones that don't
  apply to the unsaved form (API key only when the preset needs one, API version only for Azure, gate endpoint
  fields only when not reusing chat, and so on). An API key left empty is not sent, so the saved key is kept;
  "Remove saved key" sends `""`. Pause and cancel apply to queued and running jobs, and reordering applies to
  queued jobs only. The last opened space stays active, because clearing it on navigation races in StrictMode.
- **Reason:** switching presets must not need a round trip, and an empty password field must never erase a key.

### D-013 Frontend dependencies
- **Decision:** icons come from `@tabler/icons-react` (MIT) instead of shadcn's default `lucide-react`
  (ISC, not on the allowlist). Production dependencies are 48 MIT, 1 Apache-2.0 and 1 0BSD (`tslib`, pulled
  in by Radix, accepted under D-009). TypeScript is pinned to 6.0.x because typescript-eslint does not yet
  support 7.

### D-014 FastAPI's OpenTelemetry dependency stays inert
- **Decision:** accept `opentelemetry-api`, which FastAPI's core now requires. Never install
  `fastapi[standard]` or any OpenTelemetry SDK or exporter. The image also sets `OTEL_SDK_DISABLED=true`.
- **Reason:** the API package on its own is a no-op that cannot export anything. SPEC requires no telemetry.

### D-015 SearXNG engines and pinning
- **Decision:** pin `searxng/searxng:2026.10.9-f4822b3fc@sha256:85492ba8…` and keep only DuckDuckGo, Brave,
  Bing (switched on explicitly) and Wikipedia. JSON output is on, the limiter and image proxy are off, and the
  port is not published.
- **Reason:** this release disables Startpage and Mojeek upstream (CAPTCHA). Several general engines give
  fallback when one rate-limits (DuckDuckGo and Brave were rate-limited from the dev host; Bing answered).

### D-016 Startup probe for SearXNG; bundled status checks the runtime
- **Decision:** at startup the app calls SearXNG's `/healthz` (retrying for about a minute while it boots) and
  stores the result as the card's last test. Bundled capabilities show an error when the ML runtime is not
  installed, not only when model files are missing.
- **Reason:** Gate 0 needs every card to show its real status. `/healthz` sends no search words to any engine.

### D-017 Release build details
- **Decision:** release images are a single plain `linux/amd64` manifest (`provenance: false`), built with the
  GitHub Actions layer cache. The CI image check builds without models (D-006). The download script is mounted
  for its build step only, not copied into the image. The model self-check runs with `--network=none`.
- **Podman:** `service_completed_successfully` (used by `searxng-init`) needs podman-compose ≥ 1.1 or Podman
  with the docker-compose provider. This is noted in the README.

### D-018 Calibration measures what this container can really use
- **Decision:** CPU count = affinity mask capped by the cgroup quota (`/sys/fs/cgroup/cpu.max`), not
  `os.cpu_count()`, which reports the host's cores inside containers. STT speed is timed on an 11 s public-domain
  speech clip (JFK inaugural, from whisper.cpp at a pinned commit, SHA-256 checked) baked next to the Whistle
  model, because Whistle skips non-speech almost instantly (a tone measured RTF 0.002). Calibration re-runs when
  `CALIBRATION_VERSION` rises (now 2). Phase 1 bumps it when the document parser arrives.
- **Also:** the image build's self-check transcribes that clip and requires the exact words.

### D-019 Known issue: Whistle oversubscribes threads when a container sees more CPUs than it may use
- **Finding:** the Needle engine sizes its thread pool from `/sys/devices/system/cpu`, not from the process
  affinity or cgroup quota, and offers no thread setting. On this dev host (Docker inside an 8-CPU LXC that
  shows 32 CPUs) STT runs at RTF ≈ 0.8 instead of ≈ 0.09, and pinning to 2 CPUs gives RTF ≈ 2.25.
- **Decision:** keep Whistle (it is correct, and on Docker Desktop, Ludwig's setup, every visible CPU is
  usable). Calibration stores the real RTF, so estimates stay honest. Revisit in Phase 1 when STT is used for
  audio and YouTube: if calibrated RTF > 1.0, prefer the SPEC fallback (`pywhispercpp`, which takes an
  explicit thread count) or a configured STT endpoint.

## 2026-10-10 — Phase 1

### D-020 Bundled models were slow in containers: thread count and FP32 (Gate 0 finding F1)
- **Finding:** Ludwig's 32-thread laptop (Docker Desktop, WSL2) measured 4 chunks/s and 8.9 pairs/s. That
  is the same raw throughput as the 8-CPU dev container (~470 GFLOP/s embedding, ~560–620 ranking, at
  ~117 GFLOP per 450-token chunk). So his container effectively used about 8 cores' worth.
- **Cause:**
  1. Trustbook never set a model thread count. Torch picks one once, at import, from the CPU topology the
     container shows, which inside Docker Desktop/WSL2 or nested containers need not match the CPUs the
     process may use: here the LXC shows 2 "physical" cores for 8 usable CPUs, and Docker shows 32 CPUs
     for 8 usable. Jobs call the models from worker threads, and OpenMP/MKL keep their thread settings per
     calling thread, so even a correct import-time setting is not guaranteed to reach them.
  2. Everything ran in FP32, although the CPU has native BF16 (AVX-512 BF16).
- **Decision:**
  - Calibration (version 3) records the CPU: model, visible CPUs, usable CPUs, physical cores, BF16.
  - It measures embedding speed at several thread counts (all usable CPUs, physical cores, half) and keeps
    the fastest.
  - It compares BF16 against FP32 for embeddings and the reranker. BF16 is kept only if it is ≥ 1.25×
    faster and gives the same results: every embedding at cosine ≥ 0.99 to its FP32 twin, every reranker
    score within 0.03.
  - The chosen thread count is applied in every thread that calls a model. Before calibration, the default
    is all usable CPUs.
  - The grounding checker always runs in FP32 (grounding correctness before speed). FP16 is never used.
- **Result on the dev container:** indexing 2.25 → 11.05 chunks/s, ranking 6.9 → 23.8 pairs/s (7 threads,
  BF16 for both). Ludwig's machine could not be measured from here. The Speed test now shows
  "uses U of L CPUs · T model threads · BF16/FP32", so Gate 1 confirms the fix there.
- **Note:** BF16 and FP32 embeddings of the same text agree at cosine ≥ 0.99, so a space indexed before a
  recalibration stays usable.

### D-021 "High" power is capped (Gate 0 finding F3)
- **Decision:** High = max(2, usable CPUs − 2), capped at **8** workers and by memory:
  (memory − 4 GB) / 1.5 GB, where memory is the cgroup limit or the machine total. It never goes below Normal's 2.
- **Reason:** 30 workers on a 32-CPU laptop. The models are loaded once per process and shared, but every
  running job holds its own document and parser state (Docling in Phase 1), and parallel model calls only
  compete for the same cores.

### D-022 Speech-to-text shown as "N× faster than real time" (Gate 0 finding F2)
- **Decision:** the stored value stays the real-time factor (processing seconds per audio second). The UI
  shows 1/factor as "N× faster than real time", or "N× slower than real time" when the factor is above 1.

### D-023 Linked folders index code with the fallback chunker until Phase 3
- **Decision:** in Phase 1, code files in a linked folder are chunked with the generic chunker from SPEC §7.3
  (blank-line blocks, target 400, max 800 tokens; locator = file + lines). Phase 3 adds tree-sitter for
  C#, TypeScript, JavaScript and Python.
- **Reason:** Gate 1 runs the smoke set, which includes 5 code questions about `psf/requests`. The §7.3
  chunker is already specified, and its locators are file + line ranges, as the later code checks need.

### D-024 Secret scanning of linked folders starts in Phase 1
- **Decision:** run `gitleaks detect --no-git` on every linked folder before indexing (SPEC §7.4), from
  Phase 1 instead of Phase 3. Files with findings are skipped and reported by file, line and rule ID only.
- **Reason:** data safety ranks above features. Without the scan, a secret in a linked folder could end up
  in a chunk sent to a remote chat endpoint. The gitleaks binary is MIT and small.

### D-025 Headless-browser fallback is plain Playwright, not Crawl4AI
- **Decision:** when Trafilatura gets < 200 characters, render the page with Playwright 1.63 (Apache-2.0)
  and Chromium's headless shell (BSD-3-Clause, third-party notices shipped), then run Trafilatura on the
  rendered HTML. The browser blocks requests to private, loopback and link-local addresses and to `file:`,
  and blocks downloads and service workers.
- **Reason:** Crawl4AI's LICENSE adds an attribution requirement on top of Apache-2.0, so it fails the
  license rule. It also pulls 62 packages (a LiteLLM fork, two browser drivers, nltk) and writes to HOME on
  import. SPEC §15: the most widely used MIT/Apache option.

### D-026 Phase 1 parsing and index stack
- **Docling** `docling-slim==2.137.0` (MIT), with only the extras for PDF/DOCX/PPTX/XLSX and RapidOCR
  (Apache-2.0, PP-OCRv6 small, torch backend). Baked models: layout
  `docling-project/docling-layout-heron@8f39ad3c…` (Apache-2.0); TableFormer "accurate"
  `docling-project/docling-models@2199320848…`, the commit that dual-licenses the same weights as
  Apache-2.0 (the `v2.3.0` tag Docling asks for says CDLA-Permissive-2.0 only). Docling gets the calibrated
  thread count (`AcceleratorOptions`), since its stages otherwise call `set_num_threads(4)` (D-020).
  Remote services and external plugins are explicitly off. Offline use was verified with no network access.
- **pypdf** 6.20 (BSD-3) stays as the SPEC fallback and as the fast "does this PDF have text?" check.
- **Trafilatura** 2.3.1 (Apache-2.0); the snapshot date is the fetch date (its own guess is unreliable).
- **yt-dlp** 2026.8.19 (Unlicense), plain install (the `[default]` extra adds GPL `mutagen`), `cachedir=False`.
- **LanceDB** 0.40 + pyarrow 26 (Apache-2.0), native FTS, embedded; no network (verified with strace).
- **Token counts** come from EmbeddingGemma's own `tokenizer.json` (already baked); no new dependency.

### D-027 Further transitive license exceptions (extends D-009)
- `tld` (via Trafilatura → courlan) is MPL-1.1 / GPL-2.0 / LGPL-2.1+: we take **MPL-1.1**, unmodified.
- `opencv-python-headless` (Apache-2.0, required by Docling's TableFormer) bundles LGPL-2.1 FFmpeg
  libraries, GCC runtime libraries (GPL-3 with the runtime exception) and OpenSSL 1.1.1k (end of life;
  Trustbook never uses OpenCV's networking).
- `av` (PyAV, BSD-3-Clause) decodes audio for STT (YouTube audio, mp3/m4a uploads). Its wheels bundle LGPL
  FFmpeg, dynamically linked and unmodified. There is no allowlisted decoder for AAC/Opus.
- Chromium's headless shell ships `LICENSE.headless_shell` with third-party (incl. LGPL) notices. It is kept
  in the image, and Playwright's separate ffmpeg (video recording only) is deleted.
- **Reason:** no allowlisted alternative provides these capabilities. All are used unmodified, as
  separately replaceable shared libraries.

### D-028 Phase 1 UI choices
- **Wording:** chunks are called "passages" in the UI. "Using X of Y sources": X = ticked and ready, Y = ready.
- **Refresh:** also offered for `search` sources (they are web pages). The home folder itself cannot be linked
  as one source (too broad).
- **Deferred:** the time estimate before large adds (SPEC §11) comes with the estimates work in Phase 2,
  as SPEC §14 lists it.
- **Bundle size:** the size warning limit is 800 kB (561 kB bundle, served from localhost only).
- New runtime dependency `@radix-ui/react-dropdown-menu` (MIT, accessible menus). New dev dependency
  `@playwright/test` (Apache-2.0).

### D-029 Grounded chat choices (Phase 1)
- A sentence that cites any chunk id not given in the prompt is dropped, even if its other ids are valid
  (stricter than SPEC §8 layer 3; grounding first).
- A reply that is unusable twice, or whose sentences all lack valid ids, is refused as "No answer with
  citations" (`invalid_output`). An empty `sentences` list is "Not in your sources".
- Token budget: the top chunk is always kept; chunks that don't fit are skipped and smaller ones still tried.
- If full-text search fails, retrieval falls back to vector hits, still filtered and reranked, and still
  subject to the refusal threshold.
- Overview: a generic "main topics" query plus each source's first chunk, reranked, then chosen
  round-robin across sources within 8 chunks / 6,000 tokens. Citations are forced. It is stale when the set of
  enabled, ready sources (or their snapshot times) changes.
- **The overview is generated automatically only when the chat endpoint is local.** When it is remote, the
  card shows the token estimate and waits for "Write overview" (SPEC §11: estimates before the overview when
  the endpoint is remote). It is never refreshed automatically.
- Layer 1 is enforced by a test that follows every import reachable from `app/chat` and `app/retrieval` and
  fails on any ingestion, parsing, search, fetching or socket code.

### D-030 Calibration gets the CPU to itself
- **Finding:** on a fresh install, calibration ran while the first sources were being indexed. The contention
  made BF16 look no faster (so embeddings stayed FP32) and calibrated speeds came out about 3× too low.
- **Decision:** the bundled models sit behind a writer-preferring readers-writer lock. Parsing, embedding,
  retrieval and the overview use it shared. Calibration takes it exclusively: it waits for current model work
  to finish, and new model work waits until calibration is done (about a minute, once).

## 2026-10-10 — Phase 2 (Gate 1 follow-ups)

### D-031 G1: power labels show the real worker counts
- `Settings.workers_by_power` returns the capped counts (D-021). The UI labels each option from it; Ludwig saw
  "High: 30 workers" while 8 were used.

### D-032 G3: code answers get the definitions of the constants they use
- **Finding:** code-03 answered "DEFAULT_POOLSIZE / DEFAULT_POOLBLOCK" instead of 10 / 10 / False, because the
  values sit in other chunks of the same file.
- **Decision:** after selecting the context, retrieval finds UPPER_CASE names used but not defined in each code
  chunk, and adds the chunk that assigns each one (`NAME =` or `NAME:` at column 0; same file first, then same
  source). At most 3 are added. They are ordinary numbered chunks the model may cite, appended after the
  ranked chunks. Total context may reach 11 chunks but never exceeds 6,000 tokens; definitions push out only
  the lowest-ranked chunks, never the top one or the chunk that needs them. The refusal threshold still uses
  rerank scores only.
- **How:** a query-time `GLOB` over `space.db` (~1.2 ms per name on 3,600 chunks); no new table.
- **Eval:** `must_contain` is now a target metric (`--min-must-contain`). Refused answers count as misses, and
  a run with no keyword questions fails the target.

### D-033 G4: the secrets report is viewable
- `GET …/sources/{id}/secrets` returns file, line and rule ID only (other fields of `secrets.json` are never
  passed on). The source row shows one button, "N files skipped: possible secrets", which opens the report.

### D-034 G5: readable refusal suggestion
- Built from the user's own question: the leading question words are stripped, "how do/can I …" becomes
  "how to …", the result is trimmed on a word boundary and quoted: `Add sources that cover “…”, then ask
  again.` A question with nothing meaningful left gets a generic sentence, and `no_sources` has its own
  fixed sentence. The card title is the refusal message; the subtitle is only the suggestion.

### D-035 G6: the source viewer gets context from the server, not character offsets
- **Cause:** the backend's offsets are Python code points, correct in `snapshot.md`. The browser sliced the
  whole snapshot with UTF-16 indices, so every emoji before the chunk (HISTORY.md in `requests`) shifted the
  highlight. Loading the 3.7 MB snapshot also showed the top of the file briefly.
- **Decision:** `GET …/chunks/{id}/context` returns either the cited file's lines with a highlighted line range
  (code view: monospace, line numbers, commit; whole file up to 2,000 lines, else ±200 lines), or
  before/highlight/after strings with a page, section or timestamp label. Any locator with a file and line
  numbers uses the code view. The viewer computes its scroll position from line numbers before the first
  paint.

### D-036 G7: failed sources are unticked
- A source that fails is stored unticked, and ticking it returns 409. Unticking or renaming still works (group
  "untick all"). A refresh re-ticks it when the refresh is queued, and it is unticked again if the refresh
  fails. The UI disables the checkbox and leaves failed sources out of group states and counts.

### D-037 G8: web search ranking
- SearXNG queries carry `language=en` (a request parameter; SearXNG stays unmodified). For queries of three or
  more words, results that are bare homepages (no path, `/`, a locale-only path such as `/sv-se/`, or an index
  page) are moved below the other results, for every provider, before the result limit is applied.

### D-038 G2: indexing one large source scales across cores
- **Biggest cause (a bug):** folders indexed generated files as text. In `requests`, `ext/requests-logo.ai`
  (an Illustrator file stored as PDF text) produced 3,017 of 3,572 chunks, and the logo SVG one 727k-token
  chunk: 93% of all tokens. Folders now skip `svg ai eps ps`, text or code files whose content starts with
  `%PDF-` or `%!PS`, and code files with a line over 10,000 characters (minified or generated). These are counted
  as `skipped_generated`. Real PDFs still go to Docling. `requests` is now 554 chunks.
- **Parallel embedding:** `RuntimeOptions` gains `embedding_streams` (1–8) and `stream_threads`. With more than
  one stream, ingestion uses separate embedder replicas (own model and tokenizer; HF fast tokenizers are not
  thread-safe). Replicas load lazily and are kept until the next reconfigure (about 2.2 GB at 4 × BF16).
  Chat keeps one shared instance on `torch_threads`, so reranking, the grounding checker and Docling are
  unaffected. Replicas must fit in memory minus 4 GB, else fewer streams are used. Batches of 8 are
  length-sorted, longest first, and pass through a bounded queue.
- **Calibration v5** times 1×T*, 2×U/2, 4×U/4 and 8×U/8 (at least 2 threads each, if memory allows) on
  ~685-token code-like chunks, and keeps more than one stream only if it is at least 1.1× faster. It stores
  `embedding_chunks_per_s_code`.
- **Reading:** folder files are read on up to 8 threads in path order. Docling conversions run one at a time
  (it has its own threads). Chunking stays sequential (0.28 s for `requests`, limited by the GIL).
- **Writing:** chunk rows go to `space.db` first, vectors to LanceDB every 512 chunks, and the full-text index
  is rebuilt once at the end. A cancelled or failed run removes both.
- **Model loads take turns:** transformers sets a process-wide default dtype while building a model, and
  concurrent loads produced broken BF16 replicas.
- **Result (8 CPUs):** 300 real code chunks went from 5.69 to 10.24 chunks/s (4 streams × 2 threads, cores busy
  4.0 → 7.3). The whole `requests` folder went from 19 min to 79 s.

## 2026-10-10 — Phase 2 (core)

### D-039 Answer verification (grounding layers 4–5)
- The premise for each check is the cited chunk exactly as the model saw it, including a code chunk's
  `File: … (lines a-b)` header, so claims about file names can be checked. A sentence's score is the maximum
  over its cited chunks; it passes at ≥ `thresholds.verify` (0.5).
- **The backticked-name check applies to every sentence**, not only those citing code (stricter than the
  contract; follows SPEC §8). The only allowance is a trailing `()`. A source without a readable `map.json`
  fails all its file and line citations.
- More than half failing → one regeneration with an extra instruction → the refusal rule (> 50% fail or the
  first fails → `unsupported`). If the regeneration is unusable, the answer is refused. Removed sentences go
  to `removed_sentences` and the UI's collapsed drawer. Citations are renumbered over the kept sentences.
- "Checker off" means the grounding capability is disabled (badge `unverified`; layer 5 still runs). If it is
  enabled but the model is unavailable, the answer ends in an error, never an unverified answer.
- `diagnostics` records the top rerank score, each sentence's score (0.0 for a code-check failure, `null` when
  the checker is off) and whether the answer was regenerated. The eval sidecar holds only ids and numbers,
  never question text.
- **Inline citation markers:** models sometimes pack several sentences into one `text` with markers like
  `["c1", "c2"]`. Text is split at each marker; each part cites its own marker's ids, trailing text uses the
  declared ids, and markers never reach stored text.
- **Answer prompt:** one rule added: "Be specific: give the exact values, names and conditions the chunks state,
  not general descriptions." Answers had dropped details such as `raise_for_status` or latin-1. No grounding
  rule was changed.

### D-040 Gate router
- The gate (default: the chat endpoint, temperature 0, 5 s limit, only the question is sent) classifies each
  question as `sources`, `brainstorm` or `chitchat`, and every decision is logged to `gate_log`. It never
  produces an ungrounded answer:
  - `chitchat` gets a fixed reply without an answer call.
  - `brainstorm` runs the normal grounded answer plus an "Open Ideation" hint.
  - A gate that is not ready, fails or times out counts as `sources`, with `confidence: null`.
- Corrections are made in the space's gate log dialog. `{"decision": null}` clears one. The answer carries
  `gate_log_id` for a later inline "That's not right" button.
- The eval runner counts the chitchat reply as a refusal (reason `chitchat`).

### D-041 Estimates and queue ETA
- Estimates come from calibration rates converted to tokens per second and back, because calibration chunks
  (686 tokens for code, 351 for prose) differ from real chunks. The real sizes were measured on `requests` with
  the production tokenizer: code 1,125 bytes / 360 tokens per chunk, text 1,400 bytes / 445 tokens.
- **Heuristics:**
  - Documents: pages from bytes by type (pdf 25 KB, docx 20 KB, pptx 60 KB, xlsx 50 KB per page).
  - Audio: duration from bytes and typical bitrates.
  - Fixed costs: url 8 s, youtube 15 s, folder 15 s + 0.02 s per file.
  - Without calibration: about 700 tokens/s, labelled "rough estimate (speed test not run yet)".
- The folder walk is the ingestion walker itself, and only sniffs the first 64 KB of large files.
  `requests` is predicted at 80 s against a real 79 s.
- Estimates are stored in the job payload (`estimate_s`). Queue ETA = the sum of remaining estimates divided
  by min(workers, jobs), but never less than the longest single remaining job. Jobs report `eta_seconds`.
- The UI asks to confirm adds above 2 minutes. If the estimate call itself fails, the add goes ahead.

### D-042 Verification reads a sentence's cited chunks together; qualified names are matched by part
- **Finding:** the first smoke run with layers 4–5 falsely refused 3 of 12 answerable code questions.
  - code-03 "the default pool_connections is 10" needs two cited chunks together:
    `pool_connections=DEFAULT_POOLSIZE` and `DEFAULT_POOLSIZE = 10` (the G3 definition chunk). Scored one
    chunk at a time it got 0.07.
  - code-02's first sentence used `Response.ok`, which never occurs verbatim in code (`class Response` …
    `def ok`). The layer 5 failure then triggered the first-sentence refusal rule.
- **Decision:**
  - A sentence citing two or more chunks is also scored against all of them joined, in citation order.
    The score is the best of the single and joined scores. The premise is still only what the sentence
    cites, so no grounding layer is weakened.
  - A backticked span passes layer 5 if it occurs verbatim in a cited chunk, or if every identifier and
    number in it occurs there as a whole word (exact, case-sensitive). A span without identifiers, such as a
    quoted literal, must still occur verbatim.

### D-043 The gate asks reasoning models not to think
- **Finding:** with Ludwig's llama-server (Qwen3.6, a reasoning model), every gate call failed. The 200-token
  limit ran out while the model was still thinking, so the reply was empty, and each failed call kept the
  server busy while the real answer waited. With 1,024 tokens it worked but took 15 s.
- **Decision:** chat adapters take `think`. With `think=False` the OpenAI-compatible adapter sends
  `chat_template_kwargs: {"enable_thinking": false}` (honoured by llama-server, vLLM and LM Studio). If the
  endpoint answers 400, the field is dropped and the request is sent again, before any other fallback; OpenAI
  rejects unknown fields. Only the gate uses `think=False`. Answers keep thinking. Measured: 0.6–1.0 s per gate
  call, correct decisions for a question, a greeting and a brainstorming request.

### D-044 Verifying claims about code: focused premises and a literal check
- **Finding:** the 45-question tuning run (smoke + refusal set; refuse threshold 0 to collect data) had 23/23
  correct refusals and 91% precision, but 6/22 answerable questions were refused. Thresholds weren't the
  lever: verify 0.3–0.5 gave identical results. Four of the six were refused by verification of correct
  claims about code. Offline tests showed HHEM scoring a true claim ("DEFAULT_POOLSIZE is set to 10") against
  a chunk containing `DEFAULT_POOLSIZE = 10` at 0.18, and a true and a false claim about Basic auth at
  0.08 and 0.01. Long code chunks (up to ~950 tokens, beyond HHEM's 512-token training length) drown the
  lines that matter.
- **Decision:**
  - **Layer 4:** each sentence is also scored against a focused window: the lines (code) or sentences
    (prose) of its cited chunks that share its content terms, with neighbours, in order, at most 1,200
    characters. The window is cut only from cited text. With focused premises HHEM separates true from false
    claims (0.69 vs 0.06; 0.94 vs 0.02).
  - **Layer 5 (code citations):** every number and every `True`/`False`/`None` in a sentence citing code
    must occur as a whole word in the cited chunks. HHEM still scores "pool_block is True" 0.56–0.71
    against a `False` premise, so this deterministic check covers it.
- The two other false refusals were the model itself answering "not in the chunks" (one with a top rerank
  score of 0.14), a retrieval matter, not verification.
- **Code facts (second iteration):** on live data the focused window still missed the line that gives the
  value, and HHEM rated even the right raw lines borderline (0.48). The window now leads with plain
  restatements of the simple facts the cited code states, followed by the best-matching lines:
  - `NAME = value` becomes "NAME is value."
  - A parameter line `name: type = DEFAULT,` becomes "name defaults to DEFAULT, which is 10." (the constant
    is resolved only if its definition is also cited).
  - Only literals, constant names and empty calls are restated.
- Offline, restated facts scored 0.88 for true claims and 0.03 for false ones. On Ludwig's data, the HTTPAdapter
  defaults answer went from refused to three verified sentences at 0.95–0.97.
- **Third iteration:** restated facts rank ahead of raw lines at equal overlap, and the window keeps at most
  six units, so noise cannot drown the fact. The literal check applies `True`/`False`/`None` only to value
  claims ("is", "defaults to", "set to", "equals"). "returns True if the host changes" describes behaviour,
  which code rarely spells as a literal. Numbers are always checked.
- **Known limit:** claims describing what code does ("joins the username and password with a colon") still
  score low. HHEM cannot read `b64encode(b":".join(...))`. Such answers stay refused or partially verified
  (grounding before usefulness). Code-aware verification is a Phase 3 item (see docs/BACKLOG.md).
- Thresholds stay at refuse 0.15 / verify 0.5. The sweep showed no gain from moving them.

## 2026-10-11 — Phase 3

### D-047 Code chunking with tree-sitter
- Official per-language MIT wheels: tree-sitter 0.26.0, tree-sitter-python 0.25.0, tree-sitter-javascript
  0.25.0, tree-sitter-typescript 0.23.2 (TS and TSX), tree-sitter-c-sharp 0.23.5. tree-sitter-languages was
  rejected because it is unmaintained.
- **What makes a chunk:**
  - A class chunk holds what isn't a method or nested class, in contiguous runs. Its symbol is the class name;
    methods are `Class.method`.
  - Decorators, attributes and adjacent comments belong to their definition.
  - A C# property gets its own chunk only if its accessors have code.
  - Namespaces add no symbol.
  - Module code is grouped up to 500 tokens, and a single statement is split above 800. Symbols are split above
    1,500 tokens at statement boundaries.
  - A file with any syntax error uses the SPEC §7.3 fallback.
- On `requests`: 554 → 993 chunks (code 425 → 864), mean 379 → 227 tokens, max 1,492. No file falls back.
  Chunks are whole lines, never overlap, and lose nothing but brace-only lines.

### D-048 Incremental refresh
- Per file: SHA-256 of the raw bytes, kind, chunker version, its offsets in `snapshot.md`, and its chunk ids
  (space migration 004).
- Unchanged files are neither decoded nor re-parsed (Docling is skipped too). Their text and map segments are
  copied into the rebuilt snapshot, and their chunks keep ids, text and vectors; only offsets move, so old
  citations still resolve.
- Changed, new and removed files are re-indexed or deleted.
- Records are trusted only if their chunks still exist and match. A failed run forces a full re-index next time.
  `CHUNKER_VERSION` forces re-chunking whenever chunk output changes.
- **Known gaps:** refresh estimates still assume a full re-read, and a reused PDF loses its OCR quality warning.

### D-049 Commits ahead without the git binary
- dulwich 1.2.17 under its Apache-2.0 option; its count matches `git rev-list --count old..new`.
- `commits_ahead` is null when a commit is missing (shallow clone), the history is unreadable, or the count
  exceeds 10,000. HEAD is cached for 5 s per folder, counts per commit pair.
- `ahead` is shown only for ready folder sources whose HEAD differs from the indexed commit.

### D-045 Claims about code: a strict chat-model judge, anchored to the cited lines
- **Why:** HHEM (prose NLI) can't judge claims about code (D-044). Symbol/literal matching alone would pass false
  claims ("joined with a semicolon" matches the same lines as "with a colon"), which would weaken grounding.
- **Decision:** a sentence that cites code and fails only the checker gets one judgement for the whole answer.
  The judge runs at temperature 0, `think=False`, with a JSON schema and a 30 s limit, and sees only the cited
  code with real line numbers. The sentence passes only if **all** hold:
  - the judge says supported;
  - every evidence range lies inside a chunk the sentence cites;
  - every code anchor of the sentence occurs in the evidence lines (anchors: backticked spans, identifiers,
    numbers, quoted strings, value-claim `True`/`False`/`None`; character names such as "colon" or "semicolon"
    must occur inside a string literal in the evidence; letter-only acronyms like HTTP or URL are prose, while MD5
    or DEFAULT_POOLSIZE are anchors);
  - layer 5 still passes.
  A sentence with no anchors needs half its content words in the evidence lines. Names may also match the
  chunk's header line or `locator.symbol`; a dotted name needs its last part in the evidence.
- The judge can rescue a sentence, never fail one. A failure or timeout leaves the checker's result. The setting is
  `grounding.code_judge` (default on). `diagnostics.sentence_methods` records `checker` / `code_judge` per
  sentence. The judge prompt is verbatim in `app/grounding/code_judge.py`.
- **Measured** (Qwen3.6-35B-A3B; real reranker and HHEM; tree-sitter chunks): 17/18 claims as expected, 10
  statements judged in 9.2 s.
  - Every false claim failed: semicolon, SHA-1, UTF-8, only-the-password, pool_block True, pool_maxsize 20,
    Digest default SHA-256, SHA-1/BLAKE2, `rate_limited`.
  - True claims passed via the judge: colon + base64, the "Basic " prefix, pool_connections 10, Digest MD5
    default, MD5-SESS/SHA-256/SHA-512.
  - The one miss is the strict D-042 backtick rule (`requests.codes` not in the cited chunk).

### D-046 Retrieval agreement guard
- A chunk in the top 3 of **both** the vector and full-text lists is kept even if the reranker (a prose
  cross-encoder) drops it. It replaces the lowest-ranked kept chunks within the token budget, never the top
  chunk. The refusal threshold still uses rerank scores only.
- **Why:** for ref-near-08 and ref-near-10 the right code chunk was #1 in both lists and was reranked out of the
  context.

### D-050 Mock test hooks
- `TB-UNSUPPORTED` appends one unsupported sentence last. 1 good + 1 bad is exactly half, so the answer is
  `partially_verified`, not refused.
- The mock code judge and the `mock` web-search provider exist only when `TRUSTBOOK_MOCK_LLM=1`: the registry
  refuses the provider otherwise, and the adapter factory refuses to build it.

### D-051 Full-text search ignores chunk headers' line numbers
- **Finding:** code chunks start with `File: path · symbol (lines a-b)`. Full-text search indexed those numbers,
  so "HTTP 429" first matched chunks whose *line numbers* contain 429, and the status table ranked low.
- **Decision:** the text indexed for full-text search keeps the path and symbol but drops `(lines a-b)`.
  The stored chunk text is unchanged. `CHUNKER_VERSION` was bumped so refreshes re-index.

### D-046 (extended) The top of the fused list is guarded too
- Besides chunks in the top 3 of **both** lists, the top 3 of the RRF-fused list are kept even when the prose
  reranker drops them. Measured on tree-sitter chunks: the Basic-auth function chunk ranked vector #14 /
  full-text #3, and the status table vector #8, so the stricter rule alone missed them. Rerank scores still
  decide refusals.

### D-052 Citations are pruned to the chunks that support a sentence on their own
- **Finding:** precision fell to 86.4% because models over-cite. A true sentence about netrc look-up also
  cited two Quickstart chunks that only mention `.netrc`, and passed on the joined premise.
- **Decision:** when at least one cited chunk passes the checker on its own, only such chunks stay cited. When
  only the joined or focused premise passed, all stay, because they support the sentence together. Sentences
  rescued by the code judge keep their citations, since the evidence anchoring already ties them to cited lines.

### D-053 A web or document chunk is named by the section holding most of it
- A chunk starting in the last lines of one section was labelled with that section (ref-near-09). The
  `heading` is now the section covering most of the chunk. When it spans several, `headings` lists them all,
  and the eval runner accepts any of them.

### D-054 Memory: release indexing replicas; score the grounding checker in small batches
- **Finding:** in the Gate 3 dry run the app was killed by the out-of-memory killer (`OOMKilled=true`)
  while verifying an answer right after indexing in the same container. Two causes stacked up:
  - The embedding replicas from the parallel streams (D-038) stayed resident after indexing (~2.2 GB).
  - The grounding checker scored all pairs of an answer in one batch, padded to the longest premise. Joined
    premises could reach 32,000 characters, and T5 attention memory grows with the square of the length.
- **Decision:**
  - When an ingest job ends and no other ingest job is waiting, the replicas are released. They load again
    for the next indexing job; chat never uses them.
  - The grounding checker scores in length-sorted batches of 4, with premises capped at 6,000 characters
    (~1,500 tokens; HHEM was trained on 512, and focused windows are 1,200 characters).

### D-055 Gate 3 rework: process decisions (Ludwig, 2026-10-11)
- **Comparable runs:** eval runs use temperature 0 and a fixed seed through a per-request generation override
  that only the eval runner sends. The app's SPEC default (temperature 0.2) is unchanged. One of the three
  workbench gate runs deliberately uses that default, labelled "real-use", to show behaviour in normal use.
- **Model comparison:** the runner takes `--chat-endpoint` / `--chat-model`, and the report header names the model.
  Gate 3 is also run with a stronger reference model (`Qwen3.8-27B-UD-Q4_K_XL` on Ludwig's server, his choice) so failures can be
  attributed to the pipeline or to the model. Results go to `docs/MODELS.md` with a recommended minimum model.
- **Job status, not only source status:** e2e and eval assert the ingest **job** status and the Queue UI state for
  every source type. A bare `raise` once failed every job while every source looked ready.
- **Calibration follows the pipeline:** the calibration version is bumped whenever chunking or embedding changes,
  tied to `CHUNKER_VERSION` and the embedding model revision so it cannot be forgotten (the Phase 3 chunker left
  the Phase 2 "Indexing code" rate in place).
- **Scope:** Phase 3b (agent CLI + Claude Code skill) is queued after Gate 3 passes, not mixed into the fixes.

### D-056 Chunking: data literals split by element, locator kind, document files on whole lines
- **Finding (ref-near-10):** the `_codes` table in `status_codes.py` (lines 23-104, ~1,300 tokens) was one
  module-level statement. It was cut only at its inner comments and packed with the docstring, giving a
  747-token chunk (lines 40-91) without a symbol. That chunk held the 429 row but was vector #7, absent from
  the full-text top 40 and reranked #40 of 40, so it never reached the context. A prototype with 150-token
  groups put the 429 chunk at vector #1 / full-text #2 / fused #1 (in context), and brought the right row into
  context for 6 of 6 entry questions (base: 3 of 6). Smaller groups did the retrieval work; the symbol in the
  header is a small full-text gain and shows the model the table's name.
- **Decision (data literals):** an assignment of one collection literal to one name at module or class level is
  a member of its own above `DATA_MIN_TOKENS` (250), syntax-driven for all four grammars (Python
  dict/list/tuple/set, also wrapped in one call such as `frozenset({…})`; JS/TS object/array incl. `as const`,
  `Object.freeze(…)`, `module.exports =`, `export default`; C# initializer and collection expressions incl.
  `new Dictionary<K, V> { … }`). It is cut only where an element starts a line, so multi-line elements stay
  whole, and the comment lines above an element go with it. Groups are balanced around `DATA_TARGET_TOKENS`
  (150): parts = ceil(total / 150), and a small last group joins the one before. Each group is named by the
  qualified assignment (`_codes`, `FlaskyStyle.styles`, `Codes.Names`) with a part number in the header
  (`… · _codes (part 6) (lines 77-85)`); `locator.symbol` is the bare name. Chunks stay verbatim whole lines.
  Full-text search drops the part number with the line numbers (D-051).
- **Decision (locator kind):** code locators carry `kind`: `function` | `method` | `class` | `module` |
  `data`, and `null` for fallback-chunked code and document files. A function inside a class (any depth) is a
  `method`; a class's own lines are `class`. Retrieval can now look up a function's definition by symbol.
- **Finding (ref-near-07):** the document chunker cuts at sentence ends, so a file chunk could end inside a
  line while its locator claimed the whole line. `advanced.rst` 639-693 stopped after the second of three
  sentences on line 693; the viewer and the eval showed line 693, including the `socks5h` sentence the chunk
  did not hold, and the model cited that chunk for the `socks5h` claim. Setting `end_line` to the last whole
  line instead would have dropped the partial text from the range (the other half of the same error).
- **Decision (whole lines):** chunks of document files located by line (folder sources, not paged PDFs) end
  on line ends. Units are also split at line ends and marked "line start" / "clean start" (a sentence,
  paragraph, list item or table row starts there). When the target is reached inside a line, the chunk ends at
  the latest line end with at least half the target before it, preferring a clean one; the rest of the line
  starts the next chunk, and the 80-token overlap also starts on a line. Only one line longer than the 800-token
  maximum is cut inside, and its locator then names the line(s) the text is part of. A line break counts as one
  token, as in the code chunkers. Web pages and paged documents are unchanged (their locators name sections or
  pages, not lines). This is a general rule: any line-located file in any language.
- **Measured** on the `requests` folder source with the production tokenizer (the Smoke snapshot re-chunked by
  the image's chunker and by this one):
  - Code: 856 → 872 chunks; only `status_codes.py` (5 → 12) and `docs/_themes/flask_theme_support.py`
    (3 → 12) change. `_codes` is 8 parts of 164-182 tokens; line 85 (429) is in part 6, lines 77-85. The
    largest chunk drops from 1,492 tokens (`FlaskyStyle.styles`) to 1,261. Mean 191 → 188 tokens.
    872/872 chunks are exact line ranges (before 856/856).
  - Kinds: method 487, function 145, class 74, module 66, data 18; null 219 (137 document + 82 fallback code).
  - Document files: 129 → 137 chunks, mean 445 → 424 tokens, max 585 → 543. Chunks whose line range holds
    exactly their text: 99/129 → 137/137 (the other 30 ended or started inside a line). 98 of 107 neighbour
    pairs still overlap (before 90 of 99). Line 693 of `advanced.rst` is now whole in chunk 681-723.
- `CHUNKER_VERSION` = `p3-treesitter-4`: refreshes re-chunk every file.

### D-057 Calibration is tied to the chunker and the embedding model (Gate 3 item C)
- `CALIBRATION_VERSION` = 6 (the Phase 3 chunks). Saved results also record `pipeline` =
  `calibration-<version>|chunker-<CHUNKER_VERSION>|embeddings-<model id>@<revision>` (the revision from
  `models.lock.json`). On startup calibration is queued when the saved version is older **or** its pipeline
  differs from the installed one, so a chunking or embedding change re-measures without anyone remembering to
  bump the version. Results saved before v6 have no pipeline and are re-measured.
- Not changed: the estimate's chunk-size constants (`app/ingest/estimate.py`, measured on the Phase 2 chunker:
  code 1,125 bytes / 360 tokens per chunk). Re-measured on `requests` with this chunker: code 540 bytes / 188
  tokens per chunk, documents 1,318 / 424. Estimates convert through tokens per byte, which moved little
  (code 0.320 in the constants vs 0.348 measured, documents 0.318 vs 0.322), so code estimates run about 9%
  low. Left for the estimate owner; the per-chunk embedding overhead of twice as many code chunks is not
  modelled either way.

### D-058 A split literal's part number is not code; its definition is its parts (review of D-056)
- **Finding (layer 5):** the deterministic checks searched the whole chunk text, header included. Each split
  data literal's header says `(part N)` with N from 1 to about 12, so "`_codes` … retries 6 times" citing part 6
  passed the literal check (`missing_literals` returned only the other wrong number) and could then be rescued
  by the code judge. A layer-5 failure is never rescued, so the part number weakened layer 5.
- **Decision:** `missing_literals` and `missing_identifiers` drop the header's `(part N)` before searching
  (`app/chunking/header.py`, which also owns `header_line`). Nothing else changes; the checks are only
  stricter. The header's `(lines a-b)` is still searched, as before D-056: the prompt shows every chunk as
  "file, lines a–b", so a sentence restating where code is ("lines 23–104") must keep passing in any language,
  and removing them would reject true location statements. Their hole (a value equal to a range end) predates
  this phase and is unchanged.
- **Finding (Gate 1 G3):** constant definitions are found by a `NAME = ` line at column 0, which only part 1 of a
  split literal has. For `ERROR_MESSAGES = {…}` (30 entries, 513 tokens) used as `ERROR_MESSAGES[code]`, the
  extra was part 1 (lines 1-8) while 'E025' is in part 4. Before D-056 the table was one chunk. `requests` has
  no UPPER_CASE literal over 250 tokens, so the D-056 measurement could not show it.
- **Decision:** a definition that is part 1 of a split literal brings the literal's parts (same source, file and
  `locator.symbol`, consecutive part numbers), counted as **one** of the 3 extras, up to 800 tokens (the
  chunker's module chunk maximum, so a literal of that size comes in whole as before). A bigger literal brings
  the parts the reranker scored highest for the question first, then the parts in order; they are added in
  line order and go through the same budget fit as other extras. Part 1 alone in the context no longer counts
  as the name's definition, so the rest still comes. Lower-case literals (`_codes`) are not constants and are
  unaffected. General: syntax-driven for every grammar the chunker splits.

### D-059 Retrieval ties are ordered by content, never by write order or uuid
- **Finding (variance diagnosis):** LanceDB returns full-text hits with equal BM25 scores in an order that depends
  on the order rows were written, and cuts a tie group anywhere; RRF broke ties by chunk id, a new uuid4 on every
  index. Measured on the Smoke space (live table searched twice + 4 tables rebuilt in other write orders, 45
  questions): the full-text top 40 differed for 10/45 questions and the fused top 40 for 6/45. Repeating the same
  search 7 times on the live table changed the full-text and fused top 40 for 1/45 (ref-near-10).
- **Decision:**
  - Every `Hit` carries `key` = SHA-256 of the indexed text. Vector and full-text search fetch twice the limit,
    doubling while the tie group at the cut is still open, then sort by (score, key, chunk id) and cut.
  - RRF sorts by (score, best single rank, key, chunk id). Chunk ids only decide between identical texts.
  - Rerank ties keep the fused order, which is now fixed; the fused rank is evidence, a hash is not.
- **Measured after:** 0/45 differences in the vector, full-text and fused top 40 across the same 6 tables, and 0/45
  over 7 repeated searches. Only the order among exactly equal scores changes: no threshold, guard or grounding
  layer is touched, and refusals still read the top rerank score. Test: indexes rebuilt in three write orders under
  fresh uuids give identical top-40 lists (LanceDB and the in-memory index), and so does the full `retrieve`.

### D-060 Callee definitions: the context gets the bodies of the functions its code calls
- **Finding (code-05):** for "How does requests encode the username and password when it builds an HTTP Basic
  Authorization header?" the chunks matching the wording are call sites (`HTTPBasicAuth.__call__`, the proxy
  headers, a test), all `headers[...] = _basic_auth_str(user, password)`. The body that answers it (auth.py 34-75)
  is rerank #10 and never reached the context (8 chunks, 2,252 of 6,000 tokens). The model answered from the call
  site and cited the test, or said nothing (refused `not_in_sources`). The prose reranker moves that body between
  #1 and outside the top 40 depending on wording, so no rank cut-off fixes it. D-032 already solved the same shape
  for constants.
- **Decision (generalises D-032 to functions):** after the guards and the constants step, calls in the context's
  code chunks (`name(`, `self.`/`this.`/`cls.name(`) are resolved to code chunks of the same source whose
  `locator.symbol` is the name (or `Class.name` for the caller's class) and that are a function or method
  (`locator.kind` when the chunker records it; else a definition line in the text: `def`, `function`, arrow
  `const`, C#/TS method signature, AL `procedure`). Symbol-less chunks qualify only from the fallback chunker
  (the syntax chunker's symbol-less chunks are module-level code). The callee must be one of the question's
  reranked candidates (fused top 40), outside test files and the context. Most callers first, then rerank
  position; at most 2, within the token budget, never displacing the top chunk, a calling chunk or a constant.
  Only chunks made by a code chunker count as callers or callees (code examples in `.rst`/Markdown files do not).
- **Why the candidate gate:** without it the prototype added unrelated helpers (fixtures, class bodies) to 21/45
  questions; with it 9/45 get 1-2 extra chunks (code-05, ref-near-01/07/10, ref-unans-12/15/17/19), all real
  callees.
- **Grounding:** unchanged. Added chunks are ordinary numbered chunks with their rerank score; the model must cite
  them and every layer applies. The refusal threshold still reads only the ranked list.
- **Measured (live Smoke space, Qwen3.6-35B-A3B, code judge on):** the body enters the context for the eval
  wording and for paraphrases that ranked it #27 and #9.
  - code-05 eval wording, 2 runs: verified (5 sentences) and partially_verified (4 kept, one true "base64 encoded"
    sentence removed by the code judge's word-share rule, a separate verification issue). latin1, colon, base64
    and "Basic " in both; all 9 kept citations are auth.py 34-75. Before: answer from the call site citing
    tests/test_requests.py 554-570, or `not_in_sources`.
  - Paraphrase "How is the HTTP Basic auth header value constructed from the credentials": verified, 4/4
    sentences, all citing auth.py 34-75.
- **Known limit:** a callee outside the fused top 40 is not added (e.g. "When I pass auth=('user','pass') ...").
  One level only.

### D-061 Exact-copy dedup of the context: measured, not adopted
- **Proposal (code-02 diagnosis, optional):** while filling the 8-chunk context, skip a chunk whose body is ≥ 0.95
  word-3-gram contained in an already selected chunk (never the top or a guarded chunk) and take the next one.
- **Measured** on the Smoke space with D-059/D-060 retrieval, 45 questions: 5 contexts change. The skipped copies
  are the web "Basic Authentication" section (1.00 inside authentication.rst 1-65; code-05, ref-unans-19), the web
  "Transport Adapters" section (0.99; ref-near-09) and `Response.__nonzero__` (0.97 inside `__bool__`; code-02,
  ref-near-10). None of the dropped or the newly added chunks is an expected locator; the added ones score
  0.06-0.33 (a proxies section, two tests, two small code fragments).
- **Decision:** not adopted. It brings no expected evidence into any context, adds low-ranked noise, and removes
  one source's copy before generation, so a space loses that source's citation even where the user or a key wants
  it (web page vs. repository file). code-05 no longer needs the slot (D-060 adds its body outside the 8-chunk
  count). Over-citation of copies is a citation question and is handled after verification, where the sentence
  is known (the code-02 / docs-04 citation-collapse work in the chat pipeline).

### D-062 Callee lookup follows qualified calls, keeps a constant's referrer, scans each candidate once
- **Review findings on D-060:** (1) only bare and `self.`/`this.`/`cls.` calls were followed, so the usual call
  shapes in other code (C# `AuthHelpers.BuildBasic(…)`, Python `utils.basic_auth(…)`, Go `pkg.Func(…)`, Java
  static utilities) never found their helper even when it was a ranked candidate; (2) a callee body could displace
  the chunk a D-032 constant had been added for, leaving the constant in the context with nothing referring to it;
  (3) `defines()` re-scanned every symbol-less (fallback-chunked: Java, Go, Rust, PHP, AL, …) candidate with six
  regexes for every call, measured at 1.87 s (8 × 32 Java chunks of 3.7 KB) and 7.58 s (heavier) per question.
- **Decision:**
  - Qualified calls `Q.name(` (last qualifier of a chain) resolve to a function or method whose symbol is `Q.name`
    (a static or class method), or a module-level `name` whose file is `Q.*` or lies in a folder `Q` (Python module,
    Java class file, Go package, `Q/__init__.py`). Symbol-less fallback chunks need that file/folder match too, so
    `obj.method(` on an arbitrary object never matches a same-named function elsewhere. `super.`/`base.` and calls on
    expressions (`x[0].f(`, `f().g(`) are not followed. All other D-060 gates are unchanged: same source, ranked
    candidate (fused top 40), not a test file, not in the context, at most 2.
  - `RetrievedChunk.referrer` records the context chunk an added chunk was found for; `with_callees` protects the
    referrers of constants as well as the constants (the D-032 / `fit_extras` guarantee across both steps).
  - Candidates are indexed by (source, function name) once per question; a symbol-less candidate's definitions are
    computed once and reused. Same 8 × 32 Java benchmark: 0.01 s.
- **Measured on the Smoke space (45 questions, branch retrieval, no LLM calls):** the callees chosen are identical
  to the D-060 rule on all 45 (requests calls its helpers bare), so nothing measured before changes. Tests cover the
  C# static helper, the Python module function (a same-named function in another module is not taken), Java/Go
  fallback files, the orphaned-constant reproducer and the one-scan-per-candidate bound.
- **Grounding:** unchanged; no threshold, verification or refusal rule is touched.

### D-052 (extended) Near-copy citations collapse to the defining chunk
- **Correction to D-052's finding:** the two Quickstart chunks in docs-04 do not "only mention `.netrc`": they repeat
  the look-up sentence word for word ("Requests will search for the netrc file at `~/.netrc`, `~/_netrc`, …") and
  pass the checker alone (0.95–0.97). D-052 correctly kept them; nothing chose among chunks that each support a
  sentence. The same happened in code-02 (`Response.ok`, `__bool__` and `__nonzero__` share a docstring; each
  0.97 alone) and ref-near-05 (a `Session.__init__` comment restates the docs, 0.98 alone).
- **Decision (checker-passed sentences only; judge-rescued sentences keep their citations):**
  1. D-052 as before.
  2. Among the chunks that support the sentence alone, a chunk whose supporting window (the units sharing the most
     terms with the sentence) is ≥ 90 % word-3-gram contained in an already kept chunk's window is dropped.
     Preference order: the chunk whose `locator.symbol` the sentence names (full dotted name, `Class.name`, a
     backticked last part, or a last part of ≥ 4 characters as a word), then the one the question names, then the
     higher rerank score for the question, then context order. Windows under 4 shingles are never called copies.
  3. Guarded upgrade: a kept citation whose symbol the sentence does not name is swapped for a better-ranked
     context chunk whose symbol it does name, only if that chunk's window contains the cited window (≥ 90 %) and
     the chunk passes verification alone (checker or judge, no layer 5 problems).
  4. A choice that would leave a backticked name or a literal outside the kept chunks is not made.
  Citations are only removed when a kept chunk supports the sentence alone; `diagnostics.citation_changes` lists
  every drop and swap (chunk ids only).
- **Why 90 % and the best-units window:** focus windows (D-044) of the docs-04 copies overlapped only 0.11–0.73,
  so they would not collapse; best-units windows were 1.00 for all true copies (docs-04, code-02) and 0.70–0.79
  for genuinely different support (`__bool__` vs `ok` for "a response in a boolean context returns self.ok").
- **Measured** (live Smoke space, real HHEM, reranker and judge; 25 recorded model replies from the diagnoses,
  replayed through the old and the new pipeline): distinct citations correct 52/58 (89.7 %) → 45/45. code-02
  `[ok, __bool__, __nonzero__]` → `[ok]`; docs-04 → Authentication only; ref-near-05 `[SSL web, advanced.rst,
  Session.__init__]` → `[SSL web]`. No answer changed outcome because of the choice.

### D-063 Gate 3 rework: the judge for every citation, confirmation, citation completion, regeneration, eval generation
(Review corrections in D-064: the prose rescue and citation completion below were withdrawn, rescues are asked
again alone, and digit-term abbreviations are one-letter initials only.)
- **Judge for every citation type (rescue; withdrawn by D-064).** The D-045 judge now rescues any sentence that
  fails only the checker, whatever it cites. **Why:** HHEM fails correct paraphrases of technical prose (ref-near-07: "resolve hostnames on
  the proxy server instead of your own machine" vs the source's "resolve the domains on the proxy server", 0.34–0.36),
  and whether a sentence got the judge depended on provenance (the same text as `.rst` was judged, as a web page not).
  Prose chunks are numbered from 1, a line per sentence (lines over 160 characters split at sentence ends), headed by
  title and section. Same evidence anchoring and anchor/content-word rules; the prompt says "excerpts (source code or
  documentation)" and adds "direction (which side does what)". In prose evidence a named character ("comma") may
  match the word itself.
- **Judge confirms every checker pass (veto).** HHEM passes role-swapped false claims: "With socks5h, DNS resolution
  happens on the client" 0.718–0.98, "This causes DNS resolution to happen on the proxy server rather than the
  client" citing the chunk that says the opposite 0.58–0.60, "setting trust_env to True" 0.739, an invented
  `HOME_NETRC` variable 0.857, "Response.ok returns True only if the status code is exactly 200" 0.702.
  **Band:** measured on 71 statements (43 true, 28 false; 9 questions), false checker passes scored 0.58–0.98 and
  true ones 0.56–0.99, so no band separates them: every checker pass is confirmed (`CONFIRM_BELOW = inf`). A veto
  needs the verdict to say unsupported, name evidence outside the sentence's citations, or name evidence lines
  missing an anchor, **and** the same when the sentence is asked again alone: in batches of similar statements
  the judge rejected true sentences stated verbatim in a docstring ("returns True when the status code is between
  200 and 400"), 4 times in 3 answers; alone it accepted 5/5 such true sentences and vetoed 9/9 false passes.
  Content-share shortfalls and missing verdicts never veto. Vetoed sentences get `sentence_scores` 0.0 and are
  listed in `diagnostics.judge_vetoed`.
- **Measured:** batch of 71 statements: all 10 false checker passes vetoed in the batch (each of the 9 distinct
  ones again when asked alone), 0 of 32 true checker passes vetoed, 11/11 true checker failures rescued, 0/18 false
  checker failures rescued. On the 25 replayed
  replies, the live false pass (c2 role swap) was removed; no answer was refused that was answered before.
  **Latency:** one judge call per answer that has sentences: 1.4–8.4 s (mean 3.4 s over 25 answers, Qwen3.6-35B-A3B
  on Ludwig's shared server, no thinking), plus ~1–1.5 s per vetoed sentence for the repeat.
- **Grounding:** confirmation can only fail sentences (stronger). The prose rescue made checker failures on prose
  weaker, which D-064 corrects. Judge failures keep the checker's result (as D-045).
- **Digit-bearing names (judge vocabulary).** `b64` ↔ base64, `sha256` ↔ SHA-256, `utf8` ↔ UTF-8 match in both
  directions: equal keys after dropping case, `-` and `_`, or a letter-prefix abbreviation with the same digits and
  suffix (a one-letter abbreviation needs a word of ≥ 4 letters). sha1/sha256, utf8/utf16, md4/md5, base32/b64,
  e2/ec2 and s3/sha3 do not match (tests). Hyphenated prose terms (SHA-256, UTF-8) are anchors; they match the same
  term in any spelling or, as before, their numbers as whole words. Backticked spans stay exact.
- **Two exact-match fixes found while measuring (both stricter or neutral):** a backticked span no longer matches
  inside a longer name (`socks5` is not in `socks5h`), in layer 5 and in anchoring; Markdown escapes are removed
  before matching (web pages store `trust\_env`; a true sentence naming trust_env was vetoed).
- **Citation completion (ref-near-10; withdrawn by D-064).** Before verification, a sentence whose backticked name is missing from its
  cited chunks but present in another context chunk also cites it (same file first, then context order, at most 2;
  nothing when a name is in no context chunk). Every layer then runs on the new set. Measured on current chunks
  (ref-near-10's table chunk 40-91 added to the context by hand, since retrieval misses it until the chunker fix):
  the `requests.codes` sentence gained status_codes.py 1-39 and lost its layer-5 failure, but the judge did not
  accept it (checker 0.25), so it still fails; `requests.codes.rate_limited` and `codes.TOO_MANY_REQUESTS` get
  nothing added and fail layer 5. With retrieval as it is, ref-near-10 stays `not_in_sources` (2/2 runs).
- **Prompt rule:** "Use the chunks' own words for names and facts. Do not replace them with the question's
  wording." The regeneration note repeats it. (Diagnosis sample: HHEM-only first-sentence failures 4/8 → 1/6.)
- **Regeneration (§15 decision; SPEC §8 is silent on the first-sentence case):** the one regeneration also runs when
  only the first sentence failed; the refusal rule applies to the second reply unchanged.
- **Eval generation override (F5).** `POST …/messages` takes `"generation": {"temperature": 0..1, "seed": int}`.
  Default unchanged (0.2, no seed). The seed goes to the answer, gate and judge calls; OpenAI-compatible/Azure send
  `seed` (dropped on a 400 naming it), Anthropic ignores it. Diagnostics gain `generation` and
  `retrieval_fingerprint` (16 hex of SHA-256 over the ordered context chunk ids).
- **Live before/after (real LLM, temperature 0.2, 2 runs each, current chunks):** code-02 verified 2/2 (all
  citations `Response.ok`), docs-04 2/2 answered with correct citations only (one run regenerated after its first
  sentence listed `~/.netrc` twice and was vetoed), ref-near-05 verified 2/2 citing the SSL section, ref-near-07
  verified 2/2 (the question-worded paraphrase rescued by the judge in both), ref-near-10 `not_in_sources` 2/2 (retrieval).

### D-064 Review corrections to D-063: no prose rescue, no citation completion, rescues asked twice, initials only
Four review findings on D-063, each checked with a reproducer and fixed, never by loosening a threshold:
- **Prose rescue withdrawn.** Rescue is back to the D-045 scope: only sentences citing code, judged on their cited
  code chunks only. **Why:** for prose the deterministic rescue gates do almost nothing. Prose has no anchors, so
  the only gate left is "half the content words are in the evidence line", and negations and direction words
  are stopwords. Reproduced: with a judge "supported", `assess()` accepted "With socks5h, DNS resolution happens
  on the client instead of the proxy server" (a role swap), "Requests does not resolve DNS on the proxy server
  when socks5h is used" (content share 0.75, no anchors) and, for a PDF page saying "The budget was not approved,
  so the meeting was moved to Friday", "The meeting was moved to Friday because the budget was approved". So a
  checker failure on prose (HHEM's home domain) came down to one verdict from the model that wrote the answer.
  The 0/18 safety figure came from 71 statements on one English corpus. A negation/direction-word gate would be
  language-specific (Swedish "inte" is not English "not"), so it is not a general fix; prose stays checker-only
  for rescue. Confirmation (vetoes) still covers prose; it can only fail sentences.
- **Rescues are asked again alone.** A rescue from the batch now needs the same "accepted" when the sentence is
  judged alone, symmetric with the veto (D-063 measured batch noise; a judge-only pass is the riskiest path).
- **Citation completion withdrawn.** It rewrote the model's citations before layer 5, so a sentence citing the
  wrong chunk for a backticked name passed layer 5 when any context chunk held the name (the added chunk was
  chosen because it holds the name, not because it supports the claim; reproduced with "Set `max_redirects` to 0
  to stop Requests from reading netrc." citing a netrc chunk, completed with `sessions.py`). SPEC §8 layer 5 says
  such a sentence fails. Completion also did not fix ref-near-10 (D-063: still `not_in_sources`).
  `citation_changes` loses `added`.
- **Digit-term abbreviations: one-letter initials only.** `terms_match` accepted any letter prefix, so `sha256`
  matched `shake_256` and `py3` matched `pypy3` (a judge anchor for SHA256 was "found" in `hashlib.shake_256`).
  Now only a one-letter initial of a word of ≥ 4 letters abbreviates (`b64` ↔ base64, `h2` ↔ http2); equal keys
  (`SHA-256` ↔ `sha256`) are unchanged.
- **Measured** (live Smoke space, real HHEM and judge, the 10 recorded replies of the D-063 before/after run
  replayed): every outcome, method and citation unchanged. The 3 rescues (code-02 run 2 s4, ref-near-07 both runs,
  which cite `docs/user/advanced.rst` from the folder source) were accepted again alone; judge time per answer
  1.5–6.1 s (ref-near-07 run 2: 3.2 → 6.0 s, one extra call per rescue). ref-near-07 still depends on the folder
  source's `.rst` chunk being cited; a sentence citing only the web page would now fail on the checker.

### D-065 Eval runner: generation override, chat model switch, job checks, run comparison
SPEC §13 and D-055 do not say how the runner does these things. Choices made under §15:
- **Generation:** the default is `{"temperature": 0, "seed": 42}`. `--temperature` and `--seed` change it, and
  `--app-default` sends nothing (the "real-use" run). The header states the settings and that a seed is not
  honoured by every endpoint: the variance diagnosis measured 2–3 different answers in 3 identical calls on
  llama-server (`--parallel 2`, speculative decoding) even at temperature 0 with a seed. An API that rejects the
  field stops the run (exit 2) rather than silently running at the app default, because a report must never
  claim settings it did not use.
- **Chat switch:** an endpoint is saved as the `custom` preset, which covers every OpenAI-compatible server.
  The endpoint is tested once before the run, so a typo fails in seconds rather than after 45 refusals. The
  original preset, base URL, model and API version are restored in a `finally` (Ctrl-C and SIGTERM included),
  then read back and compared. Keys: the backend deletes a saved key when the chat origin changes, so that it
  never leaks to another host. The runner never sees keys, so it could not restore one. An override to
  another origin while a key is saved is therefore refused before anything changes. The header names the
  model and `host:port` read back from the API after the switch, not the flag values.
- **Job checks:** the runner waits for each finished source's ingest job and compares the two statuses, as
  set out in `eval/format.md`. Any problem fails the run (exit 1), like a missed target, because the F1 bug
  was invisible in source status. An entry can declare `expect: failed`. Old jobs no longer listed (only the
  last 50 finished are kept) are noted, not failed, but only for sources this run did not add. Jobs that
  ended before the run: D-066.
- **Comparison:** `--compare A.json B.json` lists only questions whose outcome cell differs. Retrieval is "same"
  by `retrieval_fingerprint`. For sidecars without one, an equal top rerank score is used instead and labelled
  "same top score", which is weaker evidence. Repeated runs (`--repeat`) were not added: three separate runs
  plus `--compare` cover F4 and keep the runner simple.
- **Sidecar version 2** adds `generation_requested`, `chat`, `setup.jobs`, and per question
  `retrieval_fingerprint`, `generation` and `must_contain_found/total`. It holds no question or answer text
  (D-039's rule).

### D-066 Eval runner: jobs that ended before the run, and `--refresh`
Review of D-065 found a problem. A gate re-run with `--setup` on a space indexed by `phase-3-rc` always exited 1.
That image failed every ingest job after indexing the source correctly (F1). Those failed jobs stay listed in
`GET /api/jobs`, so every source showed as a "job/source mismatch". The cause was that image, not the one
under test. Choices under §15:
- **Earlier job:** the runner reads `GET /api/jobs` before it adds anything. A job of a source already in the
  space that had ended by then may have run on another image. It is noted ("job/source mismatch from an
  earlier ingest" when it was one), and not judged. The source's current status is still judged, because this
  run's answers depend on it: a `failed` source that should be `ready` still fails the run. A job that was
  running when the run started, or that the run started itself, is always checked in full. The metric names
  how many jobs were not re-checked, so a pass on earlier jobs alone is visible as such.
- **`--refresh`** (with `--setup`) re-ingests every corpus source already in the space through the refresh
  route, so every job runs on the image under test and is checked in full. A refused refresh fails the run.
  The README tells the gate's first run on a new image to use it. The check itself was not loosened: a
  mismatch on a job of the running image still fails the run.
- **Sidecar:** `setup.refreshed` (count) and `setup.jobs[].earlier` are added. The sidecar stays version 2,
  because both fields are additive.

### D-067 e2e: every source type's ingest job must agree with its source (item B)
- `frontend/e2e/job-status.spec.ts` adds one source of each type and waits until **both** the source
  (ready/failed) and its job from `GET /api/jobs` (via `source.job_id`) have finished. A source turns ready
  a moment before its job closes, so polling only the source would miss a job that fails afterwards (the F1 bug).
- **The invariant** (helper `expectSourceMatchesJob`): ready ⇔ job `done` with no error; failed ⇔ job `failed`
  whose `error` equals the source's `failure_reason`. After each test every source left in the space is
  checked the same way. The Queue page must list the job under "Recently finished" with the matching
  badge (Done/Failed), the reason for a failure, and never under "In progress and waiting".
- **Per type:** paste through the UI (Done), text-file upload through the UI (Done), PDF upload (the existing
  OCR spec now also checks its job), folder (Done; needs `E2E_BROWSE_ROOT`), LAN URL (Failed, local-network
  reason), web search with the mock provider (Failed, local-network reason: the search runs in the backend, but
  the test rewrites the two specific results to literal private IPs `10.255.0.1` / `172.31.255.1` with
  Playwright `page.route` before the page sees them, so adding them needs no DNS lookup and no connection).
- **No network by default** (CLAUDE.md "no network calls in tests", SPEC §13 "no real endpoints in CI"): the
  public web page (`https://example.com/`, Done) and YouTube ("Me at the zoo", Done **or** Failed with a reason,
  since captions may be blocked; the outcome is a test annotation) need the real internet, so they run only with
  `E2E_INTERNET=1`. `release.yml` does not set it, so the release gate never depends on outside sites; there is
  no runner-side probe either. The whole suite passes against a mock container started with `--dns 127.0.0.2`
  (no working resolver), which shows nothing else needs the network.
- **Open for Ludwig:** item B asks for URL and YouTube job coverage; doing that offline in CI would need a seam
  that lets the backend fetch a local fixture server, i.e. relaxing the SSRF address guard under the mock flag.
  That was not done (security layer). Until he decides, run `E2E_INTERNET=1 pnpm e2e` by hand for those two.
- **The bug class itself:** after a successful paste the header processing indicator returns to empty, the
  Queue shows exactly one job for the space, Done, no Failed row and no unfinished list, and the API lists the
  space's jobs as `["done"]`.
- No app code or API changed; the Queue's existing `job-<id>` test ids and badges were enough.

## 2026-10-11 — Phase 3, Gate 3 round 2 (chunking)

### D-068 Document files carry their section: heading path in the chunk header, headings in every common form
- **Finding (ref-near-03, false refusal in 2 of 4 gate runs):** "Which requests release was the first to officially
  support CPython 3.12?" retrieved HISTORY.md 172-213, which holds "Requests has officially added support for
  CPython 3.12" (line 188), but the release heading "2.32.0 (2024-05-20)" (line 159, a setext heading) was in the
  previous chunk. The model guessed the version ("preceding 2.31.0") and the judge rightly vetoed it, or it named
  2.32.0 and layer 5 failed it ("2.32 is not in the cited code"). Two causes: a document file's chunk said only
  `File: <path>`, and the chunker only knew Markdown `#` headings, so the setext and reStructuredText headings of
  every `.md`/`.rst` file in `requests` never started a chunk or named a section.
- **Decision:** document files of folder sources (`app/chunking/headings.py`, `dispatch.py`):
  - Headings: ATX (`#`…`######`, column 0), underlined titles after a blank line (adornment of one repeated
    character, ≥ 3 long) and over- and underlined titles, outside fenced code (a `~~~~~` adornment is tried as a
    heading before it is taken for a fence). Markdown files (`.md`, `.markdown`, documents parsed from PDF/Word/…)
    underline only with `=` (level 1) and `-` (level 2) as CommonMark defines, and skip YAML front matter; other
    text files (`.rst`, `.txt`, …) accept every reStructuredText adornment, levels in order of first appearance
    (docutils' rule). An adornment other than `=`/`-` must be as long as its title; bullet items, table rows,
    quotes, directives and field lists are never titles. *(Corrected by D-071: AsciiDoc and Org files get
    their own heading syntax, and reStructuredText/plain text has no `~~~` fences.)*
  - Header: `File: <path> · <heading path> (lines a-b)`, the heading path of the chunk's first line, outermost
    first (` › `; outer headings dropped past 200 characters); `File: <path> (lines a-b)` before the first heading;
    paged documents without `(lines a-b)`. The `File: … (lines a-b)` convention of code chunks is kept, so full-text
    search (drops only the line numbers, D-051), layer 5, the judge's header handling, the viewer, the mock and the
    definition lookup keep working unchanged.
  - Locator: `heading` (innermost heading of the section holding most of the chunk, as D-053 for web pages; `null`
    before the first heading) and `headings` when the chunk spans several. Every document locator has the
    `heading` key and no code locator has it, which is how callee lookup now tells a document chunk from code
    (D-070).
  - Chunking: a heading block (two or three lines for underlined titles) is one unit and starts a new chunk once the
    current one has 100 tokens (as `#` lines always did). New: a section under 100 tokens that starts at its heading
    joins the chunk **before** it when that stays within the 500-token target (otherwise the next one, as before).
    Without this, HISTORY.md's tiny "2.32.1" entry merged forward and the 2.32.0 entry sat under a "2.32.1" header.
    Bodies are whole lines including the first line's indentation (before, 43 of 137 chunks started after it).
  - Web pages, pasted and uploaded documents are unchanged: their chunks have no `File:` line and the prompt and
    judge already name their section from the locator (`section “…”`); their sections stay Markdown `#` only, so
    their chunk boundaries do not move.
  - `CHUNKER_VERSION` = `p3-treesitter-5`: refreshes re-chunk every file, and calibration re-measures (D-057).
- **Measured** on the `requests` folder (the Smoke snapshot's corpus re-chunked inside trustbook-g3 with the
  production tokenizer; scripts in the session scratchpad `r2chunk/`):
  - Document chunks 137 → 173 (code chunks unchanged), mean 424 → 328 tokens, max 543 → 560. HISTORY.md 44 → 63,
    quickstart.rst 12 → 17, advanced.rst 29 → 33, api.rst 5 → 8; the count is unchanged for 22 of 30 files.
  - Line range equals the body exactly (first line's indentation included): 94/137 → 173/173.
  - 170/173 chunks name a heading; the 3 without are files with no heading (two issue templates,
    requirements-dev.txt).
  - The chunk holding HISTORY.md line 188 is `File: HISTORY.md · Release History › 2.32.0 (2024-05-20) (lines
    159-199)`, locator heading `2.32.0 (2024-05-20)`.
- **Known limits:**
  - A heading ending in "(part N)" right before `(lines a-b)` reads as a split literal's part number (D-058): its
    N is then not searched by layer 5 or full-text search (stricter, never looser).
  - A single `.md`/`.rst`/`.txt` file **uploaded** (not in a linked folder) still gets Markdown `#` sections only
    (`sections_from_markdown`), so its setext/reStructuredText headings name no section. Changing that also moves
    web page sections and chunk boundaries, which needs its own measurement; left as a follow-up.

### D-069 The section of a document chunk is context for the judge and its anchors
- **Why:** the judge names the fewest lines (HISTORY.md 188), which never repeat their release heading, so
  "Requests 2.32.0 officially added support for CPython 3.12" would be vetoed "not in the evidence lines: 2.32" even
  with the heading in the chunk.
- **Decision (D-045 rule 3, document file chunks only):** a name, number or hyphenated term of the sentence may also
  be in the **section of the evidence lines**: the header's heading path, or, for lines below a heading inside the
  chunk, that heading's title (`app.chunking.header.section_at`; strict: a line after a later heading never gets the
  header's section). Quoted strings, `True`/`False`/`None` and named characters must still be in the lines. The
  header's heading path is no longer a source of *names* for the whole chunk (`chunk_names` keeps `File: <path>`
  only), so a name in it does not cover lines under a later heading. The judge sees each document excerpt as
  `[cN] <path>, lines a-b, in section “<heading path>”`; evidence numbering stays on the body lines.
  Code chunks are untouched (their locator has no `heading`; a symbol or `(part N)` is never a section).
- **No prompt change:** a judge rule "a section heading applies to the lines under it" was measured and dropped:
  with and without it the live reply and all 8 recorded claims below had the same verdicts and evidence.
- Layer 5 and the checker are unchanged: the premise has always been the chunk text with its header, so "2.32.0" in
  the header passes the literal check exactly like a symbol in a code chunk's header did. *(Corrected by D-071: a
  header number counts only as written in full, never a heading's numbering or the header's line numbers, and an
  evidence range across a later heading gets no section.)*
- **Measured live** (trustbook-g3, Qwen3.6-35B-A3B on Ludwig's server, temperature 0, seed 42, real HHEM and judge;
  the Smoke space only read, nothing re-indexed; script `r2chunk/live_history.py` in the session scratchpad): the new
  HISTORY.md chunks for lines 159-213 (159-199, 193-202, 205-229) with the answer prompt, 5 runs:
  - 5/5 answered 2.32.0 and were verified. 4 replied "Requests 2.32.0 officially added support for CPython 3.12."
    citing 159-199: checker 0.947, judge confirmed with evidence line 188 only (2.32 found in the section). 1 replied
    "Requests 2.32.0 was the first release to officially add support for CPython 3.12." (llama-server is not
    deterministic, D-065): checker 0.114, rescued by the judge (evidence 188, accepted in the batch and alone).
  - The old chunks (134-176, 172-213, 206-251) for contrast: 5/5 named 2.32.0 citing 172-213 and were refused by
    layer 5 ("2.32 is not in the cited code"), the gate's false refusal.
  - Recorded claims citing the new 159-199 chunk (same outcome in all 5 runs): true "2.32.0, released on 2024-05-20,
    officially added support for CPython 3.12" passes (0.915, confirmed); every false claim fails: 2.31.0 / 2.33.0
    (layer 5), 2.32.1 (checker 0.05, judge no support), "2.32.0 dropped support for 3.12" (checker passed 0.556,
    judge vetoed), released 2024-05-22 (layer 5), "support for CPython 3.13" (layer 5). "2.32.0 was the first
    release …" asked on its own failed (0.10; the judge found no support for "first"): an inference, either
    outcome acceptable.
- **Measured, D-063 statement set** (`r2chunk/false_claims.py`: the 51 statements of the D-063 band measurement,
  21 true and 30 false, for ref-near-07, code-02, docs-04, ref-near-05, code-05, code-03; live retrieval, then
  every folder document chunk in the context replaced by all new chunks overlapping its lines; real checker and
  judge on both versions): false claims passing 0/30 before and 0/30 after, including the role swaps citing
  `advanced.rst` (F2, F5, F5b and F4 vetoed or failed). True claims passing 16/21 → 19/21: T3, T4 and T6 citing
  advanced.rst (c3) now pass the checker on the SOCKS section's new chunks (the replay cites every new chunk
  overlapping the old one, so some of that gain may be the extra lines). 15 of the 51 cite a document file.
  Identical in two replays (the second on the final code).

### D-070 Every consumer of the `File:` header checked for the document header
The document header now has the shape of a code header, so each reader of it was checked (tests added where the
behaviour is new):
- `app/index/base.py` `search_text`: drops ` (lines a-b)` only; the heading path is indexed (full-text search finds
  "2.32.0" in every chunk of that release). Unchanged code.
- `app/grounding/code_checks.py` (layer 5, focus window, code facts): the header has always been part of the
  searched text and skipped as a unit, for documents too (`File: <path>` before). *Changed by D-071*: layer 5
  reads only what the header states (no line numbers, no heading numbering) and its numbers only as written in
  full.
- `app/grounding/code_judge.py`: excerpt title and anchors as D-069; the header line is still not numbered.
- `app/retrieval/callees.py`: **changed.** A code chunk was recognised by `File: … (lines a-b)`; document chunks now
  match that too, so `code_path` also requires no `heading` in the locator. Without it, code examples in `.rst`
  files would have become callers and callees (D-060's rule).
- `app/retrieval/definitions.py` (`code_body`, `code_file`, split-literal parts): strips the first line and reads
  the locator; a document's `header_part` is `None` (no `· symbol (part N)`), so nothing changes.
- `app/spaces/chunk_context.py` (viewer): line-located chunks show the file's lines from the snapshot; the header is
  stripped like a code chunk's. Paged documents keep their label (`<file> · Page 3`); pages win over the heading.
- `app/capabilities/adapters/mock.py`: strips any `File:` first line before quoting (the mock never quotes headers).
- `app/ingest/incremental.py` and the viewer's offset check compare the body (`endswith`), so reused file records
  still match; a record from another `CHUNKER_VERSION` is re-chunked.
- Frontend: `Locator.heading` already exists; no change.

### D-071 Section context is checked strictly: what a header states, ranges across headings, heading syntax per format
Review of D-068..D-070 found five ways the section context loosened grounding or named wrong sections; all fixed
(`app/chunking/headings.py`, `header.py`, `app/grounding/code_checks.py`, `code_judge.py`):
- **An evidence range across a heading had the header's section.** D-069 took the section from the range's start
  line only, so evidence 187-194 of the HISTORY chunk (header section 2.32.0, heading "2.31.0" at line 190)
  accepted "Requests 2.32.0 fixed versions up to v2.30.0" (the 2.31.0 entry). Now `section_of(start, end)`: a
  range in which a heading inside the chunk starts after its first line has **no** section (the lines are in
  more than one section and none speaks for all); otherwise the section of its first line as before.
- **Every number of a heading path counted as stated.** Layer 5 searched the whole header and the judge's anchor
  check matched numbers anywhere in the section, so "4 Limits › 4.2 Timeouts" let "defaults to 4 seconds" and
  "4.2 seconds" pass, and "3.0.0 (2025-01-20)" let "keeps 2025 connections", "keeps 3 connections" and "20
  connections" pass. Now, in both guards:
  - What a header states (`header_content`) is its path and section or symbol, without `(part N)`, `(lines a-b)`
    and each heading's **numbering** (a leading `4`, `4.2`, `4.2.` followed by a word: a location like a line
    number; "2.32.0 (2024-05-20)" has no numbering, no word follows the number).
  - A number of the header or section vouches for a sentence's number only **as written in full**
    (`complete_numbers`, `stated_in_full`): `2.32.0` for "2.32.0" (or "v2.32.0"), `2024-05-20` for that date;
    never "2.32", "3.0", "3", "2025" or "20", and not when the sentence also writes the same literal elsewhere.
    Every other number must be in the chunk body (layer 5) or the evidence lines (judge). `True`/`False`/`None`
    and the digits of a hyphenated term are never taken from a header. Names and terms of a section still count
    (D-069), with its numbers blanked out.
- **Line numbers of the header counted as stated.** `missing_literals` dropped only `(part N)`, so "The history
  lists 38 fixes." passed against `File: HISTORY.md · Release History (lines 1-38)`. Layer 5 now drops `(lines
  a-b)` too, for code headers as well (stricter than phase-3, where a code chunk's line numbers counted the
  same way); `missing_identifiers` and the judge's header names likewise.
- **AsciiDoc and Org files got reStructuredText rules.** `[source,yaml]` over `----` and `[NOTE]` over `====`
  became level-1/2 headings, the real `== Section` and `* Heading` lines none. Now the heading syntax follows
  the file (`heading_syntax`): Markdown (`.md`, `.markdown`, parsed documents); reStructuredText rules for
  `.rst`/`.txt`; **AsciiDoc** (`.adoc`, `.asciidoc`): `=`…`======` and `#` titles at column 0, no headings in
  delimited blocks (`----`, `....`, `====`, `****`, `____`, `++++`, `////`, ```), block attributes never titles;
  deprecated two-line titles are not recognised (a missed heading only drops context; a wrong one misattributes);
  **Org** (`.org`): `*` headlines at column 0 (tags dropped), no headings in `#+BEGIN_…`/`#+END_…` blocks. The
  units chunker uses the same per-syntax block lines (`FENCES`).
- **A `~~~~` line in a reStructuredText file opened a code fence** (a transition, or a tilde underline shorter
  than its title) and hid every later heading, so the rest of the file kept a stale section. reStructuredText and
  plain text now fence only with ```; a tilde line is an adornment or a transition.
- **Measured:** the 30 text document files of `requests` chunk exactly as before (174 chunks with the character
  estimate, every header identical; `r3fix/cmp.py` in the session scratchpad), so D-068's corpus measurements
  stand. Unit tests hold every reviewer reproducer (false claims fail, the HISTORY.md true claim passes).
  The D-069 replay of the D-063 statement set (`r2chunk/false_claims.py`, trustbook-g3, real checker and judge)
  on the final code: false claims passing 0/30 old and new, true claims 16/21 → 19/21, every row's outcome
  identical to the D-069 replay (`r3fix/false_claims.log`).
- `CHUNKER_VERSION` stays `p3-treesitter-5` (never released; `requests` chunks are unchanged).

### D-072 Gate 3 round 2: anchors ignore inline markup; plural acronyms are prose; dotted symbols name their parts
- **Finding** (4 gate runs on `p3g`, judge reasons in the container log): true first sentences were vetoed, and the
  first-sentence rule then refused the answer. Three of the veto reasons were matching artefacts, not claims the
  judge rejected:
  - `"not a check to see if the response code is 200 OK."` quoted from the `Response.ok` docstring, whose source
    reads `is **not** a check to see if the response code is ``200 OK``.` (RST emphasis and literal);
  - `URLs`: `_is_identifier` took the plural acronym for a camel-case name (`[A-Z]{2,}[a-z]`);
  - `HTTPAdapter`: a method chunk's class is named only by its symbol `HTTPAdapter.send`.
- **Decision** (all general, none specific to `requests`):
  - Quoted-phrase anchors and verbatim checks (layer 5 backticked spans and the judge's `code` anchors) also match
    after removing inline markup and collapsing whitespace. The source side drops RST ``` ``x`` ```, Markdown
    `` `x` ``, `*x*` and `**x**`; the sentence side drops only ``` ``x`` ``` and `` `x` `` (a sentence's `*test*` is
    a glob, not emphasis). Emphasis needs a non-word character outside its markers and no space inside, so
    `*args, **kwargs` and `2*x*y` are untouched. One trailing `.,;:` of a quoted phrase is not required (English
    puts the sentence's full stop inside the quotes). The exact match is tried first; the normalised one only adds
    matches where every word is present in order: a quoted phrase with one word changed (`status code` for
    `response code`) still fails (test, and live: vetoed, "not in the cited chunks").
  - **Review correction:** underscore emphasis (`_x_`) is not removed. It turned Python sunder names into words on
    both sides (`_value_` → `value`, `self._value_` → `self.value`), so layer 5 passed `` `self.value` `` against a
    chunk with only `self._value_` and `` `_missing_` `` against prose saying "missing".
  - **Review correction:** a quoted phrase must match at word boundaries and may not start right after a negation
    (not, no, never, nor, cannot, neither, …n't), in the raw and in the normalised text. Stripping `**not**` had
    made "a check to see if the response code is 200 OK" a substring of "is not a check to see …", so the inverted
    quote was accepted (as a rescue, too). The raw path was a plain substring test before, so this is also
    stricter than phase 3. A true sentence that negates outside the quote (`is not "a check …"`) is now vetoed:
    a false refusal, never a false pass.
  - Two or more capitals followed by `s` (URLs, APIs, IDs) is a prose word, like HTTP and URL (D-045). Names with
    digits (`MD5s`) stay anchors.
  - A chunk's names (its header line and `locator.symbol`) include each dotted component (`HTTPAdapter.send` →
    `HTTPAdapter`, `send`), from the symbol and from the header's symbol (a split literal's `(part N)` excluded).
- **Grounding:** nothing passes that would not pass with the source's markup typed out; layer 5 still needs every
  backticked name in a cited chunk.

### D-073 Confirmation evidence lines count as their docstring field; rescues keep evidence-line anchoring
- **Finding:** a checker-passed true sentence was vetoed when the judge said *supported* but named too few lines:
  "When max_retries is left at its default, Requests does not retry failed connections." cites `HTTPAdapter`
  (adapters.py 158-199) whose docstring has `:param max_retries:` on one line and "By default, Requests does not
  retry failed connections" two lines below, in the same field; the judge named only the latter, so the D-063 rule
  "evidence lines missing an anchor" vetoed it.
- **Decision:** for a sentence the checker passed (a confirmation), each evidence line of a code chunk counts as
  the whole **docstring field** it is in: a reStructuredText field (`:param name:`, `:returns:`, `:raises X:`) or a
  Google-style entry (`name (type): text`) under a section line (`Args:`, `Returns:`, `Raises:`, …), plus its
  continuation lines (indented deeper, no blank line). A field's name is the subject of every line of it, so the
  binding between the anchor and the judged statement is kept. A verdict still vetoes when it
  1. says not supported (unchanged), or
  2. names evidence outside the sentence's cited chunks or outside a chunk's lines (unchanged), or
  3. names evidence lines that, widened to their docstring fields, lack one of the sentence's anchors (D-063,
     narrowed only by the field). When those lines hold a longer name starting with the anchor (`socks5h` for
     `socks5`) the reason says so.
  Ordinary code, list items (changelog bullets) and prose chunks (web pages, PDFs, transcripts: numbered a sentence
  per line) are never widened. The repeat-alone rule (D-063) is unchanged. A **rescue** (checker failed) still needs
  every anchor in the named evidence lines (D-045). `JudgeOutcome.absent` lists a veto's anchors that are in none
  of the cited chunks, for D-074; it never decides a veto.
- **Review correction (the first version of D-073 is withdrawn):** it dropped the veto whenever every missing
  anchor occurred *anywhere* in the cited chunks (header line included). For a checker pass that is the test layer
  5 had already applied, so the evidence-line binding did nothing: a name or value swapped within one chunk passed
  (reproduced: `` `DEFAULT_RETRIES` is 10 `` next to `DEFAULT_POOLSIZE = 10`; `` `DEFAULT_POOLSIZE` is 64 `` with
  64 only in the header's `(lines 60-64)`; "the warranty lasts 30 days" on a page saying 12 months and returns
  within 30 days; `pool_block` set to `DEFAULT_POOLSIZE`), and it lost the one measured correct veto of the gate
  runs: ref-near-03, "CPython 3.12 support was added in 2.31" citing HISTORY.md 172-213, judge evidence line 188;
  the chunk's next release heading `2.31.0 (2023-05-22)` at line 205 made `2.31` count as found. Its "Measured"
  lists had no same-chunk swap, so they could not show this. All of these are vetoed again (tests).
- **Measured** (live Smoke space in `trustbook-g3`, real HHEM, reranker and judge Qwen3.6-35B-A3B, seed 42; scripts
  `scratchpad/r2c/claims3.py` = the round-2 `r2b/claims.py` 56 claims + 6 review false claims, `r2b/replay.py`):
  - Forced confirmation (checker stub passes everything), twice: 61/62 both times. The `max_retries` sentence is
    kept ("not in the evidence lines: max_retries (in their docstring field)"); the quoted docstring, `URLs` and
    HTTPAdapter sentences pass; every D-045/D-063 false claim fails; the review's false claims fail (2.31 vetoed
    "not in the evidence lines: 2.31"; the negation-dropping quote ×2, `DEFAULT_RETRIES` 10, `DEFAULT_POOLSIZE` 0:
    "the judge found no support"). The one miss is the false "added in the release preceding 2.31.0": the judge
    names lines that hold `2.31.0` and says supported. The `p3g` verifier passes it identically (not a regression;
    the 2.32.0 heading is in the previous chunk, so the judge cannot see which release the entry is under; root
    cause is missing section context, see the gate notes).
  - Real checker: 59/62: the false "preceding 2.31.0" above; the true prose paraphrase at 0.36 (never rescued since
    D-064); the true "mounts one HTTPAdapter" at 0.41, a rescue the judge did not anchor (the strict rescue path is
    unchanged; the first D-073 code and this one fail it identically).
  - 36 recorded first replies (108 sentences) replayed: 107 identical to the first D-073 code; the one difference
    is a code-05 rescue the judge's lines supported this time (judge noise, the rescue path is unchanged). Against
    `p3g`, the `max_retries` sentence vetoed before is kept.

### D-074 The regeneration is told which sentences failed and why
- **Finding:** the one regeneration (D-063) only said "Some sentences were not supported", without saying which. On
  recorded failing first replies the second reply mostly repeated the failure (3 of 4 below; ref-near-10 cited
  `requests.codes` from the table part that never names it again; docs-04 garbled the netrc paths again; the web
  page stores them Markdown-escaped, `\~/.netrc, \~/\_netrc`).
- **Decision:** the regeneration's system prompt adds, after `REGENERATE_INSTRUCTION`, one line per failed sentence
  of the first reply (text ≤ 300 characters, its cited labels) with its concrete reason: for each name or literal
  that no cited chunk holds (layer 5's backticked names, numbers and value literals; the anchors behind a D-073
  veto) which other context chunks hold it under layer 5's test (up to 3 labels), or "is in none of the chunks";
  otherwise "not supported by the cited chunks". The pipeline never edits a citation: the second reply is verified
  as given (D-064 stands). The failed sentences are marked as quoted material.
- **Measured** (real LLM, temperature 0, seed 42):
  - Through the pipeline, ref-near-10 2/2: first reply "The attribute names available on `requests.codes` … are
    `too_many_requests` and `too_many`" citing the table part (c8) failed layer 5; feedback "`requests.codes` is
    not in c8; it is in c1, c2, c3"; second reply dropped the namespace, judge-rescued, **verified** (was refused in
    gate run 2 and the Qwen3.8 run). code-02 2/2 verified on the first reply (no regeneration; the round-1
    refusals were the D-072/D-073 vetoes). docs-04 2/2 still refused: the model writes `~/.netrc, ~/.netrc` again
    (the judge rightly vetoes it; the feedback can only say "not supported").
  - A/B on 4 recorded refused/partial first replies, same chunks, generic note vs targeted feedback: generic 1/4
    verified, targeted 4/4 (docs-04 ×2 now quote the paths in backticks correctly, ref-near-07 adds the `.rst`
    citation and passes, ref-near-10 drops the unsupported namespace).

### D-075 Failed sentences are kept in the answer's diagnostics
- Refused (`unsupported`) and partially verified answers store `diagnostics.failed_sentences: [{"text", "reason"}]`
  for the reply the outcome rests on (the second after a regeneration, unless it was unusable), both capped at 300
  characters. Reasons: the layer 5 problems, `judge veto: <reason>`, `checker score 0.31 < 0.5` (plus
  `; judge: <reason>` when a rescue was refused, e.g. "not in the evidence lines: x (in their docstring field)"),
  or `no citations`. **Why:** a false refusal stored no sentence text, so the gate runs could only be diagnosed by
  re-running the model.

### D-076 Eval sidecar version 3: failed sentence text for false refusals
- The runner copies `failed_sentences` into each question of the sidecar (`null` when the API sent none) and the
  report lists them under "False refusals: failed sentences". The sidecar was "ids and numbers only" (D-039,
  D-065); the failed sentences are the one exception, because diagnosing a false refusal needs them and the report
  already quotes the answers of answered unanswerable questions. Question text is still never written. The
  version is bumped to 3 so a reader can tell; `--compare` reads versions 1–3.

## 2026-10-11 — Phase 3, Gate 3 round 2b (parent/child retrieval)

### D-077 Parent/child retrieval: children are ranked and cited; a parent adds citable siblings and a value-free code outline
- **Context:** Gate 3 round 2 (Ludwig): section context for documents must be the first slice of a general
  parent/child mechanism.
  - Measured failures:
    - ref-near-03: the 2.32.0 heading was in the chunk before line 188. D-068's header fixed it (5/5 live, D-069),
      but only for document files.
    - An answer about a method needs its class (`HTTPAdapter.send`).
    - `requests.codes` needs `_init` and `_codes` (ref-near-10).
    - Constants used in a function are defined at module level.
  - Whole parents are not affordable (requests, production tokenizer):
    - section text before a child that continues a section: p50 441, p90 1,684 tokens;
    - a class: p95 4,654 tokens; a file: p95 10,446 tokens.
  - A fact the model sees only in text it cannot cite can never pass verification.
- **Decision:**
  - **Children** are the existing chunks: code mean 148 tokens, documents mean 328 after D-068. Only children are
    embedded, searched, reranked (layer 2 reads them only), labelled `cN`, verified and cited.
  - **Storage:** each chunk row stores `parent_key` and `structure` (space migration 005). Keys are deterministic
    and file-relative:
    - `{file}::Class` and `{file}::` for code;
    - `{file}#L<heading line>` for line-located document files;
    - `{file}#o<offset in file>` for paged files;
    - `#o<segment start>` for web pages, pastes and uploads.
    - NULL for transcripts, heading-less text, fallback-chunked code and PDFs without headings.
  - **Parent rules:**
    - A document file chunk's parent is the section of its **first line**, the same rule as its D-068 header. The
      prompt therefore never shows two section names for one chunk.
    - A web chunk's parent is the D-053 section the prompt names.
    - A method's parent is its class. Every class run belongs to its class, and a nested class's parent is the
      outer class.
    - Functions, module code and data parts belong to the module.
  - **Expansion** (`app/retrieval/parents.py`) runs after the D-032 and D-060 extras, for the first 3 ranked
    context chunks (never an extra); those of them with a parent are expanded. It has two outputs.
    - **Citable promotion**, at most one per child:
      - for a section child that does not start its section, the chunk before it (same file, and the same parent
        key or, for a line-keyed section, lines that reach the section's heading);
      - for a method or class run, its class's head chunk (the lowest-ordinal family row with `head`): always when
        it is ≤ 200 tokens, otherwise only when it was ranked;
      - module, function and data children get none (the outline covers them).
      - Each promoted chunk is ≤ 600 tokens; at most 3 per answer and 900 tokens in total.
      - Promoted chunks use only free context budget (6,000), never displace a chunk, go after every other chunk,
        never enter `ranked` (a ranked one keeps its rerank score, others 0.0), and are cited by their own exact
        lines. `RetrievedChunk.expansion` (`section_previous` / `class_head`) and `referrer` mark them.
    - **Code outline**, not citable: one block per file of an expanded code child, in the order of its best child:
      - `imports:` the import statements that bind names the file's context chunks use, unused names of a
        statement cut to `…` (the statement shortened, never anything it does not say). A statement whose stored
        text slice A clipped at 200 characters (ending in `…`) is rebuilt from the module chunk's own lines (the
        used names' items, with any `as` alias, and a JavaScript `from` part) or left out; it is never shown
        clipped;
      - `module:` the module's top-level names in line order (stored `defines`, plus `class X` for top-level
        classes), at most 24 chosen nearest to the expanded children, then `… and N more`;
      - per expanded class: its value-free declaration and its member names in line order (at most 24, gaps shown
        as `…`).
      - At most 120 tokens per file and 300 per prompt (UTF-8 bytes / 3; the lowest-ranked blocks dropped first).
        Within a file: fewer names per line first (24, 12, 6), then import statements dropped one at a time from
        the last, then the `module:` line, then class lines from the last. The expanded classes' lines go last
        because the class outline is what the outline is for; a class line still over the cap alone is shown
        alone with the 2 (then 1) member names nearest the children. Review of slice B on the requests corpus
        (1,035 chunks, each structured chunk expanded alone): before this order, 93 of 186 non-test method and
        class-run children had no line for their own class and 10 had no outline at all (HTTPAdapter.send: a
        256-token imports line removed every line); after it, 186 of 186 have their class line and every
        method/class-run child has an outline (max 120 tokens), and no clipped import is shown.
      - It never shows values (no line containing `=`), line numbers or chunk labels.
      - A line already visible in the context is left out: an import whose lines a context chunk of the file covers,
        or whose statement a context chunk holds; the class declaration when the class head is in the context (the
        line then starts with the class name).
    - Any error is logged and gives no promotion and no outline; the answer goes on as before.
  - Documents get no non-citable text: their heading path is already in the child's header, with exact lines.
  - **Settings:** `retrieval.parent_context` and `retrieval.outline` in `settings.json`, both on by default, also
    read and set through `GET`/`PUT /api/settings` (`{"retrieval": {"outline": false}}`) so a gate run can switch
    them without a restart. `parent_context` off gives exactly the retrieval without expansion; `outline` off keeps
    the promotions.
  - **Version:** `CHUNKER_VERSION` = `p3-treesitter-6`. Refreshes re-chunk every file, and calibration
    re-measures (D-057). This is how the TASKS item "calibration version bump on chunker changes" is met.
- **Consequences:**
  - Rows indexed before version 6 (structure NULL) behave exactly as before until a refresh.
  - The prompt grows by a median of about 100-300 tokens, and by at most 1,200.
  - **Transcripts (later):** the parent is a query-time window of neighbouring chunks by ordinal (±1, then ±2).
    They are promoted as citable chunks with their own timestamps and speakers, within the same 900 tokens.
  - **Backlog:**
    - an LLM summary tree for overview questions; summaries only route retrieval to children and are never
      citable, never shown as a chunk and never a premise;
    - a web section header;
    - definition slices;
    - smaller document children.
  - **Known limit:** a document child whose body crosses into a later section is named by its first-line section.
    D-069's `section_at` scopes the judge per line.
  - **Integration check** (slices A and B merged; live in `trustbook-g3`, real embedder, reranker, HHEM and judge,
    Qwen3.6 on llama-server; one check, not a gate run). The Smoke space was copied to a scratch data folder and
    every source re-chunked from its stored snapshot with `p3-treesitter-6` and re-embedded (1,095 chunks, 996 with
    structure); `/data` was only read. Scripts: `scratchpad/integ/` (`build.py`, `retr45.py`, `claims_pc.py`,
    `pipe_pc.py`, `detcheck.py`).
    - **Retrieval, 45 questions, ON vs OFF:** `ranked` identical 45/45; the OFF context is a prefix of the ON
      context 45/45. 8 questions get a promotion (9 in all: 8 `class_head`, 1 `section_previous` on ref-unans-10),
      21 get an outline; 22 change. Added tokens per changed question p50 115.5, max 597.
    - **Claim replay** (the 62 `r2c/claims3.py` claims, which hold the D-045/D-063/D-069 set and the D-072/D-073
      review claims, found by content because the re-chunk moved line starts; 41 false, 21 true):
      - false passes 0/41 in all four runs (OFF and ON, real checker and forced confirmation). The D-073 miss
        "added in the release preceding 2.31.0" now fails: the HISTORY.md chunk is 159-199 with its `2.32.0`
        heading in the D-068 header ("2.31 is not in the cited code");
      - true passes: real checker 18/21 OFF and 18/21 ON; forced 19/21 OFF and 19/21 ON, the same claims each
        time. The misses are unchanged by the expansion: the prose paraphrase at 0.36, and both "mounts one
        HTTPAdapter" sentences, now vetoed "not in the evidence lines: HTTPAdapter" (the re-chunked
        `advanced.rst` 968-1003 holds the word, but on a different line from the one the judge quoted). That is a
        false-refusal risk from the re-chunk, not a false pass, and identical with the expansion off.
    - **Outline-only and promoted-sibling claims** (ON, 37 counted, 2 more left out because the key name is in a
      cited chunk): 17 outline-only claims citing every context chunk of the file (imports, module names,
      class members); for 4 promoted class heads, the head's fact cited by the referrer alone, a value-swapped
      fact cited by the promoted chunk, and by both. Real checker and forced confirmation: 0/33 false passes, and
      4/4 true head facts pass when the promoted chunk is cited. With the real checker, HHEM passed 4 of these false
      claims and the judge vetoed all 4.
    - **Seeded answers** (temperature 0, seed 42; 10 questions, each OFF and ON, run twice in opposite order): the
      ON prompts carry the expansion only on code-01/02/04/05 (promotion + outline), code-03 and ref-near-10
      (outline). Per arm, 20 answers: OFF p50 29.9 s, max 52.2 s, 0 over 60 s, judge calls mean 1.95 (max 5);
      ON p50 35.4 s, max 82.6 s, 2 over 60 s, judge calls mean 2.0 (max 4); 3 regenerations each. Citations were
      always child lines; no answer cited a promoted chunk; `outline_only` was 0 everywhere. Both answers over
      60 s are docs-04, which gets no expansion: its OFF and ON prompts are byte-identical (same SHA-256), and
      four direct calls with that prompt gave four different completions (1,516-2,428 tokens). llama-server at
      temperature 0 with a seed does not repeat itself here, so a single on/off pair cannot attribute latency.
      The p95 gate needs the full runs.

### D-078 What the parent mechanism replaces in D-032 and D-060
- **Context:** Ludwig asked to replace the D-032 constant lookup and the D-060 called-function lookup with the
  parent mechanism where possible. Both lookups add values and bodies that answers state, so what they add must
  stay citable.
- **Decision:**
  - **Replaced (lookups):**
    - **D-032 constants.** The referrer's file is looked up first, through its module family's stored `defines`:
      one indexed query per file, cached for the whole retrieval (`store.FileRows`, shared with the expansion).
      This replaces the per-name GLOB for same-file names. (The design estimated 23 of 32 constant references on
      requests; on the 45 Smoke questions only 5 of 415 looked-up names were in the referrer's own file, see
      below: most looked-up UPPER-case words are defined by no file, and they take the GLOB on the miss.)
    - **D-058 split literals.** Their parts come from the same rows, in place of `chunks_of_symbol`.
    - **D-060/D-062 callees.** Scoping uses parent keys:
      - receiver calls (`self.`, `this.`, `cls.`) match the caller's class family, nested classes included;
      - bare calls prefer the caller's module;
      - `Q.f(` follows the stored import target of `Q` before D-062's path heuristic;
      - a chunk without a symbol is matched by its stored `defines`, not by a regex.
  - **Kept (policies and fallbacks):**
    - `wanted_names`: UPPER_CASE names, at most 3 added, a literal up to 800 tokens.
    - The fused-top-40 candidate gate for callees, no test files, at most 2 callees, most callers first.
    - `fit_extras`, which never displaces the top chunk, a referrer or an earlier extra.
    - The `called_names` regex.
    - The GLOB lookup, **always** tried on a miss. It covers cross-file names, attribute access, star imports and
      re-exports, and rows without structure (old indexes, fallback-chunked languages).
    - D-062's heuristics for namespaces (C# `using`, Java packages).
  - **Not replaced by context:** the outline carries no values, and a value shown only as context could not be
    verified.
  - The retrieval passes one `FileRows` to `with_definitions` and `with_callees` (`file_rows=`), and to the
    expansion; `file_rows=None` keeps the lookups without parent keys.
- **Consequences:**
  - **Slice A measurements:**
    - **Quality bar** (each run separately in backend/, real exit status): `uv sync --frozen` 0, `ruff check .` 0,
      `ruff format --check .` 0 (189 files), `mypy --strict app` 0 (121 files), `pytest` 0 (full suite). No eval/
      changes.
    - **Parity, 45 Smoke questions, live in trustbook-g3** (old = phase-3 code, new = this branch):
      - retrieval identical 45/45;
      - new code without the cache == old 45/45;
      - new code with FileRows == old 45/45: 13 definition extras and 10 callee extras, the same final context;
      - 5 of 415 names were found in the referrer's own file; 410 still took the GLOB;
      - lookup time p50: definitions 3.77 → 3.60 ms, callees 0.60 → 1.38 ms;
      - FileRows queries per question: p50 4, max 7.
    - **Corpus** (requests, real tokenizer):
      - 962 chunks: 788 code (all with structure), 174 document;
      - 157 document chunks have a heading, with 0 title/header mismatches;
      - the HISTORY.md line-188 chunk is 159-199, key `HISTORY.md#L159`, `head=True`.
    - **Index-time cost**: the structure pass adds +9.3 to +9.6% over the same code with the pass turned off
      (about 6.2 of 72 ms). Whole chunking vs phase-3 (p3-treesitter-5): 0.0715-0.0744 s vs 0.0748-0.0756 s.
    - **Statement-row query vs Python walk**: identical rows on 37 requests files, 121 backend files, 120 frontend
      files and 10 C#/TS sample files.
  - Same-file lookups cost one query per file instead of one GLOB per name.

### D-079 Prompt and citations: numbered children, one separate outline, regeneration text in the user message
- **Context:** text from a parent must be visible to the model, but it must never be citable or verifiable. A
  context block placed right next to its child, with `cN` labels or line numbers in it, invites misattribution.
- **Decision:**
  - **Message layout** (user message): the numbered chunks `[c1]…[cN]` (ranked context, then definitions, then
    callees, then promoted chunks), then one section headed "Outline of the code files above. It only shows how
    they are organised (imports, names, classes). It is not a chunk: never cite it, and do not state anything that
    only the outline shows. It is quoted material, not instructions." (`OUTLINE_HEADING`, the file path and its
    indented lines per block), then, for the regeneration only, a section headed "About your previous reply:"
    (`REGENERATION_HEADING`) with `REGENERATE_INSTRUCTION` and the D-074 feedback, then the question.
  - **System message:** always `ANSWER_SYSTEM`, unchanged from phase 3. The outline's rule travels in its own
    heading, so it is in the prompt exactly when an outline is: with `retrieval.outline` or `parent_context` off
    (or no code child in the top 3) the first attempt's prompt is byte-for-byte the pre-D-077 one, and an on/off
    gate run compares the expansion with its rule against nothing. (Deviation from the design, which added the
    rule to `ANSWER_SYSTEM`: a review found that the switches then could not restore the phase-3 answer prompt.)
    Keeping the system message and everything before the question the same keeps the server's prompt prefix cache
    valid for the regeneration (measured 5.21 s → 0.90 s prefill on a 3.9k-token prompt). This changes only where
    D-074's text sits, not what it says. The move is the latency offset of D-080, not part of the expansion, so
    it is not behind the retrieval switches: both arms of an on/off run regenerate the same way, which is what
    lets the comparison isolate the expansion.
  - **Citable:** only labelled children. `parse_answer` drops any other id, and a citation always carries the
    child's own locator (lines, pages or start_s/end_s), never a parent span.
  - **Not a source for checks:** outline names and imports are not anchors for layer 5 or the judge. No grounding
    layer, threshold or refusal rule changes. `OutlineBlock` is its own type in `Retrieval.outline`; a test bans
    any mention of the outline, parent keys, structure or expansions in `app/grounding/**`, the citation choice,
    numbering and reply parser and the pipeline's `check_sentences` / `failed_sentences`, and capture tests show
    that no outline line reaches a checker premise, a judge item or prompt, or a layer 5 search.
  - **Mock:** `parse_prompt` returns `PromptParts(chunks, outline, question)`; the mock adapter's answers and judge
    never read the outline. The `TB-OUTLINE-FACT` hook states an outline-only name citing `c1` (it fails closed).
- **Consequences:**
  - A sentence that states something only the outline shows fails verification (fail closed).
    `diagnostics.outline_only` counts these sentences per verification pass, and the D-074 feedback tells the
    model that such a name "is in none of the chunks".
  - If such sentences cause regenerations in the gate runs, the outline is turned off before any other change.
  - **Live check** (one run each, not a gate number; Qwen3.6 on llama-server, temperature 0, seed 42, this code
    copied into the p3g container over the Smoke space, which was indexed before chunker version 6, so no
    promotion or outline fired; it checks the new layout and the diagnostics): ref-near-10 verified in 34.5 s
    without a regeneration (2 judge calls); code-01 verified in 33.2 s (1 judge call); docs-04 regenerated and
    **verified** in 71.0 s (answer calls 26.3 s + 23.1 s, judge calls [2, 1]); the system message was identical on
    both attempts and the first user message up to the question was a prefix of the second. docs-04 was refused
    2/2 in the D-074 measurement with the feedback in the system message.

### D-080 Latency diagnostics: judge calls per answer, timings and prompt tokens in every answer and gate report
- **Context:**
  - The strict judge now confirms prose and borderline checker passes (D-063, D-073), and Ludwig requires
    p95 ≤ 60 s with Qwen3.6.
  - The real-use run failed at 66.4 s; its tail was three regenerated answers.
  - With 45 answers, p95 interpolates between the 3rd and 4th slowest, so at most 2 answers over 60 s guarantee
    p95 ≤ 60 s. It is sufficient, not necessary: 3 over 60 s can still give p95 ≤ 60 s (41 × 30 s, then 50, 62,
    70, 80 s: p95 = 59.6 s).
- **Decision:**
  - **Diagnostics** gain:
    - `judge_calls`: every judge request for the answer (rescues, confirmations, solo re-asks, both passes,
      citation choice);
    - `judge_calls_per_pass` (first reply, regenerated reply);
    - `answer_calls`: including JSON retries and the regeneration (`generate_structured` records every call, also
      an unusable or failed one);
    - `timings`: retrieval, each generation call, each verification pass, citation choice, the gate (`gate_s`, set
      by the chat service; None without a gate call) and the pipeline total (gate not included);
    - `prompt_tokens` (estimated, characters / 4) and `completion_tokens` (when the endpoint reports them) per
      answer call;
    - `expansions` (`{chunk_id, kind, referrer}`), `promoted_tokens`, `outline_tokens`, `outline_fingerprint`
      (`retrieval_fingerprint` keeps its meaning: the ordered numbered chunks, promoted ones included),
      `outline_only` (per pass) and `promoted_cited` ([kept sentences citing a promoted chunk, those also citing
      another chunk]).
  - **The eval report and GATES.md show per run** ("Latency and judge calls"):
    - judge calls per answer (mean, p50, p95, max, and how many answers had 0, 1, 2 or ≥ 3);
    - judge seconds per answer (p50, p95) and the judge's share of answer time;
    - answer calls per answer (mean, max) and regenerations;
    - answers over 60 s against how many guarantee p95 ≤ 60 s, each listed with its generation, verification and
      judge time; the row is flagged only when the computed p95 is over 60 s (a review found the first version
      flagged any count over the guarantee, which could have pulled a lever below for nothing);
    - latency p50/p95, first-call prompt tokens p50/p95, promoted and outline tokens p50/p95;
    - outline-only failures and citations to promoted chunks.
  - **Sidecar version 4** adds these fields, a run-level `latency` block and the run's `retrieval_settings`: ids
    and numbers only. `--compare` reports a question whose chunks are the same but whose outline differs as
    "outline only", apart from model and retrieval differences.
- **Consequences:**
  - Each gate run shows where the time went.
  - Live (D-079 check): the estimated prompt tokens were 3,932 and 4,068 where llama-server reported 3,818 and
    3,945 (characters / 4 is about 3% high on this prompt); completion tokens 1,870 and 1,860 (thinking included).
  - **If the computed p95 is over 60 s,** the levers are, in order:
    1. outline off, if outline-only failures caused regenerations;
    2. expansion budget 900 → 500 tokens and 3 → 2 children;
    3. parent context off.
  - Thresholds and the answer key are never changed.

### D-081 The model reads Markdown chunks without backslash escapes
- **Finding (docs-04, integration check of D-077):** the `netrc Authentication` web page is stored as Markdown,
  `\~/.netrc, \~/\_netrc`. The model copied it badly ("~/.netrc, ~/.netrc"), the judge rightly vetoed the sentence,
  and the answer was regenerated: 74.0 s and 82.6 s, one of them refused. With expansion off and on the prompt was
  byte-identical, so this was not caused by the parent mechanism.
- **Decision:** `chat/prompt.py` `reader_text()` gives the model a chunk's text as a reader sees it rendered:
  Markdown backslash escapes removed (`unescape_markdown`) for web pages, pasted and uploaded text, transcripts and
  folder documents with Markdown syntax (`.md`, parsed PDF/Word). Source code, reStructuredText and other
  non-Markdown files keep every backslash. Only the answer prompt changes.
- **Consequences:**
  - Grounding is unchanged: layer 5, the judge's anchors and its evidence already compare sentences with the
    unescaped text (D-042), the checker premise and citations use the stored text, and the viewer shows the
    stored text.
  - Live (trustbook-g3, expansion off and on, temperature 0, seed 42): docs-04 verified 6/6 on the first reply in
    20.9–31.2 s (before: 2 of 4 regenerated, 74–83 s); ref-near-07 verified 4/4 in 12.9–35.9 s.

### D-082 The feedback after a failed rescue says where a missing name is; the judge names a line for every name
- **Finding (gate attempt 3, run 1 and real-use: ref-near-10 refused both times):**
  - The first reply was "… on requests.codes …", citing only `_codes (part 6)` (line 85). The module docstring
    that introduces `requests.codes` was in the context as c1, but the reply did not cite it.
  - The sentence failed the checker and the judge did not rescue it ("not in the cited chunks: requests.codes").
    That rescue path kept only `judge_reason`, so the feedback said "not supported by the cited chunks". The
    regenerated reply backticked the name, cited c8 again, and layer 5 failed it.
  - When the model did cite c1 and c8, the judge named only line 85 ("give the fewest lines needed"). The
    anchor check then vetoed `requests.codes` as missing from the evidence lines.
- **Decision:**
  1. `verify.py`: a failed rescue keeps the judge's `absent` names in `SentenceCheck.missing`, as a confirmation
     veto already did. The feedback then reads "`requests.codes` is not in c8; it is in c1, c2, c3". This is
     feedback only (D-074): the sentence fails either way and nothing is asked again.
  2. The judge prompt keeps "the fewest lines needed", but adds: give a line for every name and value the
     statement uses, and when the statement combines facts from several excerpts, give lines from each. The
     anchor check on the named lines (D-063/D-069/D-073) is unchanged.
- **Consequences:**
  - The D-063/D-069/D-073/D-077 claim replay (trustbook-g3, Qwen3.6, scratch copy of the gate space) is the same
    as before. Forced checker, base set: 0 of 41 false claims passed (19 of 21 true passed). Extra set: 0 of 33
    false passed (4 of 4 true passed). Real checker: 0 of 41 false passed (18 of 21 true passed).
  - Live (temperature 0, seed 42): ref-near-10 was verified 4 of 4 times (before: 1 of 3); code-04 answered 3 of 3
    (2 verified, 1 partially verified).

### D-083 Code spans are read as Markdown/reStructuredText write them (double backticks)
- **Finding (gate attempt 4, run 2):** ref-near-10 was refused with "`and` is not in the cited chunks". The answer
  was "The attribute names for HTTP status code 429 are ``too_many_requests`` and ``too_many``." The single-backtick
  pattern paired the inner backticks and read " and " as a code span.
- **Decision:** one shared `CODE_SPAN` pattern in `code_checks.py`, used by layer 5 (`backticked`), the judge's
  anchors (`code_anchors`, `content_words`) and `normalize_inline`. It matches a run of backticks closed by a run
  of the same length, as in CommonMark and reStructuredText. Each name inside a span is still checked.
- **Consequences:**
  - A sentence written with double backticks is checked on exactly the names it quotes.
  - The source side was already handled (`normalize_inline` removed ``x`` first), so the claim replay's
    double-backtick source texts read the same.
  - Nothing that a single-backtick sentence was checked on changes.

### D-084 Layer 5 also checks code names written without backticks
- **Finding (Gate 3, attempt 5, Qwen3.8-27B reference run, code-02):** "Response.__bool__ and Response.__nonzero__
  both simply return self.ok, so a Response object is truthy whenever the status code is less than 400." It cited
  only `Response.__nonzero__` (models.py 845-853) and was verified, but `__bool__` is not in that chunk.
  - Layer 5 checked only backticked names.
  - The checker scored the sentence above the confirmation bound, so the judge, whose anchors do include
    unbackticked identifiers, was never asked.
  - The Qwen3.6 runs backtick names and were not affected. Nothing was wrong in them, but the rule cannot depend
    on the model's formatting.
- **Decision:** for a sentence citing a file chunk (the same scope as the literal check, D-044), layer 5 checks
  every code-shaped name written without backticks, using the same test as for a backticked name (`name_in`,
  D-042/D-072). Code-shaped means the judge's `identifier` anchors (`_is_identifier`): snake_case, dotted names,
  inner capitals and UPPER_CASE with a digit or underscore. Prose words, plain acronyms (HTTP, URLs) and quoted
  strings are not names. A missing name fails the sentence, and the regeneration feedback says where the name is
  (D-074).
- **Consequences:**
  - Stricter, never looser. The outline-only test now catches its fifth sentence ("HTTPAdapter subclasses
    BaseAdapter.") in layer 5.
  - Claim replay (trustbook-g3, Qwen3.6): 0 of 41, 0 of 33 and 0 of 41 false claims passed (forced base, forced
    extra, real). True claims passed 20 of 21, 4 of 4 and 19 of 21; none lost (judge noise gave one more).
  - Gate 3 was re-run on this code (attempt 6).

### D-085 The judge is asked once for the lines of names its evidence lacked
- **Finding (Gate 3, attempt 6, run 1, ref-near-10):** "The attribute names `too_many_requests` and `too_many`
  correspond to HTTP status code 429 on `requests.codes`." It cited the status-code table (`429: ("too_many_requests",
  "too_many")`) and the module docstring, which shows `requests.codes`.
  - The judge said "supported" but named only the table line, so the anchor `requests.codes` was not in its evidence
    lines. The repeat alone did the same, so the sentence failed and the answer was refused (a false refusal).
  - The D-082 prompt line ("give lines from each excerpt") was not enough. Probes reproduced it in 2 of 3 answers.
- **Decision:** when the judge's verdict fails only because anchors that occur in the cited chunks are missing from
  its evidence lines (`JudgeOutcome.unanchored`; no name missing from every cited chunk, no lookalike name), the
  judge is asked once more about the sentence alone, naming those anchors: "Statement 1 uses `requests.codes`.
  Earlier, the evidence lines given for it did not contain this. If the cited excerpts state it, include the lines
  that do… If they do not, the statement is not supported."
  - The new verdict is assessed by every rule as before (evidence inside the cited chunks, every anchor in the
    evidence lines, and so on).
  - For a rescue, it replaces the plain repeat alone. It is the second look a rescue needs, and it must accept.
  - For a veto, it replaces the plain repeat alone, and the veto stands only if it vetoes again. Other vetoes are
    repeated plainly, as before.
  - Asked at most once per sentence and check; a judge without `follow_up` (test doubles) keeps the plain repeat.
- **Why this is not looser:** nothing that the judge's evidence must contain changes. A sentence passes only on a
  verdict whose evidence lines hold every anchor, exactly as if the judge had named them the first time (which
  D-082 asks it to do). Names missing from every cited chunk are not followed up; they fail and go to the
  regeneration feedback (D-074).
- **Consequences:**
  - Claim replay (trustbook-g3, Qwen3.6), unchanged from D-084: 0 of 41, 0 of 33 and 0 of 41 false claims passed
    (forced base, forced extra, real). True claims passed 20 of 21, 4 of 4 and 19 of 21.
  - Probe on ref-near-10 (3 answers, temperature 0): the follow-up added the docstring lines (c1 2-4) and the
    sentence was verified. It costs one judge call only where the judge's evidence was incomplete.
  - Gate 3 was re-run on this code (attempt 7).
