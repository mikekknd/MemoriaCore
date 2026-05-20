# YouTubeBridge Prepared Turn Consumer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deepen `YouTubeBridge/turn_pipeline.py` from a prepared-turn policy helper into the single Module that consumes prepared turns, so planned prefetch, audience gap, closing drain, final closing, and Super Chat closing all claim, present, complete, cancel, broadcast, mark injected, and schedule follow-up prefetch with one rule set.

**Architecture:** Keep `bridge_engine.py`, `engine_director_runtime.py`, and `engine_closing.py` as outer Adapters because they own runtime state, storage, broadcast, and TTS/presentation I/O. Move the shared prepared-turn consumption algorithm into `turn_pipeline.py` behind a small Adapter Interface. Fix the audience-gap latency bug by making the pipeline rule "chain after a presented audience gap when no next task owns the slot" instead of the current caller-local `consumed_count == 0` guard.

**Tech Stack:** Python 3.12, asyncio, existing `BridgeStorage`, existing YouTubeBridge manager mixins, pytest.

---

## Architecture Findings

1. **`turn_pipeline.py` is still shallow**
   - **Files:** `YouTubeBridge/turn_pipeline.py`, `YouTubeBridge/engine_director_runtime.py`, `YouTubeBridge/engine_closing.py`
   - **Problem:** `turn_pipeline.py` only decides `expected_status`, `presentation_source`, `may_chain`, `mark_audience_events_injected`, and `dedicated_closing`. The hard parts still live in callers: claiming `prefetched/prepared -> presenting`, presenting prepared items, counting visible output, completing or cancelling interactions, marking audience events injected, broadcasting lifecycle events, and deciding whether to chain the next prefetch.
   - **Deletion test:** deleting `turn_pipeline.py` would force the same policy literals back into multiple callers, so the direction is right. But deleting `_consume_prefetched_episode_turn()`, `_present_ready_audience_gap_turn()`, `_present_ready_prefetch_for_closing_drain()`, `_consume_final_closing_prefetch()`, and `_consume_closing_super_chat_prefetch()` would still leave five separate implementations of the same consumption algorithm. That means the Module is not deep enough yet.
   - **Solution:** add a `consume_prepared_turn()` algorithm to `turn_pipeline.py`. The Module owns the state transition and result rules; director and closing pass an Adapter for storage/broadcast/presentation effects.
   - **Benefits:** locality for prepared-turn behavior; a single test surface for consume/refuse/cancel/chain rules; lower chance that planned turns, audience gaps, and closing get different behavior again.

2. **The current audience-gap-to-planned prefetch bug is a caller-local guard bug**
   - **Files:** `YouTubeBridge/engine_director_runtime.py:2197-2206`, `YouTubeBridge/engine_director_runtime.py:1488-1522`
   - **Problem:** `_after_main_turn_sequence()` currently computes `chain_next_prefetch = prefetch_task is None and consumed_count == 0` before presenting a ready audience gap. This avoids duplicate chains at the start of a sequence, but it also suppresses the needed chain after a planned prefetch has been consumed and the audience gap is presented before the next planned turn. In that case `consumed_count == 1`, so the audience gap can play, but it does not start the next planned prefetch during playback.
   - **Solution:** centralize follow-up scheduling in the prepared-turn consumer. The rule should be: audience gap may start a planned-only follow-up prefetch when no live next task owns the slot, the runtime is not stopping/closing, there is no in-flight director prefetch, and the consumed audience-gap interaction has a decision/base state. It must not depend on `consumed_count == 0`.
   - **Benefits:** fixes the reported gap after a prepared audience response; prevents another "sometimes prefetch, sometimes not" rule from living only inside one caller.

3. **Closing consume paths duplicate normal consume behavior with extra validators**
   - **Files:** `YouTubeBridge/engine_closing.py:387-470`, `YouTubeBridge/engine_closing.py:1435-1623`, `YouTubeBridge/engine_closing.py:1625-1790`
   - **Problem:** closing drain, final closing prefetch, and Super Chat closing prefetch repeat the same claim/present/complete/broadcast shape as director prefetch consume, then add closing-specific stale target and SC state checks around it. Because the shared behavior is duplicated, a bug fix in one path can miss another path.
   - **Solution:** make stale visible target and SC state checks pre-consume validators. Once validators pass, all three closing paths call the same `consume_prepared_turn()` with `allow_followup_prefetch=False` and action-specific completion metadata.
   - **Benefits:** closing stays semantically dedicated, but the playback/ACK/completion/cancellation rules become the same as normal prepared turns.

4. **`bridge_engine.py` is the presentation Adapter, not the pipeline owner**
   - **Files:** `YouTubeBridge/bridge_engine.py:434-643`, `YouTubeBridge/engine_director_runtime.py`, `YouTubeBridge/engine_closing.py`
   - **Problem:** `bridge_engine.py` correctly owns TTS preparation, presentation item ordering, browser ACK waiting, and `chat_message` broadcast. The current friction is not there; it is that callers decide when and how to invoke it.
   - **Solution:** keep `present_prepared_stream_results()` as the Adapter method. The pipeline Module calls it through the Adapter and remains agnostic about TTS/audio/browser details.
   - **Benefits:** no risky rewrite of presentation playback, while prepared-turn lifecycle rules still gain locality.

