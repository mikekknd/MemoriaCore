# MemoriaCore Transient Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `transient_context` chat request contract so PersonaCore can inject scene/world runtime context into final chat generation without writing that context into session history or memory extraction.

**Architecture:** Add a small request model and normalization path beside `external_context`, keep both context channels mutually exclusive, and carry normalized runtime context through `session_ctx` only. Final chat prompt rendering stays in `core/prompt_utils.py`, where dynamic prefix blocks already live, and renders a minimal `<runtime_context>` block immediately before the latest user message.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, pytest, existing MemoriaCore chat orchestration modules.

---

## Confirmed Contract

- `transient_context` is a new field on `ChatSyncRequest`.
- First-version schema: `source`, `context_text`, optional `max_chars`.
- `source` is cleaned and kept for debug/summary only; it is not rendered into the LLM prompt.
- `context_text` is normalized, capped, and rendered as:

```xml
<runtime_context>
...
</runtime_context>
```

- `transient_context` only reaches final `category=chat`; it does not feed router, expand, tool routing, or memory lookup query text.
- Clean user content still follows current rules: persist `display_content` when present, otherwise persist `content`.
- `transient_context.context_text` is not persisted in session history and is not part of memory extraction snapshots.
- A request with both `external_context` and `transient_context` is rejected with HTTP 400 and a safe log message.
- `/chat/sync` and `/chat/stream-sync` share the same `prepare_chat_execution()` path, so the contract must be implemented there rather than in only one endpoint.

## File Structure

- Modify `api/models/requests.py`
  - Add visible constants for the transient context size contract.
  - Add `TransientContextRequest`.
  - Add `transient_context` to `ChatSyncRequest`.
- Modify `api/routers/chat_rest.py`
  - Add `_resolve_transient_context_payload()`.
  - Add `_reject_mutually_exclusive_contexts()`.
  - Keep `_memory_write_policy_for_request()` unchanged for transient context.
- Modify `api/routers/chat/execution.py`
  - Add `transient_context` to `PreparedChatExecution`.
  - Resolve and validate transient context in `prepare_chat_execution()`.
  - Pass transient context into `_build_session_ctx()` and `_build_extra_session_ctx()`.
- Modify `core/prompt_utils.py`
  - Add `_build_runtime_context_block()`.
  - Render runtime context as the last dynamic block before the latest user message.
- Modify `prompts_default.json`
  - Add `runtime_context_block`.
- Modify `tests/test_chat_external_context.py`
  - Cover normalization, cap, source cleaning, mutual exclusion, memory policy, and display persistence.
- Modify `tests/test_prompt_utils.py`
  - Cover prompt prefix rendering and absence of source metadata in LLM-visible text.
- Modify `tests/test_chat_orchestrator_unit/test_memory_context.py`
  - Cover final chat prompt placement.
- Modify `tests/test_architecture_refactor.py`
  - Keep sync and stream path guarantees visible to future agents.
- Modify `docs/API_使用說明書.md`
  - Document request field, cap behavior, final-chat-only visibility, and mutual exclusion.
- Modify `docs/codebase-structure.md`
  - Add an agent-facing chat context contract note.

---

### Task 1: Request Model And Transient Context Normalization

**Files:**
- Modify: `api/models/requests.py`
- Modify: `api/routers/chat_rest.py`
- Test: `tests/test_chat_external_context.py`

- [ ] **Step 1: Write failing tests for transient context normalization**

In `tests/test_chat_external_context.py`, extend the import block:

```python
from api.routers.chat_rest import (
    _build_external_context_visible_event,
    _chat_user_display_name,
    _external_context_group_turn_limit,
    _live_session_scope_for_external_context,
    _memory_write_policy_for_request,
    _messages_for_orchestration,
    _reject_mutually_exclusive_contexts,
    _resolve_chat_display_content,
    _resolve_external_context_payload,
    _resolve_transient_context_payload,
    _transient_user_content_for_external_context,
)
```

Append these tests near the existing external context payload tests:

```python
def test_transient_context_payload_is_generic_and_capped():
    body = ChatSyncRequest(
        content="可以看一下房間裡面有甚麼東西嗎",
        transient_context={
            "source": "personacore scene!",
            "context_text": "x" * 1500,
            "max_chars": 1000,
        },
    )

    context, summary = _resolve_transient_context_payload(body)

    assert context is not None
    assert context["source"] == "personacore_scene_"
    assert len(context["context_text"]) == 1000
    assert summary == {
        "source": "personacore_scene_",
        "truncated": True,
        "max_chars": 1000,
    }


def test_transient_context_payload_ignores_empty_context_text():
    body = ChatSyncRequest(
        content="hello",
        transient_context={
            "source": "personacore_scene",
            "context_text": "  \r\n  ",
        },
    )

    context, summary = _resolve_transient_context_payload(body)

    assert context is None
    assert summary == {}


def test_transient_context_default_cap_is_visible_to_agents():
    from api.models.requests import (
        TRANSIENT_CONTEXT_DEFAULT_MAX_CHARS,
        TRANSIENT_CONTEXT_HARD_MAX_CHARS,
    )

    body = ChatSyncRequest(
        content="hello",
        transient_context={
            "source": "personacore_scene",
            "context_text": "x" * (TRANSIENT_CONTEXT_DEFAULT_MAX_CHARS + 100),
        },
    )

    context, summary = _resolve_transient_context_payload(body)

    assert len(context["context_text"]) == TRANSIENT_CONTEXT_DEFAULT_MAX_CHARS
    assert summary["truncated"] is True
    assert TRANSIENT_CONTEXT_HARD_MAX_CHARS >= TRANSIENT_CONTEXT_DEFAULT_MAX_CHARS
```

- [ ] **Step 2: Run tests and verify they fail for missing model/helper**

Run:

```powershell
python -m pytest tests/test_chat_external_context.py::test_transient_context_payload_is_generic_and_capped tests/test_chat_external_context.py::test_transient_context_payload_ignores_empty_context_text tests/test_chat_external_context.py::test_transient_context_default_cap_is_visible_to_agents --basetemp=.pyTestTemp/basetemp-transient-context-request -q
```

Expected: FAIL because `ChatSyncRequest` does not accept `transient_context` and `_resolve_transient_context_payload` does not exist.

- [ ] **Step 3: Add request constants and model**

In `api/models/requests.py`, insert these constants near the top after `USERNAME_RE`:

```python
TRANSIENT_CONTEXT_DEFAULT_MAX_CHARS = 8000
TRANSIENT_CONTEXT_MIN_MAX_CHARS = 1000
TRANSIENT_CONTEXT_HARD_MAX_CHARS = 12000
TRANSIENT_CONTEXT_SOURCE_MAX_CHARS = 64
```

Insert this model before `ChatSyncRequest`:

```python
class TransientContextRequest(BaseModel):
    """Final-chat-only runtime context supplied by an app integration.

    Agent navigation note:
    - `context_text` is capped by TRANSIENT_CONTEXT_* constants.
    - The rendered LLM prompt uses only `context_text`.
    - `source` is debug metadata and is not rendered into final chat.
    """

    source: str = Field("runtime", max_length=128)
    context_text: str
    max_chars: Optional[int] = Field(
        None,
        ge=TRANSIENT_CONTEXT_MIN_MAX_CHARS,
        le=TRANSIENT_CONTEXT_HARD_MAX_CHARS,
    )
```

Update `ChatSyncRequest`:

```python
class ChatSyncRequest(BaseModel):
    content: str
    display_content: Optional[str] = None
    session_id: Optional[str] = None
    character_ids: Optional[list[str]] = None
    group_name: Optional[str] = None
    channel: Optional[str] = None
    channel_uid: Optional[str] = None
    user_id: Optional[str] = None
    channel_class: Optional[Literal["public", "private"]] = None
    persona_face: Optional[Literal["public", "private"]] = None
    external_context: Optional[dict] = None
    transient_context: Optional[TransientContextRequest] = None
    include_speech: bool = True
    memory_write_policy: Literal["normal", "transient"] = "normal"
```

- [ ] **Step 4: Add normalization helper**

In `api/routers/chat_rest.py`, add imports near the existing request import:

```python
from api.models.requests import (
    ChatSyncRequest,
    TRANSIENT_CONTEXT_DEFAULT_MAX_CHARS,
    TRANSIENT_CONTEXT_HARD_MAX_CHARS,
    TRANSIENT_CONTEXT_MIN_MAX_CHARS,
)
```

Replace the existing single-line import of `ChatSyncRequest`.

Add this helper after `_resolve_external_context_payload()`:

```python
def _resolve_transient_context_payload(body: ChatSyncRequest) -> tuple[dict | None, dict]:
    """Normalize app runtime context for final chat only.

    Agent navigation note:
    - This is not YouTubeBridge `external_context`.
    - `context_text` is the only LLM-visible value.
    - The cap constants live in api.models.requests for fast code search.
    """
    raw = body.transient_context
    if raw is None:
        return None, {}

    source = re.sub(
        r"[^A-Za-z0-9_.:-]",
        "_",
        str(raw.source or "runtime").strip() or "runtime",
    )[:64]
    context_text_original = str(raw.context_text or "").replace("\r", "\n")
    context_text = context_text_original.strip()
    if not context_text:
        return None, {}

    requested_max = raw.max_chars
    if requested_max is None:
        max_chars = TRANSIENT_CONTEXT_DEFAULT_MAX_CHARS
    else:
        max_chars = max(
            TRANSIENT_CONTEXT_MIN_MAX_CHARS,
            min(int(requested_max), TRANSIENT_CONTEXT_HARD_MAX_CHARS),
        )
    if len(context_text) > max_chars:
        context_text = context_text[:max_chars].rstrip()

    summary = {
        "source": source,
        "truncated": len(context_text_original.strip()) > len(context_text),
        "max_chars": max_chars,
    }
    return {"source": source, "context_text": context_text, "summary": summary}, summary
```

- [ ] **Step 5: Run normalization tests and verify they pass**

Run:

```powershell
python -m pytest tests/test_chat_external_context.py::test_transient_context_payload_is_generic_and_capped tests/test_chat_external_context.py::test_transient_context_payload_ignores_empty_context_text tests/test_chat_external_context.py::test_transient_context_default_cap_is_visible_to_agents --basetemp=.pyTestTemp/basetemp-transient-context-request -q
```

Expected: PASS.

- [ ] **Step 6: Commit request model and normalization**

```powershell
git add api/models/requests.py api/routers/chat_rest.py tests/test_chat_external_context.py
git commit -m "feat(chat): add transient context request contract"
```

---

### Task 2: Mutual Exclusion And Safe Logging

**Files:**
- Modify: `api/routers/chat_rest.py`
- Modify: `api/routers/chat/execution.py`
- Test: `tests/test_chat_external_context.py`

- [ ] **Step 1: Write failing mutual exclusion test**

Append this test to `tests/test_chat_external_context.py`:

```python
def test_external_and_transient_context_are_mutually_exclusive(monkeypatch):
    from fastapi import HTTPException
    import core.system_logger as system_logger

    logged = []

    def fake_log_error(category, message, details=None):
        logged.append({"category": category, "message": message, "details": details or {}})

    monkeypatch.setattr(system_logger.SystemLogger, "log_error", fake_log_error)
    body = ChatSyncRequest(
        content="hello",
        session_id="sid-a",
        external_context={"source": "youtube_live", "context_text": "觀眾: hi"},
        transient_context={
            "source": "personacore_scene",
            "context_text": "[PersonaCore scene awareness]\nCurrent scene: Room",
        },
    )

    with pytest.raises(HTTPException) as exc:
        _reject_mutually_exclusive_contexts(body)

    assert exc.value.status_code == 400
    assert "mutually exclusive" in str(exc.value.detail)
    assert logged
    assert "mutually exclusive" in logged[0]["message"]
    assert logged[0]["details"]["session_id"] == "sid-a"
    assert logged[0]["details"]["external_source"] == "youtube_live"
    assert logged[0]["details"]["transient_source"] == "personacore_scene"
    assert "context_text" not in logged[0]["details"]
```

- [ ] **Step 2: Run the mutual exclusion test and verify it fails**

Run:

```powershell
python -m pytest tests/test_chat_external_context.py::test_external_and_transient_context_are_mutually_exclusive --basetemp=.pyTestTemp/basetemp-transient-context-mutual -q
```

Expected: FAIL because `_reject_mutually_exclusive_contexts` does not exist.

- [ ] **Step 3: Add mutual exclusion helper**

In `api/routers/chat_rest.py`, add:

```python
def _reject_mutually_exclusive_contexts(body: ChatSyncRequest) -> None:
    if not body.external_context or body.transient_context is None:
        return
    from core.system_logger import SystemLogger

    external_source = ""
    if isinstance(body.external_context, dict):
        external_source = str(body.external_context.get("source") or "").strip()[:64]
    transient_source = str(getattr(body.transient_context, "source", "") or "").strip()[:64]
    message = "external_context and transient_context are mutually exclusive"
    SystemLogger.log_error(
        "ChatTransientContext",
        message,
        details={
            "session_id": str(body.session_id or "")[:160],
            "external_source": external_source,
            "transient_source": transient_source,
        },
    )
    raise HTTPException(400, detail=message)
```

- [ ] **Step 4: Call the helper from the shared prepare path**

In `api/routers/chat/execution.py`, inside `prepare_chat_execution()` immediately before resolving contexts:

```python
    require_db_writes_enabled()
    chat_rest._reject_mutually_exclusive_contexts(body)
    external_context, external_context_summary = chat_rest._resolve_external_context_payload(body)
    transient_context, transient_context_summary = chat_rest._resolve_transient_context_payload(body)
```

Keep `transient_context_summary` local for now; Task 3 stores `transient_context`.

- [ ] **Step 5: Run mutual exclusion test and shared architecture test**

Run:

```powershell
python -m pytest tests/test_chat_external_context.py::test_external_and_transient_context_are_mutually_exclusive tests/test_architecture_refactor.py --basetemp=.pyTestTemp/basetemp-transient-context-mutual -q
```

Expected: PASS.

- [ ] **Step 6: Commit mutual exclusion**

```powershell
git add api/routers/chat_rest.py api/routers/chat/execution.py tests/test_chat_external_context.py
git commit -m "fix(chat): reject conflicting transient context channels"
```

---

### Task 3: Final Chat Prompt Injection

**Files:**
- Modify: `prompts_default.json`
- Modify: `core/prompt_utils.py`
- Modify: `tests/test_prompt_utils.py`
- Modify: `tests/test_chat_orchestrator_unit/test_memory_context.py`

- [ ] **Step 1: Write failing prompt utility test**

In `tests/test_prompt_utils.py`, add `runtime_context_block` to `_FakePromptManager.get()`:

```python
            "runtime_context_block": (
                "<runtime_context>\n"
                "{context_text}\n"
                "</runtime_context>"
            ),
```

Append this test:

```python
def test_runtime_context_prefix_renders_clean_block_without_metadata(monkeypatch):
    monkeypatch.setattr(prompt_utils, "get_prompt_manager", lambda: _FakePromptManager())

    prefix = prompt_utils.build_user_prefix(
        [{"role": "user", "content": "可以看一下房間裡面有甚麼東西嗎"}],
        session_ctx={
            "transient_runtime_context": {
                "source": "personacore_scene",
                "context_text": (
                    "# Chat Scene Awareness Contract\n"
                    "[PersonaCore scene awareness]\n"
                    "Current scene: Room\n"
                    "Persistent scene objects: window, low table, sofa"
                ),
            },
        },
    )

    assert "<runtime_context>" in prefix
    assert "[PersonaCore scene awareness]" in prefix
    assert "Persistent scene objects: window, low table, sofa" in prefix
    assert "personacore_scene" not in prefix
    assert "persist=" not in prefix
    assert "visibility=" not in prefix
```

- [ ] **Step 2: Write failing final chat placement test**

Append this test to `tests/test_chat_orchestrator_unit/test_memory_context.py`:

```python
def test_build_final_chat_context_injects_runtime_context_before_latest_user_message():
    from core.chat_orchestrator.generation_context import build_final_chat_context

    api_messages, _clean_history, sys_prompt = build_final_chat_context(
        char_sys_prompt="角色 prompt",
        group_participants_block="",
        mem_ctx="",
        reply_rules="用繁體中文回應。",
        session_messages=[{"role": "user", "content": "可以看一下房間裡面有甚麼東西嗎"}],
        context_window=5,
        user_prefs={},
        session_ctx={
            "transient_runtime_context": {
                "source": "personacore_scene",
                "context_text": (
                    "[PersonaCore scene awareness]\n"
                    "Persistent scene objects: window, low table, sofa"
                ),
            },
        },
        force_group=False,
    )

    latest_user = api_messages[-1]["content"]
    assert "<runtime_context>" not in sys_prompt
    assert "[PersonaCore scene awareness]" not in sys_prompt
    assert latest_user.index("<runtime_context>") < latest_user.index("可以看一下房間裡面有甚麼東西嗎")
    assert "Persistent scene objects: window, low table, sofa" in latest_user
```

- [ ] **Step 3: Run prompt tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_prompt_utils.py::test_runtime_context_prefix_renders_clean_block_without_metadata tests/test_chat_orchestrator_unit/test_memory_context.py::test_build_final_chat_context_injects_runtime_context_before_latest_user_message --basetemp=.pyTestTemp/basetemp-runtime-context-prompt -q
```

Expected: FAIL because runtime context is not rendered.

- [ ] **Step 4: Add prompt template**

In `prompts_default.json`, add this key near `external_chat_context_block`:

```json
  "runtime_context_block": {
    "label": "暫態 runtime context 區塊 (Runtime Context Block)",
    "description": "注入在最新使用者訊息前的 app runtime context；只服務 final chat，不寫入 session history 或 memory extraction。",
    "used_by": "core/prompt_utils.py → build_user_prefix()",
    "placeholders": [
      "{context_text}"
    ],
    "template": "<runtime_context>\n{context_text}\n</runtime_context>"
  },
```

When editing JSON, keep commas valid around neighboring keys.

- [ ] **Step 5: Add runtime context prefix builder**

In `core/prompt_utils.py`, add this helper after `_build_external_chat_context_block()`:

```python
def _build_runtime_context_block(session_ctx: dict | None) -> str:
    """注入 app runtime context；只把 context_text 渲染給 final chat LLM。"""
    if not session_ctx:
        return ""
    runtime_context = session_ctx.get("transient_runtime_context")
    if not isinstance(runtime_context, dict):
        return ""
    context_text = str(runtime_context.get("context_text") or "").strip()
    if not context_text:
        return ""
    return get_prompt_manager().get("runtime_context_block").format(
        context_text=context_text,
    )
```

In `build_user_prefix()`, after `external_chat_context_block = ...`, add:

```python
    runtime_context_block = _build_runtime_context_block(session_ctx)
```

Replace the return statement with:

```python
    blocks = [env_block]
    if user_identity_block:
        blocks.append(user_identity_block)
    if external_chat_context_block:
        blocks.append(external_chat_context_block)
    if emo_block.strip():
        blocks.append(emo_block.strip())
    if runtime_context_block:
        blocks.append(runtime_context_block)
    return "\n".join(blocks) + "\n\n"
```

This keeps `<runtime_context>` closest to the latest user message.

- [ ] **Step 6: Run prompt tests and verify they pass**

Run:

```powershell
python -m pytest tests/test_prompt_utils.py::test_runtime_context_prefix_renders_clean_block_without_metadata tests/test_chat_orchestrator_unit/test_memory_context.py::test_build_final_chat_context_injects_runtime_context_before_latest_user_message --basetemp=.pyTestTemp/basetemp-runtime-context-prompt -q
```

Expected: PASS.

- [ ] **Step 7: Commit final chat prompt injection**

```powershell
git add prompts_default.json core/prompt_utils.py tests/test_prompt_utils.py tests/test_chat_orchestrator_unit/test_memory_context.py
git commit -m "feat(chat): inject runtime context into final prompt"
```

---

### Task 4: Execution Path And Memory Policy Boundary

**Files:**
- Modify: `api/routers/chat/execution.py`
- Modify: `api/routers/chat_rest.py`
- Test: `tests/test_chat_external_context.py`

- [ ] **Step 1: Write failing execution-boundary tests**

Append these tests to `tests/test_chat_external_context.py`:

```python
def test_transient_context_does_not_force_transient_memory_write_policy():
    body = ChatSyncRequest(
        content="我喜歡低矮桌旁邊的位置",
        transient_context={
            "source": "personacore_scene",
            "context_text": "[PersonaCore scene awareness]\nCurrent scene: Room",
        },
    )

    context, _summary = _resolve_transient_context_payload(body)

    assert context is not None
    assert _memory_write_policy_for_request(body, None) == "normal"


