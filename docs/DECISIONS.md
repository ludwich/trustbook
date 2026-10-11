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

### D-056 Retrieval ties are ordered by content, never by write order or uuid
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

### D-057 Callee definitions: the context gets the bodies of the functions its code calls
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

### D-058 Exact-copy dedup of the context: measured, not adopted
- **Proposal (code-02 diagnosis, optional):** while filling the 8-chunk context, skip a chunk whose body is ≥ 0.95
  word-3-gram contained in an already selected chunk (never the top or a guarded chunk) and take the next one.
- **Measured** on the Smoke space with D-056/D-057 retrieval, 45 questions: 5 contexts change. The skipped copies
  are the web "Basic Authentication" section (1.00 inside authentication.rst 1-65; code-05, ref-unans-19), the web
  "Transport Adapters" section (0.99; ref-near-09) and `Response.__nonzero__` (0.97 inside `__bool__`; code-02,
  ref-near-10). None of the dropped or the newly added chunks is an expected locator; the added ones score
  0.06-0.33 (a proxies section, two tests, two small code fragments).
- **Decision:** not adopted. It brings no expected evidence into any context, adds low-ranked noise, and removes
  one source's copy before generation, so a space loses that source's citation even where the user or a key wants
  it (web page vs. repository file). code-05 no longer needs the slot (D-057 adds its body outside the 8-chunk
  count). Over-citation of copies is a citation question and is handled after verification, where the sentence
  is known (the code-02 / docs-04 citation-collapse work in the chat pipeline).

### D-059 Callee lookup follows qualified calls, keeps a constant's referrer, scans each candidate once
- **Review findings on D-057:** (1) only bare and `self.`/`this.`/`cls.` calls were followed, so the usual call
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
    expressions (`x[0].f(`, `f().g(`) are not followed. All other D-057 gates are unchanged: same source, ranked
    candidate (fused top 40), not a test file, not in the context, at most 2.
  - `RetrievedChunk.referrer` records the context chunk an added chunk was found for; `with_callees` protects the
    referrers of constants as well as the constants (the D-032 / `fit_extras` guarantee across both steps).
  - Candidates are indexed by (source, function name) once per question; a symbol-less candidate's definitions are
    computed once and reused. Same 8 × 32 Java benchmark: 0.01 s.
- **Measured on the Smoke space (45 questions, branch retrieval, no LLM calls):** the callees chosen are identical
  to the D-057 rule on all 45 (requests calls its helpers bare), so nothing measured before changes. Tests cover the
  C# static helper, the Python module function (a same-named function in another module is not taken), Java/Go
  fallback files, the orphaned-constant reproducer and the one-scan-per-candidate bound.
- **Grounding:** unchanged; no threshold, verification or refusal rule is touched.
