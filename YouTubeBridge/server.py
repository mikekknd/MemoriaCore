"""YouTubeBridge FastAPI server。

啟動：
    python server.py
    uvicorn server:app --host 127.0.0.1 --port 8091
"""
from __future__ import annotations

import asyncio
from functools import wraps
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

# Windows 預設 Proactor loop 在長時間本機 SSE / keep-alive 壓測下可能讓 uvicorn
# accept socket 失效；server 啟動前改用 Selector policy，讓 8091 行為和 8088 一致。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from bridge_engine import YouTubeBridgeManager
from memoria_client import MemoriaClient
from models import (
    CleanupRequest, ConnectorConfig, DirectorGuidanceRequest, DirectorStartRequest,
    E2ECheckpointRequest, EpisodePlanBindRequest, EpisodePlanEvidenceImportRequest, EpisodePlanImportRequest,
    FactCardImportRequest, InterruptRequest,
    LiveSessionConfig, MemoriaAuthConfig, ReplyRecentRequest,
    StudioAvatarUploadRequest,
    StudioDisplaySettings, StudioLiveDefaults, StudioSettingsPatch, StudioTestSettings,
    YouTubeLiveGlobalSuffixRequest,
    ResearchRequest, SummarizeRequest, TestChatGenerateRequest, TopicPackCreateRequest,
    TopicPackEntryCreateRequest, TopicPackEntryUpdateRequest,
    TopicPackUpdateRequest, WriteMemoryRequest,
)
from server_presenters import (
    sanitize_chat_preview_message,
    sanitize_chat_preview_session,
    sanitize_interaction,
    sanitize_interaction_metadata,
    sanitize_public_text,
    sanitize_topic_pack_usage_status,
)
from server_security import (
    is_loopback_request as security_is_loopback_request,
    require_bridge_key as security_require_bridge_key,
)
from server_routes import (
    connectors as _connectors_routes,
    director as _director_routes,
    episode_plans as _episode_plans_routes,
    fact_cards as _fact_cards_routes,
    memoria as _memoria_routes,
    register_routes,
    research as _research_routes,
    sessions as _sessions_routes,
    studio_settings as _studio_settings_routes,
    summaries as _summaries_routes,
    testing as _testing_routes,
    topic_packs as _topic_packs_routes,
    ui as _ui_routes,
)
from server_state import BridgeAppState
from storage import BridgeStorage, DEFAULT_CONNECTOR_ID
from summary_engine import YouTubeLiveSummaryManager
from youtube_client import extract_video_id


STATIC_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
UI_ASSETS_ROOT = Path(STATIC_ROOT) / "ui"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
E2E_CHECKPOINT_PATH = PROJECT_ROOT / "runtime" / "youtube_bridge_e2e_checkpoint.json"
STUDIO_AVATAR_ROOT = PROJECT_ROOT / "runtime" / "YouTubeBridge" / "StudioAvatars"
FREE_TALK_TOPIC_ROOT = PROJECT_ROOT / "runtime" / "YouTubeBridge" / "freeTalkTopics"
EPISODE_PLAN_ROOT = PROJECT_ROOT / "runtime" / "YouTubeBridge" / "EpisodePlans"
logger = logging.getLogger("youtube_bridge")


storage = BridgeStorage()
chat_preview_cache: dict[str, dict[str, Any]] = {}


def _apply_memoria_config() -> None:
    config = storage.get_memoria_config()
    os.environ["MEMORIACORE_BASE_URL"] = str(config.get("base_url") or "http://localhost:8088/api/v1")
    os.environ["MEMORIACORE_USERNAME"] = str(config.get("username") or "")
    os.environ["MEMORIACORE_PASSWORD"] = str(config.get("password") or "")
    os.environ["MEMORIACORE_ADMIN_BYPASS"] = "1" if config.get("admin_bypass", True) else "0"


_apply_memoria_config()
manager = YouTubeBridgeManager(storage)
summary_manager = YouTubeLiveSummaryManager(storage)
app_state = BridgeAppState(
    storage=storage,
    manager=manager,
    summary_manager=summary_manager,
    chat_preview_cache=chat_preview_cache,
    static_root=Path(STATIC_ROOT),
    ui_assets_root=UI_ASSETS_ROOT,
    studio_avatar_root=STUDIO_AVATAR_ROOT,
    free_talk_topic_root=FREE_TALK_TOPIC_ROOT,
    episode_plan_root=EPISODE_PLAN_ROOT,
    e2e_checkpoint_path=E2E_CHECKPOINT_PATH,
    apply_memoria_config=_apply_memoria_config,
)


