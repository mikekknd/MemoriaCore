"""Log 讀取與管理端點"""
import json
import os
import sys
from fastapi import APIRouter, Query
from api.models.responses import LogEntryDTO

router = APIRouter(prefix="/logs", tags=["logs"])


def _get_log_path() -> str:
    """
    與 system_logger.py 的寫入路徑保持一致。

    兩種執行環境：
    - 開發模式：__file__ = .../LLMTest_New/api/routers/logs.py
                dirname×3 → LLMTest_New/  （專案根目錄）
    - PyInstaller 打包：system_logger.__file__ 解析到 _internal/，
                       因此 logs 寫在 _internal/llm_trace.jsonl。
                       此處同樣用 sys._MEIPASS 指向 _internal/ 讀取。
    """
    if getattr(sys, 'frozen', False):
        # 打包模式：system_logger 寫到 _internal/，從這裡讀
        return os.path.join(sys._MEIPASS, "llm_trace.jsonl")
    else:
        # 開發模式：專案根目錄
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(project_root, "llm_trace.jsonl")


def _entry_sort_key(entry: dict, fallback_index: int) -> tuple[int, int | str]:
    seq = entry.get("trace_seq")
    if isinstance(seq, int):
        return (0, seq)
    timestamp = entry.get("logged_at") or entry.get("timestamp")
    if timestamp:
        return (1, str(timestamp))
    return (2, fallback_index)


@router.get("", response_model=list[LogEntryDTO])
async def list_logs(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    type: str | None = Query(None),
    category: str | None = Query(None),
):
    log_path = _get_log_path()
    if not os.path.exists(log_path):
        return []

    entries = []
    with open(log_path, "r", encoding="utf-8") as f:
        line_index = 0
        for line in f:
            line_index += 1
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if type and entry.get("type") != type:
                continue
            if category and entry.get("category") != category:
                continue
            entries.append((line_index, entry))

    # 反序排列（最新在前）
    entries.sort(key=lambda item: _entry_sort_key(item[1], item[0]), reverse=True)
    sliced = [entry for _, entry in entries[offset:offset + limit]]

    # 單筆容錯：驗證失敗的條目跳過，不讓整批請求崩潰
    result = []
    for e in sliced:
        try:
            result.append(LogEntryDTO(**e))
        except Exception:
            pass
    return result


@router.delete("")
async def clear_logs():
    log_path = _get_log_path()
    if os.path.exists(log_path):
        with open(log_path, "w", encoding="utf-8") as f:
            f.truncate(0)
    return {"status": "cleared"}
