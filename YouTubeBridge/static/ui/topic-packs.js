import { $, state } from "./core.js";
import { currentTopicEntryId, selectedSessionId, selectedSessionInfo } from "./selectors.js";

export {
  setTopicEntryEditorBusy,
  topicEntryPayload,
  fillTopicPackForm,
  fillTopicEntryForm,
  selectTopicEntryForEditing,
  cancelTopicEntryEdit,
  topicEntryPreviewText,
  renderTopicEntries,
  bindTopicEntryCardButtons,
  refreshTopicPacks,
  refreshSessionTopicPackSelection,
  refreshTopicEntries,
  createTopicPack,
  updateTopicPack,
  deleteTopicPack,
  deleteAllTopicPacks,
  linkTopicPack,
  bindSessionTopicPack,
  addTopicEntry,
  updateTopicEntry,
  deleteTopicEntry,
} from "./topic-pack-crud.js";
export {
  setFactCardImportBusy,
  importFactCardsFolder,
  rebuildTopicEmbeddings,
} from "./fact-card-import.js";
export {
  setTopicGraphBusy,
  setTopicGraphLoadedState,
  resetTopicGraphView,
  openTopicGraphModal,
  closeTopicGraphModal,
  clearTopicGraphSelection,
  selectTopicGraphNode,
  renderTopicGraph,
  renderTopicGraphTrace,
  refreshTopicGraph,
  rebuildTopicGraph,
  refreshTopicGraphTrace,
  scheduleTopicGraphTraceRefresh,
} from "./topic-graph.js";

export function setTopicActionVisible(id, visible) {
  const element = $(id);
  if (!element) return;
  element.classList.toggle("is-hidden", !visible);
  element.disabled = !visible;
}

export function updateTopicActionVisibility() {
  const hasSession = !!selectedSessionId();
  const hasPack = Number($("topicPackSelect").value || 0) > 0;
  const hasEntry = currentTopicEntryId() > 0;
  const entryBusy = !!state.topicEntryEditorBusy;
  const importBusy = !!state.factCardImportBusy;
  const graphBusy = !!state.topicGraphBusy;
  const liveLocked = factCardActionsBlockedDuringLive();
  const hasPackTitle = !!$("topicPackTitle").value.trim();
  const hasEntryContent = !!$("topicEntryTitle").value.trim() && !!$("topicEntryBody").value.trim();

  setTopicActionVisible("createTopicPack", !hasPack);
  setTopicActionVisible("updateTopicPack", hasPack);
  setTopicActionVisible("deleteTopicPack", hasPack);
  setTopicActionVisible("deleteAllTopicPacks", state.topicPacks.length > 0);
  setTopicActionVisible("linkTopicPack", hasPack && hasSession);
  setTopicActionVisible("addTopicEntry", hasPack && !hasEntry);
  setTopicActionVisible("updateTopicEntry", hasPack && hasEntry);
  setTopicActionVisible("cancelTopicEntryEdit", hasPack && hasEntry);
  setTopicActionVisible("rebuildTopicEmbeddings", hasPack);
  setTopicActionVisible("importFactCardsFolder", hasPack && !liveLocked);
  setTopicActionVisible("refreshTopicGraph", hasPack);
  setTopicActionVisible("rebuildTopicGraph", hasPack);
  setTopicActionVisible("refreshTopicGraphTrace", hasPack && hasSession);
  setTopicActionVisible("resetTopicGraphView", hasPack);
  setTopicActionVisible("openTopicGraphModal", hasPack);
  $("topicEntryPanel").classList.toggle("is-hidden", !hasPack);
  $("topicGraphPanel").classList.toggle("is-hidden", !hasPack);
  $("topicFactCardLiveLockNotice").classList.toggle("is-hidden", !(hasPack && liveLocked));

  $("createTopicPack").disabled = hasPack || !hasPackTitle;
  $("updateTopicPack").disabled = !hasPack || !hasPackTitle;
  $("addTopicEntry").disabled = !hasPack || hasEntry || !hasEntryContent || entryBusy;
  $("updateTopicEntry").disabled = !hasPack || !hasEntry || !hasEntryContent || entryBusy;
  $("cancelTopicEntryEdit").disabled = !hasPack || !hasEntry || entryBusy;
  $("importFactCardsFolder").disabled = !hasPack || liveLocked || importBusy;
  $("refreshTopicGraph").disabled = !hasPack || graphBusy;
  $("rebuildTopicGraph").disabled = !hasPack || graphBusy;
  $("refreshTopicGraphTrace").disabled = !hasPack || !hasSession || graphBusy;
  $("resetTopicGraphView").disabled = !hasPack || graphBusy;
  $("openTopicGraphModal").disabled = !hasPack || graphBusy;
  $("importFactCardsFolder").textContent = importBusy ? "匯入中..." : "匯入 FactCards 資料夾";
}

export function factCardActionsBlockedDuringLive() {
  const session = selectedSessionInfo();
  const runtimeStatus = session?.runtime_status?.status || session?.status || "";
  return !!(
    session?.runtime_status?.running
    || ["starting", "running", "closing"].includes(runtimeStatus)
  );
}
