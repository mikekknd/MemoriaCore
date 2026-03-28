"""
FastAPI 應用程式組裝 — 唯一的 API 閘道入口。
啟動: uvicorn api.main:app --host 0.0.0.0 --port 8088
"""
import asyncio
from contextlib import asynccontextmanager

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from api.dependencies import init_all, get_storage, get_router, get_memory_sys
from api.session_manager import session_manager
from api.telegram_bot import start_telegram_bot, stop_telegram_bot
from core.background_gatherer import start_background_gather_loop
from api.routers import health, memory, profile, system, session, logs, chat_ws, character, prompts


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
    user_prefs = get_storage().load_prefs()
    tg_token = user_prefs.get("telegram_bot_token", "")
    if tg_token:
        await start_telegram_bot(tg_token)

    # 天氣快取預熱（有設定城市 + API key 才執行）
    weather_city = user_prefs.get("weather_city", "")
    ow_key = user_prefs.get("openweather_api_key", "")
    if weather_city and ow_key:
        from tools.weather_cache import WeatherCache
        wc = WeatherCache()
        await asyncio.to_thread(wc.ensure_today, weather_city, ow_key)

    # 背景話題搜集 (每 4 小時一次)
    bg_gather_task = None
    if user_prefs.get("tavily_api_key"):
        db_path = get_memory_sys().db_path
        if db_path:
            # interval 暫定 14400 秒 (4小時)，也可後續拉出到設定檔
            bg_gather_task = asyncio.create_task(
                start_background_gather_loop(db_path, get_router(), get_storage(), default_interval_seconds=14400)
            )

    yield

    # Shutdown
    await stop_telegram_bot()
    cleanup_task.cancel()
    if bg_gather_task:
        bg_gather_task.cancel()
        
    try:
        await cleanup_task
        if bg_gather_task:
            await bg_gather_task
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
app.include_router(character.router, prefix=PREFIX)
app.include_router(prompts.router, prefix=PREFIX)


# ── 根路由 → Dashboard ───────────────────────────────────
@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/static/dashboard.html")


# ── 靜態檔案服務 ──────────────────────────────────────────
_static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
app.mount("/static", StaticFiles(directory=_static_dir, html=True), name="static")


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
