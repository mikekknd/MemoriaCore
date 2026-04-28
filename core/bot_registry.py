"""Bot registry：集中管理外部平台 bot token 與角色綁定。"""
from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from typing import Any


BOT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")
SUPPORTED_PLATFORMS = {"telegram", "discord", "other"}


class BotRegistryError(ValueError):
    """Bot registry 驗證錯誤。"""


class BotRegistry:
    """讀寫 bot_configs.json，並處理舊 telegram_bot_token 遷移。"""

    def __init__(self, configs_file: str = "bot_configs.json"):
        self.configs_file = configs_file

    def load_configs(self, prefs: dict | None = None) -> list[dict[str, Any]]:
        """讀取所有 bot 設定；首次載入時可由舊 telegram_bot_token 建立 legacy 設定。"""
        if not os.path.exists(self.configs_file):
            migrated = self._legacy_configs_from_prefs(prefs or {})
            if migrated:
                self.save_configs(migrated)
                return migrated
            return []

        try:
            with open(self.configs_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return []

        if not isinstance(raw, list):
            return []
        return [self.normalize_config(c) for c in raw if isinstance(c, dict)]

    def save_configs(self, configs: list[dict[str, Any]]) -> None:
        normalized = [self.normalize_config(c) for c in configs]
        with open(self.configs_file, "w", encoding="utf-8") as f:
            json.dump(normalized, f, ensure_ascii=False, indent=2)

    def list_configs(self, prefs: dict | None = None) -> list[dict[str, Any]]:
        return deepcopy(self.load_configs(prefs))

    def get_config(self, bot_id: str, prefs: dict | None = None) -> dict[str, Any] | None:
        for cfg in self.load_configs(prefs):
            if cfg.get("bot_id") == bot_id:
                return cfg
        return None

    def upsert_config(
        self,
        config: dict[str, Any],
        *,
        character_ids: set[str],
        prefs: dict | None = None,
        create: bool = False,
    ) -> dict[str, Any]:
        configs = self.load_configs(prefs)
        cfg = self.normalize_config(config)
        self.validate_config(cfg, character_ids=character_ids)

        found = False
        for idx, existing in enumerate(configs):
            if existing.get("bot_id") == cfg["bot_id"]:
                if create:
                    raise BotRegistryError("bot_id 已存在")
                configs[idx] = cfg
                found = True
                break
        if not found:
            configs.append(cfg)

        self.validate_configs(configs, character_ids=character_ids)
        self.save_configs(configs)
        return cfg

    def delete_config(self, bot_id: str, prefs: dict | None = None) -> bool:
        configs = self.load_configs(prefs)
        kept = [c for c in configs if c.get("bot_id") != bot_id]
        if len(kept) == len(configs):
            return False
        self.save_configs(kept)
        return True

    def configs_using_character(self, character_id: str, prefs: dict | None = None) -> list[dict[str, Any]]:
        return [c for c in self.load_configs(prefs) if c.get("character_id") == character_id]

    @staticmethod
    def normalize_config(config: dict[str, Any]) -> dict[str, Any]:
        return {
            "bot_id": str(config.get("bot_id", "")).strip(),
            "platform": str(config.get("platform", "telegram")).strip().lower() or "telegram",
            "display_name": str(config.get("display_name", "")).strip(),
            "character_id": str(config.get("character_id", "default")).strip() or "default",
            "token": str(config.get("token", "")).strip(),
            "enabled": bool(config.get("enabled", False)),
        }

    @staticmethod
    def validate_config(config: dict[str, Any], *, character_ids: set[str]) -> None:
        bot_id = config.get("bot_id", "")
        platform = config.get("platform", "")
        character_id = config.get("character_id", "")
        if not BOT_ID_RE.fullmatch(bot_id):
            raise BotRegistryError("bot_id 必須為 3-64 字元，且只能包含英數、底線、連字號")
        if platform not in SUPPORTED_PLATFORMS:
            raise BotRegistryError("platform 必須是 telegram、discord 或 other")
        if character_id not in character_ids:
            raise BotRegistryError(f"character_id 不存在: {character_id}")
        if config.get("enabled") and platform == "telegram" and not config.get("token"):
            raise BotRegistryError("enabled Telegram bot 必須提供 token")

    def validate_configs(self, configs: list[dict[str, Any]], *, character_ids: set[str]) -> None:
        seen_ids: set[str] = set()
        seen_enabled_telegram_tokens: set[str] = set()
        for cfg in configs:
            self.validate_config(cfg, character_ids=character_ids)
            bot_id = cfg["bot_id"]
            if bot_id in seen_ids:
                raise BotRegistryError(f"bot_id 重複: {bot_id}")
            seen_ids.add(bot_id)
            token = cfg.get("token", "")
            if cfg.get("enabled") and cfg.get("platform") == "telegram" and token:
                if token in seen_enabled_telegram_tokens:
                    raise BotRegistryError("enabled Telegram token 不可重複")
                seen_enabled_telegram_tokens.add(token)

    @staticmethod
    def _legacy_configs_from_prefs(prefs: dict[str, Any]) -> list[dict[str, Any]]:
        token = str(prefs.get("telegram_bot_token", "") or "").strip()
        if not token:
            return []
        return [{
            "bot_id": "legacy-telegram",
            "platform": "telegram",
            "display_name": "Legacy Telegram",
            "character_id": prefs.get("active_character_id", "default") or "default",
            "token": token,
            "enabled": True,
        }]
