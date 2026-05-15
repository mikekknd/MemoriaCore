"""LiveEpisodePlan phase pipeline orchestration."""
from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from bridge_runtime import LiveRuntime


logger = logging.getLogger("youtube_bridge")


class PhasePipelineManagerMixin:
    @staticmethod
    def _summary_phase_from_action(action: str) -> str:
        action_text = str(action or "").strip()
        if action_text.startswith("post_plan_free_talk"):
            return "post_plan_free_talk"
        if action_text == "free_talk_audience_closing":
            return "free_talk_audience_closing"
        return ""

    def _event_phase_for_session(self, session_id: str) -> str:
        state = self.storage.get_director_state(session_id) or {}
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        phase = str(metadata.get("phase") or metadata.get("live_phase") or "").strip()
        if phase in {"planned_content", "main_audience_closing", "post_plan_free_talk", "free_talk_audience_closing", "free_talk"}:
            return phase
        return "planned_content"

    def _interaction_phase_for_session(self, session_id: str, *, source: str = "", action: str = "") -> str:
        action_phase = self._summary_phase_from_action(action)
        if action_phase:
            return action_phase
        source_text = str(source or "").strip()
        if source_text == "main_audience_closing":
            return "main_audience_closing"
        return self._event_phase_for_session(session_id)

    async def finish_main_phase(
        self,
        session_id: str,
        *,
        reason: str,
        enter_free_talk: bool,
        topic_root: Path,
    ) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        if str(session.get("status") or "") != "running":
            raise ValueError("live session 尚未開始")

        runtime = self._runtimes.get(session_id)
        if runtime is None or not runtime.running:
            raise ValueError("live session 尚未開始")
        reason_text = str(reason or "episode_plan_completed")[:120]

        async with runtime.closing_lock:
            session = self.storage.get_session(session_id) or session
            if str(session.get("status") or "") != "running" or not runtime.running:
                raise ValueError("live session 尚未開始")
            if session.get("status") == "ended" or runtime.status == "ended":
                return {
                    "phase": "ended",
                    "session": session,
                    "director": self.storage.get_director_state(session_id),
            }
            started_at = datetime.now().isoformat()
            runtime.status = "main_audience_closing"
            session = self.storage.update_session_fields(
                session_id,
                auto_inject=False,
            ) or session
            metadata = dict((self.storage.get_director_state(session_id) or {}).get("metadata") or {})
            metadata["phase"] = "main_audience_closing"
            metadata["main_audience_closing"] = {
                **dict(metadata.get("main_audience_closing") or {}),
                "status": "running",
                "started_at": started_at,
                "reason": reason_text,
            }
            director_state = self.storage.update_director_state(
                session_id,
                status="main_audience_closing",
                metadata=metadata,
            )
            await self._broadcast(session_id, {"type": "director_state", "director": director_state})
            await self._broadcast(
                session_id,
                {
                    "type": "status",
                    "status": "main_audience_closing",
                    "message": "main phase completed; thanking main Super Chats",
                },
            )

            closing = await self._run_main_audience_sc_closing(runtime, session, reason=reason_text)
            metadata = dict((self.storage.get_director_state(session_id) or {}).get("metadata") or {})
            metadata["main_audience_closing"] = {
                **dict(metadata.get("main_audience_closing") or {}),
                "status": "completed",
                "completed_at": datetime.now().isoformat(),
                "closing": closing,
            }
            metadata["main_summary"] = {
                "status": "queued",
                "reason": reason_text,
                "queued_at": datetime.now().isoformat(),
            }
            director_state = self.storage.update_director_state(
                session_id,
                status="main_summary_queued",
                metadata=metadata,
            )
            await self._broadcast(session_id, {"type": "director_state", "director": director_state})
            self._schedule_main_summary_record(session_id, reason=reason_text)

        should_enter_free_talk = bool(enter_free_talk and session.get("post_plan_free_talk_enabled"))
        if should_enter_free_talk:
            return await self.start_post_plan_free_talk_test(
                session_id,
                topic_root=Path(topic_root),
                transition_reason=reason_text,
            )

        metadata = dict((self.storage.get_director_state(session_id) or {}).get("metadata") or {})
        metadata["phase"] = "finalizing_main_only"
        director_state = self.storage.update_director_state(
            session_id,
            status="finalizing_main_only",
            metadata=metadata,
        )
        await self._broadcast(session_id, {"type": "director_state", "director": director_state})
        finalized = await self.finalize_phase_pipeline(session_id, reason=reason_text)
        return {
            "phase": "finalizing_main_only",
            "director": self.storage.get_director_state(session_id),
            "finalized": finalized,
        }

    async def finalize_phase_pipeline(
        self,
        session_id: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        runtime = self._runtimes.setdefault(
            session_id,
            LiveRuntime(
                session_id=session_id,
                running=str(session.get("status") or "") == "running",
                status=str(session.get("status") or "stopped"),
            ),
        )
        reason_text = str(reason or "operator_finalize")[:120]

        state = self.storage.get_director_state(session_id)
        phase = str((state.get("metadata") or {}).get("phase") or "").strip()
        if phase == "post_plan_free_talk":
            async with runtime.closing_lock:
                session = self.storage.get_session(session_id) or session
                metadata = dict((self.storage.get_director_state(session_id) or {}).get("metadata") or {})
                metadata["phase"] = "free_talk_audience_closing"
                metadata["free_talk_audience_closing"] = {
                    **dict(metadata.get("free_talk_audience_closing") or {}),
                    "status": "completed",
                    "reason": reason_text,
                    "completed_at": datetime.now().isoformat(),
                }
                metadata["free_talk_summary"] = {
                    **dict(metadata.get("free_talk_summary") or {}),
                    "status": "queued",
                    "reason": reason_text,
                    "queued_at": datetime.now().isoformat(),
                }
                director_state = self.storage.update_director_state(
                    session_id,
                    status="free_talk_summary_queued",
                    metadata=metadata,
                )
                await self._broadcast(session_id, {"type": "director_state", "director": director_state})

            await self.run_phase_summary(session_id, summary_phase="free_talk", reason=reason_text)
            finalized = await self._finalize_live_session(
                runtime,
                self.storage.get_session(session_id) or session,
                finalized_by="phase_finalize",
                closing_message="free talk summary completed; closing live session",
                ended_message="free talk summary completed",
                metadata={
                    "phase": "ended",
                    "phase_finalize": {
                        "status": "completed",
                        "reason": reason_text,
                        "completed_at": datetime.now().isoformat(),
                    },
                },
            )
            cleanup = await self.maybe_run_phase_cleanup(session_id)
            return {
                "phase": "free_talk_summary",
                **finalized,
                "cleanup": cleanup,
            }

        async with runtime.closing_lock:
            session = self.storage.get_session(session_id) or session
            metadata = dict((self.storage.get_director_state(session_id) or {}).get("metadata") or {})
            metadata["phase_finalize"] = {
                **dict(metadata.get("phase_finalize") or {}),
                "status": "running",
                "reason": reason_text,
                "started_at": datetime.now().isoformat(),
            }
            director_state = self.storage.update_director_state(
                session_id,
                status="finalizing_main_only",
                metadata=metadata,
            )
            await self._broadcast(session_id, {"type": "director_state", "director": director_state})
            finalized = await self._finalize_live_session(
                runtime,
                session,
                finalized_by="phase_finalize",
                closing_message="phase finalize requested; closing live session",
                ended_message="phase finalize requested",
                metadata={
                    "phase": "ended",
                    "phase_finalize": {
                        **dict(metadata.get("phase_finalize") or {}),
                        "status": "completed",
                        "reason": reason_text,
                        "completed_at": datetime.now().isoformat(),
                    },
                },
            )
        return {
            "phase": "finalized",
            **finalized,
            "cleanup": await self.maybe_run_phase_cleanup(session_id),
        }

    def _schedule_main_summary_record(self, session_id: str, *, reason: str) -> None:
        if not getattr(self, "phase_summary_callback", None):
            return
        task = asyncio.create_task(self._run_main_summary_background(session_id, reason=reason))

        def _log_background_error(done: asyncio.Task) -> None:
            try:
                exc = done.exception()
            except asyncio.CancelledError:
                return
            if exc is not None:
                logger.warning("main summary metadata task failed session_id=%s error=%s", session_id, exc)

        task.add_done_callback(_log_background_error)

    async def _run_main_summary_background(self, session_id: str, *, reason: str) -> None:
        await self.run_phase_summary(session_id, summary_phase="main", reason=reason)
        await self.maybe_run_phase_cleanup(session_id)

    async def run_phase_summary(self, session_id: str, *, summary_phase: str, reason: str) -> dict[str, Any]:
        phase = str(summary_phase or "").strip()
        if phase not in {"main", "free_talk"}:
            raise ValueError("summary_phase must be main or free_talk")
        key = "main_summary" if phase == "main" else "free_talk_summary"
        reason_text = str(reason or "")[:120]
        state = self.storage.get_director_state(session_id)
        metadata = dict(state.get("metadata") or {})
        metadata[key] = {
            **dict(metadata.get(key) or {}),
            "status": "running",
            "reason": reason_text,
            "started_at": datetime.now().isoformat(),
        }
        director_state = self.storage.update_director_state(session_id, metadata=metadata)
        await self._broadcast(session_id, {"type": "director_state", "director": director_state})

        callback = getattr(self, "phase_summary_callback", None)
        if not callback:
            result: dict[str, Any] = {
                "summary": None,
                "memory_write": {"status": "skipped", "reason": "callback_missing"},
            }
        else:
            callback_result = callback(session_id, summary_phase=phase, reason=reason_text)
            result = await callback_result if inspect.isawaitable(callback_result) else callback_result
            if not isinstance(result, dict):
                result = {"summary": result, "memory_write": {"status": "unknown"}}

        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        memory_write = result.get("memory_write") if isinstance(result.get("memory_write"), dict) else {}
        memory_write_status = str(memory_write.get("status") or "unknown")
        status = "completed" if memory_write_status == "completed" else "failed"
        metadata = dict((self.storage.get_director_state(session_id) or {}).get("metadata") or {})
        metadata[key] = {
            **dict(metadata.get(key) or {}),
            "status": status,
            "reason": reason_text,
            "summary_id": summary.get("id"),
            "memory_write_status": memory_write_status,
            "completed_at": datetime.now().isoformat(),
        }
        director_state = self.storage.update_director_state(session_id, metadata=metadata)
        await self._broadcast(session_id, {"type": "director_state", "director": director_state})
        return result

    async def maybe_run_phase_cleanup(self, session_id: str) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            return {"status": "missing"}
        if not session.get("auto_delete_after_processed"):
            return {"status": "skipped", "reason": "auto_delete_disabled"}
        state = self.storage.get_director_state(session_id)
        metadata = dict(state.get("metadata") or {})
        cleanup_state = metadata.get("phase_cleanup") if isinstance(metadata.get("phase_cleanup"), dict) else {}
        if cleanup_state.get("status") == "completed":
            return {"status": "cleaned", "cleanup": cleanup_state.get("result")}

        required = ["main_summary"]
        if session.get("post_plan_free_talk_enabled") or isinstance(metadata.get("post_plan_free_talk"), dict):
            required.append("free_talk_summary")
        for key in required:
            item = metadata.get(key) if isinstance(metadata.get(key), dict) else {}
            if item.get("status") != "completed" or item.get("memory_write_status") != "completed":
                return {"status": "waiting", "reason": f"{key}_not_complete", "required": required}

        callback = getattr(self, "phase_cleanup_callback", None)
        if not callback:
            return {"status": "skipped", "reason": "cleanup_callback_missing"}
        cleanup_result = callback(session_id)
        cleanup = await cleanup_result if inspect.isawaitable(cleanup_result) else cleanup_result
        if self.storage.get_session(session_id):
            metadata = dict((self.storage.get_director_state(session_id) or {}).get("metadata") or {})
            metadata["phase_cleanup"] = {
                "status": "completed",
                "completed_at": datetime.now().isoformat(),
                "result": cleanup,
            }
            self.storage.update_director_state(session_id, status="ended", metadata=metadata)
        return {"status": "cleaned", "cleanup": cleanup}
