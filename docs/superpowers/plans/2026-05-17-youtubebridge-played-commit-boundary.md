# YouTubeBridge Played Commit Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent generated-but-unplayed planned turns from entering the main MemoriaCore conversation context.

**Architecture:** Treat live presentation playback as the commit boundary. Planned prefetch generation writes to a draft sidecar session and local presentation items only; after those items are actually presented, YouTubeBridge commits the played lines into the main Memoria session in playback order.

**Tech Stack:** Python 3.12, FastAPI, SQLite, pytest, MemoriaCore session API, YouTubeBridge presentation queue.

---

## Problem Evidence

From `runtime/llm_trace.jsonl` and `runtime/YouTubeBridge/youtube_live.db`:

- `live_interactions.id=2138` was `source=director_prefetch`, generated a full plan turn, then ended as `interrupted/live_session_ended`.
- presentation items `891` and `893` for that job stayed `ready` and had no `presented_at`.
- later prompt `log_id=899668a4c921435ebe803dd168f0f9eb` still contained the unplayed text, and the next reply answered it.

The fix is not to hide ready items in Studio. The fix is to stop prefetch from writing generated assistant messages into the live Memoria session before playback.

## File Structure

- Modify: `api/models/requests.py`
  - Add a request model for committing assistant messages from trusted bridge code.
- Modify: `api/routers/session.py`
  - Add a protected endpoint that appends an assistant message to an existing session.
- Modify: `YouTubeBridge/memoria_client.py`
  - Add `add_assistant_event()` wrapper for the new endpoint.
- Modify: `YouTubeBridge/engine_director_runtime.py`
  - Route `prefetch_only=True` generation to a sidecar draft session.
  - Commit only presented prefetched lines to the main session after `present_prepared_stream_results()`.
- Modify: `YouTubeBridge/storage_repositories/interactions.py`
  - Preserve draft session id / committed state metadata.
- Test: `YouTubeBridge/tests/test_bridge_engine_director.py`
  - Add regression tests for draft prefetch and playback commit.
- Test: `tests/test_session_routes.py` or `tests/test_server_auth.py`
  - Add API test for assistant-event endpoint authorization.

## Task 1: Add A Trusted Assistant Message Endpoint

**Files:**
- Modify: `api/models/requests.py`
- Modify: `api/routers/session.py`
- Test: `tests/test_server_auth.py`

- [ ] **Step 1: Write the failing endpoint test**

Append this test near existing session route tests in `tests/test_server_auth.py`:

```python
@pytest.mark.asyncio
async def test_session_assistant_event_appends_character_message(monkeypatch):
    from api.routers import session as session_router

    class FakeSessionManager:
        def __init__(self):
            self.sessions = {
                "session-a": type("Session", (), {"user_id": "admin-user"})()
            }
            self.calls = []

        async def get(self, session_id):
            return self.sessions.get(session_id)

        async def restore_from_db(self, session_id, user_id=None):
            return self.sessions.get(session_id)

        async def add_assistant_message(
            self,
            session_id,
            content,
            debug_info=None,
            extracted_entities=None,
            character_name=None,
            character_id=None,
        ):
            self.calls.append({
                "session_id": session_id,
                "content": content,
                "debug_info": debug_info,
                "extracted_entities": extracted_entities,
                "character_name": character_name,
                "character_id": character_id,
            })
            return 123

    fake = FakeSessionManager()
    monkeypatch.setattr(session_router, "session_manager", fake)

    body = session_router.SessionAssistantEventRequest(
        content="已播放台詞。",
        character_id="char-a",
        character_name="可可",
        debug_info={"event_type": "youtube_live_played_commit"},
    )
    result = await session_router.add_session_assistant_event(
        "session-a",
        body,
        current_user={"id": "admin-user", "role": "admin"},
    )

    assert result == {"status": "created", "session_id": "session-a", "message_id": 123}
    assert fake.calls == [{
        "session_id": "session-a",
        "content": "已播放台詞。",
        "debug_info": {"event_type": "youtube_live_played_commit"},
        "extracted_entities": None,
        "character_name": "可可",
        "character_id": "char-a",
    }]
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
python -m pytest tests\test_server_auth.py::test_session_assistant_event_appends_character_message -q
```

