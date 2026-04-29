"""Group Router：決定群組對話下一位發話角色。"""
import json

from core.chat_orchestrator.dataclasses import GroupRouterResult
from core.prompt_manager import get_prompt_manager
from core.system_logger import SystemLogger


GROUP_ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "should_respond": {"type": "boolean"},
        "target_character_id": {"type": ["string", "null"]},
        "reason": {"type": "string"},
    },
    "required": ["should_respond", "target_character_id", "reason"],
}


def run_group_router(
    session_messages: list[dict],
    active_characters: list[dict],
    router,
    *,
    temperature: float = 0.0,
    last_speaker_id: str | None = None,
    honor_mentions: bool = True,
) -> GroupRouterResult:
    """根據近期群組上下文選出下一位 AI；無需接話時回傳 should_respond=False。"""
    participants = _normalize_characters(active_characters)
    if not participants:
        return GroupRouterResult(False, None, "no participants")

    mentioned_id = _detect_mention(_latest_user_text(session_messages), participants) if honor_mentions else None
    if mentioned_id:
        return GroupRouterResult(True, mentioned_id, "explicit mention")

    if len(participants) == 1:
        only_id = participants[0]["character_id"]
        if only_id == last_speaker_id:
            return GroupRouterResult(False, None, "single participant already spoke")
        return GroupRouterResult(True, only_id, "single participant")

    prompt = get_prompt_manager().get("group_router_system").format(
        participants_json=json.dumps(participants, ensure_ascii=False, indent=2),
        history_text=_format_history(session_messages[-12:]),
        last_speaker_id=last_speaker_id or "",
        mentioned_character_id=mentioned_id or "",
    )

    try:
        parsed = router.generate_json(
            "group_router",
            [{"role": "user", "content": prompt}],
            schema=GROUP_ROUTER_SCHEMA,
            temperature=temperature,
        )
    except Exception as exc:
        SystemLogger.log_error("GroupRouter", f"{type(exc).__name__}: {exc}")
        return _fallback(participants, last_speaker_id)

    if not isinstance(parsed, dict):
        return _fallback(participants, last_speaker_id)

    should_respond = bool(parsed.get("should_respond"))
    target = parsed.get("target_character_id")
    valid_ids = {p["character_id"] for p in participants}
    if not should_respond:
        return GroupRouterResult(False, None, str(parsed.get("reason", "")))
    if target not in valid_ids:
        return _fallback(participants, last_speaker_id)
    if target == last_speaker_id and len(participants) > 1:
        alternatives = [p["character_id"] for p in participants if p["character_id"] != last_speaker_id]
        return GroupRouterResult(True, alternatives[0], "avoid repeated speaker")
    return GroupRouterResult(True, target, str(parsed.get("reason", "")))


def _normalize_characters(active_characters: list[dict]) -> list[dict]:
    normalized = []
    seen = set()
    for char in active_characters:
        cid = str(char.get("character_id") or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        normalized.append({
            "character_id": cid,
            "name": char.get("name") or cid,
            "summary": _summarize_character(char),
        })
    return normalized


def _summarize_character(char: dict) -> str:
    text = char.get("system_prompt") or char.get("reply_rules") or ""
    return " ".join(str(text).split())[:240]


def _latest_user_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return str(msg.get("content", ""))
    return ""


def _detect_mention(text: str, participants: list[dict]) -> str | None:
    if not text:
        return None
    normalized_text = text.replace("＠", "@")
    for participant in participants:
        cid = participant["character_id"]
        name = participant.get("name") or cid
        needles = [f"@{cid}", f"@{name}", f"＠{cid}", f"＠{name}"]
        if any(needle and needle in normalized_text for needle in needles):
            return cid
    return None


def _format_history(messages: list[dict]) -> str:
    lines = []
    for msg in messages:
        role = msg.get("role", "")
        content = str(msg.get("content", ""))
        if role == "assistant":
            name = msg.get("character_name") or msg.get("character_id") or "assistant"
            cid = msg.get("character_id") or ""
            label = f"{name}|{cid}" if cid else str(name)
        elif role == "user":
            label = "user"
        else:
            label = role or "unknown"
        lines.append(f"[{label}]: {content[:800]}")
    return "\n".join(lines)


def _fallback(participants: list[dict], last_speaker_id: str | None) -> GroupRouterResult:
    for participant in participants:
        cid = participant["character_id"]
        if cid != last_speaker_id:
            return GroupRouterResult(True, cid, "fallback")
    return GroupRouterResult(False, None, "fallback no alternative")
