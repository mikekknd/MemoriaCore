import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_storage_manager_uses_focused_storage_mixins():
    from core.storage_manager import StorageManager
    from core.storage import (
        ConversationRepositoryMixin,
        CoreMemoryRepositoryMixin,
        MemoryBlockRepositoryMixin,
        MemoryInspectRepositoryMixin,
        MessageStatsRepositoryMixin,
        PersonaSnapshotRepositoryMixin,
        ProfileRepositoryMixin,
        StorageCommonMixin,
        TopicCacheRepositoryMixin,
        UserRepositoryMixin,
    )

    expected_mixins = {
        StorageCommonMixin,
        MemoryBlockRepositoryMixin,
        CoreMemoryRepositoryMixin,
        ProfileRepositoryMixin,
        TopicCacheRepositoryMixin,
        MemoryInspectRepositoryMixin,
        UserRepositoryMixin,
        ConversationRepositoryMixin,
        MessageStatsRepositoryMixin,
        PersonaSnapshotRepositoryMixin,
    }

    assert expected_mixins <= set(StorageManager.__mro__)
    assert len((PROJECT_ROOT / "core" / "storage_manager.py").read_text(encoding="utf-8").splitlines()) < 140


def test_orchestration_result_dataclass_is_the_normalized_contract():
    from api.routers.chat.orchestration import _unpack_orchestration_result
    import core.chat_orchestrator.coordinator as coordinator
    from core.prompt_manager import get_prompt_manager
    from core.chat_orchestrator.dataclasses import OrchestrationResult, SharedToolState

    assert coordinator.get_prompt_manager is get_prompt_manager

    result = OrchestrationResult(
        reply_text="ok",
        new_entities=["topic"],
        retrieval_context={"has_memory": False},
        topic_shifted=False,
        pipeline_data=None,
        inner_thought="thinking",
        status_metrics=None,
        tone=None,
        speech=None,
        thinking_speech="",
        cited_uids=[],
        tool_state_export=SharedToolState(executed=False),
    )

    unpacked = _unpack_orchestration_result(result)

    assert unpacked[0] == "ok"
    assert unpacked[1] == ["topic"]
    assert isinstance(unpacked[-1], SharedToolState)


def test_chat_rest_uses_shared_execution_core():
    import api.routers.chat.execution as execution
    from api.routers import chat_rest

    assert chat_rest.prepare_chat_execution is execution.prepare_chat_execution
    assert chat_rest.execute_chat_turns is execution.execute_chat_turns
    assert chat_rest.persist_single_turn_result is execution.persist_single_turn_result
    assert chat_rest.iter_chat_sse_events is execution.iter_chat_sse_events

    sync_source = inspect.getsource(chat_rest.chat_sync)
    stream_source = inspect.getsource(chat_rest.chat_stream_sync)
    assert "prepare_chat_execution" in sync_source
    assert "execute_chat_turns" in sync_source
    assert "iter_chat_sse_events" in stream_source

    execution_source = inspect.getsource(execution.iter_chat_sse_events)
    single_sse_source = inspect.getsource(execution._iter_single_sse_events)
    persist_source = inspect.getsource(execution.persist_single_turn_result)
    single_sync_source = inspect.getsource(execution._execute_single_chat_turn)
    assert "chat_rest.is_group_session" in execution_source
    assert "chat_rest._unpack_orchestration_result" in persist_source
    assert "chat_rest.persist_single_turn_result" in single_sync_source
    assert "chat_rest.persist_single_turn_result" in single_sse_source
    assert "orch_task.cancel()" in single_sse_source
    assert '{"type": "error"' in single_sse_source


@pytest.mark.asyncio
async def test_single_sse_persist_error_is_reported_as_error_event(monkeypatch):
    from api.models.requests import ChatSyncRequest
    from api.routers import chat_rest
    from api.routers.chat.execution import PreparedChatExecution, _iter_single_sse_events

    monkeypatch.setattr(chat_rest, "_messages_for_orchestration", lambda messages, body, context: [])

    async def fail_persist(prepared, result):
        raise RuntimeError("persist failed")

    monkeypatch.setattr(chat_rest, "persist_single_turn_result", fail_persist)

    prepared = PreparedChatExecution(
        body=ChatSyncRequest(content="hello", include_speech=False),
        current_user={"id": "1"},
        external_context=None,
        external_context_summary={},
        session=SimpleNamespace(character_id="default"),
        runtime_session=SimpleNamespace(messages=[], last_entities=[]),
        session_id="sid",
        orchestration_prompt="hello",
        transient_user_content="hello",
        memory_write_policy="normal",
        roster_event=None,
        user_prefs={},
        orchestration_fn=lambda *args, **kwargs: ("reply", [], {}, False, None, None, None, None, None, "", [], None),
        include_speech=False,
        session_ctx={},
        extra_session_ctx=None,
    )

    events = []
    async for event in _iter_single_sse_events(prepared):
        events.append(event)

    assert any('"type": "error"' in event and "persist failed" in event for event in events)


def test_youtube_bridge_topic_pack_ui_is_split_into_focused_modules():
    ui_root = PROJECT_ROOT / "YouTubeBridge" / "static" / "ui"

    topic_packs = (ui_root / "topic-packs.js").read_text(encoding="utf-8")
    topic_graph = (ui_root / "topic-graph.js").read_text(encoding="utf-8")
    topic_pack_crud = (ui_root / "topic-pack-crud.js").read_text(encoding="utf-8")
    fact_card_import = (ui_root / "fact-card-import.js").read_text(encoding="utf-8")

    assert "export {" in topic_packs
    assert "function topicGraphLayout" in topic_graph
    assert "export async function createTopicPack" in topic_pack_crud
    assert "export async function importFactCardsFolder" in fact_card_import
    assert len(topic_packs.splitlines()) < 220
