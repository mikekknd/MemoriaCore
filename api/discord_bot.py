"""Discord Bot 多實例整合模組。"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

try:
    import discord
except ImportError:  # pragma: no cover - 套件未安裝時仍允許 API 匯入
    discord = None

from core.bot_registry import BotRegistry
from tools.minimax_image import generated_image_path

logger = logging.getLogger("discord_bot")

_IMAGE_MARKDOWN_RE = re.compile(r"!\[.*?\]\(/api/v1/chat/generated-images/([^/]+)/([^/]+)\.jpeg\)")
DISCORD_MESSAGE_SAFE_LIMIT = 1900


@dataclass
class DiscordRuntime:
    bot_id: str
    platform: str = "discord"
    client: Any | None = None
    task: asyncio.Task | None = None
    status: str = "disabled"
    last_error: str | None = None
    token_fingerprint: str = ""
    character_id: str = "default"


def _split_message(text: str, max_len: int = DISCORD_MESSAGE_SAFE_LIMIT) -> list[str]:
    """將 Discord 訊息切成安全長度，避免超過 2000 字元限制。"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            if text.strip():
                chunks.append(text.strip())
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

        chunk = text[:cut].strip()
        if chunk:
            chunks.append(chunk)
        text = text[cut:].lstrip()
    return chunks


def _message_channel(message: Any) -> str:
    return "discord_private" if getattr(message, "guild", None) is None else "discord_public"


def _message_channel_uid(message: Any) -> str:
    author_id = str(getattr(getattr(message, "author", None), "id", ""))
    guild = getattr(message, "guild", None)
    channel = getattr(message, "channel", None)
    channel_id = str(getattr(channel, "id", ""))
    if guild is None:
        return f"dm:{author_id}"
    return f"guild:{getattr(guild, 'id', '')}:channel:{channel_id}"


def _session_key(bot_id: str, message: Any) -> tuple[str, str, str, str, str]:
    channel = _message_channel(message)
    author_id = str(getattr(getattr(message, "author", None), "id", ""))
    guild_id = str(getattr(getattr(message, "guild", None), "id", ""))
    channel_id = str(getattr(getattr(message, "channel", None), "id", ""))
    return (bot_id, channel, author_id, guild_id, channel_id)


def _clean_bot_mentions(text: str, client_user_id: int | str | None) -> str:
    if client_user_id is None:
        return text.strip()
    uid = re.escape(str(client_user_id))
    text = re.sub(rf"<@!?{uid}>", "", text)
    return text.strip()


def _is_reply_to_client(message: Any, client_user: Any) -> bool:
    reference = getattr(message, "reference", None)
    resolved = getattr(reference, "resolved", None)
    author = getattr(resolved, "author", None)
    if not author or not client_user:
        return False
    return str(getattr(author, "id", "")) == str(getattr(client_user, "id", ""))


def _is_guild_trigger(message: Any, client_user: Any) -> bool:
    if getattr(message, "guild", None) is None:
        return True
    if _is_reply_to_client(message, client_user):
        return True
    client_id = str(getattr(client_user, "id", ""))
    for mention in getattr(message, "mentions", []) or []:
        if str(getattr(mention, "id", "")) == client_id:
            return True
    return False


class DiscordSessionMap:
    """管理 Discord 訊息來源到 API session_id 的映射。"""

    def __init__(self):
        self._map: dict[tuple[str, str, str, str, str], str] = {}

    async def get_or_create_session(self, bot_id: str, message: Any, character_id: str) -> str:
        from api.dependencies import get_storage
        from api.session_manager import session_manager

        key = _session_key(bot_id, message)
        sid = self._map.get(key)
        if sid:
            s = await session_manager.get(sid)
            if s:
                return sid
            del self._map[key]

        get_storage().load_prefs()
        user_id = str(getattr(message.author, "id", ""))
        session = await session_manager.create(
            channel=_message_channel(message),
            channel_uid=_message_channel_uid(message),
            user_id=user_id,
            character_id=character_id,
            bot_id=bot_id,
        )
        self._map[key] = session.session_id
        return session.session_id

    async def clear_session(self, bot_id: str, message: Any) -> bool:
        from api.session_manager import session_manager

        key = _session_key(bot_id, message)
        sid = self._map.pop(key, None)
        if sid:
            await session_manager.delete(sid)
            return True
        return False

    def get_session_id(self, bot_id: str, message: Any) -> str | None:
        return self._map.get(_session_key(bot_id, message))

    def clear_bot(self, bot_id: str) -> None:
        for key in [key for key in self._map if key[0] == bot_id]:
            del self._map[key]


