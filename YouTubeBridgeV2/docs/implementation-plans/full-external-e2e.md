# Full External E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a skipped-by-default full external E2E harness that can drive YouTubeBridgeV2 through real MemoriaCore HTTP transport, durable V2 storage, runtime tick, display stream, and TTS delivery state when explicit external environment variables are provided.

**Architecture:** Existing `test_memoria_real_integration.py` verifies the raw Memoria HTTP adapter in isolation. This item adds a V2-level external harness that composes `MemoriaPlannedShowRunner` with `MemoriaSyncHttpTransport`, creates a real V2 app over temporary `StorageManager` paths, runs create/plan/tick, then verifies display stream and TTS queue/ack/timeout. The test is opt-in via a new `YB2_FULL_EXTERNAL_E2E=1` gate and never calls external services during the default suite.

**Tech Stack:** pytest, FastAPI TestClient, real `StorageManager` temp DB, `MemoriaSyncHttpTransport`, `MemoriaPlannedShowRunner`, existing display/TTS API contracts, docs/API index updates.

---

## Scope Boundary

- Implement only `Final Hardening / full external E2E`.
- Do not implement YouTube Data API polling, real TTS provider, browser audio playback, or startup/shutdown validation.
- Do not change runtime behavior to make the harness pass; if behavior is missing, add a failing test and fix only that boundary.
- Do not edit `YouTubeBridgeV2/docs/roadmap.md` checkbox state.
- External MemoriaCore calls require all explicit opt-in env vars; default pytest must skip external calls.
- YouTube live chat is represented by existing V2 API/runtime contracts in this item; true live YouTube polling remains outside this checklist item unless credentials and scheduler scope are explicitly introduced later.

## File Structure

- Create `tests/youtubebridge_v2/test_full_external_e2e.py`
  - Owns opt-in env parsing, skip behavior, and the full external V2 harness.
- Modify `YouTubeBridgeV2/docs/architecture-index.md`
  - Add Final Hardening external E2E status note.
- Modify `YouTubeBridgeV2/docs/api-reference-index.md`
  - Reference the new external E2E harness and env contract.
- Modify `YouTubeBridgeV2/docs/modules/memoria-adapter.md`
  - Distinguish adapter-level real integration from full V2 external E2E.

---

### Task 1: External E2E Settings and Skip Contract

**Files:**
- Create: `tests/youtubebridge_v2/test_full_external_e2e.py`

- [ ] **Step 1: Add settings parser and default skip tests**

Create `tests/youtubebridge_v2/test_full_external_e2e.py` with this initial content:

```python
from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping

import pytest
from fastapi.testclient import TestClient

from core.storage_manager import StorageManager
from YouTubeBridgeV2.adapters.memoria_http import (
    MemoriaHttpTransportConfig,
    MemoriaSyncHttpTransport,
)
from YouTubeBridgeV2.app import create_v2_app
from YouTubeBridgeV2.composition import create_v2_composition
from YouTubeBridgeV2.runtime.memoria_runners import MemoriaPlannedShowRunner


_TRUE_VALUES = {"1", "true", "yes", "on"}
STARTED_AT = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class FullExternalE2ESettings:
    enabled: bool
    memoria_base_url: str | None
    memoria_api_key: str | None = field(default=None, repr=False)
    character_id: str | None = None
    session_id: str = "yb2-full-external-e2e"
    user_id: str = "__youtube_live_external_e2e__"
    timeout_seconds: float = 10.0
    max_attempts: int = 1

    def transport_config(self) -> MemoriaHttpTransportConfig:
        if self.memoria_base_url is None:
            raise ValueError("YB2_EXTERNAL_MEMORIA_BASE_URL is required")
        return MemoriaHttpTransportConfig(
            base_url=self.memoria_base_url,
            api_key=self.memoria_api_key,
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.max_attempts,
        )


def _settings_from_env(env: Mapping[str, str]) -> FullExternalE2ESettings:
    return FullExternalE2ESettings(
        enabled=_enabled(env.get("YB2_FULL_EXTERNAL_E2E")),
        memoria_base_url=_optional_env(
            env.get("YB2_EXTERNAL_MEMORIA_BASE_URL")
            or env.get("YB2_MEMORIA_BASE_URL")
        ),
        memoria_api_key=_optional_env(
            env.get("YB2_EXTERNAL_MEMORIA_API_KEY")
            or env.get("YB2_MEMORIA_API_KEY")
        ),
        character_id=_optional_env(
            env.get("YB2_EXTERNAL_MEMORIA_CHARACTER_ID")
            or env.get("YB2_MEMORIA_CHARACTER_ID")
        ),
        session_id=_optional_env(env.get("YB2_FULL_EXTERNAL_SESSION_ID"))
        or "yb2-full-external-e2e",
        user_id=_optional_env(env.get("YB2_FULL_EXTERNAL_USER_ID"))
        or "__youtube_live_external_e2e__",
        timeout_seconds=_float_env(env.get("YB2_FULL_EXTERNAL_TIMEOUT_SECONDS"), 10.0),
        max_attempts=_int_env(env.get("YB2_FULL_EXTERNAL_MAX_ATTEMPTS"), 1),
    )


def _require_enabled_settings(settings: FullExternalE2ESettings) -> FullExternalE2ESettings:
    if not settings.enabled:
        pytest.skip("set YB2_FULL_EXTERNAL_E2E=1 to run full external V2 E2E")
    if settings.memoria_base_url is None:
        pytest.skip("set YB2_EXTERNAL_MEMORIA_BASE_URL or YB2_MEMORIA_BASE_URL")
    if settings.character_id is None:
        pytest.skip("set YB2_EXTERNAL_MEMORIA_CHARACTER_ID or YB2_MEMORIA_CHARACTER_ID")
    return settings


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUE_VALUES


def _optional_env(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _float_env(value: str | None, default: float) -> float:
    if _optional_env(value) is None:
        return default
    return float(str(value))


def _int_env(value: str | None, default: int) -> int:
    if _optional_env(value) is None:
        return default
    return int(str(value))


def _assert_no_secret_payload(value: object) -> None:
    text = repr(value).lower()
    for forbidden in (
        "secret-token",
        "authorization",
        "bearer",
        "raw_payload",
        "access_token",
        "hidden_prompt",
        "raw_topic_pack",
    ):
        assert forbidden not in text


def test_full_external_e2e_settings_default_is_disabled():
    settings = _settings_from_env({})

    assert settings.enabled is False
    assert settings.memoria_base_url is None
    assert settings.character_id is None
    assert settings.session_id == "yb2-full-external-e2e"
    assert settings.user_id == "__youtube_live_external_e2e__"
    assert settings.timeout_seconds == 10.0
    assert settings.max_attempts == 1


def test_full_external_e2e_settings_parse_env_without_secret_repr():
    settings = _settings_from_env(
        {
            "YB2_FULL_EXTERNAL_E2E": "1",
            "YB2_EXTERNAL_MEMORIA_BASE_URL": "http://127.0.0.1:8088",
            "YB2_EXTERNAL_MEMORIA_API_KEY": "secret-token",
            "YB2_EXTERNAL_MEMORIA_CHARACTER_ID": "host-character",
            "YB2_FULL_EXTERNAL_SESSION_ID": "external-session",
            "YB2_FULL_EXTERNAL_USER_ID": "external-user",
            "YB2_FULL_EXTERNAL_TIMEOUT_SECONDS": "3.5",
            "YB2_FULL_EXTERNAL_MAX_ATTEMPTS": "2",
        }
    )

    assert settings.enabled is True
    assert settings.memoria_base_url == "http://127.0.0.1:8088"
    assert settings.memoria_api_key == "secret-token"
    assert settings.character_id == "host-character"
    assert settings.session_id == "external-session"
    assert settings.user_id == "external-user"
    assert settings.timeout_seconds == 3.5
    assert settings.max_attempts == 2
    assert "secret-token" not in repr(settings)
    assert "secret-token" not in repr(settings.transport_config())


def test_full_external_e2e_requires_explicit_opt_in():
    settings = _settings_from_env({})

    with pytest.raises(pytest.skip.Exception, match="YB2_FULL_EXTERNAL_E2E=1"):
        _require_enabled_settings(settings)


def test_full_external_e2e_requires_memoria_endpoint_and_character():
    settings = _settings_from_env({"YB2_FULL_EXTERNAL_E2E": "1"})

    with pytest.raises(pytest.skip.Exception, match="MEMORIA_BASE_URL"):
        _require_enabled_settings(settings)

    settings = _settings_from_env(
        {
            "YB2_FULL_EXTERNAL_E2E": "1",
            "YB2_EXTERNAL_MEMORIA_BASE_URL": "http://127.0.0.1:8088",
        }
    )
    with pytest.raises(pytest.skip.Exception, match="MEMORIA_CHARACTER_ID"):
        _require_enabled_settings(settings)
```

