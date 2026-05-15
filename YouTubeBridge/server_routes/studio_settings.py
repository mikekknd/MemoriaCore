"""Studio 專用設定聚合 routes。"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime
from pathlib import Path
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from free_talk_topics import load_free_talk_sidecar, load_free_talk_topic_library
from memoria_client import MemoriaClient
from models import (
    StudioAvatarUploadRequest,
    StudioDisplaySettings,
    StudioLiveDefaults,
    StudioSettingsPatch,
    StudioTestSettings,
)
from server_helpers import public_connector
from server_routes.persona_overlays import list_tts_sources


router = APIRouter()
_state = None
storage = None
manager = None
summary_manager = None


AVATAR_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
AVATAR_EXT_TYPES = {value: key for key, value in AVATAR_TYPES.items()}
MAX_AVATAR_BYTES = 2 * 1024 * 1024
DATA_URL_RE = re.compile(r"^data:(?P<mime>image/(?:png|jpeg|webp|gif));base64,(?P<data>.+)$", re.DOTALL)


def configure(state):
    global _state, storage, manager, summary_manager
    _state = state
    storage = state.storage
    manager = state.manager
    summary_manager = state.summary_manager


def _require_state():
    if _state is None:
        raise RuntimeError("server route state is not configured")
    return _state


def _settings_with_defaults(model_cls, payload: dict | None) -> dict:
    data = payload if isinstance(payload, dict) else {}
    return model_cls(**data).model_dump()


def _avatar_root() -> Path:
    state = _require_state()
    root = Path(getattr(state, "studio_avatar_root", None) or Path("runtime") / "YouTubeBridge" / "StudioAvatars")
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _free_talk_topic_root() -> Path:
    state = _require_state()
    configured = getattr(state, "free_talk_topic_root", None)
    root = Path(configured) if configured and Path(configured) != Path() else Path("runtime") / "YouTubeBridge" / "freeTalkTopics"
    return root.resolve()


def _free_talk_sidecar_path(episode_plan_id: str) -> Path | None:
    state = _require_state()
    plan_id = str(episode_plan_id or "").strip()
    if not plan_id:
        return None
    get_plan = getattr(storage, "get_live_episode_plan", None) or getattr(storage, "get_episode_plan", None)
    if not get_plan:
        return None
    plan = get_plan(plan_id)
    source_path = plan.get("source_path") if isinstance(plan, dict) else None
    if not source_path:
        return None
    plan_path = Path(source_path)
    if not plan_path.is_absolute():
        episode_plan_root = Path(getattr(state, "episode_plan_root", None) or Path("runtime") / "YouTubeBridge" / "EpisodePlans")
        plan_path = episode_plan_root / plan_path
    return plan_path.parent / "free-talk-topics.json"


def _avatar_response(path: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "url": f"/studio/avatar-assets/{path.name}",
        "size_bytes": stat.st_size,
        "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "content_type": AVATAR_EXT_TYPES.get(path.suffix.lower(), "application/octet-stream"),
    }


def _safe_avatar_stem(filename: str) -> str:
    stem = Path(filename).stem
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-_")
    return (safe or "avatar")[:80]


def _decode_avatar_data_url(data_url: str) -> tuple[str, bytes]:
    match = DATA_URL_RE.match(str(data_url or "").strip())
    if not match:
        raise HTTPException(status_code=400, detail="本地頭像只支援 PNG/JPEG/WebP/GIF data URL")
    mime = match.group("mime")
    try:
        payload = base64.b64decode(match.group("data"), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="頭像資料不是有效的 base64") from exc
    if not payload:
        raise HTTPException(status_code=400, detail="頭像檔案不可為空")
    if len(payload) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=400, detail="頭像檔案不可超過 2MB")
    return mime, payload


async def _studio_settings_response() -> dict:
    settings = storage.get_all_studio_settings()
    connector = storage.ensure_single_connector()
    live_defaults_payload = settings.get("live_defaults")
    live_defaults = _settings_with_defaults(StudioLiveDefaults, live_defaults_payload)
    live_defaults["post_plan_free_talk_topic_pack_ids_configured"] = (
        isinstance(live_defaults_payload, dict)
        and "post_plan_free_talk_topic_pack_ids" in live_defaults_payload
    )
    return {
        "connector": public_connector(connector),
        "memoria_auth": storage.get_public_memoria_config(),
        "test_settings": _settings_with_defaults(StudioTestSettings, settings.get("test_settings")),
        "display_settings": _settings_with_defaults(StudioDisplaySettings, settings.get("display_settings")),
        "live_defaults": live_defaults,
        "persona_overlays": storage.list_live_persona_overlays(),
        "tts_profiles": storage.list_tts_profiles(),
        "tts_sources": await list_tts_sources(),
    }


@router.get("/studio/settings")
async def get_studio_settings():
    _require_state()
    return await _studio_settings_response()


@router.patch("/studio/settings")
async def update_studio_settings(body: StudioSettingsPatch):
    state = _require_state()
    requested = body.model_fields_set
    if "connector" in requested and body.connector is not None:
        storage.upsert_single_connector(body.connector.model_dump())
    if "memoria_auth" in requested and body.memoria_auth is not None:
        storage.upsert_memoria_config(body.memoria_auth.model_dump())
        if state.apply_memoria_config:
            state.apply_memoria_config()
        if manager and hasattr(manager, "reset_memoria_client"):
            manager.reset_memoria_client()
        if summary_manager is not None:
            summary_manager.memoria_client = MemoriaClient()
    if "test_settings" in requested and body.test_settings is not None:
        storage.upsert_studio_settings("test_settings", body.test_settings.model_dump())
    if "display_settings" in requested and body.display_settings is not None:
        storage.upsert_studio_settings("display_settings", body.display_settings.model_dump())
    if "live_defaults" in requested and body.live_defaults is not None:
        existing_live_defaults = storage.get_studio_settings("live_defaults")
        storage.upsert_studio_settings(
            "live_defaults",
            {
                **existing_live_defaults,
                **body.live_defaults.model_dump(exclude_unset=True),
            },
        )
    return await _studio_settings_response()


@router.get("/studio/free-talk-topics")
async def list_studio_free_talk_topics(episode_plan_id: str = ""):
    _require_state()
    library = load_free_talk_topic_library(_free_talk_topic_root())
    sidecar = load_free_talk_sidecar(_free_talk_sidecar_path(episode_plan_id))
    return {
        **library,
        "sidecar": sidecar,
        "total_topic_count": int(library.get("total_topic_count", 0)) + int(sidecar.get("topic_count", 0)),
    }


@router.get("/studio/avatar-assets")
async def list_studio_avatar_assets():
    root = _avatar_root()
    avatars = [
        _avatar_response(path)
        for path in root.iterdir()
        if path.is_file() and path.suffix.lower() in AVATAR_EXT_TYPES
    ]
    avatars.sort(key=lambda item: item["updated_at"], reverse=True)
    return {"avatars": avatars[:200], "max_bytes": MAX_AVATAR_BYTES}


@router.post("/studio/avatar-assets")
async def upload_studio_avatar_asset(body: StudioAvatarUploadRequest):
    root = _avatar_root()
    mime, payload = _decode_avatar_data_url(body.data_url)
    ext = AVATAR_TYPES[mime]
    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    name = f"{stamp}-{_safe_avatar_stem(body.filename)}{ext}"
    path = (root / name).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="頭像檔名不合法") from exc
    path.write_bytes(payload)
    return _avatar_response(path)


@router.get("/studio/avatar-assets/{filename:path}")
async def get_studio_avatar_asset(filename: str):
    root = _avatar_root()
    requested = (root / filename).resolve()
    try:
        requested.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="avatar not found") from exc
    if not requested.is_file() or requested.suffix.lower() not in AVATAR_EXT_TYPES:
        raise HTTPException(status_code=404, detail="avatar not found")
    return FileResponse(requested, media_type=AVATAR_EXT_TYPES[requested.suffix.lower()])
