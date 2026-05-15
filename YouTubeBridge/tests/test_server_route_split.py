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
