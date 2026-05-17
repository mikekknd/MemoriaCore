# YouTubeBridge Main Thread Audience Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 YouTubeBridge 直播流程改成「主線 Main Thread 只播放 planned/prefetched turn；所有普通留言與 SC 都先進獨立 audience preprocessing queue，完成角色回應與 TTS 後，只在 planned turn 間隔插入」。

**Architecture:** 新增一條長駐 audience preprocessing worker task，負責收集已安全分類的普通留言與 SC、依 SC priority 排序、生成角色回應、預先完成 TTS，並把 ready batch 交給主線。主線 director loop 在每個 planned turn 完整播放且 ACK 後，只做三件事：播放 ready audience batch、等待 prefetched planned turn ready、播放 prefetched planned turn；manual finalize 只封存入口並 drain 已開始/已準備的流程，不再 interrupt 正在播放或生成中的內容。

**Tech Stack:** Python 3.12、FastAPI async runtime、SQLite storage、pytest、YouTubeBridge presentation/TTS pipeline、Studio browser ACK flow。

---

## Scope

本計畫只處理 YouTubeBridge V1.5 目前路徑，不改 YouTubeBridgeV2。

必須滿足的行為：

- 普通留言與 Super Chat 都只能進 audience preprocessing queue。
- Super Chat 只能提高 queue priority，不可呼叫 `interrupt_session()`。
- 角色回應留言與 TTS 必須由獨立 background worker 預先完成。
- Main thread 只在 planned turn 完整播放並收到 ACK 後，拉取 ready audience batch。
- 沒有 ready audience batch 時，要等待 prefetched planned turn ready 後再播放；只有沒有 TTS 或 timeout 時才 fallback 直接生成/播放。
- 正常流程不應留下未播放的 prefetch / audience ready item。
- 手動結束直播代表「停止接受新留言、停止產生新的 planned turn」，但不得中斷目前正在播放或已準備要播放的 planned/audience response；要 drain 後才進 closing thanks / final closing。

## File Map

- Modify: `YouTubeBridge/bridge_runtime.py`
  - 新增 audience worker task、queue wake event、graceful drain flags。
- Modify: `YouTubeBridge/engine_runtime_lifecycle.py`
  - 啟動/停止 audience preprocessing worker；closing 時只封存入口，不取消正在 drain 的 presentation。
- Modify: `YouTubeBridge/engine_injection.py`
  - 移除 live event / SC 對 `interrupt_session()` 的依賴；把 auto inject 與 SC 轉成 audience queue wake-up。
- Modify: `YouTubeBridge/engine_director_runtime.py`
  - 將 audience gap prepare 改成 worker-owned；main thread 在 planned/prefetched turn ACK 後 consume ready audience batch，再等待/播放 prefetch。
- Modify: `YouTubeBridge/engine_episode_plans.py`
  - 保留 presentation mode 下 `presentation_audience_gap_lane`，但讓 audience prepare decision 可由 worker 穩定讀取。
- Modify: `YouTubeBridge/bridge_engine.py`
  - 區分「播放 timeout / 手動 skip」和「未消費 ready item」；正常 drain 不應把未播放 ready item 標成 skipped。
- Modify: `YouTubeBridge/engine_closing.py`
  - manual finalize 進入 graceful drain，不再先 `_stop_runtime_background_tasks_for_closing()` / `_interrupt_active_generation_for_closing()`。
- Modify: `YouTubeBridge/static/ui/studio.js`
  - 確認 graceful closing 不觸發 `handlePresentationInterrupt()`；只在真正 interrupt_requested 時停止/skip current item。
- Test: `YouTubeBridge/tests/test_bridge_engine_injection.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_director.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_closing.py`
- Test: `YouTubeBridge/tests/test_presentation_queue.py`
- Optional Test: `YouTubeBridge/tests/test_studio_ui.py`

## Terms

- **Audience preprocessing queue:** 程式上是一條 `asyncio.Task` worker，不是 OS thread。它是與 main director loop 分離的背景工作線。
- **Ready audience batch:** `source="director_audience_prepare"` 且 interaction status 為 `prepared`，presentation items status 為 `ready`。
- **Ready prefetch turn:** `source="director_prefetch"` 且 interaction status 為 `prefetched`，presentation items status 為 `ready`。
- **Drain:** 停止接收新留言與新 planned generation，但完成目前已開始或已準備的 presentation，再進 closing。

---

### Task 1: Lock The No-Interrupt Contract With Failing Tests

**Files:**
- Modify: `YouTubeBridge/tests/test_bridge_engine_injection.py`
- Modify: `YouTubeBridge/tests/test_bridge_engine_director.py`
- Modify: `YouTubeBridge/tests/test_bridge_engine_closing.py`

- [ ] **Step 1: Add a failing test that SC never interrupts active planned generation in presentation episode sessions**

Append this test to `YouTubeBridge/tests/test_bridge_engine_injection.py`.

```python
@pytest.mark.asyncio
async def test_super_chat_enters_audience_queue_without_interrupting_active_planned_turn(monkeypatch):
    with temp_storage() as storage:
        storage.upsert_connector({
            "connector_id": "yt",
            "name": "YouTube",
            "api_key": "key",
            "enabled": True,
        })
        plan = sample_plan()
        storage.upsert_live_episode_plan(plan)
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt",
            "display_name": "Live A",
            "status": "running",
            "presentation_enabled": True,
            "tts_enabled": True,
            "auto_inject": True,
            "episode_plan_id": plan["plan_id"],
            "target_memoria_session_id": "mem-a",
            "character_ids": ["char-a"],
            "max_sc_per_batch": 3,
        })
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            metadata={"planned_state": initial_planned_state(plan)},
        )
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        active = storage.create_interaction({
            "session_id": "live-a",
            "source": "director",
            "priority": 50,
            "status": "running",
            "event_ids": [],
            "memoria_session_id": "mem-a",
            "character_ids": ["char-a"],
            "content": "planned turn running",
            "metadata": {"decision": {"episode_plan": {"mode": "planned_turn"}}},
        })
        event = storage.save_event({
            "session_id": "live-a",
            "connector_id": "yt",
            "youtube_message_id": "sc-no-interrupt-1",
            "author_display_name": "SC viewer",
            "message_text": "這段可以補充嗎？",
            "priority_class": "super_chat",
            "amount_micros": 5000000,
            "amount_display_string": "NT$150",
            "sc_tier": 3,
            "status": "active",
        })
        storage.update_event_safety(
            int(event["id"]),
            status="completed",
            label="clean",
            safe_message_text="這段可以補充嗎？",
            safety_summary="clean question",
            reason="test",
            confidence=1.0,
        )
        manager = YouTubeBridgeManager(storage)

        async def fail_interrupt(*_args, **_kwargs):
            raise AssertionError("SC must not interrupt active planned turn in presentation episode mode")

        monkeypatch.setattr(manager, "interrupt_session", fail_interrupt)
        result = await manager._prepare_director_owned_auto_inject(
            runtime,
            storage.get_session("live-a"),
            storage.list_events("live-a", uninjected_only=True),
            max_events=12,
            max_sc_per_batch=3,
            active=active,
        )

        assert result["selected_source"] == "super_chat"
        assert result["selected_event_ids"] == [event["id"]]
        assert storage.get_interaction(active["job_id"])["status"] == "running"
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_injection.py::test_super_chat_enters_audience_queue_without_interrupting_active_planned_turn -q --basetemp=.pyTestTemp/basetemp-audience-queue
```

