"""測試與 E2E checkpoint routes。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from models import E2ECheckpointRequest, TestChatGenerateRequest
from server_helpers import build_e2e_checkpoint, read_e2e_checkpoint, write_e2e_checkpoint
from storage import DEFAULT_CONNECTOR_ID
from youtube_client import extract_video_id


router = APIRouter()
_state = None
storage = None
manager = None
summary_manager = None
chat_preview_cache = None
STATIC_ROOT = ""
UI_ASSETS_ROOT = None
E2E_CHECKPOINT_PATH = None


def configure(state):
    global _state, storage, manager, summary_manager, chat_preview_cache
    global STATIC_ROOT, UI_ASSETS_ROOT, E2E_CHECKPOINT_PATH
    _state = state
    storage = state.storage
    manager = state.manager
    summary_manager = state.summary_manager
    chat_preview_cache = state.chat_preview_cache
    STATIC_ROOT = str(state.static_root)
    UI_ASSETS_ROOT = state.ui_assets_root
    E2E_CHECKPOINT_PATH = state.e2e_checkpoint_path


def _require_state():
    if _state is None:
        raise RuntimeError("server route state is not configured")
    return _state


@router.post("/testing/live-session/cleanup-ended")
async def cleanup_ended_live_sessions(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        limit = int(body.get("limit", 1) or 1) if isinstance(body, dict) else 1
    except (TypeError, ValueError):
        limit = 1
    result = storage.cleanup_ended_sessions(limit=limit)
    for session_id in result.get("deleted_session_ids", []):
        runtime = manager._runtimes.pop(session_id, None)
        if runtime:
            runtime.running = False
            for cancel_event in runtime.cancel_events.values():
                cancel_event.set()
            await manager._stop_runtime_background_tasks_for_closing(runtime)
        chat_preview_cache.pop(session_id, None)
    return result


@router.post("/testing/live-session/bootstrap")
async def bootstrap_live_session(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        cleanup_limit = int(body.get("cleanup_limit", 1) or 1)
    except (TypeError, ValueError):
        cleanup_limit = 1
    cleanup = storage.cleanup_ended_sessions(limit=cleanup_limit) if body.get("cleanup_ended", True) else {
        "deleted_count": 0,
        "deleted_session_ids": [],
    }
    for session_id in cleanup.get("deleted_session_ids", []):
        runtime = manager._runtimes.pop(session_id, None)
        if runtime:
            runtime.running = False
            for cancel_event in runtime.cancel_events.values():
                cancel_event.set()
            await manager._stop_runtime_background_tasks_for_closing(runtime)
        chat_preview_cache.pop(session_id, None)

    should_start = bool(body.get("start", False))
    payload = dict(body.get("session") if isinstance(body.get("session"), dict) else body)
    for key in ("cleanup_ended", "cleanup_limit", "start"):
        payload.pop(key, None)
    payload.setdefault("connector_id", DEFAULT_CONNECTOR_ID)
    payload.setdefault("display_name", f"YT Live {datetime.now().strftime('%Y/%m/%d %H:%M:%S')}")
    payload.setdefault("auto_connect", True)
    payload.setdefault("auto_inject", True)
    payload.setdefault("dynamic_inject_enabled", True)
    payload.setdefault("auto_finalize_on_duration", True)
    payload.setdefault("auto_delete_after_processed", True)
    payload.setdefault("auto_sc_thanks_on_finalize", True)
    payload["connector_id"] = DEFAULT_CONNECTOR_ID
    payload["video_id"] = extract_video_id(payload.get("video_id", ""))
    storage.ensure_single_connector()
    session = storage.upsert_session(payload)
    try:
        runtime_status = await manager.start_session(session["session_id"]) if should_start else manager.get_status(session["session_id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "cleanup": cleanup,
        "session": session,
        "runtime_status": runtime_status,
    }


@router.post("/sessions/{session_id}/test-events/generate")
async def generate_test_chat_events(session_id: str, body: TestChatGenerateRequest):
    try:
        return await manager.generate_test_events(
            session_id,
            count=body.count,
            topic_hint=body.topic_hint,
            use_llm=body.use_llm,
            super_chat_count=body.super_chat_count,
            include_malicious_sc=body.include_malicious_sc,
            sc_burst=body.sc_burst,
            manual_events=[event.model_dump() for event in body.manual_events],
        )
    except ValueError as exc:
        status_code = 404 if "不存在" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/sessions/{session_id}/test-events/auto/start")
async def start_auto_test_events(session_id: str):
    try:
        return await manager.start_auto_test_events(session_id)
    except ValueError as exc:
        status_code = 404 if "不存在" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc))


@router.post("/sessions/{session_id}/test-events/auto/stop")
async def stop_auto_test_events(session_id: str):
    try:
        return await manager.stop_auto_test_events(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/sessions/{session_id}/test-events/auto")
async def get_auto_test_events(session_id: str):
    if not storage.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    status = manager.get_status(session_id)
    return {
        "session_id": session_id,
        "enabled": bool((storage.get_session(session_id) or {}).get("auto_test_events_enabled")),
        "running": bool(status.get("auto_test_events_running")),
        "last_auto_test_event_at": status.get("last_auto_test_event_at"),
        "last_auto_test_event_error": status.get("last_auto_test_event_error"),
    }


@router.get("/testing/e2e-checkpoint")
async def get_e2e_checkpoint(session_id: str | None = None):
    if session_id:
        try:
            return build_e2e_checkpoint(storage, session_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
    checkpoint = read_e2e_checkpoint(E2E_CHECKPOINT_PATH)
    if not checkpoint:
        raise HTTPException(status_code=404, detail="checkpoint not found")
    return checkpoint


@router.post("/testing/e2e-checkpoint")
async def save_e2e_checkpoint(body: E2ECheckpointRequest):
    try:
        checkpoint = build_e2e_checkpoint(storage, body.session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return write_e2e_checkpoint(E2E_CHECKPOINT_PATH, checkpoint)
