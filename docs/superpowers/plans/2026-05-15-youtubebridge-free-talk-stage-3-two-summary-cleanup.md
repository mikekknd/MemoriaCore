# YouTubeBridge Free Talk Stage 3 Two Summary Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan with worker and verifier subagents. The main orchestrator monitors flow, reviews outputs, runs final verification, and updates roadmap status. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce independent `main` and `free_talk` summaries, write both to Shared Memory, and defer runtime cleanup until every required phase summary and memory write is complete.

**Architecture:** Extend the summary engine with phase-filtered summary generation and summary lookup by `metadata.summary_phase`. Add a manager callback for phase summary jobs so the runtime pipeline does not import route modules or summary route state directly. Cleanup becomes a phase pipeline decision: delete runtime/session only after the required summaries have completed memory writes.

**Tech Stack:** Summary engine, BridgeStorage summaries repository, MemoriaClient Shared Memory write, phase pipeline manager callback, FastAPI summary routes, pytest.

---

## File Structure

- Modify `YouTubeBridge/summary_engine.py`: add `summarize_session_phase(...)` and phase-filtered event/interaction selection.
- Modify `YouTubeBridge/storage_repositories/summaries.py`: add `get_session_summary_by_phase(...)` and `list_session_summaries_by_phase(...)`.
- Modify `YouTubeBridge/storage_repositories/events.py`: add helper to mark metadata phase on events if needed by tests.
- Modify `YouTubeBridge/storage_repositories/interactions.py`: ensure interaction metadata phase can be stored and read.
- Modify `YouTubeBridge/bridge_engine.py`: add `phase_summary_callback` and `phase_cleanup_callback` attributes.
- Modify `YouTubeBridge/engine_phase_pipeline.py`: queue and monitor main/free-talk summary jobs.
- Modify `YouTubeBridge/server.py`: install summary and cleanup callbacks.
- Modify `YouTubeBridge/server_routes/summaries.py`: expose phase query support for Studio and debugging.
- Modify `YouTubeBridge/static/ui/studio.js`: render main/free-talk summary status when available.
- Modify `YouTubeBridge/tests/test_summary_engine.py`: phase summary tests.
- Modify `YouTubeBridge/tests/test_storage.py`: summary lookup by phase tests.
- Modify `YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py`: cleanup gate tests.
- Modify `YouTubeBridge/tests/test_studio_ui.py`: source tests for summary status display.

---

### Task 1: Summary Lookup by Phase

**Files:**
- Modify: `YouTubeBridge/storage_repositories/summaries.py`
- Test: `YouTubeBridge/tests/test_storage.py`

- [ ] **Step 1: Write failing storage tests**

Append to `YouTubeBridge/tests/test_storage.py`:

```python
def test_get_session_summary_by_phase_returns_latest_matching_phase(tmp_path):
    storage = BridgeStorage(tmp_path / "youtube_live.db")
    storage.upsert_connector({"connector_id": "youtube-main", "display_name": "YT", "api_key": "", "enabled": True})
    storage.upsert_session({"session_id": "live-a", "connector_id": "youtube-main", "display_name": "Summary"})

    main = storage.create_summary("live-a", {
        "title": "正式節目摘要",
        "summary_text": "main",
        "memory_text": "main memory",
        "event_count": 2,
        "metadata": {"summary_phase": "main", "memory_write_status": "completed"},
    })
    free_talk = storage.create_summary("live-a", {
        "title": "雜談摘要",
        "summary_text": "free",
        "memory_text": "free memory",
        "event_count": 3,
        "metadata": {"summary_phase": "free_talk", "memory_write_status": "completed"},
    })

    assert storage.get_session_summary_by_phase("live-a", "main")["id"] == main["id"]
    assert storage.get_session_summary_by_phase("live-a", "free_talk")["id"] == free_talk["id"]
    assert storage.get_session_summary_by_phase("live-a", "missing") is None
```

- [ ] **Step 2: Run storage test and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_storage.py::test_get_session_summary_by_phase_returns_latest_matching_phase -q
```

Expected: FAIL because `get_session_summary_by_phase` does not exist.

- [ ] **Step 3: Add repository methods**

Add to `YouTubeBridge/storage_repositories/summaries.py`:

```python
    def get_session_summary_by_phase(self, session_id: str, summary_phase: str) -> dict | None:
        summaries = self.list_session_summaries_by_phase(session_id, summary_phase=summary_phase, limit=1)
        return summaries[0] if summaries else None

    def list_session_summaries_by_phase(self, session_id: str, *, summary_phase: str, limit: int = 20) -> list[dict]:
        limit = max(1, min(int(limit or 20), 100))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM youtube_live_summaries
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit * 5),
            ).fetchall()
        summaries = [summary for row in rows if (summary := self._row_to_summary(row))]
        return [
            summary for summary in summaries
            if (summary.get("metadata") or {}).get("summary_phase") == summary_phase
        ][:limit]