Expected before implementation: FAIL if imports/helpers need adjustment or if the current path still allows SC interruption through the auto inject loop. If it unexpectedly passes, keep it as a regression and continue to the stricter tests below.

- [ ] **Step 3: Add a failing test that main thread presents audience only after planned ACK**

Append this test to `YouTubeBridge/tests/test_bridge_engine_director.py`.

```python
@pytest.mark.asyncio
async def test_main_thread_presents_ready_audience_only_after_planned_turn_ack(monkeypatch):
    with temp_storage() as storage:
        storage.upsert_connector({"connector_id": "yt", "name": "YouTube", "api_key": "key", "enabled": True})
        plan = sample_plan()
        storage.upsert_live_episode_plan(plan)
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt",
            "display_name": "Live A",
            "status": "running",
            "presentation_enabled": True,
            "tts_enabled": True,
            "presentation_ack_timeout_seconds": 5,
            "episode_plan_id": plan["plan_id"],
            "target_memoria_session_id": "mem-a",
            "character_ids": ["char-a"],
        })
        state = storage.update_director_state(
            "live-a",
            director_enabled=True,
            metadata={"planned_state": initial_planned_state(plan)},
        )
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        manager = YouTubeBridgeManager(storage, tts_provider_factory=lambda: FakeTTSProvider())
        queue = asyncio.Queue()
        manager.subscribe("live-a", queue)

        audience_interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_audience_prepare",
            "priority": 45,
            "status": "prepared",
            "event_ids": [101],
            "memoria_session_id": "mem-a:audience",
            "character_ids": ["char-a"],
            "content": "audience reply",
            "metadata": {"prepare_only": True, "decision": {"action": "reply_chat_batch", "episode_plan": {"mode": "audience_gap"}}},
        })
        audience_item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": audience_interaction["job_id"],
            "message_id": "audience-msg:0",
            "character_id": "char-a",
            "character_name": "角色A",
            "sequence_index": 0,
            "text": "先回應觀眾這句。",
            "status": "ready",
            "audio_path": "ready.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_audience_prepare"},
        })

        planned_message = {
            "message_id": "planned-msg",
            "role": "assistant",
            "content": "主線 planned turn。",
            "character_id": "char-a",
            "character_name": "角色A",
        }
        planned_item = await manager._prepare_presentation_item(
            storage.get_session("live-a"),
            planned_message,
            "主線 planned turn。",
            index=0,
            source="director",
            interaction_job_id="planned-job",
            runtime=runtime,
        )
        present_task = asyncio.create_task(manager.present_prepared_stream_results(
            "live-a",
            [{"message": planned_message, "items": [planned_item]}],
            source="director",
            interaction_job_id="planned-job",
        ))
        ready = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert ready["item"]["item_id"] == planned_item["item_id"]
        assert storage.get_interaction(audience_interaction["job_id"])["status"] == "prepared"

        await manager.ack_presentation_item("live-a", planned_item["item_id"])
        await present_task
        await manager._present_ready_audience_gap_turn(runtime, storage.get_session("live-a"), state)

        audience_ready = await _next_queue_event(queue, "presentation_item_ready", timeout=1)
        assert audience_ready["item"]["item_id"] == audience_item["item_id"]
```

- [ ] **Step 4: Run the failing director ordering test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_director.py::test_main_thread_presents_ready_audience_only_after_planned_turn_ack -q --basetemp=.pyTestTemp/basetemp-audience-queue
```

Expected before implementation: likely PASS for the direct helper, but it anchors the required ordering. If imports for `FakeTTSProvider` are not available in this file, copy the existing fake from `test_presentation_queue.py` exactly.

- [ ] **Step 5: Add a failing test that manual finalize drains instead of interrupting current presentation**

Append this test to `YouTubeBridge/tests/test_bridge_engine_closing.py`.

```python
@pytest.mark.asyncio
async def test_manual_finalize_enters_graceful_drain_without_interrupting_presenting_item(monkeypatch):
    with temp_storage() as storage:
        storage.upsert_connector({"connector_id": "yt", "name": "YouTube", "api_key": "key", "enabled": True})
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt",
            "display_name": "Live A",
            "status": "running",
            "presentation_enabled": True,
            "tts_enabled": True,
            "target_memoria_session_id": "mem-a",
            "character_ids": ["char-a"],
        })
        item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": "job-presenting",
            "message_id": "planned-msg:0",
            "character_id": "char-a",
            "character_name": "角色A",
            "sequence_index": 0,
            "text": "正在播放的 planned turn。",
            "status": "presenting",
            "audio_path": "planned.wav",
            "audio_format": "wav",
            "metadata": {"source": "director"},
        })
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeClosingMemoriaClient)
        manager._runtimes["live-a"] = runtime

        async def fail_interrupt(*_args, **_kwargs):
            raise AssertionError("manual finalize must not interrupt currently presenting item")

        monkeypatch.setattr(manager, "_interrupt_active_generation_for_closing", fail_interrupt)
        monkeypatch.setattr(manager, "_drain_live_session_before_closing", AsyncMock(return_value={"status": "drained"}))
        monkeypatch.setattr(manager, "_run_final_closing_turn", AsyncMock(return_value={"status": "completed"}))
        monkeypatch.setattr(manager, "_resolve_pending_safety_for_closing", AsyncMock(return_value={"status": "no_pending"}))
        monkeypatch.setattr(manager, "run_closing_super_chat_thanks", AsyncMock(return_value={"status": "skipped", "reason": "no_unhandled_super_chats"}))

        await manager.finalize_session("live-a", finalized_by="manual_finalize")

        refreshed = storage.get_presentation_item(item["item_id"])
        assert refreshed["status"] == "presenting"
        assert manager._drain_live_session_before_closing.await_count == 1
