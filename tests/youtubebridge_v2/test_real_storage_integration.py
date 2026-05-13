from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from core.storage_manager import StorageManager
from tests.youtubebridge_v2.fakes import (
    FakeAftertalkRunner,
    FakeClosingRunner,
    FakePlannedShowRunner,
)
from YouTubeBridgeV2.app import create_v2_app
from YouTubeBridgeV2.composition import create_v2_composition
from YouTubeBridgeV2.query_service import V2QueryService
from YouTubeBridgeV2.runtime.application_service import (
    RuntimeCommand,
    RuntimeCommandType,
    RuntimeServiceResult,
)
from YouTubeBridgeV2.runtime.automation import (
    dispatch_scheduler_cycle,
    dispatch_scheduler_recovery_cycle,
)
from YouTubeBridgeV2.runtime.memoria_runners import MemoriaPlannedShowRunner
from YouTubeBridgeV2.runtime.phase import LiveSessionPhase


STARTED_AT = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)


class FakeMemoriaTransport:
    def __init__(self, *responses: dict[str, object]) -> None:
        self.responses = list(responses)
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


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


def _storage_manager(tmp_path):
    return StorageManager(
        prefs_file=str(tmp_path / "prefs.json"),
        history_file=str(tmp_path / "history.json"),
        persona_snapshot_db_path=str(tmp_path / "persona_snapshots.db"),
        youtube_bridge_v2_db_path=str(tmp_path / "youtubebridge_v2.db"),
    )


def _composition(storage_manager):
    planned_show = FakePlannedShowRunner(storage_manager)
    aftertalk = FakeAftertalkRunner(storage_manager)
    closing = FakeClosingRunner(storage_manager)
    composition = create_v2_composition(
        storage_manager=storage_manager,
        planned_show_runner=planned_show,
        aftertalk_runner=aftertalk,
        closing_runner=closing,
    )
    return composition, planned_show, aftertalk, closing


def _command(command_id: str, session_id: str, now: datetime) -> RuntimeCommand:
    return RuntimeCommand(
        command_id=command_id,
        session_id=session_id,
        command_type=RuntimeCommandType.TICK,
        issued_at=now,
        permission_context={"operator_id": "real-storage-test"},
        payload={},
    )


