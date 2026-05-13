"""Sync MemoriaCore HTTP transport boundary for YouTubeBridgeV2.

本模組只建立可注入 HTTP transport 與設定解析邊界。Production 是否啟用
真外呼由後續 wiring toggle 決定；未顯式注入時仍應保持 no-op runner。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from json import JSONDecodeError
from typing import Mapping, Protocol
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse

from YouTubeBridgeV2.adapters.memoria import MemoriaRequestPayload


MEMORIA_TRANSPORT_PREFS_KEY = "youtubebridge_v2_memoria_transport"


class MemoriaHttpConfigError(ValueError):
    """MemoriaCore HTTP transport 設定不符合 V2 contract。"""


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


@dataclass(frozen=True)
class MemoriaHttpTransportConfig:
    """可安全顯示摘要的 MemoriaCore HTTP transport 設定。"""

    base_url: str
    api_key: str | None = field(default=None, repr=False)
    timeout_seconds: float = 10.0
    max_attempts: int = 2

    def __post_init__(self) -> None:
        cleaned_base_url = _normalize_base_url(self.base_url)
        timeout = _coerce_timeout(self.timeout_seconds)
        attempts = _coerce_max_attempts(self.max_attempts)
        object.__setattr__(self, "base_url", cleaned_base_url)
        object.__setattr__(self, "timeout_seconds", timeout)
        object.__setattr__(self, "max_attempts", attempts)
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
            "max_attempts": self.max_attempts,
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
        max_attempts=_raw_max_attempts(raw_config.get("max_attempts")),
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
    if (
        parsed.username
        or parsed.password
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise MemoriaHttpConfigError(
            "base_url must not include credentials, params, query, or fragment"
        )
    return text


def _raw_timeout(value: object) -> float:
    if value is None:
        return 10.0
    return _coerce_timeout(value)


def _raw_max_attempts(value: object) -> int:
    if value is None:
        return 2
    return _coerce_max_attempts(value)


def _coerce_timeout(value: object) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise MemoriaHttpConfigError("timeout_seconds must be numeric") from exc
    if timeout <= 0:
        raise MemoriaHttpConfigError("timeout_seconds must be greater than zero")
    return timeout


def _coerce_max_attempts(value: object) -> int:
    try:
        attempts = int(value)
    except (TypeError, ValueError) as exc:
        raise MemoriaHttpConfigError("max_attempts must be an integer") from exc
    if attempts < 1:
        raise MemoriaHttpConfigError("max_attempts must be at least one")
    return attempts


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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
    if isinstance(summary, dict) and summary:
        summary.setdefault("error_type", error.error_type)
        summary.setdefault("retryable", error.retryable)
        if error.status_code is not None:
            summary.setdefault("status_code", error.status_code)
        return summary
    if summary:
        public_summary: dict[str, object] = {
            "message": summary,
            "error_type": error.error_type,
            "retryable": error.retryable,
        }
        if error.status_code is not None:
            public_summary["status_code"] = error.status_code
        return public_summary
    summary: dict[str, object] = {
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


_PUBLIC_FORBIDDEN_TEXT = (
    "authorization",
    "bearer ",
    "secret-token",
    "token=",
    "x-api-key",
)


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
        if any(marker in lowered for marker in _PUBLIC_FORBIDDEN_TEXT):
            return "[redacted]"
    return value


__all__ = [
    "MEMORIA_TRANSPORT_PREFS_KEY",
    "MemoriaHttpConfigError",
    "MemoriaHttpTransportConfig",
    "MemoriaHttpTransportError",
    "MemoriaSyncHttpTransport",
    "SyncJsonHttpClientProtocol",
    "UrllibSyncJsonHttpClient",
    "load_memoria_http_transport_config",
    "parse_memoria_http_transport_config",
]
