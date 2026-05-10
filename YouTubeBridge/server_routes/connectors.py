"""Connector routes。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from models import ConnectorConfig
from server_helpers import public_connector


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


@router.get("/connectors")
async def list_connectors():
    return [public_connector(storage.ensure_single_connector())]


@router.post("/connectors")
async def upsert_connector(body: ConnectorConfig):
    return public_connector(storage.upsert_single_connector(body.model_dump()))


@router.get("/connectors/{connector_id}")
async def get_connector(connector_id: str):
    return public_connector(storage.ensure_single_connector())


@router.delete("/connectors/{connector_id}")
async def delete_connector(connector_id: str):
    raise HTTPException(status_code=400, detail="single connector cannot be deleted")
