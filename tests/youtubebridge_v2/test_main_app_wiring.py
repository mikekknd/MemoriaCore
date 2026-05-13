from __future__ import annotations

import ast
from pathlib import Path

from fastapi.testclient import TestClient

from core.storage_manager import StorageManager


ROOT = Path(__file__).resolve().parents[2]


def _storage_manager(tmp_path):
    return StorageManager(
        prefs_file=str(tmp_path / "prefs.json"),
        history_file=str(tmp_path / "history.json"),
        persona_snapshot_db_path=str(tmp_path / "persona_snapshots.db"),
        youtube_bridge_v2_db_path=str(tmp_path / "youtubebridge_v2.db"),
    )


def _install_test_storage(monkeypatch, storage):
    import api.main as api_main

    monkeypatch.setattr(api_main, "get_storage", lambda: storage)
    monkeypatch.setattr(api_main, "_v2_composition_cache", None, raising=False)
    monkeypatch.setattr(api_main, "_v2_composition_storage_id", None, raising=False)
    return api_main


def _loopback_client(app):
    return TestClient(app, client=("127.0.0.1", 50000))


def _remote_client(app):
    return TestClient(app, client=("203.0.113.10", 50000))


def test_main_app_v2_routes_use_real_storage_composition(tmp_path, monkeypatch):
    storage = _storage_manager(tmp_path)
    api_main = _install_test_storage(monkeypatch, storage)
    client = _loopback_client(api_main.app)

    response = client.post(
        "/v2/sessions",
        json={
            "command_id": "cmd-create",
            "session_id": "session-main",
            "aftertalk_policy": "auto",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["phase"] == "planned_show"
    assert "v2_runtime_not_configured" not in repr(response.json())
    assert storage.get_v2_session("session-main")["current_phase"] == "planned_show"


def test_main_app_v2_status_reads_durable_session(tmp_path, monkeypatch):
    storage = _storage_manager(tmp_path)
    api_main = _install_test_storage(monkeypatch, storage)
    client = _loopback_client(api_main.app)
    client.post(
        "/v2/sessions",
        json={
            "command_id": "cmd-create",
            "session_id": "session-status",
            "aftertalk_policy": "auto",
        },
    )

    response = client.get("/v2/sessions/session-status/phase")

    assert response.status_code == 200
    assert response.json()["session_id"] == "session-status"
    assert response.json()["phase"] == "planned_show"
    assert "v2_runtime_not_configured" not in repr(response.json())


def test_main_app_v2_reuses_cached_composition_for_same_storage(tmp_path, monkeypatch):
    import api.main as api_main
    from YouTubeBridgeV2.composition import create_v2_composition

    storage_a = _storage_manager(tmp_path / "a")
    storage_b = _storage_manager(tmp_path / "b")
    calls = []

    def factory(storage_manager):
        calls.append(storage_manager)
        return create_v2_composition(storage_manager=storage_manager)

    monkeypatch.setattr(api_main, "create_production_v2_composition", factory, raising=False)
    monkeypatch.setattr(api_main, "_v2_composition_cache", None, raising=False)
    monkeypatch.setattr(api_main, "_v2_composition_storage_id", None, raising=False)
    monkeypatch.setattr(api_main, "get_storage", lambda: storage_a)

    first = api_main._get_v2_composition()
    second = api_main._get_v2_composition()
    monkeypatch.setattr(api_main, "get_storage", lambda: storage_b)
    third = api_main._get_v2_composition()

    assert first is second
    assert third is not first
    assert calls == [storage_a, storage_b]


def test_main_app_v2_missing_session_returns_sanitized_404(tmp_path, monkeypatch):
    storage = _storage_manager(tmp_path)
    api_main = _install_test_storage(monkeypatch, storage)
    client = _loopback_client(api_main.app)

    response = client.get("/v2/sessions/missing-session/phase")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "session_not_found",
            "message": "session not found",
        },
        "correlation_id": "query-missing-session",
    }


def test_main_app_v2_missing_session_events_and_streams_return_sanitized_404(
    tmp_path,
    monkeypatch,
):
    storage = _storage_manager(tmp_path)
    api_main = _install_test_storage(monkeypatch, storage)
    client = _loopback_client(api_main.app)

    responses = [
        client.get("/v2/sessions/missing-session/events"),
        client.get("/v2/sessions/missing-session/operator-stream"),
        client.get("/v2/sessions/missing-session/display-stream"),
    ]

    for response in responses:
        assert response.status_code == 404
        assert response.json() == {
            "error": {
                "code": "session_not_found",
                "message": "session not found",
            },
            "correlation_id": "query-missing-session",
        }


def test_main_app_v2_rejects_non_loopback_api_request(tmp_path, monkeypatch):
    storage = _storage_manager(tmp_path)
    api_main = _install_test_storage(monkeypatch, storage)
    client = _remote_client(api_main.app)

    response = client.post(
        "/v2/sessions",
        json={"command_id": "cmd-create", "session_id": "remote-session"},
    )

    assert response.status_code in {401, 403}
    assert response.json()["error"]["code"] in {"unauthorized", "forbidden"}
    assert storage.get_v2_session("remote-session") is None


def test_main_app_v2_loopback_boundary_ignores_similar_non_v2_prefix(
    tmp_path,
    monkeypatch,
):
    storage = _storage_manager(tmp_path)
    api_main = _install_test_storage(monkeypatch, storage)
    client = _remote_client(api_main.app)

    response = client.get("/v2-health")

    assert response.status_code == 404
    assert "unauthorized" not in response.text.lower()
    assert "forbidden" not in response.text.lower()


def test_main_app_v2_static_assets_remain_served(tmp_path, monkeypatch):
    storage = _storage_manager(tmp_path)
    api_main = _install_test_storage(monkeypatch, storage)
    client = _remote_client(api_main.app)

    operator_response = client.get("/v2/static/operator-console/index.html")
    display_response = client.get("/v2/static/chat-display/index.html")

    assert operator_response.status_code == 200
    assert 'id="operatorConsoleRoot"' in operator_response.text
    assert display_response.status_code == 200
    assert 'id="chatDisplayRoot"' in display_response.text


def test_main_app_v2_wiring_does_not_import_legacy_youtubebridge():
    paths = [
        ROOT / "api" / "main.py",
        ROOT / "YouTubeBridgeV2" / "production.py",
        ROOT / "YouTubeBridgeV2" / "runtime" / "noop_runners.py",
        ROOT / "YouTubeBridgeV2" / "server" / "main_security.py",
    ]

    forbidden_imports = []
    for path in paths:
        assert path.exists(), f"missing expected wiring file: {path}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {node.module or ""}
            else:
                continue
            forbidden_imports.extend(
                name
                for name in names
                if name == "YouTubeBridge" or name.startswith("YouTubeBridge.")
            )

    assert forbidden_imports == []
