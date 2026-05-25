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
