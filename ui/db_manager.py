# 【環境假設】：Python 3.12, Streamlit 1.30+。記憶庫管理獨立視圖模組。
# 已遷移為瘦客戶端：所有業務邏輯透過 FastAPI REST API 執行。
import json

import streamlit as st
import pandas as pd
from core.i18n import DEFAULT_LOCALE, normalize_locale, t
from ui import api_client as requests


def _locale(user_prefs: dict | None = None) -> str:
    try:
        return normalize_locale((user_prefs or {}).get("ui_locale"))
    except ValueError:
        return DEFAULT_LOCALE


def _api_get_json(api_base: str, path: str, params: dict | None = None, timeout: int = 15):
    resp = requests.get(f"{api_base}{path}", params=params, timeout=timeout)
    if not resp.ok:
        raise RuntimeError(resp.text)
    return resp.json()


def _api_post_json(api_base: str, path: str, json_body: dict | None = None, timeout: int = 15):
    resp = requests.post(f"{api_base}{path}", json=json_body or {}, timeout=timeout)
    if not resp.ok:
        raise RuntimeError(resp.text)
    return resp.json()


def _api_delete_json(api_base: str, path: str, params: dict | None = None, timeout: int = 15):
    resp = requests.delete(f"{api_base}{path}", params=params, timeout=timeout)
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


def _short_text(value, limit: int = 80) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:max(0, limit - 3)] + "..."


def _row_delete_label(table_key: str, row: dict) -> str:
    if table_key == "blocks":
        return f"{row.get('block_id', '')} - {_short_text(row.get('overview'))}"
    if table_key == "core":
        return f"{row.get('core_id', '')} - {_short_text(row.get('insight'))}"
    if table_key == "profile":
        return f"{row.get('fact_key', '')}={_short_text(row.get('fact_value'))}"
    return f"{row.get('topic_id', '')} - {_short_text(row.get('summary_content'))}"


def _render_maintenance_controls(api_base: str, locale: str) -> None:
    try:
        status = _api_get_json(api_base, "/memory/maintenance", timeout=10)
    except Exception as e:
        st.error(t("db_manager.streamlit.maintenance_load_failed", locale, message=e))
        return

    current_enabled = bool(status.get("enabled"))
    desired_enabled = st.toggle(
        t("db_manager.streamlit.maintenance_toggle", locale),
        value=current_enabled,
        help=t("db_manager.streamlit.maintenance_help", locale),
        key="db_maintenance_mode_toggle",
    )
    if desired_enabled != current_enabled:
        try:
            updated = _api_post_json(
                api_base,
                "/memory/maintenance",
                {"enabled": desired_enabled},
                timeout=10,
            )
            st.success(
                t(
                    "db_manager.streamlit.maintenance_enabled"
                    if updated.get("enabled")
                    else "db_manager.streamlit.maintenance_disabled",
                    locale,
                )
            )
            st.rerun()
        except Exception as e:
            st.error(t("db_manager.streamlit.maintenance_update_failed", locale, message=e))

    if current_enabled:
        st.warning(t("db_manager.streamlit.maintenance_active_warning", locale))

    if st.button(t("db_manager.streamlit.refresh_cache", locale), use_container_width=True):
        try:
            _api_post_json(api_base, "/memory/maintenance/refresh-cache", {}, timeout=30)
            st.session_state.pop("db_inspect_meta", None)
            st.success(t("db_manager.streamlit.refresh_cache_done", locale))
        except Exception as e:
            st.error(t("db_manager.streamlit.refresh_cache_failed", locale, message=e))

    droppable = [r for r in status.get("droppable_tables", []) if r.get("exists")]
    if droppable:
        st.divider()
        st.subheader(t("db_manager.streamlit.drop_legacy_title", locale))
        st.caption(t("db_manager.streamlit.drop_legacy_caption", locale))
        selected = st.selectbox(
            t("db_manager.streamlit.drop_legacy_table", locale),
            droppable,
            format_func=lambda r: f"{r.get('table_name')} ({r.get('count', 0)} rows)",
            key="db_drop_legacy_table",
        )
        table_name = selected.get("table_name", "")
        confirm = st.text_input(
            t("db_manager.streamlit.drop_legacy_confirm", locale, table=table_name),
            key="db_drop_legacy_confirm",
        )
        if st.button(
            t("db_manager.streamlit.drop_legacy_button", locale),
            disabled=(confirm != table_name),
            use_container_width=True,
        ):
            try:
                result = _api_post_json(
                    api_base,
                    "/memory/maintenance/drop-table",
                    {"table_name": table_name, "confirm_table_name": confirm},
                    timeout=30,
                )
                st.success(t("db_manager.streamlit.drop_legacy_done", locale, table=result.get("table", table_name)))
                st.session_state.pop("db_inspect_meta", None)
                st.rerun()
            except Exception as e:
                st.error(t("db_manager.streamlit.drop_legacy_failed", locale, message=e))


