#!/usr/bin/env python3
"""Fetch the smoke-set corpus: psf/requests (Apache-2.0) at the pinned release commit.

    python eval/smoke/fetch_corpus.py <browse root>/trustbook-eval/requests

With git on PATH this makes a shallow clone of exactly the pinned commit (so Trustbook records the
commit). Without git it downloads the GitHub tarball of that commit instead (no commit recorded).
Standard library only; works on Linux, macOS and Windows. Run it once; re-running is a no-op.
"""

from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

REPO = "https://github.com/psf/requests.git"
TAG = "v2.34.2"
SHA = "6e83187b8feb273ed4c6cdab5efd8d54901dfab3"
VERSION = "2.34.2"
TARBALL = f"https://codeload.github.com/psf/requests/tar.gz/{SHA}"


def git(git_bin: str, *args: str, cwd: Path) -> str:
    # Fixed arguments only (the pinned repo URL and sha); no shell.
    done = subprocess.run(  # noqa: S603
        [git_bin, *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def head_sha(git_bin: str, target: Path) -> str | None:
    try:
        return git(git_bin, "rev-parse", "HEAD", cwd=target)
    except (subprocess.CalledProcessError, OSError):
        return None


def fetch_with_git(git_bin: str, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    git(git_bin, "init", "--quiet", cwd=target)
    git(git_bin, "remote", "add", "origin", REPO, cwd=target)
    git(git_bin, "fetch", "--quiet", "--depth", "1", "origin", SHA, cwd=target)
    git(git_bin, "-c", "advice.detachedHead=false", "checkout", "--quiet", "FETCH_HEAD", cwd=target)


def fetch_tarball(target: Path) -> None:
    print("git not found: downloading the tarball (Trustbook will not record a commit)")
    with urllib.request.urlopen(TARBALL, timeout=120) as resp:
        data = resp.read()
    with tempfile.TemporaryDirectory() as tmp, tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(tmp, filter="data")
        (top,) = list(Path(tmp).iterdir())  # requests-<sha>/
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.rmdir()  # empty (checked by the caller)
        shutil.move(str(top), str(target))


def check_version(target: Path) -> None:
    version_file = target / "src" / "requests" / "__version__.py"
    text = version_file.read_text(encoding="utf-8")
    if f'__version__ = "{VERSION}"' not in text:
        sys.exit(f"error: {version_file} does not say {VERSION}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "target",
        nargs="?",
        default=str(Path.home() / "trustbook-eval" / "requests"),
        help="where to put the checkout; the folder name must be 'requests' "
        "(default: ~/trustbook-eval/requests)",
    )
    args = parser.parse_args()
    target = Path(args.target).expanduser().resolve()
    if target.name != "requests":
        print("warning: the folder is not named 'requests'; expected_sources expect that title")
    git_bin = shutil.which("git")

    if target.exists() and any(target.iterdir()):
        if git_bin and head_sha(git_bin, target) == SHA:
            print(f"already at {TAG} ({SHA}): {target}")
            check_version(target)
            return 0
        sys.exit(f"error: {target} exists and is not a checkout of {SHA}; remove it first")

    print(f"fetching psf/requests {TAG} ({SHA}) into {target}")
    if git_bin:
        fetch_with_git(git_bin, target)
        if head_sha(git_bin, target) != SHA:
            sys.exit("error: checkout is not at the pinned commit")
    else:
        fetch_tarball(target)
    check_version(target)
    print(f"done: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