Expected: FAIL because `SessionAssistantEventRequest` and `add_session_assistant_event` do not exist.

- [ ] **Step 3: Add the request model**

In `api/models/requests.py`, add this model near `SessionSystemEventRequest`:

```python
class SessionAssistantEventRequest(BaseModel):
    content: str = Field(..., min_length=1)
    character_id: str = ""
    character_name: str = ""
    debug_info: dict[str, Any] = Field(default_factory=dict)
    extracted_entities: list[str] | None = None
```

- [ ] **Step 4: Add the route**

In `api/routers/session.py`, import `SessionAssistantEventRequest` and add this route after `add_session_system_event`:

```python
@router.post("/{session_id}/assistant-event")
async def add_session_assistant_event(
    session_id: str,
    body: SessionAssistantEventRequest,
    current_user: dict = Depends(get_current_user),
):
    s = await session_manager.get(session_id)
    if not s:
        try:
            s = await session_manager.restore_from_db(session_id, user_id=None)
        except PermissionError:
            raise HTTPException(403, detail="Session owner mismatch")
    if not s:
        raise HTTPException(404, detail=f"Session {session_id} not found")
    if s.user_id != str(current_user["id"]) and current_user.get("role") != "admin":
        raise HTTPException(403, detail="Session owner mismatch")
    message_id = await session_manager.add_assistant_message(
        session_id,
        body.content,
        body.debug_info,
        body.extracted_entities,
        character_name=body.character_name or None,
        character_id=body.character_id or None,
    )
    if message_id is None:
        raise HTTPException(404, detail=f"Session {session_id} not found")
    return {"status": "created", "session_id": session_id, "message_id": message_id}
```

- [ ] **Step 5: Verify the endpoint test passes**

Run:

```powershell
python -m pytest tests\test_server_auth.py::test_session_assistant_event_appends_character_message -q
```

Expected: PASS.

## Task 2: Add MemoriaClient Commit Wrapper

**Files:**
- Modify: `YouTubeBridge/memoria_client.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_director.py`

- [ ] **Step 1: Write the failing client-level expectation in a director test fake**

In the next task's fake client, the manager will call:

```python
def add_assistant_event(self, *, session_id, content, character_id="", character_name="", debug_info=None, extracted_entities=None):
    committed.append({
        "session_id": session_id,
        "content": content,
        "character_id": character_id,
        "character_name": character_name,
        "debug_info": debug_info or {},
        "extracted_entities": extracted_entities,
    })
    return {"status": "created", "message_id": len(committed)}
```

This step intentionally has no standalone pytest command because the wrapper is covered by Task 3's manager test.

- [ ] **Step 2: Implement `add_assistant_event()`**

In `YouTubeBridge/memoria_client.py`, add this method after `add_system_event()`:

```python
    def add_assistant_event(
        self,
        *,
        session_id: str,
        content: str,
        character_id: str = "",
        character_name: str = "",
        debug_info: dict[str, Any] | None = None,
        extracted_entities: list[str] | None = None,
    ) -> dict[str, Any]:
        self.ensure_auth()
        response = self.session.post(
            f"{self.base_url}/session/{session_id}/assistant-event",
            json={
                "content": content,
                "character_id": character_id,
                "character_name": character_name,
                "debug_info": debug_info or {},
                "extracted_entities": extracted_entities,
            },
            headers=self._headers(),
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"MemoriaCore assistant event failed: HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        return data if isinstance(data, dict) else {}
```

## Task 3: Make Planned Prefetch Use A Draft Session

**Files:**
- Modify: `YouTubeBridge/engine_director_runtime.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_director.py`

- [ ] **Step 1: Write the failing regression test**

Append this test near the existing prefetch tests:

```python
@pytest.mark.asyncio
async def test_episode_prefetch_uses_draft_session_and_commits_only_when_presented(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({
            "connector_id": "yt-main",
            "display_name": "YouTube Main",
            "enabled": True,
        })
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "display_name": "Plan Live",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a", "analyst-b"],
            "presentation_enabled": True,
            "tts_enabled": True,
            "presentation_ack_timeout_seconds": 5,
        })
        storage.upsert_live_episode_plan(sample_plan())
        session = storage.bind_episode_plan_to_session("live-a", "plan-general-panel")
        storage.upsert_tts_profile({
            "character_id": "host-a",
            "ref_audio_path": "host-a.wav",
            "prompt_text": "參考語音文字。",
        })

        captured = {}
        committed = []

        class FakeTTSProvider:
            def synthesize(self, text, profile):
                return TTSResult(ok=True, audio_bytes=f"audio:{text}".encode("utf-8"), audio_format="wav")

        class DraftMemoriaClient:
            def list_characters(self):
                return _episode_plan_characters()

            def chat_stream_sync(self, **kwargs):
                captured.update(kwargs)
                assert kwargs["session_id"] != "mem-main"
                kwargs["on_result"]({
                    "message_id": "draft-msg-1",
                    "reply": "這是預取但尚未播放的句子。",
                    "character_id": "host-a",
                    "character_name": "主持A",
                    "extracted_entities": ["預取"],
                })
                return {
                    "session_id": "mem-draft-prefetch",
                    "message_id": "draft-result-1",
                    "reply": "這是預取但尚未播放的句子。",
                    "extracted_entities": ["預取"],
                }

            def add_assistant_event(self, **kwargs):
                committed.append(kwargs)
                return {"status": "created", "message_id": len(committed)}

        manager = YouTubeBridgeManager(
            storage,
            youtube_client=LiveEndedClient(),
            memoria_client_factory=DraftMemoriaClient,
            tts_provider_factory=FakeTTSProvider,
        )
        runtime = LiveRuntime(session_id="live-a", running=True, status="running")
        manager._runtimes["live-a"] = runtime

        prefetch = await manager._send_director_turn(
            session,
            storage.get_director_state("live-a"),
            {
                "action": "continue_topic",
                "prompt": "Beat shape: draft_only.",
                "current_topic": "測試主題",
                "episode_plan": {"mode": "planned_turn", "turn_id": "seg_01_turn_01"},
            },
            prefetch_only=True,
        )

        assert prefetch["interaction"]["status"] == "prefetched"
        assert storage.get_session("live-a")["target_memoria_session_id"] == "mem-main"
        assert committed == []

        consumed = await manager._consume_prefetched_episode_turn(runtime, session, prefetch)

        assert consumed is not None
        assert committed == [{
            "session_id": "mem-main",
            "content": "這是預取但尚未播放的句子。",
            "character_id": "host-a",
            "character_name": "主持A",
            "debug_info": {
                "event_type": "youtube_live_played_commit",
                "source": "director_prefetch",
                "bridge_session_id": "live-a",
                "interaction_job_id": prefetch["interaction"]["job_id"],
                "draft_session_id": "mem-draft-prefetch",
                "presentation_message_id": "draft-msg-1",
            },
            "extracted_entities": ["預取"],
        }]
        assert storage.get_session("live-a")["target_memoria_session_id"] == "mem-main"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_director.py::test_episode_prefetch_uses_draft_session_and_commits_only_when_presented -q
```

Expected: FAIL because prefetch still uses `mem-main` and there is no commit call.

- [ ] **Step 3: Add draft session helpers**

In `YouTubeBridge/engine_director_runtime.py`, add helpers near `_session_with_memoria_result()`:

```python
    @staticmethod
    def _prefetch_draft_session_id(session_id: str, job_id: str) -> str:
        base = str(session_id or "").strip()
        suffix = str(job_id or "").strip()
        return f"{base}:prefetch:{suffix}" if base and suffix else ""

    def _session_for_prefetch_generation(
        self,
        session: dict[str, Any],
        interaction: dict[str, Any],
    ) -> dict[str, Any]:
        updated = dict(session)
        draft_session_id = self._prefetch_draft_session_id(
            str(session.get("session_id") or ""),
            str(interaction.get("job_id") or ""),
        )
        updated["target_memoria_session_id"] = draft_session_id
        return updated
```

