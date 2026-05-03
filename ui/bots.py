"""Streamlit Bot 管理頁。"""
import streamlit as st
from core.i18n import DEFAULT_LOCALE, normalize_locale, t
from ui import api_client as requests


def _locale(user_prefs: dict | None) -> str:
    try:
        return normalize_locale((user_prefs or {}).get("ui_locale"))
    except ValueError:
        return DEFAULT_LOCALE


def render_bots_page(api_base: str, user_prefs: dict | None = None):
    locale = _locale(user_prefs)
    st.title(t("bots.streamlit.title", locale))
    st.caption(t("bots.streamlit.caption", locale))

    try:
        bots_resp = requests.get(f"{api_base}/bots", timeout=5)
        bots = bots_resp.json() if bots_resp.ok else []
    except Exception as e:
        st.error(t("bots.streamlit.load_failed", locale, message=e))
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

    st.subheader(t("bots.streamlit.configured", locale))
    if not bots:
        st.info(t("bots.empty", locale))
    for bot in bots:
        status = bot.get("runtime_status") or {}
        status_text = status.get("status", "disabled")
        title = f"{bot.get('display_name') or bot.get('bot_id')} ({bot.get('platform')})"
        with st.expander(title, expanded=False):
            st.write(f"**Bot ID:** `{bot.get('bot_id')}`")
            st.write(t("bots.streamlit.character_md", locale, value=character_label(bot.get("character_id", "default"))))
            st.write(t("bots.streamlit.enabled_md", locale, value=bot.get("enabled")))
            st.write(f"**Runtime:** `{status_text}`")
            if status.get("last_error"):
                st.error(status.get("last_error"))

            col1, col2, col3 = st.columns(3)
            with col1:
                if st.button(t("bots.streamlit.reload", locale), key=f"reload_{bot.get('bot_id')}"):
                    r = requests.post(f"{api_base}/bots/{bot.get('bot_id')}/reload", timeout=10)
                    if r.ok:
                        st.success(t("bots.streamlit.reloaded", locale))
                        st.rerun()
                    else:
                        st.error(r.text)
            with col2:
                if st.button(t("bots.streamlit.edit", locale), key=f"edit_{bot.get('bot_id')}"):
                    st.session_state["bot_edit"] = bot
                    st.rerun()
            with col3:
                if st.button(t("bots.streamlit.delete", locale), key=f"delete_{bot.get('bot_id')}"):
                    r = requests.delete(f"{api_base}/bots/{bot.get('bot_id')}", timeout=10)
                    if r.ok:
                        st.success(t("bots.streamlit.deleted", locale))
                        st.session_state.pop("bot_edit", None)
                        st.rerun()
                    else:
                        st.error(r.text)

    st.divider()
    draft = st.session_state.get("bot_edit") or {}
    editing = bool(draft.get("bot_id"))
    st.subheader(t("bots.streamlit.edit_bot", locale) if editing else t("bots.streamlit.new_bot", locale))

    with st.form("bot_form"):
        bot_id = st.text_input(
            "Bot ID",
            value=draft.get("bot_id", ""),
            disabled=editing,
            help=t("bots.streamlit.bot_id_help", locale),
        )
        platform = st.selectbox(
            t("bots.platform", locale),
            ["telegram", "discord", "other"],
            index=["telegram", "discord", "other"].index(draft.get("platform", "telegram"))
            if draft.get("platform", "telegram") in ["telegram", "discord", "other"] else 0,
        )
        display_name = st.text_input(t("bots.display_name", locale), value=draft.get("display_name", ""))
        current_char = draft.get("character_id", "default")
        char_index = char_options.index(current_char) if current_char in char_options else 0
        character_id = st.selectbox(
            t("bots.character", locale),
            char_options,
            index=char_index,
            format_func=character_label,
        )
        token_visible_key = "bot_token_visible"
        token_visible = bool(st.session_state.get(token_visible_key, False))
        token_col, show_col = st.columns([4, 1])
        with token_col:
            token = st.text_input(
                "Token",
                type="default" if token_visible else "password",
                value=draft.get("token", ""),
            )
        with show_col:
            st.write("")
            st.write("")
            if st.form_submit_button(t("bots.hide", locale) if token_visible else t("bots.show", locale)):
                st.session_state[token_visible_key] = not token_visible
                st.rerun()
        if platform == "discord":
            st.info(t("bots.streamlit.discord_hint", locale))
        enabled = st.checkbox(t("bots.enabled", locale), value=bool(draft.get("enabled", False)))

        col_save, col_cancel = st.columns(2)
        submitted = col_save.form_submit_button(t("bots.save", locale), type="primary")
        cancelled = col_cancel.form_submit_button(t("bots.streamlit.cancel_edit", locale) if editing else t("bots.streamlit.clear_form", locale))

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
            "settings": {},
        }
        if editing:
            r = requests.put(f"{api_base}/bots/{draft.get('bot_id')}", json=payload, timeout=10)
        else:
            payload["bot_id"] = bot_id
            r = requests.post(f"{api_base}/bots", json=payload, timeout=10)
        if r.ok:
            st.session_state.pop("bot_edit", None)
            st.success(t("bots.streamlit.saved", locale))
            st.rerun()
        else:
            st.error(r.text)
