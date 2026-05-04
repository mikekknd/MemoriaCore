"""YouTubeBridge Streamlit UI。"""
from __future__ import annotations

import os

import requests
import streamlit as st


DEFAULT_BRIDGE_URL = os.getenv("YOUTUBE_BRIDGE_URL", "http://localhost:8091").rstrip("/")
BRIDGE_KEY = os.getenv("YOUTUBE_BRIDGE_API_KEY", "")


def _headers() -> dict[str, str]:
    return {"X-Bridge-Key": BRIDGE_KEY} if BRIDGE_KEY else {}


def _get(path: str):
    return requests.get(f"{DEFAULT_BRIDGE_URL}{path}", headers=_headers(), timeout=10)


def _post(path: str, payload: dict | None = None):
    return requests.post(f"{DEFAULT_BRIDGE_URL}{path}", json=payload or {}, headers=_headers(), timeout=300)


def _delete(path: str):
    return requests.delete(f"{DEFAULT_BRIDGE_URL}{path}", headers=_headers(), timeout=10)


def _json_or_text(resp):
    try:
        return resp.json()
    except Exception:
        return resp.text


def _load_memoria_characters() -> tuple[list[dict], str]:
    resp = _get("/memoria/characters")
    if not resp.ok:
        return [], str(_json_or_text(resp))
    data = resp.json()
    return data if isinstance(data, list) else [], ""


def _load_memoria_sessions() -> tuple[list[dict], str]:
    resp = _get("/memoria/sessions?limit=100")
    if not resp.ok:
        return [], str(_json_or_text(resp))
    data = resp.json()
    return data if isinstance(data, list) else [], ""


def _character_options(characters: list[dict], selected_ids: list[str] | None = None) -> tuple[list[str], dict[str, str]]:
    selected_ids = selected_ids or []
    labels: dict[str, str] = {}
    options: list[str] = []
    for char in characters:
        char_id = str(char.get("character_id") or "").strip()
        if not char_id:
            continue
        name = str(char.get("name") or char_id)
        labels[char_id] = f"{name} ({char_id})"
        options.append(char_id)
    for char_id in selected_ids:
        if char_id and char_id not in labels:
            labels[char_id] = f"{char_id}（未在角色清單中）"
            options.append(char_id)
    return options, labels


def _session_options(
    sessions: list[dict],
    selected_id: str | None = None,
    *,
    empty_label: str = "不指定",
) -> tuple[list[str], dict[str, str]]:
    selected_id = str(selected_id or "").strip()
    labels: dict[str, str] = {"": empty_label}
    options: list[str] = [""]
    for session in sessions:
        session_id = str(session.get("session_id") or "").strip()
        if not session_id:
            continue
        created_at = str(session.get("created_at") or "")
        channel = str(session.get("channel") or "")
        group_name = str(session.get("group_name") or "")
        character_ids = session.get("character_ids") or ([session.get("character_id")] if session.get("character_id") else [])
        ai_label = group_name or ", ".join(str(cid) for cid in character_ids if cid) or "default"
        message_count = int(session.get("message_count") or 0)
        labels[session_id] = f"{created_at} [{channel}] {ai_label} - {message_count} 則 - {session_id}"
        options.append(session_id)
    if selected_id and selected_id not in labels:
        labels[selected_id] = f"{selected_id}（未在 session 清單中）"
        options.append(selected_id)
    return options, labels


st.set_page_config(page_title="YouTubeBridge", layout="wide")
st.title("YouTubeBridge")
st.caption("管理 YouTube connector、live session，並把直播留言作為 external context 注入 MemoriaCore。")

health = _get("/health")
if not health.ok:
    st.error(f"無法連線 YouTubeBridge API：{health.status_code} {health.text}")
    st.stop()

memoria_characters, memoria_character_error = _load_memoria_characters()
memoria_sessions, memoria_session_error = _load_memoria_sessions()

tabs = st.tabs(["Connectors", "Live Sessions", "Inject", "Summary"])

