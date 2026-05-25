# Display Event Contract Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the Wave 6A display event boundary so `/v2/sessions/{session_id}/display-stream` emits display-safe events that the Chat Display UI can render directly.

**Architecture:** Add a small `YouTubeBridgeV2.display.events` contract module that converts stored/query events into a stable display event envelope. `V2QueryService.iter_display_events(...)` should use that module instead of forwarding raw event-history projections, while FastAPI routes keep the SSE envelope and final display-safe redaction. The scope does not redesign the Chat Display UI or wire full TTS delivery; it only guarantees the event contract that later Wave 6B-6E work consumes.

**Tech Stack:** Python 3, FastAPI SSE `StreamingResponse`, existing V2 storage/query contracts, existing ESM Chat Display UI tests via Node.

---

## File Structure

- Create `YouTubeBridgeV2/display/__init__.py`: public re-export for display event contract helpers.
- Create `YouTubeBridgeV2/display/events.py`: display-safe normalization, redaction, flag mapping, and stable event envelope constants.
- Modify `YouTubeBridgeV2/query_service.py`: use `normalize_display_event(...)` for `iter_display_events(...)`; keep `get_session_events(...)` unchanged for operator/history consumers.
- Modify `YouTubeBridgeV2/server/routes.py`: apply display contract redaction as the final SSE safety layer for display stream payloads.
- Modify `tests/youtubebridge_v2/test_display_event_contract.py`: new contract-level red/green tests for audience, Super Chat, system/closing, ordering metadata, and privacy.
- Modify `tests/youtubebridge_v2/test_server_api_surface.py`: assert display stream emits normalized display event types through route SSE.
- Modify `tests/youtubebridge_v2/test_youtube_ingestion_boundaries.py`: assert fake-backed YouTube ingestion display stream exposes renderable Super Chat display metadata.
- Modify docs: `docs/modules/chat-display-ui.md`, `docs/modules/server-api-surface.md`, `docs/architecture-index.md`, `docs/api-reference-index.md`.

---

### Task 1: Red Tests For Display Contract

**Files:**
- Create: `tests/youtubebridge_v2/test_display_event_contract.py`
- Modify: `tests/youtubebridge_v2/test_server_api_surface.py`
- Modify: `tests/youtubebridge_v2/test_youtube_ingestion_boundaries.py`

- [ ] **Step 1: Add contract tests for stored YouTube display events**

