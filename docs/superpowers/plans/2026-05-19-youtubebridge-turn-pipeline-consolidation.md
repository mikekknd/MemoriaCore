# YouTubeBridge Turn Pipeline Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate YouTubeBridge live turn preparation, prefetch consumption, presentation playback, opening handoff, and closing handoff into one shared turn pipeline so latency fixes do not need to be patched separately in planned, audience, final closing, and Super Chat closing paths.

**Architecture:** Add a deep `turn_pipeline` Module that owns prepared-turn policy and replayable status transitions behind a small Interface. Existing manager mixins remain the outer Adapter layer at first, but planned prefetch, audience prepare, closing drain, final closing, and closing Super Chat consume the same policy and playback helper. Opening kickoff is then routed through the same after-turn chain path as normal planned turns.

**Tech Stack:** Python 3.12, asyncio, existing `BridgeStorage`, existing YouTubeBridge manager mixins, pytest.

---

## Architecture Deepening Candidates

1. **Prepared Turn Pipeline Module**
   - **Files:** `YouTubeBridge/turn_pipeline.py`, `YouTubeBridge/engine_director_runtime.py`, `YouTubeBridge/engine_closing.py`
   - **Problem:** The same statuses are interpreted in several places: `director_prefetch/prefetched`, `director_audience_prepare/prepared`, `final_closing/prefetched`, and `closing_super_chat_thanks/prefetched`. Each place claims, presents, completes, cancels, and broadcasts slightly differently.
   - **Solution:** Create a deep Module whose Interface answers: expected status, presentation source, whether chaining is allowed, whether audience events should be marked injected, and whether the prepared turn is a dedicated closing turn.
   - **Benefits:** Better locality for status rules; tests can hit the Interface directly instead of constructing full live sessions for every small policy variation.

2. **Opening Handoff Adapter**
   - **Files:** `YouTubeBridge/engine_director_runtime.py`, `YouTubeBridge/tests/test_director_prefetch_chain.py`
   - **Problem:** `_director_kickoff()` is a special path for opening and post-opening. It enters `_after_main_turn_sequence()`, but state setup and prefetch callback setup are duplicated with normal episode turns.
   - **Solution:** Extract a kickoff Adapter that calls the same "send and run after-turn chain" helper used by normal planned turns.
   - **Benefits:** Opening to first planned turn gets the same prefetch and audience ordering semantics as the rest of the show.

3. **Studio Presentation Player Locality**
   - **Files:** `YouTubeBridge/static/ui/studio.js`
   - **Problem:** Presentation playback now has one supported browser surface: Studio. Older plans assumed a second browser playback adapter would keep sharing queue, preload, play, debug, and ACK logic, but that surface has been removed.
   - **Solution:** Keep Presentation Player behavior local to Studio unless a second supported browser surface is intentionally introduced later. Future playback timing fixes should improve the Studio implementation directly.
   - **Benefits:** Playback timing fixes keep locality without rebuilding a legacy adapter target.

This plan implements candidates 1 and 2 first. Candidate 3 is now a boundary note for future Studio-only playback work, not a shared browser-adapter extraction task.

---

## File Structure

- Create: `YouTubeBridge/turn_pipeline.py`
  - Owns prepared-turn policy classification and small pure helpers.
  - No storage writes in the first task; this keeps the Module deep without creating a risky migration.
- Modify: `YouTubeBridge/engine_director_runtime.py`
  - Uses `turn_pipeline` for `_consume_prefetched_episode_turn()`.
  - Extracts opening kickoff send/chain flow into one helper shared by episode planned kickoff and plain opening kickoff.
- Modify: `YouTubeBridge/engine_closing.py`
  - Uses the same prepared-turn policy for closing drain, final closing prefetch consume, and closing Super Chat prefetch consume.
  - Keeps final-closing target validation in `engine_closing.py` because it is closing-specific.
- Create: `YouTubeBridge/tests/test_turn_pipeline.py`
  - Unit tests for policy classification.
- Modify: `YouTubeBridge/tests/test_director_prefetch_chain.py`
  - Regression tests for opening handoff using the common chain.
