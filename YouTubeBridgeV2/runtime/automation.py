"""Scheduler tick contracts for YouTubeBridgeV2 runtime automation.

This module defines deterministic scheduler tick intents and a single-dispatch
helper. It does not own background threads, timers, storage, or adapter calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from YouTubeBridgeV2.runtime.application_service import RuntimeCommand, RuntimeCommandType


@dataclass(frozen=True)
class AutomationTickPolicy:
    """Runtime automation policy for one scheduler tick source."""

    enabled: bool = True
    paused: bool = False
    interval_seconds: int = 5
    command_prefix: str = "scheduler"
    source: str = "runtime_scheduler"

    def __post_init__(self) -> None:
        object.__setattr__(self, "enabled", bool(self.enabled))
        object.__setattr__(self, "paused", bool(self.paused))
        object.__setattr__(
            self,
            "interval_seconds",
            max(1, _int_value(self.interval_seconds, 5)),
        )
        object.__setattr__(self, "command_prefix", str(self.command_prefix or "scheduler"))
        object.__setattr__(self, "source", str(self.source or "runtime_scheduler"))


@dataclass(frozen=True)
class SchedulerTickIntent:
    """A scheduler decision for one session and one wall-clock tick."""

    session_id: str
    should_dispatch: bool
    command_id: str
    command_type: RuntimeCommandType
    source: str
    skip_reason: str = ""
    next_run_delay_seconds: int = 5
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SchedulerSessionRef:
    """Scheduler-owned reference to one candidate runtime session."""

    session_id: str
    current_phase: object | None = None
    automation_enabled: bool = True
    automation_paused: bool = False


@dataclass(frozen=True)
class SchedulerCycleResult:
    """Summary for one scheduler cycle over explicit session refs."""

    intents: tuple[SchedulerTickIntent, ...] = ()
    dispatched: tuple[object, ...] = ()
    skipped: tuple[SchedulerTickIntent, ...] = ()
    next_run_delay_seconds: int = 5


@dataclass(frozen=True)
class SchedulerRecoverySessionRef:
    """Scheduler-owned reference for restart recovery."""

    session_id: str
    current_phase: object | None = None
    plan_completed: bool = False
    manual_close_requested: bool = False
    closing_completed: bool = False
    automation_enabled: bool = True
    automation_paused: bool = False


@dataclass(frozen=True)
class SchedulerRecoveryIntent:
    """A scheduler recovery decision for one session state marker."""

    session_id: str
    should_dispatch: bool
    command_id: str
    command_type: RuntimeCommandType
    source: str
    skip_reason: str = ""
    payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SchedulerRecoveryCycleResult:
    """Summary for one restart recovery cycle."""

    intents: tuple[SchedulerRecoveryIntent, ...] = ()
    dispatched: tuple[object, ...] = ()
    skipped: tuple[SchedulerRecoveryIntent, ...] = ()


def build_scheduler_tick_intent(
    session_id: str,
    now: datetime,
    policy: AutomationTickPolicy | None = None,
    *,
    current_phase: object | None = None,
) -> SchedulerTickIntent:
    """Build one deterministic scheduler tick intent without side effects."""

    tick_policy = policy or AutomationTickPolicy()
    safe_now = _utc_datetime(now)
    command_id = _command_id(tick_policy, session_id, safe_now)
    skip_reason = _skip_reason(tick_policy, current_phase)
    return SchedulerTickIntent(
        session_id=str(session_id),
        should_dispatch=not bool(skip_reason),
        command_id=command_id,
        command_type=RuntimeCommandType.TICK,
        source=tick_policy.source,
        skip_reason=skip_reason,
        next_run_delay_seconds=tick_policy.interval_seconds,
        payload={
            "source": tick_policy.source,
            "scheduler": {
                "interval_seconds": tick_policy.interval_seconds,
                "issued_at": safe_now.isoformat(),
            },
        },
    )


def build_scheduler_cycle_intents(
    sessions: object,
    now: datetime,
    policy: AutomationTickPolicy | None = None,
) -> tuple[SchedulerTickIntent, ...]:
    """Build deterministic tick intents for explicit scheduler session refs."""

    tick_policy = policy or AutomationTickPolicy()
    return tuple(
        build_scheduler_tick_intent(
            ref.session_id,
            now,
            _session_policy(tick_policy, ref),
            current_phase=ref.current_phase,
        )
        for ref in (_session_ref(session) for session in _iter_sessions(sessions))
    )


def dispatch_scheduler_cycle(
    runtime_service: object,
    sessions: object,
    now: datetime,
    policy: AutomationTickPolicy | None = None,
) -> SchedulerCycleResult:
    """Dispatch one scheduler cycle through RuntimeApplicationService ticks."""

    tick_policy = policy or AutomationTickPolicy()
    intents = build_scheduler_cycle_intents(sessions, now, tick_policy)
    dispatched: list[object] = []
    skipped: list[SchedulerTickIntent] = []
    for intent in intents:
        if intent.should_dispatch:
            dispatched.append(dispatch_scheduler_tick(runtime_service, intent, now))
        else:
            skipped.append(intent)
    return SchedulerCycleResult(
        intents=intents,
        dispatched=tuple(dispatched),
        skipped=tuple(skipped),
        next_run_delay_seconds=tick_policy.interval_seconds,
    )


def build_scheduler_recovery_intents(
    sessions: object,
    now: datetime,
    policy: AutomationTickPolicy | None = None,
) -> tuple[SchedulerRecoveryIntent, ...]:
    """Build deterministic recovery intents for explicit session refs."""

    recovery_policy = policy or AutomationTickPolicy()
    safe_now = _utc_datetime(now)
    return tuple(
        _recovery_intent(_recovery_ref(session), safe_now, recovery_policy)
        for session in _iter_sessions(sessions)
    )


def dispatch_scheduler_recovery_cycle(
    runtime_service: object,
    sessions: object,
    now: datetime,
    policy: AutomationTickPolicy | None = None,
) -> SchedulerRecoveryCycleResult:
    """Dispatch one restart recovery cycle through RuntimeApplicationService."""

    recovery_policy = policy or AutomationTickPolicy()
    intents = build_scheduler_recovery_intents(sessions, now, recovery_policy)
    dispatched: list[object] = []
    skipped: list[SchedulerRecoveryIntent] = []
    for intent in intents:
        if intent.should_dispatch:
            dispatched.append(dispatch_scheduler_recovery(runtime_service, intent, now))
        else:
            skipped.append(intent)
    return SchedulerRecoveryCycleResult(
        intents=intents,
        dispatched=tuple(dispatched),
        skipped=tuple(skipped),
    )


def dispatch_scheduler_recovery(
    runtime_service: object,
    intent: SchedulerRecoveryIntent,
    now: datetime,
) -> object:
    """Dispatch one restart recovery intent through RuntimeApplicationService."""

    if not intent.should_dispatch:
        return {
            "status": "skipped",
            "session_id": intent.session_id,
            "phase": None,
            "events": [],
            "errors": [],
            "correlation_id": intent.command_id,
            "skip_reason": intent.skip_reason,
        }
    command = RuntimeCommand(
        command_id=intent.command_id,
        session_id=intent.session_id,
        command_type=intent.command_type,
        issued_at=now,
        permission_context={
            "auth_method": "scheduler",
            "permission_group": "internal",
            "source": intent.source,
        },
        payload=dict(intent.payload),
    )
    return runtime_service.recover_session(command, now)


def dispatch_scheduler_tick(
    runtime_service: object,
    intent: SchedulerTickIntent,
    now: datetime,
) -> object:
    """Dispatch one scheduler tick intent through RuntimeApplicationService."""

    if not intent.should_dispatch:
        return {
            "status": "skipped",
            "session_id": intent.session_id,
            "phase": None,
            "events": [],
            "errors": [],
            "correlation_id": intent.command_id,
            "skip_reason": intent.skip_reason,
        }
    command = RuntimeCommand(
        command_id=intent.command_id,
        session_id=intent.session_id,
        command_type=intent.command_type,
        issued_at=now,
        permission_context={
            "auth_method": "scheduler",
            "permission_group": "internal",
            "source": intent.source,
        },
        payload=dict(intent.payload),
    )
    return runtime_service.tick_session(command, now)


def _recovery_intent(
    ref: SchedulerRecoverySessionRef,
    now: datetime,
    policy: AutomationTickPolicy,
) -> SchedulerRecoveryIntent:
    ref_policy = AutomationTickPolicy(
        enabled=policy.enabled and ref.automation_enabled,
        paused=policy.paused or ref.automation_paused,
        interval_seconds=policy.interval_seconds,
        command_prefix=policy.command_prefix,
        source=policy.source,
    )
    skip_reason = _skip_reason(ref_policy, ref.current_phase)
    return SchedulerRecoveryIntent(
        session_id=ref.session_id,
        should_dispatch=not bool(skip_reason),
        command_id=_recovery_command_id(ref_policy, ref),
        command_type=RuntimeCommandType.RECOVER,
        source=ref_policy.source,
        skip_reason=skip_reason,
        payload={
            "source": ref_policy.source,
            "scheduler": {
                "recovery": True,
                "issued_at": now.isoformat(),
                "state_marker": _recovery_state_marker(ref),
            },
        },
    )


def _session_policy(
    policy: AutomationTickPolicy,
    ref: SchedulerSessionRef,
) -> AutomationTickPolicy:
    return AutomationTickPolicy(
        enabled=policy.enabled and ref.automation_enabled,
        paused=policy.paused or ref.automation_paused,
        interval_seconds=policy.interval_seconds,
        command_prefix=policy.command_prefix,
        source=policy.source,
    )


def _iter_sessions(sessions: object) -> tuple[object, ...]:
    if sessions is None:
        return ()
    if isinstance(sessions, tuple):
        return sessions
    if isinstance(sessions, list):
        return tuple(sessions)
    return tuple(sessions)  # type: ignore[arg-type]


def _recovery_ref(value: object) -> SchedulerRecoverySessionRef:
    if isinstance(value, SchedulerRecoverySessionRef):
        return value
    data = value if isinstance(value, dict) else vars(value)
    automation_enabled, automation_paused = _automation_flags(data)
    return SchedulerRecoverySessionRef(
        session_id=str(data["session_id"]),
        current_phase=data.get("current_phase"),
        plan_completed=bool(data.get("plan_completed", False)),
        manual_close_requested=bool(data.get("manual_close_requested", False)),
        closing_completed=bool(data.get("closing_completed", False)),
        automation_enabled=automation_enabled,
        automation_paused=automation_paused,
    )


def _session_ref(value: object) -> SchedulerSessionRef:
    if isinstance(value, SchedulerSessionRef):
        return value
    data = value if isinstance(value, dict) else vars(value)
    automation_enabled, automation_paused = _automation_flags(data)
    return SchedulerSessionRef(
        session_id=str(data["session_id"]),
        current_phase=data.get("current_phase"),
        automation_enabled=automation_enabled,
        automation_paused=automation_paused,
    )


def _automation_flags(data: object) -> tuple[bool, bool]:
    mapping = data if isinstance(data, dict) else vars(data)
    metadata = mapping.get("metadata", {})
    control = metadata.get("automation_control", {}) if isinstance(metadata, dict) else {}
    enabled = mapping.get("automation_enabled", control.get("enabled", True))
    paused = mapping.get("automation_paused", control.get("paused", False))
    return bool(enabled), bool(paused)


def _recovery_command_id(
    policy: AutomationTickPolicy,
    ref: SchedulerRecoverySessionRef,
) -> str:
    return f"{policy.command_prefix}:recover:{ref.session_id}:{_recovery_state_marker(ref)}"


def _recovery_state_marker(ref: SchedulerRecoverySessionRef) -> str:
    return ":".join(
        [
            _phase_value(ref.current_phase) or "unknown",
            "plan_done" if ref.plan_completed else "plan_open",
            "manual_close" if ref.manual_close_requested else "auto",
            "closing_done" if ref.closing_completed else "closing_open",
        ]
    )


def _skip_reason(policy: AutomationTickPolicy, current_phase: object | None) -> str:
    if not policy.enabled:
        return "automation_disabled"
    if policy.paused:
        return "automation_paused"
    if _phase_value(current_phase) == "ended":
        return "session_ended"
    return ""


def _phase_value(value: object | None) -> str:
    if value is None:
        return ""
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return str(enum_value).lower()
    return str(value).lower()


def _command_id(policy: AutomationTickPolicy, session_id: str, now: datetime) -> str:
    return f"{policy.command_prefix}:{session_id}:{now.strftime('%Y%m%dT%H%M%SZ')}"


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _int_value(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


__all__ = [
    "AutomationTickPolicy",
    "SchedulerCycleResult",
    "SchedulerRecoveryCycleResult",
    "SchedulerRecoveryIntent",
    "SchedulerRecoverySessionRef",
    "SchedulerSessionRef",
    "SchedulerTickIntent",
    "build_scheduler_cycle_intents",
    "build_scheduler_recovery_intents",
    "build_scheduler_tick_intent",
    "dispatch_scheduler_cycle",
    "dispatch_scheduler_recovery",
    "dispatch_scheduler_recovery_cycle",
    "dispatch_scheduler_tick",
]
