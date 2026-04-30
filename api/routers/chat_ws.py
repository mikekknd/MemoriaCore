"""WebSocket 對話端點：/chat/stream。

REST + SSE 端點見 chat_rest.py；
共用編排邏輯見 api/routers/chat/。
"""
import asyncio
import base64
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.auth_utils import AUTH_COOKIE_NAME, decode_jwt
from api.dependencies import get_storage, get_tts_client
from api.session_manager import session_manager
from api.routers.chat.ws_manager import ws_manager
from api.routers.chat.orchestration import (
    _run_chat_orchestration,
    _select_orchestration,
    _unpack_orchestration_result,
)
from api.routers.chat.pipeline import _run_memory_pipeline_bg
from api.routers.chat.group_loop import is_group_session, run_group_chat_loop
from api.routers.chat.timer import StepTimer


router = APIRouter(prefix="/chat", tags=["chat"])


def _user_display_name(current_user: dict) -> str:
    return (
        current_user.get("nickname")
        or current_user.get("username")
        or str(current_user.get("id", ""))
    )


def _get_session_character(character_id: str) -> dict:
    from api.dependencies import get_character_manager
    char_mgr = get_character_manager()
    char = char_mgr.get_character(character_id)
    if not char:
        from core.system_logger import SystemLogger
        SystemLogger.log_error("character_missing", f"missing_character_id={character_id}; fallback=default")
        char = char_mgr.get_active_character("default")
    return char or {}


async def _authenticate_ws(ws: WebSocket) -> dict | None:
    token = ws.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        await ws.close(code=1008)
        return None
    try:
        payload = decode_jwt(token)
        storage = get_storage()
        user = storage.get_user_by_id(payload.get("sub"))
        if not user or int(user.get("token_version", 0)) != int(payload.get("ver", -1)):
            await ws.close(code=1008)
            return None
        return user
    except Exception:
        await ws.close(code=1008)
        return None


# ════════════════════════════════════════════════════════════
# SECTION: WebSocket 端點
# ════════════════════════════════════════════════════════════

