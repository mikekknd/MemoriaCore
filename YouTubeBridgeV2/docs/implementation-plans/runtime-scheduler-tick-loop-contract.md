# Runtime Scheduler Tick Loop Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 roadmap `4A`：建立 scheduler/tick loop contract，讓後續 automation 可用同一個可測 envelope 產生 runtime tick command，但本階段不啟動 background scheduler。

**Architecture:** 新增 pure-ish `YouTubeBridgeV2/runtime/automation.py` contract。它負責把 `session_id + now + AutomationTickPolicy` 轉成 `SchedulerTickIntent`，並提供一個單次 dispatch helper 把 intent 轉成 `RuntimeCommandType.TICK` 呼叫 `RuntimeApplicationService.tick_session(...)`；不建立 thread、timer、async loop 或 UI pause/resume route。

**Tech Stack:** Python 3.13、dataclasses、pytest、existing `RuntimeCommand` / `RuntimeCommandType.TICK`。

---

## Scope

Roadmap item：`4A：scheduler/tick loop contract`

完成條件：

- 新增 public contract：
  - `AutomationTickPolicy`
  - `SchedulerTickIntent`
  - `build_scheduler_tick_intent(session_id, now, policy, current_phase=None)`
  - `dispatch_scheduler_tick(runtime_service, intent, now)`
- Scheduler intent 會產生 deterministic `RuntimeCommandType.TICK` command id，方便 command idempotency。
- `enabled=False`、`paused=True`、`current_phase="ended"` 時 intent 會 skip，不呼叫 runtime service。
- Dispatch helper 只呼叫 `runtime_service.tick_session(command, now)`，不直接改 phase、不呼叫 adapter、不碰 storage。
- API reference / architecture docs 記錄 4A status。

不包含：

- background thread、async task、cron、heartbeat 或 service lifecycle。
- planned_show/aftertalk/closing 自動推進策略；4B 再接。
- restart/recovery hardening；4C 再接。
- operator pause/resume route 或 UI controls；4D 再接。

## File Structure

- Create: `YouTubeBridgeV2/runtime/automation.py`
  - scheduler tick policy/intent dataclasses。
  - deterministic command id builder。
  - dispatch helper。
- Create: `tests/youtubebridge_v2/test_runtime_automation.py`
  - contract tests。
  - no side effect tests。
- Modify docs:
  - `YouTubeBridgeV2/docs/modules/runtime-application-service.md`
  - `YouTubeBridgeV2/docs/api-reference-index.md`
  - `YouTubeBridgeV2/docs/architecture-index.md`

## Contract Shape

Implementation target:

```python
@dataclass(frozen=True)
class AutomationTickPolicy:
    enabled: bool = True
    paused: bool = False
    interval_seconds: int = 5
    command_prefix: str = "scheduler"
    source: str = "runtime_scheduler"


@dataclass(frozen=True)
class SchedulerTickIntent:
    session_id: str
    should_dispatch: bool
    command_id: str
    command_type: RuntimeCommandType
    source: str
    skip_reason: str = ""
    next_run_delay_seconds: int = 5
    payload: dict[str, object] = field(default_factory=dict)
```

Rules:

- `interval_seconds` coerces to at least `1`.
- `command_prefix` defaults to `scheduler` when empty.
- `source` defaults to `runtime_scheduler` when empty.
- `command_id` format: `<prefix>:<session_id>:<YYYYMMDDTHHMMSSZ>`。
- `payload` contains:

```python
{
    "source": policy.source,
    "scheduler": {
        "interval_seconds": interval_seconds,
        "issued_at": now.isoformat(),
    },
}
```

---

### Task 1: Red Contract Tests

**Files:**
- Create: `tests/youtubebridge_v2/test_runtime_automation.py`

- [ ] **Step 1: Add tests**

Create file:

```python
from __future__ import annotations

from datetime import datetime, timezone

from YouTubeBridgeV2.runtime.application_service import RuntimeCommandType
from YouTubeBridgeV2.runtime.automation import (
    AutomationTickPolicy,
    build_scheduler_tick_intent,
    dispatch_scheduler_tick,
)
from YouTubeBridgeV2.runtime.phase import LiveSessionPhase


NOW = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)


class FakeRuntimeService:
    def __init__(self):
        self.calls = []

    def tick_session(self, command, now):
        self.calls.append((command, now))
        return {
            "status": "ok",
            "session_id": command.session_id,
            "phase": "planned_show",
            "events": [],
            "errors": [],
            "correlation_id": f"runtime-{command.command_id}",
        }


def test_scheduler_tick_intent_builds_deterministic_tick_command():
    intent = build_scheduler_tick_intent(
        "session-1",
        NOW,
        AutomationTickPolicy(interval_seconds=10),
    )

    assert intent.should_dispatch is True
    assert intent.session_id == "session-1"
    assert intent.command_type is RuntimeCommandType.TICK
    assert intent.command_id == "scheduler:session-1:20260512T080000Z"
    assert intent.next_run_delay_seconds == 10
    assert intent.payload == {
        "source": "runtime_scheduler",
        "scheduler": {
            "interval_seconds": 10,
            "issued_at": "2026-05-12T08:00:00+00:00",
        },
    }


def test_scheduler_tick_intent_skips_disabled_paused_and_ended_sessions():
    disabled = build_scheduler_tick_intent(
        "session-1",
        NOW,
        AutomationTickPolicy(enabled=False),
    )
    paused = build_scheduler_tick_intent(
        "session-1",
        NOW,
        AutomationTickPolicy(paused=True),
    )
    ended = build_scheduler_tick_intent(
        "session-1",
        NOW,
        AutomationTickPolicy(),
        current_phase=LiveSessionPhase.ENDED,
    )

    assert disabled.should_dispatch is False
    assert disabled.skip_reason == "automation_disabled"
    assert paused.should_dispatch is False
    assert paused.skip_reason == "automation_paused"
    assert ended.should_dispatch is False
    assert ended.skip_reason == "session_ended"


def test_dispatch_scheduler_tick_calls_runtime_once_with_internal_context():
    service = FakeRuntimeService()
    intent = build_scheduler_tick_intent("session-1", NOW, AutomationTickPolicy())

    result = dispatch_scheduler_tick(service, intent, NOW)

    assert result["status"] == "ok"
    assert len(service.calls) == 1
    command, called_now = service.calls[0]
    assert command.command_id == intent.command_id
    assert command.command_type is RuntimeCommandType.TICK
    assert command.payload == intent.payload
    assert command.permission_context == {
        "auth_method": "scheduler",
        "permission_group": "internal",
        "source": "runtime_scheduler",
    }
    assert called_now == NOW


def test_dispatch_scheduler_tick_skips_without_runtime_side_effect():
    service = FakeRuntimeService()
    intent = build_scheduler_tick_intent(
        "session-1",
        NOW,
        AutomationTickPolicy(enabled=False),
    )

    result = dispatch_scheduler_tick(service, intent, NOW)

    assert result == {
        "status": "skipped",
        "session_id": "session-1",
        "phase": None,
        "events": [],
        "errors": [],
        "correlation_id": "scheduler:session-1:20260512T080000Z",
        "skip_reason": "automation_disabled",
    }
    assert service.calls == []
```

- [ ] **Step 2: Run red tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_automation.py -q
```

Expected before implementation:

- Import fails because `YouTubeBridgeV2.runtime.automation` does not exist.

### Task 2: Implement Automation Contract

**Files:**
- Create: `YouTubeBridgeV2/runtime/automation.py`

- [ ] **Step 1: Add implementation**

Create file:

```python
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
        object.__setattr__(self, "interval_seconds", max(1, _int_value(self.interval_seconds, 5)))
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
```

- [ ] **Step 2: Run automation tests green**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_automation.py -q
```

Expected:

- 4 tests pass.

### Task 3: Boundary and Integration Tests

**Files:**
- Modify: `tests/youtubebridge_v2/test_runtime_automation.py`

- [ ] **Step 1: Add route/storage boundary scan**

Append:

```python
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    return modules


def test_runtime_automation_contract_does_not_import_server_storage_or_adapters():
    modules = _imported_modules(ROOT / "YouTubeBridgeV2" / "runtime" / "automation.py")
    forbidden = [
        module
        for module in modules
        if module.startswith("YouTubeBridgeV2.server")
        or module.startswith("YouTubeBridgeV2.storage")
        or module.startswith("YouTubeBridgeV2.adapters")
        or module in {"sqlite3", "aiosqlite", "requests", "googleapiclient"}
    ]

    assert forbidden == []
```

