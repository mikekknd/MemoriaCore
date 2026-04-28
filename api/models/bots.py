"""Bot registry API DTOs。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


BotPlatform = Literal["telegram", "discord", "other"]


class BotRuntimeStatusDTO(BaseModel):
    bot_id: str
    platform: BotPlatform
    status: str = "disabled"
    running: bool = False
    last_error: Optional[str] = None


class BotConfigBase(BaseModel):
    platform: BotPlatform = "telegram"
    display_name: str = ""
    character_id: str = "default"
    token: str = ""
    enabled: bool = False


class BotConfigCreateRequest(BotConfigBase):
    bot_id: str = Field(..., min_length=3, max_length=64)


class BotConfigUpdateRequest(BotConfigBase):
    pass


class BotConfigDTO(BotConfigBase):
    bot_id: str
    runtime_status: Optional[BotRuntimeStatusDTO] = None
