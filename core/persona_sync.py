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
                    active_character_id: str,
                    fragment_limit: int = 400,
                    persona_face: str = "public") -> dict:
    """Path D pipeline：3 次 LLM 呼叫（trait_diff → report → persona.md）。

    分支判斷：若目前無活躍 trait（V1），走 ``build_trait_v1_prompt``；否則走
    ``build_trait_vn_prompt`` 帶入活躍清單。兩者都只有一次 LLM 呼叫。

    回傳 ``{"persona", "trait_diff", "active_traits", "summary", "output_dir"}``：
    - ``trait_diff`` 為 ``TraitDiff`` 實例（Vn）或包 V1 new_traits 的 TraitDiff
    - ``active_traits`` 為本輪進 prompt 的活躍清單（供 report builder / 日誌）
    - ``summary`` 為 LLM 報告的首段純文字（去 # 標頭）

    失敗時拋出例外由 ``run_sync`` 捕捉。
    """
    from pathlib import Path
    from datetime import datetime as _dt

    from llm_client import LLMClient, LLMConfig
    from probe_engine import (
        _messages_to_text,
        build_persona_md_prompt,
        build_trait_report_prompt,
        build_trait_v1_prompt,
        build_trait_vn_prompt,
        load_fragments_from_db,
    )
    from api.dependencies import get_storage
    from core.persona_evolution.extractor import (
        TRAIT_V1_SCHEMA,
        TRAIT_VN_SCHEMA,
        parse_trait_v1,
        parse_trait_vn,
    )
    from core.persona_evolution.snapshot_store import PersonaSnapshotStore
    from core.persona_evolution.trait_diff import TraitDiff

    # 1. 讀取對話片段（近期優先滑動窗口）
    # 各 face 只取對應 channel_class 的訊息，嚴格隔離
    channel_class_filter = ["private"] if persona_face == "private" else ["public"]
    messages = load_fragments_from_db(
        db_path, limit=fragment_limit, channel_class_filter=channel_class_filter
    )
    if not messages:
        raise ValueError("conversation.db 中沒有對話記錄")
    fragments_text = _messages_to_text(messages)

    # 2. 查當前活躍 trait → 決定 V1 / Vn 分支（依 persona_face 取對應血統樹）
    store = PersonaSnapshotStore(get_storage())
    active_traits = store.list_active_traits(active_character_id, persona_face=persona_face)
    is_v1 = len(active_traits) == 0

    # 3. 建立 LLM Client
    config = LLMConfig(
        provider=llm_provider,
        model=model_name,
        api_key=or_key,
        ollama_base_url=ollama_url,
        temperature=0.7,
    )
    client = LLMClient(config)

    # 4. 第 1 次 LLM：trait diff
    if is_v1:
        prompt_msgs = build_trait_v1_prompt(fragments_text, existing_persona)
        raw = client.chat(prompt_msgs, response_format=TRAIT_V1_SCHEMA)
        trait_diff = TraitDiff(updates=[], new_traits=parse_trait_v1(raw))
    else:
        prompt_msgs = build_trait_vn_prompt(fragments_text, existing_persona, active_traits)
        raw = client.chat(prompt_msgs, response_format=TRAIT_VN_SCHEMA)
        trait_diff = parse_trait_vn(raw)

    # 5. 第 2 次 LLM：敘事報告（Markdown）
    report_msgs = build_trait_report_prompt(
        trait_diff.model_dump(), active_traits, fragments_text
    )
    full_report = client.chat(report_msgs)

    # 6. 第 3 次 LLM：更新 persona.md（沿用既有 builder，它以報告分析區塊驅動更新）
    persona_msgs = build_persona_md_prompt(full_report, existing_persona)
    persona_content = client.chat(persona_msgs)

    # 7. 寫入 PersonaProbe result 目錄（留存備份）
    output_root = Path(_PROBE_DIR) / "result"
    timestamp = _dt.now().strftime("%Y%m%d-%H%M%S")
    out_dir = output_root / f"fragment-{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "probe-report.md").write_text(full_report, encoding="utf-8")
    (out_dir / "persona.md").write_text(persona_content, encoding="utf-8")
    (out_dir / "fragment-input.md").write_text(
        f"# 原始輸入片段\n\n{fragments_text}", encoding="utf-8"
    )

    # 取 probe-report 的首段純文字作為 summary（跳過 Markdown 標頭行與空行）
    summary = ""
    for line in full_report.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        summary = s
        break

    return {
        "persona": persona_content,
        "trait_diff": trait_diff,
        "active_traits": active_traits,
        "summary": summary,
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
            return {}

    def _save_state(self, state: dict) -> None:
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            SystemLogger.log_error("persona_sync_state_save", str(e))

    _FACE_STATE_DEFAULT = staticmethod(lambda: {
        "last_reflection_at": None,
        "today_date": "",
        "today_run_count": 0,
    })

    def _load_face_state(self, face: str) -> dict:
        """讀取指定 persona_face 的獨立觸發狀態。"""
        raw = self._load_state()
        return dict(raw.get("faces", {}).get(face, self._FACE_STATE_DEFAULT()))

    def _save_face_state(self, face: str, face_state: dict) -> None:
        """寫入指定 persona_face 的觸發狀態。"""
        raw = self._load_state()
        raw.setdefault("faces", {})[face] = face_state
        self._save_state(raw)

    def _reset_daily_count_if_needed(self, state: dict) -> dict:
        today_str = date.today().isoformat()
        if state.get("today_date") != today_str:
            state["today_run_count"] = 0
            state["today_date"] = today_str
        return state

    # ── 觸發條件判斷 ──────────────────────────────────────

    async def should_run(self, storage, prefs: dict, persona_face: str = "public") -> tuple[bool, str]:
        """
        依序檢查所有觸發條件，回傳 (should_run, reason)。
        reason 為跳過原因（skip 時）或 ok 描述（執行時）。
        persona_face 決定獨立計算哪條 face 的訊息閾值與每日次數。
        """
        # 1. 全局開關
        if not prefs.get("persona_sync_enabled", True):
            return False, "disabled"

        # 2. 每日上限（per face）
        state = self._load_face_state(persona_face)
        state = self._reset_daily_count_if_needed(state)
        max_per_day = prefs.get("persona_sync_max_per_day", 2)
        if state.get("today_run_count", 0) >= max_per_day:
            return False, f"daily_limit_reached({state['today_run_count']}/{max_per_day})"

        # 3. 閒置檢查（per face：只計該 face 對應 channel_class 的最後訊息時間）
        channel_class = "private" if persona_face == "private" else "public"
        try:
            last_msg_time = storage.get_last_message_time_by_channel_class(channel_class)
        except Exception as e:
            return False, f"storage_error({e})"

        if last_msg_time is None:
            return False, "no_messages_yet"

        idle_minutes = prefs.get("persona_sync_idle_minutes", 10)
        elapsed = (datetime.now() - last_msg_time).total_seconds() / 60
        if elapsed < idle_minutes:
            return False, f"not_idle({elapsed:.1f}min < {idle_minutes}min)"

        # 4. 最低訊息數（per face：private face 只計 private channel 訊息）
        min_messages = prefs.get("persona_sync_min_messages", 50)
        since_iso = state.get("last_reflection_at") or "1970-01-01T00:00:00"
        try:
            if persona_face == "private":
                new_count = storage.count_messages_since_by_channel_class(since_iso, "private")
            else:
                new_count = storage.count_messages_since(since_iso)
        except Exception as e:
            return False, f"count_error({e})"

        if new_count < min_messages:
            return False, f"insufficient_messages({new_count}/{min_messages})"

        return True, f"ok(face={persona_face}, new_msgs={new_count}, idle={elapsed:.1f}min)"

    # ── 主執行流程 ────────────────────────────────────────

    async def run_sync(self, storage, prefs: dict, count_toward_daily: bool = True,
                       persona_face: str = "public") -> bool:
        """
        直接呼叫 PersonaProbe probe_engine 執行人格分析，並將結果寫入 evolved_prompt。
        count_toward_daily=False 時不佔用每日執行次數（手動觸發專用）。
        persona_face 決定從哪條 channel 取訊息、寫哪條血統樹。
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
            "persona_face": persona_face,
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
                active_char_id,
                fragment_limit,
                persona_face,
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

        # 寫入 active character 的 evolved_prompt（per-face）
        success = char_mgr.set_evolved_prompt(active_char_id, new_persona, persona_face=persona_face)
        if not success:
            SystemLogger.log_error("persona_sync", f"Character not found: {active_char_id}")
            return False

        # 寫入結構化 snapshot（失敗不回滾 evolved_prompt，對話體驗優先）
        snapshot_id = None
        try:
            from api.dependencies import get_storage
            from core.persona_evolution.snapshot_store import PersonaSnapshotStore
            from core.persona_evolution.trait_diff import TraitDiff

            trait_diff = data.get("trait_diff") or TraitDiff()
            store = PersonaSnapshotStore(get_storage())
            snapshot_id = store.save_snapshot(
                character_id=active_char_id,
                trait_diff=trait_diff,
                summary=data.get("summary", ""),
                evolved_prompt=new_persona,
                persona_face=persona_face,
            )
            SystemLogger.log_system_event("persona_snapshot_saved", {
                "character_id": active_char_id,
                "persona_face": persona_face,
                "snapshot_id": snapshot_id,
                "trait_updates": len(trait_diff.updates),
                "trait_new": len(trait_diff.new_traits),
            })
        except Exception as e:
            SystemLogger.log_error("persona_snapshot", f"snapshot write failed: {e}")

        # 更新 per-face 狀態（手動觸發不佔每日次數）
        face_state = self._load_face_state(persona_face)
        face_state = self._reset_daily_count_if_needed(face_state)
        face_state["last_reflection_at"] = datetime.now().isoformat()
        if count_toward_daily:
            face_state["today_run_count"] = face_state.get("today_run_count", 0) + 1
        self._save_face_state(persona_face, face_state)

        trait_diff = data.get("trait_diff")
        trait_updates = len(trait_diff.updates) if trait_diff else 0
        trait_new = len(trait_diff.new_traits) if trait_diff else 0
        active_count = len(data.get("active_traits", []))
        SystemLogger.log_system_event("persona_sync_complete", {
            "character_id": active_char_id,
            "character_name": active_char.get("name", ""),
            "persona_face": persona_face,
            "llm_provider": llm_provider,
            "llm_model": model_name,
            "elapsed_sec": round(elapsed_sec, 1),
            "new_persona_length": len(new_persona),
            "active_traits_before": active_count,
            "trait_updates": trait_updates,
            "trait_new": trait_new,
            "is_v1": active_count == 0,
            "today_run_count": face_state["today_run_count"],
            "output_dir": data.get("output_dir", ""),
            "snapshot_id": snapshot_id,
        })
        return True

    # ── 狀態查詢（供 REST API 使用） ──────────────────────

    def get_sync_status(self, storage=None, persona_face: str = "public") -> dict:
        """回傳目前的同步狀態，供 GET /system/personality/sync-status 使用。

        Args:
            storage: 選填，StorageManager 實例；提供時會額外計算距上次反思的訊息數。
            persona_face: 要查詢的 face（public / private），預設 public。
        """
        state = self._load_face_state(persona_face)
        state = self._reset_daily_count_if_needed(state)
        since_iso = state.get("last_reflection_at") or "1970-01-01T00:00:00"

        messages_since = 0
        if storage is not None:
            try:
                if persona_face == "private":
                    messages_since = storage.count_messages_since_by_channel_class(since_iso, "private")
                else:
                    messages_since = storage.count_messages_since(since_iso)
            except Exception:
                pass

        return {
            "last_reflection_at": state.get("last_reflection_at"),
            "today_date": state.get("today_date", ""),
            "today_run_count": state.get("today_run_count", 0),
            "messages_since_last": messages_since,
            "persona_face": persona_face,
        }
