# YouTubeBridge Event Safety Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 `YouTubeBridge/bridge_engine.py` 的直播留言安全分類責任拆成 focused mixin，讓 injection、closing 與 external context 共用同一個安全邊界。

**Architecture:** 新增 `YouTubeBridge/engine_event_safety.py`，提供 `EventSafetyManagerMixin`。`bridge_engine.py` 只保留 manager facade、public presenter、test event、Research Gate 與 external context；安全分類流程由新 mixin 負責，並透過既有 manager helper 呼叫 `_memoria_client()`、`_public_event()`、`_public_live_event()`、`_broadcast()` 與 `_single_line()`。為了維持既有相容性，`bridge_engine.py` 保留 `SAFETY_CLASSIFIER_BATCH_LIMIT` 與 `SAFETY_CLASSIFIER_SCHEMA` re-export。

**Tech Stack:** Python 3.12+、asyncio、pytest。

---

### Task 1: Event safety mixin contract

**Files:**
- Modify: `YouTubeBridge/tests/test_bridge_engine_split_modules.py`

- [x] **Step 1: Write the failing test**

在 split module contract 測試中新增：

```python
def test_bridge_manager_uses_event_safety_mixin():
    from bridge_engine import YouTubeBridgeManager
    from engine_event_safety import EventSafetyManagerMixin

    assert issubclass(YouTubeBridgeManager, EventSafetyManagerMixin)
```

- [x] **Step 2: Run red**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_split_modules.py::test_bridge_manager_uses_event_safety_mixin -q
```

Expected: fail with `ModuleNotFoundError: No module named 'engine_event_safety'`。

### Task 2: Move pending-event safety classification

**Files:**
- Create: `YouTubeBridge/engine_event_safety.py`
- Modify: `YouTubeBridge/bridge_engine.py`

- [x] **Step 1: Create `EventSafetyManagerMixin`**

新增 `engine_event_safety.py`，建立：

```python
"""YouTubeBridge live event safety classification mixin。"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from bridge_contracts import SAFETY_CLASSIFIER_BATCH_LIMIT, SAFETY_CLASSIFIER_SCHEMA


class EventSafetyManagerMixin:
    async def classify_pending_events(self, session_id: str, *, limit: int = 50) -> dict[str, Any]:
        ...

    @staticmethod
    def _normalize_safety_classifications(
        result: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> dict[int, dict[str, Any]]:
        ...
```

`classify_pending_events()` 內容直接從 `bridge_engine.py` 搬移，保留既有行為：

```python
result = await asyncio.to_thread(
    self._memoria_client().generate_prompt_json,
    prompt_key="youtube_live_safety_classifier_prompt",
    variables={"events_json": json.dumps(request_events, ensure_ascii=False, indent=2)},
    task_key="router",
    temperature=0.0,
    schema=SAFETY_CLASSIFIER_SCHEMA,
)
```

失敗與成功分支仍透過 manager helper：

```python
self.storage.update_event_safety(...)
self._public_event(updated)
self._public_live_event(updated)
await self._broadcast(...)
```

- [x] **Step 2: Wire manager inheritance**

在 `bridge_engine.py` 加入：

```python
from engine_event_safety import EventSafetyManagerMixin
```

並調整繼承順序：

```python
class YouTubeBridgeManager(
    DirectorRuntimeManagerMixin,
    ClosingManagerMixin,
    InjectionManagerMixin,
    RuntimeLifecycleManagerMixin,
    EventSafetyManagerMixin,
    DirectorManagerMixin,
    TopicPackManagerMixin,
):
```

從 `bridge_engine.py` 移除已搬移的：

```python
async def classify_pending_events(...)
def _normalize_safety_classifications(...)
```

### Task 3: Verify behavior and compatibility

**Files:**
- Modify: `YouTubeBridge/tests/test_bridge_engine_split_modules.py`
- Create: `YouTubeBridge/engine_event_safety.py`
- Modify: `YouTubeBridge/bridge_engine.py`

- [x] **Step 1: Run contract test**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_split_modules.py::test_bridge_manager_uses_event_safety_mixin -q
```

Expected: pass。

- [x] **Step 2: Run targeted engine tests**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_split_modules.py YouTubeBridge/tests/test_bridge_engine.py -q
```

Expected: pass；安全分類、closing safety、injection 與 external context 測試維持綠燈。

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
git diff --check -- YouTubeBridge docs/superpowers/plans/2026-05-06-youtube-bridge-event-safety-split.md
```

Expected: exit 0。

### Task 4: Commit and push

**Files:**
- Create: `YouTubeBridge/engine_event_safety.py`
- Modify: `YouTubeBridge/bridge_engine.py`
- Modify: `YouTubeBridge/tests/test_bridge_engine_split_modules.py`
- Create: `docs/superpowers/plans/2026-05-06-youtube-bridge-event-safety-split.md`

- [ ] **Step 1: Review status**

Run:

```powershell
git status -sb
```

Expected: only Event Safety split files are modified or untracked。

- [ ] **Step 2: Stage scoped files**

Run:

```powershell
git add -- YouTubeBridge/bridge_engine.py YouTubeBridge/engine_event_safety.py YouTubeBridge/tests/test_bridge_engine_split_modules.py docs/superpowers/plans/2026-05-06-youtube-bridge-event-safety-split.md
```

- [ ] **Step 3: Commit**

Run:

```powershell
git commit -m "refactor: split youtube bridge event safety"
```

- [ ] **Step 4: Push**

Run:

```powershell
git push
```
