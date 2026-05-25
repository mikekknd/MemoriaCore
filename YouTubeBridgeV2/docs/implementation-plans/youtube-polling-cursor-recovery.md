# YouTube Polling Cursor Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 roadmap `3B`：保存 YouTube polling cursor 到 V2 session metadata，讓 restart 後可讀回 cursor、延續 seen event id 去重，避免同一 live chat event 重複 dispatch。

**Architecture:** `YouTubePollingCursor` 仍由 `YouTubeBridgeV2/adapters/youtube.py` 定義；`RuntimeStoragePort` 只負責把 cursor 轉成 sanitized session metadata 與讀回 dataclass。`RuntimeApplicationService.handle_youtube_event(...)` 在 3A normalization 之前載入 cursor、在事件保存後 advance/persist cursor，若 cursor 判定 duplicate event 則只保存/回報 ignored event，不 dispatch planned/aftertalk/closing runner。

**Tech Stack:** Python 3.13、pytest、`YouTubePollingCursor` dataclass、StorageManager session metadata JSON、既有 Runtime Application Service command idempotency。

---

## Scope

Roadmap item：`3B：polling cursor/storage/restart recovery`

完成條件：

- runtime 可從 command payload 或 storage metadata 取得 `YouTubePollingCursor`。
- runtime normalization 會把 cursor 傳給 `normalize_youtube_event(...)`，讓 seen event id 可標記 duplicate。
- 每次處理 YouTube event 後，cursor 會保存到 session metadata 的 `youtube_polling_cursor`。
- cursor 保存內容包含 `live_chat_id`、`next_page_token`、`polling_interval_millis`、`seen_event_ids`。
- StorageManager 重新建立後，`RuntimeStoragePort.load_youtube_polling_cursor(session_id)` 可讀回同一 cursor。
- duplicate event id 不會再次 dispatch runner，但會保存 display-safe ignored event summary。

不包含：

- 真 YouTube API polling transport/client。
- scheduler/background loop。
- `/v2` ingestion route。
- Super Chat closing handoff。
- long-term pruning policy beyond a small bounded cursor record。

## File Structure

- Modify: `YouTubeBridgeV2/runtime/application_service.py`
  - `_youtube_runtime_event_payload(...)` 接受 optional cursor，回傳 normalized payload 與 advanced cursor。
  - `handle_youtube_event(...)` 從 payload/storage 讀 cursor、保存 advanced cursor、duplicate 時不 dispatch runner。
- Modify: `YouTubeBridgeV2/storage/runtime_store.py`
  - 新增 `save_youtube_polling_cursor(session_id, cursor, now)`。
  - 新增 `load_youtube_polling_cursor(session_id)`。
- Modify: `tests/youtubebridge_v2/test_runtime_application_service.py`
  - 測 runtime 使用 payload/storage cursor、advance cursor、duplicate skip。
- Modify: `tests/youtubebridge_v2/test_storage.py`
  - 測 `RuntimeStoragePort` 用 fake backend 保存/讀回 cursor。
- Modify: `tests/youtubebridge_v2/test_storage_manager_durable_backend.py`
  - 測 real StorageManager restart 後 cursor metadata 不遺失。
- Modify docs:
  - `YouTubeBridgeV2/docs/modules/runtime-application-service.md`
  - `YouTubeBridgeV2/docs/modules/storage.md`
  - `YouTubeBridgeV2/docs/modules/youtube-adapter.md`
  - `YouTubeBridgeV2/docs/api-reference-index.md`
  - `YouTubeBridgeV2/docs/architecture-index.md`

## Cursor Metadata Contract

Session metadata key:

```python
"youtube_polling_cursor"
```

Stored shape:

```python
{
    "live_chat_id": "live-chat-1",
    "next_page_token": "page-2",
    "polling_interval_millis": 2500,
    "seen_event_ids": ["yt-evt-1", "yt-evt-2"],
    "updated_at": "2026-05-12T08:15:00+00:00",
}
```