def _delete_row(api_base: str, table_key: str, row: dict):
    if table_key == "blocks":
        return _api_delete_json(
            api_base,
            f"/memory/maintenance/blocks/{row.get('block_id')}",
            {
                "user_id": row.get("user_id", ""),
                "character_id": row.get("character_id", ""),
                "visibility": row.get("visibility", ""),
            },
            timeout=30,
        )
    if table_key == "core":
        return _api_delete_json(
            api_base,
            f"/memory/maintenance/core/{row.get('core_id')}",
            {
                "user_id": row.get("user_id", ""),
                "character_id": row.get("character_id", ""),
                "visibility": row.get("visibility", ""),
            },
            timeout=30,
        )
    if table_key == "profile":
        return _api_delete_json(
            api_base,
            "/memory/maintenance/profile",
            {
                "user_id": row.get("user_id", ""),
                "fact_key": row.get("fact_key", ""),
                "fact_value": row.get("fact_value", ""),
                "visibility": row.get("visibility", ""),
            },
            timeout=30,
        )
    return _api_delete_json(
        api_base,
        f"/memory/maintenance/topics/{row.get('topic_id')}",
        {
            "user_id": row.get("user_id", ""),
            "character_id": row.get("character_id", ""),
            "visibility": row.get("visibility", ""),
        },
        timeout=30,
    )


def _render_delete_controls(api_base: str, table_key: str, rows: list[dict], locale: str) -> None:
    if not rows:
        return
    st.divider()
    st.subheader(t("db_manager.streamlit.delete_title", locale))
    st.warning(t("db_manager.streamlit.delete_warning", locale))
    selected_row = st.selectbox(
        t("db_manager.streamlit.delete_target", locale),
        rows,
        format_func=lambda r: _row_delete_label(table_key, r),
        key=f"db_delete_target_{table_key}",
    )
    st.json(selected_row, expanded=False)
    confirm = st.text_input(
        t("db_manager.streamlit.delete_confirm", locale),
        key=f"db_delete_confirm_{table_key}",
    )
    if st.button(
        t("db_manager.streamlit.delete_button", locale),
        disabled=(confirm != "DELETE"),
        type="primary",
        use_container_width=True,
    ):
        try:
            result = _delete_row(api_base, table_key, selected_row)
            st.success(t("db_manager.streamlit.delete_done", locale, deleted=result.get("deleted", 0)))
            st.session_state.pop("db_inspect_meta", None)
            st.session_state.pop("db_inspect_rows", None)
            st.rerun()
        except Exception as e:
            st.error(t("db_manager.streamlit.delete_failed", locale, message=e))


def _render_inspect_table(table_key: str, rows: list[dict], scope_text: str, locale: str) -> None:
    if not rows:
        st.info(t("db_manager.streamlit.scope_empty", locale, scope=scope_text))
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
            "status": t("db_manager.streamlit.revoked", locale) if r.get("confidence", 1) < 0 else t("db_manager.streamlit.valid", locale),
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
            "status": t("db_manager.streamlit.mentioned", locale) if r.get("is_mentioned_to_user") else t("db_manager.streamlit.unmentioned", locale),
            "interest_keyword": r.get("interest_keyword", ""),
            "summary_content": r.get("summary_content", ""),
            "topic_id": r.get("topic_id", ""),
        } for r in rows])

    st.dataframe(df, use_container_width=True)


