from __future__ import annotations

import os

import pytest

from gemini_token import GEMINI_API_KEY_ENV, ensure_gemini_token, resolve_token_path


def test_resolve_token_path_defaults_to_config(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    default = resolve_token_path()
    assert default == tmp_path / ".config" / "amagent" / "gemini_api_token"


def test_resolve_token_path_uses_overrides(monkeypatch, tmp_path):
    override = tmp_path / "override.txt"
    monkeypatch.setenv("AMEDIS_GEMINI_TOKEN_PATH", str(override))
    resolved = resolve_token_path()
    assert resolved == override


def test_ensure_gemini_token_from_cli(tmp_path, monkeypatch):
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    path = tmp_path / "token.txt"
    token, source = ensure_gemini_token("abc", persist=True, path=path)
    assert token == "abc"
    assert source == "cli"
    assert os.environ[GEMINI_API_KEY_ENV] == "abc"
    assert path.read_text(encoding="utf-8").strip() == "abc"


def test_ensure_gemini_token_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv(GEMINI_API_KEY_ENV, "from-env")
    path = tmp_path / "token.txt"
    token, source = ensure_gemini_token(persist=True, path=path)
    assert token == "from-env"
    assert source == "env"
    assert path.read_text(encoding="utf-8").strip() == "from-env"


def test_ensure_gemini_token_from_file(tmp_path, monkeypatch):
    token_path = tmp_path / "stored.txt"
    token_path.write_text("saved-token\n", encoding="utf-8")
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)

    token, source = ensure_gemini_token(path=token_path)
    assert token == "saved-token"
    assert source == "file"
    assert os.environ[GEMINI_API_KEY_ENV] == "saved-token"


def test_ensure_gemini_token_empty(monkeypatch, tmp_path):
    token_path = tmp_path / "empty.txt"
    token_path.write_text("\n", encoding="utf-8")
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)

    token, source = ensure_gemini_token(path=token_path)
    assert token is None
    assert source == "none"


def test_ensure_gemini_token_rejects_empty_cli():
    with pytest.raises(ValueError):
        ensure_gemini_token("   ")

