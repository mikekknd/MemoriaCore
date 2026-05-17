# YouTubeBridge Audience Response Precache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pre-cache clean audience and Super Chat responses in the background so the audience gap can present ready content without starting LLM generation at the insertion point.

**Architecture:** Split audience handling into prepare-time and present-time gates. Safety-clean events are grouped and prepared as soon as possible into `director_audience_prepare` interactions; audience gap timing, cooldown, and planned-turn limits decide only when to present an already prepared batch.

**Tech Stack:** Python 3.12, SQLite, pytest, YouTubeBridge director loop, existing presentation queue/TTS pipeline.

---

## Problem Evidence

From the latest live DB:

- event `3005` became clean at `14:48:35`, but `director_audience_prepare` interaction `2134` was created only at `14:49:27`.
- event `3008` Super Chat became clean at `14:49:38`, but preparation started at `14:50:58`.
- LLM generation itself was short. Most delay came from waiting until the audience gap decision before starting response generation, then waiting in the playback queue.

The desired behavior is:

`live plan turn plays -> clean audience batch is prepared in background -> next safe gap presents prepared batch -> plan continues`

## File Structure

- Modify: `YouTubeBridge/engine_episode_plans.py`
  - Add a prepare decision that ignores presentation cooldown but still filters safe/unhandled events.
- Modify: `YouTubeBridge/engine_director_runtime.py`
  - Schedule prepare decisions continuously while plan/presentation continues.
  - Keep present decisions gated by cooldown and planned-turn limits.
- Modify: `YouTubeBridge/engine_injection.py`
  - Use the prepare decision from auto-inject loop without blocking on active playback.
- Modify: `YouTubeBridge/storage_repositories/interactions.py`
  - Add helpers to find event ids already covered by audience prepare jobs.
- Test: `YouTubeBridge/tests/test_bridge_engine_episode_plan_runtime.py`
  - Unit-test prepare vs present gate separation.
- Test: `YouTubeBridge/tests/test_bridge_engine_injection.py`
  - Runtime-test background prepare starts before gap is presentable.
- Test: `YouTubeBridge/tests/test_bridge_engine_director.py`
  - Presentation test that ready prepared batch is consumed at the next allowed gap.

## Task 1: Track Audience Events Already Covered By Prepare Jobs

**Files:**
- Modify: `YouTubeBridge/storage_repositories/interactions.py`
- Test: `YouTubeBridge/tests/test_storage.py`

- [ ] **Step 1: Write failing storage test**

Append to `YouTubeBridge/tests/test_storage.py`:

```python
def test_list_audience_prepare_event_ids_excludes_interrupted_and_failed(tmp_path):
    storage = BridgeStorage(tmp_path / "youtube_live.db")
    storage.upsert_connector({"connector_id": "yt-main", "display_name": "YT", "enabled": True})
    storage.upsert_session({"session_id": "live-a", "connector_id": "yt-main"})
    storage.create_interaction({
        "session_id": "live-a",
        "source": "director_audience_prepare",
        "status": "preparing",
        "event_ids": [1, 2],
        "content": "prepare",
    })
    storage.create_interaction({
        "session_id": "live-a",
        "source": "director_audience_prepare",
        "status": "prepared",
        "event_ids": [3],
        "content": "prepared",
    })
    storage.create_interaction({
        "session_id": "live-a",
        "source": "director_audience_prepare",
        "status": "interrupted",
        "event_ids": [4],
        "content": "interrupted",
    })
    storage.create_interaction({
        "session_id": "live-a",
        "source": "director",
        "status": "completed",
        "event_ids": [5],
        "content": "not prepare",
    })

    assert storage.list_audience_prepare_event_ids("live-a") == {1, 2, 3}
```

- [ ] **Step 2: Run failing test**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_storage.py::test_list_audience_prepare_event_ids_excludes_interrupted_and_failed -q
```

Expected: FAIL because `list_audience_prepare_event_ids()` does not exist.

- [ ] **Step 3: Implement storage helper**

In `YouTubeBridge/storage_repositories/interactions.py`, add:

```python
    def list_audience_prepare_event_ids(self, session_id: str) -> set[int]:
        rows = self.list_interactions(session_id, limit=500)
        covered: set[int] = set()
        active_statuses = {"preparing", "prepared", "presenting", "completed"}
        for row in rows:
            if row.get("source") != "director_audience_prepare":
                continue
            if str(row.get("status") or "") not in active_statuses:
                continue
            for raw_event_id in row.get("event_ids") or []:
                try:
                    event_id = int(raw_event_id)
                except (TypeError, ValueError):
                    continue
                if event_id > 0:
                    covered.add(event_id)
        return covered
