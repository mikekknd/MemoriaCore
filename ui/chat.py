# 【環境假設】：Python 3.12, Streamlit 1.30+。對話大廳獨立視圖模組。
# 已遷移為瘦客戶端：所有業務邏輯透過 FastAPI REST API 執行。
import streamlit as st
import json
import requests


def _render_perf_panel(di):
    """效能計時面板"""
    perf = di.get('perf_timing', {})
    if not perf or not perf.get('steps'):
        return
    total_ms = perf.get('total_ms', 0)
    steps = perf.get('steps', [])

    with st.expander(f"⏱️ 效能分析 (總耗時: {total_ms:,.1f} ms)"):
        # 瀑布圖式文字呈現
        max_ms = max((s['ms'] for s in steps), default=1)
        for s in steps:
            name = s['name']
            ms = s['ms']
            pct = (ms / total_ms * 100) if total_ms > 0 else 0
            bar_len = int((ms / max_ms) * 20) if max_ms > 0 else 0
            bar = "█" * bar_len + "░" * (20 - bar_len)
            st.text(f"{bar} {ms:>8.1f} ms ({pct:>5.1f}%)  {name}")

        st.divider()

        # 分類統計
        llm_ms = sum(s['ms'] for s in steps if 'LLM' in s['name'])
        search_ms = sum(s['ms'] for s in steps if '檢索' in s['name'] or 'Search' in s['name'])
        other_ms = total_ms - llm_ms - search_ms
        st.markdown(
            f"**LLM 呼叫**: `{llm_ms:,.1f} ms` ({llm_ms/total_ms*100:.1f}%) · "
            f"**向量檢索**: `{search_ms:,.1f} ms` ({search_ms/total_ms*100:.1f}%) · "
            f"**其他**: `{other_ms:,.1f} ms` ({other_ms/total_ms*100:.1f}%)"
            if total_ms > 0 else "無計時資料"
        )


def _render_debug_panel(di):
    """偵錯面板共用渲染邏輯"""
    _render_perf_panel(di)

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

            ctx_count = di.get('context_messages_count')
            if ctx_count is not None:
                st.markdown(f"**[對話紀錄窗口]** 本次 LLM 上下文包含 `{ctx_count}` 則對話紀錄（context_window 範圍內）")

            if di.get('dynamic_prompt', ''):
                st.code(di.get('dynamic_prompt', ''), language="markdown")


@st.cache_data(ttl=5, show_spinner=False)
def _load_session_list(api_base):
    """載入歷史 session 列表（快取 5 秒避免重繪重複請求）"""
    try:
        resp = requests.get(f"{api_base}/session/history", params={"channel": "streamlit", "limit": 30}, timeout=5)
        if resp.ok:
            return resp.json()
    except Exception:
        pass
    return []


def _create_new_session(api_base):
    """建立新 session 並更新 st.session_state"""
    resp = requests.post(f"{api_base}/session", json={"channel": "streamlit"}, timeout=5)
    if resp.ok:
        new_sid = resp.json()["session_id"]
        st.session_state.api_session_id = new_sid
        st.session_state.chat_messages_cache = []
        return new_sid
    return None


def _restore_session(api_base, session_id):
    """還原歷史 session 到記憶體並載入訊息"""
    try:
        # 先嘗試還原到記憶體
        requests.post(f"{api_base}/session/{session_id}/restore", timeout=5)
        # 載入歷史訊息
        hist_resp = requests.get(f"{api_base}/session/history/{session_id}", timeout=5)
        if hist_resp.ok:
            msgs = hist_resp.json().get("messages", [])
            st.session_state.api_session_id = session_id
            st.session_state.chat_messages_cache = msgs
            return True
    except Exception:
        pass
    return False


