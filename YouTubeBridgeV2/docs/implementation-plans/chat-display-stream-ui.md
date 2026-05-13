# Chat Display Stream UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 roadmap `6B`：讓 Chat Display 靜態頁成為可實際放進直播畫面的 display stream UI，能穩定接收 `/v2/sessions/{session_id}/display-stream`、呈現最新事件、處理中斷狀態，並在桌面與手機 viewport 不破版。

**Architecture:** 沿用 Wave 6A 的 display event contract，不再直接解 raw event-history shape。`chat-display.js` 保留 event model classes，新增薄的 stream view shell：event list、stream status、bounded history 與 auto-scroll。Server/query contract 不再擴張；6B 只改 Chat Display UI 與 browser smoke 驗證，不進入 presentation metadata integration 或 TTS queue。

**Tech Stack:** Plain ESM JavaScript、CSS、shared `MCI18N` i18n、pytest + Node unit tests、opt-in Playwright browser smoke、FastAPI live `/v2` server。

---

## Scope

Roadmap item：`6B：chat display stream UI`

完成條件：

- Chat Display root 保持穩定 shell，不因每次 render 全頁無結構替換而難以做 browser QA。
- display events 顯示在 `data-testid="display-event-list"` 中，最新內容靠近底部，適合 OBS/browser source。
- `mountChatDisplay(...)` 支援 `maxEvents`，避免長時間直播讓 DOM 無限制增長。
- SSE disconnect/stale 狀態最多顯示一個狀態 banner，不重複堆疊。
- Super Chat、audience message、character response、system/closing 狀態仍沿用既有 renderer。
- 新增 opt-in browser smoke，預設 skip；啟用時用 live 8088 `/v2` server 驗證 desktop/mobile render、no overflow、no private payload。
- 不修改 roadmap checkbox，不碰 presentation/TTS queue/ack/timeout。

## File Structure

- Modify: `YouTubeBridgeV2/static/chat-display/chat-display.js`
  - 新增 stream shell rendering、bounded event list、single stale banner、auto-scroll helper。
- Modify: `YouTubeBridgeV2/static/chat-display/chat-display.css`
  - 新增 full-height stream layout、bottom anchoring、bounded list styling、mobile constraints。
- Modify: `tests/youtubebridge_v2/test_chat_display_ui.py`
  - 增加 shell/maxEvents/stale de-dup/CSS assertions。
- Create: `tests/youtubebridge_v2/test_chat_display_browser_smoke.py`
  - opt-in live browser smoke，和 5E operator smoke 一樣預設 skip。
- Modify docs:
  - `YouTubeBridgeV2/docs/modules/chat-display-ui.md`
  - `YouTubeBridgeV2/docs/architecture-index.md`
  - `YouTubeBridgeV2/docs/api-reference-index.md`

---

### Task 1: Red Tests For Stream UI Shell

**Files:**
- Modify: `tests/youtubebridge_v2/test_chat_display_ui.py`

- [ ] **Step 1: Add shell + bounded list test**

Add this test after `test_display_permission_does_not_call_control_api`:

```python
def test_mount_chat_display_uses_stable_stream_shell_and_bounded_event_list():
    result = _run_node_json(
        """
const root = {innerHTML: "", dataset: {}};
const sources = [];
class FakeSource {
  constructor(url) {
    this.url = url;
    sources.push(this);
  }
}
ui.mountChatDisplay({
  root,
  sessionId: "session-shell",
  eventSourceFactory: (url) => new FakeSource(url),
  initialEvents: [
    {event_type: "audience_message", sequence: 1, public_payload: {author_display_name: "A", message_text: "first"}},
    {event_type: "audience_message", sequence: 2, public_payload: {author_display_name: "B", message_text: "second"}}
  ],
  maxEvents: 2
});
sources[0].onmessage({
  data: JSON.stringify({
    event_type: "audience_message",
    sequence: 3,
    public_payload: {author_display_name: "C", message_text: "third"}
  })
});
console.log(JSON.stringify({html: root.innerHTML, sourceUrl: sources[0].url}));
"""
    )

    assert 'data-testid="chat-display-shell"' in result["html"]
    assert 'data-testid="display-event-list"' in result["html"]
    assert result["sourceUrl"] == "/v2/sessions/session-shell/display-stream"
    assert "first" not in result["html"]
    assert "second" in result["html"]
    assert "third" in result["html"]
```

- [ ] **Step 2: Add stale de-dup test**

Add:

```python
def test_mount_chat_display_shows_single_stale_banner():
    result = _run_node_json(
        """
const root = {innerHTML: "", dataset: {}};
const sources = [];
class FakeSource {
  constructor(url) {
    sources.push(this);
  }
}
ui.mountChatDisplay({
  root,
  sessionId: "session-stale",
  eventSourceFactory: (url) => new FakeSource(url),
  initialEvents: [
    {event_type: "audience_message", public_payload: {author_display_name: "A", message_text: "visible"}}
  ]
});
sources[0].onerror(new Error("disconnect one"));
sources[0].onerror(new Error("disconnect two"));
console.log(JSON.stringify({html: root.innerHTML}));
"""
    )

    assert result["html"].count('data-testid="status-banner"') == 1
    assert "visible" in result["html"]
    assert "Display stream is stale" in result["html"]
```

- [ ] **Step 3: Add CSS layout assertions**

Add:

```python
def test_chat_display_css_stream_layout_is_bottom_anchored_without_viewport_font_scaling():
    css = UI_CSS.read_text(encoding="utf-8")

    assert ".display-event-list" in css
    assert "justify-content: flex-end" in css
    assert "overflow-anchor: auto" in css
    assert "font-size: clamp(" not in css
    assert re.search(r"font-size\\s*:\\s*[^;]*vw", css) is None
```

- [ ] **Step 4: Run red tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_chat_display_ui.py::test_mount_chat_display_uses_stable_stream_shell_and_bounded_event_list tests\youtubebridge_v2\test_chat_display_ui.py::test_mount_chat_display_shows_single_stale_banner tests\youtubebridge_v2\test_chat_display_ui.py::test_chat_display_css_stream_layout_is_bottom_anchored_without_viewport_font_scaling -q
```

Expected before implementation: first two tests fail because `mountChatDisplay` renders rows directly and has no `maxEvents`/shell; CSS test fails because `.display-event-list` and bottom anchoring do not exist.

---

### Task 2: Implement Stream Shell And Bounded Rendering

**Files:**
- Modify: `YouTubeBridgeV2/static/chat-display/chat-display.js`
- Modify: `YouTubeBridgeV2/static/chat-display/chat-display.css`

- [ ] **Step 1: Add shell renderer and event trimming helpers**

In `chat-display.js`, after `renderDisplayEvents(...)`, add:

```javascript
export function renderChatDisplayShell({events = [], streamStatus = null} = {}) {
  const statusHtml = streamStatus ? DisplaySystemStateEvent.fromEvent({
    event_type: "system_state",
    public_payload: streamStatus,
  }).render() : "";
  return `
    <section class="chat-display-shell" data-testid="chat-display-shell">
      <div class="display-event-list" data-testid="display-event-list" aria-live="polite">
        ${renderDisplayEvents(events)}
      </div>
      ${statusHtml ? `<div class="stream-status" data-testid="stream-status">${statusHtml}</div>` : ""}
    </section>
  `;
}

function trimDisplayEvents(events, maxEvents) {
  const limit = toFiniteNumber(maxEvents);
  if (limit === null || limit <= 0 || events.length <= limit) return events;
  return events.slice(events.length - limit);
}
```

- [ ] **Step 2: Update `mountChatDisplay(...)` signature and render flow**

Change the signature:

```javascript
export function mountChatDisplay({
  root,
  sessionId,
  eventSourceFactory = defaultEventSourceFactory,
  initialEvents = [],
  maxEvents = 80,
} = {}) {
```

Inside `mountChatDisplay`, replace the direct `target.innerHTML = renderDisplayEvents(events);` render function with:

```javascript
  let streamStatus = null;

  const render = () => {
    target.innerHTML = renderChatDisplayShell({events, streamStatus});
    scrollDisplayToLatest(target);
  };
```

When loading initial events and appending stream events, trim:

```javascript
  for (const event of initialEvents) {
    events.push(normalizeDisplayEvent(event));
  }
  events.splice(0, events.length, ...trimDisplayEvents(events, maxEvents));
```

In `onEvent`, replace:

```javascript
      events.push(event);
      render();
```

with:

```javascript
      streamStatus = null;
      events.push(event);
      events.splice(0, events.length, ...trimDisplayEvents(events, maxEvents));
      render();
```

In `onStale`, replace pushing a synthetic event with a single status object:

```javascript
      streamStatus = {
        phase: "unknown",
        message: state.message,
      };
      render();
```

- [ ] **Step 3: Add scroll helper**

Near other private helpers, add:

```javascript
function scrollDisplayToLatest(target) {
  if (typeof target?.querySelector !== "function") return;
  const list = target.querySelector("[data-testid='display-event-list']");
  if (list && typeof list.scrollTo === "function") {
    list.scrollTo({top: list.scrollHeight});
  } else if (list) {
    list.scrollTop = list.scrollHeight;
  }
}
```

- [ ] **Step 4: Add CSS shell layout**

In `chat-display.css`, update `.chat-display-root`:

```css
.chat-display-root {
  align-items: stretch;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  min-height: 100vh;
  padding: 18px;
}
```

Add:

```css
.chat-display-shell {
  align-items: stretch;
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: calc(100vh - 36px);
  min-height: 0;
}

.display-event-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  justify-content: flex-end;
  min-height: 0;
  overflow-anchor: auto;
  overflow-y: auto;
}

