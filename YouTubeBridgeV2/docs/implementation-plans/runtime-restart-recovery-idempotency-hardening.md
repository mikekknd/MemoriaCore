# Runtime Restart Recovery Idempotency Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 roadmap `4C`：讓 runtime automation 在 process restart 後可從 durable storage 找回需要恢復的 sessions，並用 state-aware recovery command ids 避免重複 side effects 或卡住下一個恢復階段。

**Architecture:** 在 storage boundary 增加 durable recoverable-session listing；在 runtime automation module 增加 recovery cycle contract。Recovery cycle 與 4B tick cycle 一樣不掃 storage、不啟動 background process，只接受上層傳入的 session records/refs；command id 使用 phase + plan/closing/manual-close state marker，而不是 wall-clock timestamp，讓同一恢復狀態可 idempotent replay，狀態變化後可產生下一個 recovery command。

**Tech Stack:** Python 3.13、dataclasses、pytest、SQLite via `core/storage/` only、existing `RuntimeApplicationService.recover_session(...)`。

---

## Scope

Roadmap item：`4C：restart/recovery 與 idempotency hardening`

完成條件：

- StorageManager durable backend 可列出非 ended 的 V2 sessions，供 restart recovery bootstrap 使用。
- RuntimeStoragePort expose `list_recoverable_sessions(limit=100)`，不回傳 hidden/raw payload。
- Automation module 新增 public recovery cycle contract：
  - `SchedulerRecoverySessionRef`
  - `SchedulerRecoveryIntent`
  - `SchedulerRecoveryCycleResult`
  - `build_scheduler_recovery_intents(sessions, now, policy=None)`
  - `dispatch_scheduler_recovery_cycle(runtime_service, sessions, now, policy=None)`
- Recovery command id 不使用 timestamp；同一 state marker repeat 會 replay command result，不重複 runner side effects。
- State marker 包含 `current_phase`、`plan_completed`、`manual_close_requested`、`closing_completed`，讓 planned-show plan completion 或 closing finalization 後可產生下一個 recovery command。
- Real storage integration 覆蓋 restart 後 listing -> recovery cycle -> idempotent repeat。

不包含：

- 不建立 background scheduler process、thread、async task 或 service lifecycle。
- 不新增 operator pause/resume API/UI；4D 再做。
- 不變更 roadmap checkbox。

## File Structure

- Modify: `core/storage/youtube_bridge_v2.py`
  - 新增 `list_v2_sessions_for_recovery(limit=100)` durable query。
- Modify: `YouTubeBridgeV2/storage/runtime_store.py`
  - 新增 `list_recoverable_sessions(limit=100)` port delegation。
- Modify: `YouTubeBridgeV2/runtime/automation.py`
  - 新增 recovery refs/intents/cycle helpers。
- Modify tests:
  - `tests/youtubebridge_v2/test_storage_manager_durable_backend.py`
  - `tests/youtubebridge_v2/test_runtime_automation.py`
  - `tests/youtubebridge_v2/test_real_storage_integration.py`
- Modify docs:
  - `YouTubeBridgeV2/docs/modules/runtime-application-service.md`
  - `YouTubeBridgeV2/docs/api-reference-index.md`
  - `YouTubeBridgeV2/docs/architecture-index.md`

---

### Task 1: Red Durable Recovery Listing Tests

**Files:**
- Modify: `tests/youtubebridge_v2/test_storage_manager_durable_backend.py`

- [ ] **Step 1: Add storage recovery listing test**

Append near session/storage tests:

```python
def test_list_v2_sessions_for_recovery_returns_active_sessions_only(tmp_path):
    storage = _storage(tmp_path)
    storage.create_v2_session(_session_record(session_id="planned-session"))
    storage.create_v2_session(
        _session_record(
            session_id="closing-session",
            current_phase="closing",
            plan_completed=True,
        )
    )
    storage.create_v2_session(
        _session_record(
            session_id="ended-session",
            current_phase="ended",
            plan_completed=True,
            closing_completed=True,
        )
    )

    sessions = storage.list_v2_sessions_for_recovery(limit=10)

    assert [session["session_id"] for session in sessions] == [
        "planned-session",
        "closing-session",
    ]
    assert all(session["current_phase"] != "ended" for session in sessions)
    _assert_no_private_payload(sessions)
```