---

## File Structure

- Modify: `YouTubeBridge/turn_pipeline.py`
  - Keep `PreparedTurnPolicy`.
  - Add `PreparedTurnPayload`, `PreparedTurnConsumeOptions`, `PreparedTurnConsumeResult`, `PreparedTurnConsumeAdapter`, and `consume_prepared_turn()`.
  - Add `prepared_turn_followup_skip_reason()` so follow-up prefetch skip reasons are logged consistently.
- Modify: `YouTubeBridge/engine_director_runtime.py`
  - Convert `_consume_prefetched_episode_turn()` into a thin Adapter call.
  - Convert `_present_ready_audience_gap_turn()` into a thin Adapter call.
  - Replace `consumed_count == 0` as the audience-gap chain guard with "no active next task owns the slot".
  - Keep `_after_main_turn_sequence()` as the sequencing loop for now; do not move the whole loop in this phase.
- Modify: `YouTubeBridge/engine_closing.py`
  - Route `_present_ready_prefetch_for_closing_drain()`, `_consume_final_closing_prefetch()`, and `_consume_closing_super_chat_prefetch()` through `consume_prepared_turn()`.
  - Keep final-closing decision building, target signatures, and SC-state signatures in closing.
- Modify: `YouTubeBridge/tests/test_turn_pipeline.py`
  - Add unit tests for the consumer using a fake Adapter.
- Modify: `YouTubeBridge/tests/test_director_audience_preprocessing.py`
  - Add the fail-first regression for "planned prefetch -> audience gap -> next planned prefetch".
- Modify: `YouTubeBridge/tests/test_director_prefetch_chain.py`
  - Keep existing chain/reuse tests green and add one test for follow-up skip diagnostics.
- Modify: `YouTubeBridge/tests/test_bridge_engine_closing.py`
  - Change the existing sentinel-policy tests so they prove the dedicated closing paths use `consume_prepared_turn()`.

---

### Task 1: Pin the Missing Audience-Gap Follow-Up Prefetch

**Files:**
- Modify: `YouTubeBridge/tests/test_director_audience_preprocessing.py`

- [ ] **Step 1: Add a failing regression test**

Append this test near the existing `_after_main_turn_sequence` audience ordering tests:

```python
@pytest.mark.asyncio
async def test_audience_gap_after_consumed_planned_prefetch_starts_next_planned_prefetch(monkeypatch):
    with temp_storage() as storage:
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        manager._runtimes["live-a"] = runtime
        session = {
            "session_id": "live-a",
            "presentation_enabled": True,
            "target_memoria_session_id": "mem-main",
        }
        state = {
            "session_id": "live-a",
            "status": "running",
            "metadata": {},
        }

        planned_interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "priority": 40,
            "status": "prefetched",
            "memoria_session_id": "mem-main",
            "metadata": {
                "decision": {
                    "action": "continue_topic",
                    "episode_plan": {"mode": "planned_turn", "turn_id": "seg_01_turn_01"},
                },
                "base_state": state,
                "prefetch_ready": True,
            },
        })
        planned_item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": planned_interaction["job_id"],
            "message_id": "planned-msg:0",
            "character_id": "host-a",
            "character_name": "主持A",
            "sequence_index": 0,
            "text": "planned line",
            "status": "ready",
            "audio_path": "planned.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_prefetch"},
        })
        audience_decision = {
            "action": "reply_chat_batch",
            "episode_plan": {"mode": "audience_gap_prepare"},
        }
        audience_interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_audience_prepare",
            "priority": 45,
            "status": "prepared",
            "event_ids": [101],
            "memoria_session_id": "mem-main",
            "metadata": {
                "decision": audience_decision,
                "base_state": state,
                "main_memoria_session_id": "mem-main",
                "prepare_ready": True,
            },
        })
        storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": audience_interaction["job_id"],
            "message_id": "audience-msg:0",
            "character_id": "cohost-a",
            "character_name": "可可",
            "sequence_index": 0,
            "text": "audience bridge line",
            "status": "ready",
            "audio_path": "audience.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_audience_prepare"},
        })

        order: list[str] = []

        async def fake_present(session_id, prepared_results, **kwargs):
            order.append(f"present:{kwargs.get('source')}")
            for prepared in prepared_results:
                for item in prepared.get("items") or []:
                    storage.update_presentation_item(
                        item["item_id"],
                        status="played",
                        acked_at="2026-05-20T00:00:00",
                    )
            return []

        async def fake_prefetch(_runtime, prefetch_session, prefetch_state, decision, *, allow_audience):
            order.append("prefetch:next-planned")
            assert allow_audience is False
            assert prefetch_session["target_memoria_session_id"] == "mem-main"
            assert decision == audience_decision
            next_interaction = storage.create_interaction({
                "session_id": "live-a",
                "source": "director_prefetch",
                "priority": 40,
                "status": "prefetched",
                "memoria_session_id": "mem-main",
                "metadata": {
                    "decision": {
                        "action": "continue_topic",
                        "episode_plan": {"mode": "planned_turn", "turn_id": "seg_01_turn_02"},
                    },
                    "base_state": prefetch_state,
                    "prefetch_ready": True,
                },
            })
            return {
                "interaction": next_interaction,
                "memoria_result": {"session_id": "mem-main", "reply": "next planned"},
                "prepared_results": [],
                "decision": next_interaction["metadata"]["decision"],
                "base_state": prefetch_state,
            }

        async def fake_update_state(_runtime, _session, current_state, _consumed, **_kwargs):
            return current_state

        monkeypatch.setattr(manager, "present_prepared_stream_results", fake_present)
        monkeypatch.setattr(manager, "_prefetch_next_presentation_turn", fake_prefetch)
        monkeypatch.setattr(manager, "_update_director_state_after_prefetch_consumed", fake_update_state)

        async def first_prefetch_task():
            return {
                "interaction": planned_interaction,
                "memoria_result": {"session_id": "mem-main", "reply": "planned line"},
                "prepared_results": [{
                    "message": {"message_id": "planned-msg", "content": "planned line"},
                    "items": [planned_item],
                }],
                "decision": planned_interaction["metadata"]["decision"],
                "base_state": state,
            }

        await manager._after_main_turn_sequence(
            runtime,
            session,
            state,
            asyncio.create_task(first_prefetch_task()),
        )

        assert order == [
            "present:director",
            "present:director_audience_gap",
            "prefetch:next-planned",
        ]
```

