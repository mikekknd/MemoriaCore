import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

spec = importlib.util.spec_from_file_location("youtube_bridge_server_for_route_split", BRIDGE_ROOT / "server.py")
server_module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(server_module)


def _route_paths() -> set[str]:
    return {
        path
        for route in server_module.app.routes
        for path in [getattr(route, "path", "")]
        if path
    }


def test_server_uses_app_state_and_route_registration():
    from server_state import BridgeAppState
    from server_routes import register_routes

    assert isinstance(server_module.app_state, BridgeAppState)
    assert callable(register_routes)
    assert server_module.app_state.storage is server_module.storage
    assert server_module.app_state.manager is server_module.manager
    assert server_module.app_state.summary_manager is server_module.summary_manager


def test_split_routes_keep_existing_public_paths():
    paths = _route_paths()

    expected = {
        "/health",
        "/ui-config",
        "/ui-assets/{asset_path:path}",
        "/ui",
        "/ui/",
        "/studio",
        "/studio/",
        "/studio/settings",
        "/studio/avatar-assets",
        "/studio/avatar-assets/{filename:path}",
        "/episode-plans",
        "/episode-plans/sync-local",
        "/episode-plans/{plan_id}/characters",
        "/live",
        "/live/",
        "/live-chat",
        "/live-chat/",
        "/connectors",
        "/connectors/{connector_id}",
        "/sessions",
        "/sessions/current/start",
        "/sessions/{session_id}",
        "/sessions/{session_id}/start",
        "/sessions/{session_id}/stop",
        "/sessions/{session_id}/phase/free-talk-test/start",
        "/sessions/{session_id}/recent",
        "/sessions/{session_id}/events",
        "/sessions/{session_id}/interactions",
        "/sessions/{session_id}/chat-preview",
        "/sessions/{session_id}/director",
        "/sessions/{session_id}/director/start",
        "/sessions/{session_id}/director/stop",
        "/sessions/{session_id}/director/guidance",
        "/topic-packs",
        "/topic-packs/{pack_id}",
        "/topic-packs/{pack_id}/entries",
        "/topic-packs/{pack_id}/entries/{entry_id}",
        "/sessions/{session_id}/summary",
        "/memoria/config",
    }
    assert expected <= paths


def test_phase_pipeline_routes_are_registered():
    from server_routes.sessions import router

    paths = {route.path for route in router.routes}

    assert "/sessions/{session_id}/phase/finish-main" in paths
    assert "/sessions/{session_id}/phase/finalize" in paths


@pytest.mark.asyncio
async def test_free_talk_debug_route_requires_running_runtime(tmp_path):
    class FakeStorage:
        def get_session(self, session_id: str):
            return {"session_id": session_id, "status": "stopped"}

    class FakeManager:
        called = False

        def get_status(self, session_id: str):
            return {"session_id": session_id, "running": False, "status": "stopped"}

        async def start_post_plan_free_talk_test(self, *args, **kwargs):
            self.called = True
            return {"phase": "post_plan_free_talk"}

    manager = FakeManager()
    server_module._sessions_routes.configure(SimpleNamespace(
        storage=FakeStorage(),
        manager=manager,
        summary_manager=SimpleNamespace(),
        chat_preview_cache={},
        static_root=tmp_path,
        ui_assets_root=tmp_path,
        e2e_checkpoint_path=tmp_path / "checkpoint.json",
        free_talk_topic_root=tmp_path / "freeTalkTopics",
    ))

    with pytest.raises(HTTPException) as exc_info:
        await server_module._sessions_routes.start_free_talk_test("session-a")

    assert exc_info.value.status_code == 409
    assert manager.called is False


@pytest.mark.asyncio
async def test_finish_main_phase_route_requires_running_runtime_before_manager_call(tmp_path):
    class FakeStorage:
        def get_session(self, session_id: str):
            return {"session_id": session_id, "status": "stopped"}

    class FakeManager:
        called = False

        def get_status(self, session_id: str):
            return {"session_id": session_id, "running": False, "status": "stopped"}

        async def finish_main_phase(self, *args, **kwargs):
            self.called = True
            return {"phase": "post_plan_free_talk"}

    manager = FakeManager()
    server_module._sessions_routes.configure(SimpleNamespace(
        storage=FakeStorage(),
        manager=manager,
        summary_manager=SimpleNamespace(),
        chat_preview_cache={},
        static_root=tmp_path,
        ui_assets_root=tmp_path,
        e2e_checkpoint_path=tmp_path / "checkpoint.json",
        free_talk_topic_root=tmp_path / "freeTalkTopics",
    ))

    with pytest.raises(HTTPException) as exc_info:
        await server_module._sessions_routes.finish_main_phase(
            "session-a",
            server_module._sessions_routes.FinishMainPhaseRequest(reason="operator"),
        )

    assert exc_info.value.status_code == 409
    assert manager.called is False


