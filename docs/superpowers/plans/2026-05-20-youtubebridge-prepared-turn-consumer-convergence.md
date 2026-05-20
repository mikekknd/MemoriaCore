# YouTubeBridge Prepared Turn Consumer Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish converging prepared-turn consumption so `YouTubeBridge/turn_pipeline.py` is the stable Module for claim, present, complete, injected marking, and follow-up prefetch eligibility, while director and closing only provide runtime Adapter effects.

**Architecture:** Keep the external seam at `consume_prepared_turn(adapter, payload, options)`. Increase Depth by moving follow-up eligibility into the Module Interface and by moving the duplicated director/closing Adapter implementations into one runtime Adapter file. Do not open Research Gate, external context, Studio, or phase-status refactors in this plan.

**Tech Stack:** Python 3.12+, asyncio, existing YouTubeBridge runtime mixins, existing `BridgeStorage`, pytest.

---

## Current Verified State

- `YouTubeBridge/turn_pipeline.py` already owns the main consumption Implementation: policy lookup, status claim, start broadcast, optional follow-up scheduling, presentation, audience injected marking, completion metadata, completion broadcast, and refusal reasons.
- `YouTubeBridge/engine_director_runtime.py` still owns `_DirectorPreparedTurnAdapter` and caller-side follow-up skip calculation for audience gaps.
- `YouTubeBridge/engine_closing.py` still owns `_ClosingPreparedTurnAdapter` and closing-specific pre-consume validators.
- Targeted sweep passed after the known Windows `.pyTestTemp\basetemp` ACL cleanup:
  - `python -m pytest YouTubeBridge\tests\test_turn_pipeline.py YouTubeBridge\tests\test_director_prefetch_chain.py YouTubeBridge\tests\test_director_audience_preprocessing.py YouTubeBridge\tests\test_bridge_engine_closing.py YouTubeBridge\tests\test_server_route_split.py -q`
  - Result: `156 passed`
  - `python -m compileall YouTubeBridge\turn_pipeline.py YouTubeBridge\engine_director_runtime.py YouTubeBridge\engine_closing.py`
  - Result: pass
  - `git diff --check`
  - Result: pass

---

## Architecture Findings

1. **Follow-up eligibility still leaks out of the Module**
   - **Files:** `YouTubeBridge/turn_pipeline.py`, `YouTubeBridge/engine_director_runtime.py`
   - **Problem:** `consume_prepared_turn()` performs follow-up scheduling, but `_present_ready_audience_gap_turn()` still computes `prepared_turn_followup_skip_reason()` before crossing the seam. The caller must know which facts block follow-up: missing decision, missing base state, runtime stop, graceful closing, and in-flight prefetch. That makes the Interface shallower than it should be.
   - **Solution:** add a `PreparedTurnFollowupGate` value to `turn_pipeline.py`. The caller passes raw runtime facts; `consume_prepared_turn()` decides the skip reason and returns it on `PreparedTurnConsumeResult`.
   - **Benefits:** better Locality for follow-up rules and better Leverage for tests, because one `test_turn_pipeline.py` table can lock skip reasons across planned turns and audience gaps.

2. **The two concrete Adapters duplicate almost all runtime effects**
   - **Files:** `YouTubeBridge/engine_director_runtime.py`, `YouTubeBridge/engine_closing.py`
   - **Problem:** `_DirectorPreparedTurnAdapter` and `_ClosingPreparedTurnAdapter` both implement storage lookup, prepared result lookup, status claim, broadcast, presentation, visible filtering, item counting, audience injected marking, and completion. Only follow-up scheduling and the closing before-present callback vary. Two Adapters make the seam real, but duplicating the shared Implementation reduces Locality.
   - **Solution:** move the concrete Adapter classes into `YouTubeBridge/engine_prepared_turn_adapters.py`. Put shared methods in `PreparedTurnRuntimeAdapter`, then keep `DirectorPreparedTurnAdapter` and `ClosingPreparedTurnAdapter` as small concrete Adapters.
   - **Benefits:** future claim/complete/broadcast changes touch one Adapter Module instead of two mixins. The director and closing mixins become callers of a small Interface rather than hosts for repeated Adapter code.

