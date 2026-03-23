"""Session CRUD 端點 + 對話歷史查詢"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from api.session_manager import session_manager, SessionState
from api.models.requests import CreateSessionRequest
from api.models.responses import (
    SessionDTO, SessionMessageDTO,
    ConversationSessionDTO, ConversationHistoryDTO,
)
from api.dependencies import get_storage

router = APIRouter(prefix="/session", tags=["session"])


def _state_to_dto(s: SessionState) -> SessionDTO:
    return SessionDTO(
        session_id=s.session_id,
        messages=[SessionMessageDTO(role=m["role"], content=m["content"],
                                     debug_info=m.get("debug_info"))
                  for m in s.messages],
        last_entities=s.last_entities,
        created_at=s.created_at.isoformat(),
        last_active=s.last_active.isoformat(),
    )


@router.post("", response_model=SessionDTO)
async def create_session(body: CreateSessionRequest = CreateSessionRequest()):
    s = await session_manager.create(channel=body.channel, channel_uid=body.channel_uid)
    return _state_to_dto(s)


@router.get("/history", response_model=list[ConversationSessionDTO])
async def list_conversation_history(
    channel: Optional[str] = Query(None, description="篩選 channel：streamlit / telegram / websocket / rest"),
    limit: int = Query(50, ge=1, le=200),
):
    """列出所有歷史 sessions（含 channel 標記與訊息數）"""
    storage = get_storage()
    sessions = storage.load_conversation_sessions(channel=channel, limit=limit)
    return [ConversationSessionDTO(**s) for s in sessions]


@router.get("/history/{session_id}", response_model=ConversationHistoryDTO)
async def get_conversation_history(session_id: str):
    """從 DB 載入指定 session 的完整訊息（含已結束的 session）"""
    storage = get_storage()
    # 先查 session 元資料
    all_sessions = storage.load_conversation_sessions(limit=500)
    session_info = next((s for s in all_sessions if s["session_id"] == session_id), None)
    if not session_info:
        raise HTTPException(404, detail=f"Session {session_id} not found in history")

    messages = storage.load_conversation_messages(session_id)
    return ConversationHistoryDTO(
        session=ConversationSessionDTO(**session_info),
        messages=[SessionMessageDTO(role=m["role"], content=m["content"],
                                     debug_info=m.get("debug_info"))
                  for m in messages],
    )


@router.get("/{session_id}", response_model=SessionDTO)
async def get_session(session_id: str):
    s = await session_manager.get(session_id)
    if not s:
        raise HTTPException(404, detail=f"Session {session_id} not found")
    return _state_to_dto(s)


@router.delete("/{session_id}")
async def delete_session(session_id: str):
    ok = await session_manager.delete(session_id)
    if not ok:
        raise HTTPException(404, detail=f"Session {session_id} not found")
    return {"status": "deleted", "session_id": session_id}


@router.post("/{session_id}/bridge")
async def bridge_session(session_id: str):
    ok = await session_manager.bridge(session_id)
    if not ok:
        raise HTTPException(404, detail=f"Session {session_id} not found")
    return {"status": "bridged", "session_id": session_id}
