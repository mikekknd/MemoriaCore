"""共用 pytest fixtures：MemorySystem、Router、Analyzer 等核心元件的隔離式初始化"""
import os
import sys
import pytest

# 將專案根目錄加入 Python path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core_memory import MemorySystem
from memory_analyzer import MemoryAnalyzer
from preference_aggregator import PreferenceAggregator
from llm_gateway import OllamaProvider, LLMRouter
from storage_manager import StorageManager
from tests.test_config import OLLAMA_SIM_MODEL, OLLAMA_TASK_MODEL, EMBED_MODEL, OLLAMA_AVAILABLE


@pytest.fixture(autouse=True, scope="session")
def ensure_project_root():
    """確保工作目錄為專案根目錄，使 StreamingAssets/Models/*.onnx 相對路徑正確"""
    original_cwd = os.getcwd()
    os.chdir(PROJECT_ROOT)
    yield
    os.chdir(original_cwd)


@pytest.fixture(scope="session")
def ollama_provider():
    """OllamaProvider 單例（session scope 避免重複初始化）"""
    if not OLLAMA_AVAILABLE:
        pytest.skip("Ollama 未啟動")
    return OllamaProvider()


@pytest.fixture
def router(ollama_provider):
    """LLMRouter：註冊所有 7 個 task key 至 Ollama 本地模型"""
    r = LLMRouter()
    # pipeline 任務使用較小模型
    for task_key in ["pipeline", "expand", "compress", "distill", "ep_fuse", "profile"]:
        r.register_route(task_key, ollama_provider, OLLAMA_TASK_MODEL)
    # 對話模擬使用較大模型
    r.register_route("chat", ollama_provider, OLLAMA_SIM_MODEL)
    return r


@pytest.fixture
def memory_system(ollama_provider, tmp_path):
    """全新的 MemorySystem，使用隔離的臨時 SQLite DB"""
    db_path = str(tmp_path / "test_memory.db")

    ms = MemorySystem()
    ms.embed_provider = ollama_provider
    ms.embed_model = EMBED_MODEL
    ms.db_path = db_path
    # 使用臨時檔案避免碰正式設定
    ms.storage = StorageManager(
        prefs_file=str(tmp_path / "test_prefs.json"),
        history_file=str(tmp_path / "test_history.json")
    )
    # 初始化 DB Schema
    ms.storage._init_db(db_path)
    ms.memory_blocks = []
    ms.core_memories = []
    ms.user_profiles = []
    return ms


@pytest.fixture
def analyzer(memory_system):
    """MemoryAnalyzer 綁定至測試用 MemorySystem"""
    return MemoryAnalyzer(memory_system)


@pytest.fixture
def pref_aggregator(memory_system):
    """PreferenceAggregator 綁定至測試用 MemorySystem"""
    return PreferenceAggregator(memory_system)
