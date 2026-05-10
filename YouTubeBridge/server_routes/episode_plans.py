"""Episode plan routes."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, HTTPException

from episode_plan_character_binding import (
    EpisodePlanCharacterBindingError,
    resolve_episode_plan_character_ids,
)
from live_episode_plan_contract import LiveEpisodePlanValidationError
from memoria_client import MemoriaClient
from models import EpisodePlanBindRequest, EpisodePlanImportRequest
from models import EpisodePlanEvidenceImportRequest


router = APIRouter()
_state = None
storage = None
manager = None
EPISODE_PLANS_ROOT = Path(__file__).resolve().parents[2] / "runtime" / "YouTubeBridge" / "EpisodePlans"


def configure(state):
    global _state, storage, manager
    _state = state
    storage = state.storage
    manager = state.manager


@router.get("/episode-plans")
async def list_episode_plans(limit: int = 100):
    return storage.list_live_episode_plans(limit=limit)


def _episode_plans_root() -> Path:
    return Path(EPISODE_PLANS_ROOT).resolve()


def _discover_local_episode_plan_files() -> list[Path]:
    root = _episode_plans_root()
    if not root.exists():
        return []
    candidates: list[Path] = []
    for path in root.rglob("episode-plan.json"):
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not resolved.is_file() or not resolved.is_relative_to(root):
            continue
        candidates.append(resolved)
    candidates.sort(key=lambda item: (-item.stat().st_mtime, item.relative_to(root).as_posix()))
    return candidates


def _local_episode_plan_files(max_files: int) -> list[Path]:
    max_files = max(1, min(int(max_files or 200), 500))
    candidates = _discover_local_episode_plan_files()
    return candidates[:max_files]


def _source_path_for_local_plan(path: Path) -> str:
    return path.resolve().relative_to(_episode_plans_root()).as_posix()


def _normalized_local_source_path(source_path: str) -> str:
    value = str(source_path or "").strip().replace("\\", "/")
    if not value or "\0" in value:
        return ""
    pure_path = PurePosixPath(value)
    episode_root_indexes = [
        index
        for index, part in enumerate(pure_path.parts)
        if part.lower() == "episodeplans"
    ]
    if episode_root_indexes:
        suffix = pure_path.parts[episode_root_indexes[-1] + 1 :]
        if not suffix:
            return ""
        pure_path = PurePosixPath(*suffix)
    if pure_path.is_absolute() or ".." in pure_path.parts:
        return ""
    if pure_path.parts and ":" in pure_path.parts[0]:
        return ""
    return pure_path.as_posix()


def _is_local_child_episode_plan_source(source_path: str) -> bool:
    normalized = _normalized_local_source_path(source_path)
    if not normalized:
        return False
    parts = PurePosixPath(normalized).parts
    return len(parts) >= 2 and normalized.endswith("/episode-plan.json")


def _prune_missing_local_episode_plans(current_plan_ids_by_source: dict[str, str] | None = None) -> list[str]:
    current_plan_ids_by_source = current_plan_ids_by_source or {}
    present_sources = {
        _normalized_local_source_path(_source_path_for_local_plan(path))
        for path in _discover_local_episode_plan_files()
    }
    removed: list[str] = []
    for plan in storage.list_live_episode_plans(limit=500):
        source_path = _normalized_local_source_path(plan.get("source_path") or "")
        if not _is_local_child_episode_plan_source(source_path):
            continue
        plan_id = str(plan.get("plan_id") or "").strip()
        if source_path in present_sources:
            current_plan_id = str(current_plan_ids_by_source.get(source_path) or "").strip()
            if not current_plan_id or plan_id == current_plan_id:
                continue
        if plan_id and storage.delete_live_episode_plan(plan_id):
            removed.append(plan_id)
    removed.sort()
    return removed


def _error_item(source_path: str, exc: Exception) -> dict[str, str]:
    return {
        "source_path": source_path,
        "error": str(exc),
    }


def _load_local_episode_plan(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_episode_plan_characters(plan_json: dict[str, Any]) -> list[str]:
    return resolve_episode_plan_character_ids(
        plan_json,
        MemoriaClient().list_characters(),
    )


def _active_live_session_for_episode_plan_evidence() -> str:
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


def _ensure_episode_plan_evidence_import_allowed() -> None:
    active_session_id = _active_live_session_for_episode_plan_evidence()
    if active_session_id:
        raise HTTPException(
            status_code=409,
            detail=f"直播中不產生或匯入 Fact Cards；active session={active_session_id}",
        )


def _safe_factcards_candidate(path: Path, root: Path) -> Path | None:
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(root):
        return None
    return resolved


def _episode_plan_factcards_dir(plan: dict[str, Any]) -> Path:
    root = _episode_plans_root()
    plan_id = str(plan.get("plan_id") or "").strip()
    source_path = str(plan.get("source_path") or "").strip().replace("\\", "/")
    candidates: list[Path] = []
    if source_path:
        source_file = _safe_factcards_candidate(root / source_path, root)
        if source_file is not None:
            candidates.append(source_file.parent / "factcards")
    if plan_id:
        candidates.append(root / plan_id / "factcards")

    safe_candidates = [
        resolved
        for candidate in candidates
        if (resolved := _safe_factcards_candidate(candidate, root)) is not None
    ]
    for candidate in safe_candidates:
        if candidate.is_dir():
            return candidate
    if safe_candidates:
        return safe_candidates[0]
    raise ValueError("找不到可用的企劃 factcards 路徑")


def _episode_plan_evidence_pack_payload(plan: dict[str, Any]) -> dict[str, str]:
    plan_id = str(plan.get("plan_id") or "").strip()
    title = str(plan.get("title") or plan_id or "Episode Plan").strip()
    safe_title = f"Evidence - {title}"[:200]
    description = (
        f"Imported Topic Evidence Cards from LiveEpisodePlan {plan_id}. "
        "This pack is linked to the selected live session for turn-level evidence retrieval."
    )
    return {
        "title": safe_title,
        "description": description[:1000],
    }


@router.post("/episode-plans/sync-local")
async def sync_local_episode_plans(max_files: int = 200):
    plans: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    current_plan_ids_by_source: dict[str, str] = {}
    for path in _local_episode_plan_files(max_files):
        source_path = _source_path_for_local_plan(path)
        try:
            saved = storage.upsert_live_episode_plan(
                _load_local_episode_plan(path),
                source_path=source_path,
            )
        except (OSError, json.JSONDecodeError, LiveEpisodePlanValidationError) as exc:
            errors.append(_error_item(source_path, exc))
            continue
        plans.append(saved)
        current_plan_ids_by_source[_normalized_local_source_path(source_path)] = str(saved.get("plan_id") or "")
    removed_plan_ids = _prune_missing_local_episode_plans(current_plan_ids_by_source)
    return {
        "root": str(_episode_plans_root()),
        "imported_count": len(plans),
        "skipped_count": len(errors),
        "removed_count": len(removed_plan_ids),
        "removed_plan_ids": removed_plan_ids,
        "plans": plans,
        "errors": errors,
    }


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
        plan = storage.get_live_episode_plan(body.plan_id)
        if not plan:
            raise ValueError("episode plan 不存在")
        character_ids = await asyncio.to_thread(
            _resolve_episode_plan_characters,
            plan.get("plan_json") or {},
        )
        storage.bind_episode_plan_to_session(session_id, body.plan_id)
        updated = storage.update_session_fields(session_id, character_ids=character_ids)
        if not updated:
            raise RuntimeError("episode plan 綁定失敗")
        return {
            **updated,
            "episode_plan_character_binding": {
                "character_ids": character_ids,
            },
        }
    except EpisodePlanCharacterBindingError as exc:
        raise HTTPException(status_code=400, detail=f"企劃角色對應失敗：{exc}") from exc
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(
            status_code=404 if "不存在" in message else 400,
            detail=message,
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"企劃角色對應失敗：{exc}") from exc


@router.delete("/sessions/{session_id}/episode-plan")
async def unbind_episode_plan(session_id: str):
    try:
        return storage.unbind_episode_plan_from_session(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/episode-plan/evidence/import")
async def import_episode_plan_evidence(session_id: str, body: EpisodePlanEvidenceImportRequest):
    _ensure_episode_plan_evidence_import_allowed()
    session = storage.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="live session 不存在")
    plan_id = str(body.plan_id or session.get("episode_plan_id") or "").strip()
    if not plan_id:
        raise HTTPException(status_code=400, detail="請先選擇或綁定節目企劃")
    plan = storage.get_live_episode_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="episode plan not found")
    factcards_dir = _episode_plan_factcards_dir(plan)
    if not factcards_dir.exists():
        raise HTTPException(status_code=404, detail=f"企劃 factcards 資料夾不存在：{factcards_dir}")
    pack = storage.create_topic_pack(_episode_plan_evidence_pack_payload(plan))
    try:
        result = await asyncio.to_thread(
            manager.import_fact_cards_folder,
            session_id,
            fact_cards_dir=factcards_dir,
            pack_id=int(pack["id"]),
            max_files=body.max_files,
        )
    except ValueError as exc:
        storage.delete_topic_pack(int(pack["id"]))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        storage.delete_topic_pack(int(pack["id"]))
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        **result,
        "plan_id": plan_id,
        "pack_id": int(pack["id"]),
        "fact_cards_dir": str(factcards_dir),
    }
