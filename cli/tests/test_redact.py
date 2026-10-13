"""The redaction safety net: saved keys and key-shaped strings never reach the output."""

from __future__ import annotations

import json
from pathlib import Path

from trustbook_cli.redact import MASK, Redactor, saved_secrets, settings_paths


def _settings(data: Path, content: object) -> None:
    (data / "config").mkdir(parents=True)
    (data / "config" / "settings.json").write_text(json.dumps(content), encoding="utf-8")


def test_saved_secrets_are_read_from_the_data_folder(tmp_path: Path) -> None:
    _settings(
        tmp_path,
        {
            "capabilities": {
                "chat": {"preset": "openai", "api_key": "plain-key-123456", "model": "gpt"},
                "web_search": {"provider": "brave", "api_key": "short"},
            },
            "extra": [{"token": "token-value-abcdef"}],
        },
    )
    env = {"TRUSTBOOK_DATA": str(tmp_path)}
    assert settings_paths(env) == [tmp_path / "config" / "settings.json"]
    assert saved_secrets(env) == ["token-value-abcdef", "plain-key-123456"]
    assert saved_secrets({"TRUSTBOOK_DATA": str(tmp_path / "missing")}) == []


def test_unreadable_settings_give_no_secrets(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.json").write_text("{not json", encoding="utf-8")
    assert saved_secrets({"TRUSTBOOK_DATA": str(tmp_path)}) == []


def test_redactor_masks_saved_values_and_their_json_escapes() -> None:
    redact = Redactor(["plain-key-123456", "kéy-with-accent-1", "tiny"])
    assert redact("x plain-key-123456 y") == f"x {MASK} y"
    assert redact(json.dumps({"v": "kéy-with-accent-1"})) == json.dumps({"v": MASK})
    assert redact("tiny") == "tiny"  # too short to be a key


def test_redactor_masks_key_shapes() -> None:
    redact = Redactor()
    for key in (
        "sk-proj-abcdefghijklmnopqrstuv",
        "sk-ant-api03-abcdefghijklmnop",
        "ghp_abcdefghijklmnopqrstuvwxyz0123",
        "github_pat_11ABCDEFG0123456789_abcdef",
        "AKIAABCDEFGHIJKLMNOP",
        "AIza" + "a" * 35,
        "xoxb-1234567890-abcdef",
        "tvly-abcdefghijklmnop1234",
    ):
        assert key not in redact(f"value {key} end"), key
    assert redact("Authorization: Bearer abc.def-ghi_jkl012") == f"Authorization: Bearer {MASK}"
    assert redact('{"api_key": "abcdef123456"}') == f'{{"api_key": "{MASK}"}}'
    assert redact("https://x.test/s?q=a&api_key=abcdef123456&n=1") == (
        f"https://x.test/s?q=a&api_key={MASK}&n=1"
    )


def test_redactor_keeps_code_and_prose() -> None:
    redact = Redactor()
    text = (
        "api_key = settings.api_key\n"
        "Set the key with --api-key. The task-runner skips sk-short.\n"
        "def get(url):\n    return request('get', url)"
    )
    assert redact(text) == text
