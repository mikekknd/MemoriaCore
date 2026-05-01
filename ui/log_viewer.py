"""
Log 檢視器頁面 — 透過 FastAPI REST API 載入 Log 資料。
不再直接讀取 llm_trace.jsonl 檔案。
"""
import streamlit as st
from ui import api_client as requests
from datetime import datetime
from core.i18n import DEFAULT_LOCALE, normalize_locale, t


def _locale(user_prefs: dict | None = None) -> str:
    try:
        return normalize_locale((user_prefs or {}).get("ui_locale"))
    except ValueError:
        return DEFAULT_LOCALE

CATEGORY_META = {
    "chat":          {"label": "💬 對話生成",    "color": "#4A9EFF"},
    "router":        {"label": "🔀 工具意圖偵測","color": "#818CF8"},
    "pipeline":      {"label": "🧠 記憶管線",    "color": "#A78BFA"},
    "expand":        {"label": "🔍 意圖擴展",    "color": "#34D399"},
    "fuse":          {"label": "⚡ 核心融合",    "color": "#F59E0B"},
    "profile":       {"label": "👤 使用者畫像",  "color": "#EC4899"},
    "system_event":  {"label": "🔧 系統事件",    "color": "#6B7280"},
    "error":         {"label": "❌ 錯誤",         "color": "#EF4444"},
}


# ──────────────────────────────────────────
# 資料載入與分組
# ──────────────────────────────────────────

@st.cache_data(ttl=10, show_spinner=False)
def _load_entries_from_api(api_base, limit=1000):
    """從 API 載入 Log 條目（快取 10 秒）"""
    try:
        resp = requests.get(f"{api_base}/logs?limit={limit}", timeout=10)
        if resp.ok:
            return resp.json()
        return []
    except Exception:
        return []


def _group_into_blocks(entries):
    """
    將 entries 合併成顯示用的 blocks：
      - llm_call prompt + response → 一個 llm_pair block
      - system_event / error       → 獨立 block
    """
    blocks = []
    pending_prompt = None

    for entry in entries:
        etype = entry.get("type")

        if etype == "llm_call":
            direction = entry.get("direction")
            if direction == "prompt":
                if pending_prompt:
                    blocks.append({
                        "type": "llm_pair",
                        "category": pending_prompt.get("category", "unknown"),
                        "model": pending_prompt.get("model", ""),
                        "timestamp": pending_prompt.get("timestamp", ""),
                        "prompt": pending_prompt,
                        "response": None,
                    })
                pending_prompt = entry
            elif direction == "response":
                blocks.append({
                    "type": "llm_pair",
                    "category": entry.get("category", "unknown"),
                    "model": entry.get("model", ""),
                    "timestamp": entry.get("timestamp", ""),
                    "prompt": pending_prompt,
                    "response": entry,
                })
                pending_prompt = None

        elif etype in ("system_event", "error"):
            blocks.append({
                "type": etype,
                "category": entry.get("category", etype),
                "timestamp": entry.get("timestamp", ""),
                "entry": entry,
            })

    if pending_prompt:
        blocks.append({
            "type": "llm_pair",
            "category": pending_prompt.get("category", "unknown"),
            "model": pending_prompt.get("model", ""),
            "timestamp": pending_prompt.get("timestamp", ""),
            "prompt": pending_prompt,
            "response": None,
        })

    return blocks


# ──────────────────────────────────────────
# 工具函式
# ──────────────────────────────────────────

def _fmt_ts(iso_str: str) -> str:
    try:
        return datetime.fromisoformat(iso_str).strftime("%m/%d %H:%M:%S")
    except Exception:
        return iso_str[:19] if iso_str else ""


def _fmt_messages(messages: list) -> tuple[str, int, int]:
    """
    回傳 (formatted_text, total_chars, message_count)。
    所有訊息類型（system/user/assistant/tool/tool_calls）完整輸出，不做任何截斷。
    """
    sep = "\n" + "─" * 50 + "\n"
    parts = []
    total_chars = 0

    for msg in (messages or []):
        role = msg.get("role", "unknown").upper()
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls")
        tool_call_id = msg.get("tool_call_id")

        header = f"[{role}]"
        if tool_call_id:
            header += f" (tool_call_id={tool_call_id})"

        body_parts = []
        if content:
            body_parts.append(content)
        if tool_calls:
            import json as _json
            body_parts.append("[TOOL_CALLS]\n" + _json.dumps(tool_calls, ensure_ascii=False, indent=2))

        body = "\n".join(body_parts) if body_parts else "(empty)"
        total_chars += len(content) + sum(len(str(tc)) for tc in (tool_calls or []))
        parts.append(f"{header}\n{body}")

    return sep.join(parts), total_chars, len(messages or [])


# ──────────────────────────────────────────
# 渲染單一 block
# ──────────────────────────────────────────

