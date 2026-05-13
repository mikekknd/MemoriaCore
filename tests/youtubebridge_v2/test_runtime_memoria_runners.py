from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests.youtubebridge_v2.fakes import InMemoryV2StorageManager
from YouTubeBridgeV2.adapters.memoria_http import MemoriaHttpTransportError
from YouTubeBridgeV2.display.events import normalize_display_event
from YouTubeBridgeV2.runtime.application_service import RuntimeCommand, RuntimeCommandType
from YouTubeBridgeV2.runtime.memoria_runners import (
    MemoriaAftertalkRunner,
    MemoriaClosingRunner,
    MemoriaPlannedShowRunner,
)
from YouTubeBridgeV2.runtime.phase import (
    LiveSessionPhase,
    PhaseTransition,
    PhaseTransitionReason,
)
from YouTubeBridgeV2.storage.runtime_store import RuntimeStoragePort


NOW = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)


class FakeMemoriaTransport:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class AuthFailure(Exception):
    status_code = 401


def _valid_plan(turn_count: int = 1) -> dict[str, object]:
    turns = [
        {
            "id": f"turn-{index + 1}",
            "purpose": f"Planned purpose {index + 1}",
            "topic_cue": f"Topic cue {index + 1}",
            "speaker_policy": {"type": "fixed", "speaker_ids": ["host", "cohost"]},
            "audience_insertion": {"enabled": False, "allow_super_chats": False},
            "raw_topic_pack": "must not leak",
        }
        for index in range(turn_count)
    ]
    return {
        "plan_id": "plan-runner",
        "title": "Runner plan",
        "turns": turns,
        "topic_pack_fact_cards": "must not leak",
    }


def _create_bound_session(
    storage: InMemoryV2StorageManager,
    *,
    session_id: str = "session-runner",
    turn_count: int = 1,
) -> RuntimeStoragePort:
    port = RuntimeStoragePort(storage)
    port.create_session(
        RuntimeCommand(
            command_id=f"{session_id}-create",
            session_id=session_id,
            command_type=RuntimeCommandType.CREATE_SESSION,
            issued_at=NOW,
            payload={
                "aftertalk_policy": "auto",
                "metadata": {
                    "duration_policy": {
                        "planned_duration_seconds": 3600,
                        "auto_finalize_on_duration": True,
                    }
                },
            },
        ),
        NOW,
    )
    port.bind_plan(
        RuntimeCommand(
            command_id=f"{session_id}-bind",
            session_id=session_id,
            command_type=RuntimeCommandType.BIND_PLAN,
            issued_at=NOW,
            payload={"plan": _valid_plan(turn_count=turn_count)},
        ),
        NOW,
    )
    return port


def _command(command_id: str, session_id: str = "session-runner") -> RuntimeCommand:
    return RuntimeCommand(
        command_id=command_id,
        session_id=session_id,
        command_type=RuntimeCommandType.TICK,
        issued_at=NOW,
        payload={},
    )


def _transition(
    phase: LiveSessionPhase,
    *,
    action: str,
    reason: PhaseTransitionReason = PhaseTransitionReason.NO_CHANGE,
) -> PhaseTransition:
    return PhaseTransition(
        current_phase=phase,
        next_phase=phase,
        changed=False,
        reason=reason,
        metadata={"remaining_time_seconds": 1200, "duration_reached": False},
        next_action=action,
    )


def _state_from_session(session: dict[str, object]) -> dict[str, object]:
    metadata = session.get("metadata", {})
    if isinstance(metadata, dict) and isinstance(metadata.get("live_episode_plan_state"), dict):
        return metadata["live_episode_plan_state"]
    return session["live_episode_plan_state"]


def _assert_no_private_payload(value: object) -> None:
    text = repr(value).lower()
    for forbidden in (
        "hidden_prompt",
        "raw_payload",
        "raw_topic_pack",
        "topic_pack_fact_cards",
        "raw_memoriacore_payload",
        "token",
        "secret",
        "must not leak",
    ):
        assert forbidden not in text


