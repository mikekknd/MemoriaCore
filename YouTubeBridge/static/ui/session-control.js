import { $, SINGLE_CONNECTOR_ID, state, api, clearLog, log, summarizeSsePayload } from "./core.js";
import { defaultLiveSession, isSelectedSessionRunning, selectedSessionId, selectedSessionInfo } from "./selectors.js";
import { bindSessionTopicPack, refreshTopicPacks, scheduleTopicGraphTraceRefresh } from "./topic-packs.js";
import { scheduleChatPreviewRefresh, selectedCharacterIds, syncCharacterSelectionLimit, validateSelectedCharacters } from "./memoria-control.js?v=topic-graph-sources-v2";
import { refreshEvents, renderEvents, testEventControlsDisabled, updateTestEventControls } from "./events-control.js?v=topic-graph-sources-v2";
import { refreshDirector, refreshQueue, refreshSummary, renderNoSummary, renderSummary, setDirector, updateDirectorControls } from "./summary-director-control.js?v=topic-graph-sources-v2";
function sessionIsFinalized(session = selectedSessionInfo()) {
  return !!(
    session?.finalized_at
    || session?.status === "ended"
    || ["completed", "summarizing"].includes(session?.summary_status)
  );
}

function sessionHasStarted(session = selectedSessionInfo()) {
  return !!(session?.started_at || session?.runtime_status?.running || session?.status === "running");
}

function sessionIsClosing(session = selectedSessionInfo()) {
  const status = session?.runtime_status?.status || session?.status || "";
  return ["closing", "summarizing", "finalizing"].includes(status);
}

export function updateLiveSessionControls() {
  const hasSession = !!selectedSessionId();
  const running = isSelectedSessionRunning();
  const session = selectedSessionInfo();
  const hasStarted = sessionHasStarted(session);
  const finalized = sessionIsFinalized(session);
  const closing = sessionIsClosing(session);
  syncCharacterSelectionLimit();
  const characterValidation = validateSelectedCharacters();
  const isStartAction = !(running || (hasStarted && !finalized)) && !closing;
  $("toggleSession").textContent = closing
    ? "收尾中"
    : ((running || (hasStarted && !finalized)) ? "結束直播並收尾" : (finalized ? "開始全新直播" : "開始直播"));
  $("toggleSession").className = (running || (hasStarted && !finalized)) && !closing ? "danger" : "blue";
  $("toggleSession").disabled = closing;
  if (!closing && isStartAction) {
    $("toggleSession").disabled = !characterValidation.ok;
    $("toggleSession").title = characterValidation.ok ? "" : characterValidation.message;
  } else {
    $("toggleSession").title = "";
  }
  $("updateSession").textContent = "更新設定";
  $("updateSession").hidden = !hasSession || finalized || closing;
  $("updateSession").disabled = !$("updateSession").hidden && !characterValidation.ok;
  $("sessionActions").className = $("updateSession").hidden ? "session-actions single" : "session-actions";
  $("sessionActions").hidden = $("updateSession").hidden;
  const autoTestRunning = !!session?.runtime_status?.auto_test_events_running;
  $("toggleAutoTestEvents").textContent = autoTestRunning ? "停止自動測試" : "啟動自動測試";
  $("toggleAutoTestEvents").className = autoTestRunning ? "danger" : "";
  $("toggleAutoTestEvents").disabled = !hasSession;
  updateTestEventControls();
}

export function injectMinIntervalSeconds() {
  const baseSeconds = Number($("injectInterval").value || 30);
  const minSeconds = Number($("injectMinIntervalSeconds").value || 10);
  return Math.max(5, Math.min(minSeconds, baseSeconds || 600));
}

export async function loadHealth() {
  try {
    const data = await api("/health");
    $("health").textContent = data.ok ? "Bridge online" : "Bridge error";
    $("health").className = data.ok ? "status good" : "status bad";
  } catch (error) {
    $("health").textContent = "Bridge offline";
    $("health").className = "status bad";
  }
}

export function fillConnectorForm(connector) {
  if (!connector) return;
  $("connectorState").textContent = connector.api_key_configured
    ? "已儲存 API key，connector 自動啟用"
    : "尚未儲存 API key；真實直播需設定";
  $("connectorState").className = connector.api_key_configured ? "status good" : "status warn";
}

