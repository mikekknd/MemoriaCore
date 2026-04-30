"""SystemLogger 結構化日誌測試。"""
import json
import os

from core.system_logger import SystemLogger


def test_system_logger_adds_log_id(monkeypatch):
    log_path = os.path.join(os.getcwd(), ".pyTestTemp", "system_logger_test.jsonl")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    if os.path.exists(log_path):
        os.remove(log_path)
    monkeypatch.setattr("core.system_logger._LOG_FILE", str(log_path))

    SystemLogger.log_error("test_context", "boom")

    with open(log_path, "r", encoding="utf-8") as f:
        entry = json.loads(f.read().strip())
    assert entry["type"] == "error"
    assert entry["log_id"]
    assert len(entry["log_id"]) == 32
