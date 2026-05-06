import { $, state, api, escapeHtml, log } from "./core.js";
import { currentTopicEntryId, selectedSessionId, selectedSessionInfo, selectedTopicEntry, selectedTopicPack, topicEntryById } from "./selectors.js";

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
  $("topicEntryPanel").classList.toggle("is-hidden", !hasPack);
  $("topicFactCardLiveLockNotice").classList.toggle("is-hidden", !(hasPack && liveLocked));

  $("createTopicPack").disabled = hasPack || !hasPackTitle;
  $("updateTopicPack").disabled = !hasPack || !hasPackTitle;
  $("addTopicEntry").disabled = !hasPack || hasEntry || !hasEntryContent || entryBusy;
  $("updateTopicEntry").disabled = !hasPack || !hasEntry || !hasEntryContent || entryBusy;
  $("cancelTopicEntryEdit").disabled = !hasPack || !hasEntry || entryBusy;
  $("importFactCardsFolder").disabled = !hasPack || liveLocked || importBusy;
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

export function setTopicEntryEditorBusy(isBusy) {
  const busy = !!isBusy;
  state.topicEntryEditorBusy = busy;
  $("topicEntryTitle").disabled = busy;
  $("topicEntryBody").disabled = busy;
  $("updateTopicEntry").textContent = busy ? "儲存中..." : "儲存";
  updateTopicActionVisibility();
}

export function setFactCardImportBusy(isBusy, message = "正在讀取 FactCards 資料夾、建立資料卡並重建向量，請稍候。") {
  const busy = !!isBusy;
  state.factCardImportBusy = busy;
  $("factCardImportMessage").textContent = message;
  $("factCardImportOverlay").classList.toggle("is-hidden", !busy);
  $("factCardImportOverlay").setAttribute("aria-hidden", busy ? "false" : "true");
  updateTopicActionVisibility();
}

export function topicEntryPayload() {
  const tags = $("topicEntryTags").value.split(/[\s,，]+/).map((tag) => tag.trim()).filter(Boolean).slice(0, 12);
  return {
    title: $("topicEntryTitle").value.trim(),
    body: $("topicEntryBody").value.trim(),
    source_url: $("topicEntrySourceUrl").value.trim(),
    source_type: $("topicEntrySourceType").value.trim() || "manual",
    tags,
  };
}

export function fillTopicPackForm(pack) {
  $("topicPackTitle").value = pack?.title || "";
  $("topicPackDescription").value = pack?.description || "";
  updateTopicActionVisibility();
}

export function fillTopicEntryForm(entry) {
  state.currentTopicEntryId = Number(entry?.id || 0);
  $("topicEntrySelect").value = entry?.id ? String(entry.id) : "";
  $("topicEntryTitle").value = entry?.title || "";
  $("topicEntryBody").value = entry?.body || "";
  $("topicEntrySourceUrl").value = entry?.source_url || "";
  $("topicEntrySourceType").value = entry?.source_type || "manual";
  $("topicEntryTags").value = (entry?.tags || []).join(" ");
  updateTopicActionVisibility();
}

export function selectTopicEntryForEditing(entryId) {
  const entry = topicEntryById(entryId);
  if (!entry) {
    fillTopicEntryForm(null);
    return;
  }
  if (entry.pack_id && state.topicPacks.some((pack) => Number(pack.id) === Number(entry.pack_id))) {
    $("topicPackSelect").value = String(entry.pack_id);
    fillTopicPackForm(selectedTopicPack());
  }
  fillTopicEntryForm(entry);
}

export function cancelTopicEntryEdit() {
  if (state.topicEntryEditorBusy) return;
  fillTopicEntryForm(null);
}

export function topicEntryPreviewText(entry) {
  const body = String(entry?.body || "")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^\s*[-*]\s+/gm, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!body) return "尚無內容摘要";
  return body.length > 180 ? `${body.slice(0, 180)}...` : body;
}

export function renderTopicEntries(entries) {
  return entries.map((entry) => {
    return `
      <div class="item topic-entry-card" data-topic-entry-id="${escapeHtml(entry.id)}">
        <strong>${escapeHtml(entry.title)}</strong>
        ${entry.similarity !== undefined ? `<p class="muted">相似度：${Number(entry.similarity || 0).toFixed(3)}</p>` : ""}
        <p>${escapeHtml(topicEntryPreviewText(entry))}</p>
        <div class="toolbar" style="margin-top:8px">
          <button type="button" data-edit-topic-entry="${escapeHtml(entry.id)}">編輯</button>
          <button type="button" class="danger" data-delete-topic-entry="${escapeHtml(entry.id)}">刪除</button>
        </div>
      </div>
    `;
  }).join("");
}

