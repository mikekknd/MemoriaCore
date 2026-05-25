# Runtime Phase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkbox syntax for tracking. This plan is intentionally Red-Green-Refactor first.

**Goal:** Implement the YouTubeBridgeV2 runtime phase pure decision layer defined by `docs/modules/runtime-phase.md`.

**Architecture:** Runtime Phase is a pure Python module. It owns enum/dataclass contracts and deterministic transition decisions, but it does not call storage, HTTP, YouTube, MemoriaCore, UI, TTS, or the Legacy director.

**Tech Stack:** Python 3.12, stdlib `dataclasses`, `enum`, `datetime`, `pytest`.

---

## Scope

This plan is for the future runtime implementation. Creating this plan does not create runtime code.

Planned runtime files:

- Source: `YouTubeBridgeV2/runtime/phase.py`
- Test: `tests/youtubebridge_v2/test_runtime_phase.py`

Allowed implementation scope:

- enum/dataclass definitions for the planned symbols listed below
- `evaluate_duration(session_started_at, now, duration_policy)`
- `advance_phase(session_snapshot, now)`
- small private helpers inside `phase.py`

Out of scope:

- storage writes or repositories
- HTTP endpoints, SSE, UI, browser tests
- YouTube polling, MemoriaCore calls, TTS
- LiveEpisodePlan turn execution
- Aftertalk cue generation
- closing finalization implementation
- Legacy no-plan director compatibility

## Planned Symbols

The following symbols are planned contracts. They must not be added to `docs/api-reference-index.md` with `Source` until the implementation exists.

```python
class LiveSessionPhase(str, Enum):
    PLANNED_SHOW = "planned_show"
    AFTERTALK = "aftertalk"
    CLOSING = "closing"
    ENDED = "ended"

class AftertalkPolicy(str, Enum):
    DISABLED = "disabled"
    AUTO = "auto"

class PhaseTransitionReason(str, Enum):
    PLAN_COMPLETED = "plan_completed"
    AFTERTALK_ENABLED = "aftertalk_enabled"
    DURATION_REACHED = "duration_reached"
    MANUAL_CLOSE = "manual_close"
    CLOSING_COMPLETED = "closing_completed"
    INVALID_STATE_RECOVERY = "invalid_state_recovery"
    NO_CHANGE = "no_change"

@dataclass(frozen=True)
class DurationPolicy:
    planned_duration_seconds: int | None
    auto_finalize_on_duration: bool
    aftertalk_requires_remaining_time: bool = True

@dataclass(frozen=True)
class DurationSummary:
    duration_reached: bool
    remaining_time_seconds: int | None
    aftertalk_allowed: bool

@dataclass(frozen=True)
class LiveSessionSnapshot:
    current_phase: LiveSessionPhase | str
    session_started_at: datetime
    plan_completed: bool
    aftertalk_policy: AftertalkPolicy | str
    duration_policy: DurationPolicy
    manual_close_requested: bool = False
    closing_completed: bool = False

@dataclass(frozen=True)
class PhaseTransition:
    current_phase: LiveSessionPhase | str
    next_phase: LiveSessionPhase
    changed: bool
    reason: PhaseTransitionReason
    metadata: dict[str, object]
    next_action: str
```

Public functions:

```python
def evaluate_duration(
    session_started_at: datetime,
    now: datetime,
    duration_policy: DurationPolicy,
) -> DurationSummary:
    """Return duration boundary information without side effects."""

def advance_phase(session_snapshot: LiveSessionSnapshot, now: datetime) -> PhaseTransition:
    """Return the next phase decision without side effects."""
```

## Red Cases

Write all red tests in `tests/youtubebridge_v2/test_runtime_phase.py`. Run them before implementation and confirm they fail because `YouTubeBridgeV2.runtime.phase` does not exist or the planned symbols are missing.

Required red test names:

- `test_planned_show_continues_when_plan_not_completed`
- `test_planned_show_completed_enters_aftertalk_when_auto_policy_has_remaining_time`
- `test_planned_show_completed_enters_closing_when_aftertalk_disabled`
- `test_planned_show_completed_enters_closing_when_no_remaining_time`
- `test_manual_close_from_planned_show_enters_closing`
- `test_manual_close_from_aftertalk_enters_closing`
- `test_aftertalk_continues_before_duration_limit`
- `test_aftertalk_enters_closing_when_duration_reached`
- `test_closing_enters_ended_only_when_closing_completed`
- `test_ended_stays_ended`
- `test_invalid_phase_recovers_to_closing`
- `test_evaluate_duration_reports_positive_zero_negative_and_unbounded`
- `test_phase_decision_is_idempotent_for_same_snapshot`
- `test_phase_decision_has_no_external_side_effects`

Minimum shared fixtures for tests:

```python
from datetime import datetime, timezone

BASE_NOW = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)
STARTED_AT = datetime(2026, 5, 12, 7, 30, tzinfo=timezone.utc)
```

Expected red command:

```powershell
python -m pytest tests/youtubebridge_v2/test_runtime_phase.py -q
```

Expected red result before implementation:

```text
FAILED ... ModuleNotFoundError: No module named 'YouTubeBridgeV2.runtime'
```

If the module path already exists for another reason, the red result must instead be missing symbol or assertion failure tied to the planned contract.

## Green Scope