@router.websocket("/stream")
async def chat_stream(ws: WebSocket, session_id: str | None = None):
    current_user = await _authenticate_ws(ws)
    if not current_user:
        return
    user_id = str(current_user["id"])
    user_name = _user_display_name(current_user)
    channel_class = "private" if current_user.get("role") == "admin" else "public"
    persona_face = "private" if current_user.get("role") == "admin" else "public"
    user_prefs = get_storage().load_prefs()
    session = None
    if session_id:
        session = await session_manager.get(session_id)
        if session is not None and session.user_id != user_id:
            await ws.close(code=1008)
            return
        if session is None:
            try:
                session = await session_manager.restore_from_db(session_id, user_id=user_id)
            except PermissionError:
                await ws.close(code=1008)
                return
    if session is None:
        session = await session_manager.create(
            channel="websocket",
            channel_uid=user_id,
            user_id=user_id,
            character_id=user_prefs.get("active_character_id", "default"),
            channel_class=channel_class,
            persona_face=persona_face,
        )
    sid = session.session_id
    await ws_manager.connect(sid, ws)

    # 發送 session 初始化訊息
    await ws.send_json({"type": "session_init", "session_id": sid})

    try:
        while True:
            raw = await ws.receive_text()
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "code": "INVALID_JSON", "message": "Invalid JSON frame"})
                continue

            frame_type = frame.get("type", "")

            if frame_type == "ping":
                await ws.send_json({"type": "pong"})
                continue

            if frame_type == "cancel":
                await ws_manager.cancel_active_task(sid)
                await ws.send_json({"type": "system_event", "action": "cancelled"})
                continue

            if frame_type == "clear_context":
                await ws_manager.cancel_active_task(sid)
                # 保留原 session 的隔離身份，重建乾淨的對話歷史
                old_channel = session.channel
                old_channel_uid = session.channel_uid
                old_bot_id = session.bot_id
                old_user_id = session.user_id
                old_character_id = session.character_id
                old_character_ids = list(session.active_character_ids or [session.character_id])
                old_session_mode = session.session_mode
                old_group_name = session.group_name
                old_persona_face = session.persona_face
                old_channel_class = session.channel_class
                await session_manager.delete(sid)
                session = await session_manager.create(
                    channel=old_channel,
                    channel_uid=old_channel_uid,
                    bot_id=old_bot_id,
                    user_id=old_user_id,
                    character_id=old_character_id,
                    character_ids=old_character_ids,
                    session_mode=old_session_mode,
                    group_name=old_group_name,
                    channel_class=old_channel_class,
                    persona_face=old_persona_face,
                )
                sid = session.session_id
                ws_manager._connections[sid] = ws
                await ws.send_json({"type": "session_init", "session_id": sid})
                continue

            if frame_type != "chat_message":
                await ws.send_json({"type": "error", "code": "UNKNOWN_FRAME", "message": f"Unknown frame type: {frame_type}"})
                continue

            content = frame.get("content", "").strip()
            if not content:
                await ws.send_json({"type": "error", "code": "EMPTY_MESSAGE", "message": "Empty message"})
                continue

            # 打斷機制：取消前一個活躍任務
            await ws_manager.cancel_active_task(sid)

            # 加入使用者訊息
            await session_manager.add_user_message(sid, content)
            s = await session_manager.get(sid)
            if not s:
                await ws.send_json({"type": "error", "code": "SESSION_LOST", "message": "Session lost"})
                continue

            user_prefs = get_storage().load_prefs()

            session_ctx = {
                "user_id": s.user_id,
                "character_id": s.character_id,
                "persona_face": s.persona_face,
                "session_id": sid,
                "bot_id": s.bot_id,
                "channel": s.channel,
                "user_name": user_name,
                "active_character_ids": list(s.active_character_ids or [s.character_id]),
                "session_mode": s.session_mode,
                "group_name": s.group_name,
            }

            # 建立即時事件推送 callback（從工作執行緒安全呼叫 async WS send）
            loop = asyncio.get_running_loop()

            def _ws_event_cb(data: dict):
                asyncio.run_coroutine_threadsafe(ws.send_json(data), loop)

            # 選擇對話編排函式（雙層 or 單層）
            orchestration_fn = _select_orchestration(user_prefs)

            if is_group_session(s):
                async def _send_group_turn(turn: dict):
                    await ws.send_json({"type": "retrieval_context", "data": turn["retrieval_context"],
                                        "turn_index": turn["turn_index"],
                                        "character_id": turn["character_id"],
                                        "character_name": turn["character_name"]})
                    await ws.send_json({"type": "token", "content": turn["reply"],
                                        "turn_index": turn["turn_index"],
                                        "character_id": turn["character_id"],
                                        "character_name": turn["character_name"]})
                    await ws.send_json({
                        "type": "chat_done",
                        "reply": turn["reply"],
                        "extracted_entities": turn["extracted_entities"],
                        "internal_thought": turn["internal_thought"],
                        "turn_index": turn["turn_index"],
                        "is_final": turn["is_final"],
                        "character_id": turn["character_id"],
                        "character_name": turn["character_name"],
                    })

                task = asyncio.create_task(run_group_chat_loop(
                    session=s,
                    user_prompt=content,
                    user_prefs=user_prefs,
                    orchestration_fn=orchestration_fn,
                    on_event=_ws_event_cb,
                    on_turn=_send_group_turn,
                    user_name=user_name,
                ))
                ws_manager.set_active_task(sid, task)
                try:
                    turns = await task
                except asyncio.CancelledError:
                    continue
                finally:
                    ws_manager.clear_active_task(sid)

                await ws.send_json({"type": "group_done", "session_id": sid, "turn_count": len(turns)})

                tts = get_tts_client()
                if tts:
                    from api.dependencies import get_router
                    for turn in turns:
                        reply_char = _get_session_character(turn["character_id"])
                        asyncio.create_task(_translate_and_tts_send(
                            ws, tts, turn["reply"],
                            reply_char.get("tts_language", ""),
                            reply_char.get("tts_rules", ""),
                            get_router(),
                            turn_index=turn["turn_index"],
                            character_id=turn["character_id"],
                            character_name=turn["character_name"],
                        ))
                continue

            # 在執行緒池中跑關鍵路徑，包裝為 Task 以支援取消
            task = asyncio.create_task(asyncio.to_thread(
                orchestration_fn,
                list(s.messages), list(s.last_entities), content, user_prefs,
                on_event=_ws_event_cb, session_ctx=session_ctx,
            ))
            ws_manager.set_active_task(sid, task)

            try:
                result = await task
            except asyncio.CancelledError:
                # 任務被取消（使用者打斷），跳過後續處理
                continue
            finally:
                ws_manager.clear_active_task(sid)

            reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_data, \
                inner_thought, status_metrics, tone, speech, thinking_speech, cited_uids, \
                _tool_state_export = _unpack_orchestration_result(result)

            # 如果話題偏移，通知客戶端並在背景啟動記憶管線
            if topic_shifted:
                await ws.send_json({"type": "system_event", "action": "topic_shift"})
                if pipeline_data:
                    asyncio.create_task(_run_memory_pipeline_bg(sid, pipeline_data))

            # 推送檢索上下文
            await ws.send_json({
                "type": "retrieval_context",
                "data": retrieval_ctx,
                "turn_index": 0,
                "character_id": s.character_id,
            })

            # 推送完整回覆（非串流模式，因為底層 LLM 目前不支援 async yield）
            await ws.send_json({
                "type": "token",
                "content": reply_text,
                "turn_index": 0,
                "character_id": s.character_id,
            })
            # 準備包含詳細狀態的 done payload
            done_payload = {
                "type": "chat_done",
                "reply": reply_text,
                "extracted_entities": new_entities,
                "internal_thought": inner_thought,
                "character_id": s.character_id,
                "turn_index": 0,
                "is_final": True,
            }
            reply_char = _get_session_character(s.character_id)
            character_name = reply_char.get("name") or s.character_id
            done_payload["character_name"] = character_name
            await ws.send_json(done_payload)

            # 寫入 assistant 回覆（cited_uids 隨 retrieval_ctx 進入 debug_info 持久化，
            # 不再拼進 content；上下文清洗由 dialogue_format 負責）
            await session_manager.add_assistant_message(
                sid, reply_text, retrieval_ctx, new_entities,
                persona_state={"internal_thought": inner_thought},
                character_name=character_name,
                character_id=s.character_id,
            )

            # 如果話題偏移，執行橋接
            if topic_shifted:
                await session_manager.bridge(sid)

            # TTS 合成（若啟用）：背景執行翻譯 + 合成，不阻塞文字回覆顯示
            tts = get_tts_client()
            if tts:
                from api.dependencies import get_router
                asyncio.create_task(_translate_and_tts_send(
                    ws, tts, reply_text,
                    reply_char.get("tts_language", ""),
                    reply_char.get("tts_rules", ""),
                    get_router(),
                    turn_index=0,
                    character_id=s.character_id,
                    character_name=character_name,
                ))

    except WebSocketDisconnect:
        ws_manager.disconnect(sid)
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "code": "INTERNAL", "message": str(e)})
        except Exception:
            pass
        ws_manager.disconnect(sid)


