import streamlit as st
import requests

def render_character_page(api_base: str, user_prefs: dict):
    st.title("🎭 角色設定 (Character Settings)")
    st.markdown("在此建立並管理不同的 AI 角色設定。你可以手動輸入，或透過 LLM 自動為你擴充完整的角色世界觀與心理追蹤指標。")

    # Fetch characters
    try:
        res = requests.get(f"{api_base}/character", timeout=5)
        characters = res.json() if res.ok else []
    except Exception as e:
        st.error(f"無法載入角色列表: {e}")
        characters = []

    # Prepare navigation via radio to allow programmatic switching
    tab_options = ["📋 角色列表", "✏️ 新增 / 編輯角色"]
    if "char_tab" not in st.session_state:
        st.session_state.char_tab = "📋 角色列表"

    selected_tab = st.radio(
        "選擇模式", 
        tab_options, 
        index=tab_options.index(st.session_state.char_tab),
        horizontal=True, 
        label_visibility="collapsed"
    )
    
    if selected_tab != st.session_state.char_tab:
        st.session_state.char_tab = selected_tab
        st.rerun()

    st.divider()

    # === Tab 1: List ===
    if st.session_state.char_tab == "📋 角色列表":
        if not characters:
            st.info("目前沒有建立任何角色。")
        else:
            for char in characters:
                with st.expander(f"🎭 {char.get('name', '未命名')}"):
                    st.write(f"**ID:** `{char.get('character_id')}`")
                    st.text_area("System Prompt", char.get("system_prompt", ""), height=100, disabled=True, key=f"sp_{char.get('character_id')}")
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write("**心理追蹤指標 (Metrics):**")
                        for m in char.get("metrics", []):
                            st.caption(f"- {m}")
                    with col2:
                        st.write("**允許語氣 (Allowed Tones):**")
                        for t in char.get("allowed_tones", []):
                            st.caption(f"- {t}")
                    
                    if char.get("tts_language"):
                        st.markdown(f"**🗣️ TTS 發音語言:** `{char.get('tts_language')}`")
                    st.write("**說話規則 (Speech Rules):**")
                    st.info(char.get("speech_rules", ""))
                    
                    is_active = user_prefs.get("active_character_id") == char.get('character_id')
                    if is_active:
                        st.success("✅ 目前指定的對話角色")
                    
                    col_btn1, col_btn2, col_btn3 = st.columns(3)
                    with col_btn1:
                        if not is_active and st.button("🎯 設為當前角色", key=f"act_{char.get('character_id')}"):
                            new_prefs = user_prefs.copy()
                            new_prefs["active_character_id"] = char.get("character_id")
                            requests.put(f"{api_base}/system/config", json=new_prefs)
                            st.rerun()
                            
                    with col_btn2:
                        if st.button("✏️ 編輯此角色", key=f"edit_{char.get('character_id')}"):
                            st.session_state["char_draft"] = char
                            st.session_state.char_tab = "✏️ 新增 / 編輯角色"
                            st.rerun()
                    
                    with col_btn3:
                        if st.button("🗑️ 刪除", key=f"del_{char.get('character_id')}"):
                            try:
                                requests.delete(f"{api_base}/character/{char.get('character_id')}")
                                st.rerun()
                            except Exception as e:
                                st.error(f"刪除失敗: {e}")

    # === Tab 2: Edit/Create ===
    elif st.session_state.char_tab == "✏️ 新增 / 編輯角色":
        st.subheader("🤖 AI 助理生成區")
        st.markdown("只需輸入簡短描述，讓 AI 幫您補齊所有細節設定！")
        
        with st.form("ai_gen_form"):
            gen_desc = st.text_input("一句話描述你想建立的角色 (例如：毒舌但不坦率的青梅竹馬)")
            gen_submit = st.form_submit_button("✨ 讓 AI 自動生成草稿")
            
        if gen_submit and gen_desc:
            with st.spinner("AI 正在絞盡腦汁撰寫設定..."):
                try:
                    res = requests.post(f"{api_base}/character/generate", json={"description": gen_desc}, timeout=30)
                    if res.ok:
                        data = res.json()
                        if "error" in data:
                            st.error(f"生成出錯: {data['error']}")
                        else:
                            st.session_state["char_draft"] = data
                            st.success("✨ 生成成功！請在下方檢查並儲存。")
                    else:
                        st.error(f"API 回應錯誤: {res.status_code}")
                except Exception as e:
                    st.error(f"請求失敗: {e}")

        st.divider()
        st.subheader("📝 手動微調表單")
        
        # Load draft from state or use empty default
        draft = st.session_state.get("char_draft", {})
        
        if "character_id" in draft:
            st.info(f"正在編輯已存在的角色：**{draft.get('name')}** (ID: `{draft['character_id']}`)")
            if st.button("取消編輯 (清空表單)"):
                del st.session_state["char_draft"]
                st.session_state.char_tab = "📋 角色列表"
                st.rerun()
        
        with st.form("char_edit_form"):
            c_name = st.text_input("角色名稱 (Name)", value=draft.get("name", ""))
            c_prompt = st.text_area("系統提示詞 (System Prompt)", value=draft.get("system_prompt", ""), height=200)
            c_metrics = st.text_input("心理追蹤指標 (英文，用逗號分隔)", value=",".join(draft.get("metrics", [])))
            c_tones = st.text_input("允許的語氣字眼 (英文，用逗號分隔)", value=",".join(draft.get("allowed_tones", [])))
            c_tts = st.text_input("🗣️ TTS 獨立發音語言 (例如：日文。若無雙語需求請留空)", value=draft.get("tts_language", ""))
            c_speech = st.text_input("說話與情緒規則 (Speech Rules)", value=draft.get("speech_rules", ""))
            
            save_submit = st.form_submit_button("💾 儲存角色")
            
            if save_submit:
                if not c_name:
                    st.error("請輸入角色名稱!")
                else:
                    metrics_list = [m.strip() for m in c_metrics.split(",") if m.strip()]
                    tones_list = [t.strip() for t in c_tones.split(",") if t.strip()]
                    
                    payload = {
                        "name": c_name,
                        "system_prompt": c_prompt,
                        "metrics": metrics_list,
                        "allowed_tones": tones_list,
                        "tts_language": c_tts.strip(),
                        "speech_rules": c_speech
                    }
                    if "character_id" in draft:
                        payload["character_id"] = draft["character_id"]
                    
                    try:
                        res = requests.post(f"{api_base}/character", json=payload)
                        if res.ok:
                            st.success("儲存成功！")
                            if "char_draft" in st.session_state:
                                del st.session_state["char_draft"]
                            st.session_state.char_tab = "📋 角色列表"
                            st.rerun()
                        else:
                            st.error(f"儲存失敗: {res.text}")
                    except Exception as e:
                        st.error(f"請求發生錯誤: {e}")
