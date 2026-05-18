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

def test_live_page_propagates_requested_session_id_to_live_chat_frame():
    live_html = (Path(server_module.STATIC_ROOT) / "live.html").read_text(encoding="utf-8")

    assert 'id="liveChatFrame"' in live_html
    assert "URLSearchParams(location.search)" in live_html
    assert "session_id" in live_html


def test_session_routes_expose_presentation_endpoints():
    source = (BRIDGE_ROOT / "server_routes" / "sessions.py").read_text(encoding="utf-8")

    assert '@router.post("/sessions/{session_id}/presentation/{item_id}/ack")' in source
    assert '@router.get("/sessions/{session_id}/presentation/{item_id}/audio")' in source
    assert '@router.post("/sessions/{session_id}/presentation/current/skip")' in source
    assert "InstrumentedSseResponse" in source
    assert "return InstrumentedSseResponse(" in source
    assert "StreamingResponse(gen()" not in source
    assert '"_sse_yield_at": datetime.now().isoformat()' in source
    assert "list_presented_messages" in source


def test_control_ui_honors_requested_session_id_on_initial_load():
    index_html = _control_ui_source()

    assert "function requestedSessionIdFromUrl()" in index_html
    assert "loadSessions(requestedSessionIdFromUrl())" in index_html


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


def test_control_ui_exposes_live_presentation_tts_session_controls():
    source = _control_ui_source()

    assert 'id="presentationEnabled"' in source
    assert 'id="ttsEnabled"' in source
    assert 'id="presentationAckTimeout"' in source
    assert "presentation_enabled: $(\"presentationEnabled\").checked" in source
    assert "tts_enabled: $(\"ttsEnabled\").checked" in source
    assert "presentation_ack_timeout_seconds: Number($(\"presentationAckTimeout\").value || 120)" in source
    assert '$(\"presentationEnabled\").checked = !!session.presentation_enabled;' in source
    assert '$(\"ttsEnabled\").checked = !!session.tts_enabled;' in source


def test_live_session_config_accepts_presentation_tts_settings():
    config = server_module.LiveSessionConfig(
        connector_id="youtube-main",
        presentation_enabled=True,
        tts_enabled=True,
        presentation_ack_timeout_seconds=9,
    )

    assert config.presentation_enabled is True
    assert config.tts_enabled is True
    assert config.tts_provider == "gpt_sovits"
    assert config.presentation_ack_timeout_seconds == 9


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


def test_live_session_moves_legacy_director_knobs_into_legacy_block():
    index_html = _control_ui_source()
    live_session_block = index_html[
        index_html.index('<div id="liveSessionPane"'):
        index_html.index('<div id="eventsPane"')
    ]
    legacy_start = live_session_block.index('id="legacyDirectorFields"')
    legacy_block = live_session_block[
        legacy_start:
        live_session_block.index("</details>", legacy_start)
    ]
    primary_director_grid = live_session_block[
        live_session_block.index('<div class="grid">'):
        legacy_start
    ]

    legacy_field_ids = (
        "directorIdle",
        "directorAnchorEveryTurns",
        "directorGroupTurnLimit",
        "directorGuidance",
    )
    for field_id in legacy_field_ids:
        assert f'id="{field_id}"' in legacy_block
        assert f'id="{field_id}"' not in primary_director_grid

    for field_id in ("directorDialogueExpansionEnabled",):
        assert f'id="{field_id}"' in primary_director_grid
        assert f'id="{field_id}"' not in legacy_block
    assert 'id="episodePlanHandoffGapSeconds"' not in live_session_block
    assert 'id="episodePlanTurnGapSeconds"' not in live_session_block
    assert 'id="directorMaxChatBatches"' in live_session_block
    assert 'id="directorMaxChatBatches"' not in legacy_block


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
    assert left_panel.index('id="characterLimitState"') > left_panel.index('id="legacyDirectorFields"')
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


