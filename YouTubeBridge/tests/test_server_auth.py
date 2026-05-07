import importlib.util
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE_ROOT))

spec = importlib.util.spec_from_file_location("youtube_bridge_server_for_auth", BRIDGE_ROOT / "server.py")
server_module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(server_module)
require_bridge_key = server_module.require_bridge_key


def _request(host: str, key: str = "", path: str = ""):
    return SimpleNamespace(
        client=SimpleNamespace(host=host),
        headers={"X-Bridge-Key": key} if key else {},
        url=SimpleNamespace(path=path) if path else None,
    )


def _control_ui_source() -> str:
    static_root = Path(server_module.STATIC_ROOT)
    parts = [(static_root / "index.html").read_text(encoding="utf-8")]
    ui_root = static_root / "ui"
    if ui_root.exists():
        for name in ("index.css", "core.js", "selectors.js", "topic-packs.js", "control.js", "app.js"):
            path = ui_root / name
            if path.exists():
                parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_bridge_key_is_required_even_for_loopback(monkeypatch):
    monkeypatch.setenv("YOUTUBE_BRIDGE_API_KEY", "secret")

    with pytest.raises(HTTPException) as exc:
        require_bridge_key(_request("127.0.0.1"))

    assert exc.value.status_code == 403


def test_bridge_key_accepts_matching_loopback_header(monkeypatch):
    monkeypatch.setenv("YOUTUBE_BRIDGE_API_KEY", "secret")

    require_bridge_key(_request("127.0.0.1", key="secret"))


def test_ui_config_bypasses_key_only_for_loopback(monkeypatch):
    monkeypatch.setenv("YOUTUBE_BRIDGE_API_KEY", "secret")

    require_bridge_key(_request("127.0.0.1", path="/ui-config"))

    with pytest.raises(HTTPException) as exc:
        require_bridge_key(_request("203.0.113.10", path="/ui-config"))

    assert exc.value.status_code == 403


def test_ui_assets_bypass_key_only_for_loopback(monkeypatch):
    monkeypatch.setenv("YOUTUBE_BRIDGE_API_KEY", "secret")

    require_bridge_key(_request("127.0.0.1", path="/ui-assets/app.js"))

    with pytest.raises(HTTPException) as exc:
        require_bridge_key(_request("203.0.113.10", path="/ui-assets/app.js"))

    assert exc.value.status_code == 403


def test_live_page_static_files_are_registered():
    static_root = Path(server_module.STATIC_ROOT)

    assert (static_root / "live.html").exists()
    assert (static_root / "live_chat.html").exists()


def test_memoriacore_launcher_uses_windows_selector_policy_before_uvicorn_import():
    source = (BRIDGE_ROOT.parent / "run_server.py").read_text(encoding="utf-8")

    assert "WindowsSelectorEventLoopPolicy" in source
    assert source.index("WindowsSelectorEventLoopPolicy") < source.index("import uvicorn")


def test_bridge_server_uses_windows_selector_policy_before_uvicorn_import():
    source = (BRIDGE_ROOT / "server.py").read_text(encoding="utf-8")

    assert "WindowsSelectorEventLoopPolicy" in source
    assert source.index("WindowsSelectorEventLoopPolicy") < source.index("import uvicorn")


def test_bridge_hot_reload_does_not_watch_factcard_markdown_files():
    source = (BRIDGE_ROOT / "run_server_hot_reload.py").read_text(encoding="utf-8")

    assert '"*.md"' not in source
    assert '"*.py", "*.html", "*.js", "*.css", "*.json"' in source


def test_bridge_hot_reload_launcher_uses_full_process_tree_cleanup():
    start_script = (BRIDGE_ROOT / "start_hot_reload.bat").read_text(encoding="utf-8")
    stop_script_path = BRIDGE_ROOT / "stop_8091.bat"

    assert stop_script_path.exists()
    assert 'call "%~dp0stop_8091.bat"' in start_script
    assert start_script.index('call "%~dp0stop_8091.bat"') < start_script.index('run_server_hot_reload.py')
    assert "Get-NetTCPConnection -LocalPort %API_PORT% -State Listen" not in start_script
    assert "Stop-Process -Id $_ -Force" not in start_script


def test_bridge_launchers_write_process_logs_under_runtime_log():
    start_script = (BRIDGE_ROOT / "start.bat").read_text(encoding="utf-8")
    hot_reload_script = (BRIDGE_ROOT / "start_hot_reload.bat").read_text(encoding="utf-8")

    for source in (start_script, hot_reload_script):
        assert r"runtime\log" in source
        assert ".out.log" in source
        assert ".err.log" in source
        assert r"runtime\youtube_bridge" not in source.lower()


def test_memoriacore_launchers_write_process_logs_under_runtime_log():
    root = BRIDGE_ROOT.parent
    scripts = [
        root / "start.bat",
        root / "start_full.bat",
        root / "startServerHotReload.bat",
    ]

    for script in scripts:
        source = script.read_text(encoding="utf-8")
        assert r"runtime\log" in source
        assert ".out.log" in source
        assert ".err.log" in source
        assert r"runtime\api_8088" not in source.lower()


