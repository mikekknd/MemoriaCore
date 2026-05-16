# YouTubeBridge Closing Cancel Source Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 live session closing 在源頭停止已被中斷的群組接力，避免 final closing 與上一個 follow-up LLM call 重疊。

**Architecture:** 目前 Bridge 會設定 `cancel_event` 並標記 interaction interrupted，但 MemoriaCore SSE group loop 仍可能在取消傳遞前啟動下一個角色的 LLM call。本計畫在 MemoriaCore group loop 增加 cooperative cancel checkpoint，並在 Bridge `MemoriaClient` 收到 stream result 前再次檢查 cancel，讓取消狀態能阻止新的 follow-up 生成與 downstream dispatch。

**Tech Stack:** FastAPI SSE、asyncio task cancellation、threading.Event、YouTubeBridge `MemoriaClient`、pytest。

---

## File Structure

- Modify `api/routers/chat/group_loop.py`: 加入 `cancel_event` 參數與 turn-level cancellation checkpoint。
- Modify `api/routers/chat/execution.py`: 建立並傳入 SSE cancel event，在 client disconnect/finally 時先 set event 再 cancel task。
- Modify `YouTubeBridge/memoria_client.py`: stream result dispatch 前再次檢查 `cancel_event` / `should_cancel`。
- Modify `tests/test_chat_orchestrator_unit/test_group_loop.py`: 新增 group loop 取消後不得啟動第二位角色的回歸測試。
- Modify `YouTubeBridge/tests/test_memoria_client.py`: 新增 cancel 發生於 stream result 前時不得呼叫 `on_result` 的回歸測試。

---

### Task 1: MemoriaCore Group Loop Cancel Checkpoints

**Files:**
- Modify: `api/routers/chat/group_loop.py`
- Test: `tests/test_chat_orchestrator_unit/test_group_loop.py`

- [ ] **Step 1: Write the failing group loop cancellation test**

Append this test to `tests/test_chat_orchestrator_unit/test_group_loop.py`:

```python
@pytest.mark.asyncio
async def test_group_loop_stops_before_followup_when_cancel_requested(monkeypatch):
    from api.routers.chat import group_loop

    session = SessionState(
        session_id="sid-cancel-before-followup",
        messages=[{"role": "system_event", "content": "直播節奏提示"}],
        user_id="__youtube_live__",
        character_id="char-a",
        active_character_ids=["char-a", "char-b"],
        session_mode="group",
        persona_face="public",
        channel="youtube_live",
    )
    session_manager._sessions[session.session_id] = session

    class FakeCharacterManager:
        def get_character(self, character_id):
            return {
                "character_id": character_id,
                "name": "角色A" if character_id == "char-a" else "角色B",
                "system_prompt": "測試角色",
                "tts_language": "",
                "tts_rules": "",
            }

    route_results = [
        GroupRouterResult(True, "char-a", "first", "new_speaker_add", "group_discussion"),
        GroupRouterResult(True, "char-b", "second", "new_speaker_reply_to_ai", "continue_group_discussion"),
    ]
    cancel_event = asyncio.Event()
    orchestration_calls = []

    def fake_group_router(*_args, **_kwargs):
        return route_results.pop(0)

    def fake_orchestration(*_args, **kwargs):
        orchestration_calls.append(kwargs["session_ctx"]["character_id"])
        cancel_event.set()
        return (
            "第一段回覆", [], {}, False, None,
            "內心", None, None, None,
            "", [], SharedToolState(executed=False),
        )

    monkeypatch.setattr(group_loop, "get_character_manager", lambda: FakeCharacterManager())
    monkeypatch.setattr(group_loop, "get_router", lambda: object())
    monkeypatch.setattr(group_loop, "run_group_router", fake_group_router)

    try:
        turns = await group_loop.run_group_chat_loop(
            session=session,
            user_prompt="直播自主推進",
            user_prefs={"group_chat_max_bot_turns": 2, "group_chat_turn_delay_seconds": 0},
            orchestration_fn=fake_orchestration,
            cancel_event=cancel_event,
        )
    finally:
        session_manager._sessions.clear()

    assert [turn["character_id"] for turn in turns] == ["char-a"]
    assert orchestration_calls == ["char-a"]
    assert route_results == [
        GroupRouterResult(True, "char-b", "second", "new_speaker_reply_to_ai", "continue_group_discussion")
    ]
```

