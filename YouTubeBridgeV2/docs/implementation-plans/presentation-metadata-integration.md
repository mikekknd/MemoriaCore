# Presentation Metadata Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire completed MemoriaCore character interactions into the Chat Display stream as display-safe `character_response` events carrying presentation metadata.

**Architecture:** Memoria response normalization preserves only display-safe speaker and presentation fields. Runtime Memoria runners persist the existing interaction summary, then build a provider-neutral `PresentationEvent` and append one live event with `public_metadata.display_event`; the display normalizer and Chat Display UI consume the existing 6A/6B display contract. This item deliberately stops before TTS queue, ack, timeout, provider delivery, or runtime phase decisions.

**Tech Stack:** Python dataclasses and typed dict-shaped payloads, existing V2 StorageManager-like append methods, existing `YouTubeBridgeV2.presentation.tts`, existing `YouTubeBridgeV2.display.events`, pytest.

---

## Scope Boundary

- Implement only roadmap item 6C: `presentation metadata integration`.
- Do not implement 6D `TTS queue/ack/timeout behavior`.
- Do not implement 6E end-to-end test matrix beyond focused presentation/display integration coverage.
- Do not edit `YouTubeBridgeV2/docs/roadmap.md` checkbox state.
- Do not import legacy `YouTubeBridge/` modules.
- Do not add direct SQLite access inside `YouTubeBridgeV2/`.

## File Structure

- Modify `YouTubeBridgeV2/adapters/memoria.py`
  - Preserve display-safe optional fields from Memoria responses: `speaker_name`, `role_label`, `voice_id`, and `presentation`.
  - Continue redacting private/raw/operator fields.
- Modify `YouTubeBridgeV2/presentation/tts.py`
  - Include `phase` on the generated character display event so Chat Display can render the phase badge.
- Modify `YouTubeBridgeV2/runtime/memoria_runners.py`
  - Build completed interaction records with enough public summary to support presentation display.
  - Append display live events through `append_v2_live_event` when the storage boundary provides it.
  - Keep presentation events as consumers; do not let them affect phase decisions.
- Modify `tests/youtubebridge_v2/test_memoria_adapter.py`
  - Add failing test for preserving safe presentation metadata during normalization.
- Modify `tests/youtubebridge_v2/test_presentation_tts.py`
  - Update/add coverage for `phase` in the generated display event.
- Modify `tests/youtubebridge_v2/test_runtime_memoria_runners.py`
  - Add failing tests proving planned show and aftertalk interactions append display-safe presentation live events.
- Modify `tests/youtubebridge_v2/test_display_event_contract.py`
  - Add coverage that presentation live events normalize to `character_response` display events with public payload only.
- Modify docs:
  - `YouTubeBridgeV2/docs/modules/presentation-tts.md`
  - `YouTubeBridgeV2/docs/modules/chat-display-ui.md`
  - `YouTubeBridgeV2/docs/architecture-index.md`
  - `YouTubeBridgeV2/docs/api-reference-index.md`

---

### Task 1: Preserve Display-Safe Presentation Fields From Memoria Responses

**Files:**
- Modify: `tests/youtubebridge_v2/test_memoria_adapter.py`
- Modify: `YouTubeBridgeV2/adapters/memoria.py`

- [ ] **Step 1: Write the failing normalization test**

Add this test near `test_memoria_response_is_normalized_with_session_id` in `tests/youtubebridge_v2/test_memoria_adapter.py`:

```python
def test_memoria_response_preserves_display_safe_presentation_metadata():
    response = normalize_memoria_response(
        {
            "session_id": "memoria-session-2",
            "message_id": "m1",
            "character_id": "host",
            "character_name": "Luna",
            "role_label": "Host",
            "voice_id": "voice-luna",
            "reply": "Presentation-ready line",
            "presentation": {
                "voice_state": "speaking",
                "visual_state": "focus",
                "subtitle": "Presentation-ready line",
                "raw_memoriacore_payload": {"token": "must not leak"},
                "operator_only_metadata": {"manual_close": True},
            },
            "raw_payload": {"secret": "must not leak"},
        },
        _correlation(),
    )

    assert isinstance(response, NormalizedMemoriaResponse)
    assert response.messages == (
        {
            "message_id": "m1",
            "speaker_id": "host",
            "content": "Presentation-ready line",
            "speaker_name": "Luna",
            "role_label": "Host",
            "voice_id": "voice-luna",
            "presentation": {
                "voice_state": "speaking",
                "visual_state": "focus",
                "subtitle": "Presentation-ready line",
            },
        },
    )
    _assert_no_private_payload(response.messages)
```

