import { $, SINGLE_CONNECTOR_ID, state, api, clearLog, escapeHtml, log, summarizeSsePayload } from "./core.js";
import { defaultLiveSession, isSelectedSessionRunning, selectedSessionId, selectedSessionInfo } from "./selectors.js";
import { refreshTopicPacks } from "./topic-packs.js";

export function updateLiveSessionControls() {
  const hasSession = !!selectedSessionId();
  const running = isSelectedSessionRunning();
  const session = selectedSessionInfo();
  const hasStarted = !!(session?.started_at || session?.runtime_status?.running);
  $("toggleSession").textContent = running ? "停止" : "開始";
  $("toggleSession").className = running ? "danger" : "blue";
  $("deleteSession").disabled = !hasSession;
  $("updateSession").textContent = "更新設定";
  $("updateSession").hidden = !hasStarted;
  $("sessionActions").className = hasStarted ? "session-actions" : "session-actions single";
  const autoTestRunning = !!session?.runtime_status?.auto_test_events_running;
  $("toggleAutoTestEvents").textContent = autoTestRunning ? "停止自動測試" : "啟動自動測試";
  $("toggleAutoTestEvents").className = autoTestRunning ? "danger" : "";
  $("toggleAutoTestEvents").disabled = !hasSession;
}

export function selectedCharacterIds() {
  return Array.from($("characterSelect").selectedOptions).map((option) => option.value).filter(Boolean);
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
  $("connectorName").value = connector.display_name || "";
  $("connectorEnabled").checked = !!connector.enabled;
  $("connectorState").textContent = connector.api_key_configured ? "已儲存 API key" : "尚未儲存 API key";
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

export async function loadMemoriaConfig() {
  try {
    const config = await api("/memoria/config");
    $("memoriaBaseUrl").value = config.base_url || "http://localhost:8088/api/v1";
    $("memoriaUsername").value = config.username || "";
    $("memoriaPassword").value = "";
    $("memoriaPassword").placeholder = config.password_configured ? "已儲存密碼；留空沿用" : "可留空改用 admin bypass";
    $("memoriaAdminBypass").checked = config.admin_bypass !== false;
    $("memoriaAuthState").textContent = config.password_configured || config.admin_bypass ? "已設定" : "尚未設定";
    $("memoriaAuthState").className = config.password_configured || config.admin_bypass ? "status good" : "status warn";
    scheduleChatPreviewRefresh();
  } catch (error) {
    $("memoriaAuthState").textContent = "設定讀取失敗";
    $("memoriaAuthState").className = "status bad";
    log("MemoriaCore 設定讀取失敗", String(error));
  }
}

export function memoriaAuthPayload() {
  return {
    base_url: $("memoriaBaseUrl").value.trim() || "http://localhost:8088/api/v1",
    username: $("memoriaUsername").value.trim(),
    password: $("memoriaPassword").value,
    admin_bypass: $("memoriaAdminBypass").checked,
  };
}

export function memoriaChatUrl(targetSessionId = "") {
  const rawBase = $("memoriaBaseUrl").value.trim() || "http://localhost:8088/api/v1";
  const url = new URL(rawBase, window.location.href);
  url.pathname = url.pathname.replace(/\/api\/v1\/?$/, "").replace(/\/$/, "") + "/static/chat.html";
  url.search = "";
  url.hash = "";
  if (targetSessionId) url.searchParams.set("session_id", targetSessionId);
  url.searchParams.set("embed", "youtube_bridge");
  return url;
}

export function shortTime() {
  return new Date().toLocaleTimeString("zh-TW", { hour12: false });
}

export function selectedTargetMemoriaSessionId() {
  const session = selectedSessionInfo();
  return session?.target_memoria_session_id || $("memoriaSession").value || "";
}

export function updateOpenChatLink(targetSessionId = selectedTargetMemoriaSessionId()) {
  const link = $("openFullChat");
  if (!link) return;
  if (!targetSessionId) {
    link.href = "#";
    link.dataset.href = "";
    link.classList.add("disabled");
    link.setAttribute("aria-disabled", "true");
    return;
  }
  try {
    const href = memoriaChatUrl(targetSessionId).toString();
    link.href = href;
    link.dataset.href = href;
    link.classList.remove("disabled");
    link.setAttribute("aria-disabled", "false");
  } catch {
    link.href = "#";
    link.dataset.href = "";
    link.classList.add("disabled");
    link.setAttribute("aria-disabled", "true");
  }
}

export function chatRoleLabel(message) {
  if (message.role === "assistant") return message.character_name || "AI";
  if (message.role === "user") return "使用者";
  if (message.role === "system_event") return "系統事件";
  return message.role || "訊息";
}

export function renderChatPreview(messages) {
  const newestFirst = (messages || []).slice().reverse();
  $("chatPreviewList").innerHTML = newestFirst.map((message) => {
    const role = ["user", "assistant", "system_event", "system"].includes(message.role) ? message.role : "system";
    const idText = message.message_id !== undefined && message.message_id !== null ? ` #${message.message_id}` : "";
    return `<div class="chat-msg ${escapeHtml(role)}">
      <div class="chat-msg-meta">${escapeHtml(chatRoleLabel(message))}${escapeHtml(idText)}</div>
      <div class="chat-msg-content">${escapeHtml(message.content || "")}</div>
    </div>`;
  }).join("") || `<div class="muted">目前沒有可顯示的聊天紀錄</div>`;
  $("chatPreviewList").scrollTop = 0;
}

export async function refreshChatPreview({ silent = false } = {}) {
  let id = selectedSessionId();
  if (!id) {
    $("chatPreviewState").textContent = "尚未選擇 Live Session";
    $("chatPreviewState").className = "status warn";
    $("chatPreviewList").innerHTML = `<div class="muted">請先建立或選擇 Live Session。</div>`;
    updateOpenChatLink("");
    if (!silent) log("Chat Preview 未更新", "請先建立或選擇 Live Session。");
    return;
  }
  if (!silent) {
    $("refreshChatPreview").disabled = true;
    $("chatPreviewState").textContent = "Chat Preview 更新中...";
    $("chatPreviewState").className = "status";
  }
  try {
    const data = await api(`/sessions/${encodeURIComponent(id)}/chat-preview?limit=80`);
    updateOpenChatLink(data.memoria_session_id || "");
    renderChatPreview(data.messages || []);
    if (data.memoria_session_id) {
      const shown = (data.messages || []).length;
      if (data.stale) {
        $("chatPreviewState").textContent = `後端忙碌，使用快取：${data.memoria_session_id.slice(0, 8)}... / ${shown}/${data.message_count || 0} 則`;
        $("chatPreviewState").className = "status warn";
      } else {
        $("chatPreviewState").textContent = `MemoriaCore session: ${data.memoria_session_id.slice(0, 8)}... / ${shown}/${data.message_count || 0} 則，${shortTime()} 已更新`;
        $("chatPreviewState").className = "status good";
      }
    } else {
      $("chatPreviewState").textContent = data.stale ? "後端忙碌，沒有可用快取" : "尚未綁定 MemoriaCore session";
      $("chatPreviewState").className = "status warn";
    }
    if (!silent) {
      log(data.stale ? "Chat Preview 使用快取" : "Chat Preview 已更新", {
        message_count: data.message_count || 0,
        shown: (data.messages || []).length,
        stale: !!data.stale,
        last_success_at: data.last_success_at || "",
        error: data.error || "",
      });
    }
  } catch (error) {
    $("chatPreviewState").textContent = "Chat Preview 讀取失敗";
    $("chatPreviewState").className = "status bad";
    if (!silent) log("Chat Preview 讀取失敗", String(error));
  } finally {
    if (!silent) $("refreshChatPreview").disabled = false;
  }
}

export function scheduleChatPreviewRefresh({ reloadSessions = false } = {}) {
  if (state.chatPreviewRefreshTimer) clearTimeout(state.chatPreviewRefreshTimer);
  state.chatPreviewRefreshTimer = setTimeout(async () => {
    state.chatPreviewRefreshTimer = null;
    try {
      if (reloadSessions) await loadSessions(selectedSessionId());
      await refreshChatPreview({ silent: true });
    } catch (error) {
      log("Chat Preview 讀取失敗", String(error));
    }
  }, 150);
}

export async function saveMemoriaConfig() {
  const data = await api("/memoria/config", {
    method: "POST",
    body: JSON.stringify(memoriaAuthPayload()),
  });
  $("memoriaPassword").value = "";
  $("memoriaAuthState").textContent = data.password_configured || data.admin_bypass ? "已儲存" : "尚未設定";
  $("memoriaAuthState").className = data.password_configured || data.admin_bypass ? "status good" : "status warn";
  log("MemoriaCore 設定已儲存", data);
  await loadMemoriaConfig();
  await loadMemoriaRefs();
  await refreshChatPreview({ silent: true });
}

export async function testMemoriaAuth() {
  const data = await api("/memoria/auth/test", {
    method: "POST",
    body: JSON.stringify(memoriaAuthPayload()),
  });
  $("memoriaAuthState").textContent = `連線成功：${data.character_count} 角色 / ${data.session_count} sessions`;
  $("memoriaAuthState").className = "status good";
  log("MemoriaCore 連線測試成功", data);
  await loadMemoriaRefs();
  await refreshChatPreview({ silent: true });
}

export async function loadMemoriaRefs() {
  try {
    state.characters = await api("/memoria/characters");
    $("characterSelect").innerHTML = state.characters.map((c) =>
      `<option value="${escapeHtml(c.character_id)}" title="${escapeHtml(c.character_id)}">${escapeHtml(c.name || c.character_id)}</option>`
    ).join("");
  } catch (error) {
    $("characterSelect").innerHTML = `<option value="">角色清單讀取失敗，請先設定 MemoriaCore Auth</option>`;
    log("角色清單讀取失敗", String(error));
  }
  try {
    state.memoriaSessions = await api("/memoria/sessions?limit=200");
    $("memoriaSession").innerHTML = `<option value="">自動建立或沿用 Live Session 目標</option>` + state.memoriaSessions.map((s) =>
      `<option value="${escapeHtml(s.session_id)}">${escapeHtml(s.group_name || s.session_id)} - ${escapeHtml(s.session_id)}</option>`
    ).join("");
  } catch (error) {
    $("memoriaSession").innerHTML = `<option value="">Session 清單讀取失敗，請先設定 MemoriaCore Auth</option>`;
    log("MemoriaCore session 清單讀取失敗", String(error));
  }
}

export async function loadSessions(preferredId = "", options = {}) {
  const selectDefault = options.selectDefault !== false;
  state.sessions = await api("/sessions");
  $("sessionSelect").innerHTML = `<option value="">新直播草稿</option>` + state.sessions.map((s) => {
    const name = s.display_name || s.session_id;
    const status = s.runtime_status?.status || s.status;
    return `<option value="${escapeHtml(s.session_id)}">${escapeHtml(name)} - ${escapeHtml(s.session_id)} [${escapeHtml(status)}]</option>`;
  }).join("");
  const selected = selectDefault ? defaultLiveSession(preferredId) : null;
  if (selected) {
    $("sessionSelect").value = selected.session_id;
    fillSessionForm(selected);
  }
  else newSessionDraft();
  subscribeEvents();
}

export function fillSessionForm(session) {
  $("sessionId").value = session.session_id || "";
  $("sessionName").value = session.display_name || "";
  $("videoId").value = session.video_id || "";
  $("memoriaSession").value = session.target_memoria_session_id || "";
  $("injectInterval").value = session.inject_interval_seconds || 30;
  $("minPending").value = session.min_pending_events || 1;
  $("maxPending").value = session.max_pending_events || 12;
  $("plannedDuration").value = session.planned_duration_minutes || 30;
  $("scInterruptCooldown").value = session.sc_interrupt_cooldown_seconds || 30;
  $("maxScPerBatch").value = session.max_sc_per_batch || 5;
  $("directorGroupTurnLimit").value = session.director_group_turn_limit || 3;
  $("directorMaxChatBatches").value = session.director_max_chat_batches_before_anchor || 2;
  $("autoInject").checked = !!session.auto_inject;
  $("dynamicInject").checked = session.dynamic_inject_enabled !== false;
  $("autoFinalize").checked = !!session.auto_finalize_on_duration;
  $("autoScThanksOnFinalize").checked = session.auto_sc_thanks_on_finalize !== false;
  $("autoDeleteProcessed").checked = !!session.auto_delete_after_processed;
  $("autoDirector").checked = $("autoDirector").checked !== false;
  $("directorGuidance").value = session.director_guidance || "";
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
  $("sessionName").value = `YT Live ${new Date().toLocaleString()}`;
  $("videoId").value = "";
  $("memoriaSession").value = "";
  Array.from($("characterSelect").options).forEach((option) => option.selected = false);
  $("injectInterval").value = 30;
  $("minPending").value = 1;
  $("maxPending").value = 12;
  $("plannedDuration").value = 30;
  $("scInterruptCooldown").value = 30;
  $("maxScPerBatch").value = 5;
  $("directorGroupTurnLimit").value = 3;
  $("directorMaxChatBatches").value = 2;
  $("autoInject").checked = true;
  $("dynamicInject").checked = true;
  $("autoFinalize").checked = true;
  $("autoScThanksOnFinalize").checked = true;
  $("autoDeleteProcessed").checked = true;
  $("autoDirector").checked = true;
  $("directorGuidance").value = "";
  $("researchEnabled").checked = false;
  $("autoTestEvents").checked = false;
  $("testEventMinSeconds").value = 20;
  $("testEventMaxSeconds").value = 45;
  $("testEventCountPerTick").value = 3;
  $("testUseLlm").checked = true;
  $("testSuperChatCountPerTick").value = 0;
  $("testMaliciousSc").checked = false;
  $("testScBurst").checked = false;
  $("directorState").textContent = "stopped";
  $("directorState").className = "status";
  $("directorJson").textContent = "{}";
  updateDirectorControls({ director_enabled: false, status: "stopped" });
  renderNoSummary();
  $("topicPackSelect").value = "";
  $("topicPackEntries").innerHTML = `<div class="muted">尚無 fact card</div>`;
  $("chatPreviewState").textContent = "尚未選擇 Live Session";
  $("chatPreviewState").className = "status warn";
  $("chatPreviewList").innerHTML = `<div class="muted">請先建立或選擇 Live Session。</div>`;
  $("sessionSelect").value = "";
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
    display_name: $("connectorName").value.trim(),
    api_key: $("apiKey").value,
    enabled: $("connectorEnabled").checked,
  };
  const data = await api("/connectors", { method: "POST", body: JSON.stringify(payload) });
  $("apiKey").value = "";
  log("Connector 已儲存", data);
  await loadConnectors();
}

