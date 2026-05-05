# YouTubeBridge Closing Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 `YouTubeBridge/bridge_engine.py` 的直播收尾、duration finalize、closing Super Chat thanks 與 closing safety resolution 拆成 focused mixin。

**Architecture:** 新增 `YouTubeBridge/engine_closing.py`，提供 `ClosingManagerMixin`。此 mixin 擁有 duration 收尾流程、收尾 SC 感謝、timeout fallback、pending safety fail-closed 與 closing 前中斷 active generation。`bridge_engine.py` 保留 polling loop、director runtime、Research Gate、external context 與 test event runtime；closing mixin 仍透過 manager facade 呼叫既有 `classify_pending_events()`、`_send_director_turn()`、`_broadcast()`、`_event_line()`、`_public_event()` 等方法。

**Tech Stack:** Python 3.12+、asyncio、pytest。

---

### Task 1: Closing mixin contract

**Files:**
- Modify: `YouTubeBridge/tests/test_bridge_engine_split_modules.py`

- [x] **Step 1: Write the failing test**

在 split module contract 測試中新增：

```python
def test_bridge_manager_uses_closing_mixin():
    from bridge_engine import YouTubeBridgeManager
    from engine_closing import ClosingManagerMixin

    assert issubclass(YouTubeBridgeManager, ClosingManagerMixin)
```

- [x] **Step 2: Run red**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_split_modules.py::test_bridge_manager_uses_closing_mixin -q
```

Expected: fail with `ModuleNotFoundError: No module named 'engine_closing'`。

### Task 2: Move duration finalize and fallback closing

**Files:**
- Create: `YouTubeBridge/engine_closing.py`
- Modify: `YouTubeBridge/bridge_engine.py`

- [x] **Step 1: Create `ClosingManagerMixin`**

新增 `engine_closing.py`，建立：

```python
class ClosingManagerMixin:
    async def _finalize_for_duration(self, runtime: LiveRuntime, session: dict[str, Any]) -> None:
        ...

    async def _complete_closing_super_chat_thanks_fallback(
        self,
        session_id: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        ...
```

必要 imports：

```python
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from bridge_contracts import SAFETY_CLASSIFIER_BATCH_LIMIT
from bridge_runtime import LiveRuntime
```

- [x] **Step 2: Wire manager inheritance**

在 `bridge_engine.py` 加入：

```python
from engine_closing import ClosingManagerMixin

class YouTubeBridgeManager(
    ClosingManagerMixin,
    InjectionManagerMixin,
    RuntimeLifecycleManagerMixin,
    DirectorManagerMixin,
    TopicPackManagerMixin,
):
```

移除 `bridge_engine.py` 中已搬入 mixin 的 `_finalize_for_duration()` 與 `_complete_closing_super_chat_thanks_fallback()`。

### Task 3: Move safety resolution and active interrupt

**Files:**
- Modify: `YouTubeBridge/engine_closing.py`
- Modify: `YouTubeBridge/bridge_engine.py`

- [x] **Step 1: Move closing safety resolution**

搬入：

```python
async def _resolve_pending_safety_for_closing(
    self,
    session_id: str,
    *,
    timeout_seconds: float = 20.0,
    per_batch_timeout_seconds: float = 75.0,
    batch_limit: int = 10,
) -> dict[str, Any]:
    ...
```

此方法仍呼叫：

```python
self.classify_pending_events(...)
self.storage.update_event_safety(...)
self._public_event(...)
self._broadcast(...)
```

- [x] **Step 2: Move closing active generation interrupt**

搬入：

```python
async def _interrupt_active_generation_for_closing(
    self,
    runtime: LiveRuntime,
    *,
    timeout_seconds: float = 1.0,
) -> list[dict[str, Any]]:
    ...
```

此方法仍使用 `runtime.cancel_events`、`self.storage.request_interrupt()` 與 `self.storage.finalize_incomplete_interactions()`。

### Task 4: Move closing Super Chat thanks

**Files:**
- Modify: `YouTubeBridge/engine_closing.py`
- Modify: `YouTubeBridge/bridge_engine.py`

- [x] **Step 1: Move `run_closing_super_chat_thanks()`**

搬入：

```python
async def run_closing_super_chat_thanks(self, session_id: str) -> dict[str, Any]:
    ...
```

此方法仍呼叫：

```python
self._is_public_live_event_displayable(...)
self._event_line(...)
self._send_director_turn(...)
self.storage.mark_super_chats_handled_in_closing(...)
self._broadcast(...)
```

不搬 `_send_director_turn()`，它仍屬於 director runtime。

### Task 5: Verification

**Files:**
- Test only

- [x] **Step 1: Run targeted closing tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_split_modules.py YouTubeBridge/tests/test_bridge_engine.py -q
```

Expected: pass。

- [x] **Step 2: Run full YouTubeBridge suite**

Run:

```powershell
python -m pytest YouTubeBridge/tests -q
```

Expected: pass. If Windows `.pyTestTemp\basetemp` ACL cleanup fails, run `scripts\cleanup_pytest_temp.bat` first, then rerun the same pytest command.

- [x] **Step 3: Check diff formatting and scope**

Run:

```powershell
git diff --check -- YouTubeBridge docs/superpowers/plans/2026-05-06-youtube-bridge-closing-split.md
git status -sb
```

Expected: no whitespace errors; changed files are `bridge_engine.py`, `test_bridge_engine_split_modules.py`, `engine_closing.py`, and this plan file.
