from datetime import datetime, timedelta, timezone
import inspect

from YouTubeBridgeV2.runtime.phase import (
    AftertalkPolicy,
    DurationPolicy,
    LiveSessionPhase,
    LiveSessionSnapshot,
    PhaseTransition,
    PhaseTransitionReason,
    advance_phase,
    evaluate_duration,
)


BASE_NOW = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)
STARTED_AT = datetime(2026, 5, 12, 7, 30, tzinfo=timezone.utc)


def _duration_policy(
    planned_duration_seconds=3600,
    *,
    auto_finalize_on_duration=True,
    aftertalk_requires_remaining_time=True,
):
    return DurationPolicy(
        planned_duration_seconds=planned_duration_seconds,
        auto_finalize_on_duration=auto_finalize_on_duration,
        aftertalk_requires_remaining_time=aftertalk_requires_remaining_time,
    )


def _snapshot(
    *,
    current_phase=LiveSessionPhase.PLANNED_SHOW,
    plan_completed=False,
    aftertalk_policy=AftertalkPolicy.AUTO,
    duration_policy=None,
    manual_close_requested=False,
    closing_completed=False,
):
    return LiveSessionSnapshot(
        current_phase=current_phase,
        session_started_at=STARTED_AT,
        plan_completed=plan_completed,
        aftertalk_policy=aftertalk_policy,
        duration_policy=duration_policy or _duration_policy(),
        manual_close_requested=manual_close_requested,
        closing_completed=closing_completed,
    )


def _assert_transition(
    transition,
    *,
    current_phase,
    next_phase,
    changed,
    reason,
    next_action,
):
    assert transition.current_phase == current_phase
    assert transition.next_phase == next_phase
    assert transition.changed is changed
    assert transition.reason == reason
    assert transition.next_action == next_action


