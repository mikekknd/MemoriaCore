"""YouTubeBridge session runtime lifecycle mixin。"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from bridge_runtime import LiveRuntime


logger = logging.getLogger("youtube_bridge")


class RuntimeLifecycleManagerMixin:
    def get_status(self, session_id: str) -> dict[str, Any]:
        runtime = self._runtimes.get(session_id)
        session = self.storage.get_session(session_id)
        mode = "youtube" if session and (session.get("live_chat_id") or session.get("video_id")) else "test"
        if not runtime:
            return {
                "session_id": session_id,
                "status": session.get("status", "stopped") if session else "missing",
                "running": False,
                "mode": mode,
                "last_error": None,
                "active_interaction": self._public_interaction_status(self.storage.get_active_interaction(session_id)),
                "director": self._public_director_state(self.storage.get_director_state(session_id)),
                "auto_test_events_running": False,
            }
        return {
            "session_id": session_id,
            "status": runtime.status,
            "running": runtime.running,
            "mode": runtime.mode,
            "last_error": runtime.last_error,
            "auto_inject_running": bool(runtime.running and runtime.inject_task and not runtime.inject_task.done()),
            "last_auto_inject_at": runtime.last_auto_inject_at,
            "last_auto_inject_error": runtime.last_auto_inject_error,
            "auto_test_events_running": bool(runtime.running and runtime.test_event_task and not runtime.test_event_task.done()),
            "last_auto_test_event_at": runtime.last_auto_test_event_at,
            "last_auto_test_event_error": runtime.last_auto_test_event_error,
            "active_interaction": self._public_interaction_status(self.storage.get_active_interaction(session_id)),
            "director": self._public_director_state(self.storage.get_director_state(session_id)),
        }

    async def sync_autostart(self) -> None:
        for session in self.storage.list_sessions():
            status = session.get("status")
            should_resume = (
                session.get("auto_connect")
                and status in {"starting", "running"}
                and not self._session_is_finalized(session)
            )
            if should_resume:
                try:
                    self.storage.finalize_incomplete_interactions(
                        session["session_id"],
                        status="interrupted",
                        reason="server_restarted",
                        metadata={"finalized_by": "sync_autostart"},
                    )
                    await self.start_session(session["session_id"])
                except Exception as exc:
                    logger.warning("live session autostart failed: %s: %s", session["session_id"], exc)
                    self.storage.update_session_fields(session["session_id"], status="stopped")
            elif status in {"starting", "running"}:
                self.storage.update_session_fields(session["session_id"], status="stopped")

    async def start_session(self, session_id: str) -> dict[str, Any]:
        async with self._lock:
            session = self.storage.get_session(session_id)
            if not session:
                raise ValueError("live session 不存在")
            if self._session_is_finalized(session):
                raise ValueError("live session 已標記結束；請建立或更新為新的 video_id 後再啟動")
            if hasattr(self.storage, "ensure_single_connector"):
                self.storage.ensure_single_connector()
                session = self.storage.get_session(session_id) or session
            if session.get("episode_plan_id"):
                try:
                    bound_character_ids = self._episode_character_ids_for_session(session)
                except RuntimeError as exc:
                    raise ValueError(str(exc)) from exc
                session = self.storage.update_session_fields(
                    session_id,
                    character_ids=bound_character_ids,
                ) or session
            connector = self.storage.get_connector(session["connector_id"])
            if not connector:
                raise ValueError("connector 不存在")
            if not connector.get("enabled"):
                raise ValueError("connector 未啟用")
            needs_youtube_polling = bool(session.get("live_chat_id") or session.get("video_id"))
            if needs_youtube_polling and not connector.get("api_key"):
                raise ValueError("connector 缺少 YouTube API key")
            if needs_youtube_polling and not session.get("live_chat_id"):
                live_chat_id = await asyncio.to_thread(
                    self.youtube_client.resolve_live_chat_id,
                    api_key=connector["api_key"],
                    video_id=session["video_id"],
                )
                session = self.storage.update_session_fields(session_id, live_chat_id=live_chat_id) or session
            if needs_youtube_polling:
                self._disable_test_events_for_real_youtube_session(session_id, session)
                session = self.storage.get_session(session_id) or session
            existing = self._runtimes.get(session_id)
            if existing and existing.running:
                return self.get_status(session_id)

            runtime = existing or LiveRuntime(session_id=session_id)
            runtime.mode = "youtube" if session.get("live_chat_id") else "test"
            runtime.status = "starting"
            runtime.last_error = None
            runtime.last_auto_inject_error = None
            runtime.running = True
            runtime.task = asyncio.create_task(self._poll_loop(runtime)) if runtime.mode == "youtube" else None
            runtime.inject_task = asyncio.create_task(self._auto_inject_loop(runtime))
            if session.get("auto_test_events_enabled"):
                runtime.test_event_task = asyncio.create_task(self._auto_test_event_loop(runtime))
            director_state = self.storage.get_director_state(session_id)
            if director_state.get("director_enabled"):
                runtime.director_task = asyncio.create_task(self._director_loop(runtime))
            self._runtimes[session_id] = runtime
            self.storage.update_session_fields(
                session_id,
                status="running",
                started_at=session.get("started_at") or datetime.now().isoformat(),
            )
            runtime.status = "running"
            await self._broadcast(session_id, {"type": "status", "status": "running", "mode": runtime.mode})
            return self.get_status(session_id)

    async def stop_session(self, session_id: str) -> dict[str, Any]:
        async with self._lock:
            runtime = self._runtimes.get(session_id)
            if runtime:
                runtime.running = False
                for cancel_event in runtime.cancel_events.values():
                    cancel_event.set()
            if runtime and runtime.task:
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
            if runtime and runtime.director_task:
                runtime.director_task.cancel()
                try:
                    await runtime.director_task
                except asyncio.CancelledError:
                    pass
            if runtime and runtime.director_kickoff_task:
                runtime.director_kickoff_task.cancel()
                try:
                    await runtime.director_kickoff_task
                except asyncio.CancelledError:
                    pass
            if runtime and runtime.test_event_task:
                runtime.test_event_task.cancel()
                try:
                    await runtime.test_event_task
                except asyncio.CancelledError:
                    pass
            if runtime and runtime.safety_task:
                runtime.safety_task.cancel()
                try:
                    await runtime.safety_task
                except asyncio.CancelledError:
                    pass
            if runtime:
                runtime.status = "stopped"
                runtime.task = None
                runtime.inject_task = None
                runtime.director_task = None
                runtime.director_kickoff_task = None
                runtime.test_event_task = None
                runtime.safety_task = None
                self.storage.update_director_state(session_id, status="stopped")
            self.storage.finalize_incomplete_interactions(
                session_id,
                status="interrupted",
                reason="session_stopped",
                metadata={"finalized_by": "session_stop"},
            )
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

    @staticmethod
    def _session_is_finalized(session: dict[str, Any]) -> bool:
        return bool(
            session.get("finalized_at")
            or session.get("status") == "ended"
            or session.get("summary_status") in {"completed", "summarizing"}
        )

    @staticmethod
    def _session_elapsed(session: dict[str, Any]) -> tuple[int, int, int]:
        planned = max(0, int(session.get("planned_duration_minutes", 0) or 0))
        created_at = str(session.get("started_at") or session.get("created_at") or "")
        try:
            started = datetime.fromisoformat(created_at)
        except ValueError:
            started = datetime.now()
        elapsed = max(0, int((datetime.now() - started).total_seconds() // 60))
        if planned <= 0:
            return elapsed, 0, 0
        percent = max(0, min(100, int(round((elapsed / planned) * 100))))
        remaining = max(0, planned - elapsed)
        return elapsed, percent, remaining

    @staticmethod
    def _parse_iso_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    def _duration_reached(self, session: dict[str, Any]) -> bool:
        planned = max(0, int(session.get("planned_duration_minutes", 0) or 0))
        if planned <= 0 or not session.get("auto_finalize_on_duration"):
            return False
        _elapsed, percent, _remaining = self._session_elapsed(session)
        return percent >= 100

    async def _cancel_runtime_task(self, runtime: LiveRuntime, attr: str) -> None:
        task = getattr(runtime, attr)
        if not task:
            return
        current = asyncio.current_task()
        if task is current:
            return
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        setattr(runtime, attr, None)

    async def _stop_runtime_background_tasks_for_closing(self, runtime: LiveRuntime) -> None:
        for cancel_event in runtime.cancel_events.values():
            cancel_event.set()
        await self._cancel_runtime_task(runtime, "task")
        if not runtime.inject_lock.locked():
            await self._cancel_runtime_task(runtime, "inject_task")
        await self._cancel_runtime_task(runtime, "director_task")
        await self._cancel_runtime_task(runtime, "director_kickoff_task")
        await self._cancel_runtime_task(runtime, "test_event_task")
