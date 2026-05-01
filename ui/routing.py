# 【環境假設】：Python 3.12, Streamlit 1.30+
# 任務路由映射獨立視圖模組。
import streamlit as st
from core.i18n import DEFAULT_LOCALE, normalize_locale, t
from ui import api_client as requests
def render_routing_page(api_base, user_prefs=None):
    if not user_prefs:
        try:
            resp = requests.get(f"{api_base}/system/config", timeout=5)
            user_prefs = resp.json() if resp.ok else {}
        except Exception:
            user_prefs = {}
            st.error("無法從 API 載入設定。")

    try:
        current_locale = normalize_locale(user_prefs.get("ui_locale"))
    except ValueError:
        current_locale = DEFAULT_LOCALE

    st.title(t("routing.streamlit.title", current_locale))

    routing_config = user_prefs.get("routing_config", {})
    provider_names = ["Ollama (本地)", "llama.cpp (本地)", "OpenAI (雲端)", "OpenRouter (雲端)"]
    provider_label_keys = {
        "Ollama (本地)": "routing.provider.ollama",
        "llama.cpp (本地)": "routing.provider.llamacpp",
        "OpenAI (雲端)": "routing.provider.openai",
        "OpenRouter (雲端)": "routing.provider.openrouter",
    }

    task_keys = [
        "chat", "expand", "pipeline", "compress", "distill", "ep_fuse",
        "profile", "persona_sync", "persona_seed", "background_gather",
        "character_gen", "router", "group_router", "translate", "browser",
    ]
    task_infos = {
        task_key: {
            "desc": t(f"routing.tasks.{task_key}.desc", current_locale),
            "help": t(f"routing.tasks.{task_key}.help", current_locale),
        }
        for task_key in task_keys
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
            p_sel = st.selectbox(
                t("routing.streamlit.provider", current_locale, task=task_key),
                provider_names,
                index=p_idx,
                key=f"p_{task_key}",
                format_func=lambda p: t(provider_label_keys.get(p, p), current_locale),
            )

            if not saved_model:
                saved_model = "qwen3.5"

            m_sel = st.text_input(
                t("routing.streamlit.model", current_locale, task=task_key),
                value=saved_model,
                key=f"m_{task_key}",
                help=info["help"],
            )

            new_routing_config[task_key] = {"provider": p_sel, "model": m_sel}

    if st.button("💾 儲存路由設定", use_container_width=True, type="primary"):
        try:
            resp = requests.put(
                f"{api_base}/system/config",
                json={"routing_config": new_routing_config},
                timeout=10,
            )
            if resp.ok:
                st.success(t("routing.streamlit.saved", current_locale))
            else:
                st.error(t("routing.streamlit.save_failed", current_locale, message=resp.text))
        except Exception as e:
            st.error(t("routing.streamlit.save_failed", current_locale, message=e))