- Modify: `YouTubeBridge/tests/test_bridge_engine_closing.py`
  - Regression tests proving closing drain/final/SC paths use the shared policy and keep no-chain semantics.

---

### Task 1: Add Prepared Turn Policy Module

**Files:**
- Create: `YouTubeBridge/turn_pipeline.py`
- Create: `YouTubeBridge/tests/test_turn_pipeline.py`

- [ ] **Step 1: Write the failing policy tests**

Add this file:

```python
from turn_pipeline import prepared_turn_policy_for_interaction


def _interaction(source, status, action=""):
    metadata = {}
    if action:
        metadata["decision"] = {"action": action}
    return {
        "job_id": "job-1",
        "source": source,
        "status": status,
        "metadata": metadata,
    }


def test_director_prefetch_policy_allows_chain_for_normal_turn():
    policy = prepared_turn_policy_for_interaction(
        _interaction("director_prefetch", "prefetched", "planned_turn")
    )

    assert policy.expected_status == "prefetched"
    assert policy.presentation_source == "director"
    assert policy.may_chain is True
    assert policy.mark_audience_events_injected is False
    assert policy.dedicated_closing is False


def test_audience_prepare_policy_marks_events_without_general_prefetch_chain():
    policy = prepared_turn_policy_for_interaction(
        _interaction("director_audience_prepare", "prepared", "audience_gap_prepare")
    )

    assert policy.expected_status == "prepared"
    assert policy.presentation_source == "director_audience_gap"
    assert policy.may_chain is False
    assert policy.mark_audience_events_injected is True
    assert policy.dedicated_closing is False


def test_final_closing_policy_is_prefetched_without_chain():
    policy = prepared_turn_policy_for_interaction(
        _interaction("director_prefetch", "prefetched", "final_closing")
    )

    assert policy.expected_status == "prefetched"
    assert policy.presentation_source == "director_closing"
    assert policy.may_chain is False
    assert policy.dedicated_closing is True


def test_closing_super_chat_policy_is_prefetched_without_chain():
    policy = prepared_turn_policy_for_interaction(
        _interaction("director_prefetch", "prefetched", "closing_super_chat_thanks")
    )

    assert policy.expected_status == "prefetched"
    assert policy.presentation_source == "director_super_chat"
    assert policy.may_chain is False
    assert policy.dedicated_closing is True


def test_unknown_source_has_no_policy():
    assert prepared_turn_policy_for_interaction(
        _interaction("director", "completed", "planned_turn")
    ) is None
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_turn_pipeline.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'turn_pipeline'`.

- [ ] **Step 3: Implement the policy Module**

Create `YouTubeBridge/turn_pipeline.py`:

```python
"""Prepared live turn policy helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PreparedTurnPolicy:
    expected_status: str
    presentation_source: str
    may_chain: bool
    mark_audience_events_injected: bool
    dedicated_closing: bool = False


def _decision_action(interaction: dict[str, Any]) -> str:
    metadata = interaction.get("metadata") if isinstance(interaction.get("metadata"), dict) else {}
    decision = metadata.get("decision") if isinstance(metadata.get("decision"), dict) else {}
    return str(decision.get("action") or "")


def prepared_turn_policy_for_interaction(interaction: dict[str, Any] | None) -> PreparedTurnPolicy | None:
    if not isinstance(interaction, dict):
        return None
    source = str(interaction.get("source") or "")
    action = _decision_action(interaction)
    if source == "director_audience_prepare":
        return PreparedTurnPolicy(
            expected_status="prepared",
            presentation_source="director_audience_gap",
            may_chain=False,
            mark_audience_events_injected=True,
        )
    if source != "director_prefetch":
        return None
    if action == "final_closing":
        return PreparedTurnPolicy(
            expected_status="prefetched",
            presentation_source="director_closing",
            may_chain=False,
            mark_audience_events_injected=False,
            dedicated_closing=True,
        )
    if action == "closing_super_chat_thanks":
        return PreparedTurnPolicy(
            expected_status="prefetched",
            presentation_source="director_super_chat",
            may_chain=False,
            mark_audience_events_injected=False,
            dedicated_closing=True,
        )
    return PreparedTurnPolicy(
        expected_status="prefetched",
        presentation_source="director",
        may_chain=True,
        mark_audience_events_injected=False,
    )
```

