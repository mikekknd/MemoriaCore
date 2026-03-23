# 【環境假設】：Python 3.12, Streamlit 1.30+。對話大廳獨立視圖模組。
# 已遷移為瘦客戶端：所有業務邏輯透過 FastAPI REST API 執行。
import streamlit as st
import json
import requests


def _render_debug_panel(di):
    """偵錯面板共用渲染邏輯"""
    with st.expander(f"🔍 記憶喚醒分析 (Threshold: {di.get('threshold', 0):.2f})"):
        st.markdown("**[核心認知喚醒]**")
        st.info(di.get('core_debug_text', '未觸發核心認知。'))
        st.markdown("**[使用者畫像召回]**")
        st.info(di.get('profile_debug_text', '未觸發使用者偏好。'))
        st.divider()

        st.markdown("**[雙軌檢索脈絡]**")
        st.markdown(f"- **User意圖擴充**: `{di.get('expanded_keywords', '')}`")
        st.markdown(f"- **AI影子繼承**: `{di.get('inherited_tags', [])}`")
        st.info(f"原句: {di.get('original_query', '')}")
        st.divider()

        if not di.get('has_memory', False):
            st.warning("未喚醒任何情境記憶。")
        else:
            st.success(f"成功精準命中 {di.get('block_count', 0)} 個情境區塊！")
            st.markdown(f"**[運算斬殺線]** 綜合喚醒閾值: `{di.get('threshold', 0):.2f}` | 基礎語意底線 (Hard Base): `{di.get('hard_base', 0):.2f}`")

            for bd in di.get('block_details', []):
                st.caption(f"🎯 **命中區塊 {bd['id']}**: {bd['overview']}")
                imp_text = f", 權重加成: `{bd['importance']:.3f}`" if 'importance' in bd and bd['importance'] > 0 else ""
                st.caption(f"└─ 綜合得分: `{bd['hybrid']:.3f}` (Dense: `{bd['dense']:.3f}`, Sparse: `{bd['sparse']:.3f}`, 時間加成: `{bd['recency']:.3f}`{imp_text})")

            if di.get('dynamic_prompt', ''):
                st.code(di.get('dynamic_prompt', ''), language="markdown")


def render_chat_page(api_base, user_prefs):
    st.title("💬 異質模型分流: 雙軌混合檢索版")

    with st.expander("🎭 機器人設定 (System Prompt)", expanded=False):
        try:
            current_prompt = requests.get(f"{api_base}/system/prompt", timeout=5).json().get("prompt", "")
        except Exception:
            current_prompt = ""
        system_prompt_input = st.text_area("System Prompt：", value=current_prompt, height=150)
        if st.button("💾 儲存提示詞"):
            try:
                requests.put(f"{api_base}/system/prompt", json={"prompt": system_prompt_input}, timeout=5)
                st.success("系統設定已儲存！")
            except Exception as e:
                st.error(f"儲存失敗: {e}")

    # Session 管理：透過 API 建立/取得 session
    if "api_session_id" not in st.session_state:
        try:
            resp = requests.post(f"{api_base}/session", json={"channel": "streamlit"}, timeout=5)
            if resp.ok:
                st.session_state.api_session_id = resp.json()["session_id"]
            else:
                st.error("無法建立 Session")
                return
        except Exception as e:
            st.error(f"無法連線到 API: {e}")
            return

    session_id = st.session_state.api_session_id

    # 對話歷史：優先使用本地快取，避免每次 rerun 都打 GET /session/{id}
    # 只有在首次進入頁面（快取不存在）時才從 API 拉一次
    if "chat_messages_cache" not in st.session_state:
        try:
            sess_resp = requests.get(f"{api_base}/session/{session_id}", timeout=5)
            if sess_resp.ok:
                st.session_state.chat_messages_cache = sess_resp.json().get("messages", [])
            else:
                st.session_state.chat_messages_cache = []
        except Exception:
            st.session_state.chat_messages_cache = []

    messages = st.session_state.chat_messages_cache

    # 渲染對話歷史
    for message in messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("debug_info"):
                _render_debug_panel(message['debug_info'])

    if "is_generating" not in st.session_state:
        st.session_state.is_generating = False

    if prompt := st.chat_input("請輸入...", disabled=st.session_state.is_generating):
        st.session_state.is_generating = True

        # 立即將使用者訊息加入本地快取並顯示
        st.session_state.chat_messages_cache.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("⚡ 雙路檢索中 (情境回憶 + 核心認知)..."):
                try:
                    chat_resp = requests.post(
                        f"{api_base}/chat/sync",
                        json={"content": prompt, "session_id": session_id},
                        timeout=120,
                    )
                    if chat_resp.ok:
                        result = chat_resp.json()
                        reply_text = result.get("reply", "解析錯誤")
                        retrieval_ctx = result.get("retrieval_context", {})

                        # 將 AI 回覆加入本地快取（附帶 debug_info）
                        st.session_state.chat_messages_cache.append({
                            "role": "assistant",
                            "content": reply_text,
                            "debug_info": retrieval_ctx,
                        })

                        _render_debug_panel(retrieval_ctx)
                        st.markdown(reply_text)
                    else:
                        st.error(f"API 錯誤: {chat_resp.status_code} - {chat_resp.text}")
                except requests.Timeout:
                    st.error("請求超時，LLM 回覆時間過長。")
                except Exception as e:
                    st.error(f"生成錯誤: {e}")
                finally:
                    st.session_state.is_generating = False
                    st.rerun()
