# Memoria Transport Config And Sync HTTP Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 roadmap `2E-A`：建立可注入的真 MemoriaCore transport implementation、設定解析邊界與可替換的同步 HTTP client，且不把 secret 硬寫進程式碼。

**Architecture:** 新增 `YouTubeBridgeV2/adapters/memoria_http.py` 作為 MemoriaCore HTTP transport 邊界；既有 `YouTubeBridgeV2/adapters/memoria.py` 繼續只負責 request/response contract，`YouTubeBridgeV2/runtime/memoria_runners.py` 繼續只依賴 `MemoriaTransportProtocol.send(...)`。本 item 不把 production `/v2` 自動切到真外呼，production toggle 保留給 `2E-D`。

**Tech Stack:** Python 3.12、pytest、stdlib `urllib.request`、StorageManager `load_prefs()` 只作設定來源，不直接讀寫 SQLite。

---

## Scope

Roadmap item：`2E-A：transport config 與 sync HTTP client boundary`

完成條件：

- 新增可注入 transport implementation，符合 `MemoriaTransportProtocol.send(request) -> dict[str, object]`。
- 設定來源不能硬寫 secret；從 StorageManager prefs 或明確傳入 mapping 解析。
- 測試可替換 fake client，不需要真 MemoriaCore 服務。

不包含：

- `2E-B` timeout/retry/auth/invalid response 詳細分類。
- `2E-C` 真 MemoriaCore integration harness。
- `2E-D` production wiring toggle；`create_production_v2_composition(storage)` 未顯式提供 transport 時仍維持 no-op。

## File Structure

- Create: `YouTubeBridgeV2/adapters/memoria_http.py`
  - 擁有 `MemoriaHttpTransportConfig`、設定 loader、可注入 sync JSON client protocol、stdlib HTTP client、`MemoriaSyncHttpTransport`。
  - 不 import `api.main`、不 import Legacy `YouTubeBridge/`、不直接碰 storage DB。
- Modify: `tests/youtubebridge_v2/test_memoria_adapter.py`
  - 新增 config parsing、secret redaction、fake client injection、stdlib client request encoding 的 red tests。
- Modify: `tests/youtubebridge_v2/test_runtime_tick_vertical_slice.py`
  - 新增一個 production composition 明確注入 `MemoriaSyncHttpTransport` 的 fake-client vertical smoke，確認 runner 可替換 fake client，且未注入仍 no-op 的既有測試不變。
- Modify: `YouTubeBridgeV2/docs/modules/memoria-adapter.md`
  - 補 `MemoriaSyncHttpTransport` 與 config loader 屬於 MemoriaCore Adapter transport boundary。
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
  - 補新增 public entrypoint 的 Source。
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
  - 補 Wave 2E-A 狀態，明確說明真 HTTP transport 邊界存在但 production auto toggle 尚未完成。

## Public Symbols To Add

- `MEMORIA_TRANSPORT_PREFS_KEY`
- `MemoriaHttpConfigError`
- `MemoriaHttpTransportConfig`
- `SyncJsonHttpClientProtocol`
- `UrllibSyncJsonHttpClient`
- `MemoriaSyncHttpTransport`
- `parse_memoria_http_transport_config(raw_config)`
- `load_memoria_http_transport_config(storage_manager)`

---

### Task 1: Config Red Tests

**Files:**
- Modify: `tests/youtubebridge_v2/test_memoria_adapter.py`
- Create later: `YouTubeBridgeV2/adapters/memoria_http.py`

- [ ] **Step 1: Write failing imports and config tests**

Add these imports near the existing adapter imports:

```python
from YouTubeBridgeV2.adapters.memoria_http import (
    MEMORIA_TRANSPORT_PREFS_KEY,
    MemoriaHttpConfigError,
    MemoriaHttpTransportConfig,
    load_memoria_http_transport_config,
    parse_memoria_http_transport_config,
)
```

Append these tests to `tests/youtubebridge_v2/test_memoria_adapter.py`:

```python
class FakePrefsStorage:
    def __init__(self, prefs):
        self.prefs = prefs

    def load_prefs(self):
        return self.prefs


def test_memoria_http_transport_config_loads_from_storage_prefs_without_secret_repr():
    storage = FakePrefsStorage(
        {
            MEMORIA_TRANSPORT_PREFS_KEY: {
                "base_url": "http://127.0.0.1:8088/",
                "api_key": "secret-token",
                "timeout_seconds": 7.5,
            }
        }
    )

    config = load_memoria_http_transport_config(storage)

    assert isinstance(config, MemoriaHttpTransportConfig)
    assert config.base_url == "http://127.0.0.1:8088"
    assert config.api_key == "secret-token"
    assert config.timeout_seconds == 7.5
    assert config.resolve_url("/api/v1/chat/sync") == "http://127.0.0.1:8088/api/v1/chat/sync"
    assert config.public_summary() == {
        "base_url": "http://127.0.0.1:8088",
        "timeout_seconds": 7.5,
        "has_api_key": True,
    }
    assert "secret-token" not in repr(config)
    assert "secret-token" not in repr(config.public_summary())


def test_memoria_http_transport_config_absent_or_blank_stays_disabled():
    assert load_memoria_http_transport_config(FakePrefsStorage({})) is None
    assert parse_memoria_http_transport_config({}) is None
    assert parse_memoria_http_transport_config({"base_url": "   "}) is None


def test_memoria_http_transport_config_rejects_invalid_url_and_timeout():
    for raw in (
        {"base_url": "file:///tmp/memoria"},
        {"base_url": "http://127.0.0.1:8088", "timeout_seconds": 0},
        {"base_url": "http://127.0.0.1:8088", "timeout_seconds": "slow"},
    ):
        try:
            parse_memoria_http_transport_config(raw)
        except MemoriaHttpConfigError as exc:
            assert str(exc)
        else:
            raise AssertionError(f"expected invalid config to fail: {raw!r}")


def test_memoria_http_transport_config_rejects_absolute_endpoint_override():
    config = MemoriaHttpTransportConfig(
        base_url="http://127.0.0.1:8088",
        api_key="secret-token",
    )

    for endpoint in (
        "https://example.test/steal",
        "//example.test/steal",
    ):
        try:
            config.resolve_url(endpoint)
        except MemoriaHttpConfigError as exc:
            assert "relative endpoint" in str(exc)
        else:
            raise AssertionError(f"expected absolute endpoint to fail: {endpoint!r}")
```