```

- [ ] **Step 6: Run the failing closing drain test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_closing.py::test_manual_finalize_enters_graceful_drain_without_interrupting_presenting_item -q --basetemp=.pyTestTemp/basetemp-audience-queue
```

Expected before implementation: FAIL because `_drain_live_session_before_closing` does not exist and current closing calls interrupt/stop first.

- [ ] **Step 7: Commit tests**

```powershell
git add YouTubeBridge/tests/test_bridge_engine_injection.py YouTubeBridge/tests/test_bridge_engine_director.py YouTubeBridge/tests/test_bridge_engine_closing.py
git commit -m "test: lock YouTubeBridge audience queue sequencing"
```

Expected: commit succeeds with only test files staged.

---

### Task 2: Add Runtime Flags For Audience Worker And Graceful Drain

**Files:**
- Modify: `YouTubeBridge/bridge_runtime.py`
- Modify: `YouTubeBridge/engine_runtime_lifecycle.py`

- [ ] **Step 1: Add runtime fields**

In `YouTubeBridge/bridge_runtime.py`, update `LiveRuntime` with these fields after `audience_gap_prepare_task`.

```python
    audience_preprocess_task: asyncio.Task | None = None
    audience_preprocess_wake: asyncio.Event = field(default_factory=asyncio.Event)
    accepting_audience_events: bool = True
    stop_after_current_turn: bool = False
    graceful_closing_requested: bool = False
    drain_started_at: str = ""
```

- [ ] **Step 2: Initialize flags on session start**

In `YouTubeBridge/engine_runtime_lifecycle.py:start_session()`, after `runtime.running = True`, add:

```python
            runtime.accepting_audience_events = True
            runtime.stop_after_current_turn = False
            runtime.graceful_closing_requested = False
            runtime.drain_started_at = ""
            runtime.audience_preprocess_wake.set()
```

- [ ] **Step 3: Start the audience preprocessing worker when director episode flow is enabled**

In `start_session()`, after creating `runtime.inject_task`, add:

```python
            if self._audience_preprocessing_enabled(session):
                runtime.audience_preprocess_task = asyncio.create_task(
                    self._audience_preprocessing_loop(runtime)
                )
```

This method will be implemented in Task 3.

- [ ] **Step 4: Stop the worker in hard stop only**

In `stop_session()`, where existing tasks are cancelled, cancel `audience_preprocess_task` with the same pattern as `audience_gap_prepare_task`.

```python
            if runtime and runtime.audience_preprocess_task:
                runtime.audience_preprocess_task.cancel()
                try:
                    await runtime.audience_preprocess_task
                except asyncio.CancelledError:
                    pass
```

In `_stop_runtime_background_tasks_for_closing()`, do not cancel this task yet. Replace the existing cancellation list with a hard-stop helper in Task 7. For now add only:

```python
        await self._cancel_runtime_task(runtime, "audience_preprocess_task")
```

This will be moved behind the graceful drain in Task 7.

- [ ] **Step 5: Run runtime smoke tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_lifecycle.py YouTubeBridge/tests/test_bridge_engine_split_modules.py -q --basetemp=.pyTestTemp/basetemp-audience-queue
```

Expected: tests may fail because `_audience_preprocessing_loop` is not implemented yet. If failure is only missing method, continue to Task 3.

- [ ] **Step 6: Commit runtime field change**

```powershell
git add YouTubeBridge/bridge_runtime.py YouTubeBridge/engine_runtime_lifecycle.py
git commit -m "feat: add audience preprocessing runtime state"
```

Expected: commit succeeds after Task 3 makes tests pass; do not commit while missing-method tests are red.

---

### Task 3: Implement The Audience Preprocessing Worker

**Files:**
- Modify: `YouTubeBridge/engine_injection.py`
- Modify: `YouTubeBridge/engine_director_runtime.py`

- [ ] **Step 1: Add the preprocessing gate**

In `YouTubeBridge/engine_injection.py`, add this helper near `_director_owns_auto_inject()`:

```python
    def _audience_preprocessing_enabled(self, session: dict[str, Any]) -> bool:
        return bool(
            self._presentation_enabled(session)
            and self._episode_plan_for_session(session)
        )

    def _audience_preprocessing_accepts_events(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
    ) -> bool:
        if not self._audience_preprocessing_enabled(session):
            return False
        if not runtime.running:
            return False
        if runtime.graceful_closing_requested or not runtime.accepting_audience_events:
            return False
        if str(runtime.status or "") in {"closing", "ended", "stopped"}:
            return False
        if str(session.get("status") or "") in {"closing", "ended", "stopped"}:
            return False
        return True
```

- [ ] **Step 2: Add worker loop**

In `YouTubeBridge/engine_director_runtime.py`, add this method before `_schedule_audience_gap_prepare_if_needed()`:

```python
    async def _audience_preprocessing_loop(self, runtime: LiveRuntime) -> None:
        while runtime.running:
            session = self.storage.get_session(runtime.session_id)
            if not session:
                return
            if not self._audience_preprocessing_enabled(session):
                await asyncio.sleep(1.0)
                continue
            if not self._audience_preprocessing_accepts_events(runtime, session):
                await asyncio.sleep(0.5)
                continue
            try:
                state = self.storage.get_director_state(runtime.session_id)
                prepared = await self._prepare_next_audience_gap_turn(
                    runtime,
                    session,
                    state,
                )
                if prepared:
                    runtime.audience_preprocess_wake.set()
                    await self._broadcast(runtime.session_id, {
                        "type": "director_audience_preprocessed",
                        "interaction": prepared.get("interaction", {}),
                    })
                    await asyncio.sleep(0.2)
                    continue
                runtime.audience_preprocess_wake.clear()
                try:
                    await asyncio.wait_for(runtime.audience_preprocess_wake.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "audience preprocessing failed session_id=%s error=%s",
                    runtime.session_id,
                    exc,
                    exc_info=True,
                )
                latest_state = self.storage.get_director_state(runtime.session_id) or {}
                metadata = dict(latest_state.get("metadata") if isinstance(latest_state.get("metadata"), dict) else {})
                metadata["last_audience_prepare_error"] = str(exc)[:500]
                self.storage.update_director_state(runtime.session_id, metadata=metadata)
                await asyncio.sleep(1.0)
