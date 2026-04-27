"""三模組（Router / Middleware / Persona）共用的資料結構，以及記憶管線上下文。"""
from dataclasses import dataclass, field


# ════════════════════════════════════════════════════════════
# SECTION: Module A 輸出
# ════════════════════════════════════════════════════════════

@dataclass
class RouterResult:
    """Module A 的輸出。"""
    needs_tools: bool
    tool_calls: list[dict] = field(default_factory=list)
    thinking_speech: str = ""


# ════════════════════════════════════════════════════════════
# SECTION: Module B 輸出
# ════════════════════════════════════════════════════════════

@dataclass
class ToolContext:
    """Module B 的輸出。"""
    tool_results: list[dict] = field(default_factory=list)   # [{"tool_name": str, "result": str}]
    tool_results_formatted: str = ""                          # 格式化文字，注入 Module C
    thinking_speech_sent: str = ""                            # 已推播給前端的過渡語


# ════════════════════════════════════════════════════════════
# SECTION: Module C 輸出
# ════════════════════════════════════════════════════════════

@dataclass
class PersonaResult:
    """Module C 的輸出。"""
    reply_text: str = ""
    new_entities: list[str] = field(default_factory=list)
    inner_thought: str | None = None
    speech: str | None = None
    # status_metrics / tone 已從 LLM schema 移除，保留欄位供向後相容（值永遠為 None）
    status_metrics: dict | None = None
    tone: str | None = None


# ════════════════════════════════════════════════════════════
# SECTION: 記憶管線上下文
# ════════════════════════════════════════════════════════════

@dataclass
class PipelineContext:
    """記憶管線執行所需的完整上下文，取代先前的 (msgs_to_extract, last_block) 2-tuple。

    session_ctx 包含：{"user_id": str, "character_id": str, "persona_face": str}
    """
    msgs_to_extract: list[dict]
    last_block: dict | None
    session_ctx: dict = field(default_factory=dict)
