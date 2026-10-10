"""A small ``.gitignore`` matcher: ``*``, ``?``, ``[...]``, ``**``, ``!``, trailing ``/`` and
anchored patterns, with nested ``.gitignore`` files applying to their own folder.

Directories that are ignored are pruned by the walker, so (as in git) files inside them cannot be
re-included by a later ``!`` rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    base: str  # folder of the .gitignore, relative to the walk root ("" for the root)
    regex: re.Pattern[str]
    negate: bool
    dir_only: bool


def _char_class(pattern: str, i: int) -> tuple[str, int]:
    end = pattern.find("]", i + 2 if pattern[i + 1 : i + 2] in ("!", "^") else i + 1)
    if end == -1:
        return re.escape("["), i + 1
    body = pattern[i + 1 : end]
    if body[:1] in ("!", "^"):
        body = "^" + body[1:]
    return "[" + body.replace("\\", "\\\\") + "]", end + 1


def translate(pattern: str) -> str:
    """Regex source for one gitignore glob (without anchoring)."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("/**", i) and i + 3 == len(pattern):
            out.append("/.*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            part, i = _char_class(pattern, i)
            out.append(part)
        elif c == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


def parse_gitignore(text: str, base: str = "") -> list[Rule]:
    """Rules from the contents of a ``.gitignore`` located in folder ``base``."""
    rules: list[Rule] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line.endswith("\\ "):
            line = line.rstrip(" \t")
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        if negate:
            line = line[1:]
        elif line.startswith("\\!") or line.startswith("\\#"):
            line = line[1:]
        dir_only = line.endswith("/")
        line = line.rstrip("/")
        if not line:
            continue
        anchored = "/" in line
        line = line.lstrip("/")
        body = translate(line)
        source = f"^{body}$" if anchored else f"^(?:.*/)?{body}$"
        try:
            regex = re.compile(source)
        except re.error:
            continue
        rules.append(Rule(base=base, regex=regex, negate=negate, dir_only=dir_only))
    return rules


def is_ignored(rules: list[Rule], path: str, is_dir: bool) -> bool:
    """Whether ``path`` (relative to the walk root, "/" separators) is ignored. Last match wins."""
    ignored = False
    for rule in rules:
        if rule.base:
            if not path.startswith(rule.base + "/"):
                continue
            sub = path[len(rule.base) + 1 :]
        else:
            sub = path
        if rule.dir_only and not is_dir:
            continue
        if rule.regex.match(sub):
            ignored = not rule.negate
    return ignored
