"""
FastAPI 應用程式組裝 — 唯一的 API 閘道入口。
啟動: uvicorn api.main:app --host 0.0.0.0 --port 8000
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.dependencies import init_all, get_storage
from api.session_manager import session_manager
from api.telegram_bot import start_telegram_bot, stop_telegram_bot
from api.routers import health, memory, profile, system, session, logs, chat_ws


# ── Lifespan：啟動 / 關機 ────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_all()

    # 背景 Session 過期清理 (每 5 分鐘)
    async def _session_cleanup_loop():
        while True:
            await asyncio.sleep(300)
            await session_manager.expire_stale()

    cleanup_task = asyncio.create_task(_session_cleanup_loop())

    # Telegram Bot（可選：有 token 才啟動）
    tg_token = get_storage().load_prefs().get("telegram_bot_token", "")
    if tg_token:
        await start_telegram_bot(tg_token)

    yield

    # Shutdown
    await stop_telegram_bot()
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass


# ── 應用程式實例 ──────────────────────────────────────────
app = FastAPI(
    title="LLM Memory System API",
    description="情境記憶 LLM 系統的微服務後端",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS（Unity 桌面端需要 allow_origins=["*"]） ─────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 掛載路由 ──────────────────────────────────────────────
PREFIX = "/api/v1"
app.include_router(health.router, prefix=PREFIX)
app.include_router(memory.router, prefix=PREFIX)
app.include_router(profile.router, prefix=PREFIX)
app.include_router(system.router, prefix=PREFIX)
app.include_router(session.router, prefix=PREFIX)
app.include_router(logs.router, prefix=PREFIX)
app.include_router(chat_ws.router, prefix=PREFIX)


# ── 全域例外處理 ──────────────────────────────────────────
@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"error": {"code": "BAD_REQUEST", "message": str(exc)}})


@app.exception_handler(FileNotFoundError)
async def not_found_handler(request: Request, exc: FileNotFoundError):
    return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": str(exc)}})


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL", "message": str(exc)}})
