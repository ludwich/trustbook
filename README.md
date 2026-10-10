# Trustbook

> **Trust your notebook.** A local-first research notebook. Add sources into isolated *spaces* and ask
> questions. Every answer comes only from those sources, with citations you can check in one click.

Status: **proof of concept, Phase 0 (foundations)**. Spaces, settings, the setup wizard and the job queue
work; adding sources and chat arrive in Phase 1. See [`SPEC.md`](SPEC.md) for the full plan.

- Runs on your machine in containers (Docker Desktop, Docker Engine, Podman or Rancher Desktop).
- Embeddings, reranking, answer checking and speech-to-text run locally on CPU, bundled in the image.
- The chat model is yours: OpenAI, Azure OpenAI, Anthropic, Ollama, llama-server, LM Studio, vLLM or any
  OpenAI-compatible endpoint.
- No telemetry and no update checks. Settings → Request log shows exactly what was sent to any endpoint.

## Install

You need Docker (or Podman) with Compose, and about 6 GB of free disk space.

1. Create a folder, for example `trustbook`, and put two files in it:
   - [`compose.yaml`](compose.yaml)
   - [`.env.example`](.env.example), renamed to `.env`
2. Edit `.env`. Use full paths, not `~`:

   | Setting | Windows example | Linux / macOS example | Meaning |
   |---|---|---|---|
   | `TRUSTBOOK_BROWSE_ROOT` | `C:/Users/anna` | `/home/anna` | Folder Trustbook may read (your home). Mounted read-only. |
   | `TRUSTBOOK_DATA` | `C:/Users/anna/Trustbook` | `/home/anna/Trustbook` | Where spaces and settings are stored. |
   | `TRUSTBOOK_PORT` | `8484` | `8484` | Port for the web UI. |
   | `TRUSTBOOK_IMAGE_TAG` | `latest` | `latest` | Image version. |
   | `GITHUB_OWNER` | `ludwich` | `ludwich` | Owner of the image on GitHub Container Registry. |

3. Start it:

   ```
   docker compose pull
   docker compose up -d
   ```

4. Open <http://localhost:8484> and follow the setup wizard. Point it at your chat model and press **Test**.

Stop with `docker compose down`. Your data stays in `TRUSTBOOK_DATA`.

### Connecting to a model on the same machine

Inside the container, `localhost` is the container itself. To reach Ollama, llama-server or LM Studio
running on your computer, use `http://host.docker.internal:<port>/v1` (the presets do this for you).

## Your data

```
Trustbook/
├── config/settings.json   # endpoints and API keys (only here, file mode 600)
├── global.db              # job queue and request log
└── spaces/<space>/        # one folder per space: delete it to delete the space, zip it to hand it over
```

API keys are never stored inside a space folder.

## Development

```
# backend
cd backend && uv sync && uv run pytest
uv run uvicorn app.main:app --reload --port 8484

# frontend (pnpm 10)
cd frontend && pnpm install && pnpm dev     # proxies /api to :8484

# whole stack, built locally
docker compose -f compose.dev.yaml up --build
```

Builder notes: [`CLAUDE.md`](CLAUDE.md) · architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) ·
decisions: [`docs/DECISIONS.md`](docs/DECISIONS.md) · gates: [`docs/GATES.md`](docs/GATES.md).

## Credits

- The Ideation tab (Phase 4) follows the *grill-me* interview pattern by Matt Pocock; Trustbook's
  implementation and prompts are its own.
- Bundled models and libraries are listed in [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md).
- Web search uses an unmodified [SearXNG](https://github.com/searxng/searxng) container (AGPL-3.0),
  run as a separate service.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
