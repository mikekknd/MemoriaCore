import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from test_live_episode_plan_contract import sample_plan


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
        for name in (
            "index.css",
            "base.css",
            "live-session.css",
            "topic-pack.css",
            "topic-graph.css",
            "overlays.css",
            "core.js",
            "selectors.js",
            "topic-packs.js",
            "topic-graph.js",
            "topic-pack-crud.js",
            "fact-card-import.js",
            "memoria-control.js",
            "live-persona-control.js",
            "events-control.js",
            "summary-director-control.js",
            "session-control.js",
            "control.js",
            "app.js",
        ):
            path = ui_root / name
            if path.exists():
                parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _live_chat_source() -> str:
    static_root = Path(server_module.STATIC_ROOT)
    ui_root = static_root / "ui"
    parts = [(static_root / "live_chat.html").read_text(encoding="utf-8")]
    for name in ("live-chat.css", "live-chat.js"):
        path = ui_root / name
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _assert_launcher_uses_runtime_log_dir(source: str, legacy_runtime_prefix: str) -> None:
    assert r"runtime\log" in source
    assert ".foreground.log" in source or (".out.log" in source and ".err.log" in source)
    assert legacy_runtime_prefix not in source.lower()

# Split from test_server_auth.py: server, launcher, route, and UI contracts.

def test_live_page_static_files_are_registered():
    static_root = Path(server_module.STATIC_ROOT)

    assert (static_root / "live.html").exists()
    assert (static_root / "live_chat.html").exists()


def test_bridge_server_uses_windows_selector_policy_before_uvicorn_import():
    source = (BRIDGE_ROOT / "server.py").read_text(encoding="utf-8")

    assert "WindowsSelectorEventLoopPolicy" in source
    assert source.index("WindowsSelectorEventLoopPolicy") < source.index("import uvicorn")


def test_live_chat_uses_immediate_sse_refresh_for_chat_payloads():
    live_chat_html = _live_chat_source()

    assert 'live-chat.js?v=audio-preload-v1' in live_chat_html
    assert "LIVE_CHAT_REFRESH_TYPES" in live_chat_html
    assert '"chat_message"' in live_chat_html
    assert '"youtube_live_event"' in live_chat_html
    assert '"interaction_completed"' in live_chat_html
    assert '"director_injected"' in live_chat_html
    assert "appendChatMessage(payload.message)" in live_chat_html
    assert "function ensureSubscription()" in live_chat_html
    assert "state.subscribedSessionId === state.sessionId" in live_chat_html
    assert "presentationEnabled" in live_chat_html
    assert "state.presentationEnabled = !!selected.presentation_enabled" in live_chat_html
    assert "if (state.presentationEnabled) return;" in live_chat_html
    assert live_chat_html.index("ensureSubscription();") < live_chat_html.index(
        'api(`/sessions/${encodeURIComponent(state.sessionId)}/chat-preview?limit=120`)'
    )
    assert "const liveEventMessages = state.presentationEnabled ? [] : state.liveEventMessages" in live_chat_html
    assert "state.displayMessages, liveEventMessages, data.messages || []" in live_chat_html
    assert '${message.role || "message"}:${messageId}' in live_chat_html
    assert "scheduleRefresh(0)" in live_chat_html


def test_live_chat_recovers_after_interrupt_events():
    live_chat_html = _live_chat_source()

    assert "interruptRecoveryTimers" in live_chat_html
    assert "function scheduleInterruptRecoveryRefreshes()" in live_chat_html
    assert "function handleInteractionInterrupt(payload = {})" in live_chat_html
    assert 'payload.type === "interrupt_requested"' in live_chat_html
    assert 'payload.type === "interrupt_requested" || payload.type === "interaction_interrupted"' not in live_chat_html
    interrupt_branch = live_chat_html[
        live_chat_html.index('if (payload.type === "interrupt_requested")'):
        live_chat_html.index('if (payload.type === "interaction_interrupted")')
    ]
    assert "handleInteractionInterrupt(payload)" in interrupt_branch
    assert "state.presentationQueue = []" in live_chat_html
    assert "state.currentAudio.pause()" in live_chat_html
    assert "presentation/current/skip" in live_chat_html
    assert "35000" in live_chat_html


def test_live_chat_interaction_interrupted_preserves_current_audio():
    live_chat_html = _live_chat_source()

    interrupted_branch = live_chat_html[
        live_chat_html.index('if (payload.type === "interaction_interrupted")'):
        live_chat_html.index('if (payload.type === "presentation_item_ready"')
    ]
    assert "handleInteractionInterrupt" not in interrupted_branch
    assert "stopPresentationPlayback" not in interrupted_branch
    assert "presentation/current/skip" not in interrupted_branch
    assert "scheduleInterruptRecoveryRefreshes()" in interrupted_branch
    assert "scheduleRefresh(0)" in interrupted_branch