- [ ] **Step 2: Run the settings tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_full_external_e2e.py -q
```

Expected: PASS for settings/skip contract only. No external calls occur.

---

### Task 2: Full External V2 Runtime Harness

**Files:**
- Modify: `tests/youtubebridge_v2/test_full_external_e2e.py`

- [ ] **Step 1: Append V2 app helpers and the opt-in test**

Append this code:

```python
def _storage_manager(tmp_path):
    return StorageManager(
        prefs_file=str(tmp_path / "prefs.json"),
        history_file=str(tmp_path / "history.json"),
        persona_snapshot_db_path=str(tmp_path / "persona_snapshots.db"),
        youtube_bridge_v2_db_path=str(tmp_path / "youtubebridge_v2.db"),
    )


def _create_session_payload(settings: FullExternalE2ESettings) -> dict[str, object]:
    return {
        "command_id": f"{settings.session_id}-create",
        "session_id": settings.session_id,
        "aftertalk_policy": "auto",
        "metadata": {
            "duration_policy": {
                "planned_duration_seconds": 3600,
                "auto_finalize_on_duration": True,
                "aftertalk_requires_remaining_time": True,
            },
            "tts_policy": {
                "enabled": True,
                "provider": "external-e2e",
                "default_voice_id": "external-e2e-fallback",
            },
            "hidden_prompt": "must not leak",
        },
    }


def _plan_payload(settings: FullExternalE2ESettings) -> dict[str, object]:
    return {
        "command_id": f"{settings.session_id}-bind",
        "plan": {
            "plan_id": "plan-full-external-e2e",
            "title": "Full External E2E",
            "raw_topic_pack": "must not leak",
            "turns": [
                {
                    "id": "external-smoke",
                    "purpose": (
                        "Reply briefly to confirm YouTubeBridgeV2 full external "
                        "MemoriaCore transport works."
                    ),
                    "topic_cue": "Full external E2E smoke test.",
                    "speaker_policy": {
                        "type": "fixed",
                        "speaker_ids": [settings.character_id],
                    },
                    "audience_insertion": {
                        "enabled": False,
                        "allow_super_chats": False,
                    },
                    "metadata": {"test_scope": "full_external_e2e"},
                }
            ],
        },
    }


