# YouTubeBridge Free Talk Stage 4 Low Signal Closing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make free talk closing drain all eligible pending comments safely while filtering low-signal spam and protecting finalization from large chat backlogs.

**Architecture:** Add a deterministic low-signal classifier that runs before LLM batching, add event repository helpers to mark skipped events, and replace Stage 3's immediate free-talk closing completion with a protected batch loop. Batch size is computed from total eligible pending count divided by a target batch count, then clamped by configured minimum and maximum values.

**Tech Stack:** Pure Python filtering helpers, BridgeStorage event repository, YouTubeBridge injection path, phase pipeline manager, pytest, browser E2E.

---

## File Structure

- Create `YouTubeBridge/free_talk_low_signal.py`: deterministic low-signal comment classifier and batch sizing helper.
- Modify `YouTubeBridge/models.py`: add free talk closing batch defaults.
- Modify `YouTubeBridge/storage_schema.py`: add session fields for closing batch settings.
- Modify `YouTubeBridge/storage_mappers.py`: map closing batch settings.
- Modify `YouTubeBridge/storage_repositories/sessions.py`: persist closing batch settings.
- Modify `YouTubeBridge/storage_repositories/events.py`: add helpers to mark low-signal and closing-timeout skipped events.
- Modify `YouTubeBridge/engine_phase_pipeline.py`: drain free talk closing pending comments before free talk summary.
- Modify `YouTubeBridge/static/studio.html`, `YouTubeBridge/static/ui/studio.js`: expose closing batch settings in Studio live settings.
- Create `YouTubeBridge/tests/test_free_talk_low_signal.py`: low-signal and batch sizing tests.
- Modify `YouTubeBridge/tests/test_storage.py`: event skip-state tests.
- Modify `YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py`: protected closing tests.
- Modify `YouTubeBridge/tests/test_studio_ui.py`: UI source tests.

---

### Task 1: Low-Signal Filter and Batch Sizing

**Files:**
- Create: `YouTubeBridge/free_talk_low_signal.py`
- Test: `YouTubeBridge/tests/test_free_talk_low_signal.py`

- [ ] **Step 1: Write failing low-signal tests**

Create `YouTubeBridge/tests/test_free_talk_low_signal.py`:

```python
from free_talk_low_signal import classify_low_signal_comment, free_talk_closing_batch_size


def test_classify_low_signal_comment_filters_repeated_short_tokens_and_emoji():
    assert classify_low_signal_comment("666666") == "repeated_short_token"
    assert classify_low_signal_comment("😂😂😂😂😂") == "emoji_or_symbol_only"
    assert classify_low_signal_comment("wwwwwwww") == "repeated_short_token"
    assert classify_low_signal_comment("這個工具適合團隊共用嗎？") == ""


def test_free_talk_closing_batch_size_uses_target_batches_with_clamp():
    assert free_talk_closing_batch_size(20, target_batches=10, min_batch_size=5, max_batch_size=30) == 5
    assert free_talk_closing_batch_size(80, target_batches=10, min_batch_size=5, max_batch_size=30) == 8
    assert free_talk_closing_batch_size(500, target_batches=10, min_batch_size=5, max_batch_size=30) == 30
```

- [ ] **Step 2: Run low-signal tests and verify they fail**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_free_talk_low_signal.py -q
```

Expected: FAIL because `free_talk_low_signal` does not exist.

- [ ] **Step 3: Implement filter and batch helper**

Create `YouTubeBridge/free_talk_low_signal.py`:

```python
"""Deterministic low-signal filtering for free talk closing."""
from __future__ import annotations

import math
import re


EMOJI_SYMBOL_RE = re.compile(r"^[\W_]+$", re.UNICODE)