- [ ] **Step 4: Use draft session for `prefetch_only`**

In `_send_director_turn()`, after `interaction = self.storage.create_interaction(...)` and before `target_session_id` is used by the Memoria call, set:

```python
        main_target_session_id = target_session_id
        draft_target_session_id = ""
        if prefetch_only:
            draft_target_session_id = self._prefetch_draft_session_id(session_id, interaction["job_id"])
            target_session_id = draft_target_session_id
```

Update the `chat_stream_sync()` call to use the mutable `target_session_id` variable. Update prefetch metadata:

```python
                "metadata": {
                    "result_message_id": result.get("message_id"),
                    "prefetch_ready": bool(prefetch_only),
                    "prepare_ready": bool(prepare_only),
                    "prepared_result_count": len(prepared_clean),
                    "main_memoria_session_id": main_target_session_id,
                    "draft_memoria_session_id": draft_target_session_id,
                    "played_commit_status": "pending" if prefetch_only else "",
                },
```

- [ ] **Step 5: Commit presented prefetched lines**

Add this helper near `_consume_prefetched_episode_turn()`:

```python
    def _commit_prefetched_played_results(
        self,
        session: dict[str, Any],
        interaction: dict[str, Any],
        prefetch: dict[str, Any],
        prepared_results: list[dict[str, Any]],
    ) -> None:
        metadata = interaction.get("metadata") if isinstance(interaction.get("metadata"), dict) else {}
        main_session_id = str(metadata.get("main_memoria_session_id") or session.get("target_memoria_session_id") or "")
        draft_session_id = str(metadata.get("draft_memoria_session_id") or "")
        if not main_session_id:
            return
        for prepared in prepared_results:
            message = prepared.get("message") if isinstance(prepared.get("message"), dict) else {}
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            self._memoria_client().add_assistant_event(
                session_id=main_session_id,
                content=content,
                character_id=str(message.get("character_id") or ""),
                character_name=str(message.get("character_name") or ""),
                debug_info={
                    "event_type": "youtube_live_played_commit",
                    "source": "director_prefetch",
                    "bridge_session_id": str(session.get("session_id") or ""),
                    "interaction_job_id": str(interaction.get("job_id") or ""),
                    "draft_session_id": draft_session_id,
                    "presentation_message_id": str(message.get("message_id") or ""),
                },
                extracted_entities=(prefetch.get("memoria_result") or {}).get("extracted_entities"),
            )
```

Call it in `_consume_prefetched_episode_turn()` immediately after `await self.present_prepared_stream_results(...)` returns and before the interaction is marked completed:

```python
        self._commit_prefetched_played_results(session, started, prefetch, prepared_results)
```

- [ ] **Step 6: Keep session target pinned to main**

Remove or guard this block in `_consume_prefetched_episode_turn()`:

```python
        if result_session_id and result_session_id != str(session.get("target_memoria_session_id") or ""):
            self.storage.update_session_fields(runtime.session_id, target_memoria_session_id=result_session_id)
```

Replace it with:

```python
        main_session_id = str(
            (started.get("metadata") or {}).get("main_memoria_session_id")
            or session.get("target_memoria_session_id")
            or ""
        )
        if main_session_id:
            self.storage.update_session_fields(runtime.session_id, target_memoria_session_id=main_session_id)
```

