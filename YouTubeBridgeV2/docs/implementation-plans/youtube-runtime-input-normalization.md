# YouTube Runtime Input Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 roadmap `3A`：把 YouTube live chat raw event 透過既有 YouTube adapter normalization 接到 Runtime Application Service 的 `HANDLE_YOUTUBE_EVENT` input，並保存 display/public-safe runtime event。

**Architecture:** `YouTubeBridgeV2/adapters/youtube.py` 仍擁有 raw YouTube event normalization；`RuntimeApplicationService.handle_youtube_event(...)` 只把 command payload 正規化成 runtime input、保存 normalized public event，然後交回既有 phase/tick dispatch。`RuntimeStoragePort.persist_youtube_event(...)` 負責保存 normalized event id/type/public metadata，不保存 raw YouTube payload。

**Tech Stack:** Python 3.13、pytest、dataclass runtime contracts、既有 `NormalizedYouTubeEvent` / `normalize_youtube_event(...)`、StorageManager-like V2 event repository。

---

## Scope

Roadmap item：`3A：live chat event normalization 對接 runtime input`

完成條件：

- `RuntimeApplicationService.handle_youtube_event(...)` 接受 command payload 中的 raw YouTube live chat event。
- raw event 會先經過 `normalize_youtube_event(...)`，而不是直接寫入 storage。
- storage 保存 normalized `event_id`、`event_type`、`public_payload`、`display_event` 與 `should_dispatch`。
- public/runtime event 不包含 `raw_payload`、`raw_youtube_payload`、token、authorization、secret、hidden prompt。
- `HANDLE_YOUTUBE_EVENT` command idempotency 不會重複保存同一 command 的 event。

不包含：

- YouTube API polling client。
- polling cursor persistence / restart recovery。
- duplicate event id policy beyond command idempotency。
- Super Chat closing handoff。
- `/v2` HTTP ingestion route、scheduler 或 background polling loop。
- 真 YouTube integration test。

## File Structure

- Modify: `YouTubeBridgeV2/runtime/application_service.py`
  - `handle_youtube_event(...)` 新增 command-level idempotency check。
  - 新增 private helper `_youtube_runtime_event_payload(...)`，把 raw event 轉成 storage-safe dict。
  - 新增 private helper `_youtube_contract_error(...)`，處理 payload 不是 mapping 的 contract error。
- Modify: `YouTubeBridgeV2/storage/runtime_store.py`
  - `persist_youtube_event(...)` 改為尊重 normalized payload 的 `event_id` / `event_type` / `public_payload` / `display_event`。
- Modify: `tests/youtubebridge_v2/test_runtime_application_service.py`
  - 增加 runtime input normalization、redaction、command idempotency tests。
- Modify: `tests/youtubebridge_v2/test_storage.py`
  - 增加 `RuntimeStoragePort.persist_youtube_event(...)` normalized event persistence test。
- Modify: `YouTubeBridgeV2/docs/modules/runtime-application-service.md`
  - 補 `HANDLE_YOUTUBE_EVENT` input contract。
- Modify: `YouTubeBridgeV2/docs/modules/youtube-adapter.md`
  - 補 3A runtime handoff 行為與 deferred cursor/polling 邊界。
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
  - 補 runtime YouTube event input/source 說明。
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
  - 新增 Integration Wave 3A 狀態。

## Runtime Payload Contract

`RuntimeCommandType.HANDLE_YOUTUBE_EVENT` command payload 支援：

```python
{
    "youtube_event": {
        "id": "evt-1",
        "snippet": {
            "type": "textMessageEvent",
            "publishedAt": "2026-05-12T08:00:00Z",
            "displayMessage": "Hello host",
            "textMessageDetails": {"messageText": "Hello host"},
            "authorChannelId": "channel-1",
        },
        "authorDetails": {
            "displayName": "Mika",
            "channelId": "channel-1",
            "isChatModerator": True,
        },
        "raw_payload": {"access_token": "must not leak"},
    }
}
```

Saved storage payload shape:

```python
{
    "event_id": "evt-1",
    "event_type": "youtube_text_message",
    "public_payload": {
        "event_id": "evt-1",
        "event_type": "text_message",
        "author_channel_id": "channel-1",
        "author_display_name": "Mika",
        "message_text": "Hello host",
        "published_at": "2026-05-12T08:00:00Z",
        "author_badges": ["moderator"],
        "duplicate": False,
        "should_dispatch": True,
    },
    "display_event": {
        "event_id": "evt-1",
        "event_type": "audience_message",
        "author_display_name": "Mika",
        "message_text": "Hello host",
        "published_at": "2026-05-12T08:00:00Z",
        "author_badges": ["moderator"],
        "duplicate": False,
        "should_dispatch": True,
    },
    "should_dispatch": True,
}
```

---

### Task 1: Runtime Service Red Tests

**Files:**
- Modify: `tests/youtubebridge_v2/test_runtime_application_service.py`

- [ ] **Step 1: Extend test imports**

Add `asdict` import and keep existing `replace` import:

```python
from dataclasses import asdict, replace
```

- [ ] **Step 2: Extend `FakeStorage` with YouTube event persistence**

In `FakeStorage.__init__`, add:

```python
self.youtube_events = []
```

Add this method to `FakeStorage`:

```python
def persist_youtube_event(self, session_id, payload, now):
    self.calls.append(("persist_youtube_event", session_id, now))
    self.youtube_events.append(
        {
            "session_id": session_id,
            "payload": payload,
            "created_at": now,
        }
    )
```

- [ ] **Step 3: Add raw YouTube fixture and private payload assertion**

Add near existing helpers:

```python
def _raw_youtube_text_event(**overrides):
    event = {
        "id": "yt-evt-1",
        "snippet": {
            "type": "textMessageEvent",
            "publishedAt": "2026-05-12T08:10:00Z",
            "displayMessage": "Hello runtime",
            "textMessageDetails": {"messageText": "Hello runtime"},
            "authorChannelId": "channel-1",
            "rawTopicPack": {"hidden_prompt": "must not leak"},
        },
        "authorDetails": {
            "displayName": "Mika",
            "channelId": "channel-1",
            "isChatOwner": False,
            "isChatModerator": True,
            "isChatSponsor": False,
        },
        "raw_payload": {"access_token": "secret-value"},
    }
    event.update(overrides)
    return event
```

Extend `_assert_no_forbidden_payload(...)` to include YouTube private strings:

```python
for forbidden in (
    "hidden_prompt",
    "raw_payload",
    "topic_pack",
    "rawtopicpack",
    "factcard",
    "fact_card",
    "memoriacore_raw",
    "youtube_raw",
    "raw_youtube_payload",
    "access_token",
    "authorization",
    "secret-value",
    "must not leak",
):
    assert forbidden not in text
```

- [ ] **Step 4: Add failing runtime normalization test**

Append after `test_tick_reads_snapshot_before_advancing_phase`:

```python
def test_handle_youtube_event_normalizes_raw_event_before_storage_and_tick():
    storage = FakeStorage(snapshot=_snapshot())
    phase = FakePhaseAdvancer(_transition("run_planned_show"))
    runner = FakeRunner()
    service = _service(
        storage=storage,
        phase_advancer=phase,
        planned_show_runner=runner,
    )
    command = _command(
        RuntimeCommandType.HANDLE_YOUTUBE_EVENT,
        command_id="cmd-youtube-1",
        payload={"youtube_event": _raw_youtube_text_event()},
    )

    result = service.handle_youtube_event(command, BASE_NOW)

    assert result.status == "ok"
    assert len(storage.youtube_events) == 1
    stored_payload = storage.youtube_events[0]["payload"]
    assert stored_payload["event_id"] == "yt-evt-1"
    assert stored_payload["event_type"] == "youtube_text_message"
    assert stored_payload["public_payload"]["message_text"] == "Hello runtime"
    assert stored_payload["public_payload"]["author_badges"] == ["moderator"]
    assert stored_payload["display_event"] == {
        "event_id": "yt-evt-1",
        "event_type": "audience_message",
        "author_display_name": "Mika",
        "message_text": "Hello runtime",
        "published_at": "2026-05-12T08:10:00Z",
        "author_badges": ["moderator"],
        "duplicate": False,
        "should_dispatch": True,
    }
    assert stored_payload["should_dispatch"] is True
    assert len(runner.calls) == 1
    _assert_no_forbidden_payload(stored_payload)
    _assert_no_forbidden_payload(result.events)
```

