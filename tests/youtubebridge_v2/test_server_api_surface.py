import ast
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from YouTubeBridgeV2.runtime.application_service import (
    RuntimeCommand,
    RuntimeCommandType,
    RuntimeServiceEvent,
    RuntimeServiceResult,
)
from YouTubeBridgeV2.runtime.phase import LiveSessionPhase
from YouTubeBridgeV2.server import routes


NOW = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)


class FakeRuntimeService:
    def __init__(self):
        self.calls = []

    def create_session(self, command, now):
        self.calls.append(("create_session", command, now))
        return _result(command, phase=LiveSessionPhase.PLANNED_SHOW)

    def bind_plan(self, command, now):
        self.calls.append(("bind_plan", command, now))
        return _result(command, phase=LiveSessionPhase.PLANNED_SHOW)

    def update_aftertalk_policy(self, command, now):
        self.calls.append(("update_aftertalk_policy", command, now))
        return _result(command, phase=LiveSessionPhase.AFTERTALK)

    def request_manual_close(self, command, now):
        self.calls.append(("request_manual_close", command, now))
        return _result(command, phase=LiveSessionPhase.CLOSING)


class FailingRuntimeService:
    def create_session(self, command, now):
        raise RuntimeError("hidden_prompt raw_payload token must not leak")


class FakeQueryService:
    def __init__(self):
        self.calls = []

    def get_session(self, session_id):
        self.calls.append(("get_session", session_id))
        return {
            "session_id": session_id,
            "phase": "planned_show",
            "public_summary": {"title": "V2"},
        }

    def get_phase(self, session_id):
        self.calls.append(("get_phase", session_id))
        return {
            "session_id": session_id,
            "phase": "aftertalk",
            "closing_completion_status": "incomplete",
        }

    def get_session_events(self, session_id, limit):
        self.calls.append(("get_session_events", session_id, limit))
        return [
            {
                "event_id": "evt-1",
                "event_type": "phase_update",
                "public_payload": {"phase": "aftertalk"},
                "hidden_prompt": "must not leak",
            }
        ]

    def iter_operator_events(self, session_id):
        self.calls.append(("iter_operator_events", session_id))
        return iter(
            [
                {
                    "event_type": "operator_status",
                    "session_id": session_id,
                    "payload": {"phase": "aftertalk", "diagnostics": {"retryable": False}},
                }
            ]
        )

    def iter_display_events(self, session_id):
        self.calls.append(("iter_display_events", session_id))
        return iter(
            [
                {
                    "event_type": "display_message",
                    "session_id": session_id,
                    "diagnostics": {"retryable": False},
                    "public_payload": {
                        "text": "nested visible",
                        "diagnostics": {"operator_only": True},
                    },
                    "payload": {
                        "text": "visible",
                        "diagnostics": {"retryable": False},
                        "operator_controls": {"manual_close": True},
                        "nested": {"operator_controls": {"manual_close": True}},
                        "raw_memoriacore_payload": {"token": "must not leak"},
                    },
                }
            ]
        )


def _app(runtime_service=None, query_service=None):
    app = FastAPI()
    app.include_router(routes.router)
    if runtime_service is not None:
        app.dependency_overrides[routes.get_runtime_service] = lambda: runtime_service
    if query_service is not None:
        app.dependency_overrides[routes.get_query_service] = lambda: query_service
    app.dependency_overrides[routes.get_now] = lambda: NOW
    return app


def _result(command, *, phase):
    return RuntimeServiceResult(
        status="ok",
        session_id=command.session_id,
        phase=phase,
        events=[
            RuntimeServiceEvent(
                event_type="runtime_action_dispatched",
                session_id=command.session_id,
                phase=phase,
                payload={"safe": "visible", "hidden_prompt": "must not leak"},
                correlation_id=f"runtime-{command.command_id}",
            )
        ],
        errors=[],
        correlation_id=f"runtime-{command.command_id}",
    )


def _assert_no_private_payload(value):
    text = repr(value).lower()
    for forbidden in (
        "hidden_prompt",
        "raw_payload",
        "raw_memoriacore_payload",
        "operator_controls",
        "access_token",
        "token",
    ):
        assert forbidden not in text


