# 【環境假設】：Python 3.12, Streamlit 1.30+。記憶庫管理獨立視圖模組。
# 已遷移為瘦客戶端：所有業務邏輯透過 FastAPI REST API 執行。
import streamlit as st
import pandas as pd
from ui import api_client as requests

def render_db_manager_page(api_base, user_prefs):
    st.title("🧠 記憶庫管理")

    tab_memory, tab_raw_db, tab_dev = st.tabs([
        "🌌 記憶操作", "🗄️ 底層資料庫", "🧪 開發者工具",
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
    # Tab 2: 底層資料庫
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