Command payload may include:

```python
{
    "youtube_event": {"id": "yt-evt-2", "snippet": {...}, "authorDetails": {...}},
    "polling_cursor": {
        "live_chat_id": "live-chat-1",
        "next_page_token": "page-1",
        "polling_interval_millis": 1500,
        "seen_event_ids": ["yt-evt-1"],
    },
    "page_info": {
        "next_page_token": "page-2",
        "polling_interval_millis": 2500,
    },
}
```

If payload cursor is missing, runtime should try `storage.load_youtube_polling_cursor(session_id)`. Payload cursor wins over storage cursor for testability and manual replay.

---

### Task 1: Runtime Cursor Red Tests

**Files:**
- Modify: `tests/youtubebridge_v2/test_runtime_application_service.py`

- [ ] **Step 1: Import cursor dataclass**

Add:

```python
from YouTubeBridgeV2.adapters.youtube import YouTubePollingCursor
```

- [ ] **Step 2: Extend FakeStorage cursor state**

In `FakeStorage.__init__`, add:

```python
self.youtube_polling_cursor = None
```

Add these methods:

```python
def load_youtube_polling_cursor(self, session_id):
    self.calls.append(("load_youtube_polling_cursor", session_id))
    return self.youtube_polling_cursor

def save_youtube_polling_cursor(self, session_id, cursor, now):
    self.calls.append(("save_youtube_polling_cursor", session_id, now))
    self.youtube_polling_cursor = cursor
```

- [ ] **Step 3: Add cursor advance red test**

Append after 3A YouTube event tests:

```python
def test_handle_youtube_event_advances_and_persists_polling_cursor_from_payload():
    storage = FakeStorage(snapshot=_snapshot())
    runner = FakeRunner()
    service = _service(
        storage=storage,
        phase_advancer=FakePhaseAdvancer(_transition("run_planned_show")),
        planned_show_runner=runner,
    )
    command = _command(
        RuntimeCommandType.HANDLE_YOUTUBE_EVENT,
        command_id="cmd-youtube-cursor",
        payload={
            "youtube_event": _raw_youtube_text_event(id="yt-evt-2"),
            "polling_cursor": {
                "live_chat_id": "live-chat-1",
                "next_page_token": "page-1",
                "polling_interval_millis": 1500,
                "seen_event_ids": ["yt-evt-1"],
            },
            "page_info": {
                "next_page_token": "page-2",
                "polling_interval_millis": 2500,
            },
        },
    )

    result = service.handle_youtube_event(command, BASE_NOW)

    assert result.status == "ok"
    assert len(runner.calls) == 1
    assert isinstance(storage.youtube_polling_cursor, YouTubePollingCursor)
    assert storage.youtube_polling_cursor.live_chat_id == "live-chat-1"
    assert storage.youtube_polling_cursor.next_page_token == "page-2"
    assert storage.youtube_polling_cursor.polling_interval_millis == 2500
    assert storage.youtube_polling_cursor.seen_event_ids == ("yt-evt-1", "yt-evt-2")
```

- [ ] **Step 4: Add restart duplicate skip red test**

Append:

```python
def test_handle_youtube_event_uses_stored_cursor_to_skip_duplicate_after_restart():
    storage = FakeStorage(snapshot=_snapshot())
    storage.youtube_polling_cursor = YouTubePollingCursor(
        live_chat_id="live-chat-1",
        next_page_token="page-2",
        polling_interval_millis=2500,
        seen_event_ids=("yt-evt-1",),
    )
    runner = FakeRunner()
    service = _service(
        storage=storage,
        phase_advancer=FakePhaseAdvancer(_transition("run_planned_show")),
        planned_show_runner=runner,
    )
    command = _command(
        RuntimeCommandType.HANDLE_YOUTUBE_EVENT,
        command_id="cmd-youtube-duplicate",
        payload={"youtube_event": _raw_youtube_text_event()},
    )

    result = service.handle_youtube_event(command, BASE_NOW)

    assert result.status == "ok"
    assert not runner.calls
    assert len(storage.youtube_events) == 1
    stored_payload = storage.youtube_events[0]["payload"]
    assert stored_payload["duplicate"] is True
    assert stored_payload["should_dispatch"] is False
    assert result.events[0].event_type == "youtube_event_ignored"
    assert result.adapter_result.summary == {
        "youtube_event": "duplicate",
        "event_id": "yt-evt-1",
    }
```