def test_create_session_delegates_to_runtime_service():
    service = FakeRuntimeService()
    client = TestClient(_app(runtime_service=service))

    response = client.post(
        "/v2/sessions",
        json={
            "command_id": "cmd-create",
            "session_id": "session-1",
            "plan_id": "plan-1",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["session_id"] == "session-1"
    assert body["phase"] == "planned_show"
    assert service.calls[0][0] == "create_session"
    command = service.calls[0][1]
    assert isinstance(command, RuntimeCommand)
    assert command.command_type == RuntimeCommandType.CREATE_SESSION
    assert command.command_id == "cmd-create"
    assert command.payload["plan_id"] == "plan-1"
    _assert_no_private_payload(body)


def test_bind_plan_validates_request_shape():
    client = TestClient(_app(runtime_service=FakeRuntimeService()))

    response = client.post(
        "/v2/sessions/session-1/plan",
        json={"command_id": "cmd-bind", "hidden_prompt": "must not leak"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    _assert_no_private_payload(response.json())


def test_create_session_rejects_invalid_aftertalk_policy():
    service = FakeRuntimeService()
    client = TestClient(_app(runtime_service=service))

    response = client.post(
        "/v2/sessions",
        json={
            "command_id": "cmd-create",
            "session_id": "session-1",
            "aftertalk_policy": "legacy_director",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert service.calls == []


def test_get_session_delegates_to_query_service():
    query = FakeQueryService()
    client = TestClient(_app(query_service=query))

    response = client.get("/v2/sessions/session-1")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "session-1",
        "phase": "planned_show",
        "public_summary": {"title": "V2"},
    }
    assert query.calls == [("get_session", "session-1")]


def test_bind_plan_delegates_to_runtime_service():
    service = FakeRuntimeService()
    client = TestClient(_app(runtime_service=service))

    response = client.post(
        "/v2/sessions/session-1/plan",
        json={"command_id": "cmd-bind", "plan": {"plan_id": "plan-1"}},
    )

    assert response.status_code == 200
    assert service.calls[0][0] == "bind_plan"
    command = service.calls[0][1]
    assert command.command_type == RuntimeCommandType.BIND_PLAN
    assert command.payload == {"plan": {"plan_id": "plan-1"}}


def test_get_phase_returns_phase_status_body():
    query = FakeQueryService()
    client = TestClient(_app(query_service=query))

    response = client.get("/v2/sessions/session-1/phase")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "session-1",
        "phase": "aftertalk",
        "closing_completion_status": "incomplete",
    }
    assert query.calls == [("get_phase", "session-1")]


def test_aftertalk_policy_update_delegates_to_service():
    service = FakeRuntimeService()
    client = TestClient(_app(runtime_service=service))

    response = client.post(
        "/v2/sessions/session-1/aftertalk-policy",
        json={"command_id": "cmd-policy", "aftertalk_policy": "disabled"},
    )

    assert response.status_code == 200
    assert service.calls[0][0] == "update_aftertalk_policy"
    command = service.calls[0][1]
    assert command.command_type == RuntimeCommandType.UPDATE_AFTERTALK_POLICY
    assert command.payload == {"aftertalk_policy": "disabled"}


def test_aftertalk_policy_update_rejects_invalid_policy():
    service = FakeRuntimeService()
    client = TestClient(_app(runtime_service=service))

    response = client.post(
        "/v2/sessions/session-1/aftertalk-policy",
        json={"command_id": "cmd-policy", "aftertalk_policy": "legacy_director"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert service.calls == []


def test_manual_close_delegates_without_direct_phase_change():
    service = FakeRuntimeService()
    client = TestClient(_app(runtime_service=service))

    response = client.post(
        "/v2/sessions/session-1/manual-close",
        json={"command_id": "cmd-close", "reason": "operator"},
    )

    assert response.status_code == 200
    assert response.json()["phase"] == "closing"
    assert service.calls[0][0] == "request_manual_close"
    assert service.calls[0][1].command_type == RuntimeCommandType.MANUAL_CLOSE
    assert not hasattr(service.calls[0][1], "next_phase")


def test_get_session_events_returns_event_history():
    query = FakeQueryService()
    client = TestClient(_app(query_service=query))

    response = client.get("/v2/sessions/session-1/events?limit=20")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "session-1",
        "events": [
            {
                "event_id": "evt-1",
                "event_type": "phase_update",
                "public_payload": {"phase": "aftertalk"},
            }
        ],
    }
    assert query.calls == [("get_session_events", "session-1", 20)]


def test_get_session_events_uses_query_service_without_direct_storage_access():
    query = FakeQueryService()
    client = TestClient(_app(query_service=query))

    client.get("/v2/sessions/session-1/events")

    assert query.calls == [("get_session_events", "session-1", 100)]


def test_operator_stream_emits_operator_events():
    query = FakeQueryService()
    client = TestClient(_app(query_service=query))

    with client.stream("GET", "/v2/sessions/session-1/operator-stream") as response:
        response.read()
        text = response.text

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "operator_status" in text
    assert "diagnostics" in text
    assert query.calls == [("iter_operator_events", "session-1")]
    _assert_no_private_payload(text)


def test_display_stream_emits_display_safe_events():
    query = FakeQueryService()
    client = TestClient(_app(query_service=query))

    with client.stream("GET", "/v2/sessions/session-1/display-stream") as response:
        response.read()
        text = response.text

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "display_message" in text
    assert "visible" in text
    assert "nested visible" in text
    assert "diagnostics" not in text
    assert query.calls == [("iter_display_events", "session-1")]
    _assert_no_private_payload(text)


def test_route_error_response_is_sanitized():
    client = TestClient(_app(runtime_service=FailingRuntimeService()))

    response = client.post(
        "/v2/sessions",
        json={"command_id": "cmd-error", "session_id": "session-1"},
    )

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "service_error"
    assert body["correlation_id"] == "runtime-cmd-error"
    _assert_no_private_payload(body)


def test_routes_do_not_call_adapters_directly():
    route_path = Path(routes.__file__)
    tree = ast.parse(route_path.read_text(encoding="utf-8"))
    forbidden_imports = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names = {node.module or ""}
        else:
            continue
        if any(
            name.startswith("YouTubeBridgeV2.adapters")
            or name.startswith("YouTubeBridgeV2.storage")
            or name in {"sqlite3", "aiosqlite"}
            for name in names
        ):
            forbidden_imports.extend(sorted(names))

    assert forbidden_imports == []
