# YouTubeBridge Studio TTS Playback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/studio/` the main TTS playback and ACK surface for YouTubeBridge live sessions, with one sentence played at a time and the next ready audio cached before the current one ends.

**Architecture:** Keep the existing backend presentation queue and GPT-SoVITS synthesis flow. Add a focused Studio-side presentation player that consumes `presentation_item_ready`, preloads received audio URLs, ACKs only after playback finishes or fallback text is accepted, and clears stale local playback when backend interruption events arrive.

**Tech Stack:** FastAPI static UI assets, vanilla JavaScript, HTML/CSS, pytest source-contract tests, existing YouTubeBridge presentation queue tests.

---

## File Structure

- Modify `YouTubeBridge/tests/test_studio_ui.py`
  - Add fail-first source-contract tests one slice at a time.
  - Keep each commit green before moving to the next slice.
- Modify `YouTubeBridge/static/studio.html`
  - Add compact conversation-toolbar controls for presentation audio status, audio unlock, and skip-current-sentence.
  - Bump Studio asset query strings from `studio-v24` to `studio-v25`.
- Modify `YouTubeBridge/static/ui/studio.css`
  - Style the compact presentation audio controls inside the existing conversation toolbar.
- Modify `YouTubeBridge/static/ui/studio.js`
  - Map saved Studio output settings into live session start payload.
  - Add presentation playback state, audio preload cache, ACK/skip helpers, interrupt handling, and lifecycle cleanup.
  - Route SSE `presentation_item_ready` into the player instead of only refreshing the conversation.
- Do not modify `YouTubeBridge/static/ui/live-chat.js`
  - It remains the legacy reference surface and must not become the shared implementation in this task.
- Do not modify backend routes
  - Existing routes are sufficient: `/sessions/current/start`, `/sessions/{session_id}/events`, `/sessions/{session_id}/presentation/{item_id}/ack`, `/sessions/{session_id}/presentation/current/skip`, and presentation audio URLs.

## Task 1: Enable Studio Session Flags and Asset Version

**Files:**
- Modify: `YouTubeBridge/tests/test_studio_ui.py`
- Modify: `YouTubeBridge/static/studio.html`
- Modify: `YouTubeBridge/static/ui/studio.js`
- Test: `YouTubeBridge/tests/test_studio_ui.py`

- [ ] **Step 1: Update fail-first tests for cache busting and payload mapping**

Change `test_studio_html_uses_external_assets_without_inline_code()` to expect `studio-v25`:

```python
def test_studio_html_uses_external_assets_without_inline_code():
    studio_html = _studio_source()

    assert '<link rel="stylesheet" href="/ui-assets/studio.css?v=studio-v25">' in studio_html
    assert '<script type="module" src="/ui-assets/studio.js?v=studio-v25"></script>' in studio_html
    assert "<style>" not in studio_html
    assert "<script>\n" not in studio_html
```

In `test_studio_p0_exposes_preflight_and_manual_source_session_flow()`, replace the fixed-false assertions with setting-mapping assertions:

```python
    assert 'presentation_enabled: liveDefaults.presentation_queue_enabled' in studio_js
    assert 'tts_enabled: liveDefaults.tts_enabled' in studio_js
    assert 'presentation_enabled: false' not in studio_js
    assert 'tts_enabled: false' not in studio_js
```

- [ ] **Step 2: Run the targeted tests and verify they fail**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_ui.py::test_studio_html_uses_external_assets_without_inline_code YouTubeBridge/tests/test_studio_ui.py::test_studio_p0_exposes_preflight_and_manual_source_session_flow -q
```

Expected: FAIL because `studio.html` still uses `studio-v24`, and `studioLiveSessionPayload()` still sends `presentation_enabled: false` and `tts_enabled: false`.

- [ ] **Step 3: Bump Studio asset URLs**

In `YouTubeBridge/static/studio.html`, change both asset query strings to `studio-v25`:

```html
  <link rel="stylesheet" href="/ui-assets/studio.css?v=studio-v25">
```

```html
  <script type="module" src="/ui-assets/studio.js?v=studio-v25"></script>
