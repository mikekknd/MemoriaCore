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
            return resp.json()  # 回傳完整 dict：content, has_evolved, character_name
    except Exception:
        pass
    return None


@st.cache_data(ttl=30, show_spinner=False)
def _cached_sync_status(api_base):
    try:
        resp = requests.get(f"{api_base}/system/personality/sync-status", timeout=5)
        if resp.ok:
            return resp.json()
    except Exception:
        pass
    return None


def render_db_manager_page(api_base, user_prefs):
    st.title("🧠 記憶庫管理")

    tab_memory, tab_personality, tab_raw_db, tab_dev = st.tabs([
        "🌌 記憶操作", "🧬 AI 個性", "🗄️ 底層資料庫", "🧪 開發者工具",
    ])

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Tab 1: 記憶操作
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    with tab_memory:
        current_cluster_threshold = user_prefs.get("cluster_threshold", 0.75)

        st.subheader("核心認知收束 (大腦反芻)")
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

        st.divider()

        st.subheader("偏好聚合分析")
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

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Tab 2: AI 個性
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    with tab_personality:
        # ── PersonaSync 狀態區 ──────────────────────────────
        st.subheader("🧬 PersonaProbe 同步狀態")
        sync_status = _cached_sync_status(api_base)
        if sync_status:
            col_s1, col_s2, col_s3 = st.columns(3)
            col_s1.metric("今日已執行", f"{sync_status.get('today_run_count', 0)} 次")
            col_s2.metric("上次反思時間", sync_status.get("last_reflection_at", "從未") or "從未")
            col_s3.metric("距上次反思訊息數", f"{sync_status.get('messages_since_last', 0)} 筆")
        else:
            st.info("無法取得同步狀態（後端可能未啟動）。")

        if st.button("🚀 立即執行 PersonaProbe 反思", use_container_width=True, type="primary"):
            with st.spinner("正在呼叫 PersonaProbe 進行深度人格分析（約需 1-2 分鐘）..."):
                try:
                    ref_resp = requests.post(f"{api_base}/system/personality/sync-now", timeout=660)
                    if ref_resp.ok:
                        result = ref_resp.json()
                        status = result.get("status", "")
                        if status == "success":
                            st.success("✅ PersonaProbe 反思完成！個性檔案已更新。")
                            st.cache_data.clear()
                            st.rerun()
                        elif status == "skipped":
                            st.info(f"⏭️ 已跳過：{result.get('reason', '')}")
                        else:
                            st.error(f"執行失敗：{result.get('message', ref_resp.text)}")
                    else:
                        st.error(f"API 錯誤：{ref_resp.text}")
                except Exception as e:
                    st.error(f"請求失敗: {e}")

        st.divider()

        # ── 演化人設檢視 / 手動編輯 ────────────────────────
        try:
            personality_data = _cached_personality(api_base)
            if personality_data is not None:
                char_name = personality_data.get("character_name", "")
                has_evolved = personality_data.get("has_evolved", False)
                current_content = personality_data.get("content", "")

                if has_evolved:
                    st.subheader(f"🧬 演化人設 — {char_name}")
                    st.caption("目前使用 PersonaProbe 產出的演化版本。儲存將覆寫演化內容。")
                else:
                    st.subheader(f"📝 原始人設 — {char_name}")
                    st.caption("尚無演化版本，目前使用原始 system_prompt。反思完成後此處將顯示演化內容。")

                edited_personality = st.text_area(
                    "人設內容",
                    value=current_content, height=400,
                    help="由 PersonaProbe 定期自動更新。你也可以直接在此手動調整後儲存至演化人設。",
                )
                if st.button("💾 保存為演化人設", use_container_width=True):
                    try:
                        save_resp = requests.put(
                            f"{api_base}/system/personality",
                            json={"content": edited_personality}, timeout=10,
                        )
                        if save_resp.ok:
                            st.success("演化人設已保存！")
                            st.cache_data.clear()
                        else:
                            st.error(f"保存失敗: {save_resp.text}")
                    except Exception as e:
                        st.error(f"保存失敗: {e}")
            else:
                st.error("無法載入人設資料")
        except Exception as e:
            st.error(f"載入人設失敗: {e}")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Tab 3: 底層資料庫
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    with tab_raw_db:
        if st.button("🔄 載入目前資料庫內容", key="load_raw_db"):
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

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Tab 4: 開發者工具
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    with tab_dev:
        st.subheader("🏭 模擬資料生成器")
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
