import { $, state, api, escapeHtml, log } from "./core.js";
export function renderLivePersonaCharacterOptions() {
  const select = $("livePersonaCharacterSelect");
  if (!select) return;
  select.innerHTML = state.characters.map((character) =>
    `<option value="${escapeHtml(character.character_id)}">${escapeHtml(character.name || character.character_id)}</option>`
  ).join("");
}

function livePersonaAddressingOptions(selectedTargetId = "") {
  const currentId = $("livePersonaCharacterSelect")?.value || "";
  const characters = state.characters.filter((character) => character.character_id !== currentId);
  if (selectedTargetId && !characters.some((character) => character.character_id === selectedTargetId)) {
    characters.push({ character_id: selectedTargetId, name: selectedTargetId });
  }
  return [
    '<option value="">選擇角色</option>',
    ...characters.map((character) => {
      const id = character.character_id || "";
      const selected = id === selectedTargetId ? " selected" : "";
      return `<option value="${escapeHtml(id)}"${selected}>${escapeHtml(character.name || id)}</option>`;
    }),
  ].join("");
}

function renderLivePersonaAddressingEmpty() {
  const rows = $("livePersonaAddressingRows");
  if (!rows) return;
  if (rows.querySelector(".live-persona-addressing-row")) return;
  rows.innerHTML = '<div class="live-persona-addressing-empty muted">尚未設定其他角色稱呼。</div>';
}

export function addLivePersonaAddressingRow(targetId = "", address = "") {
  const rows = $("livePersonaAddressingRows");
  if (!rows) return;
  rows.querySelector(".live-persona-addressing-empty")?.remove();
  const row = document.createElement("div");
  row.className = "live-persona-addressing-row";
  row.innerHTML = `
    <select class="live-persona-addressing-target">${livePersonaAddressingOptions(targetId)}</select>
    <input class="live-persona-addressing-value" placeholder="例：白蓮大人、可可前輩" value="${escapeHtml(address)}">
    <button type="button" class="danger live-persona-addressing-delete" aria-label="刪除稱呼">X</button>
  `;
  row.querySelector(".live-persona-addressing-delete").addEventListener("click", () => {
    row.remove();
    renderLivePersonaAddressingEmpty();
  });
  rows.append(row);
}

function renderLivePersonaAddressingRows(addressing = {}) {
  const rows = $("livePersonaAddressingRows");
  if (!rows) return;
  rows.innerHTML = "";
  const entries = Object.entries(addressing || {}).filter(([targetId, address]) => targetId && address);
  for (const [targetId, address] of entries) {
    addLivePersonaAddressingRow(targetId, address);
  }
  renderLivePersonaAddressingEmpty();
}

function readLivePersonaAddressingRows() {
  const rows = $("livePersonaAddressingRows");
  const addressing = {};
  if (!rows) return addressing;
  for (const row of rows.querySelectorAll(".live-persona-addressing-row")) {
    const targetId = row.querySelector(".live-persona-addressing-target")?.value || "";
    const address = row.querySelector(".live-persona-addressing-value")?.value.trim() || "";
    if (!targetId && !address) continue;
    if (!targetId || !address) {
      throw new Error("每筆稱呼都需要選擇角色並填入稱呼");
    }
    if (addressing[targetId]) {
      throw new Error("同一個角色只能設定一筆稱呼");
    }
    addressing[targetId] = address;
  }
  return addressing;
}

export function livePersonaOverlayFor(characterId) {
  return (state.livePersonaOverlays || []).find((overlay) => overlay.character_id === characterId) || {
    character_id: characterId,
    enabled: false,
    mode: "replace",
    system_prompt: "",
    self_address: "",
    addressing: {},
    opening_intro: "",
    reply_rules: "",
  };
}

export function liveTtsProfileFor(characterId) {
  return (state.liveTtsProfiles || []).find((profile) => profile.character_id === characterId) || {
    character_id: characterId,
    enabled: false,
    ref_audio_path: "",
    prompt_text: "",
    text_lang: "zh",
    prompt_lang: "zh",
    speed_factor: 1,
    media_type: "wav",
  };
}

function renderLiveTtsSourceOptions(selectedAudioPath = "") {
  const select = $("liveTtsSourcePreset");
  if (!select) return;
  const sources = state.liveTtsSources || [];
  select.innerHTML = [
    '<option value="">手動輸入範例語音</option>',
    ...sources.map((source) => {
      const audioPath = source.audio_path || "";
      const selected = audioPath && audioPath === selectedAudioPath ? " selected" : "";
      return `<option value="${escapeHtml(audioPath)}"${selected}>${escapeHtml(source.name || audioPath)}</option>`;
    }),
  ].join("");
}

export function applyLiveTtsSourcePreset() {
  const select = $("liveTtsSourcePreset");
  if (!select || !select.value) return;
  const source = (state.liveTtsSources || []).find((item) => item.audio_path === select.value);
  if (!source) return;
  $("liveTtsRefAudioPath").value = source.audio_path || "";
  $("liveTtsPromptText").value = source.prompt_text || "";
}

export function fillLivePersonaOverlayForm(overlay) {
  if (!$("livePersonaCharacterSelect")) return;
  $("livePersonaEnabled").checked = !!overlay.enabled;
  $("livePersonaMode").value = overlay.mode || "replace";
  $("livePersonaSelfAddress").value = overlay.self_address || "";
  $("livePersonaSystemPrompt").value = overlay.system_prompt || "";
  $("livePersonaOpeningIntro").value = overlay.opening_intro || "";
  renderLivePersonaAddressingRows(overlay.addressing || {});
  $("livePersonaReplyRules").value = overlay.reply_rules || "";
  fillLiveTtsProfileForm(liveTtsProfileFor(overlay.character_id || $("livePersonaCharacterSelect").value));
}

