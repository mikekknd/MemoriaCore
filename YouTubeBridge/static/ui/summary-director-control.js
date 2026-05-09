import { $, state, api, escapeHtml, log } from "./core.js";
import { selectedSessionId } from "./selectors.js";
import { fillSessionForm, loadSessions, saveSession, updateEpisodePlanModeControls } from "./session-control.js?v=episode-evidence-v1";
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
    renderEpisodePlanDebugList(null);
    updateDirectorControls({ director_enabled: false, status: "stopped" });
    return null;
  }
  const data = await api(`/sessions/${encodeURIComponent(id)}/director`);
  $("directorJson").textContent = JSON.stringify(data, null, 2);
  $("directorState").textContent = data.status || "stopped";
  $("directorState").className = data.director_enabled ? "status good" : "status";
  renderDirectorSegmentState(data);
  renderEpisodePlanDebugList(data);
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
    const completed = planned.plan_status === "completed";
    target.textContent = completed
      ? `企劃：${planned.plan_id} / 已完成${interruptText}`
      : `企劃：${planned.plan_id} / 段落 ${segmentIndex} / turn ${turnIndex}${interruptText}`;
    target.className = completed
      ? "director-segment-state status good"
      : (interrupt.status === "handling_audience"
      ? "director-segment-state status warn"
      : "director-segment-state status good");
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

function episodePlanStatusLabel(status = "") {
  if (status === "active") return "目前";
  if (status === "completed") return "完成";
  if (status === "missing") return "遺失";
  if (status === "not_started") return "未開始";
  return "待講";
}

function episodeTurnReplyBudget(turn = {}) {
  const defaults = {
    opening: [1, 1],
    cohost_intro: [1, 1],
    handoff: [1, 1],
    hook: [1, 2],
    background: [1, 2],
    transition: [1, 2],
    analysis: [2, 3],
    counterpoint: [2, 3],
    chat_bridge: [2, 3],
    audience_answer: [2, 3],
    closing: [1, 2],
    final_closing: [2, 2],
  };
  const turnType = String(turn.turn_type || "");
  const [minDefault, maxDefault] = defaults[turnType] || [1, 2];
  const policy = turn.reply_budget || turn.dialogue_policy || {};
  return {
    min_replies: Math.max(1, Number(policy.min_replies || minDefault) || minDefault),
    max_replies: Math.max(1, Number(policy.max_replies || maxDefault) || maxDefault),
    autonomy: policy.autonomy || "guided",
  };
}

function episodeReplyBudgetLabel(budget = {}) {
  const minReplies = Number(budget.min_replies || 1);
  const maxReplies = Number(budget.max_replies || minReplies);
  return minReplies === maxReplies ? `回覆 ${maxReplies}` : `回覆 ${minReplies}-${maxReplies}`;
}

function episodePlanDebugFallback() {
  const session = state.sessions.find((item) => item.session_id === selectedSessionId());
  const planId = session?.episode_plan_id || $("episodePlanSelect")?.value || "";
  if (!planId) return {};
  const plan = state.episodePlans.find((item) => item.plan_id === planId);
  const planJson = plan?.plan_json || {};
  const segments = Array.isArray(planJson.segments) ? planJson.segments.map((segment) => ({
    segment_id: segment.segment_id || "",
    title: segment.title || segment.segment_id || "",
    goal: segment.goal || "",
    status: "pending",
    turns: Array.isArray(segment.planned_turn_contracts)
      ? segment.planned_turn_contracts.map((turn) => ({
        turn_id: turn.turn_id || "",
        turn_type: turn.turn_type || "",
        intent: turn.intent || "",
        reply_budget: episodeTurnReplyBudget(turn),
        status: "pending",
      }))
      : [],
  })) : [];
  return {
    plan_id: planId,
    title: planJson.title || plan?.title || planId,
    plan_status: plan ? "not_started" : "missing",
    segments,
  };
}

