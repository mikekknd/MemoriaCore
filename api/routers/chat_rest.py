"""REST 與 SSE 對話端點：/chat/sync 與 /chat/stream-sync。

WebSocket 端點見 chat_ws.py；
共用編排邏輯見 api/routers/chat/。
"""
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from api.dependencies import (
    get_current_user, get_storage, get_character_manager,
)
from api.session_manager import session_manager
from api.models.requests import ChatSyncRequest
from api.models.responses import ChatSyncResponseDTO
from api.routers.chat.execution import (
    execute_chat_turns,
    iter_chat_sse_events,
    persist_single_turn_result,
    prepare_chat_execution,
)
# execution.py 透過 chat_rest.* lazy lookup 保留既有 monkeypatch / 擴充面。
from api.routers.chat.orchestration import _select_orchestration, _unpack_orchestration_result
from api.routers.chat.group_loop import is_group_session, run_group_chat_loop
from api.routers.chat.roster import normalize_character_ids
from tools.minimax_image import generated_image_path


router = APIRouter(prefix="/chat", tags=["chat"])

YOUTUBE_LIVE_EXTERNAL_SOURCES = {"youtube_live", "youtube_live_director"}
YOUTUBE_LIVE_USER_ID = "__youtube_live__"


def _user_display_name(current_user: dict) -> str:
    return (
        current_user.get("nickname")
        or current_user.get("username")
        or str(current_user.get("id", ""))
    )


def _get_session_character(character_id: str) -> dict:
    char_mgr = get_character_manager()
    char = char_mgr.get_character(character_id)
    if not char:
        from core.system_logger import SystemLogger
        SystemLogger.log_error("character_missing", f"missing_character_id={character_id}; fallback=default")
        char = char_mgr.get_active_character("default")
    return char or {}


def _can_expose_llm_trace(current_user: dict) -> bool:
    return current_user.get("role") == "admin"


def _is_youtube_live_external_context(external_context: dict | None) -> bool:
    if not isinstance(external_context, dict):
        return False
    return str(external_context.get("source") or "").strip() in YOUTUBE_LIVE_EXTERNAL_SOURCES


def _body_declares_youtube_live_scope(body: ChatSyncRequest) -> bool:
    return (
        str(body.channel or "").strip() == "youtube_live"
        and str(body.user_id or "").strip() == YOUTUBE_LIVE_USER_ID
        and str(body.channel_class or "").strip() == "public"
        and str(body.persona_face or "").strip() == "public"
    )


def _normalize_character_prompt_overrides(raw_overrides) -> dict[str, dict]:
    if not isinstance(raw_overrides, dict):
        return {}
    normalized: dict[str, dict] = {}
    for raw_character_id, raw in raw_overrides.items():
        character_id = str(raw_character_id or "").strip()
        if not character_id or not isinstance(raw, dict):
            continue
        system_prompt = str(raw.get("system_prompt") or "").replace("\r", "\n").strip()[:8000]
        if not system_prompt:
            continue
        raw_mode = str(raw.get("mode") or "replace").strip()
        mode = raw_mode if raw_mode in {"replace", "append"} else "replace"
        addressing_raw = raw.get("addressing") if isinstance(raw.get("addressing"), dict) else {}
        addressing = {
            str(key or "").strip()[:120]: str(value or "").strip()[:120]
            for key, value in addressing_raw.items()
            if str(key or "").strip() and str(value or "").strip()
        }
        normalized[character_id[:120]] = {
            "enabled": raw.get("enabled") is not False,
            "mode": mode,
            "system_prompt": system_prompt,
            "self_address": str(raw.get("self_address") or "").strip()[:120],
            "addressing": addressing,
            "opening_intro": str(raw.get("opening_intro") or "").replace("\r", "\n").strip()[:1200],
            "reply_rules": str(raw.get("reply_rules") or "").replace("\r", "\n").strip()[:2000],
        }
    return normalized