def test_bridge_launcher_is_api_only_without_streamlit():
    start_script = (BRIDGE_ROOT / "start.bat").read_text(encoding="utf-8")
    requirements = (BRIDGE_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()

    assert not (BRIDGE_ROOT / "app.py").exists()
    assert "streamlit" not in start_script.lower()
    assert "streamlit" not in requirements
    assert "8503" not in start_script
    assert "server.py" in start_script


def test_stop_8091_script_kills_listener_wrappers_and_worker_tree():
    batch_source = (BRIDGE_ROOT / "stop_8091.bat").read_text(encoding="utf-8")
    source = (BRIDGE_ROOT / "stop_8091.ps1").read_text(encoding="utf-8")

    assert 'set "BRIDGE_ROOT=%~dp0."' in batch_source
    assert "stop_8091.ps1" in batch_source
    assert "Get-NetTCPConnection -LocalPort $Port -State Listen" in source
    assert "Win32_Process" in source
    assert "start_hot_reload.bat" in source
    assert "run_server_hot_reload.py" in source
    assert "ParentProcessId" in source
    assert "taskkill.exe" in source
    assert "/T" in source
    assert "/F" in source
    assert "[KILL]" in source
    assert "[REMAINING]" in source


def test_live_page_propagates_requested_session_id_to_live_chat_frame():
    live_html = (Path(server_module.STATIC_ROOT) / "live.html").read_text(encoding="utf-8")

    assert 'id="liveChatFrame"' in live_html
    assert "URLSearchParams(location.search)" in live_html
    assert "session_id" in live_html


def test_live_chat_uses_immediate_sse_refresh_for_chat_payloads():
    live_chat_html = (Path(server_module.STATIC_ROOT) / "live_chat.html").read_text(encoding="utf-8")

    assert "LIVE_CHAT_REFRESH_TYPES" in live_chat_html
    assert '"chat_message"' in live_chat_html
    assert '"youtube_live_event"' in live_chat_html
    assert '"interaction_completed"' in live_chat_html
    assert '"director_injected"' in live_chat_html
    assert "appendChatMessage(payload.message)" in live_chat_html
    assert "function ensureSubscription()" in live_chat_html
    assert "state.subscribedSessionId === state.sessionId" in live_chat_html
    assert live_chat_html.index("ensureSubscription();") < live_chat_html.index(
        'api(`/sessions/${encodeURIComponent(state.sessionId)}/chat-preview?limit=120`)'
    )
    assert "state.displayMessages, state.liveEventMessages, data.messages || []" in live_chat_html
    assert '${message.role || "message"}:${messageId}' in live_chat_html
    assert "scheduleRefresh(0)" in live_chat_html
    assert "setInterval(() => refreshChat({ silent: true }), 8000)" not in live_chat_html


def test_live_chat_polls_memoria_history_while_sse_is_connected():
    live_chat_html = (Path(server_module.STATIC_ROOT) / "live_chat.html").read_text(encoding="utf-8")

    assert "historyRefreshTimer" in live_chat_html
    assert "function startHistoryRefresh()" in live_chat_html
    assert "startHistoryRefresh();" in live_chat_html
    assert "setInterval(async () => {" in live_chat_html
    assert "await refreshChat({ silent: true })" in live_chat_html


def test_live_chat_assigns_stable_assistant_bubble_colors():
    live_chat_html = (Path(server_module.STATIC_ROOT) / "live_chat.html").read_text(encoding="utf-8")

    assert "ASSISTANT_COLOR_CLASSES" in live_chat_html
    assert "characterColorMap" in live_chat_html
    assert "function setCharacterColorMap(characterIds)" in live_chat_html
    assert "function characterColorClass(message)" in live_chat_html
    assert "state.characterColorMap[colorKey]" in live_chat_html
    assert "setCharacterColorMap(selected.character_ids || [])" in live_chat_html
    assert "style=\"--character-color:" in live_chat_html
    assert ".msg.assistant.character-color-0" in live_chat_html
    assert ".msg.assistant.character-color-5" in live_chat_html


def test_live_chat_shows_elapsed_and_target_duration():
    live_chat_html = (Path(server_module.STATIC_ROOT) / "live_chat.html").read_text(encoding="utf-8")

    assert 'id="durationBadge"' in live_chat_html
    assert "durationRefreshTimer" in live_chat_html
    assert "function formatDuration(seconds)" in live_chat_html
    assert "function updateDurationBadge()" in live_chat_html
    assert "function startDurationRefresh()" in live_chat_html
    assert "selected.started_at || selected.created_at" in live_chat_html
    assert "selected.planned_duration_minutes" in live_chat_html
    assert "已直播" in live_chat_html
    assert "目標" in live_chat_html


def test_control_ui_honors_requested_session_id_on_initial_load():
    index_html = _control_ui_source()

    assert "function requestedSessionIdFromUrl()" in index_html
    assert "loadSessions(requestedSessionIdFromUrl())" in index_html


def test_control_ui_loads_external_css_and_module_script():
    index_html = (Path(server_module.STATIC_ROOT) / "index.html").read_text(encoding="utf-8")

    assert '<link rel="stylesheet" href="/ui-assets/index.css?v=topic-graph-primary-focus-v1">' in index_html
    assert '<script type="module" src="/ui-assets/app.js?v=topic-graph-primary-focus-v1"></script>' in index_html
    assert "<style>" not in index_html
    assert "<script>\n" not in index_html


@pytest.mark.asyncio
async def test_ui_asset_route_serves_split_css_and_js():
    css_response = await server_module.bridge_ui_asset("index.css")
    js_response = await server_module.bridge_ui_asset("app.js")
    rules_response = await server_module.bridge_ui_asset("live_runtime_rules.md")

    assert Path(css_response.path).name == "index.css"
    assert Path(js_response.path).name == "app.js"
    assert Path(rules_response.path).name == "live_runtime_rules.md"


@pytest.mark.asyncio
async def test_ui_asset_route_rejects_traversal_and_non_assets():
    with pytest.raises(HTTPException) as traversal_exc:
        await server_module.bridge_ui_asset("../index.html")

    with pytest.raises(HTTPException) as html_exc:
        await server_module.bridge_ui_asset("index.html")

    assert traversal_exc.value.status_code == 404
    assert html_exc.value.status_code == 404


def test_control_ui_removes_manual_session_picker_and_delete_flow():
    index_html = _control_ui_source()

    assert 'async function loadSessions(preferredId = "", options = {})' in index_html
    assert "const selectDefault = options.selectDefault !== false" in index_html
    chat_preview_block = index_html[index_html.index("async function refreshChatPreview"):index_html.index("function scheduleChatPreviewRefresh")]
    assert 'id="deleteSessionConfirmText"' not in index_html
    assert 'id="confirmDeleteSession"' not in index_html
    assert 'id="sessionSelect"' not in index_html
    assert 'id="deleteSession"' not in index_html
    assert "requestDeleteSessionConfirmation" not in index_html
    assert "async function deleteSession" not in index_html
    assert "session_id_confirm_mismatch" not in index_html
    assert '$("deleteSession")' not in index_html
    assert "startCurrentSession" in index_html
    assert "/sessions/current/start" in index_html
    assert "defaultLiveSession()" not in chat_preview_block
    assert "fallback.session_id" not in chat_preview_block


def test_control_ui_exposes_fact_cards_folder_import_for_anime_topic_flow():
    index_html = _control_ui_source()

    assert 'id="importFactCardsFolder"' in index_html
    assert 'id="generateGeminiFactCards"' not in index_html
    assert 'id="topicAutoBuildControls"' not in index_html
    assert 'id="autoBuildTopicPack"' not in index_html
    assert 'id="autoBuildCount"' not in index_html
    assert 'id="autoBuildUseResearch"' not in index_html
    assert 'id="autoBuildTopic"' not in index_html
    assert 'id="updateTopicPack"' in index_html
    assert 'id="deleteTopicPack"' in index_html
    assert 'id="deleteAllTopicPacks"' in index_html
    assert 'id="updateTopicEntry"' in index_html
    assert 'id="cancelTopicEntryEdit"' in index_html
    assert 'data-delete-topic-entry=' in index_html
    assert 'id="topicEntrySelect"' in index_html
    assert 'class="topic-workspace"' in index_html
    assert 'class="topic-panel topic-pack-panel"' in index_html
    assert 'id="topicEntryPanel" class="topic-panel topic-entry-panel is-hidden"' in index_html
    assert 'class="topic-panel topic-ops-panel"' not in index_html
    assert 'id="topicPackUsageState"' not in index_html
    assert 'data-testid="director-idle-seconds"' in index_html
    assert "PUT" in index_html
    assert "DELETE" in index_html
    assert "/topic-packs/fact-cards/import-folder" in index_html
    assert "/topic-packs/fact-cards/generate" not in index_html
    assert "/topic-packs/${packId}" in index_html
    assert 'api("/topic-packs", { method: "DELETE" })' in index_html
    assert "/topic-packs/${packId}/entries/${entryId}" in index_html
    assert "/topic-packs/${packId}/search" not in index_html
    assert "/topic-packs/usage" not in index_html
    assert "/topic-packs/auto-build" not in index_html
    assert "管理備註" in index_html
    assert "生成主題（執行時使用，不會自動儲存）" not in index_html
    assert "自動建立張數" not in index_html
    assert "依主題自動建立資料卡" not in index_html
    assert "依主題生成 Fact Cards" not in index_html
    assert "補卡與狀態" not in index_html
    assert "匯入 FactCards 資料夾" in index_html
    assert 'id="factCardImportOverlay"' in index_html
    assert 'id="factCardImportMessage"' in index_html
    assert 'role="progressbar"' in index_html
    assert "初始化預設 Fact Cards" not in index_html
    assert "自動資料卡主題" not in index_html
    assert 'id="researchQuery"' not in index_html
    assert 'id="runResearch"' not in index_html
    assert "Research Gate 查詢" not in index_html
    topic_pack_delete_block = index_html[
        index_html.index("async function deleteTopicPack"):
        index_html.index("async function linkTopicPack")
    ]
    topic_entry_delete_block = index_html[
        index_html.index("async function deleteTopicEntry"):
        index_html.index("async function importFactCardsFolder")
    ]
    assert "confirm(" not in topic_pack_delete_block
    assert "window.confirm" not in topic_pack_delete_block
    delete_all_block = index_html[
        index_html.index("async function deleteAllTopicPacks"):
        index_html.index("async function linkTopicPack")
    ]
    assert "confirm(" not in delete_all_block
    assert "window.confirm" not in delete_all_block
    assert "prompt(" not in delete_all_block
    assert "confirm(" not in topic_entry_delete_block
    assert "window.confirm" not in topic_entry_delete_block
    assert "已召回" not in index_html
    assert "未召回" not in index_html
    assert "最近補卡" not in index_html
    assert "四月新番最新話細節、作畫與劇情討論" not in index_html
    assert "LLM 基礎、美食直播話題" not in index_html


def test_control_ui_exposes_runtime_rules_reference_tab():
    index_html = _control_ui_source()
    rules_doc = Path(server_module.UI_ASSETS_ROOT) / "live_runtime_rules.md"

    assert rules_doc.exists()
    rules_text = rules_doc.read_text(encoding="utf-8")
    assert "# YouTubeBridge 直播底層規則" in rules_text
    assert "SC 打斷冷卻秒數" in rules_text
    assert "注入間隔秒數" in rules_text
    assert "導播回合上限" in rules_text
    assert "幾批留言後回主軸" in rules_text
    assert "Research Gate" in rules_text

    assert '<button class="tab" data-pane="runtimeRulesPane">規則說明</button>' in index_html
    assert 'id="runtimeRulesPane"' in index_html
    assert 'id="runtimeRulesContent"' in index_html
    assert 'id="reloadRuntimeRules"' in index_html
    assert 'fetch("/ui-assets/live_runtime_rules.md"' in index_html
    assert "renderRuntimeRulesMarkdown" in index_html
    assert "loadRuntimeRules" in index_html


@pytest.mark.asyncio
async def test_memoria_refs_exposes_backend_character_limit(monkeypatch):
    class FakeClient:
        def list_characters(self):
            return [{"character_id": f"char-{idx}", "name": f"角色 {idx}"} for idx in range(8)]

        def get_system_config(self):
            return {"max_session_characters": 6}

    monkeypatch.setattr(server_module._memoria_routes, "MemoriaClient", lambda *args, **kwargs: FakeClient())

    result = await server_module.memoria_refs()

    assert result["max_session_characters"] == 6
    assert len(result["characters"]) == 8


def test_control_ui_limits_character_selection_and_blocks_start_without_character():
    index_html = _control_ui_source()

    assert "maxSessionCharacters: 6" in index_html
    assert 'id="characterLimitState"' in index_html
    assert 'aria-describedby="characterLimitState"' in index_html
    assert "function maxSessionCharacters" in index_html
    assert "function syncCharacterSelectionLimit" in index_html
    assert "function validateSelectedCharacters" in index_html
    assert "state.maxSessionCharacters = Number(data.max_session_characters || 6);" in index_html
    assert 'state.characters = data.characters || [];' in index_html
    assert 'await api("/memoria/refs")' in index_html
    assert '$("characterSelect").addEventListener("change", () => {' in index_html
    assert 'syncCharacterSelectionLimit();' in index_html
    assert 'if (!closing && isStartAction) {' in index_html
    assert '$("toggleSession").disabled = !characterValidation.ok;' in index_html
    assert "請先選擇至少 1 位角色" in index_html
    assert "最多只能選擇" in index_html


def test_events_pane_is_grouped_as_test_comment_tool():
    index_html = _control_ui_source()

    assert '<button class="tab active" data-pane="liveSessionPane">Live Session</button>' in index_html
    assert '<button class="tab" data-pane="eventsPane">留言測試</button>' in index_html
    assert "Recent Events" not in index_html
    assert 'class="event-tool-group manual-events"' in index_html
    assert 'class="event-tool-group auto-events"' in index_html
    assert 'class="event-tool-group pending-events"' in index_html

    manual_block = index_html[
        index_html.index('<div class="event-tool-group manual-events">'):
        index_html.index('<div class="event-tool-group auto-events">')
    ]
    auto_block = index_html[
        index_html.index('<div class="event-tool-group auto-events">'):
        index_html.index('<div class="event-tool-group pending-events">')
    ]
    pending_block = index_html[
        index_html.index('<div class="event-tool-group pending-events">'):
        index_html.index('<div id="summaryPane"')
    ]

    assert "手動生成" in manual_block
    assert 'id="generateTestEvents"' in manual_block
    assert 'id="injectPending"' not in manual_block
    assert "自動測試" in auto_block
    assert 'id="toggleAutoTestEvents"' in auto_block
    assert 'id="injectSelected"' not in auto_block
    assert "待處理留言" in pending_block
    assert 'id="eventsList"' in pending_block
    assert 'id="injectSelected"' in pending_block
    assert 'id="injectPending"' in pending_block
    assert 'id="injectContent"' in pending_block
    assert "<summary>進階注入提示</summary>" in pending_block


def test_control_ui_checkbox_inputs_keep_native_compact_size():
    index_html = _control_ui_source()

    assert 'href="/ui-assets/index.css?v=topic-graph-primary-focus-v1"' in index_html
    assert '\ninput[type="checkbox"] {' in index_html
    checkbox_block = index_html[
        index_html.index('\ninput[type="checkbox"] {'):
        index_html.index('\ntextarea {', index_html.index('\ninput[type="checkbox"] {'))
    ]
    assert "width: 16px;" in checkbox_block
    assert "height: 16px;" in checkbox_block
    assert "min-height: 16px;" in checkbox_block
    assert "padding: 0;" in checkbox_block
    assert "accent-color: var(--accent);" in checkbox_block


def test_topic_pack_buttons_are_contextual_in_control_ui():
    index_html = _control_ui_source()
    visibility_block = index_html[
        index_html.index("function updateTopicActionVisibility"):
        index_html.index("function factCardActionsBlockedDuringLive")
    ]

    assert ".is-hidden { display: none !important; }" in index_html
    assert "function updateTopicActionVisibility()" in index_html
    assert "const hasSession = !!selectedSessionId();" in visibility_block
    assert 'setTopicActionVisible("createTopicPack", !hasPack);' in index_html
    assert 'setTopicActionVisible("updateTopicPack", hasPack);' in index_html
    assert 'setTopicActionVisible("deleteTopicPack", hasPack);' in index_html
    assert 'setTopicActionVisible("deleteAllTopicPacks", state.topicPacks.length > 0);' in index_html
    assert 'setTopicActionVisible("linkTopicPack", hasPack && hasSession);' in index_html
    assert 'setTopicActionVisible("addTopicEntry", hasPack && !hasEntry);' in index_html
    assert 'setTopicActionVisible("updateTopicEntry", hasPack && hasEntry);' in index_html
    assert 'setTopicActionVisible("cancelTopicEntryEdit", hasPack && hasEntry);' in index_html
    assert 'setTopicActionVisible("deleteTopicEntry", hasPack && hasEntry);' not in index_html
    assert 'setTopicActionVisible("rebuildTopicEmbeddings", hasPack);' in index_html
    assert 'setTopicActionVisible("topicAutoBuildControls"' not in index_html
    assert 'setTopicActionVisible("autoBuildTopicPack"' not in index_html
    assert 'setTopicActionVisible("generateGeminiFactCards"' not in index_html
    assert 'setTopicActionVisible("generateGeminiFactCards", hasSession);' not in index_html
    assert 'setTopicActionVisible("importFactCardsFolder", hasPack && !liveLocked);' in index_html
    assert '$("importFactCardsFolder").disabled = !hasPack || liveLocked || importBusy;' in index_html
    assert '$("importFactCardsFolder").textContent = importBusy ? "匯入中..." : "匯入 FactCards 資料夾";' in index_html
    assert 'setTopicActionVisible("importFactCardsFolder", hasSession);' not in index_html
    assert 'setTopicActionVisible("runResearch", hasSession);' not in index_html
    assert 'setTopicActionVisible("searchTopicPack"' not in index_html
    assert 'setTopicActionVisible("restoreTopicEntries"' not in index_html
    assert "} else if (!previousPackId && state.topicPacks.length === 1) {" in index_html
    assert '$("topicPackSelect").value = String(state.topicPacks[0].id);' in index_html
    assert '$("topicEntryPanel").classList.toggle("is-hidden", !hasPack);' in index_html
    assert '$("topicPackTitle").addEventListener("input", updateTopicActionVisibility);' in index_html
    assert '$("topicEntryTitle").addEventListener("input", updateTopicActionVisibility);' in index_html
    assert '$("topicEntryBody").addEventListener("input", updateTopicActionVisibility);' in index_html
    assert '<button id="createTopicPack" class="primary" disabled>建立</button>' in index_html
    assert '<button id="updateTopicPack" class="is-hidden">儲存</button>' in index_html
    assert '<button id="linkTopicPack" class="blue is-hidden">綁定本場</button>' in index_html
    assert '<button id="deleteAllTopicPacks" class="danger is-hidden">清空所有資料包</button>' in index_html
    assert '<button id="addTopicEntry" class="primary is-hidden">新增</button>' in index_html
    assert '<button id="cancelTopicEntryEdit" class="is-hidden">取消</button>' in index_html
    assert 'id="deleteTopicEntry"' not in index_html
    assert '<button id="searchTopicPack"' not in index_html
    assert '<button id="restoreTopicEntries"' not in index_html
    assert '<button id="autoBuildTopicPack"' not in index_html
    assert '<button id="generateGeminiFactCards"' not in index_html
    assert '<button id="importFactCardsFolder" class="blue is-hidden">匯入 FactCards 資料夾</button>' in index_html
    init_start = index_html.index("installTestIds();")
    init_block = index_html[init_start:index_html.index("initBridgeKey()", init_start)]
    assert "updateTopicActionVisibility();" in init_block


def test_install_test_ids_preserves_explicit_stable_testids():
    index_html = _control_ui_source()

    assert 'data-testid="director-idle-seconds"' in index_html
    assert "if (element && !element.dataset.testid) element.dataset.testid = id;" in index_html
    assert "element.dataset.testid = id;" not in index_html.replace(
        "if (element && !element.dataset.testid) element.dataset.testid = id;",
        "",
    )


def test_topic_pack_vector_search_controls_are_not_exposed_in_control_ui():
    index_html = _control_ui_source()

    assert 'id="topicSearchQuery"' not in index_html
    assert 'id="searchTopicPack"' not in index_html
    assert 'id="restoreTopicEntries"' not in index_html
    assert "async function searchTopicPack" not in index_html
    assert "async function restoreTopicEntries" not in index_html
    assert "topicEntrySearchActive" not in index_html


def test_topic_pack_rebuild_embeddings_action_lives_with_pack_controls():
    index_html = _control_ui_source()
    pack_panel = index_html[
        index_html.index('<div class="topic-panel topic-pack-panel">'):
        index_html.index('<div id="topicEntryPanel"')
    ]
    entry_panel = index_html[
        index_html.index('<div id="topicEntryPanel"'):
        index_html.index('</div>\n        </div>\n\n        <div id="systemSettingsPane"')
    ]

    assert '<button id="rebuildTopicEmbeddings" class="is-hidden">重建向量</button>' in pack_panel
    assert 'id="rebuildTopicEmbeddings"' not in entry_panel
    assert '<button id="importFactCardsFolder" class="blue is-hidden">匯入 FactCards 資料夾</button>' in entry_panel
    assert entry_panel.index('id="importFactCardsFolder"') < entry_panel.index('<label>標題')
    assert 'class="topic-search-group"' not in entry_panel
    assert '<div class="topic-panel topic-ops-panel">' not in index_html


def test_control_ui_exposes_topic_graph_debug_panel():
    index_html = _control_ui_source()

    assert 'id="topicGraphPanel"' in index_html
    assert 'id="topicGraphState"' in index_html
    assert 'id="refreshTopicGraph"' in index_html
    assert 'id="rebuildTopicGraph"' in index_html
    assert 'id="refreshTopicGraphTrace"' in index_html
    assert 'id="resetTopicGraphView"' in index_html
    assert 'id="openTopicGraphModal"' in index_html
    assert 'id="topicGraphModal"' in index_html
    assert 'id="topicGraphModalSvg"' in index_html
    assert 'id="topicGraphModalDetails"' in index_html
    assert 'id="closeTopicGraphModal"' in index_html
    assert 'id="topicGraphSvg"' in index_html
    assert 'id="topicGraphSelectedNode"' in index_html
    assert 'id="topicGraphLatestTrace"' in index_html
    assert 'id="topicGraphTraces"' in index_html
    assert 'function refreshTopicGraph' in index_html
    assert 'function rebuildTopicGraph' in index_html
    assert 'function renderTopicGraph' in index_html
    assert 'function selectTopicGraphNode' in index_html
    assert "function clearTopicGraphSelection" in index_html
    assert "function toggleTopicGraphNodeSelection" in index_html
    assert "function renderTopicGraphSelectedNodeDetails" in index_html
    assert "function topicGraphLabelCandidateForce" in index_html
    assert "function topicGraphLabelCandidateVisible" in index_html
    assert "topicGraphBusy: false" in index_html
    assert "topicGraphViewport:" in index_html
    assert "topicGraphNodePositions:" in index_html
    assert "topicGraphModalOpen: false" in index_html
    assert "topicGraphTraceAutoFollow: true" in index_html
    assert "topicGraphTraceRefreshTimer: null" in index_html
    assert "let topicGraphDrag = null;" in index_html
    assert "function setTopicGraphBusy(action" in index_html
    assert "function topicGraphLayout(nodes, edges)" in index_html
    assert "function topicGraphPositions(nodes, edges)" in index_html
    assert "function topicGraphRelatedNodeIds(selectedNodeId, edges)" in index_html
    assert "function topicGraphPrimaryTraceNodeId(trace)" in index_html
    assert "function topicGraphNodeClass(node, selected, relatedNodeIds, focusNodeIds, traceNodeIds, primaryTraceNodeId)" in index_html
    assert "function topicGraphEdgeClass(edge, traceNodeIds, selected, relatedNodeIds, focusNodeIds)" in index_html
    assert "function topicGraphAutoFocusNodeIds(trace, edges)" in index_html
    assert "function shouldRenderTopicGraphLabel" in index_html
    assert "const denseGraph = nodes.length > 36;" in index_html
    assert "entity: 52" in index_html
    assert "const maxVisibleLabels = selected ? Math.max(18, relatedNodeIds.size) : (focusNodeIds.size ? Math.max(18, focusNodeIds.size) : (denseGraph ? 46 : 64));" in index_html
    assert "function clampTopicGraphScale" in index_html
    assert "function zoomTopicGraph" in index_html
    assert "function beginTopicGraphNodeDrag" in index_html
    assert "function resetTopicGraphView" in index_html
    assert "function openTopicGraphModal" in index_html
    assert "function closeTopicGraphModal" in index_html
    assert "function bindTopicGraphViewportControls" in index_html
    assert "function renderTopicGraphToSvg(svg)" in index_html
    assert "const focusNodeIds = selected ? relatedNodeIds : topicGraphAutoFocusNodeIds(state.topicGraphLatestTrace, edges);" in index_html
    assert "const primaryTraceNodeId = topicGraphPrimaryTraceNodeId(state.topicGraphLatestTrace);" in index_html
    assert 'const activeTrace = Number(node.id) === primaryTraceNodeId;' in index_html
    assert 'class="topic-graph-trace-pulse"' in index_html
    assert 'const graphBusy = !!state.topicGraphBusy;' in index_html
    assert '$("refreshTopicGraph").disabled = !hasPack || graphBusy;' in index_html
    assert '$("rebuildTopicGraph").disabled = !hasPack || graphBusy;' in index_html
    assert 'setTopicActionVisible("openTopicGraphModal", hasPack);' in index_html
    assert '$("openTopicGraphModal").disabled = !hasPack || graphBusy;' in index_html
    assert '$("refreshTopicGraph").textContent = action === "refresh" ? "刷新中..." : "刷新關係圖";' in index_html
    assert '$("rebuildTopicGraph").textContent = action === "rebuild" ? "重建中..." : "重建關係圖";' in index_html
    assert 'setTopicGraphBusy("refresh", "正在刷新關係圖...");' in index_html
    assert 'setTopicGraphBusy("rebuild", "正在重建關係圖...");' in index_html
    assert 'setTopicGraphBusy("trace", "正在刷新召回路徑...");' in index_html
    assert 'refreshTopicGraphTrace({ showBusy: false })' in index_html
    assert "function scheduleTopicGraphTraceRefresh" in index_html
    assert 'scheduleTopicGraphTraceRefresh({ reason: payload.type });' in index_html
    assert "function setTopicGraphLoadedState" in index_html
    assert "setTopicGraphLoadedState(state.topicGraph);" in index_html
    assert '$("topicGraphState").textContent = "關係圖刷新失敗";' in index_html
    assert '$("topicGraphState").textContent = "關係圖重建失敗";' in index_html
    assert "/topic-packs/${packId}/graph" in index_html
    assert "/topic-packs/${packId}/graph/rebuild" in index_html
    assert "/sessions/${encodeURIComponent(id)}/topic-graph/traces" in index_html
    assert "/sessions/${encodeURIComponent(id)}/topic-graph/latest-trace" in index_html
    assert '$("refreshTopicGraph").onclick = () => refreshTopicGraph()' in index_html
    assert '$("rebuildTopicGraph").onclick = () => rebuildTopicGraph()' in index_html
    assert '$("refreshTopicGraphTrace").onclick = () => refreshTopicGraphTrace()' in index_html
    assert '$("resetTopicGraphView").onclick = () => resetTopicGraphView();' in index_html
    assert '$("openTopicGraphModal").onclick = () => openTopicGraphModal();' in index_html
    assert '$("closeTopicGraphModal").onclick = () => closeTopicGraphModal();' in index_html
    assert 'svg.addEventListener("wheel", onWheel' in index_html
    assert 'svg.addEventListener("pointerdown", onPointerDown' in index_html
    assert 'svg.addEventListener("click", onSvgClick' in index_html
    assert "const TOPIC_GRAPH_NODE_CLICK_SLOP_PX = 5;" in index_html
    assert 'topicGraphDrag = {' in index_html
    assert "const clientDx = event.clientX - topicGraphDrag.clientX;" in index_html
    assert "if (!topicGraphDrag.moved && Math.hypot(clientDx, clientDy) < TOPIC_GRAPH_NODE_CLICK_SLOP_PX) return;" in index_html
    assert "topicGraphDrag.moved = true;" in index_html
    assert "const completedDrag = topicGraphDrag;" in index_html
    assert "toggleTopicGraphNodeSelection(completedDrag.nodeId);" in index_html
    assert "handled: !completedDrag.moved" in index_html
    assert "if (topicGraphLastNodeDrag?.handled || topicGraphLastNodeDrag?.moved)" in index_html
    assert 'state.topicGraphNodePositions[String(topicGraphDrag.nodeId)]' in index_html
    assert 'clearTopicGraphSelection();' in index_html
    assert "toggleTopicGraphNodeSelection(item.dataset.topicGraphNode)" in index_html
    assert 'renderTopicGraphSelectedNodeDetails(null, []);' in index_html
    assert 'renderTopicGraphSelectedNodeDetails(node, edges);' in index_html
    assert "目前召回焦點" in index_html
    assert "補充召回" in index_html
    assert "自動跟隨" in index_html
    assert 'if (selected && !relatedNodeIds.has(Number(candidate.node.id))) return;' in index_html
    assert 'force: topicGraphLabelCandidateForce(node, selected, relatedNodeIds, traceNodeIds, focusNodeIds)' in index_html
    assert 'if (!topicGraphLabelCandidateVisible(candidate, selected, visibleLabels.size, maxVisibleLabels)) return;' in index_html
    assert 'denseGraph && ["entity", "reference"].includes(candidate.node.node_type)' not in index_html
    assert '["topicGraphSelectedNode", "topicGraphModalDetails"]' in index_html
    assert 'class="topic-graph-viewport"' in index_html
    assert "pointer-events: auto;" in index_html
    assert ".topic-graph-node.is-active-trace circle" in index_html
    assert ".topic-graph-node.is-recalled-trace circle" in index_html
    assert ".topic-graph-trace-pulse" in index_html
    assert "@keyframes topicGraphTracePulse" in index_html
    assert ".topic-graph-node.is-dimmed" in index_html
    assert ".topic-graph-edge.is-dimmed" in index_html
    assert ".topic-graph-node.is-dimmed text" in index_html
    assert ".topic-graph-modal-body" in index_html
    assert ".topic-graph-modal-details" in index_html


def test_topic_pack_entry_list_drives_edit_and_delete_actions():
    index_html = _control_ui_source()

    assert "currentTopicEntryId: 0" in index_html
    assert "function currentTopicEntryId()" in index_html
    assert "function topicEntryById(entryId)" in index_html
    assert "function selectTopicEntryForEditing(entryId)" in index_html
    assert 'data-edit-topic-entry="${escapeHtml(entry.id)}"' in index_html
    assert 'data-delete-topic-entry="${escapeHtml(entry.id)}"' in index_html
    assert "function bindTopicEntryCardButtons()" in index_html
    assert "button.dataset.deleteTopicEntry" in index_html
    assert "deleteTopicEntry(entryId)" in index_html
    assert "const entryId = currentTopicEntryId();" in index_html
    assert "const entry = topicEntryById(entryId) || selectedTopicEntry();" in index_html
    assert "$(\"updateTopicEntry\").onclick = () => updateTopicEntry()" in index_html
    assert "$(\"deleteTopicEntry\").onclick" not in index_html


def test_topic_pack_entry_editor_can_cancel_editing():
    index_html = _control_ui_source()

    assert "function cancelTopicEntryEdit()" in index_html
    assert "fillTopicEntryForm(null);" in index_html[
        index_html.index("function cancelTopicEntryEdit"):
        index_html.index("function topicEntryPreviewText")
    ]
    assert '$("cancelTopicEntryEdit").onclick = () => cancelTopicEntryEdit();' in index_html


def test_topic_pack_entry_save_locks_editor_while_request_is_running():
    index_html = _control_ui_source()
    update_block = index_html[
        index_html.index("async function updateTopicEntry"):
        index_html.index("async function deleteTopicEntry")
    ]

    assert "topicEntryEditorBusy: false" in index_html
    assert "function setTopicEntryEditorBusy(isBusy)" in index_html
    assert '$("topicEntryTitle").disabled = busy;' in index_html
    assert '$("topicEntryBody").disabled = busy;' in index_html
    assert '$("updateTopicEntry").textContent = busy ? "儲存中..." : "儲存";' in index_html
    assert "setTopicEntryEditorBusy(true);" in update_block
    assert "finally {" in update_block
    assert "setTopicEntryEditorBusy(false);" in update_block


def test_fact_card_gemini_generation_ui_is_not_exposed():
    index_html = _control_ui_source()

    assert 'id="factCardGenerationOverlay"' not in index_html
    assert 'id="factCardGenerationMessage"' not in index_html
    assert "factCardGenerationBusy" not in index_html
    assert "function setFactCardGenerationBusy" not in index_html
    assert "async function generateGeminiFactCards" not in index_html
    assert 'log("Gemini FactCards 開始產生"' not in index_html
    assert 'id="autoBuildTopic"' not in index_html


def test_fact_cards_folder_import_shows_blocking_progress_feedback():
    index_html = _control_ui_source()
    import_block = index_html[
        index_html.index("async function importFactCardsFolder"):
        index_html.index("async function rebuildTopicEmbeddings")
    ]

    assert 'id="factCardImportOverlay"' in index_html
    assert 'id="factCardImportMessage"' in index_html
    assert 'aria-labelledby="factCardImportTitle"' in index_html
    assert 'role="progressbar"' in index_html
    assert "factCardImportBusy: false" in index_html
    assert "function setFactCardImportBusy(isBusy" in index_html
    assert '$("factCardImportOverlay").classList.toggle("is-hidden", !busy);' in index_html
    assert "setFactCardImportBusy(true);" in import_block
    assert "匯入完成，但關係圖建立失敗" in import_block
    assert "請查看 Log 或點重建關係圖" in import_block
    assert "finally {" in import_block
    assert "setFactCardImportBusy(false);" in import_block


def test_topic_pack_entry_save_clears_editor_after_success():
    index_html = _control_ui_source()
    update_block = index_html[
        index_html.index("async function updateTopicEntry"):
        index_html.index("async function deleteTopicEntry")
    ]

    assert 'log("fact card 已更新，已清空編輯區", data);' in update_block
    assert "await refreshTopicEntries();" in update_block
    assert "fillTopicEntryForm(null);" in update_block
    assert "selectTopicEntryForEditing(entryId);" not in update_block
    assert update_block.index("await refreshTopicEntries();") < update_block.index("fillTopicEntryForm(null);")


def test_topic_pack_entry_editor_hides_system_metadata_fields():
    index_html = _control_ui_source()

    assert 'id="topicEntrySelectorRow" class="is-hidden"' in index_html
    assert 'id="topicEntryMetadataFields" class="is-hidden" aria-hidden="true"' in index_html
    assert 'id="topicEntrySourceType" type="hidden"' in index_html
    assert 'id="topicEntryTags" type="hidden"' in index_html
    assert 'id="topicEntrySourceUrl" type="hidden"' in index_html
    assert "<label>類型" not in index_html
    assert "<label>標籤" not in index_html
    assert "<label>來源" not in index_html
    assert "function topicEntryPreviewText(entry)" in index_html
    assert "${escapeHtml(entry.body)}</p>" not in index_html
    assert "topicEntryPreviewText(entry)" in index_html


def test_director_controls_are_integrated_into_live_session_panel():
    index_html = _control_ui_source()
    live_session_block = index_html[
        index_html.index('<div id="liveSessionPane"'):
        index_html.index('<div id="eventsPane"')
    ]
    tabs_block = index_html[
        index_html.index('<div class="tabs">'):
        index_html.index('<div id="liveSessionPane"')
    ]
    director_block = index_html[
        index_html.index('<div id="directorControls"'):
        index_html.index('<div id="sessionActions"')
    ]

    assert 'data-pane="directorPane"' not in tabs_block
    assert 'id="directorPane"' not in index_html
    assert 'id="directorControls"' in live_session_block
    assert "導播設定" in live_session_block
    assert "角色停頓後續話秒數" in live_session_block
    assert 'id="directorIdle"' in live_session_block
    assert 'data-testid="director-idle-seconds"' in live_session_block
    assert "單一話題持續回合數" in live_session_block
    assert 'id="directorAnchorEveryTurns"' in director_block
    assert 'id="directorGroupTurnLimit"' in director_block
    assert live_session_block.index('id="directorGroupTurnLimit"') > live_session_block.index('id="directorControls"')
    assert 'id="directorGuidance"' in live_session_block
    assert 'id="updateDirectorGuidance"' in live_session_block
    assert "直播開始後會自動啟動導播與開場" in live_session_block
    assert 'id="autoDirector"' not in live_session_block
    assert 'id="toggleDirector"' not in live_session_block
    assert 'id="directorState"' in live_session_block
    assert '<details class="director-debug">' in live_session_block
    assert "<summary>導播除錯資訊</summary>" in live_session_block
    assert 'id="directorJson"' in live_session_block
    assert "toggleDirector" not in index_html
    assert '$("autoDirector")' not in index_html
    assert "await setDirector(true, true);" in index_html


def test_live_session_places_director_below_selected_roles_and_runtime_settings_on_right():
    index_html = _control_ui_source()
    live_session_block = index_html[
        index_html.index('<div id="liveSessionPane"'):
        index_html.index('<div id="eventsPane"')
    ]
    left_panel = live_session_block[
        live_session_block.index('class="live-session-panel live-session-main-panel"'):
        live_session_block.index('class="live-session-panel live-session-settings-panel"')
    ]
    right_panel = live_session_block[
        live_session_block.index('class="live-session-panel live-session-settings-panel"'):
        live_session_block.index('</div>\n        </div>', live_session_block.index('class="live-session-panel live-session-settings-panel"'))
    ]
    director_block = left_panel[
        left_panel.index('id="directorControls"'):
        left_panel.index('</div>\n            </div>', left_panel.index('id="directorControls"'))
    ]

    assert 'class="live-session-workspace"' in live_session_block
    assert 'id="videoId"' in left_panel
    assert 'id="characterSelect"' in left_panel
    assert 'id="directorControls"' in left_panel
    assert left_panel.index('id="characterLimitState"') < left_panel.index('id="directorControls"')
    assert 'id="toggleSession"' in director_block
    assert 'id="injectInterval"' not in left_panel
    assert 'id="sessionTopicPackSelect"' not in left_panel
    assert 'id="directorControls"' not in right_panel
    assert 'id="injectInterval"' in right_panel
    assert 'id="sessionTopicPackSelect"' in right_panel
    assert 'id="autoInject"' in right_panel
    assert 'id="sessionActions"' in right_panel
    assert 'id="toggleSession"' not in right_panel
    assert 'id="updateSession"' in right_panel
    assert ".live-session-workspace" in index_html
    assert ".live-session-panel + .live-session-panel" in index_html


def test_connector_and_memoria_settings_are_in_system_settings_tab():
    index_html = _control_ui_source()
    live_session_block = index_html[
        index_html.index('<div id="liveSessionPane"'):
        index_html.index('<div id="eventsPane"')
    ]
    tabs_block = index_html[
        index_html.index('<div class="tabs">'):
        index_html.index('<div id="liveSessionPane"')
    ]
    system_settings_block = index_html[
        index_html.index('<div id="systemSettingsPane"'):
        index_html.index('\n\n      </section>', index_html.index('<div id="systemSettingsPane"'))
    ]

    assert '<button class="tab" data-pane="systemSettingsPane">系統設定</button>' in tabs_block
    assert 'id="systemSettingsPane"' in index_html
    assert "<h2>Connector</h2>" not in live_session_block
    assert "<h2>MemoriaCore Auth</h2>" not in live_session_block
    assert "<h2>Connector</h2>" in system_settings_block
    assert "<h2>MemoriaCore Auth</h2>" in system_settings_block
    assert 'id="saveConnector"' in system_settings_block
    assert 'id="testMemoriaAuth"' in system_settings_block


def test_live_session_automation_options_have_clear_labels_and_tooltips():
    index_html = _control_ui_source()
    live_session_block = index_html[
        index_html.index('<div id="liveSessionPane"'):
        index_html.index('<div id="eventsPane"')
    ]

    expected_options = {
        "autoInject": ("自動注入待處理留言", "每隔一段時間把 pending 留言送進角色回應流程"),
        "autoFinalize": ("到達預計直播時間後自動收尾", "預計直播分鐘到達後執行結束與摘要收尾流程"),
        "autoScThanksOnFinalize": ("收尾時逐一感謝未處理 SC", "結束前以片尾名單方式逐一點名感謝尚未處理的 Super Chat"),
        "autoDeleteProcessed": ("摘要與記憶完成後清除 runtime session", "摘要和 shared memory 完成後刪除 Bridge 暫存 runtime session"),
        "researchEnabled": ("觀眾提問啟用安全搜尋補充", "觀眾提出資料型問題且資料包不足時，經安全判定後補充搜尋上下文"),
    }

    assert "<h3>直播自動化選項</h3>" in live_session_block
    assert 'id="dynamicInject"' not in live_session_block
    assert 'id="injectMinIntervalSeconds"' in live_session_block
    assert 'id="injectMinIntervalPercent"' not in live_session_block
    assert "動態注入最短秒數" in live_session_block
    assert "pending 接近上限時允許縮短到的最快注入間隔" not in live_session_block
    assert "最低間隔比例" not in live_session_block
    for field_id, (label, tooltip) in expected_options.items():
        assert f'id="{field_id}"' in live_session_block
        assert label in live_session_block
        assert f'data-tooltip="{tooltip}"' in live_session_block
        assert f'aria-label="{tooltip}"' in live_session_block

    assert 'class="help-tip"' in live_session_block
    assert ".help-tip::after" in index_html
    assert "left: calc(100% + 8px);" in index_html
    assert ".help-tip.tooltip-left::after" in index_html
    assert "positionHelpTooltip" in index_html
    assert ".help-tip:hover::after" in index_html
    assert ".help-tip:focus::after" in index_html
    assert 'style="width:auto;min-height:auto"' not in live_session_block


def test_live_session_core_fields_have_detailed_tooltips():
    index_html = _control_ui_source()
    live_session_block = index_html[
        index_html.index('<div id="liveSessionPane"'):
        index_html.index('<div id="sessionActions"')
    ]
    expected_fields = {
        "videoId": "留空時使用測試直播；填入 YouTube video_id 或 URL 時會連到真實直播聊天室，並停用測試留言功能。",
        "characterSelect": "選擇本場會收到直播上下文並參與回應的角色；可多選，至少選一位才有 AI 回應。",
        "injectInterval": "自動注入的正常等待秒數；pending 留言少或正在有角色回應時，會以這個值作為主要節奏。",
        "injectMinIntervalSeconds": "pending 留言接近強制注入上限時，動態注入最多只會縮短到這個秒數。",
        "minPending": "pending 留言達到這個數量後，自動注入才會把留言送進角色回應流程；Super Chat 可優先觸發。",
        "maxPending": "單次自動注入最多帶入的 pending 留言數；正在回應中但 backlog 達到此值時，會允許強制排入下一輪。",
        "plannedDuration": "大於 0 時代表預計直播長度；啟用自動收尾後，到時間會執行 SC 感謝、摘要與記憶寫入。",
        "scInterruptCooldown": "Super Chat 打斷正在進行的回應後，下一次允許再次打斷前必須等待的秒數。",
        "maxScPerBatch": "每次注入最多帶入幾則 Super Chat；系統會優先選較高 tier，再依留言順序處理。",
        "sessionTopicPackSelect": "本場直播啟動或更新時要綁定的 Topic Pack；直播中只讀取已綁定資料，不執行 Fact Card 生成或匯入。",
        "directorGroupTurnLimit": "導播每次推話題時允許角色連續互相接話的回合上限，避免一次導播指令延伸過久。",
        "directorMaxChatBatches": "連續處理幾批聊天室留言後，導播會強制把話題拉回本場主軸，避免直播被留言帶偏。",
        "directorIdle": "角色與互動停止超過這個秒數後，導播會嘗試推進下一段話題或讓角色續話。",
        "directorAnchorEveryTurns": "同一個導播話題最多連續推進幾輪 AI 對話；達到後會釋放回合限制，讓下一次導播決策可以切換或重新錨定話題。",
        "directorGuidance": "本場直播的高層方向，只提供給導播與角色作為內部參考，不會直接顯示在 live chat。",
    }

    for field_id, tooltip in expected_fields.items():
        assert f'id="{field_id}"' in live_session_block
        assert f'data-tooltip="{tooltip}"' in live_session_block
        assert f'aria-label="{tooltip}"' in live_session_block

    for label_text, field_id in [
        ("YouTube video_id 或 URL", "videoId"),
        ("角色", "characterSelect"),
        ("注入間隔秒數", "injectInterval"),
        ("動態注入最短秒數", "injectMinIntervalSeconds"),
        ("話題資料包", "sessionTopicPackSelect"),
        ("單一話題持續回合數", "directorAnchorEveryTurns"),
        ("導播回合上限", "directorGroupTurnLimit"),
        ("幾批留言後回主軸", "directorMaxChatBatches"),
        ("角色停頓後續話秒數", "directorIdle"),
        ("本場直播方向", "directorGuidance"),
    ]:
        pattern = (
            rf'<label[^>]*>\s*<span class="field-label">{re.escape(label_text)}\s*'
            rf'<span class="help-tip"[^>]*>\?</span>\s*</span>\s*'
            rf'<(?:input|select|textarea)[^>]*id="{field_id}"'
        )
        assert re.search(pattern, live_session_block, flags=re.DOTALL), field_id


def test_control_ui_uses_single_live_session_flow():
    index_html = _control_ui_source()
    live_session_block = index_html[
        index_html.index('<div id="liveSessionPane"'):
        index_html.index('<div id="eventsPane"')
    ]
    summary_pane = index_html[
        index_html.index('id="summaryPane"'):
        index_html.index('id="topicPackPane"')
    ]

    assert 'id="sessionName"' not in live_session_block
    assert 'id="newSession"' not in live_session_block
    assert 'class="advanced-session-picker"' not in live_session_block
    assert 'id="sessionSelect"' not in live_session_block
    assert 'id="deleteSession"' not in live_session_block
    assert 'id="finalizeSession"' not in live_session_block
    assert 'id="writeMemory"' not in summary_pane
    assert "startCurrentSession" in index_html
    assert "/sessions/current/start" in index_html
    assert "結束直播並收尾" in index_html
    assert "開始全新直播" in index_html
    assert "暫停" not in index_html
    assert '$("newSession")' not in index_html
    assert '$("sessionSelect")' not in index_html
    assert '$("deleteSession")' not in index_html
    assert '$("writeMemory")' not in index_html
    assert 'sessionAction("stop")' not in index_html
    assert '<button class="tab active" data-pane="liveSessionPane">Live Session</button>' in index_html
    assert 'id="liveSessionPane" class="pane active"' in index_html


def test_control_ui_auto_creates_memoria_session_without_manual_picker():
    index_html = _control_ui_source()
    live_session_block = index_html[
        index_html.index('<div id="liveSessionPane"'):
        index_html.index('<div id="eventsPane"')
    ]

    assert "MemoriaCore session" not in live_session_block
    assert 'id="memoriaSession"' not in live_session_block
    assert '$("memoriaSession")' not in index_html
    assert "/memoria/sessions?limit=200" not in index_html
    assert "memoriaSessions" not in index_html
    assert 'target_memoria_session_id: ""' in index_html


def test_control_ui_uses_single_primary_start_or_finalize_action():
    index_html = _control_ui_source()

    primary_action = index_html[
        index_html.index('id="primarySessionAction"'):
        index_html.index('id="sessionActions"')
    ]
    update_actions = index_html[
        index_html.index('id="sessionActions"'):
        index_html.index("</section>", index_html.index('id="sessionActions"'))
    ]
    summary_pane = index_html[
        index_html.index('id="summaryPane"'):
        index_html.index('id="topicPackPane"')
    ]

    assert 'id="toggleSession"' in primary_action
    assert 'id="updateSession"' not in primary_action
    assert 'id="toggleSession"' not in update_actions
    assert 'id="updateSession"' in update_actions
    assert 'id="finalizeSession"' not in primary_action
    assert "結束直播並收尾" in index_html
    assert 'id="finalizeSession"' not in summary_pane
    assert "標記結束" not in index_html
    assert "收尾中" in index_html
    assert "開始全新直播" in index_html
    assert "startCurrentSession" in index_html
    assert "finalizeCurrentSession" in index_html
    assert 'log("直播操作失敗", String(error))' in index_html
    assert 'log("直播收尾失敗", String(error))' not in index_html


def test_live_session_can_bind_topic_pack_from_session_tab():
    index_html = _control_ui_source()
    live_session_block = index_html[
        index_html.index('<div id="liveSessionPane"'):
        index_html.index('<div id="eventsPane"')
    ]

    assert 'id="sessionTopicPackSelect"' in live_session_block
    assert "話題資料包" in live_session_block
    assert "bindSessionTopicPack" in index_html
    assert "await bindSessionTopicPack(data.session_id);" in index_html
    assert "/topic-packs/${packId}?replace=true" in index_html


def test_session_topic_pack_selector_clears_when_session_has_no_pack():
    index_html = _control_ui_source()
    selection_block = index_html[
        index_html.index("async function refreshSessionTopicPackSelection"):
        index_html.index("async function refreshTopicEntries")
    ]

    assert "const hasLinkedPack = packId && state.topicPacks.some" in selection_block
    assert 'selector.value = hasLinkedPack ? String(packId) : "";' in selection_block


def test_live_session_can_unbind_topic_pack_from_session_tab():
    index_html = _control_ui_source()
    bind_block = index_html[
        index_html.index("async function bindSessionTopicPack"):
        index_html.index("async function addTopicEntry")
    ]
    routes_source = (BRIDGE_ROOT / "server_routes" / "topic_packs.py").read_text(encoding="utf-8")

    assert 'api(`/sessions/${encodeURIComponent(sessionId)}/topic-packs`, {' in bind_block
    assert 'method: "DELETE"' in bind_block
    assert 'log("直播已解除話題資料包綁定", data);' in bind_block
    assert '@router.delete("/sessions/{session_id}/topic-packs")' in routes_source


def test_fact_cards_folder_import_is_blocked_during_live_runtime():
    index_html = _control_ui_source()

    assert "function factCardActionsBlockedDuringLive" in index_html
    assert "直播中不產生或匯入 Fact Cards" in index_html
    assert 'id="topicFactCardLiveLockNotice"' in index_html
    assert 'setTopicActionVisible("generateGeminiFactCards"' not in index_html
    assert 'setTopicActionVisible("importFactCardsFolder", hasPack && !liveLocked);' in index_html
    assert 'setTopicActionVisible("autoBuildTopicPack"' not in index_html


def test_control_ui_restores_primary_action_after_start_or_finalize_failure():
    index_html = _control_ui_source()
    start_block = index_html[
        index_html.index("async function startCurrentSession"):
        index_html.index("async function finalizeCurrentSession")
    ]
    finalize_block = index_html[
        index_html.index("async function finalizeCurrentSession"):
        index_html.index("async function toggleSession")
    ]

    assert "try {" in start_block
    assert "finally {" in start_block
    assert "updateLiveSessionControls();" in start_block[start_block.index("finally {"):]
    assert "try {" in finalize_block
    assert "finally {" in finalize_block
    assert "updateLiveSessionControls();" in finalize_block[finalize_block.index("finally {"):]


def test_control_ui_disables_test_event_controls_for_real_youtube_sessions():
    index_html = _control_ui_source()

    assert 'id="testEventsModeNotice"' in index_html
    assert "真實 YouTube 直播會停用測試留言" in index_html
    assert "function isRealYoutubeLiveSession" in index_html
    assert "function updateTestEventControls" in index_html
    assert 'testEventControlsDisabled' in index_html
    assert 'manualGroup.classList.toggle("is-disabled", blocked);' in index_html
    assert 'autoGroup.classList.toggle("is-disabled", blocked);' in index_html
    assert '$("generateTestEvents").disabled = blocked || !hasSession;' in index_html
    assert '$("toggleAutoTestEvents").disabled = blocked || !hasSession;' in index_html
    assert '$("autoTestEvents").checked = false;' in index_html
    assert '$("autoTestEvents").disabled = blocked;' in index_html
    assert '$("videoId").addEventListener("input", updateLiveSessionControls);' in index_html
    assert "真實 YouTube 直播不允許插入測試留言" in index_html


def test_single_connector_ui_only_collects_api_key_and_auto_enables_connector():
    index_html = _control_ui_source()
    connector_block = index_html[
        index_html.index("<h2>Connector</h2>"):
        index_html.index("<h2>MemoriaCore Auth</h2>")
    ]
    save_block = index_html[
        index_html.index("async function saveConnector"):
        index_html.index("async function saveSession")
    ]

    assert 'id="apiKey"' in connector_block
    assert "只要 API key 正確，connector 會自動啟用" in connector_block
    assert 'id="connectorName"' not in connector_block
    assert 'id="connectorEnabled"' not in connector_block
    assert '$("connectorName")' not in save_block
    assert '$("connectorEnabled")' not in save_block
    assert 'display_name: "YouTube Main"' in save_block
    assert "enabled: true" in save_block


def test_memoria_auth_refresh_button_names_updated_resources():
    index_html = _control_ui_source()
    memoria_block = index_html[
        index_html.index("<h2>MemoriaCore Auth</h2>"):
        index_html.index('<div class="stack right-rail">', index_html.index("<h2>MemoriaCore Auth</h2>"))
    ]

    assert 'id="testMemoriaAuth"' in memoria_block
    assert "測試連線並更新角色與 Session 清單" in memoria_block
    assert "測試連線並更新下拉" not in memoria_block


def test_single_connector_storage_auto_enables_legacy_disabled_connector(tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "youtube-main",
        "display_name": "YouTube Main",
        "api_key": "key",
        "enabled": False,
    })

    connector = storage.ensure_single_connector()

    assert connector["connector_id"] == "youtube-main"
    assert connector["api_key"] == "key"
    assert connector["enabled"] is True


