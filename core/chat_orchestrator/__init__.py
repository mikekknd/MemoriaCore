"""異步雙層 Agent 語音互動架構。

對話編排拆分為三個獨立模組 + 一個協調函式：
- dataclasses.py    — RouterResult, ToolContext, PersonaResult
- router_agent.py   — Module A：意圖路由層（判斷是否需要工具 + 過渡語音）
- middleware.py     — Module B：非同步中介層（並行執行工具）
- persona_agent.py  — Module C：角色渲染層（生成結構化 JSON 回覆）
- coordinator.py    — 頂層協調函式 run_dual_layer_orchestration

對外公開：
    from core.chat_orchestrator import run_dual_layer_orchestration
"""
from core.chat_orchestrator.coordinator import run_dual_layer_orchestration, _generate_tts_speech
from core.chat_orchestrator.dataclasses import RouterResult, ToolContext, PersonaResult
from core.chat_orchestrator.router_agent import run_router_agent, DIRECT_CHAT_SCHEMA
from core.chat_orchestrator.middleware import run_middleware
from core.chat_orchestrator.persona_agent import run_persona_agent

__all__ = [
    "run_dual_layer_orchestration",
    "_generate_tts_speech",
    "RouterResult", "ToolContext", "PersonaResult",
    "run_router_agent", "DIRECT_CHAT_SCHEMA",
    "run_middleware",
    "run_persona_agent",
]
