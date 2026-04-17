"""
WebSocket 對話端點 + 同步 REST 對話端點。
核心邏輯從 ui_chat.py 提取，脫離 Streamlit 生命週期。
"""
import asyncio
import json
import queue as sync_queue
import re
import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from api.dependencies import (
    get_memory_sys, get_storage, get_router, get_analyzer,
    get_embed_model, db_write_lock,
    get_character_manager
)
from core.prompt_manager import get_prompt_manager
from api.session_manager import session_manager
from api.models.requests import ChatSyncRequest
from api.models.responses import ChatSyncResponseDTO, RetrievalContextDTO


# ── 效能計時器 ────────────────────────────────────────────
class StepTimer:
    """記錄每個步驟的耗時，供效能分析使用。"""
    def __init__(self):
        self._steps: list[dict] = []
        self._wall_start = time.perf_counter()

    def step(self, name: str):
        """回傳一個 context manager，自動記錄該步驟的耗時。"""
        return _TimedStep(self, name)

    def summary(self) -> dict:
        total = time.perf_counter() - self._wall_start
        return {
            "total_ms": round(total * 1000, 1),
            "steps": self._steps,
        }


class _TimedStep:
    def __init__(self, timer: StepTimer, name: str):
        self._timer = timer
        self._name = name
        self._start = 0.0

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        elapsed = time.perf_counter() - self._start
        self._timer._steps.append({
            "name": self._name,
            "ms": round(elapsed * 1000, 1),
        })

router = APIRouter(prefix="/chat", tags=["chat"])


# ── WebSocket 連線管理（供背景任務推送系統事件） ──────────
class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, WebSocket] = {}  # session_id -> ws
        self._active_tasks: dict[str, asyncio.Task] = {}  # session_id -> running task

    async def connect(self, session_id: str, ws: WebSocket):
        await ws.accept()
        self._connections[session_id] = ws

    def disconnect(self, session_id: str):
        self._connections.pop(session_id, None)
        self._active_tasks.pop(session_id, None)

    async def send_json(self, session_id: str, data: dict):
        ws = self._connections.get(session_id)
        if ws:
            try:
                await ws.send_json(data)
            except Exception:
                self.disconnect(session_id)

    def get_ws(self, session_id: str) -> WebSocket | None:
        return self._connections.get(session_id)

    def set_active_task(self, session_id: str, task: asyncio.Task):
        self._active_tasks[session_id] = task

    async def cancel_active_task(self, session_id: str):
        task = self._active_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    def clear_active_task(self, session_id: str):
        self._active_tasks.pop(session_id, None)


ws_manager = ConnectionManager()


