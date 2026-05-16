# YouTubeBridge Closing Interrupt Visible Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Studio live closing so closing messages appear as soon as they stream, and interrupt only blocks content that has not appeared on screen.

**Architecture:** Keep the existing YouTubeBridge session, interaction, SSE, and chat-preview contracts, but add a small visible-output contract in interaction metadata. Manual Studio finalize becomes a background phase finalize request, while SSE remains the live display path. Interrupted interactions are hidden only when their output was never broadcast; final closing uses the latest visible message as the reply target.

**Tech Stack:** FastAPI, Pydantic, asyncio tasks, SQLite-backed `BridgeStorage`, existing SSE event stream, vanilla JS Studio UI, pytest.

---

## File Structure

- Modify `YouTubeBridge/models.py`
  - Extend `FinalizePhaseRequest` with `background: bool = False`.
- Modify `YouTubeBridge/server_routes/sessions.py`
  - Add background finalize task tracking.
  - Preserve visible messages in `chat-preview` filtering.
- Modify `YouTubeBridge/bridge_engine.py`
  - Record visible chat/presentation messages after SSE broadcast.
- Modify `YouTubeBridge/engine_closing.py`
  - Keep visible interrupted output.
  - Use latest visible output as final closing reply target.
- Modify `YouTubeBridge/static/ui/studio.js`
  - Send background finalize.
  - Keep already displayed messages across refreshes.
  - Stop destructive refresh on `interaction_interrupted`.
- Modify tests:
  - `YouTubeBridge/tests/test_server_route_split.py`
  - `YouTubeBridge/tests/test_server_auth.py`
  - `YouTubeBridge/tests/test_bridge_engine_injection.py`
  - `YouTubeBridge/tests/test_bridge_engine_closing.py`
  - `YouTubeBridge/tests/test_studio_ui.py`

---

### Task 1: Add Background Phase Finalize API

**Files:**
- Modify: `YouTubeBridge/models.py:189`
- Modify: `YouTubeBridge/server_routes/sessions.py:1-80,537-547`
- Test: `YouTubeBridge/tests/test_server_route_split.py`

- [ ] **Step 1: Write the failing route test**

Add this test after `test_finalize_phase_route_returns_public_phase_shape_without_closing_internals` in `YouTubeBridge/tests/test_server_route_split.py`:

```python
@pytest.mark.asyncio
async def test_finalize_phase_route_background_returns_started_and_schedules_task(tmp_path):
    calls: list[tuple[str, str]] = []

    class FakeStorage:
        def get_session(self, session_id: str):
            return {"session_id": session_id, "status": "running"}

    class FakeManager:
        def get_status(self, session_id: str):
            return {"session_id": session_id, "running": True, "status": "running"}

        async def finalize_phase_pipeline(self, session_id: str, *, reason: str):
            calls.append((session_id, reason))
            return {"phase": "finalized", "session_id": session_id, "status": "ended"}

        async def _broadcast(self, session_id: str, payload: dict):
            calls.append((session_id, payload["type"]))

    server_module._sessions_routes.configure(SimpleNamespace(
        storage=FakeStorage(),
        manager=FakeManager(),
        summary_manager=SimpleNamespace(),
        chat_preview_cache={},
        static_root=tmp_path,
        ui_assets_root=tmp_path,
        e2e_checkpoint_path=tmp_path / "checkpoint.json",
        free_talk_topic_root=tmp_path / "freeTalkTopics",
    ))

    result = await server_module._sessions_routes.finalize_phase(
        "session-a",
        server_module._sessions_routes.FinalizePhaseRequest(
            reason="operator",
            background=True,
        ),
    )
    await asyncio.sleep(0)

    assert result == {
        "phase": "finalize_started",
        "session_id": "session-a",
        "status": "closing",
        "runtime_status": {"session_id": "session-a", "running": True, "status": "running"},
    }
    assert ("session-a", "operator") in calls
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_server_route_split.py::test_finalize_phase_route_background_returns_started_and_schedules_task -q --basetemp=.pyTestTemp/basetemp-closing-plan-task1
```

Expected: FAIL because `FinalizePhaseRequest` does not accept `background`, and `finalize_phase()` still waits for the full finalize result.