def classify_low_signal_comment(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return "empty"
    compact = re.sub(r"\s+", "", value)
    if len(compact) <= 1:
        return "too_short"
    if EMOJI_SYMBOL_RE.match(compact):
        return "emoji_or_symbol_only"
    unique_chars = set(compact)
    if len(compact) >= 4 and len(unique_chars) <= 2:
        return "repeated_short_token"
    if len(compact) <= 3 and not re.search(r"[\u4e00-\u9fffA-Za-z]", compact):
        return "too_low_information"
    return ""


def free_talk_closing_batch_size(
    eligible_count: int,
    *,
    target_batches: int,
    min_batch_size: int,
    max_batch_size: int,
) -> int:
    count = max(0, int(eligible_count or 0))
    if count <= 0:
        return max(1, int(min_batch_size or 1))
    target = max(1, int(target_batches or 1))
    minimum = max(1, int(min_batch_size or 1))
    maximum = max(minimum, int(max_batch_size or minimum))
    return max(minimum, min(math.ceil(count / target), maximum))
```

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_free_talk_low_signal.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```powershell
git add YouTubeBridge/free_talk_low_signal.py YouTubeBridge/tests/test_free_talk_low_signal.py
git commit -m "feat(youtube-bridge): filter low signal free talk comments"
```

---

### Task 2: Closing Batch Session Settings

**Files:**
- Modify: `YouTubeBridge/models.py`
- Modify: `YouTubeBridge/storage_schema.py`
- Modify: `YouTubeBridge/storage_mappers.py`
- Modify: `YouTubeBridge/storage_repositories/sessions.py`
- Modify: `YouTubeBridge/static/studio.html`
- Modify: `YouTubeBridge/static/ui/studio.js`
- Test: `YouTubeBridge/tests/test_storage.py`
- Test: `YouTubeBridge/tests/test_studio_ui.py`

- [ ] **Step 1: Write failing tests for settings persistence and Studio payload**

Append to `YouTubeBridge/tests/test_storage.py`:

```python
def test_session_persists_free_talk_closing_batch_settings(tmp_path):
    storage = BridgeStorage(tmp_path / "youtube_live.db")
    storage.upsert_connector({"connector_id": "youtube-main", "display_name": "YT", "api_key": "", "enabled": True})
    saved = storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "youtube-main",
        "display_name": "Closing",
        "free_talk_closing_target_batches": 10,
        "free_talk_closing_min_batch_size": 5,
        "free_talk_closing_max_batch_size": 30,
        "free_talk_closing_time_limit_seconds": 300,
    })

    assert saved["free_talk_closing_target_batches"] == 10
    assert saved["free_talk_closing_min_batch_size"] == 5
    assert saved["free_talk_closing_max_batch_size"] == 30
    assert saved["free_talk_closing_time_limit_seconds"] == 300
```

Append to `YouTubeBridge/tests/test_studio_ui.py`:

```python
def test_studio_free_talk_closing_batch_settings_are_in_payload():
    studio_html = STUDIO_HTML.read_text(encoding="utf-8")
    studio_js = STUDIO_JS.read_text(encoding="utf-8")

    assert "freeTalkClosingTargetBatches" in studio_html
    assert "freeTalkClosingMinBatchSize" in studio_html
    assert "freeTalkClosingMaxBatchSize" in studio_html
    assert "freeTalkClosingTimeLimitSeconds" in studio_html
    assert "free_talk_closing_target_batches" in studio_js
    assert "free_talk_closing_min_batch_size" in studio_js
    assert "free_talk_closing_max_batch_size" in studio_js
    assert "free_talk_closing_time_limit_seconds" in studio_js
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_storage.py::test_session_persists_free_talk_closing_batch_settings YouTubeBridge/tests/test_studio_ui.py::test_studio_free_talk_closing_batch_settings_are_in_payload -q
```

Expected: FAIL because fields and UI are missing.

- [ ] **Step 3: Add model, storage, and UI fields**

Add to `LiveSessionConfig` and `StudioLiveDefaults` in `YouTubeBridge/models.py`:

```python
    free_talk_closing_target_batches: int = Field(10, ge=1, le=50)
    free_talk_closing_min_batch_size: int = Field(5, ge=1, le=100)
    free_talk_closing_max_batch_size: int = Field(30, ge=1, le=200)
    free_talk_closing_time_limit_seconds: int = Field(300, ge=30, le=3600)
