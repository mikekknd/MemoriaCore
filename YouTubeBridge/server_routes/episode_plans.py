"""Episode plan routes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from live_episode_plan_contract import LiveEpisodePlanValidationError
from models import EpisodePlanBindRequest, EpisodePlanImportRequest


router = APIRouter()
_state = None
storage = None


def configure(state):
    global _state, storage
    _state = state
    storage = state.storage


@router.get("/episode-plans")
async def list_episode_plans(limit: int = 100):
    return storage.list_live_episode_plans(limit=limit)


@router.post("/episode-plans/import")
async def import_episode_plan(body: EpisodePlanImportRequest):
    try:
        return storage.upsert_live_episode_plan(
            body.plan_json,
            source_path=body.source_path,
        )
    except LiveEpisodePlanValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/episode-plans/{plan_id}")
async def get_episode_plan(plan_id: str):
    plan = storage.get_live_episode_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="episode plan not found")
    return plan


@router.delete("/episode-plans/{plan_id}")
async def delete_episode_plan(plan_id: str):
    deleted = storage.delete_live_episode_plan(plan_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="episode plan not found")
    return {"deleted": True, "plan_id": plan_id}


@router.post("/sessions/{session_id}/episode-plan")
async def bind_episode_plan(session_id: str, body: EpisodePlanBindRequest):
    try:
        return storage.bind_episode_plan_to_session(session_id, body.plan_id)
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(
            status_code=404 if "不存在" in message else 400,
            detail=message,
        ) from exc


@router.delete("/sessions/{session_id}/episode-plan")
async def unbind_episode_plan(session_id: str):
    try:
        return storage.unbind_episode_plan_from_session(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