```

- [ ] **Step 4: Verify storage test**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_storage.py::test_list_audience_prepare_event_ids_excludes_interrupted_and_failed -q
```

Expected: PASS.

## Task 2: Add A Prepare Decision Separate From Present Decision

**Files:**
- Modify: `YouTubeBridge/engine_episode_plans.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_episode_plan_runtime.py`

- [ ] **Step 1: Write failing episode-plan test**

Append to `YouTubeBridge/tests/test_bridge_engine_episode_plan_runtime.py`:

```python
def test_audience_prepare_decision_ignores_gap_cooldown_but_present_decision_blocks():
    storage = BridgeStorage(":memory:")
    storage.upsert_connector({"connector_id": "yt-main", "display_name": "YT", "enabled": True})
    plan = sample_plan()
    storage.upsert_live_episode_plan(plan)
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "yt-main",
        "target_memoria_session_id": "mem-a",
        "character_ids": ["host-a", "analyst-b", "skeptic-c"],
        "episode_plan_id": plan["plan_id"],
        "presentation_enabled": True,
        "director_audience_interrupt_cooldown_seconds": 999,
        "director_max_audience_batches_per_planned_turn": 1,
    })
    manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeSafetyMemoriaClient)
    event = storage.save_event({
        "bridge_session_id": "live-a",
        "connector_id": "yt-main",
        "youtube_message_id": "comment-a",
        "message_type": "textMessageEvent",
        "author_display_name": "觀眾A",
        "message_text": "這段可以補充嗎？",
        "safety_status": "completed",
        "safety_label": "clean",
        "safe_message_text": "這段可以補充嗎？",
        "status": "active",
    })
    state = storage.update_director_state(
        "live-a",
        status="running",
        metadata={"last_audience_gap_at": datetime.now().isoformat()},
    )

    prepare = manager._episode_plan_next_audience_prepare_decision(
        storage.get_session("live-a"),
        state,
    )
    present = manager._episode_plan_next_audience_gap_decision(
        storage.get_session("live-a"),
        state,
    )

    assert prepare["action"] == "reply_chat_batch"
    assert prepare["episode_plan"]["mode"] == "audience_gap_prepare"
    assert prepare["episode_plan"]["interrupt_state"]["source_event_ids"] == [event["id"]]
    assert present is None
```

- [ ] **Step 2: Run failing test**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_episode_plan_runtime.py::test_audience_prepare_decision_ignores_gap_cooldown_but_present_decision_blocks -q
```

Expected: FAIL because `_episode_plan_next_audience_prepare_decision()` does not exist.

- [ ] **Step 3: Implement prepare decision**

In `YouTubeBridge/engine_episode_plans.py`, add this method after `_episode_plan_next_audience_gap_decision()`:

```python
    def _episode_plan_next_audience_prepare_decision(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any] | None:
        plan, planned_state = self._episode_plan_and_state(session, state)
        if not plan:
            return None
        if str(planned_state.get("plan_status") or "") == "completed":
            return None
        completed_events = self._episode_completed_audience_events(
            session["session_id"],
            planned_state,
            limit=500,
        )
        covered_ids: set[int] = set()
        finder = getattr(self.storage, "list_audience_prepare_event_ids", None)
        if callable(finder):
            covered_ids = finder(session["session_id"])
        available_events = [
            event for event in completed_events
            if int(event.get("id") or 0) not in covered_ids
        ]
        selected_events = self._episode_select_audience_event_batch(session, available_events)
        if not selected_events:
            return None
        snapshot = self._episode_audience_backlog_snapshot(completed_events, selected_events)
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
        payload["mode"] = "audience_gap_prepare"
        payload["backlog_snapshot"] = snapshot
        return decision
```

- [ ] **Step 4: Verify prepare decision test**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_episode_plan_runtime.py::test_audience_prepare_decision_ignores_gap_cooldown_but_present_decision_blocks -q
```

Expected: PASS.

## Task 3: Schedule Background Prepare Immediately

**Files:**
- Modify: `YouTubeBridge/engine_director_runtime.py`
- Modify: `YouTubeBridge/engine_injection.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_injection.py`

- [ ] **Step 1: Write failing runtime test**

Append to `YouTubeBridge/tests/test_bridge_engine_injection.py`:

```python
@pytest.mark.asyncio
async def test_auto_inject_prepares_audience_response_even_when_gap_cooldown_blocks_present(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({"connector_id": "yt-main", "display_name": "YT", "enabled": True})
        plan = sample_plan()
        storage.upsert_live_episode_plan(plan)
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
            "episode_plan_id": plan["plan_id"],
            "status": "running",
            "presentation_enabled": True,
            "auto_inject": True,
            "min_pending_events": 1,
            "max_pending_events": 5,
            "inject_interval_seconds": 1,
            "director_audience_interrupt_cooldown_seconds": 999,
        })
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "comment-a",
            "message_type": "textMessageEvent",
            "author_display_name": "觀眾A",
            "message_text": "這段可以補充嗎？",
            "safety_status": "completed",
            "safety_label": "clean",
            "safe_message_text": "這段可以補充嗎？",
            "status": "active",
        })
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            status="running",
            metadata={"last_audience_gap_at": datetime.now().isoformat()},
        )
        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeSafetyMemoriaClient)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        manager._runtimes["live-a"] = runtime
        emitted = []
        ready = asyncio.Event()

        async def capture_broadcast(_session_id, payload):
            emitted.append(payload)
            if payload.get("type") == "director_audience_gap_ready":
                runtime.running = False
                ready.set()

        async def fake_prepare_next_audience_gap_turn(_runtime, _session, _state, *, decision=None):
            assert decision["episode_plan"]["mode"] == "audience_gap_prepare"
            return {
                "interaction": {
                    "job_id": "prepared-job",
                    "source": "director_audience_prepare",
                    "status": "prepared",
                },
            }

        monkeypatch.setattr(manager, "_broadcast", capture_broadcast)
        monkeypatch.setattr(manager, "_prepare_next_audience_gap_turn", fake_prepare_next_audience_gap_turn)
        monkeypatch.setattr(manager, "_auto_inject_delay", lambda *_args, **_kwargs: 0.01)

        await asyncio.wait_for(manager._auto_inject_loop(runtime), timeout=1)

        assert ready.is_set()
        assert storage.get_events_by_ids("live-a", [event["id"]])[0]["injected_at"] == ""
        assert any(payload.get("type") == "director_audience_gap_ready" for payload in emitted)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 2: Run failing test**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_injection.py::test_auto_inject_prepares_audience_response_even_when_gap_cooldown_blocks_present -q
```

Expected: FAIL because schedule uses `_episode_plan_next_audience_gap_decision()`, which is blocked by cooldown.

- [ ] **Step 3: Use prepare decision in scheduler**

In `YouTubeBridge/engine_director_runtime.py`, change `_schedule_audience_gap_prepare_if_needed()`:

```python
        decision = self._episode_plan_next_audience_prepare_decision(session, state)
```

Keep the broadcast event name unchanged: `director_audience_gap_ready` still means "prepared and ready for future presentation".

- [ ] **Step 4: Normalize prepare mode during send**

In `_send_director_turn()`, update audience-gap detection:

```python
        is_audience_gap_turn = (
            episode_plan_mode in {"audience_gap", "audience_gap_prepare"}
            or (prepare_only and source_name == "director_audience_prepare")
        )
```