.stream-status {
  flex: 0 0 auto;
}
```

In the mobile media query, set:

```css
  .chat-display-shell {
    max-height: calc(100vh - 20px);
  }
```

- [ ] **Step 5: Run red tests again**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_chat_display_ui.py::test_mount_chat_display_uses_stable_stream_shell_and_bounded_event_list tests\youtubebridge_v2\test_chat_display_ui.py::test_mount_chat_display_shows_single_stale_banner tests\youtubebridge_v2\test_chat_display_ui.py::test_chat_display_css_stream_layout_is_bottom_anchored_without_viewport_font_scaling -q
```

Expected after implementation: all three pass.

---

### Task 3: Add Opt-In Browser Smoke

**Files:**
- Create: `tests/youtubebridge_v2/test_chat_display_browser_smoke.py`

- [ ] **Step 1: Create browser smoke test file**

Create `tests/youtubebridge_v2/test_chat_display_browser_smoke.py`:

```python
from __future__ import annotations

import json
import os
import subprocess
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _post_json(base_url: str, path: str, payload: dict[str, object]) -> None:
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        assert 200 <= response.status < 300


@pytest.mark.skipif(
    os.environ.get("YOUTUBEBRIDGE_V2_BROWSER_SMOKE") != "1",
    reason="set YOUTUBEBRIDGE_V2_BROWSER_SMOKE=1 to run live browser smoke",
)
def test_chat_display_browser_smoke_renders_live_display_stream(tmp_path):
    base_url = os.environ.get("YOUTUBEBRIDGE_V2_BROWSER_BASE_URL", "http://127.0.0.1:8088")
    chrome = os.environ.get("PLAYWRIGHT_CHROME_EXECUTABLE")
    if not chrome:
        pytest.skip("PLAYWRIGHT_CHROME_EXECUTABLE is required")
    node = os.environ.get("NODE_EXE", "node")
    session_id = f"codex-6b-display-{int(time.time() * 1000)}"

    try:
        _post_json(base_url, "/v2/sessions", {
            "command_id": f"{session_id}-create",
            "session_id": session_id,
            "aftertalk_policy": "auto",
        })
        _post_json(base_url, f"/v2/sessions/{session_id}/youtube-events", {
            "command_id": f"{session_id}-super-chat",
            "youtube_event": {
                "id": f"{session_id}-sc-1",
                "snippet": {
                    "type": "superChatEvent",
                    "publishedAt": "2026-05-12T08:20:00Z",
                    "displayMessage": "Great stream from browser smoke",
                    "superChatDetails": {
                        "amountMicros": 150000000,
                        "currency": "TWD",
                        "amountDisplayString": "NT$150",
                        "userComment": "Great stream from browser smoke",
                        "tier": 3,
                    },
                },
                "authorDetails": {
                    "displayName": "Rin",
                    "channelId": "channel-rin",
                    "isChatSponsor": True,
                },
                "raw_youtube_payload": {
                    "access_token": "must not leak",
                    "authorization": "Bearer secret-value",
                },
            },
        })
    except urllib.error.URLError as exc:
        pytest.skip(f"live V2 server is not available: {exc}")

    script = tmp_path / "chat_display_smoke.cjs"
    script.write_text(
        textwrap.dedent(
            """
            const { chromium } = require("playwright");
            const [baseUrl, sessionId, chromePath] = process.argv.slice(2);

            (async () => {
              const browser = await chromium.launch({executablePath: chromePath, headless: true});
              const badResponses = [];
              const consoleErrors = [];
              async function checkViewport(width, height) {
                const page = await browser.newPage({viewport: {width, height}});
                page.on("console", (msg) => {
                  if (msg.type() === "error") consoleErrors.push(msg.text());
                });
                page.on("response", (response) => {
                  const url = response.url();
                  if (response.status() >= 400 && !url.endsWith("/favicon.ico")) {
                    badResponses.push(`${response.status()} ${url}`);
                  }
                });
                await page.goto(`${baseUrl}/v2/static/chat-display/index.html?session_id=${encodeURIComponent(sessionId)}`, {waitUntil: "domcontentloaded"});
                await page.waitForSelector("[data-testid='chat-display-shell']", {timeout: 10000});
                await page.waitForSelector("[data-testid='super-chat']", {timeout: 10000});
                const result = await page.evaluate(() => ({
                  text: document.body.innerText,
                  html: document.querySelector("#chatDisplayRoot")?.innerHTML || "",
                  overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
                  listExists: Boolean(document.querySelector("[data-testid='display-event-list']")),
                }));
                await page.close();
                if (!result.listExists) throw new Error(`missing event list at ${width}`);
                if (!result.text.includes("NT$150")) throw new Error(`missing amount at ${width}`);
                if (!result.text.includes("Great stream from browser smoke")) throw new Error(`missing message at ${width}`);
                if (!result.text.includes("Member") && !result.text.includes("會員")) throw new Error(`missing member flag at ${width}`);
                if (result.html.toLowerCase().includes("access_token") || result.html.toLowerCase().includes("secret-value")) {
                  throw new Error(`private payload leaked at ${width}`);
                }
                if (result.overflow) throw new Error(`horizontal overflow at ${width}`);
                return {width, ok: true};
              }
              const desktop = await checkViewport(1280, 720);
              const mobile = await checkViewport(390, 720);
              await browser.close();
              const relevantConsoleErrors = consoleErrors.filter((text) => !text.includes("404 (Not Found)"));
              if (badResponses.length || relevantConsoleErrors.length) {
                throw new Error(JSON.stringify({badResponses, consoleErrors: relevantConsoleErrors}));
              }
              console.log(JSON.stringify({desktop, mobile, ignoredConsoleErrors: consoleErrors.length - relevantConsoleErrors.length}));
            })().catch((error) => {
              console.error(error && error.stack || error);
              process.exit(1);
            });
            """
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    result = subprocess.run(
        [node, str(script), base_url, session_id, chrome],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["desktop"]["ok"] is True
    assert payload["mobile"]["ok"] is True
```