with tabs[0]:
    st.subheader("Connectors")
    connectors_resp = _get("/connectors")
    connectors = connectors_resp.json() if connectors_resp.ok else []
    if connectors:
        for connector in connectors:
            with st.expander(f"{connector.get('display_name') or connector.get('connector_id')} ({connector.get('connector_id')})"):
                st.write(f"enabled: `{connector.get('enabled')}`")
                key_configured = bool(connector.get("api_key_configured"))
                st.write(f"api_key: `{'已設定' if key_configured else '未設定'}`")
                if key_configured:
                    st.caption("已儲存 API key。基於安全性不會顯示原文。")
                if st.button("刪除 connector", key=f"delete_connector_{connector.get('connector_id')}"):
                    resp = _delete(f"/connectors/{connector.get('connector_id')}")
                    if resp.ok:
                        st.success("已刪除")
                        st.rerun()
                    else:
                        st.error(_json_or_text(resp))
    else:
        st.info("尚未建立 connector。")

    st.divider()
    st.subheader("新增 / 更新 Connector")
    st.caption("Connector 只保存 YouTube API key 與啟用狀態；角色與目標對話請在 Live Session 設定。")
    with st.form("connector_form"):
        connector_id = st.text_input("Connector ID", value="youtube-main")
        display_name = st.text_input("顯示名稱", value="YouTube Main")
        api_key = st.text_input(
            "YouTube Data API Key",
            type="password",
            help="儲存後不會回填顯示。更新既有 connector 時，留空會保留目前已儲存的 key。",
        )
        enabled = st.checkbox("啟用", value=True)
        submitted = st.form_submit_button("儲存 connector", type="primary")
    if submitted:
        resp = _post("/connectors", {
            "connector_id": connector_id,
            "display_name": display_name,
            "api_key": api_key,
            "enabled": enabled,
        })
        if resp.ok:
            st.success("已儲存 connector")
            st.rerun()
        else:
            st.error(_json_or_text(resp))

