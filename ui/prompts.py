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


def render_prompts_page(api_base: str, user_prefs: dict | None = None):
    st.title("📝 Prompt 管理")
    st.caption("集中管理所有 LLM Prompt 模板。修改後立即生效，無需重啟。")

    pm = get_prompt_manager()
    keys = pm.list_keys()

    # ── 全部重置按鈕 ──
    col_top1, col_top2 = st.columns([8, 2])
    with col_top1:
        st.markdown(f"共 **{len(keys)}** 個 Prompt 模板")
    with col_top2:
        if st.button("🔄 全部重置為預設", type="secondary", use_container_width=True):
            pm.reset_all()
            st.success("已將所有 Prompt 重置為系統內建預設值。")
            st.rerun()

    st.divider()

    # ── 逐個 Prompt 展示 ──
    for key in keys:
        meta = pm.get_meta(key)
        label = meta["label"]
        has_override = meta["has_user_override"]

        # 標題列：名稱 + 狀態標籤
        status_tag = "🟡 已自訂" if has_override else "🟢 預設"
        with st.expander(f"**{label}**　{status_tag}", expanded=False):
            # 說明
            st.caption(f"📌 {meta['description']}")
            st.caption(f"📁 使用位置：`{meta['used_by']}`")

            # Placeholder 提示
            if meta["placeholders"]:
                placeholders_str = "、".join([f"`{p}`" for p in meta["placeholders"]])
                st.info(f"⚠️ 必要佔位符（請勿刪除）：{placeholders_str}", icon="🔧")

            # 編輯區
            current = meta["current_template"]
            line_count = max(current.count("\n") + 2, 8)
            new_val = st.text_area(
                f"模板內容",
                value=current,
                height=min(line_count * 20, 600),
                key=f"prompt_edit_{key}",
                label_visibility="collapsed",
            )

            # 按鈕列
            btn_col1, btn_col2, btn_col3 = st.columns([2, 2, 6])
            with btn_col1:
                if st.button("💾 儲存", key=f"save_{key}", use_container_width=True):
                    if new_val.strip() == "":
                        st.error("Prompt 內容不可為空！")
                    elif new_val == meta["default_template"]:
                        # 跟預設一樣，等同重置
                        pm.reset_one(key)
                        st.success("內容與預設相同，已移除自訂覆寫。")
                        st.rerun()
                    else:
                        # 檢查 placeholder 是否還在
                        missing = [p for p in meta["placeholders"] if p in meta["default_template"] and p not in new_val]
                        if missing:
                            missing_str = "、".join(missing)
                            st.warning(f"⚠️ 偵測到遺失的佔位符：{missing_str}，這可能導致執行錯誤。確定要儲存嗎？")
                            if st.button("確認儲存（忽略警告）", key=f"force_save_{key}"):
                                pm.update(key, new_val)
                                st.success(f"已儲存 {label}")
                                st.rerun()
                        else:
                            pm.update(key, new_val)
                            st.success(f"已儲存 {label}")
                            st.rerun()

            with btn_col2:
                if has_override:
                    if st.button("↩️ 重置", key=f"reset_{key}", use_container_width=True):
                        pm.reset_one(key)
                        st.success(f"已將 {label} 重置為預設。")
                        st.rerun()

            # 預設值對照（僅在有自訂時顯示）
            if has_override:
                with st.popover("👁️ 檢視預設值"):
                    st.code(meta["default_template"], language=None)
