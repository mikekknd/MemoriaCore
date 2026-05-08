"""Group Router：決定群組對話下一位發話角色。"""
import json

from core.chat_orchestrator.dataclasses import GroupRouterResult
from core.chat_orchestrator.group_context import character_summary_text
from core.prompt_manager import get_prompt_manager
from core.system_logger import SystemLogger


GROUP_ROUTER_ACTIONS = (
    "stop_all_spoken",
    "stop_no_new_value",
    "new_speaker_ack",
    "new_speaker_add",
    "new_speaker_reply_to_ai",
    "repeat_speaker_reply_to_ai",
    "repeat_speaker_correction",
    "explicit_user_request",
)

GROUP_ROUTER_INTENTS = (
    "single_response",
    "group_discussion",
    "continue_group_discussion",
    "directed_character",
    "low_information_ack",
)

NEW_SPEAKER_ACTIONS = {
    "new_speaker_ack",
    "new_speaker_add",
    "new_speaker_reply_to_ai",
}

REPEAT_SPEAKER_ACTIONS = {
    "repeat_speaker_reply_to_ai",
    "repeat_speaker_correction",
}

GROUP_ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(GROUP_ROUTER_ACTIONS),
        },
        "conversation_intent": {
            "type": "string",
            "enum": list(GROUP_ROUTER_INTENTS),
        },
        "target_character_id": {"type": ["string", "null"]},
        "reason": {"type": "string"},
    },
    "required": ["conversation_intent", "action", "target_character_id", "reason"],
    "additionalProperties": False,
}


