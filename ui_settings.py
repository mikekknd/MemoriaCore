# 【環境假設】：Python 3.12, Streamlit 1.30+
# 已遷移為瘦客戶端：透過 FastAPI REST API 讀寫設定。
import streamlit as st
import requests


def render_settings_page(api_base):
    st.title("⚙️ 系統與路由設定")

    # 從 API 載入設定
    try:
        resp = requests.get(f"{api_base}/system/config", timeout=5)
        user_prefs = resp.json() if resp.ok else {}
    except Exception:
        user_prefs = {}
        st.error("無法從 API 載入設定。")

    routing_config = user_prefs.get("routing_config", {})

    col1, col2 = st.columns(2)
    with col1:
        st.header("🌐 全域 API 金鑰配置")
        new_openai_key = st.text_input("OpenAI API Key", type="password", value=user_prefs.get("openai_key", ""))
        new_or_key = st.text_input("OpenRouter API Key", type="password", value=user_prefs.get("or_key", ""))
        new_tg_token = st.text_input("Telegram Bot Token", type="password", value=user_prefs.get("telegram_bot_token", ""),
                                      help="從 @BotFather 取得。設定後重啟伺服器即可啟用 Telegram 對話。留空則不啟動 Bot。")

        st.header("🧲 基礎向量引擎")
        new_embed_model = st.text_input("Embedding 模型名稱", value=user_prefs.get("embed_model", "bge-m3:latest"))

    with col2:
        st.header("🛠️ 進階模型參數")
        new_temperature = st.slider("溫度 (Temperature)", 0.0, 1.0, user_prefs.get("temperature", 0.7), 0.1)
        new_ui_alpha = st.slider("基礎語意權重 (Alpha)", 0.0, 1.0, user_prefs.get("ui_alpha", 0.6), 0.1)
        new_memory_threshold = st.slider("綜合喚醒閾值", 0.0, 1.0, user_prefs.get("memory_threshold", 0.5), 0.05)
        new_memory_hard_base = st.slider("基礎語意斬殺線", 0.30, 0.90, user_prefs.get("memory_hard_base", 0.55), 0.05)
        new_shift_threshold = st.slider("話題偏移敏感度", 0.30, 0.85, user_prefs.get("shift_threshold", 0.55), 0.05)
        new_cluster_threshold = st.slider("大腦反芻收束閾值", 0.60, 0.90, user_prefs.get("cluster_threshold", 0.75), 0.05,
                                           help="數值越低，越容易將微弱相關的記憶縫合；數值越高，則只總結高度一致的話題。")

        st.subheader("🧬 AI 人格演化")
        new_ai_observe_enabled = st.checkbox("啟用 AI 自我觀察", value=user_prefs.get("ai_observe_enabled", True),
                                              help="開啟後，每輪對話結束會由 LLM 分析 AI 是否有自我陳述。")
        new_reflection_threshold = st.slider("反思觸發閾值", 3, 20, user_prefs.get("reflection_threshold", 5), 1,
                                              help="累積多少筆 AI 自我觀察後，在話題偏移時自動觸發人格反思。")

    st.header("⚙️ 異質任務路由映射表 (雙軌混合版)")
    provider_names = ["Ollama (本地)", "OpenAI (雲端)", "OpenRouter (雲端)"]

    # 從 Ollama 抓取可用模型列表（快取避免重複請求）
    @st.cache_data(ttl=60)
    def _fetch_ollama_models():
        try:
            r = requests.get("http://localhost:11434/api/tags", timeout=3)
            if r.ok:
                return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            pass
        return []

    ollama_models = _fetch_ollama_models()

    task_infos = {
        "chat": {"desc": "即時對話 (帶影子標籤)", "help": "處理玩家對話，同時伴隨生成實體標籤。建議高參數量模型。"},
        "expand": {"desc": "User意圖擴充 (秒速提取)", "help": "在提問瞬間提取使用者話語中的高密度名詞。"},
        "pipeline": {"desc": "一體化記憶管線", "help": "在背景一次性完成長文切分、摘要與圖譜修復。"},
        "compress": {"desc": "對話壓縮 (編年史化)", "help": "將過長的歷史對話壓縮為高密度編年史摘要。"},
        "distill": {"desc": "核心認知提煉 (Insight)", "help": "從情境記憶提煉使用者的深層特徵與長期價值觀。"},
        "ep_fuse": {"desc": "情境概覽縫合", "help": "將多段相關的情境記憶合併為一個高密度的綜合概覽。"},
        "profile": {"desc": "使用者畫像更新", "help": "從對話中萃取使用者的個人特徵與偏好。"},
        "ai_observe": {"desc": "AI 自我觀察提取", "help": "從對話中偵測 AI 的自我陳述與行為變化。建議快速小模型。"},
        "ai_reflect": {"desc": "AI 人格反思", "help": "綜合觀察紀錄更新 AI 個性檔案。建議高品質模型。"},
    }

    new_routing_config = {}
    for task_key, info in task_infos.items():
        with st.expander(f"任務: {info['desc']}", expanded=False):
            saved_prov = routing_config.get(task_key, {}).get("provider", "Ollama (本地)")
            saved_model = routing_config.get(task_key, {}).get("model", "")

            p_idx = provider_names.index(saved_prov) if saved_prov in provider_names else 0
            p_sel = st.selectbox(f"供應商 ({task_key})", provider_names, index=p_idx, key=f"p_{task_key}")

            if not saved_model:
                saved_model = "qwen3.5"

            # Ollama 供應商：顯示下拉選單；其他供應商：手動輸入
            if p_sel == "Ollama (本地)" and ollama_models:
                # 確保已儲存的模型在列表中，否則附加到末尾
                model_options = list(ollama_models)
                if saved_model and saved_model not in model_options:
                    model_options.append(saved_model)
                m_idx = model_options.index(saved_model) if saved_model in model_options else 0
                m_sel = st.selectbox(f"模型 ({task_key})", model_options, index=m_idx, key=f"m_{task_key}", help=info["help"])
            else:
                m_sel = st.text_input(f"模型名稱 ({task_key})", value=saved_model, key=f"m_{task_key}", help=info["help"])

            new_routing_config[task_key] = {"provider": p_sel, "model": m_sel}

    if st.button("💾 儲存所有系統與路由設定", use_container_width=True, type="primary"):
        update_payload = {
            "openai_key": new_openai_key,
            "or_key": new_or_key,
            "telegram_bot_token": new_tg_token,
            "embed_model": new_embed_model,
            "temperature": new_temperature,
            "ui_alpha": new_ui_alpha,
            "memory_threshold": new_memory_threshold,
            "memory_hard_base": new_memory_hard_base,
            "shift_threshold": new_shift_threshold,
            "cluster_threshold": new_cluster_threshold,
            "ai_observe_enabled": new_ai_observe_enabled,
            "reflection_threshold": new_reflection_threshold,
            "routing_config": new_routing_config,
        }
        try:
            resp = requests.put(f"{api_base}/system/config", json=update_payload, timeout=10)
            if resp.ok:
                st.success("系統設定與路由拓撲已保存！請重新整理頁面以套用新設定。")
            else:
                st.error(f"儲存失敗: {resp.text}")
        except Exception as e:
            st.error(f"儲存失敗: {e}")
