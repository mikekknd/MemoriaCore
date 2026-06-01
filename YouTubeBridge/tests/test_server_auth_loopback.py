import importlib.util
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from test_live_episode_plan_contract import sample_plan


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE_ROOT))

spec = importlib.util.spec_from_file_location("youtube_bridge_server_for_auth", BRIDGE_ROOT / "server.py")
server_module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(server_module)
require_bridge_key = server_module.require_bridge_key


def _request(host: str, key: str = "", path: str = ""):
    return SimpleNamespace(
        client=SimpleNamespace(host=host),
        headers={"X-Bridge-Key": key} if key else {},
        url=SimpleNamespace(path=path) if path else None,
    )


def _control_ui_source() -> str:
    static_root = Path(server_module.STATIC_ROOT)
    parts = [(static_root / "index.html").read_text(encoding="utf-8")]
    ui_root = static_root / "ui"
    if ui_root.exists():
        for name in (
            "index.css",
            "base.css",
            "live-session.css",
            "topic-pack.css",
            "topic-graph.css",
            "overlays.css",
            "core.js",
            "selectors.js",
            "topic-packs.js",
            "topic-graph.js",
            "topic-pack-crud.js",
            "fact-card-import.js",
            "memoria-control.js",
            "live-persona-control.js",
            "events-control.js",
            "summary-director-control.js",
            "session-control.js",
            "control.js",
            "app.js",
        ):
            path = ui_root / name
            if path.exists():
                parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _assert_launcher_uses_runtime_log_dir(source: str, legacy_runtime_prefix: str) -> None:
    assert r"runtime\log" in source
    assert ".foreground.log" in source or (".out.log" in source and ".err.log" in source)
    assert legacy_runtime_prefix not in source.lower()

# Split from test_server_auth.py: server, launcher, route, and UI contracts.

def test_bridge_key_is_required_even_for_loopback(monkeypatch):
    monkeypatch.setenv("YOUTUBE_BRIDGE_API_KEY", "secret")

    with pytest.raises(HTTPException) as exc:
        require_bridge_key(_request("127.0.0.1"))

    assert exc.value.status_code == 403


def test_bridge_key_accepts_matching_loopback_header(monkeypatch):
    monkeypatch.setenv("YOUTUBE_BRIDGE_API_KEY", "secret")

    require_bridge_key(_request("127.0.0.1", key="secret"))


def test_ui_config_bypasses_key_only_for_loopback(monkeypatch):
    monkeypatch.setenv("YOUTUBE_BRIDGE_API_KEY", "secret")

    require_bridge_key(_request("127.0.0.1", path="/ui-config"))

    with pytest.raises(HTTPException) as exc:
        require_bridge_key(_request("203.0.113.10", path="/ui-config"))

    assert exc.value.status_code == 403


def test_ui_assets_bypass_key_only_for_loopback(monkeypatch):
    monkeypatch.setenv("YOUTUBE_BRIDGE_API_KEY", "secret")

    require_bridge_key(_request("127.0.0.1", path="/ui-assets/app.js"))

    with pytest.raises(HTTPException) as exc:
        require_bridge_key(_request("203.0.113.10", path="/ui-assets/app.js"))

    assert exc.value.status_code == 403


def test_presentation_audio_bypasses_key_only_for_loopback(monkeypatch):
    monkeypatch.setenv("YOUTUBE_BRIDGE_API_KEY", "secret")

    require_bridge_key(_request("127.0.0.1", path="/sessions/live-a/presentation/item-a/audio"))

    with pytest.raises(HTTPException) as exc:
        require_bridge_key(_request("203.0.113.10", path="/sessions/live-a/presentation/item-a/audio"))

    assert exc.value.status_code == 403


def test_legacy_live_chat_pages_are_not_loopback_only_exceptions():
    from server_security import LOOPBACK_ONLY_PATHS

    assert "/live" not in LOOPBACK_ONLY_PATHS
    assert "/live/" not in LOOPBACK_ONLY_PATHS
    assert "/live-chat" not in LOOPBACK_ONLY_PATHS
    assert "/live-chat/" not in LOOPBACK_ONLY_PATHS
    assert "/studio" in LOOPBACK_ONLY_PATHS
    assert "/studio/" in LOOPBACK_ONLY_PATHS


def test_studio_avatar_assets_bypass_key_only_for_loopback(monkeypatch):
    monkeypatch.setenv("YOUTUBE_BRIDGE_API_KEY", "secret")

    require_bridge_key(_request("127.0.0.1", path="/studio/avatar-assets/coco.png"))

    with pytest.raises(HTTPException) as exc:
        require_bridge_key(_request("203.0.113.10", path="/studio/avatar-assets/coco.png"))

    assert exc.value.status_code == 403
