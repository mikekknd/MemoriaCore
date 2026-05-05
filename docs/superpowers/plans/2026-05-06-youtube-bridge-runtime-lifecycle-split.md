# YouTubeBridge Runtime Lifecycle Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 `YouTubeBridge/bridge_engine.py` 的 session runtime lifecycle 與狀態查詢責任拆成 focused mixin，降低主 manager 檔案複雜度。

**Architecture:** 保留 `YouTubeBridgeManager` 作為 facade 與 runtime loop 擁有者。新增 `engine_runtime_lifecycle.py`，只搬 `get_status()`、啟停 session、subscribe/unsubscribe、autostart、duration helper 與 background task cleanup；不搬 `_poll_loop()`、`_auto_inject_loop()`、`_director_loop()` 或 `_send_director_turn()`，避免同時改動 async loop 行為。

**Tech Stack:** Python 3.12+、asyncio、pytest。

---

### Task 1: Runtime lifecycle mixin contract

**Files:**
- Modify: `YouTubeBridge/tests/test_bridge_engine_split_modules.py`

- [x] **Step 1: Write the failing test**

新增測試，要求 manager 繼承新的 lifecycle mixin：

```python
def test_bridge_manager_uses_runtime_lifecycle_mixin():
    from bridge_engine import YouTubeBridgeManager
    from engine_runtime_lifecycle import RuntimeLifecycleManagerMixin

    assert issubclass(YouTubeBridgeManager, RuntimeLifecycleManagerMixin)
```

- [x] **Step 2: Run red**

Run:

```powershell
python -m pytest YouTubeBridge/tests/test_bridge_engine_split_modules.py::test_bridge_manager_uses_runtime_lifecycle_mixin -q
```

Expected: fail with `ModuleNotFoundError: No module named 'engine_runtime_lifecycle'`。

### Task 2: Move lifecycle/status methods into mixin

**Files:**
- Create: `YouTubeBridge/engine_runtime_lifecycle.py`
- Modify: `YouTubeBridge/bridge_engine.py`

- [x] **Step 1: Create `RuntimeLifecycleManagerMixin`**

新增檔案，搬入下列方法：

```python
get_status
sync_autostart
start_session
stop_session
stop_all
subscribe
unsubscribe
_session_is_finalized
_session_elapsed
_parse_iso_datetime
_duration_reached
_cancel_runtime_task
_stop_runtime_background_tasks_for_closing
```

`start_session()` 清除 trace 時呼叫 `self._clear_llm_trace_log()`，讓 `bridge_engine.py` 保留 `DEFAULT_LLM_TRACE_PATH` 與既有 monkeypatch 相容。

- [x] **Step 2: Keep facade compatibility in `bridge_engine.py`**

在 `bridge_engine.py` 中：

```python
from engine_runtime_lifecycle import RuntimeLifecycleManagerMixin

class YouTubeBridgeManager(RuntimeLifecycleManagerMixin, DirectorManagerMixin, TopicPackManagerMixin):
    ...

    @staticmethod
    def _clear_llm_trace_log() -> dict[str, Any]:
        return clear_llm_trace_log()
```

移除 manager 內已搬到 mixin 的方法；保留 `_poll_loop()`、`_auto_inject_loop()`、`_director_loop()`、`_send_director_turn()` 與 finalize 流程在原檔。

### Task 3: Verification

**Files:**
- Test only

- [x] **Step 1: Run targeted tests**

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

Expected: pass。

- [x] **Step 3: Check diff formatting**

Run:

```powershell
git diff --check -- YouTubeBridge docs/superpowers/plans/2026-05-06-youtube-bridge-runtime-lifecycle-split.md
```

Expected: no whitespace errors。
