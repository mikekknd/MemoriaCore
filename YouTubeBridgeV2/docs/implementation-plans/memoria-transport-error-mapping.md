# Memoria Transport Error Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 roadmap `2E-B`：讓真 MemoriaCore sync HTTP transport 對 timeout、5xx retry、401/403 terminal auth failure、invalid response 與 sanitized adapter summary 有明確 contract。

**Architecture:** 延續 `2E-A` 的邊界：`YouTubeBridgeV2/adapters/memoria_http.py` 擁有 sync HTTP client、retry policy 與 low-level HTTP/JSON 錯誤轉換；`YouTubeBridgeV2/adapters/memoria.py::classify_memoria_error(...)` 負責把 transport error 轉成 runner 使用的 `MemoriaAdapterError`。Runtime runners 不知道 urllib、URL、headers 或 raw response，production wiring toggle 仍留給 `2E-D`。

**Tech Stack:** Python 3.12+、pytest、stdlib `urllib.request` / `urllib.error`、可注入 fake sync JSON client。

---

## Scope

Roadmap item：`2E-B：timeout、retry、auth、sanitized error mapping`

完成條件：

- timeout 和 5xx failure 可 retry；若重試後成功，transport 回傳最後成功 response。
- 401/403 auth failure 是 terminal，不重試。
- invalid JSON / non-object JSON response 轉成 terminal `invalid_response`。
- 所有 public summary、runner error summary、runtime event payload 都不外洩 URL secret、header、token、authorization 或 raw payload。
- `base_url` 本身若帶 credentials、query 或 fragment，設定解析必須 fail-closed，避免 public summary 外洩 secret-bearing URL。

不包含：

- `2E-C` 真 MemoriaCore integration harness。
- `2E-D` production prefs toggle 或自動外呼 wiring。
- background scheduler、async client、backoff sleep、external service smoke。

## File Structure

- Modify: `YouTubeBridgeV2/adapters/memoria_http.py`
  - 新增 `MemoriaHttpTransportError`、retry config、低階 urllib error wrapping、invalid JSON/object response mapping、secret-bearing base URL rejection、public-safe redaction helper。
- Modify: `YouTubeBridgeV2/adapters/memoria.py`
  - 調整 `classify_memoria_error(...)`，讓具有 `error_type` / `retryable` / `public_summary` 的 transport error 保留 sanitized summary，而不是全部壓成 generic `transport_failure`。
- Modify: `tests/youtubebridge_v2/test_memoria_adapter.py`
  - 新增 retry/auth/invalid response/redaction unit tests。
- Modify: `tests/youtubebridge_v2/test_runtime_memoria_runners.py`
  - 新增 runner summary redaction test，確認 transport error 不把 URL/header/raw payload 寫進 adapter summary。
- Modify: `YouTubeBridgeV2/docs/modules/memoria-adapter.md`
  - 補 2E-B failure mode 與 retry/auth/invalid response contract。
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
  - 補 `MemoriaHttpTransportError` public source，更新 MemoriaCore Adapter concepts。
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
  - 補 Wave 2E-B 狀態，明確 production toggle 仍未啟用。

## Public Symbols To Add Or Update

- Add: `MemoriaHttpTransportError`
- Update: `MemoriaHttpTransportConfig(max_attempts=2)`
- Update: `MemoriaSyncHttpTransport.send(...)` retry behavior
- Update: `UrllibSyncJsonHttpClient.post_json(...)` error wrapping
- Update: `classify_memoria_error(error)` custom transport error support

---

### Task 1: Transport Retry/Auth/Invalid Response Red Tests

**Files:**
- Modify: `tests/youtubebridge_v2/test_memoria_adapter.py`
- Modify later: `YouTubeBridgeV2/adapters/memoria_http.py`

- [ ] **Step 1: Add test imports**

Extend the `memoria_http` import block:

```python
from YouTubeBridgeV2.adapters.memoria_http import (
    MEMORIA_TRANSPORT_PREFS_KEY,
    MemoriaHttpConfigError,
    MemoriaHttpTransportConfig,
    MemoriaHttpTransportError,
    MemoriaSyncHttpTransport,
    UrllibSyncJsonHttpClient,
    load_memoria_http_transport_config,
    parse_memoria_http_transport_config,
)
```

