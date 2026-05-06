# YouTubeBridge Director Runtime Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 `YouTubeBridge/bridge_engine.py` 的 director runtime 啟停、kickoff、idle loop 與 director turn execution 拆成 focused mixin。

**Architecture:** 新增 `YouTubeBridge/engine_director_runtime.py`，提供 `DirectorRuntimeManagerMixin`。既有 `engine_director.py` 保留純決策/helper；新 runtime mixin 負責 `start_director()`、`stop_director()`、`_director_kickoff()`、`_director_loop()`、`_send_director_turn()`。`bridge_engine.py` 保留 polling、Research Gate、external context、test event 與 public presenter helper。runtime mixin 透過 manager facade 呼叫既有 helper，例如 `_director_decision()`、`_public_director_prompt()`、`_topic_pack_context_for_query()`、`_claim_interaction_for_execution()`、`_broadcast()`。

**Tech Stack:** Python 3.12+、asyncio、pytest。

---

### Task 1: Director runtime mixin contract

**Files:**
- Modify: `YouTubeBridge/tests/test_bridge_engine_split_modules.py`

- [x] **Step 1: Write the failing test**

在 split module contract 測試中新增：

```python
def test_bridge_manager_uses_director_runtime_mixin():
    from bridge_engine import YouTubeBridgeManager
    from engine_director_runtime import DirectorRuntimeManagerMixin

    assert issubclass(YouTubeBridgeManager, DirectorRuntimeManagerMixin)
```

- [x] **Step 2: Run red**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_split_modules.py::test_bridge_manager_uses_director_runtime_mixin -q
```

Expected: fail with `ModuleNotFoundError: No module named 'engine_director_runtime'`。

### Task 2: Move director lifecycle runtime methods

**Files:**
- Create: `YouTubeBridge/engine_director_runtime.py`
- Modify: `YouTubeBridge/bridge_engine.py`

- [x] **Step 1: Create `DirectorRuntimeManagerMixin`**

新增 `engine_director_runtime.py`，建立：

```python
class DirectorRuntimeManagerMixin:
    async def start_director(...)
    async def stop_director(...)
    async def _director_kickoff(...)
    async def _director_loop(...)
```

必要 imports：

```python
import asyncio
import logging
from datetime import datetime
from typing import Any

from bridge_runtime import LiveRuntime
```

`_director_loop()` 保持呼叫 manager 上既有方法：

```python
self._duration_reached(...)
self._finalize_for_duration(...)
self._should_block_director_for_pending_inject(...)
self._director_decision(...)
self._director_anchor_decision(...)
self._director_guidance_transition_decision(...)
self._director_idle_continue_decision(...)
self._send_director_turn(...)
```

- [x] **Step 2: Wire manager inheritance**

在 `bridge_engine.py` 加入：

```python
from engine_director_runtime import DirectorRuntimeManagerMixin

class YouTubeBridgeManager(
    DirectorRuntimeManagerMixin,
    ClosingManagerMixin,
    InjectionManagerMixin,
    RuntimeLifecycleManagerMixin,
    DirectorManagerMixin,
    TopicPackManagerMixin,
):
```

移除 `bridge_engine.py` 中已搬入 mixin 的 `start_director()`、`stop_director()`、`_director_kickoff()`、`_director_loop()`。

### Task 3: Move director turn execution

**Files:**
- Modify: `YouTubeBridge/engine_director_runtime.py`
- Modify: `YouTubeBridge/bridge_engine.py`

- [x] **Step 1: Move `_send_director_turn()`**

搬入：

```python
async def _send_director_turn(
    self,
    session: dict[str, Any],
    state: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    ...
```

必要 extra import：

```python
import threading
from memoria_client import GenerationInterrupted
```

此方法仍透過 manager facade 呼叫：

```python
self._public_director_prompt(...)
self._public_director_topic(...)
self._session_elapsed(...)
self._topic_pack_context_for_query(...)
self._claim_interaction_for_execution(...)
self._memoria_client()
self._director_display_content(...)
self._normalized_interrupt_reason(...)
self._broadcast(...)
```

不搬 `_director_decision()`、`_public_director_prompt()` 或 `_director_display_content()`；它們已屬於 `engine_director.py`。

### Task 4: Verification

**Files:**
- Test only

- [x] **Step 1: Run targeted director tests**

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
git diff --check -- YouTubeBridge docs/superpowers/plans/2026-05-06-youtube-bridge-director-runtime-split.md
git status -sb
```

Expected: no whitespace errors; changed files are `bridge_engine.py`, `test_bridge_engine_split_modules.py`, `engine_director_runtime.py`, and this plan file.
