"""PDF, DOCX, PPTX and XLSX into page-mapped snapshots.

Docling (DECISIONS D-026) is used when its runtime and baked models are present. Otherwise PDFs
fall back to pypdf (with a warning) and Office files fail with a clear reason. One Docling
converter is cached per process (its pipeline takes seconds to start); conversions are serialised
because they already use every model thread.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.ingest import errors
from app.ingest.errors import IngestError, fail
from app.parse.snapshot import Snapshot, SnapshotBuilder, normalise_text, sections_from_markdown

logger = logging.getLogger("trustbook.parse")

DOCLING_FORMATS = frozenset({"pdf", "docx", "pptx", "xlsx"})


@dataclass
class ParsedDocument:
    """Markdown per page (page ``None`` when the format has no pages) and whether OCR was used."""

    pages: list[tuple[int | None, str]]
    used_ocr: bool = False
    warnings: list[str] = field(default_factory=list)


Converter = Callable[[Path], ParsedDocument]


def docling_artifacts(models_dir: Path) -> Path:
    env = os.environ.get("DOCLING_ARTIFACTS_PATH")
    return Path(env) if env else models_dir / "docling"


def docling_available(artifacts: Path) -> bool:
    return importlib.util.find_spec("docling") is not None and artifacts.is_dir()


class DoclingConverter:
    """Lazily builds and caches Docling's ``DocumentConverter`` (offline, calibrated threads)."""

    def __init__(self, artifacts: Path, threads: Callable[[], int]) -> None:
        self._artifacts = artifacts
        self._threads = threads
        self._converter: Any = None
        self._built_threads: int | None = None
        self._lock = threading.Lock()

    def _build(self, threads: int) -> Any:
        from docling.datamodel.accelerator_options import AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        options = PdfPipelineOptions(
            artifacts_path=str(self._artifacts),
            enable_remote_services=False,
            allow_external_plugins=False,
            do_ocr=True,
            ocr_options=RapidOcrOptions(backend="torch", lang=["en"]),
            do_table_structure=True,
            generate_parsed_pages=True,
            accelerator_options=AcceleratorOptions(device="cpu", num_threads=threads),
        )
        return DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.DOCX, InputFormat.PPTX, InputFormat.XLSX],
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
        )

    def __call__(self, path: Path) -> ParsedDocument:
        threads = self._threads()
        with self._lock:
            if self._converter is None or self._built_threads != threads:
                self._converter = self._build(threads)
                self._built_threads = threads
            try:
                result = self._converter.convert(path)
            except Exception as exc:
                logger.info("Docling could not convert %s: %s", path.name, type(exc).__name__)
                raise fail(errors.DAMAGED_FILE) from exc
        document = result.document
        page_numbers = sorted(int(n) for n in (getattr(document, "pages", None) or {}))
        if page_numbers:
            pages: list[tuple[int | None, str]] = [
                (n, str(document.export_to_markdown(page_no=n))) for n in page_numbers
            ]
        else:
            pages = [(None, str(document.export_to_markdown()))]
        used_ocr = any(
            p.parsed_page is not None and any(c.from_ocr for c in p.parsed_page.textline_cells)
            for p in (result.pages or [])
        )
        return ParsedDocument(pages=pages, used_ocr=used_ocr)


def pypdf_pages(path: Path) -> ParsedDocument:
    """Text per page with pypdf (fast; image-only pages give empty text)."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(""):
            raise fail(errors.ENCRYPTED_PDF)
        pages: list[tuple[int | None, str]] = [
            (i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)
        ]
    except IngestError:
        raise
    except (PdfReadError, ValueError, OSError, KeyError, TypeError) as exc:
        raise fail(errors.DAMAGED_FILE) from exc
    return ParsedDocument(pages=pages)


def _snapshot(parsed: ParsedDocument, title: str | None) -> Snapshot:
    if len(parsed.pages) == 1 and parsed.pages[0][0] is None:
        markdown = normalise_text(parsed.pages[0][1])
        return Snapshot(markdown, "sections", sections_from_markdown(markdown), title=title)
    builder = SnapshotBuilder()
    for number, text in parsed.pages:
        cleaned = normalise_text(text).strip("\n")
        if not cleaned.strip():
            continue
        builder.separate()
        builder.add(cleaned, page=number)
    snapshot = Snapshot(builder.build(), "pages", builder.segments, title=title)
    snapshot.stats["pages"] = len(parsed.pages)
    return snapshot


class DocumentParser:
    """Parses Docling formats; ``converter`` is injectable for tests."""

    def __init__(
        self,
        artifacts: Path,
        threads: Callable[[], int],
        *,
        converter: Converter | None = None,
        available: bool | None = None,
    ) -> None:
        self.converter: Converter = converter or DoclingConverter(artifacts, threads)
        self.docling = (
            available
            if available is not None
            else (converter is not None or docling_available(artifacts))
        )

    def parse(self, path: Path, ext: str, title: str | None = None) -> Snapshot:
        """Snapshot of the document at ``path``; ``ext`` is the lower-case extension."""
        if ext not in DOCLING_FORMATS:
            raise fail(errors.UNSUPPORTED_FILE)
        warnings: list[str] = []
        if self.docling:
            try:
                parsed = self.converter(path)
            except Exception:
                if ext != "pdf":
                    raise
                logger.warning("Docling failed on a PDF; using the basic reader", exc_info=True)
                return self._basic_pdf(path, title)
            if parsed.used_ocr:
                warnings.append(errors.WARN_OCR)
            if not any(text.strip() for _, text in parsed.pages):
                raise fail(errors.SCANNED_PDF_NO_TEXT if ext == "pdf" else errors.NO_TEXT)
        elif ext == "pdf":
            return self._basic_pdf(path, title)
        else:
            raise fail(errors.OFFICE_NEEDS_PARSER)
        snapshot = _snapshot(parsed, title)
        snapshot.warnings.extend(warnings + parsed.warnings)
        return snapshot

    def _basic_pdf(self, path: Path, title: str | None) -> Snapshot:
        parsed = pypdf_pages(path)
        if not any(text.strip() for _, text in parsed.pages):
            raise fail(errors.SCANNED_PDF)
        snapshot = _snapshot(parsed, title)
        snapshot.warnings.append(errors.WARN_BASIC_PDF)
        return snapshot