with tabs[1]:
    st.subheader("Live Sessions")
    sessions_resp = _get("/sessions")
    sessions = sessions_resp.json() if sessions_resp.ok else []
    connector_ids = [c.get("connector_id") for c in connectors]
    if sessions:
        for session in sessions:
            runtime = session.get("runtime_status") or {}
            title = session.get("display_name") or session.get("session_id")
            with st.expander(f"{title} | {runtime.get('status', session.get('status'))}"):
                st.write(f"session_id: `{session.get('session_id')}`")
                st.write(f"connector_id: `{session.get('connector_id')}`")
                st.write(f"video_id: `{session.get('video_id')}`")
                st.write(f"live_chat_id: `{session.get('live_chat_id')}`")
                st.write(f"target_memoria_session_id: `{session.get('target_memoria_session_id')}`")
                st.write(f"character_ids: `{', '.join(session.get('character_ids') or [])}`")
                st.write(f"event_count: `{session.get('event_count', 0)}`")
                st.write(f"summary_status: `{session.get('summary_status', 'pending')}`")
                if session.get("finalized_at"):
                    st.write(f"finalized_at: `{session.get('finalized_at')}`")
                if session.get("summary_error"):
                    st.warning(f"summary_error: {session.get('summary_error')}")
                st.write(
                    "auto_inject: "
                    f"`{session.get('auto_inject')}` / "
                    f"{session.get('inject_interval_seconds')} 秒 / "
                    f"至少 {session.get('min_pending_events')} 則"
                )
                if runtime.get("last_auto_inject_at"):
                    st.caption(f"last_auto_inject_at: {runtime.get('last_auto_inject_at')}")
                if runtime.get("last_auto_inject_error"):
                    st.warning(f"last_auto_inject_error: {runtime.get('last_auto_inject_error')}")
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    if st.button("Start", key=f"start_{session.get('session_id')}"):
                        resp = _post(f"/sessions/{session.get('session_id')}/start")
                        st.success(_json_or_text(resp)) if resp.ok else st.error(_json_or_text(resp))
                        st.rerun()
                with col2:
                    if st.button("Stop", key=f"stop_{session.get('session_id')}"):
                        resp = _post(f"/sessions/{session.get('session_id')}/stop")
                        st.success(_json_or_text(resp)) if resp.ok else st.error(_json_or_text(resp))
                        st.rerun()
                with col3:
                    if st.button("Edit", key=f"edit_session_{session.get('session_id')}"):
                        st.session_state["session_edit"] = session
                        st.rerun()
                with col4:
                    if st.button("Delete", key=f"delete_session_{session.get('session_id')}"):
                        resp = _delete(f"/sessions/{session.get('session_id')}")
                        st.success("已刪除") if resp.ok else st.error(_json_or_text(resp))
                        if st.session_state.get("session_edit", {}).get("session_id") == session.get("session_id"):
                            st.session_state.pop("session_edit", None)
                        st.rerun()
    else:
        st.info("尚未建立 live session。")

    st.divider()
    session_draft = st.session_state.get("session_edit") or {}
    session_editing = bool(session_draft.get("session_id"))
    st.subheader("編輯 Live Session" if session_editing else "新增 Live Session")
    with st.form("session_form"):
        session_id = st.text_input(
            "Session ID",
            value=session_draft.get("session_id", "yt-live-main"),
            disabled=session_editing,
        )
        session_display_name = st.text_input("顯示名稱", value=session_draft.get("display_name", "YouTube Live"))
        connector_default = session_draft.get("connector_id") or (connector_ids[0] if connector_ids else "")
        connector_index = connector_ids.index(connector_default) if connector_default in connector_ids else 0
        connector_id = st.selectbox("Connector", connector_ids or [""], index=connector_index)
        video_id = st.text_input(
            "YouTube 直播 URL 或 video_id",
            value=session_draft.get("video_id", ""),
            help="可貼上 https://www.youtube.com/watch?v=...、https://www.youtube.com/live/... 或純 video_id；儲存時會轉成 video_id。",
        )
        live_chat_id = st.text_input("live_chat_id（可留空，啟動時由 video_id 解析）", value=session_draft.get("live_chat_id", ""))
        current_target_session_id = session_draft.get("target_memoria_session_id", "")
        if memoria_session_error:
            st.warning(f"無法讀取 MemoriaCore session 清單，暫時改用手動輸入：{memoria_session_error}")
            target_memoria_session_id = st.text_input(
                "目標 MemoriaCore session_id",
                value=current_target_session_id,
            )
        else:
            session_options, session_labels = _session_options(
                memoria_sessions,
                current_target_session_id,
                empty_label="不指定（注入時由 MemoriaCore 建立新對話）",
            )
            target_memoria_session_id = st.selectbox(
                "目標 MemoriaCore session_id",
                options=session_options,
                index=session_options.index(current_target_session_id) if current_target_session_id in session_options else 0,
                format_func=lambda sid: session_labels.get(sid, sid),
            )
        current_character_ids = session_draft.get("character_ids") or []
        character_options, character_labels = _character_options(memoria_characters, current_character_ids)
        if character_options:
            selected_character_ids = st.multiselect(
                "角色（實際保存 character_id）",
                options=character_options,
                default=current_character_ids,
                format_func=lambda char_id: character_labels.get(char_id, char_id),
            )
        else:
            if memoria_character_error:
                st.warning(f"無法讀取 MemoriaCore 角色清單，暫時改用手動輸入：{memoria_character_error}")
            character_ids_raw = st.text_input(
                "character_ids（逗號分隔，可留空）",
                value=", ".join(current_character_ids),
            )
            selected_character_ids = [x.strip() for x in character_ids_raw.split(",") if x.strip()]
        auto_connect = st.checkbox("server 啟動時自動連線", value=bool(session_draft.get("auto_connect", False)))
        auto_inject = st.checkbox(
            "自動注入待處理留言",
            value=bool(session_draft.get("auto_inject", False)),
            help="Live session 啟動後，背景程序會定期把尚未注入的留言送入目標 MemoriaCore session。",
        )
        inject_interval_seconds = st.number_input(
            "自動注入檢查間隔（秒）",
            min_value=5,
            max_value=600,
            value=int(session_draft.get("inject_interval_seconds", 30) or 30),
            disabled=not auto_inject,
        )
        min_pending_events = st.number_input(
            "至少累積幾則留言才自動注入",
            min_value=1,
            max_value=100,
            value=int(session_draft.get("min_pending_events", 1) or 1),
            disabled=not auto_inject,
        )
        max_context_messages = st.number_input(
            "注入最多留言數",
            min_value=1,
            max_value=100,
            value=int(session_draft.get("max_context_messages", 50) or 50),
        )
        max_context_chars = st.number_input(
            "注入字元上限",
            min_value=1000,
            max_value=20000,
            value=int(session_draft.get("max_context_chars", 8000) or 8000),
        )
        retention_days = st.number_input(
            "暫存保留天數",
            min_value=1,
            max_value=365,
            value=int(session_draft.get("retention_days", 30) or 30),
        )
        col_save_session, col_clear_session = st.columns(2)
        session_submitted = col_save_session.form_submit_button("儲存 live session", type="primary")
        session_cancelled = col_clear_session.form_submit_button("取消編輯" if session_editing else "清空表單")
    if session_cancelled:
        st.session_state.pop("session_edit", None)
        st.rerun()
    if session_submitted:
        resp = _post("/sessions", {
            "session_id": session_draft.get("session_id") if session_editing else session_id,
            "connector_id": connector_id,
            "display_name": session_display_name,
            "video_id": video_id,
            "live_chat_id": live_chat_id,
            "target_memoria_session_id": target_memoria_session_id,
            "character_ids": selected_character_ids,
            "status": session_draft.get("status", "stopped"),
            "auto_connect": auto_connect,
            "auto_inject": auto_inject,
            "inject_interval_seconds": int(inject_interval_seconds),
            "min_pending_events": int(min_pending_events),
            "max_context_messages": int(max_context_messages),
            "max_context_chars": int(max_context_chars),
            "retention_days": int(retention_days),
        })
        if resp.ok:
            st.success("已儲存 live session")
            st.session_state.pop("session_edit", None)
            st.rerun()
        else:
            st.error(_json_or_text(resp))