# ── 記憶管線背景任務 ──────────────────────────────────────
def _run_memory_pipeline_sync(msgs_to_extract: list[dict], last_block: dict | None):
    """
    同步執行記憶管線全流程（在背景執行緒中跑）。
    包含：記憶管線 LLM → 區塊寫入 → 畫像提取 → 偏好聚合 → 人格反思。
    回傳 pipeline_events 列表。
    """
    ms = get_memory_sys()
    analyzer = get_analyzer()
    rtr = get_router()
    embed_model = get_embed_model()
    pipeline_events = []

    t_start = time.perf_counter()

    # ─── 記憶管線 LLM ───
    try:
        pipeline_res = analyzer.process_memory_pipeline(
            msgs_to_extract, last_block, rtr, embed_model, task_key="pipeline",
        )
    except Exception as e:
        pipeline_res = {"error": str(e)}

    if "error" not in pipeline_res:
        healed_list = pipeline_res.get("healed_entities")
        if healed_list and last_block:
            old_overview = last_block["overview"]
            summary_part = old_overview.split("\n[情境摘要]: ")[-1] if "\n[情境摘要]: " in old_overview else old_overview
            new_overview = f"[核心實體]: {', '.join(healed_list)}\n[情境摘要]: {summary_part}"
            ms.update_memory_block(last_block["block_id"], new_overview)

        for block in pipeline_res.get("new_memories", []):
            entities_str = ", ".join(block.get("entities", []))
            summary_str = block.get("summary", "無摘要")
            indices = block.get("message_indices", [])
            prefs = block.get("potential_preferences", [])
            overview = f"[核心實體]: {entities_str}\n[情境摘要]: {summary_str}"
            raw_dialogues = [msgs_to_extract[idx] for idx in indices if 0 <= idx < len(msgs_to_extract)]
            if raw_dialogues:
                ms.add_memory_block(overview, raw_dialogues, router=rtr, potential_preferences=prefs)

    pipeline_events.append({"type": "system_event", "action": "pipeline_complete",
                            "new_blocks": len(pipeline_res.get("new_memories", []))})

    # ─── 使用者畫像提取 ───
    try:
        current_profile = ms.storage.load_all_profiles(ms.db_path) if ms.db_path else []
        profile_facts = analyzer.extract_user_facts(msgs_to_extract, current_profile, rtr, task_key="profile")
        if profile_facts:
            ms.apply_profile_facts(profile_facts, embed_model)
            pipeline_events.append({"type": "system_event", "action": "profile_updated",
                                    "facts_count": len(profile_facts)})
    except Exception:
        pass

    # ─── 偏好聚合 ───
    try:
        from preference_aggregator import PreferenceAggregator
        pref_agg = PreferenceAggregator(ms)
        promoted = pref_agg.aggregate(score_threshold=3.0)
        if promoted:
            pref_agg.write_to_profile(promoted)
            pipeline_events.append({"type": "system_event", "action": "preferences_aggregated",
                                    "promoted_count": len(promoted)})
    except Exception:
        pass

    pipeline_events.append({"type": "system_event", "action": "graph_updated", "entity": "memory_blocks"})

    elapsed_ms = (time.perf_counter() - t_start) * 1000
    pipeline_events.append({"type": "system_event", "action": "pipeline_timing",
                            "elapsed_ms": round(elapsed_ms, 1)})
    return pipeline_events


async def _run_memory_pipeline_bg(session_id: str, msgs_to_extract: list[dict],
                                   last_block: dict | None):
    """
    非同步包裝器：在背景執行緒中跑記憶管線，完成後推送事件給 WebSocket 客戶端。
    """
    try:
        events = await asyncio.to_thread(
            _run_memory_pipeline_sync, msgs_to_extract, last_block,
        )
        for evt in events:
            await ws_manager.send_json(session_id, evt)
    except Exception:
        pass  # 背景管線失敗不影響已完成的對話回覆


