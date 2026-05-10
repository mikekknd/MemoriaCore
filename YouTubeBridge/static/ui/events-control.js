import { $, state, api, escapeHtml, log } from "./core.js";
import { selectedSessionId, selectedSessionInfo } from "./selectors.js";
import { selectedCharacterIds, selectedTargetMemoriaSessionId, validateSelectedCharacters } from "./memoria-control.js?v=topic-graph-sources-v2";
import { loadSessions, saveSession, updateLiveSessionControls } from "./session-control.js?v=topic-graph-sources-v2";
import { refreshQueue } from "./summary-director-control.js?v=topic-graph-sources-v2";
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
  const validation = validateSelectedCharacters();
  if (!validation.ok) throw new Error(validation.message);
  const eventIds = usePending ? [] : Array.from(state.selectedEventIds);
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
  await refreshEvents();
  await refreshQueue();
}

export async function generateTestEvents() {
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
  await refreshEvents();
}

export async function toggleAutoTestEvents() {
  const id = selectedSessionId();
  if (!id) throw new Error("請先建立或選擇 Live Session");
  if (testEventControlsDisabled()) {
    $("autoTestEvents").checked = false;
    throw new Error("真實 YouTube 直播不允許插入測試留言；自動測試已停用。");
  }
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