```

- [ ] **Step 3: Make event classification wake the worker**

In `engine_event_safety.py:_classify_event_batch()`, after broadcasting a displayable event, wake the runtime worker:

```python
                runtime = self._runtimes.get(session_id)
                if runtime and runtime.audience_preprocess_wake:
                    runtime.audience_preprocess_wake.set()
```

If `engine_event_safety.py` does not import `LiveRuntime`, no import is needed because it only reads `self._runtimes`.

- [ ] **Step 4: Make `_prepare_next_audience_gap_turn()` mark finished on all outcomes**

Wrap its `_send_director_turn(... prepare_only=True ...)` call in try/finally. Replace the method body from `result = await self._send_director_turn(` through `return result` with:

```python
        result: dict[str, Any] | None = None
        try:
            result = await self._send_director_turn(
                audience_session,
                state,
                decision,
                prepare_only=True,
                prepare_source="director_audience_prepare",
            )
            result_session_id = str((result.get("memoria_result") or {}).get("session_id") or "")
            if result_session_id:
                latest_state = self.storage.get_director_state(runtime.session_id) or {}
                next_metadata = dict(
                    latest_state.get("metadata")
                    if isinstance(latest_state.get("metadata"), dict)
                    else {}
                )
                next_metadata["audience_sidecar_memoria_session_id"] = result_session_id
                next_metadata["audience_prepare_in_flight"] = False
                next_metadata["latest_audience_gap_job_id"] = result.get("interaction", {}).get("job_id", "")
                self.storage.update_director_state(runtime.session_id, metadata=next_metadata)
            return result
        except Exception as exc:
            self._mark_director_audience_prepare_finished(
                runtime.session_id,
                error=str(exc)[:500],
            )
            raise
```

- [ ] **Step 5: Run worker-specific tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_director.py::test_audience_gap_prepare_schedules_background_turn YouTubeBridge/tests/test_bridge_engine_director.py::test_audience_gap_prepare_skips_when_prepare_in_flight -q --basetemp=.pyTestTemp/basetemp-audience-queue
```

Expected: PASS after adjusting exact test names if they differ. Use `rg -n "audience_gap_prepare" YouTubeBridge/tests/test_bridge_engine_director.py` to select existing adjacent tests.

- [ ] **Step 6: Commit worker implementation**

```powershell
git add YouTubeBridge/engine_injection.py YouTubeBridge/engine_director_runtime.py YouTubeBridge/engine_event_safety.py
git commit -m "feat: preprocess audience replies in background"
```

---

### Task 4: Remove Live Event And SC Interrupt Entry Points

**Files:**
- Modify: `YouTubeBridge/engine_injection.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_injection.py`

- [ ] **Step 1: Add a test that `inject_recent()` refuses interrupt-style SC in preprocessing mode**

Append this test to `YouTubeBridge/tests/test_bridge_engine_injection.py`.

```python
@pytest.mark.asyncio
async def test_inject_recent_super_chat_routes_to_preprocessing_queue_when_enabled(monkeypatch):
    with temp_storage() as storage:
        storage.upsert_connector({"connector_id": "yt", "name": "YouTube", "api_key": "key", "enabled": True})
        plan = sample_plan()
        storage.upsert_live_episode_plan(plan)
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt",
            "display_name": "Live A",
            "status": "running",
            "presentation_enabled": True,
            "tts_enabled": True,
            "episode_plan_id": plan["plan_id"],
            "target_memoria_session_id": "mem-a",
            "character_ids": ["char-a"],
        })
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            metadata={"planned_state": initial_planned_state(plan)},
        )
        active = storage.create_interaction({
            "session_id": "live-a",
            "source": "director",
            "priority": 50,
            "status": "running",
            "event_ids": [],
            "memoria_session_id": "mem-a",
            "character_ids": ["char-a"],
            "content": "planned running",
            "metadata": {},
        })
        event = storage.save_event({
            "session_id": "live-a",
            "connector_id": "yt",
            "youtube_message_id": "sc-route-1",
            "author_display_name": "SC viewer",
            "message_text": "SC question",
            "priority_class": "super_chat",
            "amount_display_string": "NT$150",
            "sc_tier": 3,
            "status": "active",
        })
        storage.update_event_safety(
            int(event["id"]),
            status="completed",
            label="clean",
            safe_message_text="SC question",
            safety_summary="clean",
            reason="test",
            confidence=1.0,
        )
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        manager._runtimes["live-a"] = runtime

        async def fail_interrupt(*_args, **_kwargs):
            raise AssertionError("inject_recent must not interrupt in preprocessing mode")

        monkeypatch.setattr(manager, "interrupt_session", fail_interrupt)
        result = await manager.inject_recent(
            "live-a",
            event_ids=[event["id"]],
            source="super_chat",
            priority=320,
        )

        assert result["interaction"]["status"] == "queued_for_audience_preprocessing"
        assert result["summary"]["event_ids"] == [event["id"]]
        assert storage.get_interaction(active["job_id"])["status"] == "running"
        assert runtime.audience_preprocess_wake.is_set()
```

- [ ] **Step 2: Run the failing SC routing test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_injection.py::test_inject_recent_super_chat_routes_to_preprocessing_queue_when_enabled -q --basetemp=.pyTestTemp/basetemp-audience-queue
```

Expected before implementation: FAIL because `inject_recent()` currently interrupts active higher-priority jobs.

- [ ] **Step 3: Add early redirect in `inject_recent()`**

At the top of `inject_recent()`, immediately after `runtime = ...`, add:

```python
        session = self.storage.get_session(session_id)
        if (
            session
            and event_ids
            and self._audience_preprocessing_enabled(session)
            and source in {"auto_inject", "super_chat", "manual_inject"}
        ):
            runtime.audience_preprocess_wake.set()
            summary = {"event_ids": list(event_ids), "source": source, "queued_for_audience_preprocessing": True}
            interaction = {
                "session_id": session_id,
                "source": source,
                "priority": priority,
                "status": "queued_for_audience_preprocessing",
                "event_ids": list(event_ids),
            }
            await self._broadcast(session_id, {
                "type": "audience_preprocessing_queued",
                "event_ids": list(event_ids),
                "source": source,
                "priority": priority,
            })
            return {
                "summary": summary,
                "marked_injected": 0,
                "memoria_result": {},
                "interaction": interaction,
                "injected_at": datetime.now().isoformat(),
            }
```

This intentionally runs before:

```python
        active = self.storage.get_active_interaction(session_id)
        if active and active.get("status") == "running" and int(priority) > int(active.get("priority", 100)):
            await self.interrupt_session(session_id, reason=f"higher_priority:{source}")
```

- [ ] **Step 4: In `_auto_inject_loop()`, wake preprocessing instead of calling `inject_recent()` in preprocessing mode**

Inside `_auto_inject_loop()`, before the legacy `selected = self._select_pending_events_for_injection(...)` block, keep the existing director-owned branch but change it to wake the new worker:

```python
                    if self._audience_preprocessing_enabled(session):
                        if active_pending:
                            runtime.audience_preprocess_wake.set()
                        await asyncio.sleep(sleep_seconds)
                        continue
```

Do not remove the legacy branch for sessions without episode plan / presentation.

- [ ] **Step 5: Run injection regression tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_injection.py -q --basetemp=.pyTestTemp/basetemp-audience-queue
```

Expected: PASS. If legacy non-presentation tests fail, preserve their path by guarding only `_audience_preprocessing_enabled(session)`.

- [ ] **Step 6: Commit no-interrupt routing**

```powershell
git add YouTubeBridge/engine_injection.py YouTubeBridge/tests/test_bridge_engine_injection.py
git commit -m "fix: route live audience events through preprocessing queue"
```

---

### Task 5: Make Main Thread Drain Audience Batch Then Wait For Prefetch

**Files:**
- Modify: `YouTubeBridge/engine_director_runtime.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_director.py`

- [ ] **Step 1: Add helper to present ready audience after a completed main item**

In `engine_director_runtime.py`, add:

```python
    async def _present_ready_audience_batch_after_turn(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        next_state = state
        presented = await self._present_ready_audience_gap_turn(runtime, session, next_state)
        if presented:
            next_state = self.storage.get_director_state(runtime.session_id) or next_state
        return next_state
```

- [ ] **Step 2: Add helper to await prefetched planned turn readiness**

In `engine_director_runtime.py`, add:

```python
    async def _await_prefetch_task_ready(
        self,
        runtime: LiveRuntime,
        prefetch_task,
        *,
        timeout_seconds: float,
    ) -> dict[str, Any] | None:
        if not prefetch_task:
            return None
        try:
            return await asyncio.wait_for(prefetch_task, timeout=max(0.1, timeout_seconds))
        except asyncio.TimeoutError:
            _director_timing_log(
                "prefetch_wait_timeout",
                session_id=runtime.session_id,
                timeout_seconds=timeout_seconds,
            )
            return None
```

- [ ] **Step 3: Add helper that encodes the required main thread order**

In `engine_director_runtime.py`, add:

```python
    async def _after_main_turn_sequence(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        state: dict[str, Any],
        *,
        prefetch_task=None,
    ) -> dict[str, Any]:
        next_state = await self._present_ready_audience_batch_after_turn(runtime, session, state)
        if runtime.stop_after_current_turn:
            return next_state
        if prefetch_task:
            timeout = float(session.get("prefetch_wait_timeout_seconds") or 10)
            prefetched = await self._await_prefetch_task_ready(
                runtime,
                prefetch_task,
                timeout_seconds=timeout,
            )
            if prefetched:
                consumed = await self._consume_prefetched_episode_turn(runtime, session, prefetched)
                if consumed and not consumed.get("discarded"):
                    consumed_decision = consumed.get("decision") if isinstance(consumed.get("decision"), dict) else {}
                    consumed_base_state = consumed.get("base_state") if isinstance(consumed.get("base_state"), dict) else next_state
                    next_state = self.storage.update_director_state(
                        runtime.session_id,
                        status="running",
                        last_director_action_at=datetime.now().isoformat(),
                        current_topic=str(consumed_decision.get("current_topic") or next_state.get("current_topic") or ""),
                        metadata={
                            "last_decision": consumed_decision,
                            "last_result_job_id": consumed.get("interaction", {}).get("job_id", ""),
                            "chat_batches_since_anchor": 0,
                            "segment_state": self._segment_state_after_turn(
                                session,
                                consumed_base_state,
                                consumed_decision,
                                self._segment_topic_entry_for_session(session),
                            ),
                            **self._episode_metadata_after_turn(session, consumed_base_state, consumed_decision),
                        },
                    )
                    await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
                    next_state = await self._present_ready_audience_batch_after_turn(runtime, session, next_state)
        return next_state
```

- [ ] **Step 4: Replace duplicated post-turn sequence in `_director_loop()`**

Where `_director_loop()` currently does:

```python
                await self._schedule_audience_gap_prepare_if_needed(...)
                if await self._present_ready_audience_gap_turn(...):
                    ...
                prefetch_task = result.get("after_memoria_task")
                if prefetch_task:
                    next_state = await self._consume_prefetched_episode_chain(...)
```

Replace it with:

```python
                if runtime.audience_preprocess_wake:
                    runtime.audience_preprocess_wake.set()
                next_state = await self._after_main_turn_sequence(
                    runtime,
                    session,
                    next_state,
                    prefetch_task=result.get("after_memoria_task"),
                )
```

Do the same replacement in the other post-send branch near the earlier kickoff path if the file has both code paths.

- [ ] **Step 5: Keep `_consume_prefetched_episode_chain()` only for legacy internal chaining**

Do not delete `_consume_prefetched_episode_chain()` yet. Update it to call `_present_ready_audience_batch_after_turn()` instead of direct `_schedule_audience_gap_prepare_if_needed()` plus `_present_ready_audience_gap_turn()`.

```python
            if runtime.audience_preprocess_wake:
                runtime.audience_preprocess_wake.set()
            next_state = await self._present_ready_audience_batch_after_turn(
                runtime,
                session,
                next_state,
            )
```

- [ ] **Step 6: Run main sequencing tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_director.py::test_main_thread_presents_ready_audience_only_after_planned_turn_ack YouTubeBridge/tests/test_bridge_engine_director.py::test_presentation_episode_plan_prefetches_next_planned_turn_before_current_ack YouTubeBridge/tests/test_bridge_engine_director.py::test_presentation_prefetch_chain_continues_while_prefetched_turn_is_playing -q --basetemp=.pyTestTemp/basetemp-audience-queue
```

Expected: PASS. If prefetch tests need expected ordering updates, preserve the invariant: visible playback order is planned -> audience if ready -> prefetch.

- [ ] **Step 7: Commit main thread sequencing**

```powershell
git add YouTubeBridge/engine_director_runtime.py YouTubeBridge/tests/test_bridge_engine_director.py
git commit -m "feat: sequence audience batches between planned turns"
```

---

### Task 6: Prevent Normal Unplayed Ready Artifacts

**Files:**
- Modify: `YouTubeBridge/engine_director_runtime.py`
- Modify: `YouTubeBridge/bridge_engine.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_director.py`
- Test: `YouTubeBridge/tests/test_presentation_queue.py`

- [ ] **Step 1: Add test that a ready audience item is consumed before next generated planned turn**

Append this to `YouTubeBridge/tests/test_bridge_engine_director.py`.

```python
@pytest.mark.asyncio
async def test_ready_audience_item_is_consumed_before_next_new_planned_generation(monkeypatch):
    with temp_storage() as storage:
        storage.upsert_connector({"connector_id": "yt", "name": "YouTube", "api_key": "key", "enabled": True})
        plan = sample_plan()
        storage.upsert_live_episode_plan(plan)
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt",
            "display_name": "Live A",
            "status": "running",
            "presentation_enabled": True,
            "tts_enabled": True,
            "episode_plan_id": plan["plan_id"],
            "target_memoria_session_id": "mem-a",
            "character_ids": ["char-a"],
        })
        state = storage.update_director_state(
            "live-a",
            director_enabled=True,
            metadata={"planned_state": initial_planned_state(plan)},
        )
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_audience_prepare",
            "priority": 45,
            "status": "prepared",
            "event_ids": [201],
            "memoria_session_id": "mem-a:audience",
            "character_ids": ["char-a"],
            "content": "audience reply",
            "metadata": {"prepare_only": True, "decision": {"action": "reply_chat_batch", "episode_plan": {"mode": "audience_gap"}}},
        })
        item = storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "audience-ready:0",
            "character_id": "char-a",
            "character_name": "角色A",
            "sequence_index": 0,
            "text": "已預處理的觀眾回應。",
            "status": "ready",
            "audio_path": "audience.wav",
            "audio_format": "wav",
            "metadata": {"source": "director_audience_prepare"},
        })
        manager = YouTubeBridgeManager(storage)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        called_new_plan = False

        async def fail_new_plan(*_args, **_kwargs):
            nonlocal called_new_plan
            called_new_plan = True
            raise AssertionError("main thread must consume ready audience before generating another planned turn")

        monkeypatch.setattr(manager, "_send_director_turn", fail_new_plan)
        await manager._present_ready_audience_gap_turn(runtime, storage.get_session("live-a"), state)

        assert called_new_plan is False
        assert storage.get_interaction(interaction["job_id"])["status"] == "completed"
        assert storage.get_presentation_item(item["item_id"])["status"] in {"played", "failed"}
