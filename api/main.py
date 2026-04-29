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

from api.dependencies import (
    init_all, get_storage, get_router, get_memory_sys,
    get_persona_sync_manager, get_telegram_bot_manager, get_discord_bot_manager,
)
from api.session_manager import session_manager
from core.background_gatherer import start_background_gather_loop
from api.middleware.auth import AuthMiddleware
from api.routers import auth, health, memory, profile, system, session, logs, chat_ws, chat_rest, character, prompts, persona_evolution, personality_public, admin_users, bots


def _persona_sync_candidate_character_ids(storage) -> list[str]:
    """取得自動 PersonaSync 候選角色。

    候選清單由 conversation DB 推導：只有曾有 assistant 發言的角色才需要檢查。
    不以 active/default character 補位，避免同步目標被全域預設角色污染。
    """
    if hasattr(storage, "list_conversation_character_ids"):
        return storage.list_conversation_character_ids()
    return storage.list_recent_conversation_character_ids(limit=50)


def _should_log_persona_sync_skip(reason: str) -> bool:
    """判斷 PersonaSync skip 是否需要寫系統 log。"""
    quiet_prefixes = (
        "insufficient_messages(",
    )
    return not any(reason.startswith(prefix) for prefix in quiet_prefixes)


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

    # 外部平台 Bots（可選：讀取 bot_configs.json；舊 telegram_bot_token 只作首次遷移）
    user_prefs = get_storage().load_prefs()
    await get_telegram_bot_manager().sync_from_registry()
    await get_discord_bot_manager().sync_from_registry()

    # SU 天氣快取預熱（有 SU + 設定城市 + API key 才執行）
    weather_city = user_prefs.get("weather_city", "")
    ow_key = user_prefs.get("openweather_api_key", "")
    from core.deployment_config import get_su_user_id
    su_user_id = user_prefs.get("su_user_id") or get_su_user_id()
    if su_user_id and weather_city and ow_key:
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

    # PersonaSync 批次反思（每 20 分鐘檢查一次觸發條件，逐角色 + 雙 face 分開執行）
    async def _persona_sync_loop():
        while True:
            await asyncio.sleep(1200)  # 20 分鐘
            try:
                psm = get_persona_sync_manager()
                sto = get_storage()
                prefs = sto.load_prefs()
                from core.system_logger import SystemLogger
                character_ids = _persona_sync_candidate_character_ids(sto)
                for character_id in character_ids:
                    for face in ("public", "private"):
                        should, reason = await psm.should_run(
                            sto, prefs, persona_face=face, character_id=character_id,
                        )
                        if should:
                            await psm.run_sync(sto, prefs, persona_face=face, character_id=character_id)
                        elif _should_log_persona_sync_skip(reason):
                            SystemLogger.log_system_event(
                                "persona_sync_skip",
                                {"reason": reason, "character_id": character_id, "persona_face": face},
                            )
            except Exception as e:
                from core.system_logger import SystemLogger
                SystemLogger.log_error("persona_sync_loop", str(e))

    persona_sync_task = asyncio.create_task(_persona_sync_loop())

    yield

    # Shutdown
    await get_telegram_bot_manager().stop_all()
    await get_discord_bot_manager().stop_all()
    cleanup_task.cancel()
    if bg_gather_task:
        bg_gather_task.cancel()
    persona_sync_task.cancel()

    try:
        await cleanup_task
        if bg_gather_task:
            await bg_gather_task
        await persona_sync_task
    except asyncio.CancelledError:
        pass


# ── 應用程式實例 ──────────────────────────────────────────
app = FastAPI(
    title="LLM Memory System API",
    description="情境記憶 LLM 系統的微服務後端",
    version="1.0.0",
    lifespan=lifespan,
)

def _cors_origins() -> list[str]:
    raw = os.getenv("MEMORIACORE_CORS_ORIGINS", "")
    if raw.strip():
        return [x.strip() for x in raw.split(",") if x.strip()]
    return [
        "http://localhost:8088",
        "http://127.0.0.1:8088",
        "http://localhost:8501",
        "http://127.0.0.1:8501",
        "http://localhost:8502",
        "http://127.0.0.1:8502",
    ]


# AuthMiddleware 先註冊，讓後註冊的 CORS 成為外層 middleware，確保 401/403 也帶 CORS header。
app.add_middleware(AuthMiddleware)

# ── CORS（公開部署禁止使用萬用字元；需要額外 origin 請設 MEMORIACORE_CORS_ORIGINS）──
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 掛載路由 ──────────────────────────────────────────────
PREFIX = "/api/v1"
app.include_router(auth.router, prefix=PREFIX)
app.include_router(health.router, prefix=PREFIX)
app.include_router(memory.router, prefix=PREFIX)
app.include_router(profile.router, prefix=PREFIX)
app.include_router(system.router, prefix=PREFIX)
app.include_router(session.router, prefix=PREFIX)
app.include_router(logs.router, prefix=PREFIX)
app.include_router(chat_ws.router, prefix=PREFIX)
app.include_router(chat_rest.router, prefix=PREFIX)
app.include_router(character.router, prefix=PREFIX)
app.include_router(bots.router, prefix=PREFIX)
app.include_router(prompts.router, prefix=PREFIX)
app.include_router(persona_evolution.router, prefix=PREFIX)
app.include_router(personality_public.router, prefix=PREFIX)
app.include_router(admin_users.router, prefix=PREFIX)


# ── 根路由 → 一般入口 ────────────────────────────────────
@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/static/app.html")


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
