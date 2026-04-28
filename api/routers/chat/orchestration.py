"""單層對話編排：話題偏移偵測 → 雙軌檢索 → LLM 生成。

雙層編排（dual-layer agent）見 core/chat_orchestrator/。
依 user_prefs["dual_layer_enabled"] 由 _select_orchestration() 決定。
"""
import json
import re

from api.dependencies import (
    get_memory_sys, get_storage, get_router, get_analyzer,
    get_embed_model, get_character_manager,
)
from core.prompt_manager import get_prompt_manager
from core.prompt_utils import build_user_prefix
from core.chat_orchestrator.router_agent import run_router_agent
from api.routers.chat.timer import StepTimer
from api.routers.chat.pipeline import PipelineContext


# ════════════════════════════════════════════════════════════
# SECTION: 單層對話編排（_run_chat_orchestration）
# ════════════════════════════════════════════════════════════

def _run_chat_orchestration(
    session_messages: list[dict],
    last_entities: list[str],
    user_prompt: str,
    user_prefs: dict,
    on_event=None,
    session_ctx: dict | None = None,
):
    """
    同步執行對話編排的關鍵路徑（在執行緒池中跑）。
    話題偏移時的記憶管線已拆至背景，此處只做偵測 → 檢索 → 生成。
    回傳 (reply_text, new_entities, retrieval_context_dict, topic_shifted, pipeline_data,
          inner_thought, status_metrics, tone, speech, cited_uids)
    pipeline_data: 若話題偏移，回傳 PipelineContext 供呼叫端發起背景任務；否則為 None。
    on_event: 可選的 callback，用於即時推送中間狀態（如工具呼叫通知）給前端。
    session_ctx: {"user_id": str, "character_id": str, "persona_face": str}
    """
    ms = get_memory_sys()
    analyzer = get_analyzer()
    rtr = get_router()
    storage = get_storage()
    embed_model = get_embed_model()

    ctx = session_ctx or {}
    user_id = ctx.get("user_id", "default")
    character_id = ctx.get("character_id", "default")
    persona_face = ctx.get("persona_face", "public")
    write_visibility = persona_face  # persona_face == write_visibility（由 resolve_context 保證）
    visibility_filter = ["private", "public"] if persona_face == "private" else ["public"]

    shift_threshold = user_prefs.get("shift_threshold", 0.55)
    ui_alpha = user_prefs.get("ui_alpha", 0.6)
    memory_hard_base = user_prefs.get("memory_hard_base", 0.55)
    memory_threshold = user_prefs.get("memory_threshold", 0.5)
    context_window = user_prefs.get("context_window", 10)
    temperature = user_prefs.get("temperature", 0.7)

    topic_shifted = False
    pipeline_data: PipelineContext | None = None
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
        _user_blocks = ms._get_memory_blocks(user_id, character_id, write_visibility)
        last_block = copy.deepcopy(_user_blocks[-1]) if _user_blocks else None
        pipeline_data = PipelineContext(
            msgs_to_extract=msgs_to_extract,
            last_block=last_block,
            session_ctx=session_ctx or {},
        )

    # ─── 雙軌檢索 ───
    with timer.step("查詢擴展 (Query Expansion LLM)"):
        expand_res = ms.expand_query(user_prompt, session_messages, rtr, task_key="expand")
    inherited_str = " ".join(last_entities)
    combined_keywords = f"{expand_res['expanded_keywords']} {inherited_str}".strip()

    f_alpha = ui_alpha
    f_base = max(0.50, memory_hard_base - (0.05 * expand_res["entity_confidence"]))

    with timer.step("情境記憶檢索 (Memory Block Search)"):
        raw_blocks = ms.search_blocks(user_prompt, combined_keywords, 2, f_alpha, 0.5, memory_threshold, f_base,
                                      user_id=user_id, character_id=character_id, visibility_filter=visibility_filter)
        blocks = [b for b in raw_blocks if b.get("block_id") not in active_uids]

    with timer.step("核心認知檢索 (Core Memory Search)"):
        core_insights = ms.search_core_memories(user_prompt, top_k=1, threshold=0.45,
                                                user_id=user_id, character_id=character_id,
                                                visibility_filter=visibility_filter)

    core_ctx = ""
    core_debug_text = "未觸發核心認知。"
    if core_insights:
        core_ctx = f"【使用者核心資訊】：{core_insights[0]['insight']}\n"
        core_debug_text = f"觸發認知: {core_insights[0]['insight']} (Score: {core_insights[0]['score']:.3f})"

    with timer.step("使用者偏好檢索 (Profile Search)"):
        profile_matches = ms.search_profile_by_query(user_prompt, top_k=3, threshold=0.5,
                                                     user_id=user_id,
                                                     visibility_filter=visibility_filter)
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

    static_profile = ms.get_static_profile_prompt(user_id=user_id, visibility_filter=visibility_filter)
    static_profile_block = f"\n{static_profile}\n" if static_profile else ""

    proactive_topics_block = ms.get_proactive_topics_prompt(limit=1, user_id=user_id, character_id=character_id,
                                                             visibility_filter=visibility_filter)
    if proactive_topics_block:
        proactive_topics_block = f"\n{proactive_topics_block}\n"

    # 動態角色載入（evolved_prompt 優先，否則使用 system_prompt）
    char_mgr = get_character_manager()
    active_char = char_mgr.get_character(character_id)
    if not active_char:
        from core.system_logger import SystemLogger
        SystemLogger.log_error("character_missing", {
            "missing_character_id": character_id,
            "session_id": ctx.get("session_id", ""),
            "fallback": "default",
        })
        active_char = char_mgr.get_active_character("default")
    metrics = active_char.get("metrics", ["professionalism"])
    allowed_tones = active_char.get("allowed_tones", ["Neutral", "Happy", "Professional"])
    reply_rules = active_char.get("reply_rules", "Traditional Chinese. NO EMOJIS.")
    tts_rules = active_char.get("tts_rules", "")
    char_tts_lang = active_char.get("tts_language", "")
    char_sys_prompt = char_mgr.get_effective_prompt(active_char, persona_face=persona_face) or storage.load_system_prompt()

    pm = get_prompt_manager()
    speech_instruction = pm.get("chat_speech_instruction_no_tts").format(
        reply_rules=reply_rules,
    )

    _suffix = get_prompt_manager().get("chat_system_suffix").format(
        mem_ctx=mem_ctx,
        speech_instruction=speech_instruction,
    )

    sys_prompt = f"""{char_sys_prompt}
{static_profile_block}
{core_ctx}{profile_ctx}{proactive_topics_block}
{_suffix}"""

    # ⚠️ 上下文組裝必須在 debug 預覽之前完成（修正原本 clean_history 引用順序錯誤）。
    with timer.step("上下文組裝 (Context Assembly)"):
        api_messages = [{"role": "system", "content": sys_prompt}]
        clean_history = [{"role": m["role"], "content": m["content"]} for m in session_messages[-context_window:]]
        # ⚠️ 關鍵：禁止移除此行。對話紀錄必須包含在 api_messages 中，否則 LLM 將失去上下文。
        # 修改 sys_prompt 組裝邏輯後，請確認此行仍存在且在 sys_prompt 之後執行。
        api_messages.extend(clean_history)

        # 注入環境上下文 + 情緒軌跡前綴到當前使用者訊息（不放 system prompt，以保留 prefix cache）
        if api_messages and api_messages[-1]["role"] == "user":
            _prefix = build_user_prefix(session_messages)
            api_messages[-1] = {**api_messages[-1], "content": _prefix + api_messages[-1]["content"]}

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

    # ─── LLM 生成 ───（speech 移至獨立翻譯 subagent，status_metrics/tone 已移除）
    schema_properties = {
        "internal_thought": {"type": "string"},
        "reply": {"type": "string"},
        "extracted_entities": {"type": "array", "items": {"type": "string"}},
    }
    schema_required = ["internal_thought", "reply", "extracted_entities"]
    chat_schema = {
        "type": "object",
        "properties": schema_properties,
        "required": schema_required,
    }

    with timer.step("LLM 對話生成 (Chat Generation LLM)"):
        try:
            from tools.tavily import TAVILY_SEARCH_SCHEMA, execute_tool_call
            tools_list = []
            if user_prefs.get("tavily_api_key"):
                tools_list.append(TAVILY_SEARCH_SCHEMA)
            try:
                from tools.weather import WEATHER_SCHEMA
                if user_prefs.get("openweather_api_key"):
                    tools_list.append(WEATHER_SCHEMA)
            except ImportError:
                pass
            try:
                from tools.bash_tool import BASH_TOOL_SCHEMA
                if user_prefs.get("bash_tool_enabled"):
                    tools_list.append(BASH_TOOL_SCHEMA)
            except ImportError:
                pass
            try:
                from tools.browser_agent import BROWSER_AGENT_SCHEMA
                if user_prefs.get("browser_agent_enabled"):
                    tools_list.append(BROWSER_AGENT_SCHEMA)
            except ImportError:
                pass
            try:
                from tools.minimax_image import GENERATE_IMAGE_SCHEMA, GENERATE_SELF_PORTRAIT_SCHEMA
                if user_prefs.get("image_generation_enabled") and user_prefs.get("minimax_api_key"):
                    tools_list.append(GENERATE_IMAGE_SCHEMA)
                    tools_list.append(GENERATE_SELF_PORTRAIT_SCHEMA)
            except ImportError:
                pass

            # ── 第一輪：輕量工具偵測（共用 run_router_agent）──────
            # clean_history 末尾含當前 user_prompt，傳 [:-1] 避免重複追加。
            router_result = run_router_agent(
                user_prompt=user_prompt,
                tools_list=tools_list,
                router=rtr,
                temperature=0.0,
                recent_history=clean_history[-5:-1] or None,
            )
            tool_calls = router_result.tool_calls if router_result.needs_tools else []
            tool_results = []

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
                    tool_runtime_ctx = {
                        **(session_ctx or {}),
                        "visual_prompt": active_char.get("visual_prompt", ""),
                    }
                    tool_result = execute_tool_call(tc, tool_runtime_ctx)
                    tool_results.append({
                        "tool_name": tc.get("function", {}).get("name", "unknown"),
                        "result": tool_result,
                    })
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
            from core.system_logger import SystemLogger
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
                except Exception:
                    reply_text = full_res
                    new_entities = []
            else:
                reply_text = full_res
                new_entities = []

    if "tool_results" in locals():
        from tools.minimax_image import append_generated_images
        reply_text = append_generated_images(reply_text, tool_results)

    speech = None  # 翻譯移至端點層背景任務，不在此阻塞文字回覆

    # 將效能計時結果注入 retrieval_ctx
    retrieval_ctx["perf_timing"] = timer.summary()

    return reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_data, inner_thought, status_metrics, tone, speech, "", cited_uids


# ════════════════════════════════════════════════════════════
# SECTION: 編排函式選擇與結果解構
# ════════════════════════════════════════════════════════════

def _select_orchestration(user_prefs: dict):
    """根據 dual_layer_enabled 設定選擇對話編排函式。"""
    if user_prefs.get("dual_layer_enabled", False):
        from core.chat_orchestrator.coordinator import run_dual_layer_orchestration
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