```

Add columns to `live_sessions` and `LIVE_SESSION_COLUMNS` in `storage_schema.py`:

```sql
            free_talk_closing_target_batches INTEGER NOT NULL DEFAULT 10,
            free_talk_closing_min_batch_size INTEGER NOT NULL DEFAULT 5,
            free_talk_closing_max_batch_size INTEGER NOT NULL DEFAULT 30,
            free_talk_closing_time_limit_seconds INTEGER NOT NULL DEFAULT 300,
```

Map fields in `storage_mappers.py`:

```python
        "free_talk_closing_target_batches": int(row_value(row, "free_talk_closing_target_batches", 10)),
        "free_talk_closing_min_batch_size": int(row_value(row, "free_talk_closing_min_batch_size", 5)),
        "free_talk_closing_max_batch_size": int(row_value(row, "free_talk_closing_max_batch_size", 30)),
        "free_talk_closing_time_limit_seconds": int(row_value(row, "free_talk_closing_time_limit_seconds", 300)),
```

Persist in `storage_repositories/sessions.py`:

```python
                "free_talk_closing_target_batches": max(1, min(self._int_or_default(config.get("free_talk_closing_target_batches", 10), 10), 50)),
                "free_talk_closing_min_batch_size": max(1, min(self._int_or_default(config.get("free_talk_closing_min_batch_size", 5), 5), 100)),
                "free_talk_closing_max_batch_size": max(1, min(self._int_or_default(config.get("free_talk_closing_max_batch_size", 30), 30), 200)),
                "free_talk_closing_time_limit_seconds": max(30, min(self._int_or_default(config.get("free_talk_closing_time_limit_seconds", 300), 300), 3600)),
```

Normalize max:

```python
            if row_data["free_talk_closing_min_batch_size"] > row_data["free_talk_closing_max_batch_size"]:
                row_data["free_talk_closing_max_batch_size"] = row_data["free_talk_closing_min_batch_size"]
```

Add Studio inputs in the free talk section:

```html
<label class="field-block">
  <span>收尾目標批次</span>
  <input id="freeTalkClosingTargetBatches" type="number" min="1" max="50" value="10">
</label>
<label class="field-block">
  <span>收尾每批最少留言</span>
  <input id="freeTalkClosingMinBatchSize" type="number" min="1" max="100" value="5">
</label>
<label class="field-block">
  <span>收尾每批最多留言</span>
  <input id="freeTalkClosingMaxBatchSize" type="number" min="1" max="200" value="30">
</label>
<label class="field-block">
  <span>收尾保護秒數</span>
  <input id="freeTalkClosingTimeLimitSeconds" type="number" min="30" max="3600" value="300">
</label>
```

Update Studio collection and payload:

```js
free_talk_closing_target_batches: readPositiveNumber($("freeTalkClosingTargetBatches"), 10),
free_talk_closing_min_batch_size: readPositiveNumber($("freeTalkClosingMinBatchSize"), 5),
free_talk_closing_max_batch_size: readPositiveNumber($("freeTalkClosingMaxBatchSize"), 30),
free_talk_closing_time_limit_seconds: readPositiveNumber($("freeTalkClosingTimeLimitSeconds"), 300),
```

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_storage.py::test_session_persists_free_talk_closing_batch_settings YouTubeBridge/tests/test_studio_ui.py::test_studio_free_talk_closing_batch_settings_are_in_payload -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add YouTubeBridge/models.py YouTubeBridge/storage_schema.py YouTubeBridge/storage_mappers.py YouTubeBridge/storage_repositories/sessions.py YouTubeBridge/static/studio.html YouTubeBridge/static/ui/studio.js YouTubeBridge/tests/test_storage.py YouTubeBridge/tests/test_studio_ui.py
git commit -m "feat(youtube-bridge): configure free talk closing batches"
```

