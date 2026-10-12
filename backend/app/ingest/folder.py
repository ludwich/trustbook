"""Linked folders: walk (``.gitignore`` + default ignores), secret scan, read, snapshot.

Only the path is stored (SPEC §7.1). Symlinks are never followed. Files with gitleaks findings are
skipped and listed in ``sources/<id>/secrets.json`` by file, line and rule ID only (SPEC §7.4).
Generated files (vector graphics, PDF/PostScript in disguise, minified one-liners) are skipped like
binaries: they only add noise and, being huge, dominate indexing time (Gate 1 finding G2).

Files are read by a small thread pool (overlapping file-system latency, e.g. on Docker Desktop
bind mounts); Docling conversions run one at a time in their own lane, since Docling already uses
the model threads. The snapshot is assembled in path order, so the output is deterministic.

Refresh (SPEC §7.5): every file's bytes are hashed (SHA-256). A file whose hash matches the last
indexed run is not decoded or parsed again: its text is copied from the previous ``snapshot.md``
(with its map segments moved to the new offsets) and its chunks are kept by the caller.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
from collections import deque
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from app.capabilities.cpu import usable_cpus
from app.fsutil import atomic_write_json
from app.ingest import errors
from app.ingest.errors import IngestError, fail
from app.ingest.git import head_commit
from app.ingest.gitignore import Rule, is_ignored, parse_gitignore
from app.parse.documents import DOCLING_FORMATS, DocumentParser
from app.parse.snapshot import FileEntry, Segment, Snapshot, SnapshotBuilder, normalise_text
from app.parse.text import decode_text, looks_binary
from app.security.gitleaks import (
    Finding,
    ScanFailedError,
    ScannerMissingError,
    SecretScanner,
)

logger = logging.getLogger("trustbook.folder")

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILES = 20_000
SECRETS_REPORT = "secrets.json"

IGNORED_DIRS = frozenset(
    {"node_modules", "bin", "obj", "dist", ".git", "venv", ".venv", "__pycache__"}
    | {".hg", ".svn", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".idea", ".vs"}
)
# Generated files that only add noise to answers.
IGNORED_FILES = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "Cargo.lock",
        "composer.lock",
        "Gemfile.lock",
        ".DS_Store",
        "Thumbs.db",
    }
)
TEXT_DOCUMENTS = frozenset({"md", "markdown", "rst", "txt", "adoc", "asciidoc", "org"})
BINARY_EXTENSIONS = frozenset(
    (
        "png jpg jpeg gif bmp tif tiff ico webp svgz psd heic avif "
        "zip gz tgz bz2 xz 7z rar tar zst jar war ear whl egg nupkg "
        "exe dll so dylib o a lib obj pdb class pyc pyo pyd wasm bin dat iso dmg img msi "
        "mp3 mp4 m4a wav ogg webm flac avi mov mkv opus aac "
        "woff woff2 ttf otf eot doc xls ppt odt ods odp db sqlite sqlite3 mdb accdb "
        "parquet arrow lance npy npz pkl pt pth onnx safetensors gguf "
        "svg ai eps ps"  # vector graphics: text, but generated drawing data
    ).split()
)
# PDF and PostScript content (e.g. Illustrator files under another extension) is not text.
_GENERATED_MAGIC = (b"%PDF-", b"%!PS")
# A code file with a line this long is generated or minified (bundles, embedded data).
MAX_CODE_LINE_CHARS = 10_000
READ_WORKERS_MAX = 8


@dataclass
class WalkResult:
    files: list[str] = field(default_factory=list)  # relative paths, sorted
    skipped_large: int = 0
    sizes: dict[str, int] = field(default_factory=dict)  # bytes per file (for estimates)


def _extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def walk_folder(root: Path, *, max_files: int = MAX_FILES) -> WalkResult:
    """Candidate files under ``root`` (relative, "/" separators), honouring ignore rules."""
    result = WalkResult()
    rules_by_dir: dict[str, list[Rule]] = {}
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        rel_dir = Path(current).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        parent = rel_dir.rsplit("/", 1)[0] if "/" in rel_dir else ""
        rules = list(rules_by_dir.get(parent, [])) if rel_dir else []
        ignore_file = Path(current) / ".gitignore"
        if ignore_file.is_file() and not ignore_file.is_symlink():
            with contextlib.suppress(OSError):
                rules += parse_gitignore(ignore_file.read_text("utf-8", errors="replace"), rel_dir)
        rules_by_dir[rel_dir] = rules

        def rel(name: str, base: str = rel_dir) -> str:
            return f"{base}/{name}" if base else name

        kept: list[str] = []
        for name in sorted(dirnames):
            full = Path(current) / name
            if full.is_symlink() or name in IGNORED_DIRS or is_ignored(rules, rel(name), True):
                continue
            kept.append(name)
        dirnames[:] = kept

        for name in sorted(filenames):
            full = Path(current) / name
            if full.is_symlink() or name in IGNORED_FILES or name == ".gitignore":
                continue
            if _extension(name) in BINARY_EXTENSIONS or name.endswith((".min.js", ".map")):
                continue
            if is_ignored(rules, rel(name), False):
                continue
            try:
                size = full.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                result.skipped_large += 1
                continue
            if size == 0:
                continue
            result.files.append(rel(name))
            result.sizes[rel(name)] = size
            if len(result.files) > max_files:
                raise fail(errors.FOLDER_TOO_BIG)
    result.files.sort()
    return result


def secret_findings(root: Path, scanner: SecretScanner) -> list[Finding]:
    try:
        return scanner(root)
    except ScannerMissingError as exc:
        raise fail(errors.SCANNER_MISSING) from exc
    except ScanFailedError as exc:
        raise fail(errors.SCAN_FAILED) from exc


def write_secrets_report(folder: Path, findings: list[Finding]) -> None:
    """``secrets.json``: file, line and rule ID per finding — never the secret itself."""
    report = {
        "files": sorted({f.file for f in findings}),
        "findings": [f.to_json() for f in findings],
    }
    atomic_write_json(folder / SECRETS_REPORT, report)


def looks_generated(text: str) -> bool:
    """A code file with a very long line: minified or generated, not something to read."""
    return any(len(line) > MAX_CODE_LINE_CHARS for line in text.split("\n"))


def skipped_when_read(head: bytes, ext: str) -> bool:
    """Whether a text or code file starting with ``head`` is skipped when read (estimates).

    The same checks as reading the whole file: binary, PDF/PostScript content, or (code only) a
    line over :data:`MAX_CODE_LINE_CHARS`. A line that long inside ``head`` is at least as long in
    the whole file, so a head never wrongly marks a file as generated.
    """
    if looks_binary(head) or head.lstrip()[:5].startswith(_GENERATED_MAGIC):
        return True
    return ext not in TEXT_DOCUMENTS and looks_generated(normalise_text(decode_text(head)))


@dataclass
class _FileRead:
    """One file's contribution to the snapshot (``None`` fields: nothing usable)."""

    text: str | None = None
    document: Snapshot | None = None
    generated: bool = False
    sha256: str | None = None
    reused: bool = False  # same content as last time: copy it from the previous snapshot


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_one(path: Path, ext: str, previous_sha: str | None = None) -> _FileRead:
    try:
        data = path.read_bytes()
    except OSError:
        return _FileRead()
    sha = _sha256(data)
    if previous_sha is not None and sha == previous_sha:
        return _FileRead(sha256=sha, reused=True)
    if looks_binary(data) or data.lstrip()[:5].startswith(_GENERATED_MAGIC):
        return _FileRead()
    text = normalise_text(decode_text(data))
    if not text.strip():
        return _FileRead()
    if ext not in TEXT_DOCUMENTS and looks_generated(text):
        return _FileRead(generated=True)
    return _FileRead(text=text, sha256=sha)