def _normalize_live_hosting(raw_hosting) -> dict:
    if not isinstance(raw_hosting, dict):
        return {}
    host_rules = str(raw_hosting.get("host_interaction_rules") or "").replace("\r", "\n").strip()[:4000]
    try:
        segment_turns = int(raw_hosting.get("program_segment_turns", 3) or 3)
    except (TypeError, ValueError):
        segment_turns = 3
    segment_turns = max(1, min(segment_turns, 12))
    segment_state = _normalize_live_segment_state(raw_hosting.get("segment_state"))
    if not host_rules and not segment_state:
        return {}
    normalized = {
        "host_interaction_rules": host_rules,
        "program_segment_turns": segment_turns,
    }
    if segment_state:
        normalized["segment_state"] = segment_state
    return normalized


def _normalize_live_episode_plan(raw_plan) -> dict:
    if not isinstance(raw_plan, dict):
        return {}
    turn_contract = _normalize_live_episode_turn_contract(raw_plan.get("turn_contract"))
    speaker_policy = _normalize_live_episode_speaker_policy(raw_plan.get("speaker_policy"))
    if not speaker_policy and turn_contract:
        speaker_policy = turn_contract.get("speaker_policy") or {}

    normalized = {
        "plan_id": str(raw_plan.get("plan_id") or "").strip()[:120],
        "title": str(raw_plan.get("title") or "").strip()[:200],
        "mode": str(raw_plan.get("mode") or "").strip()[:40],
        "segment_id": str(raw_plan.get("segment_id") or "").strip()[:120],
        "turn_id": str(raw_plan.get("turn_id") or "").strip()[:120],
        "turn_type": str(raw_plan.get("turn_type") or "").strip()[:80],
    }
    try:
        max_turns_override = int(raw_plan.get("max_turns_override"))
    except (TypeError, ValueError):
        max_turns_override = None
    if max_turns_override is not None:
        normalized["max_turns_override"] = max(1, min(max_turns_override, 12))
    if turn_contract:
        normalized["turn_contract"] = turn_contract
    if speaker_policy:
        normalized["speaker_policy"] = speaker_policy
    dialogue_policy = _normalize_live_episode_dialogue_policy(raw_plan.get("dialogue_policy"))
    if dialogue_policy:
        normalized["dialogue_policy"] = dialogue_policy
    output_requirements = _normalize_live_episode_output_requirements(raw_plan.get("output_requirements"))
    if output_requirements:
        normalized["output_requirements"] = output_requirements
    evidence_policy = _normalize_live_episode_evidence_policy(raw_plan.get("evidence_policy"))
    if evidence_policy:
        normalized["evidence_policy"] = evidence_policy
    interrupt_state = _normalize_live_episode_interrupt_state(raw_plan.get("interrupt_state"))
    if interrupt_state:
        normalized["interrupt_state"] = interrupt_state
    return {key: value for key, value in normalized.items() if value not in ("", [], {})}


def _normalize_live_episode_turn_contract(raw_turn) -> dict:
    if not isinstance(raw_turn, dict):
        return {}
    normalized = {
        "turn_id": str(raw_turn.get("turn_id") or "").strip()[:120],
        "turn_type": str(raw_turn.get("turn_type") or "").strip()[:80],
        "intent": str(raw_turn.get("intent") or "").replace("\r", "\n").strip()[:500],
    }
    speaker_policy = _normalize_live_episode_speaker_policy(raw_turn.get("speaker_policy"))
    if speaker_policy:
        normalized["speaker_policy"] = speaker_policy
    return {key: value for key, value in normalized.items() if value not in ("", [], {})}


def _normalize_live_episode_speaker_policy(raw_policy) -> dict:
    if not isinstance(raw_policy, dict):
        return {}
    selection_mode = str(raw_policy.get("selection_mode") or "").strip()
    if selection_mode not in {"router_select", "fixed", "function_router"}:
        selection_mode = ""
    allowed_raw = raw_policy.get("allowed_character_ids")
    if not isinstance(allowed_raw, list):
        allowed_raw = []
    allowed_character_ids = [
        cid
        for raw in (allowed_raw or [])[:20]
        if (cid := str(raw or "").strip()[:120])
    ]
    preferred_role_functions = [
        role
        for raw in (raw_policy.get("preferred_role_functions") or [])[:20]
        if (role := str(raw or "").strip()[:120])
    ] if isinstance(raw_policy.get("preferred_role_functions"), list) else []
    normalized = {}
    if selection_mode:
        normalized["selection_mode"] = selection_mode
    if allowed_character_ids:
        normalized["allowed_character_ids"] = allowed_character_ids
    if preferred_role_functions:
        normalized["preferred_role_functions"] = preferred_role_functions
    if isinstance(raw_policy.get("avoid_repeat_speaker"), bool):
        normalized["avoid_repeat_speaker"] = raw_policy.get("avoid_repeat_speaker")
    return normalized


