"""YouTubeBridge polling manager。"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from memoria_client import MemoriaClient
from storage import BridgeStorage
from youtube_client import YouTubeClient, normalize_message


logger = logging.getLogger("youtube_bridge")
DEFAULT_INJECT_CONTENT = "請根據已帶入的 YouTube 直播留言上下文回應。不要開啟瀏覽器或搜尋網頁。"


@dataclass
class LiveRuntime:
    session_id: str
    task: asyncio.Task | None = None
    inject_task: asyncio.Task | None = None
    running: bool = False
    status: str = "stopped"
    next_page_token: str | None = None
    last_error: str | None = None
    last_auto_inject_at: str | None = None
    last_auto_inject_error: str | None = None
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    inject_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class YouTubeBridgeManager:
    def __init__(self, storage: BridgeStorage, youtube_client: YouTubeClient | None = None):
        self.storage = storage
        self.youtube_client = youtube_client or YouTubeClient()
        self._runtimes: dict[str, LiveRuntime] = {}
        self._lock = asyncio.Lock()

    def get_status(self, session_id: str) -> dict[str, Any]:
        runtime = self._runtimes.get(session_id)
        session = self.storage.get_session(session_id)
        if not runtime:
            return {
                "session_id": session_id,
                "status": "stopped" if session else "missing",
                "running": False,
                "last_error": None,
            }
        return {
            "session_id": session_id,
            "status": runtime.status,
            "running": runtime.running,
            "last_error": runtime.last_error,
            "auto_inject_running": bool(runtime.inject_task and not runtime.inject_task.done()),
            "last_auto_inject_at": runtime.last_auto_inject_at,
            "last_auto_inject_error": runtime.last_auto_inject_error,
        }

    async def sync_autostart(self) -> None:
        for session in self.storage.list_sessions():
            if session.get("auto_connect"):
                await self.start_session(session["session_id"])
            elif session.get("status") == "running":
                self.storage.update_session_fields(session["session_id"], status="stopped")

    async def start_session(self, session_id: str) -> dict[str, Any]:
        async with self._lock:
            session = self.storage.get_session(session_id)
            if not session:
                raise ValueError("live session 不存在")
            connector = self.storage.get_connector(session["connector_id"])
            if not connector:
                raise ValueError("connector 不存在")
            if not connector.get("enabled"):
                raise ValueError("connector 未啟用")
            if not connector.get("api_key"):
                raise ValueError("connector 缺少 YouTube API key")
            if not session.get("live_chat_id"):
                if not session.get("video_id"):
                    raise ValueError("live session 需要 video_id 或 live_chat_id")
                live_chat_id = await asyncio.to_thread(
                    self.youtube_client.resolve_live_chat_id,
                    api_key=connector["api_key"],
                    video_id=session["video_id"],
                )
                session = self.storage.update_session_fields(session_id, live_chat_id=live_chat_id) or session

            existing = self._runtimes.get(session_id)
            if existing and existing.running:
                return self.get_status(session_id)

            runtime = existing or LiveRuntime(session_id=session_id)
            runtime.status = "starting"
            runtime.last_error = None
            runtime.last_auto_inject_error = None
            runtime.running = True
            runtime.task = asyncio.create_task(self._poll_loop(runtime))
            runtime.inject_task = asyncio.create_task(self._auto_inject_loop(runtime))
            self._runtimes[session_id] = runtime
            self.storage.update_session_fields(session_id, status="running")
            await self._broadcast(session_id, {"type": "status", "status": "running"})
            return self.get_status(session_id)

    async def stop_session(self, session_id: str) -> dict[str, Any]:
        async with self._lock:
            runtime = self._runtimes.get(session_id)
            if runtime and runtime.task:
                runtime.running = False
                runtime.task.cancel()
                try:
                    await runtime.task
                except asyncio.CancelledError:
                    pass
            if runtime and runtime.inject_task:
                runtime.inject_task.cancel()
                try:
                    await runtime.inject_task
                except asyncio.CancelledError:
                    pass
            if runtime:
                runtime.status = "stopped"
                runtime.task = None
                runtime.inject_task = None
            self.storage.update_session_fields(session_id, status="stopped")
            await self._broadcast(session_id, {"type": "status", "status": "stopped"})
            return self.get_status(session_id)

    async def stop_all(self) -> None:
        for session_id in list(self._runtimes):
            await self.stop_session(session_id)

    async def subscribe(self, session_id: str) -> asyncio.Queue:
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id))
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        runtime.subscribers.add(queue)
        return queue

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        runtime = self._runtimes.get(session_id)
        if runtime:
            runtime.subscribers.discard(queue)

    async def _poll_loop(self, runtime: LiveRuntime) -> None:
        while runtime.running:
            session = self.storage.get_session(runtime.session_id)
            if not session:
                runtime.status = "missing"
                runtime.running = False
                return
            connector = self.storage.get_connector(session["connector_id"])
            if not connector:
                runtime.status = "connector_missing"
                runtime.running = False
                return
            try:
                data = await asyncio.to_thread(
                    self.youtube_client.fetch_live_chat_messages,
                    api_key=connector["api_key"],
                    live_chat_id=session["live_chat_id"],
                    page_token=runtime.next_page_token,
                )
                runtime.next_page_token = data.get("nextPageToken") or runtime.next_page_token
                runtime.status = "running"
                runtime.last_error = None
                for item in data.get("items") or []:
                    event = normalize_message(item, session=session, connector=connector)
                    if not event.get("youtube_message_id"):
                        continue
                    saved = self.storage.save_event(event)
                    if saved:
                        await self._broadcast(runtime.session_id, {"type": "youtube_live_event", "event": saved})
                interval_ms = int(data.get("pollingIntervalMillis") or 5000)
                await asyncio.sleep(max(2.0, min(interval_ms / 1000, 30.0)))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                runtime.status = "error"
                runtime.last_error = str(exc)
                self.storage.update_session_fields(runtime.session_id, status="error")
                logger.error("YouTube polling error session_id=%s error=%s", runtime.session_id, exc, exc_info=True)
                await self._broadcast(
                    runtime.session_id,
                    {"type": "status", "status": "error", "message": str(exc)},
                )
                await asyncio.sleep(15)

    async def _auto_inject_loop(self, runtime: LiveRuntime) -> None:
        while runtime.running:
            session = self.storage.get_session(runtime.session_id)
            if not session:
                return
            interval = max(5, min(int(session.get("inject_interval_seconds", 30) or 30), 600))
            try:
                if session.get("auto_inject"):
                    pending = self.storage.list_events(
                        runtime.session_id,
                        limit=int(session.get("max_context_messages", 50) or 50),
                        uninjected_only=True,
                    )
                    active_pending = [
                        event for event in pending
                        if event.get("status") == "active" and event.get("message_text")
                    ]
                    min_pending = max(1, int(session.get("min_pending_events", 1) or 1))
                    if len(active_pending) >= min_pending:
                        result = await self.inject_recent(
                            runtime.session_id,
                            event_ids=[event["id"] for event in active_pending],
                            max_events=session.get("max_context_messages", 50),
                            content=DEFAULT_INJECT_CONTENT,
                        )
                        runtime.last_auto_inject_at = result.get("injected_at")
                        runtime.last_auto_inject_error = None
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
            except ValueError as exc:
                if "沒有可注入" not in str(exc):
                    runtime.last_auto_inject_error = str(exc)
                    await self._broadcast(runtime.session_id, {
                        "type": "auto_inject_error",
                        "message": str(exc),
                    })
                await asyncio.sleep(interval)
            except Exception as exc:
                runtime.last_auto_inject_error = str(exc)
                logger.error("YouTube auto inject error session_id=%s error=%s", runtime.session_id, exc, exc_info=True)
                await self._broadcast(runtime.session_id, {
                    "type": "auto_inject_error",
                    "message": str(exc),
                })
                await asyncio.sleep(interval)

    async def _broadcast(self, session_id: str, payload: dict[str, Any]) -> None:
        runtime = self._runtimes.get(session_id)
        if not runtime:
            return
        stale: list[asyncio.Queue] = []
        for queue in list(runtime.subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                stale.append(queue)
        for queue in stale:
            runtime.subscribers.discard(queue)

    async def inject_recent(
        self,
        session_id: str,
        *,
        event_ids: list[int] | None = None,
        max_events: int | None = None,
        content: str = DEFAULT_INJECT_CONTENT,
        memoria_session_id: str = "",
        character_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id))
        async with runtime.inject_lock:
            session = self.storage.get_session(session_id)
            if not session:
                raise ValueError("live session 不存在")
            external_context, summary = self.build_external_context(
                session_id,
                event_ids=event_ids,
                max_events=max_events,
            )
            target_session_id = memoria_session_id or session.get("target_memoria_session_id", "")
            target_character_ids = character_ids or session.get("character_ids", [])
            result = await asyncio.to_thread(
                MemoriaClient().chat_sync,
                content=content,
                session_id=target_session_id,
                character_ids=target_character_ids,
                external_context=external_context,
            )
            marked_injected = self.storage.mark_events_injected(session_id, summary.get("event_ids", []))
            result_session_id = result.get("session_id") if isinstance(result, dict) else ""
            if result_session_id and not session.get("target_memoria_session_id"):
                self.storage.update_session_fields(session_id, target_memoria_session_id=result_session_id)
            injected_at = datetime.now().isoformat()
            payload = {
                "summary": summary,
                "marked_injected": marked_injected,
                "memoria_result": result,
                "injected_at": injected_at,
            }
            await self._broadcast(session_id, {
                "type": "memoria_injected",
                "summary": summary,
                "marked_injected": marked_injected,
                "memoria_session_id": result_session_id or target_session_id,
            })
            return payload

    def build_external_context(
        self,
        session_id: str,
        *,
        event_ids: list[int] | None = None,
        max_events: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        limit = max(1, min(int(max_events or session.get("max_context_messages", 50)), 100))
        if event_ids:
            events = self.storage.get_events_by_ids(session_id, event_ids, limit=limit)
            events = [event for event in events if not event.get("injected_at")]
        else:
            events = self.storage.list_events(session_id, limit=limit, uninjected_only=True)
        active_events = [event for event in events if event.get("status") == "active" and event.get("message_text")]

        lines: list[str] = []
        used_ids: list[int] = []
        visible_events: list[dict[str, Any]] = []
        max_chars = int(session.get("max_context_chars", 8000) or 8000)
        used_chars = 0
        for event in active_events:
            line = self._event_line(event)
            next_len = len(line) + 1
            if lines and used_chars + next_len > max_chars:
                break
            lines.append(line)
            used_ids.append(int(event["id"]))
            visible_events.append(self._visible_event(event))
            used_chars += next_len
        if not lines:
            raise ValueError("沒有可注入的直播留言")

        summary = {
            "source": "youtube_live",
            "source_session_id": session_id,
            "connector_id": session["connector_id"],
            "video_id": session.get("video_id", ""),
            "live_chat_id": session.get("live_chat_id", ""),
            "event_ids": used_ids,
            "event_count": len(used_ids),
            "dropped_count": max(0, len(active_events) - len(used_ids)),
        }
        payload = {
            "source": "youtube_live",
            "source_session_id": session_id,
            "connector_id": session["connector_id"],
            "video_id": session.get("video_id", ""),
            "live_chat_id": session.get("live_chat_id", ""),
            "context_text": "\n".join(lines),
            "event_ids": used_ids,
            "visible_events": visible_events,
            "max_chars": max_chars,
            "summary": summary,
        }
        return payload, summary

    @staticmethod
    def _event_line(event: dict[str, Any]) -> str:
        author = (event.get("author_display_name") or "匿名觀眾").strip()
        text = (event.get("message_text") or "").replace("\r", " ").replace("\n", " ").strip()
        return f"- {author or '匿名觀眾'}: {text}"

    @staticmethod
    def _visible_event(event: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": int(event.get("id") or 0),
            "author_display_name": (event.get("author_display_name") or "匿名觀眾").strip(),
            "author_channel_id": str(event.get("author_channel_id") or "").strip(),
            "message_text": (event.get("message_text") or "").replace("\r", " ").replace("\n", " ").strip(),
        }
