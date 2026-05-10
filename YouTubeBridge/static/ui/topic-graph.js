import { $, state, api, escapeHtml, log } from "./core.js";
import { selectedSessionId, selectedTopicPack } from "./selectors.js";
import { updateTopicActionVisibility } from "./topic-packs.js";

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

function topicGraphNodeTypeLabel(type) {
  return {
    document: "來源文件",
    category: "入口文件分類",
    topic: "入口話題",
    detail: "細節卡",
    entity: "作品/人物實體",
    reference: "參照節點",
  }[type] || "其他節點";
}

const TOPIC_GRAPH_WIDTH = 720;
const TOPIC_GRAPH_HEIGHT = 520;
const TOPIC_GRAPH_MIN_SCALE = 0.55;
const TOPIC_GRAPH_MAX_SCALE = 3.2;
const TOPIC_GRAPH_NODE_CLICK_SLOP_PX = 5;
const TOPIC_GRAPH_SOURCE_NODE_TYPES = new Set(["document", "category", "reference"]);
let topicGraphDrag = null;
let topicGraphLastNodeDrag = null;

function topicGraphNodePriority(node, degree = 0) {
  const typePriority = {
    topic: 64,
    detail: 56,
    entity: 48,
    reference: 34,
    category: 28,
    document: 20,
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

function topicGraphNodeVisible(node) {
  return !!state.topicGraphShowSourceNodes || !TOPIC_GRAPH_SOURCE_NODE_TYPES.has(node?.node_type);
}

function topicGraphVisibleGraph() {
  const nodes = (state.topicGraph.nodes || []).filter((node) => topicGraphNodeVisible(node));
  const nodeIds = new Set(nodes.map((node) => Number(node.id)));
  const edges = (state.topicGraph.edges || []).filter((edge) =>
    nodeIds.has(Number(edge.source_node_id)) && nodeIds.has(Number(edge.target_node_id))
  );
  return { nodes, edges: [...edges, ...topicGraphSyntheticSourceEdges(nodes, edges)] };
}

function topicGraphSourceNodeCount() {
  return (state.topicGraph.nodes || []).filter((node) => TOPIC_GRAPH_SOURCE_NODE_TYPES.has(node.node_type)).length;
}

function topicGraphEntitiesFromText(text) {
  const entities = [];
  const seen = new Set();
  for (const match of String(text || "").matchAll(/《([^》]{1,120})》/g)) {
    const entity = String(match[1] || "").trim();
    if (!entity || seen.has(entity)) continue;
    seen.add(entity);
    entities.push(entity);
  }
  return entities;
}

function topicGraphPrimaryEntity(node) {
  const metadata = node?.metadata || {};
  const explicit = String(metadata.primary_entity || metadata.entity || "").trim();
  if (explicit) return explicit;
  const entities = Array.isArray(metadata.entities) ? metadata.entities.filter(Boolean) : [];
  return String(entities[0] || topicGraphEntitiesFromText(`${node?.title || ""}\n${node?.summary || ""}`)[0] || "").trim();
}

function topicGraphSyntheticSourceEdges(nodes, edges) {
  if (!state.topicGraphShowSourceNodes) return [];
  const seen = new Set((edges || []).map((edge) => `${edge.source_node_id}:${edge.target_node_id}:${edge.edge_type}`));
  const syntheticEdges = [];
  const addSyntheticEdge = (sourceNode, targetNode, edgeType, weight = 0.35) => {
    if (!sourceNode || !targetNode || Number(sourceNode.id) === Number(targetNode.id)) return;
    const key = `${Number(sourceNode.id)}:${Number(targetNode.id)}:${edgeType}`;
    if (seen.has(key)) return;
    seen.add(key);
    syntheticEdges.push({
      id: 0,
      source_node_id: Number(sourceNode.id),
      target_node_id: Number(targetNode.id),
      source_node_key: sourceNode.node_key || "",
      target_node_key: targetNode.node_key || "",
      edge_type: edgeType,
      weight,
      evidence: "frontend source debug edge",
    });
  };
  const documents = (nodes || []).filter((node) => node.node_type === "document");
  const categories = (nodes || []).filter((node) => node.node_type === "category");
  const topicsByEntity = new Map();
  (nodes || []).filter((node) => node.node_type === "topic").forEach((node) => {
    const entity = topicGraphPrimaryEntity(node);
    if (entity && !topicsByEntity.has(entity)) topicsByEntity.set(entity, node);
  });
  categories.forEach((category) => {
    documents.forEach((documentNode) => addSyntheticEdge(category, documentNode, "source_file", 0.32));
  });
  documents.forEach((documentNode) => {
    const targetTopic = topicsByEntity.get(topicGraphPrimaryEntity(documentNode));
    if (targetTopic) addSyntheticEdge(documentNode, targetTopic, "source_of", 0.55);
  });
  return syntheticEdges;
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
  if (["document", "category"].includes(candidate.node.node_type) && visibleCount > 8) return false;
  return visibleCount < maxVisibleLabels;
}

function currentTopicGraphPackId() {
  return Number($("topicPackSelect")?.value || 0);
}

function currentTopicGraphLatestTrace() {
  const packId = currentTopicGraphPackId();
  return topicGraphTraceMatchesPack(state.topicGraphLatestTrace, packId) ? state.topicGraphLatestTrace : null;
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

function centerTopicGraphOnNode(nodeId) {
  const visibleGraph = topicGraphVisibleGraph();
  const node = (visibleGraph.nodes || []).find((item) => Number(item.id) === Number(nodeId || 0));
  if (!node) return;
  const positions = topicGraphPositions(visibleGraph.nodes || [], visibleGraph.edges || []);
  const point = positions.get(Number(node.id));
  if (!point) return;
  const current = state.topicGraphViewport || { scale: 1, x: 0, y: 0 };
  const scale = clampTopicGraphScale(Math.max(Number(current.scale || 1), 1.35));
  state.topicGraphViewport = {
    scale,
    x: TOPIC_GRAPH_WIDTH / 2 - point.x * scale,
    y: TOPIC_GRAPH_HEIGHT / 2 - point.y * scale,
  };
}

function jumpToTopicGraphNode(nodeId) {
  const cleanNodeId = Number(nodeId || 0);
  if (!cleanNodeId) return;
  centerTopicGraphOnNode(cleanNodeId);
  selectTopicGraphNode(cleanNodeId);
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
  const latest = currentTopicGraphLatestTrace();
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

function renderTopicGraphLegendHtml() {
  const types = ["topic", "detail", "entity", "document", "category", "reference"];
  return `
    <div class="topic-graph-side-section">
      <strong>節點顏色</strong>
      <div class="topic-graph-legend">
        ${types.map((type) => `
          <span class="topic-graph-legend-item">
            <span class="topic-graph-dot" style="--topic-graph-dot: ${topicGraphColor(type)}"></span>
            ${escapeHtml(topicGraphNodeTypeLabel(type))}
          </span>
        `).join("")}
      </div>
    </div>
  `;
}

function renderTopicGraphSourceToggleHtml() {
  const sourceCount = topicGraphSourceNodeCount();
  const showSources = !!state.topicGraphShowSourceNodes;
  return `
    <div class="topic-graph-side-section">
      <strong>來源節點</strong>
      <button type="button" class="topic-graph-source-toggle" data-topic-graph-toggle-sources="1">
        ${showSources ? "隱藏來源節點" : `顯示來源節點 (${sourceCount})`}
      </button>
      <p class="muted">來源節點只用來追溯 Markdown 檔案與入口文件分類，預設不參與主要話題檢視。</p>
    </div>
  `;
}

function renderTopicGraphEntityListHtml() {
  const nodes = state.topicGraph.nodes || [];
  const edges = state.topicGraph.edges || [];
  const degree = topicGraphDegreeMap(edges);
  const entities = nodes
    .filter((node) => node.node_type === "entity")
    .slice()
    .sort((left, right) => {
      const delta = (degree.get(Number(right.id)) || 0) - (degree.get(Number(left.id)) || 0);
      return delta || String(left.title || "").localeCompare(String(right.title || ""), "zh-Hant");
    });
  if (!entities.length) {
    return `
      <div class="topic-graph-side-section">
        <strong>綠色 entity 節點</strong>
        <p class="muted">目前沒有作品或人物實體節點。</p>
      </div>
    `;
  }
  return `
    <div class="topic-graph-side-section">
      <strong>綠色 entity 節點</strong>
      <div class="topic-graph-entity-list">
        ${entities.map((node) => `
          <button type="button" class="topic-graph-node-link" data-topic-graph-jump="${escapeHtml(node.id)}">
            <span class="topic-graph-dot" style="--topic-graph-dot: ${topicGraphColor("entity")}"></span>
            <span class="topic-graph-node-link-title">${escapeHtml(node.title || `node ${node.id}`)}</span>
            <span class="muted">${degree.get(Number(node.id)) || 0} 關聯</span>
          </button>
        `).join("")}
      </div>
    </div>
  `;
}

function bindTopicGraphDetailsActions(root) {
  root.querySelectorAll("[data-topic-graph-jump]").forEach((button) => {
    button.addEventListener("click", () => jumpToTopicGraphNode(button.dataset.topicGraphJump));
  });
  root.querySelectorAll("[data-topic-graph-toggle-sources]").forEach((button) => {
    button.addEventListener("click", toggleTopicGraphSourceNodes);
  });
}

function renderTopicGraphSelectedNodeDetails(node, edges = []) {
  const html = node ? `
    <strong>${escapeHtml(node.title)}</strong>
    <p>${escapeHtml(node.node_type)} · ${escapeHtml(node.source_name || "no source")}</p>
    <p>${escapeHtml(String(node.summary || "").slice(0, 360))}</p>
    <p class="muted">${edges.length} 條關聯</p>
  ` : renderTopicGraphAutoFocusDetails();
  const mainElement = $("topicGraphSelectedNode");
  if (mainElement) {
    if (node || html !== "尚未選擇節點") mainElement.innerHTML = html;
    else mainElement.textContent = html;
  }
  const modalElement = $("topicGraphModalDetails");
  if (!modalElement) return;
  const modalHtml = `
    ${node || html !== "尚未選擇節點" ? html : `<p class="muted">${escapeHtml(html)}</p>`}
    ${renderTopicGraphSourceToggleHtml()}
    ${renderTopicGraphLegendHtml()}
    ${renderTopicGraphEntityListHtml()}
  `;
  modalElement.innerHTML = modalHtml;
  bindTopicGraphDetailsActions(modalElement);
}

export function selectTopicGraphNode(nodeId) {
  state.selectedTopicGraphNodeId = Number(nodeId || 0);
  const node = (state.topicGraph.nodes || []).find((item) => Number(item.id) === state.selectedTopicGraphNodeId);
  if (!node || !topicGraphNodeVisible(node)) {
    clearTopicGraphSelection();
    return;
  }
  const visibleGraph = topicGraphVisibleGraph();
  const edges = (visibleGraph.edges || []).filter((edge) =>
    Number(edge.source_node_id) === Number(node.id) || Number(edge.target_node_id) === Number(node.id)
  );
  renderTopicGraphSelectedNodeDetails(node, edges);
  renderTopicGraph();
}

function toggleTopicGraphSourceNodes() {
  state.topicGraphShowSourceNodes = !state.topicGraphShowSourceNodes;
  const selectedNode = (state.topicGraph.nodes || []).find((item) =>
    Number(item.id) === Number(state.selectedTopicGraphNodeId || 0)
  );
  if (selectedNode && !topicGraphNodeVisible(selectedNode)) {
    state.selectedTopicGraphNodeId = 0;
    renderTopicGraphSelectedNodeDetails(null, []);
  } else if (selectedNode) {
    selectTopicGraphNode(selectedNode.id);
    return;
  } else {
    renderTopicGraphSelectedNodeDetails(null, []);
  }
  renderTopicGraph();
}

function renderTopicGraphToSvg(svg) {
  if (!svg) return;
  bindTopicGraphViewportControls(svg);
  const visibleGraph = topicGraphVisibleGraph();
  const nodes = visibleGraph.nodes || [];
  const edges = visibleGraph.edges || [];
  if (!nodes.length) {
    svg.innerHTML = `<text x="24" y="40" fill="#94a3b8">尚無 topic graph</text>`;
    return;
  }
  const positions = topicGraphPositions(nodes, edges);
  const latestTrace = currentTopicGraphLatestTrace();
  const traceNodeIds = new Set((latestTrace?.selected_node_ids || []).map((id) => Number(id)));
  const selected = Number(state.selectedTopicGraphNodeId || 0);
  const relatedNodeIds = topicGraphRelatedNodeIds(selected, edges);
  const focusNodeIds = selected ? relatedNodeIds : topicGraphAutoFocusNodeIds(latestTrace, edges);
  const primaryTraceNodeId = topicGraphPrimaryTraceNodeId(latestTrace);
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
  const latest = currentTopicGraphLatestTrace();
  const traces = (state.topicGraphTraces || []).filter((trace) => topicGraphTraceMatchesPack(trace, currentTopicGraphPackId()));
  $("topicGraphLatestTrace").innerHTML = latest
    ? `<strong>${escapeHtml(latest.source || "trace")} <span class="status good">自動跟隨</span></strong><p>${escapeHtml(latest.query_text || "")}</p><p class="muted">selected: ${(latest.selected_node_ids || []).join(", ")}</p>`
    : "尚無召回路徑";
  $("topicGraphTraces").innerHTML = traces.map((trace) => `
    <div class="item">
      <strong>${escapeHtml(trace.source || "trace")}</strong>
      <p>${escapeHtml(trace.query_text || "")}</p>
      <p class="muted">${escapeHtml(trace.created_at || "")}</p>
    </div>
  `).join("") || `<div class="muted">尚無 trace</div>`;
}

function topicGraphTraceMatchesPack(trace, packId) {
  return !!trace && Number(trace.pack_id || 0) === Number(packId || 0);
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
    if (!state.selectedTopicGraphNodeId) renderTopicGraphSelectedNodeDetails(null, []);
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
  const packId = Number($("topicPackSelect")?.value || 0);
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
    const packTraces = (traces.traces || []).filter((trace) => topicGraphTraceMatchesPack(trace, packId));
    state.topicGraphTraces = packTraces;
    state.topicGraphLatestTrace = topicGraphTraceMatchesPack(latest.trace, packId)
      ? latest.trace
      : (packTraces[0] || null);
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
