from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from core.storage_manager import StorageManager
from tests.youtubebridge_v2.fakes import InMemoryV2StorageManager
from YouTubeBridgeV2.app import create_v2_app
from YouTubeBridgeV2.adapters.memoria_http import (
    MemoriaHttpTransportConfig,
    MemoriaSyncHttpTransport,
)
from YouTubeBridgeV2.composition import create_v2_composition
from YouTubeBridgeV2.production import create_production_v2_composition
from YouTubeBridgeV2.runtime.memoria_runners import (
    MemoriaAftertalkRunner,
    MemoriaClosingRunner,
    MemoriaPlannedShowRunner,
)


NOW = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)


class FakeMemoriaTransport:
    def __init__(self, *responses: dict[str, object]) -> None:
        self.responses = list(responses)
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


def _plan() -> dict[str, object]:
    return {
        "plan_id": "plan-tick",
        "title": "Tick vertical slice",
        "turns": [
            {
                "id": "opening",
                "purpose": "Open the V2 tick show.",
                "topic_cue": "Runtime tick flow.",
                "speaker_policy": {"type": "fixed", "speaker_ids": ["host", "cohost"]},
                "audience_insertion": {"enabled": False, "allow_super_chats": False},
            }
        ],
        "raw_topic_pack": "must not leak",
    }


def _transport() -> FakeMemoriaTransport:
    return FakeMemoriaTransport(
        {
            "session_id": "memoria-planned",
            "message_id": "planned-1",
            "character_id": "host",
            "reply": "planned response",
        },
        {
            "session_id": "memoria-aftertalk",
            "turns": [
                {"message_id": "after-1", "character_id": "host", "reply": "aftertalk"},
            ],
        },
        {
            "session_id": "memoria-closing",
            "message_id": "closing-1",
            "character_id": "host",
            "reply": "closing",
        },
    )


def _composition(storage, transport: FakeMemoriaTransport):
    return create_v2_composition(
        storage_manager=storage,
        planned_show_runner=MemoriaPlannedShowRunner(storage, transport),
        aftertalk_runner=MemoriaAftertalkRunner(storage, transport),
        closing_runner=MemoriaClosingRunner(storage, transport),
    )


def _create_and_bind(client: TestClient, session_id: str) -> None:
    create_response = client.post(
        "/v2/sessions",
        json={
            "command_id": f"{session_id}-create",
            "session_id": session_id,
            "aftertalk_policy": "auto",
            "metadata": {
                "duration_policy": {
                    "planned_duration_seconds": 3600,
                    "auto_finalize_on_duration": True,
                    "aftertalk_requires_remaining_time": True,
                },
                "hidden_prompt": "must not leak",
            },
        },
    )
    bind_response = client.post(
        f"/v2/sessions/{session_id}/plan",
        json={"command_id": f"{session_id}-bind", "plan": _plan()},
    )
    assert create_response.status_code == 200
    assert bind_response.status_code == 200


def _assert_no_private_payload(value: object) -> None:
    text = repr(value).lower()
    for forbidden in (
        "hidden_prompt",
        "raw_payload",
        "raw_topic_pack",
        "raw_memoriacore_payload",
        "token",
        "must not leak",
    ):
        assert forbidden not in text


def test_tick_endpoint_drives_fake_transport_lifecycle_to_ended():
    storage = InMemoryV2StorageManager()
    transport = _transport()
    app = create_v2_app(_composition(storage, transport), now_provider=lambda: NOW)
    client = TestClient(app)

    _create_and_bind(client, "session-tick")
    planned = client.post("/v2/sessions/session-tick/tick", json={"command_id": "cmd-planned"})
    aftertalk = client.post(
        "/v2/sessions/session-tick/tick",
        json={"command_id": "cmd-aftertalk"},
    )
    manual_close = client.post(
        "/v2/sessions/session-tick/manual-close",
        json={"command_id": "cmd-close", "reason": "operator"},
    )
    ended = client.post("/v2/sessions/session-tick/tick", json={"command_id": "cmd-ended"})
    session = client.get("/v2/sessions/session-tick")
    events = client.get("/v2/sessions/session-tick/events?limit=50")

    assert planned.status_code == 200
    assert planned.json()["phase"] == "planned_show"
    assert aftertalk.status_code == 200
    assert aftertalk.json()["phase"] == "aftertalk"
    assert manual_close.status_code == 200
    assert manual_close.json()["phase"] == "closing"
    assert ended.status_code == 200
    assert ended.json()["phase"] == "ended"
    assert session.json()["phase"] == "ended"
    assert len(transport.requests) == 3
    assert [request.mode for request in transport.requests] == ["chat", "group_chat", "chat"]
    assert storage.get_v2_session("session-tick")["closing_completed"] is True
    assert {event["event_type"] for event in events.json()["events"]} >= {
        "session_created",
        "plan_bound",
        "runtime_action_dispatched",
    }
    _assert_no_private_payload(session.json())
    _assert_no_private_payload(events.json())


