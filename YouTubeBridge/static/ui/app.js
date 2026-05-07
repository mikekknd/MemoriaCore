import { $, state, escapeHtml, initBridgeKey, installTestIds, installTooltipPositioning, log } from "./core.js";
import { requestedSessionIdFromUrl, selectedTopicEntry } from "./selectors.js";
import {
  generateTestEvents, injectEvents, interruptNow, loadConnectors, loadHealth,
  loadMemoriaConfig, loadMemoriaRefs, loadSessions, makeSummary,
  refreshDirector, refreshEvents, refreshSummary, replySuperChats, saveConnector, saveMemoriaConfig,
  testMemoriaAuth, toggleAutoTestEvents, toggleSession,
  syncCharacterSelectionLimit, updateDirectorGuidance, updateLiveSessionControls, updateSessionSettings,
} from "./control.js?v=topic-graph-primary-focus-v1";
import {
  addTopicEntry, cancelTopicEntryEdit, createTopicPack, deleteAllTopicPacks, deleteTopicPack,
  fillTopicEntryForm, importFactCardsFolder, linkTopicPack, rebuildTopicEmbeddings,
  closeTopicGraphModal, openTopicGraphModal, rebuildTopicGraph, refreshTopicEntries, refreshTopicGraph, refreshTopicGraphTrace,
  refreshTopicPacks, resetTopicGraphView, updateTopicActionVisibility,
  updateTopicEntry, updateTopicPack,
} from "./topic-packs.js?v=topic-graph-primary-focus-v1";

async function refreshAll() {
  await loadHealth();
  await loadConnectors();
  await loadMemoriaConfig();
  await loadMemoriaRefs();
  await loadSessions(requestedSessionIdFromUrl());
  await refreshEvents();
  await refreshSummary();
  await refreshDirector();
  await refreshTopicPacks();
  await loadRuntimeRules();
}

export function renderRuntimeRulesMarkdown(markdown) {
  const html = [];
  let inList = false;
  const closeList = () => {
    if (inList) {
      html.push("</ul>");
      inList = false;
    }
  };
  for (const rawLine of String(markdown || "").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) {
      closeList();
      continue;
    }
    if (line.startsWith("### ")) {
      closeList();
      html.push(`<h4>${escapeHtml(line.slice(4))}</h4>`);
      continue;
    }
    if (line.startsWith("## ")) {
      closeList();
      html.push(`<h3>${escapeHtml(line.slice(3))}</h3>`);
      continue;
    }
    if (line.startsWith("# ")) {
      closeList();
      html.push(`<h2>${escapeHtml(line.slice(2))}</h2>`);
      continue;
    }
    if (line.startsWith("- ")) {
      if (!inList) {
        html.push("<ul>");
        inList = true;
      }
      html.push(`<li>${escapeHtml(line.slice(2))}</li>`);
      continue;
    }
    closeList();
    html.push(`<p>${escapeHtml(line)}</p>`);
  }
  closeList();
  return html.join("");
}

