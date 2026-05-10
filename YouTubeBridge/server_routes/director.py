"""Director routes。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from live_episode_plan_contract import dialogue_policy_for_turn
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


def _episode_plan_debug_status(
    *,
    index: int,
    current_index: int,
    item_id: str,
    completed_ids: set[str],
    plan_status: str,
    active: bool,
    parent_completed: bool = False,
) -> str:
    if plan_status == "completed" or parent_completed or item_id in completed_ids:
        return "completed"
    if active and index == current_index:
        return "active"
    return "pending"


def _episode_plan_debug_payload(session: dict[str, Any], director: dict[str, Any]) -> dict[str, Any]:
    plan_id = str(session.get("episode_plan_id") or "").strip()
    if not plan_id:
        return {}
    record = storage.get_live_episode_plan(plan_id)
    if not record:
        return {
            "plan_id": plan_id,
            "plan_status": "missing",
            "title": "",
            "segments": [],
        }
    plan = record.get("plan_json") if isinstance(record.get("plan_json"), dict) else {}
    metadata = director.get("metadata") if isinstance(director.get("metadata"), dict) else {}
    planned = metadata.get("planned_state") if isinstance(metadata.get("planned_state"), dict) else {}
    if str(planned.get("plan_id") or "") != plan_id:
        planned = {}
    plan_status = str(planned.get("plan_status") or "not_started")
    current_segment_index = int(planned.get("current_segment_index") or 0)
    current_turn_index = int(planned.get("current_turn_index") or 0)
    completed_segment_ids = {
        str(item)
        for item in planned.get("completed_segment_ids") or []
        if str(item).strip()
    }
    completed_turn_ids = {
        str(item)
        for item in planned.get("completed_turn_ids") or []
        if str(item).strip()
    }
    segments: list[dict[str, Any]] = []
    for segment_index, segment in enumerate(plan.get("segments") or []):
        if not isinstance(segment, dict):
            continue
        segment_id = str(segment.get("segment_id") or f"segment-{segment_index + 1}")
        segment_active = plan_status == "running" and segment_index == current_segment_index
        segment_completed = (
            plan_status == "completed"
            or segment_id in completed_segment_ids
            or (plan_status == "running" and segment_index < current_segment_index)
        )
        turns: list[dict[str, Any]] = []
        for turn_index, turn in enumerate(segment.get("planned_turn_contracts") or []):
            if not isinstance(turn, dict):
                continue
            turn_id = str(turn.get("turn_id") or f"{segment_id}_turn_{turn_index + 1}")
            turn_active = segment_active and turn_index == current_turn_index
            dialogue_policy = dialogue_policy_for_turn(turn)
            turns.append({
                "turn_id": turn_id,
                "turn_type": str(turn.get("turn_type") or ""),
                "intent": str(turn.get("intent") or ""),
                "reply_budget": {
                    "min_replies": dialogue_policy["min_replies"],
                    "max_replies": dialogue_policy["max_replies"],
                    "autonomy": dialogue_policy["autonomy"],
                },
                "status": _episode_plan_debug_status(
                    index=turn_index,
                    current_index=current_turn_index,
                    item_id=turn_id,
                    completed_ids=completed_turn_ids,
                    plan_status=plan_status,
                    active=segment_active,
                    parent_completed=segment_completed and not turn_active,
                ),
            })
        segments.append({
            "segment_id": segment_id,
            "title": str(segment.get("title") or segment_id),
            "goal": str(segment.get("goal") or ""),
            "status": _episode_plan_debug_status(
                index=segment_index,
                current_index=current_segment_index,
                item_id=segment_id,
                completed_ids=completed_segment_ids,
                plan_status=plan_status,
                active=plan_status == "running",
            ),
            "turns": turns,
        })
    next_wait = {}
    if plan_status == "running":
        try:
            idle_seconds = max(1, min(int(director.get("idle_seconds", 60) or 60), 3600))
            delay_info = manager._episode_plan_director_delay_info(
                session,
                director,
                {"episode_plan": {"mode": "planned_turn"}},
                idle_seconds,
            )
            delay_seconds = int(delay_info.get("delay_seconds") or 0)
            remaining_seconds = delay_seconds
            last_action_at = str(director.get("last_director_action_at") or "").strip()
            if last_action_at:
                try:
                    elapsed = (datetime.now() - datetime.fromisoformat(last_action_at)).total_seconds()
                    remaining_seconds = max(0, int(round(delay_seconds - elapsed)))
                except ValueError:
                    remaining_seconds = delay_seconds
            next_wait = {
                **delay_info,
                "remaining_seconds": remaining_seconds,
            }
        except Exception:
            next_wait = {}

    return {
        "plan_id": plan_id,
        "title": str(plan.get("title") or record.get("title") or plan_id),
        "plan_status": plan_status,
        "current_segment_index": current_segment_index,
        "current_turn_index": current_turn_index,
        "next_wait": next_wait,
        "segments": segments,
    }


@router.get("/sessions/{session_id}/director")
async def get_director_state(session_id: str):
    session = storage.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    director = storage.get_director_state(session_id)
    return {
        **director,
        "episode_plan_debug": _episode_plan_debug_payload(session, director),
    }


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
