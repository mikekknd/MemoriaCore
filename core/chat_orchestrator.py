"""
異步雙層 Agent 語音互動架構 — 核心模組。

將對話編排拆分為三個獨立模組：
  Module A: Router Agent（意圖路由層）
  Module B: Async Middleware（非同步中介層）
  Module C: Persona Synthesis Agent（角色渲染層）
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

from core.system_logger import SystemLogger
from core.prompt_manager import get_prompt_manager


# ── 資料結構 ──────────────────────────────────────────────

@dataclass
class RouterResult:
    """Module A 的輸出。"""
    needs_tools: bool
    tool_calls: list[dict] = field(default_factory=list)
    thinking_speech: str = ""


@dataclass
class ToolContext:
    """Module B 的輸出。"""
    tool_results: list[dict] = field(default_factory=list)   # [{"tool_name": str, "result": str}]
    tool_results_formatted: str = ""                          # 格式化文字，注入 Module C
    thinking_speech_sent: str = ""                            # 已推播給前端的過渡語


@dataclass
class PersonaResult:
    """Module C 的輸出。"""
    reply_text: str = ""
    new_entities: list[str] = field(default_factory=list)
    inner_thought: str | None = None
    status_metrics: dict | None = None
    tone: str | None = None
    speech: str | None = None


# ── Module A: Router Agent ────────────────────────────────

def _get_router_prompt():
    return get_prompt_manager().get("router_system")

# Dummy Tool Schema — 利用 function calling 的多選機制穩定意圖判定
DIRECT_CHAT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "direct_chat",
        "description": "當使用者只是在日常閒聊、表達主觀意見、分享心情或生活狀態，沒有明確詢問客觀知識或即時數據時，呼叫此工具將控制權直接交還角色進行對話。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}


def run_router_agent(
    user_prompt: str,
    char_hint: str,
    tools_list: list[dict],
    router,
    temperature: float = 0.7,
    recent_history: list[dict] | None = None,
) -> RouterResult:
    """
    Module A — 輕量 LLM 判斷是否需要工具，並產生過渡語音。

    Args:
        user_prompt: 使用者的原始輸入。
        char_hint: 角色語氣的一行描述（例如「傲嬌女僕，語氣帶點不耐煩但其實很認真」）。
        tools_list: 所有可用的 Tool Schema 列表（不含 direct_chat，會自動注入）。
        router: LLMRouter 實例。
        temperature: LLM 溫度參數。
        recent_history: 最近的對話歷史（用於提供上下文判斷意圖）。

    Returns:
        RouterResult — needs_tools / tool_calls / thinking_speech。
    """
    # 注入 dummy tool，讓 LLM 在「真工具」與「純聊天」之間做多選
    augmented_tools = tools_list + [DIRECT_CHAT_SCHEMA]

    sys_prompt = _get_router_prompt().format(char_hint=char_hint)
    messages = [{"role": "system", "content": sys_prompt}]

    # 注入最近對話歷史，讓 Router Agent 有上下文可判斷意圖
    if recent_history:
        for m in recent_history[-6:]:
            messages.append({"role": m["role"], "content": m["content"]})

    messages.append({"role": "user", "content": user_prompt})

    try:
        content, tool_calls = router.generate_with_tools(
            "router", messages, tools=augmented_tools, temperature=temperature,
        )
    except Exception as e:
        SystemLogger.log_error("RouterAgent", f"{type(e).__name__}: {e}")
        return RouterResult(needs_tools=False)

    if tool_calls:
        # 過濾掉 direct_chat — 它只是路由信號，不是真正的工具
        real_tool_calls = [
            tc for tc in tool_calls
            if tc.get("function", {}).get("name") != "direct_chat"
        ]
        if real_tool_calls:
            thinking_speech = (content or "").strip()
            return RouterResult(
                needs_tools=True,
                tool_calls=real_tool_calls,
                thinking_speech=thinking_speech,
            )

    return RouterResult(needs_tools=False)


# ── Module B: Async Middleware ────────────────────────────

def run_middleware(
    router_result: RouterResult,
    on_thinking_speech: Callable[[str], None] | None = None,
    on_tool_status: Callable[[dict], None] | None = None,
) -> ToolContext:
    """
    Module B — 同步中介層：立即推播過渡語音，並行執行工具呼叫。

    Args:
        router_result: Module A 的輸出。
        on_thinking_speech: 回呼，立即將過渡語推播給前端 TTS。
        on_tool_status: 回呼，推送工具呼叫狀態事件。

    Returns:
        ToolContext — 工具結果 + 已推播的過渡語。
    """
    from tools.tavily import execute_tool_call

    thinking_speech = router_result.thinking_speech

    # 1) 立即推播過渡語音
    if thinking_speech and on_thinking_speech:
        on_thinking_speech(thinking_speech)

    # 2) 通知前端每個工具的呼叫狀態
    tool_calls = router_result.tool_calls
    if on_tool_status:
        for tc in tool_calls:
            tool_name = tc.get("function", {}).get("name", "unknown")
            query = tc.get("function", {}).get("arguments", {}).get("query", "")
            on_tool_status({
                "type": "tool_status",
                "action": "calling",
                "tool_name": tool_name,
                "message": f"正在搜尋：{query}" if query else f"正在呼叫工具：{tool_name}",
            })

    # 3) ThreadPoolExecutor 並行執行所有工具
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(tool_calls) or 1) as executor:
        future_to_tc = {
            executor.submit(execute_tool_call, tc): tc
            for tc in tool_calls
        }
        for future in as_completed(future_to_tc):
            tc = future_to_tc[future]
            tool_name = tc.get("function", {}).get("name", "unknown")
            try:
                result_text = future.result()
            except Exception as e:
                result_text = json.dumps({"error": f"工具執行失敗: {e}"}, ensure_ascii=False)
            results.append({"tool_name": tool_name, "result": result_text})

    # 4) 通知前端工具執行完成
    if on_tool_status:
        on_tool_status({
            "type": "tool_status",
            "action": "complete",
            "message": "搜尋完成，正在整理回覆...",
        })

    # 5) 格式化工具結果
    formatted_parts = []
    for r in results:
        formatted_parts.append(f"【{r['tool_name']} 查詢結果】\n{r['result']}")
    formatted_text = (
        "[系統自動查詢結果 — 以下資料由外部工具回傳，非使用者輸入]\n"
        + "\n".join(formatted_parts)
        + "\n[查詢結果結束]"
    )

    return ToolContext(
        tool_results=results,
        tool_results_formatted=formatted_text,
        thinking_speech_sent=thinking_speech,
    )


# ── Module C: Persona Synthesis Agent ─────────────────────

def run_persona_agent(
    user_prompt: str,
    api_messages: list[dict],
    tool_context: ToolContext | None,
    chat_schema: dict,
    router,
    temperature: float = 0.7,
) -> tuple[str | None, PersonaResult | None]:
    """
    Module C — 角色渲染層：載入完整角色設定，生成結構化 JSON 回覆。

    Returns:
        (raw_llm_response, error_result) — 成功時回傳 (str, None)，
        失敗時回傳 (None, PersonaResult)。呼叫端負責解析及計時。
    """
    # 組裝最終 messages：注入 thinking_speech 和工具結果
    final_messages = list(api_messages)  # shallow copy

    if tool_context:
        # 在最後一條 user message 之前注入 assistant 的 thinking_speech
        # 讓 Module C 知道它已經說過等待語
        if tool_context.thinking_speech_sent:
            # 找到最後一條 user message 的位置
            insert_idx = len(final_messages) - 1
            for i in range(len(final_messages) - 1, -1, -1):
                if final_messages[i].get("role") == "user":
                    insert_idx = i
                    break
            final_messages.insert(insert_idx, {
                "role": "assistant",
                "content": tool_context.thinking_speech_sent,
            })

        # 在 user message 之後追加工具結果（獨立的 user 訊息，明確標記為系統工具回傳）
        final_messages.append({
            "role": "user",
            "content": (
                f"[系統通知：以下是根據你的工具查詢自動回傳的外部數據，請依據此數據回答使用者的問題]\n"
                f"{tool_context.tool_results_formatted}"
            ),
        })

    # 呼叫 LLM — 不帶 tools，帶 response_format
    try:
        full_res = router.generate(
            "chat", final_messages, temperature=temperature,
            response_format=chat_schema,
        )
    except Exception as e:
        SystemLogger.log_error("PersonaAgent", f"{type(e).__name__}: {e}")
        return None, PersonaResult(reply_text=f"生成錯誤: {e}")

    # 回傳原始 LLM 回應，讓呼叫端可以分別計時解析步驟
    return full_res, None


def _parse_persona_response(full_res: str | None) -> PersonaResult:
    """從 LLM 原始回應中解析結構化 JSON。"""
    if not full_res:
        return PersonaResult(reply_text="（無回應）")

    start = full_res.find('{')
    if start == -1:
        return PersonaResult(reply_text=full_res)

    try:
        parsed, _ = json.JSONDecoder().raw_decode(full_res, start)
        return PersonaResult(
            reply_text=parsed.get("reply", "解析錯誤"),
            new_entities=parsed.get("extracted_entities", []),
            inner_thought=parsed.get("internal_thought"),
            status_metrics=parsed.get("status_metrics"),
            tone=parsed.get("tone"),
            speech=parsed.get("speech"),
        )
    except Exception:
        return PersonaResult(reply_text=full_res)


# ── 頂層協調函式 ──────────────────────────────────────────

def run_dual_layer_orchestration(
    session_messages: list[dict],
    last_entities: list[str],
    user_prompt: str,
    user_prefs: dict,
    on_event: Callable[[dict], None] | None = None,
):
    """
    異步雙層 Agent 對話編排 — 取代 _run_chat_orchestration()。

    記憶檢索管線與工具路由管線平行執行，兩邊完成後再進入 Module C。

    維持與原函式相同的參數簽名。
    回傳 10-tuple：原 9 元素 + thinking_speech。
    """
    from api.dependencies import (
        get_memory_sys, get_storage, get_router, get_analyzer,
        get_embed_model, get_character_manager,
    )
    from api.routers.chat_ws import StepTimer

    ms = get_memory_sys()
    analyzer = get_analyzer()
    rtr = get_router()
    storage = get_storage()
    embed_model = get_embed_model()
    char_mgr = get_character_manager()

    shift_threshold = user_prefs.get("shift_threshold", 0.55)
    ui_alpha = user_prefs.get("ui_alpha", 0.6)
    memory_hard_base = user_prefs.get("memory_hard_base", 0.55)
    memory_threshold = user_prefs.get("memory_threshold", 0.5)
    temperature = user_prefs.get("temperature", 0.7)
    context_window = user_prefs.get("context_window", 10)

    main_timer = StepTimer()

    import re
    active_uids = set()
    for m in session_messages[-context_window:]:
        matches = re.findall(r'\[Ref:\s*([^\]]+)\]', m.get("content", ""))
        for uid in matches:
            active_uids.add(uid)

    # ─── Pre-fork：載入角色資訊 + 判斷可用工具（兩條分支都需要） ───
    active_char_id = user_prefs.get("active_character_id", "default")
    active_char = char_mgr.get_active_character(active_char_id)
    metrics = active_char.get("metrics", ["professionalism"])
    allowed_tones = active_char.get("allowed_tones", ["Neutral", "Happy", "Professional"])
    reply_rules = active_char.get("reply_rules", "Traditional Chinese. NO EMOJIS.")
    tts_rules = active_char.get("tts_rules", "")
    char_tts_lang = active_char.get("tts_language", "")
    char_sys_prompt = char_mgr.get_effective_prompt(active_char) or storage.load_system_prompt()
    char_name = active_char.get("name", "助理")

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

    # ═══════════════════════════════════════════════════════════
    # Branch A：記憶檢索管線（偵測 → 擴展 → 三軌檢索 → Prompt 組裝）
    # ═══════════════════════════════════════════════════════════
    def _memory_branch():
        t = StepTimer()

        # 話題偏移偵測
        with t.step("話題偏移偵測 (Topic Shift Detection)"):
            is_shift, _ = analyzer.detect_topic_shift(
                session_messages, embed_model, threshold=shift_threshold,
            )

        topic_shifted = False
        pipeline_data = None
        if is_shift:
            topic_shifted = True
            import copy
            msgs_to_extract = [{"role": m["role"], "content": m["content"]}
                               for m in session_messages[:-1]]
            last_block = copy.deepcopy(ms.memory_blocks[-1]) if ms.memory_blocks else None
            pipeline_data = (msgs_to_extract, last_block)

        # 查詢擴展（LLM 呼叫）
        with t.step("查詢擴展 (Query Expansion LLM)"):
            expand_res = ms.expand_query(user_prompt, session_messages, rtr, task_key="expand")
        inherited_str = " ".join(last_entities)
        combined_keywords = f"{expand_res['expanded_keywords']} {inherited_str}".strip()

        f_alpha = ui_alpha
        f_base = max(0.50, memory_hard_base - (0.05 * expand_res["entity_confidence"]))

        # 三軌記憶檢索
        with t.step("情境記憶檢索 (Memory Block Search)"):
            raw_blocks = ms.search_blocks(user_prompt, combined_keywords, 2, f_alpha, 0.5, memory_threshold, f_base)
            blocks = [b for b in raw_blocks if b.get("block_id") not in active_uids]

        with t.step("核心認知檢索 (Core Memory Search)"):
            core_insights = ms.search_core_memories(user_prompt, top_k=1, threshold=0.45)

        with t.step("使用者偏好檢索 (Profile Search)"):
            profile_matches = ms.search_profile_by_query(user_prompt, top_k=3, threshold=0.5)

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

        # System Prompt 組裝
        with t.step("System Prompt 組裝 (Prompt Assembly)"):
            static_profile = ms.get_static_profile_prompt()
            static_profile_block = f"\n{static_profile}\n" if static_profile else ""

            proactive_topics_block = ms.get_proactive_topics_prompt(limit=1)
            if proactive_topics_block:
                proactive_topics_block = f"\n{proactive_topics_block}\n"

            weather_block = ""
            try:
                from tools.weather_cache import WeatherCache
                weather_summary = WeatherCache().get_current_slot()
                if weather_summary:
                    weather_block = f"\n【即時天氣資訊】\n{weather_summary}\n"
            except Exception:
                pass

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

            # 上下文組裝
            api_messages = [{"role": "system", "content": sys_prompt}]
            clean_history = [{"role": m["role"], "content": m["content"]} for m in session_messages[-context_window:]]
            # ⚠️ 關鍵：禁止移除此行。對話紀錄必須包含在 api_messages 中，否則 LLM 將失去上下文。
            # 修改 sys_prompt 組裝邏輯後，請確認此行仍存在且在 sys_prompt 之後執行。
            api_messages.extend(clean_history)

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

        # JSON Schema
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

        return {
            "topic_shifted": topic_shifted,
            "pipeline_data": pipeline_data,
            "api_messages": api_messages,
            "retrieval_ctx": retrieval_ctx,
            "chat_schema": chat_schema,
            "timer_steps": t._steps,
        }

    # ═══════════════════════════════════════════════════════════
    # Branch B：工具路由管線（Module A → Module B）
    # ═══════════════════════════════════════════════════════════
    def _tool_branch():
        t = StepTimer()
        thinking = ""
        ctx = None

        if not tools_list:
            return {"tool_context": None, "thinking_speech": "", "timer_steps": []}

        char_hint = f"{char_name}（{reply_rules}）"

        with t.step("[並行] 意圖路由判斷 (Router Agent LLM)"):
            # session_messages 末尾已含當前 user_prompt（add_user_message 在 orchestration 前執行）。
            # run_router_agent 會自行在末尾追加 user_prompt，故此處傳入 [:-1] 排除最後一筆，避免重複。
            _recent_for_router = session_messages[-context_window:-1]
            router_result = run_router_agent(
                user_prompt=user_prompt,
                char_hint=char_hint,
                tools_list=tools_list,
                router=rtr,
                temperature=temperature,
                recent_history=_recent_for_router if _recent_for_router else None,
            )

        if router_result.needs_tools:
            with t.step("[並行] 過渡語音推播 (Thinking Speech Dispatch)"):
                if router_result.thinking_speech and on_event:
                    on_event({"type": "thinking_speech", "content": router_result.thinking_speech})

            with t.step("[並行] 工具並行執行 (Tool Execution)"):
                ctx = run_middleware(
                    router_result=router_result,
                    on_thinking_speech=None,
                    on_tool_status=on_event,
                )
                thinking = ctx.thinking_speech_sent

        return {
            "tool_context": ctx,
            "thinking_speech": thinking,
            "timer_steps": t._steps,
        }

    # ═══════════════════════════════════════════════════════════
    # 平行執行兩條分支
    # ═══════════════════════════════════════════════════════════
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

    # ═══════════════════════════════════════════════════════════
    # Module C：角色渲染（等兩條分支都完成後才執行）
    # ═══════════════════════════════════════════════════════════
    with main_timer.step("角色渲染生成 (Persona Agent LLM)"):
        raw_res, error_result = run_persona_agent(
            user_prompt=user_prompt,
            api_messages=api_messages,
            tool_context=tool_context,
            chat_schema=chat_schema,
            router=rtr,
            temperature=temperature,
        )

    if error_result is not None:
        persona_result = error_result
    else:
        with main_timer.step("回應解析 (Response Parsing)"):
            persona_result = _parse_persona_response(raw_res)

    reply_text = persona_result.reply_text
    new_entities = persona_result.new_entities
    cited_uids = retrieval_ctx.get("cited_uids", [])
    inner_thought = persona_result.inner_thought
    status_metrics = persona_result.status_metrics
    tone = persona_result.tone
    speech = persona_result.speech

    # 將效能計時結果注入 retrieval_ctx
    retrieval_ctx["perf_timing"] = main_timer.summary()

    return (
        reply_text, new_entities, retrieval_ctx, topic_shifted, pipeline_data,
        inner_thought, status_metrics, tone, speech, thinking_speech, cited_uids
    )
