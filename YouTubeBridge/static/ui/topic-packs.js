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
  setTopicActionVisible("refreshTopicGraph", hasPack);
  setTopicActionVisible("rebuildTopicGraph", hasPack);
  setTopicActionVisible("refreshTopicGraphTrace", hasPack && hasSession);
  $("topicEntryPanel").classList.toggle("is-hidden", !hasPack);
  $("topicGraphPanel").classList.toggle("is-hidden", !hasPack);
  $("topicFactCardLiveLockNotice").classList.toggle("is-hidden", !(hasPack && liveLocked));

  $("createTopicPack").disabled = hasPack || !hasPackTitle;
  $("updateTopicPack").disabled = !hasPack || !hasPackTitle;
  $("addTopicEntry").disabled = !hasPack || hasEntry || !hasEntryContent || entryBusy;
  $("updateTopicEntry").disabled = !hasPack || !hasEntry || !hasEntryContent || entryBusy;
  $("cancelTopicEntryEdit").disabled = !hasPack || !hasEntry || entryBusy;
  $("importFactCardsFolder").disabled = !hasPack || liveLocked || importBusy;
  $("refreshTopicGraph").disabled = !hasPack;
  $("rebuildTopicGraph").disabled = !hasPack;
  $("refreshTopicGraphTrace").disabled = !hasPack || !hasSession;
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
  await refreshTopicGraph();
  await refreshTopicGraphTrace();
  updateTopicActionVisibility();
}

function topicGraphColor(type) {
  return {
    document: "#64748b",
    category: "#38bdf8",
    topic: "#e5e7eb",
    detail: "#8b5cf6",
    entity: "#22c55e",
    reference: "#9ca3af",
  }[type] || "#cbd5e1";
}

function topicGraphPoint(index, total) {
  const width = 720;
  const height = 520;
  const radius = Math.min(width * 0.42, height * 0.39);
  const angle = total <= 1 ? 0 : (Math.PI * 2 * index) / total - Math.PI / 2;
  return {
    x: width / 2 + Math.cos(angle) * radius,
    y: height / 2 + Math.sin(angle) * radius,
  };
}

function topicGraphLabel(node, point) {
  const width = 720;
  const height = 520;
  const rawTitle = String(node.title || "");
  const limit = node.node_type === "entity" ? 14 : 16;
  const title = rawTitle.length > limit ? `${rawTitle.slice(0, limit)}...` : rawTitle;
  if (Math.abs(point.x - width / 2) < 28) {
    return {
      title,
      x: point.x,
      y: point.y < height / 2 ? point.y - 14 : point.y + 22,
      anchor: "middle",
    };
  }
  if (point.x < width / 2) {
    return {
      title,
      x: point.x - 10,
      y: point.y + 4,
      anchor: "end",
    };
  }
  return {
    title,
    x: point.x + 10,
    y: point.y + 4,
    anchor: "start",
  };
}

export function selectTopicGraphNode(nodeId) {
  state.selectedTopicGraphNodeId = Number(nodeId || 0);
  const node = (state.topicGraph.nodes || []).find((item) => Number(item.id) === state.selectedTopicGraphNodeId);
  if (!node) {
    $("topicGraphSelectedNode").textContent = "尚未選擇節點";
    renderTopicGraph();
    return;
  }
  const edges = (state.topicGraph.edges || []).filter((edge) =>
    Number(edge.source_node_id) === Number(node.id) || Number(edge.target_node_id) === Number(node.id)
  );
  $("topicGraphSelectedNode").innerHTML = `
    <strong>${escapeHtml(node.title)}</strong>
    <p>${escapeHtml(node.node_type)} · ${escapeHtml(node.source_name || "no source")}</p>
    <p>${escapeHtml(String(node.summary || "").slice(0, 220))}</p>
    <p class="muted">${edges.length} 條關聯</p>
  `;
  renderTopicGraph();
}