- [ ] **Step 2: Run the new test and verify it fails**

Run:

```powershell
python -m pytest tests/test_chat_orchestrator_unit/test_group_loop.py::test_group_loop_stops_before_followup_when_cancel_requested --basetemp=.pyTestTemp/basetemp-cancel-guard -q
```

Expected: FAIL with `TypeError: run_group_chat_loop() got an unexpected keyword argument 'cancel_event'`.

- [ ] **Step 3: Add cooperative cancel support to `run_group_chat_loop`**

In `api/routers/chat/group_loop.py`, add a helper near the top-level constants:

```python
def _group_loop_cancel_requested(cancel_event: asyncio.Event | None) -> bool:
    return bool(cancel_event and cancel_event.is_set())
```

Change the function signature:

```python
async def run_group_chat_loop(
    *,
    session: SessionState,
    user_prompt: str,
    user_prefs: dict,
    orchestration_fn: Callable[..., tuple],
    on_event: Callable[[dict], None] | None = None,
    on_turn: Callable[[dict[str, Any]], Any] | None = None,
    user_name: str = "",
    expose_llm_trace: bool = False,
    extra_session_ctx: dict | None = None,
    transient_user_content: str = "",
    max_turns_override: int | None = None,
    cancel_event: asyncio.Event | None = None,
) -> list[dict[str, Any]]:
```

Add checkpoints at these positions:

```python
for turn_index in range(max_turns):
    if _group_loop_cancel_requested(cancel_event):
        break

    route = await asyncio.to_thread(...)

    if _group_loop_cancel_requested(cancel_event):
        break

    ...

    if _group_loop_cancel_requested(cancel_event):
        break

    result = await asyncio.to_thread(...)

    if _group_loop_cancel_requested(cancel_event):
        break
```

Keep the final checkpoint before `session_manager.add_assistant_message(...)`; if cancellation arrives after provider return but before persistence, the stale reply must not be stored.

- [ ] **Step 4: Run the cancellation test and verify it passes**

Run:

```powershell
python -m pytest tests/test_chat_orchestrator_unit/test_group_loop.py::test_group_loop_stops_before_followup_when_cancel_requested --basetemp=.pyTestTemp/basetemp-cancel-guard -q
```

Expected: PASS.

---

### Task 2: Pass SSE Cancel Event From Execution Layer

**Files:**
- Modify: `api/routers/chat/execution.py`
- Test: `tests/test_chat_orchestrator_unit/test_group_loop.py`

- [ ] **Step 1: Write the execution handoff expectation**

Extend the test from Task 1 by asserting it passes through the same public API used by `_iter_group_sse_events` is not practical without a full ASGI client. Instead, keep the direct group-loop test as the behavioral contract and make the execution layer change mechanical.

- [ ] **Step 2: Wire `cancel_event` in `_iter_group_sse_events`**

In `api/routers/chat/execution.py`, create an `asyncio.Event` before `group_task`:

```python
cancel_event = asyncio.Event()
```

Pass it into `run_group_chat_loop(...)`:

```python
group_task = asyncio.create_task(chat_rest.run_group_chat_loop(
    session=prepared.runtime_session,
    user_prompt=prepared.orchestration_prompt,
    user_prefs=prepared.user_prefs,
    orchestration_fn=prepared.orchestration_fn,
    on_event=on_event,
    on_turn=on_turn,
    user_name=chat_rest._chat_user_display_name(prepared.current_user, prepared.external_context),
    expose_llm_trace=chat_rest._can_expose_llm_trace(prepared.current_user),
    extra_session_ctx=prepared.extra_session_ctx,
    transient_user_content=prepared.transient_user_content,
    max_turns_override=chat_rest._external_context_group_turn_limit(
        prepared.runtime_session,
        prepared.external_context,
    ),
    cancel_event=cancel_event,
))
```

Set the event before cancelling the task:

```python
finally:
    cancel_event.set()
    if not group_task.done():
        group_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await group_task
```