- [ ] **Step 4: Verify the focused test passes**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_turn_pipeline.py -q
```

Expected: `5 passed`.

- [ ] **Step 5: Commit**

Run:

```powershell
git add YouTubeBridge/turn_pipeline.py YouTubeBridge/tests/test_turn_pipeline.py
git commit -m "refactor: add turn pipeline policy module"
```

---

### Task 2: Extract Shared Prepared-Turn Consume Helper

**Files:**
- Modify: `YouTubeBridge/engine_director_runtime.py`
- Test: `YouTubeBridge/tests/test_director_prefetch_chain.py`

- [ ] **Step 1: Write a regression test for policy-based general consumption**

Append this test to `YouTubeBridge/tests/test_director_prefetch_chain.py` near other `_consume_prefetched_episode_turn` tests:

```python
async def test_consume_prefetched_episode_turn_uses_turn_pipeline_policy(monkeypatch):
    manager, storage = _manager_with_session(presentation_enabled=True)
    runtime = manager._runtimes["live-a"]
    session = storage.get_session("live-a")
    interaction = storage.create_interaction({
        "session_id": "live-a",
        "source": "director_prefetch",
        "status": "prefetched",
        "content": "prefetched planned turn",
        "metadata": {
            "decision": {"action": "planned_turn", "episode_plan": {"mode": "planned_turn"}},
            "base_state": {"status": "running"},
        },
    })
    item = storage.create_presentation_item({
        "session_id": "live-a",
        "interaction_job_id": interaction["job_id"],
        "sequence": 1,
        "text": "prefetched planned turn",
        "status": "ready",
        "metadata": {"source": "director_prefetch"},
    })

    presented_sources = []

    async def fake_present_prepared_stream_results(session_id, prepared_results, *, source, interaction_job_id):
        presented_sources.append(source)
        storage.update_presentation_item(item["item_id"], status="played", acked_at="now")

    monkeypatch.setattr(manager, "present_prepared_stream_results", fake_present_prepared_stream_results)
    monkeypatch.setattr(manager, "_prefetch_next_presentation_turn", AsyncMock(return_value=None))

    result = await manager._consume_prefetched_episode_turn(runtime, session, {
        "interaction": interaction,
        "decision": {"action": "planned_turn", "episode_plan": {"mode": "planned_turn"}},
        "base_state": {"status": "running"},
        "memoria_result": {"reply": "prefetched planned turn"},
        "prepared_results": [{
            "message": {"content": "prefetched planned turn"},
            "items": [item],
        }],
    })

    assert result and result["discarded"] is False
    assert presented_sources == ["director"]
    assert storage.get_interaction(interaction["job_id"])["status"] == "completed"
```

- [ ] **Step 2: Run the focused test and verify current behavior**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_director_prefetch_chain.py::test_consume_prefetched_episode_turn_uses_turn_pipeline_policy -q
```

Expected before implementation: FAIL because `turn_pipeline` is not imported and `_consume_prefetched_episode_turn()` does not use the policy Module.

- [ ] **Step 3: Import and use the policy in `_consume_prefetched_episode_turn()`**

In `YouTubeBridge/engine_director_runtime.py`, add:

```python
from turn_pipeline import prepared_turn_policy_for_interaction
```

Inside `_consume_prefetched_episode_turn()`, replace the source-specific status block:

```python
interaction_source = str(current_interaction.get("source") or interaction.get("source") or "")
is_audience_prepare = interaction_source == "director_audience_prepare"
expected_ready_status = "prepared" if is_audience_prepare else "prefetched"
```

with:

```python
policy = prepared_turn_policy_for_interaction(current_interaction)
if policy is None or policy.dedicated_closing:
    _director_timing_log(
        "prefetch_consume_refused",
        session_id=runtime.session_id,
        job_id=job_id,
        status=current_interaction.get("status"),
        reason="unsupported_prepared_turn_policy",
    )
    return None
expected_ready_status = policy.expected_status
is_audience_prepare = policy.mark_audience_events_injected
```

Replace:

