"""
WebSocket 對話端點 + 同步 REST 對話端點。
核心邏輯從 ui_chat.py 提取，脫離 Streamlit 生命週期。
"""
import asyncio
import json
import re
import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from api.dependencies import (
    get_memory_sys, get_storage, get_router, get_analyzer,
    get_embed_model, get_personality_engine, db_write_lock,
)
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

    async def connect(self, session_id: str, ws: WebSocket):
        await ws.accept()
        self._connections[session_id] = ws

    def disconnect(self, session_id: str):
        self._connections.pop(session_id, None)

    async def send_json(self, session_id: str, data: dict):
        ws = self._connections.get(session_id)
        if ws:
            try:
                await ws.send_json(data)
            except Exception:
                self.disconnect(session_id)

    def get_ws(self, session_id: str) -> WebSocket | None:
        return self._connections.get(session_id)


ws_manager = ConnectionManager()


# ── AI 自我觀察背景任務 ──────────────────────────────────
async def _extract_ai_observations_bg(reply_text: str, context_msgs: list[dict]):
    """背景提取 AI 自我觀察並存入 DB（非阻塞）"""
    try:
        pe = get_personality_engine()
        rtr = get_router()
        observations = await asyncio.to_thread(
            pe.extract_self_observations, reply_text, context_msgs, rtr
        )
        if observations:
            context_summary = " | ".join(
                [f"{m['role']}: {m['content'][:50]}" for m in (context_msgs or [])[-2:]]
            )
            for obs in observations:
                await asyncio.to_thread(pe.store_observation, obs, context_summary)
    except Exception:
        pass  # 觀察提取失敗不影響主流程


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

    # ─── AI 人格反思 ───
    try:
        pe = get_personality_engine()
        if pe.should_reflect():
            reflection_ok = pe.run_reflection(rtr)
            if reflection_ok:
                pipeline_events.append({"type": "system_event", "action": "personality_reflected"})
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
                            user_prompt: str, user_prefs: dict):
    """
    同步執行對話編排的關鍵路徑（在執行緒池中跑）。
    話題偏移時的記憶管線已拆至背景，此處只做偵測 → 檢索 → 生成。
    回傳 (reply_text, new_entities, retrieval_context_dict, topic_shifted, pipeline_data)
    pipeline_data: 若話題偏移，包含 (msgs_to_extract, last_block) 供呼叫端發起背景任務。
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
    temperature = user_prefs.get("temperature", 0.7)

    topic_shifted = False
    pipeline_data = None  # (msgs_to_extract, last_block) — 供背景管線使用
    timer = StepTimer()

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
        blocks = ms.search_blocks(user_prompt, combined_keywords, 2, f_alpha, 0.5, memory_threshold, f_base)

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
                f"【情境回憶 {i + 1}】\n[時間]: {block['timestamp']}\n[概覽]: {block['overview']}\n[當時的詳細對話]:\n{raw_text}")
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

    # AI 個性檔案注入
    pe = get_personality_engine()
    personality_ctx = pe.get_personality_prompt()
    personality_block = f"\n【AI個性記憶】\n{personality_ctx}\n" if personality_ctx else ""

    sys_prompt = f"""{storage.load_system_prompt()}
{personality_block}{static_profile_block}
{core_ctx}{profile_ctx}
【動態攔截規則】：若包含指代不明的實體，請自然發問釐清。但若使用者已明確表示「忘記、不知道或不想討論」，請立即停止追問並順應話題。

【系統核心指令】：綜合以下情境記憶區塊來回答使用者。
[情境記憶區]
{mem_ctx}

