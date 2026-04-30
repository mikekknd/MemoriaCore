# 【環境假設】：Python 3.12, Streamlit 1.30+。記憶庫管理獨立視圖模組。
# 已遷移為瘦客戶端：所有業務邏輯透過 FastAPI REST API 執行。
import json

import streamlit as st
import pandas as pd
from ui import api_client as requests


def _api_get_json(api_base: str, path: str, params: dict | None = None, timeout: int = 15):
    resp = requests.get(f"{api_base}{path}", params=params, timeout=timeout)
    if not resp.ok:
        raise RuntimeError(resp.text)
    return resp.json()


def _load_inspect_meta(api_base: str) -> tuple[dict, dict[str, str]]:
    scopes = _api_get_json(api_base, "/memory/inspect/scopes", timeout=20)
    try:
        chars = _api_get_json(api_base, "/character", timeout=10)
    except Exception:
        chars = []
    char_names = {
        str(c.get("character_id")): c.get("name") or str(c.get("character_id"))
        for c in chars
        if c.get("character_id")
    }
    return scopes, char_names


def _user_label(user: dict) -> str:
    user_id = str(user.get("user_id", ""))
    name = user.get("username") or "(外部 user_id)"
    nickname = user.get("nickname") or ""
    stats = user.get("stats") or {}
    stat_text = (
        f"blocks {stats.get('memory_blocks', 0)} / core {stats.get('core_memories', 0)} / "
        f"profile {stats.get('profiles', 0)} / topics {stats.get('topics', 0)}"
    )
    display_name = f"{name} / {nickname}" if nickname else name
    return f"{display_name} / user_id={user_id} / {stat_text}"


def _character_label(character_id: str, char_names: dict[str, str]) -> str:
    name = char_names.get(character_id)
    if name and name != character_id:
        return f"{name} ({character_id})"
    if character_id == "__global__":
        return "__global__（背景蒐集 user-level topic）"
    return character_id


def _json_text(value) -> str:
    if value in (None, "", []):
        return ""
    return json.dumps(value, ensure_ascii=False)


