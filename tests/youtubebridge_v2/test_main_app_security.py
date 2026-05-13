from __future__ import annotations

from fastapi.testclient import TestClient

from core.storage_manager import StorageManager
from YouTubeBridgeV2.runtime.phase import LiveSessionPhase
from YouTubeBridgeV2.server.security import PermissionGroup


OPERATOR_KEY = "operator-secret"
DISPLAY_KEY = "display-secret"
OBSERVER_KEY = "observer-secret"


class CapturingRuntimeService:
    def __init__(self):
        self.commands = []

    def create_session(self, command, now):
        self.commands.append(command)
        return {
            "status": "ok",
            "session_id": command.session_id,
            "phase": LiveSessionPhase.PLANNED_SHOW,
            "events": [],
            "errors": [],
            "correlation_id": f"runtime-{command.command_id}",
        }

    def tick_session(self, command, now):
        self.commands.append(command)
        return {
            "status": "ok",
            "session_id": command.session_id,
            "phase": LiveSessionPhase.PLANNED_SHOW,
            "events": [],
            "errors": [],
            "correlation_id": f"runtime-{command.command_id}",
        }


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


def _remote_client(app):
    return TestClient(app, client=("203.0.113.10", 50000))


def _loopback_client(app):
    return TestClient(app, client=("127.0.0.1", 50000))


def _save_api_keys(storage, entries=None):
    storage.save_prefs(
        {
            "youtubebridge_v2_api_keys": entries
            if entries is not None
            else [
                {"key": OPERATOR_KEY, "permission_group": "operator"},
                {"key": DISPLAY_KEY, "permission_group": "display"},
                {"key": OBSERVER_KEY, "permission_group": "observer"},
                {"key": "", "permission_group": "operator"},
                {"key": "bad-group-secret", "permission_group": "admin"},
            ]
        }
    )


def _create_remote_session(client, *, key=OPERATOR_KEY, session_id="session-sec"):
    return client.post(
        "/v2/sessions",
        headers={"x-youtubebridgev2-api-key": key},
        json={
            "command_id": f"cmd-create-{session_id}",
            "session_id": session_id,
            "aftertalk_policy": "auto",
        },
    )


def _assert_security_error(response, *, status_code, code):
    assert response.status_code == status_code
    assert response.json()["error"] == {
        "code": code,
        "message": "authentication required" if status_code == 401 else "permission denied",
    }
    lower = response.text.lower()
    for forbidden in (OPERATOR_KEY, DISPLAY_KEY, OBSERVER_KEY, "wrong-secret"):
        assert forbidden not in lower


def test_main_app_v2_remote_request_without_key_is_rejected_before_runtime(
    tmp_path,
    monkeypatch,
):
    storage = _storage_manager(tmp_path)
    _save_api_keys(storage)
    api_main = _install_test_storage(monkeypatch, storage)
    client = _remote_client(api_main.app)

    response = client.post(
        "/v2/sessions",
        json={"command_id": "cmd-create", "session_id": "remote-no-key"},
    )

    _assert_security_error(response, status_code=401, code="unauthorized")
    assert storage.get_v2_session("remote-no-key") is None


def test_main_app_v2_invalid_key_is_rejected_without_secret_leak(
    tmp_path,
    monkeypatch,
):
    storage = _storage_manager(tmp_path)
    _save_api_keys(storage)
    api_main = _install_test_storage(monkeypatch, storage)
    client = _remote_client(api_main.app)

    response = client.post(
        "/v2/sessions",
        headers={"x-youtubebridgev2-api-key": "wrong-secret"},
        json={"command_id": "cmd-create", "session_id": "remote-wrong-key"},
    )

    _assert_security_error(response, status_code=401, code="unauthorized")
    assert storage.get_v2_session("remote-wrong-key") is None


def test_main_app_v2_operator_key_can_write_and_read_all_v2_surfaces(
    tmp_path,
    monkeypatch,
):
    storage = _storage_manager(tmp_path)
    _save_api_keys(storage)
    api_main = _install_test_storage(monkeypatch, storage)
    client = _remote_client(api_main.app)

    create_response = _create_remote_session(client)
    phase_response = client.get(
        "/v2/sessions/session-sec/phase",
        headers={"x-youtubebridgev2-api-key": OPERATOR_KEY},
    )
    events_response = client.get(
        "/v2/sessions/session-sec/events",
        headers={"x-youtubebridgev2-api-key": OPERATOR_KEY},
    )
    tick_response = client.post(
        "/v2/sessions/session-sec/tick",
        headers={"x-youtubebridgev2-api-key": OPERATOR_KEY},
        json={"command_id": "cmd-operator-tick"},
    )
    with client.stream(
        "GET",
        "/v2/sessions/session-sec/operator-stream",
        headers={"x-youtubebridgev2-api-key": OPERATOR_KEY},
    ) as operator_stream:
        operator_stream.read()
        operator_text = operator_stream.text
    with client.stream(
        "GET",
        "/v2/sessions/session-sec/display-stream",
        headers={"x-youtubebridgev2-api-key": OPERATOR_KEY},
    ) as display_stream:
        display_stream.read()
        display_status = display_stream.status_code

    assert create_response.status_code == 200
    assert phase_response.status_code == 200
    assert events_response.status_code == 200
    assert tick_response.status_code == 200
    assert "operator_status" in operator_text
    assert display_status == 200


