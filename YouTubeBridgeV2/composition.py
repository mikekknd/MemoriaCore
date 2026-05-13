"""YouTubeBridgeV2 runtime composition root.

Composition 只負責把 V2 runtime service、storage port 與 query service 組合
起來。呼叫端必須注入 StorageManager-like backend 與需要的 runtime runner。
"""

from __future__ import annotations

from dataclasses import dataclass

from YouTubeBridgeV2.query_service import V2QueryService
from YouTubeBridgeV2.runtime.application_service import RuntimeApplicationService
from YouTubeBridgeV2.storage.runtime_store import RuntimeStoragePort


class V2CompositionConfigurationError(RuntimeError):
    """V2 composition 缺少必要 backend 時拋出的設定錯誤."""


@dataclass(frozen=True)
class V2RuntimeComposition:
    """V2 app/runtime/query 的組裝結果."""

    runtime_service: RuntimeApplicationService
    query_service: V2QueryService
    storage: RuntimeStoragePort
    storage_manager: object


def create_v2_composition(
    *,
    storage_manager: object | None,
    planned_show_runner: object | None = None,
    aftertalk_runner: object | None = None,
    closing_runner: object | None = None,
) -> V2RuntimeComposition:
    """建立 YouTubeBridgeV2 runtime composition。

    Args:
        storage_manager: StorageManager-like V2 backend；不得是 SQLite connection。
        planned_show_runner: `run(...)` contract 的正式節目 runner。
        aftertalk_runner: `run(...)` contract 的 aftertalk runner。
        closing_runner: `run(...)` contract 的 closing runner。

    Returns:
        V2RuntimeComposition: 可交給 `create_v2_app(...)` 的 wiring。

    Raises:
        V2CompositionConfigurationError: 未提供 storage backend。

    Side Effects:
        無；僅建立物件與注入 dependency。
    """

    if storage_manager is None:
        raise V2CompositionConfigurationError("storage_manager is required")

    storage = RuntimeStoragePort(storage_manager)
    runtime_service = RuntimeApplicationService(
        storage=storage,
        planned_show_runner=planned_show_runner,
        aftertalk=aftertalk_runner,
        closing=closing_runner,
    )
    query_service = V2QueryService(storage_manager)
    return V2RuntimeComposition(
        runtime_service=runtime_service,
        query_service=query_service,
        storage=storage,
        storage_manager=storage_manager,
    )


__all__ = [
    "V2CompositionConfigurationError",
    "V2RuntimeComposition",
    "create_v2_composition",
]
