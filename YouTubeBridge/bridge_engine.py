"""YouTubeBridge polling manager。"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import random
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import engine_public_events
from engine_closing import ClosingManagerMixin
from engine_director import DirectorManagerMixin
from engine_director_runtime import DirectorRuntimeManagerMixin
from engine_event_safety import EventSafetyManagerMixin
from engine_injection import InjectionManagerMixin
from engine_runtime_lifecycle import RuntimeLifecycleManagerMixin
from engine_test_runtime import TestRuntimeManagerMixin
from engine_topic_packs import TopicPackManagerMixin
from bridge_contracts import (
    AUDIENCE_QUERY_CLASSIFIER_SCHEMA,
    AUDIENCE_QUERY_FACT_CARD_MIN_GAP,
    AUDIENCE_QUERY_FACT_CARD_MIN_SCORE,
    AUDIENCE_QUERY_FACT_CARD_STRONG_SCORE,
    CONTROLLED_CONTEXT_CONTENT,
    DEFAULT_INJECT_CONTENT,
    DIRECTOR_SCHEMA,
    FACT_CARDS_PACK_DESCRIPTION,
    FACT_CARDS_PACK_TITLE,
    SAFETY_CLASSIFIER_BATCH_LIMIT,
    SAFETY_CLASSIFIER_SCHEMA,
    TEST_COMMENT_SCHEMA,
)
from bridge_runtime import LiveRuntime
from fact_cards import (
    DEFAULT_FACT_CARDS_DIR,
    generate_fact_card_markdown_with_gemini,
    iter_fact_card_files,
    parse_fact_card_markdown,
)
from memoria_client import MemoriaClient
from storage import BridgeStorage, infer_super_chat_tier
from youtube_client import YouTubeClient, normalize_message


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("youtube_bridge")
DEFAULT_LLM_TRACE_PATH = PROJECT_ROOT / "runtime" / "llm_trace.jsonl"


def clear_llm_trace_log(path: Path | None = None) -> dict[str, Any]:
    target = Path(path or DEFAULT_LLM_TRACE_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")
    return {"cleared": True, "path": str(target)}


class YouTubeBridgeManager(
    DirectorRuntimeManagerMixin,
    ClosingManagerMixin,
    InjectionManagerMixin,
    RuntimeLifecycleManagerMixin,
    EventSafetyManagerMixin,
    TestRuntimeManagerMixin,
    DirectorManagerMixin,
    TopicPackManagerMixin,
):
    def __init__(
        self,
        storage: BridgeStorage,
        youtube_client: YouTubeClient | None = None,
        memoria_client_factory=None,
    ):
        self.storage = storage
        self.youtube_client = youtube_client or YouTubeClient()
        self.memoria_client_factory = memoria_client_factory or MemoriaClient
        self.auto_finalize_archive_callback = None
        self._runtimes: dict[str, LiveRuntime] = {}
        self._lock = asyncio.Lock()

    def _memoria_client(self):
        return self.memoria_client_factory()

    async def _run_auto_finalize_archive_callback(
        self,
        session_id: str,
        *,
        finalized_by: str,
        finalized: dict[str, Any],
    ) -> dict[str, Any] | None:
        callback = self.auto_finalize_archive_callback
        if not callback:
            return None
        result = callback(session_id, finalized_by=finalized_by, finalized=finalized)
        if inspect.isawaitable(result):
            return await result
        return result

    @staticmethod
    def _clear_llm_trace_log() -> dict[str, Any]:
        return clear_llm_trace_log()

    @staticmethod
    def _public_decision(decision: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(decision, dict):
            return {}
        return {
            "action": decision.get("action"),
            "reason": decision.get("reason"),
            "current_topic": decision.get("current_topic"),
        }

    @staticmethod
    def _public_director_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(metadata, dict):
            return {}
        public: dict[str, Any] = {}
        for key, value in metadata.items():
            key_str = str(key)
            key_lower = key_str.lower()
            if key_lower in {"opening_decision", "last_decision", "decision"} and isinstance(value, dict):
                public[key_str] = YouTubeBridgeManager._public_decision(value)
                continue
            if "prompt" in key_lower:
                continue
            if key_lower in {"hidden_context", "external_context", "context_text", "raw_context"}:
                public[key_str] = "[hidden]"
                continue
            if key_lower in {"events", "event_ids", "super_chats", "comments"} and isinstance(value, list):
                public[key_str] = {"count": len(value)}
                continue
            if key_lower == "interaction" and isinstance(value, dict):
                public[key_str] = YouTubeBridgeManager._public_interaction_status(value)
                continue
            if isinstance(value, dict):
                public[key_str] = YouTubeBridgeManager._public_director_metadata(value)
                continue
            public[key_str] = value
        return public

    @staticmethod
    def _public_director_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(state, dict):
            return state
        public = dict(state)
        public["metadata"] = YouTubeBridgeManager._public_director_metadata(public.get("metadata"))
        return public

    @staticmethod
    def _public_interaction_status(interaction: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(interaction, dict):
            return interaction
        public = dict(interaction)
        for field in ("content", "reply_text", "closure_text"):
            public[field] = YouTubeBridgeManager._public_interaction_text(public.get(field))
        metadata = public.get("metadata") if isinstance(public.get("metadata"), dict) else {}
        public_metadata: dict[str, Any] = {}
        for key, value in metadata.items():
            key_str = str(key)
            key_lower = key_str.lower()
            if key_lower == "decision" and isinstance(value, dict):
                public_metadata["decision"] = YouTubeBridgeManager._public_decision(value)
            elif "prompt" in key_lower:
                continue
            elif key_lower in {"hidden_context", "external_context", "context_text", "raw_context"}:
                public_metadata[key_str] = "[hidden]"
            elif key_lower in {"events", "event_ids", "super_chats", "comments"} and isinstance(value, list):
                public_metadata[key_str] = {"count": len(value)}
            elif key_lower in {"summary"} and isinstance(value, dict):
                public_metadata[key_str] = {
                    summary_key: value.get(summary_key)
                    for summary_key in ("source", "source_session_id", "event_count", "dropped_count")
                    if summary_key in value
                }
            elif isinstance(value, dict):
                public_metadata[key_str] = YouTubeBridgeManager._public_director_metadata(value)
            else:
                public_metadata[key_str] = value
        public["metadata"] = public_metadata
        return public

    @staticmethod
    def _public_interaction_text(value: Any) -> str:
        text = str(value or "")
        hidden_markers = (
            "<external_chat_context",
            "<topic_pack_fact_cards",
            "hidden external context",
            "完整 SC 清單",
        )
        if any(marker in text for marker in hidden_markers):
            return "[hidden context]"
        if len(text) > 800:
            return f"{text[:800]}... [truncated {len(text)} chars]"
        return text

    async def _poll_loop(self, runtime: LiveRuntime) -> None:
        while runtime.running:
            session = self.storage.get_session(runtime.session_id)
            if not session:
                runtime.status = "missing"
                runtime.running = False
                return
            if self._duration_reached(session):
                await self._finalize_for_duration(runtime, session)
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
                        public_event = self._public_live_event(saved)
                        if public_event:
                            await self._broadcast(runtime.session_id, {"type": "youtube_live_event", "event": public_event})
                interval_ms = int(data.get("pollingIntervalMillis") or 5000)
                await asyncio.sleep(max(2.0, min(interval_ms / 1000, 30.0)))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._is_live_chat_ended_error(exc):
                    finalized_at = datetime.now().isoformat()
                    runtime.status = "ended"
                    runtime.running = False
                    runtime.last_error = str(exc)
                    self.storage.update_session_fields(
                        runtime.session_id,
                        status="ended",
                        finalized_at=finalized_at,
                        summary_status=session.get("summary_status") or "pending",
                    )
                    await self._broadcast(
                        runtime.session_id,
                        {
                            "type": "status",
                            "status": "ended",
                            "message": "YouTube live chat ended",
                            "finalized_at": finalized_at,
                        },
                    )
                    try:
                        await self._run_auto_finalize_archive_callback(
                            runtime.session_id,
                            finalized_by="youtube_live_chat_ended",
                            finalized={
                                **(self.storage.get_session(runtime.session_id) or session),
                                "runtime_status": self.get_status(runtime.session_id),
                            },
                        )
                    except Exception as archive_exc:
                        logger.warning(
                            "auto finalize archive failed session_id=%s error=%s",
                            runtime.session_id,
                            archive_exc,
                        )
                    return
                runtime.status = "error"
                runtime.last_error = str(exc)
                self.storage.update_session_fields(runtime.session_id, status="error")
                logger.error("YouTube polling error session_id=%s error=%s", runtime.session_id, exc, exc_info=True)
                await self._broadcast(
                    runtime.session_id,
                    {"type": "status", "status": "error", "message": str(exc)},
                )
                await asyncio.sleep(15)

    @staticmethod
    def _is_live_chat_ended_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "livechatended" in message or "live chat is no longer live" in message

    def _live_query_context_for_events(
        self,
        session: dict[str, Any],
        events: list[dict[str, Any]],
        lines: list[str],
    ) -> tuple[str, dict[str, Any]]:
        session_id = str(session.get("session_id") or "")
        query_intent = self._audience_query_intent_from_events(events)
        query_text = str(query_intent.get("sanitized_query") or "").strip()
        resolution: dict[str, Any] = {
            "query": query_text,
            "query_intent": query_intent,
            "local_answerable": False,
            "local_entry_count": 0,
            "local_top_similarity": None,
            "research_status": "not_needed" if not query_text else "not_attempted",
            "research_error": "",
        }
        base_query = "\n".join([*lines, str(session.get("director_guidance") or "")])
        if not query_text:
            context = self._topic_pack_sequence_context_for_session(
                session_id,
                base_query,
                usage_source="external_context",
            )
            return context, resolution

        entries, search_status = self._topic_pack_entries_for_query(
            session_id,
            base_query,
            limit=6,
            min_score=AUDIENCE_QUERY_FACT_CARD_MIN_SCORE,
            allow_fallback=False,
        )
        resolution["local_entry_count"] = len(entries)
        resolution["local_top_similarity"] = search_status.get("top_similarity")
        if self._topic_pack_entries_can_answer(entries):
            resolution["local_answerable"] = True
            resolution["research_status"] = "not_needed"
            context_entries = entries[:1]
            self._record_topic_pack_usage(session_id, context_entries, query_text, "external_context")
            return self._topic_pack_context_text(context_entries), resolution

        if not session.get("research_enabled"):
            resolution["research_status"] = "disabled"
            return "", resolution
        if not query_intent.get("needs_external_search") or not query_intent.get("safe_search_allowed"):
            resolution["research_status"] = "not_allowed"
            return "", resolution

        completed_context, completed_status = self._completed_audience_research_context(session_id, query_text)
        if completed_context:
            resolution["research_status"] = completed_status or "completed"
            return completed_context, resolution

        worker = self._ensure_audience_research_worker(
            session,
            query_text,
            pack_id=self._first_session_topic_pack_id(session_id),
        )
        resolution["research_status"] = str(worker.get("status") or "queued")
        if worker.get("error"):
            resolution["research_error"] = str(worker.get("error") or "")[:300]
        if resolution["research_status"] in {"queued", "running"}:
            raise ValueError("觀眾查詢資料搜尋中，保留留言等待 Research Gate 完成")
        return "", resolution

    @staticmethod
    def _topic_pack_entries_can_answer(entries: list[dict[str, Any]]) -> bool:
        if not entries:
            return False
        top_score = float(entries[0].get("similarity") or 0.0)
        if top_score >= AUDIENCE_QUERY_FACT_CARD_STRONG_SCORE:
            return True
        if top_score < AUDIENCE_QUERY_FACT_CARD_MIN_SCORE:
            return False
        if len(entries) == 1:
            return True
        second_score = float(entries[1].get("similarity") or 0.0)
        return (top_score - second_score) >= AUDIENCE_QUERY_FACT_CARD_MIN_GAP

    def _audience_query_text_from_events(self, events: list[dict[str, Any]]) -> str:
        return str(self._audience_query_intent_from_events(events).get("sanitized_query") or "").strip()

    def _audience_query_intent_from_events(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        request_events: list[dict[str, Any]] = []
        for event in events:
            text = self._event_safe_text(event)
            if not text:
                continue
            request_events.append({
                "author_display_name": str(event.get("author_display_name") or "匿名觀眾")[:80],
                "priority_class": str(event.get("priority_class") or "normal"),
                "message_text": text[:500],
            })
        default = {
            "is_factual_question": False,
            "needs_external_search": False,
            "safe_search_allowed": False,
            "sanitized_query": "",
            "topic_scope": "",
            "risk_label": "unknown",
            "reason": "沒有可分類的安全觀眾留言。",
        }
        if not request_events:
            return default
        try:
            try:
                client = self.memoria_client_factory(timeout=15.0)
            except TypeError:
                client = self._memoria_client()
            result = client.generate_prompt_json(
                prompt_key="youtube_live_audience_query_classifier_prompt",
                variables={"events_json": json.dumps(request_events, ensure_ascii=False, indent=2)},
                task_key="router",
                temperature=0.0,
                schema=AUDIENCE_QUERY_CLASSIFIER_SCHEMA,
            )
        except Exception as exc:
            logger.warning("audience query classifier failed error=%s", exc)
            return {**default, "reason": f"query classifier failed: {str(exc)[:180]}"}
        if not isinstance(result, dict):
            return default
        factual = bool(result.get("is_factual_question"))
        safe = bool(result.get("safe_search_allowed"))
        query = self._single_line(result.get("sanitized_query") or "")[:240]
        if not factual:
            query = ""
        return {
            "is_factual_question": factual,
            "needs_external_search": bool(result.get("needs_external_search")) and bool(query),
            "safe_search_allowed": safe and bool(query),
            "sanitized_query": query if safe else "",
            "topic_scope": self._single_line(result.get("topic_scope") or "")[:80],
            "risk_label": self._single_line(result.get("risk_label") or "unknown")[:80],
            "reason": self._single_line(result.get("reason") or "")[:240],
        }

    def _ensure_audience_research_worker(
        self,
        session: dict[str, Any],
        query_text: str,
        *,
        pack_id: int | None = None,
    ) -> dict[str, Any]:
        session_id = str(session.get("session_id") or "")
        query_key = self._audience_query_key(session_id, query_text)
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id))
        thread = runtime.audience_research_tasks.get(query_key)
        thread_alive = bool(thread and thread.is_alive())
        existing = self._audience_research_job(session_id, query_key)
        if existing.get("in_progress") and thread_alive:
            return {**existing, "status": str(existing.get("status") or "running")}
        if str(existing.get("status") or "") in {"completed", "completed_with_results", "completed_no_results", "degraded", "failed"}:
            return existing
        if thread_alive:
            return {"status": "running", "query_key": query_key, "query": query_text}
        if thread:
            runtime.audience_research_tasks.pop(query_key, None)
        started_at = datetime.now().isoformat()
        self._update_audience_research_job(session_id, query_key, {
            "status": "queued",
            "in_progress": True,
            "query": query_text,
            "pack_id": int(pack_id) if pack_id else 0,
            "started_at": started_at,
            "updated_at": started_at,
            "error": "",
        })
        thread = threading.Thread(
            target=self._run_audience_research_worker,
            args=(session_id, query_key, query_text),
            kwargs={"pack_id": pack_id},
            name=f"audience-research-{session_id[:12]}",
            daemon=True,
        )
        runtime.audience_research_tasks[query_key] = thread
        thread.start()
        return {"status": "queued", "query_key": query_key, "query": query_text}

    def _run_audience_research_worker(
        self,
        session_id: str,
        query_key: str,
        query_text: str,
        *,
        pack_id: int | None = None,
    ) -> None:
        self._update_audience_research_job(session_id, query_key, {
            "status": "running",
            "in_progress": True,
            "query": query_text,
            "pack_id": int(pack_id) if pack_id else 0,
            "updated_at": datetime.now().isoformat(),
            "error": "",
        })
        try:
            result = self._research_request_sync(
                session_id,
                query_text,
                pack_id=pack_id,
                enforce_cooldown=True,
            )
            entry = result.get("entry") if isinstance(result, dict) else {}
            record = result.get("record") if isinstance(result, dict) else {}
            status = str((record or {}).get("status") or result.get("status") or "completed")
            self._update_audience_research_job(session_id, query_key, {
                "status": status,
                "in_progress": False,
                "query": query_text,
                "pack_id": int(pack_id) if pack_id else int((entry or {}).get("pack_id") or 0),
                "entry_id": int((entry or {}).get("id") or 0),
                "updated_at": datetime.now().isoformat(),
                "error": "",
            })
        except Exception as exc:
            self._update_audience_research_job(session_id, query_key, {
                "status": "failed",
                "in_progress": False,
                "query": query_text,
                "pack_id": int(pack_id) if pack_id else 0,
                "updated_at": datetime.now().isoformat(),
                "error": str(exc)[:500],
            })
            logger.warning("audience research worker failed session_id=%s error=%s", session_id, exc)
        finally:
            runtime = self._runtimes.get(session_id)
            if runtime:
                runtime.audience_research_tasks.pop(query_key, None)

    @staticmethod
    def _audience_query_key(session_id: str, query_text: str) -> str:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"youtube-live:{session_id}:{query_text}").hex

    def _audience_research_job(self, session_id: str, query_key: str) -> dict[str, Any]:
        state = self.storage.get_director_state(session_id)
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        jobs = metadata.get("audience_query_research") if isinstance(metadata.get("audience_query_research"), dict) else {}
        job = jobs.get(query_key) if isinstance(jobs.get(query_key), dict) else {}
        return dict(job)

    def _update_audience_research_job(self, session_id: str, query_key: str, fields: dict[str, Any]) -> dict[str, Any]:
        state = self.storage.get_director_state(session_id)
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        jobs = dict(metadata.get("audience_query_research") or {})
        current = dict(jobs.get(query_key) or {})
        current.update(fields)
        current["query_key"] = query_key
        jobs[query_key] = current
        self.storage.update_director_state(session_id, metadata={"audience_query_research": jobs})
        return current

    def _first_session_topic_pack_id(self, session_id: str) -> int | None:
        packs = self.storage.list_session_topic_packs(session_id)
        if not packs:
            return None
        return int(packs[0]["id"])

    def _completed_audience_research_context(self, session_id: str, query_text: str) -> tuple[str, str]:
        query_key = self._audience_query_key(session_id, query_text)
        job = self._audience_research_job(session_id, query_key)
        status = str(job.get("status") or "")
        if status not in {"completed", "completed_with_results", "degraded"}:
            return "", status
        entry_id = int(job.get("entry_id") or 0)
        if not entry_id:
            return "", status
        entry = self.storage.get_topic_pack_entry(entry_id)
        if not entry:
            return "", status
        self._record_topic_pack_usage(session_id, [entry], query_text, "external_context")
        return self._topic_pack_context_text([entry]), status


    @classmethod
    def _research_gate_usage_status(
        cls,
        entries: list[dict[str, Any]],
        research_requests: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        statuses: dict[str, int] = {}
        total = 0
        success = 0
        degraded = 0
        entry_ids = set()
        for entry in entries:
            if str(entry.get("source_type") or "") != "research_gate":
                continue
            entry_ids.add(int(entry.get("id") or entry.get("entry_id") or 0))
            total += 1
            status = cls._classify_research_gate_entry(entry)
            statuses[status] = statuses.get(status, 0) + 1
            if status == "success":
                success += 1
            else:
                degraded += 1
        for request in research_requests or []:
            status = str(request.get("status") or "").strip() or "unknown"
            result_entry_id = int(request.get("result_entry_id") or 0)
            if result_entry_id and result_entry_id in entry_ids:
                continue
            if status == "completed_with_results":
                continue
            statuses[status] = statuses.get(status, 0) + 1
            total += 1
            degraded += 1
        return {
            "total_count": total,
            "success_count": success,
            "degraded_count": degraded,
            "statuses": statuses,
        }

    @staticmethod
    def _classify_research_gate_entry(entry: dict[str, Any]) -> str:
        body = str(entry.get("body") or "").strip()
        body_lower = body.lower()
        if not body:
            return "degraded"
        if body.startswith(("{", "[")) or '"search_results"' in body_lower or "'search_results'" in body_lower:
            return "raw_dump"
        if "completed_no_results" in body_lower:
            return "completed_no_results"
        if "completed_with_results" in body_lower:
            return "success" if str(entry.get("source_url") or "").strip() else "degraded"
        if "confidence: low" in body_lower:
            return "degraded"
        return "degraded"

















    async def research_request(
        self,
        session_id: str,
        query: str,
        *,
        pack_id: int | None = None,
        enforce_cooldown: bool = True,
    ) -> dict[str, Any]:
        result = await asyncio.to_thread(
            self._research_request_sync,
            session_id,
            query,
            pack_id=pack_id,
            enforce_cooldown=enforce_cooldown,
        )
        await self._broadcast(session_id, {
            "type": "research_card_created",
            "session_id": session_id,
            "entry": result.get("entry"),
            "research": result.get("research") or result.get("record"),
            "embedding": result.get("embedding"),
        })
        return result

    def _research_request_sync(
        self,
        session_id: str,
        query: str,
        *,
        pack_id: int | None = None,
        enforce_cooldown: bool = True,
    ) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        if not session.get("research_enabled"):
            raise ValueError("本場直播未啟用 Research Gate")
        query = str(query or "").strip()
        if not query:
            raise ValueError("research query 不可為空")
        cooldown = max(0, int(session.get("research_cooldown_seconds", 300) or 300))
        session_limit = max(0, int(session.get("research_max_per_session", 12) or 12))
        if session_limit and self.storage.count_research_requests(session_id) >= session_limit:
            raise ValueError("Research Gate 已達本場查詢上限")
        if enforce_cooldown and cooldown:
            since = (datetime.now() - timedelta(seconds=cooldown)).isoformat()
            if self.storage.count_research_requests(session_id, since_iso=since) >= 2:
                raise ValueError("Research Gate 冷卻中，稍後再查")
        target_pack_id = pack_id
        if target_pack_id is None:
            packs = self.storage.list_session_topic_packs(session_id)
            if packs:
                target_pack_id = int(packs[0]["id"])
            else:
                pack = self.storage.create_topic_pack({
                    "title": f"{session.get('display_name') or session_id} Research",
                    "description": "Bridge Research Gate 自動建立的直播 fact cards。",
                })
                self.storage.link_topic_pack_to_session(session_id, int(pack["id"]))
                target_pack_id = int(pack["id"])
        try:
            from tools.tavily import search_web

            raw_result = search_web(query=query, topic="general")
        except Exception as exc:
            self.storage.create_research_request(session_id, query, status="failed", metadata={"error": str(exc)[:500]})
            raise
        body = self._research_result_to_fact_card(query, raw_result)
        research_meta = self._research_result_metadata(raw_result)
        entry = self.storage.create_topic_pack_entry(int(target_pack_id), {
            "title": query[:120],
            "body": body,
            "source_url": research_meta["source_urls"][0] if research_meta["source_urls"] else "",
            "source_type": "research_gate",
            "tags": ["research_gate"],
        })
        embedding = None
        try:
            embedding = self.index_topic_pack_entry(int(entry["id"]))
        except Exception as exc:
            logger.warning("research fact card embedding failed session_id=%s entry_id=%s error=%s", session_id, entry["id"], exc)
        record = self.storage.create_research_request(
            session_id,
            query,
            status=research_meta["status"],
            result_entry_id=int(entry["id"]),
            metadata={
                "pack_id": int(target_pack_id),
                "status": research_meta["status"],
                "source_count": len(research_meta["source_urls"]),
                "source_urls": research_meta["source_urls"],
                "source_titles": research_meta["source_titles"],
            },
        )
        return {
            "status": research_meta["status"],
            "source_count": len(research_meta["source_urls"]),
            "source_urls": research_meta["source_urls"],
            "entry": entry,
            "research": record,
            "record": record,
            "embedding": embedding,
        }

    @staticmethod
    def _research_items(raw_result: Any) -> list[dict[str, str]]:
        raw = raw_result
        if isinstance(raw_result, str):
            stripped = raw_result.strip()
            try:
                raw = json.loads(stripped)
            except Exception:
                raw = {"search_results": [{"title": "Research Gate result", "url": "", "content": stripped}]}
        if isinstance(raw, dict):
            candidates = (
                raw.get("results")
                or raw.get("search_results")
                or raw.get("items")
                or raw.get("data")
                or []
            )
        elif isinstance(raw, list):
            candidates = raw
        else:
            candidates = []
        if isinstance(candidates, str):
            candidates = YouTubeBridgeManager._legacy_research_text_items(candidates)

        items: list[dict[str, str]] = []
        for item in candidates[:8]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or item.get("source") or "").strip()
            url = str(item.get("url") or item.get("source_url") or item.get("link") or "").strip()
            content = str(item.get("content") or item.get("snippet") or item.get("summary") or item.get("body") or "").strip()
            if not any((title, url, content)):
                continue
            items.append({
                "title": title[:180],
                "url": url[:1000],
                "content": " ".join(content.replace("\r", " ").split())[:700],
            })
        return items

    @staticmethod
    def _legacy_research_text_items(text: str) -> list[dict[str, str]]:
        """解析舊版 Tavily wrapper 的純文字 search_results。"""
        blocks = [block.strip() for block in str(text or "").split("\n\n") if block.strip()]
        items: list[dict[str, str]] = []
        for block in blocks[:8]:
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if not lines:
                continue
            title = lines[0]
            if title.startswith("[") and "]" in title:
                title = title.split("]", 1)[1].strip()
            content = " ".join(lines[1:]).strip()
            items.append({
                "title": title[:180],
                "url": "",
                "content": content[:700],
            })
        return items

    @staticmethod
    def _research_result_metadata(raw_result: Any) -> dict[str, Any]:
        items = YouTubeBridgeManager._research_items(raw_result)
        source_titles = [item["title"] for item in items if item.get("title")][:5]
        source_urls = [item["url"] for item in items if item.get("url")][:5]
        return {
            "status": "completed_with_results" if items else "completed_no_results",
            "source_titles": source_titles,
            "source_urls": source_urls,
        }

    @staticmethod
    def _research_result_to_fact_card(query: str, raw_result: Any) -> str:
        items = YouTubeBridgeManager._research_items(raw_result)
        if not items:
            return (
                f"summary: Research Gate 查詢「{query}」沒有取得可用摘要。\n"
                "facts:\n"
                "- 目前沒有可引用的外部資料。\n"
                "source_titles:\n"
                "- none\n"
                "source_urls:\n"
                "- none\n"
                "confidence: low\n"
                "status: completed_no_results"
            )
        trusted_hosts = ("official", "anime", "news", "wikipedia", "wiki", "ann", "crunchyroll")
        ranked = sorted(
            items,
            key=lambda item: (
                0 if any(token in (item.get("url", "") + " " + item.get("title", "")).lower() for token in trusted_hosts) else 1,
                len(item.get("content", "")) * -1,
            ),
        )
        top = ranked[:4]
        facts = []
        for item in top:
            content = item.get("content") or item.get("title") or item.get("url") or ""
            if content:
                facts.append(content[:240])
        source_titles = [item.get("title") or "untitled" for item in top if item.get("title") or item.get("url")]
        source_urls = [item.get("url") for item in top if item.get("url")]
        summary_text = facts[0] if facts else f"Research Gate 查詢「{query}」取得 {len(items)} 筆來源。"
        lines = [
            f"summary: {summary_text}",
            "facts:",
            *[f"- {fact}" for fact in facts[:5]],
            "source_titles:",
            *[f"- {title}" for title in source_titles[:5]],
            "source_urls:",
            *[f"- {url}" for url in source_urls[:5]],
            "confidence: medium" if source_urls else "confidence: low",
            "status: completed_with_results",
        ]
        return "\n".join(lines)

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

    @staticmethod
    def _single_line(value: Any) -> str:
        return engine_public_events.single_line(value)

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
        active_events = [
            event
            for event in events
            if event.get("status") == "active"
            and event.get("message_text")
            and event.get("safety_status") == "completed"
            and self._is_public_live_event_displayable(event)
        ]
        hidden_event_ids = [
            int(event["id"])
            for event in events
            if event.get("status") == "active"
            and event.get("message_text")
            and event.get("safety_status") in {"completed", "failed"}
            and not self._is_public_live_event_displayable(event)
        ]
        if hidden_event_ids:
            self.storage.mark_events_injected(session_id, hidden_event_ids)

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
            if self._is_public_live_event_displayable(event):
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
            "hidden_unsafe_count": len(hidden_event_ids),
            "dropped_count": max(0, len(active_events) - len(used_ids)),
        }
        topic_context, query_resolution = self._live_query_context_for_events(session, active_events, lines)
        summary["query_resolution"] = query_resolution
        payload = {
            "source": "youtube_live",
            "source_session_id": session_id,
            "connector_id": session["connector_id"],
            "video_id": session.get("video_id", ""),
            "live_chat_id": session.get("live_chat_id", ""),
            "context_text": "\n".join([part for part in ["\n".join(lines), topic_context] if part]),
            "event_ids": used_ids,
            "visible_events": visible_events,
            "max_chars": max_chars,
            "summary": summary,
        }
        return payload, summary

    @staticmethod
    def _event_line(event: dict[str, Any]) -> str:
        author = (event.get("author_display_name") or "匿名觀眾").strip()
        text = YouTubeBridgeManager._event_safe_text(event)
        if event.get("priority_class") == "super_chat":
            amount = str(event.get("amount_display_string") or "SC").strip()
            label = str(event.get("safety_label") or "unclassified")
            if label != "clean":
                safe_label = YouTubeBridgeManager._safe_label_text(label)
                return f"- [{amount}][安全標記: {safe_label}] {author or '匿名觀眾'}: {text}"
            return f"- [{amount}] {author or '匿名觀眾'}: {text}"
        if str(event.get("safety_label") or "unclassified") != "clean":
            safe_label = YouTubeBridgeManager._safe_label_text(str(event.get("safety_label") or "unclassified"))
            return f"- [安全標記: {safe_label}] {author or '匿名觀眾'}: {text}"
        return f"- {author or '匿名觀眾'}: {text}"

    @staticmethod
    def _should_block_director_for_pending_inject(event: dict[str, Any]) -> bool:
        """只有已通過安全檢查、可公開注入的留言會暫停 director idle。"""
        return YouTubeBridgeManager._is_public_live_event_displayable(event)

    @staticmethod
    def _test_comment_event_line(event: dict[str, Any]) -> str:
        if not YouTubeBridgeManager._is_public_live_event_displayable(event):
            return ""
        return YouTubeBridgeManager._visible_event_display_line(event)

    @staticmethod
    def _test_comment_interaction_line(item: dict[str, Any]) -> str:
        if str(item.get("status") or "") != "completed":
            return ""
        text = YouTubeBridgeManager._single_line(item.get("reply_text") or item.get("closure_text") or "")
        if not text:
            return ""
        source = str(item.get("source") or "")
        labels = {
            "director": "AI 回覆",
            "youtube_injection": "AI 回覆",
            "manual_inject": "AI 回覆",
            "auto_inject": "AI 回覆",
            "super_chat": "SC 回覆",
            "closing_super_chat_thanks": "SC 感謝",
        }
        label = labels.get(source, "AI 回覆")
        clean_text = YouTubeBridgeManager._sanitize_test_comment_text(text, "目前直播內容")
        return f"- {label}: {clean_text[:180]}"

    @staticmethod
    def _display_content_from_external_context(external_context: dict[str, Any]) -> str:
        lines: list[str] = []
        for event in external_context.get("visible_events") or []:
            if not isinstance(event, dict):
                continue
            line = YouTubeBridgeManager._visible_event_display_line(event)
            if line:
                lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _visible_event_display_line(event: dict[str, Any]) -> str:
        return engine_public_events.visible_event_display_line(event)

    @staticmethod
    def _visible_event(event: dict[str, Any]) -> dict[str, Any]:
        return engine_public_events.visible_event(event)

    @staticmethod
    def _event_safe_text(event: dict[str, Any]) -> str:
        return engine_public_events.event_safe_text(event)

    @staticmethod
    def _is_public_live_event_displayable(event: dict[str, Any]) -> bool:
        return engine_public_events.is_public_live_event_displayable(event)

    @staticmethod
    def _public_live_event(event: dict[str, Any]) -> dict[str, Any] | None:
        return engine_public_events.public_live_event(event)

    @staticmethod
    def _public_event(event: dict[str, Any]) -> dict[str, Any]:
        return engine_public_events.public_event(event)

    @staticmethod
    def _public_event_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        return engine_public_events.public_event_metadata(metadata)

    @staticmethod
    def _safe_label_text(label: str) -> str:
        mapping = {
            "suspicious_prompt_injection": "prompt injection 測試",
            "suspicious_secret_request": "祕密/憑證要求",
            "suspicious_url_or_token": "可疑 URL 或 token",
            "suspicious_sexual_or_coercive_roleplay": "可疑動作或角色狀態注入",
            "spam_or_duplicate": "重複或洗版",
            "unclassified": "尚未通過安全檢查",
            "unsafe_other": "可疑內容",
        }
        return mapping.get(str(label or ""), "可疑內容")

    @staticmethod
    def _topic_pack_context_text(entries: list[dict[str, Any]]) -> str:
        if not entries:
            return ""
        lines = ["", "<topic_pack_fact_cards>"]
        for entry in entries[-8:]:
            lines.append(f"- {entry.get('title')}: {entry.get('body')}".strip())
        lines.append("</topic_pack_fact_cards>")
        return "\n".join(lines)
