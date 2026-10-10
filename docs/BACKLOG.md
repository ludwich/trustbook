# Backlog

Ideas accepted for later, not scheduled in a phase. SPEC §16 lists what is out of scope for the POC.

## From Gate 1 (2026-10-10)

### Questions that name a source
"Give me info on the Wikipedia page" is refused today, because retrieval matches passages, not source names.
Detect a question that names a source (title or URL), and either scope retrieval to that source or point to
the space overview. A candidate for the `gate` router (fixed-choice output, logged to `gate_log`).

### Embeddings via an endpoint
Make `embeddings` a `bundled_with_endpoint_override` capability (OpenAI-compatible `/v1/embeddings`). The
model is locked per space when the space is created (`space.json` already records the model and revision), so
changing it still means re-indexing. The Test button reports passages per second. This would let AMD, Intel
and Apple GPUs (via LM Studio or Ollama) speed up indexing without a CUDA image. Remote endpoints follow the
usual locality and request-log rules.
