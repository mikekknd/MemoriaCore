# YouTubeBridge Test Runtime Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 `YouTubeBridge/bridge_engine.py` 的 auto test event runtime 與 test event generation 拆成 focused mixin。

**Architecture:** 新增 `YouTubeBridge/engine_test_runtime.py`，提供 `TestRuntimeManagerMixin`。既有 `engine_test_events.py` 保留純測試留言 helper；新 runtime mixin 負責 `_auto_test_event_loop()`、`start_auto_test_events()`、`stop_auto_test_events()`、`generate_test_events()` 與 manager facade 上的測試留言 wrapper。`bridge_engine.py` 繼續保留 public presenter、polling、Research Gate、external context 與 `_broadcast()`。為維持測試與外部相容性，`bridge_engine.py` 保留 `random` import，讓既有 `bridge_engine.random.*` monkeypatch 仍能影響 helper 模組使用的標準 `random` module。

**Tech Stack:** Python 3.12+、asyncio、pytest。

---

### Task 1: Test runtime mixin contract

**Files:**
- Modify: `YouTubeBridge/tests/test_bridge_engine_split_modules.py`

- [x] **Step 1: Write the failing test**

在 split module contract 測試中新增：

```python
def test_bridge_manager_uses_test_runtime_mixin():
    from bridge_engine import YouTubeBridgeManager
    from engine_test_runtime import TestRuntimeManagerMixin

    assert issubclass(YouTubeBridgeManager, TestRuntimeManagerMixin)
```

- [x] **Step 2: Run red**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_split_modules.py::test_bridge_manager_uses_test_runtime_mixin -q
```

Expected: fail with `ModuleNotFoundError: No module named 'engine_test_runtime'`。

### Task 2: Move auto test event runtime

**Files:**
- Create: `YouTubeBridge/engine_test_runtime.py`
- Modify: `YouTubeBridge/bridge_engine.py`

- [x] **Step 1: Create `TestRuntimeManagerMixin` runtime methods**

新增 `engine_test_runtime.py`，建立：

```python
"""YouTubeBridge test event runtime mixin。"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime
from typing import Any

from bridge_runtime import LiveRuntime

logger = logging.getLogger("youtube_bridge")


class TestRuntimeManagerMixin:
    async def _auto_test_event_loop(self, runtime: LiveRuntime) -> None:
        ...

    async def start_auto_test_events(self, session_id: str) -> dict[str, Any]:
        ...

    async def stop_auto_test_events(self, session_id: str) -> dict[str, Any]:
        ...
```

內容直接從 `bridge_engine.py` 搬移，仍透過 manager facade 呼叫：

```python
self.generate_test_events(...)
self.get_status(session_id)
self._broadcast(...)
```

- [x] **Step 2: Wire manager inheritance**

在 `bridge_engine.py` 加入：

```python
from engine_test_runtime import TestRuntimeManagerMixin
```

並調整繼承順序：

```python
class YouTubeBridgeManager(
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

從 `bridge_engine.py` 移除 `_auto_test_event_loop()`、`start_auto_test_events()`、`stop_auto_test_events()`。

### Task 3: Move test event generation runtime

**Files:**
- Modify: `YouTubeBridge/engine_test_runtime.py`
- Modify: `YouTubeBridge/bridge_engine.py`

- [x] **Step 1: Move `generate_test_events()`**

搬入：

```python
async def generate_test_events(
    self,
    session_id: str,
    *,
    count: int = 5,
    topic_hint: str = "",
    use_llm: bool = True,
    super_chat_count: int = 0,
    include_malicious_sc: bool = False,
    sc_burst: bool = False,
) -> dict[str, Any]:
    ...
```

必要 extra imports：

```python
import uuid
from storage_event_utils import infer_super_chat_tier
```

保留既有行為：儲存一般 test message、test super chat、broadcast `youtube_live_event`、`super_chat_received` 與 `test_events_generated`。

- [x] **Step 2: Move test generation facade wrappers**

搬入下列方法，保持 `YouTubeBridgeManager._generate_test_super_chats(...)` 等既有呼叫可用。需引用 manager class helper 的 wrapper 使用 `@classmethod`，單純轉呼叫 helper 使用 `@staticmethod`：

```python
@staticmethod
def _format_test_amount(...)

@staticmethod
def _variant_test_comment_text(...)

@staticmethod
def _variant_test_super_chat_text(...)

@staticmethod
def _generate_test_super_chats(...)

@staticmethod
def _test_super_chat_malicious_flags(...)

def _generate_test_comments(...)

@staticmethod
def _clean_test_comments(...)

@staticmethod
def _fallback_test_comments(...)
```

必要 extra imports：

```python
import engine_test_events
from bridge_contracts import TEST_COMMENT_SCHEMA
```

`_generate_test_comments()` 仍透過 manager helper 呼叫：

```python
self._public_test_topic(...)
self._test_comment_event_line(...)
self._test_comment_interaction_line(...)
self._memoria_client()
self._clean_test_comments(...)
self._fallback_test_comments(...)
```

### Task 4: Verify behavior and compatibility

**Files:**
- Create: `YouTubeBridge/engine_test_runtime.py`
- Modify: `YouTubeBridge/bridge_engine.py`
- Modify: `YouTubeBridge/tests/test_bridge_engine_split_modules.py`

- [x] **Step 1: Run contract test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_split_modules.py::test_bridge_manager_uses_test_runtime_mixin -q
```

Expected: pass。

- [x] **Step 2: Run targeted engine tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_split_modules.py YouTubeBridge/tests/test_bridge_engine.py -q
```

Expected: pass；test event generation、auto test event runtime、monkeypatch `bridge_engine.random.*` 與 safety/injection 周邊測試維持綠燈。

- [x] **Step 3: Run full YouTubeBridge tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests -q
```

Expected: pass。若 Windows 發生 `.pyTestTemp\basetemp` ACL / PermissionError，先執行：

```powershell
scripts\cleanup_pytest_temp.bat
```

再重跑完整測試。

- [x] **Step 4: Check whitespace**

Run:

```powershell
git diff --check -- YouTubeBridge docs/superpowers/plans/2026-05-06-youtube-bridge-test-runtime-split.md
```

Expected: exit 0。

### Task 5: Commit and push

**Files:**
- Create: `YouTubeBridge/engine_test_runtime.py`
- Modify: `YouTubeBridge/bridge_engine.py`
- Modify: `YouTubeBridge/tests/test_bridge_engine_split_modules.py`
- Create: `docs/superpowers/plans/2026-05-06-youtube-bridge-test-runtime-split.md`

- [ ] **Step 1: Review status**

Run:

```powershell
git status -sb
```

Expected: only Test Runtime split files are modified or untracked。

- [ ] **Step 2: Stage scoped files**

Run:

```powershell
git add -- YouTubeBridge/bridge_engine.py YouTubeBridge/engine_test_runtime.py YouTubeBridge/tests/test_bridge_engine_split_modules.py docs/superpowers/plans/2026-05-06-youtube-bridge-test-runtime-split.md
```

- [ ] **Step 3: Commit**

Run:

```powershell
git commit -m "refactor: split youtube bridge test runtime"
```

- [ ] **Step 4: Push**

Run:

```powershell
git push
```