```python
should_chain_prefetch = (
    bool(decision)
    and bool(base_state)
    and not runtime.stop_after_current_turn
    and not runtime.graceful_closing_requested
)
```

with:

```python
should_chain_prefetch = (
    policy.may_chain
    and bool(decision)
    and bool(base_state)
    and not runtime.stop_after_current_turn
    and not runtime.graceful_closing_requested
)
```

Replace:

```python
presentation_source = "director_audience_gap" if is_audience_prepare else "director"
```

with:

```python
presentation_source = policy.presentation_source
```

- [ ] **Step 4: Verify the focused test passes**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_director_prefetch_chain.py::test_consume_prefetched_episode_turn_uses_turn_pipeline_policy -q
```

Expected: `1 passed`.

- [ ] **Step 5: Run the director prefetch regression file**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_director_prefetch_chain.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

Run:

```powershell
git add YouTubeBridge/engine_director_runtime.py YouTubeBridge/tests/test_director_prefetch_chain.py
git commit -m "refactor: consume planned turns through turn pipeline policy"
```

---

### Task 3: Route Closing Drain Through the Same Prepared-Turn Policy

**Files:**
- Modify: `YouTubeBridge/engine_closing.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_closing.py`

- [ ] **Step 1: Write a closing drain regression test**

Append this test to `YouTubeBridge/tests/test_bridge_engine_closing.py` near existing drain tests:

```python
async def test_closing_drain_uses_turn_pipeline_policy_for_ready_prefetch(monkeypatch):
    manager, storage = _manager_with_session(presentation_enabled=True)
    runtime = manager._runtimes["live-a"]
    session = storage.get_session("live-a")
    interaction = storage.create_interaction({
        "session_id": "live-a",
        "source": "director_prefetch",
        "status": "prefetched",
        "content": "ready planned during drain",
        "metadata": {"decision": {"action": "planned_turn", "episode_plan": {"mode": "planned_turn"}}},
    })
    item = storage.create_presentation_item({
        "session_id": "live-a",
        "interaction_job_id": interaction["job_id"],
        "sequence": 1,
        "text": "ready planned during drain",
        "status": "ready",
        "metadata": {"source": "director_prefetch"},
    })
    presented_sources = []

    async def fake_present_prepared_stream_results(session_id, prepared_results, *, source, interaction_job_id):
        presented_sources.append(source)
        storage.update_presentation_item(item["item_id"], status="played", acked_at="now")

    monkeypatch.setattr(manager, "present_prepared_stream_results", fake_present_prepared_stream_results)

    result = await manager._drain_live_session_before_closing(runtime, session, timeout_seconds=1)

    assert result["status"] == "drained"
    assert presented_sources == ["director"]
    assert storage.get_interaction(interaction["job_id"])["status"] == "completed"
```

- [ ] **Step 2: Run the focused test**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_closing.py::test_closing_drain_uses_turn_pipeline_policy_for_ready_prefetch -q
```

Expected before implementation: FAIL because closing drain still derives expected status and presentation source locally.

- [ ] **Step 3: Use policy in `_present_ready_prefetch_for_closing_drain()`**

In `YouTubeBridge/engine_closing.py`, add:

```python
from turn_pipeline import prepared_turn_policy_for_interaction
```

Inside `_present_ready_prefetch_for_closing_drain()`, replace local source validation:

```python
interaction_source = str(interaction.get("source") or "")
if interaction_source not in {"director_prefetch", "director_audience_prepare"}:
    continue
expected_status = "prepared" if interaction_source == "director_audience_prepare" else "prefetched"
```

with:

```python
policy = prepared_turn_policy_for_interaction(interaction)
if policy is None or policy.dedicated_closing:
    continue
expected_status = policy.expected_status
```

Replace:

```python
is_audience_prepare = interaction_source == "director_audience_prepare"
...
source="director_audience_gap" if is_audience_prepare else "director",
```

with:

```python
is_audience_prepare = policy.mark_audience_events_injected
...
source=policy.presentation_source,
```

