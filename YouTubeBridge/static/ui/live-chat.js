const state = {
  sessionId: "",
  subscribedSessionId: "",
  eventSource: null,
  newestBottom: true,
  refreshTimer: null,
  fallbackRefreshTimer: null,
  historyRefreshTimer: null,
  historyRefreshInFlight: false,
  interruptRecoveryTimers: [],
  presentationQueue: [],
  presentationPlaying: false,
  currentPresentationItem: null,
  currentAudio: null,
  presentationAudioCache: new Map(),
  durationRefreshTimer: null,
  sessionTiming: null,
  startupRetryTimers: [],
  liveEventMessages: [],
  displayMessages: [],
  presentationEnabled: false,
  characterColorMap: {},
  nextColorIndex: 0,
  nextMessageOrder: 0,
};
const LIVE_CHAT_REFRESH_TYPES = new Set([
  "chat_message",
  "youtube_live_event",
  "safety_classified",
  "test_events_generated",
  "test_events_auto_generated",
  "super_chat_batch_injected",
  "interaction_started",
  "interaction_completed",
  "interaction_interrupted",
  "interaction_failed",
  "memoria_injected",
  "director_injected",
  "closing_super_chat_thanks_completed",
  "status",
]);
const ASSISTANT_COLOR_PALETTE = [
  { color: "#0f766e", bg: "#ecfdf5" },
  { color: "#2563eb", bg: "#eff6ff" },
  { color: "#c2410c", bg: "#fff7ed" },
  { color: "#7c3aed", bg: "#f5f3ff" },
  { color: "#be123c", bg: "#fff1f2" },
  { color: "#047857", bg: "#ecfdf5" },
];
const ASSISTANT_COLOR_CLASSES = ASSISTANT_COLOR_PALETTE.map((_item, index) => `character-color-${index}`);
const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  "\"": "&quot;",
  "'": "&#39;",
}[ch]));
let _bridgeKey = "";

async function initBridgeKey() {
  try {
    const cfg = await fetch("/ui-config").then((r) => r.json());
    _bridgeKey = cfg.bridge_key || "";
  } catch {
    _bridgeKey = "";
  }
}

