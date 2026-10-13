"""Safety net: API keys never reach stdout or stderr (CLAUDE.md, "Hard rules").

The CLI never asks the API for settings, and the API never returns a key (only a hint), so no key
should ever be in its output. Everything it writes still passes :class:`Redactor`, which masks
the values saved in ``settings.json`` (when this computer or container has it) and anything
shaped like a well-known key.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

MASK = "[redacted]"
# Settings values under these names are secrets; shorter values are not worth masking.
_SECRET_NAMES = re.compile(r"(^|_)(api_?key|key|token|secret|password)$", re.IGNORECASE)
_MIN_SECRET = 8
_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),  # OpenAI, Anthropic (sk-ant-…)
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\btvly-[A-Za-z0-9\-]{16,}"),  # Tavily
    re.compile(r"\bBSA[A-Za-z0-9_\-]{20,}"),  # Brave Search
)
_BEARER = re.compile(r"(\bBearer\s+)[A-Za-z0-9._~+/=\-]{12,}", re.IGNORECASE)
# A key in JSON (``"api_key": "…"``) or a URL query (``?api_key=…``); not code like
# ``api_key = settings.api_key``, which cited passages may quote.
_KEY_JSON = re.compile(r'("(?:api_?key|x-api-key)"\s*:\s*")[^"]{6,}(")', re.IGNORECASE)
_KEY_QUERY = re.compile(r"([?&](?:api_?key|key|token)=)[^&\s\"']{6,}", re.IGNORECASE)


def settings_paths(env: Mapping[str, str]) -> list[Path]:
    """Where ``settings.json`` may be: the data folder as the app finds it (config.py)."""
    found: list[Path] = []
    if env.get("TRUSTBOOK_DATA"):
        found.append(Path(env["TRUSTBOOK_DATA"]))
    else:
        found.extend((Path("/data"), Path.home() / "Trustbook"))
    return [p / "config" / "settings.json" for p in found]


def _secret_values(node: Any, name: str = "") -> Iterable[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _secret_values(value, str(key))
    elif isinstance(node, list):
        for item in node:
            yield from _secret_values(item, name)
    elif isinstance(node, str) and len(node) >= _MIN_SECRET and _SECRET_NAMES.search(name):
        yield node


def saved_secrets(env: Mapping[str, str] | None = None) -> list[str]:
    """Key values saved in ``settings.json``, longest first; [] when it can't be read."""
    env = os.environ if env is None else env
    secrets: set[str] = set()
    for path in settings_paths(env):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        secrets.update(_secret_values(data))
    return sorted(secrets, key=len, reverse=True)


class Redactor:
    """Masks known secret values and key-shaped strings in text."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self._secrets = [s for s in secrets if len(s) >= _MIN_SECRET]

    def __call__(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, MASK)
            # The JSON output is ASCII-escaped; a key with non-ASCII characters appears escaped.
            escaped = json.dumps(secret)[1:-1]
            if escaped != secret:
                text = text.replace(escaped, MASK)
        for pattern in _PATTERNS:
            text = pattern.sub(MASK, text)
        text = _BEARER.sub(lambda m: m.group(1) + MASK, text)
        text = _KEY_JSON.sub(lambda m: m.group(1) + MASK + m.group(2), text)
        return _KEY_QUERY.sub(lambda m: m.group(1) + MASK, text)
