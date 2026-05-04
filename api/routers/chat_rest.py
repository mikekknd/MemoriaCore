"""REST 與 SSE 對話端點：/chat/sync 與 /chat/stream-sync。

WebSocket 端點見 chat_ws.py；
共用編排邏輯見 api/routers/chat/。
"""
import asyncio
import json
import re
import queue as sync_queue
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

import base64

from api.dependencies import (
    get_current_user, get_storage, get_tts_client, get_character_manager, get_router,
    require_db_writes_enabled,
)
from api.session_manager import session_manager
from api.models.requests import ChatSyncRequest
from api.models.responses import ChatSyncResponseDTO, ChatTurnDTO, RetrievalContextDTO
from api.routers.chat.orchestration import _select_orchestration, _unpack_orchestration_result
from api.routers.chat.pipeline import _run_memory_pipeline_bg
from api.routers.chat.group_loop import is_group_session, run_group_chat_loop
from api.routers.chat.roster import apply_roster_update, normalize_character_ids
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
    visible_events = _normalize_visible_events(raw.get("visible_events"))
    return {
        "source": source,
        "context_text": context_text,
        "visible_events": visible_events,
        "summary": summary,
    }, summary


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
        event_type = "youtube_live_director_notice"
        title = "直播節奏"
        preview_limit = 1
        preview_lines = [_director_display_from_context(external_context)]
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
        return "請根據已提供的直播流程提示回應。"
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
        raw_limit = external_context.get("group_turn_limit", summary.get("group_turn_limit", 3))
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
    require_db_writes_enabled()
    external_context, external_context_summary = _resolve_external_context_payload(body)
    session = await _resolve_session(body.session_id, current_user, body.character_ids, body.group_name, external_context)
    sid = session.session_id
    orchestration_prompt = _external_context_user_prompt(body.content, external_context)
    transient_user_content = _transient_user_content_for_external_context(body, external_context)
    memory_write_policy = _memory_write_policy_for_request(body, external_context)

    try:
        roster_event = await apply_roster_update(session, body.character_ids, group_name=body.group_name)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    if roster_event:
        session = await session_manager.get(sid) or session

    await _persist_incoming_chat_message(sid, body, external_context, external_context_summary)
    s = await session_manager.get(sid)
    if not s:
        return ChatSyncResponseDTO(session_id=sid, reply="Session error", roster_event=roster_event)

    user_prefs = get_storage().load_prefs()
    orchestration_fn = _select_orchestration(user_prefs)
    include_speech = body.include_speech and get_tts_client() is not None

    if is_group_session(s):
        extra_session_ctx = {}
        if external_context:
            extra_session_ctx["external_chat_context"] = external_context
        if memory_write_policy == "transient":
            extra_session_ctx["memory_write_policy"] = "transient"
        turns = await run_group_chat_loop(
            session=s,
            user_prompt=orchestration_prompt,
            user_prefs=user_prefs,
            orchestration_fn=orchestration_fn,
            user_name=_chat_user_display_name(current_user, external_context),
            expose_llm_trace=_can_expose_llm_trace(current_user),
            extra_session_ctx=extra_session_ctx or None,
            transient_user_content=transient_user_content,
            max_turns_override=_external_context_group_turn_limit(s, external_context),
        )
        if not turns:
            return ChatSyncResponseDTO(session_id=sid, reply="（無回應）", turns=[], roster_event=roster_event)
        if include_speech:
            from core.chat_orchestrator.coordinator import _generate_tts_speech
            for turn in turns:
                if not turn.get("speech"):
                    reply_char = _get_session_character(turn["character_id"])
                    turn["speech"] = _generate_tts_speech(
                        turn["reply"],
                        reply_char.get("tts_language", ""),
                        reply_char.get("tts_rules", ""),
                        get_router(),
                    )
        final_turn = turns[-1]
        if external_context_summary:
            for turn in turns:
                turn.setdefault("retrieval_context", {})["external_context"] = external_context_summary
        joined_reply = "\n\n".join(
            f"{turn['character_name']}: {turn['reply']}" for turn in turns
        )
        return ChatSyncResponseDTO(
            session_id=sid,
            message_id=final_turn.get("message_id"),
            reply=joined_reply,
            extracted_entities=final_turn.get("extracted_entities", []),
            retrieval_context=RetrievalContextDTO(**final_turn.get("retrieval_context", {})),
            cited_memory_uids=final_turn.get("cited_memory_uids", []),
            internal_thought=final_turn.get("internal_thought"),
            speech=final_turn.get("speech"),
            thinking_speech=final_turn.get("thinking_speech"),
            character_id=final_turn.get("character_id"),
            character_name=final_turn.get("character_name"),
            turns=[ChatTurnDTO(**turn) for turn in turns],
            roster_event=roster_event,
        )

    session_ctx = {
        "user_id": session.user_id,
        "character_id": session.character_id,
        "persona_face": session.persona_face,
        "session_id": sid,
        "bot_id": session.bot_id,
        "channel": session.channel,
        "user_name": _chat_user_display_name(current_user, external_context),
        "active_character_ids": list(session.active_character_ids or [session.character_id]),
        "session_mode": session.session_mode,
        "group_name": session.group_name,
        "expose_llm_trace": _can_expose_llm_trace(current_user),
    }
    if external_context:
        session_ctx["external_chat_context"] = external_context
    if memory_write_policy == "transient":
        session_ctx["memory_write_policy"] = "transient"

    result = await asyncio.to_thread(
        orchestration_fn,
        _messages_for_orchestration(s.messages, body, external_context), list(s.last_entities), orchestration_prompt, user_prefs,
        session_ctx=session_ctx,
    )
    reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_data, \
        inner_thought, status_metrics, tone, speech, thinking_speech, cited_uids, \
        _tool_state_export = _unpack_orchestration_result(result)
    if external_context_summary:
        retrieval_ctx["external_context"] = external_context_summary

    reply_char = _get_session_character(session.character_id)
    character_name = reply_char.get("name") or session.character_id
    # cited_uids 透過 retrieval_ctx["cited_uids"] 隨 debug_info 持久化（見 storage_manager.save_conversation_message）
    message_id = await session_manager.add_assistant_message(
        sid, reply_text, retrieval_ctx, new_entities,
        persona_state={"internal_thought": inner_thought},
        character_name=character_name,
        character_id=session.character_id,
    )

    if topic_shifted:
        await session_manager.bridge(sid)
        if pipeline_data:
            asyncio.create_task(_run_memory_pipeline_bg(sid, pipeline_data))

    # /sync 為完整請求-回應週期；外部 bridge 可關閉 speech 以避免注入被 TTS 翻譯阻塞。
    if include_speech and not speech:
        from core.chat_orchestrator.coordinator import _generate_tts_speech
        speech = _generate_tts_speech(
            reply_text,
            reply_char.get("tts_language", ""),
            reply_char.get("tts_rules", ""),
            get_router(),
        )

    return ChatSyncResponseDTO(
        session_id=sid,
        message_id=message_id,
        reply=reply_text,
        extracted_entities=new_entities,
        retrieval_context=RetrievalContextDTO(**retrieval_ctx),
        cited_memory_uids=cited_uids,
        internal_thought=inner_thought,
        speech=speech,
        thinking_speech=thinking_speech or None,
        character_id=session.character_id,
        character_name=character_name,
        turns=[ChatTurnDTO(
            message_id=message_id,
            reply=reply_text,
            extracted_entities=new_entities,
            retrieval_context=RetrievalContextDTO(**retrieval_ctx),
            cited_memory_uids=cited_uids,
            internal_thought=inner_thought,
            speech=speech,
            thinking_speech=thinking_speech or None,
            character_id=session.character_id,
            character_name=character_name,
            turn_index=0,
            is_final=True,
        )],
        roster_event=roster_event,
    )