- [ ] **Step 5: Add failing command idempotency test**

Append:

```python
def test_handle_youtube_event_duplicate_command_does_not_persist_twice():
    storage = FakeStorage(snapshot=_snapshot())
    phase = FakePhaseAdvancer(_transition("run_planned_show"))
    runner = FakeRunner()
    service = _service(
        storage=storage,
        phase_advancer=phase,
        planned_show_runner=runner,
    )
    command = _command(
        RuntimeCommandType.HANDLE_YOUTUBE_EVENT,
        command_id="cmd-youtube-idempotent",
        payload={"youtube_event": _raw_youtube_text_event()},
    )

    first = service.handle_youtube_event(command, BASE_NOW)
    second = service.handle_youtube_event(command, BASE_NOW)

    assert second == first
    assert len(storage.youtube_events) == 1
    assert len(runner.calls) == 1
```

- [ ] **Step 6: Run red runtime tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_application_service.py::test_handle_youtube_event_normalizes_raw_event_before_storage_and_tick tests\youtubebridge_v2\test_runtime_application_service.py::test_handle_youtube_event_duplicate_command_does_not_persist_twice -q
```

Expected: FAIL because `handle_youtube_event(...)` currently writes `command.payload` directly and does not check existing command result before persistence.

---

### Task 2: Storage Port Red Test

**Files:**
- Modify: `tests/youtubebridge_v2/test_storage.py`

- [ ] **Step 1: Import RuntimeStoragePort**

Add:

```python
from YouTubeBridgeV2.storage.runtime_store import RuntimeStoragePort
```

- [ ] **Step 2: Add normalized YouTube persistence test**

Append after `test_append_live_event_persists_normalized_event`:

```python
def test_runtime_storage_port_persists_normalized_youtube_event_shape():
    storage = FakeStorageManager()
    port = RuntimeStoragePort(storage)

    port.persist_youtube_event(
        "session-1",
        {
            "event_id": "yt-evt-1",
            "event_type": "youtube_text_message",
            "public_payload": {
                "event_id": "yt-evt-1",
                "message_text": "Hello runtime",
                "raw_payload": {"youtube_raw": "must not leak"},
            },
            "display_event": {
                "event_id": "yt-evt-1",
                "event_type": "audience_message",
                "message_text": "Hello runtime",
            },
            "should_dispatch": True,
            "raw_youtube_payload": {"access_token": "secret-value"},
        },
        NOW,
    )

    stored = storage.events[0]
    assert stored["event_id"] == "yt-evt-1"
    assert stored["event_type"] == "youtube_text_message"
    assert stored["public_metadata"] == {
        "public_payload": {
            "event_id": "yt-evt-1",
            "message_text": "Hello runtime",
        },
        "display_event": {
            "event_id": "yt-evt-1",
            "event_type": "audience_message",
            "message_text": "Hello runtime",
        },
        "should_dispatch": True,
    }
    assert stored["created_at"] == NOW
    _assert_no_private_payload(stored)
```

- [ ] **Step 3: Run red storage test**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_storage.py::test_runtime_storage_port_persists_normalized_youtube_event_shape -q
```

Expected: FAIL because `RuntimeStoragePort.persist_youtube_event(...)` currently generates its own event id and stores the whole payload under `public_metadata`.

---

### Task 3: Green Runtime Implementation

**Files:**
- Modify: `YouTubeBridgeV2/runtime/application_service.py`
- Test: `tests/youtubebridge_v2/test_runtime_application_service.py`

- [ ] **Step 1: Add imports**

Change imports near the top:

```python
from typing import Any, Callable, Mapping

from YouTubeBridgeV2.adapters.youtube import (
    NormalizedYouTubeEvent,
    normalize_youtube_event,
)
```

