"""頂層協調函式 — 雙層 Agent 平行編排。

Branch A（_memory_branch）: 話題偏移偵測 → 查詢擴展 → 三軌記憶檢索 → System Prompt 組裝
Branch B（_tool_branch）  : Module A Router → Module B Middleware（工具並行執行）
兩條分支以 ThreadPoolExecutor 平行跑，完成後再餵給 Module C（Persona Agent）。
"""
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from core.storage_manager import DEFAULT_SYSTEM_PROMPT
from core.prompt_manager import get_prompt_manager
from core.chat_orchestrator.router_agent import run_router_agent
from core.chat_orchestrator.middleware import run_middleware
from core.chat_orchestrator.persona_agent import run_persona_agent, _parse_persona_response
from core.chat_orchestrator.dataclasses import OrchestrationResult, PipelineContext, SharedExpandState, SharedToolState, ToolContext
from core.chat_orchestrator.dialogue_format import (
    collect_cited_uids,
    snapshot_messages_for_pipeline,
    strip_system_events,
)
from core.chat_orchestrator.memory_context import build_retrieved_memory_context
from core.chat_orchestrator.router_hints import build_router_context_hints
from core.chat_orchestrator.generation_context import (
    build_available_tools,
    build_chat_response_schema,
    build_final_chat_context,
    build_history_preview,
    resolve_orchestration_scope,
)
from core.chat_orchestrator.group_context import (
    build_group_participants_block,
    build_llm_log_context,
)
from core.chat_orchestrator.group_followup import inject_group_followup_instruction
from core.chat_orchestrator.live_persona import resolve_live_persona_prompt
from core.opening_penalty import get_opening_penalty_manager


def _generate_tts_speech(reply_text: str, tts_lang: str, tts_rules: str, rtr) -> str | None:
    """獨立 TTS 翻譯 subagent — 將 reply_text 以 tts_lang 翻譯，供 TTS 合成使用。
    與主對話 LLM 呼叫完全分離，避免角色扮演 LLM 對語言指令的遵守問題。
    """
    if not tts_lang or not reply_text:
        return None
    pm = get_prompt_manager()
    prompt = pm.get("tts_speech_translate").format(
        tts_lang=tts_lang,
        tts_rules=("\n- " + tts_rules) if tts_rules else "",
        reply_text=reply_text,
    )
    try:
        result = rtr.generate("translate", [{"role": "user", "content": prompt}], temperature=0.2)
        return result.strip() if result else None
    except Exception as e:
        from core.system_logger import SystemLogger
        SystemLogger.log_error("TTSTranslate", f"{type(e).__name__}: {e}")
        return None


# ════════════════════════════════════════════════════════════
# SECTION: run_dual_layer_orchestration（對外入口）
# ════════════════════════════════════════════════════════════