---

### Task 3: Event Skip-State Helpers

**Files:**
- Modify: `YouTubeBridge/storage_repositories/events.py`
- Test: `YouTubeBridge/tests/test_storage.py`

- [ ] **Step 1: Write failing event skip-state test**

Append to `YouTubeBridge/tests/test_storage.py`:

```python
def test_mark_events_low_signal_skipped_records_reason(tmp_path):
    storage = BridgeStorage(tmp_path / "youtube_live.db")
    storage.upsert_connector({"connector_id": "youtube-main", "display_name": "YT", "api_key": "", "enabled": True})
    storage.upsert_session({"session_id": "live-a", "connector_id": "youtube-main", "display_name": "Events"})
    event = storage.save_event({
        "bridge_session_id": "live-a",
        "connector_id": "youtube-main",
        "youtube_message_id": "msg-1",
        "message_type": "textMessageEvent",
        "author_channel_id": "u1",
        "author_display_name": "觀眾",
        "message_text": "666666",
        "published_at": "2026-05-15T10:00:00",
        "received_at": "2026-05-15T10:00:00",
        "status": "active",
    })

    updated = storage.mark_events_low_signal_skipped("live-a", {event["id"]: "repeated_short_token"})

    assert updated == 1
    refreshed = storage.list_events("live-a", include_inactive=True)[0]
    assert refreshed["status"] == "low_signal_skipped"
    assert refreshed["metadata"]["low_signal_reason"] == "repeated_short_token"
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_storage.py::test_mark_events_low_signal_skipped_records_reason -q
```

Expected: FAIL because the helper is missing or `list_events` cannot include inactive rows.

- [ ] **Step 3: Add repository helper**

Modify `YouTubeBridge/storage_repositories/events.py`:

```python
    def mark_events_low_signal_skipped(self, session_id: str, reasons_by_event_id: dict[int, str]) -> int:
        if not reasons_by_event_id:
            return 0
        now = datetime.now().isoformat()
        updated = 0
        with self._lock, self._connect() as conn:
            for event_id, reason in reasons_by_event_id.items():
                row = conn.execute(
                    "SELECT * FROM live_events WHERE id = ? AND bridge_session_id = ?",
                    (int(event_id), session_id),
                ).fetchone()
                if not row:
                    continue
                metadata = self._json_load(row["metadata_json"], {})
                metadata["low_signal_reason"] = str(reason)[:120]
                metadata["low_signal_skipped_at"] = now
                conn.execute(
                    """
                    UPDATE live_events
                    SET status = 'low_signal_skipped',
                        metadata_json = ?,
                        updated_at = ?
                    WHERE id = ? AND bridge_session_id = ?
                    """,
                    (self._json_dump(metadata), now, int(event_id), session_id),
                )
                updated += 1
            conn.commit()
        return updated
```

If `list_events` lacks `include_inactive`, add an optional argument:

```python
def list_events(self, session_id: str, *, limit: int = 100, after_id: int | None = None, uninjected_only: bool = False, include_inactive: bool = False) -> list[dict]:
```

Only include `status = 'active'` when `include_inactive` is false.

