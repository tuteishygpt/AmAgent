"""Utilities for managing Gemini API tokens.

This module centralises the logic for persisting and loading the Gemini API
token that the Google ADK runtime expects in the ``GOOGLE_API_KEY`` environment
variable.  The helper functions keep the token handling lightweight so the CLI
and other entry points can ensure a token is available without duplicating
filesystem or environment management code.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional, Tuple

GEMINI_API_KEY_ENV = "GOOGLE_API_KEY"
TOKEN_PATH_ENV = "AMEDIS_GEMINI_TOKEN_PATH"
_DEFAULT_FILENAME = "gemini_api_token"

TokenSource = Literal["cli", "env", "file", "none"]


def resolve_token_path(path: Optional[Path | str] = None) -> Path:
    """Return the filesystem path that should store the Gemini API token."""

    if path:
        return Path(path).expanduser()

    override = os.getenv(TOKEN_PATH_ENV)
    if override:
        return Path(override).expanduser()

    config_home = os.getenv("XDG_CONFIG_HOME")
    if config_home:
        base_dir = Path(config_home).expanduser()
    else:
        base_dir = Path.home() / ".config"

    return base_dir / "amagent" / _DEFAULT_FILENAME


def ensure_gemini_token(
    token: Optional[str] = None,
    *,
    persist: bool = False,
    path: Optional[Path | str] = None,
) -> Tuple[Optional[str], TokenSource]:
    """Ensure a Gemini API token is present and optionally persist it.

    Parameters
    ----------
    token:
        Explicit token provided by the caller. When supplied it takes priority
        over other sources and will be written to the environment immediately.
    persist:
        When ``True`` the resolved token will be written to the configured path
        for subsequent runs.
    path:
        Optional custom path that overrides the default location on disk.

    Returns
    -------
    tuple
        A ``(token_value, source)`` pair where ``source`` indicates how the
        token was obtained (``"cli"``, ``"env"``, ``"file"`` or ``"none"``).
    """

    resolved_path = resolve_token_path(path)

    if token is not None:
        cleaned = token.strip()
        if not cleaned:
            raise ValueError("Gemini API token cannot be empty")
        os.environ[GEMINI_API_KEY_ENV] = cleaned
        if persist:
            _write_token(resolved_path, cleaned)
        return cleaned, "cli"

    env_token = os.environ.get(GEMINI_API_KEY_ENV)
    if env_token:
        cleaned_env = env_token.strip()
        if cleaned_env:
            if persist:
                _write_token(resolved_path, cleaned_env)
            return cleaned_env, "env"

    file_token = _read_token(resolved_path)
    if file_token:
        os.environ[GEMINI_API_KEY_ENV] = file_token
        return file_token, "file"

    return None, "none"


def _read_token(path: Path) -> Optional[str]:
    try:
        contents = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return contents or None


def _write_token(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{token}\n", encoding="utf-8")


__all__ = [
    "GEMINI_API_KEY_ENV",
    "TokenSource",
    "ensure_gemini_token",
    "resolve_token_path",
]

