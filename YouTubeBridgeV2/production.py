"""Production wiring helpers for YouTubeBridgeV2.

本模組只把主專案 `StorageManager` singleton 接到 V2 composition。外部服務
runner 在 Wave 2B 維持顯式 no-op，避免啟動 8088 時意外呼叫 YouTube、
MemoriaCore 或 TTS。
"""

from __future__ import annotations

from YouTubeBridgeV2.composition import V2RuntimeComposition, create_v2_composition
from YouTubeBridgeV2.runtime.noop_runners import (
    NoopAftertalkRunner,
    NoopClosingRunner,
    NoopPlannedShowRunner,
)


def create_production_v2_composition(storage_manager: object) -> V2RuntimeComposition:
    """以主專案 StorageManager 建立 production V2 composition。"""

    return create_v2_composition(
        storage_manager=storage_manager,
        planned_show_runner=NoopPlannedShowRunner(),
        aftertalk_runner=NoopAftertalkRunner(),
        closing_runner=NoopClosingRunner(),
    )


__all__ = ["create_production_v2_composition"]
