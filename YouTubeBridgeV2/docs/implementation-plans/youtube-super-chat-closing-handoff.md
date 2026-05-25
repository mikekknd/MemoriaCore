# YouTube Super Chat Closing Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 roadmap `3C`：把已 normalized/persisted 的 YouTube Super Chat metadata 交給 closing runner，產生 display-safe acknowledgement actions 與 MemoriaCore closing context。

**Architecture:** YouTube adapter 仍只負責抽取 `SuperChatMetadata`；runtime/storage 已保存 normalized `youtube_super_chat` event。`MemoriaClosingRunner` 在 closing 開始時從 storage event history 讀取 pending Super Chat public metadata，轉成 `build_closing_request(...)` 的 `pending_super_chats`，讓現有 Closing contract 產生 acknowledgement actions。

**Tech Stack:** Python 3.13、pytest、`NormalizedYouTubeEvent` / `SuperChatMetadata` public payload、`MemoriaClosingRunner`、StorageManager-like `list_v2_live_events`。

---

## Scope

Roadmap item：`3C：Super Chat metadata 與 closing handoff`

完成條件：

- `MemoriaClosingRunner` 可從 `list_v2_live_events(session_id)` 讀取 `youtube_super_chat` event。
- 只使用 event 的 sanitized `public_metadata.public_payload.super_chat` 與 display fields。
- pending Super Chat 會進入 `ClosingRequest.super_chat_actions`，並出現在 Memoria closing external context 的 `closing.super_chat_actions`。
- malformed / already acknowledged Super Chat 不阻斷 closing。
- raw YouTube payload、access token、authorization、secret 不進 closing request/finalization summary。

不包含：

- 真 YouTube API polling。
- scheduler / HTTP ingestion route。
- 實際回覆 YouTube Super Chat 或修改 YouTube 狀態。
- acknowledgement status durable write-back。

## File Structure

- Modify: `YouTubeBridgeV2/runtime/memoria_runners.py`
  - 新增 `_pending_super_chats(...)` 與 `_super_chat_from_event(...)` helper。
  - `MemoriaClosingRunner.run(...)` 使用 command payload pending list + storage event-derived pending list。
- Modify: `tests/youtubebridge_v2/test_runtime_memoria_runners.py`
  - 新增 Super Chat event handoff regression。
- Modify docs:
  - `YouTubeBridgeV2/docs/modules/closing.md`
  - `YouTubeBridgeV2/docs/modules/youtube-adapter.md`
  - `YouTubeBridgeV2/docs/api-reference-index.md`
  - `YouTubeBridgeV2/docs/architecture-index.md`

## Event Contract

Storage event shape consumed by 3C:

```python
{
    "event_id": "sc-1",
    "event_type": "youtube_super_chat",
    "public_metadata": {
        "public_payload": {
            "event_id": "sc-1",
            "author_display_name": "Rin",
            "message_text": "Great stream",
            "published_at": "2026-05-12T08:05:00Z",
            "super_chat": {
                "super_chat_id": "sc-1",
                "amount_micros": 150000000,
                "currency": "TWD",
                "amount_display_string": "NT$150",
                "public_message": "Great stream",
                "acknowledgement_status": "pending",
            },
        },
        "display_event": {
            "event_type": "super_chat",
            "author_display_name": "Rin",
            "message_text": "Great stream",
        },
    },
}
```

Closing pending item shape:

```python
{
    "super_chat_id": "sc-1",
    "author_display_name": "Rin",
    "amount_display_string": "NT$150",
    "message_text": "Great stream",
}
```

---

### Task 1: Super Chat Closing Red Test

**Files:**
- Modify: `tests/youtubebridge_v2/test_runtime_memoria_runners.py`

- [ ] **Step 1: Add Super Chat event helper**

Add near `_assert_no_private_payload(...)`:

```python
def _append_super_chat_event(storage: InMemoryV2StorageManager) -> None:
    storage.append_v2_live_event(
        "session-runner",
        {
            "event_id": "sc-1",
            "event_type": "youtube_super_chat",
            "public_metadata": {
                "public_payload": {
                    "event_id": "sc-1",
                    "event_type": "super_chat",
                    "author_display_name": "Rin",
                    "message_text": "Great stream",
                    "published_at": "2026-05-12T08:05:00Z",
                    "super_chat": {
                        "super_chat_id": "sc-1",
                        "amount_micros": 150000000,
                        "currency": "TWD",
                        "amount_display_string": "NT$150",
                        "public_message": "Great stream",
                        "acknowledgement_status": "pending",
                    },
                },
                "display_event": {
                    "event_id": "sc-1",
                    "event_type": "super_chat",
                    "author_display_name": "Rin",
                    "message_text": "Great stream",
                    "raw_youtube_payload": {"access_token": "must not leak"},
                },
            },
        },
    )
```

