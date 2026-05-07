"""YouTubeBridge 直播收尾與 duration finalize mixin。"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from bridge_contracts import SAFETY_CLASSIFIER_BATCH_LIMIT
from bridge_runtime import LiveRuntime


logger = logging.getLogger("youtube_bridge")


class ClosingManagerMixin:
    def _list_unhandled_super_chats_for_closing(
        self,
        session_id: str,
        *,
        batch_size: int = 100,
    ) -> list[dict[str, Any]]:
        batch_size = max(1, min(int(batch_size or 100), 500))
        offset = 0
        super_chats: list[dict[str, Any]] = []
        while True:
            batch = self.storage.list_super_chats(
                session_id,
                unhandled_only=True,
                limit=batch_size,
                offset=offset,
            )
            if not batch:
                break
            super_chats.extend(batch)
            if len(batch) < batch_size:
                break
            offset += len(batch)
        return super_chats

    def _closing_super_chat_credit_lines(self, super_chats: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for event in super_chats:
            author = str(event.get("author_display_name") or "匿名觀眾").strip() or "匿名觀眾"
            amount = str(event.get("amount_display_string") or "").strip()
            amount_text = f"{amount} " if amount else ""
            if self._is_public_live_event_displayable(event):
                summary = self._single_line(self._event_safe_text(event))[:120]
                suffix = f"：{summary}" if summary else "。"
            else:
                suffix = "（內容不公開）。"
            lines.append(f"感謝 {author} 的 {amount_text}SC{suffix}")
        return lines

    async def _finalize_for_duration(self, runtime: LiveRuntime, session: dict[str, Any]) -> None:
        if not runtime.running or runtime.status in {"closing", "ended"}:
            return
        finalized = await self._finalize_live_session(
            runtime,
            session,
            finalized_by="duration_finalize",
            closing_message="planned duration reached; closing live session",
            ended_message="planned duration reached",
        )
        try:
            await self._run_auto_finalize_archive_callback(
                runtime.session_id,
                finalized_by="duration_finalize",
                finalized=finalized,
            )
        except Exception as exc:
            logger.warning("auto finalize archive failed session_id=%s error=%s", runtime.session_id, exc)

    async def finalize_session(self, session_id: str) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        runtime = self._runtimes.get(session_id)
        if runtime is None:
            runtime = LiveRuntime(
                session_id=session_id,
                running=False,
                status=str(session.get("status") or "stopped"),
            )
            self._runtimes[session_id] = runtime
        return await self._finalize_live_session(
            runtime,
            session,
            finalized_by="manual_finalize",
            closing_message="manual finalize requested; closing live session",
            ended_message="manual finalize requested",
        )

    async def _finalize_live_session(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        *,
        finalized_by: str,
        closing_message: str,
        ended_message: str,
    ) -> dict[str, Any]:
        if runtime.status == "ended" and session.get("status") == "ended":
            director_metadata = self.storage.get_director_state(runtime.session_id).get("metadata") or {}
            return {
                **(self.storage.get_session(runtime.session_id) or session),
                "runtime_status": self.get_status(runtime.session_id),
                "closing_super_chat_thanks": director_metadata.get("closing_super_chat_thanks"),
                "closing_safety_resolution": director_metadata.get("closing_safety_resolution"),
            }
        runtime.status = "closing"
        runtime.running = False
        self.storage.update_session_fields(
            runtime.session_id,
            status="closing",
            auto_inject=False,
            auto_test_events_enabled=False,
        )
        await self._stop_runtime_background_tasks_for_closing(runtime)
        await self._broadcast(
            runtime.session_id,
            {
                "type": "status",
                "status": "closing",
                "message": closing_message,
            },
        )
        await self._interrupt_active_generation_for_closing(runtime)
        safety_closing_result = await self._resolve_pending_safety_for_closing(runtime.session_id)
        closing_result = None
        if session.get("auto_sc_thanks_on_finalize", True):
            pending_super_chats = self._list_unhandled_super_chats_for_closing(runtime.session_id, batch_size=500)
            if not pending_super_chats:
                closing_result = {
                    "status": "skipped",
                    "reason": "no_unhandled_super_chats",
                    "super_chat_count": 0,
                }
            else:
                try:
                    closing_result = await asyncio.wait_for(
                        self.run_closing_super_chat_thanks(runtime.session_id),
                        timeout=45,
                    )
                except asyncio.TimeoutError:
                    closing_result = await self._complete_closing_super_chat_thanks_fallback(
                        runtime.session_id,
                        reason="timeout",
                    )
                except Exception as exc:
                    logger.warning("closing super chat thanks failed session_id=%s error=%s", runtime.session_id, exc)
                    closing_result = {"status": "failed", "error": str(exc)[:500]}
        finalized_at = datetime.now().isoformat()
        runtime.status = "ended"
        self.storage.finalize_incomplete_interactions(
            runtime.session_id,
            status="interrupted",
            reason="live_session_ended",
            metadata={"finalized_by": finalized_by},
        )
        self.storage.update_session_summary_state(
            runtime.session_id,
            summary_status=session.get("summary_status") or "pending",
            summary_error=session.get("summary_error", ""),
            finalized_at=finalized_at,
        )
        self.storage.update_session_fields(runtime.session_id, status="ended")
        director_state = self.storage.update_director_state(
            runtime.session_id,
            director_enabled=False,
            status="ended",
            consecutive_ai_turns=0,
            metadata={
                "finalized_by": finalized_by,
                "closing_super_chat_thanks": closing_result,
                "closing_safety_resolution": safety_closing_result,
            },
        )
        await self._broadcast(runtime.session_id, {"type": "director_state", "director": director_state})
        await self._broadcast(
            runtime.session_id,
            {
                "type": "status",
                "status": "ended",
                "message": ended_message,
                "finalized_at": finalized_at,
                "closing_super_chat_thanks": closing_result,
                "closing_safety_resolution": safety_closing_result,
            },
        )
        return {
            **(self.storage.get_session(runtime.session_id) or session),
            "runtime_status": self.get_status(runtime.session_id),
            "closing_super_chat_thanks": closing_result,
            "closing_safety_resolution": safety_closing_result,
        }

    async def _complete_closing_super_chat_thanks_fallback(
        self,
        session_id: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            return {"status": "failed", "error": "live session 不存在", "super_chat_count": 0}
        super_chats = self._list_unhandled_super_chats_for_closing(session_id, batch_size=500)
        if not super_chats:
            return {"status": "skipped", "reason": "no_unhandled_super_chats", "super_chat_count": 0}

        credit_lines = self._closing_super_chat_credit_lines(super_chats)
        reply_text = "感謝本場 Super Chat 支持。\n" + "\n".join(credit_lines)
        target_session_id = str(session.get("target_memoria_session_id") or "")
        message_result: dict[str, Any] = {}
        if target_session_id:
            client = self._memoria_client()
            add_system_event = getattr(client, "add_system_event", None)
            if callable(add_system_event):
                try:
                    message_result = await asyncio.to_thread(
                        add_system_event,
                        session_id=target_session_id,
                        content=reply_text,
                        debug_info={
                            "event_type": "youtube_live_closing_super_chat_fallback",
                            "source_session_id": session_id,
                            "reason": reason,
                            "super_chat_count": len(super_chats),
                        },
                    )
                except Exception as exc:
                    message_result = {"error": str(exc)[:300]}

        state = self.storage.get_director_state(session_id)
        decision = {
            "action": "closing_super_chat_thanks",
            "reason": f"直播收尾 fallback：{reason}",
            "current_topic": state.get("current_topic") or session.get("director_guidance") or "直播收尾",
        }
        interaction = self.storage.create_interaction({
            "session_id": session_id,
            "source": "director",
            "priority": 50,
            "status": "completed",
            "event_ids": [],
            "memoria_session_id": target_session_id,
            "character_ids": session.get("character_ids", []),
            "content": "直播即將收尾，感謝本場 Super Chat 支持。",
            "reply_text": reply_text,
            "completed_at": datetime.now().isoformat(),
            "metadata": {
                "decision": decision,
                "fallback": True,
                "fallback_reason": reason,
                "result_message_id": message_result.get("message_id"),
                "system_event_error": message_result.get("error", ""),
            },
        })
        marked = self.storage.mark_super_chats_handled_in_closing(
            session_id,
            [int(event["id"]) for event in super_chats],
        )
        await self._broadcast(session_id, {
            "type": "closing_super_chat_thanks_completed",
            "session_id": session_id,
            "marked": marked,
            "interaction": interaction,
            "fallback": True,
        })
        return {
            "status": "completed_by_timeout" if reason == "timeout" else "completed_by_fallback",
            "super_chat_count": len(super_chats),
            "marked": marked,
            "interaction": interaction,
            "message_result": message_result,
        }

    async def _resolve_pending_safety_for_closing(
        self,
        session_id: str,
        *,
        timeout_seconds: float = 20.0,
        per_batch_timeout_seconds: float = 75.0,
        batch_limit: int = 10,
    ) -> dict[str, Any]:
        """Resolve last-minute pending events before final closing interactions.

        Auto-generated test events can arrive close to the planned end time. The
        live page must fail closed instead of leaving ended sessions with pending
        safety state that may later leak into summaries or audits.
        """
        initial_pending = self.storage.list_events_pending_safety(session_id, limit=500)
        if not initial_pending:
            return {"status": "no_pending", "initial_pending_count": 0, "fallback_count": 0}

        classified_count = 0
        failed_count = 0
        batch_count = 0
        classify_error = ""
        loop = asyncio.get_running_loop()
        closing_batch_limit = min(max(1, int(batch_limit or 10)), SAFETY_CLASSIFIER_BATCH_LIMIT)
        expected_batches = max(1, (len(initial_pending) + closing_batch_limit - 1) // closing_batch_limit)
        per_batch_timeout = max(1.0, float(per_batch_timeout_seconds))
        total_timeout = max(1.0, float(timeout_seconds), expected_batches * per_batch_timeout)
        deadline = loop.time() + total_timeout
        while self.storage.list_events_pending_safety(session_id, limit=1):
            remaining_seconds = deadline - loop.time()
            if remaining_seconds <= 0:
                classify_error = "timeout"
                break
            try:
                classify_result = await asyncio.wait_for(
                    self.classify_pending_events(
                        session_id,
                        limit=closing_batch_limit,
                    ),
                    timeout=max(0.1, min(per_batch_timeout, remaining_seconds)),
                )
            except asyncio.TimeoutError:
                classify_error = "timeout"
                break
            except Exception as exc:
                classify_error = str(exc)[:300]
                break
            batch_count += 1
            classified_count += int(classify_result.get("classified_count") or 0)
            failed_count += int(classify_result.get("failed_count") or 0)
            if classify_result.get("error"):
                classify_error = str(classify_result.get("error") or "")[:300]
                break

        fallback_events: list[dict[str, Any]] = []
        remaining = self.storage.list_events_pending_safety(session_id, limit=500)
        for event in remaining:
            updated = self.storage.update_event_safety(
                int(event["id"]),
                status="failed",
                label="unclassified",
                safe_message_text="安全檢查未完成，暫不顯示原始留言。",
                safety_summary="直播收尾前安全檢查未完成，已採用 fail-closed 處理。",
                reason=classify_error or "closing fail-closed",
                confidence=0.0,
            )
            if updated:
                public_event = self._public_event(updated)
                fallback_events.append(public_event)
                await self._broadcast(session_id, {"type": "safety_classified", "event": public_event})

        status = "completed"
        if classify_error:
            status = "fallback_after_error"
        elif fallback_events:
            status = "fallback_after_partial"
        return {
            "status": status,
            "initial_pending_count": len(initial_pending),
            "classified_count": classified_count,
            "failed_count": failed_count,
            "fallback_count": len(fallback_events),
            "batch_count": batch_count,
            "error": classify_error,
        }

    async def _interrupt_active_generation_for_closing(
        self,
        runtime: LiveRuntime,
        *,
        timeout_seconds: float = 1.0,
    ) -> list[dict[str, Any]]:
        active = self.storage.get_active_interaction(runtime.session_id)
        if not active:
            return []

        interrupted = self.storage.request_interrupt(
            runtime.session_id,
            reason="live_session_closing",
        )
        for interaction in interrupted:
            cancel_event = runtime.cancel_events.get(str(interaction.get("job_id") or ""))
            if cancel_event:
                cancel_event.set()
            await self._broadcast(
                runtime.session_id,
                {"type": "interaction_interrupted", "interaction": interaction},
            )

        deadline = datetime.now() + timedelta(seconds=max(0.1, timeout_seconds))
        while datetime.now() < deadline:
            if not self.storage.get_active_interaction(runtime.session_id):
                return interrupted
            await asyncio.sleep(0.1)

        finalized = self.storage.finalize_incomplete_interactions(
            runtime.session_id,
            status="interrupted",
            reason="live_session_closing",
            metadata={
                "finalized_by": "duration_closing",
                "forced_before_closing_thanks": True,
            },
        )
        for interaction in finalized:
            await self._broadcast(
                runtime.session_id,
                {"type": "interaction_interrupted", "interaction": interaction},
            )
        return finalized or interrupted

    async def run_closing_super_chat_thanks(self, session_id: str) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        if not session.get("auto_sc_thanks_on_finalize", True):
            return {"status": "skipped", "reason": "auto_sc_thanks_disabled", "super_chat_count": 0}
        super_chats = self._list_unhandled_super_chats_for_closing(session_id, batch_size=100)
        if not super_chats:
            return {"status": "skipped", "reason": "no_unhandled_super_chats", "super_chat_count": 0}
        credit_lines = self._closing_super_chat_credit_lines(super_chats)
        closing_instruction = (
            "請像片尾名單一樣逐一點名所有 SC，每則獨立一句，語氣接近「感謝 XXX 的 SC」。"
            "可以簡短帶過乾淨留言內容；不適合公開回覆的內容只感謝支持，不要重述原文。"
            "不可只挑高 tier、不可只挑部分留言、不可省略名單。"
        )
        state = self.storage.get_director_state(session_id)
        decision = {
            "action": "closing_super_chat_thanks",
            "reason": "直播收尾前感謝本場 Super Chat，並避免逐字重述可疑內容。",
            "prompt": (
                "直播即將收尾，請感謝本場 Super Chat 支持。\n"
                f"{closing_instruction}\n\n"
                "本場 SC 片尾名單：\n" + "\n".join(f"- {line}" for line in credit_lines)
            ),
            "current_topic": state.get("current_topic") or session.get("director_guidance") or "直播收尾",
        }
        result = await self._send_director_turn(session, state, decision)
        marked = self.storage.mark_super_chats_handled_in_closing(
            session_id,
            [int(event["id"]) for event in super_chats],
        )
        await self._broadcast(session_id, {
            "type": "closing_super_chat_thanks_completed",
            "session_id": session_id,
            "marked": marked,
            "interaction": result.get("interaction"),
        })
        return {
            "status": "completed",
            "super_chat_count": len(super_chats),
            "marked": marked,
            "interaction": result.get("interaction"),
        }
