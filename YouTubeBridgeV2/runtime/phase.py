"""YouTubeBridgeV2 runtime phase decision contracts.

本模組只提供純 phase decision。呼叫端可依 `PhaseTransition` 寫入
storage、發送 event 或呼叫 adapter，但這些 side effect 不屬於本模組。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum


class LiveSessionPhase(str, Enum):
    """V2 live session lifecycle phase."""

    PLANNED_SHOW = "planned_show"
    AFTERTALK = "aftertalk"
    CLOSING = "closing"
    ENDED = "ended"


class AftertalkPolicy(str, Enum):
    """LiveEpisodePlan 完成後的 aftertalk 啟用策略."""

    DISABLED = "disabled"
    AUTO = "auto"


class PhaseTransitionReason(str, Enum):
    """Runtime phase 維持或轉換的原因."""

    PLAN_COMPLETED = "plan_completed"
    AFTERTALK_ENABLED = "aftertalk_enabled"
    DURATION_REACHED = "duration_reached"
    MANUAL_CLOSE = "manual_close"
    CLOSING_COMPLETED = "closing_completed"
    INVALID_STATE_RECOVERY = "invalid_state_recovery"
    NO_CHANGE = "no_change"


@dataclass(frozen=True)
class DurationPolicy:
    """直播時間上限與 aftertalk 剩餘時間策略."""

    planned_duration_seconds: int | None
    auto_finalize_on_duration: bool
    aftertalk_requires_remaining_time: bool = True


@dataclass(frozen=True)
class DurationSummary:
    """Duration boundary 的純計算結果."""

    duration_reached: bool
    remaining_time_seconds: int | None
    aftertalk_allowed: bool


@dataclass(frozen=True)
class LiveSessionSnapshot:
    """Runtime phase decision 所需的 session 摘要."""

    current_phase: LiveSessionPhase | str
    session_started_at: datetime
    plan_completed: bool
    aftertalk_policy: AftertalkPolicy | str
    duration_policy: DurationPolicy
    manual_close_requested: bool = False
    closing_completed: bool = False


@dataclass(frozen=True)
class PhaseTransition:
    """下一個 phase decision 與呼叫端 next action."""

    current_phase: LiveSessionPhase | str
    next_phase: LiveSessionPhase
    changed: bool
    reason: PhaseTransitionReason
    metadata: dict[str, object]
    next_action: str


def evaluate_duration(
    session_started_at: datetime,
    now: datetime,
    duration_policy: DurationPolicy,
) -> DurationSummary:
    """回傳 duration boundary 摘要，不產生任何 side effect.

    Args:
        session_started_at: live session 起始時間。
        now: phase decision 使用的目前時間。
        duration_policy: 時間上限、自動收尾與 aftertalk 剩餘時間策略。

    Returns:
        DurationSummary: 是否到達時間上限、剩餘秒數與 aftertalk 是否允許。

    Side Effects:
        無。
    """

    planned_seconds = duration_policy.planned_duration_seconds
    if planned_seconds is None or planned_seconds <= 0:
        return DurationSummary(
            duration_reached=False,
            remaining_time_seconds=None,
            aftertalk_allowed=not duration_policy.aftertalk_requires_remaining_time,
        )

    elapsed_seconds = int((now - session_started_at).total_seconds())
    remaining_seconds = planned_seconds - elapsed_seconds
    duration_reached = remaining_seconds <= 0

    if duration_policy.aftertalk_requires_remaining_time:
        aftertalk_allowed = remaining_seconds > 0
    else:
        aftertalk_allowed = True

    return DurationSummary(
        duration_reached=duration_reached,
        remaining_time_seconds=remaining_seconds,
        aftertalk_allowed=aftertalk_allowed,
    )


def advance_phase(session_snapshot: LiveSessionSnapshot, now: datetime) -> PhaseTransition:
    """根據 session snapshot 回傳下一個 phase decision.

    Args:
        session_snapshot: 已整理好的 V2 session phase/policy/completion state。
        now: duration 與 transition 判斷使用的目前時間。

    Returns:
        PhaseTransition: 下一個 phase、轉換原因、metadata summary 與 next action。

    Side Effects:
        無。
    """

    duration_summary = evaluate_duration(
        session_snapshot.session_started_at,
        now,
        session_snapshot.duration_policy,
    )
    current_phase = _coerce_phase(session_snapshot.current_phase)

    if current_phase is None:
        return _transition(
            session_snapshot=session_snapshot,
            duration_summary=duration_summary,
            next_phase=LiveSessionPhase.CLOSING,
            reason=PhaseTransitionReason.INVALID_STATE_RECOVERY,
            next_action="start_closing",
        )

    if current_phase is LiveSessionPhase.ENDED:
        return _transition(
            session_snapshot=session_snapshot,
            duration_summary=duration_summary,
            next_phase=LiveSessionPhase.ENDED,
            reason=PhaseTransitionReason.NO_CHANGE,
            next_action="wait",
        )

    if (
        current_phase in {LiveSessionPhase.PLANNED_SHOW, LiveSessionPhase.AFTERTALK}
        and session_snapshot.manual_close_requested
    ):
        return _transition(
            session_snapshot=session_snapshot,
            duration_summary=duration_summary,
            next_phase=LiveSessionPhase.CLOSING,
            reason=PhaseTransitionReason.MANUAL_CLOSE,
            next_action="start_closing",
        )

    if current_phase is LiveSessionPhase.PLANNED_SHOW:
        return _advance_planned_show(session_snapshot, duration_summary)

    if current_phase is LiveSessionPhase.AFTERTALK:
        return _advance_aftertalk(session_snapshot, duration_summary)

    return _advance_closing(session_snapshot, duration_summary)


def _advance_planned_show(
    session_snapshot: LiveSessionSnapshot,
    duration_summary: DurationSummary,
) -> PhaseTransition:
    if _duration_forces_closing(session_snapshot.duration_policy, duration_summary):
        return _transition(
            session_snapshot=session_snapshot,
            duration_summary=duration_summary,
            next_phase=LiveSessionPhase.CLOSING,
            reason=PhaseTransitionReason.DURATION_REACHED,
            next_action="start_closing",
        )

    if session_snapshot.plan_completed:
        aftertalk_policy = _coerce_aftertalk_policy(session_snapshot.aftertalk_policy)
        if aftertalk_policy is AftertalkPolicy.AUTO and duration_summary.aftertalk_allowed:
            return _transition(
                session_snapshot=session_snapshot,
                duration_summary=duration_summary,
                next_phase=LiveSessionPhase.AFTERTALK,
                reason=PhaseTransitionReason.AFTERTALK_ENABLED,
                next_action="start_aftertalk",
            )

        return _transition(
            session_snapshot=session_snapshot,
            duration_summary=duration_summary,
            next_phase=LiveSessionPhase.CLOSING,
            reason=_plan_completion_closing_reason(duration_summary),
            next_action="start_closing",
        )

    return _transition(
        session_snapshot=session_snapshot,
        duration_summary=duration_summary,
        next_phase=LiveSessionPhase.PLANNED_SHOW,
        reason=PhaseTransitionReason.NO_CHANGE,
        next_action="run_planned_show",
    )


def _advance_aftertalk(
    session_snapshot: LiveSessionSnapshot,
    duration_summary: DurationSummary,
) -> PhaseTransition:
    if _duration_forces_closing(session_snapshot.duration_policy, duration_summary):
        return _transition(
            session_snapshot=session_snapshot,
            duration_summary=duration_summary,
            next_phase=LiveSessionPhase.CLOSING,
            reason=PhaseTransitionReason.DURATION_REACHED,
            next_action="start_closing",
        )

    return _transition(
        session_snapshot=session_snapshot,
        duration_summary=duration_summary,
        next_phase=LiveSessionPhase.AFTERTALK,
        reason=PhaseTransitionReason.NO_CHANGE,
        next_action="continue_aftertalk",
    )


def _advance_closing(
    session_snapshot: LiveSessionSnapshot,
    duration_summary: DurationSummary,
) -> PhaseTransition:
    if session_snapshot.closing_completed:
        return _transition(
            session_snapshot=session_snapshot,
            duration_summary=duration_summary,
            next_phase=LiveSessionPhase.ENDED,
            reason=PhaseTransitionReason.CLOSING_COMPLETED,
            next_action="mark_ended",
        )

    return _transition(
        session_snapshot=session_snapshot,
        duration_summary=duration_summary,
        next_phase=LiveSessionPhase.CLOSING,
        reason=PhaseTransitionReason.NO_CHANGE,
        next_action="start_closing",
    )


def _transition(
    *,
    session_snapshot: LiveSessionSnapshot,
    duration_summary: DurationSummary,
    next_phase: LiveSessionPhase,
    reason: PhaseTransitionReason,
    next_action: str,
) -> PhaseTransition:
    current_phase = session_snapshot.current_phase
    metadata = _metadata(
        session_snapshot=session_snapshot,
        duration_summary=duration_summary,
        next_phase=next_phase,
        reason=reason,
    )
    return PhaseTransition(
        current_phase=current_phase,
        next_phase=next_phase,
        changed=_phase_value(current_phase) != next_phase.value,
        reason=reason,
        metadata=metadata,
        next_action=next_action,
    )


def _metadata(
    *,
    session_snapshot: LiveSessionSnapshot,
    duration_summary: DurationSummary,
    next_phase: LiveSessionPhase,
    reason: PhaseTransitionReason,
) -> dict[str, object]:
    return {
        "previous_phase": _phase_value(session_snapshot.current_phase),
        "next_phase": next_phase.value,
        "reason": reason.value,
        "plan_completed": session_snapshot.plan_completed,
        "aftertalk_policy": _policy_value(session_snapshot.aftertalk_policy),
        "duration_summary": asdict(duration_summary),
        "manual_close_requested": session_snapshot.manual_close_requested,
        "closing_completed": session_snapshot.closing_completed,
    }


def _duration_forces_closing(
    duration_policy: DurationPolicy,
    duration_summary: DurationSummary,
) -> bool:
    return duration_policy.auto_finalize_on_duration and duration_summary.duration_reached


def _plan_completion_closing_reason(
    duration_summary: DurationSummary,
) -> PhaseTransitionReason:
    if duration_summary.duration_reached:
        return PhaseTransitionReason.DURATION_REACHED
    return PhaseTransitionReason.PLAN_COMPLETED


def _coerce_phase(phase: LiveSessionPhase | str) -> LiveSessionPhase | None:
    if isinstance(phase, LiveSessionPhase):
        return phase
    try:
        return LiveSessionPhase(str(phase))
    except ValueError:
        return None


def _coerce_aftertalk_policy(policy: AftertalkPolicy | str) -> AftertalkPolicy | None:
    if isinstance(policy, AftertalkPolicy):
        return policy
    try:
        return AftertalkPolicy(str(policy))
    except ValueError:
        return None


def _phase_value(phase: LiveSessionPhase | str) -> str:
    if isinstance(phase, LiveSessionPhase):
        return phase.value
    return str(phase)


def _policy_value(policy: AftertalkPolicy | str) -> str:
    if isinstance(policy, AftertalkPolicy):
        return policy.value
    return str(policy)


__all__ = [
    "AftertalkPolicy",
    "DurationPolicy",
    "DurationSummary",
    "LiveSessionPhase",
    "LiveSessionSnapshot",
    "PhaseTransition",
    "PhaseTransitionReason",
    "advance_phase",
    "evaluate_duration",
]