def _append_super_chat_event(storage: InMemoryV2StorageManager) -> None:
    storage.append_v2_live_event(
        "session-runner",
        {
            "event_id": "sc-1",
            "event_type": "youtube_super_chat",
            "public_metadata": {
                "public_payload": {
                    "event_id": "sc-1",
                    "event_type": "super_chat",
                    "author_display_name": "Rin",
                    "message_text": "Great stream",
                    "published_at": "2026-05-12T08:05:00Z",
                    "super_chat": {
                        "super_chat_id": "sc-1",
                        "amount_micros": 150000000,
                        "currency": "TWD",
                        "amount_display_string": "NT$150",
                        "public_message": "Great stream",
                        "acknowledgement_status": "pending",
                    },
                },
                "display_event": {
                    "event_id": "sc-1",
                    "event_type": "super_chat",
                    "author_display_name": "Rin",
                    "message_text": "Great stream",
                    "raw_youtube_payload": {"access_token": "must not leak"},
                },
            },
        },
    )


def _append_ignored_super_chat_events(storage: InMemoryV2StorageManager) -> None:
    storage.append_v2_live_event(
        "session-runner",
        {
            "event_id": "sc-acknowledged",
            "event_type": "youtube_super_chat",
            "public_metadata": {
                "public_payload": {
                    "author_display_name": "Mika",
                    "super_chat": {
                        "super_chat_id": "sc-acknowledged",
                        "amount_display_string": "NT$75",
                        "public_message": "Thanks",
                        "acknowledgement_status": "acknowledged",
                    },
                },
            },
        },
    )
    storage.append_v2_live_event(
        "session-runner",
        {
            "event_id": "sc-malformed",
            "event_type": "youtube_super_chat",
            "public_metadata": {
                "public_payload": {
                    "author_display_name": "Kai",
                    "message_text": "Missing metadata",
                },
            },
        },
    )


def test_planned_show_runner_sends_next_turn_and_advances_plan_state():
    storage = InMemoryV2StorageManager()
    port = _create_bound_session(storage)
    transport = FakeMemoriaTransport(
        {
            "session_id": "memoria-1",
            "message_id": "msg-1",
            "character_id": "host",
            "reply": "Planned response",
            "raw_payload": {"token": "must not leak"},
        }
    )
    runner = MemoriaPlannedShowRunner(storage, transport)

    result = runner.run(
        command=_command("cmd-planned"),
        snapshot=port.read_snapshot("session-runner"),
        transition=_transition(LiveSessionPhase.PLANNED_SHOW, action="run_planned_show"),
        now=NOW,
    )

    session = storage.get_v2_session("session-runner")
    state = _state_from_session(session)
    assert result.status == "ok"
    assert result.summary["turn_id"] == "turn-1"
    assert result.summary["plan_completed"] is True
    assert len(transport.requests) == 1
    assert transport.requests[0].public_summary["turn_id"] == "turn-1"
    assert state["cursor"] == 1
    assert state["completed_turn_ids"] == ["turn-1"]
    assert state["last_memoria_session_id"] == "memoria-1"
    assert session["plan_completed"] is True
    assert len(storage.interactions) == 1
    assert storage.interactions[0]["phase"] == "planned_show"
    assert storage.interactions[0]["public_content_summary"]["content"] == "Planned response"
    _assert_no_private_payload(result)
    _assert_no_private_payload(storage.interactions)