Create `tests/youtubebridge_v2/test_display_event_contract.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from YouTubeBridgeV2.display.events import normalize_display_event, sanitize_display_value
from YouTubeBridgeV2.query_service import V2QueryService


NOW = datetime(2026, 5, 12, 8, 30, tzinfo=timezone.utc)


class FakeStorage:
    def __init__(self):
        self.sessions = {
            "session-1": {
                "session_id": "session-1",
                "current_phase": "planned_show",
                "aftertalk_policy": "auto",
                "plan_completed": False,
                "manual_close_requested": False,
                "closing_completed": False,
            }
        }
        self.events = []

    def get_v2_session(self, session_id):
        return self.sessions.get(session_id)

    def list_v2_live_events(self, session_id, limit):
        return list(self.events[:limit])


def _assert_no_private_payload(value):
    text = repr(value).lower()
    for forbidden in (
        "hidden_prompt",
        "raw_prompt",
        "raw_payload",
        "raw_memoriacore_payload",
        "operator_controls",
        "operator_only",
        "manual_close",
        "access_token",
        "authorization",
        "client_secret",
        "refresh_token",
        "secret-value",
        "must not leak",
    ):
        assert forbidden not in text


def test_normalize_youtube_text_event_uses_display_event_contract():
    event = normalize_display_event(
        {
            "event_id": "yt-evt-1",
            "event_type": "youtube_text_message",
            "created_at": NOW,
            "public_metadata": {
                "public_payload": {
                    "message_text": "Hello runtime",
                    "author_display_name": "Mika",
                    "raw_payload": {"access_token": "must not leak"},
                },
                "display_event": {
                    "event_id": "yt-evt-1",
                    "event_type": "audience_message",
                    "author_display_name": "Mika",
                    "message_text": "Hello runtime",
                    "published_at": "2026-05-12T08:10:00Z",
                    "author_badges": ["moderator", "unknown_badge"],
                    "operator_controls": {"manual_close": True},
                },
            },
        }
    )

    assert event == {
        "display_contract_version": "v1",
        "event_id": "yt-evt-1",
        "event_type": "audience_message",
        "source_event_type": "youtube_text_message",
        "created_at": NOW.isoformat(),
        "public_payload": {
            "author_display_name": "Mika",
            "message_text": "Hello runtime",
            "timestamp": "2026-05-12T08:10:00Z",
            "display_flags": {"moderator": True},
        },
    }
    _assert_no_private_payload(event)


def test_normalize_youtube_super_chat_flattens_public_amount_metadata():
    event = normalize_display_event(
        {
            "event_id": "sc-1",
            "event_type": "youtube_super_chat",
            "public_metadata": {
                "display_event": {
                    "event_id": "sc-1",
                    "event_type": "super_chat",
                    "author_display_name": "Rin",
                    "message_text": "Great stream",
                    "published_at": "2026-05-12T08:20:00Z",
                    "author_badges": ["member"],
                    "super_chat": {
                        "amount_display_string": "NT$150",
                        "currency": "TWD",
                        "acknowledgement_status": "pending",
                        "raw_payload": {"authorization": "Bearer secret-value"},
                    },
                }
            },
        }
    )

    assert event["event_type"] == "super_chat"
    assert event["public_payload"] == {
        "author_display_name": "Rin",
        "message_text": "Great stream",
        "timestamp": "2026-05-12T08:20:00Z",
        "amount_display_string": "NT$150",
        "currency": "TWD",
        "acknowledgement_status": "pending",
        "display_flags": {"member": True},
    }
    _assert_no_private_payload(event)


def test_normalize_runtime_event_becomes_system_state():
    event = normalize_display_event(
        {
            "event_id": "runtime-1",
            "event_type": "runtime_action_dispatched",
            "public_metadata": {
                "phase": "aftertalk",
                "payload": {"summary": {"message": "aftertalk started"}},
                "operator_controls": {"manual_close": True},
            },
        }
    )

    assert event == {
        "display_contract_version": "v1",
        "event_id": "runtime-1",
        "event_type": "system_state",
        "source_event_type": "runtime_action_dispatched",
        "public_payload": {
            "phase": "aftertalk",
            "message": "aftertalk started",
            "status": "runtime_action_dispatched",
        },
    }
    _assert_no_private_payload(event)


def test_query_service_display_stream_yields_normalized_display_events():
    storage = FakeStorage()
    storage.events.append(
        {
            "event_id": "yt-evt-1",
            "event_type": "youtube_text_message",
            "public_metadata": {
                "display_event": {
                    "event_id": "yt-evt-1",
                    "event_type": "audience_message",
                    "author_display_name": "Mika",
                    "message_text": "Hello display",
                },
            },
        }
    )

    events = list(V2QueryService(storage).iter_display_events("session-1"))

    assert events[0]["event_type"] == "audience_message"
    assert events[0]["public_payload"]["message_text"] == "Hello display"
    assert events[0]["source_event_type"] == "youtube_text_message"


def test_sanitize_display_value_removes_nested_private_key_patterns():
    sanitized = sanitize_display_value(
        {
            "safe": "visible",
            "client_secret": "must not leak",
            "nested": {
                "refresh_token": "must not leak",
                "operator_only_metadata": {"manual_close": True},
                "text": "Bearer secret-value",
            },
        }
    )

    assert sanitized == {"safe": "visible", "nested": {"text": "[redacted]"}}
    _assert_no_private_payload(sanitized)
```

- [ ] **Step 2: Update route-level SSE test expectations**

In `tests/youtubebridge_v2/test_server_api_surface.py`, modify `FakeQueryService.iter_display_events(...)` to return a query-like raw stored event that exercises route final sanitization:

```python
    def iter_display_events(self, session_id):
        self.calls.append(("iter_display_events", session_id))
        return iter(
            [
                {
                    "display_contract_version": "v1",
                    "event_id": "display-1",
                    "event_type": "audience_message",
                    "source_event_type": "youtube_text_message",
                    "public_payload": {
                        "author_display_name": "Mika",
                        "message_text": "visible",
                        "display_flags": {"moderator": True},
                        "diagnostics": {"operator_only": True},
                        "operator_controls": {"manual_close": True},
                    },
                }
            ]
        )
```

Then update `test_display_stream_emits_display_safe_events`:

```python
    assert "audience_message" in text
    assert "youtube_text_message" in text
    assert "visible" in text
    assert "moderator" in text
    assert "diagnostics" not in text
    assert "operator_controls" not in text
```