```

- [ ] **Step 4: Run storage test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_storage.py::test_get_session_summary_by_phase_returns_latest_matching_phase -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add YouTubeBridge/storage_repositories/summaries.py YouTubeBridge/tests/test_storage.py
git commit -m "feat(youtube-bridge): look up summaries by phase"
```

---

### Task 2: Phase-Filtered Summary Generation

**Files:**
- Modify: `YouTubeBridge/summary_engine.py`
- Test: `YouTubeBridge/tests/test_summary_engine.py`

- [ ] **Step 1: Write failing summary test**

Append to `YouTubeBridge/tests/test_summary_engine.py`:

```python
def test_summarize_session_phase_filters_events_and_interactions(tmp_path):
    storage = BridgeStorage(tmp_path / "youtube_live.db")
    storage.upsert_connector({"connector_id": "youtube-main", "display_name": "YT", "api_key": "", "enabled": True})
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "youtube-main",
        "display_name": "Phase Summary",
        "character_ids": ["char-a"],
    })
    storage.save_event({
        "bridge_session_id": "live-a",
        "connector_id": "youtube-main",
        "youtube_message_id": "main-1",
        "message_type": "textMessageEvent",
        "author_channel_id": "u1",
        "author_display_name": "觀眾A",
        "message_text": "正式段落問題",
        "published_at": "2026-05-15T10:00:00",
        "received_at": "2026-05-15T10:00:00",
        "status": "active",
        "metadata": {"phase": "planned_content"},
    })
    storage.save_event({
        "bridge_session_id": "live-a",
        "connector_id": "youtube-main",
        "youtube_message_id": "free-1",
        "message_type": "textMessageEvent",
        "author_channel_id": "u2",
        "author_display_name": "觀眾B",
        "message_text": "雜談問題",
        "published_at": "2026-05-15T10:10:00",
        "received_at": "2026-05-15T10:10:00",
        "status": "active",
        "metadata": {"phase": "post_plan_free_talk"},
    })
    client = FakeSummaryClient()
    manager = YouTubeLiveSummaryManager(storage, memoria_client=client)

    result = manager.summarize_session_phase("live-a", summary_phase="main", force=True)

    assert result["status"] == "completed"
    summary = result["summary"]
    assert summary["metadata"]["summary_phase"] == "main"
    assert "正式段落問題" in client.last_variables["summary_source"]
    assert "雜談問題" not in client.last_variables["summary_source"]
```

If `FakeSummaryClient` is not available in the file, add:

```python
class FakeSummaryClient:
    def __init__(self):
        self.last_variables = {}

    def generate_prompt_json(self, *, variables, **kwargs):
        self.last_variables = variables
        return {
            "title": "摘要",
            "overview": "摘要內容",
            "topic_tags": ["test"],
            "key_points": ["重點"],
            "qa_pairs": [],
            "audience_mood": "平穩",
            "memory_text": "可寫入記憶的摘要",
            "memory_text_requires_review": False,
        }
```

- [ ] **Step 2: Run summary test and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_summary_engine.py::test_summarize_session_phase_filters_events_and_interactions -q
```

Expected: FAIL because `summarize_session_phase` does not exist.

- [ ] **Step 3: Add `summarize_session_phase`**

In `YouTubeBridge/summary_engine.py`, add a public method:

```python
    def summarize_session_phase(
        self,
        session_id: str,
        *,
        summary_phase: str,
        force: bool = False,
        min_events: int = 1,
        max_events: int = 1000,
        chunk_size: int = 120,
        include_memoria_session: bool = False,
        safe_memory_text: bool = True,
    ) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        existing = self.storage.get_session_summary_by_phase(session_id, summary_phase)
        if existing and not force:
            return {"status": "completed", "reused": True, "summary": existing}
        phase_map = {
            "main": {"planned_content", "main_audience_closing"},
            "free_talk": {"post_plan_free_talk", "free_talk_audience_closing"},
        }
        allowed_phases = phase_map.get(summary_phase)
        if not allowed_phases:
            raise ValueError("summary_phase must be main or free_talk")
        return self._summarize_session_inner(
            session,
            min_events=max(1, int(min_events or 1)),
            max_events=max(1, min(int(max_events or 1000), 5000)),
            chunk_size=max(20, min(int(chunk_size or 120), 500)),
            include_memoria_session=include_memoria_session,
            safe_memory_text=safe_memory_text,
            finalized_at=datetime.now().isoformat(),
            summary_phase=summary_phase,
            allowed_event_phases=allowed_phases,
        )
