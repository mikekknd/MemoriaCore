# Memoria Real Integration Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 roadmap `2E-C`：建立可明確 opt-in 的本機 MemoriaCore real HTTP integration test harness，預設 CI/pytest 不依賴外部服務。

**Architecture:** 只新增 test harness 與 docs，不接 production wiring toggle。Integration test 直接使用既有 `MemoriaSyncHttpTransport`、`build_memoria_request(...)`、`normalize_memoria_response(...)`，並透過 `YB2_MEMORIA_INTEGRATION=1` 加上必要 env vars 才會嘗試呼叫本機 MemoriaCore。預設執行完整 V2 pytest 時，real external test 會以明確 skip reason 跳過。

**Tech Stack:** Python 3.12+、pytest、環境變數 opt-in、既有 stdlib urllib-backed `MemoriaSyncHttpTransport`。

---

## Scope

Roadmap item：`2E-C：real MemoriaCore integration test harness`

完成條件：

- 可用明確 env opt-in 跑本機 MemoriaCore `/api/v1/chat/sync` integration。
- 預設 `python -m pytest tests\youtubebridge_v2 -q` 不需要 MemoriaCore、8088、API key 或 character fixture。
- Opt-in harness 使用既有 V2 adapter/transport contract，不新增 production auto-wiring。
- Harness 文件列出 Windows PowerShell 執行方式、必要 env、可選 env、skip 行為與安全邊界。

不包含：

- `2E-D` production wiring toggle。
- 自動啟動 8088 或修改 `start.bat`。
- 依賴固定本機角色資料或硬寫 API key。
- YouTube polling、TTS、background scheduler。

## File Structure

- Modify: `pytest.ini`
  - 註冊 `memoria_integration` marker，避免 unknown marker noise。
- Create: `tests/youtubebridge_v2/test_memoria_real_integration.py`
  - 包含 harness settings parser unit tests。
  - 包含預設 skip 的 real MemoriaCore round-trip test。
  - 只在 `YB2_MEMORIA_INTEGRATION=1` 且 `YB2_MEMORIA_BASE_URL` / `YB2_MEMORIA_CHARACTER_ID` 都存在時外呼。
- Modify: `YouTubeBridgeV2/docs/modules/memoria-adapter.md`
  - 補 integration harness 的 opt-in env contract 與預設 skip 邊界。
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
  - 補 Wave 2E-C 狀態，明確 production toggle 仍留給 2E-D。

## Environment Contract

Required for external call:

- `YB2_MEMORIA_INTEGRATION=1`
- `YB2_MEMORIA_BASE_URL=http://127.0.0.1:8088`
- `YB2_MEMORIA_CHARACTER_ID=<existing MemoriaCore character id>`

Optional:

- `YB2_MEMORIA_API_KEY=<token>`：若本機 MemoriaCore 需要 bearer auth。
- `YB2_MEMORIA_USER_ID=__youtube_live_integration__`
- `YB2_MEMORIA_SESSION_ID=yb2-integration-session`
- `YB2_MEMORIA_TIMEOUT_SECONDS=10`
- `YB2_MEMORIA_MAX_ATTEMPTS=1`

---

### Task 1: Harness Settings Red Tests

**Files:**
- Modify: `pytest.ini`
- Create: `tests/youtubebridge_v2/test_memoria_real_integration.py`

- [ ] **Step 1: Register pytest marker**

Add under `markers =` in `pytest.ini`:

```ini
    memoria_integration: opt-in real MemoriaCore HTTP integration tests
```

- [ ] **Step 2: Add red tests for env parsing and skip guard**

Create `tests/youtubebridge_v2/test_memoria_real_integration.py` with only these imports and tests first:

```python
from __future__ import annotations

import pytest


def test_memoria_real_integration_settings_default_is_disabled():
    settings = _settings_from_env({})

    assert settings.enabled is False
    assert settings.base_url is None
    assert settings.character_id is None
    assert settings.user_id == "__youtube_live_integration__"
    assert settings.session_id == "yb2-integration-session"
    assert settings.timeout_seconds == 10.0
    assert settings.max_attempts == 1


def test_memoria_real_integration_settings_parse_explicit_env_without_secret_repr():
    settings = _settings_from_env(
        {
            "YB2_MEMORIA_INTEGRATION": "1",
            "YB2_MEMORIA_BASE_URL": "http://127.0.0.1:8088",
            "YB2_MEMORIA_API_KEY": "secret-token",
            "YB2_MEMORIA_CHARACTER_ID": "host-character",
            "YB2_MEMORIA_USER_ID": "integration-user",
            "YB2_MEMORIA_SESSION_ID": "integration-session",
            "YB2_MEMORIA_TIMEOUT_SECONDS": "3.5",
            "YB2_MEMORIA_MAX_ATTEMPTS": "2",
        }
    )

    assert settings.enabled is True
    assert settings.base_url == "http://127.0.0.1:8088"
    assert settings.api_key == "secret-token"
    assert settings.character_id == "host-character"
    assert settings.user_id == "integration-user"
    assert settings.session_id == "integration-session"
    assert settings.timeout_seconds == 3.5
    assert settings.max_attempts == 2
    assert "secret-token" not in repr(settings.transport_config())
    assert settings.transport_config().public_summary() == {
        "base_url": "http://127.0.0.1:8088",
        "timeout_seconds": 3.5,
        "max_attempts": 2,
        "has_api_key": True,
    }


def test_memoria_real_integration_requires_opt_in_before_external_call():
    settings = _settings_from_env({})

    with pytest.raises(pytest.skip.Exception, match="YB2_MEMORIA_INTEGRATION=1"):
        _require_enabled_settings(settings)


def test_memoria_real_integration_requires_base_url_and_character_id():
    settings = _settings_from_env({"YB2_MEMORIA_INTEGRATION": "1"})

    with pytest.raises(pytest.skip.Exception, match="YB2_MEMORIA_BASE_URL"):
        _require_enabled_settings(settings)

    settings = _settings_from_env(
        {
            "YB2_MEMORIA_INTEGRATION": "1",
            "YB2_MEMORIA_BASE_URL": "http://127.0.0.1:8088",
        }
    )

    with pytest.raises(pytest.skip.Exception, match="YB2_MEMORIA_CHARACTER_ID"):
        _require_enabled_settings(settings)
```

- [ ] **Step 3: Run red tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_real_integration.py::test_memoria_real_integration_settings_default_is_disabled tests\youtubebridge_v2\test_memoria_real_integration.py::test_memoria_real_integration_settings_parse_explicit_env_without_secret_repr tests\youtubebridge_v2\test_memoria_real_integration.py::test_memoria_real_integration_requires_opt_in_before_external_call tests\youtubebridge_v2\test_memoria_real_integration.py::test_memoria_real_integration_requires_base_url_and_character_id -q
```

Expected: FAIL with `NameError: name '_settings_from_env' is not defined`.

---

### Task 2: Harness Settings Green Implementation

**Files:**
- Modify: `tests/youtubebridge_v2/test_memoria_real_integration.py`
- Verify: `pytest.ini`

- [ ] **Step 1: Add settings helpers above the tests**

Insert above the tests:

```python
import os
from dataclasses import dataclass, field
from typing import Mapping

from YouTubeBridgeV2.adapters.memoria_http import MemoriaHttpTransportConfig


_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class MemoriaRealIntegrationSettings:
    enabled: bool
    base_url: str | None
    api_key: str | None = field(default=None, repr=False)
    character_id: str | None = None
    user_id: str = "__youtube_live_integration__"
    session_id: str = "yb2-integration-session"
    timeout_seconds: float = 10.0
    max_attempts: int = 1

    def transport_config(self) -> MemoriaHttpTransportConfig:
        if self.base_url is None:
            raise ValueError("YB2_MEMORIA_BASE_URL is required")
        return MemoriaHttpTransportConfig(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
            max_attempts=self.max_attempts,
        )


def _settings_from_env(env: Mapping[str, str]) -> MemoriaRealIntegrationSettings:
    return MemoriaRealIntegrationSettings(
        enabled=_enabled(env.get("YB2_MEMORIA_INTEGRATION")),
        base_url=_optional_env(env.get("YB2_MEMORIA_BASE_URL")),
        api_key=_optional_env(env.get("YB2_MEMORIA_API_KEY")),
        character_id=_optional_env(env.get("YB2_MEMORIA_CHARACTER_ID")),
        user_id=_optional_env(env.get("YB2_MEMORIA_USER_ID"))
        or "__youtube_live_integration__",
        session_id=_optional_env(env.get("YB2_MEMORIA_SESSION_ID"))
        or "yb2-integration-session",
        timeout_seconds=_float_env(env.get("YB2_MEMORIA_TIMEOUT_SECONDS"), 10.0),
        max_attempts=_int_env(env.get("YB2_MEMORIA_MAX_ATTEMPTS"), 1),
    )


def _require_enabled_settings(
    settings: MemoriaRealIntegrationSettings,
) -> MemoriaRealIntegrationSettings:
    if not settings.enabled:
        pytest.skip("set YB2_MEMORIA_INTEGRATION=1 to run real MemoriaCore integration")
    if settings.base_url is None:
        pytest.skip("set YB2_MEMORIA_BASE_URL to run real MemoriaCore integration")
    if settings.character_id is None:
        pytest.skip("set YB2_MEMORIA_CHARACTER_ID to run real MemoriaCore integration")
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
```

- [ ] **Step 2: Run green tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_real_integration.py::test_memoria_real_integration_settings_default_is_disabled tests\youtubebridge_v2\test_memoria_real_integration.py::test_memoria_real_integration_settings_parse_explicit_env_without_secret_repr tests\youtubebridge_v2\test_memoria_real_integration.py::test_memoria_real_integration_requires_opt_in_before_external_call tests\youtubebridge_v2\test_memoria_real_integration.py::test_memoria_real_integration_requires_base_url_and_character_id -q
```