- [ ] **Step 2: Run automation tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_automation.py -q
```

Expected:

- 5 tests pass.

### Task 4: Documentation

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/runtime-application-service.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`

- [ ] **Step 1: Update runtime application service module**

Add after Wave 3B status:

```markdown
Wave 4A scheduler contract:
- `AutomationTickPolicy` 與 `SchedulerTickIntent` 定義 scheduler/tick loop 的 command envelope。
- `build_scheduler_tick_intent(...)` 產生 deterministic `RuntimeCommandType.TICK` command id，讓 scheduler tick 可走既有 command idempotency。
- `dispatch_scheduler_tick(...)` 只做單次 runtime service delegation；background loop、phase automation policy、restart hardening 與 operator pause/resume controls 分別留給 4B/4C/4D。
```

- [ ] **Step 2: Update API reference**

Add under Runtime Application Service concepts/source:

```markdown
- `AutomationTickPolicy`
- `SchedulerTickIntent`
- `build_scheduler_tick_intent`
- `dispatch_scheduler_tick`
```

Source:

```markdown
- `YouTubeBridgeV2/runtime/automation.py::AutomationTickPolicy`
- `YouTubeBridgeV2/runtime/automation.py::SchedulerTickIntent`
- `YouTubeBridgeV2/runtime/automation.py::build_scheduler_tick_intent`
- `YouTubeBridgeV2/runtime/automation.py::dispatch_scheduler_tick`
```

- [ ] **Step 3: Update architecture status**

Add:

```markdown
## Integration Wave 4A 狀態

- [x] Scheduler tick contract：`AutomationTickPolicy` / `SchedulerTickIntent` 可產生 deterministic runtime tick command。
- [x] Single-dispatch helper：`dispatch_scheduler_tick(...)` 只委派 `RuntimeApplicationService.tick_session(...)`，不直接執行 adapter/storage/UI side effects。
- [x] Scope boundary：本階段不啟動 background scheduler，不處理 4B automatic advancement、4C restart hardening 或 4D pause/resume controls。
```

- [ ] **Step 4: Run docs sanity search**

Run:

```powershell
rg -n "Integration Wave 4A|AutomationTickPolicy|SchedulerTickIntent|dispatch_scheduler_tick" YouTubeBridgeV2\docs YouTubeBridgeV2\runtime
```

Expected:

- Matches include docs and `runtime/automation.py`.

### Task 5: Final Verification and Commit

**Files:**
- Created test/code and docs above.

- [ ] **Step 1: Run focused verification**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_automation.py -q
python -m pytest tests\youtubebridge_v2\test_runtime_phase.py -q
python -m pytest tests\youtubebridge_v2\test_runtime_application_service.py -q
python -m pytest tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py -q
```

Expected:

- Focused suites pass.

- [ ] **Step 2: Run full roadmap verification**

Run:

```powershell
python -m pytest tests\youtubebridge_v2 -q
git diff --check
```

Expected:

- Full V2 suite passes.
- `git diff --check` exits 0. CRLF warnings are acceptable if there are no whitespace errors.

- [ ] **Step 3: Commit exact files**

Run:

```powershell
git add YouTubeBridgeV2\runtime\automation.py tests\youtubebridge_v2\test_runtime_automation.py YouTubeBridgeV2\docs\modules\runtime-application-service.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\implementation-plans\runtime-scheduler-tick-loop-contract.md
git diff --cached --check
git commit -m "feat: add runtime scheduler tick contract"
```

Expected:

- Commit succeeds.

## Self-Review

- Spec coverage: 4A requires scheduler/tick loop contract; this plan adds the command envelope and single-dispatch helper, while explicitly deferring automatic phase progression, restart hardening, and pause/resume controls to later checklist items.
- Placeholder scan: no `TBD`, `TODO`, or vague implementation placeholders remain.
- Type consistency: intent uses existing `RuntimeCommandType.TICK` and dispatch helper calls existing `tick_session(command, now)`.
