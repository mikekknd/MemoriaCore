import asyncio
import contextlib
import json
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from bridge_engine_test_support import (
    BRIDGE_ROOT,
    BridgeStorage,
    CapturingDirectorDecisionClient,
    ContractOnlyQueryClient,
    FakeBatchRecordingSafetyClient,
    FakeClosingFailingSafetyClient,
    FakeClosingMemoriaClient,
    FakeClosingSystemEventClient,
    FakeEmbeddingMemoriaClient,
    FakeFailingSafetyMemoriaClient,
    FakeSafetyMemoriaClient,
    LiveEndedClient,
    LiveRuntime,
    OffTopicEmbeddingMemoriaClient,
    OneMessagePollingClient,
    ResolveLiveChatFailedClient,
    YouTubeBridgeManager,
    _mark_event_clean,
    _tmp_dir,
    bridge_engine,
    normalize_message,
)


def test_research_gate_module_can_be_constructed_with_manager_adapters():
    from research_gate import ResearchGateModule, TavilyResearchSearchAdapter

    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        manager = YouTubeBridgeManager(storage, memoria_client_factory=OffTopicEmbeddingMemoriaClient)

        module = ResearchGateModule(
            storage=storage,
            runtime_lookup=lambda session_id: manager._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id)),
            topic_pack_context_text=manager._topic_pack_context_text,
            record_topic_pack_usage=manager._record_topic_pack_usage,
            index_topic_pack_entry=manager.index_topic_pack_entry,
            search_adapter=TavilyResearchSearchAdapter(),
        )

        assert module.storage is storage
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_research_gate_module_request_sync_creates_research_fact_card():
    from research_gate import ResearchGateModule

    class FakeSearchAdapter:
        def search(self, query: str):
            assert query == "最新一話聲優陣容"
            return {
                "results": [
                    {
                        "title": "官方聲優陣容公告",
                        "url": "https://example.test/cast",
                        "content": "官方公告列出主役聲優與配角聲優，社群討論集中在聲線變化。",
                    }
                ]
            }

    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({"connector_id": "yt-main", "display_name": "YouTube Main", "api_key": "key", "enabled": True})
        storage.upsert_session({"session_id": "live-a", "connector_id": "yt-main", "video_id": "video-a", "live_chat_id": "chat-a", "research_enabled": True})
        pack = storage.create_topic_pack({"title": "直播資料包"})
        storage.link_topic_pack_to_session("live-a", pack["id"])
        module = ResearchGateModule(
            storage=storage,
            runtime_lookup=lambda session_id: LiveRuntime(session_id=session_id),
            topic_pack_context_text=lambda entries: "\n".join(str(entry["body"]) for entry in entries),
            record_topic_pack_usage=lambda *_args, **_kwargs: None,
            index_topic_pack_entry=lambda _entry_id: None,
            search_adapter=FakeSearchAdapter(),
        )

        result = module.request_sync("live-a", "最新一話聲優陣容", pack_id=pack["id"], enforce_cooldown=True)

        assert result["status"] == "completed_with_results"
        assert result["entry"]["source_type"] == "research_gate"
        assert result["research"]["result_entry_id"] == result["entry"]["id"]
        assert "官方公告列出主役聲優" in result["entry"]["body"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_research_gate_request_sync_does_not_search_when_research_disabled():
    from research_gate import ResearchGateModule

    class NoSideEffectSearchAdapter:
        def search(self, query: str):
            raise AssertionError("search adapter should not be called")

    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({"connector_id": "yt-main", "display_name": "YouTube Main", "api_key": "key", "enabled": True})
        storage.upsert_session({"session_id": "live-a", "connector_id": "yt-main", "video_id": "video-a", "live_chat_id": "chat-a", "research_enabled": False})
        module = ResearchGateModule(
            storage=storage,
            runtime_lookup=lambda session_id: LiveRuntime(session_id=session_id),
            topic_pack_context_text=lambda entries: "",
            record_topic_pack_usage=lambda session_id, entries, source, reason: None,
            index_topic_pack_entry=lambda entry_id: None,
            search_adapter=NoSideEffectSearchAdapter(),
        )

        with pytest.raises(ValueError, match="未啟用 Research Gate"):
            module.request_sync("live-a", "最新一話聲優陣容")

        assert storage.list_research_requests("live-a") == []
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_audience_question_queues_research_gate_without_blocking_injection(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "video_id": "video-a",
            "live_chat_id": "chat-a",
            "research_enabled": True,
        })
        pack = storage.create_topic_pack({"title": "直播資料包"})
        entry = storage.create_topic_pack_entry(pack["id"], {
            "title": "不相關的美食資料",
            "body": "這張卡只描述拉麵湯頭，不能回答動畫聲優問題。",
            "source_type": "manual",
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        storage.upsert_topic_pack_entry_embedding(entry["id"], [1.0, 0.0], model="fake-embed", content_hash="ramen")
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "最新一話的聲優陣容有什麼看點？",
            "author_display_name": "觀眾A",
        })
        _mark_event_clean(storage, event)
        manager = YouTubeBridgeManager(storage, memoria_client_factory=OffTopicEmbeddingMemoriaClient)
        queued: list[dict] = []

        def fail_inline_research(*_args, **_kwargs):
            raise AssertionError("Research Gate must not run inline while building live context")

        def fake_ensure_worker(session: dict, query: str, *, pack_id: int | None = None):
            queued.append({
                "session_id": session["session_id"],
                "query": query,
                "pack_id": pack_id,
            })
            return {"status": "queued", "query": query}

        monkeypatch.setattr(manager, "_research_request_sync", fail_inline_research, raising=False)
        monkeypatch.setattr(manager, "_ensure_audience_research_worker", fake_ensure_worker, raising=False)

        context, summary = manager.build_external_context("live-a")

        assert queued
        assert queued[0]["session_id"] == "live-a"
        assert "最新一話的聲優陣容" in queued[0]["query"]
        assert "相關查證仍在背景處理" in context["context_text"]
        assert summary["query_resolution"]["research_status"] == "queued"
        assert summary["query_resolution"]["fallback_reason"] == "research_incomplete"
        assert not storage.get_events_by_ids("live-a", [event["id"]])[0]["injected_at"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_audience_question_rejects_single_wrong_topic_fact_card_and_queues_research(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "video_id": "video-a",
            "live_chat_id": "chat-a",
            "research_enabled": True,
        })
        pack = storage.create_topic_pack({"title": "直播資料包"})
        magic_entry = storage.create_topic_pack_entry(pack["id"], {
            "title": "魔法帽的工作室 第一集 評價",
            "body": "這張卡描述魔法帽的工作室第一集觀眾評價與動畫演出。",
            "source_type": "manual",
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_type": "superChatEvent",
            "message_text": "《黃泉使者》的畫風好治癒，可以講一下嗎？",
            "safe_message_text": "《黃泉使者》的畫風好治癒，可以講一下嗎？",
            "author_display_name": "觀眾A",
            "amount_display_string": "NT$75",
        })
        _mark_event_clean(storage, event)
        manager = YouTubeBridgeManager(storage, memoria_client_factory=OffTopicEmbeddingMemoriaClient)
        queued: list[dict] = []

        monkeypatch.setattr(manager, "_audience_query_intent_from_events", lambda _events: {
            "is_factual_question": True,
            "needs_external_search": True,
            "safe_search_allowed": True,
            "sanitized_query": "黃泉使者 劇情 解說",
            "topic_scope": "anime_work",
            "risk_label": "clean",
            "reason": "測試 query。",
        })
        monkeypatch.setattr(
            manager,
            "_topic_pack_entries_for_query",
            lambda *_args, **_kwargs: ([{**magic_entry, "similarity": 0.556}], {"top_similarity": 0.556}),
        )

        def fake_ensure_worker(session: dict, query: str, *, pack_id: int | None = None):
            queued.append({"query": query, "pack_id": pack_id})
            return {"status": "queued", "query": query}

        monkeypatch.setattr(manager, "_ensure_audience_research_worker", fake_ensure_worker, raising=False)

        context, summary = manager.build_external_context("live-a", event_ids=[event["id"]])

        assert "魔法帽的工作室" not in context["context_text"]
        assert "相關查證仍在背景處理" in context["context_text"]
        assert queued == [{"query": "黃泉使者 劇情 解說", "pack_id": pack["id"]}]
        assert summary["query_resolution"]["local_answerable"] is False
        assert summary["query_resolution"]["research_status"] == "queued"
        assert summary["query_resolution"]["local_rejected_by_topic_count"] == 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_topic_pack_entries_can_answer_keeps_gap_rule_for_multiple_matching_entries():
    entries = [
        {
            "title": "黃泉使者 劇情與畫風解說",
            "body": "作品以柔和線條呈現黃泉使者的療癒氛圍。",
            "similarity": 0.210,
        },
        {
            "title": "黃泉使者 角色關係整理",
            "body": "這張卡整理黃泉使者的主要角色與劇情背景。",
            "similarity": 0.205,
        },
    ]

    assert YouTubeBridgeManager._topic_pack_entries_can_answer(
        entries,
        query_text="黃泉使者 劇情 解說",
    ) is False
    assert YouTubeBridgeManager._topic_pack_entries_can_answer(
        entries[:1],
        query_text="黃泉使者 劇情 解說",
    ) is True