```

- [ ] **Step 2: Run the test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_director.py::test_ready_audience_item_is_consumed_before_next_new_planned_generation -q --basetemp=.pyTestTemp/basetemp-audience-queue
```

Expected: may need explicit ACK handling in the test. If direct helper waits for ACK, make the test subscribe and ACK the `presentation_item_ready` event like existing tests.

- [ ] **Step 3: Add an invariant helper for diagnostic assertions**

In `engine_director_runtime.py`, add:

```python
    def _ready_prepared_items_for_session(self, session_id: str) -> list[dict[str, Any]]:
        return [
            item
            for item in self.storage.list_presentation_items(session_id, statuses={"ready"}, limit=500)
            if str((item.get("metadata") or {}).get("source") or "") in {
                "director_prefetch",
                "director_audience_prepare",
            }
        ]
```

Use this only for timing logs and tests. It must not auto-skip items.

- [ ] **Step 4: Do not mark unpresented ready items as skipped during normal session flow**

In `_discard_prepared_items_for_interaction()`, change the guard so it is only used when a session is not live or a worker-created prepare is cancelled before it reaches ready. Replace:

```python
            if str(item.get("status") or "") in {"played", "skipped"}:
                continue
            self.storage.update_presentation_item(
                item["item_id"],
                status="skipped",
                error=reason,
            )
```