- [ ] **Step 2: Run the config red tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_http_transport_config_loads_from_storage_prefs_without_secret_repr tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_http_transport_config_absent_or_blank_stays_disabled tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_http_transport_config_rejects_invalid_url_and_timeout tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_http_transport_config_rejects_absolute_endpoint_override -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'YouTubeBridgeV2.adapters.memoria_http'`.

- [ ] **Step 3: Commit the red tests**

Run:

```powershell
git add tests\youtubebridge_v2\test_memoria_adapter.py
git commit -m "test: cover memoria http transport config"
```

---

### Task 2: Config Green Implementation

**Files:**
- Create: `YouTubeBridgeV2/adapters/memoria_http.py`
- Test: `tests/youtubebridge_v2/test_memoria_adapter.py`

- [ ] **Step 1: Create the config implementation**

Create `YouTubeBridgeV2/adapters/memoria_http.py` with this content:

```python
"""Sync MemoriaCore HTTP transport config boundary for YouTubeBridgeV2.

本模組先建立設定解析。Production 是否啟用真外呼由後續 wiring toggle
決定；未顯式注入時仍應保持 no-op runner。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import urljoin, urlparse


MEMORIA_TRANSPORT_PREFS_KEY = "youtubebridge_v2_memoria_transport"


class MemoriaHttpConfigError(ValueError):
    """MemoriaCore HTTP transport 設定不符合 V2 contract。"""


@dataclass(frozen=True)
class MemoriaHttpTransportConfig:
    """可安全顯示摘要的 MemoriaCore HTTP transport 設定。"""

    base_url: str
    api_key: str | None = field(default=None, repr=False)
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        cleaned_base_url = _normalize_base_url(self.base_url)
        timeout = _coerce_timeout(self.timeout_seconds)
        object.__setattr__(self, "base_url", cleaned_base_url)
        object.__setattr__(self, "timeout_seconds", timeout)
        object.__setattr__(self, "api_key", _optional_string(self.api_key))

    def resolve_url(self, endpoint: str) -> str:
        """Resolve one MemoriaCore endpoint against the configured base URL."""

        endpoint_text = str(endpoint or "").strip()
        if not endpoint_text:
            raise MemoriaHttpConfigError("endpoint is required")
        endpoint_parts = urlparse(endpoint_text)
        if endpoint_parts.scheme or endpoint_parts.netloc:
            raise MemoriaHttpConfigError("endpoint must be a relative endpoint")
        return urljoin(f"{self.base_url}/", endpoint_text.lstrip("/"))

    def public_summary(self) -> dict[str, object]:
        """Return a public-safe config summary without secret values."""

        return {
            "base_url": self.base_url,
            "timeout_seconds": self.timeout_seconds,
            "has_api_key": bool(self.api_key),
        }


def parse_memoria_http_transport_config(
    raw_config: Mapping[str, object] | None,
) -> MemoriaHttpTransportConfig | None:
    """Parse explicit MemoriaCore transport config from a mapping."""

    if not isinstance(raw_config, Mapping):
        return None
    base_url = _optional_string(raw_config.get("base_url"))
    if base_url is None:
        return None
    return MemoriaHttpTransportConfig(
        base_url=base_url,
        api_key=_optional_string(raw_config.get("api_key")),
        timeout_seconds=_raw_timeout(raw_config.get("timeout_seconds")),
    )


def load_memoria_http_transport_config(
    storage_manager: object,
) -> MemoriaHttpTransportConfig | None:
    """Load MemoriaCore transport config from StorageManager prefs."""

    prefs = _load_prefs(storage_manager)
    raw_config = prefs.get(MEMORIA_TRANSPORT_PREFS_KEY)
    return parse_memoria_http_transport_config(raw_config)


def _load_prefs(storage_manager: object) -> dict[str, object]:
    if not hasattr(storage_manager, "load_prefs"):
        return {}
    try:
        prefs = storage_manager.load_prefs()
    except Exception:
        return {}
    if isinstance(prefs, dict):
        return prefs
    return {}


def _normalize_base_url(base_url: str) -> str:
    text = str(base_url or "").strip().rstrip("/")
    if not text:
        raise MemoriaHttpConfigError("base_url is required")
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise MemoriaHttpConfigError("base_url must be an http or https URL")
    return text


def _raw_timeout(value: object) -> float:
    if value is None:
        return 10.0
    return _coerce_timeout(value)


def _coerce_timeout(value: object) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise MemoriaHttpConfigError("timeout_seconds must be numeric") from exc
    if timeout <= 0:
        raise MemoriaHttpConfigError("timeout_seconds must be greater than zero")
    return timeout


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "MEMORIA_TRANSPORT_PREFS_KEY",
    "MemoriaHttpConfigError",
    "MemoriaHttpTransportConfig",
    "load_memoria_http_transport_config",
    "parse_memoria_http_transport_config",
]
```

- [ ] **Step 2: Run the config green tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_http_transport_config_loads_from_storage_prefs_without_secret_repr tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_http_transport_config_absent_or_blank_stays_disabled tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_http_transport_config_rejects_invalid_url_and_timeout tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_http_transport_config_rejects_absolute_endpoint_override -q
```

Expected: `4 passed`.

- [ ] **Step 3: Commit config implementation**

Run:

```powershell
git add YouTubeBridgeV2\adapters\memoria_http.py tests\youtubebridge_v2\test_memoria_adapter.py
git commit -m "feat: add memoria http transport config"
```

---

### Task 3: Injectable HTTP Client Red Tests

**Files:**
- Modify: `tests/youtubebridge_v2/test_memoria_adapter.py`
- Modify later: `YouTubeBridgeV2/adapters/memoria_http.py`

- [ ] **Step 1: Add transport client imports**

Extend the new import block:

```python
from YouTubeBridgeV2.adapters.memoria_http import (
    MEMORIA_TRANSPORT_PREFS_KEY,
    MemoriaHttpConfigError,
    MemoriaHttpTransportConfig,
    MemoriaSyncHttpTransport,
    UrllibSyncJsonHttpClient,
    load_memoria_http_transport_config,
    parse_memoria_http_transport_config,
)
```

- [ ] **Step 2: Add fake-client transport tests**

Append these tests:

```python
class FakeSyncJsonClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post_json(self, *, url, body, headers, timeout_seconds):
        self.calls.append(
            {
                "url": url,
                "body": dict(body),
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.response


def test_memoria_sync_http_transport_posts_prepared_request_through_injected_client():
    client = FakeSyncJsonClient(
        {
            "session_id": "memoria-session-2",
            "message_id": "m1",
            "character_id": "host",
            "reply": "hello",
        }
    )
    transport = MemoriaSyncHttpTransport(
        MemoriaHttpTransportConfig(
            base_url="http://127.0.0.1:8088",
            api_key="secret-token",
            timeout_seconds=3,
        ),
        client=client,
    )
    request = build_memoria_request(_planned_turn_intent(), _context())

    response = transport.send(request)

    assert response["reply"] == "hello"
    assert len(client.calls) == 1
    assert client.calls[0]["url"] == "http://127.0.0.1:8088/api/v1/chat/sync"
    assert client.calls[0]["body"] == request.body
    assert client.calls[0]["headers"]["Authorization"] == "Bearer secret-token"
    assert client.calls[0]["headers"]["X-Correlation-Id"] == "corr-1"
    assert client.calls[0]["headers"]["X-Request-Id"] == "request-1"
    assert client.calls[0]["timeout_seconds"] == 3.0
    assert transport.public_summary() == {
        "transport": "memoria_sync_http",
        "base_url": "http://127.0.0.1:8088",
        "timeout_seconds": 3.0,
        "has_api_key": True,
    }
    assert "secret-token" not in repr(transport)
    assert "secret-token" not in repr(transport.public_summary())


def test_memoria_sync_http_transport_omits_authorization_when_api_key_is_absent():
    client = FakeSyncJsonClient({"session_id": "memoria-session-2", "message_id": "m1", "character_id": "host", "reply": "hello"})
    transport = MemoriaSyncHttpTransport(
        MemoriaHttpTransportConfig(base_url="http://127.0.0.1:8088"),
        client=client,
    )

    transport.send(build_memoria_request(_planned_turn_intent(), _context()))

    assert "Authorization" not in client.calls[0]["headers"]
```

- [ ] **Step 3: Add stdlib JSON client encoding test**

Append this test:

```python
class FakeUrlopenResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body


def test_urllib_sync_json_client_encodes_json_request(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["method"] = request.get_method()
        captured["headers"] = dict(request.header_items())
        captured["body"] = request.data.decode("utf-8")
        return FakeUrlopenResponse(b'{"ok": true, "reply": "received"}')

    monkeypatch.setattr("YouTubeBridgeV2.adapters.memoria_http.urllib_request.urlopen", fake_urlopen)
    client = UrllibSyncJsonHttpClient()

    response = client.post_json(
        url="http://127.0.0.1:8088/api/v1/chat/sync",
        body={"content": "hello"},
        headers={"X-Request-Id": "request-1"},
        timeout_seconds=2.5,
    )

    assert response == {"ok": True, "reply": "received"}
    assert captured["url"] == "http://127.0.0.1:8088/api/v1/chat/sync"
    assert captured["method"] == "POST"
    assert captured["timeout"] == 2.5
    assert json.loads(captured["body"]) == {"content": "hello"}
    assert captured["headers"]["X-request-id"] == "request-1"
```

Also add `import json` at the top of the test file.

- [ ] **Step 4: Run the transport red tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_sync_http_transport_posts_prepared_request_through_injected_client tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_sync_http_transport_omits_authorization_when_api_key_is_absent tests\youtubebridge_v2\test_memoria_adapter.py::test_urllib_sync_json_client_encodes_json_request -q
```

Expected: FAIL with `ImportError` for missing `MemoriaSyncHttpTransport` or `UrllibSyncJsonHttpClient`.

- [ ] **Step 5: Commit the client red tests**

Run:

```powershell
git add tests\youtubebridge_v2\test_memoria_adapter.py
git commit -m "test: cover injectable memoria http client"
```

---

### Task 4: Runner Injection Smoke And Boundary Guard

**Files:**
- Modify: `YouTubeBridgeV2/adapters/memoria_http.py`
- Modify: `tests/youtubebridge_v2/test_runtime_tick_vertical_slice.py`
- Test: `YouTubeBridgeV2/production.py`

- [ ] **Step 1: Replace config-only module with full client implementation**

Replace `YouTubeBridgeV2/adapters/memoria_http.py` with this complete content:

```python
"""Sync MemoriaCore HTTP transport boundary for YouTubeBridgeV2.

