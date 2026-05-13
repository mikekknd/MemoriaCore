from dataclasses import replace
from datetime import datetime, timezone

from YouTubeBridgeV2.adapters.youtube import YouTubePollingCursor
from YouTubeBridgeV2.runtime.application_service import (
    AdapterDispatchResult,
    RecoveryDecision,
    RuntimeApplicationService,
    RuntimeCommand,
    RuntimeCommandType,
    RuntimeServiceEvent,
    RuntimeServiceResult,
)
from YouTubeBridgeV2.runtime.phase import (
    AftertalkPolicy,
    DurationPolicy,
    LiveSessionPhase,
    LiveSessionSnapshot,
    PhaseTransition,
    PhaseTransitionReason,
    advance_phase,
)


BASE_NOW = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)
STARTED_AT = datetime(2026, 5, 12, 7, 30, tzinfo=timezone.utc)


def _duration_policy():
    return DurationPolicy(
        planned_duration_seconds=3600,
        auto_finalize_on_duration=True,
    )


def _snapshot(
    *,
    phase=LiveSessionPhase.PLANNED_SHOW,
    plan_completed=False,
    aftertalk_policy=AftertalkPolicy.AUTO,
    manual_close_requested=False,
    closing_completed=False,
):
    return LiveSessionSnapshot(
        current_phase=phase,
        session_started_at=STARTED_AT,
        plan_completed=plan_completed,
        aftertalk_policy=aftertalk_policy,
        duration_policy=_duration_policy(),
        manual_close_requested=manual_close_requested,
        closing_completed=closing_completed,
    )


def _command(
    command_type=RuntimeCommandType.TICK,
    *,
    command_id="cmd-1",
    session_id="session-1",
    payload=None,
):
    return RuntimeCommand(
        command_id=command_id,
        session_id=session_id,
        command_type=command_type,
        issued_at=BASE_NOW,
        permission_context={"operator_id": "tester"},
        payload=payload or {},
    )


def _raw_youtube_text_event(**overrides):
    event = {
        "id": "yt-evt-1",
        "snippet": {
            "type": "textMessageEvent",
            "publishedAt": "2026-05-12T08:10:00Z",
            "displayMessage": "Hello runtime",
            "textMessageDetails": {"messageText": "Hello runtime"},
            "authorChannelId": "channel-1",
            "rawTopicPack": {"hidden_prompt": "must not leak"},
        },
        "authorDetails": {
            "displayName": "Mika",
            "channelId": "channel-1",
            "isChatOwner": False,
            "isChatModerator": True,
            "isChatSponsor": False,
        },
        "raw_payload": {"access_token": "secret-value"},
    }
    event.update(overrides)
    return event


def _transition(next_action, *, next_phase=None, current_phase=LiveSessionPhase.PLANNED_SHOW):
    next_phase = next_phase or current_phase
    return PhaseTransition(
        current_phase=current_phase,
        next_phase=next_phase,
        changed=next_phase != current_phase,
        reason=PhaseTransitionReason.NO_CHANGE,
        metadata={
            "previous_phase": current_phase.value,
            "next_phase": next_phase.value,
            "reason": PhaseTransitionReason.NO_CHANGE.value,
        },
        next_action=next_action,
    )


