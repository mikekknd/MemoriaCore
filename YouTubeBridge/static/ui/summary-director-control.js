import { $, state, api, escapeHtml, log } from "./core.js";
import { selectedSessionId } from "./selectors.js";
import { fillSessionForm, loadSessions, saveSession } from "./session-control.js?v=topic-graph-sources-v2";
export async function refreshSummary() {
  const id = selectedSessionId();
  if (!id) return;
  try {
    const summary = await api(`/sessions/${encodeURIComponent(id)}/summary`);
    renderSummary(summary);
  } catch {
    renderNoSummary();
  }
}

export function renderSummary(summary) {
  if (!summary) {
    renderNoSummary();
    return;
  }
  const status = summary.metadata?.memory_write_status || summary.status || "completed";
  $("summaryState").textContent = status;
  $("summaryState").className = status === "completed" ? "status good" : (String(status).includes("fail") ? "status bad" : "status warn");
  $("summaryView").innerHTML = `
    <strong>${escapeHtml(summary.title || "直播摘要")}</strong>
    <p>${escapeHtml(summary.summary_text || "")}</p>
    <p class="muted">memory_text</p>
    <p>${escapeHtml(summary.memory_text || "")}</p>
    <p class="muted">topics: ${escapeHtml((summary.topic_tags || []).join(", "))}</p>
  `;
}

export function renderNoSummary() {
  $("summaryState").textContent = "尚無摘要";
  $("summaryState").className = "status";
  $("summaryView").innerHTML = `<p class="muted">尚無摘要</p>`;
}

export async function makeSummary(force) {
  const id = selectedSessionId();
  if (!id) throw new Error("請先建立或選擇 Live Session");
  const data = await api(`/sessions/${encodeURIComponent(id)}/summarize`, {
    method: "POST",
    body: JSON.stringify({ force, include_memoria_session: true, safe_memory_text: true }),
  });
  log("摘要完成", data);
  if (data.runtime_session_deleted) {
    await loadSessions();
    renderSummary(data.summary || data);
  }
  else {
    renderSummary(data.summary || data);
    await refreshSummary();
  }
}

export async function refreshDirector() {
  const id = selectedSessionId();
  if (!id) {
    $("directorJson").textContent = "{}";
    $("directorState").textContent = "stopped";
    $("directorState").className = "status";
    updateDirectorControls({ director_enabled: false, status: "stopped" });
    return null;
  }
  const data = await api(`/sessions/${encodeURIComponent(id)}/director`);
  $("directorJson").textContent = JSON.stringify(data, null, 2);
  $("directorState").textContent = data.status || "stopped";
  $("directorState").className = data.director_enabled ? "status good" : "status";
  updateDirectorControls(data);
  return data;
}

export function updateDirectorControls(data) {
  const status = $("directorState");
  if (!status) return;
  status.title = data?.director_enabled
    ? "導播目前已啟用"
    : "導播會在直播開始後自動啟用";
}

export async function updateDirectorGuidance() {
  const id = selectedSessionId();
  if (!id) throw new Error("請先建立或選擇 Live Session");
  await saveSession(false);
  const current = await refreshDirector();
  if (current?.director_enabled) {
    await setDirector(true, false);
    return;
  }
  const data = await api(`/sessions/${encodeURIComponent(id)}/director/guidance`, {
    method: "POST",
    body: JSON.stringify({ guidance: $("directorGuidance").value.trim() }),
  });
  log("導播設定已更新", data);
  await loadSessions(id);
  const session = state.sessions.find((item) => item.session_id === id);
  if (session) fillSessionForm(session);
  await refreshDirector();
}

export async function setDirector(start, kickoff = false) {
  const id = selectedSessionId();
  const path = start ? "start" : "stop";
  const body = start ? JSON.stringify({
    idle_seconds: Number($("directorIdle").value || 60),
    guidance: $("directorGuidance").value.trim(),
    kickoff,
  }) : "{}";
  const data = await api(`/sessions/${encodeURIComponent(id)}/director/${path}`, { method: "POST", body });
  log(`director ${path}`, data);
  if (start) await loadSessions(id);
  await refreshDirector();
}

export async function refreshQueue() {
  const id = selectedSessionId();
  const queueState = $("queueState");
  const queueList = $("queueList");
  if (!id || !queueState || !queueList) return;
  const data = await api(`/sessions/${encodeURIComponent(id)}/interactions?limit=80`);
  const items = data.interactions || [];
  queueState.textContent = data.active ? `active: ${data.active.status}` : `${items.length} jobs`;
  queueState.className = data.active ? "status warn" : "status";
  queueList.innerHTML = items.map((item) => `
    <div class="item">
      <strong>${escapeHtml(item.source)} <span class="muted">${escapeHtml(item.status)} / p${escapeHtml(item.priority)}</span></strong>
      <p class="mono">${escapeHtml(item.job_id)}</p>
      <p>${escapeHtml(item.reply_text || item.closure_text || item.reason || item.content || "")}</p>
    </div>
  `).join("") || `<div class="muted">尚無 queue 紀錄</div>`;
}