本模組只建立可注入 HTTP transport 與設定解析邊界。Production 是否啟用
真外呼由後續 wiring toggle 決定；未顯式注入時仍應保持 no-op runner。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Mapping, Protocol
from urllib import request as urllib_request
from urllib.parse import urljoin, urlparse

from YouTubeBridgeV2.adapters.memoria import MemoriaRequestPayload


MEMORIA_TRANSPORT_PREFS_KEY = "youtubebridge_v2_memoria_transport"


class MemoriaHttpConfigError(ValueError):
    """MemoriaCore HTTP transport 設定不符合 V2 contract。"""


@dataclass(frozen=True)
class MemoriaHttpTransportConfig:
    """可安全顯示摘要的 MemoriaCore HTTP transport 設定。"""

    base_url: str
    api_key: str | None = field(default=None, repr=False)
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        cleaned_base_url = _normalize_base_url(self.base_url)
        timeout = _coerce_timeout(self.timeout_seconds)
        object.__setattr__(self, "base_url", cleaned_base_url)
        object.__setattr__(self, "timeout_seconds", timeout)
        object.__setattr__(self, "api_key", _optional_string(self.api_key))

    def resolve_url(self, endpoint: str) -> str:
        """Resolve one MemoriaCore endpoint against the configured base URL."""

        endpoint_text = str(endpoint or "").strip()
        if not endpoint_text:
            raise MemoriaHttpConfigError("endpoint is required")
        endpoint_parts = urlparse(endpoint_text)
        if endpoint_parts.scheme or endpoint_parts.netloc:
            raise MemoriaHttpConfigError("endpoint must be a relative endpoint")
        return urljoin(f"{self.base_url}/", endpoint_text.lstrip("/"))

    def public_summary(self) -> dict[str, object]:
        """Return a public-safe config summary without secret values."""

        return {
            "base_url": self.base_url,
            "timeout_seconds": self.timeout_seconds,
            "has_api_key": bool(self.api_key),
        }