- [ ] **Step 2: Run the new test and verify red**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_response_preserves_display_safe_presentation_metadata -q
```

Expected: FAIL because `_normalize_message()` currently drops `speaker_name`, `role_label`, `voice_id`, and `presentation`.

- [ ] **Step 3: Implement display-safe optional field preservation**

In `YouTubeBridgeV2/adapters/memoria.py`, replace `_normalize_message()` with:

```python
def _normalize_message(raw_message: dict[str, object]) -> dict[str, object] | None:
    speaker_id = raw_message.get("speaker_id") or raw_message.get("character_id")
    content = raw_message.get("content") or raw_message.get("text") or raw_message.get("reply")
    if not speaker_id or content is None:
        return None

    normalized: dict[str, object] = {
        "message_id": str(raw_message.get("message_id", raw_message.get("id", ""))),
        "speaker_id": str(speaker_id),
        "content": str(content),
    }
    optional_fields = {
        "speaker_name": raw_message.get("speaker_name")
        or raw_message.get("character_name")
        or raw_message.get("name"),
        "role_label": raw_message.get("role_label") or raw_message.get("role"),
        "voice_id": raw_message.get("voice_id"),
    }
    for key, value in optional_fields.items():
        text = _optional_string(value)
        if text:
            normalized[key] = text

    presentation = _display_safe_presentation_metadata(
        raw_message.get("presentation") or raw_message.get("presentation_metadata")
    )
    if presentation:
        normalized["presentation"] = presentation

    return _redact_public_value(normalized)
```

Add this helper below `_normalize_message()`:

```python
def _display_safe_presentation_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    allowed_keys = {
        "voice_state",
        "visual_state",
        "subtitle",
    }
    metadata = {
        key: value[key]
        for key in allowed_keys
        if key in value and _optional_string(value.get(key))
    }
    return _redact_public_value(metadata)
```

- [ ] **Step 4: Verify the normalization test passes**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_adapter.py::test_memoria_response_preserves_display_safe_presentation_metadata -q
```

Expected: PASS.

---

### Task 2: Expose Phase on Presentation Display Events

**Files:**
- Modify: `tests/youtubebridge_v2/test_presentation_tts.py`
- Modify: `YouTubeBridgeV2/presentation/tts.py`

- [ ] **Step 1: Add the failing assertion**

In `tests/youtubebridge_v2/test_presentation_tts.py`, update `test_completed_character_response_builds_presentation_event()` so the expected `event.display_event` includes phase:

```python
    assert event.display_event == {
        "event_type": "character_response",
        "event_id": "response-1",
        "session_id": "session-1",
        "character_name": "Luna",
        "role_label": "Host",
        "response_text": "Welcome back to the planned show.",
        "phase": "planned_show",
        "presentation": {
            "voice_state": "ready",
            "visual_state": "focus",
            "phase": "planned_show",
            "role_label": "Host",
            "subtitle": "Welcome back",
            "public_payload": {"correlation_id": "corr-1"},
        },
    }
```

- [ ] **Step 2: Run the focused test and verify red**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_presentation_tts.py::test_completed_character_response_builds_presentation_event -q
```

Expected: FAIL because `build_presentation_event()` does not put `phase` at the display-event top level.

- [ ] **Step 3: Add top-level phase to the presentation display event**

In `YouTubeBridgeV2/presentation/tts.py`, update the `display_event` dict inside `build_presentation_event()`:

```python
    display_event = {
        "event_type": "character_response",
        "event_id": event_id,
        "session_id": session_id,
        "character_name": character_name,
        "role_label": role_label,
        "response_text": response_text,
        "phase": metadata.phase,
        "presentation": asdict(metadata),
    }
```

- [ ] **Step 4: Verify presentation tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_presentation_tts.py -q
```

Expected: all presentation/TTS tests pass. This does not wire TTS queue behavior into runtime; it only keeps metadata aligned with Chat Display.

---