@pytest.mark.asyncio
async def test_delete_session_endpoint_returns_deleted_session_id(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "yt-main",
        "display_name": "YouTube Main",
        "api_key": "key",
        "enabled": True,
    })
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "yt-main",
    })
    monkeypatch.setattr(server_module, "storage", storage)

    stopped: list[str] = []

    class FakeManager:
        async def stop_session(self, session_id: str):
            stopped.append(session_id)

    monkeypatch.setattr(server_module, "manager", FakeManager())

    result = await server_module.delete_session("live-a")

    assert result == {"deleted": True, "session_id": "live-a"}
    assert stopped == ["live-a"]
    assert storage.get_session("live-a") is None


@pytest.mark.asyncio
async def test_finalize_session_endpoint_uses_full_finalize_manager_path(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "yt-main",
        "display_name": "YouTube Main",
        "api_key": "key",
        "enabled": True,
    })
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "yt-main",
        "status": "running",
        "auto_sc_thanks_on_finalize": False,
    })
    monkeypatch.setattr(server_module, "storage", storage)

    finalized: list[str] = []

    class FakeManager:
        async def finalize_session(self, session_id: str):
            finalized.append(session_id)
            storage.update_session_fields(session_id, status="ended")
            storage.update_session_summary_state(session_id, summary_status="pending", finalized_at="2026-05-06T10:00:00")
            return {"status": "ended", "running": False}

        def get_status(self, session_id: str):
            return {"session_id": session_id, "status": "ended", "running": False}

    monkeypatch.setattr(server_module, "manager", FakeManager())

    class FakeSummaryManager:
        def summarize_session(self, *args, **kwargs):
            return {"status": "skipped", "reason": "no_events"}

    monkeypatch.setattr(server_module, "summary_manager", FakeSummaryManager())

    result = await server_module.finalize_session("live-a")

    assert finalized == ["live-a"]
    assert result["status"] == "ended"
    assert result["runtime_status"]["status"] == "ended"