def _render_inspect_table(table_key: str, rows: list[dict], scope_text: str) -> None:
    if not rows:
        st.info(f"此 scope 沒有資料：{scope_text}")
        return

    if table_key == "blocks":
        df = pd.DataFrame([{
            "user_id": r.get("user_id", ""),
            "character_id": r.get("character_id", ""),
            "visibility": r.get("visibility", ""),
            "timestamp": r.get("timestamp", ""),
            "encounter_count": r.get("encounter_count", 1.0),
            "is_consolidated": r.get("is_consolidated", False),
            "overview": r.get("overview", ""),
            "potential_preferences": _json_text(r.get("potential_preferences", [])),
            "raw_dialogues": _json_text(r.get("raw_dialogues", [])),
        } for r in rows])
    elif table_key == "core":
        df = pd.DataFrame([{
            "user_id": r.get("user_id", ""),
            "character_id": r.get("character_id", ""),
            "visibility": r.get("visibility", ""),
            "timestamp": r.get("timestamp", ""),
            "encounter_count": r.get("encounter_count", 1.0),
            "insight": r.get("insight", ""),
            "core_id": r.get("core_id", ""),
        } for r in rows])
    elif table_key == "profile":
        df = pd.DataFrame([{
            "user_id": r.get("user_id", ""),
            "visibility": r.get("visibility", ""),
            "status": "已撤回" if r.get("confidence", 1) < 0 else "有效",
            "timestamp": r.get("timestamp", ""),
            "category": r.get("category", ""),
            "fact_key": r.get("fact_key", ""),
            "fact_value": r.get("fact_value", ""),
            "confidence": r.get("confidence", 1.0),
            "source_context": r.get("source_context", ""),
        } for r in rows])
    else:
        df = pd.DataFrame([{
            "user_id": r.get("user_id", ""),
            "character_id": r.get("character_id", ""),
            "visibility": r.get("visibility", ""),
            "created_at": r.get("created_at", ""),
            "status": "已提及" if r.get("is_mentioned_to_user") else "未提及",
            "interest_keyword": r.get("interest_keyword", ""),
            "summary_content": r.get("summary_content", ""),
            "topic_id": r.get("topic_id", ""),
        } for r in rows])

    st.dataframe(df, use_container_width=True)


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
        st.subheader("Admin Scope-Aware 記憶檢視")
        st.caption("唯讀檢視 runtime memory DB；不載入向量，不修改資料。Profile 是 per-user，不綁定角色。")

        meta_key = "db_inspect_meta"
        if st.button("🔄 重新整理 scope 清單", key="reload_db_inspect_meta"):
            st.session_state.pop(meta_key, None)

        try:
            if meta_key not in st.session_state:
                st.session_state[meta_key] = _load_inspect_meta(api_base)
            scopes, char_names = st.session_state[meta_key]
        except Exception as e:
            st.error(f"載入 scope 清單失敗: {e}")
            scopes, char_names = {"users": [], "character_ids": [], "visibilities": []}, {}

        users = scopes.get("users") or [{"user_id": uid} for uid in scopes.get("user_ids", [])]
        character_ids = set(scopes.get("character_ids") or [])
        character_ids.update(char_names.keys())
        character_ids.update({"default", "__global__"})
        character_options = sorted(character_ids, key=lambda cid: (cid != "default", cid != "__global__", cid))

        table_options = {
            "blocks": "情境記憶 Memory Blocks",
            "core": "核心認知 Core Memories",
            "profile": "使用者畫像 User Profile",
            "topics": "主動話題 Topic Cache",
        }

        col_table, col_user = st.columns([1, 2])
        with col_table:
            table_key = st.selectbox(
                "資料表",
                list(table_options.keys()),
                format_func=lambda k: table_options[k],
                key="db_inspect_table",
            )
        with col_user:
            if users:
                selected_user = st.selectbox(
                    "使用者",
                    users,
                    format_func=_user_label,
                    key="db_inspect_user",
                )
                selected_user_id = str(selected_user.get("user_id", ""))
            else:
                selected_user_id = st.text_input("使用者 user_id", value="default", key="db_inspect_user_fallback")

        col_char, col_vis, col_limit = st.columns([2, 1, 1])
        with col_char:
            selected_character_id = st.selectbox(
                "角色 / Topic Scope",
                character_options,
                format_func=lambda cid: _character_label(cid, char_names),
                disabled=(table_key == "profile"),
                key="db_inspect_character",
            )
            if table_key == "profile":
                st.caption("Profile 只依 user_id + visibility 查詢，不使用 character_id。")
        with col_vis:
            visibility = st.selectbox(
                "Visibility",
                ["all", "public", "private"],
                format_func=lambda v: "全部" if v == "all" else v,
                key="db_inspect_visibility",
            )
        with col_limit:
            limit = st.number_input("Limit", min_value=1, max_value=1000, value=200, step=50, key="db_inspect_limit")

        col_opts_a, col_opts_b, col_opts_c = st.columns(3)
        with col_opts_a:
            include_dialogues = st.checkbox(
                "顯示 raw dialogues",
                value=False,
                disabled=(table_key != "blocks"),
                key="db_inspect_include_dialogues",
            )
        with col_opts_b:
            include_tombstones = st.checkbox(
                "包含 tombstones",
                value=True,
                disabled=(table_key != "profile"),
                key="db_inspect_include_tombstones",
            )
        with col_opts_c:
            include_global = st.checkbox(
                "包含 __global__ topic",
                value=True,
                disabled=(table_key != "topics"),
                key="db_inspect_include_global",
            )
            only_unmentioned = st.checkbox(
                "只看未提及 topic",
                value=False,
                disabled=(table_key != "topics"),
                key="db_inspect_only_unmentioned",
            )

        scope_text = (
            f"user_id={selected_user_id}, "
            f"character_id={'(不適用)' if table_key == 'profile' else selected_character_id}, "
            f"visibility={visibility}, table={table_options[table_key]}"
        )

        if st.button("🔎 載入此 scope 資料", key="load_raw_db", use_container_width=True):
            try:
                params = {
                    "user_id": selected_user_id,
                    "visibility": visibility,
                    "limit": int(limit),
                }
                if table_key in ("blocks", "core", "topics"):
                    params["character_id"] = selected_character_id
                if table_key == "blocks":
                    params["include_dialogues"] = include_dialogues
                elif table_key == "profile":
                    params["include_tombstones"] = include_tombstones
                elif table_key == "topics":
                    params["include_global"] = include_global
                    params["only_unmentioned"] = only_unmentioned

                rows = _api_get_json(api_base, f"/memory/inspect/{table_key}", params=params, timeout=30)
                st.subheader(table_options[table_key])
                st.caption(scope_text)
                _render_inspect_table(table_key, rows, scope_text)
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