def _normalize_live_episode_dialogue_policy(raw_policy) -> dict:
    if not isinstance(raw_policy, dict):
        return {}
    normalized = {}
    try:
        min_replies = int(raw_policy.get("min_replies"))
    except (TypeError, ValueError):
        min_replies = None
    try:
        max_replies = int(raw_policy.get("max_replies"))
    except (TypeError, ValueError):
        max_replies = None
    if min_replies is not None:
        normalized["min_replies"] = max(1, min(min_replies, 4))
    if max_replies is not None:
        normalized["max_replies"] = max(1, min(max_replies, 4))
    autonomy = str(raw_policy.get("autonomy") or "").strip()
    if autonomy in {"strict", "guided", "open"}:
        normalized["autonomy"] = autonomy
    preferred_flow = [
        item
        for raw in (raw_policy.get("preferred_flow") or [])[:10]
        if (item := str(raw or "").strip()[:200])
    ] if isinstance(raw_policy.get("preferred_flow"), list) else []
    if preferred_flow:
        normalized["preferred_flow"] = preferred_flow
    return normalized


def _normalize_live_episode_output_requirements(raw_output) -> dict:
    if not isinstance(raw_output, dict):
        return {}
    normalized = {}
    try:
        max_sentences = int(raw_output.get("max_sentences"))
    except (TypeError, ValueError):
        max_sentences = None
    if max_sentences is not None:
        normalized["max_sentences"] = max(1, min(max_sentences, 8))
    for key in ("must_end_with_question", "allow_audience_question", "should_handoff"):
        if isinstance(raw_output.get(key), bool):
            normalized[key] = raw_output.get(key)
    handoff = str(raw_output.get("handoff_target_function") or "").strip()[:120]
    if handoff:
        normalized["handoff_target_function"] = handoff
    return normalized


def _normalize_live_episode_evidence_policy(raw_evidence) -> dict:
    if not isinstance(raw_evidence, dict):
        return {}
    queries = [
        query
        for raw in (raw_evidence.get("queries") or [])[:12]
        if (query := str(raw or "").replace("\r", "\n").strip()[:300])
    ] if isinstance(raw_evidence.get("queries"), list) else []
    required_entities = [
        entity
        for raw in (raw_evidence.get("required_entities") or [])[:20]
        if (entity := str(raw or "").strip()[:160])
    ] if isinstance(raw_evidence.get("required_entities"), list) else []
    try:
        max_cards = int(raw_evidence.get("max_cards"))
    except (TypeError, ValueError):
        max_cards = None
    normalized = {}
    if queries:
        normalized["queries"] = queries
    if required_entities:
        normalized["required_entities"] = required_entities
    if max_cards is not None:
        normalized["max_cards"] = max(0, min(max_cards, 8))
    if isinstance(raw_evidence.get("allow_unverified_claims"), bool):
        normalized["allow_unverified_claims"] = raw_evidence.get("allow_unverified_claims")
    return normalized


def _normalize_live_episode_interrupt_state(raw_state) -> dict:
    if not isinstance(raw_state, dict):
        return {}
    normalized = {}
    status = str(raw_state.get("status") or "").strip()[:80]
    if status:
        normalized["status"] = status
    try:
        remaining = int(raw_state.get("remaining_interrupt_turns"))
    except (TypeError, ValueError):
        remaining = None
    if remaining is not None:
        normalized["remaining_interrupt_turns"] = max(0, min(remaining, 12))
    source_event_id = str(raw_state.get("source_event_id") or "").strip()[:120]
    if source_event_id:
        normalized["source_event_id"] = source_event_id
    return normalized


def _normalize_live_segment_step(raw_step, *, include_description: bool = False) -> dict:
    if not isinstance(raw_step, dict):
        return {}
    step = {
        "step_id": str(raw_step.get("step_id") or "").strip()[:40],
        "name": str(raw_step.get("name") or "").strip()[:160],
    }
    description = str(raw_step.get("description") or "").replace("\r", "\n").strip()[:500]
    if include_description and description:
        step["description"] = description
    return step if step["step_id"] and step["name"] else {}