export async function loadConnectors() {
  state.connectors = await api("/connectors");
  state.connector = state.connectors[0] || {
    connector_id: SINGLE_CONNECTOR_ID,
    display_name: "YouTube Main",
    enabled: true,
    api_key_configured: false,
  };
  fillConnectorForm(state.connector);
}

export async function loadSessions(preferredId = "", options = {}) {
  const selectDefault = options.selectDefault !== false;
  state.sessions = await api("/sessions");
  const selected = selectDefault ? defaultLiveSession(preferredId) : null;
  if (selected) {
    fillSessionForm(selected);
  }
  else newSessionDraft();
  subscribeEvents();
}

export function fillSessionForm(session) {
  $("sessionId").value = session.session_id || "";
  $("videoId").value = session.video_id || "";
  $("injectInterval").value = session.inject_interval_seconds || 30;
  $("injectMinIntervalSeconds").value = session.inject_min_interval_seconds || Math.round(Number(session.inject_interval_seconds || 30) * Number(session.inject_min_interval_ratio || 0.32));
  $("minPending").value = session.min_pending_events || 1;
  $("maxPending").value = session.max_pending_events || 12;
  $("plannedDuration").value = session.planned_duration_minutes || 30;
  $("scInterruptCooldown").value = session.sc_interrupt_cooldown_seconds || 30;
  $("maxScPerBatch").value = session.max_sc_per_batch || 5;
  $("directorAnchorEveryTurns").value = session.director_anchor_every_turns || 2;
  $("directorGroupTurnLimit").value = session.director_group_turn_limit || 3;
  $("directorMaxChatBatches").value = session.director_max_chat_batches_before_anchor || 2;
  $("autoInject").checked = !!session.auto_inject;
  $("autoFinalize").checked = !!session.auto_finalize_on_duration;
  $("autoScThanksOnFinalize").checked = session.auto_sc_thanks_on_finalize !== false;
  $("autoDeleteProcessed").checked = !!session.auto_delete_after_processed;
  $("directorGuidance").value = session.director_guidance || "";
  $("hostInteractionRules").value = session.host_interaction_rules || "";
  $("programSegmentPlan").value = session.program_segment_plan || "";
  $("programSegmentTurns").value = session.program_segment_turns || 3;
  $("researchEnabled").checked = !!session.research_enabled;
  $("autoTestEvents").checked = !!session.auto_test_events_enabled;
  $("testEventMinSeconds").value = session.test_event_min_seconds || 20;
  $("testEventMaxSeconds").value = session.test_event_max_seconds || 45;
  $("testEventCountPerTick").value = session.test_event_count_per_tick || 3;
  $("testUseLlm").checked = session.test_event_use_llm !== false;
  $("testSuperChatCountPerTick").value = session.test_super_chat_count_per_tick || 0;
  $("testMaliciousSc").checked = !!session.test_malicious_sc_enabled;
  $("testScBurst").checked = !!session.test_sc_burst_mode;
  const ids = new Set(session.character_ids || []);
  Array.from($("characterSelect").options).forEach((option) => option.selected = ids.has(option.value));
  updateLiveSessionControls();
  scheduleChatPreviewRefresh();
}