- [ ] **Step 2: Add retry-capable fake client**

Append near `FakeSyncJsonClient`:

```python
class SequencedSyncJsonClient:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
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
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
```

- [ ] **Step 3: Add timeout and 5xx retry tests**

Append:

```python
def test_memoria_sync_http_transport_retries_timeout_then_returns_success():
    client = SequencedSyncJsonClient(
        MemoriaHttpTransportError(
            error_type="timeout",
            retryable=True,
            public_summary={"error_type": "timeout", "retryable": True},
        ),
        {
            "session_id": "memoria-session-2",
            "message_id": "m1",
            "character_id": "host",
            "reply": "after retry",
        },
    )
    transport = MemoriaSyncHttpTransport(
        MemoriaHttpTransportConfig(base_url="http://127.0.0.1:8088", max_attempts=2),
        client=client,
    )

    response = transport.send(build_memoria_request(_planned_turn_intent(), _context()))

    assert response["reply"] == "after retry"
    assert len(client.calls) == 2


def test_memoria_sync_http_transport_retries_5xx_then_returns_success():
    client = SequencedSyncJsonClient(
        MemoriaHttpTransportError(
            error_type="transport_failure",
            retryable=True,
            status_code=503,
            public_summary={
                "error_type": "transport_failure",
                "retryable": True,
                "status_code": 503,
            },
        ),
        {
            "session_id": "memoria-session-2",
            "message_id": "m1",
            "character_id": "host",
            "reply": "server recovered",
        },
    )
    transport = MemoriaSyncHttpTransport(
        MemoriaHttpTransportConfig(base_url="http://127.0.0.1:8088", max_attempts=2),
        client=client,
    )

    response = transport.send(build_memoria_request(_planned_turn_intent(), _context()))

    assert response["reply"] == "server recovered"
    assert len(client.calls) == 2
```

- [ ] **Step 4: Add terminal auth and exhausted retry tests**

Append:

```python
def test_memoria_sync_http_transport_does_not_retry_auth_failure():
    client = SequencedSyncJsonClient(
        MemoriaHttpTransportError(
            error_type="auth_failure",
            retryable=False,
            status_code=401,
            public_summary={
                "error_type": "auth_failure",
                "retryable": False,
                "status_code": 401,
            },
        ),
        {
            "session_id": "must-not-send",
            "message_id": "m2",
            "character_id": "host",
            "reply": "should not happen",
        },
    )
    transport = MemoriaSyncHttpTransport(
        MemoriaHttpTransportConfig(base_url="http://127.0.0.1:8088", max_attempts=3),
        client=client,
    )

    try:
        transport.send(build_memoria_request(_planned_turn_intent(), _context()))
    except MemoriaHttpTransportError as exc:
        assert exc.error_type == "auth_failure"
        assert exc.retryable is False
        assert exc.status_code == 401
    else:
        raise AssertionError("expected auth failure to raise")
    assert len(client.calls) == 1


def test_memoria_sync_http_transport_exhausted_retry_raises_last_retryable_error():
    client = SequencedSyncJsonClient(
        MemoriaHttpTransportError(
            error_type="timeout",
            retryable=True,
            public_summary={"error_type": "timeout", "retryable": True},
        ),
        MemoriaHttpTransportError(
            error_type="timeout",
            retryable=True,
            public_summary={"error_type": "timeout", "retryable": True},
        ),
    )
    transport = MemoriaSyncHttpTransport(
        MemoriaHttpTransportConfig(base_url="http://127.0.0.1:8088", max_attempts=2),
        client=client,
    )

    try:
        transport.send(build_memoria_request(_planned_turn_intent(), _context()))
    except MemoriaHttpTransportError as exc:
        assert exc.error_type == "timeout"
        assert exc.retryable is True
    else:
        raise AssertionError("expected exhausted timeout retry to raise")
    assert len(client.calls) == 2
```