- [ ] **Step 2: Replace `handle_youtube_event(...)`**

Replace the method with:

```python
def handle_youtube_event(
    self,
    command: RuntimeCommand,
    now: datetime,
) -> RuntimeServiceResult:
    existing = self._existing_result(command)
    if existing is not None:
        return existing

    try:
        youtube_payload = _youtube_runtime_event_payload(command.payload)
    except ValueError as exc:
        result = _youtube_contract_error(command, str(exc))
        self._save_result(command, result)
        return result

    if hasattr(self._storage, "persist_youtube_event"):
        self._storage.persist_youtube_event(command.session_id, youtube_payload, now)
    snapshot = self._storage.read_snapshot(command.session_id)
    return self._advance_and_dispatch(command, now, snapshot)
```

- [ ] **Step 3: Add YouTube payload helpers**

Add below `_errors_for_adapter_result(...)`:

```python
def _youtube_runtime_event_payload(payload: dict[str, object]) -> dict[str, object]:
    raw_event = payload.get("youtube_event", payload.get("raw_event", payload))
    if isinstance(raw_event, NormalizedYouTubeEvent):
        normalized = raw_event
    elif isinstance(raw_event, Mapping):
        normalized = normalize_youtube_event(raw_event)
    else:
        raise ValueError("youtube_event must be a mapping")

    event_type = normalized.event_type
    return _sanitize_public_payload(
        {
            "event_id": normalized.event_id,
            "event_type": f"youtube_{event_type}",
            "public_payload": normalized.public_payload,
            "display_event": normalized.display_event,
            "should_dispatch": normalized.should_dispatch,
        }
    )
```

Add below `_youtube_runtime_event_payload(...)`:

```python
def _youtube_contract_error(command: RuntimeCommand, message: str) -> RuntimeServiceResult:
    return RuntimeServiceResult(
        status="contract_error",
        session_id=command.session_id,
        phase=None,
        events=[],
        errors=[
            {
                "code": "invalid_youtube_event_payload",
                "message": message,
            }
        ],
        correlation_id=_correlation_id(command),
    )
```

- [ ] **Step 4: Run green runtime tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_application_service.py::test_handle_youtube_event_normalizes_raw_event_before_storage_and_tick tests\youtubebridge_v2\test_runtime_application_service.py::test_handle_youtube_event_duplicate_command_does_not_persist_twice -q
```

Expected: `2 passed`.

---

### Task 4: Green Storage Implementation

**Files:**
- Modify: `YouTubeBridgeV2/storage/runtime_store.py`
- Test: `tests/youtubebridge_v2/test_storage.py`

- [ ] **Step 1: Replace `persist_youtube_event(...)`**

Replace the method body with:

```python
def persist_youtube_event(
    self,
    session_id: str,
    payload: dict[str, object],
    now: datetime,
) -> None:
    """保存 normalized YouTube event public summary。"""

    safe_payload = _sanitize_public_payload(_object_to_dict(payload))
    public_metadata = {
        "public_payload": safe_payload.get("public_payload", {}),
        "display_event": safe_payload.get("display_event", {}),
        "should_dispatch": bool(safe_payload.get("should_dispatch", True)),
    }
    self._events.append_live_event(
        session_id,
        {
            "event_id": str(
                safe_payload.get("event_id") or f"{session_id}:youtube:{now.isoformat()}"
            ),
            "event_type": str(safe_payload.get("event_type") or "youtube_event"),
            "public_metadata": _sanitize_public_payload(public_metadata),
            "created_at": now,
        },
    )
```

- [ ] **Step 2: Run green storage test**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_storage.py::test_runtime_storage_port_persists_normalized_youtube_event_shape -q
```

Expected: `1 passed`.

---

### Task 5: Focused Regression And Boundary Tests

**Files:**
- Verify: `YouTubeBridgeV2/runtime/application_service.py`
- Verify: `YouTubeBridgeV2/storage/runtime_store.py`
- Verify: `tests/youtubebridge_v2/test_runtime_application_service.py`
- Verify: `tests/youtubebridge_v2/test_storage.py`
- Verify: `tests/youtubebridge_v2/test_youtube_adapter.py`

