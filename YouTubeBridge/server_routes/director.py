"""Director routes。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException

from models import DirectorGuidanceRequest, DirectorStartRequest


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


@router.get("/sessions/{session_id}/director")
async def get_director_state(session_id: str):
    if not storage.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return storage.get_director_state(session_id)


@router.post("/sessions/{session_id}/director/start")
async def start_director(session_id: str, body: DirectorStartRequest = DirectorStartRequest()):
    try:
        return await manager.start_director(
            session_id,
            idle_seconds=body.idle_seconds,
            guidance=body.guidance,
            kickoff=body.kickoff,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/sessions/{session_id}/director/stop")
async def stop_director(session_id: str):
    if not storage.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return await manager.stop_director(session_id)


@router.post("/sessions/{session_id}/director/guidance")
async def update_director_guidance(session_id: str, body: DirectorGuidanceRequest):
    session = storage.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    guidance = body.guidance.strip()
    guidance_changed = guidance != str(session.get("director_guidance") or "").strip()
    updated = storage.update_session_fields(session_id, director_guidance=guidance)
    director = storage.get_director_state(session_id)
    if guidance_changed and director.get("director_enabled"):
        director = storage.update_director_state(
            session_id,
            consecutive_ai_turns=0,
            status="running",
            metadata={
                "guidance_updated_at": datetime.now().isoformat(),
                "guidance_reset_turn_limit": True,
            },
        )
    return {
        "session_id": session_id,
        "director_guidance": (updated or {}).get("director_guidance", ""),
        "session": updated,
        "director": director,
    }
