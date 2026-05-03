"""YouTubeBridge FastAPI server。

啟動：
    python server.py
    uvicorn server:app --host 127.0.0.1 --port 8091
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from bridge_engine import YouTubeBridgeManager
from memoria_client import MemoriaClient
from models import CleanupRequest, ConnectorConfig, LiveSessionConfig, ReplyRecentRequest
from storage import BridgeStorage
from youtube_client import extract_video_id


storage = BridgeStorage()
manager = YouTubeBridgeManager(storage)


def _is_loopback_request(request: Request) -> bool:
    host = request.client.host if request.client else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost"}


def require_bridge_key(request: Request) -> None:
    expected = os.getenv("YOUTUBE_BRIDGE_API_KEY", "").strip()
    if expected:
        if request.headers.get("X-Bridge-Key") != expected:
            raise HTTPException(status_code=403, detail="invalid bridge key")
        return
    if not _is_loopback_request(request):
        raise HTTPException(status_code=403, detail="invalid bridge key")


def _public_connector(connector: dict | None) -> dict | None:
    if not connector:
        return None
    return {
        **connector,
        "api_key": "",
        "api_key_configured": bool(connector.get("api_key")),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    await manager.sync_autostart()
    yield
    await manager.stop_all()


app = FastAPI(
    title="YouTubeBridge API",
    description="YouTube Live Chat bridge for MemoriaCore",
    version="0.1.0",
    lifespan=lifespan,
    dependencies=[Depends(require_bridge_key)],
)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/connectors")
async def list_connectors():
    return [_public_connector(connector) for connector in storage.list_connectors()]


@app.post("/connectors")
async def upsert_connector(body: ConnectorConfig):
    return _public_connector(storage.upsert_connector(body.model_dump()))


@app.get("/connectors/{connector_id}")
async def get_connector(connector_id: str):
    connector = storage.get_connector(connector_id)
    if not connector:
        raise HTTPException(status_code=404, detail="connector not found")
    return _public_connector(connector)


@app.delete("/connectors/{connector_id}")
async def delete_connector(connector_id: str):
    deleted = storage.delete_connector(connector_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="connector not found")
    return {"deleted": True}


@app.get("/sessions")
async def list_sessions():
    sessions = storage.list_sessions()
    return [{**session, "runtime_status": manager.get_status(session["session_id"])} for session in sessions]


@app.post("/sessions")
async def upsert_session(body: LiveSessionConfig):
    config = body.model_dump()
    config["video_id"] = extract_video_id(config.get("video_id", ""))
    if not config["video_id"] and not config["live_chat_id"]:
        raise HTTPException(status_code=400, detail="video_id 或 live_chat_id 至少需要一個")
    return storage.upsert_session(config)


@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    session = storage.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return {**session, "runtime_status": manager.get_status(session_id)}


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    await manager.stop_session(session_id)
    deleted = storage.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="session not found")
    return {"deleted": True}


@app.post("/sessions/{session_id}/start")
async def start_session(session_id: str):
    try:
        return await manager.start_session(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/sessions/{session_id}/stop")
async def stop_session(session_id: str):
    return await manager.stop_session(session_id)


@app.get("/sessions/{session_id}/recent")
async def recent_events(
    session_id: str,
    limit: int = 100,
    after_id: int | None = None,
    uninjected_only: bool = False,
):
    if not storage.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return {
        "session_id": session_id,
        "events": storage.list_events(
            session_id,
            limit=limit,
            after_id=after_id,
            uninjected_only=uninjected_only,
        ),
    }


@app.get("/sessions/{session_id}/events")
async def events_stream(session_id: str):
    if not storage.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    queue = await manager.subscribe(session_id)

    async def gen():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=20)
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            await manager.unsubscribe(session_id, queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/sessions/{session_id}/reply-recent")
async def reply_recent(session_id: str, body: ReplyRecentRequest):
    session = storage.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        return await manager.inject_recent(
            session_id=session_id,
            event_ids=body.event_ids,
            max_events=body.max_events,
            content=body.content,
            memoria_session_id=body.memoria_session_id or session.get("target_memoria_session_id", ""),
            character_ids=body.character_ids or session.get("character_ids", []),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/sessions/{session_id}/cleanup")
async def cleanup_session_events(session_id: str, body: CleanupRequest):
    session = storage.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    days = body.retention_days or session.get("retention_days", 30)
    return {"deleted": storage.cleanup_events(session_id=session_id, retention_days=days)}


@app.get("/memoria/characters")
async def memoria_characters():
    try:
        return await asyncio.to_thread(MemoriaClient().list_characters)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/memoria/sessions")
async def memoria_sessions(limit: int = 100):
    try:
        return await asyncio.to_thread(MemoriaClient().list_sessions, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.exception_handler(ValueError)
async def value_error_handler(_request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8091)