# ════════════════════════════════════════════════════════════
# SECTION: SSE 串流端點 (/chat/stream-sync)
# ════════════════════════════════════════════════════════════

@router.post("/stream-sync")
async def chat_stream_sync(body: ChatSyncRequest, current_user: dict = Depends(get_current_user)):
    """
    與 /sync 功能相同，但以 SSE (Server-Sent Events) 串流回傳中間狀態。
    事件格式：data: {"type": "tool_status"|"result"|"error", ...}
    """
    require_db_writes_enabled()
    # 優先從記憶體取得 session；找不到時先嘗試從 DB 還原（後端重啟後記憶體清空的情況）；
    # 都沒有才建新 session（channel 統一用 streamlit，確保能出現在 UI session 列表）
    external_context, external_context_summary = _resolve_external_context_payload(body)
    session = await _resolve_session(body.session_id, current_user, body.character_ids, body.group_name, external_context)
    sid = session.session_id
    orchestration_prompt = _external_context_user_prompt(body.content, external_context)
    transient_user_content = _transient_user_content_for_external_context(body, external_context)
    memory_write_policy = _memory_write_policy_for_request(body, external_context)

    try:
        roster_event = await apply_roster_update(session, body.character_ids, group_name=body.group_name)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    if roster_event:
        session = await session_manager.get(sid) or session

    await _persist_incoming_chat_message(sid, body, external_context, external_context_summary)
    s = await session_manager.get(sid)
    if not s:
        async def _err():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Session error'}, ensure_ascii=False)}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    user_prefs = get_storage().load_prefs()
    event_q = sync_queue.Queue()
    orchestration_fn = _select_orchestration(user_prefs)

    session_ctx_sse = {
        "user_id": session.user_id,
        "character_id": session.character_id,
        "persona_face": session.persona_face,
        "session_id": sid,
        "bot_id": session.bot_id,
        "channel": session.channel,
        "user_name": _chat_user_display_name(current_user, external_context),
        "active_character_ids": list(session.active_character_ids or [session.character_id]),
        "session_mode": session.session_mode,
        "group_name": session.group_name,
        "expose_llm_trace": _can_expose_llm_trace(current_user),
    }
    if external_context:
        session_ctx_sse["external_chat_context"] = external_context
    if memory_write_policy == "transient":
        session_ctx_sse["memory_write_policy"] = "transient"

    def on_event(data: dict):
        event_q.put(data)

    async def event_generator():
        if roster_event:
            yield f"data: {json.dumps(roster_event, ensure_ascii=False)}\n\n"

        if is_group_session(s):
            extra_session_ctx = {}
            if external_context:
                extra_session_ctx["external_chat_context"] = external_context
            if memory_write_policy == "transient":
                extra_session_ctx["memory_write_policy"] = "transient"

            def on_turn(turn: dict):
                event_q.put({
                    "type": "result",
                    "session_id": sid,
                    **turn,
                })

            group_task = asyncio.create_task(run_group_chat_loop(
                session=s,
                user_prompt=orchestration_prompt,
                user_prefs=user_prefs,
                orchestration_fn=orchestration_fn,
                on_event=on_event,
                on_turn=on_turn,
                user_name=_chat_user_display_name(current_user, external_context),
                expose_llm_trace=_can_expose_llm_trace(current_user),
                extra_session_ctx=extra_session_ctx or None,
                transient_user_content=transient_user_content,
                max_turns_override=_external_context_group_turn_limit(s, external_context),
            ))

            while not group_task.done():
                try:
                    event = event_q.get_nowait()
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except sync_queue.Empty:
                    await asyncio.sleep(0.1)

            while not event_q.empty():
                event = event_q.get_nowait()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            try:
                turns = group_task.result()
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
                return

            yield f"data: {json.dumps({'type': 'group_done', 'session_id': sid, 'turn_count': len(turns)}, ensure_ascii=False)}\n\n"

            tts = get_tts_client() if body.include_speech else None
            if tts:
                from core.chat_orchestrator.coordinator import _generate_tts_speech
                for turn in turns:
                    reply_char = _get_session_character(turn["character_id"])
                    speech = turn.get("speech")
                    _tts_lang = reply_char.get("tts_language", "")
                    _tts_rules = reply_char.get("tts_rules", "")
                    if _tts_lang and not speech:
                        speech = await asyncio.to_thread(
                            _generate_tts_speech, turn["reply"], _tts_lang, _tts_rules, get_router())
                    raw_tts_text = speech or _strip_markdown(turn["reply"])
                    tts_text = raw_tts_text[:400] if raw_tts_text else ""
                    if not tts_text:
                        continue
                    try:
                        audio_bytes = await tts.synthesize(tts_text)
                        if audio_bytes:
                            tts_event = {
                                "type": "tts_audio",
                                "format": "mp3",
                                "data": base64.b64encode(audio_bytes).decode("ascii"),
                                "turn_index": turn["turn_index"],
                                "character_id": turn["character_id"],
                                "character_name": turn["character_name"],
                            }
                            yield f"data: {json.dumps(tts_event, ensure_ascii=False)}\n\n"
                    except Exception:
                        pass
            return

        orch_task = asyncio.create_task(asyncio.to_thread(
            orchestration_fn,
            _messages_for_orchestration(s.messages, body, external_context), list(s.last_entities), orchestration_prompt, user_prefs,
            on_event=on_event, session_ctx=session_ctx_sse,
        ))

        # 持續輪詢 event queue，即時串流中間狀態給前端
        while not orch_task.done():
            try:
                event = event_q.get_nowait()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except sync_queue.Empty:
                await asyncio.sleep(0.1)

        # 排空佇列中剩餘的事件
        while not event_q.empty():
            event = event_q.get_nowait()
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        # 取得最終結果
        try:
            result = orch_task.result()
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
            return

        reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_data, \
            inner_thought, status_metrics, tone, speech, thinking_speech, cited_uids, \
            _tool_state_export = _unpack_orchestration_result(result)
        if external_context_summary:
            retrieval_ctx["external_context"] = external_context_summary

        # 寫入 session 及背景任務（與 /sync 相同）
        reply_char = _get_session_character(session.character_id)
        character_name = reply_char.get("name") or session.character_id
        # cited_uids 透過 retrieval_ctx["cited_uids"] 隨 debug_info 持久化
        message_id = await session_manager.add_assistant_message(
            sid, reply_text, retrieval_ctx, new_entities,
            persona_state={"internal_thought": inner_thought},
            character_name=character_name,
            character_id=session.character_id,
        )

        if topic_shifted:
            await session_manager.bridge(sid)
            if pipeline_data:
                asyncio.create_task(_run_memory_pipeline_bg(sid, pipeline_data))

        # 送出最終結果（含實際使用的 session_id，讓 UI 同步更新）
        final = {
            "type": "result",
            "session_id": sid,
            "message_id": message_id,
            "reply": reply_text,
            "extracted_entities": new_entities,
            "retrieval_context": retrieval_ctx,
            "cited_memory_uids": cited_uids,
            "internal_thought": inner_thought,
            "thinking_speech": thinking_speech or None,
            "character_id": session.character_id,
            "character_name": character_name,
            "turn_index": 0,
            "is_final": True,
        }
        yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"

        # TTS 合成（若啟用）：result 事件已送出，翻譯 + 合成在此背景執行，不阻塞文字顯示
        tts = get_tts_client() if body.include_speech else None
        if tts:
            from core.chat_orchestrator.coordinator import _generate_tts_speech
            _tts_lang = reply_char.get("tts_language", "")
            _tts_rules = reply_char.get("tts_rules", "")
            if _tts_lang:
                speech = await asyncio.to_thread(
                    _generate_tts_speech, reply_text, _tts_lang, _tts_rules, get_router())
            raw_tts_text = speech or _strip_markdown(reply_text)
            tts_text = raw_tts_text[:400] if raw_tts_text else ""
        if tts and tts_text:
            from core.system_logger import SystemLogger
            SystemLogger.log_system_event("TTS", f"開始合成（SSE），文字長度={len(tts_text)}")
            try:
                audio_bytes = await tts.synthesize(tts_text)
                if audio_bytes:
                    SystemLogger.log_system_event("TTS", f"合成成功，音頻大小={len(audio_bytes)} bytes")
                    tts_event = {
                        "type": "tts_audio",
                        "format": "mp3",
                        "data": base64.b64encode(audio_bytes).decode("ascii"),
                        "turn_index": 0,
                        "character_id": session.character_id,
                        "character_name": character_name,
                    }
                    yield f"data: {json.dumps(tts_event, ensure_ascii=False)}\n\n"
                else:
                    SystemLogger.log_error("TTS", "synthesize() 回傳 None（API 無音頻輸出）")
            except Exception as e:
                SystemLogger.log_error("TTS", f"合成失敗: {type(e).__name__}: {e}")

    return StreamingResponse(event_generator(), media_type="text/event-stream")


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