def test_main_app_v2_operator_key_accepts_authorization_bearer(
    tmp_path,
    monkeypatch,
):
    storage = _storage_manager(tmp_path)
    _save_api_keys(storage)
    api_main = _install_test_storage(monkeypatch, storage)
    client = _remote_client(api_main.app)

    response = client.post(
        "/v2/sessions",
        headers={"Authorization": f"Bearer {OPERATOR_KEY}"},
        json={"command_id": "cmd-create", "session_id": "bearer-session"},
    )

    assert response.status_code == 200
    assert storage.get_v2_session("bearer-session")["current_phase"] == "planned_show"


def test_main_app_v2_operator_key_accepts_x_api_key_alias(
    tmp_path,
    monkeypatch,
):
    storage = _storage_manager(tmp_path)
    _save_api_keys(storage)
    api_main = _install_test_storage(monkeypatch, storage)
    client = _remote_client(api_main.app)

    response = client.post(
        "/v2/sessions",
        headers={"x-api-key": OPERATOR_KEY},
        json={"command_id": "cmd-create", "session_id": "x-api-key-session"},
    )

    assert response.status_code == 200
    assert storage.get_v2_session("x-api-key-session")["current_phase"] == "planned_show"


def test_main_app_v2_runtime_command_receives_api_key_permission_context(
    tmp_path,
    monkeypatch,
):
    storage = _storage_manager(tmp_path)
    _save_api_keys(storage)
    api_main = _install_test_storage(monkeypatch, storage)
    service = CapturingRuntimeService()
    monkeypatch.setitem(
        api_main.app.dependency_overrides,
        api_main.youtubebridge_v2_routes.get_runtime_service,
        lambda: service,
    )
    client = _remote_client(api_main.app)

    response = client.post(
        "/v2/sessions",
        headers={"x-youtubebridgev2-api-key": OPERATOR_KEY},
        json={"command_id": "cmd-capture", "session_id": "capture-session"},
    )

    assert response.status_code == 200
    assert len(service.commands) == 1
    permission = service.commands[0].permission_context
    assert permission is not None
    assert permission.auth_method == "api_key"
    assert permission.permission_group == PermissionGroup.OPERATOR
    assert permission.is_loopback is False
    assert "create_session" in permission.allowed_actions


def test_main_app_v2_tick_command_receives_api_key_permission_context(
    tmp_path,
    monkeypatch,
):
    storage = _storage_manager(tmp_path)
    _save_api_keys(storage)
    api_main = _install_test_storage(monkeypatch, storage)
    service = CapturingRuntimeService()
    monkeypatch.setitem(
        api_main.app.dependency_overrides,
        api_main.youtubebridge_v2_routes.get_runtime_service,
        lambda: service,
    )
    client = _remote_client(api_main.app)

    response = client.post(
        "/v2/sessions/session-sec/tick",
        headers={"x-youtubebridgev2-api-key": OPERATOR_KEY},
        json={"command_id": "cmd-capture-tick"},
    )

    assert response.status_code == 200
    assert len(service.commands) == 1
    permission = service.commands[0].permission_context
    assert permission is not None
    assert permission.auth_method == "api_key"
    assert permission.permission_group == PermissionGroup.OPERATOR
    assert permission.is_loopback is False
    assert "tick_session" in permission.allowed_actions


def test_main_app_v2_observer_key_can_read_status_events_and_operator_stream_only(
    tmp_path,
    monkeypatch,
):
    storage = _storage_manager(tmp_path)
    _save_api_keys(storage)
    api_main = _install_test_storage(monkeypatch, storage)
    client = _remote_client(api_main.app)
    assert _create_remote_session(client, session_id="observer-session").status_code == 200

    session_response = client.get(
        "/v2/sessions/observer-session",
        headers={"x-youtubebridgev2-api-key": OBSERVER_KEY},
    )
    status_response = client.get(
        "/v2/sessions/observer-session/phase",
        headers={"x-youtubebridgev2-api-key": OBSERVER_KEY},
    )
    events_response = client.get(
        "/v2/sessions/observer-session/events",
        headers={"x-youtubebridgev2-api-key": OBSERVER_KEY},
    )
    with client.stream(
        "GET",
        "/v2/sessions/observer-session/operator-stream",
        headers={"x-youtubebridgev2-api-key": OBSERVER_KEY},
    ) as operator_stream:
        operator_stream.read()
        operator_status = operator_stream.status_code
    display_response = client.get(
        "/v2/sessions/observer-session/display-stream",
        headers={"x-youtubebridgev2-api-key": OBSERVER_KEY},
    )
    tick_response = client.post(
        "/v2/sessions/observer-session/tick",
        headers={"x-youtubebridgev2-api-key": OBSERVER_KEY},
        json={"command_id": "cmd-observer-tick"},
    )
    write_response = client.post(
        "/v2/sessions",
        headers={"x-youtubebridgev2-api-key": OBSERVER_KEY},
        json={"command_id": "cmd-observer-write", "session_id": "observer-write"},
    )

    assert session_response.status_code == 200
    assert session_response.json()["session_id"] == "observer-session"
    assert status_response.status_code == 200
    assert events_response.status_code == 200
    assert operator_status == 200
    _assert_security_error(display_response, status_code=403, code="forbidden")
    _assert_security_error(tick_response, status_code=403, code="forbidden")
    _assert_security_error(write_response, status_code=403, code="forbidden")
    assert storage.get_v2_session("observer-write") is None


