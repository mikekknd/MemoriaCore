import json

import pytest

from bridge_engine_test_support import BridgeStorage, YouTubeBridgeManager
from storage import DEFAULT_CONNECTOR_ID


class FakeFreeTalkMemoriaClient:
    calls: list[dict] = []

    def chat_stream_sync(self, **kwargs):
        self.__class__.calls.append(dict(kwargs))
        return {
            "session_id": kwargs.get("session_id") or "mem-free-talk",
            "message_id": 99,
            "reply": "雜談回應",
        }

    def list_characters(self):
        return []


def _create_free_talk_session(storage: BridgeStorage, **overrides):
    storage.upsert_connector({
        "connector_id": DEFAULT_CONNECTOR_ID,
        "display_name": "測試連線",
        "enabled": True,
    })
    config = {
        "session_id": "live-free-talk",
        "connector_id": DEFAULT_CONNECTOR_ID,
        "display_name": "雜談測試",
        "status": "running",
        "target_memoria_session_id": "mem-existing",
        "character_ids": ["host-a", "host-b"],
        "director_dialogue_expansion_enabled": True,
        "director_group_turn_limit": 9,
        "post_plan_free_talk_enabled": True,
        "post_plan_free_talk_topic_pack_ids": ["casual"],
        "post_plan_free_talk_idle_turns_min": 6,
        "post_plan_free_talk_idle_turns_max": 6,
        "post_plan_free_talk_audience_turns_min": 3,
        "post_plan_free_talk_audience_turns_max": 3,
    }
    config.update(overrides)
    return storage.upsert_session(config)