def render_chat_page(api_base, user_prefs):
    active_char_id = user_prefs.get("active_character_id", "default")
    active_char_name = "預設助理"
    try:
        c_resp = requests.get(f"{api_base}/character/{active_char_id}", timeout=2)
        if c_resp.ok:
            c_data = c_resp.json()
            if "name" in c_data:
                active_char_name = c_data["name"]
    except Exception:
        pass

    st.title(f"💬 對話大廳 - 🎭 {active_char_name}")

    @st.cache_data(ttl=15, show_spinner=False)
    def _load_system_prompt(_api_base):
        try:
            return requests.get(f"{_api_base}/system/prompt", timeout=5).json().get("prompt", "")
        except Exception:
            return ""

    with st.expander("⚙️ 全域預設設定 (Global System Prompt)", expanded=False):
        st.caption("⚠️ 注意：若您已指派特定的**對話角色**，此處的全域設定將會被該角色的**專屬提示詞徹底覆蓋**。此設定僅在沒有角色或角色無提示詞時生效。")
        current_prompt = _load_system_prompt(api_base)
        system_prompt_input = st.text_area("System Prompt：", value=current_prompt, height=150)
        if st.button("💾 儲存提示詞"):
            try:
                requests.put(f"{api_base}/system/prompt", json={"prompt": system_prompt_input}, timeout=5)
                st.success("系統設定已儲存！")
            except Exception as e:
                st.error(f"儲存失敗: {e}")

    # ── Session 選擇器（側邊欄）──
    with st.sidebar:
        st.subheader("📋 對話紀錄")

        if st.button("➕ 開始新對話", use_container_width=True):
            _create_new_session(api_base)
            _load_session_list.clear()
            st.rerun()

        sessions = _load_session_list(api_base)
        if sessions:
            current_sid = st.session_state.get("api_session_id", "")
            for sess in sessions:
                sid = sess["session_id"]
                msg_count = sess.get("message_count", 0)
                created = sess.get("created_at", "")[:16]
                is_current = (sid == current_sid)
                label = f"{'▶ ' if is_current else ''}{created} ({msg_count} 則)"
                col_btn, col_del = st.columns([5, 1])
                with col_btn:
                    if st.button(label, key=f"sess_{sid}", use_container_width=True, disabled=is_current):
                        _restore_session(api_base, sid)
                        st.rerun()
                with col_del:
                    if st.button("🗑", key=f"del_{sid}", help="永久刪除此對話"):
                        try:
                            requests.delete(f"{api_base}/session/history/{sid}", timeout=10)
                        except Exception:
                            pass
                        # 若刪除的是當前 session，清除本地狀態
                        if is_current:
                            st.session_state.pop("api_session_id", None)
                            st.session_state.pop("chat_messages_cache", None)
                        _load_session_list.clear()
                        st.rerun()

    # Session 管理：首次載入時自動恢復最近的 streamlit session
    # 若無可恢復的 session，不主動建立——等使用者發送訊息時再建
    if "api_session_id" not in st.session_state:
        sessions = _load_session_list(api_base)
        if sessions:
            # 無論 message_count 是否 > 0，只要 session 存在就嘗試還原
            # （後端重啟後 message_count 可能為 0 但 DB 仍有紀錄）
            _restore_session(api_base, sessions[0]["session_id"])
        # 無論是否恢復成功，都初始化快取（避免後續 KeyError）
        if "chat_messages_cache" not in st.session_state:
            st.session_state.chat_messages_cache = []

    session_id = st.session_state.get("api_session_id")

    # 對話歷史：有 session 時才嘗試載入
    if session_id and "chat_messages_cache" not in st.session_state:
        try:
            sess_resp = requests.get(f"{api_base}/session/{session_id}", timeout=5)
            if sess_resp.ok:
                st.session_state.chat_messages_cache = sess_resp.json().get("messages", [])
            else:
                hist_resp = requests.get(f"{api_base}/session/history/{session_id}", timeout=5)
                if hist_resp.ok:
                    st.session_state.chat_messages_cache = hist_resp.json().get("messages", [])
                else:
                    st.session_state.chat_messages_cache = []
        except Exception:
            st.session_state.chat_messages_cache = []

    messages = st.session_state.get("chat_messages_cache", [])

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

        # 延遲建立 session：第一則訊息時才建立
        if not session_id:
            try:
                resp = requests.post(f"{api_base}/session", json={"channel": "streamlit"}, timeout=5)
                if resp.ok:
                    session_id = resp.json()["session_id"]
                    st.session_state.api_session_id = session_id
                    _load_session_list.clear()
                else:
                    st.error("無法建立 Session")
                    st.session_state.is_generating = False
                    st.rerun()
                    return
            except Exception as e:
                st.error(f"無法連線到 API: {e}")
                st.session_state.is_generating = False
                return

        # 立即將使用者訊息加入本地快取並顯示
        st.session_state.chat_messages_cache.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            status_placeholder = st.empty()
            thinking_placeholder = st.empty()
            status_placeholder.info("⚡ 雙路檢索中 (情境回憶 + 核心認知)...")
            try:
                chat_resp = requests.post(
                    f"{api_base}/chat/stream-sync",
                    json={"content": prompt, "session_id": session_id},
                    stream=True,
                    timeout=120,
                )
                chat_resp.raise_for_status()

                result = None
                has_error = False
                thinking_speech_text = None
                for line in chat_resp.iter_lines(decode_unicode=True):
                    if not line or not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    evt_type = event.get("type")
                    if evt_type == "tool_status":
                        status_placeholder.info(f"🔍 {event.get('message', '處理中...')}")
                    elif evt_type == "thinking_speech":
                        thinking_speech_text = event.get("content", "")
                        # 過渡語音用獨立容器，不會被後續狀態覆蓋
                        status_placeholder.empty()
                        thinking_placeholder.markdown(f"*💭 {thinking_speech_text}*")
                    elif evt_type == "error":
                        status_placeholder.empty()
                        thinking_placeholder.empty()
                        st.error(f"API 錯誤: {event.get('message', '未知錯誤')}")
                        has_error = True
                        break
                    elif evt_type == "result":
                        result = event
                        # 同步伺服器實際使用的 session_id（後端重啟後可能與 UI 儲存的不同）
                        actual_sid = event.get("session_id")
                        if actual_sid and actual_sid != st.session_state.get("api_session_id"):
                            st.session_state.api_session_id = actual_sid
                            _load_session_list.clear()

                status_placeholder.empty()
                thinking_placeholder.empty()

                if result:
                    reply_text = result.get("reply", "解析錯誤")
                    retrieval_ctx = result.get("retrieval_context", {})

                    st.session_state.chat_messages_cache.append({
                        "role": "assistant",
                        "content": reply_text,
                        "debug_info": retrieval_ctx,
                    })

                    _render_debug_panel(retrieval_ctx)
                    st.markdown(reply_text)

            except requests.Timeout:
                status_placeholder.empty()
                st.error("請求超時，LLM 回覆時間過長。")
                has_error = True
            except Exception as e:
                status_placeholder.empty()
                st.error(f"生成錯誤: {e}")
                has_error = True
            finally:
                st.session_state.is_generating = False
                if not has_error:
                    st.rerun()
