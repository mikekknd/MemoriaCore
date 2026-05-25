# Operator Console Browser UI Regression Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 roadmap `5E`：建立可重跑的 Operator Console browser/UI regression verification，證明 Wave 5 controls 在真瀏覽器、桌面與手機 viewport 下能操作 durable `/v2` API。

**Architecture:** 新增一個 opt-in pytest browser smoke harness，預設 skip，避免一般 `tests/youtubebridge_v2` 依賴本機 Chrome 或 Playwright browser binary。啟用時它連到已啟動的 8088 `/v2` server，建立 disposable session，在真瀏覽器中載入 `/v2/static/operator-console/index.html`，驗證 controls、API key create/delete、raw key 不進 DOM、桌面與 390px mobile 無水平 overflow。文件記錄 Browser plugin QA 路徑與 Playwright fallback/harness 參數。

**Tech Stack:** pytest opt-in test、Node.js CommonJS Playwright harness、system Chrome executable、FastAPI live server `/v2` API、plain Operator Console UI。

---

## Scope

Roadmap item：`5E：browser/UI regression verification`

完成條件：

- 新增可重跑 browser smoke test，檔名清楚標示是 opt-in。
- 預設 `python -m pytest tests\youtubebridge_v2 -q` 不要求本機瀏覽器，測試 skip。
- 設定 `YOUTUBEBRIDGE_V2_BROWSER_SMOKE=1` 時，test 會要求已啟動的 8088 server、Node、Playwright package 與 Chrome executable。
- Browser smoke 必須驗證：
  - Operator Console status page 可載入並顯示 operator controls。
  - API key panel 可新增 disposable display key。
  - Raw key 不出現在 visible text 或 DOM HTML。
  - Delete by fingerprint 後 prefix 從 UI 消失。
  - Desktop 1280px 與 mobile 390px viewport 都無水平 overflow。
  - Relevant console errors 與 non-favicon HTTP 4xx/5xx 為空。
- 更新 docs/API reference/architecture，標明 5E 已建立 browser regression harness。
- 不修改 roadmap checkbox，不進入 Wave 6 chat display/TTS。

## File Structure

- Create: `tests/youtubebridge_v2/test_operator_console_browser_smoke.py`
  - Opt-in live browser regression smoke test。
  - 使用 Python 先建立 disposable session，再用 Node Playwright 操作瀏覽器。
- Modify: `YouTubeBridgeV2/docs/modules/operator-console-ui.md`
  - 記錄 5E browser regression harness 與啟用環境變數。
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
  - 新增 Integration Wave 5E 狀態。
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`
  - Operator Console UI entry 補上 5E regression harness reference。

---

### Task 1: Red Opt-In Browser Smoke Test

**Files:**
- Create: `tests/youtubebridge_v2/test_operator_console_browser_smoke.py`

- [ ] **Step 1: Add opt-in skip guard and live API helper**

Create the file with:

```python
from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
import urllib.error
import urllib.request
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE_URL = "http://127.0.0.1:8088"


def _require_browser_smoke_enabled() -> None:
    if os.environ.get("YOUTUBEBRIDGE_V2_BROWSER_SMOKE") != "1":
        pytest.skip("set YOUTUBEBRIDGE_V2_BROWSER_SMOKE=1 to run live browser smoke")