```

- [ ] **Step 4: Map Studio output settings into the start payload**

In `studioLiveSessionPayload()`, replace the fixed false values with `collectLiveDefaults()` values:

```javascript
    presentation_enabled: liveDefaults.presentation_queue_enabled,
    tts_enabled: liveDefaults.tts_enabled,
    tts_provider: "gpt_sovits",
```

- [ ] **Step 5: Run the targeted tests and verify they pass**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_ui.py::test_studio_html_uses_external_assets_without_inline_code YouTubeBridge/tests/test_studio_ui.py::test_studio_p0_exposes_preflight_and_manual_source_session_flow -q
```

Expected: PASS.

- [ ] **Step 6: Commit the session-flag slice**

Run:

```powershell
git add YouTubeBridge/tests/test_studio_ui.py YouTubeBridge/static/studio.html YouTubeBridge/static/ui/studio.js
git commit -m "feat: enable studio tts session flags"
```

## Task 2: Expose Studio Presentation Audio Controls

**Files:**
- Modify: `YouTubeBridge/tests/test_studio_ui.py`
- Modify: `YouTubeBridge/static/studio.html`
- Modify: `YouTubeBridge/static/ui/studio.css`
- Test: `YouTubeBridge/tests/test_studio_ui.py`

- [ ] **Step 1: Add a fail-first source test for the toolbar controls**

Append this test near the existing Studio conversation tests:

```python
def test_studio_presentation_tts_controls_are_exposed():
    studio_html = _studio_source()
    studio_css = (Path(server_module.UI_ASSETS_ROOT) / "studio.css").read_text(encoding="utf-8")

    assert 'id="presentationAudioStatus"' in studio_html
    assert 'id="enablePresentationAudio"' in studio_html
    assert 'id="skipPresentation"' in studio_html
    assert "語音待機" in studio_html
    assert "啟用聲音" in studio_html
    assert "跳過目前句子" in studio_html
    assert ".conversation-tools .hidden" in studio_css
    assert ".conversation-tools button.small" in studio_css
```

- [ ] **Step 2: Run the new test and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_ui.py::test_studio_presentation_tts_controls_are_exposed -q
```

Expected: FAIL because the Studio conversation toolbar does not yet expose those controls.

- [ ] **Step 3: Add compact controls to the conversation toolbar**

In `YouTubeBridge/static/studio.html`, inside `<div class="conversation-tools">`, place the presentation controls after `durationBadge` and before `clearConversation`:

```html
            <span id="presentationAudioStatus" class="state-badge neutral">語音待機</span>
            <button id="enablePresentationAudio" class="secondary small hidden" type="button">啟用聲音</button>
            <button id="skipPresentation" class="secondary small" type="button" disabled>跳過目前句子</button>
```

- [ ] **Step 4: Add minimal toolbar styles**

In `YouTubeBridge/static/ui/studio.css`, add these rules near the `.conversation-tools` rules:

```css
.conversation-tools .hidden {
  display: none;
}

.conversation-tools button.small {
  min-height: 30px;
  padding: 6px 10px;
  font-size: 12px;
  line-height: 1;
}
```

- [ ] **Step 5: Run the new test and verify it passes**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_ui.py::test_studio_presentation_tts_controls_are_exposed -q
```

Expected: PASS.

- [ ] **Step 6: Commit the toolbar-control slice**

Run:

```powershell
git add YouTubeBridge/tests/test_studio_ui.py YouTubeBridge/static/studio.html YouTubeBridge/static/ui/studio.css
git commit -m "feat: add studio tts controls"
```

## Task 3: Add Studio Presentation Player Helpers

**Files:**
- Modify: `YouTubeBridge/tests/test_studio_ui.py`
- Modify: `YouTubeBridge/static/ui/studio.js`
- Test: `YouTubeBridge/tests/test_studio_ui.py`

- [ ] **Step 1: Add a fail-first source test for player state and helper functions**

Append this test near `test_studio_presentation_tts_controls_are_exposed()`:

```python
def test_studio_presentation_tts_player_helpers_are_wired():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    expected_state_fields = [
        "presentationQueue: []",
        "presentationPlaying: false",
        "currentPresentationItem: null",
        "currentAudio: null",
        "audioUnlockRequired: false",
        "presentationAckInFlight: false",
        "presentationAudioCache: new Map()",
    ]
    for field in expected_state_fields:
        assert field in studio_js

    expected_functions = [
        "function updatePresentationStatus(statusText = \"語音待機\", level = \"neutral\")",
        "function setPresentationControls(",
        "function presentationItemToMessage(item)",
        "function cachePresentationAudio(item)",
        "function audioForPresentationItem(item)",
        "function enqueuePresentationItem(item)",
        "function playPresentationItem()",
        "async function ackPresentationItem(item)",
        "async function finishPresentationItem(item, reason = \"ended\")",
        "async function skipCurrentPresentation()",
        "async function retryCurrentPresentationAudio()",
        "async function handlePresentationInterrupt(payload = {})",
        "function resetPresentationPlayer(",
    ]
    for fn in expected_functions:
        assert fn in studio_js

    assert "new Audio(audioUrl)" in studio_js
    assert 'audio.preload = "auto"' in studio_js
    assert 'api(`/sessions/${encodeURIComponent(state.sessionId)}/presentation/${encodeURIComponent(item.item_id)}/ack`, {' in studio_js
    assert 'api(`/sessions/${encodeURIComponent(state.sessionId)}/presentation/current/skip`, {' in studio_js
    assert 'appendChatPreviewMessage(presentationItemToMessage(item), { prepend: true })' in studio_js
```

- [ ] **Step 2: Run the new helper test and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_ui.py::test_studio_presentation_tts_player_helpers_are_wired -q
```

Expected: FAIL because `studio.js` does not yet contain the player state, audio cache, ACK helper, skip helper, or interrupt helper.

- [ ] **Step 3: Add presentation playback state**

In `YouTubeBridge/static/ui/studio.js`, extend the top-level `state` object after `freeTalkTopicSelectionInitialized`:

```javascript
  presentationQueue: [],
  presentationPlaying: false,
  currentPresentationItem: null,
  currentAudio: null,
  audioUnlockRequired: false,
  presentationAckInFlight: false,
  presentationAudioCache: new Map(),
```

- [ ] **Step 4: Add status, cache, and lifecycle helpers**

Insert this block after `renderConversationEmpty()` and before `summaryFromPayload()`:

```javascript
function updatePresentationStatus(statusText = "語音待機", level = "neutral") {
  const status = $("presentationAudioStatus");
  if (!status) return;
  status.textContent = statusText;
  status.className = `state-badge ${level}`;
}

function setPresentationControls({ audioUnlock = false, canSkip = false } = {}) {
  const enableButton = $("enablePresentationAudio");
  const skipButton = $("skipPresentation");
  if (enableButton) enableButton.classList.toggle("hidden", !audioUnlock);
  if (skipButton) skipButton.disabled = !canSkip;
}

function stopAudioElement(audio) {
  if (!audio) return;
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
}

function clearPresentationAudioCache() {
  state.presentationAudioCache.forEach((audio) => stopAudioElement(audio));
  state.presentationAudioCache.clear();
}

function stopCurrentPresentationAudio() {
  stopAudioElement(state.currentAudio);
  state.currentAudio = null;
}

function resetPresentationPlayer({ statusText = "語音待機" } = {}) {
  stopCurrentPresentationAudio();
  clearPresentationAudioCache();
  state.presentationQueue = [];
  state.presentationPlaying = false;
  state.currentPresentationItem = null;
  state.audioUnlockRequired = false;
  state.presentationAckInFlight = false;
  updatePresentationStatus(statusText, "neutral");
  setPresentationControls();
}
```

- [ ] **Step 5: Add item conversion, audio preload, and ACK helpers**

Place this block immediately after the helpers from Step 4:

```javascript
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