- [ ] **Step 2: Run the focused test and verify the current failure**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_director_audience_preprocessing.py::test_audience_gap_after_consumed_planned_prefetch_starts_next_planned_prefetch -q
```

Expected before implementation: FAIL because `prefetch:next-planned` is missing. This proves the latency report is not a TTS issue; the next planned turn was never prefetched after the audience gap.

- [ ] **Step 3: Commit the red test only if the team wants red-test commits**

Default for this repo: do not commit a failing test by itself unless the user explicitly asks for TDD red commits. Keep it as an uncommitted red check, then implement Task 2.

---

### Task 2: Add a Deep Prepared-Turn Consumer Interface

**Files:**
- Modify: `YouTubeBridge/turn_pipeline.py`
- Modify: `YouTubeBridge/tests/test_turn_pipeline.py`

- [ ] **Step 1: Add unit tests for the consumer**

Append these tests to `YouTubeBridge/tests/test_turn_pipeline.py`:

```python
import asyncio

from turn_pipeline import (
    PreparedTurnConsumeOptions,
    PreparedTurnPayload,
    consume_prepared_turn,
)


class FakePreparedTurnAdapter:
    def __init__(self, interaction, prepared_results):
        self.interaction = dict(interaction)
        self.prepared_results = prepared_results
        self.events: list[str] = []
        self.marked_event_ids: list[int] = []
        self.followup_calls: list[dict] = []

    def get_interaction(self, job_id):
        if job_id == self.interaction["job_id"]:
            return dict(self.interaction)
        return None

    def prepared_results_for_interaction(self, interaction, *, require_complete):
        return list(self.prepared_results)

    def claim_interaction(self, job_id, expected_status):
        assert job_id == self.interaction["job_id"]
        assert self.interaction["status"] == expected_status
        self.interaction["status"] = "presenting"
        return dict(self.interaction)

    async def broadcast(self, payload):
        self.events.append(payload["type"])

    async def present_prepared_results(self, prepared_results, *, source, interaction_job_id):
        self.events.append(f"present:{source}")
        return []

    def visible_prepared_results(self, prepared_results):
        return list(prepared_results)

    def prepared_result_item_count(self, prepared_results):
        return sum(len(prepared.get("items") or []) for prepared in prepared_results)

    def mark_audience_events_injected(self, interaction):
        ids = [int(event_id) for event_id in interaction.get("event_ids") or []]
        self.marked_event_ids.extend(ids)
        return len(ids)

    def complete_interaction(self, job_id, *, reply_text, metadata):
        assert job_id == self.interaction["job_id"]
        self.interaction["status"] = "completed"
        self.interaction["reply_text"] = reply_text
        self.interaction.setdefault("metadata", {}).update(metadata)
        return dict(self.interaction)

    async def schedule_followup_prefetch(self, payload, *, allow_audience):
        self.followup_calls.append({"payload": payload, "allow_audience": allow_audience})
        return "followup-task"


def _payload(interaction, *, decision=None, base_state=None, prepared_results=None):
    return PreparedTurnPayload(
        interaction=interaction,
        memoria_result={"session_id": "mem-main", "reply": "reply text"},
        prepared_results=prepared_results or [{"message": {"content": "reply text"}, "items": [{"item_id": "item-1"}]}],
        decision=decision or {"action": "continue_topic", "episode_plan": {"mode": "planned_turn"}},
        base_state=base_state or {"status": "running"},
    )


def test_consume_prepared_turn_claims_presents_completes_and_chains():
    interaction = {
        "job_id": "prefetch-1",
        "source": "director_prefetch",
        "status": "prefetched",
        "metadata": {"decision": {"action": "continue_topic"}},
    }
    adapter = FakePreparedTurnAdapter(interaction, _payload(interaction).prepared_results)

    result = asyncio.run(consume_prepared_turn(
        adapter,
        _payload(interaction),
        PreparedTurnConsumeOptions(
            session_id="live-a",
            allow_followup_prefetch=True,
            followup_allow_audience=True,
            completion_metadata_key="prefetch_consumed",
            started_event_type="interaction_started",
            completed_event_type="interaction_completed",
        ),
    ))

    assert result.consumed is True
    assert result.interaction["status"] == "completed"
    assert result.interaction["metadata"]["prefetch_consumed"] is True
    assert result.after_memoria_task == "followup-task"
    assert adapter.events == ["interaction_started", "present:director", "interaction_completed"]
    assert adapter.followup_calls == [{"payload": result.payload, "allow_audience": True}]


