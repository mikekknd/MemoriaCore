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
    live_episode_plan: dict | None = None,
    current_turn_instruction: str = "",
    current_turn_start_index: int | None = None,
) -> GroupRouterResult:
    """根據近期群組上下文選出下一位 AI；無需接話時回傳 should_respond=False。"""
    participants = _normalize_characters(active_characters)
    normalized_discussion_mode = _normalize_discussion_mode(discussion_mode)
    live_episode_plan_context = _normalize_live_episode_plan_context(
        live_episode_plan,
        bot_turn_index=bot_turn_index,
        max_bot_turns=max_bot_turns,
    )
    if normalized_discussion_mode == "youtube_live" and live_episode_plan_context:
        participants = _restrict_participants_for_live_episode_plan(
            participants,
            live_episode_plan_context,
            bot_turn_index=bot_turn_index,
            max_bot_turns=max_bot_turns,
            last_speaker_id=last_speaker_id,
        )
    if not participants:
        return GroupRouterResult(False, None, "no participants", "stop_no_new_value")

    latest_user_text = str(current_turn_instruction or "").strip() or _latest_user_text(session_messages)
    mentioned_id = _detect_mention(latest_user_text, participants) if honor_mentions else None
    if mentioned_id:
        return GroupRouterResult(True, mentioned_id, "explicit mention", "explicit_user_request")

    turn_start_index = _normalize_current_turn_start_index(current_turn_start_index, len(session_messages))
    if turn_start_index is None:
        spoken_after_user = _spoken_participant_ids_after_latest_user(session_messages, participants)
        recent_exchange = _recent_assistant_exchange_after_latest_user(
            session_messages,
            participants,
            limit=4,
        )
    else:
        spoken_after_user = _spoken_participant_ids_after_turn_start(session_messages, participants, turn_start_index)
        recent_exchange = _recent_assistant_exchange_after_turn_start(
            session_messages,
            participants,
            start_index=turn_start_index,
            limit=4,
        )
    already_spoken_refs = _participant_refs(spoken_after_user, participants)
    not_yet_spoken_ids = _not_yet_spoken_participant_ids(spoken_after_user, participants)
    not_yet_spoken_refs = _participant_refs(not_yet_spoken_ids, participants)
    all_participants_spoke = len(participants) > 1 and not not_yet_spoken_ids
    remaining_bot_turns = _remaining_bot_turns(bot_turn_index, max_bot_turns)

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
                "recent_assistant_exchange_this_turn": recent_exchange,
                "bot_turn_index": max(0, int(bot_turn_index or 0)),
                "max_bot_turns": max_bot_turns,
                "remaining_bot_turns_including_next": remaining_bot_turns,
                "discussion_mode": normalized_discussion_mode,
                "live_episode_plan": live_episode_plan_context,
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
    result = _enforce_youtube_live_speaker_rules(
        result,
        participants=participants,
        already_spoken_ids=spoken_after_user,
        not_yet_spoken_ids=not_yet_spoken_ids,
        last_speaker_id=last_speaker_id,
        remaining_bot_turns=remaining_bot_turns,
        discussion_mode=normalized_discussion_mode,
        live_episode_plan=live_episode_plan_context,
    )
    return _apply_youtube_live_continuation_policy(
        result,
        participants=participants,
        already_spoken_ids=spoken_after_user,
        last_speaker_id=last_speaker_id,
        remaining_bot_turns=remaining_bot_turns,
        discussion_mode=normalized_discussion_mode,
        live_episode_plan=live_episode_plan_context,
    )


def _normalize_discussion_mode(value: str | None) -> str:
    return "youtube_live" if str(value or "").strip() == "youtube_live" else "default"


