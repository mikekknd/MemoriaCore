"""
Session Manager — 記憶體內對話狀態存儲 + SQLite 持久化。
所有 create / add_message / expire 操作同步寫入 conversation.db，
支援跨介面對話紀錄查詢與伺服器重啟後歷史保留。
"""
import uuid
import asyncio
from datetime import datetime, timedelta
from dataclasses import dataclass, field


@dataclass
class SessionState:
    session_id: str
    messages: list[dict] = field(default_factory=list)
    last_entities: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_active: datetime = field(default_factory=datetime.now)
    channel: str = "rest"
    channel_uid: str = ""


class SessionManager:
    def __init__(self, ttl_minutes: int = 60, storage=None):
        self._sessions: dict[str, SessionState] = {}
        self._lock = asyncio.Lock()
        self._ttl = timedelta(minutes=ttl_minutes)
        self._storage = storage   # StorageManager instance (optional)

    def set_storage(self, storage):
        """延遲注入 storage（解決循環依賴時使用）"""
        self._storage = storage

    async def create(self, channel: str = "rest", channel_uid: str = "") -> SessionState:
        async with self._lock:
            sid = str(uuid.uuid4())
            session = SessionState(session_id=sid, channel=channel, channel_uid=channel_uid)
            self._sessions[sid] = session
            # 持久化
            if self._storage:
                self._storage.create_conversation_session(sid, channel, channel_uid)
            return session

    async def get(self, session_id: str) -> SessionState | None:
        async with self._lock:
            s = self._sessions.get(session_id)
            if s:
                s.last_active = datetime.now()
            return s

    async def get_or_create(self, session_id: str | None,
                            channel: str = "rest", channel_uid: str = "") -> SessionState:
        if session_id:
            s = await self.get(session_id)
            if s:
                return s
        return await self.create(channel=channel, channel_uid=channel_uid)

    async def restore_from_db(self, session_id: str) -> SessionState | None:
        """從 DB 還原已過期的 session 到記憶體。
        使用 bridge_after_msg_id 避免載入已被記憶管線處理過的舊訊息。
        """
        if not self._storage:
            return None
        info = self._storage.get_session_info(session_id)
        if not info:
            return None
        async with self._lock:
            # 已在記憶體中則直接返回
            if session_id in self._sessions:
                return self._sessions[session_id]
            # 取得 bridge 截斷點，只載入截斷點之後的訊息
            bridge_point = self._storage.get_bridge_point(session_id)
            messages = self._storage.load_conversation_messages(session_id, since_msg_id=bridge_point)
            session = SessionState(
                session_id=session_id,
                messages=messages,
                channel=info.get("channel", "rest"),
                channel_uid=info.get("channel_uid", ""),
                created_at=datetime.fromisoformat(info["created_at"]) if info.get("created_at") else datetime.now(),
                last_active=datetime.now(),
            )
            self._sessions[session_id] = session
            # 重新標記為活躍
            self._storage.reactivate_session(session_id)
            return session

    async def delete(self, session_id: str) -> bool:
        async with self._lock:
            removed = self._sessions.pop(session_id, None) is not None
            if self._storage:
                self._storage.deactivate_session(session_id)
            return removed

    async def bridge(self, session_id: str, keep_last_n: int = 6) -> bool:
        """橋接邏輯：話題偏移後保留最近 N 條訊息（預設 6 條 = 3 輪），並記錄截斷點到 DB。"""
        async with self._lock:
            s = self._sessions.get(session_id)
            if not s:
                return False
            bridged = s.messages[-keep_last_n:] if len(s.messages) > keep_last_n else list(s.messages)
            s.messages = bridged
            s.last_active = datetime.now()
            # 持久化截斷點，restore 時只載入這之後的訊息
            if self._storage:
                self._storage.update_bridge_point(session_id, keep_last_n=len(bridged))
            return True

    async def add_user_message(self, session_id: str, content: str) -> bool:
        async with self._lock:
            s = self._sessions.get(session_id)
            if not s:
                return False
            s.messages.append({"role": "user", "content": content})
            s.last_active = datetime.now()
            # 持久化
            if self._storage:
                self._storage.save_conversation_message(session_id, "user", content)
            return True

    async def add_assistant_message(self, session_id: str, content: str,
                                     debug_info: dict | None = None,
                                     extracted_entities: list[str] | None = None,
                                     persona_state: dict | None = None) -> bool:
        """
        persona_state: {"internal_thought": str, "status_metrics": dict, "tone": str}
        僅存於記憶體，不持久化到 DB（伺服器重啟後情緒軌跡自然重置）。
        """
        async with self._lock:
            s = self._sessions.get(session_id)
            if not s:
                return False
            msg = {"role": "assistant", "content": content}
            if debug_info:
                msg["debug_info"] = debug_info
            if persona_state:
                msg["persona_state"] = persona_state
            s.messages.append(msg)
            if extracted_entities is not None:
                s.last_entities = extracted_entities
            s.last_active = datetime.now()
            # 持久化
            if self._storage:
                self._storage.save_conversation_message(session_id, "assistant", content, debug_info)
            return True

    async def get_pipeline_context(self, session_id: str) -> list[dict]:
        """回傳排除最新 User 訊息的歷史（供記憶管線用）"""
        async with self._lock:
            s = self._sessions.get(session_id)
            if not s:
                return []
            return [{"role": m["role"], "content": m["content"]} for m in s.messages[:-1]]

    async def expire_stale(self):
        """清理過期 session"""
        now = datetime.now()
        async with self._lock:
            expired = [sid for sid, s in self._sessions.items()
                       if (now - s.last_active) > self._ttl]
            for sid in expired:
                del self._sessions[sid]
                # 持久化：標記為非活躍（不刪除紀錄）
                if self._storage:
                    self._storage.deactivate_session(sid)
            return expired

    def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())


# ── 全域單例 ──────────────────────────────────────────────
session_manager = SessionManager()
