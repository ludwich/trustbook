# The `trustbook` command line

`trustbook` creates spaces, adds sources and asks questions of a running Trustbook from a terminal,
a script or an AI agent (the Claude Code skill: [`skills/trustbook/SKILL.md`](../skills/trustbook/SKILL.md)).
It talks only to the public HTTP API (default `http://127.0.0.1:8484`), so every answer passes the
same six grounding layers and the same refusal as in the UI: the CLI cannot get an answer the UI
would not give. Questions asked here appear as normal conversations (titled `CLI: …`) in the UI.

Decisions: D-086 (package, shared client), D-087 (JSON and exit codes), D-088 (folder paths),
D-089 (the skill).

## Install

| Where | How |
|---|---|
| In the image | Nothing to install: `docker compose exec app trustbook …` (add `-T` when a script reads the output or pipes stdin) |
| Windows / macOS / Linux, Python 3.12+ | `pip install "git+https://github.com/ludwich/trustbook@main#subdirectory=cli"` (or `pipx install …`, `uv tool install …`) |
| A checkout of the repo | `pip install ./cli`; the backend's environment has it already (`uv run --project backend trustbook …`) |

The package is `trustbook-cli` (Apache-2.0); its only dependency is `httpx`.

## Options and environment

| Option | Environment | Default | Meaning |
|---|---|---|---|
| `--base-url URL` | `TRUSTBOOK_URL` | `http://127.0.0.1:8484` | Where Trustbook runs |
| `--space SPACE` | `TRUSTBOOK_SPACE` | – | Space id, slug or name (case-insensitive) |
| `--browse-root DIR` | `TRUSTBOOK_BROWSE_ROOT` | – (in the image: `/host`) | The folder Trustbook may read, as this computer sees it (`C:/Users/anna`) |
| `--json` | – | off | Exactly one JSON document on stdout |
| `--version` | – | – | `trustbook-cli <version>` |
| – | `TRUSTBOOK_EVAL_RUNNER` | `/app/eval/runner.py` or `eval/runner.py` above the current folder | The runner `trustbook eval` starts |

`--json` and `--base-url` may come before or after the command; the other options follow it.

## Commands

| Command | Does | JSON schema |
|---|---|---|
| `spaces list` | Lists spaces | `trustbook.spaces/1` |
| `spaces create NAME [--description TEXT]` | Creates a space | `trustbook.space/1` |
| `spaces delete SPACE --yes` | Deletes a space with its sources and conversations | `trustbook.deleted/1` |
| `sources add folder PATH` | Links a folder (code or documents), see "Folder paths" | `trustbook.sources/1` |
| `sources add url URL…` | Adds web pages or YouTube videos | `trustbook.sources/1` |
| `sources add file FILE…` | Uploads files (PDF, DOCX, PPTX, XLSX, text, audio, …) | `trustbook.sources/1` |
| `sources add paste [TEXT \| -] [--title T]` | Adds text; `-` or no TEXT reads stdin | `trustbook.sources/1` |
| `sources add search QUERY [--limit N]` | Web search: lists numbered results, adds nothing | `trustbook.search/1` |
| `sources add search QUERY --pick 1,3` | Adds the picked results (numbers or URLs) | `trustbook.sources/1` |
| `sources list` | The space's sources and their status | `trustbook.sources/1` |
| `sources status SOURCE` | One source (id, title or origin) | `trustbook.source/1` |
| `sources refresh SOURCE` | Re-reads a folder or web source | `trustbook.source/1` |
| `sources remove SOURCE --yes` | Removes a source and its index | `trustbook.deleted/1` |
| `ask QUESTION` | Asks in a new conversation; `-` reads the question from stdin | `trustbook.ask/1` |
| `queue` | The processing queue (all spaces) | `trustbook.jobs/1` |
| `gate-log [--limit N]` | The gate's recent decisions in a space | `trustbook.gate_log/1` |
| `eval ARGS…` | Runs `eval/runner.py` with the same base URL; all arguments go to the runner, and its Markdown report and exit codes are its own | – |

