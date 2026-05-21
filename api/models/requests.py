"""Pydantic request bodies — API 輸入資料驗證。"""
import re
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Any, Literal, Optional

from api.auth_utils import WEAK_PASSWORDS
from core.i18n import normalize_locale


USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")

TRANSIENT_CONTEXT_DEFAULT_MAX_CHARS = 8000
TRANSIENT_CONTEXT_MIN_MAX_CHARS = 1000
TRANSIENT_CONTEXT_HARD_MAX_CHARS = 12000
TRANSIENT_CONTEXT_SOURCE_MAX_CHARS = 64


class SearchRequest(BaseModel):
    query: str
    combined_keywords: str = ""
    top_k: int = 2
    alpha: float = 0.6
    threshold: float = 0.5
    hard_base: float = 0.55


class CoreSearchRequest(BaseModel):
    query: str
    top_k: int = 1
    threshold: float = 0.45


class ProfileSearchRequest(BaseModel):
    query: str
    top_k: int = 3
    threshold: float = 0.5


class ProfileUpsertRequest(BaseModel):
    fact_value: str
    category: str
    source_context: str = ""
    confidence: float = 1.0


class ConsolidateRequest(BaseModel):
    cluster_threshold: float = 0.75
    min_group_size: int = 2


class PreferenceAggregateRequest(BaseModel):
    score_threshold: float = 3.0


class SyntheticRequest(BaseModel):
    topic: str
    turns: int = 8
    sim_timestamp: Optional[str] = None


class ConfigUpdateRequest(BaseModel):
    """部分更新：僅提供需要修改的欄位"""
    ui_locale: Optional[str] = None
    routing_config: Optional[dict] = None
    temperature: Optional[float] = None
    ui_alpha: Optional[float] = None
    memory_threshold: Optional[float] = None
    memory_hard_base: Optional[float] = None
    shift_threshold: Optional[float] = None
    cluster_threshold: Optional[float] = None
    embed_model: Optional[str] = None
    openai_key: Optional[str] = None
    or_key: Optional[str] = None
    llamacpp_url: Optional[str] = None
    persona_sync_enabled: Optional[bool] = None
    persona_sync_min_messages: Optional[int] = None
    persona_sync_max_per_day: Optional[int] = None
    persona_sync_idle_minutes: Optional[int] = None
    persona_probe_url: Optional[str] = None
    persona_sync_fragment_limit: Optional[int] = None
    telegram_bot_token: Optional[str] = None
    tavily_api_key: Optional[str] = None
    openweather_api_key: Optional[str] = None
    weather_city: Optional[str] = None
    bg_gather_interval: Optional[int] = None
    active_character_id: Optional[str] = None
    dual_layer_enabled: Optional[bool] = None
    group_chat_max_bot_turns: Optional[int] = None
    group_chat_turn_delay_seconds: Optional[float] = None
    opening_penalty_enabled: Optional[bool] = None
    opening_penalty_tokenizer_ref: Optional[str] = None
    tts_enabled: Optional[bool] = None
    image_generation_enabled: Optional[bool] = None
    minimax_api_key: Optional[str] = None
    minimax_voice_id: Optional[str] = None
    minimax_model: Optional[str] = None
    minimax_speed: Optional[float] = None
    minimax_vol: Optional[float] = None
    minimax_pitch: Optional[int] = None
    browser_agent_enabled: Optional[bool] = None
    bash_tool_enabled: Optional[bool] = None
    bash_tool_allowed_commands: Optional[list[str]] = None
    registration_enabled: Optional[bool] = None
    admin_bypass_enabled: Optional[bool] = None
    # ⚠️ SECURITY: su_user_id 目前無任何權限管控，公開部署有極高風險。
    #   此欄位一旦寫入 user_prefs.json，匹配的 Telegram 用戶即獲得 private face 身份，
    #   可讀寫所有 visibility='private' 的記憶。上線前務必：
    #   1. 確認 /system/config API 已透過防火牆或 API Key 做存取控制
    #   2. 確認 server 只暴露於信任的網路區段（勿對外網開放）
    su_user_id: Optional[str] = None

    @field_validator("ui_locale")
    @classmethod
    def validate_ui_locale(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_locale(value)


class ExpandQueryRequest(BaseModel):
    query: str
    recent_history: list[dict] = []


class TransientContextRequest(BaseModel):
    """Final-chat-only runtime context supplied by an app integration.

    Agent navigation note:
    - `context_text` is capped by TRANSIENT_CONTEXT_* constants.
    - The rendered LLM prompt uses only `context_text`.
    - `source` is debug metadata and is not rendered into final chat.
    """

    source: str = Field("runtime", max_length=128)
    context_text: str
    max_chars: Optional[int] = Field(
        None,
        ge=TRANSIENT_CONTEXT_MIN_MAX_CHARS,
        le=TRANSIENT_CONTEXT_HARD_MAX_CHARS,
    )


class ChatSyncRequest(BaseModel):
    content: str
    display_content: Optional[str] = None
    session_id: Optional[str] = None
    character_ids: Optional[list[str]] = None
    group_name: Optional[str] = None
    channel: Optional[str] = None
    channel_uid: Optional[str] = None
    user_id: Optional[str] = None
    channel_class: Optional[Literal["public", "private"]] = None
    persona_face: Optional[Literal["public", "private"]] = None
    external_context: Optional[dict] = None
    transient_context: Optional[TransientContextRequest] = None
    include_speech: bool = True
    memory_write_policy: Literal["normal", "transient"] = "normal"


class PromptJsonRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    prompt_key: str = Field(..., min_length=1, max_length=128)
    variables: dict[str, Any] = Field(default_factory=dict)
    task_key: str = Field("compress", min_length=1, max_length=64)
    temperature: float = Field(0.1, ge=0.0, le=2.0)
    response_schema: Optional[dict[str, Any]] = Field(None, alias="schema")


class EmbedTextRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=12000)
    model: str = Field("", max_length=128)