with:

```python
            status = str(item.get("status") or "")
            if status in {"played", "skipped", "presenting"}:
                continue
            if status == "ready" and reason not in {"session_not_running", "interaction_not_preparing"}:
                continue
            self.storage.update_presentation_item(
                item["item_id"],
                status="skipped",
                error=reason,
            )
```

- [ ] **Step 5: Keep timeout semantics only for actual presenting items**

In `bridge_engine.py:_present_prepared_item()`, keep the existing `ack_timeout -> skipped` behavior because the item was already presented. Add a comment before the timeout handler:

```python
            # This is a real playback failure: the item was already sent to the
            # client as presenting, so timeout is a terminal skipped state.
```

- [ ] **Step 6: Run presentation and director tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_presentation_queue.py YouTubeBridge/tests/test_bridge_engine_director.py -q --basetemp=.pyTestTemp/basetemp-audience-queue
```

Expected: PASS. If existing tests intentionally expect unconsumed prefetch ready items, update expectations to the new contract: ready items are transient and must either be consumed or the session must be in graceful drain / hard stop.

- [ ] **Step 7: Commit ready-item invariant**

```powershell
git add YouTubeBridge/engine_director_runtime.py YouTubeBridge/bridge_engine.py YouTubeBridge/tests/test_bridge_engine_director.py YouTubeBridge/tests/test_presentation_queue.py
git commit -m "fix: keep prepared presentation items consumable until played"
```

---

### Task 7: Replace Manual Finalize Interrupt With Graceful Drain

**Files:**
- Modify: `YouTubeBridge/engine_closing.py`
- Modify: `YouTubeBridge/engine_runtime_lifecycle.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_closing.py`

- [ ] **Step 1: Add drain helper**

In `engine_closing.py`, add this method before `_finalize_live_session()`:

```python
    async def _drain_live_session_before_closing(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        *,
        timeout_seconds: float = 180.0,
    ) -> dict[str, Any]:
        runtime.graceful_closing_requested = True
        runtime.accepting_audience_events = False
        runtime.stop_after_current_turn = True
        runtime.drain_started_at = datetime.now().isoformat()
        if runtime.audience_preprocess_wake:
            runtime.audience_preprocess_wake.set()
        deadline = datetime.now() + timedelta(seconds=max(1.0, timeout_seconds))
        while datetime.now() < deadline:
            active = self.storage.get_active_interaction(runtime.session_id)
            presenting = self.storage.list_presentation_items(
                runtime.session_id,
                statuses={"presenting", "failed"},
                limit=20,
            )
            ready_prepared = []
            finder = getattr(self, "_ready_prepared_items_for_session", None)
            if callable(finder):
                ready_prepared = finder(runtime.session_id)
            if not active and not presenting and not ready_prepared:
                return {
                    "status": "drained",
                    "active_job_id": "",
                    "presenting_count": 0,
                    "ready_prepared_count": 0,
                }
            if ready_prepared and not presenting:
                state = self.storage.get_director_state(runtime.session_id)
                await self._present_ready_audience_batch_after_turn(runtime, session, state)
            await asyncio.sleep(0.5)
        return {
            "status": "timeout",
            "active_job_id": str((self.storage.get_active_interaction(runtime.session_id) or {}).get("job_id") or ""),
            "presenting_count": len(self.storage.list_presentation_items(runtime.session_id, statuses={"presenting", "failed"}, limit=20)),
            "ready_prepared_count": len(self._ready_prepared_items_for_session(runtime.session_id)),
        }
