# 【環境假設】：Python 3.12, Streamlit 1.30+
# 跨介面對話歷史獨立視圖模組。
#
# ⚠️ DEPRECATED: 對話歷史已移至 dashboard.html，本檔案保留作為參考，計畫移除。
#   移除進度：Streamlit 導航已移除（2026-04-27）。
import streamlit as st
import requests


@st.cache_data(ttl=5, show_spinner=False)
def _cached_session_history(api_base, channel="all", limit=50):
    try:
        params = {"limit": limit}
        if channel != "all":
            params["channel"] = channel
        resp = requests.get(f"{api_base}/session/history", params=params, timeout=10)
        if resp.ok:
            return resp.json()
    except Exception:
        pass
    return None


def render_history_page(api_base, user_prefs):
    st.title("💬 對話歷史")

    # ── 篩選與清理 ──
    col_filter, col_cleanup = st.columns([2, 1])
    with col_filter:
        channel_labels = {
            "all": "全部",
            "streamlit": "🌐 Streamlit",
            "telegram": "📱 Telegram",
            "websocket": "🔌 WebSocket",
            "rest": "🔗 REST",
        }
        selected_channel = st.selectbox(
            "篩選介面", list(channel_labels.keys()),
            format_func=lambda k: channel_labels[k], key="hist_channel",
        )
    with col_cleanup:
        cleanup_days = st.number_input("自動清理天數", min_value=1, max_value=365, value=30, key="cleanup_days")
        if st.button("🗑️ 清理舊紀錄", use_container_width=True):
            try:
                resp = requests.delete(f"{api_base}/session/history/cleanup/{cleanup_days}", timeout=30)
                if resp.ok:
                    result = resp.json()
                    _cached_session_history.clear()
                    st.success(f"已刪除 {result.get('deleted_count', 0)} 個超過 {cleanup_days} 天的 session")
                    st.rerun()
                else:
                    st.error(f"清理失敗: {resp.text}")
            except Exception as e:
                st.error(f"清理失敗: {e}")

    st.divider()

    # ── Session 列表 ──
    try:
        sessions = _cached_session_history(api_base, channel=selected_channel, limit=50)
        if sessions is not None:
            if sessions:
                for sess in sessions:
                    ch = sess.get("channel", "rest")
                    sid = sess["session_id"]
                    ch_icon = {"streamlit": "🌐", "telegram": "📱", "websocket": "🔌", "rest": "🔗"}.get(ch, "❓")
                    status = "🟢 活躍" if sess.get("is_active") else "⚪ 已結束"
                    label = f"{ch_icon} {ch} | {sess['created_at'][:16]} | {sess.get('message_count', 0)} 則 | {status}"
                    with st.expander(label):
                        col_msgs, col_del = st.columns([5, 1])
                        with col_del:
                            if st.button("🗑️ 刪除", key=f"del_{sid}"):
                                try:
                                    del_resp = requests.delete(f"{api_base}/session/history/{sid}", timeout=10)
                                    if del_resp.ok:
                                        if st.session_state.get("api_session_id") == sid:
                                            del st.session_state["api_session_id"]
                                            if "chat_messages_cache" in st.session_state:
                                                del st.session_state["chat_messages_cache"]
                                        _cached_session_history.clear()
                                        st.success("已永久刪除")
                                        st.rerun()
                                    else:
                                        st.error(f"刪除失敗: {del_resp.text}")
                                except Exception as e:
                                    st.error(f"刪除失敗: {e}")
                        with col_msgs:
                            try:
                                detail_resp = requests.get(f"{api_base}/session/history/{sid}", timeout=10)
                                if detail_resp.ok:
                                    history = detail_resp.json()
                                    for msg in history.get("messages", []):
                                        role_icon = "🧑" if msg["role"] == "user" else "🤖"
                                        st.markdown(f"**{role_icon} {msg['role']}**: {msg['content'][:500]}")
                                else:
                                    st.warning("無法載入此 session 的訊息。")
                            except Exception:
                                st.warning("載入訊息時發生錯誤。")
            else:
                st.info("尚無對話歷史紀錄。開始對話後，紀錄會自動保存。")
        else:
            st.error("載入歷史失敗")
    except Exception as e:
        st.error(f"載入對話歷史失敗: {e}")
