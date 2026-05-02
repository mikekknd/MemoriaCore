"""Log 讀取與管理端點"""
import json
import os
import sys
from fastapi import APIRouter, Query
from api.models.responses import LogEntryDTO
from core.runtime_paths import runtime_file

router = APIRouter(prefix="/logs", tags=["logs"])


def _get_log_path() -> str:
    """
    與 system_logger.py 的寫入路徑保持一致。
    """
    return runtime_file("llm_trace.jsonl")


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
    log_id: str | None = Query(None),
    trace_seq: int | None = Query(None),
    llm_call_id: str | None = Query(None),
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
            if log_id and entry.get("log_id") != log_id:
                continue
            if trace_seq is not None and entry.get("trace_seq") != trace_seq:
                continue
            if llm_call_id and entry.get("llm_call_id") != llm_call_id:
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