def _normalize_live_segment_state(raw_state) -> dict:
    if not isinstance(raw_state, dict):
        return {}
    current_step = _normalize_live_segment_step(raw_state.get("current_step"), include_description=True)
    if not current_step:
        return {}
    try:
        topic_entry_id = int(raw_state.get("topic_entry_id") or 0)
    except (TypeError, ValueError):
        topic_entry_id = 0
    try:
        turns_in_step = int(raw_state.get("turns_in_step", 0) or 0)
    except (TypeError, ValueError):
        turns_in_step = 0
    completed_steps = [
        step
        for raw_step in raw_state.get("completed_steps") or []
        if isinstance(raw_step, dict)
        if (step := _normalize_live_segment_step(raw_step, include_description=False))
    ][:20]
    remaining_steps = [
        step
        for raw_step in raw_state.get("remaining_steps") or []
        if isinstance(raw_step, dict)
        if (step := _normalize_live_segment_step(raw_step, include_description=True))
    ][:20]
    normalized = {
        "topic": str(raw_state.get("topic") or "").strip()[:200],
        "topic_entry_id": max(0, topic_entry_id),
        "current_step": current_step,
        "completed_steps": completed_steps,
        "remaining_steps": remaining_steps,
        "turns_in_step": max(0, turns_in_step),
        "last_transition_reason": str(raw_state.get("last_transition_reason") or "").strip()[:200],
    }
    if raw_state.get("all_steps_completed"):
        normalized["all_steps_completed"] = True
    return normalized


def _live_session_scope_for_external_context(
    body: ChatSyncRequest | None,
    external_context: dict | None,
) -> dict | None:
    """YouTube live bridge 固定使用 public/transient 對話 scope。

    Bridge 端使用 admin auth 只是為了取得 API 權限，不代表直播內容可以寫進
    admin 的 private face。這裡不信任 client 傳入的 user/persona override，
    只依 external context source 決定 live scope。
    """
    if not _is_youtube_live_external_context(external_context):
        return None
    summary = external_context.get("summary") if isinstance(external_context.get("summary"), dict) else {}
    channel_uid = (
        str(summary.get("source_session_id") or "").strip()
        or str(external_context.get("source_session_id") or "").strip()
        or str((body.channel_uid if body else "") or "").strip()
        or "youtube_live"
    )
    return {
        "channel": "youtube_live",
        "channel_uid": channel_uid[:128],
        "user_id": YOUTUBE_LIVE_USER_ID,
        "channel_class": "public",
        "persona_face": "public",
    }


def _session_matches_scope(session, scope: dict | None) -> bool:
    if not scope:
        return True
    return (
        session.user_id == scope["user_id"]
        and session.channel == scope["channel"]
        and session.channel_uid == scope["channel_uid"]
        and session.channel_class == scope["channel_class"]
        and session.persona_face == scope["persona_face"]
    )


def _chat_user_display_name(current_user: dict, external_context: dict | None) -> str:
    """YouTube live 注入不帶真人帳號名稱，避免角色誤以為是私人對話。"""
    if _is_youtube_live_external_context(external_context):
        return ""
    return _user_display_name(current_user)