- [ ] **Step 2: Run red storage test**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_storage_manager_durable_backend.py::test_list_v2_sessions_for_recovery_returns_active_sessions_only -q
```

Expected:

- Fails because `StorageManager.list_v2_sessions_for_recovery` does not exist.

### Task 2: Implement Durable Recovery Listing

**Files:**
- Modify: `core/storage/youtube_bridge_v2.py`
- Modify: `YouTubeBridgeV2/storage/runtime_store.py`

- [ ] **Step 1: Add StorageManager method**

Add to `YouTubeBridgeV2RepositoryMixin` after `get_v2_session(...)`:

```python
def list_v2_sessions_for_recovery(self, limit: int = 100) -> list[dict[str, object]]:
    safe_limit = max(1, min(int(limit), 500))
    with closing(self._init_youtube_bridge_v2_db()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT session_id, current_phase, session_started_at, plan_completed,
                   aftertalk_policy, duration_policy_json, manual_close_requested,
                   closing_completed, public_summary_json, metadata_json, plan_id,
                   manual_close_json, ended_at, created_at, updated_at
            FROM yb2_sessions
            WHERE current_phase != 'ended'
            ORDER BY updated_at ASC, created_at ASC
            LIMIT ?
            """,
            (safe_limit,),
        )
        rows = cursor.fetchall()
    return [_session_from_row(row) for row in rows]
```

- [ ] **Step 2: Add RuntimeStoragePort method**

Add to `RuntimeStoragePort`:

```python
def list_recoverable_sessions(self, limit: int = 100) -> list[dict[str, object]]:
    """List durable non-ended session records for restart recovery bootstrap."""

    if not hasattr(self._storage_manager, "list_v2_sessions_for_recovery"):
        raise RuntimeStorageContractError(
            "storage manager missing list_v2_sessions_for_recovery"
        )
    return [
        _sanitize_public_payload(_object_to_dict(session))
        for session in self._storage_manager.list_v2_sessions_for_recovery(limit)
    ]
```

- [ ] **Step 3: Run storage tests green**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_storage_manager_durable_backend.py::test_list_v2_sessions_for_recovery_returns_active_sessions_only tests\youtubebridge_v2\test_real_storage_integration.py::test_real_storage_restart_recovery_reads_existing_snapshot -q
```

Expected:

- Both tests pass.

### Task 3: Red Recovery Cycle Automation Tests

**Files:**
- Modify: `tests/youtubebridge_v2/test_runtime_automation.py`

- [ ] **Step 1: Add imports**

Add to existing automation imports:

```python
SchedulerRecoverySessionRef,
build_scheduler_recovery_intents,
dispatch_scheduler_recovery_cycle,
```

- [ ] **Step 2: Extend fake runtime service**

Add to `FakeRuntimeService`:

```python
def recover_session(self, command, now):
    self.calls.append((command, now))
    return {
        "status": "ok",
        "session_id": command.session_id,
        "phase": "closing",
        "events": [],
        "errors": [],
        "correlation_id": f"runtime-{command.command_id}",
    }
```

- [ ] **Step 3: Add recovery intent marker test**

Append:

```python
def test_scheduler_recovery_intents_use_state_markers_not_wall_clock():
    intents = build_scheduler_recovery_intents(
        [
            SchedulerRecoverySessionRef(
                "session-1",
                current_phase=LiveSessionPhase.PLANNED_SHOW,
                plan_completed=False,
            ),
            SchedulerRecoverySessionRef(
                "session-1",
                current_phase=LiveSessionPhase.PLANNED_SHOW,
                plan_completed=True,
            ),
        ],
        NOW,
        AutomationTickPolicy(),
    )

    assert [intent.command_id for intent in intents] == [
        "scheduler:recover:session-1:planned_show:plan_open:auto:closing_open",
        "scheduler:recover:session-1:planned_show:plan_done:auto:closing_open",
    ]
    assert all(intent.command_type is RuntimeCommandType.RECOVER for intent in intents)
```

- [ ] **Step 4: Add recovery cycle dispatch/skip test**

Append:

```python
def test_scheduler_recovery_cycle_calls_recover_session_and_skips_ended():
    service = FakeRuntimeService()
    result = dispatch_scheduler_recovery_cycle(
        service,
        [
            {
                "session_id": "closing",
                "current_phase": "closing",
                "plan_completed": True,
                "manual_close_requested": True,
                "closing_completed": False,
            },
            {
                "session_id": "ended",
                "current_phase": "ended",
                "plan_completed": True,
                "closing_completed": True,
            },
        ],
        NOW,
    )

    assert [command.command_type for command, _now in service.calls] == [
        RuntimeCommandType.RECOVER
    ]
    assert service.calls[0][0].command_id == (
        "scheduler:recover:closing:closing:plan_done:manual_close:closing_open"
    )
    assert [intent.session_id for intent in result.skipped] == ["ended"]
    assert result.skipped[0].skip_reason == "session_ended"
```

- [ ] **Step 5: Run red automation tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_automation.py::test_scheduler_recovery_intents_use_state_markers_not_wall_clock tests\youtubebridge_v2\test_runtime_automation.py::test_scheduler_recovery_cycle_calls_recover_session_and_skips_ended -q
```

Expected:

- Fails because recovery types/functions are missing.

### Task 4: Implement Recovery Cycle Contract

**Files:**
- Modify: `YouTubeBridgeV2/runtime/automation.py`

- [ ] **Step 1: Add recovery dataclasses**

Add after scheduler cycle dataclasses:

```python
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
```

- [ ] **Step 2: Add recovery builder/dispatcher**

Add:

```python
def build_scheduler_recovery_intents(
    sessions: object,
    now: datetime,
    policy: AutomationTickPolicy | None = None,
) -> tuple[SchedulerRecoveryIntent, ...]:
    recovery_policy = policy or AutomationTickPolicy()
    return tuple(
        _recovery_intent(_recovery_ref(session), _utc_datetime(now), recovery_policy)
        for session in _iter_sessions(sessions)
    )


def dispatch_scheduler_recovery_cycle(
    runtime_service: object,
    sessions: object,
    now: datetime,
    policy: AutomationTickPolicy | None = None,
) -> SchedulerRecoveryCycleResult:
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
```

- [ ] **Step 3: Add recovery helpers**

Add:

```python
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


def _recovery_ref(value: object) -> SchedulerRecoverySessionRef:
    if isinstance(value, SchedulerRecoverySessionRef):
        return value
    data = value if isinstance(value, dict) else vars(value)
    return SchedulerRecoverySessionRef(
        session_id=str(data["session_id"]),
        current_phase=data.get("current_phase"),
        plan_completed=bool(data.get("plan_completed", False)),
        manual_close_requested=bool(data.get("manual_close_requested", False)),
        closing_completed=bool(data.get("closing_completed", False)),
        automation_enabled=bool(data.get("automation_enabled", True)),
        automation_paused=bool(data.get("automation_paused", False)),
    )


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
```

Update `__all__`.

- [ ] **Step 4: Run automation tests green**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_automation.py -q
```

Expected:

- Automation tests pass.

### Task 5: Real Restart Recovery Integration Tests

**Files:**
- Modify: `tests/youtubebridge_v2/test_real_storage_integration.py`

- [ ] **Step 1: Add imports**

Add:

```python
from YouTubeBridgeV2.runtime.automation import dispatch_scheduler_recovery_cycle
```

- [ ] **Step 2: Add closing recovery/idempotency test**

Append:

```python
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
```

- [ ] **Step 3: Add planned-show state marker progression test**

Append:

```python
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
```

- [ ] **Step 4: Run integration tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_real_storage_integration.py::test_recovery_cycle_after_restart_resumes_closing_then_marks_ended_idempotently tests\youtubebridge_v2\test_real_storage_integration.py::test_recovery_cycle_uses_new_state_marker_after_plan_completion -q
```

Expected:

- Both tests pass.

### Task 6: Documentation

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/runtime-application-service.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`

- [ ] **Step 1: Update runtime module status**

Add after Wave 4B:

```markdown
Wave 4C restart/recovery hardening:
- `RuntimeStoragePort.list_recoverable_sessions(...)` delegates to durable `StorageManager.list_v2_sessions_for_recovery(...)` so restart bootstrap can recover non-ended sessions without importing storage into automation.
- `SchedulerRecoverySessionRef` / `SchedulerRecoveryIntent` / `SchedulerRecoveryCycleResult` define restart recovery command dispatch through `recover_session(...)`.
- Recovery command ids use phase + plan/manual-close/closing state markers instead of timestamps, so same state replays idempotently while changed state can advance to the next recovery command.
```

- [ ] **Step 2: Update API reference concepts/sources**

Add recovery concepts:

```markdown
- `SchedulerRecoverySessionRef`
- `SchedulerRecoveryIntent`
- `SchedulerRecoveryCycleResult`
- `build_scheduler_recovery_intents`
- `dispatch_scheduler_recovery_cycle`
- `dispatch_scheduler_recovery`
- `RuntimeStoragePort.list_recoverable_sessions`
```

Add sources:

```markdown
- `YouTubeBridgeV2/runtime/automation.py::SchedulerRecoverySessionRef`
- `YouTubeBridgeV2/runtime/automation.py::SchedulerRecoveryIntent`
- `YouTubeBridgeV2/runtime/automation.py::SchedulerRecoveryCycleResult`
- `YouTubeBridgeV2/runtime/automation.py::build_scheduler_recovery_intents`
- `YouTubeBridgeV2/runtime/automation.py::dispatch_scheduler_recovery_cycle`
- `YouTubeBridgeV2/runtime/automation.py::dispatch_scheduler_recovery`
- `YouTubeBridgeV2/storage/runtime_store.py::RuntimeStoragePort.list_recoverable_sessions`
- `core/storage/youtube_bridge_v2.py::YouTubeBridgeV2RepositoryMixin.list_v2_sessions_for_recovery`
```

- [ ] **Step 3: Update architecture status**

Add:

```markdown
## Integration Wave 4C 狀態

- [x] Durable recovery listing：`StorageManager.list_v2_sessions_for_recovery(...)` / `RuntimeStoragePort.list_recoverable_sessions(...)` 可列出 restart bootstrap 需要的 non-ended sessions。
- [x] Recovery cycle：`dispatch_scheduler_recovery_cycle(...)` 以 `RuntimeCommandType.RECOVER` 委派 `RuntimeApplicationService.recover_session(...)`。
- [x] Idempotency hardening：recovery command id 使用 phase + state markers，不使用 wall-clock timestamp，避免同一恢復狀態重複 side effects。
- [x] Scope boundary：本階段不建立 background process、不新增 pause/resume controls。
```

### Task 7: Verification and Commit

**Files:**
- Code, tests, docs, plan above.

- [ ] **Step 1: Run focused verification**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_automation.py tests\youtubebridge_v2\test_real_storage_integration.py tests\youtubebridge_v2\test_storage_manager_durable_backend.py tests\youtubebridge_v2\test_runtime_application_service.py -q
```

Expected:

- Focused recovery/storage suites pass.

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
git add core\storage\youtube_bridge_v2.py YouTubeBridgeV2\storage\runtime_store.py YouTubeBridgeV2\runtime\automation.py tests\youtubebridge_v2\test_storage_manager_durable_backend.py tests\youtubebridge_v2\test_runtime_automation.py tests\youtubebridge_v2\test_real_storage_integration.py YouTubeBridgeV2\docs\modules\runtime-application-service.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\implementation-plans\runtime-restart-recovery-idempotency-hardening.md
git diff --cached --check
git commit -m "feat: harden runtime restart recovery"
```

Expected:

- Commit succeeds.

## Self-Review

- Spec coverage: 4C restart/recovery is covered by durable recovery listing and recovery-cycle dispatch; idempotency is covered by state-marker command ids and repeated recovery integration tests.
- Placeholder scan: no `TBD`, `TODO`, or vague implementation placeholders remain.
- Type consistency: recovery cycle uses existing `RuntimeCommandType.RECOVER` and delegates to existing `recover_session(command, now)`.