def test_consume_prepared_turn_marks_audience_events_without_general_chain():
    interaction = {
        "job_id": "audience-1",
        "source": "director_audience_prepare",
        "status": "prepared",
        "event_ids": [101, 102],
        "metadata": {"decision": {"action": "reply_chat_batch"}},
    }
    adapter = FakePreparedTurnAdapter(interaction, _payload(interaction).prepared_results)

    result = asyncio.run(consume_prepared_turn(
        adapter,
        _payload(interaction),
        PreparedTurnConsumeOptions(
            session_id="live-a",
            allow_followup_prefetch=False,
            completion_metadata_key="audience_prepare_consumed",
            started_event_type="interaction_started",
            completed_event_type="interaction_completed",
        ),
    ))

    assert result.consumed is True
    assert result.after_memoria_task is None
    assert adapter.marked_event_ids == [101, 102]
    assert result.interaction["metadata"]["audience_prepare_consumed"] is True


def test_consume_prepared_turn_refuses_dedicated_closing_when_not_expected():
    interaction = {
        "job_id": "closing-1",
        "source": "director_prefetch",
        "status": "prefetched",
        "metadata": {"decision": {"action": "final_closing"}},
    }
    adapter = FakePreparedTurnAdapter(interaction, _payload(interaction).prepared_results)

    result = asyncio.run(consume_prepared_turn(
        adapter,
        _payload(interaction),
        PreparedTurnConsumeOptions(
            session_id="live-a",
            expected_dedicated_closing=False,
            completion_metadata_key="prefetch_consumed",
            started_event_type="interaction_started",
            completed_event_type="interaction_completed",
        ),
    ))

    assert result.consumed is False
    assert result.reason == "dedicated_closing_not_allowed"
    assert adapter.events == []
```

- [ ] **Step 2: Run the consumer tests and verify they fail**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_turn_pipeline.py -q
```

Expected before implementation: FAIL because `PreparedTurnPayload`, `PreparedTurnConsumeOptions`, and `consume_prepared_turn` do not exist.

- [ ] **Step 3: Add the consumer dataclasses and Adapter Protocol**

Add this to `YouTubeBridge/turn_pipeline.py` after `PreparedTurnPolicy`:

```python
from typing import Protocol


@dataclass(frozen=True)
class PreparedTurnPayload:
    interaction: dict[str, Any]
    memoria_result: dict[str, Any]
    prepared_results: list[dict[str, Any]]
    decision: dict[str, Any]
    base_state: dict[str, Any]


@dataclass(frozen=True)
class PreparedTurnConsumeOptions:
    session_id: str
    allow_followup_prefetch: bool = False
    followup_allow_audience: bool = False
    expected_dedicated_closing: bool | None = None
    require_complete_prepared_items: bool = False
    completion_metadata_key: str = "prepared_turn_consumed"
    started_event_type: str = "interaction_started"
    completed_event_type: str = "interaction_completed"


@dataclass(frozen=True)
class PreparedTurnConsumeResult:
    consumed: bool
    reason: str
    payload: PreparedTurnPayload
    interaction: dict[str, Any] | None = None
    after_memoria_task: Any | None = None
    played_item_count: int = 0
    marked_injected: int = 0


class PreparedTurnConsumeAdapter(Protocol):
    def get_interaction(self, job_id: str) -> dict[str, Any] | None: ...
    def prepared_results_for_interaction(
        self,
        interaction: dict[str, Any],
        *,
        require_complete: bool,
    ) -> list[dict[str, Any]]: ...
    def claim_interaction(self, job_id: str, expected_status: str) -> dict[str, Any] | None: ...
    async def broadcast(self, payload: dict[str, Any]) -> None: ...
    async def present_prepared_results(
        self,
        prepared_results: list[dict[str, Any]],
        *,
        source: str,
        interaction_job_id: str,
    ) -> list[dict[str, Any]]: ...
    def visible_prepared_results(self, prepared_results: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
    def prepared_result_item_count(self, prepared_results: list[dict[str, Any]]) -> int: ...
    def mark_audience_events_injected(self, interaction: dict[str, Any]) -> int: ...
    def complete_interaction(
        self,
        job_id: str,
        *,
        reply_text: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None: ...
    async def schedule_followup_prefetch(
        self,
        payload: PreparedTurnPayload,
        *,
        allow_audience: bool,
    ) -> Any | None: ...
```

- [ ] **Step 4: Implement `consume_prepared_turn()`**

Add this function to `YouTubeBridge/turn_pipeline.py`:

```python
async def consume_prepared_turn(
    adapter: PreparedTurnConsumeAdapter,
    payload: PreparedTurnPayload,
    options: PreparedTurnConsumeOptions,
) -> PreparedTurnConsumeResult:
    interaction = payload.interaction if isinstance(payload.interaction, dict) else {}
    job_id = str(interaction.get("job_id") or "")
    if not job_id:
        return PreparedTurnConsumeResult(False, "missing_job_id", payload)

    current = adapter.get_interaction(job_id) or interaction
    policy = prepared_turn_policy_for_interaction(current)
    if policy is None:
        return PreparedTurnConsumeResult(False, "unsupported_prepared_turn_policy", payload, interaction=current)
    if options.expected_dedicated_closing is not None and policy.dedicated_closing != options.expected_dedicated_closing:
        reason = "dedicated_closing_not_allowed" if policy.dedicated_closing else "dedicated_closing_required"
        return PreparedTurnConsumeResult(False, reason, payload, interaction=current)
    if str(current.get("status") or "") != policy.expected_status:
        return PreparedTurnConsumeResult(
            False,
            f"status_not_{policy.expected_status}",
            payload,
            interaction=current,
        )

    prepared_results = [
        prepared for prepared in payload.prepared_results
        if isinstance(prepared, dict)
    ]
    if not prepared_results:
        prepared_results = adapter.prepared_results_for_interaction(
            current,
            require_complete=options.require_complete_prepared_items,
        )
    if not prepared_results:
        return PreparedTurnConsumeResult(False, "missing_prepared_items", payload, interaction=current)

    started = adapter.claim_interaction(job_id, policy.expected_status)
    if not started or str(started.get("status") or "") != "presenting":
        return PreparedTurnConsumeResult(False, "presenting_claim_failed", payload, interaction=started or current)

    await adapter.broadcast({"type": options.started_event_type, "interaction": started})
    await adapter.present_prepared_results(
        prepared_results,
        source=policy.presentation_source,
        interaction_job_id=job_id,
    )
    visible_results = adapter.visible_prepared_results(prepared_results)
    played_item_count = adapter.prepared_result_item_count(visible_results)
    marked_injected = 0
    if policy.mark_audience_events_injected and played_item_count > 0:
        marked_injected = adapter.mark_audience_events_injected(started)

    metadata = {
        options.completion_metadata_key: True,
        "played_item_count": played_item_count,
        "marked_injected": marked_injected,
    }
    updated = adapter.complete_interaction(
        job_id,
        reply_text=str(payload.memoria_result.get("reply") or started.get("reply_text") or ""),
        metadata=metadata,
    )
    if not updated or str(updated.get("status") or "") != "completed":
        return PreparedTurnConsumeResult(False, "complete_failed", payload, interaction=updated or started)

    result_payload = PreparedTurnPayload(
        interaction=updated,
        memoria_result=payload.memoria_result,
        prepared_results=prepared_results,
        decision=payload.decision,
        base_state=payload.base_state,
    )
    after_memoria_task = None
    if options.allow_followup_prefetch and policy.may_chain and payload.decision and payload.base_state:
        after_memoria_task = await adapter.schedule_followup_prefetch(
            result_payload,
            allow_audience=options.followup_allow_audience,
        )
    elif options.allow_followup_prefetch and policy.mark_audience_events_injected and payload.decision and payload.base_state:
        after_memoria_task = await adapter.schedule_followup_prefetch(
            result_payload,
            allow_audience=False,
        )

    await adapter.broadcast({
        "type": options.completed_event_type,
        "interaction": updated,
        "memoria_session_id": payload.memoria_result.get("session_id") or "",
        "source": policy.presentation_source,
    })
    return PreparedTurnConsumeResult(
        True,
        "consumed",
        result_payload,
        interaction=updated,
        after_memoria_task=after_memoria_task,
        played_item_count=played_item_count,
        marked_injected=marked_injected,
    )
```

