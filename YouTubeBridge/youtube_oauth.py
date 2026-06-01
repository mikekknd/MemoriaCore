"""YouTube OAuth credential loading for local runtime files。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def oauth_runtime_dir() -> Path:
    configured = os.getenv("YOUTUBE_BRIDGE_OAUTH_DIR", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "runtime" / "YouTubeBridge" / "oauth"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _client_secret_section(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("installed", "web"):
        section = payload.get(key)
        if isinstance(section, dict):
            return section
    return payload


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def load_youtube_oauth_credentials(oauth_dir: Path | None = None) -> dict[str, Any]:
    root = oauth_dir or oauth_runtime_dir()
    combined = _read_json(root / "youtube_oauth.json")
    client_secret = _client_secret_section(_read_json(root / "client_secret.json"))
    token = _read_json(root / "token.json")

    client_id = _first_text(combined.get("client_id"), token.get("client_id"), client_secret.get("client_id"))
    client_secret_value = _first_text(
        combined.get("client_secret"),
        token.get("client_secret"),
        client_secret.get("client_secret"),
    )
    refresh_token = _first_text(
        combined.get("refresh_token"),
        token.get("refresh_token"),
        client_secret.get("refresh_token"),
    )
    token_uri = _first_text(
        combined.get("token_uri"),
        token.get("token_uri"),
        client_secret.get("token_uri"),
        "https://oauth2.googleapis.com/token",
    )
    fallback_channel_id = _first_text(
        combined.get("fallback_channel_id"),
        token.get("fallback_channel_id"),
        client_secret.get("fallback_channel_id"),
        os.getenv("YOUTUBE_BRIDGE_FALLBACK_CHANNEL_ID", ""),
    )

    return {
        "oauth_dir": str(root),
        "client_id": client_id,
        "client_secret": client_secret_value,
        "refresh_token": refresh_token,
        "token_uri": token_uri,
        "fallback_channel_id": fallback_channel_id,
        "client_secret_configured": bool(client_id and client_secret_value),
        "refresh_token_configured": bool(refresh_token),
        "configured": bool(client_id and client_secret_value and refresh_token),
    }
