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