def test_tick_replays_same_command_id_without_duplicate_memoria_dispatch():
    storage = InMemoryV2StorageManager()
    transport = _transport()
    app = create_v2_app(_composition(storage, transport), now_provider=lambda: NOW)
    client = TestClient(app)
    _create_and_bind(client, "session-replay")

    first = client.post("/v2/sessions/session-replay/tick", json={"command_id": "cmd-repeat"})
    second = client.post("/v2/sessions/session-replay/tick", json={"command_id": "cmd-repeat"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert len(transport.requests) == 1
    assert len(storage.interactions) == 1
    _assert_no_private_payload(second.json())


def _storage_manager(tmp_path):
    return StorageManager(
        prefs_file=str(tmp_path / "prefs.json"),
        history_file=str(tmp_path / "history.json"),
        persona_snapshot_db_path=str(tmp_path / "persona_snapshots.db"),
        youtube_bridge_v2_db_path=str(tmp_path / "youtubebridge_v2.db"),
    )


class FakeSyncJsonClient:
    def __init__(self, *responses: dict[str, object]) -> None:
        self.responses = list(responses)
        self.calls = []

    def post_json(self, *, url, body, headers, timeout_seconds):
        self.calls.append(
            {
                "url": url,
                "body": dict(body),
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.responses.pop(0)


def test_production_composition_accepts_memoria_sync_http_transport_with_fake_client(tmp_path):
    storage = _storage_manager(tmp_path)
    fake_client = FakeSyncJsonClient(
        {
            "session_id": "memoria-http-planned",
            "message_id": "http-1",
            "character_id": "host",
            "reply": "http planned response",
        }
    )
    transport = MemoriaSyncHttpTransport(
        MemoriaHttpTransportConfig(
            base_url="http://127.0.0.1:8088",
            api_key="secret-token",
            timeout_seconds=4,
        ),
        client=fake_client,
    )
    composition = create_production_v2_composition(storage, memoria_transport=transport)
    client = TestClient(create_v2_app(composition, now_provider=lambda: NOW))
    _create_and_bind(client, "session-http-transport")

    response = client.post(
        "/v2/sessions/session-http-transport/tick",
        json={"command_id": "cmd-http-planned"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert len(fake_client.calls) == 1
    assert fake_client.calls[0]["url"] == "http://127.0.0.1:8088/api/v1/chat/sync"
    assert fake_client.calls[0]["timeout_seconds"] == 4.0
    assert fake_client.calls[0]["headers"]["Authorization"] == "Bearer secret-token"
    assert "secret-token" not in repr(response.json())
    assert (
        storage.get_v2_session("session-http-transport")["metadata"][
            "live_episode_plan_state"
        ]["last_memoria_session_id"]
        == "memoria-http-planned"
    )


def test_production_composition_without_memoria_transport_keeps_noop_runner(tmp_path):
    storage = _storage_manager(tmp_path)
    composition = create_production_v2_composition(storage)
    client = TestClient(create_v2_app(composition, now_provider=lambda: NOW))
    _create_and_bind(client, "session-noop-transport")

    response = client.post(
        "/v2/sessions/session-noop-transport/tick",
        json={"command_id": "cmd-noop-planned"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["events"][0]["payload"]["adapter_summary"] == {
        "mode": "noop",
        "runner": "planned_show",
        "external_adapter": "not_configured",
        "next_action": "run_planned_show",
    }


def test_real_storage_tick_flow_survives_storage_and_composition_rebuild(tmp_path):
    storage = _storage_manager(tmp_path)
    transport = _transport()
    composition = create_production_v2_composition(storage, memoria_transport=transport)
    client = TestClient(create_v2_app(composition, now_provider=lambda: NOW))
    _create_and_bind(client, "session-durable-tick")

    first = client.post(
        "/v2/sessions/session-durable-tick/tick",
        json={"command_id": "cmd-durable-planned"},
    )
    assert first.status_code == 200
    assert len(transport.requests) == 1

    restarted_storage = _storage_manager(tmp_path)
    restarted_composition = create_production_v2_composition(
        restarted_storage,
        memoria_transport=transport,
    )
    restarted_client = TestClient(
        create_v2_app(restarted_composition, now_provider=lambda: NOW)
    )
    replay = restarted_client.post(
        "/v2/sessions/session-durable-tick/tick",
        json={"command_id": "cmd-durable-planned"},
    )
    aftertalk = restarted_client.post(
        "/v2/sessions/session-durable-tick/tick",
        json={"command_id": "cmd-durable-aftertalk"},
    )

    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert aftertalk.status_code == 200
    assert aftertalk.json()["phase"] == "aftertalk"
    assert len(transport.requests) == 2
    assert restarted_storage.get_v2_session("session-durable-tick")["current_phase"] == "aftertalk"