async function api(path) {
  const headers = _bridgeKey ? { "X-Bridge-Key": _bridgeKey } : {};
  const response = await fetch(path, { headers });
  const text = await response.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
  if (!response.ok) throw new Error(data.detail ? JSON.stringify(data.detail) : text || response.statusText);
  return data;
}
async function apiPost(path) {
  const headers = _bridgeKey ? { "X-Bridge-Key": _bridgeKey } : {};
  const response = await fetch(path, { method: "POST", headers });
  const text = await response.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
  if (!response.ok) throw new Error(data.detail ? JSON.stringify(data.detail) : text || response.statusText);
  return data;
}
function roleLabel(message) {
  if (message.role === "assistant") return message.character_name || "AI";
  if (message.role === "system_event" && message.source === "youtube_live_event") return "直播留言";
  if (message.role === "system_event") return "直播事件";
  if (message.role === "user") return "使用者";
  return message.role || "訊息";
}
function timeLabel(message) {
  const value = message.created_at || message.timestamp || "";
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-TW", { hour12: false });
}
function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "--";
  const totalSeconds = Math.floor(seconds);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const remainSeconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(remainSeconds).padStart(2, "0")}`;
  }
  return `${minutes}:${String(remainSeconds).padStart(2, "0")}`;
}
function updateDurationBadge() {
  const badge = $("durationBadge");
  const timing = state.sessionTiming;
  if (!badge || !timing?.startedAt) {
    if (badge) badge.textContent = "已直播 -- / 目標 --";
    return;
  }
  const start = new Date(timing.startedAt);
  if (Number.isNaN(start.getTime())) {
    badge.textContent = "已直播 -- / 目標 --";
    return;
  }
  const end = timing.finalizedAt ? new Date(timing.finalizedAt) : new Date();
  const elapsedSeconds = Math.max(0, (Number.isNaN(end.getTime()) ? Date.now() : end.getTime()) - start.getTime()) / 1000;
  const plannedMinutes = Number(timing.plannedMinutes || 0);
  const target = plannedMinutes > 0 ? formatDuration(plannedMinutes * 60) : "未設定";
  badge.textContent = `已直播 ${formatDuration(elapsedSeconds)} / 目標 ${target}`;
}
function startDurationRefresh() {
  if (state.durationRefreshTimer) return;
  updateDurationBadge();
  state.durationRefreshTimer = setInterval(updateDurationBadge, 1000);
}
function stopDurationRefresh() {
  if (!state.durationRefreshTimer) return;
  clearInterval(state.durationRefreshTimer);
  state.durationRefreshTimer = null;
}
function setCharacterColorMap(characterIds) {
  state.characterColorMap = {};
  state.nextColorIndex = 0;
  (characterIds || []).forEach((characterId) => {
    const colorKey = String(characterId || "").trim();
    if (!colorKey || state.characterColorMap[colorKey] !== undefined) return;
    state.characterColorMap[colorKey] = state.nextColorIndex % ASSISTANT_COLOR_CLASSES.length;
    state.nextColorIndex += 1;
  });
}
function characterColorIndex(message) {
  if (message.role !== "assistant") return -1;
  const colorKey = message.character_id || message.character_name || roleLabel(message);
  if (state.characterColorMap[colorKey] !== undefined) {
    return state.characterColorMap[colorKey];
  }
  state.characterColorMap[colorKey] = state.nextColorIndex % ASSISTANT_COLOR_CLASSES.length;
  state.nextColorIndex += 1;
  return state.characterColorMap[colorKey];
}
function characterColorClass(message) {
  const index = characterColorIndex(message);
  return index >= 0 ? ASSISTANT_COLOR_CLASSES[index] : "";
}
function characterColorStyle(message) {
  const index = characterColorIndex(message);
  if (index < 0) return "";
  const palette = ASSISTANT_COLOR_PALETTE[index];
  return ` style="--character-color: ${palette.color}; --character-bg: ${palette.bg};"`;
}
function eventMessageId(event) {
  return `yt-${event.id || event.event_id || event.youtube_message_id || `${event.author_display_name || "anon"}-${event.published_at || event.received_at || event.message_text || ""}`}`;
}
function liveEventToMessage(event) {
  const author = String(event.author_display_name || "匿名觀眾").trim() || "匿名觀眾";
  const amount = String(event.amount_display_string || "").trim();
  const prefix = String(event.priority_class || "") === "super_chat"
    ? (amount ? `[SC ${amount}] ` : "[SC] ")
    : "";
  return {
    message_id: eventMessageId(event),
    role: "system_event",
    content: `${prefix}${author}: ${event.message_text || ""}`.trim(),
    created_at: event.published_at || event.received_at || event.created_at || "",
    timestamp: event.published_at || event.received_at || event.created_at || "",
    source: "youtube_live_event",
  };
}
function assignMessageOrder(message) {
  const existing = Number(message._liveChatOrder || 0);
  if (Number.isFinite(existing) && existing > 0) return existing;
  state.nextMessageOrder += 1;
  message._liveChatOrder = state.nextMessageOrder;
  return state.nextMessageOrder;
}
function messageTimeValue(message) {
  const value = message.created_at || message.timestamp || "";
  if (!value) return null;
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : null;
}
function numericMessageId(message) {
  const raw = message.message_id;
  if (raw === null || raw === undefined || raw === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}
function numericMessageIdOrder(left, right) {
  const leftId = numericMessageId(left);
  const rightId = numericMessageId(right);
  if (leftId !== null && rightId !== null && leftId !== rightId) {
    return leftId - rightId;
  }
  return 0;
}
function fallbackMessageOrder(left, right) {
  const order = Number(left._liveChatOrder || 0) - Number(right._liveChatOrder || 0);
  if (order !== 0) return order;
  return String(left.message_id || "").localeCompare(String(right.message_id || ""));
}
function compareMessageOrder(left, right) {
  const leftTime = messageTimeValue(left);
  const rightTime = messageTimeValue(right);
  if (leftTime !== null && rightTime !== null) {
    if (leftTime !== rightTime) return leftTime - rightTime;
    return numericMessageIdOrder(left, right) || fallbackMessageOrder(left, right);
  }
  if (leftTime !== null) return -1;
  if (rightTime !== null) return 1;
  return numericMessageIdOrder(left, right) || fallbackMessageOrder(left, right);
}
function mergeMessages(...groups) {
  const seen = new Set();
  const merged = [];
  groups.flat().forEach((message) => {
    if (!message || !String(message.content || "").trim()) return;
    const messageId = message.message_id || "";
    const key = messageId
      ? `${message.role || "message"}:${messageId}`
      : `${message.source || message.role}:${message.created_at || message.timestamp || ""}:${message.content || ""}`;
    if (seen.has(key)) return;
    seen.add(key);
    assignMessageOrder(message);
    merged.push(message);
  });
  return merged.sort(compareMessageOrder);
}
function visibleMessages(messages) {
  return (messages || []).filter((message) => {
    const role = String(message.role || "");
    const text = String(message.content || "").trim();
    if (!text) return false;
    return ["assistant", "system_event", "user"].includes(role);
  });
}
function render(messages) {
  const list = $("chatList");
  const ordered = state.newestBottom ? messages : messages.slice().reverse();
  list.className = `list ${state.newestBottom ? "newest-bottom" : "newest-top"}`;
  list.innerHTML = ordered.map((message) => {
    const role = ["assistant", "system_event", "user"].includes(message.role) ? message.role : "system_event";
    const colorClass = role === "assistant" ? ` ${escapeHtml(characterColorClass(message))}` : "";
    return `<article class="msg ${escapeHtml(role)}${colorClass}"${characterColorStyle(message)}>
      <div class="meta"><span class="name">${escapeHtml(roleLabel(message))}</span><span>${escapeHtml(timeLabel(message))}</span></div>
      <div class="content">${escapeHtml(message.content || "")}</div>
    </article>`;
  }).join("") || `<div class="empty">目前沒有可顯示的直播對話。</div>`;
  if (state.newestBottom) {
    list.scrollTop = list.scrollHeight;
  } else {
    list.scrollTop = 0;
  }
}
function setOrder(newestBottom) {
  state.newestBottom = newestBottom;
  $("orderBottom").classList.toggle("active", newestBottom);
  $("orderTop").classList.toggle("active", !newestBottom);
  refreshChat({ silent: true });
}
function clearStartupRetries() {
  state.startupRetryTimers.forEach((timer) => clearTimeout(timer));
  state.startupRetryTimers = [];
}
function clearInterruptRecoveryRefreshes() {
  state.interruptRecoveryTimers.forEach((timer) => clearTimeout(timer));
  state.interruptRecoveryTimers = [];
}
function scheduleInterruptRecoveryRefreshes() {
  clearInterruptRecoveryRefreshes();
  [0, 500, 1500, 3000, 6000, 10000, 20000, 35000].forEach((delay) => {
    const timer = setTimeout(() => {
      state.interruptRecoveryTimers = state.interruptRecoveryTimers.filter((item) => item !== timer);
      refreshChat({ silent: true });
    }, delay);
    state.interruptRecoveryTimers.push(timer);
  });
}
function scheduleStartupRetries() {
  clearStartupRetries();
  [500, 1500, 3000, 6000].forEach((delay) => {
    const timer = setTimeout(() => {
      state.startupRetryTimers = state.startupRetryTimers.filter((item) => item !== timer);
      refreshChat({ silent: true });
    }, delay);
    state.startupRetryTimers.push(timer);
  });
}
function cacheLiveEvents(events) {
  if (state.presentationEnabled) return;
  const messages = (events || []).map(liveEventToMessage).filter((message) => message.content);
  state.liveEventMessages = mergeMessages(state.liveEventMessages, messages).slice(-120);
}
async function pickSession() {
  const sessions = await api("/sessions");
  const params = new URLSearchParams(location.search);
  const requested = params.get("session_id") || "";
  const selected = sessions.find((session) => session.session_id === requested)
    || sessions.find((session) => (session.runtime_status?.running || session.status === "running") && session.target_memoria_session_id)
    || sessions.find((session) => session.status === "closing" && session.target_memoria_session_id)
    || sessions.find((session) => session.status === "ended" && session.target_memoria_session_id)
    || sessions.find((session) => session.target_memoria_session_id)
    || sessions.find((session) => session.runtime_status?.running || session.status === "running")
    || sessions.find((session) => session.status === "closing")
    || sessions.find((session) => session.status === "ended")
    || sessions[0];
  return selected || null;
}
async function ensureSession() {
  const selected = await pickSession();
  if (!selected) {
    state.sessionId = "";
    state.subscribedSessionId = "";
    clearInterruptRecoveryRefreshes();
    stopHistoryRefresh();
    stopDurationRefresh();
    state.sessionTiming = null;
    $("sessionBadge").textContent = "尚無 live session";
    updateDurationBadge();
    return null;
  }
  if (selected.session_id !== state.sessionId) {
    state.sessionId = selected.session_id;
    state.liveEventMessages = [];
    state.displayMessages = [];
    clearInterruptRecoveryRefreshes();
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
    stopHistoryRefresh();
    state.subscribedSessionId = "";
    setCharacterColorMap(selected.character_ids || []);
    scheduleStartupRetries();
  }
  state.presentationEnabled = !!selected.presentation_enabled;
  if (state.presentationEnabled) {
    state.liveEventMessages = [];
  }
  state.sessionTiming = {
    startedAt: selected.started_at || selected.created_at || "",
    finalizedAt: selected.finalized_at || "",
    plannedMinutes: selected.planned_duration_minutes,
  };
  startDurationRefresh();
  updateDurationBadge();
  $("sessionBadge").textContent = `${selected.display_name || "YT Live"} / ${selected.session_id.slice(0, 10)}...`;
  return selected;
}
function ensureSubscription() {
  if (!state.sessionId || state.subscribedSessionId === state.sessionId) return;
  subscribe(state.sessionId);
  state.subscribedSessionId = state.sessionId;
}
async function refreshChat({ silent = false } = {}) {
  try {
    const selected = await ensureSession();
    if (!selected || !state.sessionId) {
      render([]);
      return;
    }
    ensureSubscription();
    startHistoryRefresh();
    const [data, recent] = await Promise.all([
      api(`/sessions/${encodeURIComponent(state.sessionId)}/chat-preview?limit=120`),
      api(`/sessions/${encodeURIComponent(state.sessionId)}/recent?limit=120`),
    ]);
    cacheLiveEvents(recent.events || []);
    const liveEventMessages = state.presentationEnabled ? [] : state.liveEventMessages;
    const messages = visibleMessages(mergeMessages(state.displayMessages, liveEventMessages, data.messages || []));
    state.displayMessages = messages;
    render(messages);
    $("countBadge").textContent = `${messages.length}/${data.message_count || 0} 則`;
    if (!messages.length && !data.message_count) {
      $("updatedBadge").textContent = data.stale ? "等待後端同步（快取）" : "等待後端同步";
    } else {
      $("updatedBadge").textContent = data.stale ? "使用快取" : new Date().toLocaleTimeString("zh-TW", { hour12: false });
    }
    if (data.memoria_session_id) {
      $("sessionBadge").textContent = `MemoriaCore ${data.memoria_session_id.slice(0, 8)}...`;
    }
  } catch (error) {
    if (!silent) {
      $("updatedBadge").textContent = `更新失敗：${String(error).slice(0, 80)}`;
    }
  }
}
function scheduleRefresh(delay = 0) {
  if (state.refreshTimer) clearTimeout(state.refreshTimer);
  state.refreshTimer = setTimeout(() => {
    state.refreshTimer = null;
    refreshChat({ silent: true });
  }, Math.max(0, Number(delay) || 0));
}
function appendLiveEvent(event) {
  if (state.presentationEnabled) return;
  if (!event) return;
  cacheLiveEvents([event]);
  const messages = visibleMessages(mergeMessages(state.displayMessages, state.liveEventMessages));
  state.displayMessages = messages;
  render(messages);
  $("countBadge").textContent = `${messages.length}+ 則`;
  $("updatedBadge").textContent = new Date().toLocaleTimeString("zh-TW", { hour12: false });
}
function appendChatMessage(message) {
  if (!message) return;
  const messages = visibleMessages(mergeMessages(state.displayMessages, [message]));
  state.displayMessages = messages;
  render(messages);
  $("countBadge").textContent = `${messages.length}+ 則`;
  $("updatedBadge").textContent = new Date().toLocaleTimeString("zh-TW", { hour12: false });
}
function presentationItemToMessage(item) {
  return {
    message_id: item.message_id || item.item_id,
    role: "assistant",
    content: item.text || "",
    created_at: new Date().toISOString(),
    timestamp: new Date().toISOString(),
    character_id: item.character_id || "",
    character_name: item.character_name || "AI",
    source: "presentation",
  };
}
function stopPresentationPlayback() {
  if (state.currentAudio) {
    state.currentAudio.pause();
    state.currentAudio.src = "";
  }
  state.presentationAudioCache.forEach((audio) => {
    audio.pause();
    audio.src = "";
  });
  state.presentationAudioCache.clear();
  state.presentationQueue = [];
  state.presentationPlaying = false;
  state.currentPresentationItem = null;
  state.currentAudio = null;
}
function handleInteractionInterrupt(payload = {}) {
  if (state.presentationEnabled) {
    stopPresentationPlayback();
    if (state.sessionId) {
      apiPost(`/sessions/${encodeURIComponent(state.sessionId)}/presentation/current/skip`).catch(() => {});
    }
  }
  scheduleInterruptRecoveryRefreshes();
  scheduleRefresh(0);
}
async function ackPresentationItem(item) {
  if (!item?.item_id || !state.sessionId) return;
  await apiPost(`/sessions/${encodeURIComponent(state.sessionId)}/presentation/${encodeURIComponent(item.item_id)}/ack`);
}
function cachePresentationAudio(item) {
  const itemId = String(item?.item_id || "");
  const audioUrl = String(item?.audio_url || "");
  if (!itemId || !audioUrl || state.presentationAudioCache.has(itemId)) return;
  const audio = new Audio(audioUrl);
  audio.preload = "auto";
  state.presentationAudioCache.set(itemId, audio);
}
function audioForPresentationItem(item) {
  const itemId = String(item?.item_id || "");
  const cached = itemId ? state.presentationAudioCache.get(itemId) : null;
  if (cached) {
    state.presentationAudioCache.delete(itemId);
    return cached;
  }
  const audio = new Audio(item.audio_url || "");
  audio.preload = "auto";
  return audio;
}
function finishPresentationItem(item) {
  ackPresentationItem(item).catch(() => {});
  state.presentationPlaying = false;
  state.currentPresentationItem = null;
  state.currentAudio = null;
  playPresentationItem();
}
function playPresentationItem() {
  if (state.presentationPlaying) return;
  const item = state.presentationQueue.shift();
  if (!item) return;
  state.presentationPlaying = true;
  state.currentPresentationItem = item;
  appendChatMessage(presentationItemToMessage(item));
  const audioUrl = item.audio_url || "";
  if (!audioUrl) {
    finishPresentationItem(item);
    return;
  }
  const audio = audioForPresentationItem(item);
  state.currentAudio = audio;
  audio.addEventListener("ended", () => finishPresentationItem(item), { once: true });
  audio.addEventListener("error", () => finishPresentationItem(item), { once: true });
  audio.play().catch(() => {
    $("enableAudio").classList.remove("hidden");
    state.presentationQueue.unshift(item);
    state.presentationPlaying = false;
    state.currentAudio = null;
  });
}
function enqueuePresentationItem(item) {
  if (!item?.item_id) return;
  cachePresentationAudio(item);
  state.presentationQueue.push(item);
  playPresentationItem();
}
async function skipCurrentPresentation() {
  if (state.currentAudio) {
    state.currentAudio.pause();
    state.currentAudio.src = "";
  }
  if (state.sessionId) {
    await apiPost(`/sessions/${encodeURIComponent(state.sessionId)}/presentation/current/skip`).catch(() => {});
  }
  state.presentationPlaying = false;
  state.currentPresentationItem = null;
  state.currentAudio = null;
  playPresentationItem();
}
function startFallbackRefresh() {
  if (state.fallbackRefreshTimer) return;
  state.fallbackRefreshTimer = setInterval(() => refreshChat({ silent: true }), 4000);
}
function stopFallbackRefresh() {
  if (!state.fallbackRefreshTimer) return;
  clearInterval(state.fallbackRefreshTimer);
  state.fallbackRefreshTimer = null;
}
function startHistoryRefresh() {
  if (!state.sessionId || state.historyRefreshTimer) return;
  state.historyRefreshTimer = setInterval(async () => {
    if (!state.sessionId || state.historyRefreshInFlight) return;
    state.historyRefreshInFlight = true;
    try {
      await refreshChat({ silent: true });
    } finally {
      state.historyRefreshInFlight = false;
    }
  }, 2500);
}
function stopHistoryRefresh() {
  if (!state.historyRefreshTimer) return;
  clearInterval(state.historyRefreshTimer);
  state.historyRefreshTimer = null;
  state.historyRefreshInFlight = false;
}
function subscribe(sessionId) {
  if (state.eventSource) state.eventSource.close();
  if (!sessionId) return;
  state.eventSource = new EventSource(`/sessions/${encodeURIComponent(sessionId)}/events`);
  state.eventSource.onopen = () => {
    stopFallbackRefresh();
    $("updatedBadge").textContent = "即時連線中";
  };
  state.eventSource.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      if (payload.type === "youtube_live_event" && payload.event) {
        appendLiveEvent(payload.event);
        scheduleRefresh(1000);
        return;
      }
      if (payload.type === "interrupt_requested") {
        handleInteractionInterrupt(payload);
        return;
      }
      if (payload.type === "interaction_interrupted") {
        scheduleInterruptRecoveryRefreshes();
        scheduleRefresh(0);
        return;
      }
      if (payload.type === "presentation_item_preload" && payload.item) {
        cachePresentationAudio(payload.item);
        return;
      }
      if (payload.type === "presentation_item_ready" && payload.item) {
        enqueuePresentationItem(payload.item);
        return;
      }
      if (payload.type === "chat_message" && payload.message) {
        appendChatMessage(payload.message);
        scheduleRefresh(1000);
        return;
      }
      if (LIVE_CHAT_REFRESH_TYPES.has(payload.type)) {
        scheduleRefresh(0);
      }
    } catch {
      scheduleRefresh(0);
    }
  };
  state.eventSource.onerror = () => {
    $("updatedBadge").textContent = "即時連線中斷，使用 fallback";
    startFallbackRefresh();
    scheduleRefresh(0);
  };
}
$("orderBottom").onclick = () => setOrder(true);
$("orderTop").onclick = () => setOrder(false);
$("refresh").onclick = () => refreshChat();
$("enableAudio").onclick = () => {
  $("enableAudio").classList.add("hidden");
  playPresentationItem();
};
$("skipPresentation").onclick = () => {
  skipCurrentPresentation().catch(() => {});
};
initBridgeKey().then(() => refreshChat());
