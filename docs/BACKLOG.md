# Backlog

Ideas accepted for later, not scheduled in a phase. SPEC §16 lists what is out of scope for the POC.

## Priorities (after Gate 4, 2026-10-13)

All SPEC §14 phases have passed. Order follows SPEC §15: grounding correctness > data safety > simplicity >
performance > features. Details for each item are in the sections below.

**P1: correctness of what the user reads (small, fix first)**
- G4-3 risks attached to the wrong branch in the brief (off by one).
- G4-1 a chunk spanning several headings is labelled with the first one (viewer header and brief anchors);
  with "Web section header", the same problem for web, paste and upload chunks.
- G3-2 ref-near-10 refused in a code-only space: find what retrieval misses before touching grounding.
- G3-3 a two-model-family smoke check before every gate, so a change tuned to one model family is caught.
- Gate items not yet tested: Gate 3b #8 (Claude Code skill behaviour), Gate 4 #9 (request log), Gate 4 seed
  dismiss with ×.

**P2: usability and setup (small)**
- G3b-2 optional API key for custom chat and speech-to-text endpoints.
- G3b-1 `python -m trustbook_cli` fallback and a Git Bash shim for locked-down Windows.
- G3-1 code symbol in the citation viewer header.
- G4-2 a reload keeps the Ideation tab.
- G4-4 source-list warnings and long file names wrap; the folder warning shows one line per problem.
- CLI nits: `http_status` on a known 401; a "loading models" state instead of "reading 0%".

**P3: features (larger, each needs its own design)**
- Questions that name a source (scope retrieval to it, or point to the overview).
- Summary tree for overview questions ("what is this repo about?").
- MCP server over the CLI's client; `trustbook ideas` in the CLI.
- Embeddings via an endpoint (GPU indexing without a CUDA image).
- Speaker labels in transcripts (diarization), so transcript windows show who spoke.

**P4: retrieval tuning (measure first)**
- Definition slices, smaller document children.