def _parse_one(
    documents: DocumentParser, path: Path, ext: str, rel: str, previous_sha: str | None = None
) -> _FileRead:
    try:
        sha = _sha256(path.read_bytes())
    except OSError:
        return _FileRead()
    if previous_sha is not None and sha == previous_sha:
        return _FileRead(sha256=sha, reused=True)
    try:
        return _FileRead(document=documents.parse(path, ext, title=rel), sha256=sha)
    except IngestError:
        return _FileRead()


def read_files(
    root: Path,
    files: list[str],
    documents: DocumentParser,
    progress: Callable[[float], None],
    check_cancelled: Callable[[], None],
    *,
    workers: int | None = None,
    previous_hashes: Mapping[str, str] | None = None,
) -> list[_FileRead]:
    """Read ``files`` (relative to ``root``) concurrently; results come back in ``files`` order.

    At most ``4 × workers`` files are in flight, so memory stays bounded for huge folders.
    Cancellation stops before the next file and drops the queued ones. A file whose SHA-256 is
    its ``previous_hashes`` entry comes back as ``reused`` without being decoded or parsed.
    """
    hashes = previous_hashes or {}
    size = max(1, min(workers or usable_cpus(), READ_WORKERS_MAX))
    results: list[_FileRead] = []
    pending: deque[Future[_FileRead]] = deque()
    texts = ThreadPoolExecutor(size, thread_name_prefix="read-file")
    docs = ThreadPoolExecutor(1, thread_name_prefix="read-document")
    try:
        upcoming = iter(files)
        while True:
            while len(pending) < 4 * size:
                rel = next(upcoming, None)
                if rel is None:
                    break
                path, ext, sha = root / rel, _extension(rel), hashes.get(rel)
                if ext in DOCLING_FORMATS:
                    pending.append(docs.submit(_parse_one, documents, path, ext, rel, sha))
                else:
                    pending.append(texts.submit(_read_one, path, ext, sha))
            if not pending:
                break
            check_cancelled()
            results.append(pending.popleft().result())
            progress(len(results) / max(1, len(files)))
    finally:
        for pool in (texts, docs):
            pool.shutdown(wait=True, cancel_futures=True)
    return results


