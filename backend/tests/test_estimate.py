"""Processing estimates (SPEC §11): folder walk parity, heuristics, calibration gaps, wording."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from app.db.models import Source
from app.ingest import estimate as est
from app.ingest.folder import MAX_CODE_LINE_CHARS, MAX_FILE_BYTES, folder_snapshot
from app.jobs.calibration import Calibration
from app.parse.documents import DocumentParser, ParsedDocument
from app.timeutil import utc_now_iso


def calibration(
    *,
    prose: float | None = 10.0,
    code: float | None = 5.0,
    parse: float | None = 3.0,
    rtf: float | None = 0.5,
) -> Calibration:
    return Calibration(
        version=5,
        measured_at=utc_now_iso(),
        cpu_count=8,
        embedding_chunks_per_s=prose,
        embedding_chunks_per_s_code=code,
        rerank_pairs_per_s=20.0,
        parse_pages_per_s=parse,
        stt_realtime_factor=rtf,
        notes=[],
    )


def _write(root: Path, rel: str, data: bytes | str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data)
    else:
        path.write_bytes(data)


def _tree(root: Path) -> None:
    """Kept, ignored, generated and binary files of every kind the walker and reader handle."""
    _write(root, "src/app.py", "def main():\n    return 1\n")
    _write(root, "src/big.py", "def f(x):\n    return x * 2\n\n" * 1_000)  # sniffed, kept
    _write(root, "README.md", "# Project\n\nRead me.\n")
    _write(root, "notes.md", "A long markdown line. " * 600)  # long lines are fine in docs
    _write(root, "docs/guide.pdf", b"%PDF-1.4 " + b"x" * 60_000)  # a real document: parsed
    _write(root, ".gitignore", "build/\n*.log\n")
    _write(root, "build/out.py", "x = 1\n")  # .gitignore directory
    _write(root, "debug.log", "noise\n")  # .gitignore pattern
    _write(root, "node_modules/lib/index.js", "module.exports = 1\n")  # default ignore
    _write(root, "package-lock.json", "{}\n")  # ignored generated file
    _write(root, "ext/logo.svg", "<svg>" + "M0 0 L1 1 " * 100 + "</svg>")  # vector graphics
    _write(root, "ext/logo.ai", "%PDF-1.5\n%AI12 drawing data\n" * 50)
    _write(root, "static/bundle.js", "var a=1;" * (MAX_CODE_LINE_CHARS // 8 + 10))  # minified
    _write(root, "ext/art.txt", "%!PS-Adobe-3.0\n" + "showpage\n" * 2_000)  # PostScript text
    _write(root, "data/blob.py", b"\x00\x01" * 8_000)  # binary content under a code name
    _write(root, "data/huge.txt", b"a" * (MAX_FILE_BYTES + 1))  # over the size limit
    _write(root, "empty.py", "")
    _write(root, "image.png", b"\x89PNG")


class _Converter:
    def __call__(self, path: Path) -> ParsedDocument:
        return ParsedDocument(pages=[(1, f"Text of {path.name}.")], used_ocr=False)


def test_folder_files_match_what_ingestion_reads(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _tree(root)
    work = est.folder_workload(root)
    snapshot = folder_snapshot(
        root,
        "proj",
        browse_root=tmp_path,
        scanner=lambda _root: [],
        documents=DocumentParser(tmp_path, lambda: 1, converter=_Converter(), available=True),
        report_folder=tmp_path,
        progress=lambda _f: None,
        check_cancelled=lambda: None,
    )
    indexed = [f.path for f in snapshot.files]
    assert indexed == ["README.md", "docs/guide.pdf", "notes.md", "src/app.py", "src/big.py"]
    assert work.files == len(indexed)
    code = (root / "src/app.py").stat().st_size + (root / "src/big.py").stat().st_size
    text = (root / "README.md").stat().st_size + (root / "notes.md").stat().st_size
    assert work.code_chunks == pytest.approx(code / est.CODE_BYTES_PER_CHUNK)
    pdf = est.document_workload("pdf", (root / "docs/guide.pdf").stat().st_size)
    assert work.pages == pytest.approx(pdf.pages)
    assert work.text_chunks == pytest.approx(text / est.TEXT_BYTES_PER_CHUNK + pdf.text_chunks)
    assert work.fixed_s == pytest.approx(
        est.JOB_OVERHEAD_S + est.FOLDER_OVERHEAD_S + 5 * est.FOLDER_FILE_S + pdf.fixed_s
    )


def test_folder_walk_reads_only_heads_of_large_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "proj"
    _write(root, "small.py", "x = 1\n" * 100)
    _write(root, "large.py", "x = 1\n" * 50_000)
    sniffed: list[tuple[int, str]] = []
    original = est.skipped_when_read

    def spy(head: bytes, ext: str) -> bool:
        sniffed.append((len(head), ext))
        return original(head, ext)

    monkeypatch.setattr(est, "skipped_when_read", spy)
    work = est.folder_workload(root)
    assert work.files == 2
    assert sniffed == [(est.SNIFF_BYTES, "py")]


def test_folder_over_the_file_limit_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.ingest import errors
    from app.ingest.errors import IngestError
    from app.ingest.folder import walk_folder

    root = tmp_path / "proj"
    for i in range(4):
        _write(root, f"f{i}.py", "x = 1\n")
    monkeypatch.setattr(est, "walk_folder", lambda r: walk_folder(r, max_files=2))
    with pytest.raises(IngestError) as caught:
        est.folder_workload(root)
    assert caught.value.reason == errors.FOLDER_TOO_BIG[0]


def test_rates_convert_calibration_chunks_to_real_chunk_sizes() -> None:
    rates = est.rates_from(calibration(prose=10.0, code=5.0))
    assert rates.code_chunks_per_s == pytest.approx(5.0 * 686 / 360)
    assert rates.text_chunks_per_s == pytest.approx(10.0 * 351 / 445)
    assert (rates.parse_pages_per_s, rates.stt_realtime_factor) == (3.0, 0.5)
    assert rates.missing == frozenset()
    # Calibration v4 (no code rate): code uses the prose speed per token, and is not "rough".
    older = est.rates_from(calibration(code=None))
    assert older.code_chunks_per_s == pytest.approx(10.0 * 351 / 360)
    assert older.missing == frozenset()


def test_missing_calibration_uses_slow_defaults() -> None:
    rates = est.rates_from(None)
    assert rates.missing == {"embedding", "parse", "stt"}
    assert rates.code_chunks_per_s == pytest.approx(est.DEFAULT_EMBED_TOKENS_PER_S / 360)
    assert rates.parse_pages_per_s == est.DEFAULT_PARSE_PAGES_PER_S
    assert rates.stt_realtime_factor == est.DEFAULT_STT_REALTIME_FACTOR
    # Nonsense values count as not measured.
    assert est.rates_from(calibration(parse=0.0, rtf=float("nan"))).missing == {"parse", "stt"}
    measured = est.rates_from(calibration())
    assert rates.code_chunks_per_s < measured.code_chunks_per_s


def test_rough_only_when_a_needed_rate_is_missing() -> None:
    no_stt = calibration(rtf=None)
    text = est.estimate(est.upload_workload("notes.txt", 50_000), no_stt)
    assert est.ROUGH_NOTE not in text.summary
    audio = est.estimate(est.upload_workload("talk.mp3", 1_600_000), no_stt)
    assert audio.summary.endswith(", " + est.ROUGH_NOTE)
    nothing = est.estimate(est.item_workload("paste"), None)
    assert nothing.summary == "Under a minute on this PC, rough estimate (speed test not run yet)"


def test_upload_heuristics() -> None:
    pdf = est.upload_workload("report.PDF", 250_000)
    assert (pdf.pages, pdf.text_chunks, pdf.files) == (10.0, 15.0, 1)
    assert est.upload_workload("tiny.docx", 100).pages == 1.0  # at least one page
    assert est.upload_workload("deck.pptx", 600_000).text_chunks == pytest.approx(5.0)
    assert est.upload_workload("talk.mp3", 1_600_000).audio_s == pytest.approx(100.0)
    assert est.upload_workload("talk.wav", 882_000).audio_s == pytest.approx(10.0)
    assert est.upload_workload("notes.md", 14_000).text_chunks == pytest.approx(10.0)
    assert est.upload_workload("page.html", 14_000).text_chunks == pytest.approx(3.0)
    odd = est.upload_workload("photo.heic", 5_000_000)
    assert (odd.text_chunks, odd.pages, odd.audio_s, odd.fixed_s) == (0, 0, 0, est.JOB_OVERHEAD_S)


def test_seconds_add_up_each_stage() -> None:
    cal = calibration(prose=10.0, code=5.0, parse=2.0, rtf=0.5)
    rates = est.rates_from(cal)
    audio = est.upload_workload("talk.mp3", 16_000 * 300)  # 5 minutes of speech
    expected = (
        est.JOB_OVERHEAD_S
        + 300 * 0.5  # speech-to-text
        + (300 / est.AUDIO_SECONDS_PER_CHUNK) / rates.text_chunks_per_s
    )
    assert audio.seconds(rates) == pytest.approx(expected)
    pdf = est.upload_workload("a.pdf", 500_000)  # 20 pages, 30 chunks
    assert pdf.seconds(rates) == pytest.approx(
        est.JOB_OVERHEAD_S + est.DOCUMENT_OVERHEAD_S + 20 / 2.0 + 30 / rates.text_chunks_per_s
    )
    code = est.Workload(code_chunks=360.0)
    assert code.seconds(rates) == pytest.approx(360 / (5.0 * 686 / 360))


def test_items_have_small_constants() -> None:
    url = est.item_workload("url", 3)
    assert url.fixed_s == 3 * (est.URL_FETCH_S + est.JOB_OVERHEAD_S)
    assert url.text_chunks == 3 * est.URL_CHUNKS and url.files is None
    assert est.item_workload("youtube").fixed_s == est.YOUTUBE_FETCH_S + est.JOB_OVERHEAD_S
    assert est.item_workload("paste", 2).text_chunks == 2 * est.PASTE_CHUNKS


def test_confirm_only_above_two_minutes() -> None:
    assert not est.estimate(est.Workload(fixed_s=120.0), None).confirm
    assert est.estimate(est.Workload(fixed_s=120.2), None).confirm
    assert est.estimate(est.Workload(fixed_s=120.2), None).seconds == 121


def test_workloads_add() -> None:
    total = est.Workload()
    total += est.item_workload("url")
    assert total.files is None
    total += est.upload_workload("a.txt", 10)
    total += est.upload_workload("b.txt", 10)
    assert total.files == 2


@pytest.mark.parametrize(
    ("seconds", "files", "text"),
    [
        (1_500, 4_213, "About 4,200 files, roughly 25 min on this PC"),
        (180, None, "About 3 min on this PC"),
        (30, None, "Under a minute on this PC"),
        (45, 116, "About 120 files, under a minute on this PC"),
        (61, 1, "1 file, roughly 1 min on this PC"),
        (5_400, None, "About 1 h 30 min on this PC"),
        (7_100, 37, "About 37 files, roughly 2 h on this PC"),
    ],
)
def test_summary_wording(seconds: int, files: int | None, text: str) -> None:
    assert est.summarise(seconds, files, rough=False) == text


def _source(type_: str, origin: str | None = None) -> Source:
    now = utc_now_iso()
    return Source(
        id="s1",
        type=type_,
        title="T",
        origin=origin,
        group="documents",
        created_at=now,
        updated_at=now,
    )


def test_source_workloads(tmp_path: Path) -> None:
    folder = tmp_path / "source"
    folder.mkdir()
    (folder / "snapshot.md").write_text("x" * 2_800)
    assert est.source_workload(_source("paste"), folder, tmp_path) == est.text_workload(2_800)
    (folder / "snapshot.md").unlink()
    (folder / "original.pdf").write_bytes(b"%PDF" + b"x" * 49_996)
    assert est.source_workload(_source("upload", "a.pdf"), folder, tmp_path) == (
        est.upload_workload("original.pdf", 50_000)
    )
    youtube = _source("search", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert est.source_workload(youtube, folder, tmp_path) == est.item_workload("youtube")
    url = est.source_workload(_source("url", "https://example.com/a"), folder, tmp_path)
    assert url == est.item_workload("url")
    _write(tmp_path / "host" / "proj", "a.py", "x = 1\n")
    linked = est.source_workload(_source("folder", "proj"), folder, tmp_path / "host")
    assert linked is not None and linked.files == 1
    # Problems are reported by the job, never by the estimate.
    assert est.source_workload(_source("folder", "gone"), folder, tmp_path / "host") is None
    assert est.source_workload(_source("folder", "../x"), folder, tmp_path / "host") is None
    assert est.source_workload(_source("upload", "b.pdf"), tmp_path / "none", tmp_path) is None
    seconds = est.job_seconds(_source("url", "https://example.com"), folder, tmp_path, None)
    expected = est.item_workload("url").seconds(est.rates_from(None))
    assert seconds == math.ceil(expected)