# ── 共用：完整對話編排邏輯 ────────────────────────────────
def _run_chat_orchestration(session_messages: list[dict], last_entities: list[str],
                            user_prompt: str, user_prefs: dict, on_event=None):
    """
    同步執行對話編排的關鍵路徑（在執行緒池中跑）。
    話題偏移時的記憶管線已拆至背景，此處只做偵測 → 檢索 → 生成。
    回傳 (reply_text, new_entities, retrieval_context_dict, topic_shifted, pipeline_data, inner_thought, status_metrics, tone, speech)
    pipeline_data: 若話題偏移，包含 (msgs_to_extract, last_block) 供呼叫端發起背景任務。
    on_event: 可選的 callback，用於即時推送中間狀態（如工具呼叫通知）給前端。
    """
    ms = get_memory_sys()
    analyzer = get_analyzer()
    rtr = get_router()
    storage = get_storage()
    embed_model = get_embed_model()

    shift_threshold = user_prefs.get("shift_threshold", 0.55)
    ui_alpha = user_prefs.get("ui_alpha", 0.6)
    memory_hard_base = user_prefs.get("memory_hard_base", 0.55)
    memory_threshold = user_prefs.get("memory_threshold", 0.5)
    context_window = user_prefs.get("context_window", 10)
    temperature = user_prefs.get("temperature", 0.7)

    topic_shifted = False
    pipeline_data = None  # (msgs_to_extract, last_block) — 供背景管線使用
    timer = StepTimer()

    # ─── 提取 Active UIDs ───
    active_uids = set()
    for m in session_messages[-4:]:
        matches = re.findall(r'\[Ref:\s*([^\]]+)\]', m.get("content", ""))
        for uid in matches:
            active_uids.add(uid)

    # ─── 話題偏移偵測 ───
    with timer.step("話題偏移偵測 (Topic Shift Detection)"):
        is_shift, cohesion_score = analyzer.detect_topic_shift(
            session_messages, embed_model, threshold=shift_threshold,
        )

    if is_shift:
        topic_shifted = True
        # 準備背景管線所需的資料快照（不在此處執行管線）
        import copy
        msgs_to_extract = [{"role": m["role"], "content": m["content"]}
                           for m in session_messages[:-1]]
        last_block = copy.deepcopy(ms.memory_blocks[-1]) if ms.memory_blocks else None
        pipeline_data = (msgs_to_extract, last_block)

    # ─── 雙軌檢索 ───
    with timer.step("查詢擴展 (Query Expansion LLM)"):
        expand_res = ms.expand_query(user_prompt, session_messages, rtr, task_key="expand")
    inherited_str = " ".join(last_entities)
    combined_keywords = f"{expand_res['expanded_keywords']} {inherited_str}".strip()

    f_alpha = ui_alpha
    f_base = max(0.50, memory_hard_base - (0.05 * expand_res["entity_confidence"]))

    with timer.step("情境記憶檢索 (Memory Block Search)"):
        raw_blocks = ms.search_blocks(user_prompt, combined_keywords, 2, f_alpha, 0.5, memory_threshold, f_base)
        blocks = [b for b in raw_blocks if b.get("block_id") not in active_uids]

    with timer.step("核心認知檢索 (Core Memory Search)"):
        core_insights = ms.search_core_memories(user_prompt, top_k=1, threshold=0.45)

    core_ctx = ""
    core_debug_text = "未觸發核心認知。"
    if core_insights:
        core_ctx = f"【使用者核心資訊】：{core_insights[0]['insight']}\n"
        core_debug_text = f"觸發認知: {core_insights[0]['insight']} (Score: {core_insights[0]['score']:.3f})"

    with timer.step("使用者偏好檢索 (Profile Search)"):
        profile_matches = ms.search_profile_by_query(user_prompt, top_k=3, threshold=0.5)
    profile_ctx = ""
    profile_debug_text = "未觸發使用者偏好。"
    if profile_matches:
        profile_lines = [f"- {pm['fact_key']}: {pm['fact_value']}" for pm in profile_matches]
        profile_ctx = f"【使用者相關偏好】\n" + "\n".join(profile_lines) + "\n"
        profile_debug_text = f"觸發 {len(profile_matches)} 筆偏好: " + ", ".join(
            [f"{pm['fact_key']}={pm['fact_value']} ({pm['score']:.3f})" for pm in profile_matches])

    block_details = []
    mem_ctx = "無相關記憶。"
    if blocks:
        formatted_blocks = []
        for i, block in enumerate(blocks):
            raw_text = "\n".join([f"  - {m['role']}: {m['content']}" for m in block["raw_dialogues"] if "role" in m])
            formatted_blocks.append(
                f"【情境回憶 {i + 1}】[UID: {block.get('block_id', 'unknown')}]\n[時間]: {block['timestamp']}\n[概覽]: {block['overview']}\n[當時的詳細對話]:\n{raw_text}")
            overview_header = block['overview'].split('\n')[0] if '\n' in block['overview'] else block['overview']
            block_details.append({
                "id": i + 1, "overview": overview_header,
                "hybrid": block.get("_debug_score", 0),
                "dense": block.get("_debug_raw_sim", 0),
                "sparse": block.get("_debug_sparse_raw", 0),
                "recency": block.get("_debug_recency", 0),
                "importance": block.get("_debug_importance", 0),
            })
        mem_ctx = "\n\n".join(formatted_blocks)

    static_profile = ms.get_static_profile_prompt()
    static_profile_block = f"\n{static_profile}\n" if static_profile else ""

    proactive_topics_block = ms.get_proactive_topics_prompt(limit=1)
    if proactive_topics_block:
        proactive_topics_block = f"\n{proactive_topics_block}\n"

    # 天氣快取注入（直接讀本地 JSON，無 HTTP 開銷）
    weather_block = ""
    try:
        from tools.weather_cache import WeatherCache
        weather_summary = WeatherCache().get_current_slot()
        if weather_summary:
            weather_block = f"\n【即時天氣資訊】\n{weather_summary}\n"
    except Exception:
        pass

    # 動態角色載入（evolved_prompt 優先，否則使用 system_prompt）
    char_mgr = get_character_manager()
    active_char_id = user_prefs.get("active_character_id", "default")
    active_char = char_mgr.get_active_character(active_char_id)
    metrics = active_char.get("metrics", ["professionalism"])
    allowed_tones = active_char.get("allowed_tones", ["Neutral", "Happy", "Professional"])
    reply_rules = active_char.get("reply_rules", "Traditional Chinese. NO EMOJIS.")
    tts_rules = active_char.get("tts_rules", "")
    char_tts_lang = active_char.get("tts_language", "")
    char_sys_prompt = char_mgr.get_effective_prompt(active_char) or storage.load_system_prompt()

    metrics_str = ", ".join(metrics)
    tones_str = "/".join(allowed_tones)

    pm = get_prompt_manager()
    speech_instruction = ""
    speech_json = ""
    if char_tts_lang:
        speech_instruction = pm.get("chat_speech_instruction_tts").format(
            char_tts_lang=char_tts_lang,
            tts_rules=(" " + tts_rules) if tts_rules else "",
            reply_rules=reply_rules,
        )
        speech_json = f'  "speech": "符合 {char_tts_lang} 的發音文本",\n'
    else:
        speech_instruction = pm.get("chat_speech_instruction_no_tts").format(
            reply_rules=reply_rules,
        )
        speech_json = ""

    _suffix = get_prompt_manager().get("chat_system_suffix").format(
        mem_ctx=mem_ctx,
        metrics_str=metrics_str, tones_str=tones_str,
        speech_instruction=speech_instruction,
        metrics_example=metrics[0] if metrics else 'score',
        tones_example=allowed_tones[0] if allowed_tones else 'Neutral',
        speech_json=speech_json,
    )

    sys_prompt = f"""{char_sys_prompt}
{static_profile_block}{weather_block}
{core_ctx}{profile_ctx}{proactive_topics_block}
{_suffix}"""

    # 組裝 debug 用的完整 prompt 預覽（sys_prompt + 近期對話紀錄）
    _ctx_preview_lines = []
    for m in clean_history:
        role_label = "使用者" if m["role"] == "user" else "助理"
        preview = m["content"][:300] + ("..." if len(m["content"]) > 300 else "")
        _ctx_preview_lines.append(f"[{role_label}]: {preview}")
    _history_preview = (
        f"\n\n{'─'*40}\n[對話紀錄窗口（共 {len(clean_history)} 則）]\n"
        + "\n".join(_ctx_preview_lines)
        if clean_history else "\n\n[對話紀錄窗口：空（首輪對話）]"
    )

    retrieval_ctx = {
        "original_query": user_prompt,
        "expanded_keywords": expand_res['expanded_keywords'],
        "inherited_tags": last_entities,
        "has_memory": bool(blocks),
        "block_count": len(blocks),
        "threshold": memory_threshold,
        "hard_base": f_base,
        "confidence": expand_res["entity_confidence"],
        "block_details": block_details,
        "core_debug_text": core_debug_text,
        "profile_debug_text": profile_debug_text,
        "dynamic_prompt": sys_prompt + _history_preview,
        "context_messages_count": len(clean_history),
    }

    # 由系統直接標記引用的 UIDs，不依賴 LLM 回傳以避免降智或格式錯誤
    cited_uids = [b.get("block_id") for b in blocks if "block_id" in b] if blocks else []

    # ─── LLM 生成 ───
    metrics_props = {m: {"type": "integer", "minimum": 0, "maximum": 100} for m in metrics}
    schema_properties = {
        "internal_thought": {"type": "string"},
        "status_metrics": {
            "type": "object",
            "properties": metrics_props,
            "required": metrics
        },
        "tone": {"type": "string"},
        "reply": {"type": "string"},
        "extracted_entities": {"type": "array", "items": {"type": "string"}},
    }
    schema_required = ["internal_thought", "status_metrics", "tone", "reply", "extracted_entities"]
    if char_tts_lang:
        schema_properties["speech"] = {"type": "string"}
        schema_required.insert(3, "speech")
    chat_schema = {
        "type": "object",
        "properties": schema_properties,
        "required": schema_required,
    }

    with timer.step("上下文組裝 (Context Assembly)"):
        api_messages = [{"role": "system", "content": sys_prompt}]
        clean_history = [{"role": m["role"], "content": m["content"]} for m in session_messages[-context_window:]]
        # ⚠️ 關鍵：禁止移除此行。對話紀錄必須包含在 api_messages 中，否則 LLM 將失去上下文。
        # 修改 sys_prompt 組裝邏輯後，請確認此行仍存在且在 sys_prompt 之後執行。
        api_messages.extend(clean_history)

    with timer.step("LLM 對話生成 (Chat Generation LLM)"):
        try:
            from tools.tavily import TAVILY_SEARCH_SCHEMA, execute_tool_call
            from tools.weather import WEATHER_SCHEMA
            tools_list = [TAVILY_SEARCH_SCHEMA, WEATHER_SCHEMA]

            # ── 第一輪：輕量工具偵測 ──────────────────────────
            # 只帶最近 2 輪對話（4 則訊息）+ 極簡 system prompt，
            # 不帶人格設定與記憶上下文，僅判斷是否需要呼叫工具。
            # 輸出文字一律捨棄，只取 tool_calls。
            _lite_sys = (
                "判斷使用者最新的訊息是否需要呼叫外部工具（網路搜尋或天氣查詢）。"
                "若需要，呼叫對應工具並帶入精確查詢參數；若不需要，不輸出任何內容。"
            )
            _lite_history = clean_history[-4:]  # 最近 2 輪 = 最多 4 則（含當前 user 訊息）
            lite_messages = [{"role": "system", "content": _lite_sys}] + _lite_history

            # ⚠️ 使用 "router" task key（非 "chat"），確保 log 中可區分
            # "router" 已在 dependencies.py 登錄，預設 fallback 至 chat 的 provider/model
            _, tool_calls = rtr.generate_with_tools(
                "router", lite_messages, tools=tools_list, temperature=0.0,
            )

            # ── 若有工具呼叫：執行工具並將結果注入完整上下文 ──
            if tool_calls:
                # 通知前端
                if on_event:
                    for tc in tool_calls:
                        tool_name = tc.get("function", {}).get("name", "unknown")
                        query = tc.get("function", {}).get("arguments", {}).get("query", "")
                        on_event({
                            "type": "tool_status",
                            "action": "calling",
                            "tool_name": tool_name,
                            "message": f"正在搜尋：{query}" if query else f"正在呼叫工具：{tool_name}",
                        })

                # 將 tool_calls 與結果附加到完整上下文（api_messages 含完整人格與記憶）
                api_messages.append({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": tool_calls,
                })
                for tc in tool_calls:
                    tool_result = execute_tool_call(tc)
                    tc_id = tc.get("id", f"call_{tc.get('function', {}).get('name', 'unknown')}")
                    api_messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": tool_result,
                    })

                if on_event:
                    on_event({
                        "type": "tool_status",
                        "action": "complete",
                        "message": "搜尋完成，正在整理回覆...",
                    })

            # ── 第二輪：完整人格上下文 + schema 強制格式化 ────
            # 無論是否使用工具，都用完整 api_messages 生成最終回應。
            full_res = rtr.generate(
                "chat", api_messages, temperature=temperature,
                response_format=chat_schema,
            )

        except Exception as e:
            from system_logger import SystemLogger
            SystemLogger.log_error("ChatGeneration", f"{type(e).__name__}: {e}")
            full_res = None
            reply_text = f"生成錯誤: {e}"
            new_entities = []

    inner_thought = None
    status_metrics = None
    tone = None
    speech = None

    if full_res is not None:
        with timer.step("回應解析 (Response Parsing)"):
            _start = full_res.find('{')
            if _start != -1:
                try:
                    parsed, _ = json.JSONDecoder().raw_decode(full_res, _start)
                    reply_text = parsed.get("reply", "解析錯誤")
                    new_entities = parsed.get("extracted_entities", [])
                    inner_thought = parsed.get("internal_thought")
                    status_metrics = parsed.get("status_metrics")
                    tone = parsed.get("tone")
                    speech = parsed.get("speech")
                except Exception:
                    reply_text = full_res
                    new_entities = []
            else:
                reply_text = full_res
                new_entities = []

    # 將效能計時結果注入 retrieval_ctx
    retrieval_ctx["perf_timing"] = timer.summary()

    return reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_data, inner_thought, status_metrics, tone, speech, cited_uids


