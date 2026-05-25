# YouTube Ingestion Fake-Backed Boundary Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 roadmap `3E`：用 fake-backed tests 與 boundary scans 固化 Wave 3 YouTube ingestion 行為，避免 raw YouTube payload、legacy runtime 或真外部 YouTube transport 偷渡進 V2。

**Architecture:** 本階段不新增 production behavior，只新增測試與文件狀態。測試使用 `InMemoryV2StorageManager`、fake Memoria runners 與 standalone `create_v2_app(...)`，覆蓋 API ingestion -> runtime normalization -> storage/query/SSE 的 public path，並用 AST 掃描禁止依賴。

**Tech Stack:** Python 3.13、pytest、FastAPI TestClient、AST import scan、existing V2 fake storage/runners。

---

## Scope

Roadmap item：`3E：fake-backed + boundary tests`

完成條件：

- Fake-backed API ingestion test 覆蓋 `POST /v2/sessions/{session_id}/youtube-events` 到 event history/operator stream/display stream。
- Super Chat event 的 public metadata 可在 fake-backed path 中保存與讀取，但 raw YouTube payload、token、secret、authorization 不出現在 public response/SSE。
- Duplicate event id 經 API ingestion + stored cursor 可被 skip，不重複 dispatch runner。
- Boundary scan 確認 `YouTubeBridgeV2/` Python source 不 import legacy `YouTubeBridge/`、`googleapiclient`、`requests`、`sqlite3` 或 `aiosqlite`。
- Boundary scan 確認 `server/routes.py` 仍不直接 import YouTube adapter 或 storage。

不包含：

- 真 YouTube API polling。
- scheduler / background loop。
- production credentials。
- UI controls。

## File Structure

- Create: `tests/youtubebridge_v2/test_youtube_ingestion_boundaries.py`
  - fake-backed API ingestion redaction test。
  - duplicate cursor/idempotency test。
  - V2 source import boundary scan。
  - routes no-adapter/no-storage boundary scan。
- Modify: `YouTubeBridgeV2/storage/runtime_store.py`
  - 若 fake-backed storage 將 `youtube_polling_cursor` 暫存在 session top-level，runtime load path 仍可讀回；durable backend 的 metadata path 維持不變。
- Modify docs:
  - `YouTubeBridgeV2/docs/modules/youtube-adapter.md`
  - `YouTubeBridgeV2/docs/modules/server-api-surface.md`
  - `YouTubeBridgeV2/docs/architecture-index.md`
  - `YouTubeBridgeV2/docs/api-reference-index.md`

## Test Helpers

New test file should start with:

```python
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from tests.youtubebridge_v2.fakes import (
    FakeAftertalkRunner,
    FakeClosingRunner,
    FakePlannedShowRunner,
    InMemoryV2StorageManager,
)
from YouTubeBridgeV2.app import create_v2_app
from YouTubeBridgeV2.composition import create_v2_composition


ROOT = Path(__file__).resolve().parents[2]
STARTED_AT = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)


def _assert_no_private_payload(value: object) -> None:
    text = repr(value).lower()
    for forbidden in (
        "hidden_prompt",
        "raw_payload",
        "raw_youtube_payload",
        "rawtopicpack",
        "raw_topic_pack",
        "access_token",
        "authorization",
        "secret-value",
        "token",
        "must not leak",
    ):
        assert forbidden not in text


def _composition():
    storage = InMemoryV2StorageManager()
    planned_show = FakePlannedShowRunner(storage)
    composition = create_v2_composition(
        storage_manager=storage,
        planned_show_runner=planned_show,
        aftertalk_runner=FakeAftertalkRunner(storage),
        closing_runner=FakeClosingRunner(storage),
    )
    return composition, storage, planned_show


def _client():
    composition, storage, planned_show = _composition()
    client = TestClient(create_v2_app(composition, now_provider=lambda: STARTED_AT))
    return client, storage, planned_show


def _create_session(client: TestClient, session_id: str) -> None:
    response = client.post(
        "/v2/sessions",
        json={
            "command_id": f"{session_id}-create",
            "session_id": session_id,
            "aftertalk_policy": "auto",
        },
    )
    assert response.status_code == 200
```

---

### Task 1: Fake-Backed Super Chat Ingestion Boundary Test

**Files:**
- Create: `tests/youtubebridge_v2/test_youtube_ingestion_boundaries.py`

- [ ] **Step 1: Add red test for Super Chat API ingestion redaction**

Add:

```python
def test_fake_backed_youtube_super_chat_ingestion_is_public_safe_across_reads():
    client, _storage, planned_show = _client()
    _create_session(client, "session-youtube-boundary")

    response = client.post(
        "/v2/sessions/session-youtube-boundary/youtube-events",
        json={
            "command_id": "cmd-super-chat-boundary",
            "youtube_event": {
                "id": "sc-boundary-1",
                "snippet": {
                    "type": "superChatEvent",
                    "publishedAt": "2026-05-12T08:20:00Z",
                    "displayMessage": "Great stream",
                    "superChatDetails": {
                        "amountMicros": 150000000,
                        "currency": "TWD",
                        "amountDisplayString": "NT$150",
                        "userComment": "Great stream",
                        "tier": 3,
                    },
                },
                "authorDetails": {
                    "displayName": "Rin",
                    "channelId": "channel-rin",
                    "isChatSponsor": True,
                },
                "raw_youtube_payload": {
                    "access_token": "must not leak",
                    "authorization": "Bearer secret-value",
                },
            },
        },
    )
    events_response = client.get("/v2/sessions/session-youtube-boundary/events?limit=20")
    with client.stream(
        "GET",
        "/v2/sessions/session-youtube-boundary/operator-stream",
    ) as operator_stream:
        operator_stream.read()
        operator_text = operator_stream.text
    with client.stream(
        "GET",
        "/v2/sessions/session-youtube-boundary/display-stream",
    ) as display_stream:
        display_stream.read()
        display_text = display_stream.text

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    events = events_response.json()["events"]
    super_chat_event = next(
        event for event in events if event["event_id"] == "sc-boundary-1"
    )
    assert super_chat_event["event_type"] == "youtube_super_chat"
    public_payload = super_chat_event["public_payload"]["public_payload"]
    assert public_payload["author_display_name"] == "Rin"
    assert public_payload["super_chat"]["amount_display_string"] == "NT$150"
    assert public_payload["super_chat"]["acknowledgement_status"] == "pending"
    assert len(planned_show.calls) == 1
    _assert_no_private_payload(response.json())
    _assert_no_private_payload(events_response.json())
    _assert_no_private_payload(operator_text)
    _assert_no_private_payload(display_text)
```

- [ ] **Step 2: Run red test**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_youtube_ingestion_boundaries.py::test_fake_backed_youtube_super_chat_ingestion_is_public_safe_across_reads -q
```

Expected before adding the file:

- Collection or missing file fails.

Expected after adding helper + test:

- Test passes using existing 3A-3D implementation.

### Task 2: Duplicate Cursor Boundary Test

**Files:**
- Modify: `tests/youtubebridge_v2/test_youtube_ingestion_boundaries.py`

- [ ] **Step 1: Add duplicate ingestion test**

Add:

```python
def test_api_ingestion_uses_persisted_cursor_to_skip_duplicate_event_id():
    client, _storage, planned_show = _client()
    _create_session(client, "session-youtube-duplicate")
    raw_event = {
        "id": "yt-duplicate-1",
        "snippet": {
            "type": "textMessageEvent",
            "publishedAt": "2026-05-12T08:21:00Z",
            "displayMessage": "First",
            "textMessageDetails": {"messageText": "First"},
        },
        "authorDetails": {"displayName": "Mika", "channelId": "channel-mika"},
    }

    first = client.post(
        "/v2/sessions/session-youtube-duplicate/youtube-events",
        json={
            "command_id": "cmd-youtube-first",
            "youtube_event": raw_event,
            "polling_cursor": {
                "live_chat_id": "live-chat-1",
                "next_page_token": "page-1",
                "polling_interval_millis": 1500,
                "seen_event_ids": [],
            },
            "page_info": {
                "next_page_token": "page-2",
                "polling_interval_millis": 2500,
            },
        },
    )
    duplicate = client.post(
        "/v2/sessions/session-youtube-duplicate/youtube-events",
        json={
            "command_id": "cmd-youtube-duplicate",
            "youtube_event": raw_event,
        },
    )
    events = client.get("/v2/sessions/session-youtube-duplicate/events?limit=50").json()[
        "events"
    ]

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json()["events"][0]["event_type"] == "youtube_event_ignored"
    assert duplicate.json()["events"][0]["payload"] == {
        "youtube_event": "duplicate",
        "event_id": "yt-duplicate-1",
    }
    assert len(planned_show.calls) == 1
    assert any(
        event["event_type"] == "youtube_text_message"
        and event["public_payload"]["should_dispatch"] is False
        for event in events
    )
    _assert_no_private_payload(first.json())
    _assert_no_private_payload(duplicate.json())
    _assert_no_private_payload(events)
```

- [ ] **Step 2: Run duplicate test**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_youtube_ingestion_boundaries.py::test_api_ingestion_uses_persisted_cursor_to_skip_duplicate_event_id -q
```

Expected:

- Test passes after the test file is present, proving API path reuses 3B stored cursor.
- If it fails because the fake-backed cursor is saved top-level but loaded only from metadata, update `RuntimeStoragePort.load_youtube_polling_cursor(...)` to read `metadata["youtube_polling_cursor"]` first and `record["youtube_polling_cursor"]` as fallback.

### Task 3: Import Boundary Tests

**Files:**
- Modify: `tests/youtubebridge_v2/test_youtube_ingestion_boundaries.py`

- [ ] **Step 1: Add V2 source import scan**

Add:

```python
def _python_files_under_v2() -> list[Path]:
    return sorted((ROOT / "YouTubeBridgeV2").rglob("*.py"))


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    return modules


def test_youtube_ingestion_v2_source_has_no_external_transport_or_legacy_imports():
    forbidden_prefixes = (
        "YouTubeBridge",
        "googleapiclient",
        "google.oauth",
        "requests",
        "sqlite3",
        "aiosqlite",
    )
    violations: list[tuple[str, str]] = []

    for path in _python_files_under_v2():
        for module in _imported_modules(path):
            if any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for forbidden in forbidden_prefixes
            ):
                violations.append((str(path.relative_to(ROOT)), module))

    assert violations == []
```

- [ ] **Step 2: Add server route boundary scan**

Add:

```python
def test_youtube_ingestion_route_does_not_import_adapters_or_storage():
    route_path = ROOT / "YouTubeBridgeV2" / "server" / "routes.py"
    violations = [
        module
        for module in _imported_modules(route_path)
        if module.startswith("YouTubeBridgeV2.adapters")
        or module.startswith("YouTubeBridgeV2.storage")
        or module in {"sqlite3", "aiosqlite"}
    ]

    assert violations == []
```

- [ ] **Step 3: Run boundary tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_youtube_ingestion_boundaries.py -q
```

Expected:

- All new 3E tests pass.

### Task 4: Documentation

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/youtube-adapter.md`
- Modify: `YouTubeBridgeV2/docs/modules/server-api-surface.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`

- [ ] **Step 1: Update YouTube adapter module test strategy/status**

Add under `Wave 3D API ingestion`:

```markdown
Wave 3E fake-backed boundary tests:
- Fake-backed API ingestion tests cover text and Super Chat events without real YouTube transport.
- Boundary tests assert V2 source does not import legacy `YouTubeBridge/`, `googleapiclient`, `requests`, `sqlite3`, or `aiosqlite`.
- Public event/read/SSE assertions ensure raw YouTube payload and credentials do not leak.
```

- [ ] **Step 2: Update server API module test strategy**

Add to `Test Strategy`:

```markdown
- YouTube ingestion fake-backed vertical tests：API route -> runtime -> storage/query/SSE，不接真 YouTube transport。
- import boundary tests：route 不直接 import adapter/storage。
```

- [ ] **Step 3: Update API reference**

Under Server/API Surface stability/source or after Ingest YouTube Event Endpoint, add:

```markdown
Wave 3E Boundary Coverage:
- `tests/youtubebridge_v2/test_youtube_ingestion_boundaries.py`
```

- [ ] **Step 4: Update architecture status**

Add:

```markdown
## Integration Wave 3E 狀態

- [x] Fake-backed ingestion tests：API route -> runtime -> storage/query/SSE path 覆蓋 text 與 Super Chat event。
- [x] Duplicate boundary：API ingestion 會使用 persisted cursor skip duplicate event id，不重複 dispatch runner。
- [x] Import/privacy boundary：V2 source 不引入 legacy runtime、Google/requests transport 或直接 SQLite；public API/SSE 不暴露 raw YouTube payload。
```

- [ ] **Step 5: Run docs sanity search**

Run:

```powershell
rg -n "Integration Wave 3E|test_youtube_ingestion_boundaries|Fake-backed ingestion|Duplicate boundary" YouTubeBridgeV2\docs
```

Expected:

- Matches include architecture status, API reference, and relevant module docs.

### Task 5: Final Verification and Commit

**Files:**
- New test file and docs above.

- [ ] **Step 1: Run focused verification**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_youtube_ingestion_boundaries.py -q
python -m pytest tests\youtubebridge_v2\test_youtube_adapter.py -q
python -m pytest tests\youtubebridge_v2\test_runtime_application_service.py -q
python -m pytest tests\youtubebridge_v2\test_server_api_surface.py -q
python -m pytest tests\youtubebridge_v2\test_integration_vertical_slice.py -q
```

Expected:

- Focused suites pass.

- [ ] **Step 2: Run full roadmap verification**

Run:

```powershell
python -m pytest tests\youtubebridge_v2 -q
git diff --check
```

Expected:

- Full V2 suite passes.
- `git diff --check` exits 0. CRLF warnings are acceptable if there are no whitespace errors.

- [ ] **Step 3: Commit exact files**

Run:

```powershell
git add tests\youtubebridge_v2\test_youtube_ingestion_boundaries.py YouTubeBridgeV2\storage\runtime_store.py YouTubeBridgeV2\docs\modules\youtube-adapter.md YouTubeBridgeV2\docs\modules\server-api-surface.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\implementation-plans\youtube-ingestion-fake-backed-boundary-tests.md
git diff --cached --check
git commit -m "test: harden YouTube ingestion boundaries"
```

Expected:

- Commit succeeds.

## Self-Review

- Spec coverage: `3E` is test-focused; this plan adds fake-backed ingestion tests, duplicate/cursor boundary tests, import boundary tests, and docs status without adding scheduler or real YouTube transport.
- Placeholder scan: no `TBD`, `TODO`, or vague follow-up placeholders remain.
- Type consistency: tests use existing route `/youtube-events`, existing cursor fields from 3B, and existing `InMemoryV2StorageManager` fake.
