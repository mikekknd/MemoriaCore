# YouTubeBridge Free Talk Stage 2 Main Finish Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect LiveEpisodePlan completion and Studio debug controls to a working phase pipeline that performs main SC closing, starts main summary in the background, and enters post-plan free talk when enabled.

**Architecture:** Introduce explicit phase transition request models and two route endpoints, then route LiveEpisodePlan completion through a manager-level phase pipeline instead of direct auto-finalize. This stage keeps summary splitting lightweight by recording summary job state and using existing summary calls; the full two-summary cleanup gate is Stage 3.

**Tech Stack:** FastAPI routes, YouTubeBridge manager mixins, BridgeStorage director state metadata, existing closing and injection helpers, Studio vanilla JS, pytest, Browser QA.

---

## File Structure

- Create `YouTubeBridge/engine_phase_pipeline.py`: phase transition orchestration for `finish-main` and `finalize`.
- Modify `YouTubeBridge/bridge_engine.py`: include `PhasePipelineManagerMixin` before `ClosingManagerMixin`.
- Modify `YouTubeBridge/models.py`: add request models for phase endpoints.
- Modify `YouTubeBridge/server_routes/sessions.py`: expose `/phase/finish-main` and `/phase/finalize`.
- Modify `YouTubeBridge/engine_director_runtime.py`: call `finish_main_phase()` when planned state completes.
- Modify `YouTubeBridge/engine_closing.py`: expose reusable SC-only main closing helper.
- Modify `YouTubeBridge/static/studio.html`, `YouTubeBridge/static/ui/studio.js`: add debug button `結束節目並進入雜談測試` and connect main stop button to `/phase/finalize`.
- Create or extend `YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py`: manager-level tests.
- Modify `YouTubeBridge/tests/test_server_route_split.py`: route registration tests.
- Modify `YouTubeBridge/tests/test_studio_ui.py`: source tests.

---

### Task 1: Phase Endpoint Models and Routes

**Files:**
- Modify: `YouTubeBridge/models.py`
- Modify: `YouTubeBridge/server_routes/sessions.py`
- Test: `YouTubeBridge/tests/test_server_route_split.py`

- [ ] **Step 1: Write failing route split test**

Add assertions to the route split test that lists public split paths:

```python
def test_phase_pipeline_routes_are_registered():
    from server_routes.sessions import router

    paths = {route.path for route in router.routes}

    assert "/sessions/{session_id}/phase/finish-main" in paths
    assert "/sessions/{session_id}/phase/finalize" in paths
```

- [ ] **Step 2: Run route test and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_server_route_split.py::test_phase_pipeline_routes_are_registered -q
```

Expected: FAIL because both routes are missing.

- [ ] **Step 3: Add request models**

Add to `YouTubeBridge/models.py`:

```python
class FinishMainPhaseRequest(BaseModel):
    reason: str = Field("episode_plan_completed", max_length=120)
    enter_free_talk: bool = True


class FinalizePhaseRequest(BaseModel):
    reason: str = Field("operator_finalize", max_length=120)