- [ ] **Step 5: Run the unit tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_turn_pipeline.py -q
```

Expected after implementation: PASS.

---

### Task 3: Route Director Planned and Audience-Gap Consume Through the Pipeline

**Files:**
- Modify: `YouTubeBridge/engine_director_runtime.py`
- Modify: `YouTubeBridge/tests/test_director_audience_preprocessing.py`
- Modify: `YouTubeBridge/tests/test_director_prefetch_chain.py`

- [ ] **Step 1: Add the import**

In `YouTubeBridge/engine_director_runtime.py`, extend the existing import:

```python
from turn_pipeline import (
    PreparedTurnConsumeOptions,
    PreparedTurnPayload,
    consume_prepared_turn,
    prepared_turn_policy_for_interaction,
)
```

- [ ] **Step 2: Add a local Adapter class or helper factory**

Add this nested helper near `_consume_prefetched_episode_turn()` or as private methods in `DirectorRuntimeManagerMixin`. Keep it local to the runtime file so `turn_pipeline.py` does not import `LiveRuntime`.

```python
class _DirectorPreparedTurnAdapter:
    def __init__(
        self,
        manager,
        runtime: LiveRuntime,
        session: dict[str, Any],
        *,
        allow_followup: bool,
        followup_allow_audience: bool,
    ) -> None:
        self.manager = manager
        self.runtime = runtime
        self.session = session
        self.allow_followup = allow_followup
        self.followup_allow_audience = followup_allow_audience

    def get_interaction(self, job_id: str) -> dict[str, Any] | None:
        return self.manager.storage.get_interaction(job_id)

    def prepared_results_for_interaction(self, interaction: dict[str, Any], *, require_complete: bool) -> list[dict[str, Any]]:
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

    async def present_prepared_results(self, prepared_results: list[dict[str, Any]], *, source: str, interaction_job_id: str):
        return await self.manager.present_prepared_stream_results(
            self.runtime.session_id,
            prepared_results,
            source=source,
            interaction_job_id=interaction_job_id,
        )

    def visible_prepared_results(self, prepared_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
        return self.manager.storage.mark_events_injected(self.runtime.session_id, event_ids) if event_ids else 0

    def complete_interaction(self, job_id: str, *, reply_text: str, metadata: dict[str, Any]) -> dict[str, Any] | None:
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

    async def schedule_followup_prefetch(self, payload: PreparedTurnPayload, *, allow_audience: bool):
        metadata = payload.interaction.get("metadata") if isinstance(payload.interaction.get("metadata"), dict) else {}
        chained_session = dict(self.session)
        main_session_id = str(metadata.get("main_memoria_session_id") or self.session.get("target_memoria_session_id") or "")
        if main_session_id:
            chained_session["target_memoria_session_id"] = main_session_id
        self.runtime.director_prefetch_in_flight += 1

        async def run_next_prefetch():
            try:
                await self.manager._yield_before_presentation_chain_prefetch()
                return await self.manager._prefetch_next_presentation_turn(
                    self.runtime,
                    chained_session,
                    payload.base_state,
                    payload.decision,
                    allow_audience=allow_audience,
                )
            finally:
                self.runtime.director_prefetch_in_flight = max(0, self.runtime.director_prefetch_in_flight - 1)

        return asyncio.create_task(run_next_prefetch())
```

- [ ] **Step 3: Replace `_consume_prefetched_episode_turn()` internals**

Keep the outer runtime/session-running checks at the top. Replace the duplicated claim/present/complete body with:

```python
payload = PreparedTurnPayload(
    interaction=current_interaction,
    memoria_result=prefetch.get("memoria_result") if isinstance(prefetch.get("memoria_result"), dict) else {},
    prepared_results=prepared_results,
    decision=prefetch.get("decision") if isinstance(prefetch.get("decision"), dict) else {},
    base_state=prefetch.get("base_state") if isinstance(prefetch.get("base_state"), dict) else {},
)
decision_payload = payload.decision.get("episode_plan") if isinstance(payload.decision.get("episode_plan"), dict) else {}
decision_mode = str(decision_payload.get("mode") or "")
adapter = _DirectorPreparedTurnAdapter(
    self,
    runtime,
    session,
    allow_followup=True,
    followup_allow_audience=(decision_mode == "planned_turn"),
)
consume_result = await consume_prepared_turn(
    adapter,
    payload,
    PreparedTurnConsumeOptions(
        session_id=runtime.session_id,
        allow_followup_prefetch=(
            bool(payload.decision)
            and bool(payload.base_state)
            and not runtime.stop_after_current_turn
            and not runtime.graceful_closing_requested
        ),
        followup_allow_audience=(decision_mode == "planned_turn"),
        expected_dedicated_closing=False,
        completion_metadata_key="prefetch_consumed",
        started_event_type="interaction_started",
        completed_event_type="interaction_completed",
    ),
)
if not consume_result.consumed:
    _director_timing_log(
        "prefetch_consume_refused",
        session_id=runtime.session_id,
        job_id=job_id,
        status=(consume_result.interaction or {}).get("status"),
        reason=consume_result.reason,
    )
    return None
await self._broadcast(runtime.session_id, {
    "type": "director_injected",
    "interaction": consume_result.interaction,
    "memoria_session_id": payload.memoria_result.get("session_id") or session.get("target_memoria_session_id") or "",
})
response = {
    **prefetch,
    "interaction": consume_result.interaction,
    "discarded": False,
}
if consume_result.after_memoria_task is not None:
    response["after_memoria_task"] = consume_result.after_memoria_task
return response
```

- [ ] **Step 4: Replace `_present_ready_audience_gap_turn()` consume body**

Keep the event selection and `_episode_audience_gap_block_reason()` check. After `prepared_results` is available, use the same consumer:

```python
payload = PreparedTurnPayload(
    interaction=interaction,
    memoria_result={"session_id": interaction.get("memoria_session_id") or "", "reply": interaction.get("reply_text") or ""},
    prepared_results=prepared_results,
    decision=decision,
    base_state=state,
)
adapter = _DirectorPreparedTurnAdapter(
    self,
    runtime,
    session,
    allow_followup=chain_next_prefetch,
    followup_allow_audience=False,
)
consume_result = await consume_prepared_turn(
    adapter,
    payload,
    PreparedTurnConsumeOptions(
        session_id=runtime.session_id,
        allow_followup_prefetch=(
            chain_next_prefetch
            and bool(decision)
            and bool(state)
            and runtime.director_prefetch_in_flight <= 0
            and not runtime.stop_after_current_turn
            and not runtime.graceful_closing_requested
        ),
        followup_allow_audience=False,
        expected_dedicated_closing=False,
        completion_metadata_key="audience_prepare_consumed",
        started_event_type="director_audience_gap_presenting",
        completed_event_type="director_audience_gap_presented",
    ),
)
if not consume_result.consumed:
    _director_timing_log(
        "audience_gap_present_refused",
        session_id=runtime.session_id,
        job_id=job_id,
        reason=consume_result.reason,
    )
    return None
latest_state = self.storage.get_director_state(runtime.session_id) or state
metadata = dict(latest_state.get("metadata") if isinstance(latest_state.get("metadata"), dict) else {})
if consume_result.played_item_count > 0:
    metadata["last_audience_gap_presented_at"] = datetime.now().isoformat()
if decision and consume_result.played_item_count > 0:
    metadata.update(self._episode_metadata_after_turn(session, latest_state or state, decision))
self.storage.update_director_state(runtime.session_id, status="running", metadata=metadata)
response = dict(consume_result.interaction or interaction)
if consume_result.after_memoria_task is not None:
    response["after_memoria_task"] = consume_result.after_memoria_task
return response
```

- [ ] **Step 5: Fix the audience chain guard in `_after_main_turn_sequence()`**

Replace:

```python
chain_next_prefetch = prefetch_task is None and consumed_count == 0
```

with:

```python
chain_next_prefetch = prefetch_task is None
```

This is safe after Task 3 because duplicate-chain prevention now depends on whether a next task owns the slot, `runtime.director_prefetch_in_flight`, `runtime.stop_after_current_turn`, and `runtime.graceful_closing_requested`, not on whether any previous turn was consumed.

- [ ] **Step 6: Run the targeted tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_director_audience_preprocessing.py::test_audience_gap_after_consumed_planned_prefetch_starts_next_planned_prefetch -q
python -m pytest YouTubeBridge\tests\test_director_prefetch_chain.py::test_after_main_turn_sequence_uses_audience_chain_task_instead_of_stale_prepare -q
```

Expected after implementation: both PASS.

---

### Task 4: Route Closing Drain and Dedicated Closing Through the Same Consumer

**Files:**
- Modify: `YouTubeBridge/engine_closing.py`
- Modify: `YouTubeBridge/tests/test_bridge_engine_closing.py`

- [ ] **Step 1: Add a closing Adapter**

In `YouTubeBridge/engine_closing.py`, import the consumer types and add a `_ClosingPreparedTurnAdapter` mirroring `_DirectorPreparedTurnAdapter`, with `schedule_followup_prefetch()` always returning `None`.

```python
from turn_pipeline import (
    PreparedTurnConsumeOptions,
    PreparedTurnPayload,
    consume_prepared_turn,
    prepared_turn_policy_for_interaction,
)
```

The Adapter should use these manager methods:

```python
self.manager._prepared_results_for_interaction(...)
self.manager.present_prepared_stream_results(...)
self.manager._visible_prepared_results(...)
self.manager._prepared_result_item_count(...)
self.manager.storage.mark_events_injected(...)
self.manager.storage.update_interaction_if_status(...)
self.manager._broadcast(...)
```

- [ ] **Step 2: Convert `_present_ready_prefetch_for_closing_drain()`**

For each valid `job_id`, build a `PreparedTurnPayload` from the interaction and call:

```python
consume_result = await consume_prepared_turn(
    adapter,
    payload,
    PreparedTurnConsumeOptions(
        session_id=runtime.session_id,
        allow_followup_prefetch=False,
        expected_dedicated_closing=False,
        require_complete_prepared_items=True,
        completion_metadata_key=(
            "audience_prepare_consumed"
            if prepared_turn_policy_for_interaction(interaction).mark_audience_events_injected
            else "prefetch_consumed"
        ),
        started_event_type="interaction_started",
        completed_event_type="interaction_completed",
    ),
)
```

If `consume_result.consumed` is true, broadcast the existing `director_injected` event and return the existing shape:

```python
return {
    "status": str((consume_result.interaction or {}).get("status") or "completed"),
    "interaction": consume_result.interaction,
    "prefetch_consumed": True,
}
```

- [ ] **Step 3: Convert `_consume_final_closing_prefetch()`**

Keep these existing validations before the consumer call:

```python
task is done
task is not cancelled
task.exception() is None
sc_state_signature matches
visible_target_signature matches
current interaction is a final closing prefetch interaction
prepared results exist
```

After validators pass, call:

```python
consume_result = await consume_prepared_turn(
    adapter,
    payload,
    PreparedTurnConsumeOptions(
        session_id=runtime.session_id,
        allow_followup_prefetch=False,
        expected_dedicated_closing=True,
        require_complete_prepared_items=True,
        completion_metadata_key="final_closing_prefetch_consumed",
        started_event_type="interaction_started",
        completed_event_type="interaction_completed",
    ),
)
```

On refusal, call `_cancel_final_closing_prefetch(runtime, prefetch_context, reason=f"final_closing_prefetch_{consume_result.reason}")` and return `None`. On success, broadcast `director_injected` and return the same public result shape as today.

- [ ] **Step 4: Convert `_consume_closing_super_chat_prefetch()`**

Keep these existing validations before the consumer call:

```python
task is done
task is not cancelled
task.exception() is None
SC state signature matches current unhandled SCs
visible target signature still matches
current interaction is a closing Super Chat prefetch interaction
prepared results exist
```

Start `after_memoria_callback(result)` before presenting, as the current path does, so final closing prefetch can start while SC thanks audio is playing. Then call:

```python
consume_result = await consume_prepared_turn(
    adapter,
    payload,
    PreparedTurnConsumeOptions(
        session_id=runtime.session_id,
        allow_followup_prefetch=False,
        expected_dedicated_closing=True,
        require_complete_prepared_items=True,
        completion_metadata_key="closing_super_chat_prefetch_consumed",
        started_event_type="interaction_started",
        completed_event_type="interaction_completed",
    ),
)
```

After success, await the callback task, call `mark_super_chats_handled_in_closing(...)`, broadcast `closing_super_chat_thanks_completed`, and return the same public result shape as today.

- [ ] **Step 5: Run closing tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_closing.py -q
```

Expected after implementation: PASS.

---

### Task 5: Add Follow-Up Prefetch Diagnostics and Remove Duplicate Consume Logic

**Files:**
- Modify: `YouTubeBridge/turn_pipeline.py`
- Modify: `YouTubeBridge/engine_director_runtime.py`
- Modify: `YouTubeBridge/tests/test_director_prefetch_chain.py`

- [ ] **Step 1: Add structured skip reasons**

Add this helper to `YouTubeBridge/turn_pipeline.py`:

```python
def prepared_turn_followup_skip_reason(
    *,
    requested: bool,
    has_decision: bool,
    has_base_state: bool,
    runtime_stopping: bool,
    graceful_closing: bool,
    prefetch_in_flight: bool,
) -> str:
    if not requested:
        return "not_requested"
    if not has_decision:
        return "missing_decision"
    if not has_base_state:
        return "missing_base_state"
    if runtime_stopping:
        return "runtime_stopping"
    if graceful_closing:
        return "graceful_closing"
    if prefetch_in_flight:
        return "prefetch_in_flight"
    return ""
```

- [ ] **Step 2: Log the reason when an audience gap does not chain**

In `_present_ready_audience_gap_turn()`, compute the reason before calling the consumer:

```python
skip_reason = prepared_turn_followup_skip_reason(
    requested=chain_next_prefetch,
    has_decision=bool(decision),
    has_base_state=bool(state),
    runtime_stopping=bool(runtime.stop_after_current_turn),
    graceful_closing=bool(runtime.graceful_closing_requested),
    prefetch_in_flight=runtime.director_prefetch_in_flight > 0,
)
if skip_reason:
    _director_timing_log(
        "audience_gap_followup_prefetch_skipped",
        session_id=runtime.session_id,
        job_id=interaction.get("job_id"),
        reason=skip_reason,
    )
```

- [ ] **Step 3: Add a diagnostic regression**

Append this to `YouTubeBridge/tests/test_director_prefetch_chain.py`:

```python
def test_prepared_turn_followup_skip_reason_reports_prefetch_in_flight():
    from turn_pipeline import prepared_turn_followup_skip_reason

    assert prepared_turn_followup_skip_reason(
        requested=True,
        has_decision=True,
        has_base_state=True,
        runtime_stopping=False,
        graceful_closing=False,
        prefetch_in_flight=True,
    ) == "prefetch_in_flight"
```

- [ ] **Step 4: Run the diagnostic test**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_director_prefetch_chain.py::test_prepared_turn_followup_skip_reason_reports_prefetch_in_flight -q
```

Expected after implementation: PASS.

---

### Task 6: Regression Sweep

**Files:**
- No new files.

- [ ] **Step 1: Run targeted turn-pipeline tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_turn_pipeline.py -q
```

Expected: PASS.

- [ ] **Step 2: Run director prefetch chain tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_director_prefetch_chain.py -q
```

Expected: PASS.

- [ ] **Step 3: Run audience preprocessing tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_director_audience_preprocessing.py -q
```

Expected: PASS.

- [ ] **Step 4: Run closing tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_closing.py -q
```

Expected: PASS.

- [ ] **Step 5: Run server route split tests for finalize flags**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_server_route_split.py -q
```

Expected: PASS.

- [ ] **Step 6: Run syntax and whitespace checks**

Run:

```powershell
python -m compileall YouTubeBridge\turn_pipeline.py YouTubeBridge\engine_director_runtime.py YouTubeBridge\engine_closing.py
git diff --check
```

Expected: compileall exits 0; `git diff --check` exits 0.

---

## Self-Review

- **Spec coverage:** This plan covers the requested architecture consolidation by deepening `turn_pipeline.py` beyond policy into prepared-turn consumption. It also covers the concrete prefetch bug where an audience gap after a consumed planned prefetch does not start the next planned prefetch.
- **Scope control:** This does not move root-level facades, does not rewrite Studio playback, and does not move the entire `_after_main_turn_sequence()` loop. Those are separate refactors.
- **Dedicated closing semantics:** General chain gates remain closed during graceful closing. Dedicated final closing and SC closing keep their stale visible target and SC-state validation before consuming.
- **Risk:** The Adapter Interface adds indirection. The risk is controlled by keeping runtime state and storage I/O in existing manager files, with `turn_pipeline.py` owning only the deterministic consume sequence.

Plan complete and saved to `docs/superpowers/plans/2026-05-20-youtubebridge-prepared-turn-consumer.md`. Two execution options:

1. **Subagent-Driven (recommended)** - Dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints.
