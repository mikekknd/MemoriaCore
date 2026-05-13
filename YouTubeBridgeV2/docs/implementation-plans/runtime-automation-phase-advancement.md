# Runtime Automation Phase Advancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 roadmap `4B`：讓 scheduler cycle 對已知 active sessions 自動 dispatch planned_show、aftertalk、closing tick，不再只能靠 operator 手動 tick endpoint 逐步推進。

**Architecture:** 延伸 `YouTubeBridgeV2/runtime/automation.py`，新增 explicit session ref 與 cycle result contract。Automation module 只把上層提供的 session refs 轉成 deterministic tick intents，逐一委派既有 `dispatch_scheduler_tick(...)`；phase transition、duration policy、aftertalk policy 與 runner side effects 仍由 `RuntimeApplicationService` / `Runtime Phase` 決定。

**Tech Stack:** Python 3.13、dataclasses、pytest、existing `RuntimeApplicationService` / `RuntimeCommandType.TICK` / fake runners。

---

## Scope

Roadmap item：`4B：planned_show/aftertalk/closing 自動推進`

完成條件：

- 新增 public contract：
  - `SchedulerSessionRef`
  - `SchedulerCycleResult`
  - `build_scheduler_cycle_intents(sessions, now, policy=None)`
  - `dispatch_scheduler_cycle(runtime_service, sessions, now, policy=None)`
- Cycle 對 `planned_show`、`aftertalk`、`closing` session refs 自動產生 tick command 並 dispatch。
- `ended`、disabled、paused refs 不會 dispatch。
- 用 fake runtime service 驗證多 session cycle dispatch。
- 用 existing runtime service + fake runners 驗證 successive scheduler cycles 可完成 `planned_show -> aftertalk -> closing -> ended`，不呼叫 API tick endpoint。
- 更新 runtime/API/architecture docs。

不包含：

- 不新增 background thread、async task、process supervisor、cron 或 service lifecycle。
- 不新增 durable active-session discovery；4C 再處理 restart/recovery/idempotency hardening。
- 不新增 operator pause/resume API 或 UI controls；4D 再處理。

## File Structure

- Modify: `YouTubeBridgeV2/runtime/automation.py`
  - 新增 session ref 與 cycle helper。
- Modify: `tests/youtubebridge_v2/test_runtime_automation.py`
  - 新增 fake-backed cycle dispatch tests 與 full runtime auto-advance test。
- Modify docs:
  - `YouTubeBridgeV2/docs/modules/runtime-application-service.md`
  - `YouTubeBridgeV2/docs/api-reference-index.md`
  - `YouTubeBridgeV2/docs/architecture-index.md`

## Contract Shape

Implementation target:

```python
@dataclass(frozen=True)
class SchedulerSessionRef:
    session_id: str
    current_phase: object | None = None
    automation_enabled: bool = True
    automation_paused: bool = False


@dataclass(frozen=True)
class SchedulerCycleResult:
    intents: tuple[SchedulerTickIntent, ...] = ()
    dispatched: tuple[object, ...] = ()
    skipped: tuple[SchedulerTickIntent, ...] = ()
    next_run_delay_seconds: int = 5
```

Rules:

- `build_scheduler_cycle_intents(...)` accepts `SchedulerSessionRef`, dict-like values, or objects with compatible attributes.
- Per-session `automation_enabled=False` maps to `automation_disabled`; per-session `automation_paused=True` maps to `automation_paused`.
- Global `AutomationTickPolicy(enabled=False/paused=True)` still applies to every ref.
- `dispatch_scheduler_cycle(...)` returns all intents plus dispatched results and skipped intents.
- Command ids remain `<prefix>:<session_id>:<YYYYMMDDTHHMMSSZ>` and keep existing idempotency behavior.

---

### Task 1: Red Cycle Contract Tests

**Files:**
- Modify: `tests/youtubebridge_v2/test_runtime_automation.py`

- [ ] **Step 1: Add imports**

Add:

```python
from datetime import timedelta

from tests.youtubebridge_v2.fakes import (
    FakeAftertalkRunner,
    FakeClosingRunner,
    FakePlannedShowRunner,
    InMemoryV2StorageManager,
)
from YouTubeBridgeV2.composition import create_v2_composition
from YouTubeBridgeV2.runtime.application_service import RuntimeCommand
from YouTubeBridgeV2.runtime.automation import (
    SchedulerSessionRef,
    build_scheduler_cycle_intents,
    dispatch_scheduler_cycle,
)
```

- [ ] **Step 2: Add multi-session cycle test**

Append:

```python
def test_scheduler_cycle_dispatches_active_phase_refs_and_skips_safe_refs():
    service = FakeRuntimeService()
    sessions = [
        SchedulerSessionRef("planned", current_phase=LiveSessionPhase.PLANNED_SHOW),
        SchedulerSessionRef("aftertalk", current_phase=LiveSessionPhase.AFTERTALK),
        SchedulerSessionRef("closing", current_phase=LiveSessionPhase.CLOSING),
        SchedulerSessionRef("ended", current_phase=LiveSessionPhase.ENDED),
        SchedulerSessionRef(
            "paused",
            current_phase=LiveSessionPhase.PLANNED_SHOW,
            automation_paused=True,
        ),
    ]

    result = dispatch_scheduler_cycle(service, sessions, NOW, AutomationTickPolicy())

    assert [command.session_id for command, _now in service.calls] == [
        "planned",
        "aftertalk",
        "closing",
    ]
    assert [command.command_id for command, _now in service.calls] == [
        "scheduler:planned:20260512T080000Z",
        "scheduler:aftertalk:20260512T080000Z",
        "scheduler:closing:20260512T080000Z",
    ]
    assert [intent.session_id for intent in result.skipped] == ["ended", "paused"]
    assert [intent.skip_reason for intent in result.skipped] == [
        "session_ended",
        "automation_paused",
    ]
    assert len(result.dispatched) == 3
    assert result.next_run_delay_seconds == 5
```

- [ ] **Step 3: Add dict coercion test**

Append:

```python
def test_scheduler_cycle_builds_intents_from_mapping_refs():
    intents = build_scheduler_cycle_intents(
        [
            {
                "session_id": "session-map",
                "current_phase": "planned_show",
                "automation_enabled": False,
            }
        ],
        NOW,
        AutomationTickPolicy(interval_seconds=15),
    )

    assert len(intents) == 1
    assert intents[0].session_id == "session-map"
    assert intents[0].should_dispatch is False
    assert intents[0].skip_reason == "automation_disabled"
    assert intents[0].next_run_delay_seconds == 15
```

- [ ] **Step 4: Run red tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_automation.py -q
```

Expected:

- Fails because `SchedulerSessionRef`, `build_scheduler_cycle_intents`, and `dispatch_scheduler_cycle` are missing.

### Task 2: Implement Cycle Contract

**Files:**
- Modify: `YouTubeBridgeV2/runtime/automation.py`

- [ ] **Step 1: Add dataclasses and cycle helpers**

Add after `SchedulerTickIntent`:

```python
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
```

Add functions:

```python
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
```

Add helpers:

```python
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


def _session_ref(value: object) -> SchedulerSessionRef:
    if isinstance(value, SchedulerSessionRef):
        return value
    data = value if isinstance(value, dict) else vars(value)
    return SchedulerSessionRef(
        session_id=str(data["session_id"]),
        current_phase=data.get("current_phase"),
        automation_enabled=bool(data.get("automation_enabled", True)),
        automation_paused=bool(data.get("automation_paused", False)),
    )
```

Update `__all__`:

```python
"SchedulerCycleResult",
"SchedulerSessionRef",
"build_scheduler_cycle_intents",
"dispatch_scheduler_cycle",
```

- [ ] **Step 2: Run cycle tests green**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_automation.py -q
```

Expected:

- Cycle contract tests pass.

### Task 3: Runtime Auto-Advance Integration Test

**Files:**
- Modify: `tests/youtubebridge_v2/test_runtime_automation.py`

- [ ] **Step 1: Add helpers**

Append:

```python
def _runtime_command(command_id, session_id, command_type, now, payload=None):
    return RuntimeCommand(
        command_id=command_id,
        session_id=session_id,
        command_type=command_type,
        issued_at=now,
        permission_context={"operator_id": "automation-test"},
        payload=payload or {},
    )


def _plan():
    return {
        "plan_id": "plan-auto",
        "title": "Automation phase advancement",
        "turns": [
            {
                "id": "opening",
                "purpose": "Open the show.",
                "topic_cue": "Automation test.",
                "speaker_policy": {"type": "fixed", "speaker_ids": ["host"]},
                "audience_insertion": {"enabled": False, "allow_super_chats": False},
            }
        ],
        "raw_topic_pack": "must not leak",
    }


def _current_ref(storage, session_id):
    record = storage.get_v2_session(session_id)
    return SchedulerSessionRef(session_id, current_phase=record["current_phase"])
```

- [ ] **Step 2: Add successive scheduler cycle integration test**

Append:

```python
def test_scheduler_cycle_auto_advances_planned_aftertalk_closing_to_ended():
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
    session_id = "session-auto-advance"
    composition.runtime_service.create_session(
        _runtime_command(
            "auto-create",
            session_id,
            RuntimeCommandType.CREATE_SESSION,
            NOW,
            {
                "aftertalk_policy": "auto",
                "metadata": {
                    "duration_policy": {
                        "planned_duration_seconds": 30,
                        "auto_finalize_on_duration": True,
                        "aftertalk_requires_remaining_time": True,
                    }
                },
            },
        ),
        NOW,
    )
    composition.runtime_service.bind_plan(
        _runtime_command(
            "auto-bind",
            session_id,
            RuntimeCommandType.BIND_PLAN,
            NOW,
            {"plan": _plan()},
        ),
        NOW,
    )

    first = dispatch_scheduler_cycle(
        composition.runtime_service,
        [_current_ref(storage, session_id)],
        NOW + timedelta(seconds=10),
    )
    second = dispatch_scheduler_cycle(
        composition.runtime_service,
        [_current_ref(storage, session_id)],
        NOW + timedelta(seconds=20),
    )
    third = dispatch_scheduler_cycle(
        composition.runtime_service,
        [_current_ref(storage, session_id)],
        NOW + timedelta(seconds=35),
    )
    fourth = dispatch_scheduler_cycle(
        composition.runtime_service,
        [_current_ref(storage, session_id)],
        NOW + timedelta(seconds=40),
    )

    assert [result.phase for result in first.dispatched] == [LiveSessionPhase.PLANNED_SHOW]
    assert [result.phase for result in second.dispatched] == [LiveSessionPhase.AFTERTALK]
    assert [result.phase for result in third.dispatched] == [LiveSessionPhase.CLOSING]
    assert [result.phase for result in fourth.dispatched] == [LiveSessionPhase.ENDED]
    assert len(planned_show.calls) == 1
    assert len(aftertalk.calls) == 1
    assert len(closing.calls) == 1
    assert storage.get_v2_session(session_id)["current_phase"] == "ended"
    assert storage.get_v2_session(session_id)["closing_completed"] is True
```

- [ ] **Step 3: Run integration test**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_automation.py::test_scheduler_cycle_auto_advances_planned_aftertalk_closing_to_ended -q
```

Expected:

- Test passes and proves auto advancement is driven by scheduler cycle helpers, not API tick route.

### Task 4: Documentation

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/runtime-application-service.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`

- [ ] **Step 1: Update runtime module status**

Add after Wave 4A:

```markdown
Wave 4B automation phase advancement:
- `SchedulerSessionRef` / `SchedulerCycleResult` 定義一輪 scheduler cycle 的 explicit session input 與結果。
- `build_scheduler_cycle_intents(...)` / `dispatch_scheduler_cycle(...)` 可對已知 active refs 自動發出 planned_show、aftertalk、closing tick。
- Phase transition、duration policy、aftertalk policy 與 runner side effects 仍由 `RuntimeApplicationService` / Runtime Phase 決定；本階段不負責 durable active-session discovery、process lifecycle 或 pause/resume API。
```

- [ ] **Step 2: Update API reference concepts and sources**

Add concepts:

```markdown
- `SchedulerSessionRef`
- `SchedulerCycleResult`
- `build_scheduler_cycle_intents`
- `dispatch_scheduler_cycle`
```

Add sources:

```markdown
- `YouTubeBridgeV2/runtime/automation.py::SchedulerSessionRef`
- `YouTubeBridgeV2/runtime/automation.py::SchedulerCycleResult`
- `YouTubeBridgeV2/runtime/automation.py::build_scheduler_cycle_intents`
- `YouTubeBridgeV2/runtime/automation.py::dispatch_scheduler_cycle`
```

- [ ] **Step 3: Update architecture status**

Add:

```markdown
## Integration Wave 4B 狀態

- [x] Scheduler cycle refs：`SchedulerSessionRef` 讓上層 scheduler 以 explicit refs 提供候選 session，不由 automation module 掃 storage。
- [x] Automatic phase advancement：`dispatch_scheduler_cycle(...)` 可連續推進 `planned_show -> aftertalk -> closing -> ended`，沿用 runtime phase/duration/aftertalk policy。
- [x] Scope boundary：本階段不建立 background process、不做 durable restart discovery、不新增 operator pause/resume controls。
```

### Task 5: Verification and Commit

**Files:**
- Code, tests, docs, plan above.

- [ ] **Step 1: Run focused verification**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_automation.py tests\youtubebridge_v2\test_runtime_phase.py tests\youtubebridge_v2\test_runtime_application_service.py tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py -q
```

Expected:

- Focused Wave 4 suites pass.

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
git add YouTubeBridgeV2\runtime\automation.py tests\youtubebridge_v2\test_runtime_automation.py YouTubeBridgeV2\docs\modules\runtime-application-service.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\implementation-plans\runtime-automation-phase-advancement.md
git diff --cached --check
git commit -m "feat: automate runtime phase advancement"
```

Expected:

- Commit succeeds.

## Self-Review

- Spec coverage: 4B is covered by cycle refs, multi-session dispatch, and a real runtime/fake runner test proving automatic planned_show/aftertalk/closing progression without the API tick endpoint.
- Placeholder scan: no `TBD`, `TODO`, or vague implementation placeholders remain.
- Type consistency: cycle helpers use existing `SchedulerTickIntent`, `AutomationTickPolicy`, `dispatch_scheduler_tick(...)`, and `RuntimeCommandType.TICK`.
