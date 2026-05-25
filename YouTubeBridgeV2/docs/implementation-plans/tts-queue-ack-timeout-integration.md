# TTS Queue Ack Timeout Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate provider-neutral TTS queue, ack, and timeout behavior into YouTubeBridgeV2 storage/runtime/API without adding a real TTS provider or changing runtime phase decisions.

**Architecture:** Wave 6C already emits display-safe `presentation_character_response` live events. Wave 6D adds a durable TTS delivery store exposed through StorageManager-like methods, lets Memoria runners enqueue `TTSRequest` records only when a session `tts_policy.enabled` flag is true, and adds small `/v2` routes to list queued deliveries, acknowledge delivery, and mark timeouts. The existing `YouTubeBridgeV2.presentation.tts` dataclasses remain the provider-neutral contract; storage/API integration records public state only.

**Tech Stack:** Python dataclasses, FastAPI routes, existing V2 composition/query service, SQLite through `core/storage/youtube_bridge_v2.py`, StorageManager-like test fakes, pytest.

---

## Scope Boundary

- Implement only roadmap item 6D: `TTS queue/ack/timeout integration`.
- Do not implement real TTS synthesis, browser playback, WebSocket/SSE ack callbacks, provider retries, or OBS/browser E2E.
- Do not alter runtime phase decisions from TTS queue, ack, or timeout state.
- Do not edit `YouTubeBridgeV2/docs/roadmap.md` checkbox state.
- Do not import legacy `YouTubeBridge/` modules.
- Do not add SQLite access inside `YouTubeBridgeV2/`; durable SQL changes stay in `core/storage/youtube_bridge_v2.py`.

## File Structure

- Modify `core/storage/youtube_bridge_v2.py`
  - Add `yb2_tts_deliveries` durable table.
  - Add StorageManager methods: `append_v2_tts_request`, `list_v2_tts_deliveries`, `ack_v2_tts_delivery`, `timeout_v2_tts_delivery`.
- Modify `YouTubeBridgeV2/storage/repositories.py`
  - Add `TTSDeliveryRepository` facade and module helper methods for StorageManager-like backends.
- Modify `YouTubeBridgeV2/query_service.py`
  - Add `get_tts_queue(session_id, limit=100, status=None)`.
- Modify `YouTubeBridgeV2/runtime/memoria_runners.py`
  - After Wave 6C presentation event creation, enqueue TTS when session metadata has `tts_policy.enabled == true`.
  - Do not enqueue when policy is missing/disabled.
- Modify `YouTubeBridgeV2/storage/runtime_store.py`
  - Persist create-session metadata into the durable session metadata field so runtime-side integrations can read `tts_policy`.
- Modify `YouTubeBridgeV2/server/routes.py`
  - Add `GET /v2/sessions/{session_id}/tts-queue`.
  - Add `POST /v2/sessions/{session_id}/tts-deliveries/{delivery_id}/ack`.
  - Add `POST /v2/sessions/{session_id}/tts-deliveries/{delivery_id}/timeout`.
- Modify `YouTubeBridgeV2/server/security.py` and `YouTubeBridgeV2/server/main_security.py`
  - Add permission actions for TTS queue read and delivery mutation.
- Modify `tests/youtubebridge_v2/fakes.py`
  - Add in-memory TTS delivery methods.
- Modify tests:
  - `tests/youtubebridge_v2/test_storage_manager_durable_backend.py`
  - `tests/youtubebridge_v2/test_storage.py`
  - `tests/youtubebridge_v2/test_runtime_memoria_runners.py`
  - `tests/youtubebridge_v2/test_server_api_surface.py`
  - `tests/youtubebridge_v2/test_main_app_security.py`
  - `tests/youtubebridge_v2/test_real_storage_integration.py`
  - Existing `tests/youtubebridge_v2/test_presentation_tts.py`
- Modify docs:
  - `YouTubeBridgeV2/docs/modules/presentation-tts.md`
  - `YouTubeBridgeV2/docs/modules/storage.md`
  - `YouTubeBridgeV2/docs/modules/server-api-surface.md`
  - `YouTubeBridgeV2/docs/architecture-index.md`
  - `YouTubeBridgeV2/docs/api-reference-index.md`

---

### Task 1: Durable TTS Delivery Storage Contract

**Files:**
- Modify: `tests/youtubebridge_v2/test_storage_manager_durable_backend.py`
- Modify: `core/storage/youtube_bridge_v2.py`

- [ ] **Step 1: Write failing durable storage tests**

Add these tests near the existing finalization storage tests:

```python
def test_append_and_list_v2_tts_deliveries_are_ordered_and_redacted(tmp_path):
    storage = _storage(tmp_path)
    storage.create_v2_session(_session_record())

    first = storage.append_v2_tts_request(
        "session-1",
        {
            "delivery_id": "tts-event-1",
            "event_id": "event-1",
            "character_id": "host",
            "text": "First line",
            "voice_id": "voice-host",
            "provider": "local",
            "queue_position": 1,
            "status": "pending",
            "metadata": {
                "interaction_id": "interaction-1",
                "raw_payload": {"token": "must not leak"},
            },
            "created_at": NOW,
        },
    )
    second = storage.append_v2_tts_request(
        "session-1",
        {
            "delivery_id": "tts-event-2",
            "event_id": "event-2",
            "character_id": "cohost",
            "text": "Second line",
            "voice_id": "voice-cohost",
            "provider": "local",
            "queue_position": 2,
            "status": "pending",
            "metadata": {"interaction_id": "interaction-2"},
            "created_at": NOW,
        },
    )

    deliveries = storage.list_v2_tts_deliveries("session-1", limit=10)

    assert first["delivery_id"] == "tts-event-1"
    assert second["delivery_id"] == "tts-event-2"
    assert [item["delivery_id"] for item in deliveries] == ["tts-event-1", "tts-event-2"]
    assert [item["queue_position"] for item in deliveries] == [1, 2]
    assert all(item["status"] == "pending" for item in deliveries)
    assert deliveries[0]["metadata"] == {"interaction_id": "interaction-1"}
    _assert_no_private_payload(deliveries)
```

