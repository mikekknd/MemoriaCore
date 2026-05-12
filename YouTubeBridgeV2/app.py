"""YouTubeBridgeV2 FastAPI app factory.

本模組只負責把 V2 routes 與已建立的 composition 接起來。它不自行建立
runtime dependency，也不讀取 Legacy YouTubeBridge 狀態。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from YouTubeBridgeV2.server import routes


class V2AppConfigurationError(RuntimeError):
    """V2 app factory 缺少必要 composition 時拋出的設定錯誤."""


def create_v2_app(
    composition: object | None,
    *,
    now_provider: Callable[[], datetime] | None = None,
) -> FastAPI:
    """建立獨立 YouTubeBridgeV2 FastAPI app。

    Args:
        composition: 已由 `create_v2_composition(...)` 建立的 runtime wiring。
        now_provider: 測試可注入的 deterministic clock。

    Returns:
        FastAPI: 已掛載 `/v2` routes 與 `/v2/static` 的 app。

    Raises:
        V2AppConfigurationError: 未提供 composition 或必要 service。

    Side Effects:
        建立 FastAPI dependency overrides；不啟動 server、不呼叫外部 API。
    """

    if composition is None:
        raise V2AppConfigurationError("YouTubeBridgeV2 composition is required")
    runtime_service = _required_attr(composition, "runtime_service")
    query_service = _required_attr(composition, "query_service")
    clock = now_provider or (lambda: datetime.now(timezone.utc))

    app = FastAPI(
        title="YouTubeBridgeV2 API",
        description="YouTubeBridgeV2 integration app factory",
        version="0.1.0",
    )
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_runtime_service] = lambda: runtime_service
    app.dependency_overrides[routes.get_query_service] = lambda: query_service
    app.dependency_overrides[routes.get_now] = clock

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount(
            "/v2/static",
            StaticFiles(directory=str(static_dir), html=True),
            name="youtubebridge-v2-static",
        )
    return app


def _required_attr(composition: object, name: str) -> Any:
    value = getattr(composition, name, None)
    if value is None:
        raise V2AppConfigurationError(f"YouTubeBridgeV2 composition missing {name}")
    return value


__all__ = ["V2AppConfigurationError", "create_v2_app"]