class FakeStorage:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot or _snapshot()
        self.calls = []
        self.command_results = {}
        self.transitions = []
        self.events = []
        self.error_summaries = []
        self.youtube_events = []
        self.youtube_polling_cursor = None
        self.fail_persist_transition = False

    def get_command_result(self, command_id):
        self.calls.append(("get_command_result", command_id))
        return self.command_results.get(command_id)

    def save_command_result(self, command_id, result):
        self.calls.append(("save_command_result", command_id))
        self.command_results[command_id] = result

    def create_session(self, command, now):
        self.calls.append(("create_session", command.session_id, now))
        self.snapshot = command.payload.get("snapshot", self.snapshot)
        return self.snapshot

    def read_snapshot(self, session_id):
        self.calls.append(("read_snapshot", session_id))
        return self.snapshot

    def request_manual_close(self, session_id, command_id, now):
        self.calls.append(("request_manual_close", session_id, command_id, now))
        self.snapshot = replace(self.snapshot, manual_close_requested=True)
        return self.snapshot

    def persist_transition(self, session_id, command_id, transition, now):
        self.calls.append(("persist_transition", session_id, command_id, now))
        if self.fail_persist_transition:
            raise RuntimeError("storage unavailable")
        ref = {
            "transition_id": f"transition-{len(self.transitions) + 1}",
            "session_id": session_id,
            "previous_phase": transition.current_phase,
            "next_phase": transition.next_phase,
            "reason": transition.reason,
        }
        self.transitions.append(ref)
        return ref

    def persist_service_event(self, event):
        self.calls.append(("persist_service_event", event.event_type))
        self.events.append(event)

    def persist_error_summary(self, session_id, command_id, summary, retryable):
        self.calls.append(("persist_error_summary", session_id, command_id, retryable))
        self.error_summaries.append(
            {
                "session_id": session_id,
                "command_id": command_id,
                "summary": summary,
                "retryable": retryable,
            }
        )

    def persist_youtube_event(self, session_id, payload, now):
        self.calls.append(("persist_youtube_event", session_id, now))
        self.youtube_events.append(
            {
                "session_id": session_id,
                "payload": payload,
                "created_at": now,
            }
        )

    def load_youtube_polling_cursor(self, session_id):
        self.calls.append(("load_youtube_polling_cursor", session_id))
        return self.youtube_polling_cursor

    def save_youtube_polling_cursor(self, session_id, cursor, now):
        self.calls.append(("save_youtube_polling_cursor", session_id, now))
        self.youtube_polling_cursor = cursor


class FakePhaseAdvancer:
    def __init__(self, transition=None):
        self.transition = transition
        self.calls = []

    def __call__(self, snapshot, now):
        self.calls.append((snapshot, now))
        if self.transition is not None:
            return self.transition
        return advance_phase(snapshot, now)


class FakeRunner:
    def __init__(self, result=None):
        self.result = result or AdapterDispatchResult(
            status="ok",
            summary={"message": "ok"},
            retryable=False,
        )
        self.calls = []

    def run(self, *, command, snapshot, transition, now):
        self.calls.append(
            {
                "command": command,
                "snapshot": snapshot,
                "transition": transition,
                "now": now,
            }
        )
        return self.result


def _service(
    *,
    storage=None,
    phase_advancer=None,
    planned_show_runner=None,
    aftertalk=None,
    closing=None,
):
    return RuntimeApplicationService(
        storage=storage or FakeStorage(),
        phase_advancer=phase_advancer or FakePhaseAdvancer(),
        planned_show_runner=planned_show_runner or FakeRunner(),
        aftertalk=aftertalk or FakeRunner(),
        closing=closing or FakeRunner(),
    )


def _assert_no_forbidden_payload(value):
    text = repr(value).lower()
    for forbidden in (
        "hidden_prompt",
        "raw_payload",
        "topic_pack",
        "rawtopicpack",
        "factcard",
        "fact_card",
        "memoriacore_raw",
        "youtube_raw",
        "raw_youtube_payload",
        "access_token",
        "authorization",
        "secret-value",
        "must not leak",
    ):
        assert forbidden not in text


def test_create_session_command_delegates_to_storage():
    storage = FakeStorage()
    service = _service(storage=storage)
    command = _command(RuntimeCommandType.CREATE_SESSION)

    result = service.create_session(command, BASE_NOW)

    assert storage.calls[1][0] == "create_session"
    assert result.status == "ok"
    assert result.session_id == command.session_id
    assert result.phase == LiveSessionPhase.PLANNED_SHOW
    assert isinstance(result.events[0], RuntimeServiceEvent)


