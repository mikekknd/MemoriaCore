from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tests.youtubebridge_v2.fakes import (
    FakeAftertalkRunner,
    FakeClosingRunner,
    FakePlannedShowRunner,
    InMemoryV2StorageManager,
)
from YouTubeBridgeV2.app import V2AppConfigurationError, create_v2_app
from YouTubeBridgeV2.composition import create_v2_composition
from YouTubeBridgeV2.runtime.application_service import RuntimeCommand, RuntimeCommandType
from YouTubeBridgeV2.runtime.phase import LiveSessionPhase


STARTED_AT = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)


def _assert_no_private_payload(value: object) -> None:
    text = repr(value).lower()
    for forbidden in (
        "hidden_prompt",
        "raw_payload",
        "raw_topic_pack",
        "raw_memoriacore_payload",
        "topic_pack_fact_cards",
        "access_token",
        "token",
        "must not leak",
    ):
        assert forbidden not in text


def _command(command_id: str, session_id: str, now: datetime) -> RuntimeCommand:
    return RuntimeCommand(
        command_id=command_id,
        session_id=session_id,
        command_type=RuntimeCommandType.TICK,
        issued_at=now,
        permission_context={"operator_id": "integration-test"},
        payload={},
    )


def _integration_composition():
    storage = InMemoryV2StorageManager()
    planned_show = FakePlannedShowRunner(storage)
    aftertalk = FakeAftertalkRunner(storage)
    closing = FakeClosingRunner(storage)
    composition = create_v2_composition(
        storage_manager=storage,
        planned_show_runner=planned_show,
        aftertalk_runner=aftertalk,
        closing_runner=closing,
    )
    return composition, storage, planned_show, aftertalk, closing


def test_create_v2_app_requires_explicit_composition():
    with pytest.raises(V2AppConfigurationError, match="composition"):
        create_v2_app(None)


def test_create_v2_app_mounts_routes_and_static_files():
    composition, *_ = _integration_composition()
    client = TestClient(create_v2_app(composition, now_provider=lambda: STARTED_AT))

    create_response = client.post(
        "/v2/sessions",
        json={
            "command_id": "cmd-create",
            "session_id": "session-1",
            "aftertalk_policy": "auto",
            "metadata": {
                "duration_policy": {
                    "planned_duration_seconds": 3600,
                    "auto_finalize_on_duration": True,
                    "aftertalk_requires_remaining_time": True,
                }
            },
        },
    )
    static_response = client.get("/v2/static/operator-console/index.html")

    assert create_response.status_code == 200
    assert create_response.json()["phase"] == "planned_show"
    assert static_response.status_code == 200
    assert 'id="operatorConsoleRoot"' in static_response.text


