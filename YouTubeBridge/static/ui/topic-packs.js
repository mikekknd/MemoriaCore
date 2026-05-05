import { $, state, api, escapeHtml, log } from "./core.js";
import { currentTopicEntryId, selectedSessionId, selectedTopicEntry, selectedTopicPack, topicEntryById } from "./selectors.js";

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
  const factCardBusy = !!state.factCardGenerationBusy;
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
  setTopicActionVisible("topicAutoBuildControls", hasSession);
  setTopicActionVisible("autoBuildTopicPack", hasSession);
  setTopicActionVisible("generateGeminiFactCards", true);
  setTopicActionVisible("importFactCardsFolder", true);
  setTopicActionVisible("searchTopicPack", hasPack);
  setTopicActionVisible("restoreTopicEntries", hasPack && state.topicEntrySearchActive);

  $("createTopicPack").disabled = hasPack || !hasPackTitle;
  $("updateTopicPack").disabled = !hasPack || !hasPackTitle;
  $("addTopicEntry").disabled = !hasPack || hasEntry || !hasEntryContent || entryBusy;
  $("updateTopicEntry").disabled = !hasPack || !hasEntry || !hasEntryContent || entryBusy;
  $("cancelTopicEntryEdit").disabled = !hasPack || !hasEntry || entryBusy;
  $("autoBuildTopicPack").disabled = !hasSession || factCardBusy;
  $("generateGeminiFactCards").disabled = factCardBusy;
  $("importFactCardsFolder").disabled = factCardBusy;
}

export function setTopicEntryEditorBusy(isBusy) {
  const busy = !!isBusy;
  state.topicEntryEditorBusy = busy;
  $("topicEntryTitle").disabled = busy;
  $("topicEntryBody").disabled = busy;
  $("updateTopicEntry").textContent = busy ? "儲存中..." : "儲存";
  updateTopicActionVisibility();
}

export function setFactCardGenerationBusy(isBusy, message = "Gemini 正在搜尋並寫入資料卡，請稍候。") {
  const busy = !!isBusy;
  state.factCardGenerationBusy = busy;
  $("factCardGenerationMessage").textContent = message;
  $("factCardGenerationOverlay").classList.toggle("is-hidden", !busy);
  $("factCardGenerationOverlay").setAttribute("aria-hidden", busy ? "false" : "true");
  $("autoBuildTopic").disabled = busy;
  $("generateGeminiFactCards").textContent = busy ? "生成中..." : "依主題生成 Fact Cards";
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
  state.topicPacks = await api("/topic-packs");
  $("topicPackSelect").innerHTML = `<option value="">選擇資料包</option>` + state.topicPacks.map((pack) =>
    `<option value="${escapeHtml(pack.id)}">${escapeHtml(pack.title)}</option>`
  ).join("");
  if (previousPackId && state.topicPacks.some((pack) => Number(pack.id) === previousPackId)) {
    $("topicPackSelect").value = String(previousPackId);
  }
  $("topicPackState").textContent = `${state.topicPacks.length} 個資料包`;
  $("topicPackState").className = "status good";
  fillTopicPackForm(selectedTopicPack());
  await refreshTopicEntries();
}

