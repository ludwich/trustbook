"""Token counting with the embedding model's own tokenizer, or ``ceil(chars / 4)`` without it."""

from __future__ import annotations

import logging
import math
import threading
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger("trustbook.tokens")


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


class CharEstimate:
    """About four characters per token (used when the tokenizer is not installed)."""

    def count(self, text: str) -> int:
        return math.ceil(len(text) / 4)


class HFTokenCounter:
    """``tokenizers.Tokenizer`` from ``tokenizer.json`` (no special tokens, no truncation)."""

    def __init__(self, tokenizer: Any) -> None:
        self._tokenizer = tokenizer

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(self._tokenizer.encode(text, add_special_tokens=False).ids)


_cache: dict[Path, TokenCounter] = {}
_lock = threading.Lock()


def load_token_counter(tokenizer_file: Path) -> TokenCounter:
    """The tokenizer at ``tokenizer_file`` when it and the runtime exist, else the estimate."""
    with _lock:
        cached = _cache.get(tokenizer_file)
        if cached is not None:
            return cached
        counter: TokenCounter = CharEstimate()
        if tokenizer_file.is_file():
            try:
                from tokenizers import Tokenizer

                tokenizer = Tokenizer.from_file(str(tokenizer_file))
                tokenizer.no_truncation()
                tokenizer.no_padding()
                counter = HFTokenCounter(tokenizer)
            except ImportError:
                pass
            except Exception:
                logger.warning(
                    "Could not load %s; estimating tokens", tokenizer_file, exc_info=True
                )
        _cache[tokenizer_file] = counter
        return counter