- [ ] **Step 5: Run red tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_sync_http_transport_retries_timeout_then_returns_success tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_sync_http_transport_retries_5xx_then_returns_success tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_sync_http_transport_does_not_retry_auth_failure tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_sync_http_transport_exhausted_retry_raises_last_retryable_error -q
```

Expected: FAIL with import/config constructor errors because `MemoriaHttpTransportError` and `max_attempts` do not exist yet.

---

### Task 2: Transport Retry/Auth Green Implementation

**Files:**
- Modify: `YouTubeBridgeV2/adapters/memoria_http.py`
- Test: `tests/youtubebridge_v2/test_memoria_adapter.py`

- [ ] **Step 1: Add transport error and retry config**

In `memoria_http.py`, add:

```python
@dataclass(frozen=True)
class MemoriaHttpTransportError(RuntimeError):
    """Sanitized MemoriaCore HTTP transport failure."""

    error_type: str
    retryable: bool
    public_summary: dict[str, object] = field(default_factory=dict)
    status_code: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "public_summary", _public_error_summary(self))

    def __str__(self) -> str:
        return self.error_type
```

Update `MemoriaHttpTransportConfig`:

```python
    max_attempts: int = 2
```

Update `__post_init__`:

```python
        attempts = _coerce_max_attempts(self.max_attempts)
        object.__setattr__(self, "max_attempts", attempts)
```

Update `public_summary()`:

```python
            "max_attempts": self.max_attempts,
```

Update parser:

```python
        max_attempts=_raw_max_attempts(raw_config.get("max_attempts")),
```

Add helpers:

```python
def _raw_max_attempts(value: object) -> int:
    if value is None:
        return 2
    return _coerce_max_attempts(value)


def _coerce_max_attempts(value: object) -> int:
    try:
        attempts = int(value)
    except (TypeError, ValueError) as exc:
        raise MemoriaHttpConfigError("max_attempts must be an integer") from exc
    if attempts < 1:
        raise MemoriaHttpConfigError("max_attempts must be at least one")
    return attempts
```

- [ ] **Step 2: Add retry loop**

Replace `MemoriaSyncHttpTransport.send(...)` with:

```python
    def send(self, request: MemoriaRequestPayload) -> dict[str, object]:
        """Send one prepared Memoria request through the configured HTTP client."""

        attempts = max(1, self._config.max_attempts)
        last_error: MemoriaHttpTransportError | None = None
        for attempt in range(1, attempts + 1):
            try:
                return self._client.post_json(
                    url=self._config.resolve_url(request.endpoint),
                    body=request.body,
                    headers=_request_headers(self._config, request),
                    timeout_seconds=self._config.timeout_seconds,
                )
            except MemoriaHttpTransportError as exc:
                last_error = exc
                if not exc.retryable or attempt >= attempts:
                    raise
        assert last_error is not None
        raise last_error
```

- [ ] **Step 3: Export new symbol**

Add to `__all__`:

```python
    "MemoriaHttpTransportError",
```

- [ ] **Step 4: Run green tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_sync_http_transport_retries_timeout_then_returns_success tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_sync_http_transport_retries_5xx_then_returns_success tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_sync_http_transport_does_not_retry_auth_failure tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_sync_http_transport_exhausted_retry_raises_last_retryable_error -q
```

Expected: `4 passed`.

---

### Task 3: Urllib Error Mapping And Redaction Red Tests

**Files:**
- Modify: `tests/youtubebridge_v2/test_memoria_adapter.py`
- Modify later: `YouTubeBridgeV2/adapters/memoria_http.py`

- [ ] **Step 1: Add stdlib test imports**

Add:

```python
from urllib.error import HTTPError, URLError
```

- [ ] **Step 2: Add HTTPError auth/5xx mapping tests**

Append:

```python
def _assert_no_transport_secret(value):
    text = repr(value).lower()
    for forbidden in (
        "secret-token",
        "authorization",
        "x-api-key",
        "token=secret",
        "raw_payload",
        "bearer",
    ):
        assert forbidden not in text


def test_urllib_sync_json_client_maps_5xx_http_error_to_retryable_transport_error(monkeypatch):
    def fake_urlopen(_request, _timeout):
        raise HTTPError(
            url="http://127.0.0.1:8088/api/v1/chat/sync?token=secret",
            code=503,
            msg="Service Unavailable",
            hdrs={"Authorization": "Bearer secret-token"},
            fp=None,
        )

    monkeypatch.setattr(
        "YouTubeBridgeV2.adapters.memoria_http.urllib_request.urlopen",
        fake_urlopen,
    )
    client = UrllibSyncJsonHttpClient()

    try:
        client.post_json(
            url="http://127.0.0.1:8088/api/v1/chat/sync?token=secret",
            body={"content": "hello"},
            headers={"Authorization": "Bearer secret-token"},
            timeout_seconds=2.5,
        )
    except MemoriaHttpTransportError as exc:
        assert exc.error_type == "transport_failure"
        assert exc.retryable is True
        assert exc.status_code == 503
        _assert_no_transport_secret(exc.public_summary)
        _assert_no_transport_secret(str(exc))
    else:
        raise AssertionError("expected transport error")


def test_urllib_sync_json_client_maps_401_http_error_to_terminal_auth_failure(monkeypatch):
    def fake_urlopen(_request, _timeout):
        raise HTTPError(
            url="http://127.0.0.1:8088/api/v1/chat/sync?token=secret",
            code=401,
            msg="Unauthorized",
            hdrs={"Authorization": "Bearer secret-token"},
            fp=None,
        )

    monkeypatch.setattr(
        "YouTubeBridgeV2.adapters.memoria_http.urllib_request.urlopen",
        fake_urlopen,
    )
    client = UrllibSyncJsonHttpClient()

    try:
        client.post_json(
            url="http://127.0.0.1:8088/api/v1/chat/sync?token=secret",
            body={"content": "hello"},
            headers={"Authorization": "Bearer secret-token"},
            timeout_seconds=2.5,
        )
    except MemoriaHttpTransportError as exc:
        assert exc.error_type == "auth_failure"
        assert exc.retryable is False
        assert exc.status_code == 401
        _assert_no_transport_secret(exc.public_summary)
    else:
        raise AssertionError("expected auth failure")
```

- [ ] **Step 3: Add timeout/urlerror/invalid response mapping tests**

Append:

```python
def test_urllib_sync_json_client_maps_timeout_to_retryable_transport_error(monkeypatch):
    def fake_urlopen(_request, _timeout):
        raise TimeoutError("timed out with token=secret")

    monkeypatch.setattr(
        "YouTubeBridgeV2.adapters.memoria_http.urllib_request.urlopen",
        fake_urlopen,
    )
    client = UrllibSyncJsonHttpClient()

    try:
        client.post_json(
            url="http://127.0.0.1:8088/api/v1/chat/sync",
            body={"content": "hello"},
            headers={"Authorization": "Bearer secret-token"},
            timeout_seconds=2.5,
        )
    except MemoriaHttpTransportError as exc:
        assert exc.error_type == "timeout"
        assert exc.retryable is True
        _assert_no_transport_secret(exc.public_summary)
    else:
        raise AssertionError("expected timeout")


def test_urllib_sync_json_client_maps_urlerror_to_retryable_transport_failure(monkeypatch):
    def fake_urlopen(_request, _timeout):
        raise URLError("connection failed with token=secret")

    monkeypatch.setattr(
        "YouTubeBridgeV2.adapters.memoria_http.urllib_request.urlopen",
        fake_urlopen,
    )
    client = UrllibSyncJsonHttpClient()

    try:
        client.post_json(
            url="http://127.0.0.1:8088/api/v1/chat/sync",
            body={"content": "hello"},
            headers={"Authorization": "Bearer secret-token"},
            timeout_seconds=2.5,
        )
    except MemoriaHttpTransportError as exc:
        assert exc.error_type == "transport_failure"
        assert exc.retryable is True
        _assert_no_transport_secret(exc.public_summary)
    else:
        raise AssertionError("expected transport failure")


def test_urllib_sync_json_client_maps_invalid_json_to_terminal_invalid_response(monkeypatch):
    def fake_urlopen(_request, _timeout):
        return FakeUrlopenResponse(b'{"raw_payload": "secret"')

    monkeypatch.setattr(
        "YouTubeBridgeV2.adapters.memoria_http.urllib_request.urlopen",
        fake_urlopen,
    )
    client = UrllibSyncJsonHttpClient()

    try:
        client.post_json(
            url="http://127.0.0.1:8088/api/v1/chat/sync",
            body={"content": "hello"},
            headers={"Authorization": "Bearer secret-token"},
            timeout_seconds=2.5,
        )
    except MemoriaHttpTransportError as exc:
        assert exc.error_type == "invalid_response"
        assert exc.retryable is False
        _assert_no_transport_secret(exc.public_summary)
    else:
        raise AssertionError("expected invalid response")
```