def test_fake_backed_vertical_slice_reaches_aftertalk_closing_and_ended():
    composition, storage, planned_show, aftertalk, closing = _integration_composition()
    client = TestClient(create_v2_app(composition, now_provider=lambda: STARTED_AT))

    create_response = client.post(
        "/v2/sessions",
        json={
            "command_id": "cmd-create",
            "session_id": "session-1",
            "plan_id": "plan-1",
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
        "/v2/sessions/session-1/plan",
        json={
            "command_id": "cmd-bind",
            "plan": {
                "plan_id": "plan-1",
                "title": "V2 episode",
                "raw_topic_pack": "must not leak",
            },
        },
    )

    first_tick = composition.runtime_service.tick_session(
        _command("cmd-tick-planned", "session-1", STARTED_AT + timedelta(seconds=10)),
        STARTED_AT + timedelta(seconds=10),
    )
    second_tick = composition.runtime_service.tick_session(
        _command("cmd-tick-aftertalk", "session-1", STARTED_AT + timedelta(seconds=20)),
        STARTED_AT + timedelta(seconds=20),
    )
    phase_response = client.get("/v2/sessions/session-1/phase")
    close_response = client.post(
        "/v2/sessions/session-1/manual-close",
        json={"command_id": "cmd-close", "reason": "operator"},
    )
    ended_tick = composition.runtime_service.tick_session(
        _command("cmd-tick-ended", "session-1", STARTED_AT + timedelta(seconds=40)),
        STARTED_AT + timedelta(seconds=40),
    )
    session_response = client.get("/v2/sessions/session-1")
    events_response = client.get("/v2/sessions/session-1/events?limit=50")

    with client.stream("GET", "/v2/sessions/session-1/operator-stream") as operator_stream:
        operator_stream.read()
        operator_text = operator_stream.text
    with client.stream("GET", "/v2/sessions/session-1/display-stream") as display_stream:
        display_stream.read()
        display_text = display_stream.text

    assert create_response.status_code == 200
    assert bind_response.status_code == 200
    assert first_tick.phase == LiveSessionPhase.PLANNED_SHOW
    assert second_tick.phase == LiveSessionPhase.AFTERTALK
    assert phase_response.json()["phase"] == "aftertalk"
    assert close_response.json()["phase"] == "closing"
    assert ended_tick.phase == LiveSessionPhase.ENDED
    assert session_response.json()["phase"] == "ended"
    assert len(planned_show.calls) == 1
    assert len(aftertalk.calls) == 1
    assert len(closing.calls) == 1
    assert storage.get_v2_session("session-1")["closing_completed"] is True
    assert {event["event_type"] for event in events_response.json()["events"]} >= {
        "session_created",
        "plan_bound",
        "runtime_action_dispatched",
    }
    assert "operator_status" in operator_text
    assert "runtime_action_dispatched" in display_text
    _assert_no_private_payload(create_response.json())
    _assert_no_private_payload(bind_response.json())
    _assert_no_private_payload(phase_response.json())
    _assert_no_private_payload(close_response.json())
    _assert_no_private_payload(session_response.json())
    _assert_no_private_payload(events_response.json())
    _assert_no_private_payload(operator_text)
    _assert_no_private_payload(display_text)


def test_youtube_event_api_ingestion_persists_normalized_public_event():
    composition, _storage, planned_show, *_ = _integration_composition()
    client = TestClient(create_v2_app(composition, now_provider=lambda: STARTED_AT))
    client.post(
        "/v2/sessions",
        json={
            "command_id": "cmd-create-youtube",
            "session_id": "session-youtube-api",
            "aftertalk_policy": "auto",
        },
    )

    response = client.post(
        "/v2/sessions/session-youtube-api/youtube-events",
        json={
            "command_id": "cmd-youtube-event",
            "youtube_event": {
                "id": "yt-evt-api-1",
                "snippet": {
                    "type": "textMessageEvent",
                    "publishedAt": "2026-05-12T08:10:00Z",
                    "displayMessage": "Hello from API",
                    "textMessageDetails": {"messageText": "Hello from API"},
                },
                "authorDetails": {
                    "displayName": "Mika",
                    "channelId": "channel-1",
                    "isChatModerator": True,
                },
                "raw_payload": {"access_token": "must not leak"},
            },
        },
    )
    events_response = client.get("/v2/sessions/session-youtube-api/events?limit=20")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    events = events_response.json()["events"]
    assert any(
        event["event_id"] == "yt-evt-api-1"
        and event["event_type"] == "youtube_text_message"
        and event["public_payload"]["public_payload"]["message_text"] == "Hello from API"
        for event in events
    )
    assert len(planned_show.calls) == 1
    _assert_no_private_payload(response.json())
    _assert_no_private_payload(events_response.json())


def test_repeated_command_id_does_not_duplicate_transition_or_runner_dispatch():
    composition, storage, planned_show, *_ = _integration_composition()
    client = TestClient(create_v2_app(composition, now_provider=lambda: STARTED_AT))
    client.post(
        "/v2/sessions",
        json={
            "command_id": "cmd-create",
            "session_id": "session-idempotent",
            "aftertalk_policy": "auto",
            "metadata": {
                "duration_policy": {
                    "planned_duration_seconds": 3600,
                    "auto_finalize_on_duration": True,
                }
            },
        },
    )
    command = _command(
        "cmd-repeat",
        "session-idempotent",
        STARTED_AT + timedelta(seconds=10),
    )

    first_result = composition.runtime_service.tick_session(
        command,
        STARTED_AT + timedelta(seconds=10),
    )
    repeated_result = composition.runtime_service.tick_session(
        command,
        STARTED_AT + timedelta(seconds=10),
    )

    assert first_result == repeated_result
    assert len(storage.transitions) == 1
    assert len(planned_show.calls) == 1


def test_duration_boundary_closes_without_aftertalk():
    composition, _storage, _planned_show, aftertalk, closing = _integration_composition()
    client = TestClient(create_v2_app(composition, now_provider=lambda: STARTED_AT))
    client.post(
        "/v2/sessions",
        json={
            "command_id": "cmd-create",
            "session_id": "session-duration",
            "aftertalk_policy": "auto",
            "metadata": {
                "duration_policy": {
                    "planned_duration_seconds": 30,
                    "auto_finalize_on_duration": True,
                    "aftertalk_requires_remaining_time": True,
                }
            },
        },
    )

    result = composition.runtime_service.tick_session(
        _command(
            "cmd-duration-reached",
            "session-duration",
            STARTED_AT + timedelta(seconds=35),
        ),
        STARTED_AT + timedelta(seconds=35),
    )

    assert result.phase == LiveSessionPhase.CLOSING
    assert not aftertalk.calls
    assert len(closing.calls) == 1