- [ ] **Step 5: Run red runtime tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_application_service.py::test_handle_youtube_event_advances_and_persists_polling_cursor_from_payload tests\youtubebridge_v2\test_runtime_application_service.py::test_handle_youtube_event_uses_stored_cursor_to_skip_duplicate_after_restart -q
```

Expected: FAIL because runtime does not load/save cursor and always dispatches after YouTube event persistence.

---

### Task 2: Storage Cursor Red Tests

**Files:**
- Modify: `tests/youtubebridge_v2/test_storage.py`
- Modify: `tests/youtubebridge_v2/test_storage_manager_durable_backend.py`

- [ ] **Step 1: Import cursor dataclass in storage tests**

In both files, add:

```python
from YouTubeBridgeV2.adapters.youtube import YouTubePollingCursor
from YouTubeBridgeV2.storage.runtime_store import RuntimeStoragePort
```

`test_storage.py` already imports `RuntimeStoragePort` from 3A; add only missing imports there.

- [ ] **Step 2: Add fake-backend cursor persistence test**

Append in `test_storage.py` after the normalized YouTube event storage test:

```python
def test_runtime_storage_port_saves_and_loads_youtube_polling_cursor():
    storage = FakeStorageManager()
    storage.create_v2_session(_session_record())
    port = RuntimeStoragePort(storage)

    port.save_youtube_polling_cursor(
        "session-1",
        YouTubePollingCursor(
            live_chat_id="live-chat-1",
            next_page_token="page-2",
            polling_interval_millis=2500,
            seen_event_ids=("yt-evt-1", "yt-evt-2"),
        ),
        NOW,
    )

    loaded = port.load_youtube_polling_cursor("session-1")

    assert loaded == YouTubePollingCursor(
        live_chat_id="live-chat-1",
        next_page_token="page-2",
        polling_interval_millis=2500,
        seen_event_ids=("yt-evt-1", "yt-evt-2"),
    )
    stored = storage.sessions["session-1"]["metadata"]["youtube_polling_cursor"]
    assert stored["seen_event_ids"] == ["yt-evt-1", "yt-evt-2"]
    assert stored["updated_at"] == NOW.isoformat()
    text = repr(stored).lower()
    assert "access_token" not in text
    assert "authorization" not in text
    assert "secret" not in text
    assert "must not leak" not in text
```

- [ ] **Step 3: Add real StorageManager restart recovery test**

Append in `test_storage_manager_durable_backend.py`:

```python
def test_youtube_polling_cursor_survives_storage_manager_restart(tmp_path):
    storage = _storage(tmp_path)
    storage.create_v2_session(_session_record())
    RuntimeStoragePort(storage).save_youtube_polling_cursor(
        "session-1",
        YouTubePollingCursor(
            live_chat_id="live-chat-1",
            next_page_token="page-2",
            polling_interval_millis=2500,
            seen_event_ids=("yt-evt-1", "yt-evt-2"),
        ),
        NOW,
    )

    restarted = _storage(tmp_path)
    loaded = RuntimeStoragePort(restarted).load_youtube_polling_cursor("session-1")

    assert loaded == YouTubePollingCursor(
        live_chat_id="live-chat-1",
        next_page_token="page-2",
        polling_interval_millis=2500,
        seen_event_ids=("yt-evt-1", "yt-evt-2"),
    )
    _assert_no_private_payload(restarted.get_v2_session("session-1"))