- [ ] **Step 4: Run storage test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_storage.py::test_mark_events_low_signal_skipped_records_reason -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add YouTubeBridge/storage_repositories/events.py YouTubeBridge/tests/test_storage.py
git commit -m "feat(youtube-bridge): mark low signal live events"
```

---

### Task 4: Protected Free Talk Closing Drain

**Files:**
- Modify: `YouTubeBridge/engine_phase_pipeline.py`
- Test: `YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py`

- [ ] **Step 1: Write failing protected-drain test**

Append to `YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py`:

```python
@pytest.mark.asyncio
async def test_free_talk_closing_skips_low_signal_and_batches_eligible_comments(tmp_path):
    storage = _storage(tmp_path)
    storage.upsert_session({
        "session_id": "live-a",
        "connector_id": "youtube-main",
        "display_name": "Closing Drain",
        "post_plan_free_talk_enabled": True,
        "free_talk_closing_target_batches": 10,
        "free_talk_closing_min_batch_size": 5,
        "free_talk_closing_max_batch_size": 30,
        "free_talk_closing_time_limit_seconds": 300,
    })
    for index, text in enumerate(["666666", "這個工具適合團隊共用嗎？", "可以補充實際案例嗎？"], start=1):
        storage.save_event({
            "bridge_session_id": "live-a",
            "connector_id": "youtube-main",
            "youtube_message_id": f"msg-{index}",
            "message_type": "textMessageEvent",
            "author_channel_id": f"u{index}",
            "author_display_name": f"觀眾{index}",
            "message_text": text,
            "published_at": f"2026-05-15T10:00:0{index}",
            "received_at": f"2026-05-15T10:00:0{index}",
            "status": "active",
            "metadata": {"phase": "post_plan_free_talk"},
        })
    manager = YouTubeBridgeManager(storage, memoria_client_factory=FakeMemoriaClient)

    result = await manager._run_free_talk_audience_closing("live-a", reason="test")

    assert result["low_signal_skipped_count"] == 1
    assert result["eligible_processed_count"] == 2
    events = storage.list_events("live-a", include_inactive=True)
    low_signal = [event for event in events if event["status"] == "low_signal_skipped"]
    assert low_signal[0]["metadata"]["low_signal_reason"] == "repeated_short_token"
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py::test_free_talk_closing_skips_low_signal_and_batches_eligible_comments -q
```

Expected: FAIL because `_run_free_talk_audience_closing` does not exist.

- [ ] **Step 3: Implement free talk closing drain**

Add imports to `YouTubeBridge/engine_phase_pipeline.py`:

```python
import time

from free_talk_low_signal import classify_low_signal_comment, free_talk_closing_batch_size
```

Add method:

```python
    async def _run_free_talk_audience_closing(self, session_id: str, *, reason: str) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        started = time.monotonic()
        limit_seconds = int(session.get("free_talk_closing_time_limit_seconds", 300) or 300)
        pending = [
            event for event in self.storage.list_events(session_id, limit=500, uninjected_only=True)
            if str(event.get("safety_status") or "completed") == "completed"
            and str(event.get("priority_class") or "normal") != "super_chat"
        ]
        low_signal: dict[int, str] = {}
        eligible: list[dict[str, Any]] = []
        seen_texts: set[str] = set()
        for event in pending:
            text = str(event.get("message_text") or "")
            reason_code = classify_low_signal_comment(text)
            normalized = "".join(text.split()).lower()
            if normalized and normalized in seen_texts:
                reason_code = reason_code or "duplicate_message"
            if reason_code:
                low_signal[int(event["id"])] = reason_code
            else:
                eligible.append(event)
                seen_texts.add(normalized)
        skipped_count = self.storage.mark_events_low_signal_skipped(session_id, low_signal)
        batch_size = free_talk_closing_batch_size(
            len(eligible),
            target_batches=int(session.get("free_talk_closing_target_batches", 10) or 10),
            min_batch_size=int(session.get("free_talk_closing_min_batch_size", 5) or 5),
            max_batch_size=int(session.get("free_talk_closing_max_batch_size", 30) or 30),
        )
        processed = 0
        batches = 0
        for start in range(0, len(eligible), batch_size):
            if time.monotonic() - started >= limit_seconds:
                break
            batch = eligible[start:start + batch_size]
            if not batch:
                continue
            await self.inject_recent(
                session_id=session_id,
                event_ids=[int(event["id"]) for event in batch],
                max_events=len(batch),
                content="以下是雜談收尾時尚未回覆的聊天室留言摘要。請用自然收尾語氣一次回應主要問題與情緒，不需要逐條點名。",
                memoria_session_id=session.get("target_memoria_session_id", ""),
                character_ids=session.get("character_ids", []),
                source="free_talk_audience_closing",
            )
            processed += len(batch)
            batches += 1
        remaining = max(0, len(eligible) - processed)
        metadata = dict((self.storage.get_director_state(session_id) or {}).get("metadata") or {})
        metadata["free_talk_audience_closing"] = {
            "status": "completed" if remaining == 0 else "completed_with_skips",
            "reason": reason,
            "eligible_processed_count": processed,
            "low_signal_skipped_count": skipped_count,
            "closing_skipped_count": remaining,
            "batch_size": batch_size,
            "batch_count": batches,
            "completed_at": datetime.now().isoformat(),
        }
        self.storage.update_director_state(session_id, status="free_talk_summary", metadata=metadata)
        return metadata["free_talk_audience_closing"]