- [ ] **Step 3: Add fake-backed Super Chat display-stream assertion**

In `tests/youtubebridge_v2/test_youtube_ingestion_boundaries.py`, after reading `display_text` in `test_fake_backed_youtube_super_chat_ingestion_is_public_safe_across_reads`, assert:

```python
    assert '"event_type": "super_chat"' in display_text
    assert '"source_event_type": "youtube_super_chat"' in display_text
    assert "NT$150" in display_text
    assert "pending" in display_text
```

- [ ] **Step 4: Run red tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_display_event_contract.py tests\youtubebridge_v2\test_server_api_surface.py::test_display_stream_emits_display_safe_events tests\youtubebridge_v2\test_youtube_ingestion_boundaries.py::test_fake_backed_youtube_super_chat_ingestion_is_public_safe_across_reads -q
```

Expected before implementation: import failure for `YouTubeBridgeV2.display.events` or assertions showing display stream still emits raw event-history shape.

---

### Task 2: Implement Display Event Contract Module

**Files:**
- Create: `YouTubeBridgeV2/display/__init__.py`
- Create: `YouTubeBridgeV2/display/events.py`
- Modify: `YouTubeBridgeV2/query_service.py`
- Modify: `YouTubeBridgeV2/server/routes.py`

- [ ] **Step 1: Add public re-export**

Create `YouTubeBridgeV2/display/__init__.py`:

```python
"""Display-safe event contracts for YouTubeBridgeV2."""

from YouTubeBridgeV2.display.events import (
    DISPLAY_CONTRACT_VERSION,
    normalize_display_event,
    sanitize_display_value,
)

