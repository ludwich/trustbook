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