@pytest.mark.asyncio
async def test_manual_free_talk_tick_sends_only_active_topic_context(tmp_path):
    storage = BridgeStorage(tmp_path / "youtube_live.db")
    session = _create_free_talk_session(storage)
    topic_root = tmp_path / "free-talk-topics"
    topic_root.mkdir()
    (topic_root / "casual.json").write_text(
        json.dumps([
            {
                "title": "創作近況",
                "prompt": "請聊聊最近創作時遇到的事情。",
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    FakeFreeTalkMemoriaClient.calls = []
    manager = YouTubeBridgeManager(
        storage,
        memoria_client_factory=lambda: FakeFreeTalkMemoriaClient(),
    )

    result = await manager.start_post_plan_free_talk_test(
        session["session_id"],
        topic_root=topic_root,
        transition_reason="operator_debug_start_free_talk",
    )

    assert result["phase"] == "post_plan_free_talk"
    assert result["status"] == "topic_chat"
    assert len(FakeFreeTalkMemoriaClient.calls) == 1
    call = FakeFreeTalkMemoriaClient.calls[0]
    assert "雜談話題：創作近況" in call["content"]
    assert "請聊聊最近創作時遇到的事情。" in call["content"]
    external_context = call["external_context"]
    context_text = external_context["context_text"]
    assert "雜談話題：創作近況" in context_text
    assert "請聊聊最近創作時遇到的事情。" in context_text
    assert "topic_queue" not in context_text
    assert "post_plan_free_talk" not in context_text
    assert "raw library" not in context_text.lower()
    assert "packs" not in context_text
    assert "topic_queue" not in external_context
    assert "post_plan_free_talk" not in external_context
    assert external_context["group_turn_limit"] == 6

    director_state = storage.get_director_state(session["session_id"])
    metadata = director_state["metadata"]
    assert metadata["phase"] == "post_plan_free_talk"
    assert metadata["post_plan_free_talk"]["topic_cursor"] == 1
    assert metadata["post_plan_free_talk"]["topic_count"] == 1
    assert "topic_queue" not in metadata["post_plan_free_talk"]
    assert "請聊聊最近創作時遇到的事情。" not in json.dumps(metadata, ensure_ascii=False)
    assert metadata["last_tick_action"] == "topic_chat"
    public_director = manager.get_status(session["session_id"])["director"]
    assert "請聊聊最近創作時遇到的事情。" not in json.dumps(public_director, ensure_ascii=False)
    assert "topic_queue" not in json.dumps(public_director, ensure_ascii=False)


@pytest.mark.asyncio
async def test_manual_free_talk_tick_waits_for_active_interaction_without_sending_topic(tmp_path):
    storage = BridgeStorage(tmp_path / "youtube_live.db")
    session = _create_free_talk_session(storage)
    storage.create_interaction({
        "session_id": session["session_id"],
        "status": "running",
        "source": "director",
        "content": "already running",
    })
    topic_root = tmp_path / "free-talk-topics"
    topic_root.mkdir()
    (topic_root / "casual.json").write_text(
        json.dumps([{"title": "創作近況", "prompt": "SHOULD_NOT_SEND"}], ensure_ascii=False),
        encoding="utf-8",
    )
    FakeFreeTalkMemoriaClient.calls = []
    manager = YouTubeBridgeManager(
        storage,
        memoria_client_factory=lambda: FakeFreeTalkMemoriaClient(),
    )

    result = await manager.start_post_plan_free_talk_test(
        session["session_id"],
        topic_root=topic_root,
        transition_reason="operator_debug_start_free_talk",
    )

    assert result["phase"] == "post_plan_free_talk"
    assert result["status"] == "wait"
    assert FakeFreeTalkMemoriaClient.calls == []
    assert storage.get_director_state(session["session_id"])["status"] == "waiting_active_interaction"


@pytest.mark.asyncio
async def test_manual_free_talk_tick_refuses_stopped_session(tmp_path):
    storage = BridgeStorage(tmp_path / "youtube_live.db")
    session = _create_free_talk_session(storage, status="stopped")
    topic_root = tmp_path / "free-talk-topics"
    topic_root.mkdir()
    FakeFreeTalkMemoriaClient.calls = []
    manager = YouTubeBridgeManager(
        storage,
        memoria_client_factory=lambda: FakeFreeTalkMemoriaClient(),
    )

    with pytest.raises(ValueError, match="尚未開始"):
        await manager.start_post_plan_free_talk_test(
            session["session_id"],
            topic_root=topic_root,
            transition_reason="operator_debug_start_free_talk",
        )

    assert storage.get_session(session["session_id"])["status"] == "stopped"
    assert manager.get_status(session["session_id"])["running"] is False
    assert FakeFreeTalkMemoriaClient.calls == []


@pytest.mark.asyncio
async def test_manual_free_talk_tick_treats_empty_pack_selection_as_natural_chat(tmp_path):
    storage = BridgeStorage(tmp_path / "youtube_live.db")
    session = _create_free_talk_session(
        storage,
        post_plan_free_talk_topic_pack_ids=[],
        post_plan_free_talk_idle_turns_min=4,
        post_plan_free_talk_idle_turns_max=4,
    )
    topic_root = tmp_path / "free-talk-topics"
    topic_root.mkdir()
    (topic_root / "casual.json").write_text(
        json.dumps([
            {
                "title": "SHOULD_NOT_LOAD",
                "prompt": "SHOULD_NOT_SEND",
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    FakeFreeTalkMemoriaClient.calls = []
    manager = YouTubeBridgeManager(
        storage,
        memoria_client_factory=lambda: FakeFreeTalkMemoriaClient(),
    )

    result = await manager.start_post_plan_free_talk_test(
        session["session_id"],
        topic_root=topic_root,
        transition_reason="operator_debug_start_free_talk",
    )

    assert result["phase"] == "post_plan_free_talk"
    assert result["status"] == "natural_chat"
    assert len(FakeFreeTalkMemoriaClient.calls) == 1
    call = FakeFreeTalkMemoriaClient.calls[0]
    assert "SHOULD_NOT_LOAD" not in call["content"]
    assert "SHOULD_NOT_SEND" not in call["content"]
    context_text = call["external_context"]["context_text"]
    assert "SHOULD_NOT_LOAD" not in context_text
    assert "SHOULD_NOT_SEND" not in context_text
    assert call["external_context"]["group_turn_limit"] == 4

    metadata = storage.get_director_state(session["session_id"])["metadata"]
    free_talk_state = metadata["post_plan_free_talk"]
    assert free_talk_state["selected_pack_ids"] == []
    assert free_talk_state["selected_available_pack_ids"] == []
    assert free_talk_state["topic_cursor"] == 0
    assert metadata["last_tick_action"] == "natural_chat"


@pytest.mark.asyncio
async def test_manual_free_talk_tick_falls_back_to_natural_chat_for_missing_pack(tmp_path):
    storage = BridgeStorage(tmp_path / "youtube_live.db")
    session = _create_free_talk_session(
        storage,
        post_plan_free_talk_topic_pack_ids=["missing"],
        post_plan_free_talk_idle_turns_min=5,
        post_plan_free_talk_idle_turns_max=8,
    )
    topic_root = tmp_path / "free-talk-topics"
    topic_root.mkdir()
    (topic_root / "empty.json").write_text("[]", encoding="utf-8")
    FakeFreeTalkMemoriaClient.calls = []
    manager = YouTubeBridgeManager(
        storage,
        memoria_client_factory=lambda: FakeFreeTalkMemoriaClient(),
    )

    result = await manager.start_post_plan_free_talk_test(
        session["session_id"],
        topic_root=topic_root,
        transition_reason="operator_debug_start_free_talk",
    )

    assert result["phase"] == "post_plan_free_talk"
    assert result["status"] == "natural_chat"
    assert len(FakeFreeTalkMemoriaClient.calls) == 1
    call = FakeFreeTalkMemoriaClient.calls[0]
    assert "自然雜談" in call["content"]
    assert call["external_context"]["group_turn_limit"] == 5
    assert "topic_queue" not in call["external_context"]["context_text"]

    metadata = storage.get_director_state(session["session_id"])["metadata"]
    assert metadata["post_plan_free_talk"]["topic_cursor"] == 0
    assert metadata["last_tick_action"] == "natural_chat"
