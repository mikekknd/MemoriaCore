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
    async def _finalize_for_duration(self, runtime: LiveRuntime, session: dict[str, Any]) -> None:
        if not runtime.running or runtime.status in {"closing", "ended"}:
            return
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
                "message": "planned duration reached; closing live session",
            },
        )
        await self._interrupt_active_generation_for_closing(runtime)
        safety_closing_result = await self._resolve_pending_safety_for_closing(runtime.session_id)
        closing_result = None
        if session.get("auto_sc_thanks_on_finalize", True):
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
            metadata={"finalized_by": "duration_finalize"},
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
                "message": "planned duration reached",
                "finalized_at": finalized_at,
                "closing_super_chat_thanks": closing_result,
                "closing_safety_resolution": safety_closing_result,
            },
        )

    async def _complete_closing_super_chat_thanks_fallback(
        self,
        session_id: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            return {"status": "failed", "error": "live session 不存在", "super_chat_count": 0}
        super_chats = self.storage.list_super_chats(session_id, unhandled_only=True, limit=500)
        if not super_chats:
            return {"status": "skipped", "reason": "no_unhandled_super_chats", "super_chat_count": 0}

        authors = []
        for event in super_chats:
            author = str(event.get("author_display_name") or "").strip()
            if author and author not in authors:
                authors.append(author)
            if len(authors) >= 5:
                break
        representative = "、".join(authors)
        if len(super_chats) > len(authors):
            representative += f" 等 {len(super_chats)} 則" if representative else f"{len(super_chats)} 則"
        reply_text = (
            "感謝本場 Super Chat 支持。"
            f"本場共收到 {len(super_chats)} 則 SC"
            + (f"，包含 {representative}" if representative else "")
            + "；代表性問題已在直播中回應，其餘不適合公開回覆或重複內容已略過。"
        )
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
        super_chats = self.storage.list_super_chats(session_id, unhandled_only=True, limit=100)
        if not super_chats:
            return {"status": "skipped", "reason": "no_unhandled_super_chats", "super_chat_count": 0}
        clean_super_chats = [
            event for event in super_chats
            if self._is_public_live_event_displayable(event)
        ]
        safe_lines = [self._event_line(event).lstrip("- ") for event in clean_super_chats[:20]]
        if len(clean_super_chats) > 20:
            safe_lines.append(f"另有 {len(clean_super_chats) - 20} 則 SC 以分組方式感謝。")
        hidden_sc_count = max(0, len(super_chats) - len(clean_super_chats))
        if not safe_lines and hidden_sc_count:
            safe_lines.append("本場另有部分 SC 不適合公開逐條回覆，請概括感謝支持即可。")
        if len(super_chats) <= 8:
            closing_instruction = (
                "本場可公開逐條回覆的 SC 數量不多，請逐條感謝。每則最多 1 句，需包含暱稱與問題/支持內容的短摘要；"
                "不要逐字照抄留言。不適合公開回覆的內容不要提起。"
            )
        else:
            closing_instruction = (
                "本場可公開回覆的 SC 數量較多，請先點名高 tier 或代表性 SC，再按主題分組感謝；"
                "不要逐字念完全部留言。不適合公開回覆的內容不要提起。"
            )
        state = self.storage.get_director_state(session_id)
        decision = {
            "action": "closing_super_chat_thanks",
            "reason": "直播收尾前感謝本場 Super Chat，並避免逐字重述可疑內容。",
            "prompt": (
                "直播即將收尾，請感謝本場 Super Chat 支持。\n"
                f"{closing_instruction}\n\n"
                "本場 SC：\n" + "\n".join(f"- {line}" for line in safe_lines)
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