```

- [ ] **Step 4: Add routes**

Modify imports in `YouTubeBridge/server_routes/sessions.py`:

```python
from models import FinishMainPhaseRequest, FinalizePhaseRequest, InterruptRequest, LiveSessionConfig, ReplyRecentRequest
```

Add routes:

```python
@router.post("/sessions/{session_id}/phase/finish-main")
async def finish_main_phase(session_id: str, body: FinishMainPhaseRequest):
    if not storage.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    try:
        return await manager.finish_main_phase(
            session_id,
            reason=body.reason,
            enter_free_talk=body.enter_free_talk,
            topic_root=_require_state().free_talk_topic_root,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/sessions/{session_id}/phase/finalize")
async def finalize_phase(session_id: str, body: FinalizePhaseRequest = FinalizePhaseRequest()):
    if not storage.get_session(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    try:
        return await manager.finalize_phase_pipeline(session_id, reason=body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
```

- [ ] **Step 5: Run route test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_server_route_split.py::test_phase_pipeline_routes_are_registered -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add YouTubeBridge/models.py YouTubeBridge/server_routes/sessions.py YouTubeBridge/tests/test_server_route_split.py
git commit -m "feat(youtube-bridge): add phase pipeline routes"
```

---

### Task 2: Main SC Closing and Finish-Main Pipeline

**Files:**
- Create: `YouTubeBridge/engine_phase_pipeline.py`
- Modify: `YouTubeBridge/bridge_engine.py`
- Modify: `YouTubeBridge/engine_closing.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py`

- [ ] **Step 1: Write failing pipeline tests**

Create `YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py`:

```python
from pathlib import Path

import pytest

from bridge_engine import YouTubeBridgeManager
from storage import BridgeStorage


class FakeMemoriaClient:
    def __init__(self):
        self.calls = []

    def chat_stream_sync(self, **kwargs):
        self.calls.append(kwargs)
        return {"session_id": kwargs.get("session_id") or "mem-a", "message_id": 10, "reply": "ok"}


def _storage(tmp_path: Path) -> BridgeStorage:
    storage = BridgeStorage(tmp_path / "youtube_live.db")
    storage.upsert_connector({"connector_id": "youtube-main", "display_name": "YT", "api_key": "", "enabled": True})
    return storage


@pytest.mark.asyncio
async def test_finish_main_phase_handles_sc_and_enters_free_talk(tmp_path):
    topic_root = tmp_path / "runtime" / "YouTubeBridge" / "freeTalkTopics"
    topic_root.mkdir(parents=True)
    (topic_root / "casual.json").write_text(
        '[{"title":"雜談題","prompt":"請聊一輪雜談。"}]',
        encoding="utf-8",
    )
    storage = _storage(tmp_path)
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "youtube-main",
        "display_name": "Pipeline",
        "post_plan_free_talk_enabled": True,
        "post_plan_free_talk_topic_pack_ids": ["casual"],
        "auto_sc_thanks_on_finalize": True,
    })
    storage.save_event({
        "bridge_session_id": "live-a",
        "connector_id": "youtube-main",
        "youtube_message_id": "sc-1",
        "message_type": "superChatEvent",
        "author_channel_id": "a",
        "author_display_name": "SC觀眾",
        "message_text": "謝謝直播",
        "published_at": "2026-05-15T10:00:00",
        "received_at": "2026-05-15T10:00:00",
        "status": "active",
        "amount_display_string": "NT$75",
        "amount_micros": 75000000,
        "priority_class": "super_chat",
        "metadata": {"phase": "planned_content"},
    })
    manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeMemoriaClient)

    result = await manager.finish_main_phase(
        "live-a",
        reason="episode_plan_completed",
        enter_free_talk=True,
        topic_root=topic_root,
    )

    assert result["phase"] == "post_plan_free_talk"
    state = storage.get_director_state("live-a")
    metadata = state["metadata"]
    assert metadata["phase"] == "post_plan_free_talk"
    assert metadata["main_audience_closing"]["status"] == "completed"
    assert metadata["post_plan_free_talk"]["transition_reason"] == "episode_plan_completed"
```

- [ ] **Step 2: Run the pipeline test and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py::test_finish_main_phase_handles_sc_and_enters_free_talk -q
```

Expected: FAIL because `finish_main_phase` does not exist.

- [ ] **Step 3: Create phase pipeline mixin**

Create `YouTubeBridge/engine_phase_pipeline.py`:

```python
"""Phase pipeline helpers for LiveEpisodePlan completion and post-plan free talk."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from bridge_runtime import LiveRuntime


class PhasePipelineManagerMixin:
    async def finish_main_phase(
        self,
        session_id: str,
        *,
        reason: str,
        enter_free_talk: bool,
        topic_root: Path,
    ) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        runtime = self._runtimes.setdefault(session_id, LiveRuntime(session_id=session_id, running=True, status="running"))
        metadata = dict((self.storage.get_director_state(session_id) or {}).get("metadata") or {})
        metadata["phase"] = "main_audience_closing"
        metadata["main_audience_closing"] = {
            "status": "running",
            "started_at": datetime.now().isoformat(),
            "reason": reason,
        }
        self.storage.update_director_state(session_id, status="main_audience_closing", metadata=metadata)
        closing = await self._run_main_audience_sc_closing(runtime, session, reason=reason)
        metadata = dict((self.storage.get_director_state(session_id) or {}).get("metadata") or {})
        metadata["main_audience_closing"] = {
            **metadata.get("main_audience_closing", {}),
            "status": "completed",
            "completed_at": datetime.now().isoformat(),
            "closing": closing,
        }
        metadata["main_summary"] = {"status": "queued", "reason": reason}
        self.storage.update_director_state(session_id, status="main_summary_queued", metadata=metadata)
        asyncio.create_task(self._run_main_summary_background(session_id, reason=reason))
        should_enter_free_talk = bool(enter_free_talk and session.get("post_plan_free_talk_enabled"))
        if should_enter_free_talk:
            return await self.start_post_plan_free_talk_test(
                session_id,
                topic_root=topic_root,
                transition_reason=reason,
            )
        metadata = dict((self.storage.get_director_state(session_id) or {}).get("metadata") or {})
        metadata["phase"] = "finalizing_main_only"
        state = self.storage.update_director_state(session_id, status="finalizing_main_only", metadata=metadata)
        await self._broadcast(session_id, {"type": "director_state", "director": state})
        return {"phase": "finalizing_main_only", "director": state}

    async def _run_main_summary_background(self, session_id: str, *, reason: str) -> None:
        state = self.storage.get_director_state(session_id)
        metadata = dict(state.get("metadata") or {})
        metadata["main_summary"] = {"status": "running", "reason": reason, "started_at": datetime.now().isoformat()}
        self.storage.update_director_state(session_id, metadata=metadata)
```

The background method records state only in Stage 2. Stage 3 replaces it with phase-filtered summary and Shared Memory writing.

- [ ] **Step 4: Include the mixin in the manager**

Modify `YouTubeBridge/bridge_engine.py` imports:

```python
from engine_phase_pipeline import PhasePipelineManagerMixin
```

Modify `YouTubeBridgeManager` base classes:

```python
class YouTubeBridgeManager(
    EpisodePlanManagerMixin,
    PhasePipelineManagerMixin,
    DirectorRuntimeManagerMixin,
    ClosingManagerMixin,
    InjectionManagerMixin,
    RuntimeLifecycleManagerMixin,
    EventSafetyManagerMixin,
    TestRuntimeManagerMixin,
    DirectorManagerMixin,
    TopicPackManagerMixin,
):
```

- [ ] **Step 5: Add SC-only main closing helper**

In `YouTubeBridge/engine_closing.py`, add:

```python
async def _run_main_audience_sc_closing(self, runtime: LiveRuntime, session: dict[str, Any], *, reason: str) -> dict[str, Any]:
    if not session.get("auto_sc_thanks_on_finalize", True):
        return {"status": "skipped", "reason": "auto_sc_thanks_disabled"}
    super_chats = [
        event for event in self.storage.list_super_chats(runtime.session_id, unhandled_only=True, limit=100)
        if (event.get("metadata") or {}).get("phase", "planned_content") == "planned_content"
    ]
    if not super_chats:
        return {"status": "skipped", "reason": "no_unhandled_main_super_chats"}
    result = await self.inject_recent(
        session_id=runtime.session_id,
        event_ids=[int(event["id"]) for event in super_chats],
        max_events=len(super_chats),
        content="正式節目段落結束，請逐一感謝尚未處理的 Super Chat。",
        memoria_session_id=session.get("target_memoria_session_id", ""),
        character_ids=session.get("character_ids", []),
        source="main_audience_closing",
    )
    self.storage.mark_super_chats_handled_in_closing(runtime.session_id, [int(event["id"]) for event in super_chats])
    return {"status": "completed", "super_chat_count": len(super_chats), "result": result}
```

- [ ] **Step 6: Run pipeline test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py::test_finish_main_phase_handles_sc_and_enters_free_talk -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add YouTubeBridge/engine_phase_pipeline.py YouTubeBridge/bridge_engine.py YouTubeBridge/engine_closing.py YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py
git commit -m "feat(youtube-bridge): route main completion through phase pipeline"
```

---

### Task 3: LiveEpisodePlan Completion Uses Finish-Main Pipeline

**Files:**
- Modify: `YouTubeBridge/engine_closing.py`
- Modify: `YouTubeBridge/engine_director_runtime.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_director.py`

- [ ] **Step 1: Write failing test that plan completion does not direct-finalize**

Append to `YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py`:

```python
@pytest.mark.asyncio
async def test_episode_plan_completed_enters_phase_pipeline_instead_of_direct_finalize(tmp_path, monkeypatch):
    topic_root = tmp_path / "runtime" / "YouTubeBridge" / "freeTalkTopics"
    topic_root.mkdir(parents=True)
    storage = _storage(tmp_path)
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "youtube-main",
        "display_name": "Plan Complete",
        "post_plan_free_talk_enabled": False,
    })
    manager = YouTubeBridgeManager(storage)
    called = []

    async def fake_finish_main(session_id, *, reason, enter_free_talk, topic_root):
        called.append((session_id, reason, enter_free_talk))
        return {"phase": "finalizing_main_only"}

    monkeypatch.setattr(manager, "finish_main_phase", fake_finish_main)
    runtime = manager._runtimes.setdefault("live-a", LiveRuntime(session_id="live-a", running=True, status="running"))

    await manager._finalize_for_episode_plan_completed(runtime, storage.get_session("live-a"), {"plan_status": "completed"})

    assert called == [("live-a", "episode_plan_completed", True)]
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py::test_episode_plan_completed_enters_phase_pipeline_instead_of_direct_finalize -q
```

Expected: FAIL because `_finalize_for_episode_plan_completed` still calls direct finalize logic.

- [ ] **Step 3: Replace direct finalize call**

Modify `YouTubeBridge/engine_closing.py` `_finalize_for_episode_plan_completed` so it delegates:

```python
    async def _finalize_for_episode_plan_completed(
        self,
        runtime: LiveRuntime,
        session: dict[str, Any],
        planned_state: dict[str, Any],
    ) -> None:
        await self.finish_main_phase(
            runtime.session_id,
            reason="episode_plan_completed",
            enter_free_talk=True,
            topic_root=PROJECT_ROOT / "runtime" / "YouTubeBridge" / "freeTalkTopics",
        )
```

If `PROJECT_ROOT` is not imported in `engine_closing.py`, add:

```python
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
```

Keep `_finalize_live_session` unchanged because manual legacy finalize still uses it.

- [ ] **Step 4: Run targeted tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py YouTubeBridge/tests/test_bridge_engine_director.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add YouTubeBridge/engine_closing.py YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py
git commit -m "feat(youtube-bridge): enter phase pipeline when episode plan completes"
```

---

### Task 4: Studio Controls for Finish-Main and Finalize

**Files:**
- Modify: `YouTubeBridge/static/studio.html`
- Modify: `YouTubeBridge/static/ui/studio.js`
- Test: `YouTubeBridge/tests/test_studio_ui.py`

- [ ] **Step 1: Write failing Studio source test**

Append to `YouTubeBridge/tests/test_studio_ui.py`:

```python
def test_studio_phase_pipeline_controls():
    studio_html = STUDIO_HTML.read_text(encoding="utf-8")
    studio_js = STUDIO_JS.read_text(encoding="utf-8")

    assert "結束節目並進入雜談測試" in studio_html
    assert "/phase/finish-main" in studio_js
    assert "/phase/finalize" in studio_js
    assert 'enter_free_talk: true' in studio_js
    assert 'reason: "operator_debug_skip_to_free_talk"' in studio_js
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_ui.py::test_studio_phase_pipeline_controls -q
```

Expected: FAIL because controls are not wired.

- [ ] **Step 3: Add debug finish-main button**

In `YouTubeBridge/static/studio.html`, add to the Debug or Test tab:

```html
<button class="secondary" id="skipMainToFreeTalk" type="button">結束節目並進入雜談測試</button>
<p id="skipMainToFreeTalkState" class="muted">測試用：直接結束正式節目階段並進入雜談。</p>
```

In `YouTubeBridge/static/ui/studio.js`, add:

```js
async function skipMainToFreeTalk() {
  if (!(state.sessionId && state.live)) {
    $("skipMainToFreeTalkState").textContent = "請先開始直播。";
    return;
  }
  try {
    const result = await api(`/sessions/${encodeURIComponent(state.sessionId)}/phase/finish-main`, {
      method: "POST",
      body: {
        reason: "operator_debug_skip_to_free_talk",
        enter_free_talk: true,
      },
    });
    $("skipMainToFreeTalkState").textContent = "已結束正式節目階段並進入雜談流程。";
    appendLog("INFO", `Phase transition：${result.phase || "post_plan_free_talk"}`);
    await refreshStudioSession();
    await refreshChatPreview();
  } catch (error) {
    $("skipMainToFreeTalkState").textContent = `雜談跳轉失敗：${error.message || error}`;
    appendLog("WARN", `雜談跳轉失敗：${error.message || error}`);
  }
}

$("skipMainToFreeTalk").addEventListener("click", skipMainToFreeTalk);
```

- [ ] **Step 4: Wire main stop button to phase finalize**

In the existing stop handler, replace `/sessions/${sessionId}/stop` with:

```js
await api(`/sessions/${encodeURIComponent(state.sessionId)}/phase/finalize`, {
  method: "POST",
  body: { reason: "operator_finalize" },
});
```

Keep emergency `/stop` out of the Studio main control. If an emergency stop remains, place it only in Debug and label it `停止 runtime，不做摘要`.

- [ ] **Step 5: Run Studio source test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_studio_ui.py::test_studio_phase_pipeline_controls -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add YouTubeBridge/static/studio.html YouTubeBridge/static/ui/studio.js YouTubeBridge/tests/test_studio_ui.py
git commit -m "feat(youtube-bridge): wire studio phase transition controls"
```

---

### Task 5: Stage 2 E2E Gate

**Files:**
- No new files.

- [ ] **Step 1: Run Stage 2 regression**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py YouTubeBridge/tests/test_bridge_engine_director.py YouTubeBridge/tests/test_server_route_split.py YouTubeBridge/tests/test_studio_ui.py -q
node --check YouTubeBridge/static/ui/studio.js
git diff --check
```

Expected: pytest PASS, `node --check` exit 0, `git diff --check` exit 0 or only existing CRLF warnings.

- [ ] **Step 2: Browser E2E**

Start 8091 in a visible foreground window:

```powershell
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "G:\ClaudeProject\MemoriaCore\YouTubeBridge\start.bat" -WorkingDirectory "G:\ClaudeProject\MemoriaCore\YouTubeBridge" -WindowStyle Normal
```

Browser QA:

- Open `http://127.0.0.1:8091/studio/`.
- Select a LiveEpisodePlan.
- Enable `Plan 結束後進入無導播雜談`.
- Start test-mode live session.
- Inject one SC test event.
- Click `結束節目並進入雜談測試`.
- Confirm Debug Log shows phase transition.
- Confirm the SC closing interaction runs before the first free talk topic tick.
- Confirm central conversation continues in free talk.
- Confirm the main stop button calls `/phase/finalize`, not `/stop`.

- [ ] **Step 3: Commit Browser QA note if the repo uses QA notes**

If a repo-local QA notes file exists under `docs/superpowers/`, append the Stage 2 Browser QA result. If no such file exists, include the Browser QA result in the final response instead of creating a new tracking file.

---

## Stage 2 Acceptance Criteria

- LiveEpisodePlan completion no longer direct-finalizes a session.
- `finish-main` runs main SC closing and then enters free talk when enabled.
- Manual Studio finalize does not enter free talk.
- Debug skip button can end main phase and enter free talk for E2E.
- Existing `/finalize` remains available as legacy/manual fallback, but Studio main control uses `/phase/finalize`.