def _resolve_external_context_payload(body: ChatSyncRequest) -> tuple[dict | None, dict]:
    """將外部 bridge payload 轉成暫態 LLM context。

    Bridge 提供的內容一律視為不可信外部上下文，只注入本次 LLM 呼叫，
    不寫入對話紀錄或個人記憶。
    """
    raw = body.external_context if isinstance(body.external_context, dict) else {}
    if not raw:
        return None, {}

    source = re.sub(r"[^A-Za-z0-9_.:-]", "_", str(raw.get("source", "external_bridge") or "external_bridge"))[:64]
    context_text = str(raw.get("context_text", "") or "").replace("\r", "\n").strip()
    if not context_text:
        return None, {}

    try:
        max_chars = int(raw.get("max_chars", 12000))
    except (TypeError, ValueError):
        max_chars = 12000
    max_chars = max(1000, min(max_chars, 20000))
    if len(context_text) > max_chars:
        context_text = context_text[:max_chars].rstrip()

    raw_summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
    try:
        event_count = int(raw_summary.get("event_count", 0) or 0)
    except (TypeError, ValueError):
        event_count = 0
    summary = {
        "source": source,
        "source_session_id": str(raw.get("source_session_id", "") or ""),
        "event_count": event_count,
        "truncated": len(str(raw.get("context_text", "") or "")) > len(context_text),
    }
    if isinstance(raw.get("event_ids"), list):
        summary["event_ids"] = [str(x) for x in raw["event_ids"][:100]]
    if raw_summary.get("dropped_count") is not None:
        try:
            summary["dropped_count"] = int(raw_summary.get("dropped_count") or 0)
        except (TypeError, ValueError):
            summary["dropped_count"] = 0
    for key in ("connector_id", "video_id", "live_chat_id"):
        if raw.get(key):
            summary[key] = str(raw.get(key))
    group_turn_limit = raw.get("group_turn_limit", raw_summary.get("group_turn_limit"))
    try:
        group_turn_limit = int(group_turn_limit)
    except (TypeError, ValueError):
        group_turn_limit = None
    if group_turn_limit is not None:
        group_turn_limit = max(1, min(group_turn_limit, 12))
        summary["group_turn_limit"] = group_turn_limit
    visible_events = _normalize_visible_events(raw.get("visible_events"))
    character_prompt_overrides = {}
    live_hosting = {}
    live_episode_plan = {}
    if source in YOUTUBE_LIVE_EXTERNAL_SOURCES and _body_declares_youtube_live_scope(body):
        character_prompt_overrides = _normalize_character_prompt_overrides(raw.get("character_prompt_overrides"))
        live_hosting = _normalize_live_hosting(raw.get("live_hosting"))
        live_episode_plan = _normalize_live_episode_plan(raw.get("live_episode_plan"))
    for key in ("episode_plan_id", "episode_plan_turn_id", "episode_plan_mode"):
        value = raw_summary.get(key)
        if value is not None:
            summary[key] = str(value or "").strip()[:120]
    context = {
        "source": source,
        "context_text": context_text,
        "visible_events": visible_events,
        "summary": summary,
    }
    if group_turn_limit is not None:
        context["group_turn_limit"] = group_turn_limit
    if character_prompt_overrides:
        context["character_prompt_overrides"] = character_prompt_overrides
    if live_hosting:
        context["live_hosting"] = live_hosting
    if live_episode_plan:
        context["live_episode_plan"] = live_episode_plan
    return context, summary


def _normalize_visible_events(raw_events) -> list[dict]:
    if not isinstance(raw_events, list):
        return []
    events: list[dict] = []
    for raw in raw_events[:100]:
        if not isinstance(raw, dict):
            continue
        author = str(raw.get("author_display_name") or raw.get("author") or "匿名觀眾").strip()
        author_id = str(raw.get("author_channel_id") or raw.get("author_id") or "").strip()
        message = str(raw.get("message_text") or raw.get("text") or "").replace("\r", " ").replace("\n", " ").strip()
        if not message:
            continue
        events.append({
            "event_id": str(raw.get("event_id") or raw.get("id") or "").strip(),
            "author_display_name": author or "匿名觀眾",
            "author_channel_id": author_id,
            "message_text": message,
            "priority_class": str(raw.get("priority_class") or "normal"),
            "amount_display_string": str(raw.get("amount_display_string") or "").strip(),
            "safety_label": str(raw.get("safety_label") or "clean"),
        })
    return events


def _visible_event_display_line(event: dict) -> str:
    author = str(event.get("author_display_name") or "匿名觀眾").strip() or "匿名觀眾"
    message = str(event.get("message_text") or "").replace("\r", " ").replace("\n", " ").strip()
    if not message:
        return ""
    if str(event.get("priority_class") or "normal") == "super_chat":
        amount = str(event.get("amount_display_string") or "").strip()
        prefix = f"[SC {amount}] " if amount else "[SC] "
        if str(event.get("safety_label") or "clean") != "clean":
            message = "已收到一則可疑 SC，將安全回應。"
        return f"{prefix}{author}: {message}"
    return f"{author}: {message}"


