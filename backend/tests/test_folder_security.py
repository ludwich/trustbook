from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from app.ingest.browse_root import BrowsePathError, normalise_relative, resolve_in_root
from app.ingest.errors import IngestError
from app.ingest.folder import folder_snapshot, walk_folder
from app.ingest.git import head_commit
from app.ingest.gitignore import is_ignored, parse_gitignore
from app.parse.documents import DocumentParser
from app.security.addresses import (
    PRIVATE_ADDRESS_MESSAGE,
    BlockedAddressError,
    check_url,
    is_public_ip,
)
from app.security.gitleaks import (
    Finding,
    ScanFailedError,
    ScannerMissingError,
    parse_report,
    scan_folder,
)
from tests.ingest_helpers import fake_converter, fake_scanner

SHA = "a" * 40


# --- addresses -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.216.34", True),
        ("2606:2800:220:1:248:1893:25c8:1946", True),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("192.168.1.10", False),
        ("172.17.0.2", False),
        ("169.254.169.254", False),
        ("::1", False),
        ("fe80::1", False),
        ("fd00::1", False),
        ("::ffff:127.0.0.1", False),
        ("224.0.0.1", False),
        ("0.0.0.0", False),
        ("100.64.0.1", False),
        ("not-an-ip", False),
    ],
)
def test_is_public_ip(address: str, public: bool) -> None:
    assert is_public_ip(address) is public


def test_check_url_blocks_private_and_odd_urls() -> None:
    check_url("https://example.com/page", lambda _h: ["93.184.216.34"])
    with pytest.raises(BlockedAddressError, match="local network"):
        check_url("http://localhost:8484/api", lambda _h: ["127.0.0.1"])
    with pytest.raises(BlockedAddressError, match="local network"):
        check_url("http://searxng:8080/", lambda _h: ["172.18.0.3"])
    with pytest.raises(BlockedAddressError) as info:
        check_url("http://192.168.1.1/", lambda _h: [])  # literal IP: resolver not used
    assert str(info.value) == PRIVATE_ADDRESS_MESSAGE
    with pytest.raises(BlockedAddressError, match="local network"):
        check_url("https://mixed.example/", lambda _h: ["93.184.216.34", "10.0.0.1"])
    with pytest.raises(BlockedAddressError, match="http"):
        check_url("file:///etc/passwd", lambda _h: [])
    with pytest.raises(BlockedAddressError, match="user name"):
        check_url("https://user:pw@example.com/", lambda _h: ["93.184.216.34"])


# --- gitignore -------------------------------------------------------------------------------


def test_gitignore_rules() -> None:
    rules = parse_gitignore(
        "# comment\n*.log\n!keep.log\nbuild/\n/top.txt\ndocs/**/*.tmp\n**/cache\nsecret?.txt\n"
    )
    assert is_ignored(rules, "a.log", False)
    assert is_ignored(rules, "deep/x/a.log", False)
    assert not is_ignored(rules, "keep.log", False)
    assert is_ignored(rules, "build", True)
    assert is_ignored(rules, "src/build", True)
    assert not is_ignored(rules, "build", False)  # trailing slash: directories only
    assert is_ignored(rules, "top.txt", False)
    assert not is_ignored(rules, "sub/top.txt", False)  # anchored
    assert is_ignored(rules, "docs/a/b/x.tmp", False)
    assert is_ignored(rules, "docs/x.tmp", False)
    assert is_ignored(rules, "a/b/cache", True)
    assert is_ignored(rules, "secret1.txt", False)
    assert not is_ignored(rules, "secret12.txt", False)


def test_nested_gitignore_applies_to_its_folder() -> None:
    rules = parse_gitignore("*.txt\n", "sub")
    assert is_ignored(rules, "sub/a.txt", False)
    assert not is_ignored(rules, "a.txt", False)


# --- browse root -----------------------------------------------------------------------------


def test_normalise_relative_refuses_escapes() -> None:
    assert normalise_relative("") == ""
    assert normalise_relative("./a//b/") == "a/b"
    for bad in ("/etc", "../x", "a/../../b", "\\server"):
        with pytest.raises(BrowsePathError):
            normalise_relative(bad)


def test_resolve_in_root_refuses_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "host"
    (root / "inside").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link-out").symlink_to(outside)
    (root / "link-in").symlink_to(root / "inside")
    assert resolve_in_root(root, "inside")[1] == "inside"
    assert resolve_in_root(root, "link-in")[0] == (root / "inside").resolve()
    with pytest.raises(BrowsePathError):
        resolve_in_root(root, "link-out")
    with pytest.raises(FileNotFoundError):
        resolve_in_root(root, "missing")


# --- git HEAD --------------------------------------------------------------------------------


