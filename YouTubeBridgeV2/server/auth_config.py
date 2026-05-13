"""YouTubeBridgeV2 主 app API key 設定解析。"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
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


@dataclass(frozen=True)
class V2ApiKeyPublicEntry:
    """可回傳給 operator UI 的 API key 摘要，不包含 raw key。"""

    key_fingerprint: str
    key_prefix: str
    permission_group: str


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


def list_v2_api_key_entries(storage_manager: object) -> list[V2ApiKeyPublicEntry]:
    """列出 sanitized API key entries，不回傳 raw key。"""

    prefs = _load_prefs(storage_manager)
    return [_public_entry(key, group) for key, group in _valid_raw_entries(prefs)]


def upsert_v2_api_key_entry(
    storage_manager: object,
    *,
    key: str,
    permission_group: str,
) -> V2ApiKeyPublicEntry:
    """新增或更新一組 V2 API key，回傳 sanitized public entry。"""

    normalized_key = str(key or "").strip()
    group = _coerce_public_group(permission_group)
    if not normalized_key or group is None:
        raise ValueError("invalid api key entry")

    prefs = _load_prefs(storage_manager)
    entries = [
        {"key": existing_key, "permission_group": existing_group.value}
        for existing_key, existing_group in _valid_raw_entries(prefs)
        if _fingerprint(existing_key) != _fingerprint(normalized_key)
    ]
    entries.append({"key": normalized_key, "permission_group": group.value})
    prefs[V2_API_KEYS_PREFS_KEY] = entries
    _save_prefs(storage_manager, prefs)
    return _public_entry(normalized_key, group)


def delete_v2_api_key_entry(storage_manager: object, *, key_fingerprint: str) -> int:
    """依 key fingerprint 撤銷 V2 API key，回傳移除筆數。"""

    fingerprint = str(key_fingerprint or "").strip().lower()
    prefs = _load_prefs(storage_manager)
    kept: list[dict[str, str]] = []
    removed = 0
    for existing_key, existing_group in _valid_raw_entries(prefs):
        if _fingerprint(existing_key) == fingerprint:
            removed += 1
            continue
        kept.append({"key": existing_key, "permission_group": existing_group.value})
    prefs[V2_API_KEYS_PREFS_KEY] = kept
    _save_prefs(storage_manager, prefs)
    return removed


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


def _save_prefs(storage_manager: object, prefs: dict[str, object]) -> None:
    if not hasattr(storage_manager, "save_prefs"):
        raise RuntimeError("storage manager does not support save_prefs")
    storage_manager.save_prefs(prefs)


def _valid_raw_entries(prefs: dict[str, object]) -> list[tuple[str, PermissionGroup]]:
    raw_entries = prefs.get(V2_API_KEYS_PREFS_KEY, [])
    if not isinstance(raw_entries, list):
        return []

    entries: list[tuple[str, PermissionGroup]] = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").strip()
        if not key:
            continue
        group = _coerce_public_group(entry.get("permission_group"))
        if group is None:
            continue
        entries.append((key, group))
    return entries


def _public_entry(key: str, group: PermissionGroup) -> V2ApiKeyPublicEntry:
    fingerprint = _fingerprint(key)
    return V2ApiKeyPublicEntry(
        key_fingerprint=fingerprint,
        key_prefix=fingerprint[:12],
        permission_group=group.value,
    )


def _fingerprint(key: str) -> str:
    return sha256(key.encode("utf-8")).hexdigest()


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
    "V2ApiKeyPublicEntry",
    "delete_v2_api_key_entry",
    "list_v2_api_key_entries",
    "load_v2_api_key_config",
    "upsert_v2_api_key_entry",
]
