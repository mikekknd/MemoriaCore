"""對話編排子模組。

子模組劃分：
- timer.py        — StepTimer 效能計時器
- ws_manager.py   — WebSocket 連線管理器
- pipeline.py     — 記憶管線（話題偏移時的背景處理）
- orchestration.py — 單層對話編排（_run_chat_orchestration）
                     雙層編排見 core/chat_orchestrator/

對應 router 檔：
- ../chat_ws.py    — WebSocket /chat/stream 端點
- ../chat_rest.py  — REST /chat/sync 與 SSE /chat/stream-sync 端點
"""
