"""Production wiring helpers for YouTubeBridgeV2.

本模組只把主專案 `StorageManager` singleton 接到 V2 composition。未顯式
注入 Memoria transport 且 prefs 未明確啟用時維持 no-op runner，避免啟動
8088 時意外呼叫 YouTube、MemoriaCore 或 TTS。
"""

from __future__ import annotations

from collections.abc import Mapping

from YouTubeBridgeV2.adapters.memoria_http import (
    MEMORIA_TRANSPORT_PREFS_KEY,
    MemoriaHttpConfigError,
    MemoriaSyncHttpTransport,
    parse_memoria_http_transport_config,
)
from YouTubeBridgeV2.composition import V2RuntimeComposition, create_v2_composition
from YouTubeBridgeV2.runtime.noop_runners import (
    NoopAftertalkRunner,
    NoopClosingRunner,
    NoopPlannedShowRunner,
)


def create_production_v2_composition(
    storage_manager: object,
    *,
    memoria_transport: object | None = None,
) -> V2RuntimeComposition:
    """以主專案 StorageManager 建立 production V2 composition。"""

    resolved_memoria_transport = memoria_transport
    if resolved_memoria_transport is None:
        resolved_memoria_transport = load_production_memoria_transport(storage_manager)

    if resolved_memoria_transport is not None:
        from YouTubeBridgeV2.runtime.memoria_runners import (
            MemoriaAftertalkRunner,
            MemoriaClosingRunner,
            MemoriaPlannedShowRunner,
        )

        return create_v2_composition(
            storage_manager=storage_manager,
            planned_show_runner=MemoriaPlannedShowRunner(
                storage_manager,
                resolved_memoria_transport,
            ),
            aftertalk_runner=MemoriaAftertalkRunner(
                storage_manager,
                resolved_memoria_transport,
            ),
            closing_runner=MemoriaClosingRunner(
                storage_manager,
                resolved_memoria_transport,
            ),
        )

    return create_v2_composition(
        storage_manager=storage_manager,
        planned_show_runner=NoopPlannedShowRunner(),
        aftertalk_runner=NoopAftertalkRunner(),
        closing_runner=NoopClosingRunner(),
    )


def load_production_memoria_transport(storage_manager: object) -> object | None:
    """Load the opt-in MemoriaCore HTTP transport for production V2."""

    raw_config = _raw_memoria_transport_config(storage_manager)
    if not _production_memoria_transport_enabled(raw_config):
        return None
    try:
        config = parse_memoria_http_transport_config(raw_config)
    except MemoriaHttpConfigError:
        return None
    if config is None:
        return None
    return MemoriaSyncHttpTransport(config)


def _raw_memoria_transport_config(storage_manager: object) -> Mapping[str, object] | None:
    if not hasattr(storage_manager, "load_prefs"):
        return None
    try:
        prefs = storage_manager.load_prefs()
    except Exception:
        return None
    if not isinstance(prefs, Mapping):
        return None
    raw_config = prefs.get(MEMORIA_TRANSPORT_PREFS_KEY)
    if isinstance(raw_config, Mapping):
        return raw_config
    return None


def _production_memoria_transport_enabled(
    raw_config: Mapping[str, object] | None,
) -> bool:
    if raw_config is None:
        return False
    return _truthy(raw_config.get("enabled"))


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["create_production_v2_composition", "load_production_memoria_transport"]