Every `sources add`, `status` and `refresh` takes `--wait`: it polls until each source is ready or
failed, prints progress to stderr (`[12s] auth.py: indexing 40%`), and with `--timeout SECONDS`
gives up (exit 1; the sources keep processing). Without `--wait` the command returns right away
with the sources `queued`.

### Folder paths

The API reads folders inside the browse root only: the home folder that `compose.yaml` mounts
read-only at `/host` (`TRUSTBOOK_BROWSE_ROOT` in `.env`).

- A relative path is relative to the browse root, not to the current folder: `trustbook-eval/requests`.
- `\` and `/` both work: `trustbook-eval\requests`.
- An absolute path is mapped through `--browse-root` (or `TRUSTBOOK_BROWSE_ROOT`):
  `C:\Users\anna\code\app` with `--browse-root C:/Users/anna` becomes `code/app`. Drive paths
  compare case-insensitively. Inside the container `TRUSTBOOK_BROWSE_ROOT` is `/host`, so
  `/host/code/app` works there as is.
- A path outside the browse root, `..`, or the root itself is refused before anything is sent.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | OK. For `ask`: an answer (verified, partially verified or unverified) |
| 1 | Error: bad arguments, unknown space or source, API unreachable or an API error |
| 2 | `ask` was refused: the answer is not in the sources (`refusal.reason` says why) |
| 3 | A source failed (after `--wait`, or in `sources list` / `status`): `failure_reason` says why |
| 130 | Interrupted (Ctrl-C) |

`eval` returns the runner's own codes (0 report written, 1 a target missed or a setup job did not end as
expected, 2 invalid input or API unreachable, 130 interrupted).

## JSON output

With `--json`, stdout holds exactly one JSON document on one line, ASCII-only (other characters
are `\uXXXX` escapes, so any console code page reads it), also for errors. Progress and notes go to
stderr. Every document has `schema`: a name and a version. A version only changes when a field
is removed or changes meaning; new fields may be added within a version.

### `trustbook.ask/1`

Shown indented; the CLI writes it on one line.

```json
{
  "schema": "trustbook.ask/1",
  "space": {"id": "8c1f…", "name": "Smoke"},
  "question": "How does HTTPBasicAuth add the header?",
  "conversation_id": "5b0e…",
  "message_id": "e2a9…",
  "verdict": "verified",
  "answer": "HTTPBasicAuth sets the Authorization header with _basic_auth_str. [1]",
  "sentences": [
    {"text": "HTTPBasicAuth sets the Authorization header with _basic_auth_str.", "citations": [1]}
  ],
  "citations": [
    {
      "n": 1, "source": "requests", "source_id": "41d7…", "origin": "trustbook-eval/requests",
      "chunk_id": "c0f2…", "file": "src/requests/auth.py", "lines": [96, 100], "pages": null,
      "start_s": null, "end_s": null, "speaker": null, "heading": null, "symbol": "HTTPBasicAuth.__call__",
      "text": "    def __call__(self, r):\n        r.headers[\"Authorization\"] = _basic_auth_str(self.username, self.password)\n        return r"
    }
  ],
  "removed_statements": [],
  "refusal": null,
  "latency_s": 7.42
}
```

- `verdict`: `verified`, `partially_verified` (some statements were removed), `unverified`, or
  `refused`.
- `answer`: the sentences joined, each followed by its citation numbers. `sentences[].citations`
  holds the numbers; `citations[]` describes each number once.
- A citation's locator fields are `null` when they don't apply: `file` + `lines` + `symbol` for
  code, `pages` for PDFs, `start_s` / `end_s` / `speaker` for transcripts, `heading` for documents
  and web pages. `origin`: the source's folder path or URL, an upload's file name, `null` for
  pasted text.
- `text`: the cited passage as the source viewer shows it: the cited lines for code, the
  highlighted passage otherwise (`null` if it can no longer be read).
- `removed_statements`: statements Trustbook removed because they failed verification, with the
  reason (`{text, reason}`). They are **not** from the sources and must not be presented as facts.
- `refusal`: `null`, or `{reason, message, suggestion}` with `verdict` `refused`, empty `answer`,
  `sentences` and `citations`, and exit code 2. Reasons: `not_in_sources`, `no_sources` (nothing
  ready in the space), `invalid_output` (the model's reply was unusable), `unsupported`, and
  `chitchat` (a greeting got the fixed small-talk reply).
- `latency_s`: seconds from sending the question to the final answer.

### Other documents

| Schema | Fields |
|---|---|
| `trustbook.spaces/1` | `spaces[]`: `{id, slug, name, description, source_count, created_at}` |
| `trustbook.space/1` | `space`: as above |
| `trustbook.sources/1` | `space {id, name}`, `sources[]`: `{id, type, title, origin, group, status, progress, failure_reason, failure_next_step, quality_warning, enabled, snapshot_at, commit_hash, stats, created_at, job_id, ahead}` |
| `trustbook.source/1` | `space {id, name}`, `source`: as above |
| `trustbook.search/1` | `space {id, name}`, `query`, `results[]`: `{n, title, url, snippet}` |
| `trustbook.jobs/1` | `jobs[]`: `{id, kind, title, space_id, space_name, status, progress, position, error, created_at, started_at, finished_at, eta_seconds}`, `summary {queued, running, paused, eta_seconds, workers}` |
| `trustbook.gate_log/1` | `space {id, name}`, `entries[]`: `{id, created, input, decision, confidence, user_correction}` |
| `trustbook.deleted/1` | `kind` (`space` or `source`), `id`, `name` |
| `trustbook.error/1` | `message`, `http_status` (the API's status code, `null` when there was no response) |

Source `status`: `queued`, `reading`, `indexing`, `ready`, `failed`; `progress` is 0–1 or `null`.

## Examples

Bash (in the container or with the CLI installed):

```bash
trustbook spaces create Smoke
trustbook sources add folder trustbook-eval/requests --space Smoke --wait
trustbook ask "How does HTTPBasicAuth add the Authorization header?" --space Smoke
trustbook ask "What is the capital of Mongolia?" --space Smoke --json > out.json; echo "exit $?"
jq .refusal out.json   # exit 2: {"reason": "not_in_sources", …}
echo "Long question from a file…" | trustbook ask - --space Smoke --json
```

PowerShell (Windows), with the CLI installed on the laptop:

```powershell
$env:TRUSTBOOK_SPACE = "Smoke"
trustbook sources add folder C:\Users\anna\trustbook-eval\requests --browse-root C:/Users/anna --wait --json