```

- [ ] **Step 4: Run red storage tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_storage.py::test_runtime_storage_port_saves_and_loads_youtube_polling_cursor tests\youtubebridge_v2\test_storage_manager_durable_backend.py::test_youtube_polling_cursor_survives_storage_manager_restart -q
```

Expected: FAIL because `RuntimeStoragePort` has no cursor load/save methods yet.

---

### Task 3: Green Storage Cursor Implementation

**Files:**
- Modify: `YouTubeBridgeV2/storage/runtime_store.py`
- Test: `tests/youtubebridge_v2/test_storage.py`
- Test: `tests/youtubebridge_v2/test_storage_manager_durable_backend.py`

- [ ] **Step 1: Import `YouTubePollingCursor`**

Add:

```python
from YouTubeBridgeV2.adapters.youtube import YouTubePollingCursor
```

- [ ] **Step 2: Add load/save methods to `RuntimeStoragePort`**

Add below `persist_youtube_event(...)`:

```python
def save_youtube_polling_cursor(
    self,
    session_id: str,
    cursor: YouTubePollingCursor | dict[str, object],
    now: datetime,
) -> None:
    """保存 YouTube polling cursor 到 session metadata。"""

    polling_cursor = _youtube_polling_cursor(cursor)
    self._update_session(
        session_id,
        {
            "youtube_polling_cursor": {
                "live_chat_id": polling_cursor.live_chat_id,
                "next_page_token": polling_cursor.next_page_token,
                "polling_interval_millis": polling_cursor.polling_interval_millis,
                "seen_event_ids": list(polling_cursor.seen_event_ids),
                "updated_at": now.isoformat(),
            }
        },
    )


def load_youtube_polling_cursor(self, session_id: str) -> YouTubePollingCursor | None:
    """從 session metadata 讀回 YouTube polling cursor。"""

    if not hasattr(self._storage_manager, "get_v2_session"):
        raise RuntimeStorageContractError("storage manager missing get_v2_session")
    record = self._storage_manager.get_v2_session(session_id)
    if record is None:
        raise KeyError(session_id)
    metadata = _object_to_dict(record.get("metadata", {}))
    raw_cursor = metadata.get("youtube_polling_cursor")
    if raw_cursor is None:
        return None
    return _youtube_polling_cursor(raw_cursor)
```

- [ ] **Step 3: Add cursor coercion helper**

Add near `_object_to_dict(...)` helpers:

```python
def _youtube_polling_cursor(value: YouTubePollingCursor | dict[str, object]) -> YouTubePollingCursor:
    if isinstance(value, YouTubePollingCursor):
        return value
    data = _object_to_dict(value)
    return YouTubePollingCursor(
        live_chat_id=str(data.get("live_chat_id", "")),
        next_page_token=data.get("next_page_token"),
        polling_interval_millis=data.get("polling_interval_millis"),
        seen_event_ids=_list_value(data.get("seen_event_ids")),
    )
```

- [ ] **Step 4: Run green storage tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_storage.py::test_runtime_storage_port_saves_and_loads_youtube_polling_cursor tests\youtubebridge_v2\test_storage_manager_durable_backend.py::test_youtube_polling_cursor_survives_storage_manager_restart -q
```

Expected: `2 passed`.

---

### Task 4: Green Runtime Cursor Implementation

**Files:**
- Modify: `YouTubeBridgeV2/runtime/application_service.py`
- Test: `tests/youtubebridge_v2/test_runtime_application_service.py`

- [ ] **Step 1: Import cursor dataclass**

Extend the YouTube adapter import:

```python
from YouTubeBridgeV2.adapters.youtube import (
    NormalizedYouTubeEvent,
    YouTubePollingCursor,
    normalize_youtube_event,
)
```

- [ ] **Step 2: Update `handle_youtube_event(...)`**

Replace the current YouTube normalization block with:

```python
cursor = _youtube_cursor_for_command(command, self._storage)
try:
    youtube_payload, advanced_cursor = _youtube_runtime_event_payload(
        command.payload,
        cursor=cursor,
    )
