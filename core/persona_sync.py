"""
PersonaSyncManager — 定時批次人格同步管理器。

每 20 分鐘檢查一次觸發條件，滿足後直接呼叫 PersonaProbe probe_engine 函式，
將分析結果寫回 ai_personality.md，取代舊有的即時觀察/反思機制。

PersonaProbe server 不需要啟動；probe_engine.py 與 llm_client.py 直接作為函式庫使用。

觸發條件（全部滿足才執行）：
  1. persona_sync_enabled == True
  2. 今日執行次數 < persona_sync_max_per_day
  3. 最後一筆訊息距今 > persona_sync_idle_minutes（系統閒置中）
  4. 上次反思後新訊息數 >= persona_sync_min_messages
"""
import json
import os
import sys
from datetime import datetime, date

from core.system_logger import SystemLogger

# ── PersonaProbe 路徑注入（只做一次）────────────────────────
_PROBE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "PersonaProbe")
if _PROBE_DIR not in sys.path:
    sys.path.insert(0, _PROBE_DIR)

_PROVIDER_MAP = {
    "Ollama (本地)": "ollama",
    "OpenAI (雲端)": "openrouter",
    "OpenRouter (雲端)": "openrouter",
    "llama.cpp (本地)": "ollama",
}

STATE_FILE = "persona_sync_state.json"


def _run_probe_sync(db_path: str, existing_persona: str, llm_provider: str,
                    model_name: str, ollama_url: str, or_key: str,
                    fragment_limit: int = 400) -> dict:
    """
    在同步執行緒中直接呼叫 PersonaProbe 核心函式，完成 8 次 LLM 分析。
    回傳 {"persona": str, "dimensions_found": list[str], "output_dir": str}，
    失敗時拋出例外。
    """
    import json as _json
    from pathlib import Path
    from datetime import datetime as _dt

    from llm_client import LLMClient, LLMConfig
    from probe_engine import (
        DIMENSION_SPECS,
        _messages_to_text,
        build_fragment_aggregation_prompt,
        build_fragment_extraction_prompt,
        build_persona_md_prompt,
        load_fragments_from_db,
    )

    # 1. 讀取對話片段（近期優先滑動窗口）
    messages = load_fragments_from_db(db_path, limit=fragment_limit)
    if not messages:
        raise ValueError("conversation.db 中沒有對話記錄")

    fragments_text = _messages_to_text(messages)

    # 2. 建立 LLM Client
    config = LLMConfig(
        provider=llm_provider,
        model=model_name,
        api_key=or_key,
        ollama_base_url=ollama_url,
        temperature=0.7,
    )
    client = LLMClient(config)

    # 3. 6 維度提取（6 次 LLM 呼叫）
    extraction_results: dict = {}
    for dim_id in sorted(DIMENSION_SPECS.keys()):
        prompt_msgs = build_fragment_extraction_prompt(dim_id, fragments_text, existing_persona)
        try:
            raw = client.chat(prompt_msgs)
            result = _json.loads(raw)
        except _json.JSONDecodeError:
            result = {"confidence": "none"}
        extraction_results[dim_id] = result

    # 4. 聚合生成完整報告（1 次 LLM 呼叫）
    agg_msgs = build_fragment_aggregation_prompt(extraction_results, fragments_text, existing_persona)
    full_report = client.chat(agg_msgs)

    # 5. 萃取 persona.md（1 次 LLM 呼叫）
    persona_msgs = build_persona_md_prompt(full_report, existing_persona)
    persona_content = client.chat(persona_msgs)

    # 6. 寫入 PersonaProbe result 目錄（留存備份）
    output_root = Path(_PROBE_DIR) / "result"
    timestamp = _dt.now().strftime("%Y%m%d-%H%M%S")
    out_dir = output_root / f"fragment-{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "probe-report.md").write_text(full_report, encoding="utf-8")
    (out_dir / "persona.md").write_text(persona_content, encoding="utf-8")
    (out_dir / "fragment-input.md").write_text(
        f"# 原始輸入片段\n\n{fragments_text}", encoding="utf-8"
    )

    dimensions_found = [
        DIMENSION_SPECS[dim_id]["name"]
        for dim_id, result in extraction_results.items()
        if result.get("confidence", "none") != "none"
    ]

    return {
        "persona": persona_content,
        "dimensions_found": dimensions_found,
        "output_dir": str(out_dir),
    }