def _youtube_live_group_router_rules(live_hosting: dict | None = None) -> str:
    base = (
        "<youtube_live_group_router_rules>\n"
        "- 這是 YouTube 直播的多角色對話，不是普通使用者問答。\n"
        "- 一般直播對話禁止同一角色連續發言；若上一位 speaker 與候選 speaker 相同，除非是 final_closing、系統安全補充、使用者明確指定或格式錯誤重試，必須改派其他角色或停止。\n"
        "- 同一個 live_episode_plan.turn_id / turn_type 內，同一角色不可針對同一段任務發言兩次；所有可用角色都已完成後應停止並交回導播推進下一段。\n"
        "- remaining_bot_turns_including_next 是硬性上限，不是必須用完的目標；段落核心資訊已說出後，可停止而不是補同一資料點。\n"
        "- 角色把問題丟給觀眾時，不代表應該等待觀眾；應讓另一位角色接住，除非目前正在回應留言或 Super Chat。\n"
        "- 若 live_episode_plan.speaker_policy.anchor_status=first_reply_already_completed，代表第一棒指定角色已完成；後續應優先評估其他角色是否能接話、補充或反駁。\n"
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


def _normalize_live_episode_plan_context(
    live_episode_plan: dict | None,
    *,
    bot_turn_index: int = 0,
    max_bot_turns: int | None = None,
) -> dict:
    if not isinstance(live_episode_plan, dict) or not live_episode_plan:
        return {}
    turn_contract = live_episode_plan.get("turn_contract") if isinstance(live_episode_plan.get("turn_contract"), dict) else {}
    speaker_policy = live_episode_plan.get("speaker_policy") if isinstance(live_episode_plan.get("speaker_policy"), dict) else {}
    if not speaker_policy and isinstance(turn_contract.get("speaker_policy"), dict):
        speaker_policy = turn_contract["speaker_policy"]
    dialogue_policy = _normalize_live_episode_dialogue_policy_context(live_episode_plan.get("dialogue_policy"))
    speaker_policy_context = _normalize_live_episode_speaker_policy_context(speaker_policy)
    if _fixed_speaker_anchor_should_expand(
        speaker_policy_context,
        dialogue_policy,
        bot_turn_index=bot_turn_index,
        max_bot_turns=max_bot_turns,
    ):
        speaker_policy_context = _speaker_policy_after_fixed_anchor(speaker_policy_context)
    turn_contract_context = _normalize_live_episode_turn_contract_context(turn_contract)
    if _fixed_speaker_anchor_should_expand(
        turn_contract_context.get("speaker_policy") if isinstance(turn_contract_context.get("speaker_policy"), dict) else {},
        dialogue_policy,
        bot_turn_index=bot_turn_index,
        max_bot_turns=max_bot_turns,
    ):
        turn_contract_context["speaker_policy"] = _speaker_policy_after_fixed_anchor(turn_contract_context["speaker_policy"])
    normalized = {
        "plan_id": str(live_episode_plan.get("plan_id") or "").strip(),
        "mode": str(live_episode_plan.get("mode") or "").strip(),
        "turn_id": str(live_episode_plan.get("turn_id") or turn_contract.get("turn_id") or "").strip(),
        "turn_contract": turn_contract_context,
        "speaker_policy": speaker_policy_context,
        "dialogue_policy": dialogue_policy,
    }
    return {key: value for key, value in normalized.items() if value not in ("", [], {})}


def _normalize_live_episode_turn_contract_context(turn_contract: dict) -> dict:
    if not isinstance(turn_contract, dict) or not turn_contract:
        return {}
    normalized = {
        "turn_id": str(turn_contract.get("turn_id") or "").strip(),
        "turn_type": str(turn_contract.get("turn_type") or "").strip(),
        "intent": str(turn_contract.get("intent") or "").strip(),
    }
    speaker_policy = _normalize_live_episode_speaker_policy_context(turn_contract.get("speaker_policy"))
    if speaker_policy:
        normalized["speaker_policy"] = speaker_policy
    return {key: value for key, value in normalized.items() if value not in ("", [], {})}


def _normalize_live_episode_speaker_policy_context(speaker_policy: dict | None) -> dict:
    if not isinstance(speaker_policy, dict) or not speaker_policy:
        return {}
    selection_mode = str(speaker_policy.get("selection_mode") or "").strip()
    allowed_raw = speaker_policy.get("allowed_character_ids")
    allowed_character_ids = [
        cid
        for raw in (allowed_raw or [])
        if (cid := str(raw or "").strip())
    ] if isinstance(allowed_raw, list) else []
    preferred_role_functions = [
        role
        for raw in (speaker_policy.get("preferred_role_functions") or [])
        if (role := str(raw or "").strip())
    ] if isinstance(speaker_policy.get("preferred_role_functions"), list) else []
    normalized = {}
    if selection_mode in {"router_select", "fixed", "function_router"}:
        normalized["selection_mode"] = selection_mode
    if allowed_character_ids:
        normalized["allowed_character_ids"] = allowed_character_ids
    if preferred_role_functions:
        normalized["preferred_role_functions"] = preferred_role_functions
    if isinstance(speaker_policy.get("avoid_repeat_speaker"), bool):
        normalized["avoid_repeat_speaker"] = speaker_policy.get("avoid_repeat_speaker")
    return normalized


def _normalize_live_episode_dialogue_policy_context(dialogue_policy: dict | None) -> dict:
    if not isinstance(dialogue_policy, dict) or not dialogue_policy:
        return {}
    normalized = {}
    try:
        min_replies = int(dialogue_policy.get("min_replies"))
    except (TypeError, ValueError):
        min_replies = None
    try:
        max_replies = int(dialogue_policy.get("max_replies"))
    except (TypeError, ValueError):
        max_replies = None
    if min_replies is not None:
        normalized["min_replies"] = max(1, min(min_replies, 4))
    if max_replies is not None:
        normalized["max_replies"] = max(1, min(max_replies, 4))
    autonomy = str(dialogue_policy.get("autonomy") or "").strip()
    if autonomy in {"strict", "guided", "open"}:
        normalized["autonomy"] = autonomy
    preferred_flow = [
        item
        for raw in (dialogue_policy.get("preferred_flow") or [])
        if (item := str(raw or "").strip())
    ] if isinstance(dialogue_policy.get("preferred_flow"), list) else []
    if preferred_flow:
        normalized["preferred_flow"] = preferred_flow[:6]
    return normalized


def _fixed_speaker_anchor_should_expand(
    speaker_policy: dict,
    dialogue_policy: dict,
    *,
    bot_turn_index: int = 0,
    max_bot_turns: int | None = None,
) -> bool:
    allowed_ids = [
        str(item or "").strip()
        for item in speaker_policy.get("allowed_character_ids") or []
        if str(item or "").strip()
    ]
    return (
        str(speaker_policy.get("selection_mode") or "").strip() == "fixed"
        and len(allowed_ids) == 1
        and int(bot_turn_index or 0) > 0
        and _live_episode_plan_allows_multi_reply({"dialogue_policy": dialogue_policy}, max_bot_turns)
    )


def _speaker_policy_after_fixed_anchor(speaker_policy: dict) -> dict:
    normalized = {
        key: value
        for key, value in speaker_policy.items()
        if key != "allowed_character_ids"
    }
    anchored_ids = [
        str(item or "").strip()
        for item in speaker_policy.get("allowed_character_ids") or []
        if str(item or "").strip()
    ]
    normalized["selection_mode"] = "router_select"
    normalized["anchored_character_ids"] = anchored_ids
    normalized["anchor_status"] = "first_reply_already_completed"
    return normalized


def _restrict_participants_for_live_episode_plan(
    participants: list[dict],
    live_episode_plan: dict,
    *,
    bot_turn_index: int = 0,
    max_bot_turns: int | None = None,
    last_speaker_id: str | None = None,
) -> list[dict]:
    speaker_policy = live_episode_plan.get("speaker_policy") if isinstance(live_episode_plan.get("speaker_policy"), dict) else {}
    allowed_ids = {
        str(item or "").strip()
        for item in speaker_policy.get("allowed_character_ids") or []
        if str(item or "").strip()
    }
    if not allowed_ids:
        return participants
    selection_mode = str(speaker_policy.get("selection_mode") or "").strip()
    if _youtube_live_fixed_policy_would_repeat_previous(
        live_episode_plan,
        allowed_ids=allowed_ids,
        selection_mode=selection_mode,
        last_speaker_id=last_speaker_id,
    ):
        return participants
    if (
        selection_mode == "fixed"
        and len(allowed_ids) == 1
        and int(bot_turn_index or 0) > 0
        and _live_episode_plan_allows_multi_reply(live_episode_plan, max_bot_turns)
    ):
        return participants
    return [participant for participant in participants if participant["character_id"] in allowed_ids]


def _youtube_live_fixed_policy_would_repeat_previous(
    live_episode_plan: dict,
    *,
    allowed_ids: set[str],
    selection_mode: str,
    last_speaker_id: str | None,
) -> bool:
    if selection_mode != "fixed" or len(allowed_ids) != 1:
        return False
    if _youtube_live_allows_same_speaker_repeat(
        GroupRouterResult(True, next(iter(allowed_ids)), "", "new_speaker_ack", "group_discussion"),
        live_episode_plan,
    ):
        return False
    return next(iter(allowed_ids)) == str(last_speaker_id or "").strip()


def _live_episode_plan_allows_multi_reply(live_episode_plan: dict, max_bot_turns: int | None) -> bool:
    try:
        if int(max_bot_turns or 0) > 1:
            return True
    except (TypeError, ValueError):
        pass
    dialogue_policy = (
        live_episode_plan.get("dialogue_policy")
        if isinstance(live_episode_plan.get("dialogue_policy"), dict)
        else {}
    )
    try:
        return int(dialogue_policy.get("max_replies") or 1) > 1
    except (TypeError, ValueError):
        return False


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


def _normalize_current_turn_start_index(value: int | None, message_count: int) -> int | None:
    if value is None:
        return None
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, min(index, max(0, int(message_count or 0))))