export function renderEpisodePlanDebugList(data) {
  const target = $("episodePlanDebugList");
  const badge = $("episodePlanDebugState");
  const waitTarget = $("episodePlanDebugWait");
  if (!target || !badge) return;
  const debug = data?.episode_plan_debug?.plan_id ? data.episode_plan_debug : episodePlanDebugFallback();
  if (!debug?.plan_id) {
    badge.textContent = "未綁定";
    badge.className = "status";
    target.className = "episode-plan-debug-list muted";
    target.innerHTML = "尚未綁定節目企劃。";
    if (waitTarget) {
      waitTarget.className = "episode-plan-debug-wait muted";
      waitTarget.textContent = "下一輪等待：未啟動";
    }
    return;
  }
  const segments = Array.isArray(debug.segments) ? debug.segments : [];
  const activeSegment = segments.find((segment) => segment.status === "active");
  const activeTurn = activeSegment?.turns?.find((turn) => turn.status === "active");
  badge.textContent = activeTurn
    ? `${activeSegment.title || activeSegment.segment_id} / ${activeTurn.turn_type || activeTurn.turn_id}`
    : episodePlanStatusLabel(debug.plan_status);
  badge.className = debug.plan_status === "completed" ? "status good" : "status warn";
  if (waitTarget) {
    const nextWait = debug.next_wait || {};
    const hasDelay = nextWait.delay_seconds !== undefined && nextWait.delay_seconds !== null;
    if (!hasDelay) {
      waitTarget.textContent = "下一輪等待：尚無等待資訊";
    } else {
      const delaySeconds = Number(nextWait.delay_seconds || 0);
      const remainingSeconds = Number(nextWait.remaining_seconds ?? delaySeconds);
      const label = nextWait.label || nextWait.reason || "等待";
      waitTarget.textContent = remainingSeconds <= 0
        ? `下一輪等待：可立即推進 / ${label}（設定 ${delaySeconds} 秒）`
        : `下一輪等待：剩餘 ${remainingSeconds} 秒 / ${label}（設定 ${delaySeconds} 秒）`;
    }
    waitTarget.className = "episode-plan-debug-wait";
  }
  target.className = "episode-plan-debug-list";
  target.innerHTML = segments.map((segment, segmentIndex) => {
    const segmentStatus = segment.status || "pending";
    const turns = Array.isArray(segment.turns) ? segment.turns : [];
    const turnHtml = turns.map((turn) => {
      const turnStatus = turn.status || "pending";
      const replyBudget = episodeTurnReplyBudget(turn);
      return `
        <div class="episode-plan-turn ${escapeHtml(turnStatus)}">
          <span class="episode-plan-turn-type">${escapeHtml(turn.turn_type || turn.turn_id || "turn")}</span>
          <span class="episode-plan-turn-type">${escapeHtml(episodeReplyBudgetLabel(replyBudget))}</span>
          <span>${escapeHtml(turn.intent || turn.turn_id || "")}</span>
        </div>
      `;
    }).join("");
    return `
      <div class="episode-plan-segment ${escapeHtml(segmentStatus)}">
        <div class="episode-plan-segment-title">
          <span>${segmentIndex + 1}. ${escapeHtml(segment.title || segment.segment_id || "未命名段落")}</span>
          <span>${escapeHtml(episodePlanStatusLabel(segmentStatus))}</span>
        </div>
        ${segment.goal ? `<div class="muted">${escapeHtml(segment.goal)}</div>` : ""}
        <div class="episode-plan-turns">${turnHtml || '<div class="muted">尚無預計 turn</div>'}</div>
      </div>
    `;
  }).join("") || `<div class="muted">企劃沒有段落。</div>`;
}

function episodePlanFolderName(plan) {
  const sourcePath = String(plan?.source_path || "").trim().replaceAll("\\", "/");
  const parts = sourcePath.split("/").filter(Boolean);
  if (parts.length >= 2) return parts[parts.length - 2];
  return String(plan?.plan_id || "").trim();
}

function episodePlanSelectLabel(plan) {
  const folder = episodePlanFolderName(plan);
  const title = String(plan?.title || plan?.plan_id || "").trim();
  if (folder && title) return `${folder}/${title}`;
  return title || folder || "";
}

export async function refreshEpisodePlans(options = {}) {
  if (options.syncLocal !== false) {
    try {
      await syncLocalEpisodePlans({ silent: true });
    } catch (error) {
      log("本地企劃同步失敗", { error: String(error) });
    }
  }
  const plans = await api("/episode-plans");
  state.episodePlans = Array.isArray(plans) ? plans : [];
  const select = $("episodePlanSelect");
  if (select) {
    const current = select.value;
    select.innerHTML = `<option value="">不使用企劃</option>` + state.episodePlans.map((plan) => (
      `<option value="${escapeHtml(plan.plan_id)}">${escapeHtml(episodePlanSelectLabel(plan))}</option>`
    )).join("");
    select.value = current;
    updateEpisodePlanModeControls();
  }
}

export function showEpisodePlanError(error) {
  const status = $("episodePlanStatus");
  if (!status) return;
  status.textContent = "企劃角色對應失敗";
  status.className = "status bad";
  status.title = String(error || "");
}

export async function syncLocalEpisodePlans(options = {}) {
  const synced = await api("/episode-plans/sync-local", { method: "POST" });
  if (!options.silent) {
    log("本地企劃已同步", {
      imported_count: synced.imported_count || 0,
      skipped_count: synced.skipped_count || 0,
      removed_count: synced.removed_count || 0,
      root: synced.root || "",
    });
  }
  return synced;
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
  updateEpisodePlanModeControls();
}

export async function bindSelectedEpisodePlan() {
  const id = selectedSessionId();
  if (!id) throw new Error("請先建立或選擇 Live Session");
  const planId = $("episodePlanSelect")?.value || "";
  if (!planId) throw new Error("請先選擇節目企劃");
  if ($("episodePlanStatus")) {
    $("episodePlanStatus").textContent = "綁定中";
    $("episodePlanStatus").className = "status warn";
    $("episodePlanStatus").title = "";
  }
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
