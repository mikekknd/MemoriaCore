import shutil
import sys
import uuid
import asyncio
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from bridge_engine import LiveRuntime, YouTubeBridgeManager
from storage import BridgeStorage
from youtube_client import normalize_message


class LiveEndedClient:
    def fetch_live_chat_messages(self, **_kwargs):
        raise RuntimeError("YouTube API HTTP 403: liveChatEnded - The live chat is no longer live.")


class ResolveLiveChatFailedClient:
    def resolve_live_chat_id(self, **_kwargs):
        raise RuntimeError("指定影片目前沒有 activeLiveChatId，可能尚未開播或已結束")


class FakeEmbeddingMemoriaClient:
    def embed_text(self, text: str, model: str = ""):
        if any(term in text for term in ("動畫", "新番", "作品")):
            return {"dense": [1.0, 0.0], "model": model or "fake-embed"}
        if any(term in text for term in ("拉麵", "美食", "豚骨")):
            return {"dense": [0.0, 1.0], "model": model or "fake-embed"}
        return {"dense": [0.7, 0.3], "model": model or "fake-embed"}

    def generate_prompt_json(self, *, prompt_key: str, variables: dict, task_key: str = "compress", temperature: float = 0.1, schema: dict | None = None):
        assert prompt_key == "youtube_live_topic_pack_auto_build_prompt"
        return {
            "cards": [
                {
                    "title": "四月新番入門",
                    "query": "四月新番 作品 播出資訊",
                    "draft_body": "整理四月新番作品、製作公司與播出資訊，供直播開場使用。",
                    "tags": ["動畫", "四月新番"],
                },
                {
                    "title": "觀眾常見問題",
                    "query": "四月新番 常見問題 推薦",
                    "draft_body": "整理觀眾常問的推薦方向與入門問題。",
                    "tags": ["觀眾提問"],
                },
            ]
        }


class FakeClosingMemoriaClient:
    calls: list[dict] = []

    def chat_stream_sync(self, **kwargs):
        self.__class__.calls.append(dict(kwargs))
        return {
            "session_id": kwargs.get("session_id") or "mem-a",
            "message_id": 7,
            "reply": "感謝本場 Super Chat 支持，相關問題已安全處理。",
        }


