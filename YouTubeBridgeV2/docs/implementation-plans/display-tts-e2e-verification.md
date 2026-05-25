# Display TTS E2E Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Wave 6E end-to-end verification that a Memoria planned response reaches Chat Display rendering and the provider-neutral TTS delivery queue/ack/timeout API without changing runtime phase.

**Architecture:** Wave 6E is a verification wave, not a new runtime feature. Add an always-on Python/Node integration test that drives real V2 storage, a fake Memoria transport, the runtime tick path, display stream normalization, chat-display renderer, TTS queue read, ack, and timeout. Add an opt-in browser smoke that starts a temporary standalone V2 app on an ephemeral port, seeds the same flow, opens the real chat-display static page in Playwright, and verifies rendered presentation metadata plus queue/ack/timeout through browser-origin API calls.

**Tech Stack:** pytest, FastAPI TestClient, real `StorageManager` with temporary V2 DB, existing `MemoriaPlannedShowRunner`, Node ESM renderer check, opt-in Playwright browser smoke, existing V2 docs.

---

## Scope Boundary

- Implement only roadmap item 6E: `display + TTS E2E verification`.
- Do not implement a real TTS provider, browser audio playback, provider retry, WebSocket callback, or OBS integration.
- Do not change runtime phase decisions from display, queue, ack, or timeout state.
- Do not edit `YouTubeBridgeV2/docs/roadmap.md` checkbox state.
- Do not import legacy `YouTubeBridge/` modules.
- Do not add SQLite access inside `YouTubeBridgeV2/`; tests use `StorageManager` with temporary DB paths.
- Browser smoke remains opt-in and skipped unless explicitly enabled through environment variables.

## File Structure

- Create `tests/youtubebridge_v2/test_display_tts_e2e.py`
  - Owns the always-on real-storage display + TTS round-trip acceptance test.
  - Owns a skipped-by-default browser smoke that starts a temporary standalone V2 app and verifies the real static chat display page.
- Modify `YouTubeBridgeV2/docs/modules/chat-display-ui.md`
  - Record Wave 6E display/TTS E2E verification coverage.
- Modify `YouTubeBridgeV2/docs/modules/presentation-tts.md`
  - Record that Wave 6E verifies queue/ack/timeout with display rendering while provider delivery remains deferred.
- Modify `YouTubeBridgeV2/docs/architecture-index.md`
  - Add Wave 6E status note and boundary.
- Modify `YouTubeBridgeV2/docs/api-reference-index.md`
  - Add the new E2E test source references under Chat Display UI and Presentation/TTS.

---

### Task 1: Always-On Display + TTS Real-Storage E2E Test

**Files:**
- Create: `tests/youtubebridge_v2/test_display_tts_e2e.py`

- [ ] **Step 1: Write the E2E test file**

Create `tests/youtubebridge_v2/test_display_tts_e2e.py` with this content:

```python
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.storage_manager import StorageManager
from YouTubeBridgeV2.app import create_v2_app
from YouTubeBridgeV2.composition import create_v2_composition
from YouTubeBridgeV2.runtime.memoria_runners import MemoriaPlannedShowRunner


ROOT = Path(__file__).resolve().parents[2]
UI_MODULE = ROOT / "YouTubeBridgeV2" / "static" / "chat-display" / "chat-display.js"
STARTED_AT = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)


class FakeMemoriaTransport:
    def __init__(self, *responses: dict[str, object]) -> None:
        self.responses = list(responses)
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        if self.responses:
            return self.responses.pop(0)
        return _memoria_response()


def _storage_manager(tmp_path):
    return StorageManager(
        prefs_file=str(tmp_path / "prefs.json"),
        history_file=str(tmp_path / "history.json"),
        persona_snapshot_db_path=str(tmp_path / "persona_snapshots.db"),
        youtube_bridge_v2_db_path=str(tmp_path / "youtubebridge_v2.db"),
    )


def _memoria_response() -> dict[str, object]:
    return {
        "session_id": "memoria-display-tts",
        "message_id": "planned-display-tts",
        "character_id": "host",
        "character_name": "Luna",
        "role_label": "Host",
        "voice_id": "voice-luna",
        "reply": "Display and TTS line",
        "presentation": {
            "voice_state": "speaking",
            "visual_state": "focus",
            "raw_payload": {"token": "must not leak"},
        },
    }


def _create_session_payload(session_id: str) -> dict[str, object]:
    return {
        "command_id": f"{session_id}-create",
        "session_id": session_id,
        "aftertalk_policy": "auto",
        "metadata": {
            "duration_policy": {
                "planned_duration_seconds": 3600,
                "auto_finalize_on_duration": True,
                "aftertalk_requires_remaining_time": True,
            },
            "tts_policy": {
                "enabled": True,
                "provider": "local",
                "default_voice_id": "fallback-voice",
            },
            "hidden_prompt": "must not leak",
        },
    }


def _plan_payload(session_id: str) -> dict[str, object]:
    return {
        "command_id": f"{session_id}-bind",
        "plan": {
            "plan_id": "plan-display-tts",
            "title": "Display TTS E2E",
            "raw_topic_pack": "must not leak",
            "turns": [
                {
                    "id": "opening",
                    "purpose": "Open with a display and TTS verification line.",
                    "topic_cue": "Display and TTS verification.",
                    "speaker_policy": {"type": "fixed", "speaker_ids": ["host"]},
                    "audience_insertion": {
                        "enabled": False,
                        "allow_super_chats": False,
                    },
                }
            ],
        },
    }


def _assert_no_private_payload(value: object) -> None:
    text = repr(value).lower()
    for forbidden in (
        "hidden_prompt",
        "raw_prompt",
        "raw_payload",
        "raw_memoriacore_payload",
        "raw_topic_pack",
        "topic_pack_fact_cards",
        "access_token",
        "authorization",
        "secret",
        "token",
        "must not leak",
    ):
        assert forbidden not in text


def _sse_payloads(text: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        payloads.append(json.loads(line.removeprefix("data: ")))
    return payloads


def _run_node_json(source: str) -> dict[str, object]:
    code = f"""
import * as ui from {json.dumps(UI_MODULE.as_uri())};
{source}
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-"],
        input=code,
        text=True,
        capture_output=True,
        cwd=ROOT,
        check=True,
    )
    return json.loads(result.stdout)


def test_display_stream_renders_presentation_and_tts_queue_round_trip(tmp_path):
    storage = _storage_manager(tmp_path)
    transport = FakeMemoriaTransport(_memoria_response())
    composition = create_v2_composition(
        storage_manager=storage,
        planned_show_runner=MemoriaPlannedShowRunner(storage, transport),
    )
    client = TestClient(create_v2_app(composition, now_provider=lambda: STARTED_AT))
    session_id = "session-display-tts"

    create_response = client.post("/v2/sessions", json=_create_session_payload(session_id))
    bind_response = client.post(f"/v2/sessions/{session_id}/plan", json=_plan_payload(session_id))
    tick_response = client.post(
        f"/v2/sessions/{session_id}/tick",
        json={"command_id": f"{session_id}-tick"},
    )
    with client.stream("GET", f"/v2/sessions/{session_id}/display-stream") as stream:
        stream.read()
        display_events = _sse_payloads(stream.text)
    queue_response = client.get(f"/v2/sessions/{session_id}/tts-queue")
    phase_before_ack = client.get(f"/v2/sessions/{session_id}/phase").json()["phase"]

    assert create_response.status_code == 200
    assert bind_response.status_code == 200
    assert tick_response.status_code == 200
    assert queue_response.status_code == 200
    character_events = [
        event for event in display_events if event.get("event_type") == "character_response"
    ]
    assert len(character_events) == 1
    character_payload = character_events[0]["public_payload"]
    assert character_payload["character_name"] == "Luna"
    assert character_payload["role_label"] == "Host"
    assert character_payload["response_text"] == "Display and TTS line"
    assert character_payload["presentation"]["voice_state"] == "speaking"
    assert character_payload["presentation"]["visual_state"] == "focus"
    assert character_payload["presentation"]["phase"] == "planned_show"

    rendered = _run_node_json(
        f"""
const events = {json.dumps(character_events)};
const html = ui.renderDisplayEvents(events);
console.log(JSON.stringify({{html}}));
"""
    )
    assert 'data-testid="character-response"' in rendered["html"]
    assert 'data-testid="presentation-metadata"' in rendered["html"]
    assert "Display and TTS line" in rendered["html"]
    assert "speaking / focus" in rendered["html"]

    queued = queue_response.json()["tts_queue"]
    assert len(queued) == 1
    assert queued[0]["text"] == "Display and TTS line"
    assert queued[0]["status"] == "pending"
    assert queued[0]["voice_id"] == "voice-luna"
    assert queued[0]["provider"] == "local"
    assert queued[0]["metadata"]["interaction_id"].endswith(":planned-display-tts")
    delivery_id = queued[0]["delivery_id"]

    ack_response = client.post(
        f"/v2/sessions/{session_id}/tts-deliveries/{delivery_id}/ack",
        json={"command_id": f"{session_id}-ack"},
    )
    timeout_response = client.post(
        f"/v2/sessions/{session_id}/tts-deliveries/{delivery_id}/timeout",
        json={"command_id": f"{session_id}-timeout", "timeout_seconds": 30},
    )
    phase_after_timeout = client.get(f"/v2/sessions/{session_id}/phase").json()["phase"]
    delivered_queue = client.get(f"/v2/sessions/{session_id}/tts-queue?status=delivered")

    assert ack_response.status_code == 200
    assert ack_response.json()["status"] == "delivered"
    assert ack_response.json()["phase_transition_requested"] is False
    assert timeout_response.status_code == 200
    assert timeout_response.json()["timeout_ignored"] is True
    assert timeout_response.json()["phase_transition_requested"] is False
    assert phase_after_timeout == phase_before_ack
    assert delivered_queue.json()["tts_queue"][0]["delivery_id"] == delivery_id
    _assert_no_private_payload(
        (
            display_events,
            rendered,
            queue_response.json(),
            ack_response.json(),
            timeout_response.json(),
            delivered_queue.json(),
        )
    )
```

