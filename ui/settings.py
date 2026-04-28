# 【環境假設】：Python 3.12, Streamlit 1.30+
# 已遷移為瘦客戶端：透過 FastAPI REST API 讀寫設定。
import streamlit as st
from ui import api_client as requests
def render_settings_page(api_base, user_prefs=None):
    st.title("⚙️ 系統設定")

    if not user_prefs:
        try:
            resp = requests.get(f"{api_base}/system/config", timeout=5)
            user_prefs = resp.json() if resp.ok else {}
        except Exception:
            user_prefs = {}
            st.error("無法從 API 載入設定。")

    # ── ⚠️ SU 身份設定 ───────────────────────────────────────
    st.divider()
    st.header("🔐 SU 身份設定")
    st.caption(
        "⚠️ **風險提示**：此欄位涉及系統敏感設定。\n"
        "此功能一旦寫入，匹配的 Telegram 用戶即獲得 **private face** 身份，"
        "可讀寫所有 `visibility='private'` 的記憶。\n\n"
        "**上線前務必確認：**\n"
        "1. `/system/config` API 已透過防火牆或 API Key 做存取控制\n"
        "2. server 只暴露於信任的網路區段（勿對外網開放）\n\n"
        "詳見：`api/models/requests.py` 中的 `su_user_id` 安全性標註。"
    )
    new_su_user_id = st.text_input(
        "SU User ID（Telegram UID）",
        value=user_prefs.get("su_user_id", ""),
        placeholder="輸入你的 Telegram UID（數字）",
        help="設定後，user_id 匹配此值的 Telegram 用戶將獲得 private face 身份。更改後無需重啟，熱重載生效。",
    )

    col1, col2 = st.columns(2)
    with col1:
        st.header("🌐 全域 API 金鑰配置")
        new_openai_key = st.text_input("OpenAI API Key", type="password", value=user_prefs.get("openai_key", ""))
        new_or_key = st.text_input("OpenRouter API Key", type="password", value=user_prefs.get("or_key", ""))
        new_minimax_key = st.text_input(
            "MiniMax API Key（TTS / 圖片生成共用）",
            type="password",
            value=user_prefs.get("minimax_api_key", ""),
            help="從 https://platform.minimax.io 取得。TTS 與 MiniMax 圖片生成共用此 Key。",
        )
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

    # ── TTS 設定（橫跨全寬） ─────────────────────────────────
    st.divider()
    st.header("🔊 語音合成（Minimax TTS）")

    tts_col1, tts_col2 = st.columns(2)
    with tts_col1:
        new_tts_enabled = st.checkbox(
            "啟用語音合成（TTS）",
            value=user_prefs.get("tts_enabled", False),
            help="開啟後，AI 每次回覆都會呼叫 Minimax TTS 合成語音，並透過 WebSocket 以 tts_audio 事件傳送給 client。",
        )
        st.caption("TTS 使用上方「MiniMax API Key（TTS / 圖片生成共用）」。")
        new_minimax_voice = st.text_input(
            "Voice ID",
            value=user_prefs.get("minimax_voice_id", "moss_audio_7c2b39d9-1006-11f1-b9c4-4ea5324904c7"),
            help="Minimax 聲音 ID，可在平台的語音庫中查看。",
        )
        new_minimax_model = st.selectbox(
            "TTS 模型",
            options=["speech-2.8-hd", "speech-2.8", "speech-2-hd", "speech-2"],
            index=["speech-2.8-hd", "speech-2.8", "speech-2-hd", "speech-2"].index(
                user_prefs.get("minimax_model", "speech-2.8-hd")
            ) if user_prefs.get("minimax_model", "speech-2.8-hd") in ["speech-2.8-hd", "speech-2.8", "speech-2-hd", "speech-2"] else 0,
            help="speech-2.8-hd 音質最高；speech-2 速度最快。",
        )

    with tts_col2:
        new_minimax_speed = st.slider(
            "語速（Speed）", min_value=0.5, max_value=2.0,
            value=float(user_prefs.get("minimax_speed", 1.0)), step=0.1,
            help="1.0 為正常語速，2.0 為兩倍速。",
        )
        new_minimax_vol = st.slider(
            "音量（Volume）", min_value=0.1, max_value=2.0,
            value=float(user_prefs.get("minimax_vol", 1.0)), step=0.1,
            help="1.0 為正常音量。",
        )
        new_minimax_pitch = st.slider(
            "音調（Pitch）", min_value=-12, max_value=12,
            value=int(user_prefs.get("minimax_pitch", 0)), step=1,
            help="0 為原聲音調，正值升調，負值降調。",
        )

        # TTS 連線測試
        st.markdown("&nbsp;", unsafe_allow_html=True)
        if st.button("🧪 測試 TTS 連線", disabled=not new_tts_enabled):
            _test_key = new_minimax_key or user_prefs.get("minimax_api_key", "")
            if not _test_key:
                st.warning("請先填入 Minimax API Key。")
            else:
                with st.spinner("合成測試語音中…"):
                    try:
                        import asyncio
                        from core.tts_client import MinimaxTTSClient
                        _client = MinimaxTTSClient(
                            api_key=_test_key,
                            voice_id=new_minimax_voice,
                            model=new_minimax_model,
                            speed=new_minimax_speed,
                            vol=new_minimax_vol,
                            pitch=new_minimax_pitch,
                        )
                        _audio = asyncio.run(_client.synthesize("你好，TTS 連線測試成功！"))
                        if _audio:
                            st.success(f"✅ TTS 合成成功！音頻大小：{len(_audio):,} bytes")
                            st.audio(_audio, format="audio/mp3")
                        else:
                            st.error("❌ 合成失敗，請確認 API Key 與網路連線。")
                    except Exception as e:
                        st.error(f"❌ 測試失敗：{e}")

    # ── MiniMax 圖片生成設定 ────────────────────────────────
    st.divider()
    st.header("🖼️ 圖片生成（MiniMax Image）")
    new_image_generation_enabled = st.checkbox(
        "啟用圖片生成 Tool",
        value=user_prefs.get("image_generation_enabled", False),
        help="開啟後，AI 可在使用者明確要求產生圖片時呼叫 MiniMax image-01 文字生圖工具。",
    )
    st.caption("圖片生成與 TTS 共用上方 MiniMax API Key，但開關彼此獨立。")

    # ── Bash Tool 設定（橫跨全寬） ─────────────────────────────
    st.divider()
    st.header("💻 Bash Tool（本機指令執行）")

    from tools.bash_tool import PRESET_GROUPS

    bash_enabled = st.checkbox(
        "啟用 Bash Tool",
        value=user_prefs.get("bash_tool_enabled", False),
        help="開啟後 AI 可執行本機 shell 指令。僅允許下方勾選或手動填入的指令前綴，其餘一律拒絕。",
    )

    current_allowed: list[str] = user_prefs.get("bash_tool_allowed_commands", [])

    bash_col1, bash_col2 = st.columns(2)
    group_selected: list[str] = []

    with st.container():
        st.markdown("**預設允許指令群組：**")
        cols = st.columns(2)
        for i, (label, cmds) in enumerate(PRESET_GROUPS):
            default_checked = any(c in current_allowed for c in cmds)
            checked = cols[i % 2].checkbox(
                f"{label}  `{'`, `'.join(cmds)}`",
                value=default_checked,
                key=f"bash_group_{i}",
                disabled=not bash_enabled,
            )
            if checked:
                group_selected.extend(cmds)

    # 自訂指令：顯示不在任何 preset 群組中的已存指令
    preset_all = [c for _, cmds in PRESET_GROUPS for c in cmds]
    custom_existing = [c for c in current_allowed if c not in preset_all]
    custom_input = st.text_input(
        "自訂允許指令（逗號分隔，如：npm, cargo, ffmpeg）",
        value=", ".join(custom_existing),
        disabled=not bash_enabled,
        help="填入不在預設群組中的指令名稱（僅填指令本身，不含參數）。",
    )

    # 即時預覽合併後清單
    custom_cmds = [c.strip().lower() for c in custom_input.split(",") if c.strip()]
    merged = sorted(set(group_selected) | set(custom_cmds))
    if bash_enabled:
        if merged:
            st.caption(f"⚠️ 目前允許清單：`{'`, `'.join(merged)}`")
        else:
            st.caption("⚠️ 尚未勾選任何指令，Bash Tool 將不會被載入。")

    # ── Browser Agent 設定 ──────────────────────────────────────
    st.divider()
    st.header("🌐 Browser Agent（瀏覽器自動化）")
    new_browser_agent_enabled = st.checkbox(
        "啟用 Browser Agent",
        value=user_prefs.get("browser_agent_enabled", False),
        help="開啟後 AI 可控制本機瀏覽器執行自動化任務（填表、導航、截圖等）。需預先安裝 agent-browser CLI 並加入 PATH。",
    )
    if new_browser_agent_enabled:
        st.info("確認安裝：在終端機執行 `agent-browser --version` 應可看到版本號。", icon="ℹ️")

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
            # Bash Tool
            "bash_tool_enabled": bash_enabled,
            "bash_tool_allowed_commands": merged,
            # Browser Agent
            "browser_agent_enabled": new_browser_agent_enabled,
            # TTS
            "tts_enabled": new_tts_enabled,
            "image_generation_enabled": new_image_generation_enabled,
            "minimax_api_key": new_minimax_key,
            "minimax_voice_id": new_minimax_voice,
            "minimax_model": new_minimax_model,
            "minimax_speed": new_minimax_speed,
            "minimax_vol": new_minimax_vol,
            "minimax_pitch": new_minimax_pitch,
            # SU 身份
            "su_user_id": new_su_user_id,
        }
        try:
            resp = requests.put(f"{api_base}/system/config", json=update_payload, timeout=10)
            if resp.ok:
                st.success("系統設定已保存！")
            else:
                st.error(f"儲存失敗: {resp.text}")
        except Exception as e:
            st.error(f"儲存失敗: {e}")
