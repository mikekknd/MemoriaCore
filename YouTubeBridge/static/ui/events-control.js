import { $, state, api, escapeHtml, log } from "./core.js";
import { selectedSessionId, selectedSessionInfo } from "./selectors.js";
import { selectedCharacterIds, selectedTargetMemoriaSessionId, validateSelectedCharacters } from "./memoria-control.js?v=events-feedback-v3";
import { loadSessions, saveSession, updateLiveSessionControls } from "./session-control.js?v=events-feedback-v3";
import { refreshQueue } from "./summary-director-control.js?v=topic-graph-sources-v2";

const EVENTS_AUTO_REFRESH_MS = 5000;
const EVENT_ACTION_OVERLAY_DELAY_MS = 350;

export function isRealYoutubeLiveSession(session = selectedSessionInfo()) {
  return !!(
    $("videoId").value.trim()
    || session?.video_id
    || session?.live_chat_id
  );
}

export function testEventControlsDisabled() {
  return isRealYoutubeLiveSession();
}

export function setEventState(message, tone = "") {
  const badge = $("eventState");
  if (!badge) return;
  badge.textContent = message;
  badge.className = `status ${tone}`.trim();
}

export function setEventActionOverlay(visible, title = "留言操作處理中", message = "正在處理留言測試操作，請稍候。") {
  const overlay = $("eventActionOverlay");
  if (!overlay) return;
  $("eventActionTitle").textContent = title;
  $("eventActionMessage").textContent = message;
  overlay.classList.toggle("is-hidden", !visible);
  overlay.setAttribute("aria-hidden", visible ? "false" : "true");
}

async function withEventActionBusy(config, action) {
  const button = $(config.buttonId);
  const originalText = button?.textContent || "";
  const originalDisabled = !!button?.disabled;
  const busyText = config.busyText || originalText || "處理中";
  const overlayTitle = config.overlayTitle || "留言操作處理中";
  const overlayMessage = config.overlayMessage || "正在處理留言測試操作，請稍候。";
  if (button) {
    button.disabled = true;
    button.textContent = busyText;
    button.setAttribute("aria-busy", "true");
  }
  setEventState(busyText, "warn");
  clearTimeout(state.eventActionOverlayTimer);
  state.eventActionOverlayTimer = setTimeout(
    () => setEventActionOverlay(true, overlayTitle, overlayMessage),
    EVENT_ACTION_OVERLAY_DELAY_MS,
  );
  try {
    const result = await action();
    const successMessage = typeof config.successMessage === "function"
      ? config.successMessage(result)
      : config.successMessage;
    if (successMessage) setEventState(successMessage, "good");
    return result;
  } catch (error) {
    const message = String(error?.message || error || "留言操作失敗");
    setEventState(message, "bad");
    throw error;
  } finally {
    clearTimeout(state.eventActionOverlayTimer);
    state.eventActionOverlayTimer = null;
    setEventActionOverlay(false);
    if (button) {
      button.textContent = originalText;
      button.disabled = originalDisabled;
      button.removeAttribute("aria-busy");
    }
    updateLiveSessionControls();
    updateTestEventControls();
  }
}

export function mergeEventIntoState(event) {
  if (!event || event.id === undefined || event.id === null) return;
  const incomingId = Number(event.id);
  const index = state.events.findIndex((item) => Number(item.id) === incomingId);
  if (index >= 0) state.events[index] = { ...state.events[index], ...event };
  else state.events.unshift(event);
}

function pruneSelectedEventIds() {
  const selectableIds = new Set(
    state.events
      .filter((event) => !event.injected_at)
      .map((event) => Number(event.id)),
  );
  for (const id of Array.from(state.selectedEventIds)) {
    if (!selectableIds.has(Number(id))) state.selectedEventIds.delete(id);
  }
}

async function refreshEventsNow({ silent = false } = {}) {
  const id = selectedSessionId();
  if (!id) {
    state.events = [];
    renderEvents();
    if (!silent) setEventState("尚未選擇 session", "");
    return { count: 0 };
  }
  const data = await api(`/sessions/${encodeURIComponent(id)}/recent?limit=100&include_pending=true`);
  state.events = data.events || [];
  pruneSelectedEventIds();
  renderEvents();
  if (!silent) setEventState(`已更新 ${state.events.length} 則留言`, "good");
  return { count: state.events.length, data };
}

