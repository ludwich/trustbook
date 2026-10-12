"""Folder paths typed on Linux, macOS or Windows become the API's browse-root path (D-088)."""

from __future__ import annotations

import pytest

from trustbook_cli.client import TrustbookError
from trustbook_cli.paths import folder_api_path, is_absolute


@pytest.mark.parametrize(
    ("path", "root", "expected"),
    [
        ("work/proj", None, "work/proj"),
        ("work\\proj\\", None, "work/proj"),
        ("./work//proj", None, "work/proj"),
        ("/host/work/proj", "/host", "work/proj"),
        ("/home/anna/work", "/home/anna/", "work"),
        ("C:\\Users\\Anna\\work\\proj", "C:/Users/anna", "work/proj"),
        ("c:/users/anna/work", "C:\\Users\\Anna", "work"),
        ("\\\\nas\\share\\docs\\x", "\\\\nas\\share", "docs/x"),
    ],
)
def test_folder_api_path(path: str, root: str | None, expected: str) -> None:
    assert folder_api_path(path, root) == expected


@pytest.mark.parametrize(
    ("path", "root", "message"),
    [
        ("C:\\work", None, "--browse-root"),
        ("/srv/x", None, "--browse-root"),
        ("D:\\work", "C:/Users/anna", "not inside"),
        ("/home/annabel/x", "/home/anna", "not inside"),
        ("/Home/anna/x", "/home/anna", "not inside"),  # POSIX paths are case-sensitive
        ("/host/x", "host", "not inside"),
        ("work/../../etc", None, "'..'"),
        ("/host", "/host", "not the root itself"),
        ("  ", None, "Give a folder"),
    ],
)
def test_folder_api_path_errors(path: str, root: str | None, message: str) -> None:
    with pytest.raises(TrustbookError, match=message):
        folder_api_path(path, root)


def test_is_absolute() -> None:
    assert (
        is_absolute("/x")
        and is_absolute("C:\\x")
        and is_absolute("c:/")
        and is_absolute("\\\\h\\s")
    )
    assert not is_absolute("x/y") and not is_absolute("C:x") and not is_absolute("./x")
