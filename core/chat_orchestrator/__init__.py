"""異步雙層 Agent 語音互動架構。

對話編排拆分為三個獨立模組 + 一個協調函式：
- dataclasses.py    — RouterResult, ToolContext, PersonaResult
- router_agent.py   — Module A：意圖路由層（判斷是否需要工具 + 過渡語音）
- middleware.py     — Module B：非同步中介層（並行執行工具）
- persona_agent.py  — Module C：角色渲染層（生成結構化 JSON 回覆）
- coordinator.py    — 頂層協調函式 run_dual_layer_orchestration

直接 import 範例：
    from core.chat_orchestrator.coordinator import run_dual_layer_orchestration
    from core.chat_orchestrator.dataclasses import RouterResult, ToolContext, PersonaResult
"""