- [ ] **Step 3: Extend the request model**

In `YouTubeBridge/models.py`, change `FinalizePhaseRequest` to:

```python
class FinalizePhaseRequest(BaseModel):
    reason: str = Field("operator_finalize", max_length=120)
    background: bool = False
```

- [ ] **Step 4: Add background task helpers**

In `YouTubeBridge/server_routes/sessions.py`, add `import logging` near the existing imports, then add this module-level logger and task set after the global route state variables:

```python
logger = logging.getLogger(__name__)
_phase_finalize_tasks: set[asyncio.Task] = set()
```

Add these helpers before `finalize_phase()`:

```python
def _track_phase_finalize_task(task: asyncio.Task) -> None:
    _phase_finalize_tasks.add(task)

    def _discard(done: asyncio.Task) -> None:
        _phase_finalize_tasks.discard(done)
        try:
            exc = done.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            logger.warning("background phase finalize failed error=%s", exc, exc_info=exc)

    task.add_done_callback(_discard)


async def _run_phase_finalize_background(session_id: str, reason: str) -> None:
    try:
        result = await manager.finalize_phase_pipeline(session_id, reason=reason)
        broadcast = getattr(manager, "_broadcast", None)
        if callable(broadcast):
            await broadcast(session_id, {
                "type": "phase_finalize_completed",
                "session_id": session_id,
                "phase": result.get("phase") if isinstance(result, dict) else "finalized",
                "finalized": sanitize_phase_pipeline_response(result) if isinstance(result, dict) else {},
            })
    except Exception as exc:
        broadcast = getattr(manager, "_broadcast", None)
        if callable(broadcast):
            await broadcast(session_id, {
                "type": "phase_finalize_failed",
                "session_id": session_id,
                "error": str(exc)[:500],
            })
        raise
```

- [ ] **Step 5: Branch `finalize_phase()` for background mode**

Replace `finalize_phase()` with:

```python
@router.post("/sessions/{session_id}/phase/finalize")
async def finalize_phase(
    session_id: str,
    body: FinalizePhaseRequest = FinalizePhaseRequest(),
):
    _require_running_phase_session(session_id)
    if body.background:
        task = asyncio.create_task(_run_phase_finalize_background(session_id, body.reason))
        _track_phase_finalize_task(task)
        return {
            "phase": "finalize_started",
            "session_id": session_id,
            "status": "closing",
            "runtime_status": manager.get_status(session_id),
        }
    try:
        result = await manager.finalize_phase_pipeline(session_id, reason=body.reason)
        return sanitize_phase_pipeline_response(result)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
```

- [ ] **Step 6: Verify Task 1**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_server_route_split.py::test_finalize_phase_route_background_returns_started_and_schedules_task YouTubeBridge/tests/test_server_route_split.py::test_finalize_phase_route_returns_public_phase_shape_without_closing_internals -q --basetemp=.pyTestTemp/basetemp-closing-plan-task1
```

Expected: 2 passed.

- [ ] **Step 7: Commit Task 1**

```powershell
git add YouTubeBridge/models.py YouTubeBridge/server_routes/sessions.py YouTubeBridge/tests/test_server_route_split.py
git commit -m "fix: start phase finalize in background"
```

---

### Task 2: Make Studio Closing Non-Blocking and Preserve Local Visible Messages

**Files:**
- Modify: `YouTubeBridge/static/ui/studio.js:1708-1835,2064-2079`
- Test: `YouTubeBridge/tests/test_studio_ui.py`

- [ ] **Step 1: Write failing Studio assertions**

In `test_studio_app_wires_episode_plan_session_lifecycle`, add these assertions near the existing `/phase/finalize` assertion:

```python
    assert 'body: { reason: "operator_finalize", background: true }' in studio_js
    assert 'appendLog("INFO", "節目收尾流程已送出");' in studio_js
```

In `test_studio_presentation_tts_events_and_lifecycle_are_wired`, replace the existing interrupted-refresh assertion:

```python
    assert 'scheduleConversationRefresh("直播打斷");' in subscribe_body