@pytest.mark.asyncio
async def test_start_current_session_archives_existing_session_and_writes_memory(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "youtube-main",
        "display_name": "YouTube Main",
        "api_key": "key",
        "enabled": True,
    })
    old = storage.upsert_session({
        "session_id": "old-live",
        "connector_id": "youtube-main",
        "status": "running",
        "started_at": "2026-05-06T10:00:00",
        "character_ids": ["coco", "byakuren"],
        "auto_delete_after_processed": False,
    })
    storage.save_event({
        "bridge_session_id": old["session_id"],
        "external_message_id": "msg-1",
        "author_channel_id": "viewer",
        "author_display_name": "觀眾",
        "message_text": "今天新番作畫很有話題。",
        "published_at": "2026-05-06T10:01:00",
    })
    monkeypatch.setattr(server_module, "storage", storage)

    finalized: list[str] = []
    started: list[str] = []

    class FakeManager:
        async def finalize_session(self, session_id: str):
            finalized.append(session_id)
            storage.update_session_fields(session_id, status="ended")
            storage.update_session_summary_state(
                session_id,
                summary_status="pending",
                finalized_at="2026-05-06T10:10:00",
            )
            return {"session_id": session_id, "status": "ended"}

        async def start_session(self, session_id: str):
            started.append(session_id)
            storage.update_session_fields(session_id, status="running", started_at="2026-05-06T10:20:00")
            return {"session_id": session_id, "status": "running", "running": True}

        def get_status(self, session_id: str):
            session = storage.get_session(session_id)
            return {
                "session_id": session_id,
                "status": session.get("status") if session else "missing",
                "running": bool(session and session.get("status") == "running"),
            }

        async def stop_session(self, session_id: str):
            storage.update_session_fields(session_id, status="stopped")
            return self.get_status(session_id)

    class FakeSummaryManager:
        def __init__(self):
            self.calls: list[str] = []

        def summarize_session(self, session_id: str, **_kwargs):
            self.calls.append(session_id)
            summary = storage.create_summary(session_id, {
                "title": "直播摘要",
                "summary_text": "討論新番作畫。",
                "memory_text": "本場直播討論新番作畫。",
                "character_ids": ["coco", "byakuren"],
                "event_count": 1,
                "status": "completed",
                "metadata": {"memory_write_status": "not_started"},
            })
            storage.update_session_summary_state(
                session_id,
                summary_status="completed",
                summary_id=summary["id"],
                finalized_at="2026-05-06T10:10:00",
            )
            return {"status": "completed", "summary": summary}

    memory_writes: list[dict] = []

    class FakeMemoriaClient:
        def write_shared_youtube_memory(self, **kwargs):
            memory_writes.append(kwargs)
            return {"block_id": "shared-memory-1"}

    fake_summary = FakeSummaryManager()
    monkeypatch.setattr(server_module, "manager", FakeManager())
    monkeypatch.setattr(server_module, "summary_manager", fake_summary)
    monkeypatch.setattr(server_module._sessions_routes, "MemoriaClient", FakeMemoriaClient)

    result = await server_module.start_current_session(server_module.LiveSessionConfig(
        video_id="",
        character_ids=["coco"],
        auto_inject=True,
    ))

    assert finalized == ["old-live"]
    assert fake_summary.calls == ["old-live"]
    assert memory_writes and memory_writes[0]["session_id"] == "old-live"
    assert memory_writes[0]["character_ids"] == ["coco", "byakuren"]
    assert storage.get_session("old-live") is None
    assert started == [result["session_id"]]
    assert storage.get_session(result["session_id"])["status"] == "running"
    assert result["archived_sessions"][0]["session_id"] == "old-live"
    assert result["archived_sessions"][0]["memory_write"]["status"] == "completed"