Expected: `4 passed`.

---

### Task 3: Real MemoriaCore Round-Trip Test

**Files:**
- Modify: `tests/youtubebridge_v2/test_memoria_real_integration.py`

- [ ] **Step 1: Add adapter imports and helpers**

Extend imports:

```python
from YouTubeBridgeV2.adapters.memoria import (
    MemoriaAdapterError,
    build_memoria_request,
    normalize_memoria_response,
)
from YouTubeBridgeV2.adapters.memoria_http import (
    MemoriaHttpTransportConfig,
    MemoriaSyncHttpTransport,
)
from YouTubeBridgeV2.live_episode_plan.runner import PlannedTurnIntent
```

Add helpers below env helpers:

```python
def _integration_planned_turn(character_id: str) -> PlannedTurnIntent:
    return PlannedTurnIntent(
        plan_id="yb2-real-memoria-integration",
        turn_id="real-memoria-smoke",
        turn_index=0,
        purpose="Reply briefly to confirm the YouTubeBridgeV2 MemoriaCore transport works.",
        speaker_policy="fixed",
        speaker_ids=(character_id,),
        topic_cue="MemoriaCore integration harness smoke test.",
        audience_summary=None,
        audience_handling_hint="no_audience_event",
        metadata={"test_scope": "youtubebridge_v2_memoria_integration"},
    )


def _integration_context(settings: MemoriaRealIntegrationSettings) -> dict[str, object]:
    return {
        "v2_session_id": "yb2-real-memoria-integration",
        "memoria_session_id": settings.session_id,
        "user_id": settings.user_id,
        "character_id": settings.character_id,
        "correlation_id": "yb2-real-memoria-correlation",
        "request_id": "yb2-real-memoria-request",
    }


def _assert_no_secret_payload(value: object) -> None:
    text = repr(value).lower()
    for forbidden in (
        "secret-token",
        "authorization",
        "bearer",
        "raw_payload",
        "access_token",
        "hidden_prompt",
    ):
        assert forbidden not in text
```

- [ ] **Step 2: Add opt-in real integration test**

Append:

```python
@pytest.mark.memoria_integration
def test_real_memoria_sync_transport_round_trips_planned_turn():
    settings = _require_enabled_settings(_settings_from_env(os.environ))
    request = build_memoria_request(
        _integration_planned_turn(settings.character_id or ""),
        _integration_context(settings),
    )
    transport = MemoriaSyncHttpTransport(settings.transport_config())

    response_payload = transport.send(request)
    normalized = normalize_memoria_response(response_payload, request.correlation)

    assert not isinstance(normalized, MemoriaAdapterError)
    assert normalized.messages
    assert normalized.public_summary["message_count"] >= 1
    assert normalized.correlation.correlation_id == "yb2-real-memoria-correlation"
    _assert_no_secret_payload(request.public_summary)
    _assert_no_secret_payload(normalized.public_summary)
```

- [ ] **Step 3: Run default harness test**

Run without opt-in env:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_real_integration.py -q
```

Expected: helper tests pass and `test_real_memoria_sync_transport_round_trips_planned_turn` is skipped with reason mentioning `YB2_MEMORIA_INTEGRATION=1`.

- [ ] **Step 4: Document optional live command for manual use**

Do not run this command during default verification unless the user explicitly asks and confirms local MemoriaCore is running with a known character id:

```powershell
$env:YB2_MEMORIA_INTEGRATION='1'
$env:YB2_MEMORIA_BASE_URL='http://127.0.0.1:8088'
$env:YB2_MEMORIA_CHARACTER_ID='<existing-character-id>'
python -m pytest tests\youtubebridge_v2\test_memoria_real_integration.py::test_real_memoria_sync_transport_round_trips_planned_turn -q
```

Expected when local MemoriaCore is configured correctly: `1 passed`.

---

### Task 4: Docs Sync

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/memoria-adapter.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`

- [ ] **Step 1: Update module design test strategy**

In `docs/modules/memoria-adapter.md`, under `Test Strategy`, add:

```markdown
- real integration harness：`tests/youtubebridge_v2/test_memoria_real_integration.py` 預設 skip；只有設定 `YB2_MEMORIA_INTEGRATION=1`、`YB2_MEMORIA_BASE_URL` 與 `YB2_MEMORIA_CHARACTER_ID` 時才會呼叫本機 MemoriaCore。
```