def _is_loopback_request(request: Request) -> bool:
    return security_is_loopback_request(request)


def require_bridge_key(request: Request) -> None:
    security_require_bridge_key(request)


def _sanitize_chat_preview_message(message: dict) -> dict:
    return sanitize_chat_preview_message(message)


def _sanitize_chat_preview_session(session: dict | None) -> dict | None:
    return sanitize_chat_preview_session(session)


def _sanitize_public_text(value: Any, *, max_chars: int = 800) -> str:
    return sanitize_public_text(value, max_chars=max_chars)


def _sanitize_interaction_metadata(value: Any, *, depth: int = 0) -> Any:
    return sanitize_interaction_metadata(value, depth=depth)


def _sanitize_interaction(interaction: dict | None) -> dict | None:
    return sanitize_interaction(interaction)


def _sanitize_topic_pack_usage_status(status: dict[str, Any]) -> dict[str, Any]:
    return sanitize_topic_pack_usage_status(status)


def _build_e2e_checkpoint(storage_obj: BridgeStorage, session_id: str) -> dict[str, Any]:
    session = storage_obj.get_session(session_id)
    if not session:
        raise ValueError("session not found")
    packs = storage_obj.list_session_topic_packs(session_id)
    interactions = storage_obj.list_interactions(session_id, limit=100)
    events = storage_obj.list_events(session_id, limit=500)
    active_interactions = [
        item for item in interactions
        if str(item.get("status") or "") in {"queued", "running", "active"}
    ]
    usage_stats = storage_obj.get_topic_pack_usage_stats(session_id)
    director_state = storage_obj.get_director_state(session_id)
    return {
        "session_id": session_id,
        "topic_pack_id": int(packs[0]["id"]) if packs else None,
        "status": str(session.get("status") or ""),
        "started_at": str(session.get("started_at") or session.get("created_at") or ""),
        "ended_at": str(session.get("ended_at") or ""),
        "last_message_count": storage_obj.count_events(session_id),
        "last_sc_count": sum(1 for event in events if str(event.get("priority_class") or "") == "super_chat"),
        "active_interaction_count": len(active_interactions),
        "usage_stats": {
            "total_entries": int(usage_stats.get("total_entries") or 0),
            "used_entry_count": int(usage_stats.get("used_entry_count") or 0),
            "unused_entry_count": int(usage_stats.get("unused_entry_count") or 0),
            "low_unused": bool(usage_stats.get("low_unused")),
            "repeated_entry": usage_stats.get("repeated_entry") if isinstance(usage_stats.get("repeated_entry"), dict) else None,
        },
        "director_status": str(director_state.get("status") or ""),
        "checkpoint_created_at": datetime.now().isoformat(),
        "can_resume": str(session.get("status") or "") not in {"deleted"},
    }


def _write_e2e_checkpoint(checkpoint: dict[str, Any]) -> dict[str, Any]:
    E2E_CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    E2E_CHECKPOINT_PATH.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"path": str(E2E_CHECKPOINT_PATH), "checkpoint": checkpoint}