class PersonaSyncManager:
    """PersonaProbe 批次反思觸發與執行管理器。"""

    # ── 狀態讀寫 ──────────────────────────────────────────

    def _load_state(self) -> dict:
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {
                "last_reflection_at": None,
                "today_date": "",
                "today_run_count": 0,
            }

    def _save_state(self, state: dict) -> None:
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            SystemLogger.log_error("persona_sync_state_save", str(e))

    def _reset_daily_count_if_needed(self, state: dict) -> dict:
        today_str = date.today().isoformat()
        if state.get("today_date") != today_str:
            state["today_run_count"] = 0
            state["today_date"] = today_str
        return state

    # ── 觸發條件判斷 ──────────────────────────────────────

    async def should_run(self, storage, prefs: dict) -> tuple[bool, str]:
        """
        依序檢查所有觸發條件，回傳 (should_run, reason)。
        reason 為跳過原因（skip 時）或 ok 描述（執行時）。
        """
        # 1. 全局開關
        if not prefs.get("persona_sync_enabled", True):
            return False, "disabled"

        # 2. 每日上限
        state = self._load_state()
        state = self._reset_daily_count_if_needed(state)
        max_per_day = prefs.get("persona_sync_max_per_day", 2)
        if state.get("today_run_count", 0) >= max_per_day:
            return False, f"daily_limit_reached({state['today_run_count']}/{max_per_day})"

        # 3. 閒置檢查
        try:
            last_msg_time = storage.get_last_message_time()
        except Exception as e:
            return False, f"storage_error({e})"

        if last_msg_time is None:
            return False, "no_messages_yet"

        idle_minutes = prefs.get("persona_sync_idle_minutes", 10)
        elapsed = (datetime.now() - last_msg_time).total_seconds() / 60
        if elapsed < idle_minutes:
            return False, f"not_idle({elapsed:.1f}min < {idle_minutes}min)"

        # 4. 最低訊息數
        min_messages = prefs.get("persona_sync_min_messages", 50)
        since_iso = state.get("last_reflection_at") or "1970-01-01T00:00:00"
        try:
            new_count = storage.count_messages_since(since_iso)
        except Exception as e:
            return False, f"count_error({e})"

        if new_count < min_messages:
            return False, f"insufficient_messages({new_count}/{min_messages})"

        return True, f"ok(new_msgs={new_count}, idle={elapsed:.1f}min)"

    # ── 主執行流程 ────────────────────────────────────────

    async def run_sync(self, storage, prefs: dict, count_toward_daily: bool = True) -> bool:
        """
        直接呼叫 PersonaProbe probe_engine 執行人格分析，並將結果寫入 evolved_prompt。
        count_toward_daily=False 時不佔用每日執行次數（手動觸發專用）。
        回傳 True 表示成功，False 表示失敗（不更新檔案）。
        """
        import asyncio

        # 確認 conversation.db 存在
        db_path = os.path.abspath("conversation.db")
        if not os.path.exists(db_path):
            SystemLogger.log_error("persona_sync", f"conversation.db not found: {db_path}")
            return False

        # 確認 PersonaProbe 可以 import
        try:
            import probe_engine  # noqa: F401
            import llm_client    # noqa: F401
        except ImportError as e:
            SystemLogger.log_error("persona_sync", f"PersonaProbe import failed: {e}")
            return False

        # 從 active character 取得現有人設（evolved 優先，否則用原始 system_prompt）
        from api.dependencies import get_character_manager
        char_mgr = get_character_manager()
        active_char_id = prefs.get("active_character_id", "default")
        active_char = char_mgr.get_active_character(active_char_id)
        existing_persona = char_mgr.get_effective_prompt(active_char)

        # 從 routing_config 取 LLM 設定（persona_sync 獨立任務，fallback 到 chat）
        routing = prefs.get("routing_config", {})
        sync_cfg = routing.get("persona_sync") or routing.get("chat", {})
        provider_name = sync_cfg.get("provider", "Ollama (本地)")
        model_name = sync_cfg.get("model", "")
        ollama_url = prefs.get("ollama_url", "http://localhost:11434")
        or_key = prefs.get("or_key", "")
        llm_provider = _PROVIDER_MAP.get(provider_name, "ollama")

        fragment_limit = prefs.get("persona_sync_fragment_limit", 400)

        # 呼叫前 Log：記錄完整傳送參數
        SystemLogger.log_system_event("persona_sync_start", {
            "character_id": active_char_id,
            "character_name": active_char.get("name", ""),
            "llm_provider": llm_provider,
            "llm_model": model_name,
            "ollama_base_url": ollama_url,
            "db_path": db_path,
            "fragment_limit": fragment_limit,
            "existing_persona_length": len(existing_persona),
            "existing_persona_preview": existing_persona[:100] if existing_persona else "",
        })

        # 在執行緒中執行同步 LLM 呼叫（避免阻塞 event loop）
        call_start = datetime.now()
        try:
            data = await asyncio.to_thread(
                _run_probe_sync,
                db_path, existing_persona, llm_provider,
                model_name, ollama_url, or_key,
                fragment_limit,
            )
        except Exception as e:
            SystemLogger.log_error("persona_sync", f"Probe execution failed: {e}")
            return False

        elapsed_sec = (datetime.now() - call_start).total_seconds()
        new_persona = data.get("persona", "").strip()

        if not new_persona:
            SystemLogger.log_error("persona_sync", "probe_engine returned empty persona")
            return False

        # 驗證格式（必須是 Markdown，含 # 標頭）
        if not new_persona.startswith("#"):
            idx = new_persona.find("\n#")
            if idx != -1:
                new_persona = new_persona[idx + 1:]
            elif "#" in new_persona:
                new_persona = new_persona[new_persona.find("#"):]
            else:
                SystemLogger.log_error("persona_sync", "Invalid persona format, skipping write")
                return False

        # 寫入 active character 的 evolved_prompt
        success = char_mgr.set_evolved_prompt(active_char_id, new_persona)
        if not success:
            SystemLogger.log_error("persona_sync", f"Character not found: {active_char_id}")
            return False

        # 更新狀態（手動觸發不佔每日次數）
        state = self._load_state()
        state = self._reset_daily_count_if_needed(state)
        state["last_reflection_at"] = datetime.now().isoformat()
        if count_toward_daily:
            state["today_run_count"] = state.get("today_run_count", 0) + 1
        self._save_state(state)

        dimensions_found = data.get("dimensions_found", [])
        SystemLogger.log_system_event("persona_sync_complete", {
            "character_id": active_char_id,
            "character_name": active_char.get("name", ""),
            "llm_provider": llm_provider,
            "llm_model": model_name,
            "elapsed_sec": round(elapsed_sec, 1),
            "new_persona_length": len(new_persona),
            "dimensions_found": dimensions_found,
            "dimensions_count": len(dimensions_found),
            "today_run_count": state["today_run_count"],
            "output_dir": data.get("output_dir", ""),
        })
        return True

    # ── 狀態查詢（供 REST API 使用） ──────────────────────

    def get_sync_status(self, storage=None) -> dict:
        """回傳目前的同步狀態，供 GET /system/personality/sync-status 使用。

        Args:
            storage: 選填，StorageManager 實例；提供時會額外計算距上次反思的訊息數。
        """
        state = self._load_state()
        state = self._reset_daily_count_if_needed(state)
        since_iso = state.get("last_reflection_at") or "1970-01-01T00:00:00"

        messages_since = 0
        if storage is not None:
            try:
                messages_since = storage.count_messages_since(since_iso)
            except Exception:
                pass

        return {
            "last_reflection_at": state.get("last_reflection_at"),
            "today_date": state.get("today_date", ""),
            "today_run_count": state.get("today_run_count", 0),
            "messages_since_last": messages_since,
        }