def _render_llm_pair(block: dict, idx: int, locale: str):
    cat = block["category"]
    meta = CATEGORY_META.get(cat, {"label": cat, "color": "#9CA3AF"})
    ts = _fmt_ts(block["timestamp"])
    model = block["model"] or ""
    has_resp = block["response"] is not None
    status_icon = "✅" if has_resp else "⏳"

    label = f"{status_icon} {meta['label']}  ·  {ts}  ·  {model}"

    with st.expander(label, expanded=False):
        prompt_block = block.get("prompt")
        if prompt_block:
            messages = prompt_block.get("messages", [])
            prompt_text, total_chars, msg_count = _fmt_messages(messages)
            st.markdown(
                f"**📥 Prompt** — `{msg_count}` 則訊息　|　"
                f"{t('log_viewer.streamlit.total_chars', locale, count=f'{total_chars:,}')}　|　"
                f"{t('log_viewer.streamlit.approx_tokens', locale, count=f'{total_chars // 4:,}')}"
            )
            st.code(prompt_text, language=None)
        else:
            st.markdown("**📥 Prompt**")
            st.caption(t("log_viewer.streamlit.no_prompt", locale))

        if has_resp:
            response_text = block["response"].get("content", "")
            resp_chars = len(response_text)
            st.markdown(
                f"**📤 Response** — {t('log_viewer.streamlit.total_chars', locale, count=f'{resp_chars:,}')}　|　{t('log_viewer.streamlit.approx_tokens', locale, count=f'{resp_chars // 4:,}')}"
            )
            st.code(response_text, language=None)
        else:
            st.markdown("**📤 Response**")
            st.warning(t("log_viewer.streamlit.no_response", locale))


def _render_system_event(block: dict):
    entry = block["entry"]
    cat = entry.get("category", "系統事件")
    msg = entry.get("message", "")
    details = entry.get("details")
    ts = _fmt_ts(block["timestamp"])

    with st.expander(f"🔧 {cat}  ·  {ts}", expanded=False):
        st.markdown(f"**{msg}**")
        if details:
            st.json(details)


def _render_error(block: dict):
    entry = block["entry"]
    cat = entry.get("category", "錯誤")
    msg = entry.get("message", "")
    ts = _fmt_ts(block["timestamp"])

    with st.expander(f"❌ {cat}  ·  {ts}", expanded=False):
        st.error(msg)


# ──────────────────────────────────────────
# 主頁面入口
# ──────────────────────────────────────────

def render_log_viewer_page(api_base, user_prefs: dict | None = None):
    locale = _locale(user_prefs)
    st.title(t("log_viewer.streamlit.title", locale))

    col_info, col_reload = st.columns([5, 1])
    with col_info:
        st.caption(t("log_viewer.streamlit.source", locale, source=f"{api_base}/logs"))
    with col_reload:
        if st.button(t("log_viewer.reload", locale), use_container_width=True):
            _load_entries_from_api.clear()
            st.rerun()

    # 載入資料（API 回傳已按最新在前排序）
    raw = _load_entries_from_api(api_base)

    if not raw:
        st.info(t("log_viewer.streamlit.no_data", locale))
        return

    # API 回傳最新在前，需要反轉以正確配對 prompt/response
    raw_chronological = list(reversed(raw))
    blocks = _group_into_blocks(raw_chronological)

    if not blocks:
        st.info(t("log_viewer.streamlit.empty", locale))
        return

    # 統計列
    llm_pairs = [b for b in blocks if b["type"] == "llm_pair"]
    sys_events = [b for b in blocks if b["type"] == "system_event"]
    errors = [b for b in blocks if b["type"] == "error"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric(t("log_viewer.streamlit.metric_llm", locale), len(llm_pairs))
    m2.metric(t("log_viewer.streamlit.metric_events", locale), len(sys_events))
    m3.metric(t("log_viewer.streamlit.metric_errors", locale), len(errors))
    m4.metric(t("log_viewer.streamlit.metric_total", locale), len(blocks))

    st.divider()

    # 篩選列
    llm_cats = sorted(set(b["category"] for b in llm_pairs))
    sys_cats = sorted(set(b["category"] for b in sys_events))

    with st.container():
        fc1, fc2, fc3 = st.columns([3, 2, 1])
        with fc1:
            selected_llm_cats = st.multiselect(
                t("log_viewer.streamlit.llm_filter", locale), options=llm_cats, default=llm_cats,
                format_func=lambda c: CATEGORY_META.get(c, {}).get("label", c),
            )
        with fc2:
            selected_sys_cats = st.multiselect(t("log_viewer.streamlit.event_filter", locale), options=sys_cats, default=sys_cats)
        with fc3:
            show_errors = st.checkbox(t("log_viewer.streamlit.show_errors", locale), value=True)
            newest_first = st.checkbox(t("log_viewer.newest_first", locale), value=True)

    st.divider()

    display_blocks = blocks if not newest_first else list(reversed(blocks))

    rendered = 0
    for idx, block in enumerate(display_blocks):
        btype = block["type"]
        bcat = block.get("category", "")

        if btype == "llm_pair":
            if bcat not in selected_llm_cats:
                continue
            _render_llm_pair(block, idx, locale)
            rendered += 1
        elif btype == "system_event":
            if bcat not in selected_sys_cats:
                continue
            _render_system_event(block)
            rendered += 1
        elif btype == "error":
            if not show_errors:
                continue
            _render_error(block)
            rendered += 1

    if rendered == 0:
        st.info(t("log_viewer.streamlit.no_matches", locale))