- [ ] **Step 4: Run red tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_adapter.py::test_urllib_sync_json_client_maps_5xx_http_error_to_retryable_transport_error tests\youtubebridge_v2\test_memoria_adapter.py::test_urllib_sync_json_client_maps_401_http_error_to_terminal_auth_failure tests\youtubebridge_v2\test_memoria_adapter.py::test_urllib_sync_json_client_maps_timeout_to_retryable_transport_error tests\youtubebridge_v2\test_memoria_adapter.py::test_urllib_sync_json_client_maps_urlerror_to_retryable_transport_failure tests\youtubebridge_v2\test_memoria_adapter.py::test_urllib_sync_json_client_maps_invalid_json_to_terminal_invalid_response -q
```

Expected: FAIL because `UrllibSyncJsonHttpClient` currently propagates raw stdlib exceptions or `ValueError`.

---

### Task 4: Urllib Error Mapping Green Implementation

**Files:**
- Modify: `YouTubeBridgeV2/adapters/memoria_http.py`
- Test: `tests/youtubebridge_v2/test_memoria_adapter.py`

- [ ] **Step 1: Import urllib errors**

In `memoria_http.py`:

```python
from json import JSONDecodeError
from urllib.error import HTTPError, URLError
```

- [ ] **Step 2: Wrap `post_json` failures**

Replace `UrllibSyncJsonHttpClient.post_json(...)` body with:

```python
        request = urllib_request.Request(
            url,
            data=json.dumps(dict(body), ensure_ascii=False).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except TimeoutError as exc:
            raise _transport_error("timeout", retryable=True) from exc
        except HTTPError as exc:
            raise _http_error(exc.code) from exc
        except URLError as exc:
            raise _transport_error("transport_failure", retryable=True) from exc

        try:
            decoded = json.loads(raw_body or "{}")
        except JSONDecodeError as exc:
            raise _transport_error("invalid_response", retryable=False) from exc
        if not isinstance(decoded, dict):
            raise _transport_error("invalid_response", retryable=False)
        return decoded
```

- [ ] **Step 3: Add error helpers**

Add:

```python
def _http_error(status_code: int | None) -> MemoriaHttpTransportError:
    if status_code in {401, 403}:
        return _transport_error(
            "auth_failure",
            retryable=False,
            status_code=status_code,
        )
    return _transport_error(
        "transport_failure",
        retryable=_status_is_retryable(status_code),
        status_code=status_code,
    )


def _transport_error(
    error_type: str,
    *,
    retryable: bool,
    status_code: int | None = None,
) -> MemoriaHttpTransportError:
    summary: dict[str, object] = {
        "error_type": error_type,
        "retryable": retryable,
    }
    if status_code is not None:
        summary["status_code"] = status_code
    return MemoriaHttpTransportError(
        error_type=error_type,
        retryable=retryable,
        status_code=status_code,
        public_summary=summary,
    )


def _status_is_retryable(status_code: object) -> bool:
    return isinstance(status_code, int) and status_code >= 500


def _public_error_summary(error: MemoriaHttpTransportError) -> dict[str, object]:
    summary = _redact_public_value(error.public_summary)
    if not summary:
        summary = {
            "error_type": error.error_type,
            "retryable": error.retryable,
        }
        if error.status_code is not None:
            summary["status_code"] = error.status_code
    return summary


_PUBLIC_FORBIDDEN_KEYS = {
    "authorization",
    "headers",
    "raw_payload",
    "raw_response",
    "secret",
    "token",
    "url",
}


def _redact_public_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _redact_public_value(inner)
            for key, inner in value.items()
            if str(key).lower() not in _PUBLIC_FORBIDDEN_KEYS
        }
    if isinstance(value, list):
        return [_redact_public_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_public_value(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        if "bearer " in lowered or "token=" in lowered:
            return "[redacted]"
    return value
```

- [ ] **Step 4: Run green tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_adapter.py::test_urllib_sync_json_client_maps_5xx_http_error_to_retryable_transport_error tests\youtubebridge_v2\test_memoria_adapter.py::test_urllib_sync_json_client_maps_401_http_error_to_terminal_auth_failure tests\youtubebridge_v2\test_memoria_adapter.py::test_urllib_sync_json_client_maps_timeout_to_retryable_transport_error tests\youtubebridge_v2\test_memoria_adapter.py::test_urllib_sync_json_client_maps_urlerror_to_retryable_transport_failure tests\youtubebridge_v2\test_memoria_adapter.py::test_urllib_sync_json_client_maps_invalid_json_to_terminal_invalid_response -q
```

Expected: `5 passed`.

---

### Task 5: Adapter Classification And Runtime Summary Red Tests

**Files:**
- Modify: `tests/youtubebridge_v2/test_memoria_adapter.py`
- Modify: `tests/youtubebridge_v2/test_runtime_memoria_runners.py`
- Modify later: `YouTubeBridgeV2/adapters/memoria.py`

- [ ] **Step 1: Add classifier tests**

Append to `test_memoria_adapter.py`:

```python
def test_classify_memoria_error_preserves_sanitized_transport_error_summary():
    error = classify_memoria_error(
        MemoriaHttpTransportError(
            error_type="invalid_response",
            retryable=False,
            public_summary={
                "error_type": "invalid_response",
                "retryable": False,
                "raw_payload": {"token": "secret-token"},
                "message": "bad json",
            },
        )
    )

    assert isinstance(error, MemoriaAdapterError)
    assert error.error_type == "invalid_response"
    assert error.retryable is False
    assert error.public_summary == {
        "error_type": "invalid_response",
        "retryable": False,
        "message": "bad json",
    }
    _assert_no_transport_secret(error.public_summary)
```

- [ ] **Step 2: Add runner summary redaction test**

In `test_runtime_memoria_runners.py`, import:

```python
from YouTubeBridgeV2.adapters.memoria_http import MemoriaHttpTransportError
```

Append:

```python
def test_runner_error_summary_redacts_http_transport_secret_payload():
    storage = InMemoryV2StorageManager()
    port = _create_bound_session(storage)
    runner = MemoriaPlannedShowRunner(
        storage,
        FakeMemoriaTransport(
            MemoriaHttpTransportError(
                error_type="transport_failure",
                retryable=True,
                status_code=503,
                public_summary={
                    "error_type": "transport_failure",
                    "retryable": True,
                    "status_code": 503,
                    "url": "http://127.0.0.1:8088/api/v1/chat/sync?token=secret",
                    "headers": {"Authorization": "Bearer secret-token"},
                    "raw_payload": {"token": "secret-token"},
                },
            )
        ),
    )

    result = runner.run(
        command=_command("cmd-http-secret"),
        snapshot=port.read_snapshot("session-runner"),
        transition=_transition(LiveSessionPhase.PLANNED_SHOW, action="run_planned_show"),
        now=NOW,
    )

    assert result.status == "error"
    assert result.retryable is True
    assert result.summary == {
        "error_type": "transport_failure",
        "retryable": True,
        "status_code": 503,
    }
    _assert_no_private_payload(result)
```

- [ ] **Step 3: Run red tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_adapter.py::test_classify_memoria_error_preserves_sanitized_transport_error_summary tests\youtubebridge_v2\test_runtime_memoria_runners.py::test_runner_error_summary_redacts_http_transport_secret_payload -q
```

Expected: FAIL because `classify_memoria_error(...)` currently treats custom retryable errors as generic `transport_failure` and does not preserve `invalid_response`.

---

### Task 6: Adapter Classification Green Implementation

**Files:**
- Modify: `YouTubeBridgeV2/adapters/memoria.py`
- Test: `tests/youtubebridge_v2/test_memoria_adapter.py`
- Test: `tests/youtubebridge_v2/test_runtime_memoria_runners.py`

- [ ] **Step 1: Update classifier to honor sanitized transport errors**

In `classify_memoria_error(...)`, after the `TimeoutError` branch and before the auth branch, add:

```python
    explicit_error_type = _optional_string(getattr(error, "error_type", None))
    explicit_retryable = getattr(error, "retryable", None)
    explicit_summary = getattr(error, "public_summary", None)
    if explicit_error_type and explicit_retryable is not None:
        summary = _redact_public_value(
            explicit_summary
            if isinstance(explicit_summary, dict)
            else {
                "error_type": explicit_error_type,
                "retryable": bool(explicit_retryable),
            }
        )
        if not summary:
            summary = {
                "error_type": explicit_error_type,
                "retryable": bool(explicit_retryable),
            }
        if status_code is not None and "status_code" not in summary:
            summary["status_code"] = status_code
        return MemoriaAdapterError(
            error_type=explicit_error_type,
            retryable=bool(explicit_retryable),
            status_code=status_code,
            public_summary=summary,
        )
```

Keep the existing 401/403 branch before generic `hasattr(status_code)` if tests expect raw auth exceptions to remain `auth_failure`; if explicit transport errors already set `auth_failure`, the new branch preserves it.

- [ ] **Step 2: Run green tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_adapter.py::test_classify_memoria_error_preserves_sanitized_transport_error_summary tests\youtubebridge_v2\test_runtime_memoria_runners.py::test_runner_error_summary_redacts_http_transport_secret_payload -q
```

Expected: `2 passed`.

---

### Task 7: Docs Sync

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/memoria-adapter.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`

- [ ] **Step 1: Update module design**

In `docs/modules/memoria-adapter.md`, under `Public Entrypoints`, add:

```markdown
- `MemoriaHttpTransportError`：sync HTTP transport 產生的 sanitized error，提供 `error_type`、`retryable`、`status_code` 與 public-safe summary。
```

Under `Failure Modes`, add:

```markdown
- HTTP timeout 與 5xx transport failure 可依 `max_attempts` retry；重試耗盡後回 retryable sanitized error。
- HTTP 401/403 auth failure 為 terminal，不重試，public summary 只保留 `error_type`、`retryable` 與 `status_code`。
- HTTP invalid JSON 或 non-object JSON response 為 terminal `invalid_response`，不得把 raw body 寫進 public summary。
```

- [ ] **Step 2: Update API reference**

In `docs/api-reference-index.md`, under MemoriaCore Adapter Concepts and Sources, add:

```markdown
- `MemoriaHttpTransportError`
```

```markdown
- `YouTubeBridgeV2/adapters/memoria_http.py::MemoriaHttpTransportError`
```

- [ ] **Step 3: Update architecture index**

In `docs/architecture-index.md`, after `Integration Wave 2E-A 狀態`, add:

```markdown
## Integration Wave 2E-B 狀態

- [x] Timeout / 5xx retry：`MemoriaSyncHttpTransport` 會依 `max_attempts` retry retryable transport errors。
- [x] Auth terminal mapping：HTTP 401/403 會轉成 terminal `auth_failure`，不重試。
- [x] Invalid response mapping：invalid JSON / non-object JSON response 會轉成 terminal `invalid_response`。
- [x] Sanitized error summary：URL、headers、token、authorization 與 raw payload 不進入 transport public summary 或 runner adapter summary。
- [ ] Real MemoriaCore integration harness：仍保留給 Wave 2E-C。
- [ ] Production wiring toggle：仍保留給 Wave 2E-D。
```

- [ ] **Step 4: Docs sanity check**

Run:

```powershell
rg -n "MemoriaHttpTransportError|Integration Wave 2E-B|invalid_response|auth_failure" YouTubeBridgeV2\docs
```

Expected: Finds 2E-B module/API/architecture docs.

---

### Task 8: Final Verification For 2E-B

**Files:**
- Verify: `YouTubeBridgeV2/adapters/memoria_http.py`
- Verify: `YouTubeBridgeV2/adapters/memoria.py`
- Verify: `tests/youtubebridge_v2/test_memoria_adapter.py`
- Verify: `tests/youtubebridge_v2/test_runtime_memoria_runners.py`
- Verify: `tests/youtubebridge_v2/test_runtime_tick_vertical_slice.py`
- Verify: V2 docs touched in Task 7

- [ ] **Step 1: Run focused tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_adapter.py -q
python -m pytest tests\youtubebridge_v2\test_runtime_memoria_runners.py -q
python -m pytest tests\youtubebridge_v2\test_runtime_tick_vertical_slice.py -q
```

Expected: all pass.

- [ ] **Step 2: Run roadmap verification commands for Wave 2E**

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

- [ ] **Step 3: Check scope and forbidden imports**

Run:

```powershell
git status -sb
git diff --stat
rg -n "sqlite3|aiosqlite|from YouTubeBridge|import YouTubeBridge" YouTubeBridgeV2\adapters\memoria_http.py YouTubeBridgeV2\adapters\memoria.py tests\youtubebridge_v2\test_memoria_adapter.py tests\youtubebridge_v2\test_runtime_memoria_runners.py
```

Expected:

- Changed files are limited to 2E-B source/tests/docs/plan.
- No direct sqlite imports.
- No Legacy `YouTubeBridge` runtime imports.

- [ ] **Step 4: Request code review**

Use `superpowers:requesting-code-review` with scope limited to roadmap `2E-B`.

- [ ] **Step 5: Commit**

After review findings are fixed and verification is fresh:

```powershell
git add YouTubeBridgeV2\adapters\memoria_http.py YouTubeBridgeV2\adapters\memoria.py tests\youtubebridge_v2\test_memoria_adapter.py tests\youtubebridge_v2\test_runtime_memoria_runners.py YouTubeBridgeV2\docs\modules\memoria-adapter.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\implementation-plans\memoria-transport-error-mapping.md
git diff --cached --check
git commit -m "feat: harden Memoria HTTP transport errors"
```

---

## Self-Review

Spec coverage:

- `timeout 與 5xx 可 retry` covered by `MemoriaSyncHttpTransport` retry tests with timeout and 503 first-attempt failures.
- `401/403 terminal` covered by auth failure test asserting one call and no retry.
- `錯誤 response 不外洩 URL secret、header、token 或 raw payload` covered by urllib mapping tests, classifier test, and runner adapter summary redaction test.
- Review follow-up guards cover secret-bearing `base_url` rejection, secret-like summary string redaction, and required `error_type` / `retryable` fields in partial transport summaries.
- `不要跨 wave` covered by leaving real integration harness and production prefs toggle out of scope.

Placeholder scan:

- No TBD / TODO / implement later placeholders.
- Every code-changing step includes concrete code or exact command.

Type consistency:

- `MemoriaHttpTransportError` exposes `error_type`, `retryable`, `status_code`, and `public_summary`, matching `classify_memoria_error(...)`.
- `MemoriaHttpTransportConfig.max_attempts` is parsed from explicit config and used only by `MemoriaSyncHttpTransport`.
- `UrllibSyncJsonHttpClient.post_json(...)` still returns `dict[str, object]` on success, matching `SyncJsonHttpClientProtocol`.

## Execution Handoff

Plan complete and saved to `YouTubeBridgeV2/docs/implementation-plans/memoria-transport-error-mapping.md`. Two execution options:

**1. Subagent-Driven (recommended)** - dispatch a fresh worker for focused TDD tasks, then review.

**2. Inline Execution** - execute tasks in this session using `superpowers:executing-plans`, with checkpoints for review.
