"""
Prompt 管理頁面 — 集中檢視、編輯、重置所有 LLM Prompt 模板。
"""
import streamlit as st
import sys, os

# 確保專案根目錄在 sys.path
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from core.prompt_manager import get_prompt_manager
from core.i18n import DEFAULT_LOCALE, normalize_locale, t


def _locale(user_prefs: dict | None) -> str:
    try:
        return normalize_locale((user_prefs or {}).get("ui_locale"))
    except ValueError:
        return DEFAULT_LOCALE


def render_prompts_page(api_base: str, user_prefs: dict | None = None):
    locale = _locale(user_prefs)
    st.title(t("prompts.streamlit.title", locale))
    st.caption(t("prompts.streamlit.caption", locale))

    pm = get_prompt_manager()
    keys = pm.list_keys()

    # ── 全部重置按鈕 ──
    col_top1, col_top2 = st.columns([8, 2])
    with col_top1:
        st.markdown(t("prompts.streamlit.count_md", locale, count=len(keys)))
    with col_top2:
        if st.button(t("prompts.streamlit.reset_all", locale), type="secondary", use_container_width=True):
            pm.reset_all()
            st.success(t("prompts.streamlit.reset_all_done", locale))
            st.rerun()

    st.divider()

    # ── 逐個 Prompt 展示 ──
    for key in keys:
        meta = pm.get_meta(key)
        label = meta["label"]
        has_override = meta["has_user_override"]

        # 標題列：名稱 + 狀態標籤
        status_tag = t("prompts.streamlit.status_custom", locale) if has_override else t("prompts.streamlit.status_default", locale)
        with st.expander(f"**{label}**　{status_tag}", expanded=False):
            # 說明
            st.caption(f"📌 {meta['description']}")
            st.caption(t("prompts.streamlit.used_by", locale, used_by=meta["used_by"]))

            # Placeholder 提示
            if meta["placeholders"]:
                placeholders_str = "、".join([f"`{p}`" for p in meta["placeholders"]])
                st.info(t("prompts.streamlit.placeholders", locale, placeholders=placeholders_str), icon="🔧")

            # 編輯區
            current = meta["current_template"]
            line_count = max(current.count("\n") + 2, 8)
            new_val = st.text_area(
                t("prompts.streamlit.template_content", locale),
                value=current,
                height=min(line_count * 20, 600),
                key=f"prompt_edit_{key}",
                label_visibility="collapsed",
            )

            # 按鈕列
            btn_col1, btn_col2, btn_col3 = st.columns([2, 2, 6])
            with btn_col1:
                if st.button(t("prompts.streamlit.save", locale), key=f"save_{key}", use_container_width=True):
                    if new_val.strip() == "":
                        st.error(t("prompts.streamlit.empty_error", locale))
                    elif new_val == meta["default_template"]:
                        # 跟預設一樣，等同重置
                        pm.reset_one(key)
                        st.success(t("prompts.streamlit.same_as_default", locale))
                        st.rerun()
                    else:
                        # 檢查 placeholder 是否還在
                        missing = [p for p in meta["placeholders"] if p in meta["default_template"] and p not in new_val]
                        if missing:
                            missing_str = "、".join(missing)
                            st.warning(t("prompts.streamlit.missing_warning", locale, missing=missing_str))
                            if st.button(t("prompts.streamlit.force_save", locale), key=f"force_save_{key}"):
                                pm.update(key, new_val)
                                st.success(t("prompts.streamlit.saved", locale, label=label))
                                st.rerun()
                        else:
                            pm.update(key, new_val)
                            st.success(t("prompts.streamlit.saved", locale, label=label))
                            st.rerun()

            with btn_col2:
                if has_override:
                    if st.button(t("prompts.streamlit.reset", locale), key=f"reset_{key}", use_container_width=True):
                        pm.reset_one(key)
                        st.success(t("prompts.streamlit.reset_done", locale, label=label))
                        st.rerun()

            # 預設值對照（僅在有自訂時顯示）
            if has_override:
                with st.popover(t("prompts.streamlit.view_default", locale)):
                    st.code(meta["default_template"], language=None)
