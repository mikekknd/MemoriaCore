"""單層對話編排：話題偏移偵測 → 雙軌檢索 → LLM 生成。

雙層編排（dual-layer agent）見 core/chat_orchestrator/。
依 user_prefs["dual_layer_enabled"] 由 _select_orchestration() 決定。
"""
import json

from api.dependencies import (
    get_memory_sys, get_storage, get_router, get_analyzer,
    get_embed_model, get_character_manager,
)
from core.storage_manager import DEFAULT_SYSTEM_PROMPT
from core.chat_orchestrator.router_agent import run_router_agent
from core.chat_orchestrator.dialogue_format import (
    collect_cited_uids,
    snapshot_messages_for_pipeline,
    strip_system_events,
)
from core.chat_orchestrator.memory_context import build_retrieved_memory_context
from core.chat_orchestrator.router_hints import build_router_context_hints
from core.chat_orchestrator.dataclasses import OrchestrationResult, SharedExpandState, SharedToolState
from core.chat_orchestrator.generation_context import (
    build_available_tools,
    build_chat_response_schema,
    build_final_chat_context,
    build_history_preview,
    resolve_orchestration_scope,
)
from core.xml_prompt import format_tool_context_xml, format_tool_results_xml
from core.chat_orchestrator.group_context import (
    build_group_participants_block,
    build_llm_log_context,
)
from core.chat_orchestrator.group_followup import inject_group_followup_instruction
from core.chat_orchestrator.persona_agent import _sanitize_group_reply
from core.opening_penalty import get_opening_penalty_manager
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
    回傳 OrchestrationResult；呼叫端如需舊 endpoint 解構格式，統一經由 _unpack_orchestration_result()。
    pipeline_data: 若話題偏移，回傳 PipelineContext 供呼叫端發起背景任務；否則為 None。
    on_event: 可選的 callback，用於即時推送中間狀態（如工具呼叫通知）給前端。
    session_ctx: {"user_id": str, "character_id": str, "persona_face": str,
                  "shared_tool_state": SharedToolState | None,
                  "followup_instruction": dict | None}
    """
    ms = get_memory_sys()
    analyzer = get_analyzer()
    rtr = get_router()
    storage = get_storage()
    embed_model = get_embed_model()

    scope = resolve_orchestration_scope(session_ctx)
    ctx = scope.ctx
    user_id = scope.user_id
    character_id = scope.character_id
    persona_face = scope.persona_face
    write_visibility = scope.write_visibility
    visibility_filter = scope.visibility_filter
    force_group = scope.force_group
    is_group_followup_turn = bool(ctx.get("followup_instruction"))
    cached_shared_tool_state = ctx.get("shared_tool_state")
    shared_expand_state = ctx.get("shared_expand_state")
    reusing_shared_tool_state = (
        isinstance(cached_shared_tool_state, SharedToolState)
        and cached_shared_tool_state.executed
    )

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
    # 主路徑：debug_info["cited_uids"]；缺值時 fallback 到 content regex（相容舊資料）
    active_uids = set()
    for m in session_messages[-4:]:
        for uid in collect_cited_uids(m):
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
        msgs_to_extract = snapshot_messages_for_pipeline(session_messages[:-1])
        _user_blocks = ms._get_memory_blocks(user_id, character_id, write_visibility)
        last_block = copy.deepcopy(_user_blocks[-1]) if _user_blocks else None
        pipeline_data = PipelineContext(
            msgs_to_extract=msgs_to_extract,
            last_block=last_block,
            session_ctx=session_ctx or {},
        )

    # ─── 雙軌檢索 ───
    with timer.step("查詢擴展 (Query Expansion LLM)"):
        if isinstance(shared_expand_state, SharedExpandState) and shared_expand_state.executed:
            expand_res = dict(shared_expand_state.expand_result or {})
        else:
            expand_res = ms.expand_query(user_prompt, session_messages, rtr, task_key="expand", force_group=force_group)
            if isinstance(shared_expand_state, SharedExpandState):
                shared_expand_state.expand_result = dict(expand_res)
                shared_expand_state.executed = True
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

    with timer.step("使用者偏好檢索 (Profile Search)"):
        profile_matches = ms.search_profile_by_query(user_prompt, top_k=3, threshold=0.5,
                                                     user_id=user_id,
                                                     visibility_filter=visibility_filter)

    static_profile = ms.get_static_profile_prompt(user_id=user_id, visibility_filter=visibility_filter)
    proactive_topics_block = ms.get_proactive_topics_prompt(limit=1, user_id=user_id, character_id=character_id,
                                                             visibility_filter=visibility_filter)
    retrieved_memory = build_retrieved_memory_context(
        static_profile=static_profile,
        core_insights=core_insights,
        profile_matches=profile_matches,
        proactive_topics=proactive_topics_block,
        blocks=blocks,
        force_group=force_group,
    )
    mem_ctx = retrieved_memory.prompt
    block_details = retrieved_memory.block_details
    core_debug_text = retrieved_memory.core_debug_text
    profile_debug_text = retrieved_memory.profile_debug_text

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
    char_sys_prompt = char_mgr.get_effective_prompt(active_char, persona_face=persona_face) or DEFAULT_SYSTEM_PROMPT
    group_participants_block = build_group_participants_block(ctx, char_mgr, character_id)
    log_context = build_llm_log_context(ctx, char_mgr, character_id)
    opening_penalty_mgr = get_opening_penalty_manager()
    opening_penalty_plan = opening_penalty_mgr.build_plan(
        session_id=ctx.get("session_id", ""),
        character_id=character_id,
        persona_face=persona_face,
        user_prefs=user_prefs,
    )

    # ⚠️ 上下文組裝必須在 debug 預覽之前完成（修正原本 clean_history 引用順序錯誤）。
    with timer.step("上下文組裝 (Context Assembly)"):
        api_messages, clean_history, sys_prompt = build_final_chat_context(
            char_sys_prompt=char_sys_prompt,
            group_participants_block=group_participants_block,
            mem_ctx=mem_ctx,
            reply_rules=reply_rules,
            session_messages=session_messages,
            context_window=context_window,
            user_prefs=user_prefs,
            session_ctx=ctx,
            force_group=force_group,
        )

    # 組裝 debug 用的完整 prompt 預覽（sys_prompt + 近期對話紀錄）
    _history_preview = build_history_preview(clean_history)

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
    chat_schema = build_chat_response_schema()

    with timer.step("LLM 對話生成 (Chat Generation LLM)"):
        try:
            from tools.tavily import execute_tool_call
            tools_list = build_available_tools(user_prefs)

            # 群組接力 turn 1+：直接複用 turn 0 的工具結果，不再呼叫 router/execute_tool_call。
            # 即使 turn 0 沒有工具結果，接力回合也不應重新路由；否則原始 user_prompt
            # 會同時出現在已處理歷史與當前訊息，污染意圖判斷。
            cached_state = cached_shared_tool_state
            tool_calls = []
            tool_results = []
            if isinstance(cached_state, SharedToolState) and cached_state.executed:
                tool_results = list(cached_state.tool_results)
                if cached_state.tool_results_formatted and api_messages and api_messages[-1]["role"] == "user":
                    tool_notice = "\n\n" + format_tool_context_xml(
                        cached_state.tool_results_formatted,
                        source="shared_tool_state",
                    )
                    api_messages[-1] = {
                        **api_messages[-1],
                        "content": api_messages[-1]["content"] + tool_notice,
                    }
            elif not is_group_followup_turn:
                # ── 第一輪：輕量工具偵測（共用 run_router_agent）──────
                # clean_history 末尾含當前 user_prompt，傳 [:-1] 避免重複追加。
                try:
                    _profile_facts = (
                        storage.load_all_profiles(ms.db_path, user_id=user_id)
                        if getattr(ms, "db_path", None) else []
                    )
                except Exception:
                    _profile_facts = []
                _hints = build_router_context_hints(
                    session_messages=session_messages,
                    user_prefs=user_prefs,
                    session_ctx=ctx,
                    profile_facts=_profile_facts,
                )
                _recent_for_router = strip_system_events(session_messages[-context_window:][-5:-1])
                router_result = run_router_agent(
                    user_prompt=user_prompt,
                    tools_list=tools_list,
                    router=rtr,
                    temperature=0.0,
                    recent_history=_recent_for_router or None,
                    context_hints=_hints if _hints else None,
                )
                tool_calls = router_result.tool_calls if router_result.needs_tools else []

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

            # 群組接力指令（若有）：追加為最後一則 user control，僅供本次 LLM 使用。
            # 不寫進 generation_messages / session_messages，故不影響 expand/pipeline/profile。
            inject_group_followup_instruction(
                api_messages,
                (session_ctx or {}).get("followup_instruction"),
                user_prompt,
                session_messages=session_messages,
                user_prefs=user_prefs,
                session_ctx=session_ctx,
            )

            if opening_penalty_plan.prompt_block:
                opening_penalty_mgr.apply_instruction_to_messages(
                    api_messages,
                    opening_penalty_plan.prompt_block,
                )

            # ── 第二輪：完整人格上下文 + schema 強制格式化 ────
            # 無論是否使用工具，都用完整 api_messages 生成最終回應。
            full_res = rtr.generate(
                "chat", api_messages, temperature=temperature,
                response_format=chat_schema,
                log_context=log_context,
                logit_bias=opening_penalty_plan.logit_bias,
            )
            if opening_penalty_plan.blocked_openings:
                reply_for_check = opening_penalty_mgr.extract_reply_from_response(full_res)
                violated = opening_penalty_mgr.find_violation(reply_for_check, opening_penalty_plan)
                if violated:
                    retry_messages = list(api_messages)
                    retry_instruction = opening_penalty_mgr.build_retry_instruction(
                        opening_penalty_plan,
                        violated_opening=violated,
                    )
                    opening_penalty_mgr.apply_instruction_to_messages(
                        retry_messages,
                        retry_instruction,
                    )
                    from core.system_logger import SystemLogger
                    SystemLogger.log_error(
                        "OpeningPenalty",
                        f"reply 開場仍命中短期抑制片段，重試一次: {violated!r}",
                        details={
                            "blocked_openings": list(opening_penalty_plan.blocked_openings),
                            "log_context": log_context or {},
                        },
                    )
                    full_res = rtr.generate(
                        "chat", retry_messages, temperature=max(temperature * 0.5, 0.1),
                        response_format=chat_schema,
                        log_context=log_context,
                        logit_bias=opening_penalty_plan.logit_bias,
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

    parsed_reply_valid = False
    if full_res is not None:
        with timer.step("回應解析 (Response Parsing)"):
            _start = full_res.find('{')
            if _start != -1:
                try:
                    parsed, _ = json.JSONDecoder().raw_decode(full_res, _start)
                    reply_text = _sanitize_group_reply(parsed.get("reply", "解析錯誤"), log_context)
                    parsed_reply_valid = isinstance(parsed.get("reply"), str)
                    new_entities = parsed.get("extracted_entities", [])
                    inner_thought = parsed.get("internal_thought")
                except Exception:
                    reply_text = _sanitize_group_reply(full_res, log_context)
                    new_entities = []
            else:
                reply_text = _sanitize_group_reply(full_res, log_context)
                new_entities = []

    if parsed_reply_valid and opening_penalty_plan.enabled:
        opening_penalty_mgr.record_reply(
            session_id=ctx.get("session_id", ""),
            character_id=character_id,
            persona_face=persona_face,
            reply_text=reply_text,
            enabled=True,
        )

    if "tool_results" in locals():
        from tools.minimax_image import append_generated_images, strip_generated_images
        if reusing_shared_tool_state:
            reply_text = strip_generated_images(reply_text, tool_results)
        else:
            reply_text = append_generated_images(reply_text, tool_results)

    speech = None  # 翻譯移至端點層背景任務，不在此阻塞文字回覆

    # 將效能計時結果注入 retrieval_ctx
    retrieval_ctx["perf_timing"] = timer.summary()
    _llm_trace = log_context.get("_last_llm_call") if isinstance(log_context, dict) else None
    if ctx.get("expose_llm_trace") and isinstance(_llm_trace, dict) and _llm_trace.get("task_key") == "chat":
        retrieval_ctx["llm_trace"] = dict(_llm_trace)

    # 工具狀態 export：給群組接力 turn 1+ 復用
    _has_tool_results = "tool_results" in locals() and bool(tool_results)
    if _has_tool_results:
        # 重新格式化以供 cached 復用（與 middleware 的 ToolContext.tool_results_formatted 風格一致）
        _formatted_text = format_tool_results_xml(tool_results)
        tool_state_export = SharedToolState(
            tool_results=list(tool_results),
            tool_results_formatted=_formatted_text,
            thinking_speech_sent="",
            executed=True,
        )
    else:
        tool_state_export = SharedToolState(executed=False)

    return OrchestrationResult(
        reply_text=reply_text,
        new_entities=new_entities,
        retrieval_context=retrieval_ctx,
        topic_shifted=topic_shifted,
        pipeline_data=pipeline_data,
        inner_thought=inner_thought,
        status_metrics=status_metrics,
        tone=tone,
        speech=speech,
        thinking_speech="",
        cited_uids=cited_uids,
        tool_state_export=tool_state_export,
    )


# ════════════════════════════════════════════════════════════
# SECTION: 編排函式選擇與結果解構
# ════════════════════════════════════════════════════════════

def _select_orchestration(user_prefs: dict):
    """根據 dual_layer_enabled 設定選擇對話編排函式。"""
    if user_prefs.get("dual_layer_enabled", False):
        from core.chat_orchestrator.coordinator import run_dual_layer_orchestration
        return run_dual_layer_orchestration
    return _run_chat_orchestration


def _unpack_orchestration_result(result):
    """統一解構編排結果。

    OrchestrationResult / latest 12-slot tuple：
        (reply, entities, ctx, shifted, pipeline, thought, metrics, tone,
         speech, thinking_speech, cited_uids, tool_state_export)
    """
    if isinstance(result, OrchestrationResult):
        return result.as_tuple()
    if isinstance(result, tuple) and len(result) == 12:
        return result
    try:
        item_count = len(result)
    except TypeError:
        item_count = f"non-sized {type(result).__name__}"
    raise ValueError(f"orchestration result must be OrchestrationResult or a 12-slot tuple, got {item_count} items")
