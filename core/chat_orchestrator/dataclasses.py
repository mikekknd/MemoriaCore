"""三模組（Router / Middleware / Persona）共用的資料結構。"""
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