- [ ] **Step 2: Add red test**

Append after `test_closing_runner_builds_final_message_and_marks_closing_completed`:

```python
def test_closing_runner_loads_pending_super_chats_from_youtube_events():
    storage = InMemoryV2StorageManager()
    port = _create_bound_session(storage)
    storage.update_v2_session("session-runner", {"current_phase": "closing"})
    _append_super_chat_event(storage)
    transport = FakeMemoriaTransport(
        {
            "session_id": "memoria-3",
            "message_id": "close-1",
            "character_id": "host",
            "reply": "Closing message",
        }
    )
    runner = MemoriaClosingRunner(storage, transport)

    result = runner.run(
        command=_command("cmd-closing-super-chat"),
        snapshot=port.read_snapshot("session-runner"),
        transition=_transition(
            LiveSessionPhase.CLOSING,
            action="start_closing",
            reason=PhaseTransitionReason.MANUAL_CLOSE,
        ),
        now=NOW,
    )

    closing_context = transport.requests[0].body["external_context"]["closing"]
    assert result.status == "ok"
    assert closing_context["super_chat_actions"] == [
        {
            "super_chat_id": "sc-1",
            "action_type": "acknowledge",
            "status": "pending",
            "author_display_name": "Rin",
            "amount_display_string": "NT$150",
            "public_message": "Great stream",
            "error_summary": {},
        }
    ]
    _assert_no_private_payload(transport.requests[0].body)
    _assert_no_private_payload(result)
```

- [ ] **Step 3: Run red test**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_memoria_runners.py::test_closing_runner_loads_pending_super_chats_from_youtube_events -q
```

Expected: FAIL because `MemoriaClosingRunner` only reads `command.payload["pending_super_chats"]`, not storage events.

---

### Task 2: Green Closing Handoff Implementation

**Files:**
- Modify: `YouTubeBridgeV2/runtime/memoria_runners.py`
- Test: `tests/youtubebridge_v2/test_runtime_memoria_runners.py`

- [ ] **Step 1: Replace pending list call in `MemoriaClosingRunner.run(...)`**

Replace:

```python
_list_of_dicts(_object_to_dict(command.payload).get("pending_super_chats")),
```

with:

```python
_pending_super_chats(self._storage_manager, command),
```

- [ ] **Step 2: Add helpers**

Add near `_list_of_dicts(...)` helpers:

```python
def _pending_super_chats(storage_manager: object, command: RuntimeCommand) -> list[dict[str, object]]:
    payload = _object_to_dict(command.payload)
    pending = _list_of_dicts(payload.get("pending_super_chats"))
    if hasattr(storage_manager, "list_v2_live_events"):
        for event in storage_manager.list_v2_live_events(command.session_id, 500):
            pending_item = _super_chat_from_event(_object_to_dict(event))
            if pending_item is not None:
                pending.append(pending_item)
    return _redact_public_value(pending)


def _super_chat_from_event(event: dict[str, object]) -> dict[str, object] | None:
    if str(event.get("event_type", "")) != "youtube_super_chat":
        return None
    metadata = _object_to_dict(event.get("public_metadata", {}))
    public_payload = _object_to_dict(metadata.get("public_payload", {}))
    super_chat = _object_to_dict(public_payload.get("super_chat", {}))
    if str(super_chat.get("acknowledgement_status", "pending")) != "pending":
        return None
    display_event = _object_to_dict(metadata.get("display_event", {}))
    return _redact_public_value(
        {
            "super_chat_id": super_chat.get("super_chat_id") or event.get("event_id"),
            "author_display_name": public_payload.get(
                "author_display_name",
                display_event.get("author_display_name", ""),
            ),
            "amount_display_string": super_chat.get("amount_display_string", ""),
            "message_text": super_chat.get(
                "public_message",
                public_payload.get("message_text", display_event.get("message_text", "")),
            ),
        }
    )
```

- [ ] **Step 3: Run green test**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_memoria_runners.py::test_closing_runner_loads_pending_super_chats_from_youtube_events -q
```

Expected: `1 passed`.

---

### Task 3: Focused Regression

**Files:**
- Verify: `YouTubeBridgeV2/runtime/memoria_runners.py`
- Verify: `tests/youtubebridge_v2/test_runtime_memoria_runners.py`
- Verify: `tests/youtubebridge_v2/test_closing.py`
- Verify: `tests/youtubebridge_v2/test_memoria_adapter.py`

