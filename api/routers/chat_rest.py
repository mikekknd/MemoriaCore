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

from api.dependencies import get_current_user, get_storage, get_tts_client, get_character_manager, get_router
from api.session_manager import session_manager
from api.models.requests import ChatSyncRequest
from api.models.responses import ChatSyncResponseDTO, RetrievalContextDTO
from api.routers.chat.orchestration import _select_orchestration, _unpack_orchestration_result
from api.routers.chat.pipeline import _run_memory_pipeline_bg
from tools.minimax_image import generated_image_path


router = APIRouter(prefix="/chat", tags=["chat"])


def _get_session_character(character_id: str) -> dict:
    char_mgr = get_character_manager()
    char = char_mgr.get_character(character_id)
    if not char:
        from core.system_logger import SystemLogger
        SystemLogger.log_error("character_missing", f"missing_character_id={character_id}; fallback=default")
        char = char_mgr.get_active_character("default")
    return char or {}


# ════════════════════════════════════════════════════════════
# SECTION: 共用 — Session 取得/還原/建立
# ════════════════════════════════════════════════════════════

async def _resolve_session(session_id: str | None, current_user: dict):
    """取得 session：優先從記憶體取，其次從 DB 還原，最後才建新 session。"""
    user_id = str(current_user["id"])
    session = None
    if session_id:
        session = await session_manager.get(session_id)
        if session is not None and session.user_id != user_id:
            raise HTTPException(403, detail="Session owner mismatch")
        if session is None:
            try:
                session = await session_manager.restore_from_db(session_id, user_id=user_id)
            except PermissionError:
                raise HTTPException(403, detail="Session owner mismatch")
    if session is None:
        prefs = get_storage().load_prefs()
        channel_class = "private" if current_user.get("role") == "admin" else "public"
        persona_face = "private" if current_user.get("role") == "admin" else "public"
        session = await session_manager.create(
            channel="dashboard",
            channel_uid=user_id,
            user_id=user_id,
            character_id=prefs.get("active_character_id", "default"),
            channel_class=channel_class,
            persona_face=persona_face,
        )
    return session


# ════════════════════════════════════════════════════════════
# SECTION: 同步 REST 端點 (/chat/sync)
# ════════════════════════════════════════════════════════════

@router.post("/sync", response_model=ChatSyncResponseDTO)
async def chat_sync(body: ChatSyncRequest, current_user: dict = Depends(get_current_user)):
    session = await _resolve_session(body.session_id, current_user)
    sid = session.session_id

    await session_manager.add_user_message(sid, body.content)
    s = await session_manager.get(sid)
    if not s:
        return ChatSyncResponseDTO(reply="Session error")

    user_prefs = get_storage().load_prefs()
    orchestration_fn = _select_orchestration(user_prefs)

    session_ctx = {
        "user_id": session.user_id,
        "character_id": session.character_id,
        "persona_face": session.persona_face,
        "session_id": sid,
        "bot_id": session.bot_id,
        "channel": session.channel,
    }

    result = await asyncio.to_thread(
        orchestration_fn,
        list(s.messages), list(s.last_entities), body.content, user_prefs,
        session_ctx=session_ctx,
    )
    reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_data, \
        inner_thought, status_metrics, tone, speech, thinking_speech, cited_uids = \
        _unpack_orchestration_result(result)

    saved_reply_text = reply_text
    if cited_uids:
        refs_str = " ".join([f"[Ref: {u}]" for u in cited_uids])
        saved_reply_text = f"{reply_text} {refs_str}"
    reply_char = _get_session_character(session.character_id)
    character_name = reply_char.get("name") or session.character_id
    await session_manager.add_assistant_message(
        sid, saved_reply_text, retrieval_ctx, new_entities,
        persona_state={"internal_thought": inner_thought},
        character_name=character_name,
    )

    if topic_shifted:
        await session_manager.bridge(sid)
        if pipeline_data:
            asyncio.create_task(_run_memory_pipeline_bg(sid, pipeline_data))

    # /sync 為完整請求-回應週期，翻譯同步執行
    if not speech:
        from core.chat_orchestrator.coordinator import _generate_tts_speech
        speech = _generate_tts_speech(
            reply_text,
            reply_char.get("tts_language", ""),
            reply_char.get("tts_rules", ""),
            get_router(),
        )

    return ChatSyncResponseDTO(
        reply=reply_text,
        extracted_entities=new_entities,
        retrieval_context=RetrievalContextDTO(**retrieval_ctx),
        cited_memory_uids=cited_uids,
        internal_thought=inner_thought,
        speech=speech,
        thinking_speech=thinking_speech or None,
        character_name=character_name,
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
    # 優先從記憶體取得 session；找不到時先嘗試從 DB 還原（後端重啟後記憶體清空的情況）；
    # 都沒有才建新 session（channel 統一用 streamlit，確保能出現在 UI session 列表）
    session = await _resolve_session(body.session_id, current_user)
    sid = session.session_id

    await session_manager.add_user_message(sid, body.content)
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
    }

    def on_event(data: dict):
        event_q.put(data)

    async def event_generator():
        orch_task = asyncio.create_task(asyncio.to_thread(
            orchestration_fn,
            list(s.messages), list(s.last_entities), body.content, user_prefs,
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
            inner_thought, status_metrics, tone, speech, thinking_speech, cited_uids = \
            _unpack_orchestration_result(result)

        # 寫入 session 及背景任務（與 /sync 相同）
        saved_reply_text = reply_text
        if cited_uids:
            refs_str = " ".join([f"[Ref: {u}]" for u in cited_uids])
            saved_reply_text = f"{reply_text} {refs_str}"
        reply_char = _get_session_character(session.character_id)
        character_name = reply_char.get("name") or session.character_id
        await session_manager.add_assistant_message(
            sid, saved_reply_text, retrieval_ctx, new_entities,
            persona_state={"internal_thought": inner_thought},
            character_name=character_name,
        )

        if topic_shifted:
            await session_manager.bridge(sid)
            if pipeline_data:
                asyncio.create_task(_run_memory_pipeline_bg(sid, pipeline_data))

        # 送出最終結果（含實際使用的 session_id，讓 UI 同步更新）
        final = {
            "type": "result",
            "session_id": sid,
            "reply": reply_text,
            "extracted_entities": new_entities,
            "retrieval_context": retrieval_ctx,
            "cited_memory_uids": cited_uids,
            "internal_thought": inner_thought,
            "thinking_speech": thinking_speech or None,
            "character_name": character_name,
        }
        yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"

        # TTS 合成（若啟用）：result 事件已送出，翻譯 + 合成在此背景執行，不阻塞文字顯示
        tts = get_tts_client()
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
                    }
                    yield f"data: {json.dumps(tts_event)}\n\n"
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