export function bindTopicEntryCardButtons() {
  document.querySelectorAll("[data-edit-topic-entry]").forEach((button) => {
    button.onclick = () => {
      selectTopicEntryForEditing(button.dataset.editTopicEntry || "");
    };
  });
  document.querySelectorAll("[data-delete-topic-entry]").forEach((button) => {
    button.onclick = () => {
      const entryId = Number(button.dataset.deleteTopicEntry || 0);
      deleteTopicEntry(entryId).catch((error) => log("fact card 刪除失敗", String(error)));
    };
  });
}

export async function refreshTopicPacks() {
  const previousPackId = Number($("topicPackSelect").value || 0);
  const previousSessionPackId = Number($("sessionTopicPackSelect")?.value || 0);
  state.topicPacks = await api("/topic-packs");
  const optionsHtml = state.topicPacks.map((pack) =>
    `<option value="${escapeHtml(pack.id)}">${escapeHtml(pack.title)}</option>`
  ).join("");
  $("topicPackSelect").innerHTML = `<option value="">新建資料包</option>` + optionsHtml;
  $("sessionTopicPackSelect").innerHTML = `<option value="">不綁定資料包</option>` + optionsHtml;
  if (previousPackId && state.topicPacks.some((pack) => Number(pack.id) === previousPackId)) {
    $("topicPackSelect").value = String(previousPackId);
  }
  if (previousSessionPackId && state.topicPacks.some((pack) => Number(pack.id) === previousSessionPackId)) {
    $("sessionTopicPackSelect").value = String(previousSessionPackId);
  }
  $("topicPackState").textContent = `${state.topicPacks.length} 個資料包`;
  $("topicPackState").className = "status good";
  fillTopicPackForm(selectedTopicPack());
  await refreshSessionTopicPackSelection();
  await refreshTopicEntries();
}

export async function refreshSessionTopicPackSelection() {
  const id = selectedSessionId();
  const selector = $("sessionTopicPackSelect");
  if (!selector || !id) return;
  try {
    const data = await api(`/sessions/${encodeURIComponent(id)}/topic-packs`);
    const pack = (data.packs || [])[0];
    const packId = pack ? Number(pack.id) : 0;
    const hasLinkedPack = packId && state.topicPacks.some((item) => Number(item.id) === packId);
    selector.value = hasLinkedPack ? String(packId) : "";
  } catch {
    // Session may not exist yet while editing a draft; keep the operator's current selection.
  }
}

export async function refreshTopicEntries() {
  const packId = Number($("topicPackSelect").value || 0);
  const previousEntryId = currentTopicEntryId();
  let entries = [];
  if (packId) {
    const data = await api(`/topic-packs/${packId}/entries?limit=80`);
    entries = data.entries || [];
    fillTopicPackForm(selectedTopicPack());
  }
  state.topicEntries = entries;
  $("topicEntrySelect").innerHTML = `<option value="">選擇 fact card</option>` + entries.map((entry) =>
    `<option value="${escapeHtml(entry.id)}">${escapeHtml(entry.title)}</option>`
  ).join("");
  if (previousEntryId && entries.some((entry) => Number(entry.id) === previousEntryId)) {
    $("topicEntrySelect").value = String(previousEntryId);
    fillTopicEntryForm(selectedTopicEntry());
  } else {
    $("topicEntrySelect").value = "";
    fillTopicEntryForm(null);
  }
  $("topicPackEntries").innerHTML = renderTopicEntries(entries) || `<div class="muted">尚無 fact card</div>`;
  bindTopicEntryCardButtons();
  updateTopicActionVisibility();
}

export async function createTopicPack() {
  const data = await api("/topic-packs", {
    method: "POST",
    body: JSON.stringify({
      title: $("topicPackTitle").value.trim(),
      description: $("topicPackDescription").value.trim(),
    }),
  });
  log("資料包已建立", data);
  await refreshTopicPacks();
  $("topicPackSelect").value = data.id;
  await refreshTopicEntries();
}

export async function updateTopicPack() {
  const packId = Number($("topicPackSelect").value || 0);
  if (!packId) throw new Error("請先選擇資料包");
  const data = await api(`/topic-packs/${packId}`, {
    method: "PUT",
    body: JSON.stringify({
      title: $("topicPackTitle").value.trim(),
      description: $("topicPackDescription").value.trim(),
    }),
  });
  log("資料包已更新", data);
  const selectedId = String(data.id);
  await refreshTopicPacks();
  $("topicPackSelect").value = selectedId;
  fillTopicPackForm(data);
  await refreshTopicEntries();
}