export function newSessionDraft() {
  clearLog();
  $("sessionId").value = "";
  $("videoId").value = "";
  Array.from($("characterSelect").options).forEach((option) => option.selected = false);
  $("injectInterval").value = 30;
  $("injectMinIntervalSeconds").value = 10;
  $("minPending").value = 1;
  $("maxPending").value = 12;
  $("plannedDuration").value = 30;
  $("scInterruptCooldown").value = 30;
  $("maxScPerBatch").value = 5;
  $("directorAnchorEveryTurns").value = 2;
  $("directorGroupTurnLimit").value = 3;
  $("directorMaxChatBatches").value = 2;
  $("autoInject").checked = true;
  $("autoFinalize").checked = true;
  $("autoScThanksOnFinalize").checked = true;
  $("autoDeleteProcessed").checked = true;
  $("directorGuidance").value = "";
  $("hostInteractionRules").value = "";
  $("programSegmentPlan").value = "";
  $("programSegmentTurns").value = 3;
  $("researchEnabled").checked = false;
  $("autoTestEvents").checked = false;
  $("testEventMinSeconds").value = 20;
  $("testEventMaxSeconds").value = 45;
  $("testEventCountPerTick").value = 3;
  $("testUseLlm").checked = true;
  $("testSuperChatCountPerTick").value = 0;
  $("testMaliciousSc").checked = false;
  $("testScBurst").checked = false;
  $("sessionTopicPackSelect").value = "";
  $("directorState").textContent = "stopped";
  $("directorState").className = "status";
  $("directorJson").textContent = "{}";
  updateDirectorControls({ director_enabled: false, status: "stopped" });
  renderNoSummary();
  $("topicPackSelect").value = "";
  $("topicPackEntries").innerHTML = `<div class="muted">尚無 fact card</div>`;
  const previewState = $("chatPreviewState");
  const previewList = $("chatPreviewList");
  if (previewState && previewList) {
    previewState.textContent = "尚未選擇 Live Session";
    previewState.className = "status warn";
    previewList.innerHTML = `<div class="muted">請先建立或選擇 Live Session。</div>`;
  }
  state.selectedEventIds.clear();
  state.events = [];
  renderEvents();
  subscribeEvents();
  updateLiveSessionControls();
  scheduleChatPreviewRefresh();
}

export async function saveConnector() {
  const payload = {
    connector_id: SINGLE_CONNECTOR_ID,
    display_name: "YouTube Main",
    api_key: $("apiKey").value,
    enabled: true,
  };
  const data = await api("/connectors", { method: "POST", body: JSON.stringify(payload) });
  $("apiKey").value = "";
  log("Connector 已儲存", data);
  await loadConnectors();
}

export function liveSessionPayload({ createNew = false } = {}) {
  const blockTestEvents = testEventControlsDisabled();
  return {
    session_id: createNew ? "" : $("sessionId").value.trim(),
    connector_id: state.connector?.connector_id || SINGLE_CONNECTOR_ID,
    display_name: "YouTube Live",
    video_id: $("videoId").value.trim(),
    target_memoria_session_id: "",
    character_ids: selectedCharacterIds(),
    auto_connect: true,
    auto_inject: $("autoInject").checked,
    inject_interval_seconds: Number($("injectInterval").value || 30),
    inject_min_interval_seconds: injectMinIntervalSeconds(),
    min_pending_events: Number($("minPending").value || 1),
    max_pending_events: Number($("maxPending").value || 12),
    dynamic_inject_enabled: true,
    planned_duration_minutes: Number($("plannedDuration").value || 30),
    sc_interrupt_cooldown_seconds: Number($("scInterruptCooldown").value || 30),
    max_sc_per_batch: Number($("maxScPerBatch").value || 5),
    director_anchor_every_turns: Number($("directorAnchorEveryTurns").value || 2),
    director_group_turn_limit: Number($("directorGroupTurnLimit").value || 3),
    director_max_chat_batches_before_anchor: Number($("directorMaxChatBatches").value || 2),
    auto_finalize_on_duration: $("autoFinalize").checked,
    auto_sc_thanks_on_finalize: $("autoScThanksOnFinalize").checked,
    auto_delete_after_processed: $("autoDeleteProcessed").checked,
    director_guidance: $("directorGuidance").value.trim(),
    host_interaction_rules: $("hostInteractionRules").value.trim(),
    program_segment_plan: $("programSegmentPlan").value.trim(),
    program_segment_turns: Number($("programSegmentTurns").value || 3),
    research_enabled: $("researchEnabled").checked,
    auto_test_events_enabled: blockTestEvents ? false : $("autoTestEvents").checked,
    test_event_min_seconds: Number($("testEventMinSeconds").value || 20),
    test_event_max_seconds: Number($("testEventMaxSeconds").value || 45),
    test_event_count_per_tick: Number($("testEventCountPerTick").value || 3),
    test_event_use_llm: $("testUseLlm").checked,
    test_super_chat_count_per_tick: Number($("testSuperChatCountPerTick").value || 0),
    test_malicious_sc_enabled: $("testMaliciousSc").checked,
    test_sc_burst_mode: $("testScBurst").checked,
  };
}