```python
def test_v2_tts_delivery_ack_and_timeout_are_idempotent(tmp_path):
    storage = _storage(tmp_path)
    storage.create_v2_session(_session_record())
    storage.append_v2_tts_request(
        "session-1",
        {
            "delivery_id": "tts-event-1",
            "event_id": "event-1",
            "character_id": "host",
            "text": "Line",
            "voice_id": "voice-host",
            "provider": "local",
            "queue_position": 1,
            "status": "pending",
            "metadata": {},
            "created_at": NOW,
        },
    )

    ack = storage.ack_v2_tts_delivery(
        "session-1",
        "tts-event-1",
        {"acknowledged_at": NOW},
    )
    duplicate_ack = storage.ack_v2_tts_delivery(
        "session-1",
        "tts-event-1",
        {"acknowledged_at": NOW},
    )
    ignored_timeout = storage.timeout_v2_tts_delivery(
        "session-1",
        "tts-event-1",
        {"timeout_seconds": 30, "metadata": {"safe": "visible"}},
    )

    assert ack["status"] == "delivered"
    assert ack["duplicate"] is False
    assert duplicate_ack["status"] == "delivered"
    assert duplicate_ack["duplicate"] is True
    assert ignored_timeout["status"] == "delivered"
    assert ignored_timeout["timeout_ignored"] is True
    assert ignored_timeout["phase_transition_requested"] is False
    assert storage.list_v2_tts_deliveries("session-1")[0]["status"] == "delivered"
    _assert_no_private_payload((ack, duplicate_ack, ignored_timeout))
```

```python
def test_v2_tts_delivery_timeout_marks_pending_without_phase_change(tmp_path):
    storage = _storage(tmp_path)
    storage.create_v2_session(_session_record())
    storage.append_v2_tts_request(
        "session-1",
        {
            "delivery_id": "tts-event-timeout",
            "event_id": "event-timeout",
            "character_id": "host",
            "text": "Line",
            "voice_id": "voice-host",
            "provider": "local",
            "queue_position": 1,
            "status": "pending",
            "metadata": {},
            "created_at": NOW,
        },
    )

    result = storage.timeout_v2_tts_delivery(
        "session-1",
        "tts-event-timeout",
        {
            "timeout_seconds": 15,
            "metadata": {
                "safe": "visible",
                "raw_memoriacore_payload": {"token": "must not leak"},
            },
        },
    )

    assert result["status"] == "timeout"
    assert result["timeout_seconds"] == 15
    assert result["phase_transition_requested"] is False
    assert storage.list_v2_tts_deliveries("session-1")[0]["status"] == "timeout"
    _assert_no_private_payload(result)
```

- [ ] **Step 2: Run durable storage tests and verify red**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_storage_manager_durable_backend.py::test_append_and_list_v2_tts_deliveries_are_ordered_and_redacted tests\youtubebridge_v2\test_storage_manager_durable_backend.py::test_v2_tts_delivery_ack_and_timeout_are_idempotent tests\youtubebridge_v2\test_storage_manager_durable_backend.py::test_v2_tts_delivery_timeout_marks_pending_without_phase_change -q
```

Expected: FAIL because the durable backend has no TTS delivery table or methods.

- [ ] **Step 3: Add durable table and methods**

In `core/storage/youtube_bridge_v2.py`, add `yb2_tts_deliveries` to schema initialization:

```python
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS yb2_tts_deliveries (
                delivery_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                character_id TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL DEFAULT '',
                voice_id TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                queue_position INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                acknowledged_at TEXT DEFAULT NULL,
                timeout_seconds INTEGER DEFAULT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES yb2_sessions(session_id)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_yb2_tts_deliveries_session "
            "ON yb2_tts_deliveries(session_id, queue_position, created_at)"
        )
