import { $, state, api, escapeHtml, log } from "./core.js";
import { selectedSessionId, selectedSessionInfo } from "./selectors.js";
import { loadLivePersonaOverlays, renderLivePersonaCharacterOptions } from "./live-persona-control.js?v=tts-profile-v1";
import { loadSessions, updateLiveSessionControls } from "./session-control.js?v=events-feedback-v3";
export function selectedCharacterIds() {
  return Array.from($("characterSelect").selectedOptions).map((option) => option.value).filter(Boolean);
}

export function maxSessionCharacters() {
  return Math.max(1, Number(state.maxSessionCharacters || 6) || 6);
}

export function validateSelectedCharacters() {
  if (($("episodePlanSelect")?.value || "").trim()) {
    return { ok: true, count: 0, max: maxSessionCharacters(), message: "新版企劃會依參與者名稱自動對應角色" };
  }
  const count = selectedCharacterIds().length;
  const max = maxSessionCharacters();
  if (count < 1) {
    return { ok: false, count, max, message: "請先選擇至少 1 位角色" };
  }
  if (count > max) {
    return { ok: false, count, max, message: `最多只能選擇 ${max} 位角色` };
  }
  return { ok: true, count, max, message: `已選 ${count}/${max} 位角色` };
}

export function syncCharacterSelectionLimit() {
  const select = $("characterSelect");
  const stateLabel = $("characterLimitState");
  if (!select || !stateLabel) return;
  if (($("episodePlanSelect")?.value || "").trim()) {
    const validation = validateSelectedCharacters();
    stateLabel.textContent = validation.message;
    stateLabel.className = "muted";
    return;
  }
  const max = maxSessionCharacters();
  const selected = Array.from(select.selectedOptions).filter((option) => option.value);
  if (selected.length > max) {
    selected.slice(max).forEach((option) => {
      option.selected = false;
    });
  }
  const count = selectedCharacterIds().length;
  Array.from(select.options).forEach((option) => {
    option.disabled = !!option.value && !option.selected && count >= max;
  });
  const validation = validateSelectedCharacters();
  stateLabel.textContent = validation.ok ? validation.message : `${validation.message}；目前 ${validation.count}/${validation.max}`;
  stateLabel.className = validation.ok ? "muted" : "status warn";
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

export async function loadYoutubeLiveGlobalSuffix({ silent = false } = {}) {
  const field = $("youtubeLiveGlobalSuffix");
  const stateLabel = $("youtubeLiveGlobalSuffixState");
  const reloadButton = $("reloadYoutubeLiveGlobalSuffix");
  if (!field || !stateLabel) return;
  if (!silent) {
    stateLabel.textContent = "載入中";
    stateLabel.className = "status";
    if (reloadButton) reloadButton.disabled = true;
  }
  try {
    const data = await api("/memoria/youtube-live/global-suffix");
    state.youtubeLiveGlobalSuffix = data;
    field.value = data.template || "";
    stateLabel.textContent = data.has_user_override ? "已載入自訂 override" : "已載入預設";
    stateLabel.className = data.has_user_override ? "status warn" : "status good";
    if (!silent) log("YouTube Live 全域 suffix 已載入", {
      key: data.key,
      has_user_override: !!data.has_user_override,
      template_length: (data.template || "").length,
    });
  } catch (error) {
    stateLabel.textContent = "讀取失敗";
    stateLabel.className = "status bad";
    if (!silent) log("YouTube Live 全域 suffix 讀取失敗", String(error));
  } finally {
    if (!silent && reloadButton) reloadButton.disabled = false;
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
  return session?.target_memoria_session_id || "";
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
  const list = $("chatPreviewList");
  if (!list) return;
  const newestFirst = (messages || []).slice().reverse();
  list.innerHTML = newestFirst.map((message) => {
    const role = ["user", "assistant", "system_event", "system"].includes(message.role) ? message.role : "system";
    const idText = message.message_id !== undefined && message.message_id !== null ? ` #${message.message_id}` : "";
    return `<div class="chat-msg ${escapeHtml(role)}">
      <div class="chat-msg-meta">${escapeHtml(chatRoleLabel(message))}${escapeHtml(idText)}</div>
      <div class="chat-msg-content">${escapeHtml(message.content || "")}</div>
    </div>`;
  }).join("") || `<div class="muted">目前沒有可顯示的聊天紀錄</div>`;
  list.scrollTop = 0;
}

export async function refreshChatPreview({ silent = false } = {}) {
  const previewState = $("chatPreviewState");
  const previewList = $("chatPreviewList");
  const refreshButton = $("refreshChatPreview");
  if (!previewState || !previewList) return;
  let id = selectedSessionId();
  if (!id) {
    previewState.textContent = "尚未選擇 Live Session";
    previewState.className = "status warn";
    previewList.innerHTML = `<div class="muted">請先建立或選擇 Live Session。</div>`;
    updateOpenChatLink("");
    if (!silent) log("Chat Preview 未更新", "請先建立或選擇 Live Session。");
    return;
  }
  if (!silent) {
    if (refreshButton) refreshButton.disabled = true;
    previewState.textContent = "Chat Preview 更新中...";
    previewState.className = "status";
  }
  try {
    const data = await api(`/sessions/${encodeURIComponent(id)}/chat-preview?limit=80`);
    updateOpenChatLink(data.memoria_session_id || "");
    renderChatPreview(data.messages || []);
    if (data.memoria_session_id) {
      const shown = (data.messages || []).length;
      if (data.stale) {
        previewState.textContent = `後端忙碌，使用快取：${data.memoria_session_id.slice(0, 8)}... / ${shown}/${data.message_count || 0} 則`;
        previewState.className = "status warn";
      } else {
        previewState.textContent = `MemoriaCore session: ${data.memoria_session_id.slice(0, 8)}... / ${shown}/${data.message_count || 0} 則，${shortTime()} 已更新`;
        previewState.className = "status good";
      }
    } else {
      previewState.textContent = data.stale ? "後端忙碌，沒有可用快取" : "尚未綁定 MemoriaCore session";
      previewState.className = "status warn";
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
    previewState.textContent = "Chat Preview 讀取失敗";
    previewState.className = "status bad";
    if (!silent) log("Chat Preview 讀取失敗", String(error));
  } finally {
    if (!silent && refreshButton) refreshButton.disabled = false;
  }
}

export function scheduleChatPreviewRefresh({ reloadSessions = false } = {}) {
  if (!$("chatPreviewList")) return;
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
  await loadYoutubeLiveGlobalSuffix({ silent: true });
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
  await loadYoutubeLiveGlobalSuffix({ silent: true });
  await refreshChatPreview({ silent: true });
}

export async function saveYoutubeLiveGlobalSuffix() {
  const field = $("youtubeLiveGlobalSuffix");
  const stateLabel = $("youtubeLiveGlobalSuffixState");
  const saveButton = $("saveYoutubeLiveGlobalSuffix");
  if (!field || !stateLabel || !saveButton) return;
  saveButton.disabled = true;
  stateLabel.textContent = "儲存中";
  stateLabel.className = "status";
  try {
    const data = await api(`/memoria/youtube-live/global-suffix`, {
      method: "PUT",
      body: JSON.stringify({ template: field.value }),
    });
    state.youtubeLiveGlobalSuffix = data;
    field.value = data.template || "";
    stateLabel.textContent = "已儲存 override";
    stateLabel.className = "status good";
    log("YouTube Live 全域 suffix 已儲存", {
      key: data.key,
      has_user_override: !!data.has_user_override,
      template_length: (data.template || "").length,
    });
  } catch (error) {
    stateLabel.textContent = `儲存失敗：${String(error?.message || error).slice(0, 80)}`;
    stateLabel.className = "status bad";
    throw error;
  } finally {
    saveButton.disabled = false;
  }
}

export async function loadMemoriaRefs() {
  try {
    const data = await api("/memoria/refs");
    state.maxSessionCharacters = Number(data.max_session_characters || 6);
    state.characters = data.characters || [];
    $("characterSelect").innerHTML = state.characters.map((c) =>
      `<option value="${escapeHtml(c.character_id)}" title="${escapeHtml(c.character_id)}">${escapeHtml(c.name || c.character_id)}</option>`
    ).join("");
    renderLivePersonaCharacterOptions();
    await loadLivePersonaOverlays();
    syncCharacterSelectionLimit();
    updateLiveSessionControls();
  } catch (error) {
    $("characterSelect").innerHTML = `<option value="">角色清單讀取失敗，請先設定 MemoriaCore Auth</option>`;
    state.maxSessionCharacters = 6;
    syncCharacterSelectionLimit();
    updateLiveSessionControls();
    log("角色清單讀取失敗", String(error));
  }
}
