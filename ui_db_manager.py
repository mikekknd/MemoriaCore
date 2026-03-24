# 【環境假設】：Python 3.12, Streamlit 1.30+。記憶庫管理獨立視圖模組。
# 已遷移為瘦客戶端：所有業務邏輯透過 FastAPI REST API 執行。
import streamlit as st
import pandas as pd
import requests


@st.cache_data(ttl=10, show_spinner=False)
def _cached_personality(api_base):
    try:
        resp = requests.get(f"{api_base}/system/personality", timeout=5)
        if resp.ok:
            return resp.json().get("content", "")
    except Exception:
        pass
    return None


@st.cache_data(ttl=10, show_spinner=False)
def _cached_observations(api_base):
    try:
        resp = requests.get(f"{api_base}/system/personality/observations", timeout=10)
        if resp.ok:
            return resp.json()
    except Exception:
        pass
    return None


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


def render_db_manager_page(api_base, user_prefs):
    st.title("🧠 記憶庫與資料庫管理")

    current_cluster_threshold = user_prefs.get("cluster_threshold", 0.75)

    st.header("📦 短期對話快取區")
    if st.button("🗑️ 僅清空短期對話 (不打包)", use_container_width=True):
        if "api_session_id" in st.session_state:
            try:
                requests.delete(f"{api_base}/session/{st.session_state.api_session_id}", timeout=5)
                del st.session_state.api_session_id
                st.success("短期對話已清空！")
            except Exception as e:
                st.error(f"清空失敗: {e}")
        else:
            st.info("目前沒有活躍的對話 Session。")

    st.header("🌌 核心認知收束 (大腦反芻)")
    st.info(f"當前收束敏感度：{current_cluster_threshold:.2f} (數值越低越容易觸發跨領域聯想)")

    if st.button("✨ 執行大腦反芻 (提煉為重要記憶)", use_container_width=True, type="primary"):
        with st.spinner("系統正在進入睡眠模式，進行深度記憶融合..."):
            try:
                resp = requests.post(
                    f"{api_base}/system/consolidate",
                    json={"cluster_threshold": current_cluster_threshold, "min_group_size": 2},
                    timeout=300,
                )
                if resp.ok:
                    result = resp.json()
                    if result.get("status") == "no_clusters":
                        st.info("💤 目前沒有需要深度提煉的重複話題。")
                    else:
                        st.success(f"大腦反芻已啟動！發現 {result.get('cluster_count', 0)} 個話題群組正在背景處理。")
                else:
                    st.error(f"反芻失敗: {resp.text}")
            except Exception as e:
                st.error(f"反芻失敗: {e}")

    st.header("🎯 偏好聚合分析")
    st.info("從記憶區塊中的潛在偏好標籤進行純數學聚合，將高頻收斂的抽象偏好升格為長期使用者畫像。")
    agg_threshold = st.slider("升格積分閾值", min_value=1.0, max_value=10.0, value=3.0, step=0.5, key="pref_agg_threshold")
    if st.button("🔍 執行偏好聚合掃描", use_container_width=True):
        try:
            resp = requests.post(
                f"{api_base}/system/preference-aggregate",
                json={"score_threshold": agg_threshold},
                timeout=120,
            )
            if resp.ok:
                result = resp.json()
                promoted = result.get("promoted_count", 0)
                written = result.get("written", 0)
                if promoted > 0:
                    st.success(f"發現 {promoted} 個達標偏好，已升格 {written} 個至使用者畫像！")
                else:
                    st.info("目前沒有達到閾值的偏好標籤。")
            else:
                st.error(f"聚合失敗: {resp.text}")
        except Exception as e:
            st.error(f"聚合失敗: {e}")

    # ── AI 個性管理 ──
    st.header("🧬 AI 個性檔案管理")

    personality_tab, obs_tab = st.tabs(["📝 個性檔案", "🔍 觀察紀錄"])

    with personality_tab:
        try:
            current_personality = _cached_personality(api_base)
            if current_personality is not None:
                edited_personality = st.text_area(
                    "AI 個性檔案 (ai_personality.md)",
                    value=current_personality, height=300,
                    help="此檔案記錄 AI 的個性演化。你可以手動編輯，也可以讓系統透過反思機制自動更新。"
                )
                col_save, col_reflect = st.columns(2)
                with col_save:
                    if st.button("💾 保存個性修改", use_container_width=True):
                        try:
                            save_resp = requests.put(
                                f"{api_base}/system/personality",
                                json={"content": edited_personality}, timeout=10
                            )
                            if save_resp.ok:
                                st.success("個性檔案已保存！")
                            else:
                                st.error(f"保存失敗: {save_resp.text}")
                        except Exception as e:
                            st.error(f"保存失敗: {e}")
                with col_reflect:
                    if st.button("🔄 手動觸發反思", use_container_width=True, type="primary"):
                        with st.spinner("AI 正在進行自我反思..."):
                            try:
                                ref_resp = requests.post(
                                    f"{api_base}/system/personality/reflect", timeout=120
                                )
                                if ref_resp.ok:
                                    result = ref_resp.json()
                                    if result.get("status") == "success":
                                        st.success("反思完成！個性檔案已更新。")
                                        st.rerun()
                                    else:
                                        st.info(result.get("message", "無待反思觀察"))
                                else:
                                    st.error(f"反思失敗: {ref_resp.text}")
                            except Exception as e:
                                st.error(f"反思失敗: {e}")
            else:
                st.error("無法載入個性檔案")
        except Exception as e:
            st.error(f"載入個性檔案失敗: {e}")


    with obs_tab:
        try:
            obs_data = _cached_observations(api_base)
            if obs_data:
                pending_count = obs_data.get("pending_count", 0)
                observations = obs_data.get("observations", [])

                st.metric("待反思觀察", f"{pending_count} 筆")

                if observations:
                    df_obs = pd.DataFrame([{
                        "時間": o["timestamp"][:19],
                        "類別": o["category"],
                        "萃取特徵": o["extracted_trait"],
                        "原文": o["raw_statement"][:80],
                        "次數": int(o["encounter_count"]),
                        "狀態": "✅ 已反思" if o["is_reflected"] else "⏳ 待反思",
                    } for o in observations])
                    st.dataframe(df_obs, use_container_width=True)
                else:
                    st.info("尚無 AI 自我觀察紀錄。開始對話後，系統會自動提取 AI 的自我陳述。")
            else:
                st.error("載入觀察紀錄失敗")
        except Exception as e:
            st.error(f"載入觀察紀錄失敗: {e}")

    st.divider()

    # ── 對話歷史瀏覽 ──
    st.header("💬 跨介面對話歷史")

    # 自動清理區塊
    col_filter, col_cleanup = st.columns([2, 1])
    with col_filter:
        channel_labels = {"all": "全部", "streamlit": "🌐 Streamlit", "telegram": "📱 Telegram",
                          "websocket": "🔌 WebSocket", "rest": "🔗 REST"}
        selected_channel = st.selectbox("篩選介面", list(channel_labels.keys()),
                                         format_func=lambda k: channel_labels[k], key="hist_channel")
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
                                        # 如果刪除的是當前 session，清除本地狀態
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
                                detail_resp = requests.get(
                                    f"{api_base}/session/history/{sid}", timeout=10
                                )
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

    st.divider()
    # ── 合成資料生成器 ──
    st.header("🧪 開發者測試模式")
    with st.expander("🏭 模擬資料生成器", expanded=False):
        default_topics = [
            "晚餐去吃了一家很棒的豚骨拉麵，湯頭非常濃郁",
            "分享剛看完的一部賽璐璐畫風動畫，色彩通透感極佳",
            "明天預計要繼續開發 Python 畫面即時翻譯軟體",
        ]
        test_topic_selection = st.selectbox("選擇預設主題", ["(自訂主題)"] + default_topics)
        custom_test_topic = st.text_input("或自訂輸入主題：", value="" if test_topic_selection != "(自訂主題)" else "")
        final_topic = custom_test_topic if custom_test_topic else test_topic_selection
        test_turns = st.slider("預期回合數", 3, 15, 8)

        if st.button("🚀 生成並注入此筆記憶", use_container_width=True):
            if final_topic == "(自訂主題)" or not final_topic.strip():
                st.error("請選擇或輸入有效的主題！")
            else:
                with st.spinner("路由系統正在分配模型生成對話與記憶概覽..."):
                    try:
                        resp = requests.post(
                            f"{api_base}/system/synthetic",
                            json={"topic": final_topic, "turns": test_turns},
                            timeout=300,
                        )
                        if resp.ok:
                            result = resp.json()
                            if result.get("status") == "success":
                                st.success("✅ 測試資料注入成功！")
                                st.info(result.get("overview", ""))
                            else:
                                st.error(f"❌ 注入失敗: {result.get('error', '未知錯誤')}")
                        else:
                            st.error(f"API 錯誤: {resp.text}")
                    except Exception as e:
                        st.error(f"系統錯誤: {e}")

    st.divider()

    st.header("🗄️ 底層記憶資料庫 (透過 API)")
    if st.button("🔄 載入目前資料庫內容"):
        try:
            # 情境記憶區塊
            blocks_resp = requests.get(f"{api_base}/memory/blocks", timeout=10)
            if blocks_resp.ok:
                blocks = blocks_resp.json()
                st.subheader("📖 情境記憶區塊 (Memory Blocks)")
                if blocks:
                    df_blocks = pd.DataFrame([{
                        "timestamp": b["timestamp"],
                        "encounter_count": b["encounter_count"],
                        "overview": b["overview"],
                        "is_consolidated": b["is_consolidated"],
                    } for b in blocks])
                    st.dataframe(df_blocks, use_container_width=True)
                else:
                    st.info("尚無情境記憶。")
            else:
                st.error(f"載入記憶區塊失敗: {blocks_resp.text}")

            # 核心認知
            core_resp = requests.get(f"{api_base}/memory/core", timeout=10)
            if core_resp.ok:
                cores = core_resp.json()
                st.subheader("💎 長期核心認知 (Core Memories)")
                if cores:
                    df_core = pd.DataFrame([{
                        "timestamp": c["timestamp"],
                        "encounter_count": c["encounter_count"],
                        "insight": c["insight"],
                    } for c in cores])
                    st.dataframe(df_core, use_container_width=True)
                else:
                    st.info("尚無核心認知。")
            else:
                st.error(f"載入核心認知失敗: {core_resp.text}")

            # 使用者畫像
            profile_resp = requests.get(f"{api_base}/profile?include_tombstones=true", timeout=10)
            if profile_resp.ok:
                profiles = profile_resp.json()
                st.subheader("📋 使用者畫像 (User Profile)")
                if profiles:
                    df_profile = pd.DataFrame([{
                        "fact_key": p["fact_key"],
                        "fact_value": p["fact_value"],
                        "category": p["category"],
                        "status": "🪦 已撤回" if p.get("confidence", 1) < 0 else "✅ 有效",
                        "confidence": p.get("confidence", 1),
                        "timestamp": p.get("timestamp", ""),
                        "source_context": p.get("source_context", ""),
                    } for p in profiles])
                    st.dataframe(df_profile, use_container_width=True)
                else:
                    st.info("尚無使用者畫像資料。")
            else:
                st.error(f"載入使用者畫像失敗: {profile_resp.text}")

            st.success("資料載入完成！")
        except Exception as e:
            st.error(f"載入資料庫失敗: {e}")