class SyncJsonHttpClientProtocol(Protocol):
    """Injectable sync JSON client used by MemoriaSyncHttpTransport."""

    def post_json(
        self,
        *,
        url: str,
        body: Mapping[str, object],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> dict[str, object]:
        """Post a JSON object and return a JSON object response."""


def parse_memoria_http_transport_config(
    raw_config: Mapping[str, object] | None,
) -> MemoriaHttpTransportConfig | None:
    """Parse explicit MemoriaCore transport config from a mapping."""

    if not isinstance(raw_config, Mapping):
        return None
    base_url = _optional_string(raw_config.get("base_url"))
    if base_url is None:
        return None
    return MemoriaHttpTransportConfig(
        base_url=base_url,
        api_key=_optional_string(raw_config.get("api_key")),
        timeout_seconds=_raw_timeout(raw_config.get("timeout_seconds")),
    )


def load_memoria_http_transport_config(
    storage_manager: object,
) -> MemoriaHttpTransportConfig | None:
    """Load MemoriaCore transport config from StorageManager prefs."""

    prefs = _load_prefs(storage_manager)
    raw_config = prefs.get(MEMORIA_TRANSPORT_PREFS_KEY)
    return parse_memoria_http_transport_config(raw_config)


class UrllibSyncJsonHttpClient:
    """stdlib urllib-backed sync JSON client."""

    def post_json(
        self,
        *,
        url: str,
        body: Mapping[str, object],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> dict[str, object]:
        """Send one JSON POST request and decode a JSON object response."""

        request = urllib_request.Request(
            url,
            data=json.dumps(dict(body), ensure_ascii=False).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
            raw_body = response.read().decode("utf-8")
        decoded = json.loads(raw_body or "{}")
        if not isinstance(decoded, dict):
            raise ValueError("MemoriaCore response must be a JSON object")
        return decoded


class MemoriaSyncHttpTransport:
    """MemoriaTransportProtocol implementation backed by sync JSON HTTP."""

    def __init__(
        self,
        config: MemoriaHttpTransportConfig,
        *,
        client: SyncJsonHttpClientProtocol | None = None,
    ) -> None:
        self._config = config
        self._client = client or UrllibSyncJsonHttpClient()

    def send(self, request: MemoriaRequestPayload) -> dict[str, object]:
        """Send one prepared Memoria request through the configured HTTP client."""

        return self._client.post_json(
            url=self._config.resolve_url(request.endpoint),
            body=request.body,
            headers=_request_headers(self._config, request),
            timeout_seconds=self._config.timeout_seconds,
        )

    def public_summary(self) -> dict[str, object]:
        """Return a public-safe transport summary."""

        return {
            "transport": "memoria_sync_http",
            **self._config.public_summary(),
        }


def _request_headers(
    config: MemoriaHttpTransportConfig,
    request: MemoriaRequestPayload,
) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
        "X-Correlation-Id": request.correlation.correlation_id,
        "X-Request-Id": request.correlation.request_id,
    }
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    return headers


def _load_prefs(storage_manager: object) -> dict[str, object]:
    if not hasattr(storage_manager, "load_prefs"):
        return {}
    try:
        prefs = storage_manager.load_prefs()
    except Exception:
        return {}
    if isinstance(prefs, dict):
        return prefs
    return {}


def _normalize_base_url(base_url: str) -> str:
    text = str(base_url or "").strip().rstrip("/")
    if not text:
        raise MemoriaHttpConfigError("base_url is required")
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise MemoriaHttpConfigError("base_url must be an http or https URL")
    return text


def _raw_timeout(value: object) -> float:
    if value is None:
        return 10.0
    return _coerce_timeout(value)


def _coerce_timeout(value: object) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise MemoriaHttpConfigError("timeout_seconds must be numeric") from exc
    if timeout <= 0:
        raise MemoriaHttpConfigError("timeout_seconds must be greater than zero")
    return timeout


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "MEMORIA_TRANSPORT_PREFS_KEY",
    "MemoriaHttpConfigError",
    "MemoriaHttpTransportConfig",
    "MemoriaSyncHttpTransport",
    "SyncJsonHttpClientProtocol",
    "UrllibSyncJsonHttpClient",
    "load_memoria_http_transport_config",
    "parse_memoria_http_transport_config",
]
```

- [ ] **Step 2: Run client green tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_sync_http_transport_posts_prepared_request_through_injected_client tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_sync_http_transport_omits_authorization_when_api_key_is_absent tests\youtubebridge_v2\test_memoria_adapter.py::test_urllib_sync_json_client_encodes_json_request -q
```

Expected: `3 passed`.

- [ ] **Step 3: Add imports**

Add this import block:

```python
from YouTubeBridgeV2.adapters.memoria_http import (
    MemoriaHttpTransportConfig,
    MemoriaSyncHttpTransport,
)
```

- [ ] **Step 4: Add a fake-client production injection test**

Append this test to `tests/youtubebridge_v2/test_runtime_tick_vertical_slice.py`:

```python
class FakeSyncJsonClient:
    def __init__(self, *responses: dict[str, object]) -> None:
        self.responses = list(responses)
        self.calls = []

    def post_json(self, *, url, body, headers, timeout_seconds):
        self.calls.append(
            {
                "url": url,
                "body": dict(body),
                "headers": dict(headers),
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.responses.pop(0)


def test_production_composition_accepts_memoria_sync_http_transport_with_fake_client(tmp_path):
    storage = _storage_manager(tmp_path)
    fake_client = FakeSyncJsonClient(
        {
            "session_id": "memoria-http-planned",
            "message_id": "http-1",
            "character_id": "host",
            "reply": "http planned response",
        }
    )
    transport = MemoriaSyncHttpTransport(
        MemoriaHttpTransportConfig(
            base_url="http://127.0.0.1:8088",
            api_key="secret-token",
            timeout_seconds=4,
        ),
        client=fake_client,
    )
    composition = create_production_v2_composition(storage, memoria_transport=transport)
    client = TestClient(create_v2_app(composition, now_provider=lambda: NOW))
    _create_and_bind(client, "session-http-transport")

    response = client.post(
        "/v2/sessions/session-http-transport/tick",
        json={"command_id": "cmd-http-planned"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert len(fake_client.calls) == 1
    assert fake_client.calls[0]["url"] == "http://127.0.0.1:8088/api/v1/chat/sync"
    assert fake_client.calls[0]["timeout_seconds"] == 4.0
    assert fake_client.calls[0]["headers"]["Authorization"] == "Bearer secret-token"
    assert "secret-token" not in repr(response.json())
    assert storage.get_v2_session("session-http-transport")["metadata"]["live_episode_plan_state"]["last_memoria_session_id"] == "memoria-http-planned"


def test_production_composition_without_memoria_transport_keeps_noop_runner(tmp_path):
    storage = _storage_manager(tmp_path)
    composition = create_production_v2_composition(storage)
    client = TestClient(create_v2_app(composition, now_provider=lambda: NOW))
    _create_and_bind(client, "session-noop-transport")

    response = client.post(
        "/v2/sessions/session-noop-transport/tick",
        json={"command_id": "cmd-noop-planned"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["events"][0]["payload"]["adapter_summary"] == {
        "mode": "noop",
        "runner": "planned_show",
        "external_adapter": "not_configured",
        "next_action": "run_planned_show",
    }
```

- [ ] **Step 5: Run the runner injection test**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py::test_production_composition_accepts_memoria_sync_http_transport_with_fake_client tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py::test_production_composition_without_memoria_transport_keeps_noop_runner -q
```

Expected: PASS after Step 2 client implementation and Step 4 test additions. This proves `MemoriaSyncHttpTransport` is replaceable with a fake client and can be injected into existing runner wiring without a real HTTP server, while default production composition still stays no-op without explicit injection.

- [ ] **Step 6: Verify no-production-toggle boundary remains intact**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_main_app_wiring.py::test_main_app_v2_routes_use_real_storage_composition tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py::test_real_storage_tick_flow_survives_storage_and_composition_rebuild -q
```

Expected: PASS. The main app still creates production composition without implicit transport, and explicit injection still works in the test composition.

- [ ] **Step 7: Commit client implementation and injection smoke**

Run:

```powershell
git add YouTubeBridgeV2\adapters\memoria_http.py tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py
git commit -m "feat: add memoria sync http transport"
```

---

### Task 5: Docs Sync

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/memoria-adapter.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`

- [ ] **Step 1: Update Memoria adapter module design**

In `YouTubeBridgeV2/docs/modules/memoria-adapter.md`, under `Public Entrypoints`, add:

```markdown
- `MemoriaHttpTransportConfig`：真 MemoriaCore HTTP transport 的 public-safe 設定 contract。
- `parse_memoria_http_transport_config(raw_config)`：從明確 mapping 解析 transport config；空設定代表未啟用。
- `load_memoria_http_transport_config(storage_manager)`：從 `StorageManager.load_prefs()` 讀取 `youtubebridge_v2_memoria_transport`，不硬寫 secret。
- `SyncJsonHttpClientProtocol`：可替換的同步 JSON client protocol，供單元測試注入 fake client。
- `UrllibSyncJsonHttpClient`：stdlib `urllib.request` backed sync JSON client。
- `MemoriaSyncHttpTransport`：符合 `MemoriaTransportProtocol.send(...)` 的真 HTTP transport implementation。
```

Under `Failure Modes`, add:

```markdown
- HTTP transport config 缺少 `base_url` 時視為未啟用；`base_url` 非 http/https 或 timeout 非正數時回設定錯誤，不啟動真外呼。
- HTTP transport public summary 只能顯示 `base_url`、`timeout_seconds` 與 `has_api_key`，不得顯示 token/header/raw payload。
```

- [ ] **Step 2: Update API reference index**

In `YouTubeBridgeV2/docs/api-reference-index.md`, under `### MemoriaCore Adapter`, add these Sources:

```markdown
- `YouTubeBridgeV2/adapters/memoria_http.py::MEMORIA_TRANSPORT_PREFS_KEY`
- `YouTubeBridgeV2/adapters/memoria_http.py::MemoriaHttpConfigError`
- `YouTubeBridgeV2/adapters/memoria_http.py::MemoriaHttpTransportConfig`
- `YouTubeBridgeV2/adapters/memoria_http.py::SyncJsonHttpClientProtocol`
- `YouTubeBridgeV2/adapters/memoria_http.py::UrllibSyncJsonHttpClient`
- `YouTubeBridgeV2/adapters/memoria_http.py::MemoriaSyncHttpTransport`
- `YouTubeBridgeV2/adapters/memoria_http.py::parse_memoria_http_transport_config`
- `YouTubeBridgeV2/adapters/memoria_http.py::load_memoria_http_transport_config`
```

Also add a short note in the MemoriaCore Adapter `Purpose` or `Concepts` block:

```markdown
HTTP transport config/client entries define a real sync transport boundary, but production `/v2` still requires explicit transport injection until Wave 2E-D adds the opt-in toggle.
```

- [ ] **Step 3: Update architecture index**

In `YouTubeBridgeV2/docs/architecture-index.md`, add a new status section after `Integration Wave 2D 狀態`:

```markdown
## Integration Wave 2E-A 狀態

- [x] MemoriaCore transport config：已建立 `youtubebridge_v2_memoria_transport` prefs 設定解析，空設定代表未啟用，不硬寫 secret。
- [x] Sync HTTP client boundary：已建立可注入 `SyncJsonHttpClientProtocol` 與 stdlib `UrllibSyncJsonHttpClient`。
- [x] Memoria transport implementation：已建立 `MemoriaSyncHttpTransport`，符合 runner 使用的 `send(request) -> dict[str, object]`。
- [ ] Production wiring toggle：仍保留給 Wave 2E-D；主 app 未設定時繼續 no-op，不意外外呼。
```

- [ ] **Step 4: Run docs sanity checks**

Run:

```powershell
rg -n "MemoriaSyncHttpTransport|youtubebridge_v2_memoria_transport|Wave 2E-A" YouTubeBridgeV2\docs
```

Expected:

- Finds the new transport/config/API reference entries.
- Output points to the new 2E-A docs entries and does not reveal postponed-work placeholder text.

- [ ] **Step 5: Commit docs sync**

Run:

```powershell
git add YouTubeBridgeV2\docs\modules\memoria-adapter.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\architecture-index.md
git commit -m "docs: sync memoria http transport boundary"
```

---

### Task 6: Final Verification For 2E-A

**Files:**
- Verify: `YouTubeBridgeV2/adapters/memoria_http.py`
- Verify: `tests/youtubebridge_v2/test_memoria_adapter.py`
- Verify: `tests/youtubebridge_v2/test_runtime_tick_vertical_slice.py`
- Verify: V2 docs touched in Task 5

- [ ] **Step 1: Run focused Memoria adapter tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_adapter.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run runner and tick regression tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_memoria_runners.py tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Run roadmap verification commands for Wave 2E**

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
- Full `tests\youtubebridge_v2` suite passes.
- `git diff --check` prints no whitespace errors.

- [ ] **Step 4: Check worktree scope**

Run:

```powershell
git status -sb
git diff --stat
```

Expected changed paths are limited to:

```text
YouTubeBridgeV2/adapters/memoria_http.py
tests/youtubebridge_v2/test_memoria_adapter.py
tests/youtubebridge_v2/test_runtime_tick_vertical_slice.py
YouTubeBridgeV2/docs/modules/memoria-adapter.md
YouTubeBridgeV2/docs/api-reference-index.md
YouTubeBridgeV2/docs/architecture-index.md
```

- [ ] **Step 5: Final commit**

Run:

```powershell
git add YouTubeBridgeV2\adapters\memoria_http.py tests\youtubebridge_v2\test_memoria_adapter.py tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py YouTubeBridgeV2\docs\modules\memoria-adapter.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\architecture-index.md
git commit -m "feat: add memoria sync http transport boundary"
```

---

## Self-Review

Spec coverage:

- `新增可注入 transport implementation` covered by `MemoriaSyncHttpTransport` and the production composition fake-client injection test.
- `設定來源不硬寫 secret` covered by `load_memoria_http_transport_config(storage_manager)`, `MEMORIA_TRANSPORT_PREFS_KEY`, `api_key` with `repr=False`, and public summary tests.
- `測試可替換 fake client` covered by `FakeSyncJsonClient` unit and vertical smoke tests.
- `不要跨 wave 合併實作` covered by leaving retry/error mapping, opt-in integration harness, and production toggle out of scope.

Placeholder scan:

- The plan contains no postponed-work placeholder markers.
- Every code-changing step includes concrete code or exact Markdown content.

Type consistency:

- `MemoriaSyncHttpTransport.send(request)` accepts `MemoriaRequestPayload` and returns `dict[str, object]`, matching `MemoriaTransportProtocol`.
- `SyncJsonHttpClientProtocol.post_json(...)` uses keyword-only `url`, `body`, `headers`, and `timeout_seconds`, matching fake clients and `UrllibSyncJsonHttpClient`.
- `MemoriaHttpTransportConfig.public_summary()` and `MemoriaSyncHttpTransport.public_summary()` are public-safe and never include `api_key`.

## Execution Handoff

Plan complete and saved to `YouTubeBridgeV2/docs/implementation-plans/memoria-transport-config-http-client.md`. Two execution options:

**1. Subagent-Driven (recommended)** - dispatch a fresh worker for focused implementation tasks, then review between tasks.

**2. Inline Execution** - execute tasks in this session using `superpowers:executing-plans`, with checkpoints for review.
