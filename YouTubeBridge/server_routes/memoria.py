"""MemoriaCore integration routes。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from memoria_client import MemoriaClient
from models import MemoriaAuthConfig, YouTubeLiveGlobalSuffixRequest


router = APIRouter()
_state = None
storage = None
manager = None
summary_manager = None
chat_preview_cache = None
STATIC_ROOT = ""
UI_ASSETS_ROOT = None
E2E_CHECKPOINT_PATH = None
YOUTUBE_LIVE_GLOBAL_SUFFIX_KEY = "chat_system_suffix_youtube_live"


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


@router.get("/memoria/config")
async def get_memoria_config():
    return storage.get_public_memoria_config()


@router.post("/memoria/config")
async def update_memoria_config(body: MemoriaAuthConfig):
    state = _require_state()
    saved = storage.upsert_memoria_config(body.model_dump())
    if state.apply_memoria_config:
        state.apply_memoria_config()
    summary_manager.memoria_client = MemoriaClient()
    return storage.get_public_memoria_config() | {
        "auth_mode": "admin_bypass" if saved.get("admin_bypass") else "password",
    }


@router.post("/memoria/auth/test")
async def test_memoria_auth(body: MemoriaAuthConfig | None = None):
    config = body.model_dump() if body else storage.get_memoria_config()
    if not config.get("password"):
        config["password"] = storage.get_memoria_config().get("password", "")
    try:
        client = MemoriaClient(
            base_url=str(config.get("base_url") or ""),
            username=str(config.get("username") or ""),
            password=str(config.get("password") or ""),
            admin_bypass=bool(config.get("admin_bypass", True)),
            timeout=20,
        )
        client.ensure_auth()
        characters = client.list_characters()
        sessions = client.list_sessions(limit=10)
        return {
            "ok": True,
            "character_count": len(characters),
            "session_count": len(sessions),
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/memoria/characters")
async def memoria_characters():
    try:
        return await asyncio.to_thread(MemoriaClient().list_characters)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/memoria/refs")
async def memoria_refs():
    try:
        client = MemoriaClient()

        def _load_refs():
            characters = client.list_characters()
            config = client.get_system_config()
            try:
                max_session_characters = int(config.get("max_session_characters") or 6)
            except (TypeError, ValueError):
                max_session_characters = 6
            return {
                "characters": characters,
                "max_session_characters": max(1, max_session_characters),
            }

        return await asyncio.to_thread(_load_refs)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/memoria/youtube-live/global-suffix")
async def get_youtube_live_global_suffix():
    try:
        data = await asyncio.to_thread(MemoriaClient().get_prompt_metadata, YOUTUBE_LIVE_GLOBAL_SUFFIX_KEY)
        return {
            "key": data.get("key") or YOUTUBE_LIVE_GLOBAL_SUFFIX_KEY,
            "template": data.get("current_template") or data.get("template") or "",
            "default_template": data.get("default_template") or "",
            "label": data.get("label") or "",
            "description": data.get("description") or "",
            "has_user_override": bool(data.get("has_user_override")),
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.put("/memoria/youtube-live/global-suffix")
async def update_youtube_live_global_suffix(body: YouTubeLiveGlobalSuffixRequest):
    try:
        data = await asyncio.to_thread(
            MemoriaClient().update_prompt_template,
            YOUTUBE_LIVE_GLOBAL_SUFFIX_KEY,
            body.template,
        )
        return {
            "key": data.get("key") or YOUTUBE_LIVE_GLOBAL_SUFFIX_KEY,
            "template": data.get("current_template") or data.get("template") or body.template,
            "default_template": data.get("default_template") or "",
            "label": data.get("label") or "",
            "description": data.get("description") or "",
            "has_user_override": bool(data.get("has_user_override", True)),
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/memoria/sessions")
async def memoria_sessions(limit: int = 100):
    try:
        return await asyncio.to_thread(MemoriaClient().list_sessions, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