### Task 3: Append Presentation Display Events From Runtime Interactions

**Files:**
- Modify: `tests/youtubebridge_v2/test_runtime_memoria_runners.py`
- Modify: `YouTubeBridgeV2/runtime/memoria_runners.py`

- [ ] **Step 1: Write the failing planned-show integration test**

Add this import in `tests/youtubebridge_v2/test_runtime_memoria_runners.py`:

```python
from YouTubeBridgeV2.display.events import normalize_display_event
```

Add this test after `test_planned_show_runner_sends_next_turn_and_advances_plan_state()`:

```python
def test_planned_show_runner_appends_presentation_display_event():
    storage = InMemoryV2StorageManager()
    port = _create_bound_session(storage)
    transport = FakeMemoriaTransport(
        {
            "session_id": "memoria-1",
            "message_id": "msg-1",
            "character_id": "host",
            "character_name": "Luna",
            "role_label": "Host",
            "voice_id": "voice-luna",
            "reply": "Planned response",
            "presentation": {
                "voice_state": "speaking",
                "visual_state": "focus",
                "subtitle": "Planned response",
                "raw_payload": {"token": "must not leak"},
            },
            "raw_payload": {"token": "must not leak"},
        }
    )
    runner = MemoriaPlannedShowRunner(storage, transport)

    result = runner.run(
        command=_command("cmd-planned-display"),
        snapshot=port.read_snapshot("session-runner"),
        transition=_transition(LiveSessionPhase.PLANNED_SHOW, action="run_planned_show"),
        now=NOW,
    )

    assert result.status == "ok"
    assert len(storage.live_events) == 1
    event = storage.live_events[0]
    assert event["event_type"] == "presentation_character_response"
    assert event["created_at"] == NOW
    assert event["public_metadata"]["interaction_id"].endswith(":msg-1")
    display_event = normalize_display_event(event)
    assert display_event["event_type"] == "character_response"
    assert display_event["source_event_type"] == "presentation_character_response"
    assert display_event["public_payload"] == {
        "character_name": "Luna",
        "role_label": "Host",
        "response_text": "Planned response",
        "phase": "planned_show",
        "presentation": {
            "voice_state": "speaking",
            "visual_state": "focus",
            "phase": "planned_show",
            "role_label": "Host",
            "subtitle": "Planned response",
            "public_payload": {
                "correlation_id": "runtime-cmd-planned-display",
                "request_id": "cmd-planned-display",
            },
        },
    }
    _assert_no_private_payload(storage.live_events)
```

- [ ] **Step 2: Write the failing aftertalk ordering/multi-message test**

Add this test after `test_aftertalk_runner_builds_group_chat_request_and_appends_interactions()`:

```python
def test_aftertalk_runner_appends_one_presentation_display_event_per_message():
    storage = InMemoryV2StorageManager()
    port = _create_bound_session(storage)
    storage.update_v2_session("session-runner", {"plan_completed": True})
    transport = FakeMemoriaTransport(
        {
            "session_id": "memoria-2",
            "turns": [
                {
                    "message_id": "a1",
                    "character_id": "host",
                    "character_name": "Luna",
                    "role_label": "Host",
                    "reply": "Aftertalk 1",
                    "presentation": {"voice_state": "speaking"},
                },
                {
                    "message_id": "a2",
                    "character_id": "cohost",
                    "character_name": "Mika",
                    "role_label": "Cohost",
                    "reply": "Aftertalk 2",
                    "presentation": {"visual_state": "react"},
                },
            ],
            "raw_memoriacore_payload": {"token": "must not leak"},
        }
    )
    runner = MemoriaAftertalkRunner(storage, transport)

    result = runner.run(
        command=_command("cmd-aftertalk-display"),
        snapshot=port.read_snapshot("session-runner"),
        transition=_transition(LiveSessionPhase.AFTERTALK, action="continue_aftertalk"),
        now=NOW,
    )

    assert result.status == "ok"
    assert len(storage.interactions) == 2
    assert len(storage.live_events) == 2
    display_events = [normalize_display_event(event) for event in storage.live_events]
    assert [event["public_payload"]["response_text"] for event in display_events] == [
        "Aftertalk 1",
        "Aftertalk 2",
    ]
    assert [event["public_payload"]["phase"] for event in display_events] == [
        "aftertalk",
        "aftertalk",
    ]
    assert display_events[0]["public_payload"]["presentation"]["voice_state"] == "speaking"
    assert display_events[1]["public_payload"]["presentation"]["visual_state"] == "react"
    _assert_no_private_payload(storage.live_events)
```

