"""Bot registry 管理 API。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import (
    get_bot_registry,
    get_character_manager,
    get_current_user,
    get_discord_bot_manager,
    get_storage,
    get_telegram_bot_manager,
    require_admin_user,
)
from api.models.bots import (
    BotConfigCreateRequest,
    BotConfigDTO,
    BotConfigUpdateRequest,
    BotRuntimeStatusDTO,
)
from core.bot_registry import BotRegistryError


router = APIRouter(prefix="/bots", tags=["bots"])


def _character_ids() -> set[str]:
    chars = get_character_manager().load_characters()
    return {c.get("character_id") for c in chars if c.get("character_id")}


def _dto(config: dict[str, Any]) -> BotConfigDTO:
    status = _runtime_status(config["bot_id"], config.get("platform", "telegram"))
    return BotConfigDTO(
        **config,
        runtime_status=BotRuntimeStatusDTO(**status),
    )


def _runtime_status(bot_id: str, platform: str) -> dict[str, Any]:
    if platform == "telegram":
        return get_telegram_bot_manager().get_status(bot_id, platform)
    if platform == "discord":
        return get_discord_bot_manager().get_status(bot_id, platform)
    return {
        "bot_id": bot_id,
        "platform": platform,
        "status": "unsupported",
        "running": False,
        "last_error": None,
    }


async def _sync_runtime_managers() -> None:
    await get_telegram_bot_manager().sync_from_registry()
    await get_discord_bot_manager().sync_from_registry()


async def _stop_runtime(bot_id: str, platform: str) -> None:
    if platform == "telegram":
        await get_telegram_bot_manager().stop_bot(bot_id)
    elif platform == "discord":
        await get_discord_bot_manager().stop_bot(bot_id)


async def _reload_runtime(bot_id: str, platform: str) -> None:
    if platform == "telegram":
        await get_telegram_bot_manager().reload_bot(bot_id)
    elif platform == "discord":
        await get_discord_bot_manager().reload_bot(bot_id)


def _raise_registry_error(exc: Exception) -> None:
    raise HTTPException(status_code=400, detail=str(exc))


@router.get("", response_model=list[BotConfigDTO])
async def list_bots(current_user: dict = Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理員權限")
    prefs = get_storage().load_prefs()
    configs = get_bot_registry().list_configs(prefs)
    return [_dto(c) for c in configs]


@router.post("", response_model=BotConfigDTO)
async def create_bot(body: BotConfigCreateRequest, current_user: dict = Depends(require_admin_user)):
    prefs = get_storage().load_prefs()
    registry = get_bot_registry()
    try:
        cfg = registry.upsert_config(
            body.model_dump(),
            character_ids=_character_ids(),
            prefs=prefs,
            create=True,
        )
    except BotRegistryError as exc:
        _raise_registry_error(exc)
    await _sync_runtime_managers()
    return _dto(cfg)


@router.get("/{bot_id}", response_model=BotConfigDTO)
async def get_bot(bot_id: str, current_user: dict = Depends(require_admin_user)):
    prefs = get_storage().load_prefs()
    cfg = get_bot_registry().get_config(bot_id, prefs)
    if not cfg:
        raise HTTPException(status_code=404, detail="bot not found")
    return _dto(cfg)


@router.put("/{bot_id}", response_model=BotConfigDTO)
async def update_bot(
    bot_id: str,
    body: BotConfigUpdateRequest,
    current_user: dict = Depends(require_admin_user),
):
    prefs = get_storage().load_prefs()
    registry = get_bot_registry()
    existing = registry.get_config(bot_id, prefs)
    if not existing:
        raise HTTPException(status_code=404, detail="bot not found")
    payload = body.model_dump()
    payload["bot_id"] = bot_id
    try:
        cfg = registry.upsert_config(
            payload,
            character_ids=_character_ids(),
            prefs=prefs,
            create=False,
        )
    except BotRegistryError as exc:
        _raise_registry_error(exc)
    await _sync_runtime_managers()
    return _dto(cfg)


@router.delete("/{bot_id}")
async def delete_bot(bot_id: str, current_user: dict = Depends(require_admin_user)):
    prefs = get_storage().load_prefs()
    registry = get_bot_registry()
    cfg = registry.get_config(bot_id, prefs)
    if not cfg:
        raise HTTPException(status_code=404, detail="bot not found")
    if not registry.delete_config(bot_id, prefs):
        raise HTTPException(status_code=404, detail="bot not found")
    await _stop_runtime(bot_id, cfg.get("platform", "telegram"))
    await _sync_runtime_managers()
    return {"status": "deleted", "bot_id": bot_id}


@router.post("/{bot_id}/reload", response_model=BotRuntimeStatusDTO)
async def reload_bot(bot_id: str, current_user: dict = Depends(require_admin_user)):
    prefs = get_storage().load_prefs()
    cfg = get_bot_registry().get_config(bot_id, prefs)
    if not cfg:
        raise HTTPException(status_code=404, detail="bot not found")
    platform = cfg.get("platform", "telegram")
    await _reload_runtime(bot_id, platform)
    status = _runtime_status(bot_id, platform)
    return BotRuntimeStatusDTO(**status)