def test_live_chat_renders_youtube_events_as_live_events_not_user_messages():
    live_chat_html = _live_chat_source()

    assert 'if (message.role === "system_event" && message.source === "youtube_live_event") return "直播留言";' in live_chat_html
    event_mapper = live_chat_html[
        live_chat_html.index("function liveEventToMessage"):
        live_chat_html.index("function assignMessageOrder")
    ]
    assert 'role: "system_event",' in event_mapper
    assert 'role: "user",' not in event_mapper


def test_live_chat_handles_presentation_queue_events():
    live_chat_html = _live_chat_source()

    assert '"presentation_item_preload"' in live_chat_html
    assert '"presentation_item_ready"' in live_chat_html
    assert "cachePresentationAudio" in live_chat_html
    assert "audioForPresentationItem" in live_chat_html
    assert "playPresentationItem" in live_chat_html
    assert "ackPresentationItem" in live_chat_html
    assert "presentation/current/skip" in live_chat_html
    assert "audio.addEventListener(\"ended\"" in live_chat_html


def test_live_chat_missing_timestamp_uses_stable_fallback_order(tmp_path):
    if not shutil.which("node"):
        pytest.skip("node is required for live-chat.js behavior test")
    static_root = Path(server_module.STATIC_ROOT)
    source = (static_root / "ui" / "live-chat.js").read_text(encoding="utf-8")
    helper_source = source[:source.index("function visibleMessages")]
    script = tmp_path / "live_chat_order_test.mjs"
    script.write_text(
        helper_source
        + """
const mixed = mergeMessages(
  [{ message_id: 1, role: "assistant", content: "有時間", timestamp: "2026-05-10T12:00:00" }],
  [{ message_id: 2, role: "assistant", content: "空時間", timestamp: "" }],
);
const mixedOrder = mixed.map((message) => message.content).join("|");
if (mixedOrder !== "有時間|空時間") {
  throw new Error(`missing timestamp sorted as oldest: ${mixedOrder}`);
}

const numericFallback = mergeMessages(
  [{ message_id: 2, role: "assistant", content: "二號", timestamp: "" }],
  [{ message_id: 10, role: "assistant", content: "十號", timestamp: "" }],
);
const numericOrder = numericFallback.map((message) => message.content).join("|");
if (numericOrder !== "二號|十號") {
  throw new Error(`numeric message_id fallback sorted lexically: ${numericOrder}`);
}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(script)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert result.returncode == 0, result.stderr


def test_live_chat_polls_memoria_history_while_sse_is_connected():
    live_chat_html = _live_chat_source()

    assert "historyRefreshTimer" in live_chat_html
    assert "function startHistoryRefresh()" in live_chat_html
    assert "startHistoryRefresh();" in live_chat_html
    assert "setInterval(async () => {" in live_chat_html
    assert "await refreshChat({ silent: true })" in live_chat_html


def test_live_chat_assigns_stable_assistant_bubble_colors():
    live_chat_html = _live_chat_source()

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
    live_chat_html = _live_chat_source()

    assert 'id="durationBadge"' in live_chat_html
    assert "durationRefreshTimer" in live_chat_html
    assert "function formatDuration(seconds)" in live_chat_html
    assert "function updateDurationBadge()" in live_chat_html
    assert "function startDurationRefresh()" in live_chat_html
    assert "selected.started_at || selected.created_at" in live_chat_html
    assert "selected.planned_duration_minutes" in live_chat_html
    assert "已直播" in live_chat_html
    assert "目標" in live_chat_html


def test_control_ui_loads_external_css_and_module_script():
    index_html = (Path(server_module.STATIC_ROOT) / "index.html").read_text(encoding="utf-8")

    assert '<link rel="stylesheet" href="/ui-assets/index.css?v=events-feedback-v3">' in index_html
    assert '<script type="module" src="/ui-assets/app.js?v=events-feedback-v3"></script>' in index_html
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


@pytest.mark.asyncio
async def test_youtube_live_global_suffix_proxy_uses_dedicated_prompt_key(monkeypatch):
    calls = []

    class FakeClient:
        def get_prompt_metadata(self, key):
            calls.append(("get", key))
            return {
                "key": key,
                "current_template": "old suffix",
                "has_user_override": False,
            }

        def update_prompt_template(self, key, template):
            calls.append(("update", key, template))
            return {
                "key": key,
                "template": template,
                "has_user_override": True,
            }

    monkeypatch.setattr(server_module._memoria_routes, "MemoriaClient", lambda *args, **kwargs: FakeClient())

    current = await server_module.get_youtube_live_global_suffix()
    updated = await server_module.update_youtube_live_global_suffix(
        server_module.YouTubeLiveGlobalSuffixRequest(template="new suffix")
    )

    assert current["key"] == "chat_system_suffix_youtube_live"
    assert current["template"] == "old suffix"
    assert updated["key"] == "chat_system_suffix_youtube_live"
    assert updated["template"] == "new suffix"
    assert calls == [
        ("get", "chat_system_suffix_youtube_live"),
        ("update", "chat_system_suffix_youtube_live", "new suffix"),
    ]


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


def test_control_ui_exposes_live_persona_overlay_editor():
    source = _control_ui_source()

    assert "直播角色設定" in source
    assert "只覆寫 YouTubeBridge 直播時送給角色的 prompt" in source
    assert 'id="livePersonaCharacterSelect"' in source
    assert 'id="livePersonaSystemPrompt"' in source
    assert 'id="livePersonaOpeningIntro"' in source
    assert 'id="livePersonaAddressingRows"' in source
    assert 'id="addLivePersonaAddressingRow"' in source
    assert "live-persona-addressing-row" in source
    assert 'id="livePersonaAddressing"' not in source
    assert 'id="saveLivePersonaOverlay"' in source
    assert "/persona-overlays" in source


def test_control_ui_exposes_live_tts_profile_editor():
    source = _control_ui_source()

    assert "GPT-SoVITS 聲音設定" in source
    assert 'id="liveTtsSourcePreset"' in source
    assert "快速選擇聲音" in source
    assert "/tts-sources" in source
    assert "applyLiveTtsSourcePreset" in source
    assert 'id="liveTtsEnabled"' in source
    assert 'id="liveTtsRefAudioPath"' in source
    assert 'id="liveTtsPromptText"' in source
    assert 'id="liveTtsTextLang"' in source
    assert 'id="liveTtsPromptLang"' in source
    assert 'id="liveTtsSpeedFactor"' in source
    assert 'id="liveTtsMediaType"' in source
    assert "liveTtsProfileFor" in source
    assert "liveTtsProfilePayload" in source
    assert "`/persona-overlays/${encodeURIComponent(characterId)}/tts-profile`" in source


def test_control_ui_exposes_youtube_live_global_suffix_editor():
    source = _control_ui_source()

    assert "YouTube Live 全域 suffix" in source
    assert 'id="youtubeLiveGlobalSuffix"' in source
    assert 'id="reloadYoutubeLiveGlobalSuffix"' in source
    assert 'id="saveYoutubeLiveGlobalSuffix"' in source
    assert 'id="youtubeLiveGlobalSuffixState"' in source
    assert 'api("/memoria/youtube-live/global-suffix")' in source
    assert '`/memoria/youtube-live/global-suffix`' in source
    assert "loadYoutubeLiveGlobalSuffix" in source
    assert "saveYoutubeLiveGlobalSuffix" in source


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
    assert 'id="saveTestEventSettings"' in auto_block
    assert "儲存測試參數" in auto_block
    assert "saveTestEventSettings" in index_html
    assert '$("saveTestEventSettings").onclick' in index_html
    assert "await saveSession(false)" in index_html
    assert '/recent?limit=100&include_pending=true' in index_html
    assert 'id="injectSelected"' not in auto_block
    assert "待處理留言" in pending_block
    assert 'id="eventsList"' in pending_block
    assert 'id="injectSelected"' in pending_block
    assert 'id="injectPending"' in pending_block
    assert 'id="injectContent"' in pending_block
    assert "<summary>進階注入提示</summary>" in pending_block
    assert 'id="eventActionOverlay"' in index_html
    assert 'id="eventActionTitle"' in index_html
    assert 'id="eventActionMessage"' in index_html
    assert 'role="status" aria-live="polite"' in pending_block
    assert "withEventActionBusy" in index_html
    assert "startEventsAutoRefresh" in index_html
    assert "SSE 中斷，改用輪詢更新中" in index_html


def test_events_pane_actions_use_busy_feedback_and_polling_fallback():
    index_html = _control_ui_source()

    assert "EVENTS_AUTO_REFRESH_MS = 5000" in index_html
    assert "function setEventState" in index_html
    assert "function setEventActionOverlay" in index_html
    assert "async function withEventActionBusy" in index_html
    assert "button.setAttribute(\"aria-busy\", \"true\")" in index_html
    assert "eventActionOverlayTimer" in index_html
    assert "updateLiveSessionControls();" in index_html

    expected_actions = {
        "refreshEvents": "更新中",
        "generateTestEvents": "生成中",
        "saveTestEventSettings": "儲存中",
        "toggleAutoTestEvents": "啟動中",
        "injectEvents": "注入中",
        "replySuperChats": "SC 回應中",
        "interruptNow": "中斷中",
    }
    for action, busy_label in expected_actions.items():
        action_block = index_html[
            index_html.index(f"export async function {action}"):
            index_html.index("\n}", index_html.index(f"export async function {action}")) + 2
        ]
        assert "withEventActionBusy" in action_block, action
        assert busy_label in action_block, action

    assert "startEventsAutoRefresh" in index_html
    assert "stopEventsAutoRefresh" in index_html
    assert "eventsPane.classList.contains(\"active\")" in index_html
    assert "document.visibilityState === \"hidden\"" in index_html


def test_events_pane_prevents_empty_selected_injection_client_side():
    index_html = _control_ui_source()
    inject_block = index_html[
        index_html.index("export async function injectEvents"):
        index_html.index("export async function generateTestEvents")
    ]

    assert "if (!usePending && eventIds.length === 0)" in inject_block
    assert "請先勾選留言" in inject_block
    assert "reply-recent" in inject_block
    assert inject_block.index("請先勾選留言") < inject_block.index("reply-recent")


def test_events_sse_refreshes_relevant_message_types_and_falls_back_to_polling():
    index_html = _control_ui_source()
    subscribe_block = index_html[
        index_html.index("export function subscribeEvents"):
        index_html.index("state.eventSource.onerror")
    ]

    assert "state.eventSource.onopen" in index_html
    assert "mergeEventIntoState(payload.event)" in subscribe_block
    for event_type in (
        "test_events_generated",
        "test_events_auto_generated",
        "super_chat_batch_injected",
        "super_chat_received",
        "safety_classified",
        "test_event_auto_error",
        "closing_super_chat_thanks_completed",
    ):
        assert event_type in subscribe_block
    assert "startEventsAutoRefresh" in index_html
    assert "SSE 中斷，改用輪詢更新中" in index_html


def test_control_ui_checkbox_inputs_keep_native_compact_size():
    index_html = _control_ui_source()

    assert 'href="/ui-assets/index.css?v=events-feedback-v3"' in index_html
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


def test_install_test_ids_preserves_explicit_stable_testids():
    index_html = _control_ui_source()

    assert 'data-testid="director-idle-seconds"' in index_html
    assert "if (element && !element.dataset.testid) element.dataset.testid = id;" in index_html
    assert "element.dataset.testid = id;" not in index_html.replace(
        "if (element && !element.dataset.testid) element.dataset.testid = id;",
        "",
    )


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
async def test_phase_summary_callback_uses_phase_summary_and_shared_memory_helper(monkeypatch, tmp_path):
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
        "character_ids": ["coco"],
    })
    monkeypatch.setattr(server_module, "storage", storage)

    calls: list[dict] = []

    class FakeSummaryManager:
        def summarize_session_phase(self, session_id: str, **kwargs):
            calls.append({"session_id": session_id, **kwargs})
            return {
                "status": "completed",
                "summary": {
                    "id": 42,
                    "session_id": session_id,
                    "memory_text": "雜談摘要",
                    "character_ids": ["coco"],
                    "metadata": {"summary_phase": kwargs["summary_phase"]},
                },
            }

    async def fake_write(session_id: str, summary: dict):
        return {
            "summary": {**summary, "metadata": {**summary["metadata"], "memory_write_status": "completed"}},
            "memory_write": {"status": "completed"},
        }

    monkeypatch.setattr(server_module, "summary_manager", FakeSummaryManager())
    monkeypatch.setattr(
        server_module._sessions_routes,
        "_write_summary_shared_memory_without_cleanup",
        fake_write,
        raising=False,
    )

    result = await server_module._phase_summary_callback("live-a", summary_phase="free_talk", reason="test")

    assert calls == [{
        "session_id": "live-a",
        "summary_phase": "free_talk",
        "force": True,
        "min_events": 1,
        "max_events": 1000,
        "chunk_size": 120,
        "include_memoria_session": False,
        "safe_memory_text": True,
    }]
    assert result["summary"]["id"] == 42
    assert result["memory_write"]["status"] == "completed"


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