export async function loadRuntimeRules() {
  const content = $("runtimeRulesContent");
  const stateBadge = $("runtimeRulesState");
  if (!content || !stateBadge) return;
  stateBadge.textContent = "載入中";
  stateBadge.className = "status";
  try {
    const response = await fetch("/ui-assets/live_runtime_rules.md", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const markdown = await response.text();
    content.innerHTML = renderRuntimeRulesMarkdown(markdown);
    stateBadge.textContent = "已載入";
    stateBadge.className = "status good";
  } catch (error) {
    content.textContent = "規則說明載入失敗，請檢查 live_runtime_rules.md 是否存在。";
    stateBadge.textContent = "載入失敗";
    stateBadge.className = "status bad";
    log("規則說明載入失敗", String(error));
  }
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".pane").forEach((x) => x.classList.remove("active"));
    tab.classList.add("active");
    $(tab.dataset.pane).classList.add("active");
  });
});
$("refreshAll").onclick = () => refreshAll().catch((error) => log("更新失敗", String(error)));
$("saveConnector").onclick = () => saveConnector().catch((error) => log("connector 儲存失敗", String(error)));
$("saveMemoriaConfig").onclick = () => saveMemoriaConfig().catch((error) => {
  $("memoriaAuthState").textContent = "儲存失敗";
  $("memoriaAuthState").className = "status bad";
  log("MemoriaCore 設定儲存失敗", String(error));
});
$("testMemoriaAuth").onclick = () => testMemoriaAuth().catch((error) => {
  $("memoriaAuthState").textContent = "連線失敗";
  $("memoriaAuthState").className = "status bad";
  log("MemoriaCore 連線測試失敗", String(error));
});
$("reloadRuntimeRules").onclick = () => loadRuntimeRules();
$("toggleSession").onclick = () => toggleSession().catch((error) => log("直播操作失敗", String(error)));
$("updateSession").onclick = () => updateSessionSettings().catch((error) => log("直播設定更新失敗", String(error)));
$("refreshEvents").onclick = () => refreshEvents().catch((error) => log("留言更新失敗", String(error)));
$("generateTestEvents").onclick = () => generateTestEvents().catch((error) => log("測試留言生成失敗", String(error)));
$("toggleAutoTestEvents").onclick = () => toggleAutoTestEvents().catch((error) => log("自動測試留言切換失敗", String(error)));
$("injectSelected").onclick = () => injectEvents(false).catch((error) => log("注入失敗", String(error)));
$("injectPending").onclick = () => injectEvents(true).catch((error) => log("注入失敗", String(error)));
$("replySuperChats").onclick = () => replySuperChats().catch((error) => log("SC 回應失敗", String(error)));
$("interruptNow").onclick = () => interruptNow().catch((error) => log("中斷失敗", String(error)));
$("makeSummary").onclick = () => makeSummary(false).catch((error) => log("摘要失敗", String(error)));
$("forceSummary").onclick = () => makeSummary(true).catch((error) => log("強制摘要失敗", String(error)));
$("updateDirectorGuidance").onclick = () => updateDirectorGuidance().catch((error) => log("導播方向更新失敗", String(error)));
$("refreshTopicPacks").onclick = () => refreshTopicPacks().catch((error) => log("資料包更新失敗", String(error)));
$("createTopicPack").onclick = () => createTopicPack().catch((error) => log("資料包建立失敗", String(error)));
$("updateTopicPack").onclick = () => updateTopicPack().catch((error) => log("資料包更新失敗", String(error)));
$("deleteTopicPack").onclick = () => deleteTopicPack().catch((error) => log("資料包刪除失敗", String(error)));
$("deleteAllTopicPacks").onclick = () => deleteAllTopicPacks().catch((error) => log("清空資料包失敗", String(error)));
$("linkTopicPack").onclick = () => linkTopicPack().catch((error) => log("資料包綁定失敗", String(error)));
$("addTopicEntry").onclick = () => addTopicEntry().catch((error) => log("fact card 新增失敗", String(error)));
$("updateTopicEntry").onclick = () => updateTopicEntry().catch((error) => log("fact card 更新失敗", String(error)));
$("cancelTopicEntryEdit").onclick = () => cancelTopicEntryEdit();
$("importFactCardsFolder").onclick = () => importFactCardsFolder().catch((error) => log("FactCards 資料夾匯入失敗", String(error)));
$("rebuildTopicEmbeddings").onclick = () => rebuildTopicEmbeddings().catch((error) => log("向量索引重建失敗", String(error)));
$("refreshTopicGraph").onclick = () => refreshTopicGraph().catch((error) => log("Topic Graph 更新失敗", String(error)));
$("rebuildTopicGraph").onclick = () => rebuildTopicGraph().catch((error) => log("Topic Graph 重建失敗", String(error)));
$("refreshTopicGraphTrace").onclick = () => refreshTopicGraphTrace().catch((error) => log("Topic Graph trace 更新失敗", String(error)));
$("resetTopicGraphView").onclick = () => resetTopicGraphView();
$("openTopicGraphModal").onclick = () => openTopicGraphModal();
$("closeTopicGraphModal").onclick = () => closeTopicGraphModal();
$("topicPackSelect").onchange = () => refreshTopicEntries().catch((error) => log("fact card 更新失敗", String(error)));
$("sessionTopicPackSelect").onchange = () => {
  $("topicPackSelect").value = $("sessionTopicPackSelect").value;
  refreshTopicEntries().catch((error) => log("資料包預覽更新失敗", String(error)));
};
$("topicEntrySelect").onchange = () => {
  fillTopicEntryForm(selectedTopicEntry());
  updateTopicActionVisibility();
};
$("topicPackTitle").addEventListener("input", updateTopicActionVisibility);
$("topicEntryTitle").addEventListener("input", updateTopicActionVisibility);
$("topicEntryBody").addEventListener("input", updateTopicActionVisibility);
$("videoId").addEventListener("input", updateLiveSessionControls);
$("characterSelect").addEventListener("change", () => {
  syncCharacterSelectionLimit();
  updateLiveSessionControls();
});

installTestIds();
installTooltipPositioning();
updateTopicActionVisibility();
initBridgeKey().then(() => refreshAll()).catch((error) => log("初始化失敗", String(error)));
