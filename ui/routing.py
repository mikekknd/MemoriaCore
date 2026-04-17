# 【環境假設】：Python 3.12, Streamlit 1.30+
# 任務路由映射獨立視圖模組。
import streamlit as st
import requests


def render_routing_page(api_base, user_prefs=None):
    st.title("🔀 任務路由映射")

    if not user_prefs:
        try:
            resp = requests.get(f"{api_base}/system/config", timeout=5)
            user_prefs = resp.json() if resp.ok else {}
        except Exception:
            user_prefs = {}
            st.error("無法從 API 載入設定。")

    routing_config = user_prefs.get("routing_config", {})
    provider_names = ["Ollama (本地)", "llama.cpp (本地)", "OpenAI (雲端)", "OpenRouter (雲端)"]

    task_infos = {
        "chat": {"desc": "即時對話 (帶影子標籤)", "help": "處理玩家對話，同時伴隨生成實體標籤。建議高參數量模型。"},
        "expand": {"desc": "User意圖擴充 (秒速提取)", "help": "在提問瞬間提取使用者話語中的高密度名詞。"},
        "pipeline": {"desc": "一體化記憶管線", "help": "在背景一次性完成長文切分、摘要與圖譜修復。"},
        "compress": {"desc": "對話壓縮 (編年史化)", "help": "將過長的歷史對話壓縮為高密度編年史摘要。"},
        "distill": {"desc": "核心認知提煉 (Insight)", "help": "從情境記憶提煉使用者的深層特徵與長期價值觀。"},
        "ep_fuse": {"desc": "情境概覽縫合", "help": "將多段相關的情境記憶合併為一個高密度的綜合概覽。"},
        "profile": {"desc": "使用者畫像更新", "help": "從對話中萃取使用者的個人特徵與偏好。"},
        "background_gather": {"desc": "背景話題摘要", "help": "在背景將 Tavily 搜尋下來的資料摘要成主動話題。建議不需太強的模型。"},
        "character_gen": {"desc": "角色設定生成", "help": "根據簡短描述利用 AI 擴充出完整的角色系統提示詞與心理指標。"},
        "router": {"desc": "意圖路由預處理", "help": "雙層 Agent 模式的第一階段：判斷是否需要呼叫外部工具並產生過渡語音。建議使用輕量快速模型以降低延遲。"},
        "persona_sync": {"desc": "PersonaProbe 人格反思", "help": "定時呼叫 PersonaProbe 進行深度 6 維度人格分析（每次約 8 次 LLM 呼叫）。建議使用能力較強的模型以確保分析品質。"},
    }

    new_routing_config = {}
    for task_key, info in task_infos.items():
        saved_prov = routing_config.get(task_key, {}).get("provider", "Ollama (本地)")
        saved_model = routing_config.get(task_key, {}).get("model", "")
        display_model = saved_model or "qwen3.5"
        prov_short = {"Ollama (本地)": "Ollama", "llama.cpp (本地)": "llama.cpp",
                      "OpenAI (雲端)": "OpenAI", "OpenRouter (雲端)": "OpenRouter"}.get(saved_prov, saved_prov)
        label = f"**{info['desc']}**　→　`{prov_short}` / `{display_model}`"
        with st.expander(label, expanded=False):

            p_idx = provider_names.index(saved_prov) if saved_prov in provider_names else 0
            p_sel = st.selectbox(f"供應商 ({task_key})", provider_names, index=p_idx, key=f"p_{task_key}")

            if not saved_model:
                saved_model = "qwen3.5"

            m_sel = st.text_input(f"模型名稱 ({task_key})", value=saved_model, key=f"m_{task_key}", help=info["help"])

            new_routing_config[task_key] = {"provider": p_sel, "model": m_sel}

    if st.button("💾 儲存路由設定", use_container_width=True, type="primary"):
        try:
            resp = requests.put(
                f"{api_base}/system/config",
                json={"routing_config": new_routing_config},
                timeout=10,
            )
            if resp.ok:
                st.success("路由設定已保存！")
            else:
                st.error(f"儲存失敗: {resp.text}")
        except Exception as e:
            st.error(f"儲存失敗: {e}")
