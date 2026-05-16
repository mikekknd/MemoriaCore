"""SystemLogger 結構化日誌測試。"""
import io
import json
import os

from core.system_logger import SystemLogger


def test_system_logger_adds_log_id(monkeypatch):
    log_path = os.path.join(os.getcwd(), ".pyTestTemp", "system_logger_test.jsonl")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    if os.path.exists(log_path):
        os.remove(log_path)
    monkeypatch.setattr("core.system_logger._LOG_FILE", str(log_path))
    SystemLogger._reset_for_tests()

    SystemLogger.log_error("test_context", "boom")

    with open(log_path, "r", encoding="utf-8") as f:
        entry = json.loads(f.read().strip())
    assert entry["type"] == "error"
    assert entry["log_id"]
    assert len(entry["log_id"]) == 32
    assert entry["trace_seq"] == 1
    assert entry["logged_at"]


def test_system_logger_system_event_accepts_details(monkeypatch):
    log_path = os.path.join(os.getcwd(), ".pyTestTemp", "system_logger_event_details_test.jsonl")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    if os.path.exists(log_path):
        os.remove(log_path)
    monkeypatch.setattr("core.system_logger._LOG_FILE", str(log_path))
    SystemLogger._reset_for_tests()

    SystemLogger.log_system_event(
        "group_router_post_policy",
        "route adjusted by youtube_live_group_closing",
        details={
            "raw_action": "stop_no_new_value",
            "final_action": "new_speaker_reply_to_ai",
        },
    )

    with open(log_path, "r", encoding="utf-8") as f:
        entry = json.loads(f.read().strip())
    assert entry["category"] == "group_router_post_policy"
    assert entry["details"] == {
        "raw_action": "stop_no_new_value",
        "final_action": "new_speaker_reply_to_ai",
    }


def test_llm_prompt_and_response_share_call_id(monkeypatch):
    log_path = os.path.join(os.getcwd(), ".pyTestTemp", "system_logger_llm_test.jsonl")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    if os.path.exists(log_path):
        os.remove(log_path)
    monkeypatch.setattr("core.system_logger._LOG_FILE", str(log_path))
    SystemLogger._reset_for_tests()

    call_id = SystemLogger.log_llm_prompt("chat", "model-a", [{"role": "user", "content": "hi"}])
    SystemLogger.log_llm_response("chat", "model-a", "hello", llm_call_id=call_id)

    with open(log_path, "r", encoding="utf-8") as f:
        entries = [json.loads(line) for line in f if line.strip()]

    assert entries[0]["llm_call_id"] == call_id
    assert entries[1]["llm_call_id"] == call_id
    assert entries[0]["trace_seq"] == 1
    assert entries[1]["trace_seq"] == 2


def test_system_logger_continues_existing_trace_seq(monkeypatch):
    log_path = os.path.join(os.getcwd(), ".pyTestTemp", "system_logger_seq_test.jsonl")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": "2026-01-01T00:00:00", "type": "system_event", "trace_seq": 41}) + "\n")

    monkeypatch.setattr("core.system_logger._LOG_FILE", str(log_path))
    SystemLogger._reset_for_tests()

    SystemLogger.log_error("test_context", "boom")

    with open(log_path, "r", encoding="utf-8") as f:
        entries = [json.loads(line) for line in f if line.strip()]

    assert entries[-1]["trace_seq"] == 42


def test_system_logger_handles_legacy_log_without_trace_seq(monkeypatch):
    """舊資料沒有 trace_seq 時，新寫入應從 1 開始。"""
    log_path = os.path.join(os.getcwd(), ".pyTestTemp", "system_logger_legacy_test.jsonl")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": "2026-01-01T00:00:00", "type": "system_event"}) + "\n")

    monkeypatch.setattr("core.system_logger._LOG_FILE", str(log_path))
    SystemLogger._reset_for_tests()

    SystemLogger.log_error("test_context", "boom")

    with open(log_path, "r", encoding="utf-8") as f:
        entries = [json.loads(line) for line in f if line.strip()]

    assert entries[-1]["trace_seq"] == 1


def test_system_logger_tail_scan_handles_partial_first_line(monkeypatch, tmp_path):
    """檔案大於 64KB 時，反向掃描的第一段殘缺資料應被丟棄，仍能讀到最近 trace_seq。"""
    log_path = tmp_path / "system_logger_tail_test.jsonl"
    padding = json.dumps({"timestamp": "2026-01-01T00:00:00", "type": "system_event", "trace_seq": 7, "padding": "x" * 200})
    lines = [padding] * 400  # ~80KB，迫使反向掃描切到行中
    lines.append(json.dumps({"timestamp": "2026-01-01T00:00:01", "type": "system_event", "trace_seq": 999}))
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    monkeypatch.setattr("core.system_logger._LOG_FILE", str(log_path))
    SystemLogger._reset_for_tests()

    SystemLogger.log_error("test_context", "boom")

    with open(log_path, "r", encoding="utf-8") as f:
        entries = [json.loads(line) for line in f if line.strip()]

    assert entries[-1]["trace_seq"] == 1000


def test_system_logger_console_output_survives_cp950_stream(monkeypatch):
    log_path = os.path.join(os.getcwd(), ".pyTestTemp", "system_logger_cp950_test.jsonl")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    if os.path.exists(log_path):
        os.remove(log_path)
    raw_stream = io.BytesIO()
    cp950_stdout = io.TextIOWrapper(raw_stream, encoding="cp950", errors="strict")
    monkeypatch.setattr("sys.stdout", cp950_stdout)
    monkeypatch.setattr("core.system_logger._LOG_FILE", str(log_path))
    SystemLogger._reset_for_tests()

    call_id = SystemLogger.log_llm_prompt(
        "chat",
        "model-a",
        [{"role": "user", "content": "動畫與 ≤ 符號不應讓 console logging 中斷"}],
    )
    SystemLogger.log_llm_response("chat", "model-a", "这段包含簡中與貓字", llm_call_id=call_id)
    cp950_stdout.flush()

    with open(log_path, "r", encoding="utf-8") as f:
        entries = [json.loads(line) for line in f if line.strip()]

    assert entries[0]["messages"][0]["content"].startswith("動畫與")
    assert entries[1]["content"].startswith("这段包含")
