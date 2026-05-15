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
    @staticmethod
    def _is_real_youtube_session(session: dict[str, Any] | None) -> bool:
        if not session:
            return False
        return bool(str(session.get("video_id") or "").strip() or str(session.get("live_chat_id") or "").strip())

    def _disable_test_events_for_real_youtube_session(self, session_id: str, session: dict[str, Any] | None = None) -> bool:
        session = session or self.storage.get_session(session_id)
        if not self._is_real_youtube_session(session):
            return False
        if session and session.get("auto_test_events_enabled"):
            self.storage.update_session_fields(session_id, auto_test_events_enabled=False)
        runtime = self._runtimes.get(session_id)
        current_task = asyncio.current_task()
        if runtime and runtime.test_event_task and runtime.test_event_task is not current_task and not runtime.test_event_task.done():
            runtime.test_event_task.cancel()
        if runtime:
            runtime.test_event_task = None
        return True

    def _ensure_test_events_allowed(self, session_id: str, session: dict[str, Any] | None = None) -> None:
        if self._disable_test_events_for_real_youtube_session(session_id, session):
            raise ValueError("真實 YouTube 直播不允許插入測試留言；請改用無 video_id 的測試直播。")

    async def _auto_test_event_loop(self, runtime: LiveRuntime) -> None:
        await self._broadcast(runtime.session_id, {"type": "test_event_auto_started", "session_id": runtime.session_id})
        while runtime.running:
            session = self.storage.get_session(runtime.session_id)
            if not session or not session.get("auto_test_events_enabled"):
                return
            if self._disable_test_events_for_real_youtube_session(runtime.session_id, session):
                await self._broadcast(runtime.session_id, {
                    "type": "test_event_auto_stopped",
                    "session_id": runtime.session_id,
                    "reason": "real_youtube_live",
                })
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
                if bool(session.get("test_event_use_llm", True)) and self.storage.get_active_interaction(runtime.session_id):
                    await asyncio.sleep(1.0)
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
        self._ensure_test_events_allowed(session_id, session)
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
        manual_events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        self._ensure_test_events_allowed(session_id, session)
        count = max(0, min(int(count or 0), 30))
        super_chat_count = max(0, min(int(super_chat_count or 0), 30))
        manual_items = self._clean_manual_test_events(manual_events)
        if not manual_items and count == 0 and super_chat_count == 0:
            count = 1
        llm_comment_pool: list[dict[str, str]] = []
        if bool(use_llm) and (count + super_chat_count) > 0:
            llm_comment_pool = await asyncio.to_thread(
                self._generate_test_comments,
                session,
                count + super_chat_count,
                str(topic_hint or ""),
                True,
            )
        comments: list[dict[str, str]] = []
        if count > 0:
            if bool(use_llm):
                comments = llm_comment_pool[:count]
            else:
                comments = await asyncio.to_thread(
                    self._generate_test_comments,
                    session,
                    count,
                    str(topic_hint or ""),
                    False,
                )
        super_chat_comments = (
            self._generate_llm_test_super_chats(
                session,
                super_chat_count,
                str(topic_hint or ""),
                include_malicious_sc,
                sc_burst,
                llm_comment_pool[count:count + super_chat_count],
            )
            if super_chat_count > 0 and bool(use_llm)
            else self._generate_test_super_chats(
                session,
                super_chat_count,
                str(topic_hint or ""),
                include_malicious_sc=include_malicious_sc,
                sc_burst=sc_burst,
            )
        )
        saved_events: list[dict[str, Any]] = []
        for item in manual_items:
            event = self._save_manual_test_event(session, item)
            if event:
                saved_events.append(event)
                public_event = self._public_live_event(event)
                if public_event:
                    await self._broadcast(session_id, {"type": "youtube_live_event", "event": public_event})
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
        saved_event_ids = [int(event["id"]) for event in saved_events if event.get("id")]
        runtime = self._runtimes.get(session_id)
        if saved_event_ids and runtime and runtime.running:
            self._schedule_pending_event_classification(runtime, limit=len(saved_event_ids))
        elif saved_event_ids and runtime and runtime.subscribers:
            try:
                await self.classify_event_ids_serialized(session_id, saved_event_ids)
            except Exception as exc:
                logger.warning("test event safety classification failed session_id=%s error=%s", session_id, exc)
        current_events = (
            self.storage.get_events_by_ids(session_id, saved_event_ids, limit=len(saved_event_ids))
            if saved_event_ids
            else []
        )
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
                for event in current_events
                if (public_event := self._public_live_event(event))
            ],
        }

    @staticmethod
    def _format_test_amount(amount_micros: int) -> str:
        return engine_test_events.format_test_amount(amount_micros)

    @staticmethod
    def _clean_manual_test_events(manual_events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for raw in manual_events or []:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("message_text") or "").replace("\r", " ").replace("\n", " ").strip()
            if not text:
                continue
            kind = "super" if str(raw.get("kind") or "comment") == "super" else "comment"
            author = str(raw.get("author_display_name") or "").replace("\r", " ").replace("\n", " ").strip()
            amount_display = str(raw.get("amount_display_string") or "").replace("\r", " ").replace("\n", " ").strip()
            try:
                amount_micros = int(raw.get("amount_micros", 0) or 0)
            except (TypeError, ValueError):
                amount_micros = 0
            items.append({
                "kind": kind,
                "author_display_name": (author or ("SC 測試帳號" if kind == "super" else "觀眾 測試帳號"))[:80],
                "message_text": text[:500],
                "amount_display_string": amount_display[:40],
                "amount_micros": max(0, amount_micros),
            })
            if len(items) >= 30:
                break
        return items

    def _save_manual_test_event(self, session: dict[str, Any], item: dict[str, Any]) -> dict[str, Any] | None:
        kind = str(item.get("kind") or "comment")
        is_super = kind == "super"
        amount_micros = int(item.get("amount_micros", 0) or 0)
        amount_display = str(item.get("amount_display_string") or "").strip()
        if is_super and amount_micros <= 0:
            amount_micros = 750000000 if "750" in amount_display else 75000000
        if is_super and not amount_display:
            amount_display = self._format_test_amount(amount_micros)
        now = datetime.now().isoformat()
        return self.storage.save_event({
            "bridge_session_id": session["session_id"],
            "connector_id": session["connector_id"],
            "video_id": session.get("video_id", ""),
            "live_chat_id": session.get("live_chat_id", ""),
            "youtube_message_id": f"studio-test-{'sc-' if is_super else ''}{uuid.uuid4().hex}",
            "message_type": "testSuperChatEvent" if is_super else "testMessageEvent",
            "author_channel_id": f"studio-test-{uuid.uuid4().hex[:12]}",
            "author_display_name": str(item.get("author_display_name") or ("SC 測試帳號" if is_super else "觀眾 測試帳號"))[:80],
            "message_text": str(item.get("message_text") or "")[:500],
            "published_at": now,
            "received_at": now,
            "status": "active",
            "amount_display_string": amount_display if is_super else "",
            "currency": "TWD" if is_super else "",
            "amount_micros": amount_micros if is_super else 0,
            "sc_tier": infer_super_chat_tier(amount_micros, 0) if is_super else 0,
            "priority_class": "super_chat" if is_super else "normal",
            "metadata": {
                "source": "studio_test_comment",
            },
        })

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

    def _generate_llm_test_super_chats(
        self,
        session: dict[str, Any],
        count: int,
        topic_hint: str,
        include_malicious_sc: bool,
        sc_burst: bool,
        comments: list[dict[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        comments = list(comments or [])
        if len(comments) < count:
            comments.extend(self._fallback_test_comments(session, count - len(comments), topic_hint))
        fallback = self._generate_test_super_chats(
            session,
            count,
            topic_hint,
            include_malicious_sc=include_malicious_sc,
            sc_burst=sc_burst,
        )
        super_chats: list[dict[str, Any]] = []
        for index in range(count):
            base = fallback[index] if index < len(fallback) else {}
            comment = comments[index] if index < len(comments) else {}
            is_malicious = bool(base.get("is_malicious_sample") or base.get("safety_label"))
            amount_micros = int(base.get("amount_micros", 0) or 0)
            if amount_micros <= 0:
                amount_micros = 75000000
            super_chats.append({
                "author_display_name": str(comment.get("author_display_name") or base.get("author_display_name") or f"SC觀眾{index + 1}")[:80],
                "message_text": str((base if is_malicious else comment).get("message_text") or base.get("message_text") or "")[:500],
                "amount_micros": amount_micros,
                "amount_display_string": str(base.get("amount_display_string") or self._format_test_amount(amount_micros)),
                "currency": str(base.get("currency") or "TWD"),
                "sc_tier": infer_super_chat_tier(amount_micros, int(base.get("sc_tier", 0) or 0)),
                "safety_label": str(base.get("safety_label") or ""),
                "is_malicious_sample": is_malicious,
            })
        return super_chats

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