- [ ] **Step 4: Verify focused and closing tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_closing.py::test_closing_drain_uses_turn_pipeline_policy_for_ready_prefetch -q
python -m pytest YouTubeBridge\tests\test_bridge_engine_closing.py -q
```

Expected: focused test passes, then the full closing file passes.

- [ ] **Step 5: Commit**

Run:

```powershell
git add YouTubeBridge/engine_closing.py YouTubeBridge/tests/test_bridge_engine_closing.py
git commit -m "refactor: use turn pipeline policy during closing drain"
```

---

### Task 4: Consolidate Dedicated Closing Prefetch Consume Semantics

**Files:**
- Modify: `YouTubeBridge/engine_closing.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_closing.py`

- [ ] **Step 1: Add regression tests for no-chain dedicated closing policy**

Add these two tests:

```python
async def test_final_closing_prefetch_consume_uses_dedicated_no_chain_policy(monkeypatch):
    manager, storage = _manager_with_session(presentation_enabled=True)
    runtime = manager._runtimes["live-a"]
    session = storage.get_session("live-a")
    interaction = storage.create_interaction({
        "session_id": "live-a",
        "source": "director_prefetch",
        "status": "prefetched",
        "content": "final closing",
        "metadata": {"decision": {"action": "final_closing"}},
    })
    item = storage.create_presentation_item({
        "session_id": "live-a",
        "interaction_job_id": interaction["job_id"],
        "sequence": 1,
        "text": "final closing",
        "status": "ready",
        "metadata": {"source": "director_prefetch"},
    })
    monkeypatch.setattr(manager, "_prefetch_next_presentation_turn", AsyncMock(side_effect=AssertionError("final closing must not chain")))

    prefetch = {
        "task": _completed_task({
            "interaction": interaction,
            "prepared_results": [{"message": {"content": "final closing"}, "items": [item]}],
        }),
        "visible_target_signature": manager._final_closing_visible_target_signature(None),
        "sc_state_signature": manager._final_closing_sc_state_signature(None),
    }

    result = await manager._consume_final_closing_prefetch(
        runtime,
        session,
        prefetch,
        closing_super_chat_thanks=None,
    )

    assert result and result["status"] == "completed"
    assert storage.get_interaction(interaction["job_id"])["status"] == "completed"


async def test_closing_super_chat_prefetch_consume_uses_dedicated_no_chain_policy(monkeypatch):
    manager, storage = _manager_with_session(presentation_enabled=True)
    runtime = manager._runtimes["live-a"]
    session = storage.get_session("live-a")
    interaction = storage.create_interaction({
        "session_id": "live-a",
        "source": "director_prefetch",
        "status": "prefetched",
        "content": "thanks",
        "metadata": {"decision": {"action": "closing_super_chat_thanks"}},
    })
    item = storage.create_presentation_item({
        "session_id": "live-a",
        "interaction_job_id": interaction["job_id"],
        "sequence": 1,
        "text": "thanks",
        "status": "ready",
        "metadata": {"source": "director_prefetch"},
    })
    monkeypatch.setattr(manager, "_prefetch_next_presentation_turn", AsyncMock(side_effect=AssertionError("SC closing must not chain")))

    prefetch = {
        "task": _completed_task({
            "interaction": interaction,
            "prepared_results": [{"message": {"content": "thanks"}, "items": [item]}],
        }),
        "visible_target_signature": manager._final_closing_visible_target_signature(None),
    }

    result = await manager._consume_closing_super_chat_prefetch(runtime, session, prefetch)

    assert result and result["status"] == "completed"
    assert storage.get_interaction(interaction["job_id"])["status"] == "completed"
```

If `_completed_task` is not already available in the file, add this helper once near other helpers:

```python
def _completed_task(result):
    loop = asyncio.get_running_loop()
    task = loop.create_future()
    task.set_result(result)
    return task
```

- [ ] **Step 2: Run the focused tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_closing.py::test_final_closing_prefetch_consume_uses_dedicated_no_chain_policy YouTubeBridge\tests\test_bridge_engine_closing.py::test_closing_super_chat_prefetch_consume_uses_dedicated_no_chain_policy -q
```

Expected before implementation: FAIL until both consume helpers use the shared dedicated policy.

- [ ] **Step 3: Use policy in final and SC consume helpers**