3. **Caller-specific policy strings should not grow further**
   - **Files:** `YouTubeBridge/turn_pipeline.py`, `YouTubeBridge/engine_closing.py`, `YouTubeBridge/engine_director_runtime.py`
   - **Problem:** callers still pass `completion_metadata_key`, `started_event_type`, `completed_event_type`, and `expected_dedicated_closing`. Some variation is legitimate, but adding more literals at callers would make the Interface shallow again.
   - **Solution:** in this plan, do not change these strings yet. Instead, add contract tests that enumerate the allowed call profiles. If another profile is needed later, promote these literals into a small `PreparedTurnConsumeProfile` in `turn_pipeline.py`.
   - **Benefits:** avoids a premature abstraction while still placing a guardrail around the Interface.

---

## File Structure

- Modify: `YouTubeBridge/turn_pipeline.py`
  - Add `PreparedTurnFollowupGate`.
  - Add `followup_skip_reason` to `PreparedTurnConsumeResult`.
  - Let `consume_prepared_turn()` compute follow-up eligibility from `PreparedTurnFollowupGate` when provided.
- Create: `YouTubeBridge/engine_prepared_turn_adapters.py`
  - New runtime Adapter Module.
  - Hosts `PreparedTurnRuntimeAdapter`, `DirectorPreparedTurnAdapter`, and `ClosingPreparedTurnAdapter`.
- Modify: `YouTubeBridge/engine_director_runtime.py`
  - Import `PreparedTurnFollowupGate`.
  - Replace local follow-up skip computation with the returned `consume_result.followup_skip_reason`.
  - Replace `_DirectorPreparedTurnAdapter` class with import from `engine_prepared_turn_adapters`.
- Modify: `YouTubeBridge/engine_closing.py`
  - Replace `_ClosingPreparedTurnAdapter` class with import from `engine_prepared_turn_adapters`.
- Modify: `YouTubeBridge/tests/test_turn_pipeline.py`
  - Add direct follow-up gate tests.
- Modify: `YouTubeBridge/tests/test_director_prefetch_chain.py`
  - Keep skip diagnostic coverage green after moving skip ownership.
- Modify: `YouTubeBridge/tests/test_director_audience_preprocessing.py`
  - Keep audience-gap chain ordering coverage green.
- Modify: `YouTubeBridge/tests/test_bridge_engine_closing.py`
  - Keep closing drain and dedicated closing consume coverage green.

---

### Task 1: Pin Follow-Up Gate Ownership In `turn_pipeline.py`

**Files:**
- Modify: `YouTubeBridge/tests/test_turn_pipeline.py`
- Modify: `YouTubeBridge/turn_pipeline.py`

- [ ] **Step 1: Write failing tests for follow-up gate decisions**

Add `PreparedTurnFollowupGate` to the existing import list in `YouTubeBridge/tests/test_turn_pipeline.py`:

```python
from turn_pipeline import (
    PreparedTurnPolicy,
    PreparedTurnConsumeOptions,
    PreparedTurnFollowupGate,
    PreparedTurnPayload,
    consume_prepared_turn,
    prepared_turn_policy_for_interaction,
)
```

Append these tests after `test_consume_prepared_turn_schedules_followup_before_presenting`:

```python
def test_consume_prepared_turn_reports_prefetch_in_flight_followup_skip():
    interaction = {
        "job_id": "prefetch-skip-flight",
        "source": "director_prefetch",
        "status": "prefetched",
        "metadata": {"decision": {"action": "continue_topic"}},
    }
    payload = _payload(interaction)
    adapter = FakePreparedTurnAdapter(interaction, payload.prepared_results)

    result = asyncio.run(
        consume_prepared_turn(
            adapter,
            payload,
            PreparedTurnConsumeOptions(
                session_id="live-a",
                followup_gate=PreparedTurnFollowupGate(
                    requested=True,
                    runtime_stopping=False,
                    graceful_closing=False,
                    prefetch_in_flight=True,
                ),
            ),
        )
    )

    assert result.consumed is True
    assert result.after_memoria_task is None
    assert result.followup_skip_reason == "prefetch_in_flight"
    assert adapter.followup_calls == []
    assert "schedule:True" not in adapter.events


def test_consume_prepared_turn_reports_missing_base_state_followup_skip():
    interaction = {
        "job_id": "prefetch-skip-base",
        "source": "director_prefetch",
        "status": "prefetched",
        "metadata": {"decision": {"action": "continue_topic"}},
    }
    payload = _payload(interaction, base_state={})
    adapter = FakePreparedTurnAdapter(interaction, payload.prepared_results)

    result = asyncio.run(
        consume_prepared_turn(
            adapter,
            payload,
            PreparedTurnConsumeOptions(
                session_id="live-a",
                followup_gate=PreparedTurnFollowupGate(
                    requested=True,
                    runtime_stopping=False,
                    graceful_closing=False,
                    prefetch_in_flight=False,
                ),
            ),
        )
    )

    assert result.consumed is True
    assert result.after_memoria_task is None
    assert result.followup_skip_reason == "missing_base_state"
    assert adapter.followup_calls == []
```