- [ ] **Step 1: Run focused suites**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_memoria_runners.py -q
python -m pytest tests\youtubebridge_v2\test_closing.py -q
python -m pytest tests\youtubebridge_v2\test_memoria_adapter.py -q
```

Expected: all pass.

- [ ] **Step 2: Boundary check**

Run:

```powershell
rg -n "^\s*(from|import)\s+(sqlite3|aiosqlite|YouTubeBridge(\.|\s|$)|googleapiclient|requests)" YouTubeBridgeV2\runtime\memoria_runners.py tests\youtubebridge_v2\test_runtime_memoria_runners.py
```

Expected: no matches.

---

### Task 4: Docs Sync

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/closing.md`
- Modify: `YouTubeBridgeV2/docs/modules/youtube-adapter.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`

- [ ] **Step 1: Closing docs**

Add:

```markdown
Wave 3C status:
- `MemoriaClosingRunner` 會從 V2 live event history 讀取 `youtube_super_chat` normalized event，轉成 `ClosingSuperChatAction`。
- closing handoff 只使用 sanitized public metadata；raw YouTube payload 不進 Memoria closing context。
```

- [ ] **Step 2: YouTube adapter docs**

Add under Runtime Handoff:

```markdown
Wave 3C Super Chat handoff:
- normalized `youtube_super_chat` event 的 `public_payload.super_chat` 是 closing acknowledgement 的資料來源。
- Adapter 不決定 acknowledgement wording，也不呼叫 YouTube 回覆；closing runner 只建立 handoff intent。
```

- [ ] **Step 3: API/architecture docs**

In architecture index, add:

```markdown
## Integration Wave 3C 狀態

- [x] Super Chat metadata handoff：closing runner 可從 `youtube_super_chat` live event 讀取 pending Super Chat public metadata。
- [x] Closing acknowledgement intent：pending Super Chat 會進入 `ClosingRequest.super_chat_actions` 與 Memoria closing external context。
- [x] Scope boundary：本階段不實際回覆 YouTube、不更新 acknowledgement durable status、不建立 scheduler/API ingestion path。
```

In API reference, add a short Wave 3C note under Closing or YouTube Adapter.

- [ ] **Step 4: Docs sanity check**

Run:

```powershell
rg -n "Integration Wave 3C|youtube_super_chat|Super Chat metadata handoff|ClosingSuperChatAction" YouTubeBridgeV2\docs
```

Expected: finds docs entries.

---

### Task 5: Final Verification For 3C

**Files:**
- Verify: source/tests/docs touched by 3C.

- [ ] **Step 1: Run full V2 suite**

Run:

```powershell
python -m pytest tests\youtubebridge_v2 -q
git diff --check
```

Expected: full V2 suite passes and no whitespace errors.

- [ ] **Step 2: Request code review**

Use `superpowers:requesting-code-review` with scope limited to roadmap `3C`. Review focus:

- Super Chat event parsing uses only sanitized public metadata.
- closing runner does not cross into true YouTube API operations.
- malformed/acknowledged items do not block closing.
- no raw payload/secret leaks into Memoria request body or finalization summary.

- [ ] **Step 3: Commit**

After review findings are fixed and verification is fresh:

```powershell
git add YouTubeBridgeV2\runtime\memoria_runners.py tests\youtubebridge_v2\test_runtime_memoria_runners.py YouTubeBridgeV2\docs\modules\closing.md YouTubeBridgeV2\docs\modules\youtube-adapter.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\implementation-plans\youtube-super-chat-closing-handoff.md
git diff --cached --check
git commit -m "feat: hand off Super Chats to closing"
```

---

## Self-Review

Spec coverage:

- Super Chat metadata is already normalized by adapter and saved as public event metadata; 3C consumes that public metadata.
- Closing handoff is covered by `ClosingRequest.super_chat_actions` and Memoria closing external context.
- Raw YouTube payload and real acknowledgement side effects stay out of scope.

Placeholder scan:

- No placeholders remain.

Type consistency:

- `_pending_super_chats(...)` returns the list shape already consumed by `build_closing_request(...)`.
- `super_chat_id`, `author_display_name`, `amount_display_string`, and `message_text` match `ClosingSuperChatAction` input keys.

## Execution Handoff

Plan complete and saved to `YouTubeBridgeV2/docs/implementation-plans/youtube-super-chat-closing-handoff.md`. Two execution options:

**1. Subagent-Driven (recommended)** - dispatch a fresh worker for 3C implementation and review.

**2. Inline Execution** - execute tasks in this session using `superpowers:executing-plans`, with checkpoints for review.
