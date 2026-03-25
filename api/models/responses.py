"""Pydantic response DTOs — 與前端（Unity / Streamlit）的資料契約。"""
from pydantic import BaseModel, Field
from typing import Optional, Any


# ── 記憶區塊 ──────────────────────────────────────────────
class PreferenceTagDTO(BaseModel):
    tag: str
    intensity: float = 0.5


class DialogueMessageDTO(BaseModel):
    role: str
    content: str


class MemoryBlockDTO(BaseModel):
    block_id: str
    timestamp: str
    overview: str
    is_consolidated: bool = False
    encounter_count: float = 1.0
    potential_preferences: list[PreferenceTagDTO] = []
    raw_dialogues: list[DialogueMessageDTO] = []
    # 向量預設不傳，Unity 不需要
    overview_vector: Optional[list[float]] = None
    sparse_vector: Optional[dict[str, float]] = None


class SearchResultDTO(MemoryBlockDTO):
    """搜尋結果額外攜帶偵錯分數"""
    _debug_score: float = 0.0
    _debug_recency: float = 0.0
    _debug_raw_sim: float = 0.0
    _debug_sparse_raw: float = 0.0
    _debug_hard_base: float = 0.0
    _debug_sparse_norm: float = 0.0
    _debug_importance: float = 0.0


# ── 核心認知 ──────────────────────────────────────────────
class CoreMemoryDTO(BaseModel):
    core_id: str
    timestamp: str
    insight: str
    encounter_count: float = 1.0


# ── 使用者畫像 ────────────────────────────────────────────
class ProfileFactDTO(BaseModel):
    fact_key: str
    fact_value: str
    category: str
    confidence: float = 1.0
    timestamp: Optional[str] = None
    source_context: Optional[str] = None


class ProfileSearchResultDTO(BaseModel):
    fact_key: str
    fact_value: str
    category: str
    score: float


# ── Session ───────────────────────────────────────────────
class SessionMessageDTO(BaseModel):
    role: str
    content: str
    debug_info: Optional[dict] = None


class SessionDTO(BaseModel):
    session_id: str
    messages: list[SessionMessageDTO] = []
    last_entities: list[str] = []
    created_at: str
    last_active: str


# ── Log ───────────────────────────────────────────────────
class LogEntryDTO(BaseModel):
    timestamp: str
    type: str
    category: Optional[str] = None
    message: Optional[Any] = None      # 可能是 str 或 dict（人格引擎等模組寫入的格式）
    direction: Optional[str] = None
    model: Optional[str] = None
    content: Optional[Any] = None      # 同上，防禦性設計
    messages: Optional[list[dict]] = None
    details: Optional[dict] = None


# ── Health ────────────────────────────────────────────────
class HealthDTO(BaseModel):
    onnx_loaded: bool
    db_accessible: bool
    uptime_seconds: float


# ── Graph（力導向圖用） ──────────────────────────────────
class GraphNodeDTO(BaseModel):
    id: str
    type: str  # "block" | "core" | "profile"
    label: str
    weight: float = 1.0


class GraphEdgeDTO(BaseModel):
    source: str
    target: str
    weight: float


class GraphDTO(BaseModel):
    nodes: list[GraphNodeDTO] = []
    edges: list[GraphEdgeDTO] = []


# ── Chat 同步回應 ─────────────────────────────────────────
class PerfStepDTO(BaseModel):
    name: str
    ms: float

class PerfTimingDTO(BaseModel):
    total_ms: float = 0.0
    steps: list[PerfStepDTO] = []

class RetrievalContextDTO(BaseModel):
    original_query: str = ""
    expanded_keywords: str = ""
    inherited_tags: list[str] = []
    has_memory: bool = False
    block_count: int = 0
    threshold: float = 0.0
    hard_base: float = 0.0
    confidence: float = 0.0
    block_details: list[dict] = []
    core_debug_text: str = ""
    profile_debug_text: str = ""
    dynamic_prompt: str = ""
    perf_timing: PerfTimingDTO = PerfTimingDTO()


class ChatSyncResponseDTO(BaseModel):
    reply: str
    extracted_entities: list[str] = []
    retrieval_context: RetrievalContextDTO = RetrievalContextDTO()


# ── 系統設定 ──────────────────────────────────────────────
class SystemConfigDTO(BaseModel):
    routing_config: dict = {}
    temperature: float = 0.7
    ui_alpha: float = 0.6
    memory_threshold: float = 0.5
    memory_hard_base: float = 0.55
    shift_threshold: float = 0.55
    cluster_threshold: float = 0.75
    embed_model: str = "bge-m3:latest"
    openai_key: str = ""
    or_key: str = ""
    llamacpp_url: str = "http://localhost:8080"
    ai_observe_enabled: bool = True
    reflection_threshold: int = 5
    telegram_bot_token: str = ""
    tavily_api_key: str = ""
    bg_gather_interval: int = 14400


# ── 通用錯誤 ──────────────────────────────────────────────
# ── 對話歷史 ──────────────────────────────────────────────
class ConversationSessionDTO(BaseModel):
    session_id: str
    channel: str = "rest"
    channel_uid: str = ""
    created_at: str
    last_active: str
    is_active: bool = True
    message_count: int = 0


class ConversationHistoryDTO(BaseModel):
    session: ConversationSessionDTO
    messages: list[SessionMessageDTO] = []


# ── 通用錯誤 ──────────────────────────────────────────────
class ErrorDTO(BaseModel):
    code: str
    message: str


class ErrorEnvelope(BaseModel):
    error: ErrorDTO