Implement the minimum code in `YouTubeBridgeV2/runtime/phase.py` to satisfy the red tests.

Transition decisions must match `docs/modules/runtime-phase.md`:

- `planned_show` + plan not completed -> `planned_show`, `no_change`, `run_planned_show`
- `planned_show` + plan completed + `AftertalkPolicy.auto` + aftertalk allowed -> `aftertalk`, `aftertalk_enabled`, `start_aftertalk`
- `planned_show` + plan completed + `AftertalkPolicy.disabled` -> `closing`, `plan_completed`, `start_closing`
- `planned_show` + duration reached -> `closing`, `duration_reached`, `start_closing`
- `aftertalk` before duration limit -> `aftertalk`, `no_change`, `continue_aftertalk`
- `aftertalk` after duration limit -> `closing`, `duration_reached`, `start_closing`
- `closing` + not completed -> `closing`, `no_change`, `start_closing`
- `closing` + completed -> `ended`, `closing_completed`, `mark_ended`
- `ended` -> `ended`, `no_change`, `wait`
- invalid phase -> `closing`, `invalid_state_recovery`, `start_closing`
- manual close from `planned_show` or `aftertalk` overrides normal progression and enters `closing`

Duration behavior:

- finite positive `planned_duration_seconds` computes remaining seconds from `session_started_at` and `now`
- remaining seconds greater than zero means duration not reached
- remaining seconds equal to zero or less means duration reached
- `planned_duration_seconds` of `None`, zero, or negative is unbounded
- unbounded duration does not trigger automatic closing
- when `aftertalk_requires_remaining_time` is true, aftertalk is allowed only when remaining seconds is greater than zero
- when duration is unbounded and `aftertalk_requires_remaining_time` is true, aftertalk is not auto-started by duration policy
- when `auto_finalize_on_duration` is false, duration does not force closing

Metadata requirements:

- include `previous_phase`, `next_phase`, `reason`, `plan_completed`, `aftertalk_policy`, `duration_summary`, `manual_close_requested`, `closing_completed`
- do not include raw prompt, raw Topic Pack, raw FactCard, YouTube raw event, MemoriaCore raw request, or hidden context

Green verification command:

```powershell
python -m pytest tests/youtubebridge_v2/test_runtime_phase.py -q
```

Expected green result:

```text
14 passed
```

## Refactor Boundary

Allowed refactor after green:

- split private duration normalization helpers inside `phase.py`
- add private helpers for phase coercion or metadata construction
- improve enum/dataclass docstrings
- reorder private helper definitions for readability

Forbidden refactor in this plan:

- changing public function names or dataclass field names
- adding storage, HTTP, YouTube, MemoriaCore, UI, TTS, or Legacy director dependencies
- changing the lifecycle away from `planned_show -> aftertalk -> closing -> ended`
- widening scope into LiveEpisodePlan runner or Aftertalk cue generation

Run the green verification command again after refactor.

## Adapter Strategy

Runtime Phase has no adapter dependency.

Tests must verify this by using pure inputs only:

- no monkeypatch of HTTP clients
- no temporary database
- no FastAPI test client
- no YouTube or MemoriaCore fake client
- no filesystem writes other than pytest cache/temp behavior

The side-effect test should assert that `advance_phase` only returns a `PhaseTransition` and does not require any injected external dependency.

## Docs Sync

After runtime code exists:

- update `docs/api-reference-index.md` entries from conceptual contracts to actual Source values for `YouTubeBridgeV2/runtime/phase.py`
- keep `docs/modules/runtime-phase.md` unchanged unless implementation reveals a contract mismatch
- update `docs/architecture-index.md` only if lifecycle, module boundary, or checklist state changes

Do not add Source values during this docs-only planning step.

## Execution Steps

### Task 1: Red Tests

**Files:**

- Create: `tests/youtubebridge_v2/test_runtime_phase.py`

- [ ] Write the failing tests listed in Red Cases.
- [ ] Run `python -m pytest tests/youtubebridge_v2/test_runtime_phase.py -q`.
- [ ] Confirm failure is caused by missing `YouTubeBridgeV2.runtime.phase` or missing planned symbols.

### Task 2: Minimal Runtime Implementation

**Files:**

- Create: `YouTubeBridgeV2/runtime/__init__.py`
- Create: `YouTubeBridgeV2/runtime/phase.py`

- [ ] Add planned enums and dataclasses.
- [ ] Implement `evaluate_duration(...)`.
- [ ] Implement `advance_phase(...)`.
- [ ] Run `python -m pytest tests/youtubebridge_v2/test_runtime_phase.py -q`.
- [ ] Confirm all runtime phase tests pass.

### Task 3: Refactor and Docs Sync

**Files:**

- Modify: `YouTubeBridgeV2/runtime/phase.py`
- Modify if needed: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] Refactor only within the allowed boundary.
- [ ] Re-run `python -m pytest tests/youtubebridge_v2/test_runtime_phase.py -q`.
- [ ] Add Source values to `docs/api-reference-index.md` only after symbols exist.
- [ ] Confirm no Legacy `YouTubeBridge/` file was modified.

## Acceptance Criteria

- Runtime phase tests pass.
- Runtime Phase has no external side effects.
- API reference Source values are added only after implementation exists.
- No old `YouTubeBridge/` runtime file is changed.
- `docs/modules/runtime-phase.md` and implementation behavior stay aligned.