- [ ] **Step 3: Run the new runner tests and verify red**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_memoria_runners.py::test_planned_show_runner_appends_presentation_display_event tests\youtubebridge_v2\test_runtime_memoria_runners.py::test_aftertalk_runner_appends_one_presentation_display_event_per_message -q
```

Expected: FAIL because runtime runners append interactions only; no presentation live event is appended.

- [ ] **Step 4: Import the presentation builder**

In `YouTubeBridgeV2/runtime/memoria_runners.py`, add:

```python
from YouTubeBridgeV2.presentation.tts import build_presentation_event
```

- [ ] **Step 5: Replace `_append_interactions()` with interaction/event wiring**

Replace `_append_interactions()` with:

```python
def _append_interactions(
    storage_manager: object,
    *,
    session_id: str,
    phase: LiveSessionPhase,
    command_id: str,
    normalized: NormalizedMemoriaResponse,
    now: datetime,
) -> None:
    if not hasattr(storage_manager, "append_v2_interaction"):
        raise RuntimeError("storage manager missing append_v2_interaction")
    for index, message in enumerate(normalized.messages):
        message_id = str(message.get("message_id") or index + 1)
        interaction = _interaction_record_from_message(
            session_id=session_id,
            phase=phase,
            command_id=command_id,
            message_id=message_id,
            message=message,
            normalized=normalized,
            now=now,
        )
        stored = storage_manager.append_v2_interaction(
            session_id,
            _redact_public_value(interaction),
        )
        if isinstance(stored, dict):
            presentation_source = {**interaction, **stored}
        else:
            presentation_source = interaction
        _append_presentation_display_event(
            storage_manager,
            session_id=session_id,
            interaction=presentation_source,
            now=now,
        )
```

Add these helpers below `_append_interactions()`:

```python
def _interaction_record_from_message(
    *,
    session_id: str,
    phase: LiveSessionPhase,
    command_id: str,
    message_id: str,
    message: dict[str, object],
    normalized: NormalizedMemoriaResponse,
    now: datetime,
) -> dict[str, object]:
    speaker_id = str(message.get("speaker_id", ""))
    content = str(message.get("content", ""))
    speaker_name = _optional_string(
        message.get("speaker_name") or message.get("character_name")
    )
    role_label = _optional_string(message.get("role_label") or message.get("role"))
    voice_id = _optional_string(message.get("voice_id"))
    presentation = _object_to_dict(
        message.get("presentation") or message.get("presentation_metadata") or {}
    )
    interaction_id = f"{session_id}:{command_id}:{phase.value}:{message_id}"
    public_summary = {
        "message_id": message_id,
        "speaker_id": speaker_id,
        "content": content,
        "mode": normalized.mode,
        "memoria_session_id": normalized.memoria_session_id,
    }
    if speaker_name:
        public_summary["speaker_name"] = speaker_name
    if role_label:
        public_summary["role_label"] = role_label
    if voice_id:
        public_summary["voice_id"] = voice_id
    if presentation:
        public_summary["presentation"] = presentation
    metadata = {
        "correlation_id": normalized.correlation.correlation_id,
        "request_id": normalized.correlation.request_id,
        "trace_id": normalized.correlation.trace_id,
    }
    return _redact_public_value(
        {
            "interaction_id": interaction_id,
            "event_id": f"presentation:{interaction_id}",
            "status": "completed",
            "session_id": session_id,
            "phase": phase.value,
            "speaker_id": speaker_id,
            "character_id": speaker_id,
            "character_name": speaker_name or speaker_id,
            "role_label": role_label or _default_role_label(phase),
            "response_text": content,
            "completed_at": now,
            "voice_id": voice_id or "",
            "presentation": presentation,
            "metadata": {
                key: value
                for key, value in metadata.items()
                if value is not None
            },
            "public_content_summary": public_summary,
            "correlation_id": normalized.correlation.correlation_id,
            "created_at": now,
        }
    )
