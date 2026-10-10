"""Write SearXNG's settings.yml on first start: ``python -m app.searxng_init``.

Copies the template (``TRUSTBOOK_SEARXNG_TEMPLATE``, default ``/app/searxng/settings.yml``) to
``<TRUSTBOOK_SEARXNG_CONFIG_DIR>/settings.yml`` (default ``/etc/searxng``) only when that file is
missing, replacing the placeholder ``ultrasecretkey`` with a fresh random secret. Idempotent.
"""

from __future__ import annotations

import os
import secrets
import sys
from collections.abc import Mapping
from pathlib import Path

from app.fsutil import atomic_write_text

PLACEHOLDER = "ultrasecretkey"


def main(env: Mapping[str, str] | None = None) -> int:
    """Run the init step; returns the process exit code."""
    source = os.environ if env is None else env
    template = Path(source.get("TRUSTBOOK_SEARXNG_TEMPLATE") or "/app/searxng/settings.yml")
    config_dir = Path(source.get("TRUSTBOOK_SEARXNG_CONFIG_DIR") or "/etc/searxng")
    target = config_dir / "settings.yml"
    if target.exists():
        print(f"SearXNG settings already present at {target}; leaving them unchanged.")
        return 0
    if not template.is_file():
        print(f"SearXNG settings template not found at {template}.", file=sys.stderr)
        return 1
    text = template.read_text("utf-8")
    if PLACEHOLDER not in text:
        print("Warning: the template has no secret_key placeholder.", file=sys.stderr)
    text = text.replace(PLACEHOLDER, secrets.token_hex(32))
    config_dir.mkdir(parents=True, exist_ok=True)
    # SearXNG runs as its own user in its container, so the file must be world-readable.
    atomic_write_text(target, text, mode=0o644)
    print(f"Wrote SearXNG settings to {target}.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
