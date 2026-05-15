"""YouTubeBridge API models。"""
from __future__ import annotations

from typing import Literal

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
    director_audience_interrupt_cooldown_seconds: int = Field(30, ge=0, le=3600)
    director_max_audience_batches_per_planned_turn: int = Field(1, ge=0, le=20)
    director_max_chat_batches_before_anchor: int = Field(2, ge=1, le=10)
    director_offtopic_policy: str = Field("defer", max_length=40)
    director_sc_burst_policy: str = Field("summarize_batch", max_length=40)
    research_enabled: bool = False
    research_cooldown_seconds: int = Field(300, ge=0, le=3600)
    research_max_per_session: int = Field(12, ge=0, le=100)
    auto_sc_thanks_on_finalize: bool = True
    presentation_enabled: bool = False
    tts_enabled: bool = False
    tts_provider: str = Field("gpt_sovits", max_length=40)
    presentation_ack_timeout_seconds: int = Field(120, ge=1, le=600)
    post_plan_free_talk_enabled: bool = False
    post_plan_free_talk_minutes: int = Field(20, ge=0, le=240)
    post_plan_free_talk_tick_interval_seconds: int = Field(30, ge=5, le=600)
    post_plan_free_talk_idle_turns_min: int = Field(6, ge=1, le=12)
    post_plan_free_talk_idle_turns_max: int = Field(6, ge=1, le=12)
    post_plan_free_talk_audience_turns_min: int = Field(3, ge=1, le=12)
    post_plan_free_talk_audience_turns_max: int = Field(3, ge=1, le=12)
    post_plan_free_talk_topic_pack_ids: list[str] = Field(default_factory=list)
    free_talk_closing_target_batches: int = Field(10, ge=1, le=50)
    free_talk_closing_min_batch_size: int = Field(5, ge=1, le=100)
    free_talk_closing_max_batch_size: int = Field(30, ge=1, le=200)
    free_talk_closing_time_limit_seconds: int = Field(300, ge=30, le=3600)

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


class StudioTestSettings(BaseModel):
    auto_comment_enabled: bool = False
    normal_comment_count: int = Field(8, ge=0, le=50)
    super_chat_count: int = Field(2, ge=0, le=10)
    malicious_comment_enabled: bool = False
    comment_frequency_seconds: int = Field(8, ge=1, le=120)
    test_message: str = Field("", max_length=200)
    summary_preview: str = Field("", max_length=2000)


class StudioDisplaySettings(BaseModel):
    show_live_events_enabled: bool = False


class StudioLiveDefaults(BaseModel):
    auto_inject_pending_enabled: bool = True
    inject_interval_seconds: int = Field(30, ge=5, le=600)
    inject_min_interval_seconds: int = Field(10, ge=5, le=600)
    min_pending_comments: int = Field(1, ge=1, le=100)
    pending_force_limit: int = Field(12, ge=1, le=100)
    planned_duration_minutes: int = Field(52, ge=5, le=360)
    auto_finalize_at_limit: bool = True
    thank_unhandled_super_chats: bool = True
    clear_runtime_session_after_summary: bool = True
    post_plan_free_talk_enabled: bool = False
    post_plan_free_talk_minutes: int = Field(20, ge=0, le=240)
    post_plan_free_talk_tick_interval_seconds: int = Field(30, ge=5, le=600)
    post_plan_free_talk_idle_turns_min: int = Field(6, ge=1, le=12)
    post_plan_free_talk_idle_turns_max: int = Field(6, ge=1, le=12)
    post_plan_free_talk_audience_turns_min: int = Field(3, ge=1, le=12)
    post_plan_free_talk_audience_turns_max: int = Field(3, ge=1, le=12)
    post_plan_free_talk_topic_pack_ids: list[str] = Field(default_factory=list)
    free_talk_closing_target_batches: int = Field(10, ge=1, le=50)
    free_talk_closing_min_batch_size: int = Field(5, ge=1, le=100)
    free_talk_closing_max_batch_size: int = Field(30, ge=1, le=200)
    free_talk_closing_time_limit_seconds: int = Field(300, ge=30, le=3600)
    super_chat_cooldown_seconds: int = Field(45, ge=0, le=600)
    super_chat_batch_limit: int = Field(3, ge=1, le=20)
    safe_search_enabled: bool = True
    presentation_queue_enabled: bool = True
    tts_enabled: bool = False