def test_planned_show_runner_appends_presentation_display_event():
    storage = InMemoryV2StorageManager()
    port = _create_bound_session(storage)
    transport = FakeMemoriaTransport(
        {
            "session_id": "memoria-1",
            "message_id": "msg-1",
            "character_id": "host",
            "character_name": "Luna",
            "role_label": "Host",
            "voice_id": "voice-luna",
            "reply": "Planned response",
            "presentation": {
                "voice_state": "speaking",
                "visual_state": "focus",
                "subtitle": "Planned response",
                "raw_payload": {"token": "must not leak"},
            },
            "raw_payload": {"token": "must not leak"},
        }
    )
    runner = MemoriaPlannedShowRunner(storage, transport)

    result = runner.run(
        command=_command("cmd-planned-display"),
        snapshot=port.read_snapshot("session-runner"),
        transition=_transition(LiveSessionPhase.PLANNED_SHOW, action="run_planned_show"),
        now=NOW,
    )

    assert result.status == "ok"
    assert len(storage.live_events) == 1
    event = storage.live_events[0]
    assert event["event_type"] == "presentation_character_response"
    assert event["created_at"] == NOW
    assert event["public_metadata"]["interaction_id"].endswith(":msg-1")
    display_event = normalize_display_event(event)
    assert display_event["event_type"] == "character_response"
    assert display_event["source_event_type"] == "presentation_character_response"
    assert display_event["public_payload"] == {
        "character_name": "Luna",
        "role_label": "Host",
        "response_text": "Planned response",
        "phase": "planned_show",
        "presentation": {
            "voice_state": "speaking",
            "visual_state": "focus",
            "phase": "planned_show",
            "role_label": "Host",
            "subtitle": "Planned response",
            "public_payload": {
                "correlation_id": "runtime-cmd-planned-display",
                "request_id": "cmd-planned-display",
            },
        },
    }
    _assert_no_private_payload(storage.live_events)


def test_planned_show_runner_enqueues_tts_when_policy_enabled():
    storage = InMemoryV2StorageManager()
    port = _create_bound_session(storage)
    storage.update_v2_session(
        "session-runner",
        {
            "metadata": {
                **storage.get_v2_session("session-runner").get("metadata", {}),
                "tts_policy": {
                    "enabled": True,
                    "provider": "local",
                    "default_voice_id": "fallback-voice",
                },
            }
        },
    )
    transport = FakeMemoriaTransport(
        {
            "session_id": "memoria-1",
            "message_id": "msg-tts",
            "character_id": "host",
            "character_name": "Luna",
            "role_label": "Host",
            "voice_id": "voice-luna",
            "reply": "Speak this line",
            "presentation": {"voice_state": "speaking"},
        }
    )
    runner = MemoriaPlannedShowRunner(storage, transport)

    result = runner.run(
        command=_command("cmd-planned-tts"),
        snapshot=port.read_snapshot("session-runner"),
        transition=_transition(LiveSessionPhase.PLANNED_SHOW, action="run_planned_show"),
        now=NOW,
    )

    assert result.status == "ok"
    assert len(storage.tts_deliveries) == 1
    delivery = storage.tts_deliveries[0]
    assert delivery["delivery_id"].startswith("tts-presentation:")
    assert delivery["text"] == "Speak this line"
    assert delivery["voice_id"] == "voice-luna"
    assert delivery["provider"] == "local"
    assert delivery["queue_position"] == 1
    assert delivery["status"] == "pending"
    assert delivery["metadata"]["interaction_id"].endswith(":msg-tts")
    _assert_no_private_payload(storage.tts_deliveries)


def test_aftertalk_runner_builds_group_chat_request_and_appends_interactions():
    storage = InMemoryV2StorageManager()
    port = _create_bound_session(storage)
    storage.update_v2_session("session-runner", {"plan_completed": True})
    transport = FakeMemoriaTransport(
        {
            "session_id": "memoria-2",
            "turns": [
                {"message_id": "a1", "character_id": "host", "reply": "Aftertalk 1"},
                {"message_id": "a2", "character_id": "cohost", "reply": "Aftertalk 2"},
            ],
            "raw_memoriacore_payload": {"token": "must not leak"},
        }
    )
    runner = MemoriaAftertalkRunner(storage, transport)

    result = runner.run(
        command=_command("cmd-aftertalk"),
        snapshot=port.read_snapshot("session-runner"),
        transition=_transition(LiveSessionPhase.AFTERTALK, action="continue_aftertalk"),
        now=NOW,
    )

    assert result.status == "ok"
    assert result.summary["message_count"] == 2
    assert len(transport.requests) == 1
    assert transport.requests[0].mode == "group_chat"
    assert len(storage.interactions) == 2
    assert {item["phase"] for item in storage.interactions} == {"aftertalk"}
    _assert_no_private_payload(result)
    _assert_no_private_payload(storage.interactions)


