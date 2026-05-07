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

export function setTopicGraphBusy(action = "", message = "") {
  state.topicGraphBusy = !!action;
  $("refreshTopicGraph").textContent = action === "refresh" ? "刷新中..." : "刷新關係圖";
  $("rebuildTopicGraph").textContent = action === "rebuild" ? "重建中..." : "重建關係圖";
  $("refreshTopicGraphTrace").textContent = action === "trace" ? "刷新中..." : "刷新召回路徑";
  if (action && message) {
    $("topicGraphState").textContent = message;
    $("topicGraphState").className = "status";
  }
  updateTopicActionVisibility();
}

export function setTopicGraphLoadedState(graph = state.topicGraph) {
  $("topicGraphState").textContent = `${(graph.nodes || []).length} 節點 / ${(graph.edges || []).length} 關聯`;
  $("topicGraphState").className = "status good";
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
  } else if (!previousPackId && state.topicPacks.length === 1) {
    $("topicPackSelect").value = String(state.topicPacks[0].id);
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

const TOPIC_GRAPH_WIDTH = 720;
const TOPIC_GRAPH_HEIGHT = 520;
const TOPIC_GRAPH_MIN_SCALE = 0.55;
const TOPIC_GRAPH_MAX_SCALE = 3.2;
const TOPIC_GRAPH_NODE_CLICK_SLOP_PX = 5;
let topicGraphDrag = null;
let topicGraphLastNodeDrag = null;

function topicGraphNodePriority(node, degree = 0) {
  const typePriority = {
    document: 60,
    entity: 52,
    topic: 50,
    detail: 42,
    category: 36,
    reference: 34,
  }[node.node_type] || 10;
  return typePriority + Math.min(18, degree);
}

function topicGraphDegreeMap(edges) {
  const degree = new Map();
  (edges || []).forEach((edge) => {
    const source = Number(edge.source_node_id);
    const target = Number(edge.target_node_id);
    degree.set(source, (degree.get(source) || 0) + 1);
    degree.set(target, (degree.get(target) || 0) + 1);
  });
  return degree;
}

function topicGraphLayerKey(node) {
  return {
    document: "document",
    category: "topic",
    topic: "topic",
    detail: "detail",
    entity: "outer",
    reference: "outer",
  }[node.node_type] || "outer";
}

function topicGraphLayerConfig(layer) {
  return {
    document: { radius: 0, gap: 0, angleOffset: 0, yScale: 1 },
    topic: { radius: 82, gap: 44, angleOffset: -Math.PI / 2, yScale: 0.72 },
    detail: { radius: 155, gap: 48, angleOffset: -Math.PI / 2 + 0.34, yScale: 0.76 },
    outer: { radius: 226, gap: 52, angleOffset: -Math.PI / 2 + 0.18, yScale: 0.78 },
  }[layer] || { radius: 215, gap: 48, angleOffset: -Math.PI / 2, yScale: 0.76 };
}

function topicGraphLayerPoint(itemIndex, itemTotal, config) {
  if (!config.radius) {
    const spread = itemTotal <= 1 ? 0 : 18;
    return {
      x: TOPIC_GRAPH_WIDTH / 2 + ((itemIndex % 3) - 1) * spread,
      y: TOPIC_GRAPH_HEIGHT / 2 + (Math.floor(itemIndex / 3) - 0.5) * spread,
    };
  }
  const firstCapacity = Math.max(6, Math.floor((2 * Math.PI * config.radius) / 92));
  let ring = 0;
  let remaining = itemIndex;
  let capacity = firstCapacity;
  while (remaining >= capacity) {
    remaining -= capacity;
    ring += 1;
    capacity = Math.max(8, Math.floor((2 * Math.PI * (config.radius + ring * config.gap)) / 92));
  }
  const radius = config.radius + ring * config.gap;
  const ringCount = Math.min(capacity, itemTotal - (itemIndex - remaining));
  const angle = config.angleOffset + (Math.PI * 2 * (remaining + 0.5 * ring)) / Math.max(1, ringCount);
  return {
    x: TOPIC_GRAPH_WIDTH / 2 + Math.cos(angle) * radius,
    y: TOPIC_GRAPH_HEIGHT / 2 + Math.sin(angle) * radius * config.yScale,
  };
}

function topicGraphLayout(nodes, edges) {
  const degree = topicGraphDegreeMap(edges);
  const groups = new Map();
  (nodes || []).forEach((node) => {
    const key = topicGraphLayerKey(node);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(node);
  });
  const positions = new Map();
  ["document", "topic", "detail", "outer"].forEach((layer) => {
    const items = (groups.get(layer) || []).slice().sort((left, right) => {
      const delta = topicGraphNodePriority(right, degree.get(Number(right.id)) || 0)
        - topicGraphNodePriority(left, degree.get(Number(left.id)) || 0);
      return delta || String(left.title || "").localeCompare(String(right.title || ""), "zh-Hant");
    });
    const config = topicGraphLayerConfig(layer);
    items.forEach((node, index) => {
      positions.set(Number(node.id), topicGraphLayerPoint(index, items.length, config));
    });
  });
  return positions;
}

function topicGraphPositions(nodes, edges) {
  const positions = topicGraphLayout(nodes, edges);
  const liveNodeIds = new Set((nodes || []).map((node) => String(node.id)));
  Object.keys(state.topicGraphNodePositions || {}).forEach((nodeId) => {
    if (!liveNodeIds.has(nodeId)) delete state.topicGraphNodePositions[nodeId];
  });
  (nodes || []).forEach((node) => {
    const saved = state.topicGraphNodePositions?.[String(node.id)];
    if (!saved) return;
    positions.set(Number(node.id), {
      x: Number(saved.x || 0),
      y: Number(saved.y || 0),
    });
  });
  return positions;
}

function topicGraphRelatedNodeIds(selectedNodeId, edges) {
  const selected = Number(selectedNodeId || 0);
  const relatedNodeIds = new Set();
  if (!selected) return relatedNodeIds;
  relatedNodeIds.add(selected);
  (edges || []).forEach((edge) => {
    const source = Number(edge.source_node_id);
    const target = Number(edge.target_node_id);
    if (source === selected) relatedNodeIds.add(target);
    if (target === selected) relatedNodeIds.add(source);
  });
  return relatedNodeIds;
}

function topicGraphAutoFocusNodeIds(trace, edges) {
  const focusNodeIds = new Set();
  if (!state.topicGraphTraceAutoFollow || !trace) return focusNodeIds;
  const rootNodeIds = new Set();
  ["selected_node_ids", "entry_node_ids", "expanded_node_ids"].forEach((field) => {
    (trace[field] || []).forEach((rawId) => {
      const nodeId = Number(rawId || 0);
      if (!nodeId) return;
      focusNodeIds.add(nodeId);
      rootNodeIds.add(nodeId);
    });
  });
  (edges || []).forEach((edge) => {
    const source = Number(edge.source_node_id);
    const target = Number(edge.target_node_id);
    if (rootNodeIds.has(source)) focusNodeIds.add(target);
    if (rootNodeIds.has(target)) focusNodeIds.add(source);
  });
  return focusNodeIds;
}

function topicGraphPrimaryTraceNodeId(trace) {
  if (!trace) return 0;
  for (const field of ["entry_node_ids", "selected_node_ids", "expanded_node_ids"]) {
    const nodeId = Number((trace[field] || [])[0] || 0);
    if (nodeId) return nodeId;
  }
  return 0;
}

function topicGraphNodeClass(node, selected, relatedNodeIds, focusNodeIds, traceNodeIds, primaryTraceNodeId) {
  const nodeId = Number(node.id);
  const classes = ["topic-graph-node"];
  if (nodeId === selected) classes.push("is-selected");
  if (traceNodeIds?.has(nodeId)) classes.push("is-recalled-trace");
  if (nodeId === Number(primaryTraceNodeId || 0)) classes.push("is-active-trace");
  if (selected) {
    if (relatedNodeIds.has(nodeId)) classes.push("is-focused");
    if (!relatedNodeIds.has(nodeId)) classes.push("is-dimmed");
  } else if (focusNodeIds?.size) {
    if (focusNodeIds.has(nodeId)) classes.push("is-focused");
    if (!focusNodeIds.has(nodeId)) classes.push("is-dimmed");
  }
  return classes.join(" ");
}

function topicGraphEdgeClass(edge, traceNodeIds, selected, relatedNodeIds, focusNodeIds) {
  const source = Number(edge.source_node_id);
  const target = Number(edge.target_node_id);
  const classes = ["topic-graph-edge"];
  if (traceNodeIds.has(source) && traceNodeIds.has(target)) classes.push("is-trace");
  if (selected) {
    if (relatedNodeIds.has(source) && relatedNodeIds.has(target)) classes.push("is-focused");
    if (!(relatedNodeIds.has(source) && relatedNodeIds.has(target))) classes.push("is-dimmed");
  } else if (focusNodeIds?.size) {
    if (focusNodeIds.has(source) && focusNodeIds.has(target)) classes.push("is-focused");
    if (!(focusNodeIds.has(source) && focusNodeIds.has(target))) classes.push("is-dimmed");
  }
  return classes.join(" ");
}

function topicGraphLabel(node, point) {
  const rawTitle = String(node.title || "");
  const limit = node.node_type === "entity" || node.node_type === "reference" ? 12 : 17;
  const title = rawTitle.length > limit ? `${rawTitle.slice(0, limit)}...` : rawTitle;
  const clampY = (value) => Math.max(18, Math.min(TOPIC_GRAPH_HEIGHT - 12, value));
  if (Math.abs(point.x - TOPIC_GRAPH_WIDTH / 2) < 28) {
    return {
      title,
      x: point.x,
      y: clampY(point.y < TOPIC_GRAPH_HEIGHT / 2 ? point.y - 14 : point.y + 22),
      anchor: "middle",
    };
  }
  if (point.x < 130) {
    return {
      title,
      x: point.x + 10,
      y: clampY(point.y + 4),
      anchor: "start",
    };
  }
  if (point.x > TOPIC_GRAPH_WIDTH - 130 || point.x < TOPIC_GRAPH_WIDTH / 2) {
    return {
      title,
      x: point.x - 10,
      y: clampY(point.y + 4),
      anchor: "end",
    };
  }
  return {
    title,
    x: point.x + 10,
    y: clampY(point.y + 4),
    anchor: "start",
  };
}

function topicGraphLabelBox(label) {
  const width = Math.min(142, Math.max(34, String(label.title || "").length * 6.2));
  const x = label.anchor === "middle" ? label.x - width / 2 : label.anchor === "end" ? label.x - width : label.x;
  return { x, y: label.y - 11, width, height: 16 };
}

function topicGraphBoxesOverlap(left, right, padding = 5) {
  return !(
    left.x + left.width + padding < right.x
    || right.x + right.width + padding < left.x
    || left.y + left.height + padding < right.y
    || right.y + right.height + padding < left.y
  );
}

function shouldRenderTopicGraphLabel(label, placedBoxes, force = false) {
  const box = topicGraphLabelBox(label);
  if (!force && placedBoxes.some((placed) => topicGraphBoxesOverlap(box, placed))) {
    return false;
  }
  placedBoxes.push(box);
  return true;
}

function topicGraphLabelCandidateForce(node, selected, relatedNodeIds, traceNodeIds, focusNodeIds = new Set()) {
  const nodeId = Number(node.id);
  return nodeId === Number(selected || 0)
    || traceNodeIds.has(nodeId)
    || focusNodeIds.has(nodeId)
    || (selected && relatedNodeIds.has(nodeId));
}

function topicGraphLabelCandidateVisible(candidate, selected, visibleCount, maxVisibleLabels) {
  if (candidate.force) return true;
  if (selected) return false;
  return visibleCount < maxVisibleLabels;
}

function topicGraphTransform() {
  const viewport = state.topicGraphViewport || { scale: 1, x: 0, y: 0 };
  return `translate(${Number(viewport.x || 0).toFixed(1)} ${Number(viewport.y || 0).toFixed(1)}) scale(${Number(viewport.scale || 1).toFixed(3)})`;
}

function clampTopicGraphScale(scale) {
  return Math.max(TOPIC_GRAPH_MIN_SCALE, Math.min(TOPIC_GRAPH_MAX_SCALE, Number(scale || 1)));
}

function topicGraphSvgPoint(svg, event) {
  const rect = svg.getBoundingClientRect();
  const x = ((event.clientX - rect.left) / Math.max(1, rect.width)) * TOPIC_GRAPH_WIDTH;
  const y = ((event.clientY - rect.top) / Math.max(1, rect.height)) * TOPIC_GRAPH_HEIGHT;
  return { x, y };
}

function zoomTopicGraph(deltaY, center) {
  const current = state.topicGraphViewport || { scale: 1, x: 0, y: 0 };
  const nextScale = clampTopicGraphScale(current.scale * (deltaY < 0 ? 1.14 : 0.88));
  const ratio = nextScale / Math.max(0.01, current.scale);
  state.topicGraphViewport = {
    scale: nextScale,
    x: center.x - (center.x - current.x) * ratio,
    y: center.y - (center.y - current.y) * ratio,
  };
  renderTopicGraph();
}

function beginTopicGraphNodeDrag(svg, event, nodeId, point) {
  event.preventDefault();
  event.stopPropagation();
  const viewport = state.topicGraphViewport || { scale: 1, x: 0, y: 0 };
  topicGraphLastNodeDrag = null;
  topicGraphDrag = {
    nodeId: Number(nodeId),
    pointerId: event.pointerId,
    clientX: event.clientX,
    clientY: event.clientY,
    x: Number(point.x || 0),
    y: Number(point.y || 0),
    rect: svg.getBoundingClientRect(),
    scale: Number(viewport.scale || 1),
    moved: false,
  };
  svg.setPointerCapture?.(event.pointerId);
}

function clearRecentTopicGraphNodeDrag() {
  setTimeout(() => { topicGraphLastNodeDrag = null; }, 180);
}

export function resetTopicGraphView() {
  state.topicGraphViewport = { scale: 1, x: 0, y: 0 };
  renderTopicGraph();
}

export function openTopicGraphModal() {
  state.topicGraphModalOpen = true;
  $("topicGraphModal").classList.remove("is-hidden");
  $("topicGraphModal").setAttribute("aria-hidden", "false");
  document.body.classList.add("topic-graph-modal-open");
  renderTopicGraph();
}

export function closeTopicGraphModal() {
  state.topicGraphModalOpen = false;
  $("topicGraphModal").classList.add("is-hidden");
  $("topicGraphModal").setAttribute("aria-hidden", "true");
  document.body.classList.remove("topic-graph-modal-open");
}

function bindTopicGraphViewportControls(svg) {
  if (!svg || svg.dataset.viewportBound === "true") return;
  svg.dataset.viewportBound = "true";
  let panStart = null;
  let lastPanMoved = false;
  const onWheel = (event) => {
    event.preventDefault();
    zoomTopicGraph(event.deltaY, topicGraphSvgPoint(svg, event));
  };
  const onPointerDown = (event) => {
    if (event.button !== 0 || event.target.closest?.("[data-topic-graph-node]")) return;
    const viewport = state.topicGraphViewport || { scale: 1, x: 0, y: 0 };
    panStart = {
      pointerId: event.pointerId,
      clientX: event.clientX,
      clientY: event.clientY,
      x: viewport.x,
      y: viewport.y,
      rect: svg.getBoundingClientRect(),
      moved: false,
    };
    svg.classList.add("is-panning");
    svg.setPointerCapture?.(event.pointerId);
  };
  const onPointerMove = (event) => {
    if (topicGraphDrag && event.pointerId === topicGraphDrag.pointerId) {
      const clientDx = event.clientX - topicGraphDrag.clientX;
      const clientDy = event.clientY - topicGraphDrag.clientY;
      if (!topicGraphDrag.moved && Math.hypot(clientDx, clientDy) < TOPIC_GRAPH_NODE_CLICK_SLOP_PX) return;
      const dx = (clientDx / Math.max(1, topicGraphDrag.rect.width)) * TOPIC_GRAPH_WIDTH / Math.max(0.01, topicGraphDrag.scale);
      const dy = (clientDy / Math.max(1, topicGraphDrag.rect.height)) * TOPIC_GRAPH_HEIGHT / Math.max(0.01, topicGraphDrag.scale);
      topicGraphDrag.moved = true;
      state.topicGraphNodePositions[String(topicGraphDrag.nodeId)] = {
        x: topicGraphDrag.x + dx,
        y: topicGraphDrag.y + dy,
      };
      renderTopicGraph();
      return;
    }
    if (!panStart || event.pointerId !== panStart.pointerId) return;
    const dx = ((event.clientX - panStart.clientX) / Math.max(1, panStart.rect.width)) * TOPIC_GRAPH_WIDTH;
    const dy = ((event.clientY - panStart.clientY) / Math.max(1, panStart.rect.height)) * TOPIC_GRAPH_HEIGHT;
    if (Math.abs(dx) > 1 || Math.abs(dy) > 1) panStart.moved = true;
    state.topicGraphViewport = {
      ...(state.topicGraphViewport || { scale: 1 }),
      x: panStart.x + dx,
      y: panStart.y + dy,
    };
    renderTopicGraph();
  };
  const finishPan = (event) => {
    if (topicGraphDrag && event.pointerId === topicGraphDrag.pointerId) {
      svg.releasePointerCapture?.(event.pointerId);
      const completedDrag = topicGraphDrag;
      topicGraphLastNodeDrag = {
        nodeId: completedDrag.nodeId,
        moved: !!completedDrag.moved,
        handled: !completedDrag.moved,
      };
      topicGraphDrag = null;
      clearRecentTopicGraphNodeDrag();
      if (!completedDrag.moved) toggleTopicGraphNodeSelection(completedDrag.nodeId);
    }
    if (panStart && event.pointerId === panStart.pointerId) {
      svg.releasePointerCapture?.(event.pointerId);
      lastPanMoved = !!panStart.moved;
      setTimeout(() => { lastPanMoved = false; }, 0);
    }
    panStart = null;
    svg.classList.remove("is-panning");
  };
  const onSvgClick = (event) => {
    if (event.target.closest?.("[data-topic-graph-node]")) return;
    if (lastPanMoved) return;
    clearTopicGraphSelection();
  };
  svg.addEventListener("wheel", onWheel, { passive: false });
  svg.addEventListener("pointerdown", onPointerDown);
  svg.addEventListener("pointermove", onPointerMove);
  svg.addEventListener("pointerup", finishPan);
  svg.addEventListener("pointercancel", finishPan);
  svg.addEventListener("click", onSvgClick);
}

export function clearTopicGraphSelection() {
  state.selectedTopicGraphNodeId = 0;
  renderTopicGraphSelectedNodeDetails(null, []);
  renderTopicGraph();
}

function toggleTopicGraphNodeSelection(nodeId) {
  if (Number(nodeId || 0) === Number(state.selectedTopicGraphNodeId || 0)) {
    clearTopicGraphSelection();
    return;
  }
  selectTopicGraphNode(nodeId);
}

function renderTopicGraphAutoFocusDetails() {
  const latest = state.topicGraphLatestTrace;
  const selectedIds = (latest?.selected_node_ids || []).map((id) => Number(id || 0)).filter(Boolean);
  if (!latest || !selectedIds.length) return "尚未選擇節點";
  const primaryNodeId = topicGraphPrimaryTraceNodeId(latest);
  const nodesById = new Map((state.topicGraph.nodes || []).map((node) => [Number(node.id), node]));
  const primaryNode = nodesById.get(primaryNodeId);
  const supportingIds = selectedIds.filter((nodeId) => nodeId !== primaryNodeId);
  const focusTitles = supportingIds.slice(0, 8).map((nodeId) => {
    const node = nodesById.get(nodeId);
    return node ? node.title : `node ${nodeId}`;
  });
  const remaining = Math.max(0, supportingIds.length - focusTitles.length);
  return `
    <strong>目前召回焦點</strong>
    <p>${escapeHtml(latest.source || "trace")} · 自動跟隨</p>
    <p><span class="status good">主焦點</span> ${escapeHtml(primaryNode?.title || `node ${primaryNodeId}`)}</p>
    <p>${escapeHtml(latest.query_text || "無查詢文字")}</p>
    <p class="muted">補充召回：${focusTitles.map((title) => escapeHtml(title)).join("、") || "無"}${remaining ? ` 等 ${remaining} 個節點` : ""}</p>
  `;
}

function renderTopicGraphSelectedNodeDetails(node, edges = []) {
  const html = node ? `
    <strong>${escapeHtml(node.title)}</strong>
    <p>${escapeHtml(node.node_type)} · ${escapeHtml(node.source_name || "no source")}</p>
    <p>${escapeHtml(String(node.summary || "").slice(0, 360))}</p>
    <p class="muted">${edges.length} 條關聯</p>
  ` : renderTopicGraphAutoFocusDetails();
  ["topicGraphSelectedNode", "topicGraphModalDetails"].forEach((id) => {
    const element = $(id);
    if (!element) return;
    if (node || html !== "尚未選擇節點") {
      element.innerHTML = html;
    } else {
      element.textContent = html;
    }
  });
}

export function selectTopicGraphNode(nodeId) {
  state.selectedTopicGraphNodeId = Number(nodeId || 0);
  const node = (state.topicGraph.nodes || []).find((item) => Number(item.id) === state.selectedTopicGraphNodeId);
  if (!node) {
    clearTopicGraphSelection();
    return;
  }
  const edges = (state.topicGraph.edges || []).filter((edge) =>
    Number(edge.source_node_id) === Number(node.id) || Number(edge.target_node_id) === Number(node.id)
  );
  renderTopicGraphSelectedNodeDetails(node, edges);
  renderTopicGraph();
}

function renderTopicGraphToSvg(svg) {
  if (!svg) return;
  bindTopicGraphViewportControls(svg);
  const nodes = state.topicGraph.nodes || [];
  const edges = state.topicGraph.edges || [];
  if (!nodes.length) {
    svg.innerHTML = `<text x="24" y="40" fill="#94a3b8">尚無 topic graph</text>`;
    return;
  }
  const positions = topicGraphPositions(nodes, edges);
  const traceNodeIds = new Set((state.topicGraphLatestTrace?.selected_node_ids || []).map((id) => Number(id)));
  const selected = Number(state.selectedTopicGraphNodeId || 0);
  const relatedNodeIds = topicGraphRelatedNodeIds(selected, edges);
  const focusNodeIds = selected ? relatedNodeIds : topicGraphAutoFocusNodeIds(state.topicGraphLatestTrace, edges);
  const primaryTraceNodeId = topicGraphPrimaryTraceNodeId(state.topicGraphLatestTrace);
  const degree = topicGraphDegreeMap(edges);
  const edgeHtml = edges.map((edge) => {
    const start = positions.get(Number(edge.source_node_id));
    const end = positions.get(Number(edge.target_node_id));
    if (!start || !end) return "";
    return `<line class="${topicGraphEdgeClass(edge, traceNodeIds, selected, relatedNodeIds, focusNodeIds)}" x1="${start.x.toFixed(1)}" y1="${start.y.toFixed(1)}" x2="${end.x.toFixed(1)}" y2="${end.y.toFixed(1)}"></line>`;
  }).join("");
  const placedLabels = [];
  const visibleLabels = new Map();
  const denseGraph = nodes.length > 36;
  const maxVisibleLabels = selected ? Math.max(18, relatedNodeIds.size) : (focusNodeIds.size ? Math.max(18, focusNodeIds.size) : (denseGraph ? 46 : 64));
  nodes.map((node) => {
    const point = positions.get(Number(node.id));
    return {
      node,
      label: topicGraphLabel(node, point),
      force: topicGraphLabelCandidateForce(node, selected, relatedNodeIds, traceNodeIds, focusNodeIds),
      priority: topicGraphNodePriority(node, degree.get(Number(node.id)) || 0),
    };
  }).sort((left, right) => {
    if (left.force !== right.force) return left.force ? -1 : 1;
    return right.priority - left.priority;
  }).forEach((candidate) => {
    if (selected && !relatedNodeIds.has(Number(candidate.node.id))) return;
    if (!selected && focusNodeIds.size && !focusNodeIds.has(Number(candidate.node.id))) return;
    if (!topicGraphLabelCandidateVisible(candidate, selected, visibleLabels.size, maxVisibleLabels)) return;
    if (shouldRenderTopicGraphLabel(candidate.label, placedLabels, candidate.force)) {
      visibleLabels.set(Number(candidate.node.id), candidate.label);
    }
  });
  const nodeHtml = nodes.map((node) => {
    const point = positions.get(Number(node.id));
    const activeTrace = Number(node.id) === primaryTraceNodeId;
    const recalledTrace = traceNodeIds.has(Number(node.id));
    const radius = activeTrace ? 11 : (recalledTrace ? 8 : (focusNodeIds.has(Number(node.id)) ? 7 : 6));
    const label = visibleLabels.get(Number(node.id));
    const pulse = activeTrace
      ? `<circle class="topic-graph-trace-pulse" cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="15"></circle>`
      : "";
    return `
      <g class="${topicGraphNodeClass(node, selected, relatedNodeIds, focusNodeIds, traceNodeIds, primaryTraceNodeId)}" data-topic-graph-node="${escapeHtml(node.id)}">
        <title>${escapeHtml(node.title || "")}</title>
        ${pulse}
        <circle cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="${radius}" fill="${activeTrace ? "#facc15" : topicGraphColor(node.node_type)}"></circle>
        ${label ? `<text x="${label.x.toFixed(1)}" y="${label.y.toFixed(1)}" text-anchor="${label.anchor}">${escapeHtml(label.title)}</text>` : ""}
      </g>
    `;
  }).join("");
  svg.innerHTML = `<g class="topic-graph-viewport" transform="${topicGraphTransform()}">${edgeHtml}${nodeHtml}</g>`;
  svg.querySelectorAll("[data-topic-graph-node]").forEach((item) => {
    const nodeId = Number(item.dataset.topicGraphNode || 0);
    item.addEventListener("pointerdown", (event) => {
      const point = positions.get(nodeId);
      if (point) beginTopicGraphNodeDrag(svg, event, nodeId, point);
    });
    item.addEventListener("click", (event) => {
      event.stopPropagation();
      if (topicGraphLastNodeDrag?.handled || topicGraphLastNodeDrag?.moved) return;
      toggleTopicGraphNodeSelection(item.dataset.topicGraphNode);
    });
  });
}

export function renderTopicGraph() {
  renderTopicGraphToSvg($("topicGraphSvg"));
  renderTopicGraphToSvg($("topicGraphModalSvg"));
}

export function renderTopicGraphTrace() {
  const latest = state.topicGraphLatestTrace;
  $("topicGraphLatestTrace").innerHTML = latest
    ? `<strong>${escapeHtml(latest.source || "trace")} <span class="status good">自動跟隨</span></strong><p>${escapeHtml(latest.query_text || "")}</p><p class="muted">selected: ${(latest.selected_node_ids || []).join(", ")}</p>`
    : "尚無召回路徑";
  $("topicGraphTraces").innerHTML = (state.topicGraphTraces || []).map((trace) => `
    <div class="item">
      <strong>${escapeHtml(trace.source || "trace")}</strong>
      <p>${escapeHtml(trace.query_text || "")}</p>
      <p class="muted">${escapeHtml(trace.created_at || "")}</p>
    </div>
  `).join("") || `<div class="muted">尚無 trace</div>`;
}

export async function refreshTopicGraph(options = {}) {
  const showBusy = options.showBusy !== false;
  const packId = Number($("topicPackSelect").value || 0);
  if (!packId) {
    state.topicGraph = { nodes: [], edges: [] };
    $("topicGraphState").textContent = "尚未載入";
    $("topicGraphState").className = "status";
    renderTopicGraph();
    return null;
  }
  if (showBusy) setTopicGraphBusy("refresh", "正在刷新關係圖...");
  try {
    const graph = await api(`/topic-packs/${packId}/graph`);
    state.topicGraph = graph;
    setTopicGraphLoadedState(graph);
    renderTopicGraph();
    return graph;
  } catch (error) {
    $("topicGraphState").textContent = "關係圖刷新失敗";
    $("topicGraphState").className = "status bad";
    throw error;
  } finally {
    if (showBusy) setTopicGraphBusy();
  }
}

export async function rebuildTopicGraph() {
  const packId = Number($("topicPackSelect").value || 0);
  if (!packId) throw new Error("請先選擇資料包");
  setTopicGraphBusy("rebuild", "正在重建關係圖...");
  try {
    const data = await api(`/topic-packs/${packId}/graph/rebuild`, {
      method: "POST",
      body: "{}",
    });
    log("Topic Graph 已重建", data);
    await refreshTopicGraph({ showBusy: false });
    return data;
  } catch (error) {
    $("topicGraphState").textContent = "關係圖重建失敗";
    $("topicGraphState").className = "status bad";
    throw error;
  } finally {
    setTopicGraphBusy();
  }
}

export async function refreshTopicGraphTrace(options = {}) {
  const showBusy = options.showBusy !== false;
  const id = selectedSessionId();
  if (!id) {
    state.topicGraphTraces = [];
    state.topicGraphLatestTrace = null;
    renderTopicGraphTrace();
    renderTopicGraph();
    return null;
  }
  if (showBusy) setTopicGraphBusy("trace", "正在刷新召回路徑...");
  try {
    const traces = await api(`/sessions/${encodeURIComponent(id)}/topic-graph/traces?limit=20`);
    const latest = await api(`/sessions/${encodeURIComponent(id)}/topic-graph/latest-trace`);
    state.topicGraphTraces = traces.traces || [];
    state.topicGraphLatestTrace = latest.trace || null;
    if (!state.selectedTopicGraphNodeId) renderTopicGraphSelectedNodeDetails(null, []);
    renderTopicGraphTrace();
    renderTopicGraph();
    setTopicGraphLoadedState(state.topicGraph);
    return traces;
  } catch (error) {
    if (showBusy) {
      $("topicGraphState").textContent = "召回路徑刷新失敗";
      $("topicGraphState").className = "status bad";
    }
    throw error;
  } finally {
    if (showBusy) setTopicGraphBusy();
  }
}

export function scheduleTopicGraphTraceRefresh({ reason = "", delayMs = 300 } = {}) {
  if (!state.topicGraphTraceAutoFollow) return;
  const id = selectedSessionId();
  const packId = Number($("topicPackSelect")?.value || 0);
  if (!id || !packId) return;
  if (state.topicGraphTraceRefreshTimer) clearTimeout(state.topicGraphTraceRefreshTimer);
  state.topicGraphTraceRefreshTimer = setTimeout(async () => {
    state.topicGraphTraceRefreshTimer = null;
    try {
      await refreshTopicGraphTrace({ showBusy: false });
    } catch (error) {
      log("Topic Graph 自動跟隨更新失敗", { reason, error: String(error) });
    }
  }, delayMs);
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
    if (data.graph?.status && data.graph.status !== "completed") {
      $("topicGraphState").textContent = "匯入完成，但關係圖建立失敗，請查看 Log 或點重建關係圖";
      $("topicGraphState").className = "status bad";
    }
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