- [ ] **Step 2: Run default skip**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_chat_display_browser_smoke.py -q
```

Expected: `1 skipped`.

- [ ] **Step 3: Run opt-in smoke against local 8088**

With 8088 running in a visible foreground window, run:

```powershell
$env:YOUTUBEBRIDGE_V2_BROWSER_SMOKE='1'
$env:YOUTUBEBRIDGE_V2_BROWSER_BASE_URL='http://127.0.0.1:8088'
$env:PLAYWRIGHT_CHROME_EXECUTABLE='C:\Program Files\Google\Chrome\Application\chrome.exe'
$env:NODE_EXE='C:\Users\USER\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
$env:NODE_PATH='C:\Users\USER\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
python -m pytest tests\youtubebridge_v2\test_chat_display_browser_smoke.py -q
```

Expected: `1 passed`.

---

### Task 4: Docs And Final Verification

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/chat-display-ui.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] **Step 1: Update docs**

In `docs/modules/chat-display-ui.md`, add under Test Strategy:

```markdown
- Wave 6B browser smoke：opt-in `tests/youtubebridge_v2/test_chat_display_browser_smoke.py` 會使用 live 8088 `/v2` server 驗證 desktop/mobile stream render、bounded shell、no private payload 與 no horizontal overflow。
```

In `docs/architecture-index.md`, add an Integration Wave 6B status section:

```markdown
## Integration Wave 6B 狀態

- [x] Stream UI shell：Chat Display root 使用穩定 shell 與 `display-event-list`，適合 browser source/OBS 顯示。
- [x] Bounded stream：`mountChatDisplay(...)` 支援 `maxEvents`，避免長時間直播 DOM 無限制增長。
- [x] Browser smoke：新增 opt-in Chat Display browser smoke，預設 skip，不讓一般 V2 suite 依賴本機 Chrome。
- [x] Scope boundary：本階段不處理 presentation metadata integration 或 TTS queue/ack/timeout。
```

In `docs/api-reference-index.md`, update Chat Display UI concepts/sources to mention `renderChatDisplayShell` and the browser smoke file.

- [ ] **Step 2: Run focused verification**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_chat_display_ui.py tests\youtubebridge_v2\test_chat_display_browser_smoke.py -q
```

Expected: Chat Display tests pass and browser smoke skips by default.

- [ ] **Step 3: Run roadmap-required verification**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_chat_display_ui.py -q
python -m pytest tests\youtubebridge_v2\test_presentation_tts.py -q
python -m pytest tests\youtubebridge_v2 -q
git diff --check
```

Expected: full V2 suite passes; browser smoke remains skipped by default; `git diff --check` exits 0.

---

## Self-Review

- Spec coverage：本 plan 只處理 6B Chat Display stream UI，6C/6D/6E 的 presentation/TTS integration 不進入範圍。
- TDD coverage：新增 shell/maxEvents/stale/CSS tests 先 fail，再改 JS/CSS；browser smoke 先 default skip，再 opt-in live 8088 驗證。
- UI boundary：Chat Display 仍只呼叫 display stream，不呼叫 operator/manual close/aftertalk/tick APIs。
- Responsive risk：CSS 使用固定/rem/px 與 media query，不使用 viewport-width font scaling；browser smoke 覆蓋 1280 與 390 viewport。