def run_dual_layer_orchestration(
    session_messages: list[dict],
    last_entities: list[str],
    user_prompt: str,
    user_prefs: dict,
    on_event: Callable[[dict], None] | None = None,
    session_ctx: dict | None = None,
):
    """
    異步雙層 Agent 對話編排 — 取代舊版 _run_chat_orchestration()。

    記憶檢索管線與工具路由管線平行執行，兩邊完成後再進入 Module C。
    回傳 OrchestrationResult；endpoint / group loop 若需舊解構格式，統一經由
    _unpack_orchestration_result() 轉成最新 12-slot tuple。
    session_ctx: {"user_id": str, "character_id": str, "persona_face": str,
                  "shared_tool_state": SharedToolState | None,
                  "followup_instruction": dict | None}
    """
    from api.dependencies import (
        get_memory_sys, get_storage, get_router, get_analyzer,
        get_embed_model, get_character_manager,
    )
    from api.routers.chat.timer import StepTimer

    ms = get_memory_sys()
    analyzer = get_analyzer()
    rtr = get_router()
    storage = get_storage()
    embed_model = get_embed_model()
    char_mgr = get_character_manager()

    scope = resolve_orchestration_scope(session_ctx)
    _ctx = scope.ctx
    user_id = scope.user_id
    character_id = scope.character_id
    persona_face = scope.persona_face
    write_visibility = scope.write_visibility
    visibility_filter = scope.visibility_filter
    force_group = scope.force_group
    is_group_followup_turn = bool(_ctx.get("followup_instruction"))
    cached_shared_tool_state = _ctx.get("shared_tool_state")
    shared_expand_state = _ctx.get("shared_expand_state")
    reusing_shared_tool_state = (
        isinstance(cached_shared_tool_state, SharedToolState)
        and cached_shared_tool_state.executed
    )

    shift_threshold = user_prefs.get("shift_threshold", 0.55)
    ui_alpha = user_prefs.get("ui_alpha", 0.6)
    memory_hard_base = user_prefs.get("memory_hard_base", 0.55)
    memory_threshold = user_prefs.get("memory_threshold", 0.5)
    temperature = user_prefs.get("temperature", 0.7)
    context_window = user_prefs.get("context_window", 10)

    main_timer = StepTimer()

    # active_uids 主路徑讀 message["debug_info"]["cited_uids"]，缺則 fallback 到 content regex（相容舊資料）
    active_uids = set()
    for m in session_messages[-context_window:]:
        for uid in collect_cited_uids(m):
            active_uids.add(uid)

    # ════════════════════════════════════════════════════════════
    # SECTION: Pre-fork — 載入角色資訊 + 判斷可用工具
    # ════════════════════════════════════════════════════════════
    active_char = char_mgr.get_character(character_id)
    if not active_char:
        from core.system_logger import SystemLogger
        SystemLogger.log_error("character_missing", {
            "missing_character_id": character_id,
            "session_id": _ctx.get("session_id", ""),
            "fallback": "default",
        })
        active_char = char_mgr.get_active_character("default")
    metrics = active_char.get("metrics", ["professionalism"])
    allowed_tones = active_char.get("allowed_tones", ["Neutral", "Happy", "Professional"])
    reply_rules = active_char.get("reply_rules", "Traditional Chinese. NO EMOJIS.")
    tts_rules = active_char.get("tts_rules", "")
    char_tts_lang = active_char.get("tts_language", "")
    char_sys_prompt = char_mgr.get_effective_prompt(active_char, persona_face=persona_face) or DEFAULT_SYSTEM_PROMPT
    char_sys_prompt, reply_rules = resolve_live_persona_prompt(
        character_id=character_id,
        base_prompt=char_sys_prompt,
        base_reply_rules=reply_rules,
        session_ctx=_ctx,
    )
    char_name = active_char.get("name", "助理")
    group_participants_block = build_group_participants_block(_ctx, char_mgr, character_id)
    log_context = build_llm_log_context(_ctx, char_mgr, character_id)
    opening_penalty_mgr = get_opening_penalty_manager()
    opening_penalty_plan = opening_penalty_mgr.build_plan(
        session_id=_ctx.get("session_id", ""),
        character_id=character_id,
        persona_face=persona_face,
        user_prefs=user_prefs,
    )

    tools_list = build_available_tools(user_prefs)

    # ════════════════════════════════════════════════════════════
    # SECTION: Branch A — 記憶檢索管線
    # ════════════════════════════════════════════════════════════
    def _memory_branch():
        t = StepTimer()

        # 話題偏移偵測
        with t.step("話題偏移偵測 (Topic Shift Detection)"):
            is_shift, _ = analyzer.detect_topic_shift(
                session_messages, embed_model, threshold=shift_threshold,
            )

        topic_shifted = False
        pipeline_data: PipelineContext | None = None
        if is_shift:
            topic_shifted = True
            import copy
            msgs_to_extract = snapshot_messages_for_pipeline(session_messages[:-1])
            _user_blocks = ms._get_memory_blocks(user_id, character_id, write_visibility)
            last_block = copy.deepcopy(_user_blocks[-1]) if _user_blocks else None
            pipeline_data = PipelineContext(
                msgs_to_extract=msgs_to_extract,
                last_block=last_block,
                session_ctx=_ctx,
            )

        # 查詢擴展（LLM 呼叫）
        with t.step("查詢擴展 (Query Expansion LLM)"):
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

        # 三軌記憶檢索
        with t.step("情境記憶檢索 (Memory Block Search)"):
            raw_blocks = ms.search_blocks(user_prompt, combined_keywords, 2, f_alpha, 0.5, memory_threshold, f_base,
                                          user_id=user_id, character_id=character_id,
                                          visibility_filter=visibility_filter)
            blocks = [b for b in raw_blocks if b.get("block_id") not in active_uids]

        with t.step("核心認知檢索 (Core Memory Search)"):
            core_insights = ms.search_core_memories(user_prompt, top_k=1, threshold=0.45,
                                                    user_id=user_id, character_id=character_id,
                                                    visibility_filter=visibility_filter)

        with t.step("使用者偏好檢索 (Profile Search)"):
            profile_matches = ms.search_profile_by_query(user_prompt, top_k=3, threshold=0.5,
                                                         user_id=user_id,
                                                         visibility_filter=visibility_filter)

        # System Prompt 組裝
        with t.step("System Prompt 組裝 (Prompt Assembly)"):
            static_profile = ms.get_static_profile_prompt(user_id=user_id,
                                                          visibility_filter=visibility_filter)
            proactive_topics_block = ms.get_proactive_topics_prompt(limit=1, user_id=user_id,
                                                                    character_id=character_id,
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

            # 上下文組裝
            api_messages, clean_history, sys_prompt = build_final_chat_context(
                char_sys_prompt=char_sys_prompt,
                group_participants_block=group_participants_block,
                mem_ctx=mem_ctx,
                reply_rules=reply_rules,
                session_messages=session_messages,
                context_window=context_window,
                user_prefs=user_prefs,
                session_ctx=_ctx,
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
            "cited_uids": [b.get("block_id") for b in blocks if "block_id" in b] if blocks else [],
            "context_messages_count": len(clean_history),
        }

        # JSON Schema（speech 已移至獨立翻譯 subagent，status_metrics/tone 已移除）
        chat_schema = build_chat_response_schema()

        return {
            "topic_shifted": topic_shifted,
            "pipeline_data": pipeline_data,
            "api_messages": api_messages,
            "retrieval_ctx": retrieval_ctx,
            "memory_context_prompt": mem_ctx,
            "chat_schema": chat_schema,
            "timer_steps": t._steps,
        }

    # ════════════════════════════════════════════════════════════
    # SECTION: Branch B — 工具路由管線
    # ════════════════════════════════════════════════════════════
    def _tool_branch():
        t = StepTimer()
        thinking = ""
        ctx = None

        # 群組接力 turn 1+：直接複用 turn 0 的工具結果，不再呼叫 router/middleware。
        # 即使 turn 0 沒有工具結果，接力回合也不應重新路由；否則原始 user_prompt
        # 會同時出現在已處理歷史與當前訊息，污染意圖判斷。
        cached = cached_shared_tool_state
        if isinstance(cached, SharedToolState) and cached.executed:
            return {
                "tool_context": ToolContext(
                    tool_results=list(cached.tool_results),
                    tool_results_formatted=cached.tool_results_formatted,
                    thinking_speech_sent=cached.thinking_speech_sent,
                ),
                "thinking_speech": "",  # 不再二次推語音
                "timer_steps": [],
            }

        if is_group_followup_turn:
            return {"tool_context": None, "thinking_speech": "", "timer_steps": []}

        if not tools_list:
            return {"tool_context": None, "thinking_speech": "", "timer_steps": []}

        with t.step("[並行] 意圖路由判斷 (Router Agent LLM)"):
            # session_messages 末尾已含當前 user_prompt（add_user_message 在 orchestration 前執行）。
            # run_router_agent 會自行在末尾追加 user_prompt，故此處傳入 [:-1] 排除最後一筆，避免重複。
            # 同時剝掉 system_event：router 統一收 raw 訊息（user/assistant），不要看到 roster 變更事件。
            _recent_for_router = strip_system_events(session_messages[-context_window:-1])
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
                session_ctx=_ctx,
                profile_facts=_profile_facts,
            )
            router_result = run_router_agent(
                user_prompt=user_prompt,
                tools_list=tools_list,
                router=rtr,
                temperature=temperature,
                recent_history=_recent_for_router if _recent_for_router else None,
                context_hints=_hints if _hints else None,
            )

        if router_result.needs_tools:
            # thinking_speech 由模板生成，不再依賴 Router LLM 輸出文字
            router_result.thinking_speech = get_prompt_manager().get("tool_thinking_speech").format(
                char_name=char_name,
            )
            with t.step("[並行] 過渡語音推播 (Thinking Speech Dispatch)"):
                if on_event:
                    on_event({"type": "thinking_speech", "content": router_result.thinking_speech})

            with t.step("[並行] 工具並行執行 (Tool Execution)"):
                ctx = run_middleware(
                    router_result=router_result,
                    on_thinking_speech=None,
                    on_tool_status=on_event,
                    runtime_context={**_ctx, "visual_prompt": active_char.get("visual_prompt", "")},
                )
                thinking = ctx.thinking_speech_sent

        return {
            "tool_context": ctx,
            "thinking_speech": thinking,
            "timer_steps": t._steps,
        }

    # ════════════════════════════════════════════════════════════
    # SECTION: 平行執行兩條分支
    # ════════════════════════════════════════════════════════════
    parallel_start = time.perf_counter()

    if tools_list or reusing_shared_tool_state:
        # 有工具可用或已有共享工具結果：兩條分支平行跑
        with ThreadPoolExecutor(max_workers=2) as pool:
            mem_future = pool.submit(_memory_branch)
            tool_future = pool.submit(_tool_branch)
            mem_result = mem_future.result()
            tool_result = tool_future.result()
    else:
        # 無可用工具：只跑記憶分支
        mem_result = _memory_branch()
        tool_result = {"tool_context": None, "thinking_speech": "", "timer_steps": []}

    parallel_ms = round((time.perf_counter() - parallel_start) * 1000, 1)

    # 合併計時步驟（標記平行區段的實際牆鐘時間）
    main_timer._steps.append({"name": "⏱ 並行區段總牆鐘時間", "ms": parallel_ms})
    main_timer._steps.extend(mem_result["timer_steps"])
    main_timer._steps.extend(tool_result["timer_steps"])

    # 解構分支結果
    topic_shifted = mem_result["topic_shifted"]
    pipeline_data = mem_result["pipeline_data"]
    api_messages = mem_result["api_messages"]
    retrieval_ctx = mem_result["retrieval_ctx"]
    memory_context_prompt = mem_result.get("memory_context_prompt", "")
    chat_schema = mem_result["chat_schema"]
    tool_context = tool_result["tool_context"]
    thinking_speech = tool_result["thinking_speech"]

    # ════════════════════════════════════════════════════════════
    # SECTION: 群組接力指令注入（不影響 expand/pipeline/profile）
    # ════════════════════════════════════════════════════════════
    inject_group_followup_instruction(
        api_messages,
        (session_ctx or {}).get("followup_instruction"),
        user_prompt,
        session_messages=session_messages,
        user_prefs=user_prefs,
        session_ctx=session_ctx,
        memory_context=memory_context_prompt,
    )

    # ════════════════════════════════════════════════════════════
    # SECTION: Module C — 角色渲染（等兩條分支都完成後才執行）
    # ════════════════════════════════════════════════════════════
    with main_timer.step("角色渲染生成 (Persona Agent LLM)"):
        raw_res, error_result = run_persona_agent(
            user_prompt=user_prompt,
            api_messages=api_messages,
            tool_context=tool_context,
            chat_schema=chat_schema,
            router=rtr,
            temperature=temperature,
            log_context=log_context,
            opening_penalty_plan=opening_penalty_plan,
        )

    if error_result is not None:
        persona_result = error_result
    else:
        with main_timer.step("回應解析 (Response Parsing)"):
            persona_result = _parse_persona_response(raw_res, log_context=log_context)

    reply_text = persona_result.reply_text
    if tool_context:
        from tools.minimax_image import append_generated_images, strip_generated_images
        if reusing_shared_tool_state:
            reply_text = strip_generated_images(reply_text, tool_context.tool_results)
        else:
            reply_text = append_generated_images(reply_text, tool_context.tool_results)
    if opening_penalty_plan.enabled and opening_penalty_mgr.extract_reply_from_response(raw_res):
        opening_penalty_mgr.record_reply(
            session_id=_ctx.get("session_id", ""),
            character_id=character_id,
            persona_face=persona_face,
            reply_text=persona_result.reply_text,
            enabled=True,
        )
    new_entities = persona_result.new_entities
    cited_uids = retrieval_ctx.get("cited_uids", [])
    inner_thought = persona_result.inner_thought
    tone = None
    status_metrics = None
    speech = None  # 翻譯移至端點層背景任務，不在此阻塞文字回覆

    # 將效能計時結果注入 retrieval_ctx
    retrieval_ctx["perf_timing"] = main_timer.summary()
    _llm_trace = log_context.get("_last_llm_call") if isinstance(log_context, dict) else None
    if _ctx.get("expose_llm_trace") and isinstance(_llm_trace, dict) and _llm_trace.get("task_key") == "chat":
        retrieval_ctx["llm_trace"] = dict(_llm_trace)

    # 工具狀態 export：給群組接力 turn 1+ 復用，避免重複呼叫工具
    if tool_context is not None:
        tool_state_export = SharedToolState(
            tool_results=list(tool_context.tool_results),
            tool_results_formatted=tool_context.tool_results_formatted,
            thinking_speech_sent=thinking_speech,
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
        thinking_speech=thinking_speech,
        cited_uids=cited_uids,
        tool_state_export=tool_state_export,
    )