export async function refreshTopicEntries() {
  const id = selectedSessionId();
  const packId = Number($("topicPackSelect").value || 0);
  const previousEntryId = currentTopicEntryId();
  let entries = [];
  state.topicEntrySearchActive = false;
  if (packId) {
    const data = await api(`/topic-packs/${packId}/entries?limit=80`);
    entries = data.entries || [];
    fillTopicPackForm(selectedTopicPack());
  } else if (id) {
    const data = await api(`/sessions/${encodeURIComponent(id)}/topic-packs`);
    entries = data.entries || [];
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
  await refreshTopicPackUsage();
  updateTopicActionVisibility();
}

export async function refreshTopicPackUsage() {
  const id = selectedSessionId();
  updateTopicActionVisibility();
  if (!id) {
    $("topicPackUsageState").textContent = "已召回 0 / 未使用 0 / 最近補卡：尚無";
    $("topicPackUsageState").className = "status";
    return;
  }
  try {
    const data = await api(`/sessions/${encodeURIComponent(id)}/topic-packs/usage`);
    const recalled = Number(data.used_entry_count || 0);
    const unused = Number(data.unused_entry_count || 0);
    const total = Number(data.total_entries || 0);
    const fallback = data.last_replenish_fallback_mode ? ` (${data.last_replenish_fallback_mode})` : "";
    const research = data.research_gate || {};
    const researchTotal = Number(research.total_count || 0);
    const researchSuccess = Number(research.success_count || 0);
    const researchDegraded = Number(research.degraded_count || 0);
    const researchText = researchTotal
      ? ` / Research Gate：成功 ${researchSuccess} / degraded ${researchDegraded}${researchDegraded > 0 ? "；可手動重試" : ""}`
      : "";
    const recent = data.replenishment_in_progress
      ? "進行中"
      : (data.last_replenished_at ? `${data.last_replenish_reason || "補卡"} ${data.last_replenish_status || ""}${fallback}`.trim() : "尚無");
    const repeat = data.repeated_entry ? `；重複召回：${data.repeated_entry.title || data.repeated_entry.entry_id}` : "";
    $("topicPackUsageState").textContent = `已召回 ${recalled}/${total} / 未使用 ${unused} / 最近補卡：${recent}${repeat}${researchText}`;
    $("topicPackUsageState").className = (data.low_unused || researchDegraded > 0) ? "status warn" : "status good";
  } catch (error) {
    $("topicPackUsageState").textContent = `usage 狀態讀取失敗：${String(error).slice(0, 120)}`;
    $("topicPackUsageState").className = "status warn";
  }
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

export async function autoBuildTopicPack() {
  const id = selectedSessionId();
  if (!id) throw new Error("請先建立或選擇 Live Session");
  const packId = Number($("topicPackSelect").value || 0) || null;
  const data = await api(`/sessions/${encodeURIComponent(id)}/topic-packs/auto-build`, {
    method: "POST",
    body: JSON.stringify({
      topic: $("autoBuildTopic").value.trim(),
      pack_id: packId,
      card_count: Number($("autoBuildCount").value || 5),
      use_research: $("autoBuildUseResearch").checked,
    }),
  });
  log("自動資料卡已建立", data);
  await refreshTopicPacks();
  $("topicPackSelect").value = data.pack_id;
  await refreshTopicEntries();
}

export async function importFactCardsFolder() {
  const packId = Number($("topicPackSelect").value || 0) || null;
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
}

export async function generateGeminiFactCards() {
  const packId = Number($("topicPackSelect").value || 0) || null;
  const topic = $("autoBuildTopic").value.trim();
  if (!topic) throw new Error("請先輸入生成主題");
  setFactCardGenerationBusy(true, "Gemini 正在依主題產生 Fact Cards，可能需要數分鐘。");
  log("Gemini FactCards 開始產生", { topic, pack_id: packId || "auto" });
  try {
    const data = await api("/topic-packs/fact-cards/generate", {
      method: "POST",
      body: JSON.stringify({
        topic,
        pack_id: packId,
        timeout_seconds: 300,
      }),
    });
    log("Gemini FactCards 已產生並匯入", data);
    await refreshTopicPacks();
    $("topicPackSelect").value = data.import?.pack_id || packId || $("topicPackSelect").value;
    await refreshTopicEntries();
    $("autoBuildTopic").value = "";
  } finally {
    setFactCardGenerationBusy(false);
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

export async function searchTopicPack() {
  const packId = Number($("topicPackSelect").value || 0);
  if (!packId) throw new Error("請先選擇資料包");
  const query = $("topicSearchQuery").value.trim();
  if (!query) {
    await restoreTopicEntries();
    return;
  }
  const data = await api(`/topic-packs/${packId}/search?query=${encodeURIComponent(query)}&limit=8`);
  log("向量檢索完成", data);
  const entries = data.entries || [];
  state.topicEntrySearchActive = true;
  state.topicEntries = entries;
  $("topicEntrySelect").innerHTML = `<option value="">選擇 fact card</option>` + entries.map((entry) =>
    `<option value="${escapeHtml(entry.id)}">${escapeHtml(entry.title)}</option>`
  ).join("");
  $("topicPackEntries").innerHTML = renderTopicEntries(entries) || `<div class="muted">沒有找到相關 fact card</div>`;
  bindTopicEntryCardButtons();
  await refreshTopicPackUsage();
  updateTopicActionVisibility();
}

export async function restoreTopicEntries() {
  state.topicEntrySearchActive = false;
  $("topicSearchQuery").value = "";
  await refreshTopicEntries();
}