def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        pytest.fail(f"live V2 server is not reachable: {exc}")
```

- [ ] **Step 2: Add Node Playwright script builder**

Add:

```python
def _browser_smoke_script() -> str:
    return r"""
const {chromium} = require("playwright");

(async () => {
const baseUrl = process.env.YOUTUBEBRIDGE_V2_BROWSER_BASE_URL || "http://127.0.0.1:8088";
const sessionId = process.env.YOUTUBEBRIDGE_V2_BROWSER_SESSION_ID;
const chromeExecutable = process.env.PLAYWRIGHT_CHROME_EXECUTABLE;
if (!sessionId) throw new Error("missing YOUTUBEBRIDGE_V2_BROWSER_SESSION_ID");
if (!chromeExecutable) throw new Error("missing PLAYWRIGHT_CHROME_EXECUTABLE");

const smokeKey = `codex-5e-display-${Date.now()}`;
const relevantConsoleErrors = [];
const badResponses = [];
const browser = await chromium.launch({headless: true, executablePath: chromeExecutable});

async function recordPage(page) {
  page.on("console", (msg) => {
    if (msg.type() !== "error") return;
    const text = msg.text();
    if (text.includes("Failed to load resource: the server responded with a status of 404")) return;
    relevantConsoleErrors.push(text);
  });
  page.on("response", (response) => {
    const url = response.url();
    const status = response.status();
    if (status >= 400 && !url.endsWith("/favicon.ico")) {
      badResponses.push({status, url});
    }
  });
}

try {
  const desktop = await browser.newPage({viewport: {width: 1280, height: 900}});
  await recordPage(desktop);
  await desktop.goto(`${baseUrl}/v2/static/operator-console/index.html?session_id=${encodeURIComponent(sessionId)}`, {waitUntil: "networkidle"});
  await desktop.waitForSelector("[data-testid='operator-controls']", {timeout: 10000});
  await desktop.waitForSelector("[data-testid='api-key-panel']", {timeout: 10000});
  await desktop.fill("[data-testid='api-key-input']", smokeKey);
  await desktop.selectOption("[data-testid='api-key-permission-select']", "display");
  await desktop.click("[data-testid='api-key-create-button']");
  await desktop.waitForFunction(() => document.querySelector("[data-testid='api-key-list']")?.textContent?.includes("display"), {timeout: 10000});
  const bodyAfterCreate = await desktop.locator("body").innerText();
  const htmlAfterCreate = await desktop.locator("body").evaluate((element) => element.innerHTML);
  const rawVisible = bodyAfterCreate.includes(smokeKey) || htmlAfterCreate.includes(smokeKey);
  const deleteButtons = desktop.locator("[data-testid='api-key-delete-button']");
  const deleteCount = await deleteButtons.count();
  if (deleteCount < 1) throw new Error("missing api key delete button");
  const deleteButton = deleteButtons.nth(deleteCount - 1);
  const fingerprint = await deleteButton.getAttribute("data-key-fingerprint");
  if (!fingerprint) throw new Error("missing api key fingerprint");
  const prefix = fingerprint.slice(0, 12);
  await deleteButton.click();
  await desktop.waitForFunction((value) => !document.body.innerText.includes(value), prefix, {timeout: 10000});
  const desktopOverflow = await desktop.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);

  const mobile = await browser.newPage({viewport: {width: 390, height: 844}});
  await recordPage(mobile);
  await mobile.goto(`${baseUrl}/v2/static/operator-console/index.html?session_id=${encodeURIComponent(sessionId)}`, {waitUntil: "networkidle"});
  await mobile.waitForSelector("[data-testid='api-key-panel']", {timeout: 10000});
  const mobileMetrics = await mobile.evaluate(() => ({
    viewportWidth: window.innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
    horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth,
    apiKeyPanelVisible: Boolean(document.querySelector("[data-testid='api-key-panel']")),
  }));

  console.log(JSON.stringify({
    rawVisible,
    fingerprintPresent: Boolean(fingerprint),
    desktopOverflow,
    mobileMetrics,
    relevantConsoleErrors,
    badResponses,
  }));
} finally {
  await browser.close();
}
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
"""
```

- [ ] **Step 3: Add pytest wrapper**

Add:

```python
def test_operator_console_browser_regression_smoke(tmp_path):
    _require_browser_smoke_enabled()
    base_url = os.environ.get("YOUTUBEBRIDGE_V2_BROWSER_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    node = os.environ.get("NODE_EXE") or shutil.which("node")
    if not node:
        pytest.fail("node executable is required for browser smoke")
    chrome = os.environ.get("PLAYWRIGHT_CHROME_EXECUTABLE")
    if not chrome:
        pytest.fail("PLAYWRIGHT_CHROME_EXECUTABLE must point to Chrome or Edge")

    session_id = f"codex-5e-smoke-{os.getpid()}"
    created = _post_json(
        f"{base_url}/v2/sessions",
        {
            "command_id": f"cmd-create-{session_id}",
            "session_id": session_id,
            "aftertalk_policy": "auto",
        },
    )
    assert created["session_id"] == session_id

    script_path = tmp_path / "operator_console_browser_smoke.cjs"
    script_path.write_text(textwrap.dedent(_browser_smoke_script()), encoding="utf-8")
    env = os.environ.copy()
    env["YOUTUBEBRIDGE_V2_BROWSER_BASE_URL"] = base_url
    env["YOUTUBEBRIDGE_V2_BROWSER_SESSION_ID"] = session_id
    result = subprocess.run(
        [node, str(script_path)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["rawVisible"] is False
    assert payload["fingerprintPresent"] is True
    assert payload["desktopOverflow"] is False
    assert payload["mobileMetrics"]["apiKeyPanelVisible"] is True
    assert payload["mobileMetrics"]["horizontalOverflow"] is False
    assert payload["relevantConsoleErrors"] == []
    assert payload["badResponses"] == []
```

- [ ] **Step 4: Run default red/skip check**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_operator_console_browser_smoke.py -q
```

Expected: SKIP because `YOUTUBEBRIDGE_V2_BROWSER_SMOKE` is not set.

- [ ] **Step 5: Run opt-in red check**

With 8088 running and Chrome path configured, run:

```powershell
$env:YOUTUBEBRIDGE_V2_BROWSER_SMOKE="1"
$env:YOUTUBEBRIDGE_V2_BROWSER_BASE_URL="http://127.0.0.1:8088"
$env:PLAYWRIGHT_CHROME_EXECUTABLE="C:\Program Files\Google\Chrome\Application\chrome.exe"
$env:NODE_PATH="C:\Users\USER\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules"
python -m pytest tests\youtubebridge_v2\test_operator_console_browser_smoke.py -q
```

Expected before implementation: FAIL because the test file does not exist yet. After Step 3 it should PASS if the current UI is correct.

---

### Task 2: Docs Sync For Browser Regression Harness

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/operator-console-ui.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] **Step 1: Update operator console module test strategy**

Add:

```markdown
- Browser regression smoke：`YOUTUBEBRIDGE_V2_BROWSER_SMOKE=1` 啟用 `tests/youtubebridge_v2/test_operator_console_browser_smoke.py`，針對 live 8088 server 驗證 desktop/mobile、API key create/delete、no raw key DOM 與 console/network health。
```

- [ ] **Step 2: Add Wave 5E architecture status**

After Wave 5D status:

```markdown
## Integration Wave 5E 狀態

- [x] Browser regression harness：新增 opt-in pytest smoke，預設 skip，不讓一般 V2 suite 依賴本機瀏覽器。
- [x] UI coverage：harness 驗證 Operator Console desktop/mobile、operator controls、API key create/delete、no raw key DOM、console/network health。
- [x] Scope boundary：本階段只驗證 Operator Console，不進入 Wave 6 Chat Display / Presentation / TTS。
```

- [ ] **Step 3: Update API reference Operator Console UI section**

Add Wave 5E line:

```markdown
Wave 5E：新增 opt-in browser regression harness `tests/youtubebridge_v2/test_operator_console_browser_smoke.py`，用 live `/v2` server 驗證 operator controls 與 responsive layout。
```

- [ ] **Step 4: Run docs and focused UI tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_operator_console_ui.py tests\youtubebridge_v2\test_operator_console_browser_smoke.py -q
```

Expected: operator console UI tests PASS；browser smoke SKIP unless env enabled.

---

### Task 3: Verification And Commit

**Files:**
- All files touched by Tasks 1-2.

- [ ] **Step 1: Run opt-in browser smoke**

Use the command from Task 1 Step 5 with 8088 running in a visible foreground window.

Expected: PASS.

- [ ] **Step 2: Run full V2 verification**

Run:

```powershell
python -m pytest tests\youtubebridge_v2 -q
git diff --check
git status --short --branch
```

Expected: full V2 suite passes with existing skips plus the browser-smoke default skip; diff check exits 0; status shows only 5E files.

- [ ] **Step 3: Stage and commit**

Run:

```powershell
git add tests\youtubebridge_v2\test_operator_console_browser_smoke.py YouTubeBridgeV2\docs\modules\operator-console-ui.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\implementation-plans\operator-console-browser-ui-regression-verification.md
git commit -m "test: add operator console browser smoke"
```

Expected: commit succeeds. Do not modify roadmap checkboxes.

---

## Self-Review

- Spec coverage: 5E requires browser/UI regression verification. Plan adds a repeatable opt-in browser harness and runs it live, while leaving normal tests browser-independent.
- Placeholder scan: no TBD/TODO/fill-later text remains.
- Type consistency: env vars and file names match across test, docs, commands, and verification steps.