```

```python
def _append_presentation_display_event(
    storage_manager: object,
    *,
    session_id: str,
    interaction: dict[str, object],
    now: datetime,
) -> None:
    if not hasattr(storage_manager, "append_v2_live_event"):
        return
    event = build_presentation_event(interaction)
    if not event.should_present:
        return
    storage_manager.append_v2_live_event(
        session_id,
        _redact_public_value(
            {
                "event_id": event.event_id,
                "event_type": "presentation_character_response",
                "public_metadata": {
                    "interaction_id": event.interaction_id,
                    "display_event": event.display_event,
                    "presentation": asdict(event.display_metadata),
                    "public_payload": event.public_payload,
                },
                "created_at": now,
            }
        ),
    )
```

```python
def _default_role_label(phase: LiveSessionPhase) -> str:
    if phase is LiveSessionPhase.PLANNED_SHOW:
        return "Planned Show"
    if phase is LiveSessionPhase.AFTERTALK:
        return "Aftertalk"
    if phase is LiveSessionPhase.CLOSING:
        return "Closing"
    return "Character"
```

- [ ] **Step 6: Verify the runner tests pass**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_runtime_memoria_runners.py::test_planned_show_runner_appends_presentation_display_event tests\youtubebridge_v2\test_runtime_memoria_runners.py::test_aftertalk_runner_appends_one_presentation_display_event_per_message -q
```

Expected: PASS.

---

### Task 4: Lock the Display Event Contract for Presentation Live Events

**Files:**
- Modify: `tests/youtubebridge_v2/test_display_event_contract.py`

- [ ] **Step 1: Add display normalizer coverage**

Add this test after `test_normalize_runtime_event_becomes_system_state()`:

```python
def test_normalize_presentation_character_response_event():
    event = normalize_display_event(
        {
            "event_id": "presentation:interaction-1",
            "event_type": "presentation_character_response",
            "created_at": NOW,
            "public_metadata": {
                "interaction_id": "interaction-1",
                "display_event": {
                    "event_id": "presentation:interaction-1",
                    "event_type": "character_response",
                    "session_id": "session-1",
                    "character_name": "Luna",
                    "role_label": "Host",
                    "response_text": "Hello display",
                    "phase": "planned_show",
                    "presentation": {
                        "voice_state": "speaking",
                        "visual_state": "focus",
                        "raw_payload": {"token": "must not leak"},
                    },
                },
                "operator_only_metadata": {"manual_close": True},
            },
        }
    )

    assert event == {
        "display_contract_version": "v1",
        "event_id": "presentation:interaction-1",
        "event_type": "character_response",
        "source_event_type": "presentation_character_response",
        "created_at": NOW.isoformat(),
        "public_payload": {
            "character_name": "Luna",
            "role_label": "Host",
            "response_text": "Hello display",
            "phase": "planned_show",
            "presentation": {
                "voice_state": "speaking",
                "visual_state": "focus",
            },
        },
    }
    _assert_no_private_payload(event)
```

- [ ] **Step 2: Run the display contract test**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_display_event_contract.py::test_normalize_presentation_character_response_event -q
```

Expected: PASS if Tasks 2 and 3 keep display event shape aligned. If this fails, fix the event shape in `runtime/memoria_runners.py` or `display/events.py`; do not broaden 6C into UI redesign.

---

### Task 5: Update Module and API Documentation

**Files:**
- Modify: `YouTubeBridgeV2/docs/modules/presentation-tts.md`
- Modify: `YouTubeBridgeV2/docs/modules/chat-display-ui.md`
- Modify: `YouTubeBridgeV2/docs/architecture-index.md`
- Modify: `YouTubeBridgeV2/docs/api-reference-index.md`

- [ ] **Step 1: Update presentation module wording**

In `YouTubeBridgeV2/docs/modules/presentation-tts.md`, update the Public Entrypoints section with this paragraph:

```markdown
Wave 6C wires completed Memoria runner interactions into display-safe
presentation live events. Runtime runners call `build_presentation_event(...)`
after interaction persistence and append `presentation_character_response`
events containing `public_metadata.display_event`; presentation remains an event
consumer and does not request phase transitions.
```

- [ ] **Step 2: Update Chat Display module wording**

In `YouTubeBridgeV2/docs/modules/chat-display-ui.md`, add this paragraph after the display contract paragraph:

```markdown
Wave 6C adds `presentation_character_response` as a storage/source event name.
The display stream normalizes it to `event_type: "character_response"` with
`public_payload.presentation` carrying only display-safe voice/visual/subtitle
metadata. The UI continues to render this through the existing character row and
does not call runtime control APIs.
```

- [ ] **Step 3: Update architecture index**

In `YouTubeBridgeV2/docs/architecture-index.md`, add a Wave 6C note near the Wave 6 display/checklist notes:

```markdown
- [x] Wave 6C presentation metadata integration：Memoria runner completed
  interactions now append display-safe `presentation_character_response` live
  events. Display stream normalizes them to `character_response`; TTS queue,
  ack, timeout, and provider delivery remain Wave 6D scope.