__all__ = [
    "DISPLAY_CONTRACT_VERSION",
    "normalize_display_event",
    "sanitize_display_value",
]
```

- [ ] **Step 2: Add minimal display event normalizer**

Create `YouTubeBridgeV2/display/events.py` with:

```python
"""Display-safe event normalization for YouTubeBridgeV2."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping


DISPLAY_CONTRACT_VERSION = "v1"


def normalize_display_event(event: object) -> dict[str, object]:
    """Return one display-safe event envelope consumable by Chat Display UI."""

    raw_event = sanitize_display_value(_object_to_dict(event))
    source_event_type = _safe_text(raw_event.get("event_type"))
    event_id = _safe_text(raw_event.get("event_id") or raw_event.get("id"))
    created_at = _iso_text(raw_event.get("created_at") or raw_event.get("createdAt"))
    public_metadata = _object_to_dict(
        raw_event.get("public_metadata")
        or raw_event.get("public_payload")
        or raw_event.get("payload")
        or {}
    )
    display_event = _object_to_dict(public_metadata.get("display_event"))
    if not display_event:
        display_event = _object_to_dict(raw_event.get("display_event"))
    if not display_event and _is_display_event_type(source_event_type):
        display_event = {
            **public_metadata,
            "event_type": source_event_type,
            "event_id": event_id,
        }
    if display_event:
        return _display_event_envelope(
            display_event,
            event_id=event_id,
            source_event_type=source_event_type,
            created_at=created_at,
        )
    return _system_state_envelope(
        public_metadata,
        event_id=event_id,
        source_event_type=source_event_type,
        created_at=created_at,
    )


def sanitize_display_value(value: Any) -> Any:
    """Remove display-forbidden keys and redact display-forbidden text."""

    if isinstance(value, dict):
        return {
            str(key): sanitize_display_value(inner_value)
            for key, inner_value in value.items()
            if not _is_forbidden_key(key)
        }
    if isinstance(value, list):
        return [sanitize_display_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_display_value(item) for item in value)
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value
```

Then add the helper functions shown below in the same file:

```python
def _display_event_envelope(
    display_event: dict[str, object],
    *,
    event_id: str,
    source_event_type: str,
    created_at: str,
) -> dict[str, object]:
    display_type = _normalize_display_type(display_event.get("event_type"))
    if display_type == "super_chat":
        payload = _super_chat_payload(display_event)
    elif display_type == "character_response":
        payload = _character_response_payload(display_event)
    elif display_type in {"system_state", "closing_status", "aftertalk_status"}:
        payload = _system_payload(display_event, fallback_status=display_type)
        display_type = "system_state" if display_type != "closing_status" else "closing_status"
    else:
        display_type = "audience_message"
        payload = _audience_payload(display_event)
    return _envelope(
        event_type=display_type,
        event_id=_safe_text(display_event.get("event_id") or event_id),
        source_event_type=source_event_type,
        created_at=created_at,
        public_payload=payload,
    )


def _system_state_envelope(
    metadata: dict[str, object],
    *,
    event_id: str,
    source_event_type: str,
    created_at: str,
) -> dict[str, object]:
    return _envelope(
        event_type="system_state",
        event_id=event_id,
        source_event_type=source_event_type,
        created_at=created_at,
        public_payload=_system_payload(metadata, fallback_status=source_event_type),
    )


def _envelope(
    *,
    event_type: str,
    event_id: str,
    source_event_type: str,
    created_at: str,
    public_payload: dict[str, object],
) -> dict[str, object]:
    envelope: dict[str, object] = {
        "display_contract_version": DISPLAY_CONTRACT_VERSION,
        "event_id": event_id,
        "event_type": event_type,
        "source_event_type": source_event_type,
        "public_payload": sanitize_display_value(public_payload),
    }
    if created_at:
        envelope["created_at"] = created_at
    return sanitize_display_value(envelope)


def _audience_payload(values: Mapping[str, object]) -> dict[str, object]:
    return {
        "author_display_name": _safe_text(values.get("author_display_name") or values.get("authorDisplayName")),
        "message_text": _safe_text(values.get("message_text") or values.get("messageText") or values.get("text")),
        "timestamp": _safe_text(values.get("timestamp") or values.get("published_at") or values.get("publishedAt")),
        "display_flags": _display_flags(values),
    }


def _super_chat_payload(values: Mapping[str, object]) -> dict[str, object]:
    super_chat = _object_to_dict(values.get("super_chat") or values.get("superChat"))
    return {
        **_audience_payload(values),
        "amount_display_string": _safe_text(
            values.get("amount_display_string")
            or values.get("amountDisplayString")
            or super_chat.get("amount_display_string")
            or super_chat.get("amountDisplayString")
            or values.get("amount")
        ),
        "currency": _safe_text(values.get("currency") or super_chat.get("currency")),
        "acknowledgement_status": _safe_text(
            values.get("acknowledgement_status")
            or values.get("acknowledgementStatus")
            or super_chat.get("acknowledgement_status")
            or super_chat.get("acknowledgementStatus")
        ),
    }


def _character_response_payload(values: Mapping[str, object]) -> dict[str, object]:
    return {
        "character_name": _safe_text(values.get("character_name") or values.get("characterName") or values.get("speaker_name")),
        "role_label": _safe_text(values.get("role_label") or values.get("roleLabel") or values.get("role")),
        "response_text": _safe_text(values.get("response_text") or values.get("responseText") or values.get("message_text") or values.get("text")),
        "phase": _safe_text(values.get("phase")),
        "presentation": sanitize_display_value(values.get("presentation") or values.get("presentation_metadata") or {}),
    }


def _system_payload(values: Mapping[str, object], *, fallback_status: str) -> dict[str, object]:
    payload = _object_to_dict(values.get("payload"))
    summary = _object_to_dict(values.get("summary"))
    nested_summary = _object_to_dict(payload.get("summary"))
    public_summary = _object_to_dict(values.get("public_summary") or values.get("publicSummary"))
    message = _safe_text(
        values.get("message")
        or public_summary.get("message")
        or nested_summary.get("message")
        or summary.get("message")
        or fallback_status
    )
    phase = _safe_text(values.get("phase") or payload.get("phase") or public_summary.get("phase"))
    return {
        "phase": phase or "unknown",
        "message": message,
        "status": _safe_text(values.get("status") or public_summary.get("status") or fallback_status),
    }
```

Add the remaining helpers:

```python
def _display_flags(values: Mapping[str, object]) -> dict[str, bool]:
    raw_flags = values.get("display_flags") or values.get("flags") or values.get("author_badges") or []
    if isinstance(raw_flags, dict):
        keys = [key for key, value in raw_flags.items() if value is True or value == "true" or value == 1]
    elif isinstance(raw_flags, (list, tuple, set)):
        keys = list(raw_flags)
    else:
        keys = []
    flags: dict[str, bool] = {}
    for key in keys:
        normalized = _normalize_flag_key(key)
        if normalized in _DISPLAY_FLAG_ALLOWLIST:
            flags[normalized] = True
    return flags


def _is_display_event_type(value: object) -> bool:
    return _normalize_display_type(value) in {
        "audience_message",
        "character_response",
        "super_chat",
        "system_state",
        "closing_status",
        "aftertalk_status",
    }


def _normalize_display_type(value: object) -> str:
    text = _safe_text(value).lower()
    mapping = {
        "display_message": "audience_message",
        "display_character_response": "character_response",
        "display_super_chat": "super_chat",
        "phase_update": "system_state",
    }
    return mapping.get(text, text)


def _normalize_flag_key(value: object) -> str:
    return _safe_text(value).lower().replace("-", "_").replace(" ", "_")


def _object_to_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return {}


def _iso_text(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return _safe_text(value)


def _safe_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    lowered = text.lower()
    if any(pattern in lowered for pattern in _FORBIDDEN_TEXT_PATTERNS):
        return "[redacted]"
    return text


def _is_forbidden_key(key: object) -> bool:
    lowered = str(key).lower()
    return lowered in _FORBIDDEN_KEYS or any(pattern in lowered for pattern in _FORBIDDEN_KEY_PATTERNS)


_DISPLAY_FLAG_ALLOWLIST = {
    "held_for_review",
    "highlighted",
    "member",
    "moderator",
    "paid_member",
    "pinned",
    "verified",
}

_FORBIDDEN_KEYS = {
    "access_token",
    "authorization",
    "diagnostics",
    "headers",
    "hidden_prompt",
    "operator_controls",
    "operator_only",
    "operator_only_metadata",
    "password",
    "raw_adapter_payload",
    "raw_fact_card",
    "raw_fact_cards",
    "raw_factcard",
    "raw_memoriacore_payload",
    "raw_payload",
    "raw_prompt",
    "raw_super_chat",
    "raw_super_chat_payload",
    "raw_topic_pack",
    "secret",
    "token",
    "topic_pack",
    "topic_pack_fact_cards",
    "youtube_raw",
}

_FORBIDDEN_KEY_PATTERNS = (
    "api-key",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "credential",
    "manual_close",
    "operator_only",
    "refresh_token",
    "secret",
    "token",
)

_FORBIDDEN_TEXT_PATTERNS = (
    "authorization:",
    "bearer ",
    "basic ",
    "client_secret",
    "refresh_token",
    "x-api-key",
)

__all__ = [
    "DISPLAY_CONTRACT_VERSION",
    "normalize_display_event",
    "sanitize_display_value",
]
```

- [ ] **Step 3: Wire query service display stream**

In `YouTubeBridgeV2/query_service.py`, import the helper:

```python
from YouTubeBridgeV2.display.events import normalize_display_event
```

Change `iter_display_events(...)` from using `get_session_events(...)` to raw event records:

```python
    def iter_display_events(self, session_id: str) -> Iterable[dict[str, object]]:
        """產生 display-safe SSE event。"""

        self._session_record(session_id)
        for event in self._events(session_id, 100):
            yield normalize_display_event(event)
```

- [ ] **Step 4: Wire route display-safe final pass**

In `YouTubeBridgeV2/server/routes.py`, import:

```python
from YouTubeBridgeV2.display.events import sanitize_display_value
```

Change `_display_safe_payload(...)`:

```python
def _display_safe_payload(event: object) -> object:
    return sanitize_display_value(event)
```

Keep `_sanitize_public_payload(...)` for non-display API/operator paths.

- [ ] **Step 5: Run red tests again and make them pass**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_display_event_contract.py tests\youtubebridge_v2\test_server_api_surface.py::test_display_stream_emits_display_safe_events tests\youtubebridge_v2\test_youtube_ingestion_boundaries.py::test_fake_backed_youtube_super_chat_ingestion_is_public_safe_across_reads -q
```

Expected after implementation: all selected tests pass.

---

### Task 3: Chat Display Compatibility Regression

**Files:**
- Modify: `tests/youtubebridge_v2/test_chat_display_ui.py`
- Modify: `YouTubeBridgeV2/static/chat-display/chat-display.js` only if the new test fails.

- [ ] **Step 1: Add a UI regression for normalized stream events**

In `tests/youtubebridge_v2/test_chat_display_ui.py`, add:

```python
def test_chat_display_renders_normalized_display_contract_super_chat():
    result = _node_eval(
        """
