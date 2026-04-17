# 【環境假設】：Python 3.12, Streamlit 1.30+
# 已遷移為瘦客戶端：透過 FastAPI REST API 讀寫設定。
import streamlit as st
import requests


def render_settings_page(api_base, user_prefs=None):
    st.title("⚙️ 系統設定")

    if not user_prefs:
        try:
            resp = requests.get(f"{api_base}/system/config", timeout=5)
            user_prefs = resp.json() if resp.ok else {}
        except Exception:
            user_prefs = {}
            st.error("無法從 API 載入設定。")

    col1, col2 = st.columns(2)
    with col1:
        st.header("🌐 全域 API 金鑰配置")
        new_openai_key = st.text_input("OpenAI API Key", type="password", value=user_prefs.get("openai_key", ""))
        new_or_key = st.text_input("OpenRouter API Key", type="password", value=user_prefs.get("or_key", ""))
        new_tavily_key = st.text_input("Tavily API Key (網路搜尋用)", type="password", value=user_prefs.get("tavily_api_key", ""))
        new_openweather_key = st.text_input("OpenWeather API Key (天氣查詢用)", type="password", value=user_prefs.get("openweather_api_key", ""),
                                             help="從 https://openweathermap.org/api 免費申請。用於即時天氣與預報查詢。")
        new_weather_city = st.text_input("天氣快取城市 (英文名)", value=user_prefs.get("weather_city", ""),
                                          help="設定後系統會自動快取該城市當天天氣並注入對話。使用英文城市名如 Taipei, Tokyo, New York。")
        new_llamacpp_url = st.text_input(
            "llama.cpp Server URL",
            value=user_prefs.get("llamacpp_url", "http://localhost:8080"),
            help="llama.cpp server 的位址（不含 /v1）。啟動指令範例：llama-server --model model.gguf --port 8080 --cont-batching -np 4",
        )
        new_tg_token = st.text_input("Telegram Bot Token", type="password", value=user_prefs.get("telegram_bot_token", ""),
                                      help="從 @BotFather 取得。設定後重啟伺服器即可啟用 Telegram 對話。留空則不啟動 Bot。")

    with col2:
        st.header("🛠️ 記憶與模型參數")
        new_temperature = st.slider("溫度 (Temperature)", 0.0, 1.0, user_prefs.get("temperature", 0.7), 0.1)
        new_ui_alpha = st.slider("基礎語意權重 (Alpha)", 0.0, 1.0, user_prefs.get("ui_alpha", 0.6), 0.1)
        new_memory_threshold = st.slider("綜合喚醒閾值", 0.0, 1.0, user_prefs.get("memory_threshold", 0.5), 0.05)
        new_memory_hard_base = st.slider("基礎語意斬殺線", 0.30, 0.90, user_prefs.get("memory_hard_base", 0.55), 0.05)
        new_shift_threshold = st.slider("話題偏移敏感度", 0.30, 0.85, user_prefs.get("shift_threshold", 0.55), 0.05)
        new_cluster_threshold = st.slider("大腦反芻收束閾值", 0.60, 0.90, user_prefs.get("cluster_threshold", 0.75), 0.05,
                                           help="數值越低，越容易將微弱相關的記憶縫合；數值越高，則只總結高度一致的話題。")

        st.header("🤖 雙層 Agent 架構")
        new_dual_layer_enabled = st.checkbox(
            "啟用異步雙層 Agent 模式", value=user_prefs.get("dual_layer_enabled", False),
            help="開啟後，對話將拆分為「意圖路由」與「角色渲染」兩階段。需要工具時會先播放過渡語音，並行執行工具查詢，消除等待空窗。")

        st.header("🧬 PersonaProbe 定時反思")
        new_persona_sync_enabled = st.checkbox("啟用定時人格反思", value=user_prefs.get("persona_sync_enabled", True),
                                               help="每 20 分鐘在系統閒置時檢查，累積足夠訊息後呼叫 PersonaProbe 進行深度分析。")
        new_persona_sync_min_messages = st.slider("最低訊息數閾值", 10, 200, user_prefs.get("persona_sync_min_messages", 50), 10,
                                                   help="上次反思後需累積多少筆訊息才觸發。")
        new_persona_sync_max_per_day = st.slider("每日反思上限", 1, 5, user_prefs.get("persona_sync_max_per_day", 2), 1,
                                                  help="每天最多執行幾次反思。")
        new_persona_sync_idle_minutes = st.slider("閒置判定時間（分鐘）", 1, 60, user_prefs.get("persona_sync_idle_minutes", 10), 1,
                                                   help="最後一筆訊息距今超過此時間才視為系統閒置。")
        new_persona_sync_fragment_limit = st.slider(
            "分析片段上限（近期 N 筆）", 100, 2000, user_prefs.get("persona_sync_fragment_limit", 400), 50,
            help="PersonaProbe 分析時取最近 N 筆對話。數值越大分析越全面但耗時越長，建議 300-600。")
        new_persona_probe_url = st.text_input("PersonaProbe API URL", value=user_prefs.get("persona_probe_url", "http://localhost:8089"),
                                               help="PersonaProbe server 的位址（start.bat 啟動後預設 port 8089）。")

        st.header("⏳ 背景搜集設定")
        default_hours = int(user_prefs.get("bg_gather_interval", 14400)) // 3600
        new_bg_gather_hours = st.number_input("背景話題搜集頻率 (小時)", min_value=1, max_value=168, value=default_hours)
        if st.button("🚀 馬上觸發一次背景搜尋"):
            try:
                r = requests.post(f"{api_base}/system/gather_now", timeout=5)
                if r.ok:
                    st.success(r.json().get("message", "已成功發送立即搜集訊號！系統將在 10 秒內開始執行。"))
                else:
                    st.error("發送失敗，請確認後端已重新啟動。")
            except Exception as e:
                st.error(f"連線失敗: {e}")

    if st.button("💾 儲存系統設定", use_container_width=True, type="primary"):
        update_payload = {
            "openai_key": new_openai_key,
            "or_key": new_or_key,
            "tavily_api_key": new_tavily_key,
            "openweather_api_key": new_openweather_key,
            "weather_city": new_weather_city,
            "bg_gather_interval": int(new_bg_gather_hours * 3600),
            "llamacpp_url": new_llamacpp_url,
            "telegram_bot_token": new_tg_token,
            "temperature": new_temperature,
            "ui_alpha": new_ui_alpha,
            "memory_threshold": new_memory_threshold,
            "memory_hard_base": new_memory_hard_base,
            "shift_threshold": new_shift_threshold,
            "cluster_threshold": new_cluster_threshold,
            "persona_sync_enabled": new_persona_sync_enabled,
            "persona_sync_min_messages": new_persona_sync_min_messages,
            "persona_sync_max_per_day": new_persona_sync_max_per_day,
            "persona_sync_idle_minutes": new_persona_sync_idle_minutes,
            "persona_sync_fragment_limit": new_persona_sync_fragment_limit,
            "persona_probe_url": new_persona_probe_url,
            "dual_layer_enabled": new_dual_layer_enabled,
        }
        try:
            resp = requests.put(f"{api_base}/system/config", json=update_payload, timeout=10)
            if resp.ok:
                st.success("系統設定已保存！")
            else:
                st.error(f"儲存失敗: {resp.text}")
        except Exception as e:
            st.error(f"儲存失敗: {e}")