```

- [ ] **Step 4: Update API reference index**

In `YouTubeBridgeV2/docs/api-reference-index.md`, extend the presentation/TTS entry with this behavior note:

```markdown
Wave 6C integration behavior:

- Runtime Memoria runners call `build_presentation_event(interaction)` for
  completed character interactions.
- When `event.should_present` is true, runners append a live event with
  `event_type: "presentation_character_response"` and
  `public_metadata.display_event.event_type: "character_response"`.
- Presentation live events are display-safe projections only; they do not
  enqueue TTS, acknowledge delivery, timeout delivery, or change runtime phase.
```

- [ ] **Step 5: Verify documentation references**

Run:

```powershell
rg -n "presentation_character_response|Wave 6C|build_presentation_event" YouTubeBridgeV2\docs
```

Expected: hits in the four docs files above and this implementation plan.

---

### Task 6: Full Verification and Commit

**Files:**
- Verify all modified files from Tasks 1-5.

- [ ] **Step 1: Run focused tests**

Run:

```powershell
python -m pytest tests\youtubebridge_v2\test_memoria_adapter.py tests\youtubebridge_v2\test_runtime_memoria_runners.py tests\youtubebridge_v2\test_display_event_contract.py tests\youtubebridge_v2\test_chat_display_ui.py tests\youtubebridge_v2\test_presentation_tts.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run full V2 suite**

Run:

```powershell
python -m pytest tests\youtubebridge_v2 -q
```

Expected: full V2 suite passes. Existing opt-in browser smoke remains skipped unless the environment enables it.

- [ ] **Step 3: Check whitespace**

Run:

```powershell
git diff --check
```

Expected: exit code 0. Existing LF/CRLF warnings from Git are acceptable if no whitespace errors are reported.

- [ ] **Step 4: Inspect scope**

Run:

```powershell
git status --short
git diff --stat
```

Expected: changed files are limited to 6C plan, adapter/runtime/presentation/display contract tests, and docs listed above.

- [ ] **Step 5: Commit**

Run:

```powershell
git add YouTubeBridgeV2\adapters\memoria.py YouTubeBridgeV2\presentation\tts.py YouTubeBridgeV2\runtime\memoria_runners.py tests\youtubebridge_v2\test_memoria_adapter.py tests\youtubebridge_v2\test_presentation_tts.py tests\youtubebridge_v2\test_runtime_memoria_runners.py tests\youtubebridge_v2\test_display_event_contract.py YouTubeBridgeV2\docs\modules\presentation-tts.md YouTubeBridgeV2\docs\modules\chat-display-ui.md YouTubeBridgeV2\docs\architecture-index.md YouTubeBridgeV2\docs\api-reference-index.md YouTubeBridgeV2\docs\implementation-plans\presentation-metadata-integration.md
git commit -m "feat: integrate presentation metadata display events"
```

Expected: commit succeeds on `codex/youtubebridge-v2-aftertalk`.

---

## Self-Review

- Spec coverage: This plan implements only roadmap 6C by connecting completed interaction output to display-safe presentation metadata events. It intentionally leaves TTS queue/ack/timeout/provider behavior to 6D and end-to-end matrix expansion to 6E.
- Placeholder scan: No `TBD`, `TODO`, or open-ended "handle edge cases" placeholders remain; each code-changing step includes concrete snippets.
- Type consistency: Optional Memoria response fields flow as `speaker_name`, `role_label`, `voice_id`, and `presentation`; runtime converts them into `build_presentation_event()` input; display stream receives `presentation_character_response` source events normalized to `character_response`.