export function fillLiveTtsProfileForm(profile) {
  if (!$("liveTtsEnabled")) return;
  $("liveTtsEnabled").checked = !!profile.enabled;
  $("liveTtsRefAudioPath").value = profile.ref_audio_path || "";
  $("liveTtsPromptText").value = profile.prompt_text || "";
  $("liveTtsTextLang").value = profile.text_lang || "zh";
  $("liveTtsPromptLang").value = profile.prompt_lang || "zh";
  $("liveTtsSpeedFactor").value = Number(profile.speed_factor || 1);
  $("liveTtsMediaType").value = profile.media_type || "wav";
  renderLiveTtsSourceOptions(profile.ref_audio_path || "");
}

export async function loadLivePersonaOverlays() {
  const stateLabel = $("livePersonaOverlayState");
  if (!stateLabel || !$("livePersonaCharacterSelect")) return;
  try {
    const data = await api("/persona-overlays");
    try {
      const sourceData = await api("/tts-sources");
      state.liveTtsSources = sourceData.sources || [];
    } catch (error) {
      state.liveTtsSources = [];
      log("TTS 聲音來源讀取失敗", String(error));
    }
    state.livePersonaOverlays = data.overlays || [];
    state.liveTtsProfiles = data.tts_profiles || [];
    const selectedId = $("livePersonaCharacterSelect").value || state.characters[0]?.character_id || "";
    if (selectedId) $("livePersonaCharacterSelect").value = selectedId;
    fillLivePersonaOverlayForm(livePersonaOverlayFor(selectedId));
    const enabledCount = state.livePersonaOverlays.filter((overlay) => overlay.enabled).length;
    stateLabel.textContent = `已載入，啟用 ${enabledCount} 位`;
    stateLabel.className = "status good";
  } catch (error) {
    stateLabel.textContent = "讀取失敗";
    stateLabel.className = "status bad";
    log("直播角色設定讀取失敗", String(error));
  }
}

export function livePersonaOverlayPayload() {
  return {
    enabled: $("livePersonaEnabled").checked,
    mode: $("livePersonaMode").value || "replace",
    system_prompt: $("livePersonaSystemPrompt").value.trim(),
    self_address: $("livePersonaSelfAddress").value.trim(),
    addressing: readLivePersonaAddressingRows(),
    opening_intro: $("livePersonaOpeningIntro").value.trim(),
    reply_rules: $("livePersonaReplyRules").value.trim(),
  };
}

export function liveTtsProfilePayload() {
  const enabled = $("liveTtsEnabled")?.checked || false;
  const refAudioPath = $("liveTtsRefAudioPath")?.value.trim() || "";
  const promptText = $("liveTtsPromptText")?.value.trim() || "";
  if (enabled && !refAudioPath) throw new Error("啟用 TTS 時必須填入範例語音路徑");
  if (enabled && !promptText) throw new Error("啟用 TTS 時必須填入範例語音 transcript");
  return {
    enabled,
    ref_audio_path: refAudioPath,
    prompt_text: promptText,
    text_lang: $("liveTtsTextLang")?.value.trim() || "zh",
    prompt_lang: $("liveTtsPromptLang")?.value.trim() || "zh",
    speed_factor: Number($("liveTtsSpeedFactor")?.value || 1),
    media_type: $("liveTtsMediaType")?.value || "wav",
  };
}

export async function saveLivePersonaOverlay() {
  const characterId = $("livePersonaCharacterSelect").value;
  if (!characterId) throw new Error("請先選擇角色");
  const stateLabel = $("livePersonaOverlayState");
  const button = $("saveLivePersonaOverlay");
  button.disabled = true;
  stateLabel.textContent = "儲存中";
  stateLabel.className = "status";
  try {
    const data = await api(`/persona-overlays/${encodeURIComponent(characterId)}`, {
      method: "POST",
      body: JSON.stringify(livePersonaOverlayPayload()),
    });
    const ttsProfile = await api(`/persona-overlays/${encodeURIComponent(characterId)}/tts-profile`, {
      method: "POST",
      body: JSON.stringify(liveTtsProfilePayload()),
    });
    const others = (state.livePersonaOverlays || []).filter((overlay) => overlay.character_id !== characterId);
    state.livePersonaOverlays = [data, ...others];
    const otherProfiles = (state.liveTtsProfiles || []).filter((profile) => profile.character_id !== characterId);
    state.liveTtsProfiles = [ttsProfile, ...otherProfiles];
    fillLivePersonaOverlayForm(data);
    stateLabel.textContent = data.enabled || ttsProfile.enabled ? "已儲存並啟用" : "已儲存但未啟用";
    stateLabel.className = data.enabled || ttsProfile.enabled ? "status good" : "status warn";
    log("直播角色設定已儲存", {
      character_id: data.character_id,
      enabled: data.enabled,
      mode: data.mode,
      tts_enabled: ttsProfile.enabled,
    });
  } catch (error) {
    stateLabel.textContent = `儲存失敗：${String(error?.message || error).slice(0, 80)}`;
    stateLabel.className = "status bad";
    throw error;
  } finally {
    button.disabled = false;
  }
}