# ── 共用：選擇對話編排函式 ────────────────────────────────
def _select_orchestration(user_prefs: dict):
    """根據 dual_layer_enabled 設定選擇對話編排函式。"""
    if user_prefs.get("dual_layer_enabled", False):
        from core.chat_orchestrator import run_dual_layer_orchestration
        return run_dual_layer_orchestration
    return _run_chat_orchestration


def _unpack_orchestration_result(result: tuple):
    """統一解構 9/10/11-tuple 的編排結果。"""
    if len(result) == 11:
        return result  # (reply, entities, ctx, shifted, pipeline, thought, metrics, tone, speech, thinking_speech, cited_uids)
    if len(result) == 10:
        return (*result, [])
    # 舊版 9-tuple，補上 thinking_speech="" 和 cited_uids=[]
    return (*result, "", [])


# ── WebSocket 端點 ────────────────────────────────────────
@router.websocket("/stream")
async def chat_stream(ws: WebSocket, session_id: str | None = None):
    session = await session_manager.get_or_create(session_id, channel="websocket")
    sid = session.session_id
    await ws_manager.connect(sid, ws)

    # 發送 session 初始化訊息
    await ws.send_json({"type": "session_init", "session_id": sid})

    try:
        while True:
            raw = await ws.receive_text()
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "code": "INVALID_JSON", "message": "Invalid JSON frame"})
                continue

            frame_type = frame.get("type", "")

            if frame_type == "ping":
                await ws.send_json({"type": "pong"})
                continue

            if frame_type == "cancel":
                await ws_manager.cancel_active_task(sid)
                await ws.send_json({"type": "system_event", "action": "cancelled"})
                continue

            if frame_type == "clear_context":
                await ws_manager.cancel_active_task(sid)
                await session_manager.delete(sid)
                session = await session_manager.create()
                sid = session.session_id
                ws_manager._connections[sid] = ws
                await ws.send_json({"type": "session_init", "session_id": sid})
                continue

            if frame_type != "chat_message":
                await ws.send_json({"type": "error", "code": "UNKNOWN_FRAME", "message": f"Unknown frame type: {frame_type}"})
                continue

            content = frame.get("content", "").strip()
            if not content:
                await ws.send_json({"type": "error", "code": "EMPTY_MESSAGE", "message": "Empty message"})
                continue

            # 打斷機制：取消前一個活躍任務
            await ws_manager.cancel_active_task(sid)

            # 加入使用者訊息
            await session_manager.add_user_message(sid, content)
            s = await session_manager.get(sid)
            if not s:
                await ws.send_json({"type": "error", "code": "SESSION_LOST", "message": "Session lost"})
                continue

            user_prefs = get_storage().load_prefs()

            # 建立即時事件推送 callback（從工作執行緒安全呼叫 async WS send）
            loop = asyncio.get_running_loop()

            def _ws_event_cb(data: dict):
                asyncio.run_coroutine_threadsafe(ws.send_json(data), loop)

            # 選擇對話編排函式（雙層 or 單層）
            orchestration_fn = _select_orchestration(user_prefs)

            # 在執行緒池中跑關鍵路徑，包裝為 Task 以支援取消
            task = asyncio.create_task(asyncio.to_thread(
                orchestration_fn,
                list(s.messages), list(s.last_entities), content, user_prefs,
                on_event=_ws_event_cb,
            ))
            ws_manager.set_active_task(sid, task)

            try:
                result = await task
            except asyncio.CancelledError:
                # 任務被取消（使用者打斷），跳過後續處理
                continue
            finally:
                ws_manager.clear_active_task(sid)

            reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_data, \
                inner_thought, status_metrics, tone, speech, thinking_speech, cited_uids = \
                _unpack_orchestration_result(result)

            # 如果話題偏移，通知客戶端並在背景啟動記憶管線
            if topic_shifted:
                await ws.send_json({"type": "system_event", "action": "topic_shift"})
                if pipeline_data:
                    asyncio.create_task(_run_memory_pipeline_bg(sid, *pipeline_data))

            # 推送檢索上下文
            await ws.send_json({"type": "retrieval_context", "data": retrieval_ctx})

            # 推送完整回覆（非串流模式，因為底層 LLM 目前不支援 async yield）
            await ws.send_json({"type": "token", "content": reply_text})
            # 準備包含詳細狀態的 done payload
            done_payload = {
                "type": "chat_done",
                "reply": reply_text,
                "extracted_entities": new_entities,
                "internal_thought": inner_thought,
                "status_metrics": status_metrics,
                "tone": tone,
            }
            await ws.send_json(done_payload)

            # 寫入 assistant 回覆（後端隱性掛載引用 UID）
            saved_reply_text = reply_text
            if cited_uids:
                refs_str = " ".join([f"[Ref: {u}]" for u in cited_uids])
                saved_reply_text = f"{reply_text} {refs_str}"
            await session_manager.add_assistant_message(sid, saved_reply_text, retrieval_ctx, new_entities)

            # 如果話題偏移，執行橋接
            if topic_shifted:
                await session_manager.bridge(sid)

    except WebSocketDisconnect:
        ws_manager.disconnect(sid)
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "code": "INTERNAL", "message": str(e)})
        except Exception:
            pass
        ws_manager.disconnect(sid)