@pytest.mark.asyncio
async def test_finish_main_phase_route_passes_force_enter_free_talk(tmp_path):
    class FakeStorage:
        def get_session(self, session_id: str):
            return {"session_id": session_id, "status": "running"}

    class FakeManager:
        def __init__(self):
            self.kwargs = None

        def get_status(self, session_id: str):
            return {"session_id": session_id, "running": True, "status": "running"}

        async def finish_main_phase(self, *args, **kwargs):
            self.kwargs = kwargs
            return {"phase": "post_plan_free_talk"}

    manager = FakeManager()
    server_module._sessions_routes.configure(SimpleNamespace(
        storage=FakeStorage(),
        manager=manager,
        summary_manager=SimpleNamespace(),
        chat_preview_cache={},
        static_root=tmp_path,
        ui_assets_root=tmp_path,
        e2e_checkpoint_path=tmp_path / "checkpoint.json",
        free_talk_topic_root=tmp_path / "freeTalkTopics",
    ))

    await server_module._sessions_routes.finish_main_phase(
        "session-a",
        server_module._sessions_routes.FinishMainPhaseRequest(
            reason="operator",
            enter_free_talk=True,
            force_enter_free_talk=True,
        ),
    )

    assert manager.kwargs["enter_free_talk"] is True
    assert manager.kwargs["force_enter_free_talk"] is True


@pytest.mark.asyncio
async def test_finish_main_phase_route_sanitizes_phase_pipeline_response(tmp_path):
    class FakeStorage:
        def get_session(self, session_id: str):
            return {"session_id": session_id, "status": "running"}

    class FakeManager:
        def get_status(self, session_id: str):
            return {"session_id": session_id, "running": True, "status": "running"}

        async def finish_main_phase(self, *args, **kwargs):
            return {
                "phase": "post_plan_free_talk",
                "status": "topic_chat",
                "director": {
                    "session_id": "session-a",
                    "status": "running",
                    "planned_state": {
                        "plan_status": "completed",
                        "completed_turn_ids": ["seg_01_turn_01"],
                        "turn_contract": {"hidden_prompt": "TOP_LEVEL_HIDDEN_PLAN"},
                    },
                    "event_ids": [8, 9],
                    "raw_director_metadata": {"prompt": "RAW_DIRECTOR_METADATA"},
                    "metadata": {
                        "phase": "post_plan_free_talk",
                        "last_decision": {
                            "action": "post_plan_free_talk_topic",
                            "prompt": "RAW_PROMPT_SHOULD_NOT_LEAK",
                            "current_topic": "公開話題",
                        },
                        "episode_plan_completed_state": {
                            "planned_turn_contracts": [{"hidden_prompt": "HIDDEN_PLAN"}],
                        },
                    },
                },
                "closing": {
                    "status": "completed",
                    "super_chat_count": 3,
                    "marked": 2,
                    "result": {
                        "interaction": {
                            "content": "<topic_pack_fact_cards>RAW</topic_pack_fact_cards>",
                            "metadata": {
                                "decision": {
                                    "action": "closing_super_chat_thanks",
                                    "prompt": "CLOSING_PROMPT_SHOULD_NOT_LEAK",
                                },
                                "summary": {"event_ids": [1, 2, 3]},
                            },
                        },
                        "message_result": {"raw_payload": "RAW_CLOSING_RESULT"},
                    },
                    "event_ids": [1, 2, 3],
                },
                "interaction": {
                    "content": "PUBLIC",
                    "metadata": {"external_context": "RAW_CONTEXT_SHOULD_NOT_LEAK"},
                },
            }

    server_module._sessions_routes.configure(SimpleNamespace(
        storage=FakeStorage(),
        manager=FakeManager(),
        summary_manager=SimpleNamespace(),
        chat_preview_cache={},
        static_root=tmp_path,
        ui_assets_root=tmp_path,
        e2e_checkpoint_path=tmp_path / "checkpoint.json",
        free_talk_topic_root=tmp_path / "freeTalkTopics",
    ))

    result = await server_module._sessions_routes.finish_main_phase(
        "session-a",
        server_module._sessions_routes.FinishMainPhaseRequest(reason="operator"),
    )

    serialized = server_module.json.dumps(result, ensure_ascii=False)
    assert result["phase"] == "post_plan_free_talk"
    assert result["status"] == "topic_chat"
    assert "RAW_PROMPT_SHOULD_NOT_LEAK" not in serialized
    assert "HIDDEN_PLAN" not in serialized
    assert "TOP_LEVEL_HIDDEN_PLAN" not in serialized
    assert "RAW_DIRECTOR_METADATA" not in serialized
    assert "CLOSING_PROMPT_SHOULD_NOT_LEAK" not in serialized
    assert "RAW_CLOSING_RESULT" not in serialized
    assert "RAW_CONTEXT_SHOULD_NOT_LEAK" not in serialized
    assert "<topic_pack_fact_cards>" not in serialized
    assert "planned_state" not in result["director"]
    assert "event_ids" not in result["director"]
    assert "raw_director_metadata" not in result["director"]
    assert result["director"]["metadata"]["last_decision"] == {
        "action": "post_plan_free_talk_topic",
        "reason": None,
        "current_topic": "公開話題",
    }
    assert result["closing"]["event_count"] == 3
    assert result["closing"]["super_chat_count"] == 3
    assert result["closing"]["marked"] == 2
    assert "result" not in result["closing"]


