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


def _assert_launcher_uses_runtime_log_dir(source: str, legacy_runtime_prefix: str) -> None:
    assert r"runtime\log" in source
    assert ".foreground.log" in source or (".out.log" in source and ".err.log" in source)
    assert legacy_runtime_prefix not in source.lower()

# Split from test_server_auth.py: server, launcher, route, and UI contracts.

def test_control_ui_exposes_fact_cards_folder_import_for_anime_topic_flow():
    index_html = _control_ui_source()

    assert 'id="importFactCardsFolder"' in index_html
    assert 'id="importEpisodePlanEvidence"' in index_html
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
    assert "/episode-plan/evidence/import" in index_html
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
    assert "匯入企劃 Evidence" in index_html
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
    assert '<button id="importFactCardsFolder" class="blue is-hidden">匯入 FactCards 資料夾</button>' in index_html
    init_start = index_html.index("installTestIds();")
    init_block = index_html[init_start:index_html.index("initBridgeKey()", init_start)]
    assert "updateTopicActionVisibility();" in init_block


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
    assert "topicGraphShowSourceNodes: false" in index_html
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
    assert "function topicGraphVisibleGraph" in index_html
    assert "function toggleTopicGraphSourceNodes" in index_html
    assert "function topicGraphSyntheticSourceEdges" in index_html
    assert "function topicGraphPrimaryEntity" in index_html
    assert "const TOPIC_GRAPH_SOURCE_NODE_TYPES" in index_html
    assert "function shouldRenderTopicGraphLabel" in index_html
    assert "const denseGraph = nodes.length > 36;" in index_html
    assert "topic: 64" in index_html
    assert "entity: 48" in index_html
    assert "function topicGraphNodeTypeLabel" in index_html
    assert "const maxVisibleLabels = selected ? Math.max(18, relatedNodeIds.size) : (focusNodeIds.size ? Math.max(18, focusNodeIds.size) : (denseGraph ? 46 : 64));" in index_html
    assert "function clampTopicGraphScale" in index_html
    assert "function zoomTopicGraph" in index_html
    assert "function beginTopicGraphNodeDrag" in index_html
    assert "function resetTopicGraphView" in index_html
    assert "function openTopicGraphModal" in index_html
    assert "function closeTopicGraphModal" in index_html
    assert "function bindTopicGraphViewportControls" in index_html
    assert "function renderTopicGraphToSvg(svg)" in index_html
    assert "const latestTrace = currentTopicGraphLatestTrace();" in index_html
    assert "const focusNodeIds = selected ? relatedNodeIds : topicGraphAutoFocusNodeIds(latestTrace, edges);" in index_html
    assert "const primaryTraceNodeId = topicGraphPrimaryTraceNodeId(latestTrace);" in index_html
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
    assert "來源節點只用來追溯 Markdown 檔案" in index_html
    assert "綠色 entity 節點" in index_html
    assert "function topicGraphTraceMatchesPack" in index_html
    assert "const packTraces = (traces.traces || []).filter((trace) => topicGraphTraceMatchesPack(trace, packId));" in index_html
    assert 'data-topic-graph-jump="${escapeHtml(node.id)}"' in index_html
    assert "function centerTopicGraphOnNode" in index_html
    assert 'if (selected && !relatedNodeIds.has(Number(candidate.node.id))) return;' in index_html
    assert 'force: topicGraphLabelCandidateForce(node, selected, relatedNodeIds, traceNodeIds, focusNodeIds)' in index_html
    assert 'if (!topicGraphLabelCandidateVisible(candidate, selected, visibleLabels.size, maxVisibleLabels)) return;' in index_html
    assert 'denseGraph && ["entity", "reference"].includes(candidate.node.node_type)' not in index_html
    assert 'const mainElement = $("topicGraphSelectedNode");' in index_html
    assert 'const modalElement = $("topicGraphModalDetails");' in index_html
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


def test_fact_card_generation_ui_is_not_exposed():
    index_html = _control_ui_source()

    assert 'id="factCardGenerationOverlay"' not in index_html
    assert 'id="factCardGenerationMessage"' not in index_html
    assert "factCardGenerationBusy" not in index_html
    assert "function setFactCardGenerationBusy" not in index_html
    assert 'id="autoBuildTopic"' not in index_html


def test_fact_cards_folder_import_shows_blocking_progress_feedback():
    index_html = _control_ui_source()
    import_block = index_html[
        index_html.index("async function importFactCardsFolder"):
        index_html.index("async function rebuildTopicEmbeddings")
    ]
    evidence_import_block = index_html[
        index_html.index("async function importEpisodePlanEvidence"):
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
    assert "setFactCardImportBusy(true, \"正在讀取節目企劃 factcards/、建立 Evidence 資料包並重建向量，請稍候。\");" in evidence_import_block
    assert 'log("企劃 Evidence 已匯入", data);' in evidence_import_block
    assert "匯入完成，但關係圖建立失敗" in import_block
    assert "請查看 Log 或點重建關係圖" in import_block
    assert "finally {" in import_block
    assert "setFactCardImportBusy(false);" in import_block
    assert "finally {" in evidence_import_block
    assert "setFactCardImportBusy(false);" in evidence_import_block


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
    assert 'setTopicActionVisible("importFactCardsFolder", hasPack && !liveLocked);' in index_html
    assert 'setTopicActionVisible("autoBuildTopicPack"' not in index_html


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

    monkeypatch.setattr(server_module, "manager", FakeManager())

    assert not hasattr(server_module, "auto_build_session_topic_pack")

    with pytest.raises(HTTPException) as import_exc:
        await server_module.import_fact_cards_folder_to_pack(server_module.FactCardImportRequest())
    assert import_exc.value.status_code == 409
    assert "直播中不產生或匯入 Fact Cards" in import_exc.value.detail

    with pytest.raises(HTTPException) as evidence_exc:
        await server_module.import_episode_plan_evidence(
            "live-a",
            server_module.EpisodePlanEvidenceImportRequest(plan_id="plan-general-panel"),
        )
    assert evidence_exc.value.status_code == 409


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
