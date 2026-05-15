"""UI 與靜態檔 routes。"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


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


@router.get("/health")
async def health():
    return {"ok": True}


@router.get("/ui-config")
async def ui_config():
    key = os.getenv("YOUTUBE_BRIDGE_API_KEY", "").strip()
    return {"bridge_key": key}


@router.get("/ui-assets/{asset_path:path}")
async def bridge_ui_asset(asset_path: str):
    asset_root = UI_ASSETS_ROOT.resolve()
    resolved = (UI_ASSETS_ROOT / asset_path).resolve()
    try:
        resolved.relative_to(asset_root)
    except ValueError:
        raise HTTPException(status_code=404, detail="asset not found")
    if not resolved.is_file() or resolved.suffix not in {".css", ".js", ".md"}:
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(resolved)


@router.get("/ui/")
@router.get("/ui")
async def bridge_ui():
    return FileResponse(os.path.join(STATIC_ROOT, "index.html"))


@router.get("/studio/")
@router.get("/studio")
async def bridge_studio():
    return FileResponse(os.path.join(STATIC_ROOT, "studio.html"))


@router.get("/live/")
@router.get("/live")
async def bridge_live():
    return FileResponse(os.path.join(STATIC_ROOT, "live.html"))


@router.get("/live-chat/")
@router.get("/live-chat")
async def bridge_live_chat():
    return FileResponse(os.path.join(STATIC_ROOT, "live_chat.html"))