# ════════════════════════════════════════════════════════════
# SECTION: TTS 背景合成與推送
# ════════════════════════════════════════════════════════════

async def _translate_and_tts_send(
    ws: WebSocket, tts, reply_text: str,
    char_tts_lang: str, tts_rules: str, rtr,
    turn_index: int = 0,
    character_id: str | None = None,
    character_name: str | None = None,
) -> None:
    """背景翻譯 + TTS 合成：文字回覆已送出後才執行，不阻塞顯示。"""
    from api.routers.chat_rest import _strip_markdown
    from core.chat_orchestrator.coordinator import _generate_tts_speech

    if char_tts_lang:
        speech = await asyncio.to_thread(
            _generate_tts_speech, reply_text, char_tts_lang, tts_rules, rtr)
    else:
        speech = None
    raw_text = speech or _strip_markdown(reply_text)
    text = raw_text[:400]
    if text:
        await _tts_and_send(
            ws, tts, text,
            turn_index=turn_index,
            character_id=character_id,
            character_name=character_name,
        )


async def _tts_and_send(
    ws: WebSocket,
    tts,
    text: str,
    turn_index: int = 0,
    character_id: str | None = None,
    character_name: str | None = None,
) -> None:
    """
    背景合成 TTS 並以 tts_audio 事件推送給 client。

    Client 收到事件格式：
        {"type": "tts_audio", "format": "mp3", "data": "<base64 encoded bytes>"}

    Client 端（Unity/瀏覽器）解碼 base64 後即可直接播放。
    若合成失敗不影響對話流程（靜默失敗，error 會寫入 log）。
    """
    try:
        audio_bytes = await tts.synthesize(text)
        if audio_bytes:
            await ws.send_json({
                "type": "tts_audio",
                "format": "mp3",
                "data": base64.b64encode(audio_bytes).decode("ascii"),
                "turn_index": turn_index,
                "character_id": character_id,
                "character_name": character_name,
            })
    except Exception:
        pass  # 合成失敗不中斷對話，SystemLogger 已在 tts_client 內記錄