$r = trustbook ask "How does HTTPBasicAuth add the Authorization header?" --json | ConvertFrom-Json
switch ($LASTEXITCODE) {
  0 { $r.answer; $r.citations | Select-Object n, source, file, @{n='lines'; e={$_.lines -join '-'}} }
  2 { "Not in the sources: $($r.refusal.message)" }
  default { "Error: $($r.message)" }
}
```

PowerShell, through the container (no local install; `-T` keeps the output clean):

```powershell
$r = docker compose exec -T app trustbook ask "How does HTTPBasicAuth add the Authorization header?" --space Smoke --json | ConvertFrom-Json
$LASTEXITCODE   # the CLI's exit code: 0, 1, 2 or 3
docker compose exec -T app trustbook sources add folder trustbook-eval/requests --space Smoke --wait
Get-Content question.txt | docker compose exec -T app trustbook ask - --space Smoke --json
```

## Secrets

The CLI never reads settings from the API and the API never returns an API key (only a hint like
`…a1b2`). As a safety net everything the CLI writes passes a redactor that masks the key values saved
in `settings.json` (when this computer or container has it: `TRUSTBOOK_DATA`, `/data` or
`~/Trustbook`) and strings shaped like well-known keys (`sk-…`, `ghp_…`, `Bearer …`, …).
