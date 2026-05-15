"""LiveEpisodePlan phase pipeline orchestration."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from bridge_runtime import LiveRuntime


logger = logging.getLogger("youtube_bridge")


class PhasePipelineManagerMixin:
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
        }

    def _schedule_main_summary_record(self, session_id: str, *, reason: str) -> None:
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
        state = self.storage.get_director_state(session_id)
        metadata = dict(state.get("metadata") or {})
        metadata["main_summary"] = {
            **dict(metadata.get("main_summary") or {}),
            "status": "running",
            "reason": str(reason or "")[:120],
            "started_at": datetime.now().isoformat(),
            "stage": "stage2_metadata_only",
        }
        director_state = self.storage.update_director_state(session_id, metadata=metadata)
        await self._broadcast(session_id, {"type": "director_state", "director": director_state})