```

with:

```python
    interrupted_branch = subscribe_body[
        subscribe_body.index('if (payload.type === "interaction_interrupted") {'):
        subscribe_body.index('if (["interaction_completed", "super_chat_batch_injected"].includes(payload.type)) {')
    ]
    assert 'appendLog("DEBUG", "互動已中斷，保留已顯示對話");' in interrupted_branch
    assert 'scheduleConversationRefresh("直播打斷");' not in interrupted_branch
```

Add this new test after `test_studio_conversation_is_newest_first_and_live_events_default_hidden`:

```python
def test_studio_conversation_refresh_preserves_locally_visible_messages():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    assert "visibleMessages: new Map()" in studio_js
    assert "function previewMessageKey(" in studio_js
    assert "function rememberVisibleMessage(" in studio_js
    assert "const merged = mergePreviewMessages(Array.from(state.visibleMessages.values()), visible);" in studio_js
    assert "state.visibleMessages.clear();" in studio_js
```

- [ ] **Step 2: Run the failing Studio tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_ui.py::test_studio_app_wires_episode_plan_session_lifecycle YouTubeBridge/tests/test_studio_ui.py::test_studio_presentation_tts_events_and_lifecycle_are_wired YouTubeBridge/tests/test_studio_ui.py::test_studio_conversation_refresh_preserves_locally_visible_messages -q --basetemp=.pyTestTemp/basetemp-closing-plan-task2
```

Expected: FAIL because Studio still uses blocking finalize, destructive interrupted refresh, and no persistent visible message map.

- [ ] **Step 3: Add local visible message state and helpers**

In `YouTubeBridge/static/ui/studio.js`, add this field to the `state` object:

```javascript
  visibleMessages: new Map(),
```

Add these helpers before `appendChatPreviewMessage()`:

```javascript
function previewMessageKey(message = {}) {
  const messageId = String(message?.message_id || message?.id || "").trim();
  if (messageId) return `${message?.role || "message"}:${messageId}`;
  return `${message?.character_id || message?.character_name || message?.role || "message"}:${message?.created_at || message?.timestamp || ""}:${String(message?.content || message?.message_text || "").slice(0, 80)}`;
}

function previewMessageTimeValue(message = {}) {
  const raw = message?.created_at || message?.timestamp || message?.published_at || "";
  const value = raw ? new Date(raw).getTime() : 0;
  return Number.isFinite(value) ? value : 0;
}

function mergePreviewMessages(...groups) {
  const merged = new Map();
  groups.flat().forEach((message) => {
    const content = String(message?.content || message?.message_text || "").trim();
    if (!content) return;
    merged.set(previewMessageKey(message), message);
  });
  return Array.from(merged.values()).sort((left, right) => {
    const timeDelta = previewMessageTimeValue(left) - previewMessageTimeValue(right);
    if (timeDelta !== 0) return timeDelta;
    return previewMessageKey(left).localeCompare(previewMessageKey(right));
  });
}

function rememberVisibleMessage(message = {}) {
  const content = String(message?.content || message?.message_text || "").trim();
  if (!content) return;
  state.visibleMessages.set(previewMessageKey(message), message);
}
```

- [ ] **Step 4: Preserve visible messages while rendering**

In `appendChatPreviewMessage()`, after `if (!content) return;`, add:

```javascript
  rememberVisibleMessage(message);
```

Replace `renderChatPreviewMessages()` with:

```javascript
function renderChatPreviewMessages(messages = []) {
  const visible = Array.isArray(messages) ? messages.filter((message) => (
    String(message?.role || "") !== "system_event"
    && String(message?.content || message?.message_text || "").trim()
  )) : [];
  visible.forEach(rememberVisibleMessage);
  const merged = mergePreviewMessages(Array.from(state.visibleMessages.values()), visible);
  clearConversationFeed();
  if (!merged.length) {
    renderConversationEmpty(state.sessionId ? "Live Session 已建立，等待後端產生 AI 對話。" : undefined);
    return;
  }
  merged.forEach((message) => appendChatPreviewMessage(message, { prepend: true }));
}
```

In `resetConversationForNewSession()`, add:

```javascript
  state.visibleMessages.clear();
```

Also clear the map when switching to a different selected session in the existing session-change branch if one exists in `refreshStudioSession()`.

- [ ] **Step 5: Make stopLive send background finalize**