def _sse_payloads(text: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for line in text.splitlines():
        if line.startswith("data: "):
            payloads.append(json.loads(line.removeprefix("data: ")))
    return payloads


@pytest.mark.memoria_integration
def test_full_external_v2_memoria_display_tts_round_trip(tmp_path):
    settings = _require_enabled_settings(_settings_from_env(os.environ))
    storage = _storage_manager(tmp_path)
    transport = MemoriaSyncHttpTransport(settings.transport_config())
    composition = create_v2_composition(
        storage_manager=storage,
        planned_show_runner=MemoriaPlannedShowRunner(storage, transport),
    )
    client = TestClient(create_v2_app(composition, now_provider=lambda: STARTED_AT))

    create_response = client.post("/v2/sessions", json=_create_session_payload(settings))
    bind_response = client.post(
        f"/v2/sessions/{settings.session_id}/plan",
        json=_plan_payload(settings),
    )
    tick_response = client.post(
        f"/v2/sessions/{settings.session_id}/tick",
        json={"command_id": f"{settings.session_id}-tick"},
    )
    events_response = client.get(f"/v2/sessions/{settings.session_id}/events?limit=50")
    queue_response = client.get(f"/v2/sessions/{settings.session_id}/tts-queue")
    with client.stream("GET", f"/v2/sessions/{settings.session_id}/display-stream") as stream:
        stream.read()
        display_events = _sse_payloads(stream.text)

    assert create_response.status_code == 200
    assert bind_response.status_code == 200
    assert tick_response.status_code == 200
    assert tick_response.json()["dispatch"]["status"] == "ok"
    assert events_response.status_code == 200
    assert queue_response.status_code == 200

    character_events = [
        event for event in display_events if event.get("event_type") == "character_response"
    ]
    assert character_events
    response_text = character_events[0]["public_payload"]["response_text"]
    assert isinstance(response_text, str)
    assert response_text.strip()
    queued = queue_response.json()["tts_queue"]
    assert queued
    assert queued[0]["text"] == response_text
    assert queued[0]["status"] == "pending"

    delivery_id = queued[0]["delivery_id"]
    phase_before_ack = client.get(f"/v2/sessions/{settings.session_id}/phase").json()["phase"]
    ack_response = client.post(
        f"/v2/sessions/{settings.session_id}/tts-deliveries/{delivery_id}/ack",
        json={"command_id": f"{settings.session_id}-ack"},
    )
    timeout_response = client.post(
        f"/v2/sessions/{settings.session_id}/tts-deliveries/{delivery_id}/timeout",
        json={"command_id": f"{settings.session_id}-timeout", "timeout_seconds": 30},
    )
    phase_after_timeout = client.get(f"/v2/sessions/{settings.session_id}/phase").json()["phase"]

    assert ack_response.status_code == 200
    assert ack_response.json()["status"] == "delivered"
    assert ack_response.json()["phase_transition_requested"] is False
    assert timeout_response.status_code == 200
    assert timeout_response.json()["phase_transition_requested"] is False
    assert phase_after_timeout == phase_before_ack
    _assert_no_secret_payload(
        (
            create_response.json(),
            bind_response.json(),
            tick_response.json(),
            events_response.json(),
            display_events,
            queue_response.json(),
            ack_response.json(),
            timeout_response.json(),
        )
    )
```

- [ ] **Step 2: Run default test file**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_full_external_e2e.py -q
```

Expected: settings tests pass and external E2E test skips unless `YB2_FULL_EXTERNAL_E2E=1`.

- [ ] **Step 3: Document opt-in command**

Do not run this unless the operator has a real MemoriaCore 8088 foreground server and a valid character id:

```powershell
$env:YB2_FULL_EXTERNAL_E2E='1'
$env:YB2_EXTERNAL_MEMORIA_BASE_URL='http://127.0.0.1:8088'
$env:YB2_EXTERNAL_MEMORIA_CHARACTER_ID='<real-character-id>'
python -m pytest tests\youtubebridge_v2\test_full_external_e2e.py::test_full_external_v2_memoria_display_tts_round_trip -q
```

Expected when configured: PASS. If local MemoriaCore is not running or no character id is provided, report skip/not-run rather than pretending external E2E passed.

---

### Task 3: Documentation and Index Updates

**Files:**
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
- Modify: `YouTubeBridgeV2/docs/modules/memoria-adapter.md`

- [ ] **Step 1: Update architecture status**

Add a Final Hardening status note:

- `Full external E2E harness` is implemented and skipped by default.
- It exercises real MemoriaCore HTTP transport through V2 runtime/display/TTS when env vars are set.
- It does not validate YouTube polling, real TTS provider, startup/shutdown, or PR readiness.

- [ ] **Step 2: Update API reference**

Add concepts/source references:

- `YB2_FULL_EXTERNAL_E2E`
- `YB2_EXTERNAL_MEMORIA_BASE_URL`
- `YB2_EXTERNAL_MEMORIA_CHARACTER_ID`
- `tests/youtubebridge_v2/test_full_external_e2e.py::test_full_external_v2_memoria_display_tts_round_trip`

- [ ] **Step 3: Update Memoria adapter module docs**

Add a note that `test_memoria_real_integration.py` remains adapter-level, while `test_full_external_e2e.py` is V2-level and verifies runtime/display/TTS integration with the real transport.

- [ ] **Step 4: Verify docs references**

Run:

```powershell
rg -n "YB2_FULL_EXTERNAL_E2E|test_full_external_v2_memoria_display_tts_round_trip|Full external E2E" YouTubeBridgeV2\docs
```

Expected: hits in architecture index, API reference, Memoria adapter docs, and this plan.

---

### Task 4: Verification and Commit

**Files:**
- All files above.

- [ ] **Step 1: Run focused tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_full_external_e2e.py tests\youtubebridge_v2\test_memoria_real_integration.py tests\youtubebridge_v2\test_display_tts_e2e.py -q
```

Expected: PASS with external/browser tests skipped by default.

- [ ] **Step 2: Run full V2 suite**

Run:

```powershell
python -m pytest tests\youtubebridge_v2 -q
```

Expected: full V2 suite passes with external/browser tests skipped by default.

- [ ] **Step 3: Check whitespace**

Run:

```powershell
git diff --check
```

Expected: exit code 0. Existing LF/CRLF warnings are acceptable if no whitespace errors are reported.

- [ ] **Step 4: Inspect scope and commit**

Run:

```powershell
git status --short
git diff --stat
```

Expected: changed files are limited to the external E2E harness, docs, and this plan.

Commit:

```powershell
git add tests\youtubebridge_v2\test_full_external_e2e.py YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\modules\memoria-adapter.md YouTubeBridgeV2\docs\implementation-plans\full-external-e2e.md
git commit -m "test: add full external E2E harness"
```

---

## Self-Review

- Spec coverage: Covers only Final Hardening / full external E2E by adding an opt-in external harness with default skip safety. Startup/shutdown validation, legacy audit, docs sync, final code review, and PR readiness remain separate checklist items.
- Placeholder scan: No `TBD`, `TODO`, or open-ended placeholders remain. The opt-in command uses `<real-character-id>` because it must be supplied by the operator at runtime and is not a code placeholder.
- Type consistency: Env names, helper names, route paths, and response fields match existing Memoria transport and V2 runtime/display/TTS contracts.
