"""
依賴注入層 — FastAPI 的 Single Source of Truth。
複製 app.py 的初始化邏輯，但脫離 Streamlit 生命週期。
所有 ONNX / SQLite / LLMRouter 的唯一實例都在此管理。
"""
import asyncio
import sys
import os
import threading
from fastapi import HTTPException, Request

# 確保專案根目錄在 Python path 上（api/ 是子目錄）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.storage_manager import StorageManager
from core.runtime_paths import migrate_legacy_runtime_data
from core.llm_gateway import OllamaProvider, OpenAICompatibleProvider, LlamaCppProvider, LLMRouter
from core.core_memory import MemorySystem
from core.memory_analyzer import MemoryAnalyzer
from core.character_engine import CharacterManager
from core.bot_registry import BotRegistry
from core.persona_sync import PersonaSyncManager
from core.persona_evolution.snapshot_store import PersonaSnapshotStore
from core.persona_evolution.initial_seed import ensure_initial_persona_snapshots
from core.tts_client import MinimaxTTSClient
from api.telegram_bot import TelegramBotManager
from api.discord_bot import DiscordBotManager

# ── Module-level singletons ──────────────────────────────
memory_sys: MemorySystem | None = None
storage: StorageManager | None = None
analyzer: MemoryAnalyzer | None = None
global_router: LLMRouter | None = None
character_mgr: CharacterManager | None = None
bot_registry: BotRegistry | None = None
telegram_bot_mgr: TelegramBotManager | None = None
discord_bot_mgr: DiscordBotManager | None = None
persona_sync_mgr: PersonaSyncManager | None = None
persona_snapshot_store: PersonaSnapshotStore | None = None
tts_client: MinimaxTTSClient | None = None
embed_model: str = ""

# SQLite 寫入序列化鎖
db_write_lock = asyncio.Lock()

# DB 維護模式：開啟時，對話與背景寫入入口應拒絕新寫入，方便本機 DB editor 進行維護。
_db_maintenance_lock = threading.Lock()
_db_maintenance_enabled = False

# 啟動時間記錄（供 /health 使用）
_startup_time: float = 0.0


# 全部 LLM 任務 key 的單一來源（init_all + reload_router 共用）
ALL_TASK_KEYS = (
    "chat", "pipeline", "expand", "compress", "distill",
    "ep_fuse", "profile", "router", "group_router", "translate",
    "browser", "character_gen", "persona_sync", "persona_seed",
)
# 沒有獨立設定時，預設跟隨 chat 的 provider/model
TASKS_FALLBACK_TO_CHAT = ("router", "group_router", "translate", "character_gen", "persona_seed")


def _build_router(routing_config: dict, providers_map: dict, local_provider) -> "LLMRouter":
    """根據 routing_config 建立 LLMRouter；單一來源避免兩處任務清單漂移。"""
    router = LLMRouter()
    for task_key in ALL_TASK_KEYS:
        fallback = routing_config.get("chat", {}) if task_key in TASKS_FALLBACK_TO_CHAT else {}
        cfg = routing_config.get(task_key, fallback)
        p_name = cfg.get("provider", "Ollama (本地)")
        m_name = cfg.get("model", "qwen3.5")
        active_prov = providers_map.get(p_name, local_provider)
        router.register_route(task_key, active_prov, m_name)
    return router


