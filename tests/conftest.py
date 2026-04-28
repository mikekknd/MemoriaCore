"""共用 pytest fixtures：MemorySystem、Router、Analyzer 等核心元件的隔離式初始化"""
import os
import sys
import tempfile
import pytest

# 將專案根目錄加入 Python path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 統一 pytest 期間所有暫存與工具 cache，避免在專案根目錄散落 pytest_tmp* 等目錄。
PYTEST_TEMP_ROOT = os.path.join(PROJECT_ROOT, ".pyTestTemp")
PYTHON_TEMP_DIR = os.path.join(PYTEST_TEMP_ROOT, "temp")
TORCHINDUCTOR_CACHE_DIR = os.path.join(PYTEST_TEMP_ROOT, "torchinductor")
os.makedirs(PYTHON_TEMP_DIR, exist_ok=True)
os.makedirs(TORCHINDUCTOR_CACHE_DIR, exist_ok=True)
for _name in ("TMP", "TEMP", "TMPDIR"):
    os.environ[_name] = PYTHON_TEMP_DIR
os.environ["PYTEST_DEBUG_TEMPROOT"] = PYTEST_TEMP_ROOT
os.environ["TORCHINDUCTOR_CACHE_DIR"] = TORCHINDUCTOR_CACHE_DIR
tempfile.tempdir = PYTHON_TEMP_DIR

from core.core_memory import MemorySystem
from core.memory_analyzer import MemoryAnalyzer
from core.preference_aggregator import PreferenceAggregator
from core.llm_gateway import OllamaProvider, LLMRouter
from core.storage_manager import StorageManager
from tests.test_config import OLLAMA_SIM_MODEL, OLLAMA_TASK_MODEL, EMBED_MODEL, OLLAMA_AVAILABLE
from tests.mock_llm import MockRouter, MockMemorySystem, MockEmbedProvider  # noqa: F401


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


# ════════════════════════════════════════════════════════════
# SECTION: Mock Fixtures — 脫離 Ollama/檔案 I/O 的純單元測試
# ════════════════════════════════════════════════════════════


@pytest.fixture
def mock_router_with_tools():
    """MockRouter 預設含一組 tavily tool call，支援工具呼叫流程測試"""
    from tests.mock_llm import MockRouter
    router = MockRouter()
    router.set_tool_calls([{
        "id": "call_test123",
        "type": "function",
        "function": {
            "name": "tavily_search",
            "arguments": {"query": "台北天氣"}
        }
    }])
    return router


@pytest.fixture
def mock_character_manager():
    """回傳固定角色設定，避免 CharacterManager 檔案 I/O"""
    from unittest.mock import MagicMock
    cm = MagicMock()
    character = {
        "character_id": "default",
        "name": "測試助理",
        "metrics": ["professionalism"],
        "allowed_tones": ["Neutral", "Happy"],
        "reply_rules": "Traditional Chinese. NO EMOJIS.",
        "tts_rules": "",
        "tts_language": "",
        "system_prompt": "你是一個測試助理。",
        "visual_prompt": "測試助理，乾淨的角色肖像。",
        "evolved_prompt": None,
    }
    cm.get_active_character.return_value = character
    cm.get_character.return_value = character
    cm.get_effective_prompt.return_value = "你是一個測試助理。"
    return cm


@pytest.fixture
def mock_prompt_manager():
    """回傳可控 prompt 字串，杜絕 PromptManager 檔案讀取"""
    from unittest.mock import MagicMock
    pm = MagicMock()
    pm.get.side_effect = lambda key: {
        "router_system": "根據角色 {char_hint} 判斷是否需要工具。",
        "chat_speech_instruction_no_tts": "回覆規則：{reply_rules}",
        "chat_speech_instruction_tts": "TTS規則：{char_tts_lang} {reply_rules} {tts_rules}",
        "chat_system_suffix": "指標：{metrics_str} | 語氣：{tones_str}\n{speech_instruction}\n Memory: {mem_ctx}",
        "query_expand": "擴展關鍵詞：{user_query}",
        "memory_pipeline": "提取實體與摘要：\n對話：{dialogue_text}\n上一記憶：{last_overview}",
        "user_facts_extract": "從以下對話提取使用者事實：\n{dialogue_text}\n已知畫像：{profile_json}",
    }.get(key, f"MOCK_PROMPT:{key}")
    return pm


@pytest.fixture
def mock_storage():
    """StorageManager mock，回傳預設 prefs 和 system_prompt"""
    from unittest.mock import MagicMock
    s = MagicMock()
    s.load_prefs.return_value = {
        "temperature": 0.7,
        "shift_threshold": 0.55,
        "ui_alpha": 0.6,
        "memory_hard_base": 0.55,
        "memory_threshold": 0.5,
        "context_window": 10,
        "active_character_id": "default",
        "dual_layer_enabled": False,
        "tavily_api_key": "",
        "openweather_api_key": "",
    }
    s.load_system_prompt.return_value = "你是一個測試助理。"
    s.load_profile_vectors.return_value = []
    return s


@pytest.fixture
def mock_memory_system():
    """MockMemorySystem fixture — 直接代理自 tests.mock_llm"""
    return MockMemorySystem()


@pytest.fixture
def mock_router():
    """MockRouter fixture — 直接代理自 tests.mock_llm"""
    return MockRouter()


@pytest.fixture
def mock_embed_provider():
    """MockEmbedProvider fixture — 直接代理自 tests.mock_llm"""
    return MockEmbedProvider()


@pytest.fixture
def mock_analyzer():
    """Mock MemoryAnalyzer — 避免依賴真實 MemorySystem"""
    from unittest.mock import MagicMock
    from core.memory_analyzer import MemoryAnalyzer
    analyzer = MagicMock(spec=MemoryAnalyzer)
    analyzer.detect_topic_shift.return_value = (False, 0.8)
    return analyzer