def test_live_session_automation_options_have_clear_labels_and_tooltips():
    index_html = _control_ui_source()
    live_session_block = index_html[
        index_html.index('<div id="liveSessionPane"'):
        index_html.index('<div id="eventsPane"')
    ]

    expected_options = {
        "autoInject": ("自動注入待處理留言", "每隔一段時間把 pending 留言送進角色回應流程"),
        "autoFinalize": ("到達時間上限後自動收尾", "未使用 EpisodePlan 時依預計分鐘收尾；使用 EpisodePlan 時以企劃完成為主，分鐘只作為保護上限。"),
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
        "maxPending": "單次自動注入最多帶入的 pending 留言數；超出者保留或延後，不會阻止 EpisodePlan 主線。",
        "plannedDuration": "未使用 EpisodePlan 時代表預計直播長度；使用 EpisodePlan 時只是保護上限，企劃完成會優先結束。",
        "scInterruptCooldown": "Super Chat 打斷正在進行的回應後，下一次允許再次打斷前必須等待的秒數。",
        "maxScPerBatch": "每次注入最多帶入幾則 Super Chat；系統會優先選較高 tier，再依留言順序處理。",
        "sessionTopicPackSelect": "本場直播啟動或更新時要綁定的 Topic Pack；直播中只讀取已綁定資料，不執行 Fact Card 生成或匯入。",
        "directorDialogueExpansionEnabled": "開啟時，導播推話題後可讓角色互相接話直到導播回合上限；關閉時，每次導播指令只讓被指定的一位角色回應。",
        "directorGroupTurnLimit": "導播每次推話題時允許角色連續互相接話的回合上限，避免一次導播指令延伸過久。",
        "directorMaxChatBatches": "連續處理幾批聊天室留言後，導播會強制把話題拉回本場主軸，避免直播被留言帶偏。",
        "directorAudienceInterruptCooldown": "一次 audience interrupt 後，下一批普通觀眾留言至少等待的秒數；Super Chat 仍另外受 SC 冷卻限制。",
        "directorMaxAudienceBatchesPerPlannedTurn": "每個 planned turn 之間最多允許幾批聊天室插入；超出者延後，不會阻止下一個企劃 turn。",
        "directorIdle": "角色與互動停止超過這個秒數後，導播會嘗試推進下一段話題或讓角色續話。",
        "directorAnchorEveryTurns": "同一個導播話題最多連續推進幾輪 AI 對話；達到後會釋放回合限制，讓下一次導播決策可以切換或重新錨定話題。",
        "directorGuidance": "本場直播的高層方向，只提供給導播與角色作為內部參考，不會直接顯示在 live chat。",
        "hostInteractionRules": "本場直播主持節奏與角色分工，只給導播與角色看；可貼入雙主持互動規則，不會寫入角色 persona。",
        "programSegmentTurns": "同一段落建議維持幾輪導播推進後再切到下一段；不影響單次導播回合上限。",
    }

    for field_id, tooltip in expected_fields.items():
        assert f'id="{field_id}"' in live_session_block
        assert f'data-tooltip="{tooltip}"' in live_session_block
        assert f'aria-label="{tooltip}"' in live_session_block

    for label_text, field_id in [
        ("YouTube video_id 或 URL", "videoId"),
        ("角色", "characterSelect"),
        ("注入間隔秒數", "injectInterval"),
        ("預計/保護上限分鐘", "plannedDuration"),
        ("動態注入最短秒數", "injectMinIntervalSeconds"),
        ("話題資料包", "sessionTopicPackSelect"),
        ("單一話題持續回合數", "directorAnchorEveryTurns"),
        ("角色接話延伸", "directorDialogueExpansionEnabled"),
        ("導播回合上限", "directorGroupTurnLimit"),
        ("幾批留言後回主軸", "directorMaxChatBatches"),
        ("觀眾插入冷卻秒數", "directorAudienceInterruptCooldown"),
        ("每個企劃 turn 觀眾批次上限", "directorMaxAudienceBatchesPerPlannedTurn"),
        ("角色停頓後續話秒數", "directorIdle"),
        ("本場直播方向", "directorGuidance"),
        ("主持互動規則", "hostInteractionRules"),
    ]:
        pattern = (
            rf'<label[^>]*>\s*<span class="field-label">{re.escape(label_text)}\s*'
            rf'<span class="help-tip"[^>]*>\?</span>\s*</span>\s*'
            rf'<(?:input|select|textarea)[^>]*id="{field_id}"'
        )
        assert re.search(pattern, live_session_block, flags=re.DOTALL), field_id

    assert "episodePlanHandoffGapSeconds" not in live_session_block
    assert "episodePlanTurnGapSeconds" not in live_session_block
    assert "企劃交接等待秒數" not in live_session_block
    assert "企劃一般等待秒數" not in live_session_block
    assert 'id="programSegmentRows"' in live_session_block
    assert 'id="addProgramSegmentRow"' in live_session_block
    assert 'id="programSegmentPlan" type="hidden"' in live_session_block
    assert 'id="legacyDirectorFields"' in live_session_block
    assert "Legacy 主持/段落流程" in live_session_block
    assert live_session_block.index('id="characterSelect"') > live_session_block.index('id="legacyDirectorFields"')
    assert "function updateEpisodePlanModeControls" in index_html
    assert "function showEpisodePlanError" in index_html
    assert "legacyDirectorFields" in index_html
    assert "legacyFields.open = !hasEpisodePlan" in index_html
    assert "legacyFields.classList.toggle(\"legacy-disabled\", hasEpisodePlan)" in index_html
    assert "新版企劃會依參與者名稱自動對應角色" in index_html
    assert "企劃角色對應失敗" in index_html
    assert 'character_ids: $("episodePlanSelect")?.value ? [] : selectedCharacterIds()' in index_html
    assert 'id="directorSegmentState"' in live_session_block
    assert "function renderDirectorSegmentState" in index_html
    assert ".director-segment-state" in index_html


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
    assert 'handleLiveSessionError("直播操作失敗", error)' in index_html
    assert 'log("直播收尾失敗", String(error))' not in index_html


