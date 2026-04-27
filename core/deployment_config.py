"""部署情境設定與 Context 解析工具。

三維度正交隔離的入口：
- user_id    × visibility 決定資料歸屬與可見性
- persona_face 決定人格面向（private/public）

任何新增的 channel 都應在此模組登記，而非分散在各 router。
"""
import os

# 公開頻道：即使 SU 自己留言也視為 public face
PUBLIC_CHANNELS: frozenset[str] = frozenset({'livestream', 'discord_public'})

# 應對哪些 channel 的用戶進行 profile 抽取
EXTRACT_PROFILE_FROM_CHANNELS: frozenset[str] = frozenset({
    'telegram', 'rest', 'discord_private', 'dashboard',
})

# SU（SuperUser）的識別 ID
# 優先取環境變數；未設定則從 user_prefs.json 讀取（不啟動時 fallback）
SU_USER_ID: str = os.getenv('SU_USER_ID', '')

_cached_su_id: str | None = None  # 模組層級 cache，避免每次 I/O


def _load_su_user_id_from_prefs() -> str:
    """從 user_prefs.json 讀取 su_user_id（供 runtime fallback 使用）。"""
    try:
        prefs_path = os.path.join(os.path.dirname(__file__), "..", "user_prefs.json")
        if os.path.exists(prefs_path):
            import json
            with open(prefs_path, "r", encoding="utf-8") as f:
                prefs = json.load(f)
            return prefs.get("su_user_id", "") or ""
    except Exception:
        pass
    return ""


def get_su_user_id() -> str:
    """取得 SU_USER_ID（env var 優先，其次為 prefs.json）。結果會 cache。"""
    global _cached_su_id
    if _cached_su_id is None:
        _cached_su_id = os.getenv('SU_USER_ID', '') or _load_su_user_id_from_prefs()
    return _cached_su_id


def invalidate_su_id_cache() -> None:
    """清除 SU ID cache，讓下次 get_su_user_id() 重新讀取。
    適用於管理者透過 API 更新 su_user_id 後熱重載。"""
    global _cached_su_id
    _cached_su_id = None


def resolve_context(user_id: str, channel: str) -> tuple[str, str]:
    """根據 user_id + channel 決定 (persona_face, write_visibility)。

    回傳值：
        persona_face     — 'private' | 'public'，決定 AI 以哪個人格回應
        write_visibility — 'private' | 'public'，決定本次互動寫入記憶的可見性

    規則：
    - 公開頻道（livestream / discord_public）→ public / public
      即使 SU 在直播留言也是 public（SU 故意隱瞞私事是 feature）
    - SU 私訊（telegram 或 rest，且 user_id == SU_USER_ID）→ private / private
    - 其他所有情況 → public / public
    """
    if channel in PUBLIC_CHANNELS:
        return ('public', 'public')
    if get_su_user_id() and user_id == get_su_user_id():
        return ('private', 'private')
    return ('public', 'public')


def should_extract_profile(channel: str) -> bool:
    """回傳此 channel 是否應進行用戶 profile 抽取。

    直播觀眾（livestream）互動太短雜訊太多，跳過 profile 抽取。
    """
    return channel in EXTRACT_PROFILE_FROM_CHANNELS
