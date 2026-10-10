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