def _spoken_participant_ids_after_turn_start(
    messages: list[dict],
    participants: list[dict],
    start_index: int,
) -> set[str]:
    valid_ids = {p["character_id"] for p in participants}
    spoken: set[str] = set()
    for msg in messages[max(0, int(start_index or 0)):]:
        if msg.get("role") != "assistant":
            continue
        cid = str(msg.get("character_id") or "").strip()
        if cid in valid_ids:
            spoken.add(cid)
    return spoken


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


def _recent_assistant_exchange_after_turn_start(
    messages: list[dict],
    participants: list[dict],
    *,
    start_index: int,
    limit: int,
) -> list[dict]:
    valid_ids = {p["character_id"] for p in participants}
    names = {p["character_id"]: p.get("name") or p["character_id"] for p in participants}
    exchange = []
    for msg in messages[max(0, int(start_index or 0)):]:
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


def _enforce_youtube_live_speaker_rules(
    result: GroupRouterResult,
    *,
    participants: list[dict],
    already_spoken_ids: set[str],
    not_yet_spoken_ids: list[str],
    last_speaker_id: str | None,
    remaining_bot_turns: int | None,
    discussion_mode: str,
    live_episode_plan: dict,
) -> GroupRouterResult:
    if discussion_mode != "youtube_live" or not live_episode_plan:
        return result
    if remaining_bot_turns is not None and remaining_bot_turns <= 0:
        return GroupRouterResult(False, None, "youtube live planned turn reply budget exhausted", "stop_no_new_value")
    if _youtube_live_allows_same_speaker_repeat(result, live_episode_plan):
        return result
    if not result.should_respond or not result.target_character_id:
        return result

    target = str(result.target_character_id or "").strip()
    duplicate_in_turn = bool(_live_episode_turn_identity(live_episode_plan) and target in already_spoken_ids)
    repeats_previous = bool(target and target == str(last_speaker_id or "").strip())
    if not duplicate_in_turn and not repeats_previous:
        return result

    alternative = _youtube_live_unique_alternative_speaker(
        participants,
        already_spoken_ids=already_spoken_ids,
        last_speaker_id=last_speaker_id,
    )
    if alternative:
        reason_parts = []
        if repeats_previous:
            reason_parts.append("previous speaker repeat")
        if duplicate_in_turn:
            reason_parts.append("same planned turn duplicate")
        reason = "youtube live speaker guard reassigned from " + " and ".join(reason_parts)
        return GroupRouterResult(
            True,
            alternative,
            reason,
            "new_speaker_reply_to_ai",
            "continue_group_discussion",
        )
    return GroupRouterResult(
        False,
        None,
        "youtube live planned turn has no unique speaker task left",
        "stop_all_spoken",
        "continue_group_discussion",
    )