with tabs[3]:
    st.subheader("直播摘要")
    st.caption("Phase 2 第一版只把摘要保存到 YouTubeBridge DB，不會自動寫入 MemoriaCore 長期記憶。")
    session_ids = [s.get("session_id") for s in sessions]
    selected_summary_session = st.selectbox("來源 live session", session_ids or [""], index=0, key="summary_session")
    selected_summary_config = next((s for s in sessions if s.get("session_id") == selected_summary_session), {})

    if selected_summary_session:
        st.write(f"video_id: `{selected_summary_config.get('video_id', '')}`")
        st.write(f"event_count: `{selected_summary_config.get('event_count', 0)}`")
        st.write(f"summary_status: `{selected_summary_config.get('summary_status', 'pending')}`")
        if selected_summary_config.get("finalized_at"):
            st.write(f"finalized_at: `{selected_summary_config.get('finalized_at')}`")
        if selected_summary_config.get("summary_error"):
            st.warning(selected_summary_config.get("summary_error"))

        col_finalize, col_refresh = st.columns(2)
        with col_finalize:
            if st.button("標記直播結束", disabled=not selected_summary_session):
                resp = _post(f"/sessions/{selected_summary_session}/finalize")
                st.success("已標記直播結束") if resp.ok else st.error(_json_or_text(resp))
                st.rerun()
        with col_refresh:
            if st.button("重新整理摘要狀態"):
                st.rerun()

        st.divider()
        st.write("摘要設定")
        min_events = st.number_input("最少留言數", min_value=1, max_value=1000, value=1)
        max_summary_events = st.number_input("最多摘要留言數", min_value=1, max_value=5000, value=1000)
        chunk_size = st.number_input("分段大小", min_value=20, max_value=500, value=120)
        col_summary, col_force_summary = st.columns(2)
        with col_summary:
            if st.button("產生摘要", type="primary", disabled=not selected_summary_session):
                resp = _post(f"/sessions/{selected_summary_session}/summarize", {
                    "force": False,
                    "min_events": int(min_events),
                    "max_events": int(max_summary_events),
                    "chunk_size": int(chunk_size),
                })
                if resp.ok:
                    st.success("摘要完成")
                    st.json(resp.json())
                    st.rerun()
                else:
                    st.error(_json_or_text(resp))
        with col_force_summary:
            if st.button("強制重跑摘要", disabled=not selected_summary_session):
                resp = _post(f"/sessions/{selected_summary_session}/summarize", {
                    "force": True,
                    "min_events": int(min_events),
                    "max_events": int(max_summary_events),
                    "chunk_size": int(chunk_size),
                })
                if resp.ok:
                    st.success("已重跑摘要")
                    st.json(resp.json())
                    st.rerun()
                else:
                    st.error(_json_or_text(resp))

        st.divider()
        summary_resp = _get(f"/sessions/{selected_summary_session}/summary")
        if summary_resp.ok:
            summary = summary_resp.json()
            st.subheader(summary.get("title") or "摘要")
            st.write(summary.get("summary_text", ""))
            if summary.get("audience_mood"):
                st.write(f"觀眾反應：{summary.get('audience_mood')}")
            if summary.get("topic_tags"):
                st.write("主題")
                st.write(", ".join(summary.get("topic_tags") or []))
            if summary.get("key_points"):
                st.write("重點")
                for point in summary.get("key_points") or []:
                    st.write(f"- {point}")
            if summary.get("qa_pairs"):
                st.write("重要問答")
                for pair in summary.get("qa_pairs") or []:
                    question = pair.get("question", "")
                    answer = pair.get("answer", "")
                    st.write(f"Q: {question}")
                    st.write(f"A: {answer}")
            if summary.get("memory_text"):
                st.write("預備寫入共通記憶的文字")
                st.info(summary.get("memory_text"))
            with st.expander("摘要原始資料"):
                st.json(summary)
        else:
            st.info("目前尚未產生摘要。")
    else:
        st.info("尚未建立 live session。")

