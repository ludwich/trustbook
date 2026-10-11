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

## From Phase 2 tuning (2026-10-10)

### Code-aware answer verification (candidate for Phase 3)
The bundled grounding checker (HHEM, a prose NLI model) cannot judge claims that describe what code does.
D-044 restates simple facts (assignments, defaults) so those verify, but process claims are still refused.
Options to evaluate in Phase 3:
- tree-sitter-derived facts (return values, raised exceptions, calls in a function);
- a code-tuned entailment model with a permissive license;
- an optional "verify with the chat model" setting for code citations, off by default and badged
  differently, sending only the cited chunks.

## From Gate 3 (2026-10-11)

### Phase 3b: CLI for agents (scheduled right after Gate 3; details in docs/TASKS.md)
A `trustbook` CLI over the local HTTP API with `--json` output and stable exit codes, plus a Claude Code
skill, so agents can add sources and ask grounded questions. It goes through the same grounding as the UI.

### MCP server
An MCP server wrapping the same client as the CLI (after Phase 3b).
