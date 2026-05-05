# YouTubeBridge Injection Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 `YouTubeBridge/bridge_engine.py` 的留言注入與 interaction 執行責任拆成 focused mixin，降低主 manager 的 runtime 複雜度。

**Architecture:** 新增 `YouTubeBridge/engine_injection.py`，提供 `InjectionManagerMixin`。此 mixin 擁有自動注入 loop、pending event 選取、SC interrupt cooldown、手動/自動 `inject_recent()` 執行與 interaction claim/interrupt helper。`bridge_engine.py` 保留 context/research 組裝、closing finalize、director runtime 與 test event runtime，讓這一階段維持單一責任邊界。

**Tech Stack:** Python 3.12+、asyncio、pytest。

---

### Task 1: Injection mixin contract

**Files:**
- Modify: `YouTubeBridge/tests/test_bridge_engine_split_modules.py`

- [x] **Step 1: Write the failing test**

在 split module contract 測試中新增：

```python
def test_bridge_manager_uses_injection_mixin():
    from bridge_engine import YouTubeBridgeManager
    from engine_injection import InjectionManagerMixin

    assert issubclass(YouTubeBridgeManager, InjectionManagerMixin)
```

- [x] **Step 2: Run red**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_split_modules.py::test_bridge_manager_uses_injection_mixin -q
```

Expected: fail with `ModuleNotFoundError: No module named 'engine_injection'`。

### Task 2: Move auto-injection runtime into `engine_injection.py`

**Files:**
- Create: `YouTubeBridge/engine_injection.py`
- Modify: `YouTubeBridge/bridge_engine.py`

- [x] **Step 1: Create mixin module**

新增 `engine_injection.py`，建立：

```python
class InjectionManagerMixin:
    @staticmethod
    def _auto_inject_delay(...)

    @staticmethod
    def _select_pending_events_for_injection(...)

    def _sc_interrupt_allowed(...)

    async def _auto_inject_loop(...)
```

必要 imports：

```python
import asyncio
import logging
import math
from datetime import datetime, timedelta
from typing import Any

from bridge_contracts import CONTROLLED_CONTEXT_CONTENT, DEFAULT_INJECT_CONTENT
from bridge_runtime import LiveRuntime
from memoria_client import GenerationInterrupted
```

`_auto_inject_loop()` 保持呼叫現有 manager 方法：

```python
await self.inject_recent(...)
await self._broadcast(...)
self._duration_reached(...)
await self._finalize_for_duration(...)
```

- [x] **Step 2: Wire manager inheritance**

在 `bridge_engine.py` 加入：

```python
from engine_injection import InjectionManagerMixin

class YouTubeBridgeManager(
    InjectionManagerMixin,
    RuntimeLifecycleManagerMixin,
    DirectorManagerMixin,
    TopicPackManagerMixin,
):
```

移除 `bridge_engine.py` 中已搬入 mixin 的 `_auto_inject_delay`、`_select_pending_events_for_injection`、`_sc_interrupt_allowed`、`_auto_inject_loop`。

### Task 3: Move interaction execution helpers

**Files:**
- Modify: `YouTubeBridge/engine_injection.py`
- Modify: `YouTubeBridge/bridge_engine.py`

- [x] **Step 1: Move public interrupt helper**

搬入：

```python
async def interrupt_session(self, session_id: str, *, reason: str = "manual_interrupt") -> dict[str, Any]
```

此方法仍使用 `self.storage.request_interrupt()`、`runtime.cancel_events` 與 `self._broadcast()`。

- [x] **Step 2: Move execution claim helpers**

搬入：

```python
async def _claim_interaction_for_execution(...)

@staticmethod
def _normalized_interrupt_reason(...)
```

保留原 timeout、broadcast、`GenerationInterrupted` 判斷行為。

- [x] **Step 3: Move `inject_recent()`**

搬入：

```python
async def inject_recent(
    self,
    session_id: str,
    *,
    event_ids: list[int] | None = None,
    max_events: int | None = None,
    content: str = DEFAULT_INJECT_CONTENT,
    memoria_session_id: str = "",
    character_ids: list[str] | None = None,
    source: str = "manual_inject",
    priority: int = 200,
) -> dict[str, Any]:
```

此方法仍透過 manager facade 呼叫：

```python
self.classify_pending_events(...)
self.build_external_context(...)
self._display_content_from_external_context(...)
self._memoria_client()
self._broadcast(...)
self.storage.update_director_state(...)
```

不搬 `build_external_context()`，因為它含 live query/research context，留待下一階段。

### Task 4: Verification

**Files:**
- Test only

- [x] **Step 1: Run targeted injection tests**

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
git diff --check -- YouTubeBridge docs/superpowers/plans/2026-05-06-youtube-bridge-injection-split.md
git status -sb
```

Expected: no whitespace errors; only this phase plus existing uncommitted lifecycle split files are changed.
