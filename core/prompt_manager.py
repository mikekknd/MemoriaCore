"""
PromptManager — 集中式 Prompt 管理器
- 載入順序：prompts.json（使用者自訂）> prompts_default.json（系統內建）
- 支援 runtime 修改、單筆/全部重置、placeholder 驗證
"""
import json, os, shutil
from core.system_logger import SystemLogger

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_PATH = os.path.join(_BASE_DIR, "prompts_default.json")
_USER_PATH = os.path.join(_BASE_DIR, "prompts.json")


class PromptManager:
    def __init__(self):
        self._defaults: dict = {}
        self._user: dict = {}
        self._load()

    # ── 載入 ──────────────────────────────────────────────
    def _load(self):
        """載入內建預設 + 使用者自訂（若存在）"""
        # 內建預設（必須存在）
        with open(_DEFAULT_PATH, "r", encoding="utf-8") as f:
            self._defaults = json.load(f)

        # 使用者自訂（可選）
        if os.path.exists(_USER_PATH):
            try:
                with open(_USER_PATH, "r", encoding="utf-8") as f:
                    self._user = json.load(f)
            except Exception as e:
                SystemLogger.log_error("PromptManager", f"prompts.json 讀取失敗，使用內建預設: {e}")
                self._user = {}
        else:
            self._user = {}

    # ── 取用 ──────────────────────────────────────────────
    def get(self, key: str) -> str:
        """取得指定 prompt 的 template（優先使用者自訂，fallback 到內建預設）"""
        # 使用者自訂
        if key in self._user and "template" in self._user[key]:
            return self._user[key]["template"]
        # 內建預設
        if key in self._defaults and "template" in self._defaults[key]:
            return self._defaults[key]["template"]
        raise KeyError(f"Prompt key '{key}' not found in defaults or user overrides.")

    def get_default(self, key: str) -> str:
        """取得內建預設的 template"""
        if key in self._defaults and "template" in self._defaults[key]:
            return self._defaults[key]["template"]
        raise KeyError(f"Prompt key '{key}' not found in defaults.")

    def get_meta(self, key: str) -> dict:
        """取得 prompt 的 metadata（label, description, placeholders 等）"""
        source = self._defaults.get(key, {})
        user_override = self._user.get(key, {})
        return {
            "label": source.get("label", key),
            "description": source.get("description", ""),
            "used_by": source.get("used_by", ""),
            "placeholders": source.get("placeholders", []),
            "has_user_override": key in self._user and "template" in self._user.get(key, {}),
            "current_template": self.get(key),
            "default_template": source.get("template", ""),
        }

    def list_keys(self) -> list[str]:
        """列出所有可用的 prompt key（排除 _description 等 meta 欄位）"""
        keys = set()
        for k in self._defaults:
            if not k.startswith("_"):
                keys.add(k)
        for k in self._user:
            if not k.startswith("_"):
                keys.add(k)
        # 保持一致的排序
        return sorted(keys)

    # ── 修改 ──────────────────────────────────────────────
    def update(self, key: str, new_template: str) -> None:
        """更新指定 prompt（寫入使用者自訂檔）"""
        if key not in self._defaults:
            raise KeyError(f"Prompt key '{key}' not found in defaults. Cannot create new keys via update.")

        if key not in self._user:
            # 複製 metadata 從 defaults
            self._user[key] = {
                "label": self._defaults[key].get("label", key),
                "description": self._defaults[key].get("description", ""),
                "used_by": self._defaults[key].get("used_by", ""),
                "placeholders": self._defaults[key].get("placeholders", []),
            }
        self._user[key]["template"] = new_template
        self._save_user()
        SystemLogger.log_system_event("PromptManager", f"已更新 prompt: {key}")

    def reset_one(self, key: str) -> str:
        """重置指定 prompt 為內建預設"""
        if key in self._user:
            del self._user[key]
            self._save_user()
            SystemLogger.log_system_event("PromptManager", f"已重置 prompt: {key}")
        return self.get_default(key)

    def reset_all(self) -> None:
        """重置所有 prompt 為內建預設（刪除 prompts.json）"""
        self._user = {}
        if os.path.exists(_USER_PATH):
            os.remove(_USER_PATH)
        SystemLogger.log_system_event("PromptManager", "已重置所有 prompt 為內建預設")

    # ── 儲存 ──────────────────────────────────────────────
    def _save_user(self):
        """將使用者自訂寫入 prompts.json"""
        if not self._user:
            # 無自訂內容，刪除檔案
            if os.path.exists(_USER_PATH):
                os.remove(_USER_PATH)
            return
        with open(_USER_PATH, "w", encoding="utf-8") as f:
            json.dump(self._user, f, ensure_ascii=False, indent=2)

    # ── 重新載入 ──────────────────────────────────────────
    def reload(self):
        """從磁碟重新載入（用於外部修改後同步）"""
        self._load()


# ── 全域單例 ──────────────────────────────────────────────
_instance: PromptManager | None = None


def get_prompt_manager() -> PromptManager:
    global _instance
    if _instance is None:
        _instance = PromptManager()
    return _instance
