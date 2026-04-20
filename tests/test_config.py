"""測試框架設定常數與 Ollama 可用性檢查"""
import os
import pytest

# ==========================================
# 模型設定（可透過環境變數覆蓋）
# ==========================================
OLLAMA_SIM_MODEL = os.environ.get("TEST_OLLAMA_SIM_MODEL", "gemma3:12b")
OLLAMA_TASK_MODEL = os.environ.get("TEST_OLLAMA_TASK_MODEL", "aya-expanse:8b")
EMBED_MODEL = "bge-m3:latest"

# ==========================================
# Ollama 可用性偵測（明確使用 127.0.0.1 連線）
# ==========================================
def _check_ollama():
    try:
        import ollama
        # 使用 127.0.0.1 而非 localhost，避免 Windows IPv6 問題
        client = ollama.Client(host='http://127.0.0.1:11434')
        result = client.list()
        # 兼容 dict 和 object 兩種回傳格式
        models = result.get('models', []) if isinstance(result, dict) else getattr(result, 'models', [])
        return len(models) > 0
    except Exception as e:
        return False

OLLAMA_AVAILABLE = _check_ollama()

requires_ollama = pytest.mark.skipif(
    not OLLAMA_AVAILABLE,
    reason="Ollama 未啟動或無法連線，跳過需要 LLM 的測試"
)