@pytest.mark.asyncio
async def test_finalize_phase_route_returns_public_phase_shape_without_closing_internals(tmp_path):
    class FakeStorage:
        def get_session(self, session_id: str):
            return {"session_id": session_id, "status": "running"}

    class FakeManager:
        def get_status(self, session_id: str):
            return {"session_id": session_id, "running": True, "status": "running"}

        async def finalize_phase_pipeline(self, *args, **kwargs):
            return {
                "phase": "finalized",
                "session_id": "session-a",
                "status": "ended",
                "runtime_status": {"session_id": "session-a", "running": False, "status": "ended"},
                "event_ids": [1, 2, 3],
                "planned_state": {
                    "plan_status": "completed",
                    "turn_contract": {"hidden_prompt": "FINALIZE_HIDDEN_PLAN"},
                },
                "closing_super_chat_thanks": {
                    "status": "completed",
                    "super_chat_count": 2,
                    "marked": 2,
                    "event_ids": [1, 2],
                    "interaction": {
                        "content": "<topic_pack_fact_cards>RAW</topic_pack_fact_cards>",
                        "metadata": {
                            "decision": {
                                "action": "closing_super_chat_thanks",
                                "prompt": "FINALIZE_CLOSING_PROMPT",
                            }
                        },
                    },
                    "message_result": {"raw_payload": "FINALIZE_RAW_RESULT"},
                },
                "closing_safety_resolution": {
                    "status": "completed",
                    "events": [{"message_text": "raw pending event"}],
                },
            }

    server_module._sessions_routes.configure(SimpleNamespace(
        storage=FakeStorage(),
        manager=FakeManager(),
        summary_manager=SimpleNamespace(),
        chat_preview_cache={},
        static_root=tmp_path,
        ui_assets_root=tmp_path,
        e2e_checkpoint_path=tmp_path / "checkpoint.json",
        free_talk_topic_root=tmp_path / "freeTalkTopics",
    ))

    result = await server_module._sessions_routes.finalize_phase(
        "session-a",
        server_module._sessions_routes.FinalizePhaseRequest(reason="operator"),
    )

    serialized = server_module.json.dumps(result, ensure_ascii=False)
    assert result["phase"] == "finalized"
    assert result["session_id"] == "session-a"
    assert result["status"] == "ended"
    assert result["runtime_status"] == {"session_id": "session-a", "running": False, "status": "ended"}
    assert "event_ids" not in result
    assert "planned_state" not in result
    assert "closing_super_chat_thanks" not in result
    assert "closing_safety_resolution" not in result
    assert "FINALIZE_HIDDEN_PLAN" not in serialized
    assert "FINALIZE_CLOSING_PROMPT" not in serialized
    assert "FINALIZE_RAW_RESULT" not in serialized
    assert "<topic_pack_fact_cards>" not in serialized