Replace the `api()` call body in `stopLive()` with:

```javascript
    const data = await api(`/sessions/${encodeURIComponent(sessionId)}/phase/finalize`, {
      method: "POST",
      body: { reason: "operator_finalize", background: true },
    });
    appendLog("INFO", "節目收尾流程已送出");
    applySessionSnapshot({ ...(state.currentSession || {}), runtime_status: data.runtime_status || data, status: "closing" });
```

Do not call `await refreshStudioSession()` inside the success path of `stopLive()`. Let SSE `status`, `chat_message`, and `phase_finalize_completed` events drive updates.

- [ ] **Step 6: Stop destructive refresh on interrupted events**

In `subscribeSessionEvents()`, replace the `interaction_interrupted` branch with:

```javascript
      if (payload.type === "interaction_interrupted") {
        appendLog("DEBUG", "互動已中斷，保留已顯示對話");
        return;
      }
```

Add handling for background finalize completion:

```javascript
      if (payload.type === "phase_finalize_completed") {
        appendLog("INFO", "節目收尾流程已完成");
        refreshStudioSession();
        refreshConversation();
        return;
      }
      if (payload.type === "phase_finalize_failed") {
        appendLog("WARN", `節目收尾失敗：${payload.error || "unknown error"}`);
        refreshStudioSession();
        return;
      }
```

- [ ] **Step 7: Verify Task 2**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_ui.py::test_studio_app_wires_episode_plan_session_lifecycle YouTubeBridge/tests/test_studio_ui.py::test_studio_presentation_tts_events_and_lifecycle_are_wired YouTubeBridge/tests/test_studio_ui.py::test_studio_conversation_refresh_preserves_locally_visible_messages -q --basetemp=.pyTestTemp/basetemp-closing-plan-task2
```

Expected: 3 passed.

- [ ] **Step 8: Commit Task 2**

```powershell
git add YouTubeBridge/static/ui/studio.js YouTubeBridge/tests/test_studio_ui.py
git commit -m "fix: stream Studio finalize output immediately"
```

---

### Task 3: Record Visible SSE Output in Interaction Metadata

**Files:**
- Modify: `YouTubeBridge/bridge_engine.py:494-558,778-812`
- Test: `YouTubeBridge/tests/test_bridge_engine_injection.py`

- [ ] **Step 1: Write failing visible-output metadata test**

Add this test after `test_stream_result_drops_message_if_interrupted_before_broadcast`:

```python
@pytest.mark.asyncio
async def test_stream_result_marks_message_visible_after_broadcast():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["char-a"],
            "presentation_enabled": False,
        })
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director",
            "priority": 50,
            "status": "running",
            "memoria_session_id": "mem-a",
            "character_ids": ["char-a"],
            "content": "收尾前一則生成",
        })
        manager = YouTubeBridgeManager(storage)
        queue = await manager.subscribe("live-a")

        manager._dispatch_stream_chat_result(
            asyncio.get_running_loop(),
            "live-a",
            {
                "message_id": 42,
                "reply": "這句已經出現在畫面上。",
                "character_id": "char-a",
                "character_name": "可可",
                "timestamp": "2026-05-16T09:20:19",
            },
            source="director",
            interaction_job_id=interaction["job_id"],
        )

        payload = await _next_queue_event(queue, "chat_message")
        assert payload["message"]["content"] == "這句已經出現在畫面上。"

        updated = storage.get_interaction(interaction["job_id"])
        visible = updated["metadata"]["visible_messages"]
        assert visible == [{
            "message_id": 42,
            "role": "assistant",
            "content": "這句已經出現在畫面上。",
            "created_at": "2026-05-16T09:20:19",
            "timestamp": "2026-05-16T09:20:19",
            "character_id": "char-a",
            "character_name": "可可",
            "source": "director",
        }]
        assert updated["metadata"]["last_visible_message"]["content"] == "這句已經出現在畫面上。"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_injection.py::test_stream_result_marks_message_visible_after_broadcast -q --basetemp=.pyTestTemp/basetemp-closing-plan-task3
