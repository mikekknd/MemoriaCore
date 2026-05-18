import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from server_routes import summaries as summaries_route

spec = importlib.util.spec_from_file_location("youtube_bridge_server_for_studio_ui", BRIDGE_ROOT / "server.py")
server_module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(server_module)


def _route_paths() -> set[str]:
    return {
        path
        for route in server_module.app.routes
        for path in [getattr(route, "path", "")]
        if path
    }


def _studio_source() -> str:
    return (Path(server_module.STATIC_ROOT) / "studio.html").read_text(encoding="utf-8")


def test_studio_route_is_registered_as_parallel_ui_surface():
    paths = _route_paths()

    assert "/studio" in paths
    assert "/studio/" in paths


def test_studio_html_uses_external_assets_without_inline_code():
    studio_html = _studio_source()

    assert '<link rel="stylesheet" href="/ui-assets/studio.css?v=studio-v28">' in studio_html
    assert '<script type="module" src="/ui-assets/studio.js?v=studio-v30"></script>' in studio_html
    assert "<style>" not in studio_html
    assert "<script>\n" not in studio_html


@pytest.mark.asyncio
async def test_studio_assets_are_served_by_ui_asset_route():
    css_response = await server_module.bridge_ui_asset("studio.css")
    js_response = await server_module.bridge_ui_asset("studio.js")

    assert Path(css_response.path).name == "studio.css"
    assert Path(js_response.path).name == "studio.js"


@pytest.mark.asyncio
async def test_studio_route_returns_studio_html():
    response = await server_module.bridge_studio()

    assert Path(response.path).name == "studio.html"


