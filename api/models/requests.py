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
    ai_observe_enabled: Optional[bool] = None
    telegram_bot_token: Optional[str] = None
    tavily_api_key: Optional[str] = None
    bg_gather_interval: Optional[int] = None


class ExpandQueryRequest(BaseModel):
    query: str
    recent_history: list[dict] = []


class ChatSyncRequest(BaseModel):
    content: str
    session_id: Optional[str] = None


class CreateSessionRequest(BaseModel):
    channel: str = "rest"
    channel_uid: str = ""


class BlockUpdateRequest(BaseModel):
    new_overview: str