- [ ] **Step 7: Verify the prefetch commit test**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_director.py::test_episode_prefetch_uses_draft_session_and_commits_only_when_presented -q
```

Expected: PASS.

## Task 4: Do Not Commit Interrupted Or Unpresented Prefetch

**Files:**
- Modify: `YouTubeBridge/engine_director_runtime.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_director.py`

- [ ] **Step 1: Add regression test**

Append:

```python
@pytest.mark.asyncio
async def test_interrupted_prefetch_ready_items_are_not_committed(monkeypatch):
    tmp_dir = _tmp_dir()
    try:
        storage = BridgeStorage(tmp_dir / "youtube_live.db")
        storage.upsert_connector({"connector_id": "yt-main", "display_name": "YouTube Main", "enabled": True})
        storage.upsert_session({
            "session_id": "live-a",
            "connector_id": "yt-main",
            "target_memoria_session_id": "mem-main",
            "character_ids": ["host-a"],
            "presentation_enabled": True,
            "tts_enabled": True,
        })
        committed = []

        class FakeMemoriaClient:
            def list_characters(self):
                return _episode_plan_characters()

            def add_assistant_event(self, **kwargs):
                committed.append(kwargs)
                return {"status": "created"}

        manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeMemoriaClient)
        runtime = LiveRuntime(session_id="live-a", running=False, status="ended")
        interaction = storage.create_interaction({
            "session_id": "live-a",
            "source": "director_prefetch",
            "status": "interrupted",
            "content": "未播放預取",
            "metadata": {
                "main_memoria_session_id": "mem-main",
                "draft_memoria_session_id": "draft-a",
            },
        })
        storage.create_presentation_item({
            "session_id": "live-a",
            "interaction_job_id": interaction["job_id"],
            "message_id": "draft-msg-1",
            "character_id": "host-a",
            "character_name": "主持A",
            "status": "ready",
            "text": "這句不應進入主上下文。",
            "metadata": {"source": "director_prefetch"},
        })

        consumed = await manager._consume_prefetched_episode_turn(
            runtime,
            storage.get_session("live-a"),
            {
                "interaction": interaction,
                "prepared_results": [{
                    "message": {
                        "message_id": "draft-msg-1",
                        "role": "assistant",
                        "content": "這句不應進入主上下文。",
                        "character_id": "host-a",
                        "character_name": "主持A",
                    },
                    "items": storage.list_presentation_items("live-a", statuses={"ready"}),
                }],
                "memoria_result": {"session_id": "draft-a", "reply": "這句不應進入主上下文。"},
            },
        )

        assert consumed is None or consumed.get("discarded")
        assert committed == []
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 2: Run failing test**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_director.py::test_interrupted_prefetch_ready_items_are_not_committed -q
```

Expected before implementation: FAIL because `_consume_prefetched_episode_turn()` does not guard runtime/session state early enough.

- [ ] **Step 3: Add runtime/session guard**

At the top of `_consume_prefetched_episode_turn()` after extracting `job_id`:

```python
        if not runtime.running or str(runtime.status or "") in {"closing", "stopped", "ended"}:
            self.storage.update_interaction(
                job_id,
                status="interrupted",
                reason="prefetch_not_committed_session_not_running",
                completed_at=datetime.now().isoformat(),
                metadata={"discarded": True, "played_commit_status": "skipped"},
            )
            return {"interaction": interaction, "discarded": True}
```

- [ ] **Step 4: Verify regression**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_director.py::test_interrupted_prefetch_ready_items_are_not_committed -q
```

Expected: PASS.

## Task 5: Verification Suite

**Files:**
- No new files.

- [ ] **Step 1: Run focused tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_director.py tests\test_server_auth.py -q
```

Expected: PASS.

- [ ] **Step 2: Run adjacent presentation and closing tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_closing.py YouTubeBridge\tests\test_presentation_queue.py YouTubeBridge\tests\test_storage.py -q
```

Expected: PASS.

- [ ] **Step 3: Commit**

```powershell
git add api/models/requests.py api/routers/session.py YouTubeBridge/memoria_client.py YouTubeBridge/engine_director_runtime.py YouTubeBridge/storage_repositories/interactions.py YouTubeBridge/tests/test_bridge_engine_director.py tests/test_server_auth.py
git commit -m "fix: commit YouTubeBridge prefetch only after playback"
```

## Self-Review

- Spec coverage: This plan fixes generated-but-discarded plan turns leaking into future context by moving prefetch writes to draft sessions and committing only after playback.
- Placeholder scan: No TBD/TODO placeholders.
- Type consistency: `SessionAssistantEventRequest`, `add_assistant_event()`, `main_memoria_session_id`, and `draft_memoria_session_id` are defined before use.

