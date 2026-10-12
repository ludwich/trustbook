# Chat models

Trustbook bundles its embedding, reranking, answer-checking and speech-to-text models. The **chat model is
yours**. It writes the answers, and it also acts as the code judge, which double-checks claims about code
(Settings → Grounding checker). This page shows how each chat model measured on the Gate 3 evaluation and which
model we recommend as a minimum.

## How it was measured

- **Evaluation:** the Gate 3 command (`eval/runner.py`, see `docs/GATES.md` Gate 3 #1). It asks 45 questions:
  15 smoke questions and the 30-question refusal set. They cover the `requests` repository at 6e83187 and its
  documentation. 22 questions are answerable and 23 are not.
- **Pass criteria:**
  - at most 1 false refusal on code questions;
  - correct refusals ≥ 95%;
  - citation precision ≥ 90%;
  - 100% of cited line ranges exist;
  - p95 latency ≤ 60 s.
- **Machines:** Trustbook ran on the dev workbench (8 CPUs, no GPU). The chat model ran on a llama-server on
  another machine on the LAN, one model loaded at a time.
- **Settings:**
  - The temperature-0 runs (marked `0 / seed 42`) are the runner's evaluation settings.
  - "real-use" is the app's default: temperature 0.2, no seed.
  - llama-server is not deterministic, even at temperature 0 with a fixed seed: identical prompts gave different
    completions. Each run is one sample.
- **Answer key:** "precision" counts a citation as correct only if it points into a location listed in the answer
  key. A model that also cites other supporting places, such as tests or the implementation behind a documented
  behaviour, scores lower even when the citation supports its sentence. The grounding layers checked every cited
  sentence in every run.

## Results (Gate 3, final code `9708373`, attempt 8)

| Model | Quantisation | Run | Precision | Refusals | Code false refusals (≤ 1) | All false refusals | p50 / p95 latency | Judge calls / answer (mean / max) | Judge share of time | Result |
|---|---|---|---|---|---|---|---|---|---|---|
| Qwen3.6-35B-A3B (MoE, 3B active) | UD-Q4_K_XL | 1 (0 / seed 42) | 100% (38/38) | 23/23 | 0/13 | 0/22 | 21.1 s / 48.6 s | 0.95 / 9 | 10.8% | **pass** |
| Qwen3.6-35B-A3B | UD-Q4_K_XL | 2 (0 / seed 42) | 100% (36/36) | 23/23 | 0/13 | 0/22 | 19.9 s / 47.8 s | 0.93 / 4 | 9.5% | **pass** |
| Qwen3.6-35B-A3B | UD-Q4_K_XL | real-use (0.2) | 100% (39/39) | 23/23 | 0/13 | 0/22 | 19.0 s / 50.6 s | 0.93 / 4 | 10.5% | **pass** |
| Qwen3.8-27B (dense) | UD-Q4_K_XL | reference (0 / seed 42) | 77.4% (48/62) | 23/23 | 0/13 | 0/22 | 35.0 s / 85.3 s | 1.20 / 6 | 22.1% | fail (precision, p95) |

The reference run has 44 timed answers: one question got a 500 error from the model server.

**Qwen3.8-27B**
- **Answers:** longer, with more citations (62 against 36–39). Its "wrong" citations mostly point to places the key
  does not list, such as tests, the implementation behind a documented behaviour, or HISTORY entries. Each is
  checked in `docs/GATES.md` (Gate 3, "Citations outside the answer key"). Of its 14 citations outside the key, 3 support
  their sentence alone, 5 together with the sentence's other citations, 6 support a sentence that is beside the
  question, and none is wrong.
- **Latency:** as a dense 27B model it is slower on the same server. 11 of 44 answers took over 60 s, and the code
  judge takes 22% of the answer time (against about 10% with Qwen3.6).
- **Refusals:** it refused every unanswerable question and made no false refusals.

## Recommended minimum chat model

**Qwen3.6-35B-A3B at Q4_K_XL, or a model at least as capable, served at a similar speed.** It is the smallest model
that passed every Gate 3 criterion, in all three runs.

What a chat model must do for Trustbook:
- **JSON output.** It must produce JSON that matches a schema: the answer is a list of sentences with chunk ids,
  and the judge returns verdicts. llama-server, vLLM, Ollama, LM Studio and the hosted APIs all support
  schema-constrained output.
- **Context.** The first answer call's prompt was 3.2k tokens at p50 and 4.4k at p95. A reasoning model adds up to
  about 3k tokens of thinking. A context window of **16k tokens** is enough; 8k is not.
- **Speed.** Each answer makes 1–2 answer calls plus up to about 8 short judge calls. To keep p95 under 60 s, the
  server needs to be roughly as fast as Qwen3.6-35B-A3B on the test server: p50 about 20 s per answer. A slower
  model gives the same grounding, only slower.
- **Weaker or smaller models** stay safe because grounding does not depend on the model: an unsupported sentence is
  removed or the answer is refused. They do, however, refuse more answerable questions.

Not measured yet: hosted models (OpenAI, Anthropic, Azure OpenAI) and models smaller than Qwen3.6-35B-A3B. To
measure one, run the Gate 3 command with
`--chat-endpoint <url> --chat-model <name>` (the run's report header names the model) and add a row here.