- [ ] **Step 1: Run focused runtime/storage/youtube tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_application_service.py -q
python -m pytest tests\youtubebridge_v2\test_storage.py -q
python -m pytest tests\youtubebridge_v2\test_youtube_adapter.py -q
```

Expected:

- Runtime Application Service suite passes.
- Storage suite passes.
- YouTube adapter suite still passes; 3A must not move transport or polling logic into runtime.

- [ ] **Step 2: Check forbidden boundary imports**

Run:

```powershell
rg -n "sqlite3|aiosqlite|\bfrom YouTubeBridge(\.|\s|$)|\bimport YouTubeBridge(\.|\s|$)|googleapiclient|youtube_transcript|requests" YouTubeBridgeV2\runtime\application_service.py YouTubeBridgeV2\storage\runtime_store.py YouTubeBridgeV2\adapters\youtube.py
```

Expected: no matches.

---

### Task 6: Docs Sync

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/runtime-application-service.md`
- Modify: `YouTubeBridgeV2/docs/modules/youtube-adapter.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`

- [ ] **Step 1: Update Runtime Application Service module doc**

In `docs/modules/runtime-application-service.md`, under Wave status or `handle_youtube_event(...)`, add:

```markdown
Wave 3A status:
- `RuntimeCommandType.HANDLE_YOUTUBE_EVENT` 接受 raw YouTube event payload，會先經 `normalize_youtube_event(...)` 轉為 normalized public/runtime input，再保存事件並交回 tick dispatch。
- command idempotency 會在保存 YouTube event 前檢查，避免同一 `command_id` 重送時重複寫 event。
- 本階段不處理 polling cursor、YouTube API transport、scheduler 或 Super Chat closing handoff。
```

- [ ] **Step 2: Update YouTube Adapter module doc**

In `docs/modules/youtube-adapter.md`, under Public Entrypoints or Polling Rules, add:

```markdown
Wave 3A runtime handoff:
- Runtime 只呼叫 `normalize_youtube_event(...)` 取得 display-safe payload，不直接接收 raw YouTube payload 到 public event。
- `HANDLE_YOUTUBE_EVENT` 只處理單一 live chat event normalization + runtime input handoff；polling cursor / duplicate event recovery / scheduler ingestion 保留給 3B/3D。
```

- [ ] **Step 3: Update API reference index**

In `docs/api-reference-index.md`, under Runtime Application Service concepts, ensure the existing list still includes:

```markdown
- `RuntimeCommandType.HANDLE_YOUTUBE_EVENT`
```

If the list only references `RuntimeCommandType`, add a short note under Runtime Application Service Purpose:

```markdown
Wave 3A：`HANDLE_YOUTUBE_EVENT` command payload 會先經 YouTube adapter normalization，保存 normalized public/display event 後再交回 tick dispatch。
```

- [ ] **Step 4: Update architecture index**

In `docs/architecture-index.md`, add after the 2E-D status block:

```markdown
## Integration Wave 3A 狀態

- [x] Live chat event normalization handoff：`RuntimeApplicationService.handle_youtube_event(...)` 會把 raw YouTube live chat event 經 `normalize_youtube_event(...)` 轉成 storage-safe payload。
- [x] Runtime input persistence：`RuntimeStoragePort.persist_youtube_event(...)` 保存 normalized event id/type/public payload/display event，不保存 raw YouTube payload。
- [x] Scope boundary：polling cursor、YouTube API transport、scheduler ingestion 與 Super Chat closing handoff 保留給後續 3B/3C/3D。
```

- [ ] **Step 5: Docs sanity check**

Run:

```powershell
rg -n "Integration Wave 3A|HANDLE_YOUTUBE_EVENT|normalize_youtube_event|Live chat event normalization handoff" YouTubeBridgeV2\docs
```

Expected: finds runtime module, YouTube adapter module, API reference, and architecture index entries.

---