def test_control_ui_refreshes_selected_session_when_status_sse_arrives():
    index_html = _control_ui_source()

    assert 'if (payload.type === "status") {' in index_html
    status_block = index_html[
        index_html.index('if (payload.type === "status") {'):
        index_html.index('if (payload.type === "youtube_live_event")')
    ]
    assert "await loadSessions(id);" in status_block
    assert "await refreshDirector();" in status_block
    assert "updateLiveSessionControls();" in status_block


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
    assert '$("saveTestEventSettings").disabled = blocked || !hasSession;' in index_html
    assert '$("toggleAutoTestEvents").disabled = blocked || !hasSession;' in index_html
    assert '$("autoTestEvents").checked = false;' in index_html
    assert '$("autoTestEvents").disabled = blocked;' in index_html
    assert '$("videoId").addEventListener("input", updateLiveSessionControls);' in index_html
    assert "真實 YouTube 直播不允許插入測試留言" in index_html


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
async def test_recent_events_can_include_pending_for_control_queue(monkeypatch, tmp_path):
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
    storage.save_event({
        "bridge_session_id": "live-a",
        "connector_id": "yt-main",
        "youtube_message_id": "pending-a",
        "message_type": "textMessageEvent",
        "author_display_name": "測試觀眾",
        "message_text": "安全檢查前的待處理留言",
        "status": "active",
    })
    monkeypatch.setattr(server_module, "storage", storage)
    monkeypatch.setattr(server_module, "manager", server_module.YouTubeBridgeManager(storage))

    hidden = await server_module.recent_events("live-a", limit=10)
    visible = await server_module.recent_events("live-a", limit=10, include_pending=True)

    assert hidden["events"] == []
    assert len(visible["events"]) == 1
    event = visible["events"][0]
    assert event["author_display_name"] == "測試觀眾"
    assert event["safety_status"] == "pending"
    assert event["message_text"] == "安全檢查未完成，暫不顯示原始留言。"


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
async def test_start_current_session_discards_existing_runtime_without_summary(monkeypatch, tmp_path):
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
    stopped: list[str] = []
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
            stopped.append(session_id)
            storage.update_session_fields(session_id, status="stopped")
            return self.get_status(session_id)

    class FakeSummaryManager:
        def summarize_session(self, session_id: str, **_kwargs):
            raise AssertionError(f"start_current_session must not summarize stale session {session_id}")

    memory_writes: list[dict] = []

    class FakeMemoriaClient:
        def write_shared_youtube_memory(self, **kwargs):
            raise AssertionError(f"start_current_session must not write stale memory: {kwargs}")

    monkeypatch.setattr(server_module, "manager", FakeManager())
    monkeypatch.setattr(server_module, "summary_manager", FakeSummaryManager())
    monkeypatch.setattr(server_module._sessions_routes, "MemoriaClient", FakeMemoriaClient)

    result = await server_module.start_current_session(server_module.LiveSessionConfig(
        video_id="",
        character_ids=["coco"],
        auto_inject=True,
    ))

    assert finalized == []
    assert stopped == ["old-live"]
    assert memory_writes == []
    assert storage.get_session("old-live") is None
    assert storage.count_events("old-live") == 0
    assert storage.list_interactions("old-live") == []
    assert started == [result["session_id"]]
    assert storage.get_session(result["session_id"])["status"] == "running"
    assert result["archived_sessions"][0]["session_id"] == "old-live"
    assert result["archived_sessions"][0]["status"] == "discarded"
    assert result["archived_sessions"][0]["reason"] == "replace_with_new_single_live_session"
    assert result["archived_sessions"][0]["deleted"] is True


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
async def test_super_chat_reply_batch_uses_director_handoff_for_episode_session(monkeypatch, tmp_path):
    storage = server_module.BridgeStorage(tmp_path / "bridge.db")
    storage.upsert_connector({
        "connector_id": "yt-main",
        "display_name": "YouTube Main",
        "api_key": "key",
        "enabled": True,
    })
    plan = sample_plan()
    storage.upsert_live_episode_plan(plan)
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "yt-main",
        "target_memoria_session_id": "mem-a",
        "character_ids": ["host-a", "analyst-b", "skeptic-c"],
        "episode_plan_id": plan["plan_id"],
        "auto_inject": True,
    })
    storage.update_director_state("live-a", director_enabled=True, status="running")
    super_chat = storage.save_event({
        "bridge_session_id": "live-a",
        "connector_id": "yt-main",
        "youtube_message_id": "sc-clean",
        "message_type": "superChatEvent",
        "author_display_name": "紅色斗內",
        "message_text": "請優先回應這個 SC",
        "amount_display_string": "NT$750",
        "amount_micros": 750_000_000,
        "priority_class": "super_chat",
        "sc_tier": 4,
        "safety_status": "completed",
        "safety_label": "clean",
        "safe_message_text": "請優先回應這個 SC",
        "status": "active",
    })
    calls: list[dict] = []

    class FakeManager:
        def _director_owns_auto_inject(self, session):
            return True

        async def prepare_director_super_chat_reply_batch(self, session_id: str, *, event_ids):
            calls.append({"session_id": session_id, "event_ids": event_ids})
            return {
                "status": "queued_for_director",
                "session_id": session_id,
                "event_ids": event_ids,
                "source": "super_chat",
            }

        async def inject_recent(self, **_kwargs):
            raise AssertionError("director session must not use generic super_chat inject")

    monkeypatch.setattr(server_module._sessions_routes, "storage", storage)
    monkeypatch.setattr(server_module._sessions_routes, "manager", FakeManager())

    result = await server_module._sessions_routes.reply_super_chat_batch("live-a")

    assert result["status"] == "queued_for_director"
    assert calls == [{"session_id": "live-a", "event_ids": [super_chat["id"]]}]