def run_group_router(
    session_messages: list[dict],
    active_characters: list[dict],
    router,
    *,
    temperature: float = 0.0,
    last_speaker_id: str | None = None,
    honor_mentions: bool = True,
    bot_turn_index: int = 0,
    max_bot_turns: int | None = None,
    allow_single_participant_repeat: bool = True,
    discussion_mode: str = "default",
    live_hosting: dict | None = None,
) -> GroupRouterResult:
    """根據近期群組上下文選出下一位 AI；無需接話時回傳 should_respond=False。"""
    participants = _normalize_characters(active_characters)
    if not participants:
        return GroupRouterResult(False, None, "no participants", "stop_no_new_value")

    mentioned_id = _detect_mention(_latest_user_text(session_messages), participants) if honor_mentions else None
    if mentioned_id:
        return GroupRouterResult(True, mentioned_id, "explicit mention", "explicit_user_request")

    latest_user_text = _latest_user_text(session_messages)
    spoken_after_user = _spoken_participant_ids_after_latest_user(session_messages, participants)
    already_spoken_refs = _participant_refs(spoken_after_user, participants)
    not_yet_spoken_ids = _not_yet_spoken_participant_ids(spoken_after_user, participants)
    not_yet_spoken_refs = _participant_refs(not_yet_spoken_ids, participants)
    all_participants_spoke = len(participants) > 1 and not not_yet_spoken_ids
    remaining_bot_turns = _remaining_bot_turns(bot_turn_index, max_bot_turns)
    normalized_discussion_mode = _normalize_discussion_mode(discussion_mode)

    if len(participants) == 1:
        only_id = participants[0]["character_id"]
        if only_id == last_speaker_id and not allow_single_participant_repeat:
            return GroupRouterResult(False, None, "single participant already spoke", "stop_no_new_value")
        if only_id == last_speaker_id:
            return GroupRouterResult(True, only_id, "single participant repeat", "repeat_speaker_reply_to_ai")
        return GroupRouterResult(True, only_id, "single participant", "new_speaker_ack")

    prompt = get_prompt_manager().get("group_router_system").format(
        participants_json=json.dumps(participants, ensure_ascii=False, indent=2),
        history_text=_format_history(session_messages[-12:]),
        turn_state_json=json.dumps(
            {
                "original_user_request": latest_user_text,
                "latest_user_text": latest_user_text,
                "last_speaker": _participant_ref(last_speaker_id, participants),
                "already_spoken_this_turn": already_spoken_refs,
                "not_yet_spoken_this_turn": not_yet_spoken_refs,
                "all_participants_already_spoke_this_turn": all_participants_spoke,
                "recent_assistant_exchange_this_turn": _recent_assistant_exchange_after_latest_user(
                    session_messages,
                    participants,
                    limit=4,
                ),
                "bot_turn_index": max(0, int(bot_turn_index or 0)),
                "max_bot_turns": max_bot_turns,
                "remaining_bot_turns_including_next": remaining_bot_turns,
                "discussion_mode": normalized_discussion_mode,
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    if normalized_discussion_mode == "youtube_live":
        prompt += "\n\n" + _youtube_live_group_router_rules(live_hosting)

    try:
        parsed = router.generate_json(
            "group_router",
            [{"role": "user", "content": prompt}],
            schema=GROUP_ROUTER_SCHEMA,
            temperature=temperature,
        )
    except Exception as exc:
        SystemLogger.log_error("GroupRouter", f"{type(exc).__name__}: {exc}")
        return _fallback_with_unspoken(participants, not_yet_spoken_ids, last_speaker_id)

    if not isinstance(parsed, dict):
        return _fallback_with_unspoken(participants, not_yet_spoken_ids, last_speaker_id)

    parsed = _coerce_legacy_router_result(parsed, spoken_after_user, not_yet_spoken_ids)
    conversation_intent = str(parsed.get("conversation_intent") or "")
    action = str(parsed.get("action") or "")
    target = parsed.get("target_character_id")
    reason = str(parsed.get("reason", ""))
    result = _validate_action_result(
        conversation_intent=conversation_intent,
        action=action,
        target=target,
        reason=reason,
        participants=participants,
        already_spoken_ids=spoken_after_user,
        not_yet_spoken_ids=not_yet_spoken_ids,
        last_speaker_id=last_speaker_id,
    )
    return _apply_youtube_live_continuation_policy(
        result,
        participants=participants,
        already_spoken_ids=spoken_after_user,
        last_speaker_id=last_speaker_id,
        remaining_bot_turns=remaining_bot_turns,
        discussion_mode=normalized_discussion_mode,
    )


def _normalize_discussion_mode(value: str | None) -> str:
    return "youtube_live" if str(value or "").strip() == "youtube_live" else "default"


def _youtube_live_group_router_rules(live_hosting: dict | None = None) -> str:
    base = (
        "<youtube_live_group_router_rules>\n"
        "- 這是 YouTube 直播的多角色對話，不是普通使用者問答。\n"
        "- 不要因為所有角色都已各說一次就停止；只要 remaining_bot_turns_including_next > 0，仍應優先評估角色間接話。\n"
        "- 角色把問題丟給觀眾時，不代表應該等待觀眾；應讓另一位角色接住，除非目前正在回應留言或 Super Chat。\n"
        "- 只有在近期交換已自然收束、沒有具體主張可補充，或已沒有剩餘回合時才停止。\n"
        "</youtube_live_group_router_rules>"
    )
    hosting = _youtube_live_hosting_router_rules(live_hosting)
    return base + ("\n\n" + hosting if hosting else "")


def _youtube_live_hosting_router_rules(live_hosting: dict | None = None) -> str:
    if not isinstance(live_hosting, dict) or not live_hosting:
        return ""
    parts: list[str] = []
    host_rules = str(live_hosting.get("host_interaction_rules") or "").strip()
    segment_state = live_hosting.get("segment_state") if isinstance(live_hosting.get("segment_state"), dict) else {}
    if host_rules:
        parts.append("主持互動規則：\n" + host_rules)
    current = segment_state.get("current_step") if isinstance(segment_state.get("current_step"), dict) else {}
    if segment_state and str(segment_state.get("topic") or "").strip():
        parts.append(f"目前討論主題：{str(segment_state.get('topic') or '').strip()}")
    if current and str(current.get("name") or "").strip():
        parts.append(f"目前節目步驟：{str(current.get('name') or '').strip()}")
        description = str(current.get("description") or "").strip()
        if description:
            parts.append(f"目前步驟說明：{description}")
    completed = [
        str(item.get("name") or "").strip()
        for item in segment_state.get("completed_steps") or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    remaining = [
        str(item.get("name") or "").strip()
        for item in segment_state.get("remaining_steps") or []
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    if completed:
        parts.append("已完成步驟：" + "、".join(completed[:8]))
    if remaining:
        parts.append("剩餘步驟：" + "、".join(remaining[:8]))
    try:
        turns = int(live_hosting.get("program_segment_turns", 0) or 0)
    except (TypeError, ValueError):
        turns = 0
    if turns > 0:
        parts.append(f"每段落建議回合數：{turns}")
    if not parts:
        return ""
    return "<youtube_live_hosting_router_rules>\n" + "\n\n".join(parts) + "\n</youtube_live_hosting_router_rules>"


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
    return character_summary_text(char, fallback_to_prompt=True)


def _participant_ref(character_id: str | None, participants: list[dict]) -> dict | None:
    cid = str(character_id or "").strip()
    if not cid:
        return None
    for participant in participants:
        if participant["character_id"] == cid:
            return {
                "character_id": cid,
                "name": participant.get("name") or cid,
            }
    return None


def _participant_refs(character_ids: set[str] | list[str], participants: list[dict]) -> list[dict]:
    wanted = set(character_ids)
    refs = []
    for participant in participants:
        cid = participant["character_id"]
        if cid in wanted:
            refs.append({
                "character_id": cid,
                "name": participant.get("name") or cid,
            })
    return refs


def _latest_user_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return str(msg.get("content", ""))
    return ""


def _spoken_participant_ids_after_latest_user(messages: list[dict], participants: list[dict]) -> set[str]:
    latest_user_index = None
    for idx in range(len(messages) - 1, -1, -1):
        if messages[idx].get("role") == "user":
            latest_user_index = idx
            break
    if latest_user_index is None:
        return set()

    valid_ids = {p["character_id"] for p in participants}
    spoken: set[str] = set()
    for msg in messages[latest_user_index + 1:]:
        if msg.get("role") != "assistant":
            continue
        cid = str(msg.get("character_id") or "").strip()
        if cid in valid_ids:
            spoken.add(cid)
    return spoken


def _not_yet_spoken_participant_ids(spoken_ids: set[str], participants: list[dict]) -> list[str]:
    return [p["character_id"] for p in participants if p["character_id"] not in spoken_ids]


def _recent_assistant_exchange_after_latest_user(
    messages: list[dict],
    participants: list[dict],
    *,
    limit: int,
) -> list[dict]:
    latest_user_index = None
    for idx in range(len(messages) - 1, -1, -1):
        if messages[idx].get("role") == "user":
            latest_user_index = idx
            break
    if latest_user_index is None:
        return []

    valid_ids = {p["character_id"] for p in participants}
    names = {p["character_id"]: p.get("name") or p["character_id"] for p in participants}
    exchange = []
    for msg in messages[latest_user_index + 1:]:
        if msg.get("role") != "assistant":
            continue
        cid = str(msg.get("character_id") or "").strip()
        if cid not in valid_ids:
            continue
        exchange.append({
            "character_id": cid,
            "name": msg.get("character_name") or names.get(cid) or cid,
            "content": str(msg.get("content", ""))[:800],
        })
    return exchange[-max(1, int(limit or 1)):]


def _remaining_bot_turns(bot_turn_index: int, max_bot_turns: int | None) -> int | None:
    if max_bot_turns is None:
        return None
    try:
        limit = int(max_bot_turns)
        index = int(bot_turn_index or 0)
    except (TypeError, ValueError):
        return None
    return max(0, limit - max(0, index))


def _detect_mention(text: str, participants: list[dict]) -> str | None:
    """偵測使用者是否 @ 了某位參與者。
    依 needle 長度由長到短比對，避免短名稱是長名稱前綴時誤命中。
    """
    if not text:
        return None
    normalized_text = text.replace("＠", "@")
    # 收集 (needle, character_id) 並依 needle 長度降冪排序
    candidates: list[tuple[str, str]] = []
    for participant in participants:
        cid = participant["character_id"]
        name = participant.get("name") or cid
        for needle in (f"@{cid}", f"@{name}"):
            if needle and len(needle) > 1:
                candidates.append((needle, cid))
    candidates.sort(key=lambda x: len(x[0]), reverse=True)
    for needle, cid in candidates:
        if needle in normalized_text:
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


def _coerce_legacy_router_result(
    parsed: dict,
    already_spoken_ids: set[str],
    not_yet_spoken_ids: list[str],
) -> dict:
    """兼容舊版 should_respond schema；正式 prompt/schema 已改用 action。"""
    if parsed.get("action") in GROUP_ROUTER_ACTIONS:
        return parsed
    if "should_respond" not in parsed:
        return parsed

    target = parsed.get("target_character_id")
    if not bool(parsed.get("should_respond")):
        action = "stop_all_spoken" if not not_yet_spoken_ids else "stop_no_new_value"
    elif target in not_yet_spoken_ids:
        action = "new_speaker_add"
    elif target in already_spoken_ids:
        action = "repeat_speaker_reply_to_ai"
    else:
        action = "new_speaker_add"
    return {
        "conversation_intent": _legacy_intent_for_action(action),
        "action": action,
        "target_character_id": target,
        "reason": parsed.get("reason", ""),
    }


def _legacy_intent_for_action(action: str) -> str:
    if action == "explicit_user_request":
        return "directed_character"
    if action in REPEAT_SPEAKER_ACTIONS:
        return "continue_group_discussion"
    if action in NEW_SPEAKER_ACTIONS:
        return "group_discussion"
    return "single_response"


def _validate_action_result(
    *,
    conversation_intent: str,
    action: str,
    target,
    reason: str,
    participants: list[dict],
    already_spoken_ids: set[str],
    not_yet_spoken_ids: list[str],
    last_speaker_id: str | None,
) -> GroupRouterResult:
    valid_ids = {p["character_id"] for p in participants}

    if action not in GROUP_ROUTER_ACTIONS:
        return _fallback_with_unspoken(participants, not_yet_spoken_ids, last_speaker_id)

    if action == "stop_all_spoken":
        if target is not None or not_yet_spoken_ids:
            return _fallback_with_unspoken(participants, not_yet_spoken_ids, last_speaker_id)
        return GroupRouterResult(False, None, reason, action, conversation_intent)

    if action == "stop_no_new_value":
        if target is not None:
            return _fallback_with_unspoken(participants, not_yet_spoken_ids, last_speaker_id)
        return GroupRouterResult(False, None, reason, action, conversation_intent)

    if action in NEW_SPEAKER_ACTIONS:
        if target in not_yet_spoken_ids:
            return GroupRouterResult(True, target, reason, action, conversation_intent)
        return _fallback_with_unspoken(participants, not_yet_spoken_ids, last_speaker_id)

    if action in REPEAT_SPEAKER_ACTIONS:
        if target in already_spoken_ids:
            return GroupRouterResult(True, target, reason, action, conversation_intent)
        return _fallback_with_unspoken(participants, not_yet_spoken_ids, last_speaker_id)

    if action == "explicit_user_request":
        if target in valid_ids:
            return GroupRouterResult(True, target, reason, action, conversation_intent)
        return _fallback_with_unspoken(participants, not_yet_spoken_ids, last_speaker_id)

    return _fallback_with_unspoken(participants, not_yet_spoken_ids, last_speaker_id)


def _apply_youtube_live_continuation_policy(
    result: GroupRouterResult,
    *,
    participants: list[dict],
    already_spoken_ids: set[str],
    last_speaker_id: str | None,
    remaining_bot_turns: int | None,
    discussion_mode: str,
) -> GroupRouterResult:
    if discussion_mode != "youtube_live" or result.should_respond:
        return result
    if remaining_bot_turns is not None and remaining_bot_turns <= 0:
        return result
    if len(participants) < 2 or not already_spoken_ids:
        return result

    spoken_candidates = [
        participant["character_id"]
        for participant in participants
        if participant["character_id"] in already_spoken_ids and participant["character_id"] != last_speaker_id
    ]
    if not spoken_candidates:
        spoken_candidates = [
            participant["character_id"]
            for participant in participants
            if participant["character_id"] != last_speaker_id
        ]
    if not spoken_candidates:
        return result

    return GroupRouterResult(
        True,
        spoken_candidates[0],
        "youtube live discussion mode keeps role-to-role momentum within remaining turn budget",
        "repeat_speaker_reply_to_ai",
        "continue_group_discussion",
    )


def _fallback_with_unspoken(
    participants: list[dict],
    not_yet_spoken_ids: list[str],
    last_speaker_id: str | None,
) -> GroupRouterResult:
    for cid in not_yet_spoken_ids:
        if cid != last_speaker_id:
            return GroupRouterResult(True, cid, "fallback unspoken participant", "new_speaker_add")
    if not_yet_spoken_ids:
        return GroupRouterResult(True, not_yet_spoken_ids[0], "fallback unspoken participant", "new_speaker_add")
    return _fallback(participants, last_speaker_id)


def _fallback(participants: list[dict], last_speaker_id: str | None) -> GroupRouterResult:
    for participant in participants:
        cid = participant["character_id"]
        if cid != last_speaker_id:
            return GroupRouterResult(True, cid, "fallback", "new_speaker_add")
    return GroupRouterResult(False, None, "fallback no alternative", "stop_no_new_value")