@dataclass(frozen=True)
class PreviousFile:
    """A file as indexed last time: its hash and where its text sits in the old snapshot."""

    sha256: str
    kind: Literal["code", "document"]
    start: int
    end: int
    segments: tuple[Segment, ...]  # its map segments (offsets in the old snapshot)


@dataclass(frozen=True)
class PreviousSnapshot:
    """The last indexed run of a folder source, for an incremental refresh."""

    markdown: str
    files: Mapping[str, PreviousFile]


@dataclass
class FolderSnapshot(Snapshot):
    """A linked folder's snapshot plus what the incremental refresh needs.

    ``hashes``: SHA-256 per file in the snapshot. ``reused``: files copied from the previous
    snapshot, with their old start offset (their chunks are kept, moved by the offset change).
    """

    hashes: dict[str, str] = field(default_factory=dict)
    reused: dict[str, int] = field(default_factory=dict)


def _copy_previous(builder: SnapshotBuilder, rel: str, old: PreviousFile, markdown: str) -> int:
    """Append a reused file's text and its segments; return its new start offset."""
    start = builder.length
    builder.text(markdown[old.start : old.end])
    delta = start - old.start
    for segment in old.segments:
        builder.segments.append(
            replace(segment, start=segment.start + delta, end=segment.end + delta)
        )
    return start