def test_aftertalk_runner_appends_one_presentation_display_event_per_message():
    storage = InMemoryV2StorageManager()
    port = _create_bound_session(storage)
    storage.update_v2_session("session-runner", {"plan_completed": True})
    transport = FakeMemoriaTransport(
        {
            "session_id": "memoria-2",
            "turns": [
                {
                    "message_id": "a1",
                    "character_id": "host",
                    "character_name": "Luna",
                    "role_label": "Host",
                    "reply": "Aftertalk 1",
                    "presentation": {"voice_state": "speaking"},
                },
                {
                    "message_id": "a2",
                    "character_id": "cohost",
                    "character_name": "Mika",
                    "role_label": "Cohost",
                    "reply": "Aftertalk 2",
                    "presentation": {"visual_state": "react"},
                },
            ],
            "raw_memoriacore_payload": {"token": "must not leak"},
        }
    )
    runner = MemoriaAftertalkRunner(storage, transport)

    result = runner.run(
        command=_command("cmd-aftertalk-display"),
        snapshot=port.read_snapshot("session-runner"),
        transition=_transition(LiveSessionPhase.AFTERTALK, action="continue_aftertalk"),
        now=NOW,
    )

    assert result.status == "ok"
    assert len(storage.interactions) == 2
    assert len(storage.live_events) == 2
    display_events = [normalize_display_event(event) for event in storage.live_events]
    assert [event["public_payload"]["response_text"] for event in display_events] == [
        "Aftertalk 1",
        "Aftertalk 2",
    ]
    assert [event["public_payload"]["phase"] for event in display_events] == [
        "aftertalk",
        "aftertalk",
    ]
    assert display_events[0]["public_payload"]["presentation"]["voice_state"] == "speaking"
    assert display_events[1]["public_payload"]["presentation"]["visual_state"] == "react"
    _assert_no_private_payload(storage.live_events)


def test_aftertalk_runner_does_not_enqueue_tts_when_policy_disabled():
    storage = InMemoryV2StorageManager()
    port = _create_bound_session(storage)
    storage.update_v2_session(
        "session-runner",
        {
            "plan_completed": True,
            "metadata": {
                **storage.get_v2_session("session-runner").get("metadata", {}),
                "tts_policy": {"enabled": False},
            },
        },
    )
    transport = FakeMemoriaTransport(
        {
            "session_id": "memoria-2",
            "turns": [
                {
                    "message_id": "a1",
                    "character_id": "host",
                    "reply": "No TTS",
                }
            ],
        }
    )
    runner = MemoriaAftertalkRunner(storage, transport)

    result = runner.run(
        command=_command("cmd-aftertalk-no-tts"),
        snapshot=port.read_snapshot("session-runner"),
        transition=_transition(LiveSessionPhase.AFTERTALK, action="continue_aftertalk"),
        now=NOW,
    )

    assert result.status == "ok"
    assert storage.tts_deliveries == []