except ValueError as exc:
    result = _youtube_contract_error(command, str(exc))
    self._save_result(command, result)
    return result

if hasattr(self._storage, "persist_youtube_event"):
    self._storage.persist_youtube_event(command.session_id, youtube_payload, now)
if advanced_cursor is not None and hasattr(self._storage, "save_youtube_polling_cursor"):
    self._storage.save_youtube_polling_cursor(command.session_id, advanced_cursor, now)
if youtube_payload.get("should_dispatch") is False:
    snapshot = self._storage.read_snapshot(command.session_id)
    return self._youtube_duplicate_result(command, snapshot, youtube_payload)
snapshot = self._storage.read_snapshot(command.session_id)
return self._advance_and_dispatch(command, now, snapshot)
```

- [ ] **Step 3: Add duplicate result method**

Inside `RuntimeApplicationService`, add:

```python
def _youtube_duplicate_result(
    self,
    command: RuntimeCommand,
    snapshot: LiveSessionSnapshot,
    youtube_payload: dict[str, object],
) -> RuntimeServiceResult:
    summary = {
        "youtube_event": "duplicate",
        "event_id": str(youtube_payload.get("event_id", "")),
    }
    event = self._event(
        event_type="youtube_event_ignored",
        command=command,
        phase=snapshot.current_phase,
        payload=summary,
    )
    self._persist_event(event)
    result = RuntimeServiceResult(
        status="ok",
        session_id=command.session_id,
        phase=snapshot.current_phase,
        events=[event],
        errors=[],
        correlation_id=_correlation_id(command),
        adapter_result=AdapterDispatchResult(status="ok", summary=summary),
    )
    self._save_result(command, result)
    return result
```

- [ ] **Step 4: Replace YouTube payload helper**

Replace `_youtube_runtime_event_payload(...)` with:

```python
def _youtube_runtime_event_payload(
    payload: dict[str, object],
    *,
    cursor: YouTubePollingCursor | None = None,
) -> tuple[dict[str, object], YouTubePollingCursor | None]:
    raw_event = payload.get("youtube_event", payload.get("raw_event", payload))
    if isinstance(raw_event, NormalizedYouTubeEvent):
        normalized = raw_event
    elif isinstance(raw_event, Mapping):
        normalized = normalize_youtube_event(raw_event, cursor=cursor)
    else:
        raise ValueError("youtube_event must be a mapping")

    advanced_cursor = _advance_youtube_cursor(cursor, payload, normalized.event_id)
    runtime_payload = _sanitize_public_payload(
        {
            "event_id": normalized.event_id,
            "event_type": f"youtube_{normalized.event_type}",
            "public_payload": normalized.public_payload,
            "display_event": normalized.display_event,
            "duplicate": normalized.duplicate,
            "should_dispatch": normalized.should_dispatch,
        }
    )
    return runtime_payload, advanced_cursor
```

- [ ] **Step 5: Add cursor helpers**

Add below `_youtube_runtime_event_payload(...)`:

```python
def _youtube_cursor_for_command(
    command: RuntimeCommand,
    storage: object,
) -> YouTubePollingCursor | None:
    payload_cursor = command.payload.get("polling_cursor")
    if payload_cursor is not None:
        return _youtube_polling_cursor(payload_cursor)
    if hasattr(storage, "load_youtube_polling_cursor"):
        return storage.load_youtube_polling_cursor(command.session_id)
    return None


def _advance_youtube_cursor(
    cursor: YouTubePollingCursor | None,
    payload: dict[str, object],
    event_id: str,
) -> YouTubePollingCursor | None:
    if cursor is None:
        return None
    page_info = _mapping(payload.get("page_info"))
    return cursor.advance(
        next_page_token=page_info.get("next_page_token", payload.get("next_page_token")),
        polling_interval_millis=page_info.get(
            "polling_interval_millis",
            payload.get("polling_interval_millis"),
        ),
        seen_event_ids=(event_id,),
    )