def init_all():
    """在 FastAPI lifespan startup 時呼叫一次，初始化全部核心物件。"""
    global memory_sys, storage, analyzer, global_router, character_mgr, bot_registry, telegram_bot_mgr, discord_bot_mgr, persona_sync_mgr, persona_snapshot_store, tts_client, embed_model, _startup_time
    import time
    _startup_time = time.time()

    migrated = migrate_legacy_runtime_data()
    if migrated:
        print(f"[Startup] 已遷移 runtime 資料到 runtime/: {', '.join(migrated)}")

    storage = StorageManager()
    memory_sys = MemorySystem()
    analyzer = MemoryAnalyzer(memory_sys)
    character_mgr = CharacterManager()
    bot_registry = BotRegistry()
    telegram_bot_mgr = TelegramBotManager(bot_registry)
    discord_bot_mgr = DiscordBotManager(bot_registry)
    persona_sync_mgr = PersonaSyncManager()
    persona_snapshot_store = PersonaSnapshotStore(storage)
    user_prefs = storage.load_prefs()
    tts_client = MinimaxTTSClient.from_prefs(user_prefs)  # None 若未啟用
    embed_model = user_prefs.get("embed_model", "bge-m3:latest")

    # 建立 Provider 實例
    openai_key = user_prefs.get("openai_key", "")
    or_key = user_prefs.get("or_key", "")
    llamacpp_url = user_prefs.get("llamacpp_url", "http://localhost:8080")
    ollama_url = user_prefs.get("ollama_url", "http://localhost:11434")
    local_provider = OllamaProvider(host=ollama_url)
    openai_provider = OpenAICompatibleProvider(api_key=openai_key)
    or_provider = OpenAICompatibleProvider(api_key=or_key, base_url="https://openrouter.ai/api/v1")
    llamacpp_provider = LlamaCppProvider(api_key="none", base_url=f"{llamacpp_url.rstrip('/')}/v1")

    providers_map = {
        "Ollama (本地)": local_provider,
        "OpenAI (雲端)": openai_provider,
        "OpenRouter (雲端)": or_provider,
        "llama.cpp (本地)": llamacpp_provider,
    }

    # 路由註冊
    routing_config = user_prefs.get("routing_config", {})
    global_router = _build_router(routing_config, providers_map, local_provider)

    # 向量引擎初始化（觸發 ONNX 載入）
    memory_sys.switch_embedding_model(local_provider, embed_model)

    # Warmup：強制載入 ONNX Session + Tokenizer 並跑一次推論，
    # 將冷啟動延遲轉移到啟動期，避免第一次使用者請求特別慢。
    import time as _t
    _w_start = _t.perf_counter()
    try:
        local_provider.get_embedding(text="warmup", model=embed_model)
        _w_ms = (_t.perf_counter() - _w_start) * 1000
        print(f"[Startup] Embedding warmup 完成 ({_w_ms:.0f} ms)")
    except Exception as e:
        print(f"[Startup] Embedding warmup 失敗（不影響後續運作）: {e}")

    # 初始 persona snapshot seeding：移到背景 thread，避免雲端 LLM 拖慢啟動
    # （warmup 之後執行，確保萬一 V1 路徑未來需要 embedder fallback 時 ONNX 已就緒）
    threading.Thread(
        target=_seed_initial_snapshots_background,
        args=(persona_snapshot_store, character_mgr, global_router),
        daemon=True,
        name="persona-initial-seed",
    ).start()

    # 注入 storage 給 session_manager（持久化對話紀錄）
    from api.session_manager import session_manager
    session_manager.set_storage(storage)


def _seed_initial_snapshots_background(store, char_mgr, router) -> None:
    """背景執行初始 persona snapshot seeding；任何錯誤都吞下避免炸 thread。"""
    try:
        seeded = ensure_initial_persona_snapshots(
            store,
            char_mgr.load_characters(),
            router=router,
        )
        if seeded:
            from core.system_logger import SystemLogger
            SystemLogger.log_system_event("persona_initial_snapshot_seeded", seeded)
            print(f"[Startup] 已補上初始 persona snapshot：{seeded}")
    except Exception as exc:
        from core.system_logger import SystemLogger
        SystemLogger.log_error("persona_initial_snapshot_thread", f"background seed failed: {exc}")


def reload_tts(prefs: dict | None = None) -> None:
    """熱重載 TTS 設定（PUT /system/config 時呼叫）。"""
    global tts_client
    if prefs is None and storage is not None:
        prefs = storage.load_prefs()
    tts_client = MinimaxTTSClient.from_prefs(prefs or {})


