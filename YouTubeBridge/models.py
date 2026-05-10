"""YouTubeBridge API models。"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class ConnectorConfig(BaseModel):
    connector_id: str = Field("youtube-main", min_length=3, max_length=64)
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
    session_id: str = Field("", max_length=64)
    connector_id: str = Field("youtube-main", min_length=3, max_length=64)
    display_name: str = ""
    video_id: str = ""
    live_chat_id: str = ""
    target_memoria_session_id: str = ""
    episode_plan_id: str = Field("", max_length=120)
    character_ids: list[str] = Field(default_factory=list)
    status: str = "stopped"
    auto_connect: bool = True
    auto_inject: bool = False
    inject_interval_seconds: int = Field(30, ge=5, le=600)
    inject_min_interval_seconds: int = Field(10, ge=5, le=600)
    inject_min_interval_ratio: float = Field(0.32, ge=0.05, le=1.0)
    min_pending_events: int = Field(1, ge=1, le=100)
    max_pending_events: int = Field(12, ge=1, le=200)
    dynamic_inject_enabled: bool = True
    max_context_messages: int = Field(50, ge=1, le=100)
    max_context_chars: int = Field(8000, ge=1000, le=20000)
    retention_days: int = Field(30, ge=1, le=365)
    planned_duration_minutes: int = Field(30, ge=0, le=720)
    auto_finalize_on_duration: bool = True
    auto_delete_after_processed: bool = True
    director_guidance: str = Field("", max_length=2000)
    host_interaction_rules: str = Field("", max_length=4000)
    program_segment_plan: str = Field("", max_length=4000)
    program_segment_turns: int = Field(3, ge=1, le=12)
    auto_test_events_enabled: bool = False
    test_event_min_seconds: int = Field(20, ge=1, le=3600)
    test_event_max_seconds: int = Field(45, ge=1, le=3600)
    test_event_count_per_tick: int = Field(3, ge=1, le=30)
    test_event_use_llm: bool = True
    test_super_chat_count_per_tick: int = Field(0, ge=0, le=30)
    test_malicious_sc_enabled: bool = False
    test_sc_burst_mode: bool = False
    sc_interrupt_cooldown_seconds: int = Field(30, ge=0, le=600)
    max_sc_per_batch: int = Field(5, ge=1, le=30)
    director_anchor_every_turns: int = Field(2, ge=1, le=10)
    director_dialogue_expansion_enabled: bool = True
    director_group_turn_limit: int = Field(3, ge=1, le=12)
    episode_plan_handoff_gap_seconds: int = Field(2, ge=1, le=5)
    episode_plan_turn_gap_seconds: int = Field(8, ge=1, le=30)
    director_max_chat_batches_before_anchor: int = Field(2, ge=1, le=10)
    director_offtopic_policy: str = Field("defer", max_length=40)
    director_sc_burst_policy: str = Field("summarize_batch", max_length=40)
    research_enabled: bool = False
    research_cooldown_seconds: int = Field(300, ge=0, le=3600)
    research_max_per_session: int = Field(12, ge=0, le=100)
    auto_sc_thanks_on_finalize: bool = True

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        if not value.replace("_", "").replace("-", "").isalnum():
            raise ValueError("session_id 只能包含英數、底線、連字號")
        return value

    @field_validator("character_ids")
    @classmethod
    def normalize_character_ids(cls, value: list[str]) -> list[str]:
        return [str(v).strip() for v in value if str(v).strip()]


class ReplyRecentRequest(BaseModel):
    content: str = "請根據已提供的 Topic Pack / fact card / YouTube 直播留言上下文回應。不要自行開啟瀏覽器或搜尋網頁。"
    memoria_session_id: str = ""
    character_ids: list[str] = Field(default_factory=list)
    event_ids: list[int] = Field(default_factory=list)
    max_events: int = Field(50, ge=1, le=100)
    priority: int = Field(200, ge=0, le=1000)


class CleanupRequest(BaseModel):
    retention_days: int | None = Field(None, ge=1, le=365)


class SummarizeRequest(BaseModel):
    force: bool = False
    min_events: int = Field(1, ge=1, le=1000)
    max_events: int = Field(1000, ge=1, le=5000)
    chunk_size: int = Field(120, ge=20, le=500)
    include_memoria_session: bool = True
    safe_memory_text: bool = True


class InterruptRequest(BaseModel):
    reason: str = "manual_interrupt"


class EpisodePlanImportRequest(BaseModel):
    plan_json: dict = Field(default_factory=dict)
    source_path: str = Field("", max_length=1000)


class EpisodePlanBindRequest(BaseModel):
    plan_id: str = Field("", max_length=120)


class EpisodePlanEvidenceImportRequest(BaseModel):
    plan_id: str = Field("", max_length=120)
    max_files: int = Field(50, ge=1, le=200)


class DirectorStartRequest(BaseModel):
    idle_seconds: int = Field(60, ge=1, le=3600)
    guidance: str = Field("", max_length=2000)
    kickoff: bool = False


class DirectorGuidanceRequest(BaseModel):
    guidance: str = Field("", max_length=2000)


class WriteMemoryRequest(BaseModel):
    force: bool = False


class TestChatGenerateRequest(BaseModel):
    count: int = Field(5, ge=1, le=30)
    topic_hint: str = Field("", max_length=1200)
    use_llm: bool = True
    super_chat_count: int = Field(0, ge=0, le=30)
    include_malicious_sc: bool = False
    sc_burst: bool = False


class TopicPackCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=1000)


class TopicPackUpdateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=1000)


class TopicPackEntryCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1, max_length=4000)
    source_url: str = Field("", max_length=1000)
    source_type: str = Field("manual", max_length=80)
    tags: list[str] = Field(default_factory=list)


class TopicPackEntryUpdateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1, max_length=4000)
    source_url: str = Field("", max_length=1000)
    source_type: str = Field("manual", max_length=80)
    tags: list[str] = Field(default_factory=list)


class FactCardImportRequest(BaseModel):
    pack_id: int | None = None
    max_files: int = Field(50, ge=1, le=200)


class FactCardGenerateRequest(BaseModel):
    topic: str = Field("動畫新番最新一話細節討論", min_length=1, max_length=500)
    pack_id: int | None = None
    output_name: str = Field("", max_length=120)
    timeout_seconds: int = Field(300, ge=30, le=900)


class ResearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    pack_id: int | None = None


class E2ECheckpointRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=120)


class MemoriaAuthConfig(BaseModel):
    base_url: str = Field("http://localhost:8088/api/v1", max_length=500)
    username: str = Field("", max_length=128)
    password: str = Field("", max_length=512)
    admin_bypass: bool = True


class YouTubeLiveGlobalSuffixRequest(BaseModel):
    template: str = Field("", max_length=20000)


class LivePersonaOverlayRequest(BaseModel):
    enabled: bool = False
    mode: str = Field("replace", max_length=20)
    system_prompt: str = Field("", max_length=8000)
    self_address: str = Field("", max_length=120)
    addressing: dict[str, str] = Field(default_factory=dict)
    opening_intro: str = Field("", max_length=1200)
    reply_rules: str = Field("", max_length=2000)

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        value = str(value or "replace").strip()
        if value not in {"replace", "append"}:
            raise ValueError("mode 必須是 replace 或 append")
        return value