export async function saveSession(createNew) {
  const payload = liveSessionPayload({ createNew });
  const data = await api("/sessions", { method: "POST", body: JSON.stringify(payload) });
  log(createNew ? "新直播已建立" : "直播設定已更新", data);
  await loadSessions(data.session_id);
  fillSessionForm(data);
  await bindSessionTopicPack(data.session_id);
  await refreshTopicPacks();
  subscribeEvents();
  updateLiveSessionControls();
}

export async function sessionAction(action) {
  const id = selectedSessionId();
  if (!id) throw new Error("請先建立或選擇 Live Session");
  const data = await api(`/sessions/${encodeURIComponent(id)}/${action}`, { method: "POST", body: "{}" });
  log(`${action} 完成`, data);
  await loadSessions(id);
  await refreshEvents();
  updateLiveSessionControls();
}

export async function updateSessionSettings() {
  await saveSession(!selectedSessionId());
}

export async function startCurrentSession() {
  const validation = validateSelectedCharacters();
  if (!validation.ok) throw new Error(validation.message);
  const button = $("toggleSession");
  button.disabled = true;
  button.textContent = "啟動中";
  try {
    const payload = liveSessionPayload({ createNew: true });
    const data = await api("/sessions/current/start", { method: "POST", body: JSON.stringify(payload) });
    log("直播已開始", data);
    state.sessions = [data];
    fillSessionForm(data);
    await bindSessionTopicPack(data.session_id);
    subscribeEvents();
    await setDirector(true, true);
    await refreshEvents();
    await refreshSummary();
    await refreshTopicPacks();
  } finally {
    updateLiveSessionControls();
  }
}

export async function finalizeCurrentSession() {
  const id = selectedSessionId();
  if (!id) throw new Error("請先建立或選擇 Live Session");
  const button = $("toggleSession");
  button.disabled = true;
  button.textContent = "收尾中";
  try {
    const data = await api(`/sessions/${encodeURIComponent(id)}/finalize`, { method: "POST", body: "{}" });
    log("直播已結束並寫入 Shared Memory", data);
    if (data.summary) renderSummary(data.summary);
    await loadSessions("", { selectDefault: !data.runtime_session_deleted });
    await refreshEvents();
    await refreshSummary();
    await refreshTopicPacks();
  } finally {
    updateLiveSessionControls();
  }
}

export async function toggleSession() {
  const session = selectedSessionInfo();
  const shouldFinalize = isSelectedSessionRunning() || (sessionHasStarted(session) && !sessionIsFinalized(session));
  if (shouldFinalize) {
    await finalizeCurrentSession();
    return;
  }
  await startCurrentSession();
}

export function subscribeEvents() {
  if (state.eventSource) state.eventSource.close();
  const id = selectedSessionId();
  if (!id) return;
  state.eventSource = new EventSource(`/sessions/${encodeURIComponent(id)}/events`);
  state.eventSource.onmessage = async (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "youtube_live_event") {
      state.events.push(payload.event);
      renderEvents();
    }
    if (["test_events_generated", "test_events_auto_generated", "super_chat_batch_injected", "safety_classified"].includes(payload.type)) {
      await refreshEvents();
    }
    if (["test_event_auto_started", "test_event_auto_stopped"].includes(payload.type)) {
      await loadSessions(id);
      updateLiveSessionControls();
    }
    if (["memoria_injected", "interaction_started", "interaction_completed", "interaction_interrupted", "director_injected", "interrupt_requested"].includes(payload.type)) {
      await refreshQueue();
    }
    if (["memoria_injected", "interaction_started", "interaction_completed", "director_injected"].includes(payload.type)) {
      scheduleTopicGraphTraceRefresh({ reason: payload.type });
    }
    if (["research_card_created"].includes(payload.type)) {
      await refreshTopicPacks();
    }
    if (["director_state", "director_error"].includes(payload.type)) {
      await refreshDirector();
    }
    if (["interaction_completed", "memoria_injected", "director_injected", "closing_super_chat_thanks_completed"].includes(payload.type)) {
      scheduleChatPreviewRefresh({ reloadSessions: true });
    }
    log(`SSE: ${payload.type}`, summarizeSsePayload(payload));
  };
  state.eventSource.onerror = () => {
    $("eventState").textContent = "SSE 連線中斷";
    $("eventState").className = "status warn";
  };
}