### Task 7: Final Verification For 3A

**Files:**
- Verify: `YouTubeBridgeV2/runtime/application_service.py`
- Verify: `YouTubeBridgeV2/storage/runtime_store.py`
- Verify: V2 docs touched in Task 6

- [ ] **Step 1: Run Wave 3 focused commands**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_youtube_adapter.py -q
python -m pytest tests\youtubebridge_v2\test_runtime_application_service.py -q
python -m pytest tests\youtubebridge_v2\test_storage.py -q
```

Expected: all focused suites pass.

- [ ] **Step 2: Run full V2 suite**

Run:

```powershell
python -m pytest tests\youtubebridge_v2 -q
```

Expected: full V2 suite passes; real Memoria integration remains skipped by default.

- [ ] **Step 3: Run whitespace and scope checks**

Run:

```powershell
git diff --check
git status -sb
git diff --stat
rg -n "sqlite3|aiosqlite|\bfrom YouTubeBridge(\.|\s|$)|\bimport YouTubeBridge(\.|\s|$)|googleapiclient|requests" YouTubeBridgeV2\runtime\application_service.py YouTubeBridgeV2\storage\runtime_store.py YouTubeBridgeV2\adapters\youtube.py tests\youtubebridge_v2\test_runtime_application_service.py tests\youtubebridge_v2\test_storage.py
```

Expected:

- no whitespace errors.
- changed files limited to 3A source/tests/docs/plan.
- no direct SQLite, Legacy `YouTubeBridge`, or real YouTube transport imports in 3A scope.

- [ ] **Step 4: Request code review**

Use `superpowers:requesting-code-review` with scope limited to roadmap `3A`. Review focus:

- raw YouTube payload never enters public/runtime event.
- command idempotency check happens before `persist_youtube_event`.
- 3A does not implement polling cursor, scheduler, transport, or Super Chat closing handoff.
- runtime does not gain direct YouTube API transport dependency.

- [ ] **Step 5: Commit**

After review findings are fixed and verification is fresh:

```powershell
git add YouTubeBridgeV2\runtime\application_service.py YouTubeBridgeV2\storage\runtime_store.py tests\youtubebridge_v2\test_runtime_application_service.py tests\youtubebridge_v2\test_storage.py YouTubeBridgeV2\docs\modules\runtime-application-service.md YouTubeBridgeV2\docs\modules\youtube-adapter.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\implementation-plans\youtube-runtime-input-normalization.md
git diff --cached --check
git commit -m "feat: normalize YouTube runtime input"
```

---

## Self-Review

Spec coverage:

- `live chat event normalization` covered by `normalize_youtube_event(...)` inside `handle_youtube_event(...)`.
- `對接 runtime input` covered by `RuntimeCommandType.HANDLE_YOUTUBE_EVENT` command payload normalization and `RuntimeStoragePort.persist_youtube_event(...)`.
- `不讓 raw YouTube payload 進入 public API/SSE` covered by runtime/storage redaction assertions and sanitized storage shape.
- `不要跨 wave` covered by excluding polling client, cursor persistence, Super Chat closing handoff, scheduler and HTTP ingestion route.

Placeholder scan:

- No `TBD`, `TODO`, `implement later`, or placeholder-only test instructions remain.
- Every code-changing task includes exact paths, code snippets, commands, and expected results.

Type consistency:

- `RuntimeCommand.payload` remains `dict[str, object]`.
- `_youtube_runtime_event_payload(...)` returns the dict shape consumed by `RuntimeStoragePort.persist_youtube_event(...)`.
- `event_type` uses storage event type `youtube_<normalized.event_type>` while nested adapter `public_payload["event_type"]` preserves normalized adapter type like `text_message` or `super_chat`.

## Execution Handoff

Plan complete and saved to `YouTubeBridgeV2/docs/implementation-plans/youtube-runtime-input-normalization.md`. Two execution options:

**1. Subagent-Driven (recommended)** - dispatch a fresh worker for 3A implementation and review.

**2. Inline Execution** - execute tasks in this session using `superpowers:executing-plans`, with checkpoints for review.
