---
name: trustbook
description: Answer questions from the user's own sources (documents, web pages, transcripts, code folders) with verified citations, using the local Trustbook app through its `trustbook` CLI. Use when the user asks what their notebook, sources, documents or a linked code folder say, asks to "check Trustbook", or asks to add a source to a Trustbook space. A refusal (exit code 2) means the answer is not in the sources and is final.
---

# Trustbook

Trustbook is the user's local research notebook. It answers only from the sources in a *space* and
checks every sentence against them before it shows it. Use it through the `trustbook` CLI; full
reference: `docs/cli.md` in the Trustbook repository.

## Running it

- Installed on this computer: `trustbook …`
- If `trustbook` is blocked ("Access denied") or not found but the package is installed:
  `python -m trustbook_cli …` (same arguments, same exit codes). To keep typing `trustbook` in
  Git Bash, create `~/bin/trustbook` (`~/bin` must be on `PATH`) and run `chmod +x ~/bin/trustbook`:
  ```sh
  #!/bin/sh
  exec python -m trustbook_cli "$@"
  ```
- Otherwise through Docker, from the folder with Trustbook's `compose.yaml`:
  `docker compose exec -T app trustbook …`
- Always pass `--json` and read the single JSON document on stdout. Check the exit code.
- If the space is unclear, run `trustbook spaces list --json` and ask the user which one to use.

```
trustbook ask "<the user's question>" --space "<space>" --json
```

## Exit codes and what to do

| Exit | Meaning | Do |
|---|---|---|
| 0 | Answered (`verdict`: `verified`, `partially_verified` or `unverified`) | Report the answer with its citations |
| 2 | Refused: **not in the sources** | Tell the user, and stop (see below) |
| 3 | A source failed to read or index | Report the source and its `failure_reason` |
| 1 | Error (Trustbook not running, unknown space, bad arguments) | Report the `message`; do not guess an answer |
| 4 | Only `trustbook eval`: an evaluation target was missed | Report the runner's report; it is a result, not an error |

`trustbook eval` (only when the user asks for an evaluation run) uses 0, 1, 4 and 130 (interrupted);
it never returns 2.

## Answers (exit 0)

- Give the answer in the user's words if you like, but keep every statement tied to its citation
  numbers, and do not add facts that are not in `sentences`.
- Cite from `citations[]`: `source`, and `file` + `lines` for code, `pages` for PDFs,
  `start_s`–`end_s` for recordings, `heading` for documents. `text` is the cited passage: quote it
  when the user wants to see the evidence.
- `verdict` `partially_verified` means some statements were removed. Say that the answer is
  partial.
- `verdict` `unverified` means Trustbook's grounding checker is turned off (a setting), so the
  statements were not checked against their cited passages. Always say so when you present such
  an answer ("Trustbook's grounding checker is off, so these statements are unverified"), and
  never call it verified.
- **Never present `removed_statements` as facts.** They failed verification and are not from the
  sources. Mention them only as "Trustbook removed these unverified statements", if at all.

## Refusals (exit 2): final

Exit 2 (`verdict` `refused`) means Trustbook did not give a verified answer from the space's
sources. Tell the user plainly: "Trustbook could not find this in the sources of <space>" (for
`refusal.reason` `unsupported`: "Trustbook could not verify an answer from the sources of
<space>"), with `refusal.message` and `refusal.suggestion` if there is one.

Then stop. Do **not**:

- retry the question, rephrase it, split it up or ask it again in another way to get an answer;
- switch to another space on your own to look for one;
- answer it from your own knowledge, from the web, or by reading the files yourself and
  presenting that as what the sources say.

The refusal is Trustbook's result, not an error to work around.

`refusal.reason` tells why: `not_in_sources`, `no_sources` (the space has nothing ready yet: the
user may want to add or wait for sources), `invalid_output` (the model's reply was unusable; the
user may try again later), `unsupported` (an answer was drafted but Trustbook could not verify it
against its sources; tell the user that, not that the sources lack it; the user may rephrase or
name the file or function), `chitchat` (the question was taken as small talk).

## Adding sources (only when the user asks)

```
trustbook sources add folder <path> --space "<space>" --wait --json
trustbook sources add url <url> --space "<space>" --wait --json
trustbook sources add file <file> --space "<space>" --wait --json
trustbook sources list --space "<space>" --json
```

Folder paths are relative to the folder Trustbook may read (the user's home); for an absolute path
add `--browse-root <home folder>`. `--wait` prints progress to stderr; a large folder can take
minutes. Exit 3 means a source failed: report its `failure_reason` and `failure_next_step`.
If `sources add url` with several URLs fails part-way (exit 1), the error document's `created`
lists the sources that were already added: tell the user, and do not add those again.
Do not delete spaces or sources unless the user asks (`--yes` is required).