# ── 同步 REST 對話端點（供 Streamlit 使用） ──────────────
@router.post("/sync", response_model=ChatSyncResponseDTO)
async def chat_sync(body: ChatSyncRequest):
    session = None
    if body.session_id:
        session = await session_manager.get(body.session_id)
        if session is None:
            session = await session_manager.restore_from_db(body.session_id)
    if session is None:
        session = await session_manager.create(channel="streamlit")
    sid = session.session_id

    await session_manager.add_user_message(sid, body.content)
    s = await session_manager.get(sid)
    if not s:
        return ChatSyncResponseDTO(reply="Session error")

    user_prefs = get_storage().load_prefs()
    orchestration_fn = _select_orchestration(user_prefs)

    result = await asyncio.to_thread(
        orchestration_fn,
        list(s.messages), list(s.last_entities), body.content, user_prefs,
    )
    reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_data, \
        inner_thought, status_metrics, tone, speech, thinking_speech, cited_uids = \
        _unpack_orchestration_result(result)

    saved_reply_text = reply_text
    if cited_uids:
        refs_str = " ".join([f"[Ref: {u}]" for u in cited_uids])
        saved_reply_text = f"{reply_text} {refs_str}"
    await session_manager.add_assistant_message(sid, saved_reply_text, retrieval_ctx, new_entities)

    if topic_shifted:
        await session_manager.bridge(sid)
        if pipeline_data:
            asyncio.create_task(_run_memory_pipeline_bg(sid, *pipeline_data))

    return ChatSyncResponseDTO(
        reply=reply_text,
        extracted_entities=new_entities,
        retrieval_context=RetrievalContextDTO(**retrieval_ctx),
        cited_memory_uids=cited_uids,
        internal_thought=inner_thought,
        status_metrics=status_metrics,
        tone=tone,
        speech=speech,
        thinking_speech=thinking_speech or None,
    )