class ReplyRecentRequest(BaseModel):
    content: str = "請根據已提供的 Topic Pack / fact card / YouTube 直播留言上下文回應。不要自行開啟瀏覽器或搜尋網頁。"
    memoria_session_id: str = ""
    character_ids: list[str] = Field(default_factory=list)
    event_ids: list[int] = Field(default_factory=list)
    max_events: int = Field(50, ge=1, le=100)
    priority: int = Field(200, ge=0, le=1000)


class PresentationClientDebugRequest(BaseModel):
    phase: str = Field("client_event", max_length=80)
    item_id: str = Field("", max_length=120)
    status: str = Field("", max_length=80)
    details: dict = Field(default_factory=dict)


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


class FinishMainPhaseRequest(BaseModel):
    reason: str = Field("episode_plan_completed", max_length=120)
    enter_free_talk: bool = True
    force_enter_free_talk: bool = False


class FinalizePhaseRequest(BaseModel):
    reason: str = Field("operator_finalize", max_length=120)


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


class TestChatManualEvent(BaseModel):
    kind: Literal["comment", "super"] = "comment"
    author_display_name: str = Field("觀眾 測試帳號", max_length=80)
    message_text: str = Field("", min_length=1, max_length=500)
    amount_display_string: str = Field("", max_length=40)
    amount_micros: int = Field(0, ge=0)


class TestChatGenerateRequest(BaseModel):
    count: int = Field(5, ge=0, le=30)
    topic_hint: str = Field("", max_length=1200)
    use_llm: bool = True
    super_chat_count: int = Field(0, ge=0, le=30)
    include_malicious_sc: bool = False
    sc_burst: bool = False
    manual_events: list[TestChatManualEvent] = Field(default_factory=list, max_length=30)


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


class StudioSettingsPatch(BaseModel):
    connector: ConnectorConfig | None = None
    memoria_auth: MemoriaAuthConfig | None = None
    test_settings: StudioTestSettings | None = None
    display_settings: StudioDisplaySettings | None = None
    live_defaults: StudioLiveDefaults | None = None


class StudioAvatarUploadRequest(BaseModel):
    filename: str = Field("", min_length=1, max_length=240)
    data_url: str = Field("", min_length=1, max_length=3_000_000)


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
    avatar_url: str = Field("", max_length=1000)
    chat_background_color: str = Field("", max_length=20)
    chat_accent_color: str = Field("", max_length=20)

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        value = str(value or "replace").strip()
        if value not in {"replace", "append"}:
            raise ValueError("mode 必須是 replace 或 append")
        return value

    @field_validator("avatar_url")
    @classmethod
    def normalize_avatar_url(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("chat_background_color", "chat_accent_color")
    @classmethod
    def validate_chat_color(cls, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            return ""
        if len(value) != 7 or not value.startswith("#"):
            raise ValueError("角色色盤必須是 #RRGGBB 格式")
        try:
            int(value[1:], 16)
        except ValueError as exc:
            raise ValueError("角色色盤必須是 #RRGGBB 格式") from exc
        return value.lower()


class LiveTTSProfileRequest(BaseModel):
    enabled: bool = False
    ref_audio_path: str = Field("", max_length=1000)
    prompt_text: str = Field("", max_length=2000)
    text_lang: str = Field("zh", max_length=20)
    prompt_lang: str = Field("zh", max_length=20)
    speed_factor: float = Field(1.0, ge=0.25, le=4.0)
    media_type: str = Field("wav", max_length=20)

    @field_validator("text_lang", "prompt_lang", "media_type")
    @classmethod
    def normalize_short_code(cls, value: str) -> str:
        return str(value or "").strip().lower()

    @field_validator("prompt_text")
    @classmethod
    def validate_prompt_text(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("ref_audio_path")
    @classmethod
    def validate_ref_audio_path(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("media_type")
    @classmethod
    def validate_media_type(cls, value: str) -> str:
        value = str(value or "wav").strip().lower()
        if value not in {"wav", "mp3"}:
            raise ValueError("media_type 必須是 wav 或 mp3")
        return value

    @field_validator("enabled")
    @classmethod
    def validate_enabled(cls, value: bool) -> bool:
        return bool(value)

    def model_post_init(self, __context) -> None:
        if self.enabled and not self.ref_audio_path:
            raise ValueError("啟用 TTS 時必須填入範例語音路徑")
        if self.enabled and not self.prompt_text:
            raise ValueError("啟用 TTS 時必須填入範例語音 transcript")