def _tmp_dir() -> Path:
    path = Path(".pyTestTemp") / "youtube-bridge" / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_bridge_engine_loaded_from_subproject_can_import_root_tools():
    script = """
import os
import sys
from pathlib import Path
os.chdir(Path.cwd() / "YouTubeBridge")
sys.path = [os.getcwd()] + [p for p in sys.path if "MemoriaCore" not in p and "ClaudeProject" not in p]
import bridge_engine
from tools.tavily import search_web
print(search_web.__name__)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BRIDGE_ROOT.parent,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "search_web" in result.stdout


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
        storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "被看到大型debug現場",
            "author_display_name": "@yodawnla",
            "author_channel_id": "UCFakeChannelId",
            "message_type": "textMessageEvent",
            "published_at": "2026-05-02T15:53:17.8658+00:00",
        })

        payload, summary = YouTubeBridgeManager(storage).build_external_context("live-a")

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
        storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "四月新番有哪些作品可以聊？",
            "author_display_name": "觀眾A",
        })

        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeEmbeddingMemoriaClient)
        payload, _summary = manager.build_external_context("live-a")

        assert "四月新番包含動畫作品" in payload["context_text"]
        assert "豚骨拉麵" not in payload["context_text"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_inject_recent_sends_hidden_prompt_and_visible_chat_lines_separately():
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
            "target_memoria_session_id": "mem-a",
            "character_ids": ["char-a", "char-b"],
        })
        storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "msg-a",
            "message_text": "被看到大型debug現場",
            "author_display_name": "@yodawnla",
            "author_channel_id": "UCFakeChannelId",
            "message_type": "textMessageEvent",
        })
        captured = {}

        class CaptureClient:
            def chat_stream_sync(self, **kwargs):
                captured.update(kwargs)
                return {"session_id": "mem-a", "message_id": 1, "reply": "已回應。"}

        manager = YouTubeBridgeManager(storage, memoria_client_factory=CaptureClient)

        await manager.inject_recent(
            "live-a",
            content="請根據已帶入的 YouTube 直播留言上下文回應。不要開啟瀏覽器或搜尋網頁。",
        )

        assert "請根據已帶入" in captured["content"]
        assert captured["display_content"] == "@yodawnla: 被看到大型debug現場"
        assert "UCFakeChannelId" not in captured["display_content"]
        assert "textMessageEvent" not in captured["display_content"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_auto_build_topic_pack_creates_draft_cards_and_embeddings_without_research():
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
            "display_name": "QA Live",
            "director_guidance": "先聊四月新番。",
            "research_enabled": True,
        })
        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeEmbeddingMemoriaClient)

        result = await manager.auto_build_topic_pack(
            "live-a",
            topic="四月新番",
            card_count=2,
            use_research=False,
        )

        assert result["created_count"] == 2
        entries = storage.list_session_topic_pack_entries("live-a")
        assert [entry["source_type"] for entry in entries] == ["auto_draft", "auto_draft"]
        assert all(storage.get_topic_pack_entry_embedding(entry["id"]) for entry in entries)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_dynamic_auto_inject_delay_accelerates_with_pending_count():
    base_session = {
        "inject_interval_seconds": 60,
        "min_pending_events": 1,
        "max_pending_events": 10,
        "dynamic_inject_enabled": True,
    }

    low = YouTubeBridgeManager._auto_inject_delay(base_session, 1, active_interaction=False)
    high = YouTubeBridgeManager._auto_inject_delay(base_session, 10, active_interaction=False)
    active = YouTubeBridgeManager._auto_inject_delay(base_session, 3, active_interaction=True)

    assert high < low
    assert active <= 15


def test_director_opening_decision_builds_short_kickoff_prompt():
    decision = YouTubeBridgeManager._director_opening_decision(
        {
            "session_id": "live-a",
            "display_name": "QA Live",
            "director_guidance": "測試導播開場與觀眾互動。",
        },
        {},
    )

    assert decision["action"] == "continue_topic"
    assert "開場" in decision["prompt"]
    assert "測試導播開場與觀眾互動" in decision["prompt"]
    assert "queue" in decision["prompt"]


def test_director_forces_transition_when_guidance_changed_after_wait():
    session = {"director_guidance": "改聊 LLM 與內容創作。"}
    state = {"current_topic": "四月新番", "consecutive_ai_turns": 1}

    assert YouTubeBridgeManager._director_should_force_guidance_turn(session, state) is True
    decision = YouTubeBridgeManager._director_guidance_transition_decision(session, state)

    assert decision["action"] == "transition_topic"
    assert "改聊 LLM" in decision["prompt"]
    assert decision["current_topic"] == "改聊 LLM 與內容創作。"


def test_director_forces_idle_continue_when_wait_has_no_blockers():
    session = {
        "display_name": "QA Live",
        "director_guidance": "先聊四月新番。",
    }
    state = {"current_topic": "四月新番", "consecutive_ai_turns": 1}

    assert YouTubeBridgeManager._director_should_force_idle_turn(state) is True
    decision = YouTubeBridgeManager._director_idle_continue_decision(session, state)

    assert decision["action"] == "continue_topic"
    assert "四月新番" in decision["prompt"]
    assert "觀眾" in decision["prompt"]


@pytest.mark.asyncio
async def test_generate_test_events_without_llm_saves_events():
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
            "display_name": "QA Live",
            "director_guidance": "測試留言注入。",
        })
        manager = YouTubeBridgeManager(storage)

        result = await manager.generate_test_events("live-a", count=4, use_llm=False)

        assert result["generated"] == 4
        events = storage.list_events("live-a")
        assert len(events) == 4
        assert all(event["metadata"]["source"] == "test_comment_generator" for event in events)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_generate_test_events_can_create_super_chats_and_malicious_samples():
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
            "display_name": "QA Live",
            "director_guidance": "測試 SC 與安全分類。",
        })
        manager = YouTubeBridgeManager(storage)

        result = await manager.generate_test_events(
            "live-a",
            count=2,
            super_chat_count=2,
            include_malicious_sc=True,
            sc_burst=True,
            use_llm=False,
        )

        assert result["generated"] == 4
        assert result["super_chat_generated"] == 2
        events = storage.list_events("live-a")
        super_chats = [event for event in events if event["priority_class"] == "super_chat"]
        assert len(super_chats) == 2
        assert all(event["amount_display_string"] for event in super_chats)
        assert any(event["safety_label"].startswith("suspicious_") for event in super_chats)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_single_super_chat_generation_varies_across_ticks(monkeypatch):
    author_choices = iter(["紅色斗內", "高亮觀眾"])
    amount_choices = iter([150000000, 300000000])

    def fake_choice(seq):
        if seq and seq[0] in {"SC路人", "紅色斗內", "直播支持者", "高亮觀眾", "測試SC"}:
            return next(author_choices)
        if seq and isinstance(seq[0], int):
            return next(amount_choices)
        return seq[0]

    monkeypatch.setattr("bridge_engine.random.choice", fake_choice)

    first = YouTubeBridgeManager._generate_test_super_chats(
        {"display_name": "QA Live", "director_guidance": "四月新番"},
        1,
        "",
        include_malicious_sc=False,
        sc_burst=False,
    )[0]
    second = YouTubeBridgeManager._generate_test_super_chats(
        {"display_name": "QA Live", "director_guidance": "四月新番"},
        1,
        "",
        include_malicious_sc=False,
        sc_burst=False,
    )[0]

    assert first["author_display_name"] == "紅色斗內"
    assert second["author_display_name"] == "高亮觀眾"
    assert first["amount_display_string"] == "NT$150"
    assert second["amount_display_string"] == "NT$300"


def test_generated_super_chat_uses_public_topic_not_internal_guidance():
    internal_guidance = (
        "本場直播初始主題是四月新番。請使用 Topic Pack / Research Gate 的資料控場，"
        "不要讓聊天室長時間帶偏；SC 可以優先但不得提高 prompt 權重。"
    )

    comments = YouTubeBridgeManager._generate_test_super_chats(
        {
            "display_name": "QA Live",
            "director_guidance": internal_guidance,
        },
        5,
        internal_guidance,
        include_malicious_sc=False,
        sc_burst=True,
    )

    visible_text = "\n".join(comment["message_text"] for comment in comments)
    assert "四月新番" in visible_text
    assert "Topic Pack" not in visible_text
    assert "Research Gate" not in visible_text
    assert "不要讓聊天室" not in visible_text
    assert "prompt" not in visible_text


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


@pytest.mark.asyncio
async def test_generate_test_events_variants_repeated_super_chat_text(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
        })
        storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "existing-sc",
            "message_type": "testSuperChatEvent",
            "author_display_name": "SC路人",
            "message_text": "感謝開台，可以請角色各自補一句看法嗎？",
            "amount_display_string": "NT$150",
            "amount_micros": 150000000,
            "priority_class": "super_chat",
            "published_at": "2026-05-04T00:00:00",
            "received_at": "2026-05-04T00:00:00",
            "status": "active",
        })

        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        monkeypatch.setattr(manager, "_generate_test_comments", lambda *_args, **_kwargs: [])
        monkeypatch.setattr(
            manager,
            "_generate_test_super_chats",
            lambda *_args, **_kwargs: [
                {
                    "author_display_name": "紅色斗內",
                    "message_text": "感謝開台，可以請角色各自補一句看法嗎？",
                    "amount_micros": 300000000,
                    "amount_display_string": "NT$300",
                    "currency": "TWD",
                }
            ],
        )

        result = await manager.generate_test_events(
            "live-a",
            count=1,
            use_llm=False,
            super_chat_count=1,
        )

        assert result["super_chat_generated"] == 1
        new_sc = result["events"][0]
        assert new_sc["message_text"] != "感謝開台，可以請角色各自補一句看法嗎？"
        assert "想補問" in new_sc["message_text"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_select_pending_events_prioritizes_super_chat_before_normal_events():
    normal = {
        "id": 1,
        "message_text": "一般留言",
        "priority_class": "normal",
        "sc_tier": 0,
        "status": "active",
    }
    sc_low = {
        "id": 2,
        "message_text": "小額 SC",
        "priority_class": "super_chat",
        "sc_tier": 1,
        "status": "active",
    }
    sc_high = {
        "id": 3,
        "message_text": "高 tier SC",
        "priority_class": "super_chat",
        "sc_tier": 4,
        "status": "active",
    }

    selected = YouTubeBridgeManager._select_pending_events_for_injection(
        [normal, sc_low, sc_high],
        max_events=3,
        max_sc_per_batch=5,
    )

    assert [event["id"] for event in selected] == [3, 2, 1]


def test_normalize_message_marks_super_chat_priority_fields():
    item = {
        "id": "yt-sc-1",
        "snippet": {
            "type": "superChatEvent",
            "displayMessage": "請回應這個 SC",
            "publishedAt": "2026-05-04T10:00:00Z",
            "superChatDetails": {
                "amountDisplayString": "NT$150",
                "amountMicros": 150000000,
                "currency": "TWD",
                "tier": 2,
            },
        },
        "authorDetails": {
            "channelId": "author-a",
            "displayName": "SC觀眾",
        },
    }

    event = normalize_message(
        item,
        session={"session_id": "live-a", "video_id": "video-a", "live_chat_id": "chat-a"},
        connector={"connector_id": "yt-main"},
    )

    assert event["priority_class"] == "super_chat"
    assert event["amount_micros"] == 150000000
    assert event["sc_tier"] == 2


@pytest.mark.asyncio
async def test_poll_loop_marks_live_chat_ended():
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
            "auto_connect": True,
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        await manager.start_session("live-a")
        for _ in range(20):
            if storage.get_session("live-a")["status"] == "ended":
                break
            await asyncio.sleep(0.05)

        session = storage.get_session("live-a")
        assert session["status"] == "ended"
        assert session["finalized_at"]
        assert session["summary_status"] == "pending"
        assert manager.get_status("live-a")["running"] is False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_session_without_video_id_uses_test_mode():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "auto_inject": True,
        })
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        status = await manager.start_session("live-a")

        session = storage.get_session("live-a")
        assert status["running"] is True
        assert status["mode"] == "test"
        assert session["status"] == "running"
        assert session["started_at"]

        stopped = await manager.stop_session("live-a")
        assert stopped["running"] is False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_loop_applies_idle_update_without_restart(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "director_guidance": "先聊四月新番。",
        })
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            idle_seconds=60,
            status="running",
            last_director_action_at=(datetime.now() - timedelta(seconds=30)).isoformat(),
        )
        calls = []

        def fake_decision(self, session, state):
            return {
                "action": "continue_topic",
                "reason": "idle 已到，延續測試話題。",
                "prompt": "請自然延續本場直播話題。",
                "current_topic": session.get("director_guidance", ""),
            }

        async def fake_send(self, session, state, decision):
            calls.append((session["session_id"], state["idle_seconds"], decision["action"]))
            return {"interaction": {"job_id": "fake-job"}}

        monkeypatch.setattr(YouTubeBridgeManager, "_director_decision", fake_decision)
        monkeypatch.setattr(YouTubeBridgeManager, "_send_director_turn", fake_send)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        await manager.start_session("live-a")
        await asyncio.sleep(0.2)
        assert calls == []

        await manager.start_director("live-a", idle_seconds=10, guidance="改聊 LLM。", kickoff=False)
        for _ in range(30):
            if calls:
                break
            await asyncio.sleep(0.05)

        assert calls
        assert calls[0] == ("live-a", 10, "continue_topic")
        assert storage.get_director_state("live-a")["last_director_action_at"]

        await manager.stop_session("live-a")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_start_director_guidance_change_resets_turn_limit():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "director_guidance": "先聊四月新番。",
        })
        storage.update_director_state(
            "live-a",
            director_enabled=True,
            consecutive_ai_turns=2,
            status="turn_limit_wait",
        )
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        state = await manager.start_director("live-a", idle_seconds=10, guidance="改聊美食。", kickoff=False)

        assert state["consecutive_ai_turns"] == 0
        assert state["status"] == "running"
        assert state["metadata"]["guidance_reset_turn_limit"] is True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_turn_marks_cancelled_stream_error_interrupted(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        session = storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["default"],
            "director_guidance": "先聊四月新番。",
        })

        class CancelledStreamClient:
            def chat_stream_sync(self, **kwargs):
                kwargs["cancel_event"].set()
                raise RuntimeError("'NoneType' object has no attribute 'read'")

        monkeypatch.setattr("bridge_engine.MemoriaClient", lambda: CancelledStreamClient())
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        result = await manager._send_director_turn(
            session,
            {"current_topic": "四月新番"},
            {
                "action": "continue_topic",
                "reason": "測試中斷",
                "prompt": "請自然開場。",
                "current_topic": "四月新番",
            },
        )

        interaction = result["interaction"]
        assert interaction["status"] == "interrupted"
        assert interaction["closure_text"]
        assert interaction["metadata"]["discarded"] is True
        assert storage.get_active_interaction("live-a") is None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_turn_broadcasts_interaction_completed(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        session = storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["default"],
            "director_guidance": "先聊四月新番。",
        })

        class FakeStreamClient:
            def chat_stream_sync(self, **_kwargs):
                return {
                    "session_id": "mem-a",
                    "message_id": 42,
                    "reply": "導播回覆完成。",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", lambda: FakeStreamClient())
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())
        queue = await manager.subscribe("live-a")

        result = await manager._send_director_turn(
            session,
            {"current_topic": "四月新番"},
            {
                "action": "continue_topic",
                "reason": "測試完成事件",
                "prompt": "請自然延續。",
                "current_topic": "四月新番",
            },
        )

        events = []
        while not queue.empty():
            events.append((await queue.get())["type"])

        assert result["interaction"]["status"] == "completed"
        assert "interaction_completed" in events
        assert events.index("interaction_completed") < events.index("director_injected")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_director_turn_sends_simple_display_content_to_chat(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        session = storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "target_memoria_session_id": "mem-a",
            "character_ids": ["default"],
            "director_guidance": "先聊四月新番，再聊 LLM。",
        })
        captured = {}

        class CaptureStreamClient:
            def chat_stream_sync(self, **kwargs):
                captured.update(kwargs)
                return {
                    "session_id": "mem-a",
                    "message_id": 42,
                    "reply": "導播回覆完成。",
                }

        monkeypatch.setattr("bridge_engine.MemoriaClient", CaptureStreamClient)
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        result = await manager._send_director_turn(
            session,
            {"current_topic": "四月新番"},
            {
                "action": "transition_topic",
                "reason": "切換直播方向",
                "prompt": "完整導播 prompt：請切到 LLM，並包含直播進度、方向與 fact card。",
                "current_topic": "LLM",
            },
        )

        assert result["interaction"]["status"] == "completed"
        assert "完整導播 prompt" not in captured["content"]
        assert "先聊四月新番" in captured["content"]
        assert captured["display_content"] == "讓我們繼續進行下一個話題。"
        assert "直播導播 action" not in captured["display_content"]
        assert "fact card" not in captured["display_content"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_public_director_topic_removes_internal_control_policy():
    session = {
        "display_name": "QA Live",
        "director_guidance": (
            "本場直播初始主題是四月新番。請使用 Topic Pack / Research Gate 的資料控場，"
            "不要讓聊天室長時間帶偏；每處理 1-2 批留言後要回到主軸。"
        ),
    }

    topic = YouTubeBridgeManager._public_director_topic(session, {})
    prompt = YouTubeBridgeManager._public_director_prompt("continue_topic", session, {})

    assert topic == "四月新番"
    assert "Topic Pack" not in prompt
    assert "Research Gate" not in prompt
    assert "不要讓聊天室" not in prompt


@pytest.mark.asyncio
async def test_autostart_skips_finalized_session():
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
            "auto_connect": True,
        })
        storage.update_session_summary_state(
            "live-a",
            summary_status="completed",
            summary_id=1,
            finalized_at="2026-05-03T10:00:00",
        )
        manager = YouTubeBridgeManager(storage, youtube_client=LiveEndedClient())

        await manager.sync_autostart()

        assert manager.get_status("live-a")["running"] is False
        assert storage.get_session("live-a")["summary_status"] == "completed"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_autostart_marks_unavailable_live_session_stopped_without_crashing():
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
            "video_id": "ended-video",
            "auto_connect": True,
            "status": "running",
        })
        manager = YouTubeBridgeManager(storage, youtube_client=ResolveLiveChatFailedClient())

        await manager.sync_autostart()

        assert manager.get_status("live-a")["running"] is False
        assert storage.get_session("live-a")["status"] == "stopped"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_duration_finalize_runs_closing_super_chat_thanks_before_ending():
    tmp_dir = _tmp_dir()
    try:
        FakeClosingMemoriaClient.calls.clear()
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        session = storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "target_memoria_session_id": "mem-a",
            "auto_sc_thanks_on_finalize": True,
            "auto_finalize_on_duration": True,
            "planned_duration_minutes": 1,
        })
        storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "sc-a",
            "message_type": "superChatEvent",
            "author_display_name": "SC觀眾",
            "message_text": "支持一下，順便問四月新番推薦。",
            "amount_display_string": "NT$150",
            "currency": "TWD",
            "amount_micros": 150000000,
            "sc_tier": 2,
            "priority_class": "super_chat",
        })
        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeClosingMemoriaClient)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")

        await manager._finalize_for_duration(runtime, session)

        assert runtime.status == "ended"
        assert runtime.running is False
        assert storage.get_session("live-a")["status"] == "ended"
        assert storage.list_super_chats("live-a", unhandled_only=True) == []
        interactions = storage.list_interactions("live-a")
        assert interactions[0]["status"] == "completed"
        assert interactions[0]["metadata"]["decision"]["action"] == "closing_super_chat_thanks"
        assert FakeClosingMemoriaClient.calls
        closing_call = FakeClosingMemoriaClient.calls[-1]
        assert closing_call["display_content"] == "感謝本場 Super Chat。"
        assert "直播導播 action=closing_super_chat_thanks" in closing_call["external_context"]["context_text"]
        assert "直播導播 action=closing_super_chat_thanks" not in closing_call["display_content"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_duration_finalize_cancels_background_tasks_before_closing():
    tmp_dir = _tmp_dir()
    sleep_tasks: list[asyncio.Task] = []
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        session = storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "target_memoria_session_id": "mem-a",
            "auto_sc_thanks_on_finalize": True,
            "auto_finalize_on_duration": True,
            "planned_duration_minutes": 1,
        })
        storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "yt-main",
            "youtube_message_id": "sc-a",
            "message_type": "superChatEvent",
            "author_display_name": "SC觀眾",
            "message_text": "支持一下，順便問四月新番推薦。",
            "amount_display_string": "NT$150",
            "currency": "TWD",
            "amount_micros": 150000000,
            "sc_tier": 2,
            "priority_class": "super_chat",
        })
        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeClosingMemoriaClient)
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        runtime.inject_task = asyncio.create_task(asyncio.sleep(3600))
        runtime.test_event_task = asyncio.create_task(asyncio.sleep(3600))
        runtime.director_task = asyncio.create_task(asyncio.sleep(3600))
        sleep_tasks.extend([runtime.inject_task, runtime.test_event_task, runtime.director_task])

        await manager._finalize_for_duration(runtime, session)

        assert runtime.running is False
        assert runtime.status == "ended"
        assert all(task.cancelled() for task in sleep_tasks)
    finally:
        for task in sleep_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*sleep_tasks, return_exceptions=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_autostart_finalizes_stale_running_interactions_before_resume():
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "QA Live",
            "auto_connect": True,
            "status": "running",
            "auto_inject": False,
            "auto_test_events_enabled": False,
        })
        stale = storage.create_interaction({
            "session_id": "live-a",
            "source": "super_chat",
            "priority": 260,
            "status": "running",
            "event_ids": [1, 2, 3],
            "memoria_session_id": "mem-a",
            "content": "舊 process 未完成的回應。",
        })
        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeClosingMemoriaClient)

        await manager.sync_autostart()

        interaction = storage.get_interaction(stale["job_id"])
        assert interaction["status"] == "interrupted"
        assert interaction["reason"] == "server_restarted"
        assert interaction["metadata"]["finalized_by"] == "sync_autostart"
        assert storage.get_active_interaction("live-a") is None
        assert manager.get_status("live-a")["running"] is True
    finally:
        await manager.stop_all()
        shutil.rmtree(tmp_dir, ignore_errors=True)
