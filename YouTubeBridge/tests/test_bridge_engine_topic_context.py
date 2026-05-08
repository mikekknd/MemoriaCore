import asyncio
import contextlib
import json
import shutil
import subprocess
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


def test_build_external_context_uses_compact_llm_lines():
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
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "被看到大型debug現場",
            "author_display_name": "@yodawnla",
            "author_channel_id": "UCFakeChannelId",
            "message_type": "textMessageEvent",
            "published_at": "2026-05-02T15:53:17.8658+00:00",
        })
        _mark_event_clean(storage, event)

        payload, summary = YouTubeBridgeManager(
            storage,
            memoria_client_factory=FakeEmbeddingMemoriaClient,
        ).build_external_context("live-a")

        assert summary["event_count"] == 1
        assert payload["context_text"] == "- @yodawnla: 被看到大型debug現場"
        assert "2026-05-02T15:53:17.8658+00:00" not in payload["context_text"]
        assert "textMessageEvent" not in payload["context_text"]
        assert "UCFakeChannelId" not in payload["context_text"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_build_external_context_retrieves_relevant_topic_pack_fact_cards():
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
        pack = storage.create_topic_pack({"title": "直播資料包"})
        anime = storage.create_topic_pack_entry(pack["id"], {
            "title": "四月新番",
            "body": "四月新番包含動畫作品、製作公司與播出資訊。",
            "source_type": "manual",
        })
        food = storage.create_topic_pack_entry(pack["id"], {
            "title": "拉麵",
            "body": "豚骨拉麵是濃厚系美食主題。",
            "source_type": "manual",
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        storage.upsert_topic_pack_entry_embedding(anime["id"], [1.0, 0.0], model="fake-embed", content_hash="anime")
        storage.upsert_topic_pack_entry_embedding(food["id"], [0.0, 1.0], model="fake-embed", content_hash="food")
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "四月新番有哪些作品可以聊？",
            "author_display_name": "觀眾A",
        })
        _mark_event_clean(storage, event)

        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeEmbeddingMemoriaClient)
        payload, _summary = manager.build_external_context("live-a")

        assert "四月新番包含動畫作品" in payload["context_text"]
        assert "豚骨拉麵" not in payload["context_text"]
        stats = storage.get_topic_pack_usage_stats("live-a")
        anime_stat = next(item for item in stats["entries"] if item["entry_id"] == anime["id"])
        food_stat = next(item for item in stats["entries"] if item["entry_id"] == food["id"])
        assert anime_stat["usage_count"] == 1
        assert anime_stat["usage_sources"] == ["external_context"]
        assert food_stat["usage_count"] == 0
        assert stats["used_entry_count"] == 1
        assert stats["unused_entry_count"] == 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_audience_question_graph_expands_related_fact_cards_and_records_trace():
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
        pack = storage.create_topic_pack({"title": "直播資料包"})
        magic = storage.create_topic_pack_entry(pack["id"], {
            "title": "《魔法帽的工作室》：精緻奇幻新作正式攻頂",
            "body": "Anime Corner 第 4 週以 9.20% 拿下第 1，第一次把《Re:從零開始的異世界生活 第四季》拉下冠軍。",
            "source_type": "factcards_folder",
        })
        dragon = storage.create_topic_pack_entry(pack["id"], {
            "title": "第 5 話「巨鱗龍迷宮」：龍、迷宮與可可的臨場創意",
            "body": "可可一行人被困在迷宮，巨鱗龍阻擋她們靠近魔法陣。",
            "source_type": "factcards_folder",
        })
        rezero = storage.create_topic_pack_entry(pack["id"], {
            "title": "《Re:從零開始的異世界生活 第四季》：續作霸權仍然是春番基本盤",
            "body": "前三週連續拿下冠軍，第 4 週退到第 3 後仍保住前段班。",
            "source_type": "factcards_folder",
        })
        food = storage.create_topic_pack_entry(pack["id"], {
            "title": "拉麵",
            "body": "豚骨拉麵是濃厚系美食主題。",
            "source_type": "manual",
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        storage.upsert_topic_pack_entry_embedding(magic["id"], [1.0, 0.0], model="fake-embed", content_hash="magic")
        storage.upsert_topic_pack_entry_embedding(dragon["id"], [0.0, 1.0], model="fake-embed", content_hash="dragon")
        storage.upsert_topic_pack_entry_embedding(rezero["id"], [0.0, 1.0], model="fake-embed", content_hash="rezero")
        storage.upsert_topic_pack_entry_embedding(food["id"], [0.0, 1.0], model="fake-embed", content_hash="food")
        storage.replace_topic_graph(
            pack["id"],
            nodes=[
                {"node_key": f"entry:{magic['id']}", "entry_id": magic["id"], "node_type": "topic", "title": magic["title"], "summary": magic["body"]},
                {"node_key": f"entry:{dragon['id']}", "entry_id": dragon["id"], "node_type": "detail", "title": dragon["title"], "summary": dragon["body"]},
                {"node_key": f"entry:{rezero['id']}", "entry_id": rezero["id"], "node_type": "topic", "title": rezero["title"], "summary": rezero["body"]},
                {"node_key": f"entry:{food['id']}", "entry_id": food["id"], "node_type": "topic", "title": food["title"], "summary": food["body"]},
            ],
            edges=[
                {"source_node_key": f"entry:{dragon['id']}", "target_node_key": f"entry:{magic['id']}", "edge_type": "detail_of", "weight": 0.95},
                {"source_node_key": f"entry:{magic['id']}", "target_node_key": f"entry:{rezero['id']}", "edge_type": "compare_with", "weight": 0.75},
            ],
        )
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "魔法帽攻頂有什麼可以深入比較的細節？",
            "author_display_name": "觀眾A",
        })
        _mark_event_clean(storage, event)

        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeEmbeddingMemoriaClient)
        payload, _summary = manager.build_external_context("live-a")

        assert "精緻奇幻新作正式攻頂" in payload["context_text"]
        assert "巨鱗龍迷宮" in payload["context_text"]
        assert "續作霸權仍然是春番基本盤" in payload["context_text"]
        assert "豚骨拉麵" not in payload["context_text"]
        assert "召回策略" in payload["context_text"]
        assert "[深挖]" in payload["context_text"]
        assert "[關聯]" in payload["context_text"]
        trace = storage.get_latest_topic_graph_retrieval_trace("live-a")
        assert trace is not None
        assert trace["source"] == "external_context"
        assert trace["entry_node_ids"]
        assert set(trace["selected_node_ids"]) >= set(trace["entry_node_ids"])
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_build_external_context_uses_one_sequential_topic_when_no_audience_question():
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
        first = storage.create_topic_pack_entry(pack["id"], {
            "title": "第一話開場演出",
            "body": "第一話用長鏡頭建立舞台與角色關係。",
            "source_type": "factcards_folder",
        })
        second = storage.create_topic_pack_entry(pack["id"], {
            "title": "第二話作畫變化",
            "body": "第二話戰鬥段落的遠景線條簡化。",
            "source_type": "factcards_folder",
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        for entry in (first, second):
            storage.upsert_topic_pack_entry_embedding(entry["id"], [1.0, 0.0], model="fake-embed", content_hash=str(entry["id"]))
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "想聽你們聊剛剛的新番節奏",
            "author_display_name": "觀眾A",
        })
        _mark_event_clean(storage, event)

        payload, _summary = YouTubeBridgeManager(storage, memoria_client_factory=FakeEmbeddingMemoriaClient).build_external_context("live-a")

        assert "第一話用長鏡頭" in payload["context_text"]
        assert "第二話戰鬥段落" not in payload["context_text"]
        stats = storage.get_topic_pack_usage_stats("live-a")
        first_stat = next(item for item in stats["entries"] if item["entry_id"] == first["id"])
        second_stat = next(item for item in stats["entries"] if item["entry_id"] == second["id"])
        assert first_stat["usage_count"] == 1
        assert second_stat["usage_count"] == 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_sequential_topic_context_expands_detail_from_topic_graph():
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
            "director_guidance": "本場只聊魔法帽。",
        })
        pack = storage.create_topic_pack({"title": "動畫新番資料包"})
        topic = storage.create_topic_pack_entry(pack["id"], {
            "title": "《魔法帽的工作室》：精緻奇幻新作正式攻頂",
            "body": "新作攻頂，挑戰續作霸權。",
            "source_type": "factcards_folder",
        })
        detail = storage.create_topic_pack_entry(pack["id"], {
            "title": "第 5 話「巨鱗龍迷宮」：龍、迷宮與可可的臨場創意",
            "body": "可可一行人靠規則與創意解開危機。",
            "source_type": "factcards_folder",
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        storage.replace_topic_graph(
            pack["id"],
            nodes=[
                {"node_key": f"entry:{topic['id']}", "entry_id": topic["id"], "node_type": "topic", "title": topic["title"], "summary": topic["body"]},
                {"node_key": f"entry:{detail['id']}", "entry_id": detail["id"], "node_type": "detail", "title": detail["title"], "summary": detail["body"]},
            ],
            edges=[
                {"source_node_key": f"entry:{detail['id']}", "target_node_key": f"entry:{topic['id']}", "edge_type": "detail_of", "weight": 0.95},
            ],
        )
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "想聽你們聊剛剛的新番節奏",
            "author_display_name": "觀眾A",
        })
        _mark_event_clean(storage, event)

        payload, _summary = YouTubeBridgeManager(storage, memoria_client_factory=FakeEmbeddingMemoriaClient).build_external_context("live-a")

        assert "精緻奇幻新作正式攻頂" in payload["context_text"]
        assert "巨鱗龍迷宮" in payload["context_text"]
        trace = storage.get_latest_topic_graph_retrieval_trace("live-a")
        assert trace is not None
        assert trace["source"] == "external_context"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_sequential_topic_context_uses_only_topic_nodes_as_entry_points():
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
            "director_guidance": "本場只聊魔法帽。",
        })
        pack = storage.create_topic_pack({"title": "動畫新番資料包"})
        detail = storage.create_topic_pack_entry(pack["id"], {
            "title": "第 5 話「巨鱗龍迷宮」：龍、迷宮與可可的臨場創意",
            "body": "這是細節卡，不能被導播當成換題入口。",
            "source_type": "factcards_folder",
            "tags": ["20260507_magic_hat_deep_dive"],
        })
        topic = storage.create_topic_pack_entry(pack["id"], {
            "title": "《魔法帽的工作室》：精緻奇幻新作正式攻頂",
            "body": "這是入口卡，導播應該用它推進話題。",
            "source_type": "factcards_folder",
            "tags": ["topic_graph_role:entry"],
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        storage.replace_topic_graph(
            pack["id"],
            nodes=[
                {"node_key": f"entry:{detail['id']}", "entry_id": detail["id"], "node_type": "detail", "title": detail["title"], "summary": detail["body"]},
                {"node_key": f"entry:{topic['id']}", "entry_id": topic["id"], "node_type": "topic", "title": topic["title"], "summary": topic["body"]},
            ],
            edges=[
                {"source_node_key": f"entry:{detail['id']}", "target_node_key": f"entry:{topic['id']}", "edge_type": "detail_of", "weight": 0.95},
            ],
        )
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "想聽你們聊剛剛的新番節奏",
            "author_display_name": "觀眾A",
        })
        _mark_event_clean(storage, event)

        payload, _summary = YouTubeBridgeManager(storage, memoria_client_factory=FakeEmbeddingMemoriaClient).build_external_context("live-a")

        assert "[入口] 《魔法帽的工作室》" in payload["context_text"]
        assert "[入口] 第 5 話" not in payload["context_text"]
        assert "[深挖] 第 5 話" in payload["context_text"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_audience_question_hitting_detail_card_includes_parent_topic_entry():
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
            "research_enabled": False,
        })
        pack = storage.create_topic_pack({"title": "動畫新番資料包"})
        topic = storage.create_topic_pack_entry(pack["id"], {
            "title": "《魔法帽的工作室》：精緻奇幻新作正式攻頂",
            "body": "魔法帽攻頂，是目前討論入口。",
            "source_type": "factcards_folder",
            "tags": ["topic_graph_role:entry"],
        })
        detail = storage.create_topic_pack_entry(pack["id"], {
            "title": "第 5 話「巨鱗龍迷宮」：龍、迷宮與可可的臨場創意",
            "body": "巨鱗龍迷宮的細節是本話深挖內容。",
            "source_type": "factcards_folder",
            "tags": ["topic_graph_role:detail"],
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        storage.upsert_topic_pack_entry_embedding(topic["id"], [0.0, 1.0], model="fake-embed", content_hash="topic")
        storage.upsert_topic_pack_entry_embedding(detail["id"], [1.0, 0.0], model="fake-embed", content_hash="detail")
        storage.replace_topic_graph(
            pack["id"],
            nodes=[
                {"node_key": f"entry:{topic['id']}", "entry_id": topic["id"], "node_type": "topic", "title": topic["title"], "summary": topic["body"]},
                {"node_key": f"entry:{detail['id']}", "entry_id": detail["id"], "node_type": "detail", "title": detail["title"], "summary": detail["body"]},
            ],
            edges=[
                {"source_node_key": f"entry:{detail['id']}", "target_node_key": f"entry:{topic['id']}", "edge_type": "detail_of", "weight": 0.95},
            ],
        )
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "巨鱗龍迷宮那段可以查一下細節嗎？",
            "author_display_name": "觀眾A",
        })
        _mark_event_clean(storage, event)

        payload, _summary = YouTubeBridgeManager(storage, memoria_client_factory=FakeEmbeddingMemoriaClient).build_external_context("live-a")

        assert "[入口] 《魔法帽的工作室》" in payload["context_text"]
        assert "[深挖] 第 5 話" in payload["context_text"]
        assert payload["context_text"].index("[入口] 《魔法帽的工作室》") < payload["context_text"].index("[深挖] 第 5 話")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_sequential_topic_context_advances_after_three_recalls():
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
        first = storage.create_topic_pack_entry(pack["id"], {
            "title": "第一話開場演出",
            "body": "第一話用長鏡頭建立舞台與角色關係。",
            "source_type": "factcards_folder",
        })
        second = storage.create_topic_pack_entry(pack["id"], {
            "title": "第二話作畫變化",
            "body": "第二話戰鬥段落的遠景線條簡化。",
            "source_type": "factcards_folder",
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        storage.record_topic_pack_entry_usages(
            "live-a",
            [{"id": first["id"], "pack_id": pack["id"], "similarity": 0.0}],
            query_text="topic sequence",
            usage_source="external_context",
        )
        storage.record_topic_pack_entry_usages(
            "live-a",
            [{"id": first["id"], "pack_id": pack["id"], "similarity": 0.0}],
            query_text="topic sequence",
            usage_source="external_context",
        )
        storage.record_topic_pack_entry_usages(
            "live-a",
            [{"id": first["id"], "pack_id": pack["id"], "similarity": 0.0}],
            query_text="topic sequence",
            usage_source="external_context",
        )
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "這段討論可以繼續延伸",
            "author_display_name": "觀眾A",
        })
        _mark_event_clean(storage, event)

        payload, _summary = YouTubeBridgeManager(storage, memoria_client_factory=FakeEmbeddingMemoriaClient).build_external_context("live-a")

        assert "第一話用長鏡頭" not in payload["context_text"]
        assert "第二話戰鬥段落" in payload["context_text"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_audience_query_detection_uses_prompt_schema_contract():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        manager = YouTubeBridgeManager(storage, memoria_client_factory=ContractOnlyQueryClient)
        event = {
            "message_text": "STAFF名單想補一下",
            "safe_message_text": "STAFF名單想補一下",
            "safety_status": "completed",
            "safety_label": "clean",
        }

        query = manager._audience_query_text_from_events([event])

        assert query == "動畫新番 STAFF 名單與演出看點"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_audience_question_does_not_fallback_to_unrelated_fact_cards():
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
            "research_enabled": False,
        })
        pack = storage.create_topic_pack({"title": "直播資料包"})
        entry = storage.create_topic_pack_entry(pack["id"], {
            "title": "完全不相關的拉麵資料",
            "body": "豚骨拉麵的湯頭通常使用豬骨長時間熬煮。",
            "source_type": "manual",
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])
        storage.upsert_topic_pack_entry_embedding(entry["id"], [1.0, 0.0], model="fake-embed", content_hash="ramen")
        event = storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "這部動畫的聲優陣容是誰？",
            "author_display_name": "觀眾A",
        })
        _mark_event_clean(storage, event)

        manager = YouTubeBridgeManager(storage, memoria_client_factory=OffTopicEmbeddingMemoriaClient)
        payload, summary = manager.build_external_context("live-a")

        assert "這部動畫的聲優陣容是誰？" in payload["context_text"]
        assert "豚骨拉麵" not in payload["context_text"]
        assert summary["query_resolution"]["local_answerable"] is False
        assert summary["query_resolution"]["research_status"] == "disabled"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def test_topic_pack_usage_stats_counts_similarity_and_recent_repeats():
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
        entry = storage.create_topic_pack_entry(pack["id"], {
            "title": "最新話作畫爭議",
            "body": "第 6 話遠景人物線條簡化，社群正在討論這是排程壓力還是演出取捨。",
            "source_type": "factcards_folder",
        })
        storage.link_topic_pack_to_session("live-a", pack["id"])

        for score in (0.92, 0.88, 0.82):
            storage.record_topic_pack_entry_usages(
                "live-a",
                [{"id": entry["id"], "pack_id": pack["id"], "similarity": score}],
                query_text="最新一話 作畫崩壞 超展開",
                usage_source="manual_search",
            )

        stats = storage.get_topic_pack_usage_stats("live-a", recent_limit=8)

        assert stats["total_entries"] == 1
        assert stats["used_entry_count"] == 1
        assert stats["unused_entry_count"] == 0
        assert stats["low_unused"] is True
        assert stats["repeated_entry"]["entry_id"] == entry["id"]
        assert stats["repeated_entry"]["recent_count"] == 3
        assert stats["entries"][0]["usage_count"] == 3
        assert stats["entries"][0]["avg_similarity"] == pytest.approx((0.92 + 0.88 + 0.82) / 3)
        assert stats["entries"][0]["last_used_at"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
