"""Research Gate Module for YouTubeBridge live context."""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol

from bridge_runtime import LiveRuntime


logger = logging.getLogger("youtube_bridge")


class ResearchSearchAdapter(Protocol):
    def search(self, query: str) -> Any:
        """Return raw search result data for a Research Gate query."""


class TavilyResearchSearchAdapter:
    def search(self, query: str) -> Any:
        from tools.tavily import search_web

        return search_web(query=query, topic="general")


class ResearchGateModule:
    def __init__(
        self,
        *,
        storage,
        runtime_lookup: Callable[[str], LiveRuntime],
        runtime_getter: Callable[[str], LiveRuntime | None] | None = None,
        topic_pack_context_text: Callable[[list[dict[str, Any]]], str],
        record_topic_pack_usage: Callable[[str, list[dict[str, Any]], str, str], Any],
        index_topic_pack_entry: Callable[[int], Any],
        search_adapter: ResearchSearchAdapter | None = None,
    ) -> None:
        self.storage = storage
        self._runtime_lookup = runtime_lookup
        self._runtime_getter = runtime_getter or runtime_lookup
        self._topic_pack_context_text = topic_pack_context_text
        self._record_topic_pack_usage = record_topic_pack_usage
        self._index_topic_pack_entry = index_topic_pack_entry
        self._search_adapter = search_adapter or TavilyResearchSearchAdapter()

    def request_sync(
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
        target_pack_id = pack_id if pack_id is not None else self.first_session_topic_pack_id(session_id)
        if target_pack_id is None:
            pack = self.storage.create_topic_pack({
                "title": f"{session.get('display_name') or session_id} Research",
                "description": "Bridge Research Gate 自動建立的直播 fact cards。",
            })
            self.storage.link_topic_pack_to_session(session_id, int(pack["id"]))
            target_pack_id = int(pack["id"])
        try:
            raw_result = self._search_adapter.search(query)
        except Exception as exc:
            self.storage.create_research_request(session_id, query, status="failed", metadata={"error": str(exc)[:500]})
            raise
        body = self.research_result_to_fact_card(query, raw_result)
        research_meta = self.research_result_metadata(raw_result)
        entry = self.storage.create_topic_pack_entry(int(target_pack_id), {
            "title": query[:120],
            "body": body,
            "source_url": research_meta["source_urls"][0] if research_meta["source_urls"] else "",
            "source_type": "research_gate",
            "tags": ["research_gate"],
        })
        embedding = None
        try:
            embedding = self._index_topic_pack_entry(int(entry["id"]))
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

    def first_session_topic_pack_id(self, session_id: str) -> int | None:
        packs = self.storage.list_session_topic_packs(session_id)
        if not packs:
            return None
        return int(packs[0]["id"])

    def completed_context(self, session_id: str, query_text: str) -> tuple[str, str]:
        query_key = self.audience_query_key(session_id, query_text)
        job = self.audience_research_job(session_id, query_key)
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

    @staticmethod
    def audience_query_key(session_id: str, query_text: str) -> str:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"youtube-live:{session_id}:{query_text}").hex

    @staticmethod
    def research_items(raw_result: Any) -> list[dict[str, str]]:
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
            candidates = ResearchGateModule.legacy_research_text_items(candidates)

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
    def legacy_research_text_items(text: str) -> list[dict[str, str]]:
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
    def research_result_metadata(raw_result: Any) -> dict[str, Any]:
        items = ResearchGateModule.research_items(raw_result)
        source_titles = [item["title"] for item in items if item.get("title")][:5]
        source_urls = [item["url"] for item in items if item.get("url")][:5]
        return {
            "status": "completed_with_results" if items else "completed_no_results",
            "source_titles": source_titles,
            "source_urls": source_urls,
        }

    @staticmethod
    def research_result_to_fact_card(query: str, raw_result: Any) -> str:
        items = ResearchGateModule.research_items(raw_result)
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

    def ensure_audience_worker(
        self,
        session: dict[str, Any],
        query_text: str,
        *,
        pack_id: int | None = None,
        thread_factory: Callable[..., Any] | None = None,
        worker_target: Callable[..., Any] | None = None,
    ) -> dict[str, Any]:
        session_id = str(session.get("session_id") or "")
        query_key = self.audience_query_key(session_id, query_text)
        runtime = self._runtime_lookup(session_id)
        thread = runtime.audience_research_tasks.get(query_key)
        thread_alive = bool(thread and thread.is_alive())
        existing = self.audience_research_job(session_id, query_key)
        if existing.get("in_progress") and thread_alive:
            return {**existing, "status": str(existing.get("status") or "running")}
        if str(existing.get("status") or "") in {"completed", "completed_with_results", "completed_no_results", "degraded", "failed"}:
            return existing
        if thread_alive:
            return {"status": "running", "query_key": query_key, "query": query_text}
        if thread:
            runtime.audience_research_tasks.pop(query_key, None)
        started_at = datetime.now().isoformat()
        self.update_audience_research_job(session_id, query_key, {
            "status": "queued",
            "in_progress": True,
            "query": query_text,
            "pack_id": int(pack_id) if pack_id else 0,
            "started_at": started_at,
            "updated_at": started_at,
            "error": "",
        })
        thread_factory = thread_factory or threading.Thread
        worker_target = worker_target or self.run_audience_worker
        thread = thread_factory(
            target=worker_target,
            args=(session_id, query_key, query_text),
            kwargs={"pack_id": pack_id},
            name=f"audience-research-{session_id[:12]}",
            daemon=True,
        )
        runtime.audience_research_tasks[query_key] = thread
        thread.start()
        return {"status": "queued", "query_key": query_key, "query": query_text}

    def run_audience_worker(
        self,
        session_id: str,
        query_key: str,
        query_text: str,
        *,
        pack_id: int | None = None,
        request_sync: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.update_audience_research_job(session_id, query_key, {
            "status": "running",
            "in_progress": True,
            "query": query_text,
            "pack_id": int(pack_id) if pack_id else 0,
            "updated_at": datetime.now().isoformat(),
            "error": "",
        })
        try:
            sync = request_sync or self.request_sync
            result = sync(
                session_id,
                query_text,
                pack_id=pack_id,
                enforce_cooldown=True,
            )
            entry = result.get("entry") if isinstance(result, dict) else {}
            record = result.get("record") if isinstance(result, dict) else {}
            status = str((record or {}).get("status") or result.get("status") or "completed")
            self.update_audience_research_job(session_id, query_key, {
                "status": status,
                "in_progress": False,
                "query": query_text,
                "pack_id": int(pack_id) if pack_id else int((entry or {}).get("pack_id") or 0),
                "entry_id": int((entry or {}).get("id") or 0),
                "updated_at": datetime.now().isoformat(),
                "error": "",
            })
        except Exception as exc:
            self.update_audience_research_job(session_id, query_key, {
                "status": "failed",
                "in_progress": False,
                "query": query_text,
                "pack_id": int(pack_id) if pack_id else 0,
                "updated_at": datetime.now().isoformat(),
                "error": str(exc)[:500],
            })
            logger.warning("audience research worker failed session_id=%s error=%s", session_id, exc)
        finally:
            runtime = self._runtime_getter(session_id)
            if runtime:
                runtime.audience_research_tasks.pop(query_key, None)

    def audience_research_job(self, session_id: str, query_key: str) -> dict[str, Any]:
        state = self.storage.get_director_state(session_id)
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        jobs = metadata.get("audience_query_research") if isinstance(metadata.get("audience_query_research"), dict) else {}
        job = jobs.get(query_key) if isinstance(jobs.get(query_key), dict) else {}
        return dict(job)

    def update_audience_research_job(self, session_id: str, query_key: str, fields: dict[str, Any]) -> dict[str, Any]:
        state = self.storage.get_director_state(session_id)
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        jobs = dict(metadata.get("audience_query_research") or {})
        current = dict(jobs.get(query_key) or {})
        current.update(fields)
        current["query_key"] = query_key
        jobs[query_key] = current
        self.storage.update_director_state(session_id, metadata={"audience_query_research": jobs})
        return current