export function updateTestEventControls() {
  const hasSession = !!selectedSessionId();
  const blocked = testEventControlsDisabled();
  const manualGroup = document.querySelector(".manual-events");
  const autoGroup = document.querySelector(".auto-events");
  if (manualGroup) manualGroup.classList.toggle("is-disabled", blocked);
  if (autoGroup) autoGroup.classList.toggle("is-disabled", blocked);

  const notice = $("testEventsModeNotice");
  if (notice) {
    notice.textContent = blocked
      ? "真實 YouTube 直播會停用測試留言與自動測試，避免污染正式聊天室與產生額外 LLM 開銷。請改用無 video_id 的測試直播。"
      : "測試留言只會寫入 YouTubeBridge 測試聊天室，不會送到 YouTube 平台。";
    notice.className = blocked ? "status warn" : "muted";
  }

  $("generateTestEvents").disabled = blocked || !hasSession;
  $("saveTestEventSettings").disabled = blocked || !hasSession;
  $("toggleAutoTestEvents").disabled = blocked || !hasSession;
  $("autoTestEvents").disabled = blocked;
  for (const id of [
    "testCommentCount",
    "testSuperChatCount",
    "testTopicHint",
    "testUseLlm",
    "testMaliciousSc",
    "testScBurst",
    "testEventMinSeconds",
    "testEventMaxSeconds",
    "testEventCountPerTick",
    "testSuperChatCountPerTick",
  ]) {
    const element = $(id);
    if (element) element.disabled = blocked;
  }
  if (blocked) {
    $("autoTestEvents").checked = false;
  }
}

export async function refreshEvents(options = {}) {
  if (options.silent) return await refreshEventsNow({ silent: true });
  return await withEventActionBusy({
    buttonId: "refreshEvents",
    busyText: "更新中",
    overlayTitle: "留言更新中",
    overlayMessage: "正在重新讀取待處理留言清單。",
    successMessage: (result) => `已更新 ${result?.count ?? state.events.length} 則留言`,
  }, () => refreshEventsNow());
}

