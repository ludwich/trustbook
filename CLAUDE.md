# CLAUDE.md — Trustbook builder instructions

You are building **Trustbook** from `SPEC.md`. Read it fully before writing code. It is the single source of truth.

## Working mode
- **Do not ask questions.** Every decision is in `SPEC.md`. When it's silent, apply SPEC §15 (Decision rules) and log the choice in `docs/DECISIONS.md`.
- Work **one phase at a time**, in order (SPEC §14), on branch `phase-N`. **Never commit directly to `main`.**
- Keep a running task list in `docs/TASKS.md` (current phase only) and update it as you go, so a restarted session can resume.
- At the end of a phase:
  1. CI green on `phase-N`.
  2. Open PR `phase-N → main` with a summary.
  3. Confirm `ghcr.io/ludwich/trustbook:phase-N-rc` is published.
  4. Update `docs/GATES.md` with the gate checklist and exactly how to verify it.
  5. **Stop** and summarise in 5–10 lines: what shipped, the image tag, what Ludwig should check.
- When Ludwig says the gate **passed**: squash-merge the PR, tag `phase-N` on `main`, create branch `phase-N+1`, continue.
- When he reports a **failure**: fix on the same branch, push, wait for the new `-rc` image, stop again.
- Commit small and often, conventional commits (`feat:`, `fix:`, `chore:`, `test:`, `docs:`).

## Quality bar
- Backend: `ruff check`, `ruff format`, `mypy --strict app/`, `pytest` must pass.
- Frontend: `pnpm lint`, `pnpm typecheck`, `pnpm test` must pass.
- Every pipeline step has a unit test; every API route has an integration test with the mock LLM adapter.
- Never weaken a grounding layer to make a test pass.

## Hard rules
- Only MIT / Apache-2.0 / BSD / Unlicense dependencies and models are bundled. Verify each model's license on its model card; use the SPEC fallback otherwise. Keep `THIRD_PARTY_LICENSES.md` current.
- SearXNG stays an unmodified, separate container.
- No telemetry, no update checks, no network calls in tests.
- Never commit secrets, real customer data or private evaluation sets. Never print or log the GitHub token.
- API keys live only in `~/Trustbook/config/settings.json` (mode 600), never in a space folder or logs.
- Gitleaks findings are shown as file, line and rule ID only — never the secret value.

## Environment
- You run as user `dev` on LXC 130 (dev-workbench), Ubuntu 24.04, with Docker and the `gh` CLI authenticated to the `trustbook` repo.
- Build and run locally with `docker compose -f compose.dev.yaml up --build` for your own testing. Clean up dangling images after each phase (`docker image prune -f`); disk is limited.
- Images are built and published by GitHub Actions, not from this machine.
- Ludwig verifies gates on his Windows laptop: `docker compose pull` then `docker compose up -d` with `TRUSTBOOK_IMAGE_TAG=phase-N-rc` in `.env` → `http://localhost:8484`.
