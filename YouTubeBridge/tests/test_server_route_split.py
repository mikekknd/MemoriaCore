import importlib.util
import sys
from pathlib import Path


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
        "/live",
        "/live/",
        "/live-chat",
        "/live-chat/",
        "/connectors",
        "/connectors/{connector_id}",
        "/sessions",
        "/sessions/{session_id}",
        "/sessions/{session_id}/start",
        "/sessions/{session_id}/stop",
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
