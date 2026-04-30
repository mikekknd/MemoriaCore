"""Session roster 更新工具。"""

from api.dependencies import get_character_manager
from api.session_manager import SessionState, session_manager


MAX_SESSION_CHARACTERS = 6


def normalize_character_ids(raw_ids: list[str] | None) -> tuple[list[str], dict[str, str]] | None:
    """驗證並標準化前端送來的目前在場 AI 名單。"""
    if raw_ids is None:
        return None

    ids = [cid for cid in dict.fromkeys(str(cid).strip() for cid in raw_ids) if cid]
    if not ids:
        raise ValueError("至少需要選擇一位 AI")
    if len(ids) > MAX_SESSION_CHARACTERS:
        raise ValueError(f"同一個 Session 最多支援 {MAX_SESSION_CHARACTERS} 位 AI")

    char_mgr = get_character_manager()
    names: dict[str, str] = {}
    missing = []
    for cid in ids:
        char = char_mgr.get_character(cid)
        if not char:
            missing.append(cid)
            continue
        names[cid] = char.get("name") or cid
    if missing:
        raise ValueError(f"Character not found: {', '.join(missing)}")
    return ids, names


async def apply_roster_update(
    session: SessionState,
    raw_ids: list[str] | None,
    *,
    group_name: str | None = None,
) -> dict | None:
    normalized = normalize_character_ids(raw_ids)
    if normalized is None:
        return None
    ids, names = normalized
    clean_group_name = group_name.strip() if isinstance(group_name, str) else None
    return await session_manager.update_roster(
        session.session_id,
        ids,
        character_names=names,
        group_name=clean_group_name,
    )
