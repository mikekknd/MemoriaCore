"""WebSocket 連線與背景任務的管理器。

提供全域 singleton `ws_manager`，供 WS 端點與背景管線共同使用。
"""
import asyncio
from fastapi import WebSocket


# ════════════════════════════════════════════════════════════
# SECTION: ConnectionManager
# ════════════════════════════════════════════════════════════

class ConnectionManager:
    """管理活躍 WebSocket 連線與每個 session 的背景任務。"""

    def __init__(self):
        self._connections: dict[str, WebSocket] = {}      # session_id -> ws
        self._active_tasks: dict[str, asyncio.Task] = {}  # session_id -> running task

    async def connect(self, session_id: str, ws: WebSocket):
        await ws.accept()
        self._connections[session_id] = ws

    def disconnect(self, session_id: str):
        self._connections.pop(session_id, None)
        self._active_tasks.pop(session_id, None)

    async def send_json(self, session_id: str, data: dict):
        ws = self._connections.get(session_id)
        if ws:
            try:
                await ws.send_json(data)
            except Exception:
                self.disconnect(session_id)

    def get_ws(self, session_id: str) -> WebSocket | None:
        return self._connections.get(session_id)

    def set_active_task(self, session_id: str, task: asyncio.Task):
        self._active_tasks[session_id] = task

    async def cancel_active_task(self, session_id: str):
        task = self._active_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    def clear_active_task(self, session_id: str):
        self._active_tasks.pop(session_id, None)


# ════════════════════════════════════════════════════════════
# SECTION: 全域 singleton
# ════════════════════════════════════════════════════════════

ws_manager = ConnectionManager()