def render_db_manager_page(api_base, user_prefs):
    locale = _locale(user_prefs)
    st.title(t("db_manager.streamlit.title", locale))

    tab_memory, tab_raw_db, tab_dev = st.tabs([
        t("db_manager.streamlit.tab_memory", locale),
        t("db_manager.streamlit.tab_raw", locale),
        t("db_manager.streamlit.tab_dev", locale),
    ])

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Tab 1: 記憶操作
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    with tab_memory:
        current_cluster_threshold = user_prefs.get("cluster_threshold", 0.75)

        st.subheader(t("db_manager.streamlit.consolidate_title", locale))
        st.info(t("db_manager.streamlit.threshold_info", locale, value=f"{current_cluster_threshold:.2f}"))

        if st.button(t("db_manager.streamlit.consolidate_button", locale), use_container_width=True, type="primary"):
            with st.spinner(t("db_manager.streamlit.consolidating", locale)):
                try:
                    resp = requests.post(
                        f"{api_base}/system/consolidate",
                        json={"cluster_threshold": current_cluster_threshold, "min_group_size": 2},
                        timeout=300,
                    )
                    if resp.ok:
                        result = resp.json()
                        if result.get("status") == "no_clusters":
                            st.info(t("db_manager.streamlit.no_clusters", locale))
                        else:
                            st.success(t("db_manager.streamlit.consolidate_started", locale, count=result.get("cluster_count", 0)))
                    else:
                        st.error(t("db_manager.streamlit.consolidate_failed", locale, message=resp.text))
                except Exception as e:
                    st.error(t("db_manager.streamlit.consolidate_failed", locale, message=e))

        st.divider()

        st.subheader(t("db_manager.streamlit.preference_title", locale))
        st.info(t("db_manager.streamlit.preference_info", locale))
        agg_threshold = st.slider(t("db_manager.streamlit.preference_threshold", locale), min_value=1.0, max_value=10.0, value=3.0, step=0.5, key="pref_agg_threshold")
        if st.button(t("db_manager.streamlit.preference_button", locale), use_container_width=True):
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
                        st.success(t("db_manager.streamlit.preference_promoted", locale, promoted=promoted, written=written))
                    else:
                        st.info(t("db_manager.streamlit.preference_none", locale))
                else:
                    st.error(t("db_manager.streamlit.preference_failed", locale, message=resp.text))
            except Exception as e:
                st.error(t("db_manager.streamlit.preference_failed", locale, message=e))

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Tab 2: 底層資料庫
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    with tab_raw_db:
        st.subheader(t("db_manager.streamlit.inspect_title", locale))
        st.caption(t("db_manager.streamlit.inspect_caption", locale))

        with st.expander(t("db_manager.streamlit.maintenance_panel", locale), expanded=False):
            _render_maintenance_controls(api_base, locale)

        meta_key = "db_inspect_meta"
        if st.button(t("db_manager.streamlit.reload_scopes", locale), key="reload_db_inspect_meta"):
            st.session_state.pop(meta_key, None)
            st.session_state.pop("db_inspect_rows", None)

        try:
            if meta_key not in st.session_state:
                st.session_state[meta_key] = _load_inspect_meta(api_base)
            scopes, char_names = st.session_state[meta_key]
        except Exception as e:
            st.error(t("db_manager.streamlit.load_scopes_failed", locale, message=e))
            scopes, char_names = {"users": [], "character_ids": [], "visibilities": []}, {}

        users = scopes.get("users") or [{"user_id": uid} for uid in scopes.get("user_ids", [])]
        character_ids = set(scopes.get("character_ids") or [])
        character_ids.update(char_names.keys())
        character_ids.update({"default", "__global__"})
        character_options = sorted(character_ids, key=lambda cid: (cid != "default", cid != "__global__", cid))

        table_options = {
            "blocks": t("db_manager.streamlit.table_blocks", locale),
            "core": t("db_manager.streamlit.table_core", locale),
            "profile": t("db_manager.streamlit.table_profile", locale),
            "topics": t("db_manager.streamlit.table_topics", locale),
        }

        col_table, col_user = st.columns([1, 2])
        with col_table:
            table_key = st.selectbox(
                t("db_manager.streamlit.table", locale),
                list(table_options.keys()),
                format_func=lambda k: table_options[k],
                key="db_inspect_table",
            )
        with col_user:
            if users:
                selected_user = st.selectbox(
                    t("db_manager.streamlit.user", locale),
                    users,
                    format_func=_user_label,
                    key="db_inspect_user",
                )
                selected_user_id = str(selected_user.get("user_id", ""))
            else:
                selected_user_id = st.text_input(t("db_manager.streamlit.user_id", locale), value="default", key="db_inspect_user_fallback")

        col_char, col_vis, col_limit = st.columns([2, 1, 1])
        with col_char:
            selected_character_id = st.selectbox(
                t("db_manager.streamlit.character_scope", locale),
                character_options,
                format_func=lambda cid: _character_label(cid, char_names),
                disabled=(table_key == "profile"),
                key="db_inspect_character",
            )
            if table_key == "profile":
                st.caption(t("db_manager.streamlit.profile_scope_note", locale))
        with col_vis:
            visibility = st.selectbox(
                "Visibility",
                ["all", "public", "private"],
                format_func=lambda v: t("db_manager.streamlit.all", locale) if v == "all" else v,
                key="db_inspect_visibility",
            )
        with col_limit:
            limit = st.number_input("Limit", min_value=1, max_value=1000, value=200, step=50, key="db_inspect_limit")

        col_opts_a, col_opts_b, col_opts_c = st.columns(3)
        with col_opts_a:
            include_dialogues = st.checkbox(
                t("db_manager.streamlit.show_raw_dialogues", locale),
                value=False,
                disabled=(table_key != "blocks"),
                key="db_inspect_include_dialogues",
            )
        with col_opts_b:
            include_tombstones = st.checkbox(
                t("db_manager.streamlit.include_tombstones", locale),
                value=True,
                disabled=(table_key != "profile"),
                key="db_inspect_include_tombstones",
            )
        with col_opts_c:
            include_global = st.checkbox(
                t("db_manager.streamlit.include_global", locale),
                value=True,
                disabled=(table_key != "topics"),
                key="db_inspect_include_global",
            )
            only_unmentioned = st.checkbox(
                t("db_manager.streamlit.only_unmentioned", locale),
                value=False,
                disabled=(table_key != "topics"),
                key="db_inspect_only_unmentioned",
            )

        scope_text = (
            f"user_id={selected_user_id}, "
            f"character_id={t('db_manager.streamlit.not_applicable', locale) if table_key == 'profile' else selected_character_id}, "
            f"visibility={visibility}, table={table_options[table_key]}"
        )

        if st.button(t("db_manager.streamlit.load_scope", locale), key="load_raw_db", use_container_width=True):
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
                st.session_state["db_inspect_rows"] = {
                    "table_key": table_key,
                    "table_label": table_options[table_key],
                    "scope_text": scope_text,
                    "rows": rows,
                }
            except Exception as e:
                st.error(t("db_manager.streamlit.load_db_failed", locale, message=e))

        loaded = st.session_state.get("db_inspect_rows")
        if loaded:
            loaded_table = loaded.get("table_key", table_key)
            loaded_rows = loaded.get("rows") or []
            st.subheader(loaded.get("table_label") or table_options.get(loaded_table, loaded_table))
            st.caption(loaded.get("scope_text", ""))
            _render_inspect_table(loaded_table, loaded_rows, loaded.get("scope_text", ""), locale)
            _render_delete_controls(api_base, loaded_table, loaded_rows, locale)

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