# ── SSE 串流端點（供 Streamlit 即時狀態更新） ────────────
@router.post("/stream-sync")
async def chat_stream_sync(body: ChatSyncRequest):
    """
    與 /sync 功能相同，但以 SSE (Server-Sent Events) 串流回傳中間狀態。
    事件格式：data: {"type": "tool_status"|"result"|"error", ...}
    """
    # 優先從記憶體取得 session；找不到時先嘗試從 DB 還原（後端重啟後記憶體清空的情況）；
    # 都沒有才建新 session（channel 統一用 streamlit，確保能出現在 UI session 列表）
    session = None
    if body.session_id:
        session = await session_manager.get(body.session_id)
        if session is None:
            session = await session_manager.restore_from_db(body.session_id)
    if session is None:
        session = await session_manager.create(channel="streamlit")
    sid = session.session_id

    await session_manager.add_user_message(sid, body.content)
    s = await session_manager.get(sid)
    if not s:
        async def _err():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Session error'}, ensure_ascii=False)}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    user_prefs = get_storage().load_prefs()
    event_q = sync_queue.Queue()
    orchestration_fn = _select_orchestration(user_prefs)

    def on_event(data: dict):
        event_q.put(data)

    async def event_generator():
        # 在背景執行緒中啟動對話編排
        orch_task = asyncio.create_task(asyncio.to_thread(
            orchestration_fn,
            list(s.messages), list(s.last_entities), body.content, user_prefs,
            on_event=on_event,
        ))

        # 持續輪詢 event queue，即時串流中間狀態給前端
        while not orch_task.done():
            try:
                event = event_q.get_nowait()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except sync_queue.Empty:
                await asyncio.sleep(0.1)

        # 排空佇列中剩餘的事件
        while not event_q.empty():
            event = event_q.get_nowait()
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        # 取得最終結果
        try:
            result = orch_task.result()
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
            return

        reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_data, \
            inner_thought, status_metrics, tone, speech, thinking_speech, cited_uids = \
            _unpack_orchestration_result(result)

        # 寫入 session 及背景任務（與 /sync 相同）
        saved_reply_text = reply_text
        if cited_uids:
            refs_str = " ".join([f"[Ref: {u}]" for u in cited_uids])
            saved_reply_text = f"{reply_text} {refs_str}"
        await session_manager.add_assistant_message(sid, saved_reply_text, retrieval_ctx, new_entities)

        if topic_shifted:
            await session_manager.bridge(sid)
            if pipeline_data:
                asyncio.create_task(_run_memory_pipeline_bg(sid, *pipeline_data))

        # 送出最終結果（含實際使用的 session_id，讓 UI 同步更新）
        final = {
            "type": "result",
            "session_id": sid,
            "reply": reply_text,
            "extracted_entities": new_entities,
            "retrieval_context": retrieval_ctx,
            "cited_memory_uids": cited_uids,
            "internal_thought": inner_thought,
            "status_metrics": status_metrics,
            "tone": tone,
            "thinking_speech": thinking_speech or None,
        }
        yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