def folder_snapshot(
    root: Path,
    relative: str,
    *,
    browse_root: Path,
    scanner: SecretScanner,
    documents: DocumentParser,
    report_folder: Path,
    progress: Callable[[float], None],
    check_cancelled: Callable[[], None],
    previous: PreviousSnapshot | None = None,
) -> FolderSnapshot:
    """Read a linked folder into one ``files`` snapshot, reusing ``previous``'s unchanged files.

    On a refresh (``previous`` given) ``stats`` gains ``changed_files`` (new, changed and removed
    files) and ``unchanged_files``.
    """
    if not root.is_dir():
        raise fail(errors.FOLDER_MISSING)
    try:
        walk = walk_folder(root)
    except OSError as exc:
        raise fail(errors.FOLDER_MISSING) from exc
    check_cancelled()
    candidates = set(walk.files)
    findings = [f for f in secret_findings(root, scanner) if f.file in candidates]
    secret_files = {f.file for f in findings}
    if findings:
        write_secrets_report(report_folder, findings)
        logger.info("Skipped %d file(s) with possible secrets", len(secret_files))
    else:
        (report_folder / SECRETS_REPORT).unlink(missing_ok=True)

    builder = SnapshotBuilder()
    entries: list[FileEntry] = []
    hashes: dict[str, str] = {}
    reused: dict[str, int] = {}
    code_files = doc_files = generated = 0
    warnings: set[str] = set()
    files = [f for f in walk.files if f not in secret_files]
    old_files: Mapping[str, PreviousFile] = previous.files if previous is not None else {}
    reads = read_files(
        root,
        files,
        documents,
        progress,
        check_cancelled,
        previous_hashes={path: old.sha256 for path, old in old_files.items()},
    )
    for rel, read in zip(files, reads, strict=True):
        ext = _extension(rel)
        generated += read.generated
        old = old_files.get(rel)
        if read.reused and old is not None and previous is not None:
            builder.separate()
            builder.text(f"# {rel}\n\n")
            start = _copy_previous(builder, rel, old, previous.markdown)
            entries.append(FileEntry(rel, start, builder.length, old.kind))
            reused[rel] = old.start
            hashes[rel] = old.sha256
            if old.kind == "code":
                code_files += 1
            else:
                doc_files += 1
            continue
        parsed = read.document
        if parsed is not None:
            warnings.update(parsed.warnings)
            builder.separate()
            builder.text(f"# {rel}\n\n")
            start = builder.length
            for segment in parsed.segments:
                piece = parsed.markdown[segment.start : segment.end]
                if builder.length > start:
                    builder.text("\n\n")
                builder.add(piece, file=rel, page=segment.page)
            entries.append(FileEntry(rel, start, builder.length, "document"))
            if read.sha256:
                hashes[rel] = read.sha256
            doc_files += 1
            continue
        text = read.text
        if text is None:
            continue
        builder.separate()
        builder.text(f"# {rel}\n\n")
        start, end = builder.add(text, file=rel, start_line=1, end_line=text.count("\n") + 1)
        kind: Literal["code", "document"] = "document" if ext in TEXT_DOCUMENTS else "code"
        entries.append(FileEntry(rel, start, end, kind))
        if read.sha256:
            hashes[rel] = read.sha256
        if kind == "code":
            code_files += 1
        else:
            doc_files += 1
    progress(1.0)
    if not entries:
        raise fail(errors.FOLDER_EMPTY)

    snapshot = FolderSnapshot(
        markdown=builder.build(),
        kind="files",
        segments=builder.segments,
        title=root.name or relative,
        files=entries,
        group="code" if code_files > doc_files else "documents",
        commit_hash=head_commit(root, browse_root),
        hashes=hashes,
        reused=reused,
    )
    snapshot.stats["files"] = len(entries)
    if previous is not None:
        kept = {e.path for e in entries}
        removed = sum(1 for path in old_files if path not in kept)
        snapshot.stats["changed_files"] = len(entries) - len(reused) + removed
        snapshot.stats["unchanged_files"] = len(reused)
    if generated:
        snapshot.stats["skipped_generated"] = generated
        logger.info("Skipped %d generated or minified file(s)", generated)
    if secret_files:
        snapshot.stats["skipped_secrets"] = len(secret_files)
        snapshot.warnings.append(errors.skipped_secrets_warning(len(secret_files)))
    snapshot.warnings.extend(sorted(warnings))
    return snapshot
