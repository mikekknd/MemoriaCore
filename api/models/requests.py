"""Pydantic request bodies — API 輸入資料驗證。"""
from pydantic import BaseModel, Field
from typing import Optional


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
    tts_enabled: Optional[bool] = None
    minimax_api_key: Optional[str] = None
    minimax_voice_id: Optional[str] = None
    minimax_model: Optional[str] = None
    minimax_speed: Optional[float] = None
    minimax_vol: Optional[float] = None
    minimax_pitch: Optional[int] = None
    browser_agent_enabled: Optional[bool] = None
    bash_tool_enabled: Optional[bool] = None
    bash_tool_allowed_commands: Optional[list[str]] = None
    # ⚠️ SECURITY: su_user_id 目前無任何權限管控，公開部署有極高風險。
    #   此欄位一旦寫入 user_prefs.json，匹配的 Telegram 用戶即獲得 private face 身份，
    #   可讀寫所有 visibility='private' 的記憶。上線前務必：
    #   1. 確認 /system/config API 已透過防火牆或 API Key 做存取控制
    #   2. 確認 server 只暴露於信任的網路區段（勿對外網開放）
    su_user_id: Optional[str] = None


class ExpandQueryRequest(BaseModel):
    query: str
    recent_history: list[dict] = []


class ChatSyncRequest(BaseModel):
    content: str
    session_id: Optional[str] = None


class CreateSessionRequest(BaseModel):
    channel: str = "rest"
    channel_uid: str = ""
    user_id: Optional[str] = None


class BlockUpdateRequest(BaseModel):
    new_overview: str