def test_tick_reads_snapshot_before_advancing_phase():
    storage = FakeStorage(snapshot=_snapshot())
    phase = FakePhaseAdvancer(_transition("run_planned_show"))
    service = _service(storage=storage, phase_advancer=phase)

    result = service.tick_session(_command(), BASE_NOW)

    assert storage.calls[1][0] == "read_snapshot"
    assert phase.calls == [(storage.snapshot, BASE_NOW)]
    assert storage.transitions
    assert result.status == "ok"


def test_handle_youtube_event_normalizes_raw_event_before_storage_and_tick():
    storage = FakeStorage(snapshot=_snapshot())
    phase = FakePhaseAdvancer(_transition("run_planned_show"))
    runner = FakeRunner()
    service = _service(
        storage=storage,
        phase_advancer=phase,
        planned_show_runner=runner,
    )
    command = _command(
        RuntimeCommandType.HANDLE_YOUTUBE_EVENT,
        command_id="cmd-youtube-1",
        payload={"youtube_event": _raw_youtube_text_event()},
    )

    result = service.handle_youtube_event(command, BASE_NOW)

    assert result.status == "ok"
    assert len(storage.youtube_events) == 1
    stored_payload = storage.youtube_events[0]["payload"]
    assert stored_payload["event_id"] == "yt-evt-1"
    assert stored_payload["event_type"] == "youtube_text_message"
    assert stored_payload["public_payload"]["message_text"] == "Hello runtime"
    assert stored_payload["public_payload"]["author_badges"] == ["moderator"]
    assert stored_payload["display_event"] == {
        "event_id": "yt-evt-1",
        "event_type": "audience_message",
        "author_display_name": "Mika",
        "message_text": "Hello runtime",
        "published_at": "2026-05-12T08:10:00Z",
        "author_badges": ["moderator"],
        "duplicate": False,
        "should_dispatch": True,
    }
    assert stored_payload["should_dispatch"] is True
    assert len(runner.calls) == 1
    _assert_no_forbidden_payload(stored_payload)
    _assert_no_forbidden_payload(result.events)


def test_handle_youtube_event_duplicate_command_does_not_persist_twice():
    storage = FakeStorage(snapshot=_snapshot())
    phase = FakePhaseAdvancer(_transition("run_planned_show"))
    runner = FakeRunner()
    service = _service(
        storage=storage,
        phase_advancer=phase,
        planned_show_runner=runner,
    )
    command = _command(
        RuntimeCommandType.HANDLE_YOUTUBE_EVENT,
        command_id="cmd-youtube-idempotent",
        payload={"youtube_event": _raw_youtube_text_event()},
    )

    first = service.handle_youtube_event(command, BASE_NOW)
    second = service.handle_youtube_event(command, BASE_NOW)

    assert second == first
    assert len(storage.youtube_events) == 1
    assert len(runner.calls) == 1


def test_handle_youtube_event_advances_and_persists_polling_cursor_from_payload():
    storage = FakeStorage(snapshot=_snapshot())
    runner = FakeRunner()
    service = _service(
        storage=storage,
        phase_advancer=FakePhaseAdvancer(_transition("run_planned_show")),
        planned_show_runner=runner,
    )
    command = _command(
        RuntimeCommandType.HANDLE_YOUTUBE_EVENT,
        command_id="cmd-youtube-cursor",
        payload={
            "youtube_event": _raw_youtube_text_event(id="yt-evt-2"),
            "polling_cursor": {
                "live_chat_id": "live-chat-1",
                "next_page_token": "page-1",
                "polling_interval_millis": 1500,
                "seen_event_ids": ["yt-evt-1"],
            },
            "page_info": {
                "next_page_token": "page-2",
                "polling_interval_millis": 2500,
            },
        },
    )

    result = service.handle_youtube_event(command, BASE_NOW)

    assert result.status == "ok"
    assert len(runner.calls) == 1
    assert isinstance(storage.youtube_polling_cursor, YouTubePollingCursor)
    assert storage.youtube_polling_cursor.live_chat_id == "live-chat-1"
    assert storage.youtube_polling_cursor.next_page_token == "page-2"
    assert storage.youtube_polling_cursor.polling_interval_millis == 2500
    assert storage.youtube_polling_cursor.seen_event_ids == ("yt-evt-1", "yt-evt-2")