- [ ] **Step 2: Run the E2E test**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_display_tts_e2e.py::test_display_stream_renders_presentation_and_tts_queue_round_trip -q
```

Expected: PASS when Waves 6A-6D are integrated. If it fails, fix only the missing display/TTS integration boundary exposed by this acceptance test.

---

### Task 2: Opt-In Browser Display + TTS Smoke

**Files:**
- Modify: `tests/youtubebridge_v2/test_display_tts_e2e.py`

- [ ] **Step 1: Add browser smoke helpers and test**

Append this code to `tests/youtubebridge_v2/test_display_tts_e2e.py`:

```python
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _post_json(base_url: str, path: str, payload: dict[str, object]) -> None:
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        assert 200 <= response.status < 300


def _wait_for_http(url: str, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if 200 <= response.status < 500:
                    return
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
        time.sleep(0.2)
    raise RuntimeError(f"server did not become ready: {last_error}")


def _write_temp_v2_app(tmp_path: Path) -> Path:
    module_path = tmp_path / "display_tts_e2e_app.py"
    module_path.write_text(
        textwrap.dedent(
            f'''
            from datetime import datetime, timezone

            from core.storage_manager import StorageManager
            from YouTubeBridgeV2.app import create_v2_app
            from YouTubeBridgeV2.composition import create_v2_composition
            from YouTubeBridgeV2.runtime.memoria_runners import MemoriaPlannedShowRunner


            STARTED_AT = datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)


            class FakeMemoriaTransport:
                def __init__(self):
                    self.requests = []

                def send(self, request):
                    self.requests.append(request)
                    return {{
                        "session_id": "memoria-browser-display-tts",
                        "message_id": "planned-browser-display-tts",
                        "character_id": "host",
                        "character_name": "Luna",
                        "role_label": "Host",
                        "voice_id": "voice-luna",
                        "reply": "Browser display TTS line",
                        "presentation": {{
                            "voice_state": "speaking",
                            "visual_state": "focus",
                            "raw_payload": {{"token": "must not leak"}},
                        }},
                    }}


            storage = StorageManager(
                prefs_file={str(tmp_path / "prefs.json")!r},
                history_file={str(tmp_path / "history.json")!r},
                persona_snapshot_db_path={str(tmp_path / "persona_snapshots.db")!r},
                youtube_bridge_v2_db_path={str(tmp_path / "youtubebridge_v2.db")!r},
            )
            composition = create_v2_composition(
                storage_manager=storage,
                planned_show_runner=MemoriaPlannedShowRunner(storage, FakeMemoriaTransport()),
            )
            app = create_v2_app(composition, now_provider=lambda: STARTED_AT)
            '''
        ),
        encoding="utf-8",
    )
    return module_path


@pytest.mark.skipif(
    os.environ.get("YOUTUBEBRIDGE_V2_BROWSER_SMOKE") != "1",
    reason="set YOUTUBEBRIDGE_V2_BROWSER_SMOKE=1 to run browser E2E smoke",
)
def test_browser_chat_display_renders_presentation_and_tts_queue(tmp_path):
    chrome = os.environ.get("PLAYWRIGHT_CHROME_EXECUTABLE")
    if not chrome:
        pytest.skip("PLAYWRIGHT_CHROME_EXECUTABLE is required")
    node = os.environ.get("NODE_EXE", "node")
    _write_temp_v2_app(tmp_path)
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), str(tmp_path), env.get("PYTHONPATH", "")]
    )
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "display_tts_e2e_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    session_id = f"browser-display-tts-{int(time.time() * 1000)}"
    try:
        _wait_for_http(f"{base_url}/v2/static/chat-display/index.html")
        _post_json(base_url, "/v2/sessions", _create_session_payload(session_id))
        _post_json(base_url, f"/v2/sessions/{session_id}/plan", _plan_payload(session_id))
        _post_json(
            base_url,
            f"/v2/sessions/{session_id}/tick",
            {"command_id": f"{session_id}-tick"},
        )

        script = tmp_path / "display_tts_browser_smoke.cjs"
        script.write_text(
            textwrap.dedent(
                """
                const { chromium } = require("playwright");
                const [baseUrl, sessionId, chromePath] = process.argv.slice(2);

                (async () => {
                  const browser = await chromium.launch({executablePath: chromePath, headless: true});
                  const consoleErrors = [];
                  const badResponses = [];

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
                    await page.waitForSelector("[data-testid='character-response']", {timeout: 10000});
                    await page.waitForSelector("[data-testid='presentation-metadata']", {timeout: 10000});
                    const state = await page.evaluate(async (sessionId) => {
                      const root = document.querySelector("#chatDisplayRoot");
                      const queueResponse = await fetch(`/v2/sessions/${encodeURIComponent(sessionId)}/tts-queue`);
                      const queuePayload = await queueResponse.json();
                      const deliveryId = queuePayload.tts_queue[0].delivery_id;
                      const ackResponse = await fetch(`/v2/sessions/${encodeURIComponent(sessionId)}/tts-deliveries/${encodeURIComponent(deliveryId)}/ack`, {
                        method: "POST",
                        headers: {"content-type": "application/json"},
                        body: JSON.stringify({command_id: `${sessionId}-browser-ack-${window.innerWidth}`}),
                      });
                      const ackPayload = await ackResponse.json();
                      const timeoutResponse = await fetch(`/v2/sessions/${encodeURIComponent(sessionId)}/tts-deliveries/${encodeURIComponent(deliveryId)}/timeout`, {
                        method: "POST",
                        headers: {"content-type": "application/json"},
                        body: JSON.stringify({command_id: `${sessionId}-browser-timeout-${window.innerWidth}`, timeout_seconds: 30}),
                      });
                      const timeoutPayload = await timeoutResponse.json();
                      return {
                        text: document.body.innerText,
                        html: root?.innerHTML || "",
                        overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
                        queuePayload,
                        ackPayload,
                        timeoutPayload,
                      };
                    }, sessionId);
                    await page.close();
                    if (!state.text.includes("Browser display TTS line")) throw new Error(`missing rendered response at ${width}`);
                    if (!state.text.includes("speaking / focus")) throw new Error(`missing presentation metadata at ${width}`);
                    if (state.queuePayload.tts_queue[0].text !== "Browser display TTS line") throw new Error(`missing queued delivery at ${width}`);
                    if (state.ackPayload.status !== "delivered") throw new Error(`ack did not deliver at ${width}`);
                    if (state.ackPayload.phase_transition_requested !== false) throw new Error(`ack requested phase transition at ${width}`);
                    if (state.timeoutPayload.phase_transition_requested !== false) throw new Error(`timeout requested phase transition at ${width}`);
                    const lowered = state.html.toLowerCase();
                    if (lowered.includes("raw_payload") || lowered.includes("must not leak") || lowered.includes("token")) {
                      throw new Error(`private payload leaked at ${width}`);
                    }
                    if (state.overflow) throw new Error(`horizontal overflow at ${width}`);
                    return {width, ok: true};
                  }

                  const desktop = await checkViewport(1280, 720);
                  const mobile = await checkViewport(390, 720);
                  await browser.close();
                  const relevantConsoleErrors = consoleErrors.filter((text) => !text.includes("404 (Not Found)"));
                  if (badResponses.length || relevantConsoleErrors.length) {
                    throw new Error(JSON.stringify({badResponses, consoleErrors: relevantConsoleErrors}));
                  }
                  console.log(JSON.stringify({desktop, mobile}));
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
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
```

- [ ] **Step 2: Run the normal E2E file**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_display_tts_e2e.py -q
```

Expected: the always-on test passes and the browser smoke is skipped unless `YOUTUBEBRIDGE_V2_BROWSER_SMOKE=1`.

- [ ] **Step 3: Run opt-in browser smoke when local Playwright Chrome is available**

Run only if `PLAYWRIGHT_CHROME_EXECUTABLE` points to a valid Chromium/Chrome:

```powershell
$env:YOUTUBEBRIDGE_V2_BROWSER_SMOKE='1'; python -m pytest tests\youtubebridge_v2\test_display_tts_e2e.py::test_browser_chat_display_renders_presentation_and_tts_queue -q
```

Expected: PASS. If `PLAYWRIGHT_CHROME_EXECUTABLE` is missing, record the skip and use the Codex Browser plugin for manual verification instead of silently claiming browser coverage.

---

### Task 3: Documentation and Index Updates

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/chat-display-ui.md`
- Modify: `YouTubeBridgeV2/docs/modules/presentation-tts.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] **Step 1: Update module docs**

Add concise Traditional Chinese notes:

- `chat-display-ui.md`: Wave 6E verifies real storage/runtime/display stream -> `chat-display.js` renderer for character response and presentation metadata.
- `presentation-tts.md`: Wave 6E verifies the same response creates a TTS delivery queue item and that ack/timeout do not request phase transitions.

- [ ] **Step 2: Update index docs**

Add concise Traditional Chinese notes:

- `architecture-index.md`: add `Integration Wave 6E 狀態` with E2E verification and provider/audio boundary.
- `api-reference-index.md`: add `tests/youtubebridge_v2/test_display_tts_e2e.py::test_display_stream_renders_presentation_and_tts_queue_round_trip` and `tests/youtubebridge_v2/test_display_tts_e2e.py::test_browser_chat_display_renders_presentation_and_tts_queue` under relevant Chat Display UI / Presentation-TTS source sections.

- [ ] **Step 3: Verify docs references**

Run:

```powershell
rg -n "Wave 6E|display_tts_e2e|test_display_stream_renders_presentation_and_tts_queue_round_trip|test_browser_chat_display_renders_presentation_and_tts_queue" YouTubeBridgeV2\docs
```

Expected: hits in module docs, architecture index, API reference, and this implementation plan.

---

### Task 4: Verification and Commit

**Files:**
- All files above.

- [ ] **Step 1: Run Wave 6 focused tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_display_tts_e2e.py tests\youtubebridge_v2\test_chat_display_ui.py tests\youtubebridge_v2\test_presentation_tts.py tests\youtubebridge_v2\test_display_event_contract.py tests\youtubebridge_v2\test_real_storage_integration.py -q
```

Expected: PASS, with the opt-in browser smoke skipped unless enabled.

- [ ] **Step 2: Run full V2 suite**

Run:

```powershell
python -m pytest tests\youtubebridge_v2 -q
```

Expected: full V2 suite passes; browser smoke tests remain skipped by default.

- [ ] **Step 3: Check whitespace**

Run:

```powershell
git diff --check
```

Expected: exit code 0. Existing LF/CRLF warnings are acceptable if no whitespace errors are reported.

- [ ] **Step 4: Inspect scope and commit**

Run:

```powershell
git status --short
git diff --stat
```

Expected: changed files are limited to the new 6E tests, docs, and this plan.

Commit:

```powershell
git add tests\youtubebridge_v2\test_display_tts_e2e.py YouTubeBridgeV2\docs\modules\chat-display-ui.md YouTubeBridgeV2\docs\modules\presentation-tts.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\implementation-plans\display-tts-e2e-verification.md
git commit -m "test: verify display TTS E2E flow"
```

---

## Self-Review

- Spec coverage: The plan covers Wave 6E only: display stream rendering, presentation metadata, TTS queue, ack, timeout, no phase transition, browser verification, docs/API reference sync. It does not implement provider audio playback, real TTS synthesis, provider retries, or roadmap checkbox edits.
- Placeholder scan: No `TBD`, `TODO`, or "fill in later" placeholders remain. The only conditional execution is the explicitly opt-in browser smoke gate.
- Type consistency: The plan uses existing names from Waves 6A-6D: `presentation_character_response`, `character_response`, `tts_policy`, `tts_queue`, `ack`, `timeout`, `phase_transition_requested`, and `DisplayPresentationMetadata`.
