# YouTubeBridge Audience Gap Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace interrupt/discard-based audience handling with a two-lane flow: main LiveEpisodePlan turns may prefetch normally, while chat and Super Chat batches are prepared in a shared audience sidecar session and played only at director gaps.

**Architecture:** Keep the main plan lane and audience lane isolated. Main planned turns continue to use `target_memoria_session_id`; audience batches use a persistent sidecar Memoria session stored in director metadata, prepare presentation/TTS items in the background, and are presented after a plan turn has visibly completed. No audience event, including Super Chat, should request an interrupt in director-owned LiveEpisodePlan sessions.

**Tech Stack:** Python, asyncio, SQLite-backed `BridgeStorage`, YouTubeBridge director runtime, MemoriaCore `/chat/stream-sync`, existing presentation/TTS queue.

---

## File Structure

- Modify `YouTubeBridge/engine_episode_plans.py`
  - Add audience-gap decision helper that reuses existing batch selection and prompt construction without returning `audience_interrupt` from the main planned-turn selector when presentation mode is enabled.
  - Add metadata update handling for `audience_gap` completion.
- Modify `YouTubeBridge/engine_director_runtime.py`
  - Add prepare-only director send mode for audience batches.
  - Add helper methods to start audience preparation, reconstruct prepared presentation results, present a ready audience gap, and preserve the audience sidecar session id.
  - Present a ready audience gap between visible planned turns and before consuming the next prefetched planned turn.
  - Stop discarding prefetched planned turns just because pending chat exists.
- Modify `YouTubeBridge/engine_injection.py`
  - Remove director-owned Super Chat interruption.
  - Let auto-inject classify and schedule background audience preparation instead of interrupting or injecting immediately.
- Modify `YouTubeBridge/storage_repositories/interactions.py`
  - Include audience prepare statuses in incomplete-interaction finalization so shutdown/cleanup does not leave permanent `preparing` or `prepared` rows.
- Modify tests:
  - `YouTubeBridge/tests/test_bridge_engine_episode_plan_runtime.py`
  - `YouTubeBridge/tests/test_bridge_engine_director.py`
  - `YouTubeBridge/tests/test_bridge_engine_injection.py`
  - `YouTubeBridge/tests/test_storage.py`

## Task 1: Episode Plan Audience Gap Decisions

**Files:**
- Modify: `YouTubeBridge/engine_episode_plans.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_episode_plan_runtime.py`

- [ ] **Step 1: Write failing tests for presentation-mode gap decisions**

Add tests that create a session with `presentation_enabled=True`, insert completed clean chat and Super Chat events, and assert:

```python
gap_decision = manager._episode_plan_next_audience_gap_decision(session, state)
main_decision = manager._episode_plan_next_decision(session, state)

assert gap_decision["action"] == "reply_chat_batch"
assert gap_decision["episode_plan"]["mode"] == "audience_gap"
assert main_decision["episode_plan"]["mode"] == "planned_turn"
```