- [ ] **Step 2: Run tests to verify the new Interface is missing**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_turn_pipeline.py::test_consume_prepared_turn_reports_prefetch_in_flight_followup_skip YouTubeBridge\tests\test_turn_pipeline.py::test_consume_prepared_turn_reports_missing_base_state_followup_skip -q
```

Expected before implementation: FAIL with an import error for `PreparedTurnFollowupGate` or an unexpected keyword error for `followup_gate`.

- [ ] **Step 3: Add `PreparedTurnFollowupGate` and result skip metadata**

In `YouTubeBridge/turn_pipeline.py`, update the dataclasses near the top to this shape:

```python
@dataclass(frozen=True)
class PreparedTurnFollowupGate:
    requested: bool = False
    runtime_stopping: bool = False
    graceful_closing: bool = False
    prefetch_in_flight: bool = False


@dataclass(frozen=True)
class PreparedTurnConsumeOptions:
    session_id: str
    allow_followup_prefetch: bool = False
    followup_allow_audience: bool = False
    followup_gate: PreparedTurnFollowupGate | None = None
    expected_dedicated_closing: bool = False
    require_complete_prepared_items: bool = True
    completion_metadata_key: str = "prepared_turn_consumed"
    started_event_type: str = "interaction_started"
    completed_event_type: str = "interaction_completed"


@dataclass(frozen=True)
class PreparedTurnConsumeResult:
    consumed: bool
    reason: str
    payload: PreparedTurnPayload
    interaction: dict[str, Any] | None
    after_memoria_task: Any = None
    played_item_count: int = 0
    marked_injected: int = 0
    followup_skip_reason: str = "not_requested"
```

- [ ] **Step 4: Move follow-up skip decision into the consumer**

Add this helper above `consume_prepared_turn()`:

```python
def _followup_skip_reason_for_consume(
    *,
    options: PreparedTurnConsumeOptions,
    payload: PreparedTurnPayload,
    policy: PreparedTurnPolicy,
) -> str:
    if options.followup_gate is not None:
        requested = options.followup_gate.requested
        runtime_stopping = options.followup_gate.runtime_stopping
        graceful_closing = options.followup_gate.graceful_closing
        prefetch_in_flight = options.followup_gate.prefetch_in_flight
    else:
        requested = options.allow_followup_prefetch
        runtime_stopping = False
        graceful_closing = False
        prefetch_in_flight = False
    reason = prepared_turn_followup_skip_reason(
        requested=requested,
        has_decision=bool(payload.decision),
        has_base_state=bool(payload.base_state),
        runtime_stopping=runtime_stopping,
        graceful_closing=graceful_closing,
        prefetch_in_flight=prefetch_in_flight,
    )
    if reason:
        return reason
    if not (policy.may_chain or policy.mark_audience_events_injected):
        return "policy_disallows_followup"
    return ""
```

Replace the existing `can_schedule_followup = ...` block inside `consume_prepared_turn()` with:

```python
    followup_skip_reason = _followup_skip_reason_for_consume(
        options=options,
        payload=claimed_payload,
        policy=policy,
    )
    if not followup_skip_reason:
        followup_allow_audience = (
            False
            if policy.mark_audience_events_injected
            else options.followup_allow_audience
        )
        after_memoria_task = await adapter.schedule_followup_prefetch(
            claimed_payload,
            allow_audience=followup_allow_audience,
        )