export async function saveSession(createNew) {
  const payload = {
    session_id: createNew ? "" : $("sessionId").value.trim(),
    connector_id: state.connector?.connector_id || SINGLE_CONNECTOR_ID,
    display_name: $("sessionName").value.trim(),
    video_id: $("videoId").value.trim(),
    target_memoria_session_id: $("memoriaSession").value,
    character_ids: selectedCharacterIds(),
    auto_connect: true,
    auto_inject: $("autoInject").checked,
    inject_interval_seconds: Number($("injectInterval").value || 30),
    min_pending_events: Number($("minPending").value || 1),
    max_pending_events: Number($("maxPending").value || 12),
    dynamic_inject_enabled: $("dynamicInject").checked,
    planned_duration_minutes: Number($("plannedDuration").value || 30),
    sc_interrupt_cooldown_seconds: Number($("scInterruptCooldown").value || 30),
    max_sc_per_batch: Number($("maxScPerBatch").value || 5),
    director_group_turn_limit: Number($("directorGroupTurnLimit").value || 3),
    director_max_chat_batches_before_anchor: Number($("directorMaxChatBatches").value || 2),
    auto_finalize_on_duration: $("autoFinalize").checked,
    auto_sc_thanks_on_finalize: $("autoScThanksOnFinalize").checked,
    auto_delete_after_processed: $("autoDeleteProcessed").checked,
    director_guidance: $("directorGuidance").value.trim(),
    research_enabled: $("researchEnabled").checked,
    auto_test_events_enabled: $("autoTestEvents").checked,
    test_event_min_seconds: Number($("testEventMinSeconds").value || 20),
    test_event_max_seconds: Number($("testEventMaxSeconds").value || 45),
    test_event_count_per_tick: Number($("testEventCountPerTick").value || 3),
    test_event_use_llm: $("testUseLlm").checked,
    test_super_chat_count_per_tick: Number($("testSuperChatCountPerTick").value || 0),
    test_malicious_sc_enabled: $("testMaliciousSc").checked,
    test_sc_burst_mode: $("testScBurst").checked,
  };
  const data = await api("/sessions", { method: "POST", body: JSON.stringify(payload) });
  log(createNew ? "新直播已建立" : "直播設定已更新", data);
  await loadSessions(data.session_id);
  $("sessionSelect").value = data.session_id;
  fillSessionForm(data);
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

export async function deleteSession() {
  const id = selectedSessionId();
  if (!id) return;
  const data = await api(`/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
  log("runtime session 已刪除", data);
  await loadSessions("", { selectDefault: false });
  $("sessionSelect").value = "";
  newSessionDraft();
  updateLiveSessionControls();
}

export async function updateSessionSettings() {
  await saveSession(!selectedSessionId());
}

export async function toggleSession() {
  if (isSelectedSessionRunning()) {
    await sessionAction("stop");
    return;
  }
  let id = selectedSessionId();
  if (!id) {
    await saveSession(true);
    id = selectedSessionId();
  } else {
    await saveSession(false);
  }
  if (!id) throw new Error("請先建立或選擇 Live Session");
  await sessionAction("start");
  if ($("autoDirector").checked) {
    await setDirector(true, true);
  }
}

export async function refreshEvents() {
  const id = selectedSessionId();
  if (!id) return;
  const data = await api(`/sessions/${encodeURIComponent(id)}/recent?limit=100`);
  state.events = data.events || [];
  renderEvents();
  $("eventState").textContent = `${state.events.length} 則留言`;
  $("eventState").className = "status good";
}

export function renderEvents() {
  $("eventsList").innerHTML = state.events.map((event) => {
    const processed = !!event.injected_at;
    const checked = state.selectedEventIds.has(event.id) ? "checked" : "";
    const isSc = event.priority_class === "super_chat";
    const suspicious = event.safety_label && event.safety_label !== "clean";
    const amount = event.amount_display_string ? ` ${event.amount_display_string}` : "";
    const badges = `${isSc ? ` <span class="status warn">SC${escapeHtml(amount)} / tier ${escapeHtml(event.sc_tier || 0)}</span>` : ""}${suspicious ? ` <span class="status bad">安全標記</span>` : ""}`;
    return `<div class="item ${processed ? "processed" : ""} ${isSc ? "super-chat" : ""} ${suspicious ? "suspicious" : ""}">
      <label style="display:flex;gap:8px;align-items:flex-start;color:inherit">
        <input type="checkbox" data-event-id="${event.id}" ${checked} ${processed ? "disabled" : ""} style="width:auto;min-height:auto;margin-top:3px">
        <span>
          <strong>${escapeHtml(event.author_display_name || "匿名觀眾")} <span class="muted">#${escapeHtml(event.id)}${processed ? " 已注入" : ""}</span>${badges}</strong>
          <p>${escapeHtml(event.message_text || "")}</p>
        </span>
      </label>
    </div>`;
  }).join("") || `<div class="muted">尚無留言</div>`;
  $("eventsList").querySelectorAll("input[type=checkbox]").forEach((box) => {
    box.addEventListener("change", () => {
      const id = Number(box.dataset.eventId);
      if (box.checked) state.selectedEventIds.add(id);
      else state.selectedEventIds.delete(id);
    });
  });
}

export async function injectEvents(usePending) {
  const id = selectedSessionId();
  if (!id) throw new Error("請先建立或選擇 Live Session");
  const eventIds = usePending ? [] : Array.from(state.selectedEventIds);
  const payload = {
    content: $("injectContent").value,
    memoria_session_id: $("memoriaSession").value,
    character_ids: selectedCharacterIds(),
    event_ids: eventIds,
    max_events: 50,
    priority: usePending ? 120 : 220,
  };
  const data = await api(`/sessions/${encodeURIComponent(id)}/reply-recent`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  state.selectedEventIds.clear();
  log("注入完成", data);
  await refreshEvents();
  await refreshQueue();
}

export async function generateTestEvents() {
  const id = selectedSessionId();
  if (!id) throw new Error("請先建立或選擇 Live Session");
  const payload = {
    count: Number($("testCommentCount").value || 5),
    topic_hint: $("testTopicHint").value.trim(),
    use_llm: $("testUseLlm").checked,
    super_chat_count: Number($("testSuperChatCount").value || 0),
    include_malicious_sc: $("testMaliciousSc").checked,
    sc_burst: $("testScBurst").checked,
  };
  const data = await api(`/sessions/${encodeURIComponent(id)}/test-events/generate`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  log("測試留言已生成", data);
  await refreshEvents();
}

export async function toggleAutoTestEvents() {
  const id = selectedSessionId();
  if (!id) throw new Error("請先建立或選擇 Live Session");
  await saveSession(false);
  const session = selectedSessionInfo();
  const running = !!session?.runtime_status?.auto_test_events_running;
  const path = running ? "stop" : "start";
  const data = await api(`/sessions/${encodeURIComponent(id)}/test-events/auto/${path}`, {
    method: "POST",
    body: "{}",
  });
  $("autoTestEvents").checked = path === "start";
  log(`自動測試留言 ${path}`, data);
  await loadSessions(id);
  updateLiveSessionControls();
}

export async function replySuperChats() {
  const id = selectedSessionId();
  if (!id) throw new Error("請先建立或選擇 Live Session");
  const data = await api(`/sessions/${encodeURIComponent(id)}/super-chats/reply-batch`, {
    method: "POST",
    body: "{}",
  });
  log("SC 批次回應已送出", data);
  await refreshEvents();
  await refreshQueue();
}

export async function interruptNow() {
  const id = selectedSessionId();
  const data = await api(`/sessions/${encodeURIComponent(id)}/interrupt`, {
    method: "POST",
    body: JSON.stringify({ reason: "manual_ui_interrupt" }),
  });
  log("已要求中斷", data);
  await refreshQueue();
}

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

export async function writeMemory() {
  const id = selectedSessionId();
  if (!id) throw new Error("請先建立或選擇 Live Session");
  const data = await api(`/sessions/${encodeURIComponent(id)}/summary/write-memory`, {
    method: "POST",
    body: JSON.stringify({ force: false }),
  });
  log("shared memory 寫入完成", data);
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
  const button = $("toggleDirector");
  if (!button) return;
  const enabled = !!data?.director_enabled;
  button.textContent = enabled ? "停止導播" : "啟動導播";
  button.className = enabled ? "danger" : "primary";
  button.disabled = !selectedSessionId();
}

export async function updateDirectorGuidance() {
  const id = selectedSessionId();
  if (!id) throw new Error("請先建立或選擇 Live Session");
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

export async function toggleDirector() {
  const current = await refreshDirector();
  const shouldStart = !current?.director_enabled;
  await setDirector(shouldStart, shouldStart);
}

export async function refreshQueue() {
  const id = selectedSessionId();
  if (!id) return;
  const data = await api(`/sessions/${encodeURIComponent(id)}/interactions?limit=80`);
  const items = data.interactions || [];
  $("queueState").textContent = data.active ? `active: ${data.active.status}` : `${items.length} jobs`;
  $("queueState").className = data.active ? "status warn" : "status";
  $("queueList").innerHTML = items.map((item) => `
    <div class="item">
      <strong>${escapeHtml(item.source)} <span class="muted">${escapeHtml(item.status)} / p${escapeHtml(item.priority)}</span></strong>
      <p class="mono">${escapeHtml(item.job_id)}</p>
      <p>${escapeHtml(item.reply_text || item.closure_text || item.reason || item.content || "")}</p>
    </div>
  `).join("") || `<div class="muted">尚無 queue 紀錄</div>`;
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