def _youtube_polling_cursor(value: object) -> YouTubePollingCursor:
    if isinstance(value, YouTubePollingCursor):
        return value
    data = _mapping(value)
    return YouTubePollingCursor(
        live_chat_id=str(data.get("live_chat_id", "")),
        next_page_token=data.get("next_page_token"),
        polling_interval_millis=data.get("polling_interval_millis"),
        seen_event_ids=_list_value(data.get("seen_event_ids")),
    )


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _list_value(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []
```

- [ ] **Step 6: Run green runtime tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_application_service.py::test_handle_youtube_event_advances_and_persists_polling_cursor_from_payload tests\youtubebridge_v2\test_runtime_application_service.py::test_handle_youtube_event_uses_stored_cursor_to_skip_duplicate_after_restart -q
```

Expected: `2 passed`.

---

### Task 5: Docs Sync

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/runtime-application-service.md`
- Modify: `YouTubeBridgeV2/docs/modules/storage.md`
- Modify: `YouTubeBridgeV2/docs/modules/youtube-adapter.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`

- [ ] **Step 1: Runtime Application Service docs**

Extend Wave 3A status or add Wave 3B status:

```markdown
Wave 3B status:
- `HANDLE_YOUTUBE_EVENT` 會優先使用 command payload 的 `polling_cursor`，否則從 storage 讀取 `youtube_polling_cursor`。
- cursor 會在 event persistence 後 advance 並寫回 session metadata；duplicate event id 只保存 ignored event summary，不 dispatch runner。
```

- [ ] **Step 2: Storage docs**

Add under Public Entrypoints:

```markdown
- `youtube_polling_cursor`：session metadata 內的 sanitized YouTube polling cursor，包含 `live_chat_id`、`next_page_token`、`polling_interval_millis`、`seen_event_ids` 與 `updated_at`。
- `RuntimeStoragePort.save_youtube_polling_cursor(session_id, cursor, now)` / `load_youtube_polling_cursor(session_id)`：runtime-facing cursor persistence/recovery boundary。
```

- [ ] **Step 3: YouTube Adapter docs**

Add under Runtime Handoff:

```markdown
Wave 3B cursor handoff:
- Runtime 可把 storage/payload cursor 傳入 `normalize_youtube_event(..., cursor=cursor)`，讓 duplicate event id 變成 `duplicate=True`、`should_dispatch=False`。
- Adapter 仍不保存 cursor；cursor persistence 屬於 Storage/Runtime boundary。
```

- [ ] **Step 4: API reference and architecture docs**

In API reference, add `YouTubePollingCursor` to Storage or YouTube adapter references if not already present.

In architecture index, add:

```markdown
## Integration Wave 3B 狀態

- [x] Polling cursor persistence：`RuntimeStoragePort` 可保存/讀回 session metadata 的 `youtube_polling_cursor`。
- [x] Restart recovery：重建 `StorageManager` 後可讀回 `YouTubePollingCursor`，seen event ids 不遺失。
- [x] Duplicate skip：runtime 用 cursor 判定 duplicate event id 時，只保存 ignored event summary，不重複 dispatch runner。
```

- [ ] **Step 5: Docs sanity check**

Run:

```powershell
rg -n "Integration Wave 3B|youtube_polling_cursor|save_youtube_polling_cursor|load_youtube_polling_cursor|Duplicate skip" YouTubeBridgeV2\docs
```

Expected: finds storage, runtime, YouTube adapter, API/architecture docs.

---

### Task 6: Final Verification For 3B

**Files:**
- Verify: `YouTubeBridgeV2/runtime/application_service.py`
- Verify: `YouTubeBridgeV2/storage/runtime_store.py`
- Verify: `tests/youtubebridge_v2/test_runtime_application_service.py`
- Verify: `tests/youtubebridge_v2/test_storage.py`
- Verify: `tests/youtubebridge_v2/test_storage_manager_durable_backend.py`
- Verify: docs touched in Task 5

- [ ] **Step 1: Run focused Wave 3 tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_youtube_adapter.py -q
python -m pytest tests\youtubebridge_v2\test_runtime_application_service.py -q
python -m pytest tests\youtubebridge_v2\test_storage.py -q
python -m pytest tests\youtubebridge_v2\test_storage_manager_durable_backend.py -q
```

Expected: all focused suites pass.

- [ ] **Step 2: Run full V2 suite**

Run:

```powershell
python -m pytest tests\youtubebridge_v2 -q
```

Expected: full V2 suite passes; real Memoria integration remains skipped by default.

- [ ] **Step 3: Scope and whitespace checks**

Run:

```powershell
git diff --check
git status -sb
git diff --stat
rg -n "^\s*(from|import)\s+(sqlite3|aiosqlite|YouTubeBridge(\.|\s|$)|googleapiclient|requests)" YouTubeBridgeV2\runtime\application_service.py YouTubeBridgeV2\storage\runtime_store.py tests\youtubebridge_v2\test_runtime_application_service.py tests\youtubebridge_v2\test_storage.py tests\youtubebridge_v2\test_storage_manager_durable_backend.py
```

Expected:

- no whitespace errors.
- changed files limited to 3B source/tests/docs/plan.
- no direct SQLite in V2 runtime/storage port, no Legacy `YouTubeBridge`, no real YouTube transport dependency.

- [ ] **Step 4: Request code review**

Use `superpowers:requesting-code-review` with scope limited to roadmap `3B`. Review focus:

- cursor survives StorageManager restart.
- duplicate event id from cursor does not dispatch runner.
- raw YouTube payload remains out of public event/session metadata.
- implementation does not cross into true polling client/scheduler/Super Chat handoff.

- [ ] **Step 5: Commit**

After review findings are fixed and verification is fresh:

```powershell
git add YouTubeBridgeV2\runtime\application_service.py YouTubeBridgeV2\storage\runtime_store.py tests\youtubebridge_v2\test_runtime_application_service.py tests\youtubebridge_v2\test_storage.py tests\youtubebridge_v2\test_storage_manager_durable_backend.py YouTubeBridgeV2\docs\modules\runtime-application-service.md YouTubeBridgeV2\docs\modules\storage.md YouTubeBridgeV2\docs\modules\youtube-adapter.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\implementation-plans\youtube-polling-cursor-recovery.md
git diff --cached --check
git commit -m "feat: persist YouTube polling cursor"
```

---

## Self-Review

Spec coverage:

- `polling cursor/storage` covered by `save_youtube_polling_cursor(...)` and session metadata key `youtube_polling_cursor`.
- `restart recovery` covered by real StorageManager restart test.
- duplicate event id handling covered by stored cursor duplicate test.
- `不要跨 wave` covered by excluding true YouTube client, scheduler, route, and Super Chat closing handoff.

Placeholder scan:

- No `TBD`, `TODO`, `implement later`, or vague test-only instructions remain.
- Every code-changing task includes exact file paths, code snippets, commands, and expected results.

Type consistency:

- `YouTubePollingCursor` is the single cursor dataclass across adapter/runtime/storage.
- Runtime helper returns `tuple[dict[str, object], YouTubePollingCursor | None]`, matching save cursor logic.
- Storage helper accepts `YouTubePollingCursor | dict[str, object]`, matching real metadata round-trip.

## Execution Handoff

Plan complete and saved to `YouTubeBridgeV2/docs/implementation-plans/youtube-polling-cursor-recovery.md`. Two execution options:

**1. Subagent-Driven (recommended)** - dispatch a fresh worker for 3B implementation and review.

**2. Inline Execution** - execute tasks in this session using `superpowers:executing-plans`, with checkpoints for review.