```

- [ ] **Step 2: Change `_finalize_live_session()` to drain before cancelling**

In `_finalize_live_session()`, replace:

```python
        await self._stop_runtime_background_tasks_for_closing(runtime)
        ...
        await self._interrupt_active_generation_for_closing(runtime)
```

with:

```python
        runtime.graceful_closing_requested = True
        runtime.accepting_audience_events = False
        await self._cancel_runtime_task(runtime, "task")
        await self._cancel_runtime_task(runtime, "test_event_task")
        drain_result = await self._drain_live_session_before_closing(runtime, session)
        await self._cancel_runtime_task(runtime, "inject_task")
        await self._cancel_runtime_task(runtime, "audience_preprocess_task")
        await self._cancel_runtime_task(runtime, "audience_gap_prepare_task")
        await self._cancel_runtime_task(runtime, "director_task")
        await self._cancel_runtime_task(runtime, "director_kickoff_task")
```

Then include `drain_result` in final director metadata:

```python
                "graceful_drain": drain_result,
```

Do not call `_interrupt_active_generation_for_closing()` in the normal manual/duration finalize path. Keep `_interrupt_active_generation_for_closing()` only for hard shutdown or drain timeout if the user later asks for force stop.

- [ ] **Step 3: Keep duration closing from creating new planned turns**

In `_finalize_for_duration()`, before waiting for active interaction, set:

```python
            runtime.graceful_closing_requested = True
            runtime.accepting_audience_events = False
            runtime.stop_after_current_turn = True
```

- [ ] **Step 4: Run closing tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_closing.py::test_manual_finalize_enters_graceful_drain_without_interrupting_presenting_item YouTubeBridge/tests/test_bridge_engine_closing.py::test_manual_finalize_uses_full_closing_flow_and_marks_session_ended YouTubeBridge/tests/test_bridge_engine_closing.py::test_duration_finalize_waits_for_active_generation_before_closing_thanks -q --basetemp=.pyTestTemp/basetemp-audience-queue
```

Expected: PASS after adapting old tests that explicitly expected interruption. The new invariant is drain first, closing thanks/final closing second, ended status last.

- [ ] **Step 5: Commit graceful closing**

```powershell
git add YouTubeBridge/engine_closing.py YouTubeBridge/engine_runtime_lifecycle.py YouTubeBridge/tests/test_bridge_engine_closing.py
git commit -m "fix: drain live presentation before closing"
```

---

### Task 8: Align Studio UI With Graceful Closing

**Files:**
- Modify: `YouTubeBridge/static/ui/studio.js`
- Modify: `YouTubeBridge/static/studio.html` if asset version is hardcoded there
- Test: `YouTubeBridge/tests/test_studio_ui.py`

- [ ] **Step 1: Add UI assertion that closing status does not call presentation skip**

Append to `YouTubeBridge/tests/test_studio_ui.py`:

```python
def test_studio_graceful_closing_does_not_route_status_to_presentation_interrupt():
    source = Path("YouTubeBridge/static/ui/studio.js").read_text(encoding="utf-8")
    interrupt_branch = source[source.index('if (payload.type === "interrupt_requested")'):]
    interrupt_branch = interrupt_branch[:interrupt_branch.index('if (payload.type === "interaction_interrupted")')]
    assert "handlePresentationInterrupt(payload)" in interrupt_branch
    status_branch = source[source.index('if (payload.type === "status")'):]
    status_branch = status_branch[:status_branch.index('if (payload.type === "director_state")')]
    assert "handlePresentationInterrupt" not in status_branch
    assert "presentation/current/skip" not in status_branch
```

- [ ] **Step 2: If status branch currently interrupts, remove it**

In `studio.js`, ensure only this branch calls `handlePresentationInterrupt()`:

```javascript
      if (payload.type === "interrupt_requested") {
        handlePresentationInterrupt(payload).catch((error) => {
          appendLog("WARN", `直播打斷處理失敗：${error.message || error}`);
        });
        return;
      }
```

Do not call it for:

```javascript
payload.type === "status" && payload.status === "closing"
```

- [ ] **Step 3: Bump static asset version if needed**