def _create_session(client: TestClient, session_id: str) -> None:
    response = client.post(
        "/v2/sessions",
        json={
            "command_id": f"{session_id}-create",
            "session_id": session_id,
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
    assert response.status_code == 200


def _bind_plan(client: TestClient, session_id: str) -> None:
    response = client.post(
        f"/v2/sessions/{session_id}/plan",
        json={
            "command_id": f"{session_id}-bind",
            "plan": {
                "plan_id": "plan-1",
                "title": "V2 durable episode",
                "raw_topic_pack": "must not leak",
            },
        },
    )
    assert response.status_code == 200


def test_real_storage_tts_queue_ack_and_timeout_flow(tmp_path):
    storage = _storage_manager(tmp_path)
    transport = FakeMemoriaTransport(
        {
            "session_id": "memoria-tts",
            "message_id": "planned-tts",
            "character_id": "host",
            "character_name": "Luna",
            "role_label": "Host",
            "voice_id": "voice-luna",
            "reply": "TTS line",
            "presentation": {"voice_state": "speaking"},
        }
    )
    composition = create_v2_composition(
        storage_manager=storage,
        planned_show_runner=MemoriaPlannedShowRunner(storage, transport),
    )
    client = TestClient(create_v2_app(composition, now_provider=lambda: STARTED_AT))

    create_response = client.post(
        "/v2/sessions",
        json={
            "command_id": "session-tts-create",
            "session_id": "session-tts",
            "aftertalk_policy": "auto",
            "metadata": {
                "duration_policy": {
                    "planned_duration_seconds": 3600,
                    "auto_finalize_on_duration": True,
                    "aftertalk_requires_remaining_time": True,
                },
                "tts_policy": {
                    "enabled": True,
                    "provider": "local",
                    "default_voice_id": "fallback-voice",
                },
            },
        },
    )
    bind_response = client.post(
        "/v2/sessions/session-tts/plan",
        json={
            "command_id": "session-tts-bind",
            "plan": {
                "plan_id": "plan-tts",
                "title": "TTS plan",
                "turns": [
                    {
                        "id": "opening",
                        "purpose": "Open with TTS.",
                        "topic_cue": "TTS queue.",
                        "speaker_policy": {"type": "fixed", "speaker_ids": ["host"]},
                        "audience_insertion": {"enabled": False, "allow_super_chats": False},
                    }
                ],
            },
        },
    )
    tick_response = client.post(
        "/v2/sessions/session-tts/tick",
        json={"command_id": "session-tts-tick"},
    )
    queue_response = client.get("/v2/sessions/session-tts/tts-queue")

    assert create_response.status_code == 200
    assert bind_response.status_code == 200
    assert tick_response.status_code == 200
    assert queue_response.status_code == 200
    queued = queue_response.json()["tts_queue"]
    assert len(queued) == 1
    assert queued[0]["text"] == "TTS line"
    assert queued[0]["status"] == "pending"
    delivery_id = queued[0]["delivery_id"]

    ack_response = client.post(
        f"/v2/sessions/session-tts/tts-deliveries/{delivery_id}/ack",
        json={"command_id": "ack-tts"},
    )
    timeout_response = client.post(
        f"/v2/sessions/session-tts/tts-deliveries/{delivery_id}/timeout",
        json={"command_id": "timeout-tts", "timeout_seconds": 30},
    )
    delivered_queue = client.get("/v2/sessions/session-tts/tts-queue?status=delivered")

    assert ack_response.status_code == 200
    assert ack_response.json()["status"] == "delivered"
    assert ack_response.json()["phase_transition_requested"] is False
    assert timeout_response.status_code == 200
    assert timeout_response.json()["timeout_ignored"] is True
    assert timeout_response.json()["phase_transition_requested"] is False
    assert delivered_queue.json()["tts_queue"][0]["delivery_id"] == delivery_id
    _assert_no_private_payload(
        (
            queue_response.json(),
            ack_response.json(),
            timeout_response.json(),
            delivered_queue.json(),
        )
    )


def test_real_storage_vertical_slice_reaches_ended_and_persists_events(tmp_path):
    storage = _storage_manager(tmp_path)
    composition, planned_show, aftertalk, closing = _composition(storage)
    client = TestClient(create_v2_app(composition, now_provider=lambda: STARTED_AT))

    _create_session(client, "session-real")
    _bind_plan(client, "session-real")
    first_tick = composition.runtime_service.tick_session(
        _command("cmd-tick-planned", "session-real", STARTED_AT + timedelta(seconds=10)),
        STARTED_AT + timedelta(seconds=10),
    )
    second_tick = composition.runtime_service.tick_session(
        _command("cmd-tick-aftertalk", "session-real", STARTED_AT + timedelta(seconds=20)),
        STARTED_AT + timedelta(seconds=20),
    )
    phase_response = client.get("/v2/sessions/session-real/phase")
    close_response = client.post(
        "/v2/sessions/session-real/manual-close",
        json={"command_id": "cmd-close", "reason": "operator"},
    )
    ended_tick = composition.runtime_service.tick_session(
        _command("cmd-tick-ended", "session-real", STARTED_AT + timedelta(seconds=40)),
        STARTED_AT + timedelta(seconds=40),
    )
    session_response = client.get("/v2/sessions/session-real")
    events_response = client.get("/v2/sessions/session-real/events?limit=50")

    with client.stream("GET", "/v2/sessions/session-real/operator-stream") as operator_stream:
        operator_stream.read()
        operator_text = operator_stream.text
    with client.stream("GET", "/v2/sessions/session-real/display-stream") as display_stream:
        display_stream.read()
        display_text = display_stream.text

    assert first_tick.phase == LiveSessionPhase.PLANNED_SHOW
    assert second_tick.phase == LiveSessionPhase.AFTERTALK
    assert phase_response.json()["phase"] == "aftertalk"
    assert close_response.json()["phase"] == "closing"
    assert ended_tick.phase == LiveSessionPhase.ENDED
    assert session_response.json()["phase"] == "ended"
    assert len(planned_show.calls) == 1
    assert len(aftertalk.calls) == 1
    assert len(closing.calls) == 1
    assert storage.get_v2_session("session-real")["closing_completed"] is True
    assert {event["event_type"] for event in events_response.json()["events"]} >= {
        "session_created",
        "plan_bound",
        "runtime_action_dispatched",
    }
    assert "operator_status" in operator_text
    assert "runtime_action_dispatched" in display_text
    _assert_no_private_payload(session_response.json())
    _assert_no_private_payload(events_response.json())
    _assert_no_private_payload(operator_text)
    _assert_no_private_payload(display_text)


def test_bind_plan_persists_sanitized_live_episode_plan_state(tmp_path):
    storage = _storage_manager(tmp_path)
    composition, _planned_show, _aftertalk, _closing = _composition(storage)
    client = TestClient(create_v2_app(composition, now_provider=lambda: STARTED_AT))

    _create_session(client, "session-plan-state")
    _bind_plan(client, "session-plan-state")

    session = storage.get_v2_session("session-plan-state")
    plan_state = session["metadata"]["live_episode_plan_state"]
    public_summary = session["public_summary"]
    assert plan_state["contract"]["plan_id"] == "plan-1"
    assert plan_state["contract"]["title"] == "V2 durable episode"
    assert plan_state["cursor"] == 0
    assert plan_state["completed_turn_ids"] == []
    assert plan_state["last_memoria_session_id"] is None
    assert public_summary["plan_id"] == "plan-1"
    assert public_summary["plan_title"] == "V2 durable episode"
    assert public_summary["turn_count"] == 0
    assert public_summary["status"] == "invalid"
    _assert_no_private_payload(session)


def test_real_storage_restart_recovery_reads_existing_snapshot(tmp_path):
    storage = _storage_manager(tmp_path)
    composition, _planned_show, _aftertalk, _closing = _composition(storage)
    client = TestClient(create_v2_app(composition, now_provider=lambda: STARTED_AT))
    _create_session(client, "session-restart")
    _bind_plan(client, "session-restart")
    composition.runtime_service.tick_session(
        _command("cmd-tick-planned", "session-restart", STARTED_AT + timedelta(seconds=10)),
        STARTED_AT + timedelta(seconds=10),
    )
    composition.runtime_service.tick_session(
        _command("cmd-tick-aftertalk", "session-restart", STARTED_AT + timedelta(seconds=20)),
        STARTED_AT + timedelta(seconds=20),
    )

    restarted_storage = _storage_manager(tmp_path)
    restarted_composition, _planned_show2, aftertalk2, _closing2 = _composition(restarted_storage)
    snapshot = restarted_composition.storage.read_snapshot("session-restart")
    recovery_result = restarted_composition.runtime_service.recover_session(
        RuntimeCommand(
            command_id="cmd-recover",
            session_id="session-restart",
            command_type=RuntimeCommandType.RECOVER,
            issued_at=STARTED_AT + timedelta(seconds=30),
            payload={},
        ),
        STARTED_AT + timedelta(seconds=30),
    )

    assert snapshot.current_phase == LiveSessionPhase.AFTERTALK
    assert snapshot.plan_completed is True
    assert recovery_result.phase == LiveSessionPhase.AFTERTALK
    assert recovery_result.recovery_decision is not None
    assert recovery_result.recovery_decision.action == "evaluate_phase"
    assert len(aftertalk2.calls) == 1


def test_real_storage_repeated_command_id_survives_restart_without_duplicate_dispatch(
    tmp_path,
):
    storage = _storage_manager(tmp_path)
    composition, planned_show, _aftertalk, _closing = _composition(storage)
    client = TestClient(create_v2_app(composition, now_provider=lambda: STARTED_AT))
    _create_session(client, "session-idempotent")
    command = _command(
        "cmd-repeat",
        "session-idempotent",
        STARTED_AT + timedelta(seconds=10),
    )

    first_result = composition.runtime_service.tick_session(
        command,
        STARTED_AT + timedelta(seconds=10),
    )
    restarted_storage = _storage_manager(tmp_path)
    restarted_composition, restarted_planned_show, _aftertalk2, _closing2 = _composition(
        restarted_storage,
    )
    repeated_result = restarted_composition.runtime_service.tick_session(
        command,
        STARTED_AT + timedelta(seconds=10),
    )

    assert isinstance(repeated_result, RuntimeServiceResult)
    assert first_result == repeated_result
    assert len(planned_show.calls) == 1
    assert len(restarted_planned_show.calls) == 0
    assert restarted_storage.get_v2_phase_transition(
        "session-idempotent:cmd-repeat:transition"
    ) is not None
    _assert_no_private_payload(first_result)
    _assert_no_private_payload(repeated_result)


def test_recovery_cycle_after_restart_resumes_closing_then_marks_ended_idempotently(
    tmp_path,
):
    storage = _storage_manager(tmp_path)
    composition, _planned_show, _aftertalk, _closing = _composition(storage)
    client = TestClient(create_v2_app(composition, now_provider=lambda: STARTED_AT))
    _create_session(client, "session-recovery-cycle")
    storage.update_v2_session(
        "session-recovery-cycle",
        {
            "current_phase": "closing",
            "plan_completed": True,
            "manual_close_requested": True,
            "closing_completed": False,
        },
    )

    restarted_storage = _storage_manager(tmp_path)
    restarted_composition, _planned2, _aftertalk2, closing2 = _composition(
        restarted_storage,
    )
    first = dispatch_scheduler_recovery_cycle(
        restarted_composition.runtime_service,
        restarted_composition.storage.list_recoverable_sessions(),
        STARTED_AT + timedelta(seconds=10),
    )
    second = dispatch_scheduler_recovery_cycle(
        restarted_composition.runtime_service,
        restarted_composition.storage.list_recoverable_sessions(),
        STARTED_AT + timedelta(seconds=20),
    )
    repeated_second = dispatch_scheduler_recovery_cycle(
        restarted_composition.runtime_service,
        restarted_composition.storage.list_recoverable_sessions(),
        STARTED_AT + timedelta(seconds=20),
    )

    assert [result.phase for result in first.dispatched] == [LiveSessionPhase.CLOSING]
    assert [result.phase for result in second.dispatched] == [LiveSessionPhase.ENDED]
    assert [result.phase for result in repeated_second.dispatched] == []
    assert len(closing2.calls) == 1
    assert restarted_storage.get_v2_session("session-recovery-cycle")["current_phase"] == "ended"


def test_recovery_cycle_uses_new_state_marker_after_plan_completion(tmp_path):
    storage = _storage_manager(tmp_path)
    composition, _planned_show, _aftertalk, _closing = _composition(storage)
    client = TestClient(create_v2_app(composition, now_provider=lambda: STARTED_AT))
    _create_session(client, "session-plan-recovery")

    restarted_storage = _storage_manager(tmp_path)
    restarted_composition, planned2, aftertalk2, _closing2 = _composition(
        restarted_storage,
    )
    first = dispatch_scheduler_recovery_cycle(
        restarted_composition.runtime_service,
        restarted_composition.storage.list_recoverable_sessions(),
        STARTED_AT + timedelta(seconds=10),
    )
    second = dispatch_scheduler_recovery_cycle(
        restarted_composition.runtime_service,
        restarted_composition.storage.list_recoverable_sessions(),
        STARTED_AT + timedelta(seconds=20),
    )

    assert [intent.command_id for intent in first.intents] == [
        "scheduler:recover:session-plan-recovery:planned_show:plan_open:auto:closing_open"
    ]
    assert [intent.command_id for intent in second.intents] == [
        "scheduler:recover:session-plan-recovery:planned_show:plan_done:auto:closing_open"
    ]
    assert len(planned2.calls) == 1
    assert len(aftertalk2.calls) == 1
    assert restarted_storage.get_v2_session("session-plan-recovery")["current_phase"] == "aftertalk"


def test_automation_control_pause_survives_restart_and_blocks_cycles(tmp_path):
    storage = _storage_manager(tmp_path)
    composition, _planned_show, _aftertalk, _closing = _composition(storage)
    client = TestClient(create_v2_app(composition, now_provider=lambda: STARTED_AT))
    _create_session(client, "session-control")

    result = composition.runtime_service.update_automation_control(
        RuntimeCommand(
            command_id="cmd-control",
            session_id="session-control",
            command_type=RuntimeCommandType.UPDATE_AUTOMATION_CONTROL,
            issued_at=STARTED_AT,
            payload={"paused": True, "reason": "operator pause"},
        ),
        STARTED_AT,
    )

    restarted_storage = _storage_manager(tmp_path)
    restarted_composition, _planned2, _aftertalk2, _closing2 = _composition(
        restarted_storage,
    )
    tick = dispatch_scheduler_cycle(
        restarted_composition.runtime_service,
        restarted_composition.storage.list_recoverable_sessions(),
        STARTED_AT + timedelta(seconds=10),
    )
    recovery = dispatch_scheduler_recovery_cycle(
        restarted_composition.runtime_service,
        restarted_composition.storage.list_recoverable_sessions(),
        STARTED_AT + timedelta(seconds=20),
    )
    session = V2QueryService(restarted_storage).get_session("session-control")

    assert result.events[0].event_type == "automation_control_updated"
    assert tick.skipped[0].skip_reason == "automation_paused"
    assert recovery.skipped[0].skip_reason == "automation_paused"
    assert session["automation_control"]["paused"] is True