@pytest.mark.asyncio
async def test_start_current_session_validates_new_live_before_archiving_existing(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "youtube-main",
        "display_name": "YouTube Main",
        "api_key": "",
        "enabled": True,
    })
    storage.upsert_session({
        "session_id": "old-live",
        "connector_id": "youtube-main",
        "status": "running",
        "started_at": "2026-05-06T10:00:00",
        "character_ids": ["coco"],
    })
    storage.save_event({
        "bridge_session_id": "old-live",
        "external_message_id": "msg-1",
        "author_channel_id": "viewer",
        "author_display_name": "觀眾",
        "message_text": "舊直播仍在進行。",
        "published_at": "2026-05-06T10:01:00",
    })
    monkeypatch.setattr(server_module, "storage", storage)

    finalized: list[str] = []
    started: list[str] = []

    class FakeManager:
        async def finalize_session(self, session_id: str):
            finalized.append(session_id)
            storage.update_session_fields(session_id, status="ended")
            return {"session_id": session_id, "status": "ended"}

        async def start_session(self, session_id: str):
            started.append(session_id)
            raise ValueError("connector 缺少 YouTube API key")

        def get_status(self, session_id: str):
            session = storage.get_session(session_id)
            return {
                "session_id": session_id,
                "status": session.get("status") if session else "missing",
                "running": bool(session and session.get("status") == "running"),
            }

        async def stop_session(self, session_id: str):
            storage.update_session_fields(session_id, status="stopped")
            return self.get_status(session_id)

    class FakeSummaryManager:
        def summarize_session(self, *_args, **_kwargs):
            return {"status": "skipped", "reason": "not_expected"}

    monkeypatch.setattr(server_module, "manager", FakeManager())
    monkeypatch.setattr(server_module, "summary_manager", FakeSummaryManager())

    with pytest.raises(HTTPException) as exc:
        await server_module.start_current_session(server_module.LiveSessionConfig(
            video_id="real-video",
            character_ids=["coco"],
        ))

    assert exc.value.status_code == 400
    assert "API key" in str(exc.value.detail)
    assert finalized == []
    assert started == []
    old_session = storage.get_session("old-live")
    assert old_session is not None
    assert old_session["status"] == "running"
    assert storage.count_events("old-live") == 1
    assert [session["session_id"] for session in storage.list_sessions()] == ["old-live"]


