"""
Telegram Bot 多實例整合模組。

每個 bot config 對應一組 Bot / Dispatcher / polling task，與 FastAPI 共用核心單例。
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile

from core.bot_registry import BotRegistry
from tools.minimax_image import generated_image_path

logger = logging.getLogger("telegram_bot")


@dataclass
class TelegramRuntime:
    bot_id: str
    platform: str = "telegram"
    bot: Bot | None = None
    dispatcher: Dispatcher | None = None
    polling_task: asyncio.Task | None = None
    status: str = "disabled"
    last_error: str | None = None
    token_fingerprint: str = ""
    character_id: str = "default"


class TelegramSessionMap:
    """管理 (bot_id, Telegram user_id) 到 API session_id 的映射。"""

    def __init__(self):
        self._map: dict[tuple[str, int], str] = {}

    async def get_or_create_session(self, bot_id: str, user_id: int, character_id: str) -> str:
        from api.dependencies import get_storage
        from api.session_manager import session_manager

        key = (bot_id, user_id)
        sid = self._map.get(key)
        if sid:
            s = await session_manager.get(sid)
            if s:
                return sid
            del self._map[key]

        # 觸發 legacy prefs 讀取，確保 StorageManager 已初始化；角色由 bot config 指定。
        get_storage().load_prefs()
        session = await session_manager.create(
            channel="telegram",
            channel_uid=str(user_id),
            user_id=str(user_id),
            character_id=character_id,
            bot_id=bot_id,
        )
        self._map[key] = session.session_id
        return session.session_id

    async def clear_session(self, bot_id: str, user_id: int) -> bool:
        from api.session_manager import session_manager

        key = (bot_id, user_id)
        sid = self._map.pop(key, None)
        if sid:
            await session_manager.delete(sid)
            return True
        return False

    def get_session_id(self, bot_id: str, user_id: int) -> str | None:
        return self._map.get((bot_id, user_id))

    def clear_bot(self, bot_id: str) -> None:
        for key in [key for key in self._map if key[0] == bot_id]:
            del self._map[key]


# ══════════════════════════════════════════════════════════
# 訊息分割與圖片處理
# ══════════════════════════════════════════════════════════
_IMAGE_MARKDOWN_RE = re.compile(r"!\[.*?\]\(/api/v1/chat/generated-images/([^/]+)/([^/]+)\.jpeg\)")

def _split_message(text: str, max_len: int = 4000) -> list[str]:
    """將長訊息分割為多段，優先在換行或句號處分割。"""
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break

        cut = text.rfind("\n", 0, max_len)
        if cut == -1 or cut < max_len // 2:
            for sep in ("。", ". ", "！", "？", "! ", "? "):
                cut = text.rfind(sep, 0, max_len)
                if cut != -1 and cut > max_len // 2:
                    cut += len(sep)
                    break
            else:
                cut = max_len

        chunks.append(text[:cut])
        text = text[cut:].lstrip()

    return chunks


async def _send_message_with_images(message: types.Message, reply_text: str, user_id: str):
    """將包含圖片的回覆拆分並依序發送"""
    matches = list(_IMAGE_MARKDOWN_RE.finditer(reply_text))
    
    if not matches:
        chunks = _split_message(reply_text)
        for chunk in chunks:
            await message.answer(chunk)
        return

    last_end = 0
    for match in matches:
        text_before = reply_text[last_end:match.start()].strip()
        if text_before:
            for chunk in _split_message(text_before):
                await message.answer(chunk)
        
        session_id = match.group(1)
        image_id = match.group(2)
        try:
            path = generated_image_path(user_id, session_id, image_id)
            if path.exists() and path.is_file():
                await message.answer_photo(photo=FSInputFile(path))
            else:
                await message.answer(match.group(0))
        except Exception as e:
            logger.error("Failed to send photo %s: %s", image_id, e)
            await message.answer(match.group(0))
            
        last_end = match.end()
        
    text_after = reply_text[last_end:].strip()
    if text_after:
        for chunk in _split_message(text_after):
            await message.answer(chunk)


class TelegramBotManager:
    """管理多個 Telegram bot polling runtime。"""

    def __init__(self, registry: BotRegistry):
        self.registry = registry
        self._runtimes: dict[str, TelegramRuntime] = {}
        self._session_map = TelegramSessionMap()
        self._lock = asyncio.Lock()

    def get_status(self, bot_id: str, platform: str = "telegram") -> dict[str, Any]:
        if platform != "telegram":
            return {
                "bot_id": bot_id,
                "platform": platform,
                "status": "unsupported",
                "running": False,
                "last_error": None,
            }
        runtime = self._runtimes.get(bot_id)
        if not runtime:
            return {
                "bot_id": bot_id,
                "platform": "telegram",
                "status": "disabled",
                "running": False,
                "last_error": None,
            }
        running = bool(runtime.polling_task and not runtime.polling_task.done() and runtime.bot)
        return {
            "bot_id": bot_id,
            "platform": "telegram",
            "status": runtime.status,
            "running": running,
            "last_error": runtime.last_error,
        }

    async def sync_from_registry(self) -> None:
        """依 bot_configs.json 同步 runtime 狀態。"""
        from api.dependencies import get_storage

        async with self._lock:
            prefs = get_storage().load_prefs()
            configs = self.registry.load_configs(prefs)
            desired_ids = {c["bot_id"] for c in configs if c.get("platform") == "telegram" and c.get("enabled")}

            for bot_id in list(self._runtimes):
                if bot_id not in desired_ids:
                    await self._stop_bot_locked(bot_id)

            for cfg in configs:
                if cfg.get("platform") != "telegram":
                    self._runtimes[cfg["bot_id"]] = TelegramRuntime(
                        bot_id=cfg["bot_id"],
                        platform=cfg.get("platform", "other"),
                        status="unsupported",
                        character_id=cfg.get("character_id", "default"),
                    )
                    continue
                if not cfg.get("enabled"):
                    continue
                fp = self._fingerprint(cfg.get("token", ""))
                runtime = self._runtimes.get(cfg["bot_id"])
                if (
                    runtime
                    and runtime.status == "running"
                    and runtime.token_fingerprint == fp
                    and runtime.character_id == cfg.get("character_id", "default")
                    and runtime.polling_task
                    and not runtime.polling_task.done()
                ):
                    continue
                await self._stop_bot_locked(cfg["bot_id"])
                await self._start_bot_locked(cfg)

    async def reload_bot(self, bot_id: str) -> None:
        from api.dependencies import get_storage

        async with self._lock:
            prefs = get_storage().load_prefs()
            cfg = self.registry.get_config(bot_id, prefs)
            await self._stop_bot_locked(bot_id)
            if cfg and cfg.get("enabled") and cfg.get("platform") == "telegram":
                await self._start_bot_locked(cfg)
            elif cfg and cfg.get("platform") != "telegram":
                self._runtimes[bot_id] = TelegramRuntime(
                    bot_id=bot_id,
                    platform=cfg.get("platform", "other"),
                    status="unsupported",
                    character_id=cfg.get("character_id", "default"),
                )

    async def stop_bot(self, bot_id: str) -> None:
        async with self._lock:
            await self._stop_bot_locked(bot_id)

    async def stop_all(self) -> None:
        async with self._lock:
            for bot_id in list(self._runtimes):
                await self._stop_bot_locked(bot_id)

    async def _start_bot_locked(self, config: dict[str, Any]) -> None:
        bot_id = config["bot_id"]
        token = config.get("token", "").strip()
        character_id = config.get("character_id", "default")
        runtime = TelegramRuntime(
            bot_id=bot_id,
            platform="telegram",
            status="starting",
            token_fingerprint=self._fingerprint(token),
            character_id=character_id,
        )
        self._runtimes[bot_id] = runtime
        if not token:
            runtime.status = "error"
            runtime.last_error = "missing token"
            return

        try:
            bot = Bot(token=token)
            dispatcher = Dispatcher()
            dispatcher.message.register(self._cmd_start(bot_id, character_id), CommandStart())
            dispatcher.message.register(self._cmd_clear(bot_id), Command("clear"))
            dispatcher.message.register(self._cmd_status(bot_id), Command("status"))
            dispatcher.message.register(self._handle_message(bot_id, character_id))
            await bot.delete_webhook(drop_pending_updates=False)

            async def _run_polling():
                try:
                    await dispatcher.start_polling(bot, handle_signals=False)
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    runtime.status = "error"
                    runtime.last_error = self._sanitize_error(exc, token)
                    logger.error("Telegram polling error for bot_id=%s: %s", bot_id, runtime.last_error)

            runtime.bot = bot
            runtime.dispatcher = dispatcher
            runtime.polling_task = asyncio.create_task(_run_polling())
            runtime.status = "running"
            runtime.last_error = None
            logger.info("Telegram Bot started: bot_id=%s character_id=%s", bot_id, character_id)
        except Exception as exc:
            runtime.status = "error"
            runtime.last_error = self._sanitize_error(exc, token)
            logger.error("Telegram bot startup failed: bot_id=%s error=%s", bot_id, runtime.last_error)
            if runtime.bot:
                await runtime.bot.session.close()

    async def _stop_bot_locked(self, bot_id: str) -> None:
        runtime = self._runtimes.get(bot_id)
        if not runtime:
            return
        if runtime.dispatcher:
            try:
                await runtime.dispatcher.stop_polling()
            except Exception:
                pass
        if runtime.polling_task and not runtime.polling_task.done():
            runtime.polling_task.cancel()
            try:
                await runtime.polling_task
            except asyncio.CancelledError:
                pass
        if runtime.bot:
            await runtime.bot.session.close()
        self._session_map.clear_bot(bot_id)
        runtime.bot = None
        runtime.dispatcher = None
        runtime.polling_task = None
        runtime.status = "disabled"
        logger.info("Telegram Bot stopped: bot_id=%s", bot_id)

    def _cmd_start(self, bot_id: str, character_id: str):
        async def handler(message: types.Message):
            user_id = self._message_user_id(message)
            if user_id is None:
                return
            await self._session_map.get_or_create_session(bot_id, user_id, character_id)
            await message.answer(
                "你好！我是你的 AI 助手。\n"
                "直接發送訊息即可開始對話。\n\n"
                "可用指令：\n"
                "/clear — 清空對話歷史\n"
                "/status — 查看 Session 狀態"
            )
        return handler

    def _cmd_clear(self, bot_id: str):
        async def handler(message: types.Message):
            user_id = self._message_user_id(message)
            if user_id is None:
                return
            cleared = await self._session_map.clear_session(bot_id, user_id)
            if cleared:
                await message.answer("對話歷史已清空！發送訊息開始新對話。")
            else:
                await message.answer("目前沒有活躍的對話 Session。")
        return handler

    def _cmd_status(self, bot_id: str):
        async def handler(message: types.Message):
            from api.dependencies import get_memory_sys
            from api.session_manager import session_manager

            user_id = self._message_user_id(message)
            if user_id is None:
                return
            sid = self._session_map.get_session_id(bot_id, user_id)
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
                f"Bot ID: {bot_id}\n"
                f"角色 ID: {s.character_id}\n"
                f"Session ID: {sid[:8]}...\n"
                f"對話訊息數: {len(s.messages)}\n"
                f"當前實體標籤: {', '.join(s.last_entities) if s.last_entities else '(無)'}\n"
                f"記憶區塊總數: {block_count}\n"
                f"建立時間: {s.created_at.strftime('%Y-%m-%d %H:%M')}\n"
                f"最後活動: {s.last_active.strftime('%H:%M:%S')}"
            )
        return handler

    def _handle_message(self, bot_id: str, character_id: str):
        async def handler(message: types.Message):
            user_text = ""
            if message.text:
                user_text = message.text.strip()
            elif message.photo:
                await message.reply("（提示：我目前還無法看見圖片內容，但我會回覆您的文字）")
                if message.caption:
                    user_text = message.caption.strip()
            
            if not user_text:
                return

            user_id = self._message_user_id(message)
            if user_id is None:
                return

            from api.dependencies import get_storage
            from api.session_manager import session_manager
            from api.routers.chat.orchestration import _select_orchestration, _unpack_orchestration_result

            sid = await self._session_map.get_or_create_session(bot_id, user_id, character_id)
            await session_manager.add_user_message(sid, user_text)
            s = await session_manager.get(sid)
            if not s:
                await message.answer("Session 錯誤，請重試。")
                return

            await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
            user_prefs = get_storage().load_prefs()
            orchestration_fn = _select_orchestration(user_prefs)
            status_cb = self._telegram_event_callback(message)
            session_ctx = {
                "user_id": s.user_id,
                "character_id": s.character_id,
                "persona_face": s.persona_face,
                "session_id": sid,
                "bot_id": s.bot_id,
                "channel": s.channel,
            }

            try:
                result = await asyncio.to_thread(
                    orchestration_fn,
                    list(s.messages), list(s.last_entities), user_text, user_prefs,
                    on_event=status_cb,
                    session_ctx=session_ctx,
                )
                reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_data, \
                    _inner_thought, _status_metrics, _tone, _speech, _thinking_speech, cited_uids = \
                    _unpack_orchestration_result(result)
            except Exception as exc:
                logger.error("Chat orchestration failed: %s", exc, exc_info=True)
                await message.answer(f"處理失敗: {exc}")
                return

            saved_reply_text = reply_text
            if cited_uids:
                saved_reply_text = f"{reply_text} " + " ".join([f"[Ref: {u}]" for u in cited_uids])
            await session_manager.add_assistant_message(sid, saved_reply_text, retrieval_ctx, new_entities)

            if topic_shifted:
                await session_manager.bridge(sid)
                if pipeline_data:
                    from api.routers.chat.pipeline import _run_memory_pipeline_bg
                    asyncio.create_task(_run_memory_pipeline_bg(sid, pipeline_data))

            await _send_message_with_images(message, reply_text, s.user_id)
        return handler

    @staticmethod
    def _telegram_event_callback(message: types.Message):
        status_msg = None
        loop = asyncio.get_running_loop()

        def callback(data: dict):
            nonlocal status_msg
            action = data.get("action", "")
            text = data.get("message", "")
            if action == "calling" and text:
                async def _send_or_edit():
                    nonlocal status_msg
                    try:
                        if status_msg is None:
                            status_msg = await message.answer(f"🔍 {text}")
                        else:
                            await status_msg.edit_text(f"🔍 {text}")
                    except Exception:
                        pass
                asyncio.run_coroutine_threadsafe(_send_or_edit(), loop)
            elif action == "complete" and text:
                async def _edit_complete():
                    try:
                        if status_msg:
                            await status_msg.edit_text(f"✅ {text}")
                    except Exception:
                        pass
                asyncio.run_coroutine_threadsafe(_edit_complete(), loop)

        return callback

    @staticmethod
    def _message_user_id(message: types.Message) -> int | None:
        if not message.from_user:
            return None
        return int(message.from_user.id)

    @staticmethod
    def _fingerprint(token: str) -> str:
        if not token:
            return ""
        return f"{len(token)}:{token[:4]}:{token[-4:]}"

    @staticmethod
    def _sanitize_error(exc: Exception, token: str) -> str:
        message = str(exc)
        if token:
            message = message.replace(token, "[redacted-token]")
        return message