```

Expected: FAIL because visible messages are not recorded.

- [ ] **Step 3: Add visible-output helper**

In `YouTubeBridge/bridge_engine.py`, add this method near `_chat_message_from_stream_result()`:

```python
    def _mark_interaction_message_visible(
        self,
        interaction_job_id: str,
        message: dict[str, Any],
        *,
        source: str,
    ) -> None:
        if not interaction_job_id or not isinstance(message, dict):
            return
        content = str(message.get("content") or "").strip()
        if not content:
            return
        current = self.storage.get_interaction(interaction_job_id)
        if not current:
            return
        visible_message = {
            "message_id": message.get("message_id"),
            "role": message.get("role") or "assistant",
            "content": content,
            "created_at": message.get("created_at") or message.get("timestamp") or "",
            "timestamp": message.get("timestamp") or message.get("created_at") or "",
            "character_id": message.get("character_id"),
            "character_name": message.get("character_name"),
            "source": source,
        }
        metadata = dict(current.get("metadata") or {})
        visible_messages = [
            item for item in metadata.get("visible_messages", [])
            if isinstance(item, dict)
        ]
        message_id = str(visible_message.get("message_id") or "")
        visible_key = (
            f"id:{message_id}"
            if message_id
            else f"text:{visible_message['timestamp']}:{visible_message['content'][:120]}"
        )

        def item_key(item: dict[str, Any]) -> str:
            raw_id = str(item.get("message_id") or "")
            if raw_id:
                return f"id:{raw_id}"
            return f"text:{item.get('timestamp') or item.get('created_at') or ''}:{str(item.get('content') or '')[:120]}"

        if all(item_key(item) != visible_key for item in visible_messages):
            visible_messages.append(visible_message)
        self.storage.update_interaction(
            interaction_job_id,
            metadata={
                "visible_messages": visible_messages[-20:],
                "last_visible_message": visible_message,
                "has_visible_output": True,
            },
        )
```

- [ ] **Step 4: Mark visible messages after broadcast**

In `_broadcast_stream_chat_message()`, after the `await self._broadcast(...)` call, add:

```python
            self._mark_interaction_message_visible(
                interaction_job_id,
                message,
                source=source,
            )
```

In `_present_prepared_item()`, after broadcasting `presentation_item_ready`, call:

```python
        self._mark_interaction_message_visible(
            interaction_job_id,
            {
                **message,
                "message_id": item.get("message_id") or message.get("message_id"),
                "content": item.get("text") or message.get("content") or "",
                "created_at": item.get("presented_at") or message.get("created_at"),
                "timestamp": item.get("presented_at") or message.get("timestamp"),
            },
            source=source,
        )
```

In `_present_prepared_item()`, after the later `chat_message` broadcast, call the same helper with `chat_message`.

- [ ] **Step 5: Verify Task 3**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_injection.py::test_stream_result_drops_message_if_interrupted_before_broadcast YouTubeBridge/tests/test_bridge_engine_injection.py::test_stream_result_marks_message_visible_after_broadcast -q --basetemp=.pyTestTemp/basetemp-closing-plan-task3
```

Expected: 2 passed.

- [ ] **Step 6: Commit Task 3**

```powershell
git add YouTubeBridge/bridge_engine.py YouTubeBridge/tests/test_bridge_engine_injection.py
git commit -m "fix: track visible live interaction output"
```

---

### Task 4: Preserve Visible Interrupted Messages in Chat Preview

**Files:**
- Modify: `YouTubeBridge/server_routes/sessions.py:90-155,618-700`
- Test: `YouTubeBridge/tests/test_server_auth.py`

- [ ] **Step 1: Add visible-output preview test**

Add this test after `test_chat_preview_filters_interrupted_late_memoria_result`:

```python
@pytest.mark.asyncio
async def test_chat_preview_keeps_interrupted_result_once_visible(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "youtube_live.db")
    storage.upsert_connector({
        "connector_id": "yt-main",
        "display_name": "YouTube Main",
        "enabled": True,
    })
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "yt-main",
        "target_memoria_session_id": "mem-a",
        "character_ids": ["char-a", "char-b"],
        "presentation_enabled": False,
    })
    visible_prompt = "Beat shape: source_reframe. 白蓮回答榜單來源邊界。"
    visible = storage.create_interaction({
        "session_id": "live-a",
        "source": "director",
        "priority": 50,
        "status": "running",
        "memoria_session_id": "mem-a",
        "character_ids": ["char-a", "char-b"],
        "content": visible_prompt,
        "started_at": "2026-05-16T09:20:11",
        "metadata": {
            "visible_messages": [{
                "message_id": 101,
                "role": "assistant",
                "content": "這句已經出現在畫面上。",
                "timestamp": "2026-05-16T09:20:19",
                "character_id": "char-a",
                "character_name": "可可",
                "source": "director",
            }],
            "has_visible_output": True,
        },
    })
    storage.update_interaction(
        visible["job_id"],
        status="interrupted",
        reason="live_session_closing",
        completed_at="2026-05-16T09:20:15",
        interrupted_at="2026-05-16T09:20:13",
    )

    class FakeMemoriaClient:
        def get_session_history(self, session_id):
            assert session_id == "mem-a"
            return {
                "session": {"session_id": "mem-a", "message_count": 1},
                "messages": [{
                    "message_id": 101,
                    "role": "assistant",
                    "content": "這句已經出現在畫面上。",
                    "timestamp": "2026-05-16T09:20:19",
                    "character_id": "char-a",
                    "character_name": "可可",
                    "debug_info": {
                        "original_query": (
                            f"{visible_prompt}\n\n"
                            "請根據已提供的直播流程提示回應。"
                        ),
                    },
                }],
            }

    monkeypatch.setattr(server_module._sessions_routes, "storage", storage)
    monkeypatch.setattr(server_module._sessions_routes, "chat_preview_cache", {})
    monkeypatch.setattr(server_module._sessions_routes, "MemoriaClient", FakeMemoriaClient)

    preview = await server_module._sessions_routes.get_chat_preview("live-a", limit=20)

    assert [message["content"] for message in preview["messages"]] == ["這句已經出現在畫面上。"]
    assert preview["message_count"] == 1
```

- [ ] **Step 2: Run the failing preview test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_server_auth.py::test_chat_preview_keeps_interrupted_result_once_visible -q --basetemp=.pyTestTemp/basetemp-closing-plan-task4
```

Expected: FAIL because interrupted messages are filtered even when they were visible.

- [ ] **Step 3: Add visible-message matching helpers**

In `YouTubeBridge/server_routes/sessions.py`, add these helpers above `_message_matches_discarded_interaction()`:

```python
def _interaction_visible_messages(interaction: dict) -> list[dict]:
    metadata = interaction.get("metadata") if isinstance(interaction.get("metadata"), dict) else {}
    visible = metadata.get("visible_messages")
    if not isinstance(visible, list):
        return []
    return [item for item in visible if isinstance(item, dict)]


def _message_matches_visible_interaction(message: dict, interaction: dict) -> bool:
    message_id = _message_id_text(message)
    message_content = _compact_prompt_text(message.get("content"))
    for visible in _interaction_visible_messages(interaction):
        visible_id = "" if visible.get("message_id") is None else str(visible.get("message_id"))
        if visible_id and message_id and visible_id == message_id:
            return True
        visible_content = _compact_prompt_text(visible.get("content"))
        if visible_content and message_content and visible_content == message_content:
            return True
    return False
```

- [ ] **Step 4: Respect visible messages in discarded filtering**

At the top of `_message_matches_discarded_interaction()`, after the role check, add:

```python
    if _message_matches_visible_interaction(message, interaction):
        return False
```

Keep the existing result-message-id and `debug_info.original_query` matching below this visible check.

- [ ] **Step 5: Verify Task 4**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_server_auth.py::test_chat_preview_filters_interrupted_late_memoria_result YouTubeBridge/tests/test_server_auth.py::test_chat_preview_keeps_interrupted_result_once_visible -q --basetemp=.pyTestTemp/basetemp-closing-plan-task4
```

Expected: 2 passed.

- [ ] **Step 6: Commit Task 4**

```powershell
git add YouTubeBridge/server_routes/sessions.py YouTubeBridge/tests/test_server_auth.py
git commit -m "fix: keep visible interrupted chat preview messages"
```

---

### Task 5: Make Final Closing Reply to the Latest Visible Message