If `YouTubeBridge/static/studio.html` references `static/ui/studio.js?v=...`, bump only that query string:

```html
<script src="/static/ui/studio.js?v=audience-queue-drain"></script>
```

- [ ] **Step 4: Run UI contract test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_ui.py::test_studio_graceful_closing_does_not_route_status_to_presentation_interrupt -q --basetemp=.pyTestTemp/basetemp-audience-queue
```

Expected: PASS.

- [ ] **Step 5: Commit UI alignment**

```powershell
git add YouTubeBridge/static/ui/studio.js YouTubeBridge/static/studio.html YouTubeBridge/tests/test_studio_ui.py
git commit -m "fix: keep Studio playback intact during graceful closing"
```

---

### Task 9: Full Verification And Runtime Debug Artifact

**Files:**
- Create: `debug/youtube-live-audience-queue-flow-20260517.html`
- Optional Modify: existing debug HTML if the user wants replacement rather than a new file.

- [ ] **Step 1: Run targeted regression suite**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_injection.py YouTubeBridge/tests/test_bridge_engine_director.py YouTubeBridge/tests/test_bridge_engine_closing.py YouTubeBridge/tests/test_presentation_queue.py YouTubeBridge/tests/test_studio_ui.py -q --basetemp=.pyTestTemp/basetemp-audience-queue
```

Expected: PASS. If Windows temp cleanup fails with ACL/PermissionError, run:

```powershell
scripts/cleanup_pytest_temp.bat
```

Then rerun the same pytest command.

- [ ] **Step 2: Run storage smoke tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_storage.py -q --basetemp=.pyTestTemp/basetemp-audience-queue-storage
```

Expected: PASS.

- [ ] **Step 3: Generate a new debug flow HTML**

Create `debug/youtube-live-audience-queue-flow-20260517.html` showing the new accepted flow:

```text
Audience Worker:
YouTube polling -> live_events -> SafetyLLM -> audience preprocessing worker -> sidecar Memoria reply -> TTS ready batch

Main Thread:
planned turn -> playback + ACK -> ready audience batch if any -> playback + ACK -> wait prefetched planned turn -> playback + ACK -> repeat

Closing:
operator finalize -> stop accepting new audience -> drain current/prefetched/ready batches -> SC closing thanks -> final closing -> ended
```

Use Chinese node labels. Do not show `interrupt_session` as part of the normal audience/SC path.

- [ ] **Step 4: Inspect runtime paths for stale interrupt behavior**

Run:

```powershell
rg -n "higher_priority:super_chat|interrupt_requested|live_session_closing|item_skipped|ack_timeout|presentation_audience_gap_lane|audience_preprocessed" runtime/log runtime/llm_trace.jsonl -S
```

Expected after a new test live run:

- `presentation_audience_gap_lane` and `audience_preprocessed` can appear.
- `higher_priority:super_chat` should not appear for normal live-event routing.
- `interrupt_requested` should appear only for explicit force interrupt, not SC.
- `item_skipped` should appear only for manual skip or ACK timeout.
- `live_session_closing` should not be used to interrupt active planned/audience playback during normal manual finalize.

- [ ] **Step 5: Commit debug artifact if requested**

```powershell
git add debug/youtube-live-audience-queue-flow-20260517.html
git commit -m "docs: add audience queue live flow debug diagram"
```

Only commit debug output if the user wants debug artifacts versioned; otherwise leave it untracked.

---

## Acceptance Criteria

- In presentation + episode-plan sessions, ordinary chat and SC never call `interrupt_session()` through auto-inject or SC routing.
- SC is sorted ahead of ordinary chat by existing `priority_class="super_chat"` / `sc_tier`, but remains inside audience preprocessing.
- A ready audience batch is played only after the current planned/prefetched turn finishes and ACKs.
- If no ready audience batch exists, main thread waits for prefetched planned turn ready up to `prefetch_wait_timeout_seconds`; only then falls back to direct generation.
- Normal logs no longer show unplayed `director_prefetch` / `director_audience_prepare` ready items after the main loop advances.
- Manual finalize stops new audience intake and new planned generation, drains current playback/prepared batches, then runs SC closing thanks and final closing.
- Frontend does not call `/presentation/current/skip` because a graceful closing status arrived.

## Verification Commands

Run in this order:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_injection.py -q --basetemp=.pyTestTemp/basetemp-audience-queue-injection
python -m pytest YouTubeBridge/tests/test_bridge_engine_director.py -q --basetemp=.pyTestTemp/basetemp-audience-queue-director
python -m pytest YouTubeBridge/tests/test_bridge_engine_closing.py -q --basetemp=.pyTestTemp/basetemp-audience-queue-closing
python -m pytest YouTubeBridge/tests/test_presentation_queue.py YouTubeBridge/tests/test_studio_ui.py -q --basetemp=.pyTestTemp/basetemp-audience-queue-ui
python -m pytest YouTubeBridge/tests/test_storage.py -q --basetemp=.pyTestTemp/basetemp-audience-queue-storage
```

If all targeted tests pass, run the broader YouTubeBridge suite:

```powershell
python -m pytest YouTubeBridge/tests -q --basetemp=.pyTestTemp/basetemp-audience-queue-full
```

## Self-Review

- Spec coverage:
  - 普通留言與 SC 只進 queue: Task 3 and Task 4.
  - SC 只提高 priority 不 interrupt: Task 1 and Task 4.
  - 角色回應留言與 TTS 獨立 worker 預先完成: Task 2 and Task 3.
  - Main thread 在 planned ACK 後插入留言: Task 5.
  - 沒 ready audience 時等待 prefetched planned turn: Task 5.
  - 不留下未播放 prefetch/audience item: Task 6.
  - 手動結束直播 graceful drain: Task 7 and Task 8.
- Placeholder scan:
  - No `TBD`, `TODO`, or unresolved implementation names are used as final instructions.
  - New helper names are defined before later tasks reference them.
- Type consistency:
  - Runtime fields are added in Task 2 before worker/closing tasks use them.
  - `audience_preprocess_wake` is an `asyncio.Event`.
  - `source` values remain existing strings: `director_audience_prepare`, `director_prefetch`, `director`, `super_chat`, `auto_inject`.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-17-youtubebridge-main-thread-audience-queue.md`. Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints.