For Super Chat, assert `reply_super_chat_batch`, selected event count respects `max_sc_per_batch`, and metadata records `last_sc_gap_at`, not `last_sc_interrupt_at`.

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_episode_plan_runtime.py -k "audience_gap or audience_interrupt_batches_normal_backlog or super_chat_burst" -q
```

Expected: failure because `_episode_plan_next_audience_gap_decision` does not exist and main planned-turn selection still returns `audience_interrupt`.

- [ ] **Step 3: Implement `_episode_plan_next_audience_gap_decision`**

Add a method next to `_episode_plan_next_decision`:

```python
def _episode_plan_next_audience_gap_decision(
    self,
    session: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    plan, planned_state = self._episode_plan_and_state(session, state)
    if not plan or str(planned_state.get("plan_status") or "") == "completed":
        return None
    completed_events = self._episode_completed_audience_events(
        session["session_id"],
        planned_state,
        limit=500,
    )
    selected_events = self._episode_select_audience_event_batch(session, completed_events)
    snapshot = self._episode_audience_backlog_snapshot(completed_events, selected_events)
    block_reason = self._episode_audience_interrupt_block_reason(session, state, selected_events)
    if not selected_events or block_reason:
        return None
    decision = self._episode_interrupt_decision_for_event(
        plan,
        planned_state,
        selected_events[0],
        batch_events=selected_events,
        backlog_snapshot=snapshot,
    )
    if not decision:
        return None
    payload = decision.setdefault("episode_plan", {})
    payload["mode"] = "audience_gap"
    payload["backlog_snapshot"] = snapshot
    return decision
```

- [ ] **Step 4: Gate main decision selection**

In `_episode_plan_next_decision`, when `self._presentation_enabled(session)` is true, do not return the audience decision. Still attach `backlog_snapshot` and `defer_reason` to the planned turn so the director UI/debug stream can show backlog pressure.

Expected behavior:

```python
if selected_events and not block_reason and not self._presentation_enabled(session):
    return existing_audience_interrupt_decision
planned = self._episode_planned_turn_decision(session, state)
```

- [ ] **Step 5: Add audience-gap metadata handling**

In `_episode_metadata_after_turn`, treat `mode in {"audience_interrupt", "audience_gap"}` with the same segment-memory behavior, but use these metadata keys for `audience_gap`:

```python
update["last_audience_gap_at"] = now
if interrupt_type == "super_chat":
    update["last_sc_gap_at"] = now
```

Do not require `last_audience_interrupt_at` to update for `audience_gap`.

- [ ] **Step 6: Run tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_episode_plan_runtime.py -q
```

Expected: all tests in the file pass after updating assertions that are intentionally presentation-mode specific.

## Task 2: Prepare-Only Audience Director Turns

**Files:**
- Modify: `YouTubeBridge/engine_director_runtime.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_director.py`

- [ ] **Step 1: Write failing tests for sidecar preparation**

Add a test that enables presentation/TTS, inserts one completed audience event, calls the new audience prepare helper, and asserts:

```python
assert result["interaction"]["source"] == "director_audience_prepare"
assert result["interaction"]["status"] == "prepared"
assert storage.get_session("live-a")["target_memoria_session_id"] == "mem-main"
assert storage.get_director_state("live-a")["metadata"]["audience_sidecar_memoria_session_id"] == "mem-audience"
assert storage.list_presentation_items("live-a", statuses={"ready"})
assert not storage.get_events_by_ids("live-a", [event_id])[0]["injected_at"]
```

Use a fake Memoria client that returns `session_id="mem-audience"` and calls `on_result` with a single assistant result so `prepare_stream_result()` creates ready presentation items.

- [ ] **Step 2: Run the failing test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_director.py -k "audience_gap_prepare" -q
```

Expected: fail because the helper and prepare-only mode do not exist.

- [ ] **Step 3: Add prepare-only mode to `_send_director_turn`**

Extend the signature:

```python
async def _send_director_turn(
    self,
    session: dict[str, Any],
    state: dict[str, Any],
    decision: dict[str, Any],
    *,
    prefetch_only: bool = False,
    prepare_only: bool = False,
    prepare_source: str = "director_prepare",
    after_memoria_callback=None,
) -> dict[str, Any]:
```

Inside interaction creation:

```python
source_name = prepare_source if prepare_only else ("director_prefetch" if prefetch_only else "director")
initial_status = "preparing" if prepare_only else ("prefetching" if prefetch_only else "queued")
priority = 45 if prepare_only else (40 if prefetch_only else 50)
```

Skip `_claim_interaction_for_execution()` for `prepare_only`, just like prefetch mode skips it. In `on_stream_result`, prepare presentation results when either `prefetch_only` or `prepare_only` is true:

```python
if prefetch_only or prepare_only:
    future = asyncio.run_coroutine_threadsafe(
        self.prepare_stream_result(
            session_id,
            event,
            source=source_name,
            interaction_job_id=interaction["job_id"],
        ),
        loop,
    )
```

When `prepare_only` returns, update the interaction to `prepared`, save `reply_text`, `memoria_session_id`, `result_message_id`, and `prepared_result_count`, and return `prepared_results` without broadcasting `interaction_completed` and without updating the live session `target_memoria_session_id`.

- [ ] **Step 4: Add audience sidecar preparation helper**

Add `_prepare_next_audience_gap_turn(runtime, session, state)`:

```python
async def _prepare_next_audience_gap_turn(
    self,
    runtime: LiveRuntime,
    session: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    if not self._presentation_enabled(session):
        return None
    if self._audience_gap_interaction_by_status(runtime.session_id, {"preparing", "prepared", "presenting"}):
        return None
    decision = self._episode_plan_next_audience_gap_decision(session, state)
    if not decision:
        return None
    metadata = dict((state.get("metadata") or {}))
    sidecar_session_id = str(metadata.get("audience_sidecar_memoria_session_id") or "")
    audience_session = dict(session)
    audience_session["target_memoria_session_id"] = sidecar_session_id
    result = await self._send_director_turn(
        audience_session,
        state,
        decision,
        prepare_only=True,
        prepare_source="director_audience_prepare",
    )
    result_session_id = str((result.get("memoria_result") or {}).get("session_id") or "")
    if result_session_id:
        next_metadata = dict((self.storage.get_director_state(runtime.session_id) or {}).get("metadata") or {})
        next_metadata["audience_sidecar_memoria_session_id"] = result_session_id
        next_metadata["audience_prepare_in_flight"] = False
        next_metadata["latest_audience_gap_job_id"] = result.get("interaction", {}).get("job_id", "")
        self.storage.update_director_state(runtime.session_id, metadata=next_metadata)
    return result
```

Also add `_audience_gap_interaction_by_status(session_id, statuses)` by scanning `storage.list_interactions(session_id, limit=200)` for `source == "director_audience_prepare"`.

- [ ] **Step 5: Run tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_director.py -k "audience_gap_prepare" -q
```

Expected: pass.

## Task 3: Director-Owned Auto Inject Schedules, Never Interrupts

**Files:**
- Modify: `YouTubeBridge/engine_injection.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_injection.py`

- [ ] **Step 1: Update failing tests**

Add or adjust tests so a running director interaction plus higher-tier Super Chat produces:

```python
result = await manager._prepare_director_owned_auto_inject(...)
assert result["interrupted_active"] is False
assert result["selected_source"] == "super_chat"
assert result["selected_event_ids"] == [event_id]
```

In `_auto_inject_loop`, monkeypatch `_prepare_next_audience_gap_turn` and assert it is called once after `director_audience_events_ready` is emitted.

- [ ] **Step 2: Run tests and verify old interrupt behavior fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_injection.py -k "director_owned or super_chat" -q
```

Expected: fail where existing tests expect `interrupted_active=True`.

- [ ] **Step 3: Remove director-owned interrupt request**

In `_prepare_director_owned_auto_inject`, delete the branch that calls:

```python
runtime.last_sc_interrupt_at = datetime.now().isoformat()
await self.interrupt_session(session_id, reason="higher_priority:super_chat")
interrupted_active = True
```

Keep batch selection, safety classification, selected ids, and selected source.

- [ ] **Step 4: Schedule background preparation from auto-inject**

After emitting `director_audience_events_ready` in the director-owned branch of `_auto_inject_loop`, schedule preparation only if no active prepared/preparing audience interaction exists:

```python
state = self.storage.get_director_state(runtime.session_id)
prepare_result = await self._prepare_next_audience_gap_turn(runtime, session, state)
if prepare_result and prepare_result.get("interaction"):
    await self._broadcast(runtime.session_id, {
        "type": "director_audience_gap_ready",
        "interaction": prepare_result.get("interaction"),
        "event_ids": selected_event_ids,
        "source": selected_source,
    })
```

Do not call `interrupt_session()` for director-owned sessions.

- [ ] **Step 5: Run tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_injection.py -k "director_owned or super_chat" -q
```

Expected: pass with updated no-interrupt assertions.

## Task 4: Present Ready Audience Gap Between Planned Turns

**Files:**
- Modify: `YouTubeBridge/engine_director_runtime.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_director.py`
- Test: `YouTubeBridge/tests/test_storage.py`

- [ ] **Step 1: Write failing presentation-order test**

Create a test that has:

1. A completed visible plan turn.
2. A `director_audience_prepare` interaction with `status="prepared"`, event ids, and ready presentation items.
3. A prefetched next planned turn.

Call the new gap presentation helper and assert:

```python
assert updated["status"] == "completed"
assert storage.get_events_by_ids("live-a", [event_id])[0]["injected_at"]
assert emitted_types.index("presentation_item_ready") < emitted_types.index("prefetch_consume_start")
```

For unit-level precision, assert the helper updates `last_audience_gap_presented_at` and does not modify `target_memoria_session_id`.

- [ ] **Step 2: Run the failing test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_director.py -k "audience_gap_present" -q
```

Expected: fail because no helper presents prepared audience gap interactions.

- [ ] **Step 3: Add reconstruction helper**

Add `_prepared_results_for_audience_gap_interaction(session_id, interaction)`:

```python
def _prepared_results_for_audience_gap_interaction(
    self,
    session_id: str,
    interaction: dict[str, Any],
) -> list[dict[str, Any]]:
    job_id = str(interaction.get("job_id") or "")
    items = [
        item for item in self.storage.list_presentation_items(session_id, statuses={"ready"}, limit=500)
        if str(item.get("interaction_job_id") or "") == job_id
    ]
    prepared = []
    for item in items:
        message_id = str(item.get("message_id") or item.get("item_id") or "")
        base_message_id = message_id.split(":", 1)[0] if ":" in message_id else message_id
        prepared.append({
            "message": {
                "message_id": base_message_id,
                "role": "assistant",
                "content": item.get("text") or "",
                "character_id": item.get("character_id") or "",
                "character_name": item.get("character_name") or "",
                "created_at": item.get("created_at") or "",
                "timestamp": item.get("created_at") or "",
            },
            "items": [item],
        })
    return prepared
```

- [ ] **Step 4: Add `_present_ready_audience_gap_turn`**

Use the oldest prepared `director_audience_prepare` interaction:

```python
async def _present_ready_audience_gap_turn(
    self,
    runtime: LiveRuntime,
    session: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    interaction = self._audience_gap_interaction_by_status(runtime.session_id, {"prepared"})
    if not interaction:
        return None
    prepared_results = self._prepared_results_for_audience_gap_interaction(runtime.session_id, interaction)
    if not prepared_results:
        return self.storage.update_interaction(
            interaction["job_id"],
            status="failed",
            reason="audience_gap_missing_prepared_items",
            completed_at=datetime.now().isoformat(),
        )
    started = self.storage.update_interaction(interaction["job_id"], status="presenting") or interaction
    await self._broadcast(runtime.session_id, {"type": "director_audience_gap_presenting", "interaction": started})
    await self.present_prepared_stream_results(
        runtime.session_id,
        prepared_results,
        source="director_audience_gap",
        interaction_job_id=interaction["job_id"],
    )
    event_ids = [int(event_id) for event_id in interaction.get("event_ids", []) if int(event_id or 0)]
    marked = self.storage.mark_events_injected(runtime.session_id, event_ids)
    completed = self.storage.update_interaction(
        interaction["job_id"],
        status="completed",
        completed_at=datetime.now().isoformat(),
        metadata={"audience_gap_presented": True, "marked_injected": marked},
    )
    metadata = dict((self.storage.get_director_state(runtime.session_id) or {}).get("metadata") or {})
    metadata["last_audience_gap_presented_at"] = datetime.now().isoformat()
    metadata.update(self._episode_metadata_after_turn(session, state, (interaction.get("metadata") or {}).get("decision") or {}))
    self.storage.update_director_state(runtime.session_id, status="running", metadata=metadata)
    await self._broadcast(runtime.session_id, {"type": "director_audience_gap_presented", "interaction": completed})
    return completed
```

- [ ] **Step 5: Insert the gap into main flow**

After every successful visible planned turn in `_director_kickoff`, `_director_loop`, and after each consumed prefetched turn in `_consume_prefetched_episode_chain`, call:

```python
await self._present_ready_audience_gap_turn(runtime, session, next_state)
```

Call it before consuming the next prefetched planned turn. If it returns `None`, continue immediately.

- [ ] **Step 6: Stop discarding planned prefetch for pending chat**

In `_consume_prefetched_episode_turn`, remove the pending-chat discard block:

```python
if self._pending_director_blocking_events(runtime.session_id):
    ...
    return {"interaction": updated or interaction, "discarded": True}
```

Pending chat is now handled by the audience lane and must not invalidate main-lane plan prefetch.

- [ ] **Step 7: Run tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_director.py -k "audience_gap or prefetch" -q
python -m pytest YouTubeBridge/tests/test_storage.py -k "presentation or interaction" -q
```

Expected: pass.

## Task 5: Cleanup Finalization and Full Verification

**Files:**
- Modify: `YouTubeBridge/storage_repositories/interactions.py`
- Test: `YouTubeBridge/tests/test_storage.py`

- [ ] **Step 1: Write failing cleanup test**

Add:

```python
def test_finalize_incomplete_interactions_clears_audience_gap_prepare_statuses(tmp_path):
    storage = BridgeStorage(tmp_path / "youtube_live.db")
    # create connector/session
    preparing = storage.create_interaction({
        "session_id": "live-a",
        "source": "director_audience_prepare",
        "status": "preparing",
    })
    prepared = storage.create_interaction({
        "session_id": "live-a",
        "source": "director_audience_prepare",
        "status": "prepared",
    })
    finalized = storage.finalize_incomplete_interactions("live-a", reason="test_cleanup")
    assert {item["job_id"] for item in finalized} >= {preparing["job_id"], prepared["job_id"]}
```

- [ ] **Step 2: Run failing cleanup test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_storage.py -k "audience_gap_prepare_statuses" -q
```

Expected: fail because `preparing` and `prepared` are not finalized.

- [ ] **Step 3: Include new statuses in incomplete finalization**

In `finalize_incomplete_interactions`, include:

```sql
'preparing',
'prepared',
```

Do not include `completed`, `failed`, `discarded`, `interrupted`, or `expired`.

- [ ] **Step 4: Run targeted verification**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_episode_plan_runtime.py YouTubeBridge/tests/test_bridge_engine_director.py YouTubeBridge/tests/test_bridge_engine_injection.py YouTubeBridge/tests/test_presentation_queue.py YouTubeBridge/tests/test_storage.py -q
```

Expected: all selected suites pass.

## Acceptance Criteria

- Main plan turns are never marked `discarded` because chat or SC arrived.
- Director-owned Super Chat never calls `interrupt_session()` while a plan/presentation interaction is running.
- Audience replies are generated in a shared sidecar Memoria session, stored in director metadata, and do not update the live session `target_memoria_session_id`.
- Audience presentation items can be ready before the gap, but are not broadcast until `_present_ready_audience_gap_turn`.
- Events are marked injected only after the audience gap has been presented or explicitly skipped by an accepted failure policy.
- Prefetched main plan turns can be presented after an audience gap without regenerating them.