def test_closing_runner_builds_final_message_and_marks_closing_completed():
    storage = InMemoryV2StorageManager()
    port = _create_bound_session(storage)
    storage.update_v2_session("session-runner", {"current_phase": "closing"})
    transport = FakeMemoriaTransport(
        {
            "session_id": "memoria-3",
            "message_id": "close-1",
            "character_id": "host",
            "reply": "Closing message",
            "raw_memoriacore_payload": {"token": "must not leak"},
        }
    )
    runner = MemoriaClosingRunner(storage, transport)

    result = runner.run(
        command=_command("cmd-closing"),
        snapshot=port.read_snapshot("session-runner"),
        transition=_transition(
            LiveSessionPhase.CLOSING,
            action="start_closing",
            reason=PhaseTransitionReason.MANUAL_CLOSE,
        ),
        now=NOW,
    )

    session = storage.get_v2_session("session-runner")
    assert result.status == "ok"
    assert result.summary["closing_completion_status"] == "complete"
    assert len(transport.requests) == 1
    assert transport.requests[0].mode == "chat"
    assert storage.finalizations[0]["closing_completion_status"] == "complete"
    assert session["closing_completed"] is True
    _assert_no_private_payload(result)
    _assert_no_private_payload(storage.finalizations)


def test_closing_runner_loads_pending_super_chats_from_youtube_events():
    storage = InMemoryV2StorageManager()
    port = _create_bound_session(storage)
    storage.update_v2_session("session-runner", {"current_phase": "closing"})
    _append_super_chat_event(storage)
    transport = FakeMemoriaTransport(
        {
            "session_id": "memoria-3",
            "message_id": "close-1",
            "character_id": "host",
            "reply": "Closing message",
        }
    )
    runner = MemoriaClosingRunner(storage, transport)

    result = runner.run(
        command=_command("cmd-closing-super-chat"),
        snapshot=port.read_snapshot("session-runner"),
        transition=_transition(
            LiveSessionPhase.CLOSING,
            action="start_closing",
            reason=PhaseTransitionReason.MANUAL_CLOSE,
        ),
        now=NOW,
    )

    closing_context = transport.requests[0].body["external_context"]["closing"]
    assert result.status == "ok"
    assert closing_context["super_chat_actions"] == [
        {
            "super_chat_id": "sc-1",
            "action_type": "acknowledge",
            "status": "pending",
            "author_display_name": "Rin",
            "amount_display_string": "NT$150",
            "public_message": "Great stream",
            "error_summary": {},
        }
    ]
    _assert_no_private_payload(transport.requests[0].body)
    _assert_no_private_payload(result)


def test_closing_runner_ignores_acknowledged_or_malformed_super_chat_events():
    storage = InMemoryV2StorageManager()
    port = _create_bound_session(storage)
    storage.update_v2_session("session-runner", {"current_phase": "closing"})
    _append_ignored_super_chat_events(storage)
    transport = FakeMemoriaTransport(
        {
            "session_id": "memoria-3",
            "message_id": "close-1",
            "character_id": "host",
            "reply": "Closing message",
        }
    )
    runner = MemoriaClosingRunner(storage, transport)

    result = runner.run(
        command=_command("cmd-closing-super-chat-ignored"),
        snapshot=port.read_snapshot("session-runner"),
        transition=_transition(
            LiveSessionPhase.CLOSING,
            action="start_closing",
            reason=PhaseTransitionReason.MANUAL_CLOSE,
        ),
        now=NOW,
    )

    closing_context = transport.requests[0].body["external_context"]["closing"]
    assert result.status == "ok"
    assert closing_context["super_chat_actions"] == []
    _assert_no_private_payload(transport.requests[0].body)
    _assert_no_private_payload(result)


def test_closing_runner_terminal_adapter_error_finalizes_with_system_summary():
    storage = InMemoryV2StorageManager()
    port = _create_bound_session(storage)
    storage.update_v2_session("session-runner", {"current_phase": "closing"})
    runner = MemoriaClosingRunner(storage, FakeMemoriaTransport(AuthFailure("denied")))

    result = runner.run(
        command=_command("cmd-closing-auth"),
        snapshot=port.read_snapshot("session-runner"),
        transition=_transition(
            LiveSessionPhase.CLOSING,
            action="start_closing",
            reason=PhaseTransitionReason.MANUAL_CLOSE,
        ),
        now=NOW,
    )

    session = storage.get_v2_session("session-runner")
    assert result.status == "ok"
    assert result.summary["closing_completion_status"] == "complete"
    assert result.summary["finalization"] == "system_summary"
    assert len(storage.finalizations) == 1
    assert storage.finalizations[0]["closing_completion_status"] == "complete"
    assert storage.finalizations[0]["error_summary"] == {
        "error_type": "auth_failure",
        "retryable": False,
        "fallback": "system_summary",
    }
    assert session["closing_completed"] is True
    _assert_no_private_payload(result)
    _assert_no_private_payload(storage.finalizations)