```

In the final `PreparedTurnConsumeResult(...)`, include:

```python
        followup_skip_reason=followup_skip_reason,
```

- [ ] **Step 5: Run the focused tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_turn_pipeline.py::test_consume_prepared_turn_reports_prefetch_in_flight_followup_skip YouTubeBridge\tests\test_turn_pipeline.py::test_consume_prepared_turn_reports_missing_base_state_followup_skip -q
```

Expected: PASS.

---

### Task 2: Make Director Follow-Up Callers Pass Facts, Not Rules

**Files:**
- Modify: `YouTubeBridge/engine_director_runtime.py`
- Modify: `YouTubeBridge/tests/test_director_prefetch_chain.py`
- Modify: `YouTubeBridge/tests/test_director_audience_preprocessing.py`

- [ ] **Step 1: Update imports**

In `YouTubeBridge/engine_director_runtime.py`, change the `turn_pipeline` import to:

```python
from turn_pipeline import (
    PreparedTurnConsumeOptions,
    PreparedTurnFollowupGate,
    PreparedTurnPayload,
    consume_prepared_turn,
)
```

- [ ] **Step 2: Replace audience-gap skip calculation**

In `_present_ready_audience_gap_turn()`, remove the caller-side `prepared_turn_followup_skip_reason(...)` block and pass this option object into `consume_prepared_turn()`:

```python
            PreparedTurnConsumeOptions(
                session_id=runtime.session_id,
                followup_gate=PreparedTurnFollowupGate(
                    requested=chain_next_prefetch,
                    runtime_stopping=bool(runtime.stop_after_current_turn),
                    graceful_closing=bool(runtime.graceful_closing_requested),
                    prefetch_in_flight=runtime.director_prefetch_in_flight > 0,
                ),
                followup_allow_audience=False,
                expected_dedicated_closing=False,
                completion_metadata_key="audience_prepare_consumed",
                started_event_type="director_audience_gap_presenting",
                completed_event_type="director_audience_gap_presented",
            ),
```

Immediately after a consumed result, log the skip diagnostic from the returned result:

```python
        if (
            consume_result.followup_skip_reason
            and consume_result.followup_skip_reason != "not_requested"
        ):
            _director_timing_log(
                "audience_gap_followup_prefetch_skipped",
                session_id=runtime.session_id,
                job_id=interaction.get("job_id"),
                reason=consume_result.followup_skip_reason,
            )
```

- [ ] **Step 3: Replace normal prefetch consume eligibility**

In `_consume_prefetched_episode_turn()`, replace `allow_followup_prefetch = (...)` with:

```python
        followup_gate = PreparedTurnFollowupGate(
            requested=True,
            runtime_stopping=bool(runtime.stop_after_current_turn),
            graceful_closing=bool(runtime.graceful_closing_requested),
            prefetch_in_flight=False,
        )
```

Then pass this into the options:

```python
            PreparedTurnConsumeOptions(
                session_id=runtime.session_id,
                followup_gate=followup_gate,
                followup_allow_audience=(decision_mode == "planned_turn"),
                expected_dedicated_closing=False,
                completion_metadata_key="prefetch_consumed",
                started_event_type="interaction_started",
                completed_event_type="interaction_completed",
            ),
```