def _youtube_live_allows_same_speaker_repeat(result: GroupRouterResult, live_episode_plan: dict) -> bool:
    if result.action == "explicit_user_request" or result.conversation_intent == "directed_character":
        return True
    turn_type = _live_episode_turn_type(live_episode_plan)
    if turn_type == "final_closing":
        return True
    repeat_exception = str(live_episode_plan.get("speaker_repeat_exception") or "").strip()
    return repeat_exception in {"system_safety_supplement", "format_retry"}


def _youtube_live_unique_alternative_speaker(
    participants: list[dict],
    *,
    already_spoken_ids: set[str],
    last_speaker_id: str | None,
) -> str | None:
    last = str(last_speaker_id or "").strip()
    for participant in participants:
        cid = participant["character_id"]
        if cid != last and cid not in already_spoken_ids:
            return cid
    return None


def _live_episode_turn_identity(live_episode_plan: dict) -> str:
    turn_id = str(live_episode_plan.get("turn_id") or "").strip()
    if turn_id:
        return turn_id
    turn_contract = (
        live_episode_plan.get("turn_contract")
        if isinstance(live_episode_plan.get("turn_contract"), dict)
        else {}
    )
    turn_id = str(turn_contract.get("turn_id") or "").strip()
    if turn_id:
        return turn_id
    return _live_episode_turn_type(live_episode_plan)


def _live_episode_turn_type(live_episode_plan: dict) -> str:
    turn_type = str(live_episode_plan.get("turn_type") or "").strip()
    if turn_type:
        return turn_type
    turn_contract = (
        live_episode_plan.get("turn_contract")
        if isinstance(live_episode_plan.get("turn_contract"), dict)
        else {}
    )
    return str(turn_contract.get("turn_type") or "").strip()


def _apply_youtube_live_continuation_policy(
    result: GroupRouterResult,
    *,
    participants: list[dict],
    already_spoken_ids: set[str],
    last_speaker_id: str | None,
    remaining_bot_turns: int | None,
    discussion_mode: str,
    live_episode_plan: dict | None = None,
) -> GroupRouterResult:
    if discussion_mode != "youtube_live" or result.should_respond:
        return result
    if live_episode_plan:
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