def test_studio_ui_keeps_legacy_features_out_of_main_surface():
    combined = "\n".join([
        _studio_source(),
        (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8"),
    ]).lower()

    forbidden_terms = [
        "topic pack",
        "program segment",
        "programsegment",
        "autonomous director",
        "raw context",
    ]
    for term in forbidden_terms:
        assert term not in combined


def test_studio_test_tab_exposes_comment_summary_and_event_display_controls():
    studio_html = _studio_source()
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    expected_controls = [
        'data-tab="test"',
        'id="testPanel"',
        'id="autoCommentEnabled"',
        'id="normalCommentCount"',
        'id="superChatCount"',
        'id="maliciousCommentEnabled"',
        'id="commentFrequencySeconds"',
        'id="runAutoCommentBatch"',
        'id="autoCommentStatus"',
        'id="testAutoSaveState"',
        'id="showLiveEventsEnabled"',
        'id="summaryPreview"',
        'id="regenerateSummary"',
    ]
    for control in expected_controls:
        assert control in studio_html

    assert 'data-tab="summary"' not in studio_html
    assert "function generateAutoComments()" in studio_js
    assert "function applyAutoCommentState()" in studio_js
    assert "function applyTestAutoSaveState(" in studio_js
    assert "function buildAutoCommentQueue()" in studio_js
    assert "function startAutoComments()" in studio_js
    assert "function stopAutoComments()" in studio_js
    assert "一般留言" in studio_html
    assert "Super Chat" in studio_html
    assert "惡意留言" in studio_html
    assert "留言頻率" in studio_html
    assert "Summary 測試" in studio_html
    assert "顯示直播事件/觀眾留言" in studio_html


def test_studio_has_manual_free_talk_test_button():
    studio_html = _studio_source()
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    assert "開始雜談測試" in studio_html
    assert 'id="startFreeTalkTestButton"' in studio_html
    assert 'id="freeTalkTestState"' in studio_html
    assert "/phase/free-talk-test/start" in studio_js
    assert "async function startFreeTalkTest()" in studio_js
    assert "post_plan_free_talk" in studio_js
    assert 'result?.status === "wait"' in studio_js
    assert "已切換到雜談測試；目前互動執行中，會在結束後繼續。" in studio_js
    assert "await refreshStudioSession();" in studio_js


def test_studio_phase_pipeline_controls():
    studio_html = _studio_source()
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    assert "結束節目並進入雜談測試" in studio_html
    assert 'id="skipMainToFreeTalkButton"' in studio_html
    assert 'id="skipMainToFreeTalkState"' in studio_html
    assert "/phase/finish-main" in studio_js
    assert "/phase/finalize" in studio_js
    assert 'enter_free_talk: true' in studio_js
    assert 'force_enter_free_talk: true' in studio_js
    assert 'reason: "operator_debug_skip_to_free_talk"' in studio_js
    assert 'body: { reason: "operator_finalize", background: true }' in studio_js
    assert 'api(`/sessions/${encodeURIComponent(sessionId)}/stop`, {' not in studio_js


def test_studio_debug_log_starts_empty_without_mock_entries():
    studio_html = _studio_source()
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    assert 'id="debugLog"' in studio_html
    assert "尚無操作紀錄" in studio_html
    assert "10:32:40" not in studio_html
    assert "已連線 YouTube Live Chat" not in studio_html
    assert "企劃載入成功：EP08" not in studio_html
    assert "角色設定載入完成（2 人）" not in studio_html
    assert "取得直播金鑰成功" not in studio_html
    assert "function appendLog(" in studio_js
    assert "debugLog.prepend(item)" in studio_js


def test_studio_summary_test_uses_backend_summary_api_not_mock_text():
    studio_html = _studio_source()
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    assert "本段重點：介紹 AI 助理工具" not in studio_html
    assert "觀眾關注工具選擇、筆記整理與流程自動化" not in studio_js
    assert "async function regenerateSummary()" in studio_js
    assert 'api(`/sessions/${encodeURIComponent(state.sessionId)}/summarize`, {' in studio_js
    assert 'api(`/sessions/${encodeURIComponent(state.sessionId)}/summary`)' in studio_js
    assert "function renderSummaryPreview(" in studio_js
    assert "function updateSummaryControls(" in studio_js
    assert "請先停止直播再生成摘要" in studio_js
    assert 'renderSummaryPreview(null, "直播中不產生摘要；停止直播後可重新生成。")' in studio_js
    assert '"/finalize"' not in studio_js
    assert "summary/write-memory" not in studio_js


def test_studio_presentation_tts_controls_are_exposed():
    studio_html = _studio_source()
    studio_css = (Path(server_module.UI_ASSETS_ROOT) / "studio.css").read_text(encoding="utf-8")
    toolbar_start = studio_html.index('<div class="conversation-tools">')
    toolbar_end = studio_html.index("</div>", toolbar_start)
    toolbar_html = studio_html[toolbar_start:toolbar_end]

    assert 'id="presentationAudioStatus"' in studio_html
    assert 'id="enablePresentationAudio"' in studio_html
    assert 'id="skipPresentation"' in studio_html
    assert "語音待機" in studio_html
    assert "啟用聲音" in studio_html
    assert "跳過目前句子" in studio_html
    assert ".conversation-tools .hidden" in studio_css
    assert ".conversation-tools button.small" in studio_css
    expected_order = [
        'id="durationBadge"',
        'id="presentationAudioStatus"',
        'id="enablePresentationAudio"',
        'id="skipPresentation"',
        'id="clearConversation"',
    ]
    positions = [toolbar_html.index(item) for item in expected_order]
    assert positions == sorted(positions)
    assert '<span id="presentationAudioStatus" class="state-badge neutral">語音待機</span>' in toolbar_html
    assert (
        '<button id="enablePresentationAudio" class="secondary small hidden" type="button">'
        "啟用聲音</button>"
    ) in toolbar_html
    assert (
        '<button id="skipPresentation" class="secondary small" type="button" disabled>'
        "跳過目前句子</button>"
    ) in toolbar_html


def test_studio_presentation_tts_player_helpers_are_wired():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    expected_state_fields = [
        "presentationQueue: []",
        "presentationPlaying: false",
        "currentPresentationItem: null",
        "currentAudio: null",
        "audioUnlockRequired: false",
        "presentationAckInFlight: false",
        "presentationAudioCache: new Map()",
    ]
    for field in expected_state_fields:
        assert field in studio_js

    expected_functions = [
        "function updatePresentationStatus(statusText = \"語音待機\", level = \"neutral\")",
        "function setPresentationControls(",
        "function presentationItemToMessage(item)",
        "function cachePresentationAudio(item)",
        "function audioForPresentationItem(item)",
        "function enqueuePresentationItem(item)",
        "function playPresentationItem()",
        "async function ackPresentationItem(item)",
        "async function finishPresentationItem(item, reason = \"ended\")",
        "async function skipCurrentPresentation()",
        "async function retryCurrentPresentationAudio()",
        "async function handlePresentationInterrupt(payload = {})",
        "function resetPresentationPlayer(",
    ]
    for fn in expected_functions:
        assert fn in studio_js

    assert "new Audio(audioUrl)" in studio_js
    assert 'audio.preload = "auto"' in studio_js
    assert "const now = new Date().toISOString()" in studio_js
    assert "created_at: now" in studio_js
    assert "timestamp: now" in studio_js
    assert "if (state.presentationAckInFlight) return" in studio_js
    assert "if (!isCurrentPresentationItem(item) || state.currentAudio !== audio) return" in studio_js
    assert "const item = state.currentPresentationItem" in studio_js
    assert "const audio = state.currentAudio" in studio_js
    assert "state.currentPresentationItem = currentItem" in studio_js
    assert 'updatePresentationStatus("跳過失敗，請重試", "warn")' in studio_js
    assert 'updatePresentationStatus("打斷解除失敗，請重試", "warn")' in studio_js
    assert 'api(`/sessions/${encodeURIComponent(state.sessionId)}/presentation/${encodeURIComponent(item.item_id)}/ack`, {' in studio_js
    assert 'api(`/sessions/${encodeURIComponent(state.sessionId)}/presentation/current/skip`, {' in studio_js
    assert 'appendChatPreviewMessage(presentationItemToMessage(item), { prepend: true })' in studio_js
    assert 'if (payload.type === "presentation_item_preload" && payload.item) {' in studio_js
    assert "cachePresentationAudio(payload.item);" in studio_js


def test_studio_presentation_debug_logs_backend_and_audio_blocked_events():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")
    subscribe_body = studio_js[
        studio_js.index("state.eventSource.onmessage = (event) => {"):
        studio_js.index("state.eventSource.onerror = () => {", studio_js.index("state.eventSource.onmessage = (event) => {"))
    ]

    assert "function appendPresentationDebugLog(event)" in studio_js
    assert "function reportPresentationClientDebug(phase, item, details = {})" in studio_js
    assert 'if (payload.type === "presentation_debug" && payload.event) {' in subscribe_body
    assert 'if (payload.type === "presentation_item_preload" && payload.item) {' in subscribe_body
    assert subscribe_body.index('payload.type === "presentation_item_preload"') < subscribe_body.index('payload.type === "presentation_item_ready"')
    assert "appendPresentationDebugLog(payload.event);" in subscribe_body
    assert 'item_prefetch_ready: "預載完成，等待導播交付"' in studio_js
    assert 'reportPresentationClientDebug("audio_play_blocked", item, {' in studio_js
    assert 'phase: "audio_play_blocked"' in studio_js
    assert 'api(`/sessions/${encodeURIComponent(state.sessionId)}/presentation/debug`, {' in studio_js


def test_studio_displays_phase_summary_status():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    assert "function phaseSummaryText(" in studio_js
    assert "main_summary" in studio_js
    assert "free_talk_summary" in studio_js
    assert "memory_write_status" in studio_js
    assert "正式摘要" in studio_js
    assert "雜談摘要" in studio_js
    assert "sessionStateText.textContent = phaseSummaryText(session)" in studio_js


@pytest.mark.asyncio
async def test_summaries_route_filters_by_summary_phase_when_supported(monkeypatch):
    calls = []

    class FakeStorage:
        def list_session_summaries_by_phase(self, session_id, *, summary_phase, limit=20):
            calls.append((session_id, summary_phase, limit))
            return [{"id": 2, "metadata": {"summary_phase": summary_phase}}]

        def list_summaries(self, session_id=None, limit=100):
            raise AssertionError("phase query should use list_session_summaries_by_phase")

    monkeypatch.setattr(summaries_route, "storage", FakeStorage())

    result = await summaries_route.list_summaries(session_id="live-a", summary_phase="free_talk", limit=5)

    assert result == [{"id": 2, "metadata": {"summary_phase": "free_talk"}}]
    assert calls == [("live-a", "free_talk", 5)]


@pytest.mark.asyncio
async def test_summaries_route_phase_query_falls_back_when_storage_method_missing(monkeypatch):
    class FakeStorage:
        def list_summaries(self, session_id=None, limit=100):
            return [
                {"id": 3, "metadata": {"summary_phase": "main"}},
                {"id": 4, "metadata": {"summary_phase": "free_talk"}},
            ]

    monkeypatch.setattr(summaries_route, "storage", FakeStorage())

    result = await summaries_route.list_summaries(session_id="live-a", summary_phase="main", limit=10)

    assert result == [{"id": 3, "metadata": {"summary_phase": "main"}}]


def test_studio_test_comments_are_persisted_to_backend_when_session_is_live():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    assert "async function submitBackendTestEvents(" in studio_js
    assert 'api(`/sessions/${encodeURIComponent(state.sessionId)}/test-events/generate`, {' in studio_js
    assert "function eventToTestManualEvent(" in studio_js
    assert "const manualEvents = events.map(eventToTestManualEvent)" in studio_js
    assert "manual_events: manualEvents" in studio_js
    assert 'submitBackendTestEvents({ events: [{' in studio_js
    assert 'state.sessionId && state.live' in studio_js


def test_studio_live_auto_comments_use_backend_llm_generation():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    assert "function testCommentTopicHint()" in studio_js
    assert "use_llm: useLlm" in studio_js
    assert "topic_hint: testCommentTopicHint()" in studio_js
    assert "super_chat_count: Math.max(0, Math.floor(superChatCount || 0))" in studio_js
    assert "include_malicious_sc: Boolean(includeMalicious)" in studio_js
    batch_block = studio_js[studio_js.index('const result = await submitBackendTestEvents({', studio_js.index("async function generateAutoComments()")):studio_js.index("const generated = result?.generated", studio_js.index("async function generateAutoComments()"))]
    assert "count: normalCount" in batch_block
    assert "superChatCount: scCount" in batch_block
    assert "useLlm: true" in batch_block
    auto_tick_block = studio_js[studio_js.index('await submitBackendTestEvents({', studio_js.index("function startAutoComments()")):studio_js.index("state.autoCommentSent += 1", studio_js.index("function startAutoComments()"))]
    assert 'count: item.kind === "comment" ? 1 : 0' in auto_tick_block
    assert 'superChatCount: item.kind === "super" ? 1 : 0' in auto_tick_block
    assert "useLlm: !item.manualEvent" in auto_tick_block
    assert 'submitBackendTestEvents({ events: queue, source: "自動留言批次" })' not in studio_js
    assert 'submitBackendTestEvents({ events: [item], source: "自動留言" })' not in studio_js
    assert "use_llm: false" not in studio_js


def test_studio_exposes_dedicated_role_settings_tab():
    studio_html = _studio_source()
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    expected_controls = [
        'data-tab="roles"',
        'id="rolesPanel"',
        'id="roleSummaryState"',
        'id="roleSummaryList"',
        'id="openRoleSettingsButton"',
        'id="refreshRolesButton"',
        'id="livePersonaCharacterSelect"',
        'id="livePersonaSelfAddress"',
        'id="livePersonaAvatarUrl"',
        'id="livePersonaAvatarSelect"',
        'id="livePersonaAvatarFile"',
        'id="uploadAvatarButton"',
        'id="livePersonaChatBackgroundColor"',
        'id="livePersonaChatAccentColor"',
        'id="roleEditorNav"',
        'data-role-section="basic"',
        'data-role-section="dialogue"',
        'data-role-section="voice"',
        'id="roleBasicPanel"',
        'id="roleDialoguePanel"',
        'id="roleVoicePanel"',
        'id="livePersonaSystemPrompt"',
        'id="livePersonaOpeningIntro"',
        'id="livePersonaAddressingFields"',
        'id="livePersonaReplyRules"',
        'id="liveTtsSourceRoot"',
        'id="liveTtsSourcePreset"',
        'id="liveTtsEnabled"',
        'id="liveTtsRefAudioPath"',
        'id="liveTtsPromptText"',
        'id="liveTtsTextLang"',
        'id="liveTtsPromptLang"',
        'id="liveTtsSpeedFactor"',
        'id="liveTtsMediaType"',
        'id="livePersonaSaveState"',
    ]
    for control in expected_controls:
        assert control in studio_html

    expected_labels = [
        "角色設定",
        "重新讀取企劃角色",
        "基本設定",
        "對話設定",
        "聲音 TTS",
        "固定自稱",
        "頭像 URL",
        "本地頭像",
        "上傳並套用",
        "對話背景色",
        "對話強調色",
        "直播專用 prompt",
        "開場自我介紹",
        "角色互稱",
        "直播回覆規則",
        "GPT-SoVITS 聲音設定",
        "快速選擇聲音",
        "範例語音路徑",
        "範例語音 transcript",
        "輸出文字語言",
        "範例語音語言",
        "速度",
        "音訊格式",
        "自動儲存",
    ]
    for label in expected_labels:
        assert label in studio_html

    assert "直播時使用下方設定覆寫角色直播 prompt。" not in studio_html
    assert "固定覆寫" not in studio_html
    assert "Prompt 覆寫模式" not in studio_html
    assert "append：附加在原角色 prompt 後" not in studio_html
    assert "Memoria 角色綁定" not in studio_html
    assert "啟用直播角色設定" not in studio_html
    assert 'id="livePersonaPromptMode"' not in studio_html
    assert 'id="livePersonaEnabled"' not in studio_html
    assert 'class="role-toggle"' not in studio_html
    assert 'id="roleDialoguePanel" class="role-editor-panel" role="tabpanel" hidden' in studio_html
    assert 'id="roleVoicePanel" class="role-editor-panel" role="tabpanel" hidden' in studio_html
    assert "function updateRoleBindingState()" in studio_js
    assert "function openRoleSettings()" in studio_js
    assert "function switchRoleEditorSection(" in studio_js
    assert "function fillLivePersonaFormForSelectedRole()" in studio_js
    assert "function autoSaveLivePersonaSettings(" in studio_js
    assert "debounceAutoSave(" in studio_js
    assert 'api(`/persona-overlays/${encodeURIComponent(roleId)}`' in studio_js
    assert 'mode: "replace"' in studio_js
    assert "className = \"role-toggle\"" not in studio_js
    assert "scheduleLivePersonaDraftSave(roleId, currentDraft, \"角色綁定\")" not in studio_js
    assert "TTS 啟用時需要範例語音路徑與 transcript" in studio_js
    assert "rolePersonaDrafts" in studio_js
    assert "avatar_url: draft.avatarUrl" in studio_js
    assert "chat_background_color: draft.chatBackgroundColor" in studio_js
    assert "chat_accent_color: draft.chatAccentColor" in studio_js
    assert "loadAvatarAssets()" in studio_js
    assert "uploadLocalAvatar()" in studio_js
    assert 'api("/studio/avatar-assets"' in studio_js
    assert "儲存角色設定" not in studio_html
    assert 'id="saveLivePersonaSettingsButton"' not in studio_html


def test_studio_role_visual_settings_drive_chat_rendering():
    studio_html = _studio_source()
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    assert 'type="color"' in studio_html
    assert "avatarUrl" in studio_js
    assert "chatBackgroundColor" in studio_js
    assert "chatAccentColor" in studio_js
    assert "rolePersonaDrafts[roleId]?.chatBackgroundColor" in studio_js
    assert "rolePersonaDrafts[roleId]?.chatAccentColor" in studio_js
    assert "rolePersonaDrafts[roleId]?.avatarUrl" in studio_js
    assert "manualPaletteForMessage(" in studio_js


def test_studio_role_avatar_upload_and_selection_are_wired():
    studio_html = _studio_source()
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    assert 'id="livePersonaAvatarFile" type="file"' in studio_html
    assert 'accept="image/png,image/jpeg,image/webp,image/gif"' in studio_html
    assert 'id="livePersonaAvatarSelect"' in studio_html
    assert "建議使用正方形圖片" in studio_html
    assert "const maxAvatarBytes = 2 * 1024 * 1024" in studio_js
    assert "readAvatarFileAsDataUrl(" in studio_js
    assert "FileReader" in studio_js
    assert "renderAvatarAssetOptions(" in studio_js
    assert "selected.url" in studio_js
    assert "await loadAvatarAssets(selected.url)" in studio_js
    assert "applyAvatarUrl(selected.url, \"角色頭像\")" in studio_js
    assert "function saveLivePersonaOverlayNow(" in studio_js
    assert "markAutoSaveState(livePersonaSaveState, source)" in studio_js


def test_studio_role_settings_are_loaded_from_selected_episode_plan():
    studio_html = _studio_source()
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    assert "host_sakura" not in studio_html
    assert "cohost_alan" not in studio_html
    assert "小櫻" not in studio_html
    assert "艾倫" not in studio_html
    assert "const rolePersonaDrafts = {};" in studio_js
    assert "structuredClone(rolePersonaDrafts.host_sakura)" not in studio_js
    assert "async function loadEpisodePlanCharacters(" in studio_js
    assert 'api(`/episode-plans/${encodeURIComponent(planId)}/characters`)' in studio_js
    assert "function renderPlanCharacters(" in studio_js
    assert "state.planCharacters" in studio_js
    assert "loadEpisodePlanCharacters(planSelect.value)" in studio_js


def test_studio_role_addressing_uses_structured_target_fields():
    studio_html = _studio_source()
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    assert 'id="livePersonaAddressingFields"' in studio_html
    assert 'id="livePersonaRelationshipNotes"' not in studio_html
    assert "relationshipNotes" not in studio_js
    assert "function renderLivePersonaAddressingFields(" in studio_js
    assert "function readLivePersonaAddressingFields(" in studio_js
    assert "function defaultAddressingForRole(" in studio_js
    assert "addressing: draft.addressing || {}" in studio_js
    assert "{ notes:" not in studio_js
    assert "overlay.addressing?.notes" not in studio_js
    assert 'className = "addressing-row"' in studio_js
    assert 'input.dataset.targetCharacterId = target.character_id' in studio_js


def test_studio_exposes_system_settings_with_connector_and_auth():
    studio_html = _studio_source()
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    expected_controls = [
        'data-tab="system"',
        'id="systemPanel"',
        'id="liveSettingsSummary"',
        'id="injectIntervalSeconds"',
        'id="injectMinIntervalSeconds"',
        'id="minPendingComments"',
        'id="pendingForceLimit"',
        'id="autoInjectPendingEnabled"',
        'id="plannedDurationMinutes"',
        'id="autoFinalizeAtLimit"',
        'id="thankUnhandledSuperChats"',
        'id="clearRuntimeSessionAfterSummary"',
        'id="postPlanFreeTalkEnabled"',
        'id="postPlanFreeTalkMinutes"',
        'id="superChatCooldownSeconds"',
        'id="superChatBatchLimit"',
        'id="safeSearchEnabled"',
        'id="presentationQueueEnabled"',
        'id="ttsEnabled"',
        'id="systemAutoSaveState"',
        'id="connectorStatusBadge"',
        'id="connectorApiKeyInput"',
        'id="memoriaAuthState"',
        'id="memoriaBaseUrl"',
        'id="memoriaUsername"',
        'id="memoriaPassword"',
        'id="memoriaAdminBypass"',
        'id="testMemoriaAuthButton"',
    ]
    for control in expected_controls:
        assert control in studio_html

    expected_labels = [
        "系統設定",
        "YouTube Connector",
        "YouTube Data API Key",
        "MemoriaCore Auth",
        "LiveEpisodePlan 後續流程",
        "Plan 結束後進入無導播雜談",
        "雜談保護上限分鐘",
        "留言注入節奏",
        "直播時長與收尾",
        "Super Chat 處理",
        "安全與補充",
        "輸出管線",
        "Live Presentation Queue",
        "GPT-SoVITS TTS",
        "自動儲存",
    ]
    for label in expected_labels:
        assert label in studio_html

    assert "Connector 名稱" not in studio_html
    assert 'id="connectorDisplayName"' not in studio_html
    assert 'id="saveConnectorButton"' not in studio_html
    assert 'id="saveMemoriaConfigButton"' not in studio_html
    assert "function updateLiveSettingsSummary()" in studio_js
    assert "function bindLiveSettingsControls()" in studio_js
    assert "function applySystemAutoSaveState(" in studio_js
    assert "function testMemoriaAuthSettings()" in studio_js
    assert "function initStudioApi()" in studio_js
    assert 'api("/studio/settings"' in studio_js
    assert 'method: "PATCH"' in studio_js
    assert "儲存中" in studio_js
    assert "已自動儲存" in studio_js
    assert "儲存失敗" in studio_js


def test_studio_frontend_uses_dedicated_api_without_legacy_imports():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    forbidden_imports = [
        "session-control.js",
        "control.js",
        "events-control.js",
        "summary-director-control.js",
        "topic-pack",
    ]
    for forbidden in forbidden_imports:
        assert forbidden not in studio_js

    assert "function loadStudioSettings()" in studio_js
    assert "function collectTestSettings()" in studio_js
    assert "function collectLiveDefaults()" in studio_js
    assert "function scheduleTestSettingsSave(" in studio_js
    assert "function scheduleSystemSettingsSave(" in studio_js


def test_studio_keeps_free_talk_mode_out_of_role_settings():
    studio_html = _studio_source()

    assert "直播模式" not in studio_html
    assert 'id="livePersonaMode"' not in studio_html
    assert 'id="livePersonaPromptMode"' not in studio_html
    assert 'id="postPlanFreeTalkEnabled"' in studio_html
    assert studio_html.index("Plan 結束後進入無導播雜談") > studio_html.index('id="systemPanel"')


def test_studio_exposes_free_talk_topic_library_checklist():
    studio_html = _studio_source()
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    assert "雜談話題庫" in studio_html
    assert "runtime/YouTubeBridge/freeTalkTopics/" in studio_html
    assert "全部話題庫" in studio_html
    assert "重新載入話題庫" in studio_html
    assert 'api(`/studio/free-talk-topics?episode_plan_id=${encodeURIComponent(planSelect.value || "")}`)' in studio_js
    assert "post_plan_free_talk_topic_pack_ids" in studio_js
    assert "function selectedFreeTalkTopicPackIds()" in studio_js


def test_studio_free_talk_topic_selection_restores_saved_live_defaults():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")
    apply_index = studio_js.index("function applyLiveDefaults(settings = {})")
    apply_body = studio_js[apply_index:studio_js.index("function applyTtsSources", apply_index)]
    render_index = studio_js.index("function renderFreeTalkTopicChecklist(result = {})")
    render_body = studio_js[render_index:studio_js.index("async function loadFreeTalkTopics", render_index)]

    assert "settings.post_plan_free_talk_topic_pack_ids" in apply_body
    assert "state.savedFreeTalkTopicPackIds" in apply_body
    assert "setSelectedFreeTalkTopicPackIds(state.savedFreeTalkTopicPackIds)" in apply_body
    assert "state.savedFreeTalkTopicPackIds !== null" in render_body
    assert "state.selectedFreeTalkTopicPackIds = [...allPackIds]" in render_body


def test_studio_free_talk_topic_selection_uses_configured_presence_flag():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")
    apply_index = studio_js.index("function applyLiveDefaults(settings = {})")
    apply_body = studio_js[apply_index:studio_js.index("function applyTtsSources", apply_index)]
    collect_index = studio_js.index("function collectLiveDefaults()")
    collect_body = studio_js[collect_index:studio_js.index("function collectConnectorSettings", collect_index)]
    payload_index = studio_js.index("function studioLiveSessionPayload()")
    payload_body = studio_js[payload_index:studio_js.index("async function startStudioDirector", payload_index)]

    assert "settings.post_plan_free_talk_topic_pack_ids_configured === true" in apply_body
    assert "state.savedFreeTalkTopicPackIds = null" in apply_body
    assert "Array.isArray(settings.post_plan_free_talk_topic_pack_ids)" in apply_body
    assert "state.savedFreeTalkTopicPackIds !== null" in collect_body
    assert "payload.post_plan_free_talk_topic_pack_ids = selectedFreeTalkTopicPackIds()" in collect_body
    assert "state.freeTalkTopicSelectionInitialized" in payload_body


def test_studio_free_talk_closing_batch_settings_are_in_payloads():
    studio_html = _studio_source()
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")
    apply_index = studio_js.index("function applyLiveDefaults(settings = {})")
    apply_body = studio_js[apply_index:studio_js.index("function applyTtsSources", apply_index)]
    collect_index = studio_js.index("function collectLiveDefaults()")
    collect_body = studio_js[collect_index:studio_js.index("function collectConnectorSettings", collect_index)]
    payload_index = studio_js.index("function studioLiveSessionPayload()")
    payload_body = studio_js[payload_index:studio_js.index("async function startStudioDirector", payload_index)]
    controls_index = studio_js.index("const liveSettingControls = [")
    controls_body = studio_js[controls_index:studio_js.index("].map((id) => $(id));", controls_index)]
    binding_index = studio_js.index('"connectorApiKeyInput"')
    binding_body = studio_js[binding_index:studio_js.index("].forEach((id) => {", binding_index)]

    expected_controls = [
        'id="freeTalkClosingTargetBatches"',
        'id="freeTalkClosingMinBatchSize"',
        'id="freeTalkClosingMaxBatchSize"',
        'id="freeTalkClosingTimeLimitSeconds"',
    ]
    for control in expected_controls:
        assert control in studio_html

    expected_labels = [
        "雜談收尾目標批次",
        "雜談收尾每批最少留言",
        "雜談收尾每批最多留言",
        "雜談收尾保護秒數",
    ]
    for label in expected_labels:
        assert label in studio_html

    expected_fields = [
        ("freeTalkClosingTargetBatches", "free_talk_closing_target_batches", 10),
        ("freeTalkClosingMinBatchSize", "free_talk_closing_min_batch_size", 5),
        ("freeTalkClosingMaxBatchSize", "free_talk_closing_max_batch_size", 30),
        ("freeTalkClosingTimeLimitSeconds", "free_talk_closing_time_limit_seconds", 300),
    ]
    for control_id, field_name, fallback in expected_fields:
        assert f'"{control_id}"' in controls_body
        assert f'"{control_id}"' in binding_body
        assert f'setInputValue("{control_id}", settings.{field_name} ?? {fallback})' in apply_body
        assert f'{field_name}: readPositiveNumber($("{control_id}"), {fallback})' in collect_body
        assert f'{field_name}: liveDefaults.{field_name}' in payload_body


def test_studio_phase_status_mentions_free_talk_closing_counts():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")
    phase_index = studio_js.index("function phaseSummaryText(session)")
    phase_body = studio_js[phase_index:studio_js.index("function applySessionSnapshot(session)", phase_index)]

    assert "function freeTalkClosingText(" in studio_js
    assert "freeTalkClosingText(metadata)" in phase_body
    assert "free_talk_audience_closing" in studio_js
    assert "eligible_processed_count" in studio_js
    assert "closing_skipped_count" in studio_js
    assert "low_signal_skipped_count" in studio_js
    assert "雜談收尾" in studio_js
    assert "低訊號" in studio_js


def test_studio_app_wires_episode_plan_session_lifecycle():
    studio_html = _studio_source()
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    expected_controls = [
        'id="planStatusBadge"',
        'id="planStateText"',
        'id="roleBindingState"',
        'id="refreshRolesButton"',
        'id="sourceDetectionState"',
        'id="detectedVideoId"',
        'id="detectedLiveChatId"',
        'id="detectSourceButton"',
        'id="manualVideoInput"',
        'id="preflightChecklist"',
        'id="preflightPlan"',
        'id="preflightSource"',
        'id="preflightRoles"',
        'id="preflightSettings"',
        'id="startBlockReason"',
    ]
    for control in expected_controls:
        assert control in studio_html

    expected_labels = [
        "直播來源",
        "OBS",
        "手動/測試模式",
        "video_id",
        "live_chat_id",
        "開播前檢查",
        "手動指定",
    ]
    for label in expected_labels:
        assert label in studio_html

    expected_functions = [
        "async function loadEpisodePlans(",
        "async function refreshStudioSession(",
        "function studioLiveSessionPayload(",
        "async function startLive(",
        "async function stopLive(",
        "function updatePlanState()",
        "function updateRoleBindingState()",
        "function updatePreflightChecklist()",
        "function applyStartButtonState()",
    ]
    for fn in expected_functions:
        assert fn in studio_js

    assert 'api("/episode-plans/sync-local?max_files=200", { method: "POST" })' in studio_js
    assert 'api("/episode-plans?limit=100")' in studio_js
    assert 'api("/sessions/current/start", {' in studio_js
    assert 'api(`/sessions/${encodeURIComponent(sessionId)}/director/start`, {' in studio_js
    assert 'api(`/sessions/${encodeURIComponent(sessionId)}/phase/finalize`, {' in studio_js
    assert 'body: { reason: "operator_finalize", background: true }' in studio_js
    assert 'appendLog("INFO", "節目收尾流程已送出");' in studio_js
    assert 'api(`/sessions/${encodeURIComponent(state.sessionId)}/chat-preview?limit=120`)' in studio_js
    assert 'api(`/sessions/${encodeURIComponent(state.sessionId)}/recent?limit=120`)' in studio_js
    assert "new EventSource(`/sessions/${encodeURIComponent(sessionId)}/events`)" in studio_js
    assert 'presentation_enabled: liveDefaults.presentation_queue_enabled' in studio_js
    assert 'tts_enabled: liveDefaults.tts_enabled' in studio_js
    assert 'presentation_enabled: false' not in studio_js
    assert 'tts_enabled: false' not in studio_js
    assert 'character_ids: []' in studio_js
    assert 'auto_inject: liveDefaults.auto_inject_pending_enabled' in studio_js
    assert 'min_pending_events: liveDefaults.min_pending_comments' in studio_js
    assert 'max_pending_events: liveDefaults.pending_force_limit' in studio_js
    assert 'research_enabled: liveDefaults.safe_search_enabled' in studio_js
    assert 'api(`/sessions/${encodeURIComponent(sessionId)}/stop`, {' not in studio_js
    assert '"/finalize"' not in studio_js
    assert "function simulateSourceDetection(" not in studio_js
    assert "function completeSourceDetection(" not in studio_js
    assert "function makeSourceToken(" not in studio_js


def test_studio_conversation_has_no_manual_input_surface():
    studio_html = _studio_source()
    studio_css = (Path(server_module.UI_ASSETS_ROOT) / "studio.css").read_text(encoding="utf-8")

    removed_markup = [
        'class="conversation-footer"',
        'class="locked-input"',
        'class="send-button"',
        "無法手動輸入",
    ]
    for text in removed_markup:
        assert text not in studio_html

    removed_styles = [
        ".conversation-footer",
        ".locked-input",
        ".send-button",
    ]
    for selector in removed_styles:
        assert selector not in studio_css


def test_studio_topbar_omits_placeholder_navigation_controls():
    studio_html = _studio_source()
    studio_css = (Path(server_module.UI_ASSETS_ROOT) / "studio.css").read_text(encoding="utf-8")

    removed_controls = [
        'aria-label="開啟選單"',
        'class="icon-button"',
        '<button class="secondary small" type="button">設定</button>',
    ]
    for control in removed_controls:
        assert control not in studio_html

    assert ".icon-button" not in studio_css


def test_studio_conversation_is_newest_first_and_live_events_default_hidden():
    studio_html = _studio_source()
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    assert 'class="conversation-feed newest-first"' in studio_html
    assert 'class="chat-line event"' not in studio_html
    assert "YouTube Live 留言注入" not in studio_html
    assert 'class="chat-line viewer"' not in studio_html
    assert '<input id="showLiveEventsEnabled" type="checkbox">' in studio_html
    assert "尚未開始直播" in studio_html

    assert "function shouldShowLiveEvents()" in studio_js
    assert "function appendLiveEventGroup(" in studio_js
    assert "function renderChatPreviewMessages(" in studio_js
    assert "function subscribeSessionEvents(" in studio_js
    assert "function appendChatPreviewMessage(" in studio_js
    assert "直播事件顯示已關閉" in studio_js
    assert "feed.prepend(row)" in studio_js
    assert 'appendMessage("viewer"' not in studio_js
    assert "appendQueuedComment" not in studio_js
    assert "10:20:28" not in studio_html
    assert "10:20:15" not in studio_html


def test_studio_conversation_refresh_preserves_locally_visible_messages():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    assert "const CHAT_PREVIEW_VISIBLE_LIMIT = 120;" in studio_js
    assert "visibleMessages: new Map()" in studio_js
    assert "function previewMessageKey(" in studio_js
    assert "function rememberVisibleMessage(" in studio_js
    assert "function pruneVisibleMessages(" in studio_js
    assert "const merged = mergePreviewMessages(Array.from(state.visibleMessages.values()), visible).slice(-CHAT_PREVIEW_VISIBLE_LIMIT);" in studio_js
    assert "state.visibleMessages.clear();" in studio_js


def test_studio_live_events_are_stable_timeline_items_not_refresh_prepend_rows():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    assert "visibleLiveEvents: new Map()" in studio_js
    assert "function liveEventKey(" in studio_js
    assert "function rememberVisibleLiveEvent(" in studio_js
    assert "function renderConversationTimeline(" in studio_js

    refresh_body = studio_js[
        studio_js.index("async function refreshConversation()"):
        studio_js.index("function scheduleConversationRefresh", studio_js.index("async function refreshConversation()"))
    ]
    assert "events.forEach(rememberVisibleLiveEvent);" in refresh_body
    assert "renderConversationTimeline();" in refresh_body
    assert "appendLiveEventGroup(`YouTube Live 留言注入：${events.length} 則`, events);" not in refresh_body

    clear_body = studio_js[
        studio_js.index("function clearConversation()"):
        studio_js.index("async function regenerateSummary", studio_js.index("function clearConversation()"))
    ]
    assert "state.visibleLiveEvents.clear();" in clear_body


def test_studio_closing_state_disables_restart_and_clear_discards_local_visible_messages():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    assert "function sessionIsClosing(" in studio_js
    assert "function sessionFinalizeFailed(" in studio_js
    start_readiness = studio_js[
        studio_js.index("function startReadiness()"):
        studio_js.index("function updateCheckItem", studio_js.index("function startReadiness()"))
    ]
    assert 'state.sourceStatus !== "closing"' in start_readiness
    assert 'state.sourceStatus !== "closing_failed"' in start_readiness

    button_state = studio_js[
        studio_js.index("function applyStartButtonState()"):
        studio_js.index("function updatePreflightChecklist", studio_js.index("function applyStartButtonState()"))
    ]
    assert 'state.sourceStatus === "closing"' in button_state
    assert 'state.sourceStatus === "closing_failed"' in button_state
    assert 'stopButton.disabled = (!state.live && state.sourceStatus !== "closing_failed") || state.sourceStatus === "closing";' in button_state
    assert 'stopButton.textContent = state.sourceStatus === "closing_failed" ? "重試收尾" : "收尾";' in button_state

    clear_body = studio_js[
        studio_js.index("function clearConversation()"):
        studio_js.index("async function regenerateSummary", studio_js.index("function clearConversation()"))
    ]
    assert "state.visibleMessages.clear();" in clear_body


def test_studio_keeps_event_stream_open_while_closing():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    assert "function sessionShouldReceiveEvents(" in studio_js
    helper_body = studio_js[
        studio_js.index("function sessionShouldReceiveEvents("):
        studio_js.index("function phaseSummaryStatus", studio_js.index("function sessionShouldReceiveEvents("))
    ]
    assert "sessionIsRunning(session) || sessionIsClosing(session)" in helper_body

    refresh_body = studio_js[
        studio_js.index("async function refreshStudioSession()"):
        studio_js.index("async function startLive()", studio_js.index("async function refreshStudioSession()"))
    ]
    assert "if (sessionShouldReceiveEvents(selected))" in refresh_body
    assert '"直播收尾中，等待最後訊息完成。"' in refresh_body


def test_studio_graceful_closing_does_not_route_status_to_presentation_interrupt():
    source = Path("YouTubeBridge/static/ui/studio.js").read_text(encoding="utf-8")
    interrupt_branch = source[source.index('if (payload.type === "interrupt_requested")'):]
    interrupt_branch = interrupt_branch[:interrupt_branch.index('if (payload.type === "interaction_interrupted")')]
    assert "handlePresentationInterrupt(payload)" in interrupt_branch
    status_branch = source[source.index('if (payload.type === "status")'):]
    status_branch = status_branch[:status_branch.index('if (payload.type === "presentation_debug"')]
    assert "handlePresentationInterrupt" not in status_branch
    assert "presentation/current/skip" not in status_branch


def test_studio_graceful_closing_preserves_current_presentation_player():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")
    snapshot_body = studio_js[
        studio_js.index("function applySessionSnapshot(session)"):
        studio_js.index("function studioLiveSessionPayload", studio_js.index("function applySessionSnapshot(session)"))
    ]

    assert "const presentationPlayerActive =" in snapshot_body
    assert "const reachedTerminalSessionState = !state.live && !closing && !closingFailed;" in snapshot_body
    assert "if (reachedTerminalSessionState && (wasLive || presentationPlayerActive)) {" in snapshot_body
    assert "if (wasLive && !state.live) {" not in snapshot_body


def test_studio_renders_live_events_only_from_recent_aggregate_not_single_sse_or_system_event():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    assert 'String(message?.role || "") !== "system_event"' in studio_js
    assert "function scheduleConversationRefresh(" in studio_js
    assert 'if (payload.type === "youtube_live_event" && payload.event) {' in studio_js
    assert 'scheduleConversationRefresh("直播事件");' in studio_js
    assert 'appendLiveEventGroup("YouTube Live 留言注入：1 則", [eventToLiveEventItem(payload.event)])' not in studio_js


def test_studio_conversation_clear_uses_empty_state_not_character_message():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    assert 'appendMessage("host", "小櫻", "主持人", "對話區已清空' not in studio_js
    assert 'renderConversationEmpty("對話區已清空，等待新的直播內容。")' in studio_js


def test_studio_conversation_time_is_footer_and_role_visuals_are_dynamic():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")
    studio_css = (Path(server_module.UI_ASSETS_ROOT) / "studio.css").read_text(encoding="utf-8")

    assert "function rolePalette(" in studio_js
    assert "function avatarImageUrl(" in studio_js
    assert "function applyChatRoleVisuals(" in studio_js
    assert 'row.style.setProperty("--chat-bg"' in studio_js
    assert 'row.style.setProperty("--chat-accent"' in studio_js
    assert "mark.append(image)" in studio_js
    assert "copy.append(title, body, time)" in studio_js
    assert 'time.className = "chat-time"' in studio_js
    assert "grid-template-columns: 38px minmax(0, 1fr);" in studio_css
    assert ".chat-time" in studio_css
    assert "color: #9aa6b6;" in studio_css
    assert "text-align: right;" in studio_css
    assert "background: var(--chat-bg" in studio_css
    assert "box-shadow: inset 4px 0 0 var(--chat-accent" in studio_css


def test_studio_start_resets_previous_conversation_before_new_session_request():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")
    start_index = studio_js.index("async function startLive()")
    request_index = studio_js.index('api("/sessions/current/start"', start_index)
    reset_index = studio_js.index("resetConversationForNewSession();", start_index)

    assert "function resetConversationForNewSession(" in studio_js
    assert reset_index < request_index
    assert "unsubscribeSessionEvents();" in studio_js
    assert 'renderConversationEmpty("正在建立新的 Live Session，等待後端產生 AI 對話。")' in studio_js


def test_studio_refresh_subscribes_running_and_closing_session_events():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")
    refresh_index = studio_js.index("async function refreshStudioSession()")
    refresh_body = studio_js[refresh_index:studio_js.index("async function startLive()", refresh_index)]

    assert "if (sessionShouldReceiveEvents(selected))" in refresh_body
    assert "subscribeSessionEvents(selected.session_id);" in refresh_body
    assert "unsubscribeSessionEvents();" in refresh_body


def test_studio_presentation_tts_events_and_lifecycle_are_wired():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    subscribe_index = studio_js.index("function subscribeSessionEvents(sessionId)")
    subscribe_body = studio_js[subscribe_index:studio_js.index("function sessionIsRunning", subscribe_index)]
    assert 'if (payload.type === "presentation_item_ready" && payload.item) {' in subscribe_body
    assert "enqueuePresentationItem(payload.item);" in subscribe_body
    presentation_branch = subscribe_body[
        subscribe_body.index('if (payload.type === "presentation_item_ready" && payload.item) {'):
        subscribe_body.index('if (payload.type === "interrupt_requested") {')
    ]
    assert "enqueuePresentationItem(payload.item);" in presentation_branch
    assert "return;" in presentation_branch
    assert presentation_branch.index("enqueuePresentationItem(payload.item);") < presentation_branch.index("return;")
    assert 'if (payload.type === "interrupt_requested") {' in subscribe_body
    assert "handlePresentationInterrupt(payload)" in subscribe_body
    assert 'if (payload.type === "interaction_interrupted") {' in subscribe_body
    interrupted_branch = subscribe_body[
        subscribe_body.index('if (payload.type === "interaction_interrupted") {'):
        subscribe_body.index('if (["interaction_completed", "super_chat_batch_injected"].includes(payload.type)) {')
    ]
    assert 'appendLog("DEBUG", "互動已中斷，保留已顯示對話");' in interrupted_branch
    assert 'scheduleConversationRefresh("直播打斷");' not in interrupted_branch
    assert '["interaction_completed", "presentation_item_ready", "super_chat_batch_injected"]' not in subscribe_body
    assert '["interaction_completed", "super_chat_batch_injected"]' in subscribe_body

    reset_index = studio_js.index("function resetConversationForNewSession()")
    reset_body = studio_js[reset_index:studio_js.index("function chatPreviewKind", reset_index)]
    assert 'resetPresentationPlayer({ statusText: "建立新場次" });' in reset_body

    session_index = studio_js.index("function applySessionSnapshot(session)")
    session_body = studio_js[session_index:studio_js.index("function studioLiveSessionPayload", session_index)]
    assert "const wasLive = state.live;" in session_body
    assert 'resetPresentationPlayer({ statusText: "已停止" });' in session_body
    stop_condition_index = session_body.index("if (reachedTerminalSessionState && (wasLive || presentationPlayerActive)) {")
    stop_reset_index = session_body.index('resetPresentationPlayer({ statusText: "已停止" });')
    assert stop_condition_index < stop_reset_index
    stop_reset_branch = session_body[
        stop_condition_index:stop_reset_index + len('resetPresentationPlayer({ statusText: "已停止" });')
    ]
    assert 'resetPresentationPlayer({ statusText: "已停止" });' in stop_reset_branch

    binding_index = studio_js.index('"clearConversation"')
    binding_body = studio_js[binding_index:studio_js.index('"regenerateSummary"', binding_index)]
    assert '$("enablePresentationAudio").addEventListener("click", () => {' in binding_body
    assert '$("skipPresentation").addEventListener("click", () => {' in binding_body
    enable_audio_handler = binding_body[
        binding_body.index('$("enablePresentationAudio").addEventListener("click", () => {'):
        binding_body.index('$("skipPresentation").addEventListener("click", () => {')
    ]
    skip_handler = binding_body[
        binding_body.index('$("skipPresentation").addEventListener("click", () => {'):
    ]
    assert "retryCurrentPresentationAudio()" in enable_audio_handler
    assert "skipCurrentPresentation()" in skip_handler


def test_studio_route_is_loopback_only():
    from server_security import LOOPBACK_ONLY_PATHS

    assert "/studio" in LOOPBACK_ONLY_PATHS
    assert "/studio/" in LOOPBACK_ONLY_PATHS