def _director_display_from_context(external_context: dict | None) -> str:
    if not external_context:
        return "讓我們繼續直播節奏。"
    summary = external_context.get("summary") if isinstance(external_context.get("summary"), dict) else {}
    action = str(summary.get("action") or "").strip()
    return {
        "continue_topic": "讓我們繼續目前話題。",
        "transition_topic": "讓我們繼續進行下一個話題。",
        "anchor_to_topic": "讓我們回到本場直播主題。",
        "reply_chat_batch": "回應聊天室留言。",
        "reply_super_chat_batch": "回應 Super Chat 留言。",
        "closing_super_chat_thanks": "感謝本場 Super Chat。",
        "recap": "整理一下剛才的重點。",
        "close_topic": "先收束目前話題。",
    }.get(action, "讓我們繼續直播節奏。")


def _resolve_chat_display_content(body: ChatSyncRequest, external_context: dict | None) -> str:
    """決定要寫入聊天紀錄並顯示給人的 user 訊息。

    `body.content` 可能是 Bridge 給 LLM/router 的完整控制 prompt；有 external
    context 時不可直接保存。Bridge 可用 `display_content` 明確提供人類可見文字；
    若未提供，則只從 visible_events 取出觀眾姓名與留言內容。
    """
    explicit = str(body.display_content or "").replace("\r", "\n").strip()
    if explicit:
        return explicit
    if external_context:
        lines: list[str] = []
        for event in external_context.get("visible_events") or []:
            if not isinstance(event, dict):
                continue
            line = _visible_event_display_line(event)
            if line:
                lines.append(line)
        if lines:
            return "\n".join(lines)
        source = str(external_context.get("source") or "").strip()
        if source == "youtube_live_director":
            return _director_display_from_context(external_context)
        return "外部上下文已提供給 AI。"
    return str(body.content or "").strip()


def _visible_context_lines(external_context: dict, context_text: str, preview_limit: int) -> list[str]:
    visible_events = external_context.get("visible_events")
    if isinstance(visible_events, list) and visible_events:
        lines: list[str] = []
        for event in visible_events[:preview_limit]:
            line = _visible_event_display_line(event)
            if line:
                lines.append(line)
        return lines
    return [line.strip() for line in context_text.splitlines() if line.strip()][:preview_limit]


def _build_external_context_visible_event(
    external_context: dict | None,
    summary: dict,
) -> tuple[str, dict] | None:
    if not external_context:
        return None
    context_text = str(external_context.get("context_text") or "").strip()
    if not context_text:
        return None

    source = str(summary.get("source") or external_context.get("source") or "external").strip()
    if source == "youtube_live":
        event_type = "youtube_live_chat_batch"
        title = "YouTube Live 留言注入"
        preview_limit = 3
        preview_lines = _visible_context_lines(external_context, context_text, preview_limit)
    elif source == "youtube_live_director":
        return None
    else:
        event_type = "external_context_notice"
        title = "外部上下文注入"
        preview_limit = 3
        preview_lines = _visible_context_lines(external_context, context_text, preview_limit)
    fallback_line_count = len([line for line in context_text.splitlines() if line.strip()])
    event_count = int(summary.get("event_count") or len(external_context.get("visible_events") or []) or fallback_line_count)
    if source == "youtube_live_director":
        event_count = 1
    hidden_count = max(0, event_count - len(preview_lines))

    content_lines = [f"{title}：{event_count} 則"]
    content_lines.extend(preview_lines)
    if hidden_count:
        content_lines.append(f"另有 {hidden_count} 則未顯示。")

    debug_info = {
        "event_type": event_type,
        "llm_visible": False,
        "source": source,
        "preview_count": len(preview_lines),
        "event_count": event_count,
        "summary": summary,
    }
    return "\n".join(content_lines), debug_info


def _external_context_user_prompt(content: str, external_context: dict | None) -> str:
    """給 LLM/router 的暫態 user prompt；不寫入 DB。

    有 external context 時，明確告訴 router/模型資料已由 bridge 提供，
    避免把「回應 YouTube 留言」誤判成需要瀏覽器或搜尋工具。
    """
    if not external_context:
        return content
    source = str(external_context.get("source") or "external").strip() or "external"
    source_label = "直播流程" if source == "youtube_live_director" else source
    return (
        f"{content}\n\n"
        f"[外部上下文已由 {source_label} 提供；請只根據本次注入的 external_chat_context 回應。"
        "不要開啟瀏覽器、不要搜尋網頁、不要嘗試連線外部平台。]"
    )