```

Modify `_summarize_session_inner` signature:

```python
        summary_phase: str = "",
        allowed_event_phases: set[str] | None = None,
```

Filter events after `list_summary_events`:

```python
        if allowed_event_phases is not None:
            events = [
                event for event in events
                if str((event.get("metadata") or {}).get("phase") or "") in allowed_event_phases
            ]
```

Filter interactions after `list_interactions`:

```python
        if allowed_event_phases is not None:
            interactions = [
                interaction for interaction in interactions
                if str((interaction.get("metadata") or {}).get("phase") or "") in allowed_event_phases
            ]
```

Add metadata on summary creation:

```python
                    "summary_phase": summary_phase or "full_session",
```

- [ ] **Step 4: Run summary test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_summary_engine.py::test_summarize_session_phase_filters_events_and_interactions -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add YouTubeBridge/summary_engine.py YouTubeBridge/tests/test_summary_engine.py
git commit -m "feat(youtube-bridge): summarize live sessions by phase"
```

---

### Task 3: Summary Callback and Shared Memory Writes

**Files:**
- Modify: `YouTubeBridge/bridge_engine.py`
- Modify: `YouTubeBridge/engine_phase_pipeline.py`
- Modify: `YouTubeBridge/server.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py`
- Test: `YouTubeBridge/tests/test_server_auth.py`

- [ ] **Step 1: Write failing callback test**

Append to `YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py`:

```python
@pytest.mark.asyncio
async def test_phase_pipeline_runs_summary_callback_and_records_memory_status(tmp_path):
    storage = _storage(tmp_path)
    storage.upsert_session({"session_id": "live-a", "connector_id": "youtube-main", "display_name": "Summary"})
    manager = YouTubeBridgeManager(storage)
    calls = []

    async def fake_callback(session_id, *, summary_phase, reason):
        calls.append((session_id, summary_phase, reason))
        return {
            "summary": {"id": 7, "metadata": {"summary_phase": summary_phase}},
            "memory_write": {"status": "completed"},
        }

    manager.phase_summary_callback = fake_callback

    result = await manager.run_phase_summary("live-a", summary_phase="main", reason="test")

    assert calls == [("live-a", "main", "test")]
    assert result["memory_write"]["status"] == "completed"
    state = storage.get_director_state("live-a")
    assert state["metadata"]["main_summary"]["status"] == "completed"
    assert state["metadata"]["main_summary"]["memory_write_status"] == "completed"
```

- [ ] **Step 2: Run callback test and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py::test_phase_pipeline_runs_summary_callback_and_records_memory_status -q
```

Expected: FAIL because `phase_summary_callback` and `run_phase_summary` are missing.

- [ ] **Step 3: Add callback attributes**

Modify `YouTubeBridge/bridge_engine.py` `__init__`:

```python
        self.phase_summary_callback = None
        self.phase_cleanup_callback = None
```

- [ ] **Step 4: Add `run_phase_summary`**

Add to `YouTubeBridge/engine_phase_pipeline.py`:

```python
    async def run_phase_summary(self, session_id: str, *, summary_phase: str, reason: str) -> dict[str, Any]:
        callback = getattr(self, "phase_summary_callback", None)
        if not callback:
            result = {"summary": None, "memory_write": {"status": "skipped", "reason": "callback_missing"}}
        else:
            result = await callback(session_id, summary_phase=summary_phase, reason=reason)
        key = "main_summary" if summary_phase == "main" else "free_talk_summary"
        state = self.storage.get_director_state(session_id)
        metadata = dict(state.get("metadata") or {})
        memory_write = result.get("memory_write") if isinstance(result, dict) else {}
        metadata[key] = {
            **(metadata.get(key) if isinstance(metadata.get(key), dict) else {}),
            "status": "completed" if memory_write.get("status") == "completed" else "failed",
            "summary_id": (result.get("summary") or {}).get("id") if isinstance(result.get("summary"), dict) else None,
            "memory_write_status": memory_write.get("status", "unknown"),
            "completed_at": datetime.now().isoformat(),
        }
        self.storage.update_director_state(session_id, metadata=metadata)
        return result