def reload_router():
    """熱重載路由設定（PUT /system/config 時呼叫）。"""
    global global_router, embed_model
    if storage is None or memory_sys is None:
        return

    user_prefs = storage.load_prefs()
    embed_model = user_prefs.get("embed_model", "bge-m3:latest")

    openai_key = user_prefs.get("openai_key", "")
    or_key = user_prefs.get("or_key", "")
    llamacpp_url = user_prefs.get("llamacpp_url", "http://localhost:8080")
    ollama_url = user_prefs.get("ollama_url", "http://localhost:11434")
    local_provider = OllamaProvider(host=ollama_url)
    openai_provider = OpenAICompatibleProvider(api_key=openai_key)
    or_provider = OpenAICompatibleProvider(api_key=or_key, base_url="https://openrouter.ai/api/v1")
    llamacpp_provider = LlamaCppProvider(api_key="none", base_url=f"{llamacpp_url.rstrip('/')}/v1")
    providers_map = {
        "Ollama (本地)": local_provider,
        "OpenAI (雲端)": openai_provider,
        "OpenRouter (雲端)": or_provider,
        "llama.cpp (本地)": llamacpp_provider,
    }

    routing_config = user_prefs.get("routing_config", {})
    global_router = _build_router(routing_config, providers_map, local_provider)

    # 切換向量引擎（可能 embed_model 有變化）
    memory_sys.switch_embedding_model(local_provider, embed_model)


def get_uptime() -> float:
    import time
    return time.time() - _startup_time


# ── FastAPI Depends helpers ──────────────────────────────
def get_memory_sys() -> MemorySystem:
    assert memory_sys is not None, "MemorySystem not initialized"
    return memory_sys


def get_storage() -> StorageManager:
    assert storage is not None, "StorageManager not initialized"
    return storage


def is_db_maintenance_mode() -> bool:
    with _db_maintenance_lock:
        return _db_maintenance_enabled


def set_db_maintenance_mode(enabled: bool) -> bool:
    global _db_maintenance_enabled
    with _db_maintenance_lock:
        _db_maintenance_enabled = bool(enabled)
        return _db_maintenance_enabled


def require_db_writes_enabled() -> None:
    if is_db_maintenance_mode():
        raise HTTPException(
            status_code=503,
            detail="DB maintenance mode is enabled; write operations are temporarily disabled.",
        )


def get_analyzer() -> MemoryAnalyzer:
    assert analyzer is not None, "MemoryAnalyzer not initialized"
    return analyzer


def get_router() -> LLMRouter:
    assert global_router is not None, "LLMRouter not initialized"
    return global_router


def get_character_manager() -> CharacterManager:
    assert character_mgr is not None, "CharacterManager not initialized"
    return character_mgr


def get_bot_registry() -> BotRegistry:
    assert bot_registry is not None, "BotRegistry not initialized"
    return bot_registry


def get_telegram_bot_manager() -> TelegramBotManager:
    assert telegram_bot_mgr is not None, "TelegramBotManager not initialized"
    return telegram_bot_mgr


def get_discord_bot_manager() -> DiscordBotManager:
    assert discord_bot_mgr is not None, "DiscordBotManager not initialized"
    return discord_bot_mgr


def get_tts_client() -> MinimaxTTSClient | None:
    """回傳 TTS client，未啟用時為 None。"""
    return tts_client


def get_persona_sync_manager() -> PersonaSyncManager:
    assert persona_sync_mgr is not None, "PersonaSyncManager not initialized"
    return persona_sync_mgr


def get_persona_snapshot_store() -> PersonaSnapshotStore:
    assert persona_snapshot_store is not None, "PersonaSnapshotStore not initialized"
    return persona_snapshot_store


def get_embed_model() -> str:
    return embed_model


def get_current_user(request: Request) -> dict:
    user = getattr(request.state, "current_user", None)
    if not user:
        raise HTTPException(status_code=401, detail="尚未登入")
    return user


def require_admin_user(request: Request) -> dict:
    user = get_current_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理員權限")
    return user