def test_handle_youtube_event_uses_stored_cursor_to_skip_duplicate_after_restart():
    storage = FakeStorage(snapshot=_snapshot())
    storage.youtube_polling_cursor = YouTubePollingCursor(
        live_chat_id="live-chat-1",
        next_page_token="page-2",
        polling_interval_millis=2500,
        seen_event_ids=("yt-evt-1",),
    )
    runner = FakeRunner()
    service = _service(
        storage=storage,
        phase_advancer=FakePhaseAdvancer(_transition("run_planned_show")),
        planned_show_runner=runner,
    )
    command = _command(
        RuntimeCommandType.HANDLE_YOUTUBE_EVENT,
        command_id="cmd-youtube-duplicate",
        payload={"youtube_event": _raw_youtube_text_event()},
    )

    result = service.handle_youtube_event(command, BASE_NOW)

    assert result.status == "ok"
    assert not runner.calls
    assert len(storage.youtube_events) == 1
    stored_payload = storage.youtube_events[0]["payload"]
    assert stored_payload["duplicate"] is True
    assert stored_payload["should_dispatch"] is False
    assert result.events[0].event_type == "youtube_event_ignored"
    assert result.adapter_result.summary == {
        "youtube_event": "duplicate",
        "event_id": "yt-evt-1",
    }
    assert storage.youtube_polling_cursor.next_page_token == "page-2"
    assert storage.youtube_polling_cursor.polling_interval_millis == 2500


def test_phase_next_action_dispatches_planned_show_runner():
    planned_show_runner = FakeRunner()
    aftertalk = FakeRunner()
    closing = FakeRunner()
    service = _service(
        phase_advancer=FakePhaseAdvancer(_transition("run_planned_show")),
        planned_show_runner=planned_show_runner,
        aftertalk=aftertalk,
        closing=closing,
    )

    result = service.tick_session(_command(), BASE_NOW)

    assert len(planned_show_runner.calls) == 1
    assert not aftertalk.calls
    assert not closing.calls
    assert result.adapter_result.status == "ok"


def test_phase_next_action_dispatches_aftertalk():
    aftertalk = FakeRunner()
    service = _service(
        phase_advancer=FakePhaseAdvancer(
            _transition(
                "start_aftertalk",
                next_phase=LiveSessionPhase.AFTERTALK,
            )
        ),
        aftertalk=aftertalk,
    )

    result = service.tick_session(_command(), BASE_NOW)

    assert len(aftertalk.calls) == 1
    assert result.phase == LiveSessionPhase.AFTERTALK


def test_phase_next_action_dispatches_closing():
    closing = FakeRunner()
    service = _service(
        phase_advancer=FakePhaseAdvancer(
            _transition("start_closing", next_phase=LiveSessionPhase.CLOSING)
        ),
        closing=closing,
    )

    result = service.tick_session(_command(), BASE_NOW)

    assert len(closing.calls) == 1
    assert result.phase == LiveSessionPhase.CLOSING


def test_manual_close_wins_over_planned_turn_continuation():
    storage = FakeStorage(snapshot=_snapshot(plan_completed=False))
    planned_show_runner = FakeRunner()
    closing = FakeRunner()
    service = _service(
        storage=storage,
        planned_show_runner=planned_show_runner,
        closing=closing,
    )

    result = service.request_manual_close(
        _command(RuntimeCommandType.MANUAL_CLOSE),
        BASE_NOW,
    )

    assert not planned_show_runner.calls
    assert len(closing.calls) == 1
    assert result.phase == LiveSessionPhase.CLOSING
    assert result.transition_ref.next_phase == LiveSessionPhase.CLOSING