```

Modify `_run_main_summary_background` to call `await self.run_phase_summary(session_id, summary_phase="main", reason=reason)`.

- [ ] **Step 5: Install server callback**

In `YouTubeBridge/server.py`, add:

```python
async def _phase_summary_callback(session_id: str, *, summary_phase: str, reason: str) -> dict[str, Any]:
    result = await asyncio.to_thread(
        summary_manager.summarize_session_phase,
        session_id,
        summary_phase=summary_phase,
        force=True,
        min_events=1,
        max_events=1000,
        chunk_size=120,
        include_memoria_session=False,
        safe_memory_text=True,
    )
    summary = result.get("summary") if isinstance(result, dict) else None
    if not isinstance(summary, dict):
        return {"summary": summary, "memory_write": {"status": "skipped", "reason": "summary_not_created"}}
    memory_payload = await _sessions_routes._write_summary_shared_memory_without_cleanup(session_id, summary)
    return {"summary": memory_payload.get("summary", summary), "memory_write": memory_payload.get("memory_write", {})}


def _install_phase_pipeline_callbacks() -> None:
    manager.phase_summary_callback = _phase_summary_callback
    manager.phase_cleanup_callback = _phase_cleanup_callback
```

Add a helper in `server_routes/sessions.py` that writes a passed summary without deleting runtime:

```python
async def _write_summary_shared_memory_without_cleanup(session_id: str, summary: dict) -> dict:
    return await _write_summary_shared_memory(session_id, summary=summary, delete_after=False)
```

If `_write_summary_shared_memory` currently only reads current session summary, split its internals into a helper that accepts `summary`.

- [ ] **Step 6: Run callback and server tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py::test_phase_pipeline_runs_summary_callback_and_records_memory_status YouTubeBridge/tests/test_server_auth.py::test_finalize_session_endpoint_uses_full_finalize_manager_path -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add YouTubeBridge/bridge_engine.py YouTubeBridge/engine_phase_pipeline.py YouTubeBridge/server.py YouTubeBridge/server_routes/sessions.py YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py YouTubeBridge/tests/test_server_auth.py
git commit -m "feat(youtube-bridge): run phase summaries through callbacks"
```

---

### Task 4: Cleanup Gate

**Files:**
- Modify: `YouTubeBridge/engine_phase_pipeline.py`
- Modify: `YouTubeBridge/server.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py`

- [ ] **Step 1: Write failing cleanup gate tests**

Append to `YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py`:

```python
@pytest.mark.asyncio
async def test_cleanup_waits_for_required_summaries(tmp_path):
    storage = _storage(tmp_path)
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "youtube-main",
        "display_name": "Cleanup",
        "post_plan_free_talk_enabled": True,
        "auto_delete_after_processed": True,
    })
    storage.update_director_state("live-a", metadata={
        "phase": "post_plan_free_talk",
        "main_summary": {"status": "completed", "memory_write_status": "completed"},
        "free_talk_summary": {"status": "running", "memory_write_status": "not_started"},
    })
    manager = YouTubeBridgeManager(storage)
    cleanup_calls = []

    async def fake_cleanup(session_id):
        cleanup_calls.append(session_id)
        return {"deleted": True}

    manager.phase_cleanup_callback = fake_cleanup

    result = await manager.maybe_run_phase_cleanup("live-a")

    assert result["status"] == "waiting"
    assert cleanup_calls == []
```

Add companion test:

```python
@pytest.mark.asyncio
async def test_cleanup_runs_after_main_and_free_talk_summaries_complete(tmp_path):
    storage = _storage(tmp_path)
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "youtube-main",
        "display_name": "Cleanup",
        "post_plan_free_talk_enabled": True,
        "auto_delete_after_processed": True,
    })
    storage.update_director_state("live-a", metadata={
        "phase": "free_talk_audience_closing",
        "main_summary": {"status": "completed", "memory_write_status": "completed"},
        "free_talk_summary": {"status": "completed", "memory_write_status": "completed"},
    })
    manager = YouTubeBridgeManager(storage)
    cleanup_calls = []

    async def fake_cleanup(session_id):
        cleanup_calls.append(session_id)
        return {"deleted": True}

    manager.phase_cleanup_callback = fake_cleanup

    result = await manager.maybe_run_phase_cleanup("live-a")

    assert result["status"] == "cleaned"
    assert cleanup_calls == ["live-a"]
```