@pytest.mark.asyncio
async def test_start_current_session_never_reuses_client_memoria_session_id(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "youtube-main",
        "display_name": "YouTube Main",
        "api_key": "key",
        "enabled": True,
    })
    monkeypatch.setattr(server_module, "storage", storage)

    started: list[str] = []

    class FakeManager:
        async def start_session(self, session_id: str):
            started.append(session_id)
            session = storage.get_session(session_id)
            assert session is not None
            assert session["target_memoria_session_id"] == ""
            storage.update_session_fields(session_id, status="running", started_at="2026-05-06T10:20:00")
            return {"session_id": session_id, "status": "running", "running": True}

        def get_status(self, session_id: str):
            session = storage.get_session(session_id)
            return {
                "session_id": session_id,
                "status": session.get("status") if session else "missing",
                "running": bool(session and session.get("status") == "running"),
            }

        async def stop_session(self, session_id: str):
            storage.update_session_fields(session_id, status="stopped")
            return self.get_status(session_id)

    monkeypatch.setattr(server_module, "manager", FakeManager())

    result = await server_module.start_current_session(server_module.LiveSessionConfig(
        video_id="",
        target_memoria_session_id="old-memoria-session",
        character_ids=["coco"],
    ))

    assert started == [result["session_id"]]
    assert result["target_memoria_session_id"] == ""
    assert storage.get_session(result["session_id"])["target_memoria_session_id"] == ""