Under `Failure Modes`, add:

```markdown
- real integration harness 缺少 opt-in env 或必要 base URL / character id 時必須 skip，不得嘗試外呼。
```

- [ ] **Step 2: Update architecture index**

In `docs/architecture-index.md`, after `Integration Wave 2E-B 狀態`, add or update:

```markdown
## Integration Wave 2E-C 狀態

- [x] Real MemoriaCore integration harness：已建立 opt-in pytest harness，可在本機 MemoriaCore 8088 與明確 character id 設定下跑 real `/api/v1/chat/sync` round-trip。
- [x] Default pytest independence：未設定 `YB2_MEMORIA_INTEGRATION=1` 時，real external test skip，不依賴 MemoriaCore service、API key 或本機角色資料。
- [ ] Production wiring toggle：仍保留給 Wave 2E-D。
```

- [ ] **Step 3: Docs sanity check**

Run:

```powershell
rg -n "YB2_MEMORIA_INTEGRATION|memoria_integration|Integration Wave 2E-C|test_memoria_real_integration" pytest.ini YouTubeBridgeV2\docs tests\youtubebridge_v2\test_memoria_real_integration.py
```

Expected: finds marker, harness file, module docs, and architecture status.

---

### Task 5: Final Verification For 2E-C

**Files:**
- Verify: `pytest.ini`
- Verify: `tests/youtubebridge_v2/test_memoria_real_integration.py`
- Verify: `YouTubeBridgeV2/docs/modules/memoria-adapter.md`
- Verify: `YouTubeBridgeV2/docs/architecture-index.md`

- [ ] **Step 1: Run harness default verification**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_real_integration.py -q
```

Expected: settings tests pass and one real external test is skipped.

- [ ] **Step 2: Run Wave 2E verification commands**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_adapter.py -q
python -m pytest tests\youtubebridge_v2\test_runtime_memoria_runners.py -q
python -m pytest tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py -q
python -m pytest tests\youtubebridge_v2 -q
git diff --check
```

Expected:

- `test_memoria_adapter.py` passes.
- `test_runtime_memoria_runners.py` passes.
- `test_runtime_tick_vertical_slice.py` passes.
- Full `tests\youtubebridge_v2` suite passes with the real MemoriaCore test skipped by default.
- `git diff --check` prints no whitespace errors.

- [ ] **Step 3: Check scope and forbidden imports**

Run:

```powershell
git status -sb
git diff --stat
rg -n "sqlite3|aiosqlite|\bfrom YouTubeBridge(\.|\s|$)|\bimport YouTubeBridge(\.|\s|$)" tests\youtubebridge_v2\test_memoria_real_integration.py YouTubeBridgeV2\docs\modules\memoria-adapter.md YouTubeBridgeV2\docs\architecture-index.md
```

Expected:

- Changed files are limited to 2E-C harness/docs/pytest marker.
- No direct SQLite imports.
- No Legacy `YouTubeBridge` runtime imports.

- [ ] **Step 4: Request code review**

Use `superpowers:requesting-code-review` with scope limited to roadmap `2E-C`. Review must check that the default suite cannot call external MemoriaCore and that the harness does not implement production wiring.

- [ ] **Step 5: Commit**

After review findings are fixed and verification is fresh:

```powershell
git add pytest.ini tests\youtubebridge_v2\test_memoria_real_integration.py YouTubeBridgeV2\docs\modules\memoria-adapter.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\implementation-plans\memoria-real-integration-harness.md
git diff --cached --check
git commit -m "test: add opt-in MemoriaCore integration harness"
```

---

## Self-Review

Spec coverage:

- `可用明確 opt-in 設定跑本機 MemoriaCore integration` covered by `YB2_MEMORIA_INTEGRATION=1` plus base URL / character id env contract and `test_real_memoria_sync_transport_round_trips_planned_turn`.
- `預設 CI/pytest 不依賴外部服務` covered by `_require_enabled_settings(...)` skip guard and default harness verification.
- `不要跨 wave` covered by leaving production wiring toggle out of scope and only using explicit test-time env.

Placeholder scan:

- No red-flag placeholder steps remain.
- Every code-changing task includes exact code and commands.

Type consistency:

- Harness uses existing `MemoriaSyncHttpTransport` and `MemoriaHttpTransportConfig`.
- Harness normalizes with existing `normalize_memoria_response(...)`.
- `character_id` is required before building the real request, so the test does not rely on a hardcoded local character.

## Execution Handoff

Plan complete and saved to `YouTubeBridgeV2/docs/implementation-plans/memoria-real-integration-harness.md`. Two execution options:

**1. Subagent-Driven (recommended)** - dispatch a fresh worker for the test harness, then review.

**2. Inline Execution** - execute tasks in this session using `superpowers:executing-plans`, with checkpoints for review.
