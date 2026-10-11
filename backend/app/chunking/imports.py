"""Resolve a code file's imports to files of the same source (D-077).

The files are those of one folder snapshot. Resolution is deliberately narrow: an import either
names exactly one file of the source, or it resolves to nothing (``None``).

- Python relative ``.x`` / ``..x.y`` → ``x.py`` or ``x/__init__.py`` next to (above) the importer;
  ``from . import x`` → ``x.py`` / ``x/__init__.py`` of the importer's package, else its
  ``__init__.py``.
- Python absolute ``pkg.x`` → the one file whose path ends in ``pkg/x.py`` or
  ``pkg/x/__init__.py`` (so a ``src/`` layout resolves); none when several files match.
- JavaScript / TypeScript ``./x``, ``../x`` → ``x`` itself, ``x.ts``, ``x.tsx``, ``x.js``, … or
  ``x/index.*``; a ``.js`` specifier also finds the ``.ts`` file it is compiled from. Package
  specifiers (``react``) resolve to nothing.
- Anything else (C# ``using``, which names a namespace, not a file) resolves to nothing.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterable

_PYTHON_EXTENSIONS = (".py", ".pyi")
_SCRIPT_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".d.ts")


class SourceFiles:
    """The file paths of one snapshot, indexed for import resolution (built once per snapshot)."""

    def __init__(self, paths: Iterable[str]) -> None:
        self.paths = frozenset(paths)
        # Python module suffix ("requests", "utils") → files that are that module.
        self._modules: dict[tuple[str, ...], set[str]] = {}
        for path in self.paths:
            parts = _module_parts(path)
            if parts is None:
                continue
            for k in range(1, len(parts) + 1):
                self._modules.setdefault(parts[-k:], set()).add(path)

    def python(self, importer: str, module: str, name: str | None = None) -> str | None:
        """The file ``module`` (``.auth``, ``..x.y``, ``requests.utils``) of ``from module import
        name`` / ``import module`` in file ``importer`` names, else ``None``.

        ``name``: the one imported name of ``from . import name`` (a submodule, when it is a file).
        """
        dots = len(module) - len(module.lstrip("."))
        rest = module[dots:]
        if dots == 0:
            if not rest:
                return None
            found = self._modules.get(tuple(rest.split(".")), set())
            return next(iter(found)) if len(found) == 1 else None
        base = posixpath.dirname(importer)
        for _ in range(dots - 1):
            if not base:
                return None
            base = posixpath.dirname(base)
        package = posixpath.join(base, *rest.split(".")) if rest else base
        if name is not None:
            submodule = self._python_file(posixpath.join(package, name) if package else name)
            if submodule is not None:
                return submodule
        if not rest:
            init = posixpath.join(package, "__init__.py") if package else "__init__.py"
            return init if init in self.paths else None
        return self._python_file(package)

    def script(self, importer: str, specifier: str) -> str | None:
        """The file a JavaScript/TypeScript ``specifier`` (``./x``, ``../lib/y.js``) names."""
        if not specifier.startswith(("./", "../")):
            return None
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(importer), specifier))
        if joined.startswith("../") or joined == "..":
            return None
        stem, extension = posixpath.splitext(joined)
        candidates = [joined]
        if extension in (".js", ".jsx", ".mjs", ".cjs"):  # ESM imports name the compiled file
            candidates += [stem + e for e in (".ts", ".tsx", ".mts", ".cts")]
        candidates += [joined + e for e in _SCRIPT_EXTENSIONS]
        candidates += [posixpath.join(joined, "index" + e) for e in _SCRIPT_EXTENSIONS]
        return next((c for c in candidates if c in self.paths), None)

    def _python_file(self, module_path: str) -> str | None:
        for candidate in (
            *(module_path + e for e in _PYTHON_EXTENSIONS),
            *(posixpath.join(module_path, "__init__" + e) for e in _PYTHON_EXTENSIONS),
        ):
            if candidate in self.paths:
                return candidate
        return None


def _module_parts(path: str) -> tuple[str, ...] | None:
    """``("requests", "utils")`` for ``src/requests/utils.py`` (and ``…/utils/__init__.py``)."""
    stem, extension = posixpath.splitext(path)
    if extension not in _PYTHON_EXTENSIONS:
        return None
    parts = tuple(p for p in stem.split("/") if p)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return parts or None
