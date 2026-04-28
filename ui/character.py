import streamlit as st
from ui import api_client as requests
def _bump_form_version() -> None:
    """讓 char_edit_form 與 seed editor 重新初始化（版本號+1）。"""
    st.session_state["_ced_v"] = st.session_state.get("_ced_v", 0) + 1


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
    tab_options = ["📋 角色列表", "✏️ 新增 / 編輯角色", "🧬 PersonaProbe"]
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
                has_evolved = bool(char.get("evolved_prompt"))
                evolved_badge = " 🧬" if has_evolved else ""
                with st.expander(f"🎭 {char.get('name', '未命名')}{evolved_badge}"):
                    st.write(f"**ID:** `{char.get('character_id')}`")

                    if has_evolved:
                        st.caption("🧬 使用 PersonaProbe 演化人設")
                        ep = char.get("evolved_prompt", {})
                        if isinstance(ep, dict):
                            pub = ep.get("public")
                            priv = ep.get("private")
                            st.markdown(f"- 🌐 public: `{'已設定' if pub else '未設定'}`")
                            st.markdown(f"- 🔐 private: `{'已設定' if priv else '未設定'}`")
                        else:
                            st.markdown("- 🌐 public: `已設定`（舊格式）")

                    if char.get("tts_language"):
                        st.markdown(f"**🗣️ TTS 發音語言:** `{char.get('tts_language')}`")
                    st.write("**回覆文字規則 (Reply Rules):**")
                    st.info(char.get("reply_rules", ""))
                    if char.get("tts_rules"):
                        st.write("**TTS 發音指引 (TTS Rules):**")
                        st.info(char.get("tts_rules", ""))

                    is_active = user_prefs.get("active_character_id") == char.get('character_id')
                    if is_active:
                        st.success("✅ 目前指定的對話角色")

                    col_btn1, col_btn2, col_btn3, col_btn4 = st.columns(4)
                    with col_btn1:
                        if not is_active and st.button("🎯 設為當前角色", key=f"act_{char.get('character_id')}"):
                            new_prefs = user_prefs.copy()
                            new_prefs["active_character_id"] = char.get("character_id")
                            requests.put(f"{api_base}/system/config", json=new_prefs)
                            st.rerun()

                    with col_btn2:
                        if st.button("✏️ 編輯此角色", key=f"edit_{char.get('character_id')}"):
                            st.session_state["char_draft"] = char
                            _bump_form_version()
                            st.session_state.char_tab = "✏️ 新增 / 編輯角色"
                            st.rerun()

                    with col_btn3:
                        if has_evolved and st.button("🔄 重置演化", key=f"reset_{char.get('character_id')}",
                                                     help="清除 PersonaProbe 演化人設，還原為原始 system_prompt"):
                            try:
                                requests.delete(f"{api_base}/character/{char.get('character_id')}/evolved-prompt")
                                st.rerun()
                            except Exception as e:
                                st.error(f"重置失敗: {e}")

                    with col_btn4:
                        if st.button("🗑️ 刪除", key=f"del_{char.get('character_id')}"):
                            try:
                                requests.delete(f"{api_base}/character/{char.get('character_id')}")
                                st.rerun()
                            except Exception as e:
                                st.error(f"刪除失敗: {e}")

    # === Tab 2: Edit/Create ===
    elif st.session_state.char_tab == "✏️ 新增 / 編輯角色":
        draft = st.session_state.get("char_draft", {})

        if "character_id" in draft:
            st.info(f"正在編輯已存在的角色：**{draft.get('name')}** (ID: `{draft['character_id']}`)")
            if st.button("取消編輯 (清空表單)"):
                del st.session_state["char_draft"]
                _bump_form_version()
                st.session_state.char_tab = "📋 角色列表"
                st.rerun()

        # ── AI 生成區 ──
        st.subheader("🤖 AI 助理生成區")
        st.markdown("只需輸入簡短描述，讓 AI 幫您補齊所有細節設定！")

        with st.form("ai_gen_form"):
            gen_desc = st.text_input("一句話描述你想建立的角色 (例如：毒舌但不坦率的青梅竹馬)")
            gen_submit = st.form_submit_button("✨ 讓 AI 自動生成草稿")

        # 一次性提示訊息（rerun 後顯示）
        if "_char_notice" in st.session_state:
            st.success(st.session_state.pop("_char_notice"))

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
                            _bump_form_version()
                            st.session_state["_char_notice"] = "✨ 基本草稿生成完成！可繼續點擊下方「PersonaProbe 深化」產生詳細行為規格。"
                            st.rerun()
                    else:
                        st.error(f"API 回應錯誤 {res.status_code}: {res.text[:200]}")
                except Exception as e:
                    st.error(f"請求失敗: {e}")

        # ── PersonaProbe 深化 ──
        if draft.get("system_prompt"):
            st.divider()
            st.markdown("**🧬 PersonaProbe 深化人格細節**")
            st.caption("以目前草稿的 system_prompt 為種子，呼叫 PersonaProbe 快速人格生成，補充詳細行為規格書（語言行為模式、決策邊界、強度校準等）。結果會**更新當前草稿**，不會新建角色。")

            seed_v = st.session_state.get("_ced_v", 0)
            edited_seed = st.text_area(
                "人格種子內容（可手動微調後再深化）",
                value=draft.get("system_prompt", ""),
                height=200,
                key=f"_ced_seed_{seed_v}",
            )

            if st.button("🧬 PersonaProbe 深化人格細節", use_container_width=True):
                with st.spinner("PersonaProbe 快速人格生成中（約需 30~90 秒）..."):
                    try:
                        res = requests.post(
                            f"{api_base}/character/generate-from-seed",
                            json={"description": gen_desc or "", "existing_persona": edited_seed},
                            timeout=120,
                        )
                        if res.ok:
                            data = res.json()
                            if "error" in data:
                                st.error(f"生成出錯: {data['error']}")
                            else:
                                updated_draft = dict(draft)
                                updated_draft.update({k: v for k, v in data.items() if k != "character_id"})
                                st.session_state["char_draft"] = updated_draft
                                _bump_form_version()
                                st.session_state["_char_notice"] = "✨ 人格深化完成！請在下方表單確認後儲存。"
                                st.rerun()
                        else:
                            st.error(f"API 回應錯誤: {res.status_code}")
                    except Exception as e:
                        st.error(f"請求失敗: {e}")

        # ── 編輯模式：以現有人格為種子，生成全新獨立角色 ──
        if "character_id" in draft:
            st.divider()
            st.markdown("或者，直接以這位角色的現有人格當作種子，**生成另一個全新角色**：")

            ep_raw = draft.get("evolved_prompt")
            if isinstance(ep_raw, dict):
                seed_text = ep_raw.get("public") or ""
            elif isinstance(ep_raw, str) and ep_raw:
                seed_text = ep_raw
            else:
                seed_text = draft.get("system_prompt", "")

            if not seed_text.strip():
                st.caption("⚠️ 這位角色目前沒有可用的人設內容（system_prompt 為空）。")
            else:
                with st.expander("🔍 預覽即將當作種子的人格內容"):
                    st.text(seed_text[:200] + ("…" if len(seed_text) > 200 else ""))

                if st.button("🌱 以此人設為種子，生成新角色", use_container_width=True):
                    with st.spinner("正在呼叫 PersonaProbe 快速人格生成..."):
                        try:
                            res = requests.post(
                                f"{api_base}/character/generate-from-seed",
                                json={"description": gen_desc or "新角色", "existing_persona": seed_text},
                                timeout=120,
                            )
                            if res.ok:
                                data = res.json()
                                if "error" in data:
                                    st.error(f"生成出錯: {data['error']}")
                                else:
                                    new_draft = {k: v for k, v in data.items() if k != "character_id"}
                                    st.session_state["char_draft"] = new_draft
                                    _bump_form_version()
                                    st.session_state["_char_notice"] = "✨ 新角色已生成！請在下方檢查並儲存。"
                                    st.rerun()
                            else:
                                st.error(f"API 回應錯誤: {res.status_code}")
                        except Exception as e:
                            st.error(f"請求失敗: {e}")

        # ── 手動微調表單 ──
        st.divider()
        st.subheader("📝 手動微調表單")

        form_v = st.session_state.get("_ced_v", 0)
        ep_raw = draft.get("evolved_prompt") or {}
        if isinstance(ep_raw, str):
            ep_dict = {"public": ep_raw, "private": None}
        elif isinstance(ep_raw, dict):
            ep_dict = ep_raw
        else:
            ep_dict = {"public": None, "private": None}

        with st.form(f"char_edit_form_{form_v}"):
            c_name = st.text_input("角色名稱 (Name)", value=draft.get("name") or "")

            st.caption("📝 原始 System Prompt — 由你手動撰寫，PersonaProbe 反思不會覆蓋此欄位。")
            c_prompt = st.text_area("原始人設內容", value=str(draft.get("system_prompt") or ""), height=250,
                                    label_visibility="collapsed")

            with st.expander("🧬 演化人設（進階，由 PersonaProbe 生成）"):
                st.caption("📌 演化人設分為 **public** 與 **private** 兩份，各自獨立演化。SU 身份使用 private 分支。")
                c_evolved_public = st.text_area(
                    "🌐 Public Face（公開頻道使用）",
                    value=str(ep_dict.get("public") or ""),
                    height=150,
                    placeholder="public face 演化內容（留空則使用原始人設）",
                )
                c_evolved_private = st.text_area(
                    "🔐 Private Face（SU 身份使用）",
                    value=str(ep_dict.get("private") or ""),
                    height=150,
                    placeholder="private face 演化內容（留空則使用原始人設）",
                )

            c_tts = st.text_input(
                "🗣️ TTS 獨立發音語言 (例如：日文。若無雙語需求請留空)",
                value=draft.get("tts_language") or "",
            )
            c_reply_rules = st.text_input(
                "回覆文字規則 (reply_rules) — 套用於字幕文字的語言、格式與語氣強制規定",
                value=str(draft.get("reply_rules") or ""),
                help="例如：必須說繁體中文、不准用 Emoji、句尾加喵。有無 TTS 都會套用。",
            )
            c_tts_rules = st.text_input(
                "TTS 發音指引 (tts_rules) — 僅注入 speech 欄位的發音提示（可留空）",
                value=str(draft.get("tts_rules") or ""),
                help="例如：請以輕柔緩慢的語調朗讀、避免拖音。無特殊需求請留空。",
            )

            save_submit = st.form_submit_button("💾 儲存角色")

            if save_submit:
                if not c_name:
                    st.error("請輸入角色名稱!")
                else:
                    evolved_public_val = c_evolved_public.strip() or None
                    evolved_private_val = c_evolved_private.strip() or None
                    if evolved_public_val is None and evolved_private_val is None:
                        evolved_payload = None
                    else:
                        evolved_payload = {"public": evolved_public_val, "private": evolved_private_val}

                    payload = {
                        "name": c_name,
                        "system_prompt": c_prompt,
                        "evolved_prompt": evolved_payload,
                        "tts_language": c_tts.strip(),
                        "reply_rules": c_reply_rules,
                        "tts_rules": c_tts_rules,
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

    # === Tab 3: PersonaProbe ===
    elif st.session_state.char_tab == "🧬 PersonaProbe":
        st.subheader("🧬 PersonaProbe 同步")
        st.caption("同步結果會寫入 active character 的演化人設。")

        active_char_id = user_prefs.get("active_character_id", "default")
        active_char_name = next(
            (c.get("name", c.get("character_id")) for c in characters if c.get("character_id") == active_char_id),
            active_char_id,
        )

        col_a, col_b = st.columns([1, 2])
        with col_a:
            st.info(f"📌 目前活躍角色：**{active_char_name}**")
        with col_b:
            char_options = [(c.get("character_id"), c.get("name", c.get("character_id"))) for c in characters]
            char_ids = [cid for cid, _ in char_options]
            selected = st.selectbox(
                "切換目標角色",
                options=char_ids,
                format_func=lambda cid: next((name for cid_, name in char_options if cid_ == cid), cid),
                index=char_ids.index(active_char_id) if active_char_id in char_ids else 0,
                key="probe_target_char",
            )
            if selected != active_char_id:
                new_prefs = user_prefs.copy()
                new_prefs["active_character_id"] = selected
                requests.put(f"{api_base}/system/config", json=new_prefs)
                st.rerun()

        st.divider()

        try:
            sync_resp = requests.get(
                f"{api_base}/system/personality/sync-status",
                params={"character_id": active_char_id, "persona_face": "public"},
                timeout=5,
            )
            if sync_resp.ok:
                sync_status = sync_resp.json()
                col1, col2, col3 = st.columns(3)
                col1.metric("今日已執行", f"{sync_status.get('today_run_count', 0)} 次")
                col2.metric("上次反思時間", sync_status.get("last_reflection_at") or "從未")
                col3.metric("距上次反思訊息數", f"{sync_status.get('messages_since_last', 0)} 筆")
        except Exception:
            st.info("無法取得同步狀態（後端可能未啟動）。")

        st.divider()

        if st.button("🚀 立即執行 PersonaProbe 反思", use_container_width=True, type="primary"):
            with st.spinner("正在呼叫 PersonaProbe 進行深度人格分析（約需 1-2 分鐘）..."):
                try:
                    ref_resp = requests.post(
                        f"{api_base}/system/personality/sync-now",
                        params={"character_id": active_char_id, "persona_face": "public"},
                        timeout=660,
                    )
                    if ref_resp.ok:
                        result = ref_resp.json()
                        status = result.get("status", "")
                        if status == "success":
                            st.success("✅ PersonaProbe 反思完成！演化人設已更新。")
                            st.rerun()
                        elif status == "skipped":
                            st.info(f"⏭️ 已跳過：{result.get('reason', '')}")
                        else:
                            st.error(f"執行失敗：{result.get('message', ref_resp.text)}")
                    else:
                        st.error(f"API 錯誤：{ref_resp.text}")
                except Exception as e:
                    st.error(f"請求失敗: {e}")
