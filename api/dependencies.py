"""
依賴注入層 — FastAPI 的 Single Source of Truth。
複製 app.py 的初始化邏輯，但脫離 Streamlit 生命週期。
所有 ONNX / SQLite / LLMRouter 的唯一實例都在此管理。
"""
import asyncio
import sys
import os

# 確保專案根目錄在 Python path 上（api/ 是子目錄）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from storage_manager import StorageManager
from llm_gateway import OllamaProvider, OpenAICompatibleProvider, LLMRouter
from core_memory import MemorySystem
from memory_analyzer import MemoryAnalyzer
from personality_engine import PersonalityEngine

# ── Module-level singletons ──────────────────────────────
memory_sys: MemorySystem | None = None
storage: StorageManager | None = None
analyzer: MemoryAnalyzer | None = None
global_router: LLMRouter | None = None
personality_engine: PersonalityEngine | None = None
embed_model: str = ""

# SQLite 寫入序列化鎖
db_write_lock = asyncio.Lock()

# 啟動時間記錄（供 /health 使用）
_startup_time: float = 0.0


def init_all():
    """在 FastAPI lifespan startup 時呼叫一次，初始化全部核心物件。"""
    global memory_sys, storage, analyzer, global_router, personality_engine, embed_model, _startup_time
    import time
    _startup_time = time.time()

    storage = StorageManager()
    memory_sys = MemorySystem()
    analyzer = MemoryAnalyzer(memory_sys)
    personality_engine = PersonalityEngine(memory_sys, storage)

    user_prefs = storage.load_prefs()
    embed_model = user_prefs.get("embed_model", "bge-m3:latest")

    # 建立 Provider 實例
    openai_key = user_prefs.get("openai_key", "")
    or_key = user_prefs.get("or_key", "")
    local_provider = OllamaProvider()
    openai_provider = OpenAICompatibleProvider(api_key=openai_key)
    or_provider = OpenAICompatibleProvider(api_key=or_key, base_url="https://openrouter.ai/api/v1")

    providers_map = {
        "Ollama (本地)": local_provider,
        "OpenAI (雲端)": openai_provider,
        "OpenRouter (雲端)": or_provider,
    }

    # 路由註冊
    routing_config = user_prefs.get("routing_config", {})
    global_router = LLMRouter()
    tasks = ["chat", "pipeline", "expand", "compress", "distill", "ep_fuse", "profile", "ai_observe", "ai_reflect"]
    for task_key in tasks:
        p_name = routing_config.get(task_key, {}).get("provider", "Ollama (本地)")
        m_name = routing_config.get(task_key, {}).get("model", "qwen3.5")
        active_prov = providers_map.get(p_name, local_provider)
        global_router.register_route(task_key, active_prov, m_name)

    # 向量引擎初始化（觸發 ONNX 載入）
    memory_sys.switch_embedding_model(local_provider, embed_model)

    # 注入 storage 給 session_manager（持久化對話紀錄）
    from api.session_manager import session_manager
    session_manager.set_storage(storage)


def reload_router():
    """熱重載路由設定（PUT /system/config 時呼叫）。"""
    global global_router, embed_model
    if storage is None or memory_sys is None:
        return

    user_prefs = storage.load_prefs()
    embed_model = user_prefs.get("embed_model", "bge-m3:latest")

    openai_key = user_prefs.get("openai_key", "")
    or_key = user_prefs.get("or_key", "")
    local_provider = OllamaProvider()
    openai_provider = OpenAICompatibleProvider(api_key=openai_key)
    or_provider = OpenAICompatibleProvider(api_key=or_key, base_url="https://openrouter.ai/api/v1")
    providers_map = {
        "Ollama (本地)": local_provider,
        "OpenAI (雲端)": openai_provider,
        "OpenRouter (雲端)": or_provider,
    }

    routing_config = user_prefs.get("routing_config", {})
    global_router = LLMRouter()
    tasks = ["chat", "pipeline", "expand", "compress", "distill", "ep_fuse", "profile", "ai_observe", "ai_reflect"]
    for task_key in tasks:
        p_name = routing_config.get(task_key, {}).get("provider", "Ollama (本地)")
        m_name = routing_config.get(task_key, {}).get("model", "qwen3.5")
        active_prov = providers_map.get(p_name, local_provider)
        global_router.register_route(task_key, active_prov, m_name)

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


def get_analyzer() -> MemoryAnalyzer:
    assert analyzer is not None, "MemoryAnalyzer not initialized"
    return analyzer


def get_router() -> LLMRouter:
    assert global_router is not None, "LLMRouter not initialized"
    return global_router


def get_personality_engine() -> PersonalityEngine:
    assert personality_engine is not None, "PersonalityEngine not initialized"
    return personality_engine


def get_embed_model() -> str:
    return embed_model