def _assert_safe_metadata(transition):
    metadata = transition.metadata
    assert metadata["previous_phase"] == _phase_value(transition.current_phase)
    assert metadata["next_phase"] == transition.next_phase.value
    assert metadata["reason"] == transition.reason.value
    assert "plan_completed" in metadata
    assert "aftertalk_policy" in metadata
    assert "duration_summary" in metadata
    assert "manual_close_requested" in metadata
    assert "closing_completed" in metadata

    metadata_text = repr(metadata).lower()
    forbidden_fragments = [
        "raw_prompt",
        "topic_pack",
        "factcard",
        "fact_card",
        "youtube_raw",
        "memoriacore_raw",
        "hidden_context",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in metadata_text


def _phase_value(phase):
    return phase.value if isinstance(phase, LiveSessionPhase) else str(phase)


def test_planned_show_continues_when_plan_not_completed():
    transition = advance_phase(_snapshot(plan_completed=False), BASE_NOW)

    _assert_transition(
        transition,
        current_phase=LiveSessionPhase.PLANNED_SHOW,
        next_phase=LiveSessionPhase.PLANNED_SHOW,
        changed=False,
        reason=PhaseTransitionReason.NO_CHANGE,
        next_action="run_planned_show",
    )
    _assert_safe_metadata(transition)


def test_planned_show_completed_enters_aftertalk_when_auto_policy_has_remaining_time():
    transition = advance_phase(
        _snapshot(plan_completed=True, aftertalk_policy=AftertalkPolicy.AUTO),
        BASE_NOW,
    )

    _assert_transition(
        transition,
        current_phase=LiveSessionPhase.PLANNED_SHOW,
        next_phase=LiveSessionPhase.AFTERTALK,
        changed=True,
        reason=PhaseTransitionReason.AFTERTALK_ENABLED,
        next_action="start_aftertalk",
    )
    _assert_safe_metadata(transition)


def test_planned_show_completed_enters_closing_when_aftertalk_disabled():
    transition = advance_phase(
        _snapshot(
            plan_completed=True,
            aftertalk_policy=AftertalkPolicy.DISABLED,
        ),
        BASE_NOW,
    )

    _assert_transition(
        transition,
        current_phase=LiveSessionPhase.PLANNED_SHOW,
        next_phase=LiveSessionPhase.CLOSING,
        changed=True,
        reason=PhaseTransitionReason.PLAN_COMPLETED,
        next_action="start_closing",
    )
    _assert_safe_metadata(transition)


def test_planned_show_completed_enters_closing_when_no_remaining_time():
    transition = advance_phase(
        _snapshot(plan_completed=True, aftertalk_policy=AftertalkPolicy.AUTO),
        STARTED_AT + timedelta(seconds=3600),
    )

    _assert_transition(
        transition,
        current_phase=LiveSessionPhase.PLANNED_SHOW,
        next_phase=LiveSessionPhase.CLOSING,
        changed=True,
        reason=PhaseTransitionReason.DURATION_REACHED,
        next_action="start_closing",
    )
    _assert_safe_metadata(transition)


def test_manual_close_from_planned_show_enters_closing():
    transition = advance_phase(
        _snapshot(manual_close_requested=True),
        BASE_NOW,
    )

    _assert_transition(
        transition,
        current_phase=LiveSessionPhase.PLANNED_SHOW,
        next_phase=LiveSessionPhase.CLOSING,
        changed=True,
        reason=PhaseTransitionReason.MANUAL_CLOSE,
        next_action="start_closing",
    )
    _assert_safe_metadata(transition)


def test_manual_close_from_aftertalk_enters_closing():
    transition = advance_phase(
        _snapshot(
            current_phase=LiveSessionPhase.AFTERTALK,
            manual_close_requested=True,
        ),
        BASE_NOW,
    )

    _assert_transition(
        transition,
        current_phase=LiveSessionPhase.AFTERTALK,
        next_phase=LiveSessionPhase.CLOSING,
        changed=True,
        reason=PhaseTransitionReason.MANUAL_CLOSE,
        next_action="start_closing",
    )
    _assert_safe_metadata(transition)


def test_aftertalk_continues_before_duration_limit():
    transition = advance_phase(
        _snapshot(current_phase=LiveSessionPhase.AFTERTALK),
        BASE_NOW,
    )

    _assert_transition(
        transition,
        current_phase=LiveSessionPhase.AFTERTALK,
        next_phase=LiveSessionPhase.AFTERTALK,
        changed=False,
        reason=PhaseTransitionReason.NO_CHANGE,
        next_action="continue_aftertalk",
    )
    _assert_safe_metadata(transition)


def test_aftertalk_enters_closing_when_duration_reached():
    transition = advance_phase(
        _snapshot(current_phase=LiveSessionPhase.AFTERTALK),
        STARTED_AT + timedelta(seconds=3600),
    )

    _assert_transition(
        transition,
        current_phase=LiveSessionPhase.AFTERTALK,
        next_phase=LiveSessionPhase.CLOSING,
        changed=True,
        reason=PhaseTransitionReason.DURATION_REACHED,
        next_action="start_closing",
    )
    _assert_safe_metadata(transition)


def test_closing_enters_ended_only_when_closing_completed():
    incomplete = advance_phase(
        _snapshot(current_phase=LiveSessionPhase.CLOSING, closing_completed=False),
        BASE_NOW,
    )
    completed = advance_phase(
        _snapshot(current_phase=LiveSessionPhase.CLOSING, closing_completed=True),
        BASE_NOW,
    )

    _assert_transition(
        incomplete,
        current_phase=LiveSessionPhase.CLOSING,
        next_phase=LiveSessionPhase.CLOSING,
        changed=False,
        reason=PhaseTransitionReason.NO_CHANGE,
        next_action="start_closing",
    )
    _assert_transition(
        completed,
        current_phase=LiveSessionPhase.CLOSING,
        next_phase=LiveSessionPhase.ENDED,
        changed=True,
        reason=PhaseTransitionReason.CLOSING_COMPLETED,
        next_action="mark_ended",
    )
    _assert_safe_metadata(incomplete)
    _assert_safe_metadata(completed)


def test_ended_stays_ended():
    transition = advance_phase(
        _snapshot(
            current_phase=LiveSessionPhase.ENDED,
            plan_completed=True,
            manual_close_requested=True,
            closing_completed=True,
        ),
        BASE_NOW,
    )

    _assert_transition(
        transition,
        current_phase=LiveSessionPhase.ENDED,
        next_phase=LiveSessionPhase.ENDED,
        changed=False,
        reason=PhaseTransitionReason.NO_CHANGE,
        next_action="wait",
    )
    _assert_safe_metadata(transition)


def test_invalid_phase_recovers_to_closing():
    transition = advance_phase(
        _snapshot(current_phase="paused_by_unknown_runtime"),
        BASE_NOW,
    )

    _assert_transition(
        transition,
        current_phase="paused_by_unknown_runtime",
        next_phase=LiveSessionPhase.CLOSING,
        changed=True,
        reason=PhaseTransitionReason.INVALID_STATE_RECOVERY,
        next_action="start_closing",
    )
    _assert_safe_metadata(transition)


def test_evaluate_duration_reports_positive_zero_negative_and_unbounded():
    positive = evaluate_duration(STARTED_AT, BASE_NOW, _duration_policy())
    zero = evaluate_duration(
        STARTED_AT,
        STARTED_AT + timedelta(seconds=3600),
        _duration_policy(),
    )
    negative = evaluate_duration(
        STARTED_AT,
        STARTED_AT + timedelta(seconds=3660),
        _duration_policy(),
    )
    unbounded_none = evaluate_duration(
        STARTED_AT,
        BASE_NOW,
        _duration_policy(planned_duration_seconds=None),
    )
    unbounded_zero = evaluate_duration(
        STARTED_AT,
        BASE_NOW,
        _duration_policy(planned_duration_seconds=0),
    )
    unbounded_negative = evaluate_duration(
        STARTED_AT,
        BASE_NOW,
        _duration_policy(planned_duration_seconds=-60),
    )

    assert positive.duration_reached is False
    assert positive.remaining_time_seconds == 1800
    assert positive.aftertalk_allowed is True

    assert zero.duration_reached is True
    assert zero.remaining_time_seconds == 0
    assert zero.aftertalk_allowed is False

    assert negative.duration_reached is True
    assert negative.remaining_time_seconds == -60
    assert negative.aftertalk_allowed is False

    for summary in (unbounded_none, unbounded_zero, unbounded_negative):
        assert summary.duration_reached is False
        assert summary.remaining_time_seconds is None
        assert summary.aftertalk_allowed is False


def test_phase_decision_is_idempotent_for_same_snapshot():
    snapshot = _snapshot(plan_completed=True, aftertalk_policy=AftertalkPolicy.AUTO)

    first = advance_phase(snapshot, BASE_NOW)
    second = advance_phase(snapshot, BASE_NOW)

    assert first == second


def test_phase_decision_has_no_external_side_effects():
    signature = inspect.signature(advance_phase)

    assert list(signature.parameters) == ["session_snapshot", "now"]

    transition = advance_phase(_snapshot(), BASE_NOW)

    assert isinstance(transition, PhaseTransition)
    assert transition.metadata["duration_summary"] == {
        "duration_reached": False,
        "remaining_time_seconds": 1800,
        "aftertalk_allowed": True,
    }
