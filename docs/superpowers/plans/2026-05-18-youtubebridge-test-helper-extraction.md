# YouTubeBridge Test Helper Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 YouTubeBridge 測試共用 helper seam，先移除重複的 temp storage、queue wait、predicate wait、FakeTTSProvider，讓後續拆分 `test_bridge_engine_director.py`、`test_server_auth.py`、`test_bridge_engine_injection.py` 時不需要複製測試架設成本。

**Architecture:** 第一階段不搬大型 test function，不調整產品程式碼，只加深既有 `YouTubeBridge/tests/bridge_engine_test_support.py` 這個測試支援 Module。測試檔透過小型共用 helper 取得相同 interface，保留原本 test names 與行為，讓拆分可以在後續以純搬移方式進行。

**Tech Stack:** Python 3.13、pytest、pytest-asyncio strict mode、Windows PowerShell、`.pyTestTemp/` pytest temp policy。

---

## File Structure

- Modify `YouTubeBridge/tests/bridge_engine_test_support.py`
  - 新增 `temp_storage()` context manager，統一 `BridgeStorage(_tmp_dir() / "youtube_live.db")` 建立與 `shutil.rmtree(...)` 清理。
  - 新增 `_next_queue_event(...)`，統一 deadline-based queue wait，找不到事件時 fail-fast。
  - 新增 `_wait_until(...)`，統一 async predicate wait，避免裸 `asyncio.sleep(...)` 分散在 helper 定義中。
  - 新增 `FakeTTSProvider`，集中 presentation/director 測試常用 TTS fake。
- Modify `YouTubeBridge/tests/test_bridge_engine_director.py`
  - 移除本檔重複的 `temp_storage`、`FakeTTSProvider`、`_next_queue_event`、`_wait_until` 定義。
  - 從 `bridge_engine_test_support` 匯入同名 helper，保留 test body 不變。
- Modify `YouTubeBridge/tests/test_bridge_engine_injection.py`
  - 移除本檔重複的 `temp_storage`、`_next_queue_event` 定義。
  - 從 `bridge_engine_test_support` 匯入同名 helper，保留 test body 不變。
- Modify `YouTubeBridge/tests/test_bridge_engine_closing.py`
  - 移除本檔重複的 `temp_storage`、`_next_queue_event` 定義。
  - 從 `bridge_engine_test_support` 匯入同名 helper，保留 test body 不變。
- Modify `YouTubeBridge/tests/test_presentation_queue.py`
  - 移除本檔重複的 path bootstrap、`_tmp_dir`、`_wait_for`、`_next_queue_event`、`FakeTTSProvider` 定義。
  - 從 `bridge_engine_test_support` 匯入 `BridgeStorage`、`YouTubeBridgeManager`、`_tmp_dir`、`_next_queue_event`、`_wait_until` as `_wait_for`、`FakeTTSProvider`。
  - 保留本檔專用 `FailingTTSProvider`，因為它只服務此檔失敗案例。

## Out of Scope

- 不在本階段搬移 `test_bridge_engine_director.py` 的 115 個 test function。
- 不修改 `pytest.ini` 的 `testpaths = tests`，避免讓 `python -m pytest` 默默擴大到 `YouTubeBridge/tests` 並受到 live session 狀態干擾。
- 不重命名 `test_server_auth.py` 或切 route tests；這是後續第二階段。

## Future Split Order

1. `test_bridge_engine_director.py` 拆成 `test_director_turn_context.py`、`test_director_audience_preprocessing.py`、`test_director_prefetch_chain.py`、`test_director_loop_idle.py`、`test_director_public_context.py`。
2. `test_server_auth.py` 拆成 `test_server_auth_loopback.py`、`test_launcher_contract.py`、`test_control_ui_static_contract.py`、`test_session_routes.py`、`test_topic_pack_routes.py`、`test_episode_plan_routes.py`、`test_chat_preview_routes.py`。
3. `test_bridge_engine_injection.py` 拆成 `test_inject_recent.py`、`test_auto_inject_legacy.py`、`test_auto_inject_director_owned.py`、`test_super_chat_interrupt_policy.py`、`test_audience_preprocessing_queue.py`。

---

### Task 1: Add Shared YouTubeBridge Test Helpers

**Files:**
- Modify: `YouTubeBridge/tests/bridge_engine_test_support.py`
- Test: `YouTubeBridge/tests/test_presentation_queue.py`

- [x] **Step 1: Add imports for shared helper implementation**

Add these imports near the top of `YouTubeBridge/tests/bridge_engine_test_support.py`:

```python
import asyncio
import contextlib
import shutil
import time
```

Add this import after existing project imports:

```python
from tts_gpt_sovits import TTSResult
```

- [x] **Step 2: Add shared helper code**

Add this code after `_tmp_dir()`:

```python
@contextlib.contextmanager
def temp_storage():
    tmp_dir = _tmp_dir()
    try:
        yield BridgeStorage(tmp_dir / "youtube_live.db")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def _next_queue_event(queue: asyncio.Queue, event_type: str, *, timeout: float = 1.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(0.01, deadline - time.monotonic())
        event = await asyncio.wait_for(queue.get(), timeout=remaining)
        if event.get("type") == event_type:
            return event
    raise AssertionError(f"{event_type} was not received before timeout")


async def _wait_until(condition, *, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


class FakeTTSProvider:
    def __init__(self):
        self.calls = []

    def synthesize(self, text, profile):
        self.calls.append({"text": text, "profile": dict(profile)})
        return TTSResult(ok=True, audio_bytes=f"audio:{text}".encode("utf-8"), audio_format="wav")

    def call_texts(self):
        return [call["text"] for call in self.calls]
```

- [x] **Step 3: Export shared helper names**

Add these names to `__all__`:

```python
"temp_storage",
"_next_queue_event",
"_wait_until",
"FakeTTSProvider",
```

- [x] **Step 4: Run import smoke test**

Run:

```powershell
@'
import sys
from pathlib import Path
sys.path.insert(0, str(Path("YouTubeBridge/tests").resolve()))
from bridge_engine_test_support import FakeTTSProvider, _next_queue_event, _wait_until, temp_storage
print(FakeTTSProvider().__class__.__name__, callable(temp_storage), callable(_next_queue_event), callable(_wait_until))
'@ | python -
```

Expected output includes:

```text
FakeTTSProvider True True True
```

---

### Task 2: Migrate Duplicate Helper Definitions

**Files:**
- Modify: `YouTubeBridge/tests/test_bridge_engine_director.py`
- Modify: `YouTubeBridge/tests/test_bridge_engine_injection.py`
- Modify: `YouTubeBridge/tests/test_bridge_engine_closing.py`
- Modify: `YouTubeBridge/tests/test_presentation_queue.py`

- [x] **Step 1: Update `test_bridge_engine_director.py` imports and remove local helpers**

In the `from bridge_engine_test_support import (...)` block, add:

```python
    FakeTTSProvider,
    _next_queue_event,
    _wait_until,
    temp_storage,
```

Remove the local `temp_storage`, `FakeTTSProvider`, `_next_queue_event`, and `_wait_until` definitions from lines near the top of the file. Keep imports that are still used elsewhere in the file, including `contextlib`, `shutil`, `time`, and `TTSResult`.

- [x] **Step 2: Update `test_bridge_engine_injection.py` imports and remove local helpers**

In the `from bridge_engine_test_support import (...)` block, add:

```python
    _next_queue_event,
    temp_storage,
```

Remove the local `temp_storage` and `_next_queue_event` definitions. Keep `contextlib`, `shutil`, `time`, and `TTSResult`, because the file still uses them outside the removed helper block.

- [x] **Step 3: Update `test_bridge_engine_closing.py` imports and remove local helpers**

In the `from bridge_engine_test_support import (...)` block, add:

```python
    _next_queue_event,
    temp_storage,
```

Remove the local `temp_storage` and `_next_queue_event` definitions. Keep `contextlib`, `shutil`, and `time`, because the file still uses them outside the removed helper block.

- [x] **Step 4: Update `test_presentation_queue.py` bootstrap and helper imports**

Replace the top local path bootstrap and helper definitions with:

```python
import asyncio
import shutil

import pytest

from bridge_engine_test_support import (
    BridgeStorage,
    FakeTTSProvider,
    YouTubeBridgeManager,
    _next_queue_event,
    _tmp_dir,
    _wait_until as _wait_for,
)
from tts_gpt_sovits import TTSResult
```

Keep only this local provider class:

```python
class FailingTTSProvider:
    def synthesize(self, text, profile):
        return TTSResult(ok=False, audio_format="wav", error="tts offline")
```

- [x] **Step 5: Run focused migrated helper tests**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_presentation_queue.py YouTubeBridge\tests\test_bridge_engine_injection.py::test_stream_result_drops_message_if_interrupted_before_broadcast YouTubeBridge\tests\test_bridge_engine_closing.py::test_episode_plan_completed_finalize_runs_formal_final_closing YouTubeBridge\tests\test_bridge_engine_director.py::test_presentation_queue_emits_debug_events_and_server_logs -q --basetemp=.pyTestTemp\basetemp-test-helper-extraction-focused
```

Expected result:

```text
7 passed
```

---

### Task 3: Verify Collection and Director Regression Surface

**Files:**
- Verify: `YouTubeBridge/tests/bridge_engine_test_support.py`
- Verify: `YouTubeBridge/tests/test_bridge_engine_director.py`
- Verify: `YouTubeBridge/tests/test_bridge_engine_injection.py`
- Verify: `YouTubeBridge/tests/test_bridge_engine_closing.py`
- Verify: `YouTubeBridge/tests/test_presentation_queue.py`

- [x] **Step 1: Run YouTubeBridge collect-only**

Run:

```powershell
python -m pytest YouTubeBridge\tests --collect-only -q --basetemp=.pyTestTemp\basetemp-test-helper-extraction-collect
```

Expected result includes:

```text
669 tests collected
```

- [x] **Step 2: Run after-main focused regression from the previous memory spike fix**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_director.py -q -k "does_not_reconsume_same_chained_prefetch_task or uses_audience_chain_task_instead_of_stale_prepare" --basetemp=.pyTestTemp\basetemp-test-helper-extraction-after-main
```

Expected result:

```text
2 passed
```

- [x] **Step 3: Run full director test file**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_bridge_engine_director.py -q --basetemp=.pyTestTemp\basetemp-test-helper-extraction-director
```

Expected result:

```text
115 passed
```

- [x] **Step 4: Run diff checks**

Run:

```powershell
git diff --check -- YouTubeBridge\tests\bridge_engine_test_support.py YouTubeBridge\tests\test_bridge_engine_director.py YouTubeBridge\tests\test_bridge_engine_injection.py YouTubeBridge\tests\test_bridge_engine_closing.py YouTubeBridge\tests\test_presentation_queue.py docs\superpowers\plans\2026-05-18-youtubebridge-test-helper-extraction.md
```

Expected result: command exits with code 0 and prints no whitespace errors.

---

## Self-Review

- Spec coverage: This plan implements the first low-risk optimization phase from the current test-suite review: shared helper extraction before large file splitting.
- Placeholder scan: The plan contains no placeholder task and every code-changing step includes exact code or exact import names.
- Type consistency: Helper names match existing test usage: `temp_storage`, `_next_queue_event`, `_wait_until`, `_wait_for`, `FakeTTSProvider`, `_tmp_dir`.
