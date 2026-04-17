"""
PersonaProbe v2 — Streamlit UI
"""

import streamlit as st
from pathlib import Path

import llm_client as llm
import probe_engine as engine
from probe_engine import (
    ProbeState, CALIBRATION_QUESTIONS, CALIBRATION_TRANSITION,
    DIMENSION_TRANSITION, COMPLETION_MESSAGE, DIMENSION_SPECS,
    is_skip_signal, extract_memory_fact, build_persona_reconstruction_prompt,
    build_fast_persona_complete_prompt,
    parse_fragment_input_text, load_fragments_from_db, list_db_sessions,
    _messages_to_text, build_fragment_extraction_prompt,
    build_fragment_aggregation_prompt, build_persona_md_prompt,
)
from llm_client import LLMConfig, LLMClient

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="PersonaProbe",
    page_icon="🧠",
    layout="wide",
)

# ── Session state init ────────────────────────────────────────────────────────

def _init_ss():
    defaults = {
        "probe_state": None,
        "started": False,
        "interviewer": None,   # LLMClient
        "respondent": None,    # LLMClient (LLM mode only)
        "pending": None,       # "opening" | "followup" | "respondent" | "profile" | "persona_recon"
        "pending_answer": "",  # the user/llm answer that triggered the pending call
        "report": None,
        "reconstructed_seed": None,   # filled when seed_only mode completes
        "seed_only": False,
        "fast_persona": False,
        "fast_persona_result": None,  # filled when fast persona mode completes
        "output_dir": str(Path(__file__).parent),
        "or_models_cache": {},  # api_key -> list of models
        # ── 片段分析模式 ──
        "fa_report": None,     # 完整心智模型報告（Markdown str）
        "fa_persona": None,    # LLM 行為模板（Markdown str）
        "fa_output_dir": None, # 輸出目錄路徑
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_ss()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_or_fetch_openrouter_models(api_key: str) -> list[str]:
    cache = st.session_state.or_models_cache
    if api_key not in cache or not cache[api_key]:
        with st.spinner("正在載入 OpenRouter 模型列表..."):
            models = llm.fetch_openrouter_models(api_key)
        cache[api_key] = models
        st.session_state.or_models_cache = cache
    return cache.get(api_key, [])


def _build_llm_selector(label_prefix: str, key_prefix: str) -> LLMConfig | None:
    """Renders provider + model selector widgets, returns LLMConfig or None."""
    provider = st.selectbox(
        "服務商",
        ["Ollama（本地）", "OpenRouter（線上）"],
        key=f"{key_prefix}_provider",
    )
    if "Ollama" in provider:
        base_url = st.text_input(
            "Ollama 位址",
            value="http://localhost:11434",
            key=f"{key_prefix}_base_url",
        )
        models = llm.list_ollama_models(base_url)
        if not models:
            st.warning("找不到 Ollama，請確認服務是否啟動")
            return None
        model = st.selectbox("模型", models, key=f"{key_prefix}_model")
        temp = st.slider("Temperature", 0.0, 1.5, 0.7, 0.05, key=f"{key_prefix}_temp")
        return LLMConfig(provider="ollama", model=model, ollama_base_url=base_url, temperature=temp)
    else:
        api_key = st.text_input(
            "OpenRouter API Key",
            type="password",
            key=f"{key_prefix}_apikey",
        )
        if not api_key:
            st.info("請輸入 API Key 以載入模型列表")
            return None
        models = _get_or_fetch_openrouter_models(api_key)
        if not models:
            manual = st.text_input("手動輸入模型 ID", key=f"{key_prefix}_manual")
            if not manual:
                return None
            model = manual
        else:
            # Sort popular models to top
            priority = ["claude", "gpt-4", "gemini", "llama", "mistral"]
            popular = [m for m in models if any(p in m.lower() for p in priority)]
            rest = [m for m in models if m not in popular]
            model = st.selectbox("模型", popular + rest, key=f"{key_prefix}_model")
        temp = st.slider("Temperature", 0.0, 1.5, 0.7, 0.05, key=f"{key_prefix}_temp")
        return LLMConfig(provider="openrouter", model=model, api_key=api_key, temperature=temp)


# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("⚙️ 設定")

    mode = st.radio(
        "採集模式",
        ["真人採集", "LLM 人格生成", "片段分析"],
        help=(
            "真人採集：你親自回答\n"
            "LLM 人格生成：由 LLM 扮演受訪者，可完全自動運行\n"
            "片段分析：輸入現有對話片段，自動提取人格特徵"
        ),
    )

    if mode == "片段分析":
        # ── 片段分析模式側欄 ──────────────────────────────────────
        st.divider()
        st.subheader("分析 LLM")
        fragment_llm_config = _build_llm_selector("分析", "fa")
        if fragment_llm_config:
            fragment_llm_config.max_tokens = st.number_input(
                "Max Tokens（輸出上限）",
                min_value=1024,
                max_value=131072,
                value=8192,
                step=1024,
                key="fa_max_tokens",
                help="報告較長，建議 8192 以上；Ollama 模型上限依模型而定",
            )

        st.divider()
        st.subheader("輸出")
        output_dir = st.text_input(
            "輸出目錄",
            value=st.session_state.output_dir,
            key="output_dir_input",
        )
        st.session_state.output_dir = output_dir

        # 設定互動式模式所需的變數預設值
        seed_only = False
        fast_persona = False
        st.session_state.seed_only = False
        st.session_state.fast_persona = False
        interviewer_config = None
        respondent_config = None
        persona_seed = ""
        start_btn = False
        reset_btn = False

    else:
        # ── 互動式採集模式側欄 ────────────────────────────────────
        fragment_llm_config = None

        seed_only = st.checkbox(
            "🌱 僅生成人格種子",
            value=st.session_state.seed_only,
            help="勾選後只跑校準 5 題 + 人格重構，直接輸出新種子，不繼續進行維度分析",
            disabled=st.session_state.started or st.session_state.fast_persona,
        )
        st.session_state.seed_only = seed_only

        fast_persona = st.checkbox(
            "⚡ 快速人格生成",
            value=st.session_state.fast_persona,
            help="只跑校準 5 題，用一次 LLM 呼叫直接填寫行為模板（決策邊界、矛盾、強度校準等），不進行完整維度分析",
            disabled=st.session_state.started or seed_only,
        )
        st.session_state.fast_persona = fast_persona

        st.divider()
        st.subheader("提問 LLM")
        interviewer_config = _build_llm_selector("提問", "int")

        respondent_config = None
        persona_seed = ""
        if mode == "LLM 人格生成":
            st.divider()
            st.subheader("回應 LLM（受訪者）")
            respondent_config = _build_llm_selector("回應", "res")

        # Show persona seed input for LLM mode, seed_only mode, or fast_persona mode
        if mode == "LLM 人格生成" or seed_only or fast_persona:
            persona_seed = st.text_area(
                "人格種子（選填）",
                placeholder="描述受訪者的基本背景或人格設定，留空則由 LLM 自由生成",
                key="persona_seed",
                height=80,
            )

        st.divider()
        st.subheader("輸出")
        output_dir = st.text_input(
            "輸出目錄",
            value=st.session_state.output_dir,
            key="output_dir_input",
        )
        st.session_state.output_dir = output_dir

        st.divider()

        # Progress
        if st.session_state.started and st.session_state.probe_state:
            s: ProbeState = st.session_state.probe_state
            st.subheader("進度")
            if s.phase == 0:
                st.progress(s.calibration_q_index / 5, text=f"校準 {s.calibration_q_index}/5")
            elif 1 <= s.phase <= 6:
                frac = (s.phase - 1 + s.dimension_followup_count / 3) / 6
                dim_name = DIMENSION_SPECS[s.phase]["name"]
                st.progress(frac, text=f"維度 {s.phase}/6 — {dim_name}")
                st.caption(f"追問層次：{s.dimension_followup_count}/3")
            else:
                st.progress(1.0, text="採集完成 ✓")
            st.divider()

        col1, col2 = st.columns(2)
        with col1:
            start_btn = st.button("▶ 開始", type="primary", use_container_width=True,
                                  disabled=st.session_state.started)
        with col2:
            reset_btn = st.button("↺ 重置", use_container_width=True)

        if reset_btn:
            for k in ["probe_state", "started", "interviewer", "respondent",
                      "pending", "pending_answer", "report", "reconstructed_seed",
                      "fast_persona_result"]:
                st.session_state[k] = None if k != "started" else False
            st.session_state.seed_only = False
            st.session_state.fast_persona = False
            st.rerun()


# ── Start logic ───────────────────────────────────────────────────────────────

if start_btn:
    if not interviewer_config:
        st.sidebar.error("請先設定提問 LLM")
    elif mode == "LLM 人格生成" and not respondent_config:
        st.sidebar.error("請先設定回應 LLM")
    else:
        out = Path(output_dir)
        log_path = str(out / "session-log.md")
        profile_path = str(out / "profile.md")

        s = ProbeState(
            mode="llm" if mode == "LLM 人格生成" else "human",
            session_log_path=log_path,
            profile_path=profile_path,
            persona_seed=persona_seed,
        )
        st.session_state.reconstructed_seed = None
        engine.init_session_log(log_path)

        # Queue first calibration question
        first_q = CALIBRATION_QUESTIONS[0]
        s.add_message("assistant", first_q)
        s.calibration_q_index = 1
        engine.append_to_session_log(log_path, "assistant", first_q)

        st.session_state.probe_state = s
        st.session_state.interviewer = LLMClient(interviewer_config)
        if respondent_config:
            st.session_state.respondent = LLMClient(respondent_config)
        st.session_state.started = True

        # If LLM mode, immediately queue respondent
        if s.mode == "llm":
            st.session_state.pending = "respondent"
            st.session_state.pending_answer = ""

        st.rerun()


# ── Main UI ───────────────────────────────────────────────────────────────────

st.title("🧠 PersonaProbe")
st.caption("心智模型採集系統 v2")

# ── 片段分析模式主介面（早退，不進入互動式流程）─────────────────────────────

if mode == "片段分析":
    import json
    from datetime import datetime

    st.subheader("片段分析")
    st.caption("輸入現有對話記錄，自動提取 6 個維度的人格特徵並生成報告")

    # ── 片段來源 ──
    source = st.radio(
        "片段來源",
        ["純文字輸入", "從 conversation.db 讀取"],
        horizontal=True,
        key="fa_source",
    )

    messages: list[dict] = []

    if source == "純文字輸入":
        raw_text = st.text_area(
            "貼上對話片段",
            height=300,
            placeholder=(
                "用 user:/AI: 或 使用者:/助手: 標記角色，例如：\n\n"
                "user: 我最近在研究記憶系統的設計...\n"
                "AI: 你對哪個面向最感興趣？\n"
                "user: 主要是跨對話的情境保留..."
            ),
            key="fa_raw_text",
        )
        if raw_text.strip():
            messages = parse_fragment_input_text(raw_text)
            st.caption(f"已解析 {len(messages)} 則訊息")

    else:
        db_path = st.text_input(
            "conversation.db 路徑",
            value=r"G:\ClaudeProject\MemoriaCore\conversation.db",
            key="fa_db_path",
        )
        sessions: list[dict] = []
        if db_path:
            try:
                sessions = list_db_sessions(db_path)
            except Exception as e:
                st.warning(f"無法讀取資料庫：{e}")

        if sessions:
            session_options = ["（全部 sessions）"] + [
                f"{s['session_id']} — {s['last_active']}" for s in sessions
            ]
            selected = st.selectbox("選擇 Session", session_options, key="fa_session")
            if selected != "（全部 sessions）":
                selected_id = sessions[session_options.index(selected) - 1]["session_id"]
            else:
                selected_id = None

            if st.button("載入訊息", key="fa_load_db"):
                try:
                    messages = load_fragments_from_db(db_path, selected_id)
                    st.session_state["fa_loaded_messages"] = messages
                except Exception as e:
                    st.error(f"載入失敗：{e}")

            messages = st.session_state.get("fa_loaded_messages", [])
            if messages:
                st.caption(f"已載入 {len(messages)} 則訊息")

    # ── 現有 Persona（選填）──
    existing_persona = st.text_area(
        "現有 Persona（選填，用於補全缺失維度）",
        height=120,
        placeholder="貼上既有的 persona.md 或 System Prompt，若某維度在片段中找不到資料，將從此處補全",
        key="fa_existing_persona",
    )

    st.divider()

    # ── 開始分析 ──
    analyze_btn = st.button(
        "開始分析",
        type="primary",
        disabled=not messages or not fragment_llm_config,
    )
    if not messages:
        st.info("請先輸入或載入對話片段")
    elif not fragment_llm_config:
        st.info("請在左側設定分析 LLM")

    if analyze_btn and messages and fragment_llm_config:
        fragments_text = _messages_to_text(messages)
        client = LLMClient(fragment_llm_config)

        # ── 6 維度提取 ──
        extraction_results: dict = {}
        progress_bar = st.progress(0.0, text="準備分析...")

        for i, dim_id in enumerate(sorted(DIMENSION_SPECS.keys()), start=1):
            dim_name = DIMENSION_SPECS[dim_id]["name"]
            progress_bar.progress(
                (i - 1) / 8,
                text=f"提取維度 {i}/6：{dim_name}",
            )
            prompt_msgs = build_fragment_extraction_prompt(
                dim_id, fragments_text, existing_persona
            )
            raw = client.chat(prompt_msgs)
            try:
                result = json.loads(raw)
            except json.JSONDecodeError:
                # LLM 未回傳有效 JSON，標記為 none
                result = {"confidence": "none"}
            extraction_results[dim_id] = result

        # ── 聚合生成完整報告 ──
        progress_bar.progress(6 / 8, text="生成完整心智模型報告...")
        agg_msgs = build_fragment_aggregation_prompt(extraction_results, fragments_text, existing_persona)
        full_report = client.chat(agg_msgs)

        # ── 萃取 persona.md ──
        progress_bar.progress(7 / 8, text="萃取 LLM 行為模板...")
        persona_msgs = build_persona_md_prompt(full_report, existing_persona)
        persona_content = client.chat(persona_msgs)

        progress_bar.progress(1.0, text="分析完成 ✓")

        # ── 寫入檔案 ──
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = Path(output_dir) / f"fragment-{timestamp}"
        out_dir.mkdir(parents=True, exist_ok=True)

        (out_dir / "probe-report.md").write_text(full_report, encoding="utf-8")
        (out_dir / "persona.md").write_text(persona_content, encoding="utf-8")
        (out_dir / "fragment-input.md").write_text(
            f"# 原始輸入片段\n\n{fragments_text}", encoding="utf-8"
        )

        st.session_state["fa_report"] = full_report
        st.session_state["fa_persona"] = persona_content
        st.session_state["fa_output_dir"] = str(out_dir)
        st.rerun()

    # ── 顯示結果 ──
    if st.session_state.get("fa_report"):
        st.success(f"分析完成，已儲存至：{st.session_state['fa_output_dir']}")
        tab_report, tab_persona = st.tabs(["完整心智模型報告", "persona.md（LLM 行為模板）"])
        with tab_report:
            st.markdown(st.session_state["fa_report"])
            st.download_button(
                "⬇ 下載 probe-report.md",
                st.session_state["fa_report"],
                file_name="probe-report.md",
                mime="text/markdown",
                use_container_width=True,
            )
        with tab_persona:
            st.markdown(st.session_state["fa_persona"])
            st.download_button(
                "⬇ 下載 persona.md",
                st.session_state["fa_persona"],
                file_name="persona.md",
                mime="text/markdown",
                use_container_width=True,
            )

    st.stop()


if not st.session_state.started:
    st.info("在左側設定 LLM，然後點擊「開始」啟動採集。")
    st.stop()

s: ProbeState = st.session_state.probe_state
interviewer: LLMClient = st.session_state.interviewer
respondent: LLMClient = st.session_state.respondent

# ── Process pending LLM call ──────────────────────────────────────────────────

def _append_assistant(text: str):
    s.add_message("assistant", text)
    engine.append_to_session_log(s.session_log_path, "assistant", text)


def _append_user(text: str):
    s.add_message("user", text)
    engine.append_to_session_log(s.session_log_path, "user", text)


def _advance_after_user_answer():
    """Called after a user/llm answer is logged. Sets next pending call or writes fixed messages."""
    if s.phase == 0:
        # Calibration
        if s.calibration_q_index < 5:
            next_q = CALIBRATION_QUESTIONS[s.calibration_q_index]
            s.calibration_q_index += 1
            _append_assistant(next_q)
            if s.mode == "llm":
                st.session_state.pending = "respondent"
            # else: wait for human input
        else:
            # All 5 calibration done → reconstruct persona seed first
            s.calibration_q_index = 5
            st.session_state.pending = "persona_recon"
    else:
        # Dimension phase
        s.dimension_followup_count += 1
        s.dimension_answers.append(st.session_state.pending_answer)

        if s.dimension_followup_count >= 4 or is_skip_signal(st.session_state.pending_answer):
            # Move to next dimension
            s.completed_dim_names.append(DIMENSION_SPECS[s.phase]["name"])
            s.phase += 1
            s.current_dimension = s.phase
            s.dimension_followup_count = 0
            s.dimension_answers = []
            s.current_dim_qa = []   # ← reset per-dimension context

            if s.phase > 6:
                # All done
                s.interview_complete = True
                _append_assistant(COMPLETION_MESSAGE)
                st.session_state.pending = "profile"
            else:
                _append_assistant(DIMENSION_TRANSITION)
                st.session_state.pending = "opening"
        else:
            # Generate follow-up
            st.session_state.pending = "followup"


if st.session_state.pending:
    pending = st.session_state.pending

    if pending == "persona_recon":
        # ── Reconstruct persona seed from all 5 calibration answers ──────────
        with st.spinner("根據校準回答推斷人格種子..."):
            msgs = build_persona_reconstruction_prompt(s)
            reconstructed = "".join(interviewer.chat(msgs, stream=False, temperature=0.6))
        # Merge: if user provided a seed, prepend it; otherwise use reconstructed alone
        if s.persona_seed.strip():
            merged = s.persona_seed.strip() + "\n\n---\n\n" + reconstructed
        else:
            merged = reconstructed
        s.persona_seed = merged

        if st.session_state.seed_only:
            # ── Seed-only mode: store result and stop ─────────────────────────
            st.session_state.reconstructed_seed = merged
            s.interview_complete = True
            st.session_state.pending = None
            st.rerun()
        elif st.session_state.fast_persona:
            # ── Fast persona mode: one-shot behavioral template fill ──────────
            st.toast("人格種子已更新，開始快速生成人格 ✓", icon="⚡")
            st.session_state.pending = "fast_persona"
            st.rerun()
        else:
            # ── Full mode: continue to dimension analysis ─────────────────────
            st.toast("人格種子已更新 ✓", icon="🧩")
            s.phase = 1
            s.current_dimension = 1
            s.dimension_followup_count = 0
            _append_assistant(CALIBRATION_TRANSITION)
            st.session_state.pending = "opening"
            st.rerun()

    elif pending == "opening":
        with st.spinner(f"生成維度 {s.phase} 問題..."):
            msgs = engine.build_dimension_opening_prompt(s)
            result = "".join(interviewer.chat(msgs, stream=False))
        _append_assistant(result)
        # ── Track this question in current dimension Q&A log
        s.current_dim_qa.append({"q": result, "a": ""})
        st.session_state.pending = None
        if s.mode == "llm":
            st.session_state.pending = "respondent"
        st.rerun()

    elif pending == "followup":
        with st.spinner("生成追問..."):
            msgs = engine.build_followup_prompt(s, st.session_state.pending_answer)
            result = "".join(interviewer.chat(msgs, stream=False))
        _append_assistant(result)
        # ── Track this follow-up question in current dimension Q&A log
        s.current_dim_qa.append({"q": result, "a": ""})
        st.session_state.pending = None
        if s.mode == "llm":
            st.session_state.pending = "respondent"
        st.rerun()

    elif pending == "respondent":
        last_q = next(
            (m["content"] for m in reversed(s.conversation) if m["role"] == "assistant"),
            ""
        )
        with st.spinner("LLM 受訪者思考中..."):
            msgs = engine.build_llm_respondent_prompt(s, last_q)
            result = "".join(respondent.chat(msgs, stream=False, temperature=0.9))
        # ── Fill in the answer for the last tracked question
        if s.current_dim_qa and not s.current_dim_qa[-1]["a"]:
            s.current_dim_qa[-1]["a"] = result
        # ── Also store compact memory fact for cross-dimension continuity
        fact = engine.extract_memory_fact(result, last_q)
        s.respondent_memory.append(fact)
        # ── Append to shared log (for interviewer context + report generation)
        _append_user(result)
        st.session_state.pending_answer = result
        st.session_state.pending = None
        _advance_after_user_answer()
        st.rerun()

    elif pending == "profile":
        with st.spinner("生成心智模型報告（可能需要 30-90 秒）..."):
            msgs = engine.build_profile_prompt(s)
            # Use high max_tokens to avoid truncation — report template alone is ~1000 tokens
            result = "".join(interviewer.chat(
                msgs, stream=False, temperature=0.3, max_tokens=8192
            ))
        engine.write_profile(s.profile_path, result)
        st.session_state.report = result
        st.session_state.pending = None
        st.rerun()

    elif pending == "fast_persona":
        with st.spinner("快速生成行為人格（可能需要 15-30 秒）..."):
            msgs = build_fast_persona_complete_prompt(s)
            result = "".join(interviewer.chat(
                msgs, stream=False, temperature=0.5, max_tokens=4096
            ))
        st.session_state.fast_persona_result = result
        s.interview_complete = True
        # Save to file alongside other outputs
        out_path = str(Path(st.session_state.output_dir) / "fast-persona.md")
        engine.write_profile(out_path, result)
        st.session_state.pending = None
        st.rerun()


# ── Render chat ───────────────────────────────────────────────────────────────

for msg in s.conversation:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])


