"""Grounding layer 1: the chat pipeline has no way to search, scrape or fetch.

Walks every module under ``app/chat`` and ``app/retrieval`` and everything they import inside
``app`` (transitively, including imports inside functions) and fails if any forbidden module is
reachable. Third-party fetch and scraping libraries are forbidden anywhere on that path; ``httpx``
may only be reached through the chat adapter interface, never imported by chat or retrieval code.
"""

from __future__ import annotations

import ast
from pathlib import Path

import app

APP_ROOT = Path(app.__file__).parent
ROOTS = ("app.chat", "app.retrieval")

FORBIDDEN_ANYWHERE = (
    "app.ingest",
    "app.parse",
    "app.security",
    "app.capabilities.adapters.search",
    "app.capabilities.adapters.factory",  # builds search adapters too
    "app.capabilities.adapters.stt_openai",
    "app.api.sources",
    "app.api.browse",
    "trafilatura",
    "yt_dlp",
    "playwright",
    "crawl4ai",
    "requests",
    "urllib.request",
    "urllib3",
    "aiohttp",
    "socket",
    "subprocess",
)
FORBIDDEN_DIRECT = ("httpx", "app.capabilities.http")


def _module_file(name: str) -> Path | None:
    parts = name.split(".")[1:]
    package = APP_ROOT.joinpath(*parts, "__init__.py")
    if package.is_file():
        return package
    module = APP_ROOT.joinpath(*parts[:-1], f"{parts[-1]}.py") if parts else None
    return module if module and module.is_file() else None


def _imports(name: str, path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"))
    package = name if path.name == "__init__.py" else name.rsplit(".", 1)[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - node.level + 1]
                target = ".".join([*base, node.module] if node.module else base)
            else:
                target = node.module or ""
            found.add(target)
            # ``from app.x import y`` may import the submodule ``app.x.y``.
            found.update(f"{target}.{alias.name}" for alias in node.names)
    # Importing ``a.b.c`` also runs the packages ``a`` and ``a.b``.
    for full in list(found):
        parts = full.split(".")
        found.update(".".join(parts[:i]) for i in range(1, len(parts)))
    return found


def _package_modules(package: str) -> list[str]:
    folder = APP_ROOT.joinpath(*package.split(".")[1:])
    names = []
    for path in sorted(folder.rglob("*.py")):
        rel = path.relative_to(APP_ROOT).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts.pop()
        names.append(".".join(["app", *parts]))
    return names


def _matches(name: str, prefixes: tuple[str, ...]) -> str | None:
    for prefix in prefixes:
        if name == prefix or name.startswith(prefix + "."):
            return prefix
    return None


def reachable() -> dict[str, set[str]]:
    """Every app module reachable from the roots → the names it imports."""
    graph: dict[str, set[str]] = {}
    todo = [m for root in ROOTS for m in _package_modules(root)]
    while todo:
        name = todo.pop()
        if name in graph:
            continue
        path = _module_file(name)
        if path is None:
            continue
        graph[name] = _imports(name, path)
        todo.extend(i for i in graph[name] if i.startswith("app.") and i not in graph)
    return graph


def test_roots_exist() -> None:
    graph = reachable()
    for module in ("app.chat.pipeline", "app.chat.overview", "app.retrieval.service"):
        assert module in graph


def test_chat_and_retrieval_cannot_reach_search_scraping_or_fetch() -> None:
    problems = []
    for module, imported in sorted(reachable().items()):
        for name in sorted(imported):
            hit = _matches(name, FORBIDDEN_ANYWHERE)
            if hit:
                problems.append(f"{module} imports {name}")
    assert problems == []


def test_chat_and_retrieval_do_not_use_http_directly() -> None:
    problems = []
    for root in ROOTS:
        for module in _package_modules(root):
            path = _module_file(module)
            assert path is not None
            for name in _imports(module, path):
                if _matches(name, FORBIDDEN_DIRECT):
                    problems.append(f"{module} imports {name}")
    assert problems == []


def test_the_walker_detects_a_forbidden_import(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text("def f():\n    from app.ingest import paste\n    import trafilatura\n")
    imported = _imports("app.chat.probe", probe)
    assert _matches("app.ingest.paste", FORBIDDEN_ANYWHERE) == "app.ingest"
    assert {"app.ingest", "app.ingest.paste", "trafilatura"} <= imported
