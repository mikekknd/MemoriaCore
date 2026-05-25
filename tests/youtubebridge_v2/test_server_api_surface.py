import ast
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from YouTubeBridgeV2.live_episode_plan.runner import (
    PlanExecutionStatus,
    validate_episode_plan_contract,
)
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

    def update_automation_control(self, command, now):
        self.calls.append(("update_automation_control", command, now))
        return _result(command, phase=LiveSessionPhase.PLANNED_SHOW)

    def request_manual_close(self, command, now):
        self.calls.append(("request_manual_close", command, now))
        return _result(command, phase=LiveSessionPhase.CLOSING)

    def tick_session(self, command, now):
        self.calls.append(("tick_session", command, now))
        return _result(command, phase=LiveSessionPhase.AFTERTALK)

    def handle_youtube_event(self, command, now):
        self.calls.append(("handle_youtube_event", command, now))
        return _result(command, phase=LiveSessionPhase.PLANNED_SHOW)


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
                    "display_contract_version": "v1",
                    "event_id": "display-1",
                    "event_type": "audience_message",
                    "source_event_type": "youtube_text_message",
                    "public_payload": {
                        "author_display_name": "Mika",
                        "message_text": "visible",
                        "display_flags": {"moderator": True},
                        "diagnostics": {"operator_only": True},
                        "operator_controls": {"manual_close": True},
                    },
                }
            ]
        )

    def get_tts_queue(self, session_id, limit=100, status=None):
        self.calls.append(("get_tts_queue", session_id, limit, status))
        return [
            {
                "delivery_id": "tts-event-1",
                "status": "pending",
                "text": "Line",
                "metadata": {"safe": "visible", "raw_payload": {"token": "must not leak"}},
            }
        ]


class FakeTTSStorage:
    def __init__(self):
        self.acks = []
        self.timeouts = []

    def ack_v2_tts_delivery(self, session_id, delivery_id, record):
        self.acks.append((session_id, delivery_id, dict(record)))
        return {
            "delivery_id": delivery_id,
            "session_id": session_id,
            "status": "delivered",
            "duplicate": False,
            "phase_transition_requested": False,
        }

    def timeout_v2_tts_delivery(self, session_id, delivery_id, record):
        self.timeouts.append((session_id, delivery_id, dict(record)))
        return {
            "delivery_id": delivery_id,
            "session_id": session_id,
            "status": "timeout",
            "timeout_seconds": record["timeout_seconds"],
            "phase_transition_requested": False,
            "metadata": record.get("metadata", {}),
        }


def _app(runtime_service=None, query_service=None, storage_manager=None):
    app = FastAPI()
    app.include_router(routes.router)
    if runtime_service is not None:
        app.dependency_overrides[routes.get_runtime_service] = lambda: runtime_service
    if query_service is not None:
        app.dependency_overrides[routes.get_query_service] = lambda: query_service
    if storage_manager is not None:
        app.dependency_overrides[routes.get_storage_manager] = lambda: storage_manager
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


