import { $, state, initBridgeKey, installTestIds, log } from "./core.js";
import { requestedSessionIdFromUrl, selectedTopicEntry } from "./selectors.js";
import {
  deleteSession, fillSessionForm, generateTestEvents, injectEvents, interruptNow, loadConnectors, loadHealth,
  loadMemoriaConfig, loadMemoriaRefs, loadSessions, makeSummary, newSessionDraft, refreshChatPreview,
  refreshDirector, refreshEvents, refreshQueue, refreshSummary, replySuperChats, saveConnector, saveMemoriaConfig,
  sessionAction, subscribeEvents, testMemoriaAuth, toggleAutoTestEvents, toggleDirector, toggleSession,
  updateDirectorGuidance, updateLiveSessionControls, updateSessionSettings, writeMemory,
} from "./control.js";
import {
  addTopicEntry, autoBuildTopicPack, cancelTopicEntryEdit, createTopicPack, deleteAllTopicPacks, deleteTopicPack,
  fillTopicEntryForm, generateGeminiFactCards, importFactCardsFolder, linkTopicPack, rebuildTopicEmbeddings,
  refreshTopicEntries, refreshTopicPacks, restoreTopicEntries, searchTopicPack, updateTopicActionVisibility,
  updateTopicEntry, updateTopicPack,
} from "./topic-packs.js";

async function refreshAll() {
  await loadHealth();
  await loadConnectors();
  await loadMemoriaConfig();
  await loadMemoriaRefs();
  await loadSessions(requestedSessionIdFromUrl());
  await refreshEvents();
  await refreshSummary();
  await refreshDirector();
  await refreshQueue();
  await refreshTopicPacks();
  await refreshChatPreview({ silent: true });
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
$("memoriaSession").onchange = () => refreshChatPreview({ silent: true });
$("memoriaBaseUrl").onchange = () => refreshChatPreview({ silent: true });
$("newSession").onclick = () => {
  newSessionDraft();
  updateTopicActionVisibility();
};
$("toggleSession").onclick = () => toggleSession().catch((error) => log("開始 / 停止失敗", String(error)));
$("updateSession").onclick = () => updateSessionSettings().catch((error) => log("直播設定更新失敗", String(error)));
$("finalizeSession").onclick = () => sessionAction("finalize").catch((error) => log("標記結束失敗", String(error)));
$("deleteSession").onclick = () => deleteSession().catch((error) => log("刪除失敗", String(error)));
$("refreshEvents").onclick = () => refreshEvents().catch((error) => log("留言更新失敗", String(error)));
$("generateTestEvents").onclick = () => generateTestEvents().catch((error) => log("測試留言生成失敗", String(error)));
$("toggleAutoTestEvents").onclick = () => toggleAutoTestEvents().catch((error) => log("自動測試留言切換失敗", String(error)));
$("injectSelected").onclick = () => injectEvents(false).catch((error) => log("注入失敗", String(error)));
$("injectPending").onclick = () => injectEvents(true).catch((error) => log("注入失敗", String(error)));
$("replySuperChats").onclick = () => replySuperChats().catch((error) => log("SC 回應失敗", String(error)));
$("interruptNow").onclick = () => interruptNow().catch((error) => log("中斷失敗", String(error)));
$("makeSummary").onclick = () => makeSummary(false).catch((error) => log("摘要失敗", String(error)));
$("forceSummary").onclick = () => makeSummary(true).catch((error) => log("強制摘要失敗", String(error)));
$("writeMemory").onclick = () => writeMemory().catch((error) => log("寫入記憶失敗", String(error)));
$("updateDirectorGuidance").onclick = () => updateDirectorGuidance().catch((error) => log("導播方向更新失敗", String(error)));
$("toggleDirector").onclick = () => toggleDirector().catch((error) => log("導播啟動 / 停止失敗", String(error)));
$("refreshQueue").onclick = () => refreshQueue().catch((error) => log("queue 更新失敗", String(error)));
$("refreshTopicPacks").onclick = () => refreshTopicPacks().catch((error) => log("資料包更新失敗", String(error)));
$("createTopicPack").onclick = () => createTopicPack().catch((error) => log("資料包建立失敗", String(error)));
$("updateTopicPack").onclick = () => updateTopicPack().catch((error) => log("資料包更新失敗", String(error)));
$("deleteTopicPack").onclick = () => deleteTopicPack().catch((error) => log("資料包刪除失敗", String(error)));
$("deleteAllTopicPacks").onclick = () => deleteAllTopicPacks().catch((error) => log("清空資料包失敗", String(error)));
$("linkTopicPack").onclick = () => linkTopicPack().catch((error) => log("資料包綁定失敗", String(error)));
$("addTopicEntry").onclick = () => addTopicEntry().catch((error) => log("fact card 新增失敗", String(error)));
$("updateTopicEntry").onclick = () => updateTopicEntry().catch((error) => log("fact card 更新失敗", String(error)));
$("cancelTopicEntryEdit").onclick = () => cancelTopicEntryEdit();
$("autoBuildTopicPack").onclick = () => autoBuildTopicPack().catch((error) => log("自動資料卡建立失敗", String(error)));
$("importFactCardsFolder").onclick = () => importFactCardsFolder().catch((error) => log("FactCards 資料夾匯入失敗", String(error)));
$("generateGeminiFactCards").onclick = () => generateGeminiFactCards().catch((error) => log("Gemini FactCards 產生失敗", String(error)));
$("rebuildTopicEmbeddings").onclick = () => rebuildTopicEmbeddings().catch((error) => log("向量索引重建失敗", String(error)));
$("searchTopicPack").onclick = () => searchTopicPack().catch((error) => log("向量檢索失敗", String(error)));
$("restoreTopicEntries").onclick = () => restoreTopicEntries().catch((error) => log("fact card 清單恢復失敗", String(error)));
$("topicPackSelect").onchange = () => refreshTopicEntries().catch((error) => log("fact card 更新失敗", String(error)));
$("topicEntrySelect").onchange = () => {
  fillTopicEntryForm(selectedTopicEntry());
  updateTopicActionVisibility();
};
$("topicPackTitle").addEventListener("input", updateTopicActionVisibility);
$("topicEntryTitle").addEventListener("input", updateTopicActionVisibility);
$("topicEntryBody").addEventListener("input", updateTopicActionVisibility);
$("refreshChatPreview").onclick = () => refreshChatPreview().catch((error) => log("Chat Preview 更新失敗", String(error)));
$("openFullChat").onclick = (event) => {
  const link = $("openFullChat");
  if (link.getAttribute("aria-disabled") === "true" || !link.dataset.href) {
    event.preventDefault();
    log("完整 Chat 尚未可開啟", "目前 Live Session 尚未綁定 MemoriaCore session。");
  }
};
$("sessionSelect").onchange = async () => {
  const session = state.sessions.find((item) => item.session_id === $("sessionSelect").value);
  if (session) fillSessionForm(session);
  else newSessionDraft();
  updateLiveSessionControls();
  subscribeEvents();
  await refreshEvents();
  await refreshSummary();
  await refreshDirector();
  await refreshQueue();
  await refreshTopicPacks();
  await refreshChatPreview({ silent: true });
};

installTestIds();
updateTopicActionVisibility();
initBridgeKey().then(() => refreshAll()).catch((error) => log("初始化失敗", String(error)));
