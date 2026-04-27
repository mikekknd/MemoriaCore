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
    'telegram', 'rest', 'discord_private',
})

# SU（SuperUser）的 Telegram user_id；未設定時功能降級為無 SU（等同一般用戶）
SU_USER_ID: str = os.getenv('SU_USER_ID', '')


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
    if SU_USER_ID and user_id == SU_USER_ID:
        return ('private', 'private')
    return ('public', 'public')


def should_extract_profile(channel: str) -> bool:
    """回傳此 channel 是否應進行用戶 profile 抽取。

    直播觀眾（livestream）互動太短雜訊太多，跳過 profile 抽取。
    """
    return channel in EXTRACT_PROFILE_FROM_CHANNELS