def _read_e2e_checkpoint() -> dict[str, Any] | None:
    if not E2E_CHECKPOINT_PATH.exists():
        return None
    try:
        payload = json.loads(E2E_CHECKPOINT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _public_connector(connector: dict | None) -> dict | None:
    if not connector:
        return None
    return {
        **connector,
        "api_key": "",
        "api_key_configured": bool(connector.get("api_key")),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.ensure_single_connector()
    _apply_memoria_config()
    await manager.sync_autostart()
    yield
    await manager.stop_all()


app = FastAPI(
    title="YouTubeBridge API",
    description="YouTube Live Chat bridge for MemoriaCore",
    version="0.1.0",
    lifespan=lifespan,
    dependencies=[Depends(require_bridge_key)],
)

register_routes(app, app_state)

_ROUTE_MODULES_FOR_SYNC = (
    _ui_routes,
    _connectors_routes,
    _sessions_routes,
    _director_routes,
    _episode_plans_routes,
    _testing_routes,
    _topic_packs_routes,
    _fact_cards_routes,
    _research_routes,
    _studio_settings_routes,
    _summaries_routes,
    _memoria_routes,
)


def _sync_route_state() -> None:
    app_state.storage = storage
    app_state.manager = manager
    app_state.summary_manager = summary_manager
    app_state.chat_preview_cache = chat_preview_cache
    app_state.studio_avatar_root = STUDIO_AVATAR_ROOT
    app_state.free_talk_topic_root = FREE_TALK_TOPIC_ROOT
    app_state.episode_plan_root = EPISODE_PLAN_ROOT
    for route_module in _ROUTE_MODULES_FOR_SYNC:
        route_module.configure(app_state)
    _install_auto_finalize_callback()


async def _auto_finalize_archive_session(session_id: str, *, finalized_by: str, finalized: dict[str, Any]) -> dict[str, Any]:
    _sync_route_state()
    session = storage.get_session(session_id)
    if not session:
        return {"session_id": session_id, "status": "missing", "memory_write": {"status": "skipped", "reason": "session_missing"}}
    try:
        return await _sessions_routes._finalize_summarize_write_and_maybe_delete(
            session_id,
            delete_after=bool(session.get("auto_delete_after_processed")),
            reason=finalized_by,
            already_finalized=finalized,
        )
    except Exception as exc:
        logger.warning("auto summary/shared-memory archive failed session_id=%s error=%s", session_id, exc)
        storage.update_session_summary_state(
            session_id,
            summary_status="failed",
            summary_error=str(exc)[:1000],
            finalized_at=session.get("finalized_at") or datetime.now().isoformat(),
        )
        return {
            "session_id": session_id,
            "status": "failed",
            "error": str(exc)[:500],
            "memory_write": {"status": "failed", "error": str(exc)[:500]},
        }


def _install_auto_finalize_callback() -> None:
    if getattr(manager, "auto_finalize_archive_callback", None) is not _auto_finalize_archive_session:
        try:
            manager.auto_finalize_archive_callback = _auto_finalize_archive_session
        except Exception:
            pass


_install_auto_finalize_callback()


def _route_handler(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        _sync_route_state()
        return await func(*args, **kwargs)

    return wrapper


health = _route_handler(_ui_routes.health)
ui_config = _route_handler(_ui_routes.ui_config)
bridge_ui_asset = _route_handler(_ui_routes.bridge_ui_asset)
bridge_ui = _route_handler(_ui_routes.bridge_ui)
bridge_studio = _route_handler(_ui_routes.bridge_studio)
bridge_live = _route_handler(_ui_routes.bridge_live)
bridge_live_chat = _route_handler(_ui_routes.bridge_live_chat)

list_connectors = _route_handler(_connectors_routes.list_connectors)
upsert_connector = _route_handler(_connectors_routes.upsert_connector)
get_connector = _route_handler(_connectors_routes.get_connector)
delete_connector = _route_handler(_connectors_routes.delete_connector)

list_sessions = _route_handler(_sessions_routes.list_sessions)
upsert_session = _route_handler(_sessions_routes.upsert_session)
start_current_session = _route_handler(_sessions_routes.start_current_session)
get_session = _route_handler(_sessions_routes.get_session)
delete_session = _route_handler(_sessions_routes.delete_session)
start_session = _route_handler(_sessions_routes.start_session)
stop_session = _route_handler(_sessions_routes.stop_session)
recent_events = _route_handler(_sessions_routes.recent_events)
events_stream = _route_handler(_sessions_routes.events_stream)
list_session_interactions = _route_handler(_sessions_routes.list_session_interactions)
get_chat_preview = _route_handler(_sessions_routes.get_chat_preview)
ack_presentation_item = _route_handler(_sessions_routes.ack_presentation_item)
get_presentation_audio = _route_handler(_sessions_routes.get_presentation_audio)
skip_current_presentation_item = _route_handler(_sessions_routes.skip_current_presentation_item)
interrupt_session = _route_handler(_sessions_routes.interrupt_session)
reply_recent = _route_handler(_sessions_routes.reply_recent)
list_super_chats = _route_handler(_sessions_routes.list_super_chats)
reply_super_chat_batch = _route_handler(_sessions_routes.reply_super_chat_batch)
finalize_session = _route_handler(_sessions_routes.finalize_session)

get_director_state = _route_handler(_director_routes.get_director_state)
start_director = _route_handler(_director_routes.start_director)
stop_director = _route_handler(_director_routes.stop_director)
update_director_guidance = _route_handler(_director_routes.update_director_guidance)

list_episode_plans = _route_handler(_episode_plans_routes.list_episode_plans)
sync_local_episode_plans = _route_handler(_episode_plans_routes.sync_local_episode_plans)
import_episode_plan = _route_handler(_episode_plans_routes.import_episode_plan)
get_episode_plan = _route_handler(_episode_plans_routes.get_episode_plan)
get_episode_plan_characters = _route_handler(_episode_plans_routes.get_episode_plan_characters)
delete_episode_plan = _route_handler(_episode_plans_routes.delete_episode_plan)
bind_episode_plan = _route_handler(_episode_plans_routes.bind_episode_plan)
unbind_episode_plan = _route_handler(_episode_plans_routes.unbind_episode_plan)
import_episode_plan_evidence = _route_handler(_episode_plans_routes.import_episode_plan_evidence)

cleanup_ended_live_sessions = _route_handler(_testing_routes.cleanup_ended_live_sessions)
bootstrap_live_session = _route_handler(_testing_routes.bootstrap_live_session)
generate_test_chat_events = _route_handler(_testing_routes.generate_test_chat_events)
start_auto_test_events = _route_handler(_testing_routes.start_auto_test_events)
stop_auto_test_events = _route_handler(_testing_routes.stop_auto_test_events)
get_auto_test_events = _route_handler(_testing_routes.get_auto_test_events)
get_e2e_checkpoint = _route_handler(_testing_routes.get_e2e_checkpoint)
save_e2e_checkpoint = _route_handler(_testing_routes.save_e2e_checkpoint)

list_topic_packs = _route_handler(_topic_packs_routes.list_topic_packs)
create_topic_pack = _route_handler(_topic_packs_routes.create_topic_pack)
delete_all_topic_packs = _route_handler(_topic_packs_routes.delete_all_topic_packs)
update_topic_pack = _route_handler(_topic_packs_routes.update_topic_pack)
delete_topic_pack = _route_handler(_topic_packs_routes.delete_topic_pack)
list_topic_pack_entries = _route_handler(_topic_packs_routes.list_topic_pack_entries)
create_topic_pack_entry = _route_handler(_topic_packs_routes.create_topic_pack_entry)
update_topic_pack_entry = _route_handler(_topic_packs_routes.update_topic_pack_entry)
delete_topic_pack_entry = _route_handler(_topic_packs_routes.delete_topic_pack_entry)
list_session_topic_packs = _route_handler(_topic_packs_routes.list_session_topic_packs)
get_session_topic_pack_usage = _route_handler(_topic_packs_routes.get_session_topic_pack_usage)
search_session_topic_packs = _route_handler(_topic_packs_routes.search_session_topic_packs)
search_topic_pack = _route_handler(_topic_packs_routes.search_topic_pack)
rebuild_topic_pack_embeddings = _route_handler(_topic_packs_routes.rebuild_topic_pack_embeddings)
get_topic_pack_graph = _route_handler(_topic_packs_routes.get_topic_pack_graph)
rebuild_topic_pack_graph = _route_handler(_topic_packs_routes.rebuild_topic_pack_graph)
list_topic_graph_traces = _route_handler(_topic_packs_routes.list_topic_graph_traces)
get_latest_topic_graph_trace = _route_handler(_topic_packs_routes.get_latest_topic_graph_trace)
link_topic_pack = _route_handler(_topic_packs_routes.link_topic_pack)

import_fact_cards_folder_to_pack = _route_handler(_fact_cards_routes.import_fact_cards_folder_to_pack)
import_fact_cards_folder = _route_handler(_fact_cards_routes.import_fact_cards_folder)

# request_research keeps enforce_cooldown=False for manual research requests.
request_research = _route_handler(_research_routes.request_research)

summarize_session = _route_handler(_summaries_routes.summarize_session)
get_session_summary = _route_handler(_summaries_routes.get_session_summary)
write_summary_memory = _route_handler(_summaries_routes.write_summary_memory)
list_summaries = _route_handler(_summaries_routes.list_summaries)
cleanup_session_events = _route_handler(_summaries_routes.cleanup_session_events)

get_memoria_config = _route_handler(_memoria_routes.get_memoria_config)
update_memoria_config = _route_handler(_memoria_routes.update_memoria_config)
test_memoria_auth = _route_handler(_memoria_routes.test_memoria_auth)
memoria_refs = _route_handler(_memoria_routes.memoria_refs)
memoria_characters = _route_handler(_memoria_routes.memoria_characters)
memoria_sessions = _route_handler(_memoria_routes.memoria_sessions)
get_youtube_live_global_suffix = _route_handler(_memoria_routes.get_youtube_live_global_suffix)
update_youtube_live_global_suffix = _route_handler(_memoria_routes.update_youtube_live_global_suffix)

get_studio_settings = _route_handler(_studio_settings_routes.get_studio_settings)
update_studio_settings = _route_handler(_studio_settings_routes.update_studio_settings)
list_studio_avatar_assets = _route_handler(_studio_settings_routes.list_studio_avatar_assets)
upload_studio_avatar_asset = _route_handler(_studio_settings_routes.upload_studio_avatar_asset)
get_studio_avatar_asset = _route_handler(_studio_settings_routes.get_studio_avatar_asset)


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8091, access_log=False)