export function renderEvents() {
  $("eventsList").innerHTML = state.events.map((event) => {
    const processed = !!event.injected_at;
    const checked = state.selectedEventIds.has(event.id) ? "checked" : "";
    const isSc = event.priority_class === "super_chat";
    const safetyStatus = String(event.safety_status || "");
    const safetyLabel = String(event.safety_label || "");
    const pendingSafety = safetyStatus && safetyStatus !== "completed";
    const suspicious = safetyStatus === "completed" && safetyLabel && safetyLabel !== "clean";
    const amount = event.amount_display_string ? ` ${event.amount_display_string}` : "";
    const badges = `${isSc ? ` <span class="status warn">SC${escapeHtml(amount)} / tier ${escapeHtml(event.sc_tier || 0)}</span>` : ""}${pendingSafety ? ` <span class="status warn">安全檢查中</span>` : ""}${suspicious ? ` <span class="status bad">安全標記</span>` : ""}`;
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

function eventsPaneCanAutoRefresh() {
  const eventsPane = $("eventsPane");
  return !!(
    selectedSessionId()
    && eventsPane
    && eventsPane.classList.contains("active")
    && document.visibilityState !== "hidden"
  );
}

async function refreshEventsFromAutoTimer() {
  if (!eventsPaneCanAutoRefresh() || state.eventsAutoRefreshInFlight) return;
  state.eventsAutoRefreshInFlight = true;
  try {
    await refreshEvents({ silent: true });
  } catch (error) {
    setEventState("SSE 中斷，改用輪詢更新中", "warn");
    log("留言自動更新失敗", String(error));
  } finally {
    state.eventsAutoRefreshInFlight = false;
  }
}

export function stopEventsAutoRefresh() {
  if (state.eventsAutoRefreshTimer) {
    clearInterval(state.eventsAutoRefreshTimer);
    state.eventsAutoRefreshTimer = null;
  }
}

export function startEventsAutoRefresh({ immediate = false } = {}) {
  stopEventsAutoRefresh();
  if (!eventsPaneCanAutoRefresh()) return;
  state.eventsAutoRefreshTimer = setInterval(refreshEventsFromAutoTimer, EVENTS_AUTO_REFRESH_MS);
  if (immediate) {
    void refreshEventsFromAutoTimer();
  }
}

export async function injectEvents(usePending) {
  return await withEventActionBusy({
    buttonId: usePending ? "injectPending" : "injectSelected",
    busyText: "注入中",
    overlayTitle: "留言注入中",
    overlayMessage: "正在把留言送進角色回應流程。",
    successMessage: (result) => result?.skipped ? "" : "留言注入已送出",
  }, async () => {
    const id = selectedSessionId();
    if (!id) throw new Error("請先建立或選擇 Live Session");
    const eventIds = usePending ? [] : Array.from(state.selectedEventIds);
    if (!usePending && eventIds.length === 0) {
      setEventState("請先勾選留言", "warn");
      return { skipped: true };
    }
    const validation = validateSelectedCharacters();
    if (!validation.ok) throw new Error(validation.message);
    const payload = {
      content: $("injectContent").value,
      memoria_session_id: selectedTargetMemoriaSessionId(),
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
    await refreshEvents({ silent: true });
    await refreshQueue();
    return data;
  });
}

export async function generateTestEvents() {
  return await withEventActionBusy({
    buttonId: "generateTestEvents",
    busyText: "生成中",
    overlayTitle: "測試留言生成中",
    overlayMessage: "正在產生測試留言並更新待處理列表。",
    successMessage: (data) => `已生成 ${Number(data?.generated || 0)} 則留言`,
  }, async () => {
    const id = selectedSessionId();
    if (!id) throw new Error("請先建立或選擇 Live Session");
    if (testEventControlsDisabled()) {
      throw new Error("真實 YouTube 直播不允許插入測試留言；請改用無 video_id 的測試直播。");
    }
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
    await refreshEvents({ silent: true });
    return data;
  });
}

export async function saveTestEventSettings() {
  return await withEventActionBusy({
    buttonId: "saveTestEventSettings",
    busyText: "儲存中",
    overlayTitle: "測試參數儲存中",
    overlayMessage: "正在更新本場直播的測試留言參數。",
    successMessage: "測試留言參數已儲存",
  }, async () => {
    const id = selectedSessionId();
    if (!id) throw new Error("請先建立或選擇 Live Session");
    if (testEventControlsDisabled()) {
      $("autoTestEvents").checked = false;
      throw new Error("真實 YouTube 直播不允許儲存測試留言設定；請改用無 video_id 的測試直播。");
    }
    await saveSession(false);
    log("測試留言參數已儲存", {
      min_seconds: Number($("testEventMinSeconds").value || 20),
      max_seconds: Number($("testEventMaxSeconds").value || 45),
      count_per_tick: Number($("testEventCountPerTick").value || 3),
      super_chat_count_per_tick: Number($("testSuperChatCountPerTick").value || 0),
      auto_enabled: $("autoTestEvents").checked,
    });
    return { ok: true };
  });
}

export async function toggleAutoTestEvents() {
  const running = !!selectedSessionInfo()?.runtime_status?.auto_test_events_running;
  return await withEventActionBusy({
    buttonId: "toggleAutoTestEvents",
    busyText: running ? "停止中" : "啟動中",
    overlayTitle: running ? "自動測試停止中" : "自動測試啟動中",
    overlayMessage: running ? "正在停止自動測試留言。" : "正在啟動自動測試留言。",
    successMessage: (data) => data?.path === "stop" ? "自動測試已停止" : "自動測試已啟動",
  }, async () => {
    const id = selectedSessionId();
    if (!id) throw new Error("請先建立或選擇 Live Session");
    if (testEventControlsDisabled()) {
      $("autoTestEvents").checked = false;
      throw new Error("真實 YouTube 直播不允許插入測試留言；自動測試已停用。");
    }
    await saveSession(false);
    const session = selectedSessionInfo();
    const isRunning = !!session?.runtime_status?.auto_test_events_running;
    const path = isRunning ? "stop" : "start";
    const data = await api(`/sessions/${encodeURIComponent(id)}/test-events/auto/${path}`, {
      method: "POST",
      body: "{}",
    });
    $("autoTestEvents").checked = path === "start";
    log(`自動測試留言 ${path}`, data);
    await loadSessions(id);
    updateLiveSessionControls();
    await refreshEvents({ silent: true });
    return { ...data, path };
  });
}

export async function replySuperChats() {
  return await withEventActionBusy({
    buttonId: "replySuperChats",
    busyText: "SC 回應中",
    overlayTitle: "SC 回應送出中",
    overlayMessage: "正在建立未處理 Super Chat 的回應任務。",
    successMessage: "SC 批次回應已送出",
  }, async () => {
    const id = selectedSessionId();
    if (!id) throw new Error("請先建立或選擇 Live Session");
    const data = await api(`/sessions/${encodeURIComponent(id)}/super-chats/reply-batch`, {
      method: "POST",
      body: "{}",
    });
    log("SC 批次回應已送出", data);
    await refreshEvents({ silent: true });
    await refreshQueue();
    return data;
  });
}

export async function interruptNow() {
  return await withEventActionBusy({
    buttonId: "interruptNow",
    busyText: "中斷中",
    overlayTitle: "回應中斷中",
    overlayMessage: "正在要求目前的回應任務中斷。",
    successMessage: "已要求中斷目前回應",
  }, async () => {
    const id = selectedSessionId();
    if (!id) throw new Error("請先建立或選擇 Live Session");
    const data = await api(`/sessions/${encodeURIComponent(id)}/interrupt`, {
      method: "POST",
      body: JSON.stringify({ reason: "manual_ui_interrupt" }),
    });
    log("已要求中斷", data);
    await refreshQueue();
    return data;
  });
}
