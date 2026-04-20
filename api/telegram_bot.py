"""
Telegram Bot 整合模組 — 與 FastAPI 共存於同一進程。
直接呼叫內部函式（非 HTTP），共享所有單例。
使用長輪詢模式，無需公網 IP / HTTPS / ngrok。
"""
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode, ChatAction

from api.dependencies import (
    get_memory_sys, get_storage, get_router,
)
from api.session_manager import session_manager
from api.routers.chat.orchestration import _run_chat_orchestration

logger = logging.getLogger("telegram_bot")

# ── Module-level state ────────────────────────────────────
_bot: Bot | None = None
_dp: Dispatcher | None = None
_polling_task: asyncio.Task | None = None


# ══════════════════════════════════════════════════════════
# Telegram user_id → API session_id 映射
# ══════════════════════════════════════════════════════════
class TelegramSessionMap:
    """管理 Telegram user_id 到 API session_id 的映射。"""

    def __init__(self):
        self._map: dict[int, str] = {}

    async def get_or_create_session(self, user_id: int) -> str:
        """取得或建立對應的 API session。若 session 已過期則自動重建。"""
        sid = self._map.get(user_id)
        if sid:
            s = await session_manager.get(sid)
            if s:
                return sid
            # session 過期，清除映射
            del self._map[user_id]

        # 建立新 session（帶入 channel 資訊）
        session = await session_manager.create(channel="telegram", channel_uid=str(user_id))
        self._map[user_id] = session.session_id
        return session.session_id

    async def clear_session(self, user_id: int) -> bool:
        """清除使用者的對話歷史，重新開始。"""
        sid = self._map.pop(user_id, None)
        if sid:
            await session_manager.delete(sid)
            return True
        return False

    def get_session_id(self, user_id: int) -> str | None:
        return self._map.get(user_id)


_session_map = TelegramSessionMap()


# ══════════════════════════════════════════════════════════
# 訊息分割（Telegram 單訊息限制 4096 字元）
# ══════════════════════════════════════════════════════════
def _split_message(text: str, max_len: int = 4000) -> list[str]:
    """將長訊息分割為多段，優先在換行或句號處分割。"""
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break

        # 嘗試在 max_len 範圍內找最後的換行
        cut = text.rfind("\n", 0, max_len)
        if cut == -1 or cut < max_len // 2:
            # 找不到合適換行，嘗試句號
            for sep in ("。", ". ", "！", "？", "! ", "? "):
                cut = text.rfind(sep, 0, max_len)
                if cut != -1 and cut > max_len // 2:
                    cut += len(sep)
                    break
            else:
                # 最後手段：硬切
                cut = max_len

        chunks.append(text[:cut])
        text = text[cut:].lstrip()

    return chunks


# ══════════════════════════════════════════════════════════
# 指令處理
# ══════════════════════════════════════════════════════════
async def _cmd_start(message: types.Message):
    """處理 /start 指令"""
    user_id = message.from_user.id
    await _session_map.get_or_create_session(user_id)
    await message.answer(
        "你好！我是你的 AI 助手。\n"
        "直接發送訊息即可開始對話。\n\n"
        "可用指令：\n"
        "/clear — 清空對話歷史\n"
        "/status — 查看 Session 狀態"
    )


async def _cmd_clear(message: types.Message):
    """處理 /clear 指令"""
    user_id = message.from_user.id
    cleared = await _session_map.clear_session(user_id)
    if cleared:
        await message.answer("對話歷史已清空！發送訊息開始新對話。")
    else:
        await message.answer("目前沒有活躍的對話 Session。")


async def _cmd_status(message: types.Message):
    """處理 /status 指令"""
    user_id = message.from_user.id
    sid = _session_map.get_session_id(user_id)
    if not sid:
        await message.answer("目前沒有活躍的 Session。發送任何訊息即可自動建立。")
        return

    s = await session_manager.get(sid)
    if not s:
        await message.answer("Session 已過期。發送任何訊息即可自動建立新 Session。")
        return

    ms = get_memory_sys()
    block_count = len(ms.memory_blocks) if ms.memory_blocks else 0

    await message.answer(
        f"Session ID: {sid[:8]}...\n"
        f"對話訊息數: {len(s.messages)}\n"
        f"當前實體標籤: {', '.join(s.last_entities) if s.last_entities else '(無)'}\n"
        f"記憶區塊總數: {block_count}\n"
        f"建立時間: {s.created_at.strftime('%Y-%m-%d %H:%M')}\n"
        f"最後活動: {s.last_active.strftime('%H:%M:%S')}"
    )