def test_duplicate_command_id_does_not_repeat_adapter_call():
    storage = FakeStorage()
    existing = RuntimeServiceResult(
        status="ok",
        session_id="session-1",
        phase=LiveSessionPhase.PLANNED_SHOW,
        events=[],
        errors=[],
        correlation_id="correlation-existing",
    )
    storage.command_results["cmd-1"] = existing
    runner = FakeRunner()
    phase = FakePhaseAdvancer(_transition("run_planned_show"))
    service = _service(storage=storage, phase_advancer=phase, planned_show_runner=runner)

    result = service.tick_session(_command(command_id="cmd-1"), BASE_NOW)

    assert result == existing
    assert not phase.calls
    assert not runner.calls


def test_retryable_adapter_error_is_persisted_with_redacted_summary():
    runner = FakeRunner(
        AdapterDispatchResult(
            status="error",
            summary={
                "message": "timeout",
                "raw_payload": {"token": "secret"},
                "hidden_prompt": "do not expose",
            },
            retryable=True,
        )
    )
    storage = FakeStorage()
    service = _service(
        storage=storage,
        phase_advancer=FakePhaseAdvancer(_transition("run_planned_show")),
        planned_show_runner=runner,
    )

    result = service.tick_session(_command(), BASE_NOW)

    assert result.status == "retryable_error"
    assert storage.error_summaries[0]["retryable"] is True
    _assert_no_forbidden_payload(storage.error_summaries)
    _assert_no_forbidden_payload(result.events)


def test_storage_write_failure_stops_later_side_effects():
    storage = FakeStorage()
    storage.fail_persist_transition = True
    runner = FakeRunner()
    service = _service(
        storage=storage,
        phase_advancer=FakePhaseAdvancer(_transition("run_planned_show")),
        planned_show_runner=runner,
    )

    result = service.tick_session(_command(), BASE_NOW)

    assert result.status == "error"
    assert result.errors[0]["code"] == "storage_write_failed"
    assert not runner.calls


def test_crash_recovery_resumes_incomplete_closing():
    storage = FakeStorage(
        snapshot=_snapshot(phase=LiveSessionPhase.CLOSING, closing_completed=False)
    )
    closing = FakeRunner()
    service = _service(storage=storage, closing=closing)

    result = service.recover_session(
        _command(RuntimeCommandType.RECOVER),
        BASE_NOW,
    )

    assert isinstance(result.recovery_decision, RecoveryDecision)
    assert result.recovery_decision.action == "resume_closing"
    assert len(closing.calls) == 1
    assert result.phase == LiveSessionPhase.CLOSING


def test_runtime_service_event_excludes_hidden_prompt_and_raw_payload():
    runner = FakeRunner(
        AdapterDispatchResult(
            status="ok",
            summary={
                "message": "safe summary",
                "hidden_prompt": "secret",
                "topic_pack": "full topic pack",
                "raw_payload": {"memoriacore_raw": "secret"},
            },
            retryable=False,
        )
    )
    service = _service(
        phase_advancer=FakePhaseAdvancer(_transition("run_planned_show")),
        planned_show_runner=runner,
    )

    result = service.tick_session(_command(), BASE_NOW)

    assert result.events
    _assert_no_forbidden_payload(result.events)
    assert result.events[0].payload["adapter_summary"] == {"message": "safe summary"}


def test_invalid_aftertalk_policy_returns_contract_error_without_advancing_phase():
    storage = FakeStorage(
        snapshot=_snapshot(plan_completed=True, aftertalk_policy="unsupported")
    )
    phase = FakePhaseAdvancer()
    service = _service(storage=storage, phase_advancer=phase)

    result = service.tick_session(_command(), BASE_NOW)

    assert result.status == "contract_error"
    assert result.errors[0]["code"] == "invalid_aftertalk_policy"
    assert not phase.calls
    assert not storage.transitions