def test_build_session_ctx_carries_transient_context_without_external_context():
    from api.routers.chat.execution import _build_extra_session_ctx, _build_session_ctx

    class Session:
        user_id = "user-a"
        character_id = "char-a"
        persona_face = "private"
        session_id = "sid-a"
        bot_id = ""
        channel = "personacore"
        active_character_ids = ["char-a"]
        session_mode = "single"
        group_name = "PersonaCore"

    transient_context = {
        "source": "personacore_scene",
        "context_text": "[PersonaCore scene awareness]\nCurrent scene: Room",
    }

    session_ctx = _build_session_ctx(
        Session(),
        {"id": "user-a", "username": "tester"},
        None,
        transient_context,
    )
    extra_ctx = _build_extra_session_ctx(None, "normal", transient_context)

    assert session_ctx["transient_runtime_context"] == transient_context
    assert extra_ctx["transient_runtime_context"] == transient_context
    assert "external_chat_context" not in session_ctx
    assert "external_chat_context" not in extra_ctx
    assert "memory_write_policy" not in session_ctx
    assert "memory_write_policy" not in extra_ctx


@pytest.mark.asyncio
async def test_persist_incoming_message_keeps_display_content_with_transient_context(monkeypatch):
    persisted = []

    async def fake_add_user_message(session_id, content):
        persisted.append((session_id, content))
        return 1

    monkeypatch.setattr(chat_rest.session_manager, "add_user_message", fake_add_user_message)
    body = ChatSyncRequest(
        content="hidden orchestration text",
        display_content="可以看一下房間裡面有甚麼東西嗎",
        transient_context={
            "source": "personacore_scene",
            "context_text": "[PersonaCore scene awareness]\nCurrent scene: Room",
        },
    )

    await chat_rest._persist_incoming_chat_message("sid-a", body, None, {})

    assert persisted == [("sid-a", "可以看一下房間裡面有甚麼東西嗎")]
```

- [ ] **Step 2: Run execution-boundary tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_chat_external_context.py::test_transient_context_does_not_force_transient_memory_write_policy tests/test_chat_external_context.py::test_build_session_ctx_carries_transient_context_without_external_context tests/test_chat_external_context.py::test_persist_incoming_message_keeps_display_content_with_transient_context --basetemp=.pyTestTemp/basetemp-transient-context-execution -q
```

Expected: FAIL because `_build_session_ctx()` and `_build_extra_session_ctx()` do not accept transient context.

- [ ] **Step 3: Carry transient context through PreparedChatExecution**

In `api/routers/chat/execution.py`, add dataclass fields after `external_context_summary`:

```python
    transient_context: dict | None
    transient_context_summary: dict
```

In `prepare_chat_execution()`, after resolving transient context in Task 2, update session context calls:

```python
    session_ctx = _build_session_ctx(session, current_user, external_context, transient_context)
    if memory_write_policy == "transient":
        session_ctx["memory_write_policy"] = "transient"
    extra_session_ctx = _build_extra_session_ctx(external_context, memory_write_policy, transient_context)
```

In the returned `PreparedChatExecution(...)`, add:

```python
        transient_context=transient_context,
        transient_context_summary=transient_context_summary,
```

- [ ] **Step 4: Update context builder signatures**

In `api/routers/chat/execution.py`, replace `_build_session_ctx()` with:

```python
def _build_session_ctx(
    session,
    current_user: dict,
    external_context: dict | None,
    transient_context: dict | None = None,
) -> dict:
    from api.routers import chat_rest

    session_ctx = {
        "user_id": session.user_id,
        "character_id": session.character_id,
        "persona_face": session.persona_face,
        "session_id": session.session_id,
        "bot_id": session.bot_id,
        "channel": session.channel,
        "user_name": chat_rest._chat_user_display_name(current_user, external_context),
        "active_character_ids": list(session.active_character_ids or [session.character_id]),
        "session_mode": session.session_mode,
        "group_name": session.group_name,
        "expose_llm_trace": chat_rest._can_expose_llm_trace(current_user),
    }
    if external_context:
        session_ctx["external_chat_context"] = external_context
    if transient_context:
        session_ctx["transient_runtime_context"] = transient_context
    return session_ctx
```

Replace `_build_extra_session_ctx()` with:

```python
def _build_extra_session_ctx(
    external_context: dict | None,
    memory_write_policy: str,
    transient_context: dict | None = None,
) -> dict | None:
    extra_session_ctx = {}
    if external_context:
        extra_session_ctx["external_chat_context"] = external_context
    if transient_context:
        extra_session_ctx["transient_runtime_context"] = transient_context
    if memory_write_policy == "transient":
        extra_session_ctx["memory_write_policy"] = "transient"
    return extra_session_ctx or None
```

- [ ] **Step 5: Run execution-boundary tests and verify they pass**

Run:

```powershell
python -m pytest tests/test_chat_external_context.py::test_transient_context_does_not_force_transient_memory_write_policy tests/test_chat_external_context.py::test_build_session_ctx_carries_transient_context_without_external_context tests/test_chat_external_context.py::test_persist_incoming_message_keeps_display_content_with_transient_context --basetemp=.pyTestTemp/basetemp-transient-context-execution -q
```

Expected: PASS.

- [ ] **Step 6: Run sync/stream shared-path architecture tests**

Run:

```powershell
python -m pytest tests/test_architecture_refactor.py --basetemp=.pyTestTemp/basetemp-transient-context-architecture -q
```

Expected: PASS. This confirms `/chat/sync` and `/chat/stream-sync` still route through shared execution helpers.

- [ ] **Step 7: Commit execution boundary**

```powershell
git add api/routers/chat/execution.py api/routers/chat_rest.py tests/test_chat_external_context.py tests/test_architecture_refactor.py
git commit -m "feat(chat): carry transient runtime context through execution"
```

---

### Task 5: Documentation For API Callers And Agent Navigation

**Files:**
- Modify: `docs/API_使用說明書.md`
- Modify: `docs/codebase-structure.md`

- [ ] **Step 1: Update API request field table**

In `docs/API_使用說明書.md`, in the `/chat/sync` request body table, add this row after `external_context`:

```markdown
| `transient_context` | object | ❌ | null | app runtime context，只注入 final chat prompt；不寫入 session history，不進 memory extraction，且不可與 `external_context` 同時使用 |
```

- [ ] **Step 2: Add API contract subsection**

In `docs/API_使用說明書.md`, after the request body table, add:

```markdown
#### `transient_context` contract

`transient_context` 是給 PersonaCore 這類 app integration 使用的本輪暫態 runtime context。它只進 final chat LLM prompt，不進 router / expand / tool routing，也不寫入 MemoriaCore session history 或 memory extraction。

最小 payload：

```json
{
  "content": "可以看一下房間裡面有甚麼東西嗎",
  "display_content": "可以看一下房間裡面有甚麼東西嗎",
  "transient_context": {
    "source": "personacore_scene",
    "context_text": "[PersonaCore scene awareness]\nCurrent scene: Room\nPersistent scene objects: window, low table, sofa",
    "max_chars": 8000
  }
}
```

規則：

- `context_text` 會被正規化換行與裁切；預設上限由 `TRANSIENT_CONTEXT_DEFAULT_MAX_CHARS` 定義，硬上限由 `TRANSIENT_CONTEXT_HARD_MAX_CHARS` 定義。
- `source` 只供 debug / summary 使用，不會渲染進 LLM prompt。
- final chat prompt 只會看到乾淨的 `<runtime_context>...</runtime_context>`。
- 有 `display_content` 時，session history 仍保存 `display_content`；沒有時保存 `content`。
- `external_context` 和 `transient_context` 互斥；同時傳會回 HTTP 400。
```