const html = renderDisplayEvent({
  display_contract_version: "v1",
  event_type: "super_chat",
  source_event_type: "youtube_super_chat",
  public_payload: {
    author_display_name: "Rin",
    message_text: "Great stream",
    amount_display_string: "NT$150",
    currency: "TWD",
    acknowledgement_status: "pending",
    display_flags: {member: true},
    raw_payload: {authorization: "Bearer secret-value"}
  }
});
console.log(JSON.stringify({html}));
"""
    )

    assert 'data-testid="super-chat"' in result["html"]
    assert "Rin" in result["html"]
    assert "NT$150" in result["html"]
    assert "pending" in result["html"]
    assert "Member" in result["html"]
    assert "raw_payload" not in result["html"]
    assert "secret-value" not in result["html"]
```

- [ ] **Step 2: Run the UI regression**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_chat_display_ui.py::test_chat_display_renders_normalized_display_contract_super_chat -q
```

Expected: pass if the current renderer already consumes flattened contract payloads. If it fails only because Super Chat does not render flags, decide conservatively whether to add flag rendering to `DisplaySuperChatEvent.render()` or narrow the assertion to the 6A contract fields. Do not redesign styling in this item.

- [ ] **Step 3: Apply minimal JS compatibility only if needed**

If the test fails because `DisplaySuperChatEvent` does not carry `display_flags`, change its constructor in `YouTubeBridgeV2/static/chat-display/chat-display.js`:

```javascript
    this.flags = sanitizePublicValue(values.flags || values.display_flags || {});
```

Then add `${renderDisplayFlags(this.flags)}` in the `.row-meta` block next to acknowledgement status.

- [ ] **Step 4: Run focused Chat Display tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_chat_display_ui.py -q
```

Expected: all Chat Display tests pass.

---

### Task 4: Documentation And Verification

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/chat-display-ui.md`
- Modify: `YouTubeBridgeV2/docs/modules/server-api-surface.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] **Step 1: Update module docs**

In `docs/modules/chat-display-ui.md`, add a short Wave 6A status note under Public Entrypoints:

```markdown
Wave 6A adds a server-side display event contract: display stream consumers receive `display_contract_version: "v1"` events with `event_type` normalized to `audience_message`, `character_response`, `super_chat`, `system_state`, or `closing_status`. Raw storage/operator fields remain outside the display contract.
```

In `docs/modules/server-api-surface.md`, add under Endpoint Boundary Rules:

```markdown
Display stream events pass through the Wave 6A display contract normalizer before SSE encoding. Event history remains a public audit projection; display stream is the renderable projection.
```

- [ ] **Step 2: Update architecture and API index**

In `docs/architecture-index.md`, update the Wave 6 / Chat Display section to mention 6A display contract normalization and `YouTubeBridgeV2/display/events.py`.

In `docs/api-reference-index.md`, update:

- Query Service section: `iter_display_events` emits normalized display contract events.
- Chat Display UI section: `display_contract_version: "v1"` event envelope.
- Add sources for `YouTubeBridgeV2/display/events.py::normalize_display_event` and `sanitize_display_value`.

- [ ] **Step 3: Run Wave 6A focused verification**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_display_event_contract.py tests\youtubebridge_v2\test_chat_display_ui.py tests\youtubebridge_v2\test_server_api_surface.py::test_display_stream_emits_display_safe_events tests\youtubebridge_v2\test_youtube_ingestion_boundaries.py::test_fake_backed_youtube_super_chat_ingestion_is_public_safe_across_reads -q
```

Expected: all selected tests pass.

- [ ] **Step 4: Run roadmap-required verification**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_chat_display_ui.py -q
python -m pytest tests\youtubebridge_v2\test_presentation_tts.py -q
python -m pytest tests\youtubebridge_v2 -q
git diff --check
```

Expected: full V2 suite passes; real/opt-in integration tests remain skipped by default; `git diff --check` reports no whitespace errors.

---

## Self-Review

- Spec coverage: 6A covers display event contract hardening only. It does not implement new visual layout, browser smoke, presentation/TTS delivery queue wiring, or full E2E because those are 6B-6E.
- Privacy coverage: the new display module removes nested operator controls, raw payloads, token/secret key patterns, and forbidden auth-like text before route SSE.
- Boundary coverage: query service still owns read projection; server routes still own SSE envelope only; UI remains display-only and does not call control APIs.
- Type consistency: all new events use `display_contract_version`, `event_id`, `event_type`, `source_event_type`, optional `created_at`, and `public_payload`.