- [ ] **Step 2: Run cleanup tests and verify they fail**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py::test_cleanup_waits_for_required_summaries YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py::test_cleanup_runs_after_main_and_free_talk_summaries_complete -q
```

Expected: FAIL because `maybe_run_phase_cleanup` does not exist.

- [ ] **Step 3: Add cleanup gate**

Add to `YouTubeBridge/engine_phase_pipeline.py`:

```python
    async def maybe_run_phase_cleanup(self, session_id: str) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            return {"status": "missing"}
        if not session.get("auto_delete_after_processed"):
            return {"status": "skipped", "reason": "auto_delete_disabled"}
        state = self.storage.get_director_state(session_id)
        metadata = dict(state.get("metadata") or {})
        required = ["main_summary"]
        if session.get("post_plan_free_talk_enabled") or metadata.get("post_plan_free_talk"):
            required.append("free_talk_summary")
        for key in required:
            item = metadata.get(key) if isinstance(metadata.get(key), dict) else {}
            if item.get("status") != "completed" or item.get("memory_write_status") != "completed":
                return {"status": "waiting", "reason": f"{key}_not_complete"}
        callback = getattr(self, "phase_cleanup_callback", None)
        if not callback:
            return {"status": "skipped", "reason": "cleanup_callback_missing"}
        cleanup = await callback(session_id)
        metadata["phase_cleanup"] = {"status": "completed", "completed_at": datetime.now().isoformat(), "result": cleanup}
        self.storage.update_director_state(session_id, status="ended", metadata=metadata)
        return {"status": "cleaned", "cleanup": cleanup}
```

In `server.py`, add cleanup callback:

```python
async def _phase_cleanup_callback(session_id: str) -> dict[str, Any]:
    await manager.stop_session(session_id)
    deleted = False
    session = storage.get_session(session_id)
    if session and session.get("auto_delete_after_processed"):
        deleted = storage.delete_session(session_id)
        chat_preview_cache.pop(session_id, None)
    return {"deleted": deleted}
```

- [ ] **Step 4: Run cleanup tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py::test_cleanup_waits_for_required_summaries YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py::test_cleanup_runs_after_main_and_free_talk_summaries_complete -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add YouTubeBridge/engine_phase_pipeline.py YouTubeBridge/server.py YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py
git commit -m "feat(youtube-bridge): gate cleanup on phase summaries"
```

---

### Task 5: Free Talk Finalize Produces Free Talk Summary

**Files:**
- Modify: `YouTubeBridge/engine_phase_pipeline.py`
- Modify: `YouTubeBridge/server_routes/sessions.py`
- Modify: `YouTubeBridge/static/ui/studio.js`
- Test: `YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py`
- Test: `YouTubeBridge/tests/test_studio_ui.py`

- [ ] **Step 1: Write failing test for finalize during free talk**

Append to `YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py`:

```python
@pytest.mark.asyncio
async def test_finalize_phase_during_free_talk_runs_free_talk_summary(tmp_path):
    storage = _storage(tmp_path)
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "youtube-main",
        "display_name": "Free Talk Finalize",
        "post_plan_free_talk_enabled": True,
        "auto_delete_after_processed": False,
    })
    storage.update_director_state("live-a", metadata={
        "phase": "post_plan_free_talk",
        "main_summary": {"status": "completed", "memory_write_status": "completed"},
    })
    manager = YouTubeBridgeManager(storage)
    summary_calls = []

    async def fake_summary(session_id, *, summary_phase, reason):
        summary_calls.append(summary_phase)
        return {"summary": {"id": 8}, "memory_write": {"status": "completed"}}

    manager.phase_summary_callback = fake_summary

    result = await manager.finalize_phase_pipeline("live-a", reason="operator_finalize")

    assert result["phase"] == "free_talk_summary"
    assert summary_calls == ["free_talk"]
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py::test_finalize_phase_during_free_talk_runs_free_talk_summary -q
```

Expected: FAIL because `finalize_phase_pipeline` is not implemented.

- [ ] **Step 3: Implement `finalize_phase_pipeline`**

Add to `YouTubeBridge/engine_phase_pipeline.py`:

```python
    async def finalize_phase_pipeline(self, session_id: str, *, reason: str = "operator_finalize") -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        state = self.storage.get_director_state(session_id)
        metadata = dict(state.get("metadata") or {})
        phase = str(metadata.get("phase") or "planned_content")
        if phase == "post_plan_free_talk":
            metadata["phase"] = "free_talk_audience_closing"
            metadata["free_talk_audience_closing"] = {
                "status": "completed",
                "reason": reason,
                "completed_at": datetime.now().isoformat(),
            }
            self.storage.update_director_state(session_id, status="free_talk_summary", metadata=metadata)
            await self.run_phase_summary(session_id, summary_phase="free_talk", reason=reason)
            cleanup = await self.maybe_run_phase_cleanup(session_id)
            return {"phase": "free_talk_summary", "cleanup": cleanup}
        result = await self.finish_main_phase(
            session_id,
            reason=reason,
            enter_free_talk=False,
            topic_root=Path("runtime") / "YouTubeBridge" / "freeTalkTopics",
        )
        cleanup = await self.maybe_run_phase_cleanup(session_id)
        return {"phase": result.get("phase", "main_summary"), "cleanup": cleanup}
```

Stage 4 replaces the immediate `free_talk_audience_closing` completion with actual eligible pending-message draining.

- [ ] **Step 4: Run finalize test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py::test_finalize_phase_during_free_talk_runs_free_talk_summary -q
```

Expected: PASS.

- [ ] **Step 5: Add Studio summary status source test**

Append to `YouTubeBridge/tests/test_studio_ui.py`:

```python
def test_studio_displays_phase_summary_status():
    studio_js = STUDIO_JS.read_text(encoding="utf-8")

    assert "main_summary" in studio_js
    assert "free_talk_summary" in studio_js
    assert "memory_write_status" in studio_js
```

Update Studio state rendering to show:

```js
function phaseSummaryText(metadata) {
  const main = metadata?.main_summary?.memory_write_status || metadata?.main_summary?.status || "未開始";
  const freeTalk = metadata?.free_talk_summary?.memory_write_status || metadata?.free_talk_summary?.status || "未開始";
  return `正式摘要：${main} / 雜談摘要：${freeTalk}`;
}
```

- [ ] **Step 6: Run Stage 3 regression**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_summary_engine.py YouTubeBridge/tests/test_storage.py::test_get_session_summary_by_phase_returns_latest_matching_phase YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py YouTubeBridge/tests/test_studio_ui.py -q
node --check YouTubeBridge/static/ui/studio.js
git diff --check
```

Expected: pytest PASS, `node --check` exit 0, `git diff --check` exit 0 or only existing CRLF warnings.

- [ ] **Step 7: Commit**

```powershell
git add YouTubeBridge/engine_phase_pipeline.py YouTubeBridge/server_routes/sessions.py YouTubeBridge/static/ui/studio.js YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py YouTubeBridge/tests/test_studio_ui.py
git commit -m "feat(youtube-bridge): finalize free talk with separate summary"
```

---

### Task 6: Browser E2E

**Files:**
- No code files.

- [ ] **Step 1: Start services**

Use visible foreground windows:

```powershell
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "G:\ClaudeProject\MemoriaCore\start.bat" -WorkingDirectory "G:\ClaudeProject\MemoriaCore" -WindowStyle Normal
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "G:\ClaudeProject\MemoriaCore\YouTubeBridge\start.bat" -WorkingDirectory "G:\ClaudeProject\MemoriaCore\YouTubeBridge" -WindowStyle Normal
```

- [ ] **Step 2: Run browser scenario**

Browser QA:

- Open `http://127.0.0.1:8091/studio/`.
- Start a test session with free talk enabled and one topic pack selected.
- Click `結束節目並進入雜談測試`.
- Wait for a free talk response.
- Click `收尾 / 停止直播`.
- Confirm UI shows main summary status and free talk summary status.
- Confirm two summaries exist through `GET /summaries?session_id={session_id}` with metadata `summary_phase=main` and `summary_phase=free_talk`.
- Confirm cleanup does not run until both memory writes are completed.

---

## Stage 3 Acceptance Criteria

- `main` and `free_talk` summaries are separate rows in `youtube_live_summaries`.
- Both summaries write Shared Memory independently.
- Cleanup waits for both summaries when free talk was enabled.
- Cleanup waits only for main summary when free talk was not enabled.
- Studio shows phase summary status in Debug or live status output.