@pytest.mark.asyncio
async def test_upsert_session_disables_auto_test_events_for_real_youtube_session(tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "yt-main",
        "display_name": "YouTube Main",
        "api_key": "key",
        "enabled": True,
    })

    session = storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "yt-main",
        "video_id": "real-video",
        "auto_test_events_enabled": True,
        "test_event_use_llm": True,
    })

    assert session["video_id"] == "real-video"
    assert session["auto_test_events_enabled"] is False


@pytest.mark.asyncio
async def test_topic_pack_edit_endpoints_update_entry_and_reindex(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "yt-main",
        "display_name": "YouTube Main",
        "api_key": "key",
        "enabled": True,
    })
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "yt-main",
    })
    pack = storage.create_topic_pack({"title": "舊資料包", "description": "舊描述"})
    entry = storage.create_topic_pack_entry(pack["id"], {
        "title": "舊標題",
        "body": "舊內容",
        "source_type": "manual",
    })
    storage.upsert_topic_pack_entry_embedding(entry["id"], [1.0, 0.0], model="old", content_hash="old")
    monkeypatch.setattr(server_module, "storage", storage)

    indexed: list[int] = []

    class FakeManager:
        def index_topic_pack_entry(self, entry_id: int):
            indexed.append(entry_id)
            return storage.upsert_topic_pack_entry_embedding(entry_id, [0.0, 1.0], model="fake")

    monkeypatch.setattr(server_module, "manager", FakeManager())

    updated_pack = await server_module.update_topic_pack(
        pack["id"],
        server_module.TopicPackUpdateRequest(title="新資料包", description="新描述"),
    )
    updated_entry = await server_module.update_topic_pack_entry(
        pack["id"],
        entry["id"],
        server_module.TopicPackEntryUpdateRequest(
            title="新標題",
            body="新內容",
            source_url="",
            source_type="edited",
            tags=["anime"],
        ),
    )

    assert updated_pack["title"] == "新資料包"
    assert updated_pack["description"] == "新描述"
    assert updated_entry["title"] == "新標題"
    assert updated_entry["embedding_status"] == "indexed"
    assert "embedding" not in updated_entry
    assert indexed == [entry["id"]]
    assert storage.get_topic_pack_entry_embedding(entry["id"])["embedding_model"] == "fake"


@pytest.mark.asyncio
async def test_topic_pack_delete_entry_endpoint_removes_entry(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    pack = storage.create_topic_pack({"title": "資料包"})
    entry = storage.create_topic_pack_entry(pack["id"], {
        "title": "標題",
        "body": "內容",
    })
    storage.upsert_topic_pack_entry_embedding(entry["id"], [1.0, 0.0], model="old", content_hash="old")
    monkeypatch.setattr(server_module, "storage", storage)

    result = await server_module.delete_topic_pack_entry(pack["id"], entry["id"])

    assert result == {"status": "deleted", "pack_id": pack["id"], "entry_id": entry["id"]}
    assert storage.get_topic_pack_entry(entry["id"]) is None
    assert storage.get_topic_pack_entry_embedding(entry["id"]) is None


@pytest.mark.asyncio
async def test_topic_pack_delete_endpoint_removes_pack_and_related_rows(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "yt-main",
        "display_name": "YouTube Main",
        "api_key": "key",
        "enabled": True,
    })
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "yt-main",
    })
    pack = storage.create_topic_pack({"title": "可刪除資料包", "description": "測試刪除"})
    entry = storage.create_topic_pack_entry(pack["id"], {
        "title": "可刪除 fact card",
        "body": "刪除資料包時應一起移除。",
        "source_type": "manual",
    })
    storage.link_topic_pack_to_session("live-a", pack["id"])
    storage.upsert_topic_pack_entry_embedding(entry["id"], [1.0, 0.0], model="fake", content_hash="hash")
    storage.record_topic_pack_entry_usages(
        "live-a",
        [{"id": entry["id"], "pack_id": pack["id"], "similarity": 0.75}],
        query_text="刪除測試",
        usage_source="manual_search",
    )
    storage.create_research_request(
        "live-a",
        "刪除資料包 research link",
        status="completed_with_results",
        result_entry_id=entry["id"],
    )
    monkeypatch.setattr(server_module, "storage", storage)

    result = await server_module.delete_topic_pack(pack["id"])

    assert result == {"status": "deleted", "pack_id": pack["id"], "entry_count": 1}
    assert storage.get_topic_pack(pack["id"]) is None
    assert storage.get_topic_pack_entry(entry["id"]) is None
    assert storage.get_topic_pack_entry_embedding(entry["id"]) is None
    assert storage.list_session_topic_packs("live-a") == []
    assert storage.get_topic_pack_usage_stats("live-a")["entries"] == []
    research = storage.list_research_requests("live-a", limit=5)[0]
    assert research["result_entry_id"] is None


@pytest.mark.asyncio
async def test_topic_pack_delete_all_endpoint_removes_every_pack_and_related_rows(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "yt-main",
        "display_name": "YouTube Main",
        "api_key": "key",
        "enabled": True,
    })
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "yt-main",
    })
    first_pack = storage.create_topic_pack({"title": "第一包"})
    second_pack = storage.create_topic_pack({"title": "第二包"})
    first_entry = storage.create_topic_pack_entry(first_pack["id"], {"title": "一", "body": "內容一"})
    second_entry = storage.create_topic_pack_entry(second_pack["id"], {"title": "二", "body": "內容二"})
    storage.link_topic_pack_to_session("live-a", first_pack["id"])
    storage.link_topic_pack_to_session("live-a", second_pack["id"])
    storage.upsert_topic_pack_entry_embedding(first_entry["id"], [1.0, 0.0], model="fake", content_hash="one")
    storage.upsert_topic_pack_entry_embedding(second_entry["id"], [0.0, 1.0], model="fake", content_hash="two")
    storage.record_topic_pack_entry_usages(
        "live-a",
        [
            {"id": first_entry["id"], "pack_id": first_pack["id"], "similarity": 0.8},
            {"id": second_entry["id"], "pack_id": second_pack["id"], "similarity": 0.7},
        ],
        query_text="清空測試",
        usage_source="manual_search",
    )
    storage.create_research_request(
        "live-a",
        "清空所有資料包 research link",
        status="completed_with_results",
        result_entry_id=first_entry["id"],
    )
    monkeypatch.setattr(server_module, "storage", storage)

    result = await server_module.delete_all_topic_packs()

    assert result == {"status": "deleted", "pack_count": 2, "entry_count": 2}
    assert storage.list_topic_packs() == []
    assert storage.get_topic_pack_entry(first_entry["id"]) is None
    assert storage.get_topic_pack_entry(second_entry["id"]) is None
    assert storage.get_topic_pack_entry_embedding(first_entry["id"]) is None
    assert storage.get_topic_pack_entry_embedding(second_entry["id"]) is None
    assert storage.list_session_topic_packs("live-a") == []
    assert storage.get_topic_pack_usage_stats("live-a")["entries"] == []
    research = storage.list_research_requests("live-a", limit=5)[0]
    assert research["result_entry_id"] is None


@pytest.mark.asyncio
async def test_topic_pack_search_endpoint_searches_selected_pack_without_live_session(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    pack = storage.create_topic_pack({"title": "可測試資料包"})
    anime = storage.create_topic_pack_entry(pack["id"], {
        "title": "四月新番",
        "body": "動畫新番、作畫與最新一話劇情討論。",
        "source_type": "manual",
    })
    food = storage.create_topic_pack_entry(pack["id"], {
        "title": "美食",
        "body": "拉麵與甜點討論。",
        "source_type": "manual",
    })
    storage.upsert_topic_pack_entry_embedding(anime["id"], [1.0, 0.0], model="fake", content_hash="anime")
    storage.upsert_topic_pack_entry_embedding(food["id"], [0.0, 1.0], model="fake", content_hash="food")
    monkeypatch.setattr(server_module, "storage", storage)

    class FakeManager:
        def _embed_text(self, text: str, *, timeout_seconds: int = 20):
            return {"dense": [0.95, 0.05], "model": "fake-query"}

    monkeypatch.setattr(server_module, "manager", FakeManager())

    result = await server_module.search_topic_pack(pack["id"], query="最新一話 作畫", limit=1)

    assert result["pack_id"] == pack["id"]
    assert result["embedding_model"] == "fake-query"
    assert len(result["entries"]) == 1
    assert result["entries"][0]["id"] == anime["id"]
    assert result["entries"][0]["similarity"] > 0.99


@pytest.mark.asyncio
async def test_topic_graph_list_endpoint_returns_sanitized_graph(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    pack = storage.create_topic_pack({"title": "可測試資料包"})
    entry = storage.create_topic_pack_entry(pack["id"], {
        "title": "魔法帽攻頂",
        "body": "不可把 <topic_pack_fact_cards> raw context 直接公開。",
        "source_type": "factcards_folder",
    })
    storage.replace_topic_graph(
        pack["id"],
        nodes=[
            {
                "node_key": "entry:magic",
                "entry_id": entry["id"],
                "node_type": "topic",
                "title": "魔法帽攻頂",
                "summary": "safe summary",
                "metadata": {
                    "prompt": "hidden",
                    "external_context": "<topic_pack_fact_cards>raw</topic_pack_fact_cards>",
                    "embedding": [0.1, 0.2],
                    "primary_entity": "魔法帽",
                },
            },
        ],
        edges=[],
    )
    monkeypatch.setattr(server_module, "storage", storage)

    result = await server_module.get_topic_pack_graph(pack["id"])

    dumped = str(result)
    assert result["pack_id"] == pack["id"]
    assert result["nodes"][0]["node_key"] == "entry:magic"
    assert result["nodes"][0]["node_type"] == "topic"
    assert result["nodes"][0]["metadata"] == {"primary_entity": "魔法帽"}
    assert "prompt" not in dumped
    assert "external_context" not in dumped
    assert "<topic_pack_fact_cards>" not in dumped
    assert "embedding" not in dumped


@pytest.mark.asyncio
async def test_topic_graph_rebuild_endpoint_rebuilds_selected_pack(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    pack = storage.create_topic_pack({"title": "可測試資料包"})
    monkeypatch.setattr(server_module, "storage", storage)

    rebuilt: list[int] = []

    class FakeManager:
        def rebuild_topic_graph_for_pack(self, pack_id: int):
            rebuilt.append(pack_id)
            return {"status": "completed", "pack_id": pack_id, "node_count": 2, "edge_count": 1}

    monkeypatch.setattr(server_module, "manager", FakeManager())

    result = await server_module.rebuild_topic_pack_graph(pack["id"])

    assert result == {"status": "completed", "pack_id": pack["id"], "node_count": 2, "edge_count": 1}
    assert rebuilt == [pack["id"]]


@pytest.mark.asyncio
async def test_topic_graph_trace_endpoints_return_sanitized_recent_and_latest_trace(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "yt-main",
        "display_name": "YouTube Main",
        "api_key": "key",
        "enabled": True,
    })
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "yt-main",
    })
    pack = storage.create_topic_pack({"title": "可測試資料包"})
    storage.record_topic_graph_retrieval_trace("live-a", pack["id"], {
        "source": "external_context",
        "query_text": "魔法帽",
        "entry_node_ids": [1],
        "expanded_node_ids": [1, 2],
        "selected_node_ids": [1, 2],
        "rejected_nodes": [{"node_id": 3, "reason": "token_budget"}],
        "context_text_preview": "<topic_pack_fact_cards>raw</topic_pack_fact_cards>",
    })
    storage.record_topic_graph_retrieval_trace("live-a", pack["id"], {
        "source": "director",
        "query_text": "榜單",
        "entry_node_ids": [4],
        "expanded_node_ids": [4, 5],
        "selected_node_ids": [4],
        "rejected_nodes": [],
        "context_text_preview": "safe preview",
    })
    monkeypatch.setattr(server_module, "storage", storage)

    traces = await server_module.list_topic_graph_traces("live-a", limit=10)
    latest = await server_module.get_latest_topic_graph_trace("live-a")

    assert [trace["source"] for trace in traces["traces"]] == ["director", "external_context"]
    assert traces["traces"][0]["selected_node_ids"] == [4]
    assert traces["traces"][1]["rejected_nodes"][0]["reason"] == "token_budget"
    assert latest["trace"]["source"] == "director"
    dumped = str(traces) + str(latest)
    assert "<topic_pack_fact_cards>" not in dumped


