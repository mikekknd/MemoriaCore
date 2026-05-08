"""直播專用角色 overlay routes。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from models import LivePersonaOverlayRequest


router = APIRouter()
_state = None
storage = None


def configure(state):
    global _state, storage
    _state = state
    storage = state.storage


@router.get("/persona-overlays")
async def list_persona_overlays():
    return {"overlays": storage.list_live_persona_overlays()}


@router.get("/persona-overlays/{character_id}")
async def get_persona_overlay(character_id: str):
    overlay = storage.get_live_persona_overlay(character_id)
    if not overlay:
        return {
            "character_id": character_id,
            "enabled": False,
            "mode": "replace",
            "system_prompt": "",
            "self_address": "",
            "addressing": {},
            "opening_intro": "",
            "reply_rules": "",
            "created_at": "",
            "updated_at": "",
        }
    return overlay


@router.post("/persona-overlays/{character_id}")
async def update_persona_overlay(character_id: str, body: LivePersonaOverlayRequest):
    try:
        return storage.upsert_live_persona_overlay(character_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