When inserting this Markdown, escape the nested fenced JSON by using four backticks for the outer code block if needed.

- [ ] **Step 3: Add codebase navigation note**

In `docs/codebase-structure.md`, add this section near the chat router file map:

```markdown
### Chat context channels for agents

When changing chat request context behavior, distinguish these two channels:

- `external_context`: bridge / YouTubeBridge external content. It can alter live scope, visible system events, group turn limits, and transient memory policy.
- `transient_context`: app runtime context. It is final-chat-only, renders as `<runtime_context>`, does not enter session history, and does not disable normal user-message memory extraction.

Implementation map:

- Request schema and cap constants: `api/models/requests.py`
- Payload normalization and mutual exclusion: `api/routers/chat_rest.py`
- Shared sync / stream execution path: `api/routers/chat/execution.py`
- Final prompt rendering: `core/prompt_utils.py`
- Prompt assembly caller: `core/chat_orchestrator/generation_context.py`
```

- [ ] **Step 4: Verify docs contain searchable contract terms**

Run:

```powershell
rg -n "transient_context|runtime_context|TRANSIENT_CONTEXT|external_context" docs/API_使用說明書.md docs/codebase-structure.md
```

Expected: Output includes both docs and all four search terms.

- [ ] **Step 5: Commit documentation**

```powershell
git add docs/API_使用說明書.md docs/codebase-structure.md
git commit -m "docs(chat): document transient runtime context contract"
```

---

### Task 6: Focused Regression Sweep

**Files:**
- No source edits unless verification exposes a real regression.

- [ ] **Step 1: Run focused test suite**

Run:

```powershell
python -m pytest tests/test_chat_external_context.py tests/test_prompt_utils.py tests/test_chat_orchestrator_unit/test_memory_context.py tests/test_architecture_refactor.py --basetemp=.pyTestTemp/basetemp-transient-context-suite -q
```

Expected: PASS.

- [ ] **Step 2: Run broader chat orchestration checks**

Run:

```powershell
python -m pytest tests/test_chat_orchestrator_unit/test_coordinator.py tests/test_chat_orchestrator_unit/test_group_loop.py --basetemp=.pyTestTemp/basetemp-transient-context-chat -q
```

Expected: PASS.

- [ ] **Step 3: Run syntax compilation for touched modules**

Run:

```powershell
python -m compileall api/models/requests.py api/routers/chat_rest.py api/routers/chat/execution.py core/prompt_utils.py core/chat_orchestrator/generation_context.py
```

Expected: `compileall` reports successful compilation and no syntax errors.

- [ ] **Step 4: Run diff whitespace check**

Run:

```powershell
git diff --check
```

Expected: no output.

- [ ] **Step 5: Inspect final diff scope**

Run:

```powershell
git status -sb
git diff --stat
```

Expected: only these paths are modified:

```text
api/models/requests.py
api/routers/chat_rest.py
api/routers/chat/execution.py
core/prompt_utils.py
prompts_default.json
tests/test_chat_external_context.py
tests/test_prompt_utils.py
tests/test_chat_orchestrator_unit/test_memory_context.py
tests/test_architecture_refactor.py
docs/API_使用說明書.md
docs/codebase-structure.md
```

- [ ] **Step 6: Commit verification fixes if any were required**

If Step 1 through Step 4 all pass without edits, skip this commit. If verification required code or test fixes, commit only those touched files:

```powershell
git add <fixed-files>
git commit -m "test(chat): stabilize transient context contract"
```

---

## Self-Review

**Spec coverage:** The plan covers the selected A strategy: new `transient_context`, final-chat-only injection, clean user message persistence, context-only memory exclusion, minimal schema, source cleaning, cap constants, mutual exclusion with safe log, docs, and sync/stream shared-path tests.

**Placeholder scan:** The plan contains no placeholder markers, no deferred implementation markers, and every code-changing step includes concrete code or exact file content to insert.

**Type consistency:** The plan consistently uses `transient_context` for the request field, `transient_runtime_context` for internal `session_ctx`, `context_text` for LLM-visible text, and `<runtime_context>` for final prompt rendering.