# ── Seed-only output ─────────────────────────────────────────────────────────

if st.session_state.reconstructed_seed:
    st.divider()
    st.success("🌱 人格種子生成完成")
    seed_text = st.session_state.reconstructed_seed
    st.text_area(
        "生成的人格種子（可直接複製使用）",
        value=seed_text,
        height=400,
        key="seed_display",
    )
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "⬇ 下載人格種子 (.txt)",
            seed_text,
            file_name="persona-seed.txt",
            mime="text/plain",
            use_container_width=True,
        )
    with col2:
        st.download_button(
            "⬇ 下載人格種子 (.md)",
            seed_text,
            file_name="persona-seed.md",
            mime="text/markdown",
            use_container_width=True,
        )


# ── Fast persona output ───────────────────────────────────────────────────────

if st.session_state.fast_persona_result:
    st.divider()
    st.success("⚡ 快速人格生成完成")
    fp_text = st.session_state.fast_persona_result
    st.markdown(fp_text)
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "⬇ 下載快速人格 (.txt)",
            fp_text,
            file_name="fast-persona.txt",
            mime="text/plain",
            use_container_width=True,
        )
    with col2:
        st.download_button(
            "⬇ 下載快速人格 (.md)",
            fp_text,
            file_name="fast-persona.md",
            mime="text/markdown",
            use_container_width=True,
        )


# ── Report ────────────────────────────────────────────────────────────────────

if st.session_state.report:
    st.divider()
    with st.expander("📄 心智模型報告", expanded=True):
        st.markdown(st.session_state.report)
        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                "⬇ 下載報告 (profile.md)",
                st.session_state.report,
                file_name="profile.md",
                mime="text/markdown",
                use_container_width=True,
            )
        with col2:
            log_path = s.session_log_path
            try:
                log_content = Path(log_path).read_text(encoding="utf-8")
                st.download_button(
                    "⬇ 下載對話記錄 (session-log.md)",
                    log_content,
                    file_name="session-log.md",
                    mime="text/markdown",
                    use_container_width=True,
                )
            except Exception:
                pass


# ── Human input ───────────────────────────────────────────────────────────────

if not s.interview_complete and s.mode == "human" and not st.session_state.pending:
    user_input = st.chat_input("輸入你的回答... （說「下一題」可跳過當前維度）")
    if user_input:
        _append_user(user_input)
        st.session_state.pending_answer = user_input
        _advance_after_user_answer()
        st.rerun()
