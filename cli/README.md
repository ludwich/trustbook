# trustbook-cli

The `trustbook` command: create spaces, add sources and ask grounded questions of a running Trustbook
over its HTTP API (default `http://127.0.0.1:8484`). It uses only the public API, so every answer goes
through the same grounding and refusal as the UI.

```
pip install "git+https://github.com/ludwich/trustbook@main#subdirectory=cli"
trustbook spaces list
trustbook ask "How do I configure proxies?" --space Smoke --json
```

`trustbook eval` (the evaluation runner) also needs PyYAML: install the `eval` extra,
`pip install "trustbook-cli[eval]"` (from a checkout: `pip install "./cli[eval]"`).

The image ships it too: `docker compose exec app trustbook …`. Full reference, JSON schemas and exit
codes: [docs/cli.md](../docs/cli.md). Licensed under Apache-2.0.
