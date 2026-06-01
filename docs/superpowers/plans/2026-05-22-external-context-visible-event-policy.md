# External Context Visible Event Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backward-compatible `external_context.persist_visible_event=false` contract so hidden autonomous-turn context can drive an assistant reply without creating a visible system event.

**Architecture:** Keep the change inside the existing MemoriaCore chat REST normalization and visible-event persistence boundary. `_resolve_external_context_payload()` normalizes the optional flag into the runtime external context, and `_build_external_context_visible_event()` remains the single decision point for whether a visible `system_event` is persisted. YouTubeBridge compatibility is protected by regression tests that prove existing `youtube_live` and `youtube_live_director` behavior is unchanged when the new flag is absent.

**Tech Stack:** Python 3.12, FastAPI request models, existing dict-shaped `external_context`, pytest.

---

## Scope Check

This plan implements only MemoriaCore scope A:

- Add `external_context.persist_visible_event=false`.
- Preserve existing behavior when the flag is omitted.
- Add regression tests for generic external context and YouTubeBridge behavior.
- Update the MemoriaCore API document.

This plan does not implement PersonaCore scheduled world events, PersonaCore client payload changes, promise detection, event claiming, TTS playback, or `group_turn_limit` expansion for non-YouTubeBridge sources.

## Files

- Modify: `api/routers/chat_rest.py`
  - Responsibility: normalize `external_context` and decide whether to persist visible system events.
- Modify: `tests/test_chat_external_context.py`
  - Responsibility: regression coverage for external context normalization, hidden visible-event policy, and YouTubeBridge compatibility.
- Modify: `docs/API_使用說明書.md`
  - Responsibility: document the public request-body contract for `external_context.persist_visible_event`.

## Design Notes

- Field name: `persist_visible_event`.
- Accepted false value: JSON boolean `false` only after Pydantic/body parsing gives Python `False`.
- Omitted field: existing behavior remains unchanged.
- Any value other than Python `False`: existing behavior remains unchanged.
- `youtube_live_director`: remains hidden even without this flag because current hard-coded behavior is an existing contract.
- `youtube_live`: continues to create a visible `youtube_live_chat_batch` system event when the flag is omitted.
- General external context: continues to create an `external_context_notice` system event when the flag is omitted.
- `external_context` still forces transient memory write policy through `_memory_write_policy_for_request()`.
- `external_context` and `transient_context` remain mutually exclusive through `_reject_mutually_exclusive_contexts()`.

---

### Task 1: Add Failing Tests For Visible Event Policy

**Files:**
- Modify: `tests/test_chat_external_context.py`

- [x] **Step 1: Add resolver test for preserving explicit false**

Add this test after `test_external_context_payload_is_generic_and_capped()`:

```python
def test_external_context_payload_preserves_persist_visible_event_false():
    body = ChatSyncRequest(
        content="hello",
        external_context={
            "source": "personacore_world_event",
            "context_text": "Event: 抹茶千層已經送上桌。",
            "persist_visible_event": False,
        },
    )

    context, summary = _resolve_external_context_payload(body)

    assert context is not None
    assert context["source"] == "personacore_world_event"
    assert context["persist_visible_event"] is False
    assert "persist_visible_event" not in summary
```

- [x] **Step 2: Run the new resolver test and verify it fails**

Run:

```powershell
python -m pytest tests/test_chat_external_context.py::test_external_context_payload_preserves_persist_visible_event_false -q
```

Expected: FAIL with `KeyError: 'persist_visible_event'`.

- [x] **Step 3: Add hidden visible-event policy test**

Add this test after `test_external_context_visible_event_is_not_llm_visible()`:

```python
def test_external_context_persist_visible_event_false_skips_visible_system_event():
    body = ChatSyncRequest(
        content="請根據 PersonaCore world event 自然延續。",
        external_context={
            "source": "personacore_world_event",
            "context_text": (
                "[PersonaCore world event]\n"
                "Event type: item_arrives\n"
                "Event: 抹茶千層已經送上桌。"
            ),
            "persist_visible_event": False,
        },
    )
    context, summary = _resolve_external_context_payload(body)

    event = _build_external_context_visible_event(context, summary)

    assert event is None
```

- [x] **Step 4: Run the hidden visible-event policy test and verify it fails**

Run:

```powershell
python -m pytest tests/test_chat_external_context.py::test_external_context_persist_visible_event_false_skips_visible_system_event -q
```

Expected: FAIL because `_build_external_context_visible_event()` still returns an `external_context_notice` tuple.

