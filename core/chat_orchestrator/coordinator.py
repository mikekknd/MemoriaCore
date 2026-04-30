"""頂層協調函式 — 雙層 Agent 平行編排。

Branch A（_memory_branch）: 話題偏移偵測 → 查詢擴展 → 三軌記憶檢索 → System Prompt 組裝
Branch B（_tool_branch）  : Module A Router → Module B Middleware（工具並行執行）
兩條分支以 ThreadPoolExecutor 平行跑，完成後再餵給 Module C（Persona Agent）。
"""
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from core.prompt_manager import get_prompt_manager
from core.prompt_utils import build_user_prefix
from core.chat_orchestrator.router_agent import run_router_agent
from core.chat_orchestrator.middleware import run_middleware
from core.chat_orchestrator.persona_agent import run_persona_agent, _parse_persona_response
from core.chat_orchestrator.dataclasses import PipelineContext, SharedToolState, ToolContext
from core.chat_orchestrator.dialogue_format import (
    format_history_for_llm,
    format_dialogue_for_analysis,
    collect_cited_uids,
    snapshot_messages_for_pipeline,
)
from core.chat_orchestrator.router_hints import build_router_context_hints
from core.chat_orchestrator.group_context import (
    build_group_participants_block,
    build_llm_log_context,
    is_group_context,
)


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
    回傳 12-tuple：(reply_text, new_entities, retrieval_ctx, topic_shifted,
                    pipeline_data, inner_thought, status_metrics, tone,
                    speech, thinking_speech, cited_uids, tool_state_export)
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

    _ctx = session_ctx or {}
    user_id = _ctx.get("user_id", "default")
    character_id = _ctx.get("character_id", "default")
    persona_face = _ctx.get("persona_face", "public")
    write_visibility = persona_face
    visibility_filter = ["private", "public"] if persona_face == "private" else ["public"]
    force_group = is_group_context(_ctx)

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
    char_sys_prompt = char_mgr.get_effective_prompt(active_char, persona_face=persona_face) or storage.load_system_prompt()
    char_name = active_char.get("name", "助理")
    group_participants_block = build_group_participants_block(_ctx, char_mgr, character_id)
    log_context = build_llm_log_context(_ctx, char_mgr, character_id)

    tools_list = []
    try:
        from tools.tavily import TAVILY_SEARCH_SCHEMA
        if user_prefs.get("tavily_api_key"):
            tools_list.append(TAVILY_SEARCH_SCHEMA)
    except ImportError:
        pass
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
            expand_res = ms.expand_query(user_prompt, session_messages, rtr, task_key="expand", force_group=force_group)
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

        # 格式化記憶上下文
        core_ctx = ""
        core_debug_text = "未觸發核心認知。"
        if core_insights:
            core_ctx = f"【使用者核心資訊】：{core_insights[0]['insight']}\n"
            core_debug_text = f"觸發認知: {core_insights[0]['insight']} (Score: {core_insights[0]['score']:.3f})"

        profile_ctx = ""
        profile_debug_text = "未觸發使用者偏好。"
        if profile_matches:
            profile_lines = [f"- {pm['fact_key']}: {pm['fact_value']}" for pm in profile_matches]
            profile_ctx = "【使用者相關偏好】\n" + "\n".join(profile_lines) + "\n"
            profile_debug_text = f"觸發 {len(profile_matches)} 筆偏好: " + ", ".join(
                [f"{pm['fact_key']}={pm['fact_value']} ({pm['score']:.3f})" for pm in profile_matches])

        block_details = []
        mem_ctx = "無相關記憶。"
        if blocks:
            formatted_blocks = []
            for i, block in enumerate(blocks):
                raw_text = format_dialogue_for_analysis(block["raw_dialogues"], force_group=force_group)
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

        # System Prompt 組裝
        with t.step("System Prompt 組裝 (Prompt Assembly)"):
            static_profile = ms.get_static_profile_prompt(user_id=user_id,
                                                          visibility_filter=visibility_filter)
            static_profile_block = f"\n{static_profile}\n" if static_profile else ""

            proactive_topics_block = ms.get_proactive_topics_prompt(limit=1, user_id=user_id,
                                                                    character_id=character_id,
                                                                    visibility_filter=visibility_filter)
            if proactive_topics_block:
                proactive_topics_block = f"\n{proactive_topics_block}\n"

            pm = get_prompt_manager()
            speech_instruction = pm.get("chat_speech_instruction_no_tts").format(
                reply_rules=reply_rules,
            )

            _suffix = get_prompt_manager().get("chat_system_suffix").format(
                mem_ctx=mem_ctx,
                speech_instruction=speech_instruction,
            )

            sys_prompt = f"""{char_sys_prompt}
{group_participants_block}
{static_profile_block}
{core_ctx}{profile_ctx}{proactive_topics_block}
{_suffix}"""

            # 上下文組裝
            api_messages = [{"role": "system", "content": sys_prompt}]
            clean_history = format_history_for_llm(session_messages[-context_window:], force_group=force_group)
            # ⚠️ 關鍵：禁止移除此行。對話紀錄必須包含在 api_messages 中，否則 LLM 將失去上下文。
            # 修改 sys_prompt 組裝邏輯後，請確認此行仍存在且在 sys_prompt 之後執行。
            api_messages.extend(clean_history)

            # 注入環境上下文 + 情緒軌跡前綴到當前使用者訊息（不放 system prompt，以保留 prefix cache）
            if api_messages and api_messages[-1]["role"] == "user":
                _prefix = build_user_prefix(session_messages, user_prefs=user_prefs, session_ctx=_ctx)
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
            "cited_uids": [b.get("block_id") for b in blocks if "block_id" in b] if blocks else [],
            "context_messages_count": len(clean_history),
        }

        # JSON Schema（speech 已移至獨立翻譯 subagent，status_metrics/tone 已移除）
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

        return {
            "topic_shifted": topic_shifted,
            "pipeline_data": pipeline_data,
            "api_messages": api_messages,
            "retrieval_ctx": retrieval_ctx,
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

        # 群組接力 turn 1+：直接複用 turn 0 的工具結果，不再呼叫 router/middleware
        cached = (session_ctx or {}).get("shared_tool_state")
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

        if not tools_list:
            return {"tool_context": None, "thinking_speech": "", "timer_steps": []}

        with t.step("[並行] 意圖路由判斷 (Router Agent LLM)"):
            # session_messages 末尾已含當前 user_prompt（add_user_message 在 orchestration 前執行）。
            # run_router_agent 會自行在末尾追加 user_prompt，故此處傳入 [:-1] 排除最後一筆，避免重複。
            _recent_for_router = session_messages[-context_window:-1]
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

    if tools_list:
        # 有工具可用：兩條分支平行跑
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
    chat_schema = mem_result["chat_schema"]
    tool_context = tool_result["tool_context"]
    thinking_speech = tool_result["thinking_speech"]

    # ════════════════════════════════════════════════════════════
    # SECTION: 群組接力指令注入（不影響 expand/pipeline/profile）
    # ════════════════════════════════════════════════════════════
    followup = (session_ctx or {}).get("followup_instruction")
    if followup and api_messages:
        followup_text = get_prompt_manager().get("group_followup_user").format(
            user_prompt=followup.get("user_prompt_original", user_prompt),
            last_character_name=followup.get("last_character_name", ""),
            last_reply=followup.get("last_reply", ""),
        )
        if api_messages[-1]["role"] == "user":
            api_messages[-1] = {
                **api_messages[-1],
                "content": api_messages[-1]["content"] + "\n\n" + followup_text,
            }
        else:
            api_messages.append({"role": "user", "content": followup_text})

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
        )

    if error_result is not None:
        persona_result = error_result
    else:
        with main_timer.step("回應解析 (Response Parsing)"):
            persona_result = _parse_persona_response(raw_res, log_context=log_context)

    reply_text = persona_result.reply_text
    if tool_context:
        from tools.minimax_image import append_generated_images
        reply_text = append_generated_images(reply_text, tool_context.tool_results)
    new_entities = persona_result.new_entities
    cited_uids = retrieval_ctx.get("cited_uids", [])
    inner_thought = persona_result.inner_thought
    tone = None
    status_metrics = None
    speech = None  # 翻譯移至端點層背景任務，不在此阻塞文字回覆

    # 將效能計時結果注入 retrieval_ctx
    retrieval_ctx["perf_timing"] = main_timer.summary()

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

    return (
        reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_data,
        inner_thought, status_metrics, tone, speech, thinking_speech, cited_uids,
        tool_state_export,
    )