【強制輸出格式】：你的回覆必須是合法的 JSON，僅包含以下兩個欄位，禁止輸出任何額外說明或 Markdown：
{{ "reply": "你的自然語言回覆", "extracted_entities": ["核心微觀實體1", "實體2"] }}"""

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
        "dynamic_prompt": sys_prompt,
    }

    # ─── LLM 生成 ───
    chat_schema = {
        "type": "object",
        "properties": {
            "reply": {"type": "string"},
            "extracted_entities": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["reply", "extracted_entities"],
    }

    with timer.step("上下文組裝 (Context Assembly)"):
        api_messages = [{"role": "system", "content": sys_prompt}]
        clean_history = [{"role": m["role"], "content": m["content"]} for m in session_messages[-4:]]
        api_messages.extend(clean_history)

    with timer.step("LLM 對話生成 (Chat Generation LLM)"):
        try:
            full_res = rtr.generate("chat", api_messages, temperature=temperature, response_format=chat_schema)
        except Exception as e:
            full_res = None
            reply_text = f"生成錯誤: {e}"
            new_entities = []

    if full_res is not None:
        with timer.step("回應解析 (Response Parsing)"):
            _start = full_res.find('{')
            if _start != -1:
                try:
                    parsed, _ = json.JSONDecoder().raw_decode(full_res, _start)
                    reply_text = parsed.get("reply", "解析錯誤")
                    new_entities = parsed.get("extracted_entities", [])
                except Exception:
                    reply_text = full_res
                    new_entities = []
            else:
                reply_text = full_res
                new_entities = []

    # 將效能計時結果注入 retrieval_ctx
    retrieval_ctx["perf_timing"] = timer.summary()

    return reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_data


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

            if frame_type == "clear_context":
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

            # 加入使用者訊息
            await session_manager.add_user_message(sid, content)
            s = await session_manager.get(sid)
            if not s:
                await ws.send_json({"type": "error", "code": "SESSION_LOST", "message": "Session lost"})
                continue

            user_prefs = get_storage().load_prefs()

            # 在執行緒池中跑關鍵路徑（偵測 → 檢索 → 生成）
            reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_data = \
                await asyncio.to_thread(
                    _run_chat_orchestration,
                    list(s.messages), list(s.last_entities), content, user_prefs,
                )

            # 如果話題偏移，通知客戶端並在背景啟動記憶管線
            if topic_shifted:
                await ws.send_json({"type": "system_event", "action": "topic_shift"})
                if pipeline_data:
                    asyncio.create_task(_run_memory_pipeline_bg(sid, *pipeline_data))

            # 推送檢索上下文
            await ws.send_json({"type": "retrieval_context", "data": retrieval_ctx})

            # 推送完整回覆（非串流模式，因為底層 LLM 目前不支援 async yield）
            await ws.send_json({"type": "token", "content": reply_text})
            await ws.send_json({"type": "chat_done", "reply": reply_text, "extracted_entities": new_entities})

            # 寫入 assistant 回覆
            await session_manager.add_assistant_message(sid, reply_text, retrieval_ctx, new_entities)

            # AI 自我觀察提取（背景非阻塞）
            if user_prefs.get("ai_observe_enabled", True):
                asyncio.create_task(_extract_ai_observations_bg(reply_text, list(s.messages[-4:])))

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
    session = await session_manager.get_or_create(body.session_id, channel="rest")
    sid = session.session_id

    await session_manager.add_user_message(sid, body.content)
    s = await session_manager.get(sid)
    if not s:
        return ChatSyncResponseDTO(reply="Session error")

    user_prefs = get_storage().load_prefs()

    reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_data = \
        await asyncio.to_thread(
            _run_chat_orchestration,
            list(s.messages), list(s.last_entities), body.content, user_prefs,
        )

    await session_manager.add_assistant_message(sid, reply_text, retrieval_ctx, new_entities)

    # AI 自我觀察提取（背景非阻塞）
    if user_prefs.get("ai_observe_enabled", True):
        asyncio.create_task(_extract_ai_observations_bg(reply_text, list(s.messages[-4:])))

    if topic_shifted:
        await session_manager.bridge(sid)
        # 在背景啟動記憶管線（管線完成後會透過 ws_manager 推送事件給 WebSocket 客戶端）
        if pipeline_data:
            asyncio.create_task(_run_memory_pipeline_bg(sid, *pipeline_data))

    return ChatSyncResponseDTO(
        reply=reply_text,
        extracted_entities=new_entities,
        retrieval_context=RetrievalContextDTO(**retrieval_ctx),
    )