- [x] **Step 5: Add omitted-flag backward-compatibility test for generic external context**

Add this test after `test_external_context_persist_visible_event_false_skips_visible_system_event()`:

```python
def test_external_context_without_persist_visible_event_keeps_visible_system_event():
    body = ChatSyncRequest(
        content="請根據外部上下文回應。",
        external_context={
            "source": "personacore_world_event",
            "context_text": "Event: 抹茶千層已經送上桌。",
        },
    )
    context, summary = _resolve_external_context_payload(body)

    event = _build_external_context_visible_event(context, summary)

    assert event is not None
    content, debug_info = event
    assert content.startswith("外部上下文注入：1 則")
    assert "抹茶千層已經送上桌" in content
    assert debug_info["event_type"] == "external_context_notice"
    assert debug_info["llm_visible"] is False
    assert debug_info["source"] == "personacore_world_event"
```

- [x] **Step 6: Run the omitted-flag compatibility test**

Run:

```powershell
python -m pytest tests/test_chat_external_context.py::test_external_context_without_persist_visible_event_keeps_visible_system_event -q
```

Expected: PASS before implementation. This confirms the existing behavior that must remain unchanged.

- [x] **Step 7: Commit tests after the failing expectations are observed**

Run:

```powershell
git add tests/test_chat_external_context.py
git commit -m "test(chat): cover external context visible event policy"
```

Expected: commit succeeds with only `tests/test_chat_external_context.py` staged.

---

### Task 2: Implement Minimal Visible Event Policy

**Files:**
- Modify: `api/routers/chat_rest.py:522-615`
- Modify: `api/routers/chat_rest.py:775-817`

- [x] **Step 1: Preserve `persist_visible_event=false` during normalization**

In `_resolve_external_context_payload()`, after the `context = { ... }` block and before `if group_turn_limit is not None:`, add:

```python
    if raw.get("persist_visible_event") is False:
        context["persist_visible_event"] = False
```

The surrounding block should read:

```python
    context = {
        "source": source,
        "context_text": context_text,
        "visible_events": visible_events,
        "summary": summary,
    }
    if raw.get("persist_visible_event") is False:
        context["persist_visible_event"] = False
    if group_turn_limit is not None:
        context["group_turn_limit"] = group_turn_limit
```

- [x] **Step 2: Skip visible system event when the normalized flag is false**

In `_build_external_context_visible_event()`, immediately after:

```python
    if not external_context:
        return None
```

add:

```python
    if external_context.get("persist_visible_event") is False:
        return None
```

The beginning of the function should read:

```python
def _build_external_context_visible_event(
    external_context: dict | None,
    summary: dict,
) -> tuple[str, dict] | None:
    if not external_context:
        return None
    if external_context.get("persist_visible_event") is False:
        return None
    context_text = str(external_context.get("context_text") or "").strip()
    if not context_text:
        return None
```

- [x] **Step 3: Run the two new policy tests**

Run:

```powershell
python -m pytest `
  tests/test_chat_external_context.py::test_external_context_payload_preserves_persist_visible_event_false `
  tests/test_chat_external_context.py::test_external_context_persist_visible_event_false_skips_visible_system_event `
  -q
```

Expected: both tests PASS.

- [x] **Step 4: Commit implementation**

Run:

```powershell
git add api/routers/chat_rest.py
git commit -m "feat(chat): allow hidden external context events"
```

Expected: commit succeeds with only `api/routers/chat_rest.py` staged.

---

### Task 3: Protect YouTubeBridge Behavior

**Files:**
- Modify: `tests/test_chat_external_context.py`

- [x] **Step 1: Add explicit YouTube Live omitted-flag regression test**

Add this test after `test_external_context_visible_event_is_not_llm_visible()`:

```python
def test_youtube_live_external_context_without_persist_flag_still_persists_visible_event():
    body = ChatSyncRequest(
        content="請根據已帶入的 YouTube 直播留言上下文回應。",
        external_context={
            "source": "youtube_live",
            "context_text": "觀眾A: 這段怎麼看？",
            "visible_events": [
                {
                    "event_id": "evt-a",
                    "author_display_name": "觀眾A",
                    "message_text": "這段怎麼看？",
                }
            ],
            "summary": {"event_count": 1},
        },
    )
    context, summary = _resolve_external_context_payload(body)

    event = _build_external_context_visible_event(context, summary)

    assert event is not None
    content, debug_info = event
    assert content == "YouTube Live 留言注入：1 則\n觀眾A: 這段怎麼看？"
    assert debug_info["event_type"] == "youtube_live_chat_batch"
    assert debug_info["source"] == "youtube_live"
    assert debug_info["llm_visible"] is False