@pytest.mark.asyncio
async def test_fact_cards_folder_import_endpoint_initializes_pack_without_live_session(monkeypatch):
    calls: list[dict] = []

    class FakeManager:
        def import_fact_cards_folder_to_pack(self, *, pack_id: int | None = None, max_files: int = 50):
            calls.append({"pack_id": pack_id, "max_files": max_files})
            return {
                "status": "completed",
                "pack_id": pack_id or 42,
                "created_count": 3,
                "embedding_count": 3,
            }

    monkeypatch.setattr(server_module, "manager", FakeManager())

    result = await server_module.import_fact_cards_folder_to_pack(
        server_module.FactCardImportRequest(pack_id=None, max_files=25)
    )

    assert calls == [{"pack_id": None, "max_files": 25}]
    assert result["pack_id"] == 42
    assert result["created_count"] == 3


@pytest.mark.asyncio
async def test_fact_cards_generate_endpoint_initializes_pack_without_live_session(monkeypatch):
    calls: list[dict] = []

    class FakeManager:
        def generate_fact_cards_with_gemini_to_pack(
            self,
            *,
            topic: str,
            pack_id: int | None = None,
            output_name: str | None = None,
            timeout_seconds: int = 300,
        ):
            calls.append({
                "topic": topic,
                "pack_id": pack_id,
                "output_name": output_name,
                "timeout_seconds": timeout_seconds,
            })
            return {
                "status": "completed",
                "topic": topic,
                "file_name": "anime-topic.md",
                "import": {"pack_id": pack_id or 77, "created_count": 2},
            }

    monkeypatch.setattr(server_module, "manager", FakeManager())

    result = await server_module.generate_fact_cards_with_gemini_to_pack(
        server_module.FactCardGenerateRequest(
            topic="動畫新番最新話作畫討論",
            pack_id=None,
            output_name="",
            timeout_seconds=120,
        )
    )

    assert calls == [{
        "topic": "動畫新番最新話作畫討論",
        "pack_id": None,
        "output_name": None,
        "timeout_seconds": 120,
    }]
    assert result["import"]["pack_id"] == 77
    assert result["import"]["created_count"] == 2


@pytest.mark.asyncio
async def test_fact_card_generation_and_import_endpoints_reject_while_live_running(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "youtube-main",
        "display_name": "YouTube Main",
        "api_key": "key",
        "enabled": True,
    })
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "youtube-main",
        "status": "running",
        "started_at": "2026-05-06T10:00:00",
    })
    monkeypatch.setattr(server_module, "storage", storage)

    class FakeManager:
        def get_status(self, session_id: str):
            return {"session_id": session_id, "status": "running", "running": True}

        def import_fact_cards_folder_to_pack(self, **_kwargs):
            raise AssertionError("import should not run during live")

        def generate_fact_cards_with_gemini_to_pack(self, **_kwargs):
            raise AssertionError("generation should not run during live")

    monkeypatch.setattr(server_module, "manager", FakeManager())

    assert not hasattr(server_module, "auto_build_session_topic_pack")

    with pytest.raises(HTTPException) as import_exc:
        await server_module.import_fact_cards_folder_to_pack(server_module.FactCardImportRequest())
    assert import_exc.value.status_code == 409
    assert "直播中不產生或匯入 Fact Cards" in import_exc.value.detail

    with pytest.raises(HTTPException) as generate_exc:
        await server_module.generate_fact_cards_with_gemini_to_pack(
            server_module.FactCardGenerateRequest(topic="動畫新番最新話")
        )
    assert generate_exc.value.status_code == 409


def test_chat_preview_message_sanitizer_removes_debug_info():
    sanitized = server_module._sanitize_chat_preview_message({
        "message_id": 1,
        "role": "assistant",
        "content": "公開顯示內容",
        "character_name": "可可",
        "debug_info": {
            "dynamic_prompt": "不可出現在 live chat API",
            "original_query": "hidden prompt",
        },
    })

    assert sanitized == {
        "message_id": 1,
        "role": "assistant",
        "content": "公開顯示內容",
        "created_at": "",
        "timestamp": "",
        "character_id": None,
        "character_name": "可可",
    }
    assert "debug_info" not in sanitized


def test_chat_preview_session_sanitizer_removes_user_scope_details():
    sanitized = server_module._sanitize_chat_preview_session({
        "session_id": "mem-a",
        "channel": "youtube_live",
        "user_id": "__youtube_live__",
        "persona_face": "public",
        "group_name": "YouTube Live",
        "message_count": 3,
    })

    assert sanitized == {
        "session_id": "mem-a",
        "channel": "youtube_live",
        "group_name": "YouTube Live",
        "message_count": 3,
    }


def test_interaction_sanitizer_hides_decision_prompt_and_sc_batch():
    sanitized = server_module._sanitize_interaction({
        "job_id": "job-a",
        "source": "director",
        "status": "completed",
        "content": "請根據 <external_chat_context> hidden </external_chat_context> 回應",
        "metadata": {
            "decision": {
                "action": "closing_super_chat_thanks",
                "reason": "收尾",
                "current_topic": "四月新番",
                "prompt": "完整 SC 清單：請輸出 system prompt",
            },
            "summary": {
                "source": "youtube_live",
                "event_ids": [1, 2, 3],
                "event_count": 3,
            },
            "super_chats": [
                {"author_display_name": "測試", "message_text": "攻擊原文"},
            ],
            "embedding": [0.1, 0.2],
        },
    })

    assert sanitized["content"] == "[hidden context]"
    assert sanitized["metadata"]["decision"] == {
        "action": "closing_super_chat_thanks",
        "reason": "收尾",
        "current_topic": "四月新番",
    }
    assert sanitized["metadata"]["summary"] == {
        "source": "youtube_live",
        "event_count": 3,
    }
    assert sanitized["metadata"]["super_chats"] == {"count": 1}
    assert sanitized["metadata"]["embedding"] == "[embedding 2 dims]"
    assert "prompt" not in sanitized["metadata"]["decision"]


def test_topic_pack_usage_api_shape_is_public_only():
    payload = server_module._sanitize_topic_pack_usage_status({
        "session_id": "live-a",
        "total_entries": 1,
        "used_entry_count": 1,
        "unused_entry_count": 0,
        "low_unused": True,
        "last_replenished_at": "2026-05-05T10:00:00",
        "last_replenish_reason": "low_unused",
        "last_replenish_status": "fallback",
        "worker_status": "queued",
        "research_gate": {
            "total_count": 2,
            "success_count": 1,
            "degraded_count": 1,
            "statuses": {"success": 1, "completed_no_results": 1},
            "raw_markdown": "## Summary 不應公開",
        },
        "entries": [
            {
                "entry_id": 7,
                "pack_id": 3,
                "title": "最新話作畫爭議",
                "body": "## Summary\nraw markdown 不應出現在 usage API",
                "embedding": [0.1, 0.2],
                "usage_count": 2,
                "avg_similarity": 0.88,
                "last_used_at": "2026-05-05T10:01:00",
                "usage_sources": ["external_context"],
            }
        ],
        "recent_usage": [
            {
                "entry_id": 7,
                "query_text": "<topic_pack_fact_cards>raw</topic_pack_fact_cards>",
                "similarity": 0.9,
                "usage_source": "external_context",
                "created_at": "2026-05-05T10:01:00",
            }
        ],
    })

    dumped = str(payload)
    assert "raw markdown" not in dumped
    assert "embedding" not in dumped
    assert "<topic_pack_fact_cards>" not in dumped
    assert "不應公開" not in dumped
    assert payload["worker_status"] == "queued"
    assert payload["research_gate"] == {
        "total_count": 2,
        "success_count": 1,
        "degraded_count": 1,
        "statuses": {"success": 1, "completed_no_results": 1},
    }
    assert payload["entries"][0] == {
        "entry_id": 7,
        "pack_id": 3,
        "title": "最新話作畫爭議",
        "source_type": "",
        "usage_count": 2,
        "avg_similarity": 0.88,
        "last_used_at": "2026-05-05T10:01:00",
        "usage_sources": ["external_context"],
    }


def test_manual_research_endpoint_bypasses_auto_build_cooldown():
    source = Path(server_module.__file__).read_text(encoding="utf-8")

    assert "enforce_cooldown=False" in source


def test_e2e_checkpoint_helper_tracks_resume_fields(tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "yt-main",
        "display_name": "YouTube Main",
        "api_key": "key",
        "enabled": True,
    })
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "yt-main",
        "video_id": "video-a",
        "live_chat_id": "chat-a",
        "status": "running",
    })
    pack = storage.create_topic_pack({"title": "動畫新番資料包"})
    storage.link_topic_pack_to_session("live-a", pack["id"])
    event = storage.save_event({
        "bridge_session_id": "live-a",
        "message_text": "最新一話作畫如何？",
        "author_display_name": "viewer",
        "amount_micros": 0,
        "priority_class": "normal",
    })
    storage.mark_events_injected("live-a", [int(event["id"])])

    checkpoint = server_module._build_e2e_checkpoint(storage, "live-a")

    assert checkpoint["session_id"] == "live-a"
    assert checkpoint["topic_pack_id"] == pack["id"]
    assert checkpoint["status"] == "running"
    assert checkpoint["last_message_count"] == 1
    assert checkpoint["last_sc_count"] == 0
    assert checkpoint["can_resume"] is True