async def _send_discord_parts(message: Any, reply_text: str, user_id: str) -> None:
    """將文字與本地生成圖片依序送到 Discord。"""
    if discord is None:
        return

    matches = list(_IMAGE_MARKDOWN_RE.finditer(reply_text or ""))
    allowed_mentions = discord.AllowedMentions.none()
    first = True

    async def send_text(text: str) -> None:
        nonlocal first
        for chunk in _split_message(text):
            if first:
                await message.reply(chunk, mention_author=False, allowed_mentions=allowed_mentions)
                first = False
            else:
                await message.channel.send(chunk, allowed_mentions=allowed_mentions)

    async def send_file(path: Any, fallback: str) -> None:
        nonlocal first
        if path.exists() and path.is_file():
            file = discord.File(str(path), filename=path.name)
            if first:
                await message.reply(file=file, mention_author=False, allowed_mentions=allowed_mentions)
                first = False
            else:
                await message.channel.send(file=file, allowed_mentions=allowed_mentions)
        else:
            await send_text(fallback)

    if not matches:
        await send_text(reply_text)
        return

    last_end = 0
    for match in matches:
        text_before = reply_text[last_end:match.start()].strip()
        if text_before:
            await send_text(text_before)

        session_id = match.group(1)
        image_id = match.group(2)
        try:
            path = generated_image_path(user_id, session_id, image_id)
            await send_file(path, match.group(0))
        except Exception as exc:
            logger.error("Failed to send Discord image %s: %s", image_id, exc)
            await send_text(match.group(0))
        last_end = match.end()

    text_after = reply_text[last_end:].strip()
    if text_after:
        await send_text(text_after)