with tabs[2]:
    st.subheader("注入 MemoriaCore")
    st.caption("此頁會先讀取尚未注入的 YouTube 留言，送出成功後會標記為已處理，不會回寫 YouTube。")
    session_ids = [s.get("session_id") for s in sessions]
    selected_session = st.selectbox("來源 live session", session_ids or [""], index=0, key="reply_session")
    selected_session_config = next((s for s in sessions if s.get("session_id") == selected_session), {})
    content = st.text_area("送給 AI 的 user content", value="請根據已帶入的 YouTube 直播留言上下文回應。不要開啟瀏覽器或搜尋網頁。")
    if memoria_session_error:
        st.warning(f"無法讀取 MemoriaCore session 清單，暫時改用手動輸入：{memoria_session_error}")
        memoria_session_id = st.text_input("覆寫目標 MemoriaCore session_id（留空使用 live session 設定）")
    else:
        reply_session_options, reply_session_labels = _session_options(
            memoria_sessions,
            "",
            empty_label="使用 Live Session 設定",
        )
        memoria_session_id = st.selectbox(
            "覆寫目標 MemoriaCore session_id",
            options=reply_session_options,
            index=0,
            format_func=lambda sid: reply_session_labels.get(sid, sid),
        )
    reply_character_options, reply_character_labels = _character_options(memoria_characters, [])
    if reply_character_options:
        reply_character_ids = st.multiselect(
            "覆寫角色（留空使用 live session 設定）",
            options=reply_character_options,
            default=[],
            format_func=lambda char_id: reply_character_labels.get(char_id, char_id),
        )
    else:
        if memoria_character_error:
            st.warning(f"無法讀取 MemoriaCore 角色清單，暫時改用手動輸入：{memoria_character_error}")
        character_ids_raw = st.text_input("覆寫 character_ids（逗號分隔，可留空）")
        reply_character_ids = [x.strip() for x in character_ids_raw.split(",") if x.strip()]
    max_context_default = int(selected_session_config.get("max_context_messages", 50) or 50)
    max_events = st.slider("預覽 / 注入留言數", min_value=1, max_value=100, value=min(max_context_default, 100))

    last_result = st.session_state.pop("last_injection_result", None)
    if last_result:
        st.success(f"已送入 MemoriaCore，並標記 {last_result.get('marked_injected', 0)} 則留言為已處理")
        st.json(last_result.get("summary", {}))
        st.write(last_result.get("memoria_result", {}).get("reply", ""))

    if st.button("重新讀取待注入留言", disabled=not selected_session):
        st.rerun()

    pending_events: list[dict] = []
    if selected_session:
        recent_resp = _get(f"/sessions/{selected_session}/recent?limit={max_events}&uninjected_only=true")
        if recent_resp.ok:
            pending_events = recent_resp.json().get("events", [])
        else:
            st.error(_json_or_text(recent_resp))
    if pending_events:
        st.write(f"待注入留言：{len(pending_events)} 則")
        for event in pending_events:
            author = event.get("author_display_name") or "匿名觀眾"
            amount = event.get("amount_display_string")
            prefix = f"[SC {amount}] " if amount else ""
            st.write(f"{event.get('id')} | {prefix}{author}: {event.get('message_text')}")
    elif selected_session:
        st.info("目前沒有尚未注入的留言。")

    if st.button("送入 MemoriaCore", type="primary", disabled=not selected_session or not pending_events):
        resp = _post(f"/sessions/{selected_session}/reply-recent", {
            "content": content,
            "memoria_session_id": memoria_session_id,
            "character_ids": reply_character_ids,
            "event_ids": [event.get("id") for event in pending_events],
            "max_events": max_events,
        })
        if resp.ok:
            st.session_state["last_injection_result"] = resp.json()
            st.rerun()
        else:
            st.error(_json_or_text(resp))
