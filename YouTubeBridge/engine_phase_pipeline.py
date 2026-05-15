"""LiveEpisodePlan phase pipeline orchestration."""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from bridge_runtime import LiveRuntime
from free_talk_low_signal import classify_low_signal_comment, free_talk_closing_batch_size


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
        if source_text in {"main_audience_closing", "free_talk_audience_closing"}:
            return source_text
        return self._event_phase_for_session(session_id)

    @staticmethod
    def _is_free_talk_closing_candidate(event: dict[str, Any]) -> bool:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        phase = str(metadata.get("phase") or metadata.get("live_phase") or "").strip()
        if phase not in {"post_plan_free_talk", "free_talk"}:
            return False
        if str(event.get("status") or "") != "active":
            return False
        if not str(event.get("message_text") or "").strip():
            return False
        priority_class = str(event.get("priority_class") or "normal").strip()
        message_type = str(event.get("message_type") or "").strip()
        if priority_class == "super_chat" or message_type == "superChatEvent":
            return False
        return not event.get("injected_at")

    @staticmethod
    def _duplicate_message_key(text: str) -> str:
        return "".join(str(text or "").split()).lower()

    async def _run_free_talk_audience_closing(self, session_id: str, *, reason: str) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        started_clock = time.monotonic()
        started_at = datetime.now().isoformat()
        reason_text = str(reason or "operator_finalize")[:120]
        limit_seconds = max(1, int(session.get("free_talk_closing_time_limit_seconds", 300) or 300))
        self.storage.update_session_fields(session_id, auto_inject=False)
        metadata = dict((self.storage.get_director_state(session_id) or {}).get("metadata") or {})
        metadata["phase"] = "free_talk_audience_closing"
        metadata["free_talk_audience_closing"] = {
            **dict(metadata.get("free_talk_audience_closing") or {}),
            "status": "running",
            "reason": reason_text,
            "started_at": started_at,
        }
        director_state = self.storage.update_director_state(
            session_id,
            status="free_talk_audience_closing",
            metadata=metadata,
        )
        await self._broadcast(session_id, {"type": "director_state", "director": director_state})

        pending: list[dict[str, Any]] = []
        after_id = 0
        time_limited = False
        while True:
            if time.monotonic() - started_clock >= limit_seconds:
                time_limited = True
                break
            page = self.storage.list_events(
                session_id,
                limit=500,
                after_id=after_id,
                uninjected_only=True,
            )
            if not page:
                break
            after_id = max(int(event.get("id") or 0) for event in page)
            pending.extend(event for event in page if self._is_free_talk_closing_candidate(event))
            if len(page) < 500:
                break

        low_signal_reasons: dict[int, str] = {}
        eligible: list[dict[str, Any]] = []
        seen_messages: set[str] = set()
        for event in pending:
            text = str(event.get("message_text") or "")
            reason_code = classify_low_signal_comment(text)
            duplicate_key = self._duplicate_message_key(text)
            if duplicate_key and duplicate_key in seen_messages:
                reason_code = reason_code or "duplicate_message"
            if reason_code:
                low_signal_reasons[int(event["id"])] = reason_code
                continue
            eligible.append(event)
            if duplicate_key:
                seen_messages.add(duplicate_key)

        low_signal_skipped_count = self.storage.mark_events_low_signal_skipped(session_id, low_signal_reasons)
        batch_size = free_talk_closing_batch_size(
            len(eligible),
            target_batches=int(session.get("free_talk_closing_target_batches", 10) or 10),
            min_batch_size=int(session.get("free_talk_closing_min_batch_size", 5) or 5),
            max_batch_size=int(session.get("free_talk_closing_max_batch_size", 30) or 30),
        )
        processed_ids: list[int] = []
        batch_count = 0
        for start in range(0, len(eligible), batch_size):
            if time.monotonic() - started_clock >= limit_seconds:
                time_limited = True
                break
            batch = eligible[start:start + batch_size]
            if not batch:
                continue
            batch_ids = [int(event["id"]) for event in batch]
            result = await self.inject_recent(
                session_id,
                event_ids=batch_ids,
                max_events=len(batch_ids),
                content=(
                    "以下是雜談收尾時尚未回覆的聊天室留言摘要。"
                    "請用自然收尾語氣一次回應主要問題與情緒，不需要逐條點名。"
                ),
                memoria_session_id=str(session.get("target_memoria_session_id") or ""),
                character_ids=session.get("character_ids", []),
                source="free_talk_audience_closing",
                priority=260,
                claim_timeout_seconds=0.2,
            )
            injected_ids = [
                int(event_id)
                for event_id in (result.get("summary") or {}).get("event_ids", [])
            ]
            processed_ids.extend(injected_ids)
            batch_count += 1

        eligible_processed_count = len(dict.fromkeys(processed_ids))
        closing_skipped_count = max(0, len(eligible) - eligible_processed_count)
        if closing_skipped_count and time_limited:
            status = "time_limited"
        elif closing_skipped_count:
            status = "completed_with_skips"
        else:
            status = "completed"
        metadata = dict((self.storage.get_director_state(session_id) or {}).get("metadata") or {})
        closing_metadata = {
            **dict(metadata.get("free_talk_audience_closing") or {}),
            "status": status,
            "reason": reason_text,
            "eligible_processed_count": eligible_processed_count,
            "low_signal_skipped_count": low_signal_skipped_count,
            "closing_skipped_count": closing_skipped_count,
            "batch_size": batch_size,
            "batch_count": batch_count,
            "completed_at": datetime.now().isoformat(),
        }
        metadata["phase"] = "free_talk_audience_closing"
        metadata["free_talk_audience_closing"] = closing_metadata
        director_state = self.storage.update_director_state(
            session_id,
            status="free_talk_audience_closing",
            metadata=metadata,
        )
        await self._broadcast(session_id, {"type": "director_state", "director": director_state})
        await self._broadcast(session_id, {
            "type": "free_talk_audience_closing_completed",
            "session_id": session_id,
            "closing": closing_metadata,
        })
        return closing_metadata

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
                runtime.status = "free_talk_audience_closing"
                closing = await self._run_free_talk_audience_closing(session_id, reason=reason_text)
                metadata = dict((self.storage.get_director_state(session_id) or {}).get("metadata") or {})
                metadata["phase"] = "free_talk_audience_closing"
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
            final_metadata = dict((self.storage.get_director_state(session_id) or {}).get("metadata") or {})
            finalized = await self._finalize_live_session(
                runtime,
                self.storage.get_session(session_id) or session,
                finalized_by="phase_finalize",
                closing_message="free talk summary completed; closing live session",
                ended_message="free talk summary completed",
                metadata={
                    **final_metadata,
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
                "free_talk_audience_closing": closing,
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