def test_list_episode_plans_reads_child_episode_plan_json_packages(tmp_path, monkeypatch):
    root = tmp_path / "EpisodePlans"
    alpha_dir = root / "Alpha"
    beta_dir = root / "Nested" / "Beta"
    broken_dir = root / "Broken"
    alpha_dir.mkdir(parents=True)
    beta_dir.mkdir(parents=True)
    broken_dir.mkdir(parents=True)
    (alpha_dir / "episode-plan.json").write_text(
        json.dumps(
            {
                "plan_id": "plan-alpha",
                "title": "Alpha Show",
                "turns": [],
                "raw_topic_pack": {"hidden_prompt": "must not leak"},
            }
        ),
        encoding="utf-8",
    )
    (beta_dir / "episode-plan.json").write_text(
        json.dumps(
            {
                "plan_id": "plan-beta",
                "title": "Beta Show",
                "turns": [{"id": "turn-1", "hidden_prompt": "must not leak"}],
            }
        ),
        encoding="utf-8",
    )
    (broken_dir / "episode-plan.json").write_text("{not json", encoding="utf-8")
    (root / "ignored.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(routes, "runtime_path", lambda *parts: root, raising=False)
    client = TestClient(_app())

    response = client.get("/v2/episode-plans")

    assert response.status_code == 200
    body = response.json()
    assert [entry["plan_id"] for entry in body["episode_plans"]] == [
        "plan-alpha",
        "plan-beta",
    ]
    assert body["episode_plans"][0] == {
        "id": "Alpha",
        "plan_id": "plan-alpha",
        "title": "Alpha Show",
        "folder": "Alpha",
        "filename": "episode-plan.json",
        "updated_at": body["episode_plans"][0]["updated_at"],
        "plan": {"plan_id": "plan-alpha", "title": "Alpha Show", "turns": []},
    }
    assert body["episode_plans"][1]["folder"] == "Nested/Beta"
    assert body["episode_plans"][1]["plan"]["turns"] == [{"id": "turn-1"}]
    _assert_no_private_payload(body)


def test_list_episode_plans_projects_planner_segments_to_bindable_turns(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "EpisodePlans"
    plan_dir = root / "PlannerPackage"
    plan_dir.mkdir(parents=True)
    (plan_dir / "episode-plan.json").write_text(
        json.dumps(
            {
                "schema_version": "live_episode_plan.v1",
                "plan_id": "planner-plan",
                "title": "Planner Format Show",
                "participants": [
                    {"participant_id": "p_host", "display_name": "Host"},
                    {"participant_id": "p_analyst", "display_name": "Analyst"},
                ],
                "segments": [
                    {
                        "segment_id": "opening",
                        "title": "Opening",
                        "goal": "Open the show.",
                        "planned_turn_contracts": [
                            {
                                "turn_id": "opening_turn_01",
                                "turn_type": "opening",
                                "intent": "Host opens with a short greeting.",
                                "speaker_policy": {
                                    "selection_mode": "fixed",
                                    "allowed_participant_ids": ["p_host"],
                                },
                                "evidence_brief": {
                                    "facts_to_state": ["Fact A"],
                                    "source_boundaries": ["Boundary A"],
                                },
                            },
                            {
                                "turn_id": "opening_turn_02",
                                "turn_type": "cohost_intro",
                                "intent": "Analyst explains the source boundary.",
                                "speaker_policy": {
                                    "selection_mode": "fixed",
                                    "allowed_character_ids": ["character-analyst"],
                                },
                            },
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(routes, "runtime_path", lambda *parts: root, raising=False)
    client = TestClient(_app())

    response = client.get("/v2/episode-plans")

    assert response.status_code == 200
    plan = response.json()["episode_plans"][0]["plan"]
    assert plan["turns"] == [
        {
            "id": "opening_turn_01",
            "purpose": "Host opens with a short greeting.",
            "topic_cue": (
                "Segment: Opening\n"
                "Goal: Open the show.\n"
                "Intent: Host opens with a short greeting.\n"
                "Facts: Fact A\n"
                "Source boundaries: Boundary A"
            ),
            "speaker_policy": {
                "type": "fixed",
                "speaker_ids": ["p_host"],
            },
            "audience_insertion": {
                "enabled": False,
                "allow_super_chats": False,
            },
        },
        {
            "id": "opening_turn_02",
            "purpose": "Analyst explains the source boundary.",
            "topic_cue": (
                "Segment: Opening\n"
                "Goal: Open the show.\n"
                "Intent: Analyst explains the source boundary."
            ),
            "speaker_policy": {
                "type": "fixed",
                "speaker_ids": ["character-analyst"],
            },
            "audience_insertion": {
                "enabled": False,
                "allow_super_chats": False,
            },
        },
    ]
    contract = validate_episode_plan_contract(plan)
    assert contract.status is PlanExecutionStatus.RUNNING
    assert contract.validation_errors == ()
    _assert_no_private_payload(plan)


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


def test_automation_control_delegates_to_runtime_service():
    service = FakeRuntimeService()
    client = TestClient(_app(runtime_service=service))

    response = client.post(
        "/v2/sessions/session-1/automation-control",
        json={
            "command_id": "cmd-control",
            "paused": True,
            "enabled": False,
            "reason": "operator pause",
        },
    )

    assert response.status_code == 200
    assert service.calls[0][0] == "update_automation_control"
    command = service.calls[0][1]
    assert command.command_type == RuntimeCommandType.UPDATE_AUTOMATION_CONTROL
    assert command.payload == {
        "enabled": False,
        "paused": True,
        "reason": "operator pause",
    }


def test_automation_control_requires_enabled_or_paused():
    service = FakeRuntimeService()
    client = TestClient(_app(runtime_service=service))

    response = client.post(
        "/v2/sessions/session-1/automation-control",
        json={"command_id": "cmd-control", "reason": "missing flags"},
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


def test_tick_session_delegates_to_runtime_service():
    service = FakeRuntimeService()
    client = TestClient(_app(runtime_service=service))

    response = client.post(
        "/v2/sessions/session-1/tick",
        json={"command_id": "cmd-tick"},
    )

    assert response.status_code == 200
    assert response.json()["phase"] == "aftertalk"
    assert service.calls[0][0] == "tick_session"
    command = service.calls[0][1]
    assert command.command_type == RuntimeCommandType.TICK
    assert command.command_id == "cmd-tick"
    assert command.payload == {}


def test_ingest_youtube_event_delegates_to_runtime_service():
    service = FakeRuntimeService()
    client = TestClient(_app(runtime_service=service))

    response = client.post(
        "/v2/sessions/session-1/youtube-events",
        json={
            "command_id": "cmd-youtube-api",
            "youtube_event": {
                "id": "yt-evt-1",
                "snippet": {
                    "type": "textMessageEvent",
                    "displayMessage": "Hello runtime",
                    "textMessageDetails": {"messageText": "Hello runtime"},
                },
                "authorDetails": {"displayName": "Mika", "channelId": "channel-1"},
                "raw_payload": {"access_token": "must not leak"},
            },
            "polling_cursor": {
                "live_chat_id": "live-chat-1",
                "next_page_token": "page-1",
                "polling_interval_millis": 1500,
                "seen_event_ids": [],
            },
            "page_info": {
                "next_page_token": "page-2",
                "polling_interval_millis": 2500,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert service.calls[0][0] == "handle_youtube_event"
    command = service.calls[0][1]
    assert command.command_type == RuntimeCommandType.HANDLE_YOUTUBE_EVENT
    assert command.command_id == "cmd-youtube-api"
    assert command.session_id == "session-1"
    assert command.payload["youtube_event"]["id"] == "yt-evt-1"
    assert command.payload["polling_cursor"]["live_chat_id"] == "live-chat-1"
    assert command.payload["page_info"]["next_page_token"] == "page-2"
    _assert_no_private_payload(response.json())


def test_ingest_youtube_event_requires_event_payload():
    service = FakeRuntimeService()
    client = TestClient(_app(runtime_service=service))

    response = client.post(
        "/v2/sessions/session-1/youtube-events",
        json={"command_id": "cmd-youtube-api"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert service.calls == []
    _assert_no_private_payload(response.json())


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


def test_get_tts_queue_delegates_to_query_service():
    query = FakeQueryService()
    client = TestClient(_app(query_service=query))

    response = client.get("/v2/sessions/session-1/tts-queue?limit=10&status=pending")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "session-1",
        "tts_queue": [
            {
                "delivery_id": "tts-event-1",
                "status": "pending",
                "text": "Line",
                "metadata": {"safe": "visible"},
            }
        ],
    }
    assert query.calls[-1] == ("get_tts_queue", "session-1", 10, "pending")
    _assert_no_private_payload(response.json())


def test_ack_tts_delivery_delegates_to_storage_manager():
    storage = FakeTTSStorage()
    client = TestClient(_app(storage_manager=storage))

    response = client.post(
        "/v2/sessions/session-1/tts-deliveries/tts-event-1/ack",
        json={"command_id": "ack-1"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "delivered"
    assert response.json()["phase_transition_requested"] is False
    assert storage.acks[0][0:2] == ("session-1", "tts-event-1")
    assert storage.acks[0][2]["acknowledged_at"] == NOW


def test_timeout_tts_delivery_delegates_to_storage_manager_without_phase_change():
    storage = FakeTTSStorage()
    client = TestClient(_app(storage_manager=storage))

    response = client.post(
        "/v2/sessions/session-1/tts-deliveries/tts-event-1/timeout",
        json={
            "command_id": "timeout-1",
            "timeout_seconds": 30,
            "metadata": {"safe": "visible", "raw_payload": {"token": "must not leak"}},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "timeout"
    assert response.json()["timeout_seconds"] == 30
    assert response.json()["phase_transition_requested"] is False
    assert storage.timeouts[0][2]["metadata"] == {"safe": "visible"}
    _assert_no_private_payload(response.json())


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
    assert "audience_message" in text
    assert "youtube_text_message" in text
    assert "visible" in text
    assert "moderator" in text
    assert "diagnostics" not in text
    assert "operator_controls" not in text
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