```

- [ ] **Step 4: Call drain from finalize**

Modify `finalize_phase_pipeline()` free talk branch:

```python
closing = await self._run_free_talk_audience_closing(session_id, reason=reason)
await self.run_phase_summary(session_id, summary_phase="free_talk", reason=reason)
cleanup = await self.maybe_run_phase_cleanup(session_id)
return {"phase": "free_talk_summary", "free_talk_audience_closing": closing, "cleanup": cleanup}
```

- [ ] **Step 5: Run protected-drain test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py::test_free_talk_closing_skips_low_signal_and_batches_eligible_comments -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add YouTubeBridge/engine_phase_pipeline.py YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py
git commit -m "feat(youtube-bridge): drain eligible free talk comments on closing"
```

---

### Task 5: Stage 4 Regression and Browser E2E

**Files:**
- No new code files.

- [ ] **Step 1: Run Stage 4 regression**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_free_talk_low_signal.py YouTubeBridge/tests/test_storage.py::test_mark_events_low_signal_skipped_records_reason YouTubeBridge/tests/test_storage.py::test_session_persists_free_talk_closing_batch_settings YouTubeBridge/tests/test_bridge_engine_phase_pipeline.py YouTubeBridge/tests/test_studio_ui.py -q
node --check YouTubeBridge/static/ui/studio.js
git diff --check
```

Expected: pytest PASS, `node --check` exit 0, `git diff --check` exit 0 or only existing CRLF warnings.

- [ ] **Step 2: Browser E2E with noisy chat**

Start 8091 in a visible foreground window:

```powershell
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "G:\ClaudeProject\MemoriaCore\YouTubeBridge\start.bat" -WorkingDirectory "G:\ClaudeProject\MemoriaCore\YouTubeBridge" -WindowStyle Normal
```

Browser QA:

- Open `http://127.0.0.1:8091/studio/`.
- Start a test live session.
- Enter free talk through the debug button.
- Inject a batch containing normal questions, repeated `666666`, repeated emojis, and duplicate messages.
- Click `收尾 / 停止直播`.
- Confirm normal questions are batched into free talk closing.
- Confirm low-signal messages are not sent to the LLM.
- Confirm Debug Log or API status shows `low_signal_skipped_count`.
- Confirm the session reaches summary/cleanup gate without hanging.

---

## Stage 4 Acceptance Criteria

- Low-signal comments are filtered deterministically before LLM prompt construction.
- Low-signal comments are marked `low_signal_skipped` with a reason.
- Free talk closing processes all eligible pending comments unless the configured time limit is reached.
- Batch size follows `ceil(eligible_count / target_batches)` clamped by min/max settings.
- Closing metadata records processed, skipped, low-signal, batch size, and batch count.
