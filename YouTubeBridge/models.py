"""YouTubeBridge API models。"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class ConnectorConfig(BaseModel):
    connector_id: str = Field(..., min_length=3, max_length=64)
    display_name: str = ""
    api_key: str = ""
    enabled: bool = True

    @field_validator("connector_id")
    @classmethod
    def validate_connector_id(cls, value: str) -> str:
        value = value.strip()
        if not value.replace("_", "").replace("-", "").isalnum():
            raise ValueError("connector_id 只能包含英數、底線、連字號")
        return value


class LiveSessionConfig(BaseModel):
    session_id: str = Field(..., min_length=3, max_length=64)
    connector_id: str = Field(..., min_length=3, max_length=64)
    display_name: str = ""
    video_id: str = ""
    live_chat_id: str = ""
    target_memoria_session_id: str = ""
    character_ids: list[str] = Field(default_factory=list)
    status: str = "stopped"
    auto_connect: bool = False
    auto_inject: bool = False
    inject_interval_seconds: int = Field(30, ge=5, le=600)
    min_pending_events: int = Field(1, ge=1, le=100)
    max_context_messages: int = Field(50, ge=1, le=100)
    max_context_chars: int = Field(8000, ge=1000, le=20000)
    retention_days: int = Field(30, ge=1, le=365)

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, value: str) -> str:
        value = value.strip()
        if not value.replace("_", "").replace("-", "").isalnum():
            raise ValueError("session_id 只能包含英數、底線、連字號")
        return value

    @field_validator("character_ids")
    @classmethod
    def normalize_character_ids(cls, value: list[str]) -> list[str]:
        return [str(v).strip() for v in value if str(v).strip()]


class ReplyRecentRequest(BaseModel):
    content: str = "請根據已帶入的 YouTube 直播留言上下文回應。不要開啟瀏覽器或搜尋網頁。"
    memoria_session_id: str = ""
    character_ids: list[str] = Field(default_factory=list)
    event_ids: list[int] = Field(default_factory=list)
    max_events: int = Field(50, ge=1, le=100)


class CleanupRequest(BaseModel):
    retention_days: int | None = Field(None, ge=1, le=365)
