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
    renderDirectorSegmentState(null);
    updateDirectorControls({ director_enabled: false, status: "stopped" });
    return null;
  }
  const data = await api(`/sessions/${encodeURIComponent(id)}/director`);
  $("directorJson").textContent = JSON.stringify(data, null, 2);
  $("directorState").textContent = data.status || "stopped";
  $("directorState").className = data.director_enabled ? "status good" : "status";
  renderDirectorSegmentState(data);
  updateDirectorControls(data);
  return data;
}

export function renderDirectorSegmentState(data) {
  const target = $("directorSegmentState");
  if (!target) return;
  const metadata = data?.metadata || {};
  const planned = metadata.planned_state || {};
  const interrupt = metadata.interrupt_state || {};
  if (planned.plan_id) {
    const segmentIndex = Number(planned.current_segment_index || 0) + 1;
    const turnIndex = Number(planned.current_turn_index || 0) + 1;
    const interruptText = interrupt.status === "handling_audience"
      ? ` / interrupt：${interrupt.interrupt_type || "audience"}`
      : "";
    target.textContent = `企劃：${planned.plan_id} / 段落 ${segmentIndex} / turn ${turnIndex}${interruptText}`;
    target.className = interrupt.status === "handling_audience"
      ? "director-segment-state status warn"
      : "director-segment-state status good";
    const planStatus = $("episodePlanStatus");
    if (planStatus) {
      planStatus.textContent = planned.plan_id;
      planStatus.className = "status good";
    }
    return;
  }
  const planStatus = $("episodePlanStatus");
  if (planStatus) {
    const session = state.sessions.find((item) => item.session_id === selectedSessionId());
    planStatus.textContent = session?.episode_plan_id || "未綁定";
    planStatus.className = session?.episode_plan_id ? "status warn" : "status";
  }
  const segment = metadata.segment_state || {};
  const current = segment.current_step || {};
  if (!current.name) {
    target.textContent = "尚無段落狀態";
    target.className = "director-segment-state muted";
    return;
  }
  const topic = segment.topic ? `主題：${segment.topic}` : "主題：未指定";
  const turns = Number(segment.turns_in_step || 0);
  const turnsPerStep = Number(segment.turns_per_step || data?.program_segment_turns || 0);
  const remaining = Array.isArray(segment.remaining_steps) ? segment.remaining_steps.length : 0;
  const completed = Array.isArray(segment.completed_steps) ? segment.completed_steps.length : 0;
  const turnText = turnsPerStep > 0 ? `回合 ${turns}/${turnsPerStep}` : `回合 ${turns}`;
  target.textContent = `${topic} / 目前步驟：${current.name} / ${turnText} / 已完成 ${completed} / 剩餘 ${remaining}`;
  target.className = segment.all_steps_completed
    ? "director-segment-state status good"
    : "director-segment-state status warn";
}

export async function refreshEpisodePlans() {
  const plans = await api("/episode-plans");
  state.episodePlans = Array.isArray(plans) ? plans : [];
  const select = $("episodePlanSelect");
  if (select) {
    const current = select.value;
    select.innerHTML = `<option value="">不使用企劃</option>` + state.episodePlans.map((plan) => (
      `<option value="${escapeHtml(plan.plan_id)}">${escapeHtml(plan.title || plan.plan_id)}</option>`
    )).join("");
    select.value = current;
  }
}

export async function importEpisodePlanFromFile() {
  const file = $("episodePlanFile")?.files?.[0];
  if (!file) throw new Error("請先選擇 episode-plan.json");
  const text = await file.text();
  const plan_json = JSON.parse(text);
  const saved = await api("/episode-plans/import", {
    method: "POST",
    body: JSON.stringify({ plan_json, source_path: file.name }),
  });
  log("節目企劃已匯入", saved);
  await refreshEpisodePlans();
  $("episodePlanSelect").value = saved.plan_id;
}

export async function bindSelectedEpisodePlan() {
  const id = selectedSessionId();
  if (!id) throw new Error("請先建立或選擇 Live Session");
  const planId = $("episodePlanSelect")?.value || "";
  if (!planId) throw new Error("請先選擇節目企劃");
  const session = await api(`/sessions/${encodeURIComponent(id)}/episode-plan`, {
    method: "POST",
    body: JSON.stringify({ plan_id: planId }),
  });
  log("節目企劃已綁定", session);
  await loadSessions(id);
  await refreshDirector();
}

export async function unbindEpisodePlan() {
  const id = selectedSessionId();
  if (!id) throw new Error("請先建立或選擇 Live Session");
  const session = await api(`/sessions/${encodeURIComponent(id)}/episode-plan`, { method: "DELETE" });
  log("節目企劃已解除綁定", session);
  await loadSessions(id);
  await refreshDirector();
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