- [ ] **Step 5: Verify runtime test**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_injection.py::test_auto_inject_prepares_audience_response_even_when_gap_cooldown_blocks_present -q
```

Expected: PASS.

## Task 4: Enforce Present-Time Gate

**Files:**
- Modify: `YouTubeBridge/engine_director_runtime.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_director.py`

- [ ] **Step 1: Write failing present-gate test**

Append:

```python
@pytest.mark.asyncio
async def test_prepared_audience_gap_waits_until_present_gate_allows(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({"connector_id": "yt-main", "display_name": "YT", "enabled": True})
        plan = sample_plan()
        storage.upsert_live_episode_plan(plan)
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["host-a", "analyst-b", "skeptic-c"],
            "episode_plan_id": plan["plan_id"],
            "status": "running",
            "presentation_enabled": True,
            "director_audience_interrupt_cooldown_seconds": 999,
        })
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "comment-a",
            "message_type": "textMessageEvent",
            "author_display_name": "觀眾A",
            "message_text": "這段可以補充嗎？",
            "safety_status": "completed",
            "safety_label": "clean",
            "safe_message_text": "這段可以補充嗎？",
            "status": "active",
        })
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_audience_prepare",
            "status": "prepared",
            "event_ids": [event["id"]],
            "content": "prepared",
            "metadata": {
                "decision": {
                    "action": "reply_chat_batch",
                    "episode_plan": {
                        "mode": "audience_gap_prepare",
                        "interrupt_state": {"source_event_ids": [event["id"]]},
                    },
                },
            },
        })
        storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "msg-a",
            "character_id": "host-a",
            "character_name": "主持A",
            "status": "ready",
            "text": "先回應觀眾。",
            "metadata": {"source": "director_audience_prepare"},
        })
        state = storage.update_director_state(
            "live-a",
            status="running",
            metadata={"last_audience_gap_at": datetime.now().isoformat()},
        )
        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeSafetyMemoriaClient)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        result = await manager._present_ready_audience_gap_turn(runtime, storage.get_session("live-a"), state)

        assert result is None
        assert storage.get_interaction(interaction["job_id"])["status"] == "prepared"
        assert storage.get_events_by_ids("live-a", [event["id"]])[0]["injected_at"] == ""
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 2: Run failing test**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_director.py::test_prepared_audience_gap_waits_until_present_gate_allows -q
```

Expected: FAIL because `_present_ready_audience_gap_turn()` presents any prepared interaction.

- [ ] **Step 3: Add present gate**

In `_present_ready_audience_gap_turn()`, after finding `interaction`, extract event ids and block if `_episode_audience_gap_block_reason()` returns a reason:

```python
        event_ids = []
        for raw_event_id in interaction.get("event_ids") or []:
            try:
                event_id = int(raw_event_id)
            except (TypeError, ValueError):
                continue
            if event_id > 0:
                event_ids.append(event_id)
        events = self.storage.get_events_by_ids(runtime.session_id, event_ids) if event_ids else []
        block_reason = self._episode_audience_gap_block_reason(session, state, events)
        if block_reason:
            _director_timing_log(
                "audience_gap_present_deferred",
                session_id=runtime.session_id,
                reason=block_reason,
                event_ids=event_ids,
            )
            return None
```

- [ ] **Step 4: Verify present gate**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_director.py::test_prepared_audience_gap_waits_until_present_gate_allows -q
```

Expected: PASS.

## Task 5: Add Timing Metadata To Distinguish LLM, TTS, And Queue Delay

**Files:**
- Modify: `YouTubeBridge/engine_director_runtime.py`
- Modify: `YouTubeBridge/bridge_engine.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_director.py`

- [ ] **Step 1: Add assertions to existing audience prepare test**

In `test_audience_gap_prepare_uses_sidecar_session_without_injecting`, after `result = ...`, add:

```python
        interaction_metadata = result["interaction"]["metadata"]
        assert interaction_metadata["prepare_ready"] is True
        assert interaction_metadata["audience_prepare_started_at"]
        assert interaction_metadata["audience_prepare_completed_at"]
        assert interaction_metadata["prepared_result_count"] == 1
```

- [ ] **Step 2: Run failing test**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_director.py::test_audience_gap_prepare_uses_sidecar_session_without_injecting -q
```

Expected: FAIL because metadata fields are missing.

- [ ] **Step 3: Write metadata in `_send_director_turn()`**

Set a timestamp before the Memoria call:

```python
        audience_prepare_started_at = datetime.now().isoformat() if prepare_only else ""
```

In `update_fields["metadata"]` for `prepare_only`, include:

```python
                    "audience_prepare_started_at": audience_prepare_started_at,
                    "audience_prepare_completed_at": datetime.now().isoformat() if prepare_only else "",
```

- [ ] **Step 4: Verify metadata test**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_director.py::test_audience_gap_prepare_uses_sidecar_session_without_injecting -q
```

Expected: PASS.

## Task 6: Verification Suite

**Files:**
- No new files.

- [ ] **Step 1: Run focused tests**

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_episode_plan_runtime.py YouTubeBridge\tests\test_bridge_engine_director.py YouTubeBridge\tests\test_bridge_engine_injection.py YouTubeBridge\tests\test_storage.py -q
```

Expected: PASS.

- [ ] **Step 2: Commit**

```powershell
git add YouTubeBridge/engine_episode_plans.py YouTubeBridge/engine_director_runtime.py YouTubeBridge/engine_injection.py YouTubeBridge/storage_repositories/interactions.py YouTubeBridge/tests/test_bridge_engine_episode_plan_runtime.py YouTubeBridge/tests/test_bridge_engine_director.py YouTubeBridge/tests/test_bridge_engine_injection.py YouTubeBridge/tests/test_storage.py
git commit -m "fix: pre-cache YouTube audience responses before gaps"
```

## Self-Review

- Spec coverage: The plan pre-caches clean audience batches, keeps presentation gated, and adds timing evidence to distinguish generation, TTS, and queue delay.
- Placeholder scan: No TBD/TODO placeholders.
- Type consistency: `audience_gap_prepare` is introduced once and handled in both scheduler and sender.