export async function deleteTopicPack() {
  const pack = selectedTopicPack();
  if (!pack) throw new Error("請先選擇資料包");
  const packId = Number(pack.id);
  const data = await api(`/topic-packs/${packId}`, {
    method: "DELETE",
  });
  log("資料包已刪除", data);
  $("topicPackSelect").value = "";
  $("topicEntrySelect").value = "";
  state.topicEntries = [];
  fillTopicPackForm(null);
  fillTopicEntryForm(null);
  await refreshTopicPacks();
  $("topicPackSelect").value = "";
  await refreshTopicEntries();
}

export async function deleteAllTopicPacks() {
  const data = await api("/topic-packs", { method: "DELETE" });
  log("所有資料包已清空", data);
  $("topicPackSelect").value = "";
  $("topicEntrySelect").value = "";
  state.topicEntries = [];
  fillTopicPackForm(null);
  fillTopicEntryForm(null);
  await refreshTopicPacks();
  $("topicPackSelect").value = "";
  await refreshTopicEntries();
}

export async function linkTopicPack() {
  const id = selectedSessionId();
  const packId = Number($("topicPackSelect").value || 0);
  if (!id || !packId) throw new Error("請先選擇 Live Session 與資料包");
  const data = await api(`/sessions/${encodeURIComponent(id)}/topic-packs/${packId}`, {
    method: "POST",
    body: "{}",
  });
  log("資料包已綁定直播", data);
  await refreshTopicEntries();
}

export async function bindSessionTopicPack(sessionId = selectedSessionId()) {
  const packId = Number($("sessionTopicPackSelect").value || 0);
  if (!sessionId) return null;
  if (!packId) {
    const data = await api(`/sessions/${encodeURIComponent(sessionId)}/topic-packs`, {
      method: "DELETE",
    });
    $("topicPackSelect").value = "";
    log("直播已解除話題資料包綁定", data);
    return data;
  }
  const data = await api(`/sessions/${encodeURIComponent(sessionId)}/topic-packs/${packId}?replace=true`, {
    method: "POST",
    body: "{}",
  });
  $("topicPackSelect").value = String(packId);
  log("直播已綁定話題資料包", data);
  return data;
}

export async function addTopicEntry() {
  const packId = Number($("topicPackSelect").value || 0);
  if (!packId) throw new Error("請先選擇資料包");
  const data = await api(`/topic-packs/${packId}/entries`, {
    method: "POST",
    body: JSON.stringify(topicEntryPayload()),
  });
  log("fact card 已新增", data);
  fillTopicEntryForm(null);
  await refreshTopicEntries();
  $("topicEntrySelect").value = String(data.id);
  fillTopicEntryForm(selectedTopicEntry());
}

export async function updateTopicEntry() {
  const entryId = currentTopicEntryId();
  const entry = topicEntryById(entryId) || selectedTopicEntry();
  const packId = Number(entry?.pack_id || $("topicPackSelect").value || 0);
  if (!packId || !entryId) throw new Error("請先選擇資料包與 fact card");
  const payload = topicEntryPayload();
  setTopicEntryEditorBusy(true);
  try {
    const data = await api(`/topic-packs/${packId}/entries/${entryId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    log("fact card 已更新，已清空編輯區", data);
    await refreshTopicEntries();
    fillTopicEntryForm(null);
  } finally {
    setTopicEntryEditorBusy(false);
  }
}

export async function deleteTopicEntry(entryId = null) {
  const entry = topicEntryById(entryId) || selectedTopicEntry();
  const packId = Number(entry?.pack_id || $("topicPackSelect").value || 0);
  if (!packId || !entry) throw new Error("請先選擇資料包與 fact card");
  const data = await api(`/topic-packs/${packId}/entries/${entry.id}`, {
    method: "DELETE",
  });
  log("fact card 已刪除", data);
  if (currentTopicEntryId() === Number(entry.id)) {
    fillTopicEntryForm(null);
  }
  await refreshTopicEntries();
}

export async function importFactCardsFolder() {
  if (factCardActionsBlockedDuringLive()) throw new Error("直播中不產生或匯入 Fact Cards");
  const packId = Number($("topicPackSelect").value || 0) || null;
  setFactCardImportBusy(true);
  try {
    const data = await api("/topic-packs/fact-cards/import-folder", {
      method: "POST",
      body: JSON.stringify({
        pack_id: packId,
        max_files: 50,
      }),
    });
    log("FactCards 資料夾已匯入", data);
    await refreshTopicPacks();
    $("topicPackSelect").value = data.pack_id;
    await refreshTopicEntries();
  } finally {
    setFactCardImportBusy(false);
  }
}

export async function rebuildTopicEmbeddings() {
  const packId = Number($("topicPackSelect").value || 0);
  if (!packId) throw new Error("請先選擇資料包");
  const data = await api(`/topic-packs/${packId}/embeddings/rebuild`, {
    method: "POST",
    body: "{}",
  });
  log("向量索引已重建", data);
  await refreshTopicEntries();
}
