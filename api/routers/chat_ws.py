"""WebSocket 對話端點：/chat/stream。

REST + SSE 端點見 chat_rest.py；
共用編排邏輯見 api/routers/chat/。
"""
import asyncio
import base64
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.dependencies import get_storage, get_tts_client
from api.session_manager import session_manager
from api.routers.chat.ws_manager import ws_manager
from api.routers.chat.orchestration import (
    _run_chat_orchestration,
    _select_orchestration,
    _unpack_orchestration_result,
)
from api.routers.chat.pipeline import _run_memory_pipeline_bg
from api.routers.chat.timer import StepTimer


router = APIRouter(prefix="/chat", tags=["chat"])


# ════════════════════════════════════════════════════════════
# SECTION: WebSocket 端點
# ════════════════════════════════════════════════════════════

@router.websocket("/stream")
async def chat_stream(ws: WebSocket, session_id: str | None = None):
    session = await session_manager.get_or_create(session_id, channel="websocket")
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
                await session_manager.delete(sid)
                session = await session_manager.create()
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

            # 建立即時事件推送 callback（從工作執行緒安全呼叫 async WS send）
            loop = asyncio.get_running_loop()

            def _ws_event_cb(data: dict):
                asyncio.run_coroutine_threadsafe(ws.send_json(data), loop)

            # 選擇對話編排函式（雙層 or 單層）
            orchestration_fn = _select_orchestration(user_prefs)

            # 在執行緒池中跑關鍵路徑，包裝為 Task 以支援取消
            task = asyncio.create_task(asyncio.to_thread(
                orchestration_fn,
                list(s.messages), list(s.last_entities), content, user_prefs,
                on_event=_ws_event_cb,
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
                inner_thought, status_metrics, tone, speech, thinking_speech, cited_uids = \
                _unpack_orchestration_result(result)

            # 如果話題偏移，通知客戶端並在背景啟動記憶管線
            if topic_shifted:
                await ws.send_json({"type": "system_event", "action": "topic_shift"})
                if pipeline_data:
                    asyncio.create_task(_run_memory_pipeline_bg(sid, *pipeline_data))

            # 推送檢索上下文
            await ws.send_json({"type": "retrieval_context", "data": retrieval_ctx})

            # 推送完整回覆（非串流模式，因為底層 LLM 目前不支援 async yield）
            await ws.send_json({"type": "token", "content": reply_text})
            # 準備包含詳細狀態的 done payload
            done_payload = {
                "type": "chat_done",
                "reply": reply_text,
                "extracted_entities": new_entities,
                "internal_thought": inner_thought,
            }
            await ws.send_json(done_payload)

            # 寫入 assistant 回覆（後端隱性掛載引用 UID）
            saved_reply_text = reply_text
            if cited_uids:
                refs_str = " ".join([f"[Ref: {u}]" for u in cited_uids])
                saved_reply_text = f"{reply_text} {refs_str}"
            await session_manager.add_assistant_message(
                sid, saved_reply_text, retrieval_ctx, new_entities,
                persona_state={"internal_thought": inner_thought},
            )

            # 如果話題偏移，執行橋接
            if topic_shifted:
                await session_manager.bridge(sid)

            # TTS 合成（若啟用）：背景執行翻譯 + 合成，不阻塞文字回覆顯示
            tts = get_tts_client()
            if tts:
                from api.dependencies import get_character_manager, get_router
                _char_mgr = get_character_manager()
                _active_char = _char_mgr.get_active_character(
                    user_prefs.get("active_character_id", "default"))
                asyncio.create_task(_translate_and_tts_send(
                    ws, tts, reply_text,
                    _active_char.get("tts_language", ""),
                    _active_char.get("tts_rules", ""),
                    get_router(),
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
        await _tts_and_send(ws, tts, text)


async def _tts_and_send(ws: WebSocket, tts, text: str) -> None:
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
            })
    except Exception:
        pass  # 合成失敗不中斷對話，SystemLogger 已在 tts_client 內記錄