```

- [x] **Step 2: Add explicit YouTube Live false-flag behavior test**

Add this test after `test_youtube_live_external_context_without_persist_flag_still_persists_visible_event()`:

```python
def test_youtube_live_external_context_can_opt_out_of_visible_event_when_explicitly_false():
    body = ChatSyncRequest(
        content="請根據已帶入的 YouTube 直播留言上下文回應。",
        external_context={
            "source": "youtube_live",
            "context_text": "觀眾A: 這段怎麼看？",
            "visible_events": [
                {
                    "event_id": "evt-a",
                    "author_display_name": "觀眾A",
                    "message_text": "這段怎麼看？",
                }
            ],
            "persist_visible_event": False,
            "summary": {"event_count": 1},
        },
    )
    context, summary = _resolve_external_context_payload(body)

    event = _build_external_context_visible_event(context, summary)

    assert event is None
```

- [x] **Step 3: Keep YouTube Live Director hidden behavior unchanged**

Run the existing test:

```powershell
python -m pytest tests/test_chat_external_context.py::test_youtube_live_director_context_is_not_persisted_as_visible_event -q
```

Expected: PASS. This proves `youtube_live_director` remains hidden even without the new flag.

- [x] **Step 4: Run the explicit YouTubeBridge compatibility tests**

Run:

```powershell
python -m pytest `
  tests/test_chat_external_context.py::test_external_context_visible_event_is_not_llm_visible `
  tests/test_chat_external_context.py::test_youtube_live_external_context_without_persist_flag_still_persists_visible_event `
  tests/test_chat_external_context.py::test_youtube_live_external_context_can_opt_out_of_visible_event_when_explicitly_false `
  tests/test_chat_external_context.py::test_youtube_live_director_context_is_not_persisted_as_visible_event `
  tests/test_chat_external_context.py::test_youtube_live_external_context_preserves_required_response_without_final_closing `
  tests/test_chat_external_context.py::test_youtube_live_external_context_preserves_final_closing_for_final_action `
  tests/test_chat_external_context.py::test_youtube_live_external_context_ignores_final_closing_for_required_response_action `
  tests/test_chat_external_context.py::test_youtube_live_director_external_context_uses_explicit_group_turn_limit `
  tests/test_chat_external_context.py::test_youtube_live_director_context_payload_preserves_group_turn_limit `
  tests/test_chat_external_context.py::test_youtube_live_chat_external_context_keeps_short_batch_round_limit `
  -q
```

Expected: all listed tests PASS.

- [x] **Step 5: Commit YouTubeBridge compatibility tests**

Run:

```powershell
git add tests/test_chat_external_context.py
git commit -m "test(chat): lock youtube external context compatibility"
```

Expected: commit succeeds with only `tests/test_chat_external_context.py` staged.

---

### Task 4: Document The Contract

**Files:**
- Modify: `docs/API_使用說明書.md:149`
- Modify: `docs/API_使用說明書.md:174-207`

- [x] **Step 1: Update the request body table row**

Change the `external_context` row from:

```markdown
| `external_context` | object | ❌ | null | transient 外部上下文，不寫入 private memory |
```

to:

```markdown
| `external_context` | object | ❌ | null | transient 外部上下文，不寫入 private memory；可用 `persist_visible_event=false` 跳過可見 system event |
```

- [x] **Step 2: Add an `external_context` contract section before `transient_context`**

Insert this section before the existing `#### transient_context contract` heading:

```markdown
#### `external_context` contract

`external_context` 是 Bridge / runtime trigger 使用的本輪暫態外部上下文。它會進 final chat prompt，但不會把 incoming `content` 當成一般 user message 保存。MemoriaCore 仍會保存 assistant output。

欄位：

- `source?: string`：debug / summary 用來源標籤。
- `context_text: string`：本輪 external context 內容。
- `max_chars?: int`：本次 request 的裁切上限，仍受硬上限限制。
- `visible_events?: object[]`：可見事件摘要來源，例如 YouTube Live 留言。
- `persist_visible_event?: bool`：省略時維持既有行為；設為 `false` 時不建立可見 `system_event`。
- `summary?: object`：debug / response retrieval context 摘要。

規則：

- `external_context.context_text` 不會以 user message 形式寫入 session history。
- `external_context.context_text` 會注入 final chat prompt。
- 未帶 `persist_visible_event` 時，一般 external context 仍建立 `external_context_notice`；`youtube_live` 仍建立 `youtube_live_chat_batch`。
- `persist_visible_event=false` 時，不建立可見 `system_event`，也不建立 user message。
- `youtube_live_director` 既有行為維持不變：即使未帶 `persist_visible_event=false`，也不建立 visible event。
- 有 `external_context` 時，本輪 memory pipeline 以 transient policy 跳過，避免外部 trigger 內容進入長期記憶。
- `external_context` 和 `transient_context` 互斥；同時傳會回 HTTP 400。

PersonaCore autonomous assistant turn 最小 payload：

```json
{
  "session_id": "memoriacore-session-id",
  "character_ids": ["default"],
  "group_name": "PersonaCore",
  "content": "請根據 PersonaCore world event 讓角色自然延續，只輸出角色會說出口的台詞。不要提到系統事件、排程、hidden context 或內部流程。",
  "display_content": "角色主動回合。",
  "include_speech": false,
  "tool_routing_policy": "disabled",
  "memory_write_policy": "transient",
  "external_context": {
    "source": "personacore_world_event",
    "context_text": "[PersonaCore world event]\nEvent type: item_arrives\nCurrent scene: Cafe\nEvent: 抹茶千層已經送上桌。\nInstruction: Continue naturally as the character. Output only spoken dialogue.",
    "persist_visible_event": false,
    "summary": {
      "source": "personacore_world_event",
      "event_type": "item_arrives",
      "event_id": "scheduled-event-id"
    }
  }
}
```
```

- [x] **Step 3: Run a docs grep check**

Run:

```powershell
rg -n "persist_visible_event|external_context contract|youtube_live_chat_batch|personacore_world_event" docs/API_使用說明書.md
```

Expected: output includes the new field name, the new section heading, the YouTube compatibility sentence, and the PersonaCore example source.

- [x] **Step 4: Commit docs**

Run:

```powershell
git add docs/API_使用說明書.md
git commit -m "docs(chat): document external context visible event policy"
```

Expected: commit succeeds with only `docs/API_使用說明書.md` staged.

---

### Task 5: Final Verification

**Files:**
- Verify: `api/routers/chat_rest.py`
- Verify: `tests/test_chat_external_context.py`
- Verify: `docs/API_使用說明書.md`

- [x] **Step 1: Run focused external context tests**

Run:

```powershell
python -m pytest tests/test_chat_external_context.py -q
```

Expected: all tests in `tests/test_chat_external_context.py` PASS.

- [x] **Step 2: Run focused orchestrator tests for tool-routing and final prompt safety**

Run:

```powershell
python -m pytest tests/test_chat_orchestrator_unit/test_coordinator.py -q
```

Expected: all tests in `tests/test_chat_orchestrator_unit/test_coordinator.py` PASS.

- [x] **Step 3: Inspect changed file scope**

Run:

```powershell
git diff --stat
git diff -- api/routers/chat_rest.py tests/test_chat_external_context.py docs/API_使用說明書.md
```

Expected:

- Only the three planned files contain implementation, tests, and documentation changes.
- `api/routers/chat_rest.py` only preserves `persist_visible_event=False` and skips visible event persistence at `_build_external_context_visible_event()`.
- No PersonaCore files are modified.
- No YouTubeBridge engine/client files are modified.

- [x] **Step 4: Run final status check**

Run:

```powershell
git status -sb
```

Expected: working tree is clean after commits, or only unrelated pre-existing files remain unstaged.

## Post-Review Follow-Up

PersonaCore world event 目前仍使用通用 `external_chat_context_block`，因此 final prompt 會標成 `trusted="false"`。本計畫刻意不變更這個行為；後續設計應補一個通用 `trust_boundary` / `system_generated` 類欄位來表示內部排程事件，而不是新增 `source == "personacore_world_event"` 的 prompt 特例。

## Self-Review

Spec coverage:

- Hidden autonomous-turn context without visible system event is covered by Task 1 and Task 2.
- Existing behavior when the flag is omitted is covered by Task 1.
- YouTubeBridge behavior is covered by Task 3.
- API documentation is covered by Task 4.
- Final verification is covered by Task 5.

Placeholder scan:

- This plan contains concrete file paths, test functions, implementation snippets, commands, and expected outcomes.
- No step relies on unspecified validation or unspecified error handling.

Type consistency:

- The request field is consistently named `persist_visible_event`.
- The runtime normalized context uses dict key `persist_visible_event`.
- The implementation checks `external_context.get("persist_visible_event") is False`, matching the test payloads.
