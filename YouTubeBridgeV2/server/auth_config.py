"""YouTubeBridgeV2 主 app API key 設定解析。"""

from __future__ import annotations

from typing import Mapping

from YouTubeBridgeV2.server.security import PermissionGroup


V2_API_KEYS_PREFS_KEY = "youtubebridge_v2_api_keys"
_PUBLIC_API_KEY_GROUPS = {
    PermissionGroup.OPERATOR,
    PermissionGroup.DISPLAY,
    PermissionGroup.OBSERVER,
}


class V2ApiKeyConfig:
    """從 StorageManager prefs 載入後的 V2 API key 設定。"""

    __slots__ = ("_valid_api_keys",)

    def __init__(self, valid_api_keys: Mapping[str, PermissionGroup] | None = None) -> None:
        self._valid_api_keys = dict(valid_api_keys or {})

    def __repr__(self) -> str:
        return f"V2ApiKeyConfig(valid_api_key_count={len(self._valid_api_keys)})"

    def as_auth_mapping(self) -> dict[str, PermissionGroup]:
        """回傳 `AuthRequirement` 使用的 API key mapping。"""

        return dict(self._valid_api_keys)


def load_v2_api_key_config(storage_manager: object) -> V2ApiKeyConfig:
    """從 `StorageManager.load_prefs()` 載入 V2 API key。

    無效 entry 會被排除，讓 production surface 採 fail-closed，而不是把錯誤設定
    誤當成公開存取。
    """

    prefs = _load_prefs(storage_manager)
    raw_entries = prefs.get(V2_API_KEYS_PREFS_KEY, [])
    valid: dict[str, PermissionGroup] = {}
    if not isinstance(raw_entries, list):
        return V2ApiKeyConfig(valid)

    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").strip()
        if not key:
            continue
        group = _coerce_public_group(entry.get("permission_group"))
        if group is None:
            continue
        valid[key] = group
    return V2ApiKeyConfig(valid)


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


def _coerce_public_group(value: object) -> PermissionGroup | None:
    try:
        group = PermissionGroup(str(value or "").strip())
    except ValueError:
        return None
    if group not in _PUBLIC_API_KEY_GROUPS:
        return None
    return group


__all__ = [
    "V2_API_KEYS_PREFS_KEY",
    "V2ApiKeyConfig",
    "load_v2_api_key_config",
]
