"""REST / SSE 對話端點共用執行核心。"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import queue as sync_queue
from dataclasses import dataclass

from api.dependencies import get_router, get_storage, get_tts_client, require_db_writes_enabled
from api.models.requests import ChatSyncRequest
from api.models.responses import ChatSyncResponseDTO, ChatTurnDTO, RetrievalContextDTO
from api.routers.chat.pipeline import _run_memory_pipeline_bg
from api.routers.chat.roster import apply_roster_update
from api.session_manager import session_manager


@dataclass
class PreparedChatExecution:
    body: ChatSyncRequest
    current_user: dict
    external_context: dict | None
    external_context_summary: dict
    session: object
    runtime_session: object | None
    session_id: str
    orchestration_prompt: str
    transient_user_content: str
    memory_write_policy: str
    roster_event: dict | None
    user_prefs: dict
    orchestration_fn: object
    include_speech: bool
    session_ctx: dict
    extra_session_ctx: dict | None


async def prepare_chat_execution(body: ChatSyncRequest, current_user: dict) -> PreparedChatExecution:
    from api.routers import chat_rest

    require_db_writes_enabled()
    external_context, external_context_summary = chat_rest._resolve_external_context_payload(body)
    session = await chat_rest._resolve_session(
        body.session_id,
        current_user,
        body.character_ids,
        body.group_name,
        external_context,
    )
    sid = session.session_id
    orchestration_prompt = chat_rest._external_context_user_prompt(body.content, external_context)
    transient_user_content = chat_rest._transient_user_content_for_external_context(body, external_context)
    memory_write_policy = chat_rest._memory_write_policy_for_request(body, external_context)

    try:
        roster_event = await apply_roster_update(session, body.character_ids, group_name=body.group_name)
    except ValueError as exc:
        from fastapi import HTTPException
        raise HTTPException(400, detail=str(exc))
    if roster_event:
        session = await session_manager.get(sid) or session

    await chat_rest._persist_incoming_chat_message(sid, body, external_context, external_context_summary)
    runtime_session = await session_manager.get(sid)
    user_prefs = get_storage().load_prefs()
    orchestration_fn = chat_rest._select_orchestration(user_prefs)
    include_speech = body.include_speech and get_tts_client() is not None

    session_ctx = _build_session_ctx(session, current_user, external_context)
    if memory_write_policy == "transient":
        session_ctx["memory_write_policy"] = "transient"
    extra_session_ctx = _build_extra_session_ctx(external_context, memory_write_policy)

    return PreparedChatExecution(
        body=body,
        current_user=current_user,
        external_context=external_context,
        external_context_summary=external_context_summary,
        session=session,
        runtime_session=runtime_session,
        session_id=sid,
        orchestration_prompt=orchestration_prompt,
        transient_user_content=transient_user_content,
        memory_write_policy=memory_write_policy,
        roster_event=roster_event,
        user_prefs=user_prefs,
        orchestration_fn=orchestration_fn,
        include_speech=include_speech,
        session_ctx=session_ctx,
        extra_session_ctx=extra_session_ctx,
    )


async def execute_chat_turns(prepared: PreparedChatExecution) -> ChatSyncResponseDTO:
    if not prepared.runtime_session:
        return ChatSyncResponseDTO(
            session_id=prepared.session_id,
            reply="Session error",
            roster_event=prepared.roster_event,
        )
    from api.routers import chat_rest
    if chat_rest.is_group_session(prepared.runtime_session):
        return await _execute_group_chat_turns(prepared)
    return await _execute_single_chat_turn(prepared, include_speech=prepared.include_speech)


async def persist_single_turn_result(
    prepared: PreparedChatExecution,
    result,
) -> dict:
    from api.routers import chat_rest

    reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_data, \
        inner_thought, status_metrics, tone, speech, thinking_speech, cited_uids, \
        _tool_state_export = chat_rest._unpack_orchestration_result(result)
    if prepared.external_context_summary:
        retrieval_ctx["external_context"] = prepared.external_context_summary

    reply_char = chat_rest._get_session_character(prepared.session.character_id)
    character_name = reply_char.get("name") or prepared.session.character_id
    message_id = await session_manager.add_assistant_message(
        prepared.session_id,
        reply_text,
        retrieval_ctx,
        new_entities,
        persona_state={"internal_thought": inner_thought},
        character_name=character_name,
        character_id=prepared.session.character_id,
    )

    if topic_shifted:
        await session_manager.bridge(prepared.session_id)
        if pipeline_data:
            asyncio.create_task(_run_memory_pipeline_bg(prepared.session_id, pipeline_data))

    return {
        "message_id": message_id,
        "reply": reply_text,
        "extracted_entities": new_entities,
        "retrieval_context": retrieval_ctx,
        "cited_memory_uids": cited_uids,
        "internal_thought": inner_thought,
        "speech": speech,
        "thinking_speech": thinking_speech or None,
        "character_id": prepared.session.character_id,
        "character_name": character_name,
        "reply_char": reply_char,
    }


async def iter_chat_sse_events(prepared: PreparedChatExecution):
    from api.routers import chat_rest

    if prepared.roster_event:
        yield _sse(prepared.roster_event)
    if not prepared.runtime_session:
        yield _sse({"type": "error", "message": "Session error"})
        return
    if chat_rest.is_group_session(prepared.runtime_session):
        async for event in _iter_group_sse_events(prepared):
            yield event
        return
    async for event in _iter_single_sse_events(prepared):
        yield event


def _build_session_ctx(session, current_user: dict, external_context: dict | None) -> dict:
    from api.routers import chat_rest

    session_ctx = {
        "user_id": session.user_id,
        "character_id": session.character_id,
        "persona_face": session.persona_face,
        "session_id": session.session_id,
        "bot_id": session.bot_id,
        "channel": session.channel,
        "user_name": chat_rest._chat_user_display_name(current_user, external_context),
        "active_character_ids": list(session.active_character_ids or [session.character_id]),
        "session_mode": session.session_mode,
        "group_name": session.group_name,
        "expose_llm_trace": chat_rest._can_expose_llm_trace(current_user),
    }
    if external_context:
        session_ctx["external_chat_context"] = external_context
    return session_ctx


def _build_extra_session_ctx(external_context: dict | None, memory_write_policy: str) -> dict | None:
    extra_session_ctx = {}
    if external_context:
        extra_session_ctx["external_chat_context"] = external_context
    if memory_write_policy == "transient":
        extra_session_ctx["memory_write_policy"] = "transient"
    return extra_session_ctx or None


async def _execute_group_chat_turns(prepared: PreparedChatExecution) -> ChatSyncResponseDTO:
    from api.routers import chat_rest

    turns = await chat_rest.run_group_chat_loop(
        session=prepared.runtime_session,
        user_prompt=prepared.orchestration_prompt,
        user_prefs=prepared.user_prefs,
        orchestration_fn=prepared.orchestration_fn,
        user_name=chat_rest._chat_user_display_name(prepared.current_user, prepared.external_context),
        expose_llm_trace=chat_rest._can_expose_llm_trace(prepared.current_user),
        extra_session_ctx=prepared.extra_session_ctx,
        transient_user_content=prepared.transient_user_content,
        max_turns_override=chat_rest._external_context_group_turn_limit(
            prepared.runtime_session,
            prepared.external_context,
        ),
    )
    if not turns:
        return ChatSyncResponseDTO(
            session_id=prepared.session_id,
            reply="（無回應）",
            turns=[],
            roster_event=prepared.roster_event,
        )
    if prepared.include_speech:
        from core.chat_orchestrator.coordinator import _generate_tts_speech
        for turn in turns:
            if not turn.get("speech"):
                reply_char = chat_rest._get_session_character(turn["character_id"])
                turn["speech"] = _generate_tts_speech(
                    turn["reply"],
                    reply_char.get("tts_language", ""),
                    reply_char.get("tts_rules", ""),
                    get_router(),
                )
    final_turn = turns[-1]
    if prepared.external_context_summary:
        for turn in turns:
            turn.setdefault("retrieval_context", {})["external_context"] = prepared.external_context_summary
    joined_reply = "\n\n".join(
        f"{turn['character_name']}: {turn['reply']}" for turn in turns
    )
    return ChatSyncResponseDTO(
        session_id=prepared.session_id,
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
        roster_event=prepared.roster_event,
    )


async def _execute_single_chat_turn(
    prepared: PreparedChatExecution,
    *,
    include_speech: bool,
) -> ChatSyncResponseDTO:
    from api.routers import chat_rest

    result = await asyncio.to_thread(
        prepared.orchestration_fn,
        chat_rest._messages_for_orchestration(
            prepared.runtime_session.messages,
            prepared.body,
            prepared.external_context,
        ),
        list(prepared.runtime_session.last_entities),
        prepared.orchestration_prompt,
        prepared.user_prefs,
        session_ctx=prepared.session_ctx,
    )
    turn = await chat_rest.persist_single_turn_result(prepared, result)
    speech = turn["speech"]
    if include_speech and not speech:
        from core.chat_orchestrator.coordinator import _generate_tts_speech
        reply_char = turn["reply_char"]
        speech = _generate_tts_speech(
            turn["reply"],
            reply_char.get("tts_language", ""),
            reply_char.get("tts_rules", ""),
            get_router(),
        )
    return ChatSyncResponseDTO(
        session_id=prepared.session_id,
        message_id=turn["message_id"],
        reply=turn["reply"],
        extracted_entities=turn["extracted_entities"],
        retrieval_context=RetrievalContextDTO(**turn["retrieval_context"]),
        cited_memory_uids=turn["cited_memory_uids"],
        internal_thought=turn["internal_thought"],
        speech=speech,
        thinking_speech=turn["thinking_speech"],
        character_id=turn["character_id"],
        character_name=turn["character_name"],
        turns=[ChatTurnDTO(
            message_id=turn["message_id"],
            reply=turn["reply"],
            extracted_entities=turn["extracted_entities"],
            retrieval_context=RetrievalContextDTO(**turn["retrieval_context"]),
            cited_memory_uids=turn["cited_memory_uids"],
            internal_thought=turn["internal_thought"],
            speech=speech,
            thinking_speech=turn["thinking_speech"],
            character_id=turn["character_id"],
            character_name=turn["character_name"],
            turn_index=0,
            is_final=True,
        )],
        roster_event=prepared.roster_event,
    )


async def _iter_group_sse_events(prepared: PreparedChatExecution):
    from api.routers import chat_rest

    event_q = sync_queue.Queue()

    def on_event(data: dict):
        event_q.put(data)

    def on_turn(turn: dict):
        event_q.put({
            "type": "result",
            "session_id": prepared.session_id,
            **turn,
        })

    group_task = asyncio.create_task(chat_rest.run_group_chat_loop(
        session=prepared.runtime_session,
        user_prompt=prepared.orchestration_prompt,
        user_prefs=prepared.user_prefs,
        orchestration_fn=prepared.orchestration_fn,
        on_event=on_event,
        on_turn=on_turn,
        user_name=chat_rest._chat_user_display_name(prepared.current_user, prepared.external_context),
        expose_llm_trace=chat_rest._can_expose_llm_trace(prepared.current_user),
        extra_session_ctx=prepared.extra_session_ctx,
        transient_user_content=prepared.transient_user_content,
        max_turns_override=chat_rest._external_context_group_turn_limit(
            prepared.runtime_session,
            prepared.external_context,
        ),
    ))

    try:
        while not group_task.done():
            try:
                yield _sse(event_q.get_nowait())
            except sync_queue.Empty:
                await asyncio.sleep(0.1)

        while not event_q.empty():
            yield _sse(event_q.get_nowait())

        try:
            turns = group_task.result()
        except Exception as exc:
            yield _sse({"type": "error", "message": str(exc)})
            return

        yield _sse({"type": "group_done", "session_id": prepared.session_id, "turn_count": len(turns)})
        async for event in _iter_group_tts_events(prepared, turns):
            yield event
    finally:
        if not group_task.done():
            group_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await group_task


async def _iter_single_sse_events(prepared: PreparedChatExecution):
    from api.routers import chat_rest

    event_q = sync_queue.Queue()

    def on_event(data: dict):
        event_q.put(data)

    orch_task = asyncio.create_task(asyncio.to_thread(
        prepared.orchestration_fn,
        chat_rest._messages_for_orchestration(
            prepared.runtime_session.messages,
            prepared.body,
            prepared.external_context,
        ),
        list(prepared.runtime_session.last_entities),
        prepared.orchestration_prompt,
        prepared.user_prefs,
        on_event=on_event,
        session_ctx=prepared.session_ctx,
    ))

    try:
        while not orch_task.done():
            try:
                yield _sse(event_q.get_nowait())
            except sync_queue.Empty:
                await asyncio.sleep(0.1)

        while not event_q.empty():
            yield _sse(event_q.get_nowait())

        try:
            result = orch_task.result()
        except Exception as exc:
            yield _sse({"type": "error", "message": str(exc)})
            return

        try:
            turn = await chat_rest.persist_single_turn_result(prepared, result)
            final = {
                "type": "result",
                "session_id": prepared.session_id,
                "message_id": turn["message_id"],
                "reply": turn["reply"],
                "extracted_entities": turn["extracted_entities"],
                "retrieval_context": turn["retrieval_context"],
                "cited_memory_uids": turn["cited_memory_uids"],
                "internal_thought": turn["internal_thought"],
                "thinking_speech": turn["thinking_speech"],
                "character_id": turn["character_id"],
                "character_name": turn["character_name"],
                "turn_index": 0,
                "is_final": True,
            }
        except Exception as exc:
            yield _sse({"type": "error", "message": str(exc)})
            return
        yield _sse(final)
        async for event in _iter_single_tts_events(prepared, turn):
            yield event
    finally:
        if not orch_task.done():
            orch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await orch_task


async def _iter_group_tts_events(prepared: PreparedChatExecution, turns: list[dict]):
    from api.routers import chat_rest

    tts = get_tts_client() if prepared.body.include_speech else None
    if not tts:
        return
    from core.chat_orchestrator.coordinator import _generate_tts_speech
    for turn in turns:
        reply_char = chat_rest._get_session_character(turn["character_id"])
        speech = turn.get("speech")
        tts_lang = reply_char.get("tts_language", "")
        tts_rules = reply_char.get("tts_rules", "")
        if tts_lang and not speech:
            speech = await asyncio.to_thread(
                _generate_tts_speech,
                turn["reply"],
                tts_lang,
                tts_rules,
                get_router(),
            )
        raw_tts_text = speech or chat_rest._strip_markdown(turn["reply"])
        tts_text = raw_tts_text[:400] if raw_tts_text else ""
        if not tts_text:
            continue
        try:
            audio_bytes = await tts.synthesize(tts_text)
            if audio_bytes:
                yield _sse({
                    "type": "tts_audio",
                    "format": "mp3",
                    "data": base64.b64encode(audio_bytes).decode("ascii"),
                    "turn_index": turn["turn_index"],
                    "character_id": turn["character_id"],
                    "character_name": turn["character_name"],
                })
        except Exception:
            pass


async def _iter_single_tts_events(prepared: PreparedChatExecution, turn: dict):
    from api.routers import chat_rest

    tts = get_tts_client() if prepared.body.include_speech else None
    if not tts:
        return
    from core.chat_orchestrator.coordinator import _generate_tts_speech
    reply_char = turn["reply_char"]
    tts_lang = reply_char.get("tts_language", "")
    tts_rules = reply_char.get("tts_rules", "")
    speech = turn["speech"]
    if tts_lang:
        speech = await asyncio.to_thread(
            _generate_tts_speech,
            turn["reply"],
            tts_lang,
            tts_rules,
            get_router(),
        )
    raw_tts_text = speech or chat_rest._strip_markdown(turn["reply"])
    tts_text = raw_tts_text[:400] if raw_tts_text else ""
    if not tts_text:
        return
    from core.system_logger import SystemLogger
    SystemLogger.log_system_event("TTS", f"開始合成（SSE），文字長度={len(tts_text)}")
    try:
        audio_bytes = await tts.synthesize(tts_text)
        if audio_bytes:
            SystemLogger.log_system_event("TTS", f"合成成功，音頻大小={len(audio_bytes)} bytes")
            yield _sse({
                "type": "tts_audio",
                "format": "mp3",
                "data": base64.b64encode(audio_bytes).decode("ascii"),
                "turn_index": 0,
                "character_id": turn["character_id"],
                "character_name": turn["character_name"],
            })
        else:
            SystemLogger.log_error("TTS", "synthesize() 回傳 None（API 無音頻輸出）")
    except Exception as exc:
        SystemLogger.log_error("TTS", f"合成失敗: {type(exc).__name__}: {exc}")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