```

Add `"yb2_tts_deliveries"` to the schema test table set.

Add these public methods to `YouTubeBridgeV2RepositoryMixin`:

```python
    def append_v2_tts_request(
        self,
        session_id: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        safe_record = _sanitize_public_value(record)
        delivery_id = str(safe_record["delivery_id"])
        created_at = _datetime_text(safe_record.get("created_at"))
        with closing(self._init_youtube_bridge_v2_db()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR IGNORE INTO yb2_tts_deliveries (
                    delivery_id, session_id, event_id, character_id, text, voice_id,
                    provider, queue_position, status, metadata_json, acknowledged_at,
                    timeout_seconds, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    delivery_id,
                    session_id,
                    str(safe_record.get("event_id", "")),
                    str(safe_record.get("character_id", "")),
                    str(safe_record.get("text", "")),
                    str(safe_record.get("voice_id", "")),
                    str(safe_record.get("provider", "")),
                    int(safe_record.get("queue_position", 0) or 0),
                    str(safe_record.get("status", "pending")),
                    _json_text(safe_record.get("metadata", {})),
                    _optional_datetime_text(safe_record.get("acknowledged_at")),
                    _optional_int(safe_record.get("timeout_seconds")),
                    created_at,
                    _datetime_text(safe_record.get("updated_at") or created_at),
                ),
            )
            conn.commit()
        stored = self.get_v2_tts_delivery(session_id, delivery_id)
        if stored is None:
            raise KeyError(delivery_id)
        return stored
```

```python
    def get_v2_tts_delivery(
        self,
        session_id: str,
        delivery_id: str,
    ) -> dict[str, object] | None:
        with closing(self._init_youtube_bridge_v2_db()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT delivery_id, session_id, event_id, character_id, text, voice_id,
                       provider, queue_position, status, metadata_json, acknowledged_at,
                       timeout_seconds, created_at, updated_at
                FROM yb2_tts_deliveries
                WHERE session_id = ? AND delivery_id = ?
                """,
                (session_id, delivery_id),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return _tts_delivery_from_row(row)
```

```python
    def list_v2_tts_deliveries(
        self,
        session_id: str,
        limit: int = 100,
        status: str | None = None,
    ) -> list[dict[str, object]]:
        safe_limit = max(1, min(int(limit), 500))
        params: list[object] = [session_id]
        status_clause = ""
        if status:
            status_clause = "AND status = ?"
            params.append(str(status))
        params.append(safe_limit)
        with closing(self._init_youtube_bridge_v2_db()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT delivery_id, session_id, event_id, character_id, text, voice_id,
                       provider, queue_position, status, metadata_json, acknowledged_at,
                       timeout_seconds, created_at, updated_at
                FROM yb2_tts_deliveries
                WHERE session_id = ? {status_clause}
                ORDER BY queue_position ASC, created_at ASC
                LIMIT ?
                """,
                tuple(params),
            )
            rows = cursor.fetchall()
        return [_tts_delivery_from_row(row) for row in rows]
```

```python
    def ack_v2_tts_delivery(
        self,
        session_id: str,
        delivery_id: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        current = self.get_v2_tts_delivery(session_id, delivery_id)
        if current is None:
            raise KeyError(delivery_id)
        duplicate = current["status"] == "delivered"
        acknowledged_at = _datetime_text(record.get("acknowledged_at"))
        with closing(self._init_youtube_bridge_v2_db()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE yb2_tts_deliveries
                SET status = 'delivered', acknowledged_at = ?, updated_at = ?
                WHERE session_id = ? AND delivery_id = ?
                """,
                (acknowledged_at, _now_iso(), session_id, delivery_id),
            )
            conn.commit()
        stored = self.get_v2_tts_delivery(session_id, delivery_id)
        if stored is None:
            raise KeyError(delivery_id)
        return {
            **stored,
            "duplicate": duplicate,
            "phase_transition_requested": False,
            "public_summary": {"delivery_id": delivery_id, "status": "delivered"},
        }
```

```python
    def timeout_v2_tts_delivery(
        self,
        session_id: str,
        delivery_id: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        current = self.get_v2_tts_delivery(session_id, delivery_id)
        if current is None:
            raise KeyError(delivery_id)
        timeout_seconds = int(record.get("timeout_seconds", 0) or 0)
        metadata = _sanitize_public_value(record.get("metadata", {}))
        if current["status"] == "delivered":
            return {
                **current,
                "timeout_seconds": timeout_seconds,
                "metadata": metadata,
                "timeout_ignored": True,
                "phase_transition_requested": False,
                "public_summary": {
                    "delivery_id": delivery_id,
                    "status": "delivered",
                    "timeout_seconds": timeout_seconds,
                    "timeout_ignored": True,
                    "reason": "already_delivered",
                },
            }
        with closing(self._init_youtube_bridge_v2_db()) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE yb2_tts_deliveries
                SET status = 'timeout', timeout_seconds = ?, metadata_json = ?,
                    updated_at = ?
                WHERE session_id = ? AND delivery_id = ?
                """,
                (
                    timeout_seconds,
                    _json_text({**current.get("metadata", {}), **metadata}),
                    _now_iso(),
                    session_id,
                    delivery_id,
                ),
            )
            conn.commit()
        stored = self.get_v2_tts_delivery(session_id, delivery_id)
        if stored is None:
            raise KeyError(delivery_id)
        return {
            **stored,
            "phase_transition_requested": False,
            "public_summary": {
                "delivery_id": delivery_id,
                "status": "timeout",
                "timeout_seconds": timeout_seconds,
            },
        }
```

Add helpers:

```python
def _tts_delivery_from_row(row: tuple[object, ...]) -> dict[str, object]:
    return {
        "delivery_id": row[0],
        "session_id": row[1],
        "event_id": row[2],
        "character_id": row[3],
        "text": row[4],
        "voice_id": row[5],
        "provider": row[6],
        "queue_position": int(row[7]),
        "status": row[8],
        "metadata": _json_value(row[9]),
        "acknowledged_at": _optional_datetime_value(row[10]),
        "timeout_seconds": row[11],
        "created_at": _datetime_value(row[12]),
        "updated_at": _datetime_value(row[13]),
    }


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)
```

- [ ] **Step 4: Verify durable storage tests pass**

Run the same command from Step 2.

Expected: PASS.

---

### Task 2: Repository and Fake Storage Boundaries

**Files:**
- Modify: `tests/youtubebridge_v2/test_storage.py`
- Modify: `tests/youtubebridge_v2/fakes.py`
- Modify: `YouTubeBridgeV2/storage/repositories.py`

- [ ] **Step 1: Write failing repository facade test**

Add `TTSDeliveryRepository` to the imports in `tests/youtubebridge_v2/test_storage.py`.

Add this test near other repository append tests:

```python
def test_tts_delivery_repository_delegates_to_storage_manager():
    storage = FakeStorageManager()
    storage.create_v2_session(_session_record())
    repository = TTSDeliveryRepository(storage)

    queued = repository.append_tts_request(
        "session-1",
        {
            "delivery_id": "tts-event-1",
            "event_id": "event-1",
            "character_id": "host",
            "text": "Line",
            "voice_id": "voice-host",
            "provider": "local",
            "queue_position": 1,
            "status": "pending",
            "metadata": {"raw_payload": {"token": "secret"}, "safe": "visible"},
            "created_at": NOW,
        },
    )
    ack = repository.ack_delivery("session-1", "tts-event-1", {"acknowledged_at": NOW})
    timeout = repository.timeout_delivery(
        "session-1",
        "tts-event-1",
        {"timeout_seconds": 20, "metadata": {"safe": "ignored"}},
    )

    assert queued["delivery_id"] == "tts-event-1"
    assert queued["metadata"] == {"safe": "visible"}
    assert ack["status"] == "delivered"
    assert timeout["timeout_ignored"] is True
    _assert_no_private_payload((queued, ack, timeout))
```

- [ ] **Step 2: Run repository test and verify red**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_storage.py::test_tts_delivery_repository_delegates_to_storage_manager -q
```

Expected: FAIL because `TTSDeliveryRepository` does not exist.

- [ ] **Step 3: Implement repository facade**

In `YouTubeBridgeV2/storage/repositories.py`:

1. Add `self.tts_deliveries = TTSDeliveryRepository(self.storage_manager)` to `StorageManagerBackedRepository.__init__`.
2. Add class:

```python
class TTSDeliveryRepository:
    """V2 presentation/TTS delivery repository."""

    def __init__(self, storage_manager: object | None = None) -> None:
        self.storage_manager = _require_storage_manager(storage_manager)

    def append_tts_request(
        self,
        session_id: str,
        request: dict[str, object],
    ) -> dict[str, object]:
        record = _tts_request_record(session_id, request)
        if not hasattr(self.storage_manager, "append_v2_tts_request"):
            raise StorageContractError("StorageManager missing append_v2_tts_request")
        stored = self.storage_manager.append_v2_tts_request(session_id, record)
        return _redact_public_value(stored)

    def list_tts_deliveries(
        self,
        session_id: str,
        limit: int = 100,
        status: str | None = None,
    ) -> list[dict[str, object]]:
        if not hasattr(self.storage_manager, "list_v2_tts_deliveries"):
            raise StorageContractError("StorageManager missing list_v2_tts_deliveries")
        return _redact_public_value(
            self.storage_manager.list_v2_tts_deliveries(session_id, limit, status)
        )

    def ack_delivery(
        self,
        session_id: str,
        delivery_id: str,
        ack: dict[str, object],
    ) -> dict[str, object]:
        if not hasattr(self.storage_manager, "ack_v2_tts_delivery"):
            raise StorageContractError("StorageManager missing ack_v2_tts_delivery")
        return _redact_public_value(
            self.storage_manager.ack_v2_tts_delivery(session_id, delivery_id, ack)
        )

    def timeout_delivery(
        self,
        session_id: str,
        delivery_id: str,
        timeout: dict[str, object],
    ) -> dict[str, object]:
        if not hasattr(self.storage_manager, "timeout_v2_tts_delivery"):
            raise StorageContractError("StorageManager missing timeout_v2_tts_delivery")
        return _redact_public_value(
            self.storage_manager.timeout_v2_tts_delivery(session_id, delivery_id, timeout)
        )
```

Add helper:

```python
def _tts_request_record(session_id: str, request: dict[str, object]) -> dict[str, object]:
    data = _object_to_dict(request)
    return _redact_public_value(
        {
            "session_id": session_id,
            "delivery_id": str(data.get("delivery_id", "")),
            "event_id": str(data.get("event_id", "")),
            "character_id": str(data.get("character_id", "")),
            "text": str(data.get("text", "")),
            "voice_id": str(data.get("voice_id", "")),
            "provider": str(data.get("provider", "")),
            "queue_position": int(data.get("queue_position", 0) or 0),
            "status": str(data.get("status", "pending")),
            "metadata": data.get("metadata", {}),
            "created_at": data.get("created_at"),
        }
    )
```

Add `TTSDeliveryRepository` to `__all__`.

- [ ] **Step 4: Add fake storage methods**

In `tests/youtubebridge_v2/fakes.py`, add `self.tts_deliveries: list[dict[str, object]] = []` in `InMemoryV2StorageManager.__init__`, then add:

```python
    def append_v2_tts_request(
        self,
        session_id: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        stored = deepcopy(record)
        stored["session_id"] = session_id
        self.tts_deliveries.append(stored)
        return deepcopy(stored)

    def list_v2_tts_deliveries(
        self,
        session_id: str,
        limit: int = 100,
        status: str | None = None,
    ) -> list[dict[str, object]]:
        deliveries = [
            item
            for item in self.tts_deliveries
            if item.get("session_id") == session_id
            and (status is None or item.get("status") == status)
        ]
        return deepcopy(deliveries[-limit:])

    def ack_v2_tts_delivery(
        self,
        session_id: str,
        delivery_id: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        for item in self.tts_deliveries:
            if item.get("session_id") == session_id and item.get("delivery_id") == delivery_id:
                duplicate = item.get("status") == "delivered"
                item["status"] = "delivered"
                item["acknowledged_at"] = record.get("acknowledged_at")
                return deepcopy(
                    {
                        **item,
                        "duplicate": duplicate,
                        "phase_transition_requested": False,
                    }
                )
        raise KeyError(delivery_id)

    def timeout_v2_tts_delivery(
        self,
        session_id: str,
        delivery_id: str,
        record: dict[str, object],
    ) -> dict[str, object]:
        for item in self.tts_deliveries:
            if item.get("session_id") == session_id and item.get("delivery_id") == delivery_id:
                timeout_seconds = int(record.get("timeout_seconds", 0) or 0)
                if item.get("status") == "delivered":
                    return deepcopy(
                        {
                            **item,
                            "timeout_seconds": timeout_seconds,
                            "timeout_ignored": True,
                            "phase_transition_requested": False,
                        }
                    )
                item["status"] = "timeout"
                item["timeout_seconds"] = timeout_seconds
                item["metadata"] = {
                    **dict(item.get("metadata", {})),
                    **dict(record.get("metadata", {})),
                }
                return deepcopy({**item, "phase_transition_requested": False})
        raise KeyError(delivery_id)
```

Add equivalent simple methods to `FakeStorageManager` in `tests/youtubebridge_v2/test_storage.py` for the repository unit test.

- [ ] **Step 5: Verify repository test passes**

Run the repository test command from Step 2.

Expected: PASS.

---

### Task 3: Runtime Auto-Enqueue From Presentation Events

**Files:**
- Modify: `tests/youtubebridge_v2/test_runtime_memoria_runners.py`
- Modify: `YouTubeBridgeV2/runtime/memoria_runners.py`

- [ ] **Step 1: Write failing runtime enqueue tests**

Add this test near the 6C presentation display tests:

```python
def test_planned_show_runner_enqueues_tts_when_policy_enabled():
    storage = InMemoryV2StorageManager()
    port = _create_bound_session(storage)
    storage.update_v2_session(
        "session-runner",
        {
            "metadata": {
                **storage.get_v2_session("session-runner").get("metadata", {}),
                "tts_policy": {
                    "enabled": True,
                    "provider": "local",
                    "default_voice_id": "fallback-voice",
                },
            }
        },
    )
    transport = FakeMemoriaTransport(
        {
            "session_id": "memoria-1",
            "message_id": "msg-tts",
            "character_id": "host",
            "character_name": "Luna",
            "role_label": "Host",
            "voice_id": "voice-luna",
            "reply": "Speak this line",
            "presentation": {"voice_state": "speaking"},
        }
    )
    runner = MemoriaPlannedShowRunner(storage, transport)

    result = runner.run(
        command=_command("cmd-planned-tts"),
        snapshot=port.read_snapshot("session-runner"),
        transition=_transition(LiveSessionPhase.PLANNED_SHOW, action="run_planned_show"),
        now=NOW,
    )

    assert result.status == "ok"
    assert len(storage.tts_deliveries) == 1
    delivery = storage.tts_deliveries[0]
    assert delivery["delivery_id"].startswith("tts-presentation:")
    assert delivery["text"] == "Speak this line"
    assert delivery["voice_id"] == "voice-luna"
    assert delivery["provider"] == "local"
    assert delivery["queue_position"] == 1
    assert delivery["status"] == "pending"
    assert delivery["metadata"]["interaction_id"].endswith(":msg-tts")
    _assert_no_private_payload(storage.tts_deliveries)
```

```python
def test_aftertalk_runner_does_not_enqueue_tts_when_policy_disabled():
    storage = InMemoryV2StorageManager()
    port = _create_bound_session(storage)
    storage.update_v2_session(
        "session-runner",
        {
            "plan_completed": True,
            "metadata": {
                **storage.get_v2_session("session-runner").get("metadata", {}),
                "tts_policy": {"enabled": False},
            },
        },
    )
    transport = FakeMemoriaTransport(
        {
            "session_id": "memoria-2",
            "turns": [
                {
                    "message_id": "a1",
                    "character_id": "host",
                    "reply": "No TTS",
                }
            ],
        }
    )
    runner = MemoriaAftertalkRunner(storage, transport)

    result = runner.run(
        command=_command("cmd-aftertalk-no-tts"),
        snapshot=port.read_snapshot("session-runner"),
        transition=_transition(LiveSessionPhase.AFTERTALK, action="continue_aftertalk"),
        now=NOW,
    )

    assert result.status == "ok"
    assert storage.tts_deliveries == []
```

- [ ] **Step 2: Run runtime enqueue tests and verify red**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_memoria_runners.py::test_planned_show_runner_enqueues_tts_when_policy_enabled tests\youtubebridge_v2\test_runtime_memoria_runners.py::test_aftertalk_runner_does_not_enqueue_tts_when_policy_disabled -q
```

Expected: first test FAILS because runtime does not append TTS delivery records yet; second may pass after fakes exist but still run as part of this task.

- [ ] **Step 3: Enqueue TTS from runtime presentation event**

In `YouTubeBridgeV2/runtime/memoria_runners.py`, add import:

```python
from YouTubeBridgeV2.presentation.tts import build_presentation_event, enqueue_tts_request
```

Update `_append_presentation_display_event(...)` so after appending the display event it calls:

```python
    _enqueue_tts_delivery_if_enabled(
        storage_manager,
        session_id=session_id,
        event=event,
        now=now,
    )
```

Add helpers:

```python
def _enqueue_tts_delivery_if_enabled(
    storage_manager: object,
    *,
    session_id: str,
    event: object,
    now: datetime,
) -> None:
    if not hasattr(storage_manager, "append_v2_tts_request"):
        return
    policy = _tts_policy(storage_manager, session_id)
    if not policy.get("enabled", False):
        return
    pending_count = 0
    if hasattr(storage_manager, "list_v2_tts_deliveries"):
        pending_count = len(storage_manager.list_v2_tts_deliveries(session_id, 500, "pending"))
    queue_seed: list[object] = [object()] * pending_count
    request = enqueue_tts_request(event, policy, queue=queue_seed)
    if request is None:
        return
    record = asdict(request)
    record["created_at"] = now
    storage_manager.append_v2_tts_request(
        session_id,
        _redact_public_value(record),
    )
```

```python
def _tts_policy(storage_manager: object, session_id: str) -> dict[str, object]:
    if not hasattr(storage_manager, "get_v2_session"):
        return {"enabled": False}
    session = _object_to_dict(storage_manager.get_v2_session(session_id) or {})
    metadata = _object_to_dict(session.get("metadata", {}))
    policy = _object_to_dict(metadata.get("tts_policy") or session.get("tts_policy") or {})
    return _redact_public_value(policy)
```

- [ ] **Step 4: Verify runtime tests pass**

Run the command from Step 2.

Expected: PASS. Also run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_memoria_runners.py -q
```

Expected: full runner suite passes.

---

### Task 4: Query and HTTP API for TTS Delivery State

**Files:**
- Modify: `tests/youtubebridge_v2/test_server_api_surface.py`
- Modify: `tests/youtubebridge_v2/test_main_app_security.py`
- Modify: `YouTubeBridgeV2/query_service.py`
- Modify: `YouTubeBridgeV2/server/routes.py`
- Modify: `YouTubeBridgeV2/server/security.py`
- Modify: `YouTubeBridgeV2/server/main_security.py`

- [ ] **Step 1: Add failing server route tests**

In `tests/youtubebridge_v2/test_server_api_surface.py`, update `FakeQueryService` with:

```python
    def get_tts_queue(self, session_id, limit=100, status=None):
        self.calls.append(("get_tts_queue", session_id, limit, status))
        return [
            {
                "delivery_id": "tts-event-1",
                "status": "pending",
                "text": "Line",
                "metadata": {"safe": "visible", "raw_payload": {"token": "must not leak"}},
            }
        ]
```

Add `storage_manager=None` to `_app(...)` and override `routes.get_storage_manager` when provided.

Add a fake storage class:

```python
class FakeTTSStorage:
    def __init__(self):
        self.acks = []
        self.timeouts = []

    def ack_v2_tts_delivery(self, session_id, delivery_id, record):
        self.acks.append((session_id, delivery_id, dict(record)))
        return {
            "delivery_id": delivery_id,
            "session_id": session_id,
            "status": "delivered",
            "duplicate": False,
            "phase_transition_requested": False,
        }

    def timeout_v2_tts_delivery(self, session_id, delivery_id, record):
        self.timeouts.append((session_id, delivery_id, dict(record)))
        return {
            "delivery_id": delivery_id,
            "session_id": session_id,
            "status": "timeout",
            "timeout_seconds": record["timeout_seconds"],
            "phase_transition_requested": False,
            "metadata": record.get("metadata", {}),
        }
```

Add tests:

```python
def test_get_tts_queue_delegates_to_query_service():
    query = FakeQueryService()
    client = TestClient(_app(query_service=query))

    response = client.get("/v2/sessions/session-1/tts-queue?limit=10&status=pending")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": "session-1",
        "tts_queue": [
            {
                "delivery_id": "tts-event-1",
                "status": "pending",
                "text": "Line",
                "metadata": {"safe": "visible"},
            }
        ],
    }
    assert query.calls[-1] == ("get_tts_queue", "session-1", 10, "pending")
    _assert_no_private_payload(response.json())
```

```python
def test_ack_tts_delivery_delegates_to_storage_manager():
    storage = FakeTTSStorage()
    client = TestClient(_app(storage_manager=storage))

    response = client.post(
        "/v2/sessions/session-1/tts-deliveries/tts-event-1/ack",
        json={"command_id": "ack-1"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "delivered"
    assert response.json()["phase_transition_requested"] is False
    assert storage.acks[0][0:2] == ("session-1", "tts-event-1")
    assert storage.acks[0][2]["acknowledged_at"] == NOW
```

```python
def test_timeout_tts_delivery_delegates_to_storage_manager_without_phase_change():
    storage = FakeTTSStorage()
    client = TestClient(_app(storage_manager=storage))

    response = client.post(
        "/v2/sessions/session-1/tts-deliveries/tts-event-1/timeout",
        json={
            "command_id": "timeout-1",
            "timeout_seconds": 30,
            "metadata": {"safe": "visible", "raw_payload": {"token": "must not leak"}},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "timeout"
    assert response.json()["timeout_seconds"] == 30
    assert response.json()["phase_transition_requested"] is False
    assert storage.timeouts[0][2]["metadata"] == {"safe": "visible"}
    _assert_no_private_payload(response.json())
```

- [ ] **Step 2: Run route tests and verify red**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_server_api_surface.py::test_get_tts_queue_delegates_to_query_service tests\youtubebridge_v2\test_server_api_surface.py::test_ack_tts_delivery_delegates_to_storage_manager tests\youtubebridge_v2\test_server_api_surface.py::test_timeout_tts_delivery_delegates_to_storage_manager_without_phase_change -q
```

Expected: FAIL because routes and query method do not exist.

- [ ] **Step 3: Implement query method**

In `YouTubeBridgeV2/query_service.py`, add:

```python
    def get_tts_queue(
        self,
        session_id: str,
        limit: int = 100,
        status: str | None = None,
    ) -> list[dict[str, object]]:
        """回傳 presentation/TTS delivery queue 的 public projection。"""

        self._session_record(session_id)
        safe_limit = max(1, min(int(limit), 500))
        if not hasattr(self._storage_manager, "list_v2_tts_deliveries"):
            return []
        return _sanitize_public_payload(
            self._storage_manager.list_v2_tts_deliveries(session_id, safe_limit, status)
        )
```

- [ ] **Step 4: Implement routes**

In `YouTubeBridgeV2/server/routes.py`, add models:

```python
class TTSDeliveryAckRequest(BaseModel):
    command_id: str = Field(..., min_length=1)


class TTSDeliveryTimeoutRequest(BaseModel):
    command_id: str = Field(..., min_length=1)
    timeout_seconds: int = Field(..., gt=0)
    metadata: dict[str, object] = Field(default_factory=dict)
```

Add endpoints:

```python
@router.get("/sessions/{session_id}/tts-queue", response_model=None)
def get_tts_queue_endpoint(
    session_id: str,
    limit: int = 100,
    status: str | None = None,
    query_service: object = Depends(get_query_service),
) -> dict[str, object] | JSONResponse:
    """Return public TTS delivery queue state."""

    try:
        queue = query_service.get_tts_queue(session_id, max(1, min(int(limit), 500)), status)
    except V2QueryServiceError:
        return _query_not_found_response(session_id)
    return {
        "session_id": session_id,
        "tts_queue": _sanitize_public_payload(list(queue)),
    }
```

```python
@router.post("/sessions/{session_id}/tts-deliveries/{delivery_id}/ack", response_model=None)
def ack_tts_delivery_endpoint(
    session_id: str,
    delivery_id: str,
    raw_body: object = Body(...),
    storage_manager: object = Depends(get_storage_manager),
    now: datetime = Depends(get_now),
) -> dict[str, object] | JSONResponse:
    """Acknowledge one TTS delivery without changing runtime phase."""

    body = _validate_body(TTSDeliveryAckRequest, raw_body)
    if isinstance(body, JSONResponse):
        return body
    try:
        return _sanitize_public_payload(
            storage_manager.ack_v2_tts_delivery(
                session_id,
                delivery_id,
                {"acknowledged_at": now, "command_id": body.command_id},
            )
        )
    except KeyError:
        return _query_not_found_response(session_id)
```

```python
@router.post("/sessions/{session_id}/tts-deliveries/{delivery_id}/timeout", response_model=None)
def timeout_tts_delivery_endpoint(
    session_id: str,
    delivery_id: str,
    raw_body: object = Body(...),
    storage_manager: object = Depends(get_storage_manager),
) -> dict[str, object] | JSONResponse:
    """Mark one TTS delivery timeout without changing runtime phase."""

    body = _validate_body(TTSDeliveryTimeoutRequest, raw_body)
    if isinstance(body, JSONResponse):
        return body
    try:
        return _sanitize_public_payload(
            storage_manager.timeout_v2_tts_delivery(
                session_id,
                delivery_id,
                {
                    "timeout_seconds": body.timeout_seconds,
                    "metadata": body.metadata,
                    "command_id": body.command_id,
                },
            )
        )
    except KeyError:
        return _query_not_found_response(session_id)
```

Add endpoint names to `__all__`.

- [ ] **Step 5: Implement security mapping**

In `YouTubeBridgeV2/server/security.py`, add operator allowed actions:

```python
"read_tts_queue",
"ack_tts_delivery",
"timeout_tts_delivery",
```

Add `_ROUTE_ACTIONS` entries:

```python
"tts_queue": "read_tts_queue",
"tts_delivery_ack": "ack_tts_delivery",
"tts_delivery_timeout": "timeout_tts_delivery",
```

In `YouTubeBridgeV2/server/main_security.py`, update `_session_child_requirement(...)`:

```python
    if child == "tts-queue" and method == "GET":
        return PermissionGroup.OBSERVER, "tts_queue"
```

Update `_route_requirement(...)` to recognize nested delivery routes:

```python
        if len(parts) == 6 and parts[3] == "tts-deliveries":
            if parts[5] == "ack" and http_method == "POST":
                return PermissionGroup.OPERATOR, "tts_delivery_ack"
            if parts[5] == "timeout" and http_method == "POST":
                return PermissionGroup.OPERATOR, "tts_delivery_timeout"
```

- [ ] **Step 6: Add main-app security tests**

In `tests/youtubebridge_v2/test_main_app_security.py`, add tests that non-loopback observer/display permissions match the new routes. Use the file's existing helper style and assert:

- observer can `GET /v2/sessions/session-1/tts-queue`.
- display cannot `GET /v2/sessions/session-1/tts-queue`.
- observer cannot `POST /v2/sessions/session-1/tts-deliveries/tts-event-1/ack`.
- operator can POST ack/timeout on loopback or with operator key.

- [ ] **Step 7: Verify route and security tests pass**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_server_api_surface.py tests\youtubebridge_v2\test_main_app_security.py -q
```

Expected: PASS.

---

### Task 5: Real Storage Vertical Slice for Queue, Ack, Timeout

**Files:**
- Modify: `tests/youtubebridge_v2/test_real_storage_integration.py`

- [ ] **Step 1: Add failing real-storage vertical test**

Add a test that creates a real V2 app with `tts_policy.enabled`, runs one planned tick through real Memoria runner/fake transport, lists queue, acks the queued delivery, and verifies timeout-after-ack is ignored:

```python
def test_real_storage_tts_queue_ack_and_timeout_flow(tmp_path):
    storage = _storage_manager(tmp_path)
    transport = FakeMemoriaTransport(
        {
            "session_id": "memoria-tts",
            "message_id": "planned-tts",
            "character_id": "host",
            "character_name": "Luna",
            "role_label": "Host",
            "voice_id": "voice-luna",
            "reply": "TTS line",
            "presentation": {"voice_state": "speaking"},
        }
    )
    composition = create_v2_composition(
        storage_manager=storage,
        planned_show_runner=MemoriaPlannedShowRunner(storage, transport),
    )
    client = TestClient(create_v2_app(composition, now_provider=lambda: STARTED_AT))

    create_response = client.post(
        "/v2/sessions",
        json={
            "command_id": "session-tts-create",
            "session_id": "session-tts",
            "aftertalk_policy": "auto",
            "metadata": {
                "duration_policy": {
                    "planned_duration_seconds": 3600,
                    "auto_finalize_on_duration": True,
                    "aftertalk_requires_remaining_time": True,
                },
                "tts_policy": {
                    "enabled": True,
                    "provider": "local",
                    "default_voice_id": "fallback-voice",
                },
            },
        },
    )
    bind_response = client.post(
        "/v2/sessions/session-tts/plan",
        json={"command_id": "session-tts-bind", "plan": _valid_plan(turn_count=1)},
    )
    tick_response = client.post(
        "/v2/sessions/session-tts/tick",
        json={"command_id": "session-tts-tick"},
    )
    queue_response = client.get("/v2/sessions/session-tts/tts-queue")

    assert create_response.status_code == 200
    assert bind_response.status_code == 200
    assert tick_response.status_code == 200
    assert queue_response.status_code == 200
    queued = queue_response.json()["tts_queue"]
    assert len(queued) == 1
    assert queued[0]["text"] == "TTS line"
    assert queued[0]["status"] == "pending"
    delivery_id = queued[0]["delivery_id"]

    ack_response = client.post(
        f"/v2/sessions/session-tts/tts-deliveries/{delivery_id}/ack",
        json={"command_id": "ack-tts"},
    )
    timeout_response = client.post(
        f"/v2/sessions/session-tts/tts-deliveries/{delivery_id}/timeout",
        json={"command_id": "timeout-tts", "timeout_seconds": 30},
    )
    delivered_queue = client.get("/v2/sessions/session-tts/tts-queue?status=delivered")

    assert ack_response.status_code == 200
    assert ack_response.json()["status"] == "delivered"
    assert ack_response.json()["phase_transition_requested"] is False
    assert timeout_response.status_code == 200
    assert timeout_response.json()["timeout_ignored"] is True
    assert timeout_response.json()["phase_transition_requested"] is False
    assert delivered_queue.json()["tts_queue"][0]["delivery_id"] == delivery_id
    _assert_no_private_payload(
        (
            queue_response.json(),
            ack_response.json(),
            timeout_response.json(),
            delivered_queue.json(),
        )
    )
```

Use existing helpers in the file where available; if `_valid_plan` is not available, use the file's current plan helper shape.

- [ ] **Step 2: Run the real-storage test and verify red if not already covered**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_real_storage_integration.py::test_real_storage_tts_queue_ack_and_timeout_flow -q
```

Expected before Tasks 1-4: FAIL. After Tasks 1-4: PASS.

---

### Task 6: Documentation and Verification

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/presentation-tts.md`
- Modify: `YouTubeBridgeV2/docs/modules/storage.md`
- Modify: `YouTubeBridgeV2/docs/modules/server-api-surface.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] **Step 1: Update docs**

Add concise Traditional Chinese notes:

- `presentation-tts.md`: Wave 6D persists queue records when `tts_policy.enabled` is true; ack/timeout are public delivery state and never phase decisions.
- `storage.md`: list `append_v2_tts_request`, `list_v2_tts_deliveries`, `ack_v2_tts_delivery`, `timeout_v2_tts_delivery`.
- `server-api-surface.md`: list `/tts-queue`, `/tts-deliveries/{delivery_id}/ack`, `/tts-deliveries/{delivery_id}/timeout`.
- `architecture-index.md`: add Wave 6D status note, explicitly leaving real provider and E2E to later work.
- `api-reference-index.md`: add StorageManager and route entries.

- [ ] **Step 2: Verify docs references**

Run:

```powershell
rg -n "tts-queue|tts-deliveries|append_v2_tts_request|ack_v2_tts_delivery|timeout_v2_tts_delivery|Wave 6D" YouTubeBridgeV2\docs
```

Expected: hits in module docs, architecture index, API reference, and this implementation plan.

- [ ] **Step 3: Run focused tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_storage_manager_durable_backend.py tests\youtubebridge_v2\test_storage.py tests\youtubebridge_v2\test_runtime_memoria_runners.py tests\youtubebridge_v2\test_server_api_surface.py tests\youtubebridge_v2\test_main_app_security.py tests\youtubebridge_v2\test_real_storage_integration.py tests\youtubebridge_v2\test_presentation_tts.py -q
```

Expected: all selected tests pass.

- [ ] **Step 4: Run full V2 suite**

Run:

```powershell
python -m pytest tests\youtubebridge_v2 -q
```

Expected: full V2 suite passes. Existing opt-in browser smoke remains skipped unless explicitly enabled.

- [ ] **Step 5: Check whitespace**

Run:

```powershell
git diff --check
```

Expected: exit code 0. Existing LF/CRLF warnings are acceptable if no whitespace errors are reported.

- [ ] **Step 6: Inspect scope and commit**

Run:

```powershell
git status --short
git diff --stat
```

Expected: changed files are limited to 6D storage/runtime/API/tests/docs and this plan.

Commit:

```powershell
git add core\storage\youtube_bridge_v2.py YouTubeBridgeV2\storage\repositories.py YouTubeBridgeV2\storage\runtime_store.py YouTubeBridgeV2\query_service.py YouTubeBridgeV2\runtime\memoria_runners.py YouTubeBridgeV2\server\routes.py YouTubeBridgeV2\server\security.py YouTubeBridgeV2\server\main_security.py tests\youtubebridge_v2\fakes.py tests\youtubebridge_v2\test_storage_manager_durable_backend.py tests\youtubebridge_v2\test_storage.py tests\youtubebridge_v2\test_runtime_memoria_runners.py tests\youtubebridge_v2\test_server_api_surface.py tests\youtubebridge_v2\test_main_app_security.py tests\youtubebridge_v2\test_real_storage_integration.py YouTubeBridgeV2\docs\modules\presentation-tts.md YouTubeBridgeV2\docs\modules\storage.md YouTubeBridgeV2\docs\modules\server-api-surface.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\implementation-plans\tts-queue-ack-timeout-integration.md
git commit -m "feat: integrate TTS delivery queue"
```

---

## Self-Review

- Spec coverage: The plan covers 6D queue, ack, and timeout integration using durable storage, runtime enqueue, and API routes. It does not cover 6E display+TTS E2E verification, real TTS provider delivery, browser ack callbacks, or provider retries.
- Placeholder scan: No `TBD`, `TODO`, or open-ended placeholders remain. The only conditional note is to reuse existing plan helper names in `test_real_storage_integration.py`; the required test body and assertions are explicit.
- Type consistency: Delivery records use `delivery_id`, `event_id`, `character_id`, `text`, `voice_id`, `provider`, `queue_position`, `status`, `metadata`, `acknowledged_at`, and `timeout_seconds` across storage, query, routes, runtime, and tests.