- [ ] **Step 4: Run director-focused tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_director_prefetch_chain.py::test_prepared_turn_followup_skip_reason_reports_prefetch_in_flight YouTubeBridge\tests\test_director_audience_preprocessing.py::test_audience_gap_after_consumed_planned_prefetch_starts_next_planned_prefetch -q
```

Expected: PASS.

---

### Task 3: Move Runtime Adapter Implementation Out Of Mixins

**Files:**
- Create: `YouTubeBridge/engine_prepared_turn_adapters.py`
- Modify: `YouTubeBridge/engine_director_runtime.py`
- Modify: `YouTubeBridge/engine_closing.py`

- [ ] **Step 1: Create the shared runtime Adapter Module**

Create `YouTubeBridge/engine_prepared_turn_adapters.py` with this content:

```python
"""Runtime adapters for prepared turn consumption."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Callable

from bridge_runtime import LiveRuntime
from turn_pipeline import PreparedTurnPayload


class PreparedTurnRuntimeAdapter:
    def __init__(
        self,
        manager: Any,
        runtime: LiveRuntime,
        session: dict[str, Any],
        *,
        extra_completion_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.manager = manager
        self.runtime = runtime
        self.session = session
        self.extra_completion_metadata = dict(extra_completion_metadata or {})

    def get_interaction(self, job_id: str) -> dict[str, Any] | None:
        return self.manager.storage.get_interaction(job_id)

    def prepared_results_for_interaction(
        self,
        interaction: dict[str, Any],
        *,
        require_complete: bool,
    ) -> list[dict[str, Any]]:
        return self.manager._prepared_results_for_interaction(
            self.runtime.session_id,
            interaction,
            require_complete=require_complete,
        )

    def claim_interaction(self, job_id: str, expected_status: str) -> dict[str, Any] | None:
        if hasattr(self.manager.storage, "update_interaction_if_status"):
            return self.manager.storage.update_interaction_if_status(
                job_id,
                expected_status,
                status="presenting",
            )
        return self.manager.storage.update_interaction(job_id, status="presenting")

    async def broadcast(self, payload: dict[str, Any]) -> None:
        await self.manager._broadcast(self.runtime.session_id, payload)

    async def present_prepared_results(
        self,
        prepared_results: list[dict[str, Any]],
        *,
        source: str,
        interaction_job_id: str,
    ) -> Any:
        return await self.manager.present_prepared_stream_results(
            self.runtime.session_id,
            prepared_results,
            source=source,
            interaction_job_id=interaction_job_id,
        )

    def visible_prepared_results(
        self,
        prepared_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return self.manager._visible_prepared_results(self.session, prepared_results)

    def prepared_result_item_count(self, prepared_results: list[dict[str, Any]]) -> int:
        return self.manager._prepared_result_item_count(prepared_results)

    def mark_audience_events_injected(self, interaction: dict[str, Any]) -> int:
        event_ids: list[int] = []
        for raw_event_id in interaction.get("event_ids") or []:
            try:
                event_id = int(raw_event_id)
            except (TypeError, ValueError):
                continue
            if event_id > 0:
                event_ids.append(event_id)
        return (
            self.manager.storage.mark_events_injected(self.runtime.session_id, event_ids)
            if event_ids
            else 0
        )

    def complete_interaction(
        self,
        job_id: str,
        *,
        reply_text: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        if self.extra_completion_metadata:
            metadata = {**metadata, **self.extra_completion_metadata}
        metadata = self.completion_metadata(metadata)
        if hasattr(self.manager.storage, "update_interaction_if_status"):
            return self.manager.storage.update_interaction_if_status(
                job_id,
                "presenting",
                status="completed",
                reply_text=reply_text,
                completed_at=datetime.now().isoformat(),
                metadata=metadata,
            )
        return self.manager.storage.update_interaction(
            job_id,
            status="completed",
            reply_text=reply_text,
            completed_at=datetime.now().isoformat(),
            metadata=metadata,
        )

    def completion_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return metadata

    async def schedule_followup_prefetch(
        self,
        payload: PreparedTurnPayload,
        *,
        allow_audience: bool,
    ) -> Any:
        return None


class DirectorPreparedTurnAdapter(PreparedTurnRuntimeAdapter):
    def __init__(
        self,
        manager: Any,
        runtime: LiveRuntime,
        session: dict[str, Any],
        *,
        delay_before_followup: bool = True,
        extra_completion_metadata: dict[str, Any] | None = None,
        timing_log: Callable[..., None] | None = None,
    ) -> None:
        super().__init__(
            manager,
            runtime,
            session,
            extra_completion_metadata=extra_completion_metadata,
        )
        self.delay_before_followup = delay_before_followup
        self.timing_log = timing_log
        self.after_memoria_task: Any = None

    def completion_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        if metadata.get("audience_prepare_consumed") is True:
            metadata = dict(metadata)
            metadata["audience_gap_presented"] = int(metadata.get("played_item_count") or 0) > 0
        return metadata

    async def schedule_followup_prefetch(
        self,
        payload: PreparedTurnPayload,
        *,
        allow_audience: bool,
    ) -> Any:
        if self.after_memoria_task is not None:
            return self.after_memoria_task
        metadata = (
            payload.interaction.get("metadata")
            if isinstance(payload.interaction.get("metadata"), dict)
            else {}
        )
        chained_session = dict(self.session)
        main_session_id = str(
            metadata.get("main_memoria_session_id")
            or self.session.get("target_memoria_session_id")
            or ""
        )
        if main_session_id:
            chained_session["target_memoria_session_id"] = main_session_id
        if self.timing_log is not None:
            self.timing_log(
                "prefetch_chain_scheduled",
                session_id=self.runtime.session_id,
                job_id=payload.interaction.get("job_id"),
                source=payload.interaction.get("source"),
            )
        self.runtime.director_prefetch_in_flight += 1

        async def run_next_prefetch():
            try:
                if self.delay_before_followup:
                    await self.manager._yield_before_presentation_chain_prefetch()
                return await self.manager._prefetch_next_presentation_turn(
                    self.runtime,
                    chained_session,
                    payload.base_state,
                    payload.decision,
                    allow_audience=allow_audience,
                )
            finally:
                self.runtime.director_prefetch_in_flight = max(
                    0,
                    self.runtime.director_prefetch_in_flight - 1,
                )

        self.after_memoria_task = asyncio.create_task(run_next_prefetch())
        return self.after_memoria_task


class ClosingPreparedTurnAdapter(PreparedTurnRuntimeAdapter):
    def __init__(
        self,
        manager: Any,
        runtime: LiveRuntime,
        session: dict[str, Any],
        *,
        extra_completion_metadata: dict[str, Any] | None = None,
        before_present_callback=None,
    ) -> None:
        super().__init__(
            manager,
            runtime,
            session,
            extra_completion_metadata=extra_completion_metadata,
        )
        self.before_present_callback = before_present_callback
        self.callback_task: asyncio.Task | None = None

    async def present_prepared_results(
        self,
        prepared_results: list[dict[str, Any]],
        *,
        source: str,
        interaction_job_id: str,
    ) -> Any:
        if self.before_present_callback is not None and self.callback_task is None:
            maybe_callback_result = self.before_present_callback()
            if asyncio.iscoroutine(maybe_callback_result):
                self.callback_task = asyncio.create_task(maybe_callback_result)
        return await super().present_prepared_results(
            prepared_results,
            source=source,
            interaction_job_id=interaction_job_id,
        )
```

- [ ] **Step 2: Replace director-local Adapter**

In `YouTubeBridge/engine_director_runtime.py`, add:

```python
from engine_prepared_turn_adapters import DirectorPreparedTurnAdapter
```

Delete the local `_DirectorPreparedTurnAdapter` class. Replace constructor calls:

```python
adapter = DirectorPreparedTurnAdapter(
    self,
    runtime,
    session,
    delay_before_followup=False,
    extra_completion_metadata=(
        {
            "base_state": payload_base_state,
            "audience_prepare_base_state_source": base_state_source,
        }
        if payload_base_state
        else {}
    ),
    timing_log=_director_timing_log,
)
```

and:

```python
adapter = DirectorPreparedTurnAdapter(
    self,
    runtime,
    session,
    timing_log=_director_timing_log,
)
```

- [ ] **Step 3: Replace closing-local Adapter**

In `YouTubeBridge/engine_closing.py`, add:

```python
from engine_prepared_turn_adapters import ClosingPreparedTurnAdapter
```

Delete the local `_ClosingPreparedTurnAdapter` class. Replace constructor calls:

```python
adapter = ClosingPreparedTurnAdapter(
    self,
    runtime,
    session,
    extra_completion_metadata={"prefetch_consumed_during_closing_drain": True},
)
```

```python
adapter = ClosingPreparedTurnAdapter(
    self,
    runtime,
    session,
    before_present_callback=start_after_memoria_callback,
)
```

```python
adapter = ClosingPreparedTurnAdapter(self, runtime, session)
```

- [ ] **Step 4: Run Adapter integration tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_director_prefetch_chain.py YouTubeBridge\tests\test_director_audience_preprocessing.py YouTubeBridge\tests\test_bridge_engine_closing.py -q
```

Expected: PASS.

---

### Task 4: Add Contract Tests For Allowed Consume Profiles

**Files:**
- Modify: `YouTubeBridge/tests/test_turn_pipeline.py`
- Modify: `YouTubeBridge/tests/test_bridge_engine_closing.py`

- [ ] **Step 1: Add a policy guard for dedicated closing**

Append this test to `YouTubeBridge/tests/test_turn_pipeline.py`:

```python
def test_consume_prepared_turn_refuses_non_dedicated_when_dedicated_expected():
    interaction = {
        "job_id": "closing-policy-guard",
        "source": "director_prefetch",
        "status": "prefetched",
        "metadata": {"decision": {"action": "continue_topic"}},
    }
    payload = _payload(interaction)
    adapter = FakePreparedTurnAdapter(interaction, payload.prepared_results)

    result = asyncio.run(
        consume_prepared_turn(
            adapter,
            payload,
            PreparedTurnConsumeOptions(
                session_id="live-a",
                expected_dedicated_closing=True,
                completion_metadata_key="final_closing_prefetch_consumed",
            ),
        )
    )

    assert result.consumed is False
    assert result.reason == "dedicated_closing_expected"
    assert adapter.events == []
```

- [ ] **Step 2: Add a closing-drain consume profile assertion**

In `YouTubeBridge/tests/test_bridge_engine_closing.py`, extend `test_closing_drain_uses_turn_pipeline_policy_for_ready_prefetch` with:

```python
        assert consume_calls[0].started_event_type == "interaction_started"
        assert consume_calls[0].completed_event_type == "interaction_completed"
        assert consume_calls[0].require_complete_prepared_items is True
```

- [ ] **Step 3: Run profile tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_turn_pipeline.py::test_consume_prepared_turn_refuses_non_dedicated_when_dedicated_expected YouTubeBridge\tests\test_bridge_engine_closing.py::test_closing_drain_uses_turn_pipeline_policy_for_ready_prefetch -q
```

Expected: PASS.

---

### Task 5: Final Targeted Sweep

**Files:**
- Verify only, no file edits.

- [ ] **Step 1: Run prepared-turn targeted tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_turn_pipeline.py YouTubeBridge\tests\test_director_prefetch_chain.py YouTubeBridge\tests\test_director_audience_preprocessing.py YouTubeBridge\tests\test_bridge_engine_closing.py YouTubeBridge\tests\test_server_route_split.py -q
```

Expected: PASS. If this hits Windows `.pyTestTemp\basetemp` ACL errors, run:

```powershell
scripts\cleanup_pytest_temp.bat
```

Then rerun the exact same pytest command.

- [ ] **Step 2: Run syntax and whitespace checks**

Run:

```powershell
python -m compileall YouTubeBridge\turn_pipeline.py YouTubeBridge\engine_director_runtime.py YouTubeBridge\engine_closing.py YouTubeBridge\engine_prepared_turn_adapters.py
```

Expected: no compile errors.

Run:

```powershell
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 3: Check scope**

Run:

```powershell
git status --short
```

Expected changed files are limited to:

```text
M YouTubeBridge/turn_pipeline.py
A YouTubeBridge/engine_prepared_turn_adapters.py
M YouTubeBridge/engine_director_runtime.py
M YouTubeBridge/engine_closing.py
M YouTubeBridge/tests/test_turn_pipeline.py
M YouTubeBridge/tests/test_director_prefetch_chain.py
M YouTubeBridge/tests/test_director_audience_preprocessing.py
M YouTubeBridge/tests/test_bridge_engine_closing.py
```

No Research Gate, external context, Studio, server phase, or route files should change in this plan.

---

## Self-Review

- **Spec coverage:** The plan only addresses the prepared-turn consumer line. It does not open Research Gate, external context, Studio, or phase-status refactors.
- **Placeholder scan:** No task contains deferred implementation wording. Each code-changing task includes exact code or exact replacement snippets.
- **Type consistency:** `PreparedTurnFollowupGate`, `PreparedTurnConsumeOptions.followup_gate`, and `PreparedTurnConsumeResult.followup_skip_reason` are introduced before any caller uses them.
- **Depth check:** The deletion test improves after this plan. Deleting `turn_pipeline.py` would re-spread follow-up eligibility and consume rules. Deleting `engine_prepared_turn_adapters.py` would re-spread runtime Adapter effects across director and closing mixins.