def _transient_user_content_for_external_context(body: ChatSyncRequest, external_context: dict | None) -> str:
    if not external_context:
        return ""
    source = str(external_context.get("source") or "").strip()
    if source == "youtube_live_director":
        public_turn_instruction = str(body.content or "").replace("\r", "\n").strip()
        if external_context.get("director_dialogue_expansion_enabled") is False:
            base_instruction = (
                "請根據已提供的直播流程提示回應。"
                "這是直播自主推進，本次只需要目前被導播或路由指定的一位角色完成回應；"
                "不要要求其他角色接話。"
                "除非正在回應留言或 Super Chat，否則不要把問題丟回觀眾。"
            )
        else:
            base_instruction = (
                "請根據已提供的直播流程提示回應。"
                "這是直播自主推進，不保證有觀眾即時回覆；請讓角色彼此接話、補充或提出不同角度。"
                "除非正在回應留言或 Super Chat，否則不要把問題丟回觀眾。"
            )
        if public_turn_instruction:
            return f"{public_turn_instruction}\n\n{base_instruction}"
        return base_instruction
    if source == "youtube_live":
        return "請根據已帶入的 YouTube 直播留言上下文回應。"
    return "請根據已帶入的外部上下文回應。"


def _memory_write_policy_for_request(body: ChatSyncRequest, external_context: dict | None) -> str:
    if external_context:
        return "transient"
    return body.memory_write_policy


def _messages_for_orchestration(
    messages: list[dict],
    body: ChatSyncRequest,
    external_context: dict | None,
) -> list[dict]:
    out = list(messages)
    transient = _transient_user_content_for_external_context(body, external_context)
    if transient:
        out.append({
            "role": "user",
            "content": transient,
            "debug_info": {"transient_external_context_anchor": True},
        })
    return out


def _external_context_group_turn_limit(session, external_context: dict | None) -> int | None:
    if not external_context:
        return None
    participant_count = len(session.active_character_ids or [session.character_id])
    source = str(external_context.get("source") or "").strip()
    if source == "youtube_live_director":
        summary = external_context.get("summary") if isinstance(external_context.get("summary"), dict) else {}
        live_episode_plan = (
            external_context.get("live_episode_plan")
            if isinstance(external_context.get("live_episode_plan"), dict)
            else {}
        )
        raw_limit = live_episode_plan.get(
            "max_turns_override",
            external_context.get("group_turn_limit", summary.get("group_turn_limit", 3)),
        )
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = 3
        return max(1, min(limit, 12))
    return max(1, min(participant_count, 3))


async def _persist_incoming_chat_message(
    session_id: str,
    body: ChatSyncRequest,
    external_context: dict | None,
    external_context_summary: dict,
) -> None:
    if external_context:
        visible_event = _build_external_context_visible_event(external_context, external_context_summary)
        if visible_event:
            content, debug_info = visible_event
            await session_manager.add_system_event(session_id, content, debug_info)
        return
    display_content = _resolve_chat_display_content(body, external_context)
    await session_manager.add_user_message(session_id, display_content)


# ════════════════════════════════════════════════════════════
# SECTION: 共用 — Session 取得/還原/建立
# ════════════════════════════════════════════════════════════