class DiscordBotManager:
    """管理多個 Discord gateway runtime。"""

    def __init__(self, registry: BotRegistry):
        self.registry = registry
        self._runtimes: dict[str, DiscordRuntime] = {}
        self._session_map = DiscordSessionMap()
        self._lock = asyncio.Lock()

    def get_status(self, bot_id: str, platform: str = "discord") -> dict[str, Any]:
        if platform != "discord":
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
                "platform": "discord",
                "status": "disabled",
                "running": False,
                "last_error": None,
            }
        running = bool(
            runtime.task
            and not runtime.task.done()
            and runtime.client
            and not runtime.client.is_closed()
            and runtime.status == "running"
        )
        return {
            "bot_id": bot_id,
            "platform": "discord",
            "status": runtime.status,
            "running": running,
            "last_error": runtime.last_error,
        }

    async def sync_from_registry(self) -> None:
        from api.dependencies import get_storage

        async with self._lock:
            prefs = get_storage().load_prefs()
            configs = self.registry.load_configs(prefs)
            desired_ids = {c["bot_id"] for c in configs if c.get("platform") == "discord" and c.get("enabled")}

            for bot_id in list(self._runtimes):
                if bot_id not in desired_ids:
                    await self._stop_bot_locked(bot_id)

            for cfg in configs:
                if cfg.get("platform") != "discord" or not cfg.get("enabled"):
                    continue
                fp = self._fingerprint(cfg.get("token", ""))
                runtime = self._runtimes.get(cfg["bot_id"])
                if (
                    runtime
                    and runtime.status == "running"
                    and runtime.token_fingerprint == fp
                    and runtime.character_id == cfg.get("character_id", "default")
                    and runtime.task
                    and not runtime.task.done()
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
            if cfg and cfg.get("enabled") and cfg.get("platform") == "discord":
                await self._start_bot_locked(cfg)

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
        runtime = DiscordRuntime(
            bot_id=bot_id,
            status="starting",
            token_fingerprint=self._fingerprint(token),
            character_id=character_id,
        )
        self._runtimes[bot_id] = runtime

        if not token:
            runtime.status = "error"
            runtime.last_error = "missing token"
            return
        if discord is None:
            runtime.status = "error"
            runtime.last_error = "discord.py is not installed"
            return

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        runtime.client = client

        @client.event
        async def on_ready():
            runtime.status = "running"
            runtime.last_error = None
            logger.info("Discord Bot started: bot_id=%s character_id=%s user=%s", bot_id, character_id, client.user)

        @client.event
        async def on_message(message):
            await self._handle_message(bot_id, character_id, client, message)

        async def _runner():
            try:
                await client.start(token)
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                runtime.status = "error"
                runtime.last_error = self._sanitize_error(exc, token)
                logger.error("Discord client error for bot_id=%s: %s", bot_id, runtime.last_error)
            finally:
                if not client.is_closed():
                    await client.close()

        runtime.task = asyncio.create_task(_runner())

    async def _stop_bot_locked(self, bot_id: str) -> None:
        runtime = self._runtimes.get(bot_id)
        if not runtime:
            return
        if runtime.client and not runtime.client.is_closed():
            try:
                await runtime.client.close()
            except Exception:
                pass
        if runtime.task and not runtime.task.done():
            runtime.task.cancel()
            try:
                await runtime.task
            except asyncio.CancelledError:
                pass
        self._session_map.clear_bot(bot_id)
        runtime.client = None
        runtime.task = None
        runtime.status = "disabled"
        logger.info("Discord Bot stopped: bot_id=%s", bot_id)

    async def _handle_message(self, bot_id: str, character_id: str, client: Any, message: Any) -> None:
        author = getattr(message, "author", None)
        if not author or getattr(author, "bot", False) or getattr(message, "webhook_id", None):
            return
        if not _is_guild_trigger(message, getattr(client, "user", None)):
            return

        content = _clean_bot_mentions(getattr(message, "content", "") or "", getattr(getattr(client, "user", None), "id", None))
        if not content:
            return

        if content == "/clear":
            cleared = await self._session_map.clear_session(bot_id, message)
            await message.reply(
                "對話歷史已清空。發送訊息開始新對話。" if cleared else "目前沒有活躍的對話 Session。",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        if content == "/status":
            await self._send_status(bot_id, message)
            return

        from api.dependencies import get_storage
        from api.routers.chat.orchestration import _select_orchestration, _unpack_orchestration_result
        from api.session_manager import session_manager

        sid = await self._session_map.get_or_create_session(bot_id, message, character_id)
        await session_manager.add_user_message(sid, content)
        s = await session_manager.get(sid)
        if not s:
            await message.reply("Session 錯誤，請重試。", mention_author=False, allowed_mentions=discord.AllowedMentions.none())
            return

        user_prefs = get_storage().load_prefs()
        orchestration_fn = _select_orchestration(user_prefs)
        status_cb = self._discord_event_callback(message)
        session_ctx = {
            "user_id": s.user_id,
            "character_id": s.character_id,
            "persona_face": s.persona_face,
            "session_id": sid,
            "bot_id": s.bot_id,
            "channel": s.channel,
        }

        try:
            async with message.channel.typing():
                result = await asyncio.to_thread(
                    orchestration_fn,
                    list(s.messages), list(s.last_entities), content, user_prefs,
                    on_event=status_cb,
                    session_ctx=session_ctx,
                )
            reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_data, \
                _inner_thought, _status_metrics, _tone, _speech, _thinking_speech, cited_uids = \
                _unpack_orchestration_result(result)
        except Exception as exc:
            logger.error("Discord chat orchestration failed: %s", exc, exc_info=True)
            await message.reply(f"處理失敗: {exc}", mention_author=False, allowed_mentions=discord.AllowedMentions.none())
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

        await _send_discord_parts(message, reply_text, s.user_id)

    async def _send_status(self, bot_id: str, message: Any) -> None:
        from api.dependencies import get_memory_sys
        from api.session_manager import session_manager

        sid = self._session_map.get_session_id(bot_id, message)
        if not sid:
            await message.reply("目前沒有活躍的 Session。發送任何訊息即可自動建立。", mention_author=False)
            return
        s = await session_manager.get(sid)
        if not s:
            await message.reply("Session 已過期。發送任何訊息即可自動建立新 Session。", mention_author=False)
            return

        ms = get_memory_sys()
        block_count = len(ms.memory_blocks) if ms.memory_blocks else 0
        await message.reply(
            f"Bot ID: {bot_id}\n"
            f"角色 ID: {s.character_id}\n"
            f"Channel: {s.channel}\n"
            f"Session ID: {sid[:8]}...\n"
            f"對話訊息數: {len(s.messages)}\n"
            f"當前實體標籤: {', '.join(s.last_entities) if s.last_entities else '(無)'}\n"
            f"記憶區塊總數: {block_count}\n"
            f"建立時間: {s.created_at.strftime('%Y-%m-%d %H:%M')}\n"
            f"最後活動: {s.last_active.strftime('%H:%M:%S')}",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @staticmethod
    def _discord_event_callback(message: Any):
        status_msg = None
        loop = asyncio.get_running_loop()
        allowed_mentions = discord.AllowedMentions.none() if discord else None

        def callback(data: dict):
            nonlocal status_msg
            action = data.get("action", "")
            text = data.get("message", "")
            if action == "calling" and text:
                async def _send_or_edit():
                    nonlocal status_msg
                    try:
                        content = f"🔍 {text}"
                        if status_msg is None:
                            status_msg = await message.reply(
                                content,
                                mention_author=False,
                                allowed_mentions=allowed_mentions,
                            )
                        else:
                            await status_msg.edit(content=content)
                    except Exception:
                        pass
                asyncio.run_coroutine_threadsafe(_send_or_edit(), loop)
            elif action == "complete" and text:
                async def _edit_complete():
                    try:
                        if status_msg:
                            await status_msg.edit(content=f"✅ {text}")
                    except Exception:
                        pass
                asyncio.run_coroutine_threadsafe(_edit_complete(), loop)

        return callback

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