function cachePresentationAudio(item) {
  const itemId = String(item?.item_id || "");
  const audioUrl = String(item?.audio_url || "");
  if (!itemId || !audioUrl || state.presentationAudioCache.has(itemId)) return;
  const audio = new Audio(audioUrl);
  audio.preload = "auto";
  state.presentationAudioCache.set(itemId, audio);
  appendLog("DEBUG", `已預載 TTS 音訊：${itemId}`);
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

async function ackPresentationItem(item) {
  if (!item?.item_id || !state.sessionId || state.presentationAckInFlight) return false;
  state.presentationAckInFlight = true;
  try {
    await api(`/sessions/${encodeURIComponent(state.sessionId)}/presentation/${encodeURIComponent(item.item_id)}/ack`, {
      method: "POST",
    });
    appendLog("DEBUG", `TTS 播放完成並 ACK：${item.item_id}`);
    return true;
  } catch (error) {
    appendLog("WARN", `TTS ACK 失敗：${error.message || error}`);
    await refreshStudioSession();
    return false;
  } finally {
    state.presentationAckInFlight = false;
  }
}
```

- [ ] **Step 6: Add playback, skip, retry, and interrupt helpers**

Place this block immediately after Step 5:

```javascript
function isCurrentPresentationItem(item) {
  return Boolean(
    item?.item_id
    && state.currentPresentationItem?.item_id
    && item.item_id === state.currentPresentationItem.item_id
  );
}

async function finishPresentationItem(item, reason = "ended") {
  if (!isCurrentPresentationItem(item)) return;
  state.presentationPlaying = true;
  state.audioUnlockRequired = false;
  setPresentationControls({ canSkip: false });
  updatePresentationStatus(reason === "error" ? "語音錯誤，送出文字" : "送出 ACK", reason === "error" ? "warn" : "neutral");
  const acked = await ackPresentationItem(item);
  if (!isCurrentPresentationItem(item)) return;
  stopCurrentPresentationAudio();
  state.currentPresentationItem = null;
  state.presentationPlaying = false;
  if (acked) {
    updatePresentationStatus("語音待機", "neutral");
    playPresentationItem();
  } else {
    updatePresentationStatus("ACK 失敗", "warn");
    setPresentationControls();
  }
}

function playPresentationItem() {
  if (state.presentationPlaying || state.audioUnlockRequired || state.currentPresentationItem) return;
  const item = state.presentationQueue.shift();
  if (!item?.item_id) {
    updatePresentationStatus("語音待機", "neutral");
    setPresentationControls();
    return;
  }
  state.presentationPlaying = true;
  state.currentPresentationItem = item;
  state.audioUnlockRequired = false;
  feed.querySelector(".conversation-empty")?.remove();
  appendChatPreviewMessage(presentationItemToMessage(item), { prepend: true });
  updatePresentationStatus("播放中", "good");
  setPresentationControls({ canSkip: true });

  if (!item.audio_url) {
    appendLog("WARN", `TTS 音訊未產生，改以文字送出：${item.item_id}`);
    finishPresentationItem(item, "text_fallback").catch((error) => {
      appendLog("WARN", `文字 fallback ACK 失敗：${error.message || error}`);
    });
    return;
  }

  const audio = audioForPresentationItem(item);
  state.currentAudio = audio;
  audio.addEventListener("ended", () => {
    finishPresentationItem(item, "ended").catch((error) => {
      appendLog("WARN", `TTS 完播處理失敗：${error.message || error}`);
    });
  }, { once: true });
  audio.addEventListener("error", () => {
    finishPresentationItem(item, "error").catch((error) => {
      appendLog("WARN", `TTS 錯誤處理失敗：${error.message || error}`);
    });
  }, { once: true });
  audio.play().catch(() => {
    state.presentationPlaying = false;
    state.audioUnlockRequired = true;
    updatePresentationStatus("等待啟用聲音", "warn");
    setPresentationControls({ audioUnlock: true, canSkip: true });
  });
}

function enqueuePresentationItem(item) {
  if (!item?.item_id) return;
  cachePresentationAudio(item);
  state.presentationQueue.push(item);
  appendLog("DEBUG", `收到 TTS 句子：${item.item_id}`);
  playPresentationItem();
}

async function retryCurrentPresentationAudio() {
  if (!state.currentPresentationItem || !state.currentAudio) return;
  state.audioUnlockRequired = false;
  state.presentationPlaying = true;
  updatePresentationStatus("播放中", "good");
  setPresentationControls({ canSkip: true });
  try {
    await state.currentAudio.play();
  } catch {
    state.presentationPlaying = false;
    state.audioUnlockRequired = true;
    updatePresentationStatus("等待啟用聲音", "warn");
    setPresentationControls({ audioUnlock: true, canSkip: true });
  }
}

async function skipCurrentPresentation() {
  const hadCurrent = Boolean(state.currentPresentationItem);
  stopCurrentPresentationAudio();
  state.presentationPlaying = false;
  state.currentPresentationItem = null;
  state.audioUnlockRequired = false;
  updatePresentationStatus("跳過目前句子", "neutral");
  setPresentationControls();
  if (!hadCurrent || !state.sessionId) {
    playPresentationItem();
    return;
  }
  try {
    await api(`/sessions/${encodeURIComponent(state.sessionId)}/presentation/current/skip`, {
      method: "POST",
    });
    appendLog("INFO", "已跳過目前 TTS 句子");
    playPresentationItem();
  } catch (error) {
    appendLog("WARN", `跳過 TTS 句子失敗：${error.message || error}`);
    await refreshStudioSession();
  }
}

async function handlePresentationInterrupt(payload = {}) {
  const hadCurrent = Boolean(state.currentPresentationItem);
  stopCurrentPresentationAudio();
  clearPresentationAudioCache();
  state.presentationQueue = [];
  state.presentationPlaying = false;
  state.currentPresentationItem = null;
  state.audioUnlockRequired = false;
  updatePresentationStatus("直播互動打斷", "warn");
  setPresentationControls();
  appendLog("INFO", `TTS 播放已被打斷：${payload.reason || payload.closure_text || "interaction"}`);
  if (hadCurrent && state.sessionId) {
    try {
      await api(`/sessions/${encodeURIComponent(state.sessionId)}/presentation/current/skip`, {
        method: "POST",
      });
    } catch (error) {
      appendLog("WARN", `打斷後解除 TTS 等待失敗：${error.message || error}`);
      await refreshStudioSession();
    }
  }
  scheduleConversationRefresh("直播打斷");
}
```

- [ ] **Step 7: Run the helper test and verify it passes**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_ui.py::test_studio_presentation_tts_player_helpers_are_wired -q
```

Expected: PASS.

- [ ] **Step 8: Commit the player helper slice**

Run:

```powershell
git add YouTubeBridge/tests/test_studio_ui.py YouTubeBridge/static/ui/studio.js
git commit -m "feat: add studio presentation player"
```

## Task 4: Wire SSE Events, Controls, and Lifecycle

**Files:**
- Modify: `YouTubeBridge/tests/test_studio_ui.py`
- Modify: `YouTubeBridge/static/ui/studio.js`
- Test: `YouTubeBridge/tests/test_studio_ui.py`

- [ ] **Step 1: Add a fail-first source test for event routing and lifecycle cleanup**

Append this test near `test_studio_refresh_only_subscribes_running_session_events()`:

```python
def test_studio_presentation_tts_events_and_lifecycle_are_wired():
    studio_js = (Path(server_module.UI_ASSETS_ROOT) / "studio.js").read_text(encoding="utf-8")

    subscribe_index = studio_js.index("function subscribeSessionEvents(sessionId)")
    subscribe_body = studio_js[subscribe_index:studio_js.index("function sessionIsRunning", subscribe_index)]
    assert 'if (payload.type === "presentation_item_ready" && payload.item) {' in subscribe_body
    assert "enqueuePresentationItem(payload.item);" in subscribe_body
    assert 'if (payload.type === "interrupt_requested") {' in subscribe_body
    assert "handlePresentationInterrupt(payload)" in subscribe_body
    assert '["interaction_completed", "presentation_item_ready", "super_chat_batch_injected"]' not in subscribe_body
    assert '["interaction_completed", "super_chat_batch_injected"]' in subscribe_body

    reset_index = studio_js.index("function resetConversationForNewSession()")
    reset_body = studio_js[reset_index:studio_js.index("function chatPreviewKind", reset_index)]
    assert 'resetPresentationPlayer({ statusText: "建立新場次" });' in reset_body

    session_index = studio_js.index("function applySessionSnapshot(session)")
    session_body = studio_js[session_index:studio_js.index("function studioLiveSessionPayload", session_index)]
    assert "const wasLive = state.live;" in session_body
    assert 'resetPresentationPlayer({ statusText: "已停止" });' in session_body

    binding_index = studio_js.index('"clearConversation"')
    binding_body = studio_js[binding_index:studio_js.index('"regenerateSummary"', binding_index)]
    assert '$("enablePresentationAudio").addEventListener("click", () => {' in binding_body
    assert '$("skipPresentation").addEventListener("click", () => {' in binding_body
```

- [ ] **Step 2: Run the lifecycle test and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_ui.py::test_studio_presentation_tts_events_and_lifecycle_are_wired -q
```

Expected: FAIL because `subscribeSessionEvents()` still refreshes on `presentation_item_ready`, and player reset/button bindings are not yet wired.

- [ ] **Step 3: Route presentation and interrupt SSE events into the player**

In `subscribeSessionEvents(sessionId)`, replace the current combined `presentation_item_ready` refresh block with explicit branches:

```javascript
      if (payload.type === "presentation_item_ready" && payload.item) {
        enqueuePresentationItem(payload.item);
        return;
      }
      if (payload.type === "interrupt_requested") {
        handlePresentationInterrupt(payload).catch((error) => {
          appendLog("WARN", `直播打斷處理失敗：${error.message || error}`);
        });
        return;
      }
      if (payload.type === "interaction_interrupted") {
        scheduleConversationRefresh("直播打斷");
        return;
      }
      if (["interaction_completed", "super_chat_batch_injected"].includes(payload.type)) {
        refreshConversation();
      }
```

- [ ] **Step 4: Reset player state for new sessions**

In `resetConversationForNewSession()`, call the player reset before clearing the conversation:

```javascript
function resetConversationForNewSession() {
  unsubscribeSessionEvents();
  resetPresentationPlayer({ statusText: "建立新場次" });
  if (state.chatRefreshTimer) {
    clearTimeout(state.chatRefreshTimer);
    state.chatRefreshTimer = null;
  }
  state.currentSession = null;
  state.sessionId = "";
  state.messageCount = 0;
  renderConversationEmpty("正在建立新的 Live Session，等待後端產生 AI 對話。");
}
```

- [ ] **Step 5: Stop playback when the live session stops**

At the start of `applySessionSnapshot(session)`, track the previous live state:

```javascript
function applySessionSnapshot(session) {
  const wasLive = state.live;
  state.currentSession = session || null;
  state.sessionId = session?.session_id || "";
  state.detectedVideoId = session?.video_id || "";
  state.detectedLiveChatId = session?.live_chat_id || "";
  state.live = sessionIsRunning(session);
```

After `applyStartButtonState();`, add:

```javascript
  if (wasLive && !state.live) {
    resetPresentationPlayer({ statusText: "已停止" });
  }
```

- [ ] **Step 6: Bind the new toolbar buttons**

Near the existing `clearConversation` binding, add:

```javascript
  $("enablePresentationAudio").addEventListener("click", () => {
    retryCurrentPresentationAudio().catch((error) => {
      appendLog("WARN", `啟用 TTS 聲音失敗：${error.message || error}`);
    });
  });
  $("skipPresentation").addEventListener("click", () => {
    skipCurrentPresentation().catch((error) => {
      appendLog("WARN", `跳過 TTS 句子失敗：${error.message || error}`);
    });
  });
```

- [ ] **Step 7: Run the lifecycle test and verify it passes**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_ui.py::test_studio_presentation_tts_events_and_lifecycle_are_wired -q
```

Expected: PASS.

- [ ] **Step 8: Run the full Studio source test file**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_ui.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit the event/lifecycle slice**

Run:

```powershell
git add YouTubeBridge/tests/test_studio_ui.py YouTubeBridge/static/ui/studio.js
git commit -m "feat: wire studio tts playback events"
```

## Task 5: Run Presentation Queue Regressions and Manual Browser QA

**Files:**
- No source changes expected after Task 4
- Test: `YouTubeBridge/tests/test_studio_ui.py`
- Test: `YouTubeBridge/tests/test_presentation_queue.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_injection.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_director.py`

- [ ] **Step 1: Run targeted regression tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_ui.py YouTubeBridge/tests/test_presentation_queue.py YouTubeBridge/tests/test_bridge_engine_injection.py -q
```

Expected: PASS.

- [ ] **Step 2: Run director presentation prefetch regressions**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_director.py -q
```

Expected: PASS.

- [ ] **Step 3: Run the full YouTubeBridge suite**

Run:

```powershell
python -m pytest YouTubeBridge/tests -q
```

Expected: PASS.

If Windows pytest temp cleanup fails with ACL or permission errors, run the repo-approved cleanup script before retrying:

```powershell
scripts\cleanup_pytest_temp.bat
```

- [ ] **Step 4: Check formatting whitespace**

Run:

```powershell
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 5: Start YouTubeBridge in a visible foreground window for browser QA**

Use a visible CMD window, following repo rules:

```powershell
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "G:\ClaudeProject\MemoriaCore\YouTubeBridge\start.bat" -WorkingDirectory "G:\ClaudeProject\MemoriaCore\YouTubeBridge" -WindowStyle Normal
```

- [ ] **Step 6: Perform Studio browser QA**

Open `http://127.0.0.1:8091/studio/` and verify:

1. The conversation toolbar shows `語音待機`, `啟用聲音`, and `跳過目前句子`.
2. Starting a live session with Live Presentation Queue enabled sends `presentation_enabled: true` when the checkbox is checked.
3. Starting with GPT-SoVITS TTS enabled sends `tts_enabled: true` when the checkbox is checked.
4. A `presentation_item_ready` event appends exactly one visible Studio dialogue line for that item.
5. The current item is ACKed only after audio `ended`, audio `error`, or text fallback with no `audio_url`.
6. A second ready item remains queued while the current item plays, and its audio URL is preloaded through `presentationAudioCache`.
7. If browser autoplay rejects `audio.play()`, the item remains current, no ACK is sent, and `啟用聲音` resumes that same item.
8. `跳過目前句子` stops only the current item, calls `/presentation/current/skip`, and then allows the queued next item to play after the skip succeeds.
9. An `interrupt_requested` event stops the current audio, clears queued/cached audio, calls `/presentation/current/skip` only when a current item exists, and waits for new backend presentation items.
10. Opening `/live-chat/` is not required for Studio playback or ACK.

- [ ] **Step 7: Commit final QA edits only when QA changed source files**

If QA required source edits, run the targeted tests again and commit those exact files:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_ui.py YouTubeBridge/tests/test_presentation_queue.py YouTubeBridge/tests/test_bridge_engine_injection.py -q
git diff --check
git add YouTubeBridge/static/studio.html YouTubeBridge/static/ui/studio.css YouTubeBridge/static/ui/studio.js YouTubeBridge/tests/test_studio_ui.py
git commit -m "fix: polish studio tts playback qa"
```

If QA required no source edits, keep the branch as-is and report the browser QA result in the final handoff.

## Acceptance Criteria

- `studioLiveSessionPayload()` maps `presentation_enabled` and `tts_enabled` from Studio settings instead of hardcoding `false`.
- Studio receives `presentation_item_ready`, preloads any `audio_url`, displays the item, plays it, and ACKs only after playback completion, playback error, or text fallback.
- Studio does not play the next local queued item while the current item is playing, awaiting audio unlock, awaiting ACK, or blocked on a failed ACK.
- Studio clears current and queued playback on `interrupt_requested` and releases backend wait through the existing skip endpoint when a current item exists.
- Studio skip-current control affects only the current item and does not clear the entire local queue.
- `/live-chat/` remains untouched by this implementation.
- No new backend route or TTS storage schema is introduced.
- `python -m pytest YouTubeBridge/tests -q` passes.
- `git diff --check` passes.