def test_head_commit_reads_refs_packed_refs_and_detached(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    git = repo / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    assert head_commit(repo) is None
    (git / "packed-refs").write_text(f"# pack-refs\n{'b' * 40} refs/heads/main\n")
    assert head_commit(repo) == "b" * 40
    (git / "refs" / "heads" / "main").write_text(SHA + "\n")
    assert head_commit(repo) == SHA
    (git / "HEAD").write_text("c" * 40 + "\n")
    assert head_commit(repo) == "c" * 40
    assert head_commit(tmp_path) is None


# --- walking ---------------------------------------------------------------------------------


def _tree(root: Path) -> None:
    files = {
        "README.md": "# Project\n\nSome docs.",
        "src/app.py": "def main():\n    return 1\n",
        "src/util.py": "X = 1\n",
        "src/notes.log": "ignored by gitignore",
        "node_modules/pkg/index.js": "ignored dir",
        "build/out.txt": "ignored by gitignore dir rule",
        ".git/config": "[core]",
        "image.png": "binary by extension",
        "package-lock.json": "{}",
        "data.bin2": "\x00\x01binary by content",
        "empty.txt": "",
    }
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (root / ".gitignore").write_text("*.log\nbuild/\n")
    (root / "big.txt").write_bytes(b"a" * (5 * 1024 * 1024 + 1))
    (root / "link.py").symlink_to(root / "src" / "app.py")


def test_walk_folder_honours_ignores(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _tree(root)
    result = walk_folder(root)
    assert result.files == ["README.md", "data.bin2", "src/app.py", "src/util.py"]
    assert result.skipped_large == 1


def test_walk_folder_too_many_files(tmp_path: Path) -> None:
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text("x")
    with pytest.raises(IngestError, match="more than"):
        walk_folder(tmp_path, max_files=3)


def _snapshot(root: Path, findings: list[Finding] | None = None) -> object:
    return folder_snapshot(
        root,
        root.name,
        browse_root=root.parent,
        scanner=fake_scanner(findings),
        documents=DocumentParser(
            root, lambda: 1, converter=fake_converter([(1, "PDF page text.")])
        ),
        report_folder=root.parent / "report",
        progress=lambda _f: None,
        check_cancelled=lambda: None,
    )


def test_folder_snapshot_skips_secrets_and_reports_without_values(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _tree(root)
    (root / "src" / "settings.py").write_text('API_KEY = "sk-live-abcdef0123456789"\n')
    (root / "manual.pdf").write_bytes(b"%PDF-1.4 fake")
    (root / ".git" / "HEAD").write_text(SHA + "\n")
    findings = [Finding("src/settings.py", 1, "generic-api-key")]
    snap = _snapshot(root, findings)
    from app.parse.snapshot import Snapshot

    assert isinstance(snap, Snapshot)
    assert "sk-live" not in snap.markdown
    assert [f.path for f in snap.files] == ["README.md", "manual.pdf", "src/app.py", "src/util.py"]
    assert snap.stats == {"files": 4, "skipped_secrets": 1}
    assert snap.warnings[0] == "1 file skipped: possible secrets."
    assert snap.commit_hash == SHA
    assert snap.group == "documents"  # 2 documents vs 2 code files
    report = json.loads((tmp_path / "report" / "secrets.json").read_text())
    assert report == {
        "files": ["src/settings.py"],
        "findings": [{"file": "src/settings.py", "line": 1, "rule_id": "generic-api-key"}],
    }
    pdf_segments = [s for s in snap.segments if s.file == "manual.pdf"]
    assert pdf_segments[0].page == 1


def test_folder_snapshot_fails_clearly(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    with pytest.raises(IngestError, match="No readable files"):
        _snapshot(root)

    def missing(_folder: Path) -> list[Finding]:
        raise ScannerMissingError("no gitleaks")

    (root / "a.py").write_text("x = 1\n")
    with pytest.raises(IngestError, match="gitleaks") as info:
        folder_snapshot(
            root,
            "proj",
            browse_root=tmp_path,
            scanner=missing,
            documents=DocumentParser(root, lambda: 1, available=False),
            report_folder=tmp_path / "r",
            progress=lambda _f: None,
            check_cancelled=lambda: None,
        )
    assert info.value.next_step is not None


# --- gitleaks --------------------------------------------------------------------------------


def test_parse_report_keeps_only_file_line_rule(tmp_path: Path) -> None:
    raw = [
        {
            "File": str(tmp_path / "src" / "a.py"),
            "StartLine": 3,
            "RuleID": "aws-access-token",
            "Secret": "AKIAREALSECRET",
            "Match": "key=AKIAREALSECRET",
        },
        {"File": "./b.env", "StartLine": 1, "RuleID": "generic"},
        "junk",
    ]
    findings = parse_report(raw, tmp_path)
    assert findings == [Finding("src/a.py", 3, "aws-access-token"), Finding("b.env", 1, "generic")]
    assert "AKIA" not in repr(findings)
    with pytest.raises(ScanFailedError):
        parse_report({"not": "a list"}, tmp_path)


def _fake_gitleaks(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "gitleaks"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_scan_folder_runs_binary_and_reads_report(tmp_path: Path) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    report_line = '[{"File":"%s/x.py","StartLine":2,"RuleID":"r1","Secret":"REDACTED"}]'
    script = _fake_gitleaks(
        tmp_path,
        'while [ "$1" != "--report-path" ]; do shift; done\n'
        f'printf \'{report_line}\' "{folder}" > "$2"\n',
    )
    assert scan_folder(folder, str(script)) == [Finding("x.py", 2, "r1")]


def test_scan_folder_errors(tmp_path: Path) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    failing = _fake_gitleaks(tmp_path, "exit 2\n")
    with pytest.raises(ScanFailedError):
        scan_folder(folder, str(failing))
    with pytest.raises(ScannerMissingError):
        scan_folder(folder, str(tmp_path / "nope"))
    old = os.environ.get("PATH", "")
    try:
        os.environ["PATH"] = str(tmp_path / "empty")
        with pytest.raises(ScannerMissingError):
            scan_folder(folder)
    finally:
        os.environ["PATH"] = old