class CreateSessionRequest(BaseModel):
    channel: str = "rest"
    channel_uid: str = ""
    user_id: Optional[str] = None
    character_id: Optional[str] = None
    character_ids: list[str] = []
    group_name: str = ""


class SessionSystemEventRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    debug_info: dict[str, Any] = Field(default_factory=dict)


class SessionAssistantEventRequest(BaseModel):
    content: str = Field(..., min_length=1)
    character_id: Optional[str] = None
    character_name: Optional[str] = None
    debug_info: dict[str, Any] = Field(default_factory=dict)
    extracted_entities: Optional[list[str]] = None


class BlockUpdateRequest(BaseModel):
    new_overview: str


class SharedYouTubeSummaryMemoryRequest(BaseModel):
    summary_id: int
    session_id: str
    video_id: str = ""
    memory_text: str = Field(..., min_length=1, max_length=2000)
    character_ids: list[str] = Field(default_factory=list)


class MaintenanceModeRequest(BaseModel):
    enabled: bool


class DropMaintenanceTableRequest(BaseModel):
    table_name: str
    confirm_table_name: str

    @model_validator(mode="after")
    def validate_confirmation(self):
        if self.table_name != self.confirm_table_name:
            raise ValueError("confirm_table_name 必須與 table_name 相同")
        return self


# ── Auth ─────────────────────────────────────────────────
class RegisterRequest(BaseModel):
    username: str
    password: str
    password_confirm: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        username = value.strip().lower()
        if not USERNAME_RE.fullmatch(username):
            raise ValueError("username 必須為 3-32 字元，且只能包含英數、底線、連字號")
        return username

    @model_validator(mode="after")
    def validate_passwords(self):
        if self.password != self.password_confirm:
            raise ValueError("兩次密碼輸入不一致")
        validate_password_strength(self.username, self.password)
        return self


class LoginRequest(BaseModel):
    username: str
    password: str

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()


class ProfileUpdateRequest(BaseModel):
    nickname: Optional[str] = None
    telegram_uid: Optional[str] = None
    discord_uid: Optional[str] = None


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str

    @model_validator(mode="after")
    def validate_new_password(self):
        validate_password_strength("", self.new_password)
        return self


class AdminPasswordResetRequest(BaseModel):
    new_password: str


class AdminUserDeleteRequest(BaseModel):
    confirm_username: str


def validate_password_strength(username: str, password: str) -> None:
    normalized = password.strip().lower()
    if len(password) < 6:
        raise ValueError("密碼至少需要 6 字元")
    if username and normalized == username.strip().lower():
        raise ValueError("密碼不可與 username 相同")
    if normalized in WEAK_PASSWORDS:
        raise ValueError("密碼過於常見，請改用較強的密碼")