**Done or superseded:** Phase 3b CLI (merged, tag `phase-3b`); code-aware verification (Phase 3, D-045);
the citable transcript window (superseded by D-091's non-citable windows).

**Out of scope for the POC (SPEC §16):** "Suggest ideas", studio outputs, analyzers and the rest of §16.

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

### Code-aware answer verification (done in Phase 3: code judge D-045, tree-sitter chunks)
The bundled grounding checker (HHEM, a prose NLI model) cannot judge claims that describe what code does.
D-044 restates simple facts (assignments, defaults) so those verify, but process claims are still refused.
Options to evaluate in Phase 3:
- tree-sitter-derived facts (return values, raised exceptions, calls in a function);
- a code-tuned entailment model with a permissive license;
- an optional "verify with the chat model" setting for code citations, off by default and badged
  differently, sending only the cited chunks.

## From Gate 3 (2026-10-11)

### Phase 3b: CLI for agents (done: merged after Gate 3b, tag `phase-3b`)
A `trustbook` CLI over the local HTTP API with `--json` output and stable exit codes, plus a Claude Code
skill, so agents can add sources and ask grounded questions. It goes through the same grounding as the UI.

### MCP server
An MCP server wrapping the same client as the CLI (after Phase 3b).

### Summary tree for overview questions
An LLM-built summary tree (section → file/source → space) to answer overview questions ("what is this repo
about?"). Summaries only route retrieval to the right children; they are never citable and never verified
text — answers still cite the child chunks' exact lines. Builds on the Phase 3 parent/child retrieval (D-077):
the summaries hang off the stored parent keys, never appear in an answer prompt, the code outline or a
premise, and are rebuilt on every `CHUNKER_VERSION` change.

## From Gate 3 round 2b, parent/child retrieval (2026-10-11, D-077)

### Transcript window (superseded by D-091: non-citable windows, merged in Phase 3b)
A transcript chunk's parent is a query-time window of its ordinal neighbours (±1, then ±2), promoted as
ordinary citable chunks with their own `start_s` / `end_s` / speaker, within the same 900-token promotion
budget (no separate, larger cap). `store.chunks_at_ordinals` already serves it. Ships with the first
transcript eval question.

### Web section header
Web, paste and upload chunks name their D-053 section only in the prompt's `Source:` line. A stored
`Section:` header with the nested heading path (as D-068 gives document files) needs another chunker bump.

### Definition slices
Promote only the lines of a large definition that a child needs (ids like `owner~a-b`; measured 5,108 →
~275 tokens). A new citation-id kind breaks `GET /chunks/{id}` and the viewer offsets, so it needs its own
design.

### Smaller document children
About 250-token document children (today mean 328 after D-068), with the section as their parent. Not needed
for the mechanism; the eval would have to be re-measured.

## From Gate 3 pass (2026-10-12)

### G3-1 Code symbol in the citation viewer header
The viewer header shows the file and lines but not the code symbol. Gate 3 checklist #3 expects the qualified
name, e.g. `SessionRedirectMixin.should_strip_auth`. The symbol is already stored with each code chunk.

### G3-2 ref-near-10 (`requests.codes`, 429 names)
Still fails in 3 of Ludwig's last 4 runs. In the UI, in a code-only space, it is refused as `not_in_sources`,
so retrieval is involved, not only the judge (D-085). Investigate which chunks reach the gate and the answer
call for this question before touching grounding.

### G3-3 Two-model-family smoke check before every gate
The Qwen3.8-27B reference run stays around 78% precision. Before each gate, run the smoke set with a second
model family next to the main model and report both, so a prompt or grounding change tuned to one family is
seen early.

## From Gate 3b pass (2026-10-13)

### G3b-1 `python -m trustbook_cli` fallback for locked-down Windows
Application control can block the pip-generated `trustbook.exe` ("Access denied"). Add
`cli/src/trustbook_cli/__main__.py` so `python -m trustbook_cli` works, and document it in `docs/cli.md` and
`skills/trustbook/SKILL.md` as the fallback, with a Git Bash shim (`~/bin/trustbook`) for Claude Code on Windows.

### G3b-2 Optional API key for custom endpoints
"Other OpenAI-compatible" chat endpoints and the speech-to-text endpoint have no API key field; only the
OpenAI/Azure/Anthropic presets show one. OpenRouter, LiteLLM or `llama-server --api-key` need an optional key
(stored only in `settings.json`, like the preset keys).

### Gate 3b #8: Claude Code skill behaviour test (waived)
Ludwig waived #8 at Gate 3b. Still to test: a cited answer through the skill, and a refusal that Claude does not
retry, rephrase or answer from its own knowledge (D-090).

### CLI nits
- `trustbook.error/1` shows `"http_status": null` on a known 401.
- `sources add --wait` shows no progress for about 30 s while the embedder loads on first use. On a cold CI
  runner the first paste sat at "reading 0%" for 161–300+ s (startup calibration plus one embedder per
  indexing stream). Show a "loading models" state instead of reading 0%.

## From Gate 4 pass (2026-10-13)

### G4-1 Chunk heading for chunks that span several headings
Document chunks can span several headings, and the viewer header and the brief's anchors show only the first
("Example: Specific SSL Version" for a retry claim whose text is under "Example: Automatic Retries"). Either
split chunks at headings (a chunker bump and re-index) or show the heading nearest the cited text.

### G4-2 Reload keeps the Ideation tab
Reloading the page lands on the Chat tab, not Ideation. The session itself resumes correctly.

### G4-3 Risks attached to the wrong branch
In the brief, risks are attributed to the branch answered just before the one they concern ("Retrying too
aggressively" shows "from Progress bar implementation"). Attach a risk to the branch it concerns.

### G4-4 Source-list messages are clipped
Source-list warnings and errors, and long file names, do not wrap and are clipped at the sidebar edge.
The folder warning also joins two unrelated messages into one sentence (the skipped brief, and "very little
text" about `notes.md`); show one line per problem.

### Gate items not yet tested
- Gate 4 #9: every ideation call (seed, turns, judge) is in Settings → Request log.
- Gate 4 #3: dismissing a waiting seed with × (covered by Playwright `ideation.spec.ts`).

### Ideation in the CLI
A `trustbook ideas` command over the ideation API (start, answer, finish, export), with the same JSON
contract.

## From Gate 3b known limits

### Speaker labels in transcripts
No reader sets speakers yet (speech-to-text and YouTube captions have none), so transcript windows show only
times. A permissively licensed diarization step would label speakers.