**Files:**
- Modify: `YouTubeBridge/engine_closing.py:417-455,621-660`
- Test: `YouTubeBridge/tests/test_bridge_engine_closing.py`

- [ ] **Step 1: Write failing final-closing context test**

Add this test near other final closing tests in `YouTubeBridge/tests/test_bridge_engine_closing.py`:

```python
@pytest.mark.asyncio
async def test_final_closing_uses_latest_visible_message_as_reply_target(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        session = storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["char-a", "char-b"],
            "auto_sc_thanks_on_finalize": False,
        })
        storage.create_interaction({
            "session_id": "live-a",
            "source": "director",
            "priority": 50,
            "status": "interrupted",
            "memoria_session_id": "mem-a",
            "character_ids": ["char-a", "char-b"],
            "content": "前一輪來源邊界",
            "metadata": {
                "visible_messages": [{
                    "message_id": 201,
                    "role": "assistant",
                    "content": "這句已經問白蓮下一步怎麼看。",
                    "timestamp": "2026-05-16T09:20:19",
                    "character_id": "char-a",
                    "character_name": "可可",
                    "source": "director",
                }],
                "last_visible_message": {
                    "message_id": 201,
                    "role": "assistant",
                    "content": "這句已經問白蓮下一步怎麼看。",
                    "timestamp": "2026-05-16T09:20:19",
                    "character_id": "char-a",
                    "character_name": "可可",
                    "source": "director",
                },
                "has_visible_output": True,
            },
        })
        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeClosingMemoriaClient)
        runtime = LiveRuntime(session_id="live-a", running=True, status="closing")
        captured: dict[str, str] = {}

        async def capture_send(_session, _state, decision):
            captured["prompt"] = decision["prompt"]
            captured["visible_reply_target"] = decision["visible_reply_target"]["content"]
            return {"interaction": {"status": "completed"}, "memoria_result": {}}

        monkeypatch.setattr(manager, "_send_director_turn", capture_send)

        result = await manager._run_final_closing_turn(runtime, session)

        assert result["status"] == "completed"
        assert "最後已顯示訊息" in captured["prompt"]
        assert "這句已經問白蓮下一步怎麼看。" in captured["prompt"]
        assert captured["visible_reply_target"] == "這句已經問白蓮下一步怎麼看。"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 2: Run the failing final-closing test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_closing.py::test_final_closing_uses_latest_visible_message_as_reply_target -q --basetemp=.pyTestTemp/basetemp-closing-plan-task5
```

Expected: FAIL because `_run_final_closing_turn()` does not include visible reply target context.

- [ ] **Step 3: Add latest visible message helper**

In `YouTubeBridge/engine_closing.py`, add this helper before `_run_final_closing_turn()`:

```python
    def _latest_visible_message_for_session(self, session_id: str) -> dict[str, Any] | None:
        latest: dict[str, Any] | None = None
        latest_time: datetime | None = None
        for interaction in self.storage.list_interactions(session_id, limit=500):
            metadata = interaction.get("metadata") if isinstance(interaction.get("metadata"), dict) else {}
            visible_messages = metadata.get("visible_messages")
            if not isinstance(visible_messages, list):
                continue
            for message in visible_messages:
                if not isinstance(message, dict):
                    continue
                content = str(message.get("content") or "").strip()
                if not content:
                    continue
                timestamp = self._parse_iso(str(message.get("timestamp") or message.get("created_at") or ""))
                if latest is None or (
                    timestamp is not None
                    and (latest_time is None or timestamp >= latest_time)
                ):
                    latest = message
                    latest_time = timestamp
        return latest
```

- [ ] **Step 4: Attach visible target to final closing decision**

In `_run_final_closing_turn()`, before `try: result = await self._send_director_turn(...)`, add:

```python
        visible_reply_target = self._latest_visible_message_for_session(runtime.session_id)
        if visible_reply_target:
            speaker = str(
                visible_reply_target.get("character_name")
                or visible_reply_target.get("role")
                or "上一位角色"
            ).strip()
            content = str(visible_reply_target.get("content") or "").strip()
            decision["visible_reply_target"] = visible_reply_target
            decision["prompt"] = (
                decision["prompt"]
                + "\n\n最後已顯示訊息："
                + f"{speaker}: {content}\n"
                + "收尾回應必須優先承接這句已顯示內容；若這句在回答問題，請承認它已回答過；"
                + "若這句在提問或交接給下一位角色，請對這句完成自然收束。"
                + "不要回到更早的問題重答。"
            )
```

