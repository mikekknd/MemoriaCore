"""Fact card routes。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from models import FactCardGenerateRequest, FactCardImportRequest, TopicPackAutoBuildRequest


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


@router.post("/topic-packs/fact-cards/import-folder")
async def import_fact_cards_folder_to_pack(body: FactCardImportRequest):
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


@router.post("/sessions/{session_id}/topic-packs/auto-build")
async def auto_build_session_topic_pack(session_id: str, body: TopicPackAutoBuildRequest):
    try:
        return await manager.auto_build_topic_pack(
            session_id,
            topic=body.topic,
            pack_id=body.pack_id,
            card_count=body.card_count,
            use_research=body.use_research,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/sessions/{session_id}/fact-cards/import-folder")
async def import_fact_cards_folder(session_id: str, body: FactCardImportRequest):
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