def test_main_app_v2_display_key_can_read_display_stream_only(
    tmp_path,
    monkeypatch,
):
    storage = _storage_manager(tmp_path)
    _save_api_keys(storage)
    api_main = _install_test_storage(monkeypatch, storage)
    client = _remote_client(api_main.app)
    assert _create_remote_session(client, session_id="display-session").status_code == 200

    with client.stream(
        "GET",
        "/v2/sessions/display-session/display-stream",
        headers={"x-youtubebridgev2-api-key": DISPLAY_KEY},
    ) as display_stream:
        display_stream.read()
        display_status = display_stream.status_code
    phase_response = client.get(
        "/v2/sessions/display-session/phase",
        headers={"x-youtubebridgev2-api-key": DISPLAY_KEY},
    )
    events_response = client.get(
        "/v2/sessions/display-session/events",
        headers={"x-youtubebridgev2-api-key": DISPLAY_KEY},
    )
    operator_response = client.get(
        "/v2/sessions/display-session/operator-stream",
        headers={"x-youtubebridgev2-api-key": DISPLAY_KEY},
    )
    manual_close_response = client.post(
        "/v2/sessions/display-session/manual-close",
        headers={"x-youtubebridgev2-api-key": DISPLAY_KEY},
        json={"command_id": "cmd-display-close", "reason": "not-allowed"},
    )
    tick_response = client.post(
        "/v2/sessions/display-session/tick",
        headers={"x-youtubebridgev2-api-key": DISPLAY_KEY},
        json={"command_id": "cmd-display-tick"},
    )

    assert display_status == 200
    _assert_security_error(phase_response, status_code=403, code="forbidden")
    _assert_security_error(events_response, status_code=403, code="forbidden")
    _assert_security_error(operator_response, status_code=403, code="forbidden")
    _assert_security_error(manual_close_response, status_code=403, code="forbidden")
    _assert_security_error(tick_response, status_code=403, code="forbidden")


def test_main_app_v2_loopback_without_key_still_has_operator_access(
    tmp_path,
    monkeypatch,
):
    storage = _storage_manager(tmp_path)
    _save_api_keys(storage)
    api_main = _install_test_storage(monkeypatch, storage)
    client = _loopback_client(api_main.app)

    response = client.post(
        "/v2/sessions",
        json={"command_id": "cmd-loopback", "session_id": "loopback-session"},
    )

    assert response.status_code == 200
    assert storage.get_v2_session("loopback-session")["current_phase"] == "planned_show"


def test_main_app_v2_remote_fails_closed_when_no_valid_keys_configured(
    tmp_path,
    monkeypatch,
):
    storage = _storage_manager(tmp_path)
    _save_api_keys(
        storage,
        [
            {"key": "", "permission_group": "operator"},
            {"key": "bad-group-secret", "permission_group": "admin"},
        ],
    )
    api_main = _install_test_storage(monkeypatch, storage)
    client = _remote_client(api_main.app)

    response = client.post(
        "/v2/sessions",
        headers={"x-youtubebridgev2-api-key": "bad-group-secret"},
        json={"command_id": "cmd-invalid-config", "session_id": "invalid-config"},
    )

    _assert_security_error(response, status_code=401, code="unauthorized")
    assert storage.get_v2_session("invalid-config") is None


def test_main_app_v2_static_assets_remain_public_without_api_key(
    tmp_path,
    monkeypatch,
):
    storage = _storage_manager(tmp_path)
    _save_api_keys(storage)
    api_main = _install_test_storage(monkeypatch, storage)
    client = _remote_client(api_main.app)

    operator_response = client.get("/v2/static/operator-console/index.html")
    display_response = client.get("/v2/static/chat-display/index.html")

    assert operator_response.status_code == 200
    assert 'id="operatorConsoleRoot"' in operator_response.text
    assert display_response.status_code == 200
    assert 'id="chatDisplayRoot"' in display_response.text