- [ ] **Step 5: Verify Task 5**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_closing.py::test_final_closing_uses_latest_visible_message_as_reply_target YouTubeBridge/tests/test_bridge_engine_closing.py::test_duration_finalize_waits_for_active_generation_before_closing_thanks YouTubeBridge/tests/test_bridge_engine_closing.py::test_duration_finalize_interrupts_stale_active_generation_after_wait_timeout -q --basetemp=.pyTestTemp/basetemp-closing-plan-task5
```

Expected: 3 passed.

- [ ] **Step 6: Commit Task 5**

```powershell
git add YouTubeBridge/engine_closing.py YouTubeBridge/tests/test_bridge_engine_closing.py
git commit -m "fix: target final closing at visible output"
```

---

### Task 6: End-to-End Verification

**Files:**
- No code changes.

- [ ] **Step 1: Run focused regression suite**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_server_route_split.py YouTubeBridge/tests/test_server_auth.py YouTubeBridge/tests/test_bridge_engine_injection.py YouTubeBridge/tests/test_bridge_engine_closing.py YouTubeBridge/tests/test_studio_ui.py -q --basetemp=.pyTestTemp/basetemp-closing-plan-final
```

Expected: all selected tests pass.

- [ ] **Step 2: Run broader YouTubeBridge smoke suite**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py YouTubeBridge/tests/test_presentation_queue.py YouTubeBridge/tests/test_storage.py -q --basetemp=.pyTestTemp/basetemp-closing-plan-smoke
```

Expected: all selected tests pass.

- [ ] **Step 3: Manual Studio smoke**

Start services using the foreground-window rule:

```powershell
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "G:\ClaudeProject\MemoriaCore\start.bat" -WorkingDirectory "G:\ClaudeProject\MemoriaCore" -WindowStyle Normal
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "G:\ClaudeProject\MemoriaCore\YouTubeBridge\start.bat" -WorkingDirectory "G:\ClaudeProject\MemoriaCore\YouTubeBridge" -WindowStyle Normal
```

Open Studio and verify:

```text
1. Start a test Live Session.
2. Wait for at least one AI message to appear.
3. Press 收尾 / 停止直播 while a planned turn is active.
4. Expected: button returns after finalize is queued; closing messages appear one by one through SSE.
5. Expected: any message already visible before interrupt remains visible after refresh.
6. Expected: final closing text naturally responds to the last visible line, not to the line before it.
```

- [ ] **Step 4: Confirm worktree scope**

Run:

```powershell
git status -sb
git diff --stat
```

Expected:

```text
Only YouTubeBridge closing/Studio/test files from this plan are staged or modified.
Existing unrelated local edits, if any, are left unstaged.
```

- [ ] **Step 5: Final commit if previous task commits were skipped**

If tasks were not committed individually, commit the final scoped patch:

```powershell
git add YouTubeBridge/models.py YouTubeBridge/server_routes/sessions.py YouTubeBridge/bridge_engine.py YouTubeBridge/engine_closing.py YouTubeBridge/static/ui/studio.js YouTubeBridge/tests/test_server_route_split.py YouTubeBridge/tests/test_server_auth.py YouTubeBridge/tests/test_bridge_engine_injection.py YouTubeBridge/tests/test_bridge_engine_closing.py YouTubeBridge/tests/test_studio_ui.py
git commit -m "fix: preserve visible output during live closing"
```

---

## Self-Review Notes

- Requirement 1 is covered by Task 1 and Task 2: Studio queues background finalize and relies on SSE for one-by-one display.
- Requirement 2 is covered by Task 3 and Task 4: visible output is recorded and preserved through interrupted preview filtering.
- Requirement 2 reply-target behavior is covered by Task 5: final closing explicitly receives the latest visible message.
- No DB migration is required because visible output uses existing `live_interactions.metadata_json`.
- The plan intentionally does not change MemoriaCore conversation schema or the old non-Studio control panel.