def test_closing_runner_retryable_adapter_error_does_not_finalize():
    storage = InMemoryV2StorageManager()
    port = _create_bound_session(storage)
    storage.update_v2_session("session-runner", {"current_phase": "closing"})
    runner = MemoriaClosingRunner(storage, FakeMemoriaTransport(TimeoutError("slow")))

    result = runner.run(
        command=_command("cmd-closing-timeout"),
        snapshot=port.read_snapshot("session-runner"),
        transition=_transition(
            LiveSessionPhase.CLOSING,
            action="start_closing",
            reason=PhaseTransitionReason.MANUAL_CLOSE,
        ),
        now=NOW,
    )

    session = storage.get_v2_session("session-runner")
    assert result.status == "error"
    assert result.retryable is True
    assert result.summary == {"error_type": "timeout", "retryable": True}
    assert storage.finalizations == []
    assert session["closing_completed"] is False
    _assert_no_private_payload(result)


def test_memoria_transport_timeout_returns_retryable_adapter_summary():
    storage = InMemoryV2StorageManager()
    port = _create_bound_session(storage)
    runner = MemoriaPlannedShowRunner(storage, FakeMemoriaTransport(TimeoutError("slow")))

    result = runner.run(
        command=_command("cmd-timeout"),
        snapshot=port.read_snapshot("session-runner"),
        transition=_transition(LiveSessionPhase.PLANNED_SHOW, action="run_planned_show"),
        now=NOW,
    )

    assert result.status == "error"
    assert result.retryable is True
    assert result.summary == {"error_type": "timeout", "retryable": True}
    assert storage.interactions == []
    _assert_no_private_payload(result)


def test_runner_error_summary_redacts_http_transport_secret_payload():
    storage = InMemoryV2StorageManager()
    port = _create_bound_session(storage)
    runner = MemoriaPlannedShowRunner(
        storage,
        FakeMemoriaTransport(
            MemoriaHttpTransportError(
                error_type="transport_failure",
                retryable=True,
                status_code=503,
                public_summary={
                    "error_type": "transport_failure",
                    "retryable": True,
                    "status_code": 503,
                    "url": "http://127.0.0.1:8088/api/v1/chat/sync?token=secret",
                    "headers": {"Authorization": "Bearer secret-token"},
                    "raw_payload": {"token": "secret-token"},
                },
            )
        ),
    )

    result = runner.run(
        command=_command("cmd-http-secret"),
        snapshot=port.read_snapshot("session-runner"),
        transition=_transition(LiveSessionPhase.PLANNED_SHOW, action="run_planned_show"),
        now=NOW,
    )

    assert result.status == "error"
    assert result.retryable is True
    assert result.summary == {
        "error_type": "transport_failure",
        "retryable": True,
        "status_code": 503,
    }
    _assert_no_private_payload(result)


def test_memoria_transport_keyboard_interrupt_propagates():
    storage = InMemoryV2StorageManager()
    port = _create_bound_session(storage)
    runner = MemoriaPlannedShowRunner(storage, FakeMemoriaTransport(KeyboardInterrupt()))

    with pytest.raises(KeyboardInterrupt):
        runner.run(
            command=_command("cmd-interrupt"),
            snapshot=port.read_snapshot("session-runner"),
            transition=_transition(LiveSessionPhase.PLANNED_SHOW, action="run_planned_show"),
            now=NOW,
        )
