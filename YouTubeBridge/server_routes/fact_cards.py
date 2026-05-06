"""Fact card routes。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from models import FactCardGenerateRequest, FactCardImportRequest


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


def _active_live_session_for_fact_cards() -> str:
    for session in storage.list_sessions():
        session_id = str(session.get("session_id") or "")
        if not session_id:
            continue
        runtime_status = {}
        try:
            runtime_status = manager.get_status(session_id)
        except Exception:
            runtime_status = {}
        status = str(runtime_status.get("status") or session.get("status") or "")
        if runtime_status.get("running") or status in {"starting", "running", "closing"}:
            return session_id
    return ""


def _ensure_fact_card_mutation_allowed() -> None:
    active_session_id = _active_live_session_for_fact_cards()
    if active_session_id:
        raise HTTPException(
            status_code=409,
            detail=f"直播中不產生或匯入 Fact Cards；active session={active_session_id}",
        )


@router.post("/topic-packs/fact-cards/import-folder")
async def import_fact_cards_folder_to_pack(body: FactCardImportRequest):
    _ensure_fact_card_mutation_allowed()
    try:
        return await asyncio.to_thread(
            manager.import_fact_cards_folder_to_pack,
            pack_id=body.pack_id,
            max_files=body.max_files,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/topic-packs/fact-cards/generate")
async def generate_fact_cards_with_gemini_to_pack(body: FactCardGenerateRequest):
    _ensure_fact_card_mutation_allowed()
    try:
        return await asyncio.to_thread(
            manager.generate_fact_cards_with_gemini_to_pack,
            topic=body.topic,
            pack_id=body.pack_id,
            output_name=body.output_name or None,
            timeout_seconds=body.timeout_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/sessions/{session_id}/fact-cards/import-folder")
async def import_fact_cards_folder(session_id: str, body: FactCardImportRequest):
    _ensure_fact_card_mutation_allowed()
    try:
        return await asyncio.to_thread(
            manager.import_fact_cards_folder,
            session_id,
            pack_id=body.pack_id,
            max_files=body.max_files,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/sessions/{session_id}/fact-cards/generate")
async def generate_fact_cards_with_gemini(session_id: str, body: FactCardGenerateRequest):
    _ensure_fact_card_mutation_allowed()
    try:
        return await asyncio.to_thread(
            manager.generate_fact_cards_with_gemini,
            session_id,
            topic=body.topic,
            pack_id=body.pack_id,
            output_name=body.output_name or None,
            timeout_seconds=body.timeout_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
