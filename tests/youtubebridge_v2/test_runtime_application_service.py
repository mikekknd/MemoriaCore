from dataclasses import replace
from datetime import datetime, timezone

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
        "factcard",
        "fact_card",
        "memoriacore_raw",
        "youtube_raw",
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