- [ ] **Step 3: Run the group loop suite**

Run:

```powershell
python -m pytest tests/test_chat_orchestrator_unit/test_group_loop.py --basetemp=.pyTestTemp/basetemp-cancel-group-loop -q
```

Expected: PASS.

---

### Task 3: Bridge Stream Result Dispatch Guard

**Files:**
- Modify: `YouTubeBridge/memoria_client.py`
- Test: `YouTubeBridge/tests/test_memoria_client.py`

- [ ] **Step 1: Write the failing stream cancel test**

Append this test to `YouTubeBridge/tests/test_memoria_client.py`:

```python
import threading
import pytest

from memoria_client import GenerationInterrupted


class _CancelBeforeResultResponse(_FakeStreamResponse):
    def __init__(self, cancel_event):
        self.cancel_event = cancel_event
        self.status_code = 200
        self.text = ""

    def iter_lines(self, decode_unicode=False):
        self.cancel_event.set()
        line = 'data: {"type": "result", "session_id": "mem-a", "message_id": 3, "reply": "過期", "character_id": "char-b"}'
        return [line] if decode_unicode else [line.encode("utf-8")]


def test_chat_stream_sync_does_not_dispatch_result_after_cancel():
    cancel_event = threading.Event()
    client = MemoriaClient(base_url="http://memoria.test/api/v1", admin_bypass=True)
    fake_session = _FakeSession()
    fake_session.post = lambda *_args, **_kwargs: _CancelBeforeResultResponse(cancel_event)
    client.session = fake_session
    client.ensure_auth = lambda: None
    streamed = []

    with pytest.raises(GenerationInterrupted):
        client.chat_stream_sync(
            content="直播提示",
            session_id="mem-a",
            character_ids=["char-a", "char-b"],
            external_context={"source": "youtube_live_director", "source_session_id": "yt-a"},
            cancel_event=cancel_event,
            on_result=streamed.append,
        )

    assert streamed == []
```

- [ ] **Step 2: Run the new stream cancel test and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_memoria_client.py::test_chat_stream_sync_does_not_dispatch_result_after_cancel --basetemp=.pyTestTemp/basetemp-memoria-client-cancel -q
```

Expected: FAIL because `on_result` is called after cancel is set.

- [ ] **Step 3: Add the second cancel check before dispatch**

In `YouTubeBridge/memoria_client.py`, inside `if event.get("type") == "result":`, add the guard after `last_result = event` and before `on_result(event)`:

```python
if (cancel_event and cancel_event.is_set()) or (should_cancel and should_cancel()):
    response.close()
    raise GenerationInterrupted("generation interrupted")
```

Keep `last_result = event` before the guard so director timing can still know the provider returned, but never dispatch stale results.

- [ ] **Step 4: Run the stream cancel tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_memoria_client.py --basetemp=.pyTestTemp/basetemp-memoria-client-cancel -q
```

Expected: PASS.

---

### Task 4: Final Verification and Commit

**Files:**
- Verify: `api/routers/chat/group_loop.py`
- Verify: `api/routers/chat/execution.py`
- Verify: `YouTubeBridge/memoria_client.py`
- Verify: `tests/test_chat_orchestrator_unit/test_group_loop.py`
- Verify: `YouTubeBridge/tests/test_memoria_client.py`

- [ ] **Step 1: Run focused regression set**

Run:

```powershell
python -m pytest tests/test_chat_orchestrator_unit/test_group_loop.py YouTubeBridge/tests/test_memoria_client.py --basetemp=.pyTestTemp/basetemp-cancel-source-guard -q
```

Expected: PASS.

- [ ] **Step 2: Run diff check**

Run:

```powershell
git diff --check
```

Expected: exit code 0. Windows LF/CRLF reminders are acceptable if no whitespace error is reported.

- [ ] **Step 3: Commit**

Run:

```powershell
git add api/routers/chat/group_loop.py api/routers/chat/execution.py YouTubeBridge/memoria_client.py tests/test_chat_orchestrator_unit/test_group_loop.py YouTubeBridge/tests/test_memoria_client.py
git commit -m "Prevent stale live follow-up after closing cancel"
```

Expected: commit succeeds with only the five listed files.