# ══════════════════════════════════════════════════════════
# 一般訊息處理（核心對話流程）
# ══════════════════════════════════════════════════════════
async def _handle_message(message: types.Message):
    """處理一般文字訊息 — 完整對話編排流程。"""
    if not message.text:
        return

    user_id = message.from_user.id
    user_text = message.text.strip()
    if not user_text:
        return

    # 取得或建立 session
    sid = await _session_map.get_or_create_session(user_id)

    # 加入使用者訊息
    await session_manager.add_user_message(sid, user_text)
    s = await session_manager.get(sid)
    if not s:
        await message.answer("Session 錯誤，請重試。")
        return

    # 發送 typing 指示器
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)

    user_prefs = get_storage().load_prefs()

    # 建立 Telegram 即時狀態通知 callback
    _status_msg = None
    _loop = asyncio.get_running_loop()

    def _tg_event_cb(data: dict):
        nonlocal _status_msg
        action = data.get("action", "")
        text = data.get("message", "")
        if action == "calling" and text:
            # 發送或更新搜尋中提示訊息
            async def _send_or_edit():
                nonlocal _status_msg
                try:
                    if _status_msg is None:
                        _status_msg = await message.answer(f"🔍 {text}")
                    else:
                        await _status_msg.edit_text(f"🔍 {text}")
                except Exception:
                    pass
            asyncio.run_coroutine_threadsafe(_send_or_edit(), _loop)
        elif action == "complete" and text:
            async def _edit_complete():
                nonlocal _status_msg
                try:
                    if _status_msg:
                        await _status_msg.edit_text(f"✅ {text}")
                except Exception:
                    pass
            asyncio.run_coroutine_threadsafe(_edit_complete(), _loop)

    try:
        # 在執行緒池中跑完整編排（與 chat_ws.py 共用同一函式）
        reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_events = \
            await asyncio.to_thread(
                _run_chat_orchestration,
                list(s.messages), list(s.last_entities), user_text, user_prefs,
                on_event=_tg_event_cb,
            )
    except Exception as e:
        logger.error("Chat orchestration failed: %s", e, exc_info=True)
        await message.answer(f"處理失敗: {e}")
        return

    # 寫入 assistant 回覆
    await session_manager.add_assistant_message(sid, reply_text, retrieval_ctx, new_entities)

    # 話題偏移時執行橋接
    if topic_shifted:
        await session_manager.bridge(sid)

    # 分割並回傳訊息
    chunks = _split_message(reply_text)
    for chunk in chunks:
        await message.answer(chunk)


# ══════════════════════════════════════════════════════════
# 生命週期管理
# ══════════════════════════════════════════════════════════
async def start_telegram_bot(token: str):
    """啟動 Telegram Bot（長輪詢模式）。在 FastAPI lifespan 中呼叫。"""
    global _bot, _dp, _polling_task

    if not token or not token.strip():
        logger.info("Telegram Bot token is empty, skipping bot startup.")
        return

    _bot = Bot(token=token.strip())
    _dp = Dispatcher()

    # 註冊處理器
    _dp.message.register(_cmd_start, CommandStart())
    _dp.message.register(_cmd_clear, Command("clear"))
    _dp.message.register(_cmd_status, Command("status"))
    _dp.message.register(_handle_message)  # 所有其他文字訊息

    # 啟動長輪詢（在背景 task 中，handle_signals=False 避免與 uvicorn 衝突）
    async def _run_polling():
        try:
            await _dp.start_polling(_bot, handle_signals=False)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Telegram polling error: %s", e, exc_info=True)

    _polling_task = asyncio.create_task(_run_polling())
    logger.info("Telegram Bot started (long-polling mode).")


async def stop_telegram_bot():
    """停止 Telegram Bot。在 FastAPI shutdown 時呼叫。"""
    global _bot, _dp, _polling_task

    if _dp:
        await _dp.stop_polling()
        _dp = None

    if _polling_task and not _polling_task.done():
        _polling_task.cancel()
        try:
            await _polling_task
        except asyncio.CancelledError:
            pass
        _polling_task = None

    if _bot:
        await _bot.session.close()
        _bot = None

    logger.info("Telegram Bot stopped.")
