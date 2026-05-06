"""YouTubeBridge test event runtime mixin。"""
from __future__ import annotations

import asyncio
import logging
import random
import uuid
from datetime import datetime
from typing import Any

import engine_test_events
from bridge_contracts import TEST_COMMENT_SCHEMA
from bridge_runtime import LiveRuntime
from storage_event_utils import infer_super_chat_tier


logger = logging.getLogger("youtube_bridge")


class TestRuntimeManagerMixin:
    async def _auto_test_event_loop(self, runtime: LiveRuntime) -> None:
        await self._broadcast(runtime.session_id, {"type": "test_event_auto_started", "session_id": runtime.session_id})
        while runtime.running:
            session = self.storage.get_session(runtime.session_id)
            if not session or not session.get("auto_test_events_enabled"):
                return
            min_seconds = max(1, int(session.get("test_event_min_seconds", 20) or 20))
            max_seconds = max(min_seconds, int(session.get("test_event_max_seconds", 45) or 45))
            try:
                await asyncio.sleep(random.uniform(min_seconds, max_seconds))
                if not runtime.running:
                    return
                session = self.storage.get_session(runtime.session_id)
                if not session or not session.get("auto_test_events_enabled") or session.get("status") != "running":
                    continue
                result = await self.generate_test_events(
                    runtime.session_id,
                    count=int(session.get("test_event_count_per_tick", 3) or 3),
                    topic_hint=session.get("director_guidance", ""),
                    use_llm=bool(session.get("test_event_use_llm", True)),
                    super_chat_count=int(session.get("test_super_chat_count_per_tick", 0) or 0),
                    include_malicious_sc=bool(session.get("test_malicious_sc_enabled", False)),
                    sc_burst=bool(session.get("test_sc_burst_mode", False)),
                )
                runtime.last_auto_test_event_at = datetime.now().isoformat()
                runtime.last_auto_test_event_error = None
                await self._broadcast(runtime.session_id, {
                    "type": "test_events_auto_generated",
                    "session_id": runtime.session_id,
                    "result": result,
                })
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                runtime.last_auto_test_event_error = str(exc)
                logger.error("auto test event error session_id=%s error=%s", runtime.session_id, exc, exc_info=True)
                await self._broadcast(runtime.session_id, {
                    "type": "test_event_auto_error",
                    "message": str(exc),
                })
                await asyncio.sleep(5)

    async def start_auto_test_events(self, session_id: str) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        self.storage.update_session_fields(session_id, auto_test_events_enabled=True)
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id))
        if runtime.running and (not runtime.test_event_task or runtime.test_event_task.done()):
            runtime.test_event_task = asyncio.create_task(self._auto_test_event_loop(runtime))
        await self._broadcast(session_id, {"type": "test_event_auto_started", "session_id": session_id})
        return self.get_status(session_id)

    async def stop_auto_test_events(self, session_id: str) -> dict[str, Any]:
        self.storage.update_session_fields(session_id, auto_test_events_enabled=False)
        runtime = self._runtimes.get(session_id)
        if runtime and runtime.test_event_task:
            runtime.test_event_task.cancel()
            try:
                await runtime.test_event_task
            except asyncio.CancelledError:
                pass
            runtime.test_event_task = None
        await self._broadcast(session_id, {"type": "test_event_auto_stopped", "session_id": session_id})
        return self.get_status(session_id)

    async def generate_test_events(
        self,
        session_id: str,
        *,
        count: int = 5,
        topic_hint: str = "",
        use_llm: bool = True,
        super_chat_count: int = 0,
        include_malicious_sc: bool = False,
        sc_burst: bool = False,
    ) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        count = max(1, min(int(count or 5), 30))
        super_chat_count = max(0, min(int(super_chat_count or 0), 30))
        comments = await asyncio.to_thread(
            self._generate_test_comments,
            session,
            count,
            str(topic_hint or ""),
            bool(use_llm),
        )
        super_chat_comments = self._generate_test_super_chats(
            session,
            super_chat_count,
            str(topic_hint or ""),
            include_malicious_sc=include_malicious_sc,
            sc_burst=sc_burst,
        )
        saved_events: list[dict[str, Any]] = []
        recent_comment_texts = {
            str(event.get("message_text") or "").strip()
            for event in self.storage.list_events(session_id, limit=100)
            if event.get("priority_class") != "super_chat"
        }
        used_comment_texts = {text for text in recent_comment_texts if text}
        for comment in comments[:count]:
            text = str(comment.get("message_text") or "").replace("\r", " ").replace("\n", " ").strip()
            if not text:
                continue
            if text in used_comment_texts:
                text = self._variant_test_comment_text(text, len(used_comment_texts))
            used_comment_texts.add(text)
            author = str(comment.get("author_display_name") or "").strip() or random.choice(
                ["測試觀眾A", "路過觀眾", "debug民", "直播新手", "安靜觀眾"]
            )
            event = self.storage.save_event({
                "bridge_session_id": session_id,
                "connector_id": session["connector_id"],
                "video_id": session.get("video_id", ""),
                "live_chat_id": session.get("live_chat_id", ""),
                "youtube_message_id": f"test-{uuid.uuid4().hex}",
                "message_type": "testMessageEvent",
                "author_channel_id": f"test-{uuid.uuid4().hex[:12]}",
                "author_display_name": author[:80],
                "message_text": text[:500],
                "published_at": datetime.now().isoformat(),
                "received_at": datetime.now().isoformat(),
                "status": "active",
                "metadata": {
                    "source": "test_comment_generator",
                    "topic_hint": str(topic_hint or "")[:300],
                },
            })
            if event:
                saved_events.append(event)
                public_event = self._public_live_event(event)
                if public_event:
                    await self._broadcast(session_id, {"type": "youtube_live_event", "event": public_event})
        recent_super_chat_texts = {
            str(event.get("message_text") or "").strip()
            for event in self.storage.list_events(session_id, limit=100)
            if event.get("priority_class") == "super_chat"
        }
        used_super_chat_texts = {text for text in recent_super_chat_texts if text}
        for comment in super_chat_comments[:super_chat_count]:
            text = str(comment.get("message_text") or "").replace("\r", " ").replace("\n", " ").strip()
            if not text:
                continue
            if text in used_super_chat_texts:
                text = self._variant_test_super_chat_text(text, len(used_super_chat_texts))
            used_super_chat_texts.add(text)
            author = str(comment.get("author_display_name") or "SC觀眾").strip()
            amount_micros = int(comment.get("amount_micros", 150000000) or 150000000)
            sc_tier = infer_super_chat_tier(amount_micros, int(comment.get("sc_tier", 0) or 0))
            event = self.storage.save_event({
                "bridge_session_id": session_id,
                "connector_id": session["connector_id"],
                "video_id": session.get("video_id", ""),
                "live_chat_id": session.get("live_chat_id", ""),
                "youtube_message_id": f"test-sc-{uuid.uuid4().hex}",
                "message_type": "testSuperChatEvent",
                "author_channel_id": f"test-sc-{uuid.uuid4().hex[:12]}",
                "author_display_name": author[:80],
                "message_text": text[:500],
                "published_at": datetime.now().isoformat(),
                "received_at": datetime.now().isoformat(),
                "status": "active",
                "amount_display_string": str(comment.get("amount_display_string") or self._format_test_amount(amount_micros)),
                "currency": str(comment.get("currency") or "TWD"),
                "amount_micros": amount_micros,
                "sc_tier": sc_tier,
                "priority_class": "super_chat",
                "safety_label": str(comment.get("safety_label") or ""),
                "metadata": {
                    "source": "test_comment_generator",
                    "topic_hint": str(topic_hint or "")[:300],
                    "sc_burst": bool(sc_burst),
                    "include_malicious_sc": bool(include_malicious_sc),
                },
            })
            if event:
                saved_events.append(event)
                public_event = self._public_live_event(event)
                if public_event:
                    await self._broadcast(session_id, {"type": "youtube_live_event", "event": public_event})
                    await self._broadcast(session_id, {"type": "super_chat_received", "event": public_event})
        await self._broadcast(session_id, {
            "type": "test_events_generated",
            "session_id": session_id,
            "count": len(saved_events),
            "super_chat_count": len([event for event in saved_events if event.get("priority_class") == "super_chat"]),
        })
        return {
            "session_id": session_id,
            "generated": len(saved_events),
            "super_chat_generated": len([event for event in saved_events if event.get("priority_class") == "super_chat"]),
            "events": [
                public_event
                for event in saved_events
                if (public_event := self._public_live_event(event))
            ],
        }

    @staticmethod
    def _format_test_amount(amount_micros: int) -> str:
        return engine_test_events.format_test_amount(amount_micros)

    @staticmethod
    def _variant_test_comment_text(text: str, seed: int) -> str:
        return engine_test_events.variant_test_comment_text(text, seed)

    @staticmethod
    def _variant_test_super_chat_text(text: str, seed: int) -> str:
        return engine_test_events.variant_test_super_chat_text(text, seed)

    @classmethod
    def _generate_test_super_chats(
        cls,
        session: dict[str, Any],
        count: int,
        topic_hint: str,
        *,
        include_malicious_sc: bool,
        sc_burst: bool,
    ) -> list[dict[str, Any]]:
        return engine_test_events.generate_test_super_chats(
            session,
            count,
            topic_hint,
            include_malicious_sc=include_malicious_sc,
            sc_burst=sc_burst,
            public_test_topic=cls._public_test_topic,
            sanitize_test_comment_text=cls._sanitize_test_comment_text,
        )

    @staticmethod
    def _test_super_chat_malicious_flags(
        count: int,
        *,
        include_malicious_sc: bool,
        sc_burst: bool,
    ) -> list[bool]:
        return engine_test_events.test_super_chat_malicious_flags(
            count,
            include_malicious_sc=include_malicious_sc,
            sc_burst=sc_burst,
        )

    def _generate_test_comments(
        self,
        session: dict[str, Any],
        count: int,
        topic_hint: str,
        use_llm: bool,
    ) -> list[dict[str, str]]:
        recent_events = self.storage.list_events(session["session_id"], limit=20)
        recent_interactions = self.storage.list_interactions(session["session_id"], limit=12)
        public_topic = self._public_test_topic(session, topic_hint)
        event_lines = "\n".join(
            line
            for event in recent_events[-20:]
            if (line := self._test_comment_event_line(event))
        ) or "（無近期公開留言）"
        interaction_lines = "\n".join(
            line
            for item in reversed(recent_interactions)
            if (line := self._test_comment_interaction_line(item))
        ) or "（無近期公開互動）"
        if use_llm:
            try:
                result = self._memoria_client().generate_prompt_json(
                    prompt_key="youtube_live_test_comment_generator_prompt",
                    variables={
                        "session_title": session.get("display_name") or session["session_id"],
                        "director_guidance": public_topic or "（未設定）",
                        "topic_hint": public_topic or "（未設定）",
                        "count": str(count),
                        "recent_events": event_lines,
                        "recent_interactions": interaction_lines,
                    },
                    task_key="router",
                    temperature=0.7,
                    schema=TEST_COMMENT_SCHEMA,
                )
                raw_comments = result.get("comments") if isinstance(result, dict) else None
                comments = self._clean_test_comments(raw_comments, count)
                if comments:
                    return comments
            except Exception as exc:
                logger.warning("test comment LLM generation failed session_id=%s error=%s", session["session_id"], exc)
        return self._fallback_test_comments(session, count, topic_hint)

    @classmethod
    def _clean_test_comments(cls, raw_comments: Any, count: int) -> list[dict[str, str]]:
        return engine_test_events.clean_test_comments(
            raw_comments,
            count,
            sanitize_test_comment_text=cls._sanitize_test_comment_text,
        )

    @classmethod
    def _fallback_test_comments(cls, session: dict[str, Any], count: int, topic_hint: str) -> list[dict[str, str]]:
        return engine_test_events.fallback_test_comments(
            session,
            count,
            topic_hint,
            public_test_topic=cls._public_test_topic,
            sanitize_test_comment_text=cls._sanitize_test_comment_text,
        )
