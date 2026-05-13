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
    "SchedulerTickIntent",
    "build_scheduler_tick_intent",
    "dispatch_scheduler_tick",
]