def test_audience_question_rejects_wrong_topic_fact_card_sharing_short_suffix(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "video_id": "video-a",
            "live_chat_id": "chat-a",
            "research_enabled": True,
        })
        pack = storage.create_topic_pack({"title": "直播資料包"})
        wrong_entry = storage.create_topic_pack_entry(pack["id"], {
            "title": "異世界使者角色整理",
            "body": "這張卡只整理異世界作品中的使者定位與配角關係。",
            "source_type": "manual",
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_type": "superChatEvent",
            "message_text": "《黃泉使者》的畫風好治癒，可以講一下嗎？",
            "safe_message_text": "《黃泉使者》的畫風好治癒，可以講一下嗎？",
            "author_display_name": "觀眾A",
            "amount_display_string": "NT$75",
        })
        _mark_event_clean(storage, event)
        manager = YouTubeBridgeManager(storage, memoria_client_factory=OffTopicEmbeddingMemoriaClient)
        queued: list[dict] = []

        monkeypatch.setattr(manager, "_audience_query_intent_from_events", lambda _events: {
            "is_factual_question": True,
            "needs_external_search": True,
            "safe_search_allowed": True,
            "sanitized_query": "黃泉使者 劇情 解說",
            "topic_scope": "anime_work",
            "risk_label": "clean",
            "reason": "測試 query。",
        })
        monkeypatch.setattr(
            manager,
            "_topic_pack_entries_for_query",
            lambda *_args, **_kwargs: ([{**wrong_entry, "similarity": 0.556}], {"top_similarity": 0.556}),
        )

        def fake_ensure_worker(session: dict, query: str, *, pack_id: int | None = None):
            queued.append({"query": query, "pack_id": pack_id})
            return {"status": "queued", "query": query}

        monkeypatch.setattr(manager, "_ensure_audience_research_worker", fake_ensure_worker, raising=False)

        context, summary = manager.build_external_context("live-a", event_ids=[event["id"]])

        assert "異世界使者" not in context["context_text"]
        assert "相關查證仍在背景處理" in context["context_text"]
        assert queued == [{"query": "黃泉使者 劇情 解說", "pack_id": pack["id"]}]
        assert summary["query_resolution"]["local_answerable"] is False
        assert summary["query_resolution"]["research_status"] == "queued"
        assert summary["query_resolution"]["local_rejected_by_topic_count"] == 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_audience_question_accepts_single_matching_topic_fact_card(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "video_id": "video-a",
            "live_chat_id": "chat-a",
            "research_enabled": True,
        })
        pack = storage.create_topic_pack({"title": "直播資料包"})
        yomi_entry = storage.create_topic_pack_entry(pack["id"], {
            "title": "黃泉使者 劇情與畫風解說",
            "body": "作品以柔和線條呈現黃泉使者的療癒氛圍，並用單元劇情收束角色情緒。",
            "source_type": "manual",
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_type": "superChatEvent",
            "message_text": "《黃泉使者》的畫風好治癒，可以講一下嗎？",
            "safe_message_text": "《黃泉使者》的畫風好治癒，可以講一下嗎？",
            "author_display_name": "觀眾A",
            "amount_display_string": "NT$75",
        })
        _mark_event_clean(storage, event)
        manager = YouTubeBridgeManager(storage, memoria_client_factory=OffTopicEmbeddingMemoriaClient)

        monkeypatch.setattr(manager, "_audience_query_intent_from_events", lambda _events: {
            "is_factual_question": True,
            "needs_external_search": True,
            "safe_search_allowed": True,
            "sanitized_query": "黃泉使者 劇情 解說",
            "topic_scope": "anime_work",
            "risk_label": "clean",
            "reason": "測試 query。",
        })
        monkeypatch.setattr(
            manager,
            "_topic_pack_entries_for_query",
            lambda *_args, **_kwargs: ([{**yomi_entry, "similarity": 0.556}], {"top_similarity": 0.556}),
        )

        context, summary = manager.build_external_context("live-a", event_ids=[event["id"]])

        assert "黃泉使者 劇情與畫風解說" in context["context_text"]
        assert "柔和線條" in context["context_text"]
        assert summary["query_resolution"]["local_answerable"] is True
        assert summary["query_resolution"]["research_status"] == "not_needed"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_audience_question_uses_completed_research_fact_card_on_next_injection():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "video_id": "video-a",
            "live_chat_id": "chat-a",
            "research_enabled": True,
        })
        pack = storage.create_topic_pack({"title": "直播資料包"})
        storage.create_topic_pack_entry(pack["id"], {
            "title": "不相關的美食資料",
            "body": "這張卡只描述拉麵湯頭，不能回答動畫聲優問題。",
            "source_type": "manual",
        })
        research_entry = storage.create_topic_pack_entry(pack["id"], {
            "title": "最新一話聲優陣容整理",
            "body": "summary: 官方與社群討論集中在主役聲線變化、配角登場時機與情緒爆發場面。",
            "source_type": "research_gate",
            "tags": ["research_gate"],
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        storage.upsert_topic_pack_entry_embedding(research_entry["id"], [0.0, 1.0], model="fake-embed", content_hash="voice")
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "最新一話的聲優陣容有什麼看點？",
            "author_display_name": "觀眾A",
        })
        _mark_event_clean(storage, event)
        manager = YouTubeBridgeManager(storage, memoria_client_factory=OffTopicEmbeddingMemoriaClient)

        payload, summary = manager.build_external_context("live-a")

        assert "最新一話的聲優陣容有什麼看點？" in payload["context_text"]
        assert "可驗證事實：官方與社群討論" in payload["context_text"]
        assert "拉麵湯頭" not in payload["context_text"]
        assert summary["query_resolution"]["local_answerable"] is True
        assert summary["query_resolution"]["research_status"] == "not_needed"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_audience_research_worker_records_completed_status(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "video_id": "video-a",
            "live_chat_id": "chat-a",
            "research_enabled": True,
        })
        pack = storage.create_topic_pack({"title": "直播資料包"})
        storage.link_topic_pack_to_session("live-a", pack["id"])
        manager = YouTubeBridgeManager(storage, memoria_client_factory=OffTopicEmbeddingMemoriaClient)
        calls: list[dict] = []

        def fake_research_request_sync(session_id: str, query: str, *, pack_id: int | None = None, enforce_cooldown: bool = True):
            calls.append({
                "session_id": session_id,
                "query": query,
                "pack_id": pack_id,
                "enforce_cooldown": enforce_cooldown,
            })
            created = storage.create_topic_pack_entry(int(pack_id or pack["id"]), {
                "title": "最新一話聲優陣容整理",
                "body": "summary: 官方與社群討論集中在主役聲線變化、配角登場時機與情緒爆發場面。",
                "source_type": "research_gate",
                "tags": ["research_gate"],
            })
            return {
                "status": "completed",
                "entry": created,
                "record": {"status": "completed_with_results"},
                "embedding": None,
            }

        monkeypatch.setattr(manager, "_research_request_sync", fake_research_request_sync, raising=False)

        manager._run_audience_research_worker(
            "live-a",
            "voice-cast",
            "最新一話的聲優陣容有什麼看點？",
            pack_id=pack["id"],
        )

        assert calls
        assert calls[0]["session_id"] == "live-a"
        assert "最新一話的聲優陣容" in calls[0]["query"]
        state = storage.get_director_state("live-a")
        jobs = state["metadata"]["audience_query_research"]
        assert jobs["voice-cast"]["status"] == "completed_with_results"
        assert jobs["voice-cast"]["in_progress"] is False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_audience_research_worker_cleanup_does_not_create_missing_runtime(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "video_id": "video-a",
            "live_chat_id": "chat-a",
            "research_enabled": True,
        })
        manager = YouTubeBridgeManager(storage, memoria_client_factory=OffTopicEmbeddingMemoriaClient)
        assert "live-a" not in manager._runtimes

        def fake_research_request_sync(session_id: str, query: str, *, pack_id: int | None = None, enforce_cooldown: bool = True):
            return {
                "status": "completed_with_results",
                "entry": {"id": 0, "pack_id": 0},
                "record": {"status": "completed_with_results"},
                "embedding": None,
            }

        monkeypatch.setattr(manager, "_research_request_sync", fake_research_request_sync, raising=False)

        manager._run_audience_research_worker(
            "live-a",
            "voice-cast",
            "最新一話的聲優陣容有什麼看點？",
            pack_id=None,
        )

        assert "live-a" not in manager._runtimes
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_audience_research_worker_requeues_stale_in_progress_job(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "video_id": "video-a",
            "live_chat_id": "chat-a",
            "research_enabled": True,
        })
        manager = YouTubeBridgeManager(storage, memoria_client_factory=OffTopicEmbeddingMemoriaClient)
        query = "最新一話的聲優陣容有什麼看點？"
        query_key = manager._audience_query_key("live-a", query)
        manager._update_audience_research_job("live-a", query_key, {
            "status": "running",
            "in_progress": True,
            "query": query,
            "pack_id": 0,
            "started_at": "2026-05-01T00:00:00",
            "updated_at": "2026-05-01T00:00:00",
            "error": "",
        })
        started_threads: list[dict] = []

        class FakeThread:
            def __init__(self, *, target, args, kwargs, name, daemon):
                self.target = target
                self.args = args
                self.kwargs = kwargs
                self.name = name
                self.daemon = daemon

            def is_alive(self):
                return False

            def start(self):
                started_threads.append({
                    "target": self.target,
                    "args": self.args,
                    "kwargs": self.kwargs,
                    "name": self.name,
                    "daemon": self.daemon,
                })

        monkeypatch.setattr(bridge_engine.threading, "Thread", FakeThread)

        result = manager._ensure_audience_research_worker(
            storage.get_session("live-a"),
            query,
            pack_id=None,
        )

        assert result["status"] == "queued"
        assert started_threads
        refreshed = manager._audience_research_job("live-a", query_key)
        assert refreshed["status"] == "queued"
        assert refreshed["in_progress"] is True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_completed_audience_research_card_is_used_even_without_embedding(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "video_id": "video-a",
            "live_chat_id": "chat-a",
            "research_enabled": True,
        })
        pack = storage.create_topic_pack({"title": "直播資料包"})
        storage.link_topic_pack_to_session("live-a", pack["id"])
        research_entry = storage.create_topic_pack_entry(pack["id"], {
            "title": "無向量但已完成的聲優資料",
            "body": "summary: worker 已整理出聲優陣容、配角登場與情緒爆發場面。",
            "source_type": "research_gate",
            "tags": ["research_gate"],
        })
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "最新一話的聲優陣容有什麼看點？",
            "author_display_name": "觀眾A",
        })
        _mark_event_clean(storage, event)
        manager = YouTubeBridgeManager(storage, memoria_client_factory=OffTopicEmbeddingMemoriaClient)
        monkeypatch.setattr(manager, "_ensure_session_topic_pack_embeddings", lambda _session_id: None)
        query = "最新一話的聲優陣容有什麼看點？"
        query_key = manager._audience_query_key("live-a", query)
        manager._update_audience_research_job("live-a", query_key, {
            "status": "completed_with_results",
            "in_progress": False,
            "query": query,
            "entry_id": research_entry["id"],
            "pack_id": pack["id"],
        })

        payload, summary = manager.build_external_context("live-a")

        assert "可驗證事實：worker 已整理出聲優陣容" in payload["context_text"]
        assert summary["query_resolution"]["research_status"] == "completed_with_results"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_maybe_replenish_fact_cards_is_removed_and_never_generates(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "video_id": "video-a",
            "live_chat_id": "chat-a",
            "director_guidance": "本場只聊動畫新番。",
        })
        pack = storage.create_topic_pack({"title": "動畫新番資料包"})
        entry = storage.create_topic_pack_entry(pack["id"], {
            "title": "最新話作畫爭議",
            "body": "第 6 話遠景人物線條簡化。",
            "source_type": "factcards_folder",
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        storage.record_topic_pack_entry_usages(
            "live-a",
            [{"id": entry["id"], "pack_id": pack["id"], "similarity": 0.9}],
            query_text="作畫崩壞",
            usage_source="external_context",
        )
        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeEmbeddingMemoriaClient)

        assert not hasattr(manager, "_run_fact_card_replenishment_worker_process")

        result = manager.maybe_replenish_fact_cards(
            "live-a",
            reason="low_unused",
            topic_hint="第 6 話作畫崩壞和社群討論",
        )

        assert result["triggered"] is False
        assert result["reason"] == "fact_card_replenishment_removed"
        assert "fact_card_replenishment" not in storage.get_director_state("live-a")["metadata"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_topic_pack_context_usage_record_does_not_schedule_replenishment(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "video_id": "video-a",
            "live_chat_id": "chat-a",
        })
        pack = storage.create_topic_pack({"title": "動畫新番資料包"})
        storage.create_topic_pack_entry(pack["id"], {
            "title": "最新話作畫爭議",
            "body": "第 6 話遠景人物線條簡化。",
            "source_type": "factcards_folder",
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeEmbeddingMemoriaClient)

        def fail_replenish(*_args, **_kwargs):
            raise AssertionError("usage 記錄不應觸發自動補卡")

        monkeypatch.setattr(manager, "maybe_replenish_fact_cards", fail_replenish)

        context = manager._topic_pack_context_for_query(
            "live-a",
            "最新一話作畫爭議",
            usage_source="external_context",
        )

        assert "最新話作畫爭議" in context
        stats = storage.get_topic_pack_usage_stats("live-a")
        assert stats["used_entry_count"] == 1
        assert stats["entries"][0]["usage_count"] == 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_import_fact_cards_folder_to_pack_initializes_without_live_session():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeEmbeddingMemoriaClient)
        fact_cards_dir = tmp_dir / "FactCards"
        fact_cards_dir.mkdir()
        (fact_cards_dir / "anime-detail.md").write_text(
            "# 動畫新番細節\n\n"
            "## Summary\n"
            "整理四月新番最新話與社群討論。\n\n"
            "## Facts\n"
            "### 作畫討論\n"
            "第 5 話的動作場面和角色表情是直播可引用的討論點。\n",
            encoding="utf-8",
        )

        result = manager.import_fact_cards_folder_to_pack(fact_cards_dir=fact_cards_dir, max_files=10)

        assert "session_id" not in result
        assert result["created_count"] == 1
        assert result["embedding_count"] == 1
        pack = storage.get_topic_pack(result["pack_id"])
        assert pack["title"] == "動畫新番 FactCards"
        entries = storage.list_topic_pack_entries(result["pack_id"])
        assert len(entries) == 1
        assert entries[0]["title"] == "作畫討論"
        assert storage.get_topic_pack_entry_embedding(entries[0]["id"]) is not None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_topic_pack_usage_status_marks_research_gate_degraded_entries():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "api_key": "key",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "video_id": "video-a",
            "live_chat_id": "chat-a",
        })
        pack = storage.create_topic_pack({"title": "Research Gate"})
        storage.link_topic_pack_to_session("live-a", pack["id"])
        storage.create_topic_pack_entry(pack["id"], {
            "title": "成功資料",
            "body": "summary: 官方公開最新一話資訊\nfacts:\n- 第 6 話演出重點\nconfidence: medium\nstatus: completed_with_results",
            "source_url": "https://example.com/anime-news",
            "source_type": "research_gate",
        })
        storage.create_topic_pack_entry(pack["id"], {
            "title": "無結果",
            "body": "summary: Research Gate 沒有取得可用摘要\nconfidence: low\nstatus: completed_no_results",
            "source_type": "research_gate",
        })
        storage.create_topic_pack_entry(pack["id"], {
            "title": "raw dump",
            "body": '{"search_results":[{"title":"raw","content":"dump"}]}',
            "source_type": "research_gate",
        })
        storage.create_research_request(
            "live-a",
            "缺 key 測試",
            status="failed",
            metadata={"error": "missing TAVILY_API_KEY"},
        )
        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeEmbeddingMemoriaClient)

        usage = manager.get_topic_pack_usage_status("live-a")

        assert usage["research_gate"]["total_count"] == 4
        assert usage["research_gate"]["success_count"] == 1
        assert usage["research_gate"]["degraded_count"] == 3
        assert usage["research_gate"]["statuses"] == {
            "success": 1,
            "completed_no_results": 1,
            "raw_dump": 1,
            "failed": 1,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_auto_build_topic_pack_method_is_removed():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeEmbeddingMemoriaClient)

        assert not hasattr(manager, "auto_build_topic_pack")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_research_result_to_fact_card_is_structured_and_not_raw_dump():
    raw_result = {
        "search_results": [
            {
                "title": "官方動畫網站",
                "url": "https://anime.example/official",
                "content": "作品 A 於 2026 年 4 月播出，動畫製作由 Example Studio 負責。",
            },
            {
                "title": "社群討論串",
                "url": "https://social.example/thread",
                "content": "有人覺得作品 A 很好看，但內容偏主觀。",
            },
        ]
    }

    card = YouTubeBridgeManager._research_result_to_fact_card("作品 A 播出資訊", raw_result)

    assert "search_results" not in card
    assert "summary:" in card
    assert "facts:" in card
    assert "source_titles:" in card
    assert "source_urls:" in card
    assert "status: completed_with_results" in card
    assert "https://anime.example/official" in card

def test_research_items_prefers_structured_tavily_results():
    raw_result = {
        "search_results": "[1] 舊格式標題\n舊格式摘要",
        "results": [
            {
                "title": "結構化來源",
                "url": "https://example.com/source",
                "content": "這是含有 URL 的結構化 Tavily 結果。",
            }
        ],
    }

    items = YouTubeBridgeManager._research_items(raw_result)

    assert items == [{
        "title": "結構化來源",
        "url": "https://example.com/source",
        "content": "這是含有 URL 的結構化 Tavily 結果。",
    }]

def test_research_items_supports_legacy_tavily_text_results():
    raw_result = {
        "search_results": "[1] 舊格式標題\n舊格式摘要第一句。\n舊格式摘要第二句。"
    }

    items = YouTubeBridgeManager._research_items(raw_result)

    assert items[0]["title"] == "舊格式標題"
    assert "舊格式摘要第一句" in items[0]["content"]
