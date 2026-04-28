"""Streamlit Bot 管理頁。"""
import streamlit as st
from ui import api_client as requests


def render_bots_page(api_base: str, user_prefs: dict | None = None):
    st.title("🤖 Bot 管理")
    st.caption("管理各平台 bot token 與角色綁定。v1 只有 Telegram runtime 會啟動。")

    try:
        bots_resp = requests.get(f"{api_base}/bots", timeout=5)
        bots = bots_resp.json() if bots_resp.ok else []
    except Exception as e:
        st.error(f"無法載入 Bot 設定：{e}")
        bots = []

    try:
        chars_resp = requests.get(f"{api_base}/character", timeout=5)
        characters = chars_resp.json() if chars_resp.ok else []
    except Exception:
        characters = []

    def character_label(character_id: str) -> str:
        for char in characters:
            if char.get("character_id") == character_id:
                name = char.get("name") or character_id
                return f"{name} ({character_id})"
        return character_id

    char_options = [c.get("character_id") for c in characters if c.get("character_id")]
    if "default" not in char_options:
        char_options.insert(0, "default")

    st.subheader("已設定 Bot")
    if not bots:
        st.info("尚未建立任何 Bot。")
    for bot in bots:
        status = bot.get("runtime_status") or {}
        status_text = status.get("status", "disabled")
        title = f"{bot.get('display_name') or bot.get('bot_id')} ({bot.get('platform')})"
        with st.expander(title, expanded=False):
            st.write(f"**Bot ID:** `{bot.get('bot_id')}`")
            st.write(f"**角色:** {character_label(bot.get('character_id', 'default'))}")
            st.write(f"**啟用:** `{bot.get('enabled')}`")
            st.write(f"**Runtime:** `{status_text}`")
            if status.get("last_error"):
                st.error(status.get("last_error"))

            col1, col2, col3 = st.columns(3)
            with col1:
                if st.button("重新載入", key=f"reload_{bot.get('bot_id')}"):
                    r = requests.post(f"{api_base}/bots/{bot.get('bot_id')}/reload", timeout=10)
                    if r.ok:
                        st.success("已重新載入")
                        st.rerun()
                    else:
                        st.error(r.text)
            with col2:
                if st.button("編輯", key=f"edit_{bot.get('bot_id')}"):
                    st.session_state["bot_edit"] = bot
                    st.rerun()
            with col3:
                if st.button("刪除", key=f"delete_{bot.get('bot_id')}"):
                    r = requests.delete(f"{api_base}/bots/{bot.get('bot_id')}", timeout=10)
                    if r.ok:
                        st.success("已刪除")
                        st.session_state.pop("bot_edit", None)
                        st.rerun()
                    else:
                        st.error(r.text)

    st.divider()
    draft = st.session_state.get("bot_edit") or {}
    editing = bool(draft.get("bot_id"))
    st.subheader("編輯 Bot" if editing else "新增 Bot")

    with st.form("bot_form"):
        bot_id = st.text_input(
            "Bot ID",
            value=draft.get("bot_id", ""),
            disabled=editing,
            help="3-64 字元，只能包含英數、底線、連字號。",
        )
        platform = st.selectbox(
            "平台",
            ["telegram", "discord", "other"],
            index=["telegram", "discord", "other"].index(draft.get("platform", "telegram"))
            if draft.get("platform", "telegram") in ["telegram", "discord", "other"] else 0,
        )
        display_name = st.text_input("顯示名稱", value=draft.get("display_name", ""))
        current_char = draft.get("character_id", "default")
        char_index = char_options.index(current_char) if current_char in char_options else 0
        character_id = st.selectbox(
            "綁定角色",
            char_options,
            index=char_index,
            format_func=character_label,
        )
        token = st.text_input("Token", type="password", value=draft.get("token", ""))
        enabled = st.checkbox("啟用", value=bool(draft.get("enabled", False)))

        col_save, col_cancel = st.columns(2)
        submitted = col_save.form_submit_button("儲存", type="primary")
        cancelled = col_cancel.form_submit_button("取消編輯")

    if cancelled:
        st.session_state.pop("bot_edit", None)
        st.rerun()

    if submitted:
        payload = {
            "platform": platform,
            "display_name": display_name,
            "character_id": character_id,
            "token": token,
            "enabled": enabled,
        }
        if editing:
            r = requests.put(f"{api_base}/bots/{draft.get('bot_id')}", json=payload, timeout=10)
        else:
            payload["bot_id"] = bot_id
            r = requests.post(f"{api_base}/bots", json=payload, timeout=10)
        if r.ok:
            st.session_state.pop("bot_edit", None)
            st.success("Bot 設定已儲存")
            st.rerun()
        else:
            st.error(r.text)
