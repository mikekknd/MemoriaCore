"""Session CRUD 端點 + 對話歷史查詢"""
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional
from api.session_manager import session_manager, SessionState
from api.models.requests import CreateSessionRequest
from api.models.responses import (
    SessionDTO, SessionMessageDTO,
    ConversationSessionDTO, ConversationHistoryDTO,
)
from api.dependencies import get_current_user, get_storage, require_admin_user

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
async def create_session(
    body: CreateSessionRequest = CreateSessionRequest(),
    current_user: dict = Depends(get_current_user),
):
    channel_class = "private" if current_user.get("role") == "admin" else "public"
    persona_face = "private" if current_user.get("role") == "admin" else "public"
    prefs = get_storage().load_prefs()
    s = await session_manager.create(
        channel=body.channel,
        channel_uid=body.channel_uid or str(current_user["id"]),
        user_id=str(current_user["id"]),
        character_id=prefs.get("active_character_id", "default"),
        channel_class=channel_class,
        persona_face=persona_face,
    )
    return _state_to_dto(s)


@router.get("/history", response_model=list[ConversationSessionDTO])
async def list_conversation_history(
    channel: Optional[str] = Query(None, description="篩選 channel：streamlit / telegram / websocket / rest"),
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
):
    """列出所有歷史 sessions（含 channel 標記與訊息數）"""
    storage = get_storage()
    sessions = storage.load_conversation_sessions(channel=channel, limit=limit, user_id=str(current_user["id"]))
    return [ConversationSessionDTO(**s) for s in sessions]


@router.get("/history/{session_id}", response_model=ConversationHistoryDTO)
async def get_conversation_history(session_id: str, current_user: dict = Depends(get_current_user)):
    """從 DB 載入指定 session 的完整訊息（含已結束的 session）"""
    storage = get_storage()
    session_info = storage.get_session_info(session_id)
    if not session_info:
        raise HTTPException(404, detail=f"Session {session_id} not found in history")
    if session_info.get("user_id") != str(current_user["id"]):
        raise HTTPException(403, detail="Session owner mismatch")

    messages = storage.load_conversation_messages(session_id)
    return ConversationHistoryDTO(
        session=ConversationSessionDTO(**session_info),
        messages=[SessionMessageDTO(role=m["role"], content=m["content"],
                                     debug_info=m.get("debug_info"))
                  for m in messages],
    )


@router.get("/{session_id}", response_model=SessionDTO)
async def get_session(session_id: str, current_user: dict = Depends(get_current_user)):
    s = await session_manager.get(session_id)
    if not s:
        raise HTTPException(404, detail=f"Session {session_id} not found")
    if s.user_id != str(current_user["id"]):
        raise HTTPException(403, detail="Session owner mismatch")
    return _state_to_dto(s)


@router.delete("/{session_id}")
async def delete_session(session_id: str, current_user: dict = Depends(get_current_user)):
    s = await session_manager.get(session_id)
    if s and s.user_id != str(current_user["id"]):
        raise HTTPException(403, detail="Session owner mismatch")
    ok = await session_manager.delete(session_id)
    if not ok:
        raise HTTPException(404, detail=f"Session {session_id} not found")
    return {"status": "deleted", "session_id": session_id}


@router.post("/{session_id}/bridge")
async def bridge_session(session_id: str, current_user: dict = Depends(get_current_user)):
    s = await session_manager.get(session_id)
    if s and s.user_id != str(current_user["id"]):
        raise HTTPException(403, detail="Session owner mismatch")
    ok = await session_manager.bridge(session_id)
    if not ok:
        raise HTTPException(404, detail=f"Session {session_id} not found")
    return {"status": "bridged", "session_id": session_id}


@router.post("/{session_id}/restore", response_model=SessionDTO)
async def restore_session(session_id: str, current_user: dict = Depends(get_current_user)):
    """從 DB 還原已過期的 session 到記憶體"""
    try:
        s = await session_manager.restore_from_db(session_id, user_id=str(current_user["id"]))
    except PermissionError:
        raise HTTPException(403, detail="Session owner mismatch")
    if not s:
        raise HTTPException(404, detail=f"Session {session_id} not found in DB")
    return _state_to_dto(s)


@router.delete("/history/cleanup/{days}")
async def cleanup_old_sessions(days: int, current_user: dict = Depends(require_admin_user)):
    """永久刪除 N 天前的所有 session 及訊息"""
    if days < 1:
        raise HTTPException(400, detail="days 必須 >= 1")
    storage = get_storage()
    count = storage.hard_delete_sessions_older_than(days)
    return {"status": "cleanup_done", "deleted_count": count, "older_than_days": days}


@router.delete("/history/{session_id}")
async def hard_delete_session(session_id: str, current_user: dict = Depends(get_current_user)):
    """永久刪除指定 session 及其所有訊息（不可恢復）"""
    storage = get_storage()
    info = storage.get_session_info(session_id)
    if not info:
        raise HTTPException(404, detail=f"Session {session_id} not found")
    if info.get("user_id") != str(current_user["id"]) and current_user.get("role") != "admin":
        raise HTTPException(403, detail="Session owner mismatch")
    # 也從記憶體移除（如果還在的話）
    await session_manager.delete(session_id)
    storage.hard_delete_session(session_id)
    return {"status": "permanently_deleted", "session_id": session_id}
