"""Summary routes。"""
from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, HTTPException

from memoria_client import MemoriaClient
from models import CleanupRequest, SummarizeRequest, WriteMemoryRequest


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


@router.post("/sessions/{session_id}/summarize")
async def summarize_session(session_id: str, body: SummarizeRequest):
    session = storage.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        result = await asyncio.to_thread(
            summary_manager.summarize_session,
            session_id,
            force=body.force,
            min_events=body.min_events,
            max_events=body.max_events,
            chunk_size=body.chunk_size,
            include_memoria_session=body.include_memoria_session,
            safe_memory_text=body.safe_memory_text,
        )
        if session.get("auto_delete_after_processed") and not (session.get("character_ids") or []):
            await manager.stop_session(session_id)
            deleted = storage.delete_session(session_id)
            return {"summary": result, "runtime_session_deleted": deleted}
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


def _summary_matches_phase(summary: dict, summary_phase: str) -> bool:
    metadata = summary.get("metadata") if isinstance(summary.get("metadata"), dict) else {}
    return str(metadata.get("summary_phase") or "") == summary_phase


def _list_summaries_by_phase(session_id: str | None, summary_phase: str, limit: int) -> list[dict]:
    phase = str(summary_phase or "").strip()
    if session_id and hasattr(storage, "list_session_summaries_by_phase"):
        return storage.list_session_summaries_by_phase(session_id, summary_phase=phase, limit=limit)
    search_limit = max(1, min(int(limit or 100), 100)) * 5
    summaries = storage.list_summaries(session_id=session_id, limit=search_limit)
    return [summary for summary in summaries if _summary_matches_phase(summary, phase)][:limit]


def _get_session_summary_by_phase(session_id: str, summary_phase: str) -> dict | None:
    phase = str(summary_phase or "").strip()
    if hasattr(storage, "get_session_summary_by_phase"):
        return storage.get_session_summary_by_phase(session_id, phase)
    summaries = _list_summaries_by_phase(session_id, phase, 20)
    return summaries[0] if summaries else None


@router.get("/sessions/{session_id}/summary")
async def get_session_summary(session_id: str, summary_phase: str | None = None):
    if not storage.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    if summary_phase:
        summary = _get_session_summary_by_phase(session_id, summary_phase)
    else:
        summary = storage.get_session_summary(session_id)
    if not summary:
        raise HTTPException(status_code=404, detail="summary not found")
    return summary


@router.post("/sessions/{session_id}/summary/write-memory")
async def write_summary_memory(session_id: str, body: WriteMemoryRequest = WriteMemoryRequest()):
    session = storage.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    summary = storage.get_session_summary(session_id)
    if not summary:
        raise HTTPException(status_code=404, detail="summary not found")
    metadata = summary.get("metadata") if isinstance(summary.get("metadata"), dict) else {}
    if metadata.get("memory_write_status") == "completed" and not body.force:
        deleted = False
        if session.get("auto_delete_after_processed"):
            await manager.stop_session(session_id)
            deleted = storage.delete_session(session_id)
        return {
            "status": "completed",
            "reused": True,
            "summary": summary,
            "runtime_session_deleted": deleted,
        }
    memory_text = str(summary.get("memory_text") or "").strip()
    if not memory_text:
        raise HTTPException(status_code=400, detail="summary memory_text is empty")
    if metadata.get("memory_text_requires_review") and not body.force:
        raise HTTPException(status_code=409, detail="summary memory_text requires review before writing shared memory")
    character_ids = summary.get("character_ids") or session.get("character_ids") or []
    if not character_ids:
        raise HTTPException(status_code=400, detail="summary character_ids is empty")
    try:
        result = await asyncio.to_thread(
            MemoriaClient().write_shared_youtube_memory,
            summary_id=int(summary["id"]),
            session_id=session_id,
            video_id=str(summary.get("video_id") or session.get("video_id") or ""),
            memory_text=memory_text,
            character_ids=character_ids,
        )
    except Exception as exc:
        updated = storage.update_summary_metadata(
            int(summary["id"]),
            metadata={"memory_write_status": "failed", "memory_write_error": str(exc)[:500]},
        )
        raise HTTPException(status_code=502, detail={"error": str(exc), "summary": updated})
    updated = storage.update_summary_metadata(
        int(summary["id"]),
        metadata={
            "memory_write_status": "completed",
            "memory_block_id": result.get("block_id", ""),
            "memory_write_completed_at": datetime.now().isoformat(),
        },
    )
    deleted = False
    if session.get("auto_delete_after_processed"):
        await manager.stop_session(session_id)
        deleted = storage.delete_session(session_id)
    return {
        "status": "completed",
        "reused": False,
        "result": result,
        "summary": updated,
        "runtime_session_deleted": deleted,
    }


@router.get("/summaries")
async def list_summaries(session_id: str | None = None, limit: int = 100, summary_phase: str | None = None):
    if summary_phase:
        return _list_summaries_by_phase(session_id, summary_phase, limit)
    return storage.list_summaries(session_id=session_id, limit=limit)


@router.post("/sessions/{session_id}/cleanup")
async def cleanup_session_events(session_id: str, body: CleanupRequest):
    session = storage.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    days = body.retention_days or session.get("retention_days", 30)
    return {"deleted": storage.cleanup_events(session_id=session_id, retention_days=days)}
