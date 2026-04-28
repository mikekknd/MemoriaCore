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


# ── Public Character ─────────────────────────────────────
class PublicCharacterDTO(BaseModel):
    character_id: str
    name: str


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
    context_messages_count: int = 0
    perf_timing: PerfTimingDTO = PerfTimingDTO()


class ChatSyncResponseDTO(BaseModel):
    reply: str
    extracted_entities: list[str] = []
    retrieval_context: RetrievalContextDTO = RetrievalContextDTO()
    cited_memory_uids: list[str] = []
    internal_thought: Optional[str] = None
    speech: Optional[str] = None
    thinking_speech: Optional[str] = None


# ── Auth ─────────────────────────────────────────────────
class AuthUserDTO(BaseModel):
    id: int
    username: str
    nickname: str = ""
    role: str = "user"
    telegram_uid: Optional[str] = None
    discord_uid: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    csrf_token: Optional[str] = None


class AuthResponseDTO(BaseModel):
    user: AuthUserDTO
    csrf_token: str


class AuthSessionDTO(BaseModel):
    session_id: str


class AdminUserStatsDTO(BaseModel):
    sessions: int = 0
    messages: int = 0
    memory_blocks: int = 0
    core_memories: int = 0
    profiles: int = 0
    topics: int = 0


class AdminUserDTO(AuthUserDTO):
    token_version: int = 0
    stats: AdminUserStatsDTO = Field(default_factory=AdminUserStatsDTO)


class AdminUserDeleteResultDTO(BaseModel):
    status: str
    deleted_user_id: int
    deleted_username: str
    deleted_counts: AdminUserStatsDTO


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
    persona_sync_enabled: bool = True
    persona_sync_min_messages: int = 50
    persona_sync_max_per_day: int = 2
    persona_sync_idle_minutes: int = 10
    persona_probe_url: str = "http://localhost:8089"
    persona_sync_fragment_limit: int = 400
    telegram_bot_token: str = ""
    tavily_api_key: str = ""
    openweather_api_key: str = ""
    weather_city: str = ""
    bg_gather_interval: int = 14400
    active_character_id: Optional[str] = "default"
    dual_layer_enabled: bool = False
    tts_enabled: bool = False
    minimax_api_key: str = ""
    minimax_voice_id: str = "moss_audio_7c2b39d9-1006-11f1-b9c4-4ea5324904c7"
    minimax_model: str = "speech-2.8-hd"
    minimax_speed: float = 1.0
    minimax_vol: float = 1.0
    minimax_pitch: int = 0
    browser_agent_enabled: bool = False
    bash_tool_enabled: bool = False
    bash_tool_allowed_commands: list[str] = []
    registration_enabled: bool = True
    # ⚠️ SECURITY: su_user_id 目前無任何權限管控，詳見 api/models/requests.py 的風險說明
    su_user_id: str = ""


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