export function renderTopicGraph() {
  const svg = $("topicGraphSvg");
  if (!svg) return;
  const nodes = state.topicGraph.nodes || [];
  const edges = state.topicGraph.edges || [];
  if (!nodes.length) {
    svg.innerHTML = `<text x="24" y="40" fill="#94a3b8">尚無 topic graph</text>`;
    return;
  }
  const positions = new Map(nodes.map((node, index) => [Number(node.id), topicGraphPoint(index, nodes.length)]));
  const traceNodeIds = new Set((state.topicGraphLatestTrace?.selected_node_ids || []).map((id) => Number(id)));
  const edgeHtml = edges.map((edge) => {
    const start = positions.get(Number(edge.source_node_id));
    const end = positions.get(Number(edge.target_node_id));
    if (!start || !end) return "";
    const isTrace = traceNodeIds.has(Number(edge.source_node_id)) && traceNodeIds.has(Number(edge.target_node_id));
    return `<line class="topic-graph-edge ${isTrace ? "is-trace" : ""}" x1="${start.x.toFixed(1)}" y1="${start.y.toFixed(1)}" x2="${end.x.toFixed(1)}" y2="${end.y.toFixed(1)}"></line>`;
  }).join("");
  const nodeHtml = nodes.map((node) => {
    const point = positions.get(Number(node.id));
    const selected = Number(node.id) === Number(state.selectedTopicGraphNodeId);
    const radius = traceNodeIds.has(Number(node.id)) ? 8 : 6;
    const label = topicGraphLabel(node, point);
    return `
      <g class="topic-graph-node ${selected ? "is-selected" : ""}" data-topic-graph-node="${escapeHtml(node.id)}">
        <title>${escapeHtml(node.title || "")}</title>
        <circle cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="${radius}" fill="${topicGraphColor(node.node_type)}"></circle>
        <text x="${label.x.toFixed(1)}" y="${label.y.toFixed(1)}" text-anchor="${label.anchor}">${escapeHtml(label.title)}</text>
      </g>
    `;
  }).join("");
  svg.innerHTML = edgeHtml + nodeHtml;
  svg.querySelectorAll("[data-topic-graph-node]").forEach((item) => {
    item.addEventListener("click", () => selectTopicGraphNode(item.dataset.topicGraphNode));
  });
}

export function renderTopicGraphTrace() {
  const latest = state.topicGraphLatestTrace;
  $("topicGraphLatestTrace").innerHTML = latest
    ? `<strong>${escapeHtml(latest.source || "trace")}</strong><p>${escapeHtml(latest.query_text || "")}</p><p class="muted">selected: ${(latest.selected_node_ids || []).join(", ")}</p>`
    : "尚無召回路徑";
  $("topicGraphTraces").innerHTML = (state.topicGraphTraces || []).map((trace) => `
    <div class="item">
      <strong>${escapeHtml(trace.source || "trace")}</strong>
      <p>${escapeHtml(trace.query_text || "")}</p>
      <p class="muted">${escapeHtml(trace.created_at || "")}</p>
    </div>
  `).join("") || `<div class="muted">尚無 trace</div>`;
}

export async function refreshTopicGraph() {
  const packId = Number($("topicPackSelect").value || 0);
  if (!packId) {
    state.topicGraph = { nodes: [], edges: [] };
    $("topicGraphState").textContent = "尚未載入";
    renderTopicGraph();
    return null;
  }
  const graph = await api(`/topic-packs/${packId}/graph`);
  state.topicGraph = graph;
  $("topicGraphState").textContent = `${(graph.nodes || []).length} 節點 / ${(graph.edges || []).length} 關聯`;
  $("topicGraphState").className = "status good";
  renderTopicGraph();
  return graph;
}

export async function rebuildTopicGraph() {
  const packId = Number($("topicPackSelect").value || 0);
  if (!packId) throw new Error("請先選擇資料包");
  const data = await api(`/topic-packs/${packId}/graph/rebuild`, {
    method: "POST",
    body: "{}",
  });
  log("Topic Graph 已重建", data);
  await refreshTopicGraph();
  return data;
}

export async function refreshTopicGraphTrace() {
  const id = selectedSessionId();
  if (!id) {
    state.topicGraphTraces = [];
    state.topicGraphLatestTrace = null;
    renderTopicGraphTrace();
    renderTopicGraph();
    return null;
  }
  const traces = await api(`/sessions/${encodeURIComponent(id)}/topic-graph/traces?limit=20`);
  const latest = await api(`/sessions/${encodeURIComponent(id)}/topic-graph/latest-trace`);
  state.topicGraphTraces = traces.traces || [];
  state.topicGraphLatestTrace = latest.trace || null;
  renderTopicGraphTrace();
  renderTopicGraph();
  return traces;
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
