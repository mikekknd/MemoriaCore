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
    base_url = os.environ.get(
        "YOUTUBEBRIDGE_V2_BROWSER_BASE_URL",
        "http://127.0.0.1:8088",
    )
    chrome = os.environ.get("PLAYWRIGHT_CHROME_EXECUTABLE")
    if not chrome:
        pytest.skip("PLAYWRIGHT_CHROME_EXECUTABLE is required")
    node = os.environ.get("NODE_EXE", "node")
    session_id = f"codex-6b-display-{int(time.time() * 1000)}"

    try:
        _post_json(
            base_url,
            "/v2/sessions",
            {
                "command_id": f"{session_id}-create",
                "session_id": session_id,
                "aftertalk_policy": "auto",
            },
        )
        _post_json(
            base_url,
            f"/v2/sessions/{session_id}/youtube-events",
            {
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
            },
        )
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

    result = subprocess.run(
        [node, str(script), base_url, session_id, chrome],
        cwd=ROOT,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["desktop"]["ok"] is True
    assert payload["mobile"]["ok"] is True