In `_consume_closing_super_chat_prefetch()` and `_consume_final_closing_prefetch()`, after loading `current`, add:

```python
policy = prepared_turn_policy_for_interaction(current)
if policy is None or not policy.dedicated_closing:
    await self._cancel_closing_super_chat_prefetch(
        runtime,
        prefetch_context,
        reason="closing_super_chat_prefetch_invalid_policy",
    )
    return None
```

For `_consume_final_closing_prefetch()`, use `_cancel_final_closing_prefetch()` and reason `"final_closing_prefetch_invalid_policy"`.

Replace hardcoded expected status checks:

```python
if str(current.get("status") or "") != "prefetched":
```

with:

```python
if str(current.get("status") or "") != policy.expected_status:
```

Replace presentation source arguments with:

```python
source=policy.presentation_source,
```

- [ ] **Step 4: Verify closing tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_closing.py -q
```

Expected: all closing tests pass.

- [ ] **Step 5: Commit**

Run:

```powershell
git add YouTubeBridge/engine_closing.py YouTubeBridge/tests/test_bridge_engine_closing.py
git commit -m "refactor: consume closing prefetches through turn pipeline policy"
```

---

### Task 5: Unify Opening Kickoff With Normal After-Turn Chain

**Files:**
- Modify: `YouTubeBridge/engine_director_runtime.py`
- Test: `YouTubeBridge/tests/test_director_prefetch_chain.py`

- [ ] **Step 1: Write opening handoff regression**

Add this test:

```python
async def test_opening_kickoff_handoff_uses_common_after_turn_chain(monkeypatch):
    manager, storage = _manager_with_session(presentation_enabled=True)
    runtime = manager._runtimes["live-a"]
    session = storage.get_session("live-a")
    state = storage.get_director_state("live-a")
    storage.update_director_state("live-a", metadata={"episode_plan": {}})

    after_memoria_task = asyncio.create_task(asyncio.sleep(0, result=None))
    seen = {}

    async def fake_send_director_turn(sent_session, sent_state, decision, **kwargs):
        seen["decision_action"] = decision["action"]
        callback = kwargs.get("after_memoria_callback")
        assert callback is not None
        callback_result = await callback({"session_id": sent_session["target_memoria_session_id"]})
        assert callback_result is None
        return {"interaction": {"job_id": "opening-job"}, "after_memoria_task": after_memoria_task}

    async def fake_after_main_turn_sequence(seen_runtime, seen_session, seen_state, prefetch_task, **kwargs):
        seen["after_sequence_called"] = True
        seen["prefetch_task"] = prefetch_task
        seen["reset_opening_metadata"] = kwargs.get("reset_opening_metadata")
        return seen_state

    monkeypatch.setattr(manager, "_send_director_turn", fake_send_director_turn)
    monkeypatch.setattr(manager, "_after_main_turn_sequence", fake_after_main_turn_sequence)
    monkeypatch.setattr(manager, "_prefetch_next_presentation_turn", AsyncMock(return_value=None))
    monkeypatch.setattr(manager, "_episode_plan_next_decision", lambda _session, _state: None)

    await manager._director_kickoff(runtime)

    assert seen["decision_action"] == "opening"
    assert seen["after_sequence_called"] is True
    assert seen["prefetch_task"] is after_memoria_task
    assert seen["reset_opening_metadata"] is False
```

- [ ] **Step 2: Run the focused test**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_director_prefetch_chain.py::test_opening_kickoff_handoff_uses_common_after_turn_chain -q
```

Expected before implementation: FAIL if kickoff still wires the callback or after-turn state in a different shape than the common helper.

- [ ] **Step 3: Extract a kickoff send-and-chain helper**

In `YouTubeBridge/engine_director_runtime.py`, add this helper near `_director_kickoff()`:

```python
    async def _send_initial_turn_and_run_chain(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        state: dict[str, Any],
        decision: dict[str, Any],
        *,
        status: str,
        reset_opening_metadata: bool,
    ) -> dict[str, Any]:
        turn_state = self.storage.update_director_state(
            runtime.session_id,
            status=status,
            metadata={"last_decision": decision},
        )
        await self._broadcast(runtime.session_id, {"type": "director_state", "director": turn_state})
        prefetch_callback = None
        if self._presentation_enabled(session):
            async def prefetch_callback(memoria_result=None):
                prefetch_session = self._session_with_memoria_result(session, memoria_result)
                return await self._prefetch_next_presentation_turn(
                    runtime,
                    prefetch_session,
                    state,
                    decision,
                    allow_audience=True,
                )
        send_kwargs = {"after_memoria_callback": prefetch_callback} if prefetch_callback else {}
        result = await self._send_director_turn(session, state, decision, **send_kwargs)
        next_state = self.storage.update_director_state(
            runtime.session_id,
            status="running",
            last_director_action_at=datetime.now().isoformat(),
            consecutive_ai_turns=int(state.get("consecutive_ai_turns", 0) or 0) + 1,
            current_topic=str(decision.get("current_topic") or state.get("current_topic") or ""),
            metadata={
                "last_decision": decision,
                "last_result_job_id": result.get("interaction", {}).get("job_id", ""),
                "chat_batches_since_anchor": 0,
                "segment_state": self._segment_state_after_turn(
                    session,
                    state,
                    decision,
                    self._segment_topic_entry_for_session(session),
                ),
            },
        )
        await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
        runtime.audience_preprocess_wake.set()
        await self._after_main_turn_sequence(
            runtime,
            session,
            next_state,
            result.get("after_memoria_task"),
            reset_opening_metadata=reset_opening_metadata,
        )
        return next_state
```

Then replace the presentation-enabled opening branch and the episode planned kickoff branch with calls to this helper. Keep the non-presentation legacy branch intact until there is a separate test for that mode.

- [ ] **Step 4: Verify focused and chain tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_director_prefetch_chain.py::test_opening_kickoff_handoff_uses_common_after_turn_chain -q
python -m pytest YouTubeBridge\tests\test_director_prefetch_chain.py -q
```

Expected: focused test passes, then the full prefetch chain file passes.

- [ ] **Step 5: Commit**

Run:

```powershell
git add YouTubeBridge/engine_director_runtime.py YouTubeBridge/tests/test_director_prefetch_chain.py
git commit -m "refactor: route opening kickoff through common turn chain"
```

---

### Task 6: Regression Sweep and Diff Hygiene

**Files:**
- Modify only files changed by Tasks 1-5 if fixes are needed.

- [ ] **Step 1: Run policy tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_turn_pipeline.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run prefetch chain tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_director_prefetch_chain.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Run closing tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_closing.py -q
```

Expected: all tests pass.

- [ ] **Step 4: Run audience preprocessing regression**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_director_audience_preprocessing.py::test_audience_gap_prepare_finishes_during_graceful_closing -q
```

Expected: test passes.

- [ ] **Step 5: Run diff check**

Run:

```powershell
git diff --check
```

Expected: exit code 0. CRLF warnings are acceptable in this repo; whitespace errors are not.

- [ ] **Step 6: Final commit**

If Task 6 required small fixes, commit them:

```powershell
git add YouTubeBridge/turn_pipeline.py YouTubeBridge/engine_director_runtime.py YouTubeBridge/engine_closing.py YouTubeBridge/tests/test_turn_pipeline.py YouTubeBridge/tests/test_director_prefetch_chain.py YouTubeBridge/tests/test_bridge_engine_closing.py
git commit -m "test: cover consolidated turn pipeline regressions"
```

If Task 6 required no fixes, do not create an empty commit.

---

## Self-Review

- **Spec coverage:** The plan covers the scattered backend prepared-turn pipeline, opening special path, closing drain, final closing, and closing Super Chat consume paths. It intentionally leaves front-end player consolidation for a later plan because that is a separate Adapter with a different test surface.
- **Placeholder scan:** The plan contains exact file paths, test names, commands, expected outcomes, and concrete snippets. It does not rely on `TBD` or unnamed future work.
- **Type consistency:** The shared type is `PreparedTurnPolicy`; the public helper is `prepared_turn_policy_for_interaction()`. Later tasks use the same names.

Plan complete and saved to `docs/superpowers/plans/2026-05-19-youtubebridge-turn-pipeline-consolidation.md`. Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints.