async def _resolve_session(
    session_id: str | None,
    current_user: dict,
    character_ids: list[str] | None = None,
    group_name: str | None = None,
    external_context: dict | None = None,
):
    """取得 session：優先從記憶體取，其次從 DB 還原，最後才建新 session。"""
    scope = _live_session_scope_for_external_context(None, external_context)
    user_id = scope["user_id"] if scope else str(current_user["id"])
    session = None
    if session_id:
        session = await session_manager.get(session_id)
        if session is not None and scope and not _session_matches_scope(session, scope):
            session = None
        if session is not None and not scope and session.user_id != user_id:
            raise HTTPException(403, detail="Session owner mismatch")
        if session is None:
            try:
                session = await session_manager.restore_from_db(session_id, user_id=user_id)
            except PermissionError:
                if scope:
                    session = None
                else:
                    raise HTTPException(403, detail="Session owner mismatch")
            if session is not None and scope and not _session_matches_scope(session, scope):
                session = None
    if session is None:
        prefs = get_storage().load_prefs()
        channel = scope["channel"] if scope else "dashboard"
        channel_uid = scope["channel_uid"] if scope else user_id
        channel_class = scope["channel_class"] if scope else ("private" if current_user.get("role") == "admin" else "public")
        persona_face = scope["persona_face"] if scope else ("private" if current_user.get("role") == "admin" else "public")
        try:
            normalized = normalize_character_ids(character_ids)
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc))
        if normalized is not None:
            requested_ids, _names = normalized
        else:
            requested_ids = [prefs.get("active_character_id", "default")]
        session = await session_manager.create(
            channel=channel,
            channel_uid=channel_uid,
            user_id=user_id,
            character_id=requested_ids[0],
            character_ids=requested_ids,
            session_mode="group" if len(requested_ids) > 1 else "single",
            group_name=group_name.strip() if isinstance(group_name, str) else "",
            channel_class=channel_class,
            persona_face=persona_face,
        )
    return session


# ════════════════════════════════════════════════════════════
# SECTION: 同步 REST 端點 (/chat/sync)
# ════════════════════════════════════════════════════════════

@router.post("/sync", response_model=ChatSyncResponseDTO)
async def chat_sync(body: ChatSyncRequest, current_user: dict = Depends(get_current_user)):
    prepared = await prepare_chat_execution(body, current_user)
    return await execute_chat_turns(prepared)


# ════════════════════════════════════════════════════════════
# SECTION: SSE 串流端點 (/chat/stream-sync)
# ════════════════════════════════════════════════════════════

@router.post("/stream-sync")
async def chat_stream_sync(body: ChatSyncRequest, current_user: dict = Depends(get_current_user)):
    """
    與 /sync 功能相同，但以 SSE (Server-Sent Events) 串流回傳中間狀態。
    事件格式：data: {"type": "tool_status"|"result"|"error", ...}
    """
    prepared = await prepare_chat_execution(body, current_user)
    return StreamingResponse(iter_chat_sse_events(prepared), media_type="text/event-stream")


# ════════════════════════════════════════════════════════════
# SECTION: 已生成圖片讀取端點
# ════════════════════════════════════════════════════════════

@router.get("/generated-images/{session_id}/{image_id}")
async def get_generated_image(
    session_id: str,
    image_id: str,
    current_user: dict = Depends(get_current_user),
):
    """讀取目前登入使用者在指定 session 生成的圖片。"""
    storage = get_storage()
    session_info = storage.get_session_info(session_id)
    if not session_info:
        raise HTTPException(404, detail="Image session not found")
    if session_info.get("user_id") != str(current_user["id"]):
        raise HTTPException(403, detail="Session owner mismatch")

    clean_image_id = image_id.removesuffix(".jpeg")
    if not re.fullmatch(r"[A-Fa-f0-9]{32}", clean_image_id):
        raise HTTPException(404, detail="Image not found")

    path = generated_image_path(str(current_user["id"]), session_id, clean_image_id)
    if not path.exists() or not path.is_file():
        raise HTTPException(404, detail="Image not found")

    return FileResponse(Path(path), media_type="image/jpeg")


# ════════════════════════════════════════════════════════════
# SECTION: 工具函式
# ════════════════════════════════════════════════════════════

def _strip_markdown(text: str) -> str:
    """簡易去除 Markdown 符號，讓 TTS 讀出來更自然。"""
    import re
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)   # bold / italic
    text = re.sub(r'#{1,6}\s*', '', text)                   # headers
    text = re.sub(r'`{1,3}[^`]*`{1,3}', '', text)           # code / code block
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)    # links
    text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text)         # images
    text = re.sub(r'^\s*[-*>|]\s*', '', text, flags=re.MULTILINE)  # list/blockquote
    text = re.sub(r'\n{2,}', '。', text)                    # 段落換行 → 句號
    text = re.sub(r'\n', ' ', text)                          # 剩餘換行 → 空白
    return text.strip()
