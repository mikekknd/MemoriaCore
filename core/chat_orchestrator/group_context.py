"""群組對話上下文工具。

集中處理角色簡介、群組成員 prompt 區塊與 LLM log context，避免單層與雙層
編排各自拼出不同格式。
"""

from core.prompt_manager import get_prompt_manager
from core.xml_prompt import xml_attr


SUMMARY_MAX_CHARS = 240


def compact_text(text: str, limit: int = SUMMARY_MAX_CHARS) -> str:
    """壓平空白並限制長度。"""
    cleaned = " ".join(str(text or "").split())
    return cleaned[:limit]


def character_summary_text(character: dict, *, fallback_to_prompt: bool = False) -> str:
    """取得角色簡介；必要時退回舊有人設摘要。"""
    summary = compact_text(character.get("character_summary", ""))
    if summary or not fallback_to_prompt:
        return summary
    fallback = character.get("system_prompt") or character.get("reply_rules") or ""
    return compact_text(fallback)


def is_group_context(session_ctx: dict | None) -> bool:
    """從 session_ctx 明確判定是否為群組對話。"""
    if not session_ctx:
        return False
    ids = session_ctx.get("active_character_ids") or []
    return session_ctx.get("session_mode") == "group" or len(ids) > 1


def _participant_ids(session_ctx: dict | None, current_character_id: str) -> list[str]:
    raw_ids = []
    if session_ctx:
        raw_ids = session_ctx.get("active_character_ids") or []
    if not raw_ids and current_character_id:
        raw_ids = [current_character_id]
    return [cid for cid in dict.fromkeys(str(cid).strip() for cid in raw_ids) if cid]


def build_group_participants_block(
    session_ctx: dict | None,
    character_manager,
    current_character_id: str,
) -> str:
    """建立注入 chat system prompt 的群組成員區塊。

    目前角色的人格已由 char_sys_prompt 注入；此區塊只列其他 AI，避免模型把
    自己的 character_summary 視為外部對象。
    """
    if not is_group_context(session_ctx):
        return ""

    lines = []
    current_id = str(current_character_id or "").strip()
    current_char = character_manager.get_character(current_id) or {}
    current_name = current_char.get("name") or current_id or "目前角色"
    for cid in _participant_ids(session_ctx, current_character_id):
        if current_id and cid == current_id:
            continue
        char = character_manager.get_character(cid) or {}
        name = char.get("name") or cid
        summary = character_summary_text(char, fallback_to_prompt=True) or "無角色簡介"
        lines.append(
            f'<participant character_id="{xml_attr(cid)}" name="{xml_attr(name)}">\n'
            f"<summary>{summary}</summary>\n"
            "</participant>"
        )

    participants_text = "\n".join(lines) if lines else "<no_other_participants />"

    group_name = ""
    if session_ctx:
        group_name = str(session_ctx.get("group_name") or "").strip()
    group_context_line = (
        f"你正在多 AI 群組「{group_name}」中對話。"
        if group_name
        else "你正在多 AI 群組對話中。"
    )

    return get_prompt_manager().get("group_participants_block").format(
        group_context_line=group_context_line,
        group_name=group_name,
        current_character_id=current_character_id,
        current_character_name=current_name,
        participants_text=participants_text,
    )


def build_llm_log_context(
    session_ctx: dict | None,
    character_manager,
    current_character_id: str,
) -> dict:
    """建立 LLM prompt log 的可觀測性上下文。"""
    ctx = session_ctx or {}
    current_char = character_manager.get_character(current_character_id) or {}
    participants = []
    for cid in _participant_ids(ctx, current_character_id):
        char = character_manager.get_character(cid) or {}
        participants.append({
            "character_id": cid,
            "name": char.get("name") or cid,
            "character_summary": character_summary_text(char),
        })

    return {
        "session_id": ctx.get("session_id", ""),
        "session_mode": ctx.get("session_mode", "single"),
        "group_name": ctx.get("group_name", ""),
        "user_id": ctx.get("user_id", ""),
        "user_name": ctx.get("user_name", "") or ctx.get("user_display_name", ""),
        "current_character_id": current_character_id,
        "current_character_name": current_char.get("name") or current_character_id,
        "participants": participants,
    }
