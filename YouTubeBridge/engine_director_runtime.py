"""YouTubeBridge director runtime mixin。"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime
from typing import Any

from bridge_runtime import LiveRuntime
from memoria_client import GenerationInterrupted


logger = logging.getLogger("youtube_bridge")


class DirectorRuntimeManagerMixin:
    async def start_director(
        self,
        session_id: str,
        *,
        idle_seconds: int = 60,
        guidance: str = "",
        kickoff: bool = False,
    ) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        guidance = str(guidance or "").strip()
        guidance_changed = bool(guidance and guidance != str(session.get("director_guidance") or "").strip())
        if guidance:
            self.storage.update_session_fields(session_id, director_guidance=guidance[:2000])
        state_fields: dict[str, Any] = {
            "director_enabled": True,
            "idle_seconds": max(10, min(int(idle_seconds or 60), 3600)),
            "status": "running",
        }
        if guidance_changed:
            state_fields["consecutive_ai_turns"] = 0
            state_fields["metadata"] = {
                "guidance_updated_at": datetime.now().isoformat(),
                "guidance_reset_turn_limit": True,
            }
        state = self.storage.update_director_state(session_id, **state_fields)
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id))
        if runtime.running and (not runtime.director_task or runtime.director_task.done()):
            runtime.director_task = asyncio.create_task(self._director_loop(runtime))
        if kickoff and runtime.running and (not runtime.director_kickoff_task or runtime.director_kickoff_task.done()):
            runtime.director_kickoff_task = asyncio.create_task(self._director_kickoff(runtime))
        await self._broadcast(session_id, {"type": "director_state", "director": state})
        return state

    async def stop_director(self, session_id: str) -> dict[str, Any]:
        runtime = self._runtimes.get(session_id)
        if runtime and runtime.director_task:
            runtime.director_task.cancel()
            try:
                await runtime.director_task
            except asyncio.CancelledError:
                pass
            runtime.director_task = None
        if runtime and runtime.director_kickoff_task:
            runtime.director_kickoff_task.cancel()
            try:
                await runtime.director_kickoff_task
            except asyncio.CancelledError:
                pass
            runtime.director_kickoff_task = None
        state = self.storage.update_director_state(session_id, director_enabled=False, status="stopped")
        await self._broadcast(session_id, {"type": "director_state", "director": state})
        return state

    async def _director_kickoff(self, runtime: LiveRuntime) -> None:
        try:
            session = self.storage.get_session(runtime.session_id)
            state = self.storage.get_director_state(runtime.session_id)
            if not runtime.running or not session or not state.get("director_enabled"):
                return
            if self.storage.get_active_interaction(runtime.session_id):
                next_state = self.storage.update_director_state(runtime.session_id, status="waiting_active_interaction")
                await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
                return
            decision = self._director_opening_decision(session, state)
            opening_state = self.storage.update_director_state(
                runtime.session_id,
                status="opening",
                metadata={"opening_decision": decision},
            )
            await self._broadcast(runtime.session_id, {"type": "director_state", "director": opening_state})
            result = await self._send_director_turn(session, state, decision)
            final_decision = decision
            final_result = result
            sent_turns = 1
            post_opening_decision: dict[str, Any] | None = None
            if runtime.running and self.storage.list_session_topic_pack_entries(runtime.session_id, limit=1):
                refreshed_session = self.storage.get_session(runtime.session_id) or session
                refreshed_state = self.storage.get_director_state(runtime.session_id)
                if not self.storage.get_active_interaction(runtime.session_id):
                    post_opening_decision = self._director_post_opening_topic_decision(
                        refreshed_session,
                        refreshed_state,
                    )
                    post_opening_state = self.storage.update_director_state(
                        runtime.session_id,
                        status="post_opening_topic_anchor",
                        metadata={"post_opening_decision": post_opening_decision},
                    )
                    await self._broadcast(
                        runtime.session_id,
                        {"type": "director_state", "director": post_opening_state},
                    )
                    final_result = await self._send_director_turn(
                        refreshed_session,
                        post_opening_state,
                        post_opening_decision,
                    )
                    final_decision = post_opening_decision
                    sent_turns += 1
            next_state = self.storage.update_director_state(
                runtime.session_id,
                status="running",
                last_director_action_at=datetime.now().isoformat(),
                consecutive_ai_turns=int(state.get("consecutive_ai_turns", 0) or 0) + sent_turns,
                current_topic=str(final_decision.get("current_topic") or state.get("current_topic") or ""),
                metadata={
                    "last_decision": final_decision,
                    "last_result_job_id": final_result.get("interaction", {}).get("job_id", ""),
                    "opening_decision": decision,
                    "post_opening_decision": post_opening_decision,
                    "chat_batches_since_anchor": 0,
                    "program_segment": self._program_segment_after_turn(session, state, final_decision),
                },
            )
            await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("YouTube director kickoff error session_id=%s error=%s", runtime.session_id, exc, exc_info=True)
            state = self.storage.update_director_state(runtime.session_id, status="error", metadata={"last_error": str(exc)})
            await self._broadcast(runtime.session_id, {"type": "director_state", "director": state})
            await self._broadcast(runtime.session_id, {"type": "director_error", "message": str(exc)})

    async def _director_loop(self, runtime: LiveRuntime) -> None:
        while runtime.running:
            try:
                state = self.storage.get_director_state(runtime.session_id)
                if not state.get("director_enabled"):
                    return
                idle_seconds = max(10, min(int(state.get("idle_seconds", 60) or 60), 3600))
                session = self.storage.get_session(runtime.session_id)
                if not session:
                    return
                if runtime.status == "closing" or session.get("status") == "closing":
                    await asyncio.sleep(1.0)
                    continue
                if self._duration_reached(session):
                    await self._finalize_for_duration(runtime, session)
                    return
                pending = [
                    event for event in self.storage.list_events(runtime.session_id, limit=5, uninjected_only=True)
                    if self._should_block_director_for_pending_inject(event)
                ]
                if pending:
                    latest = max(int(event["id"]) for event in pending)
                    next_state = self.storage.update_director_state(
                        runtime.session_id,
                        last_seen_event_id=latest,
                        status="pending_chat_seen",
                    )
                    await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
                    await asyncio.sleep(1.0)
                    continue
                if self.storage.get_active_interaction(runtime.session_id):
                    next_state = self.storage.update_director_state(
                        runtime.session_id,
                        status="waiting_active_interaction",
                    )
                    await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
                    await asyncio.sleep(1.0)
                    continue
                if self._director_should_pause_for_turn_limit(state, idle_seconds, session):
                    update_fields = {"status": "turn_limit_wait"}
                    if not state.get("last_director_action_at"):
                        update_fields["last_director_action_at"] = datetime.now().isoformat()
                    next_state = self.storage.update_director_state(runtime.session_id, **update_fields)
                    await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
                    await asyncio.sleep(1.0)
                    continue
                if self._director_topic_turn_limit_reached(session, state):
                    state = self.storage.update_director_state(
                        runtime.session_id,
                        status="turn_limit_released",
                        consecutive_ai_turns=0,
                    )
                    await self._broadcast(runtime.session_id, {"type": "director_state", "director": state})

                last_action_at = self._parse_iso_datetime(state.get("last_director_action_at"))
                if last_action_at:
                    remaining_seconds = idle_seconds - (datetime.now() - last_action_at).total_seconds()
                    if remaining_seconds > 0:
                        await asyncio.sleep(min(1.0, max(0.2, remaining_seconds)))
                        continue
                elif runtime.director_kickoff_task and not runtime.director_kickoff_task.done():
                    await asyncio.sleep(1.0)
                    continue

                decision = await asyncio.to_thread(self._director_decision, session, state)
                action = str(decision.get("action") or "wait").strip()
                if action == "closing_super_chat_thanks":
                    decision = self._director_idle_continue_decision(session, state)
                    decision["reason"] = (
                        "一般導播決策不得提前進入 Super Chat 收尾；"
                        "SC 感謝只允許在直播時間到達後由 finalize 流程執行。"
                    )
                    action = str(decision.get("action") or "continue_topic").strip()
                if self._director_decision_is_early_live_closing(decision):
                    decision = self._director_idle_continue_decision(session, state)
                    decision["reason"] = (
                        "預定直播時間尚未到達，阻止導播因時間進度提前 recap/close；"
                        "正式收尾只由 duration finalize 流程執行。"
                    )
                    action = str(decision.get("action") or "continue_topic").strip()
                chat_batches = int((state.get("metadata") or {}).get("chat_batches_since_anchor", 0) or 0)
                max_chat_batches = max(1, int(session.get("director_max_chat_batches_before_anchor", 2) or 2))
                if chat_batches >= max_chat_batches and action in {"wait", "reply_chat_batch", "reply_super_chat_batch", "defer_offtopic"}:
                    decision = self._director_anchor_decision(session, state)
                    action = str(decision.get("action") or "anchor_to_topic").strip()
                if action == "wait" and self._director_should_force_guidance_turn(session, state):
                    decision = self._director_guidance_transition_decision(session, state)
                    action = str(decision.get("action") or "transition_topic").strip()
                if action == "wait" and self._director_should_force_idle_turn(state, session):
                    decision = self._director_idle_continue_decision(session, state)
                    action = str(decision.get("action") or "continue_topic").strip()
                if action == "wait":
                    next_state = self.storage.update_director_state(
                        runtime.session_id,
                        status="waiting",
                        last_director_action_at=datetime.now().isoformat(),
                        metadata={"last_decision": decision},
                    )
                    await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
                    continue
                result = await self._send_director_turn(session, state, decision)
                next_count = int(state.get("consecutive_ai_turns", 0) or 0) + 1
                next_state = self.storage.update_director_state(
                    runtime.session_id,
                    status="running",
                    last_director_action_at=datetime.now().isoformat(),
                    consecutive_ai_turns=next_count,
                    current_topic=str(decision.get("current_topic") or state.get("current_topic") or ""),
                    metadata={
                        "last_decision": decision,
                        "last_result_job_id": result.get("interaction", {}).get("job_id", ""),
                        "chat_batches_since_anchor": 0,
                        "program_segment": self._program_segment_after_turn(session, state, decision),
                    },
                )
                await self._broadcast(runtime.session_id, {"type": "director_state", "director": next_state})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("YouTube director error session_id=%s error=%s", runtime.session_id, exc, exc_info=True)
                state = self.storage.update_director_state(runtime.session_id, status="error", metadata={"last_error": str(exc)})
                await self._broadcast(runtime.session_id, {"type": "director_state", "director": state})
                await self._broadcast(runtime.session_id, {"type": "director_error", "message": str(exc)})
                await asyncio.sleep(15)

    async def _send_director_turn(
        self,
        session: dict[str, Any],
        state: dict[str, Any],
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        session_id = session["session_id"]
        target_session_id = session.get("target_memoria_session_id", "")
        target_character_ids = session.get("character_ids", [])
        action = str(decision.get("action") or "continue_topic")
        prompt = str(decision.get("prompt") or "").strip()
        public_prompt = self._public_director_prompt(action, session, state)
        if action == "opening":
            public_prompt = self._public_director_opening_prompt(session, state)
        public_topic = self._public_director_topic(session, state)
        elapsed_minutes, elapsed_percent, remaining_minutes = self._session_elapsed(session)
        group_turn_limit = self._director_group_turn_limit_for_action(session, action)
        if not prompt:
            prompt = f"目前適合執行 {action}，請自然延續直播對話，不要提到幕後流程。"
        topic_context = ""
        if action == "opening":
            topic_context = self._topic_pack_sequence_preview_context_for_session(session_id)
        else:
            topic_context = self._topic_pack_sequence_context_for_session(
                session_id,
                "\n".join([
                    str(public_topic or ""),
                    str(public_prompt or ""),
                    str(state.get("current_topic") or ""),
                ]),
                usage_source="director",
            )
        context_parts = [
            f"直播流程 action={action}",
            f"直播進度：{elapsed_percent}%（已 {elapsed_minutes} 分鐘，剩餘約 {remaining_minutes} 分鐘）",
            f"處理提示：{public_prompt}",
        ]
        if action not in {"reply_chat_batch", "reply_super_chat_batch"}:
            context_parts.append(
                "直播互動規則：目前不是回應留言批次；請讓角色彼此接話、補充、反駁或提出下一個切入點，不要把問題丟回聊天室。"
            )
        if action == "closing_super_chat_thanks" and prompt:
            context_parts.append("本場 Super Chat 參考內容：\n" + prompt[:3000])
        if action == "opening":
            opening_intro_context = self._opening_intro_context_for_session(session)
            if opening_intro_context:
                context_parts.append(opening_intro_context)
            if topic_context:
                context_parts.append(
                    "開場後話題導入資料：以下 <topic_pack_fact_cards> 只能在固定開場白與自我介紹完成後使用；"
                    "請用其中一個具體切入點帶入討論，不得自行捏造資料卡未提供的作品、集數或事件。"
                )
        if action == "post_opening_topic_anchor":
            if topic_context:
                context_parts.append(
                    "話題導入規則：開場已完成，接下來必須優先使用下方 <topic_pack_fact_cards> "
                    "中的具體作品、集數、事件或觀點，不得自行捏造未提供的內容。"
                )
            else:
                context_parts.append(
                    "話題導入規則：目前沒有可用話題資料卡；請延續開場與既有直播方向，不得自行捏造具體作品、集數或事件。"
                )
        live_hosting = self._live_hosting_context_for_session(session, state)
        if live_hosting:
            context_parts.append(self._live_hosting_context_text(live_hosting))
        if topic_context:
            context_parts.append(topic_context)
        external_context = {
            "source": "youtube_live_director",
            "source_session_id": session_id,
            "connector_id": session.get("connector_id", ""),
            "video_id": session.get("video_id", ""),
            "live_chat_id": session.get("live_chat_id", ""),
            "group_turn_limit": group_turn_limit,
            "context_text": "\n".join(context_parts),
            "event_ids": [],
            "visible_events": [],
            "max_chars": 4000 if action == "closing_super_chat_thanks" else 2500,
            "summary": {
                "source": "youtube_live_director",
                "source_session_id": session_id,
                "event_count": 0,
                "action": action,
                "group_turn_limit": group_turn_limit,
            },
        }
        if live_hosting:
            external_context["live_hosting"] = live_hosting
        external_context = self._attach_live_persona_overrides(session, external_context)
        interaction = self.storage.create_interaction(
            {
                "session_id": session_id,
                "source": "director",
                "priority": 50,
                "status": "queued",
                "event_ids": [],
                "memoria_session_id": target_session_id,
                "character_ids": target_character_ids,
                "content": public_prompt,
                "metadata": {"decision": decision},
            }
        )
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id))
        claimed = await self._claim_interaction_for_execution(runtime, interaction)
        if not claimed or claimed.get("status") != "running":
            return {"interaction": claimed or interaction, "memoria_result": {}}
        interaction = claimed
        cancel_event = threading.Event()
        runtime.cancel_events[interaction["job_id"]] = cancel_event
        loop = asyncio.get_running_loop()

        def should_cancel() -> bool:
            current = self.storage.get_interaction(interaction["job_id"])
            return cancel_event.is_set() or bool(current and current.get("status") == "interrupt_requested")

        def on_stream_result(event: dict[str, Any]) -> None:
            self._broadcast_stream_chat_message(loop, session_id, event, source="director")

        try:
            result = await asyncio.to_thread(
                self._memoria_client().chat_stream_sync,
                content=public_prompt,
                display_content=self._director_display_content(action),
                session_id=target_session_id,
                character_ids=target_character_ids,
                external_context=external_context,
                should_cancel=should_cancel,
                cancel_event=cancel_event,
                on_result=on_stream_result,
            )
        except GenerationInterrupted:
            updated = self.storage.update_interaction(
                interaction["job_id"],
                status="interrupted",
                closure_text="先停在這裡，剛剛聊天室有新的問題，我們切過去看。",
                completed_at=datetime.now().isoformat(),
                metadata={"discarded": True},
            )
            await self._broadcast(session_id, {"type": "interaction_interrupted", "interaction": updated})
            return {"interaction": updated, "memoria_result": {}}
        except Exception as exc:
            current = self.storage.get_interaction(interaction["job_id"])
            was_interrupted = cancel_event.is_set() or bool(current and current.get("status") == "interrupt_requested")
            reason = self._normalized_interrupt_reason(current, exc)
            updated = self.storage.update_interaction(
                interaction["job_id"],
                status="interrupted" if was_interrupted else "failed",
                reason=reason,
                closure_text="先停在這裡，剛剛聊天室有新的問題，我們切過去看。" if was_interrupted else "",
                completed_at=datetime.now().isoformat(),
                metadata={
                    "discarded": was_interrupted,
                    "error": str(exc)[:500],
                    "normalized_reason": reason,
                },
            )
            await self._broadcast(
                session_id,
                {
                    "type": "interaction_interrupted" if was_interrupted else "interaction_failed",
                    "interaction": updated,
                },
            )
            if was_interrupted:
                return {"interaction": updated, "memoria_result": {}}
            raise
        finally:
            runtime.cancel_events.pop(interaction["job_id"], None)

        current_after = self.storage.get_interaction(interaction["job_id"])
        interrupted_after_provider = bool(
            current_after and current_after.get("status") in {"interrupt_requested", "interrupted", "discarded"}
        )
        if interrupted_after_provider:
            interaction_status = "discarded"
            closure_text = "先停在這裡，剛剛聊天室有新的問題，我們切過去看。"
            reply_text = ""
        else:
            interaction_status = "completed"
            closure_text = ""
            reply_text = str(result.get("reply") or "")
        updated = self.storage.update_interaction(
            interaction["job_id"],
            status=interaction_status,
            reply_text=reply_text,
            closure_text=closure_text,
            memoria_session_id=str(result.get("session_id") or target_session_id),
            completed_at=datetime.now().isoformat(),
            metadata={
                "result_message_id": result.get("message_id"),
                "discarded_after_provider_return": interrupted_after_provider,
            },
        )
        result_session_id = str(result.get("session_id") or "")
        if result_session_id and result_session_id != target_session_id:
            self.storage.update_session_fields(session_id, target_memoria_session_id=result_session_id)
        await self._broadcast(session_id, {
            "type": "interaction_completed",
            "interaction": updated,
            "memoria_session_id": result.get("session_id") or target_session_id,
            "source": "director",
        })
        await self._broadcast(session_id, {
            "type": "director_injected",
            "interaction": updated,
            "memoria_session_id": result.get("session_id") or target_session_id,
        })
        return {"interaction": updated, "memoria_result": result}
