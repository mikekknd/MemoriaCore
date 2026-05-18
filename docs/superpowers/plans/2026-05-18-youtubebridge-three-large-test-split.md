# YouTubeBridge Three Large Test Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將 `test_bridge_engine_director.py`、`test_bridge_engine_injection.py`、`test_server_auth.py` 這三個高負載測試檔拆成較小、按行為命名的測試 Module，維持 `YouTubeBridge/tests` collect 數量不變。

**Architecture:** 不修改產品 runtime，只做測試 Module relocation。每個原大檔保留 import/header 與一段導覽註解；實際 `test_*` function 依名稱分類搬到新的 `test_*.py` 檔，每個新檔複用原檔 header，避免在拆分時同時重構 fixture 或 mock 行為。

**Tech Stack:** Python 3.13、pytest、pytest-asyncio strict mode、Windows PowerShell、`.pyTestTemp/` pytest temp policy。

---

## File Structure

- Modify `YouTubeBridge/tests/test_bridge_engine_director.py`
  - 保留 shared imports 與 `_episode_plan_characters()` helper。
  - 移除 top-level test functions，改成導覽註解。
- Create `YouTubeBridge/tests/test_director_turn_context.py`
  - 放 director prompt/context/kickoff/character binding/segment state/public context tests。
- Create `YouTubeBridge/tests/test_director_audience_preprocessing.py`
  - 放 audience gap prepare、audience preprocessing、audience presentation gate tests。
- Create `YouTubeBridge/tests/test_director_prefetch_chain.py`
  - 放 planned prefetch、presentation prefetch、timeout cleanup、`_after_main_turn_sequence()` tests。
- Create `YouTubeBridge/tests/test_director_loop_idle.py`
  - 放 director loop、idle、finalize/blocking、state status tests。

- Modify `YouTubeBridge/tests/test_bridge_engine_injection.py`
  - 保留 imports 與導覽註解。
- Create `YouTubeBridge/tests/test_inject_recent.py`
  - 放 stream result、manual `inject_recent`、selected-event injection tests。
- Create `YouTubeBridge/tests/test_auto_inject_legacy.py`
  - 放 legacy auto-inject、dynamic delay、pending-event selection tests。
- Create `YouTubeBridge/tests/test_auto_inject_director_owned.py`
  - 放 director-owned auto-inject loop tests。
- Create `YouTubeBridge/tests/test_super_chat_interrupt_policy.py`
  - 放 Super Chat priority/interruption/queue policy tests。
- Create `YouTubeBridge/tests/test_audience_preprocessing_queue.py`
  - 放 preprocessing queue、requested event id、closing/stopped state tests。

- Modify `YouTubeBridge/tests/test_server_auth.py`
  - 保留 `_request()`, `_control_ui_source()`, `_live_chat_source()`, `_assert_launcher_uses_runtime_log_dir()` helper 與導覽註解。
- Create `YouTubeBridge/tests/test_server_auth_loopback.py`
  - 放 bridge key、loopback static/audio/avatar auth tests。
- Create `YouTubeBridge/tests/test_launcher_contract.py`
  - 放 Windows launcher、runtime log、hot reload、stop script tests。
- Create `YouTubeBridge/tests/test_control_ui_static_contract.py`
  - 放 `control.js`、`live_chat.js`、Studio/control UI static contract tests。
- Create `YouTubeBridge/tests/test_session_routes.py`
  - 放 current session start/delete/finalize/recent events/session upsert route tests。
- Create `YouTubeBridge/tests/test_topic_pack_routes.py`
  - 放 topic pack import/edit/delete/search/graph/research endpoints tests。
- Create `YouTubeBridge/tests/test_episode_plan_routes.py`
  - 放 episode plan import/bind/sync/evidence/character binding tests。
- Create `YouTubeBridge/tests/test_chat_preview_routes.py`
  - 放 chat preview sanitizer/filter/public shape tests。

## Classification Rules

Use function name classification rather than hand-editing 1000s of lines:

```python
def classify_director(name: str) -> str:
    if "prefetch" in name or "after_main_turn_sequence" in name or "ready_prepared_items" in name:
        return "test_director_prefetch_chain.py"
    if "audience" in name:
        return "test_director_audience_preprocessing.py"
    if "director_loop" in name or "idle" in name or "finalizes" in name or "blocks_" in name or "start_director" in name:
        return "test_director_loop_idle.py"
    return "test_director_turn_context.py"

def classify_injection(name: str) -> str:
    if "inject_recent" in name or "stream_result" in name:
        return "test_inject_recent.py"
    if "preprocessing" in name or "requested_event_ids" in name:
        return "test_audience_preprocessing_queue.py"
    if "director_owned_auto_inject" in name or "auto_inject_loop" in name:
        return "test_auto_inject_director_owned.py"
    if "super_chat" in name:
        return "test_super_chat_interrupt_policy.py"
    return "test_auto_inject_legacy.py"

def classify_server_auth(name: str) -> str:
    if "bridge_key" in name or "loopback" in name or "bypasses_key" in name:
        return "test_server_auth_loopback.py"
    if "launcher" in name or "hot_reload" in name or "stop_8091" in name or "process_logs" in name:
        return "test_launcher_contract.py"
    if "episode_plan" in name:
        return "test_episode_plan_routes.py"
    if "topic_pack" in name or "fact_card" in name or "topic_graph" in name or "manual_research" in name:
        return "test_topic_pack_routes.py"
    if "chat_preview" in name or "sanitizer" in name or "interaction_sanitizer" in name:
        return "test_chat_preview_routes.py"
    if "session" in name or "recent_events" in name or "finalize" in name or "upsert" in name or "start_current" in name or "delete_session" in name:
        return "test_session_routes.py"
    return "test_control_ui_static_contract.py"
```

## Out of Scope

- 不改 `pytest.ini testpaths = tests`。
- 不改 product runtime。
- 不抽更多 fixture；先做 relocation，後續再精簡每個新檔的 imports。
- 不 stage/commit，因為目前 branch 已有其他未提交工作。

---

### Task 1: Mechanical Split Utility

**Files:**
- Create transient script in `.pyTestTemp/split_youtubebridge_tests.py`
- Modify generated target files under `YouTubeBridge/tests/`

- [x] **Step 1: Generate split script**

Create a temporary Python script that:

```python
from pathlib import Path
import re

ROOT = Path("YouTubeBridge/tests")

def find_blocks(text: str):
    lines = text.splitlines(keepends=True)
    starts = []
    for index, line in enumerate(lines):
        if re.match(r"^(async\s+def|def)\s+test_", line):
            start = index
            while start > 0 and lines[start - 1].startswith("@"):
                start -= 1
            starts.append(start)
    blocks = []
    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
        name_line = next(line for line in lines[start:end] if re.match(r"^(async\s+def|def)\s+test_", line))
        name = re.match(r"^(?:async\s+def|def)\s+(test_[A-Za-z0-9_]+)", name_line).group(1)
        blocks.append((start, end, name, "".join(lines[start:end])))
    return lines, blocks
```

- [x] **Step 2: Run mechanical split**

Run:

```powershell
@'
<complete script from this plan>
'@ | python -
```

Expected result: new split files are created and the three original large files retain no top-level `test_*` functions.

- [x] **Step 3: Verify old files have no collected tests**

Run:

```powershell
rg -n "^(async def|def) test_" YouTubeBridge\tests\test_bridge_engine_director.py YouTubeBridge\tests\test_bridge_engine_injection.py YouTubeBridge\tests\test_server_auth.py
```

Expected: no matches.

---

### Task 2: Director Split Verification

**Files:**
- Create: `YouTubeBridge/tests/test_director_turn_context.py`
- Create: `YouTubeBridge/tests/test_director_audience_preprocessing.py`
- Create: `YouTubeBridge/tests/test_director_prefetch_chain.py`
- Create: `YouTubeBridge/tests/test_director_loop_idle.py`

- [x] **Step 1: Run director split collect-only**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_director_turn_context.py YouTubeBridge\tests\test_director_audience_preprocessing.py YouTubeBridge\tests\test_director_prefetch_chain.py YouTubeBridge\tests\test_director_loop_idle.py --collect-only -q --basetemp=.pyTestTemp\basetemp-director-split-collect
```

Expected result includes:

```text
115 tests collected
```

- [x] **Step 2: Run focused after-main regression**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_director_prefetch_chain.py -q -k "does_not_reconsume_same_chained_prefetch_task or uses_audience_chain_task_instead_of_stale_prepare" --basetemp=.pyTestTemp\basetemp-director-split-after-main
```

Expected result:

```text
2 passed
```

---

### Task 3: Injection Split Verification

**Files:**
- Create: `YouTubeBridge/tests/test_inject_recent.py`
- Create: `YouTubeBridge/tests/test_auto_inject_legacy.py`
- Create: `YouTubeBridge/tests/test_auto_inject_director_owned.py`
- Create: `YouTubeBridge/tests/test_super_chat_interrupt_policy.py`
- Create: `YouTubeBridge/tests/test_audience_preprocessing_queue.py`

- [x] **Step 1: Run injection split collect-only**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_inject_recent.py YouTubeBridge\tests\test_auto_inject_legacy.py YouTubeBridge\tests\test_auto_inject_director_owned.py YouTubeBridge\tests\test_super_chat_interrupt_policy.py YouTubeBridge\tests\test_audience_preprocessing_queue.py --collect-only -q --basetemp=.pyTestTemp\basetemp-injection-split-collect
```

Expected result includes:

```text
47 tests collected
```

- [x] **Step 2: Run one focused case from each group**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_inject_recent.py::test_stream_result_drops_message_if_interrupted_before_broadcast YouTubeBridge\tests\test_auto_inject_legacy.py::test_dynamic_auto_inject_delay_accelerates_with_pending_count YouTubeBridge\tests\test_auto_inject_director_owned.py::test_director_owned_auto_inject_loop_schedules_audience_prepare_without_blocking YouTubeBridge\tests\test_super_chat_interrupt_policy.py::test_director_owned_super_chat_handoff_does_not_interrupt_same_event_batch YouTubeBridge\tests\test_audience_preprocessing_queue.py::test_inject_recent_preprocessing_requested_event_ids_drive_next_prepare_selection -q --basetemp=.pyTestTemp\basetemp-injection-split-focused
```

Expected result:

```text
5 passed
```

---

### Task 4: Server Auth Split Verification

**Files:**
- Create: `YouTubeBridge/tests/test_server_auth_loopback.py`
- Create: `YouTubeBridge/tests/test_launcher_contract.py`
- Create: `YouTubeBridge/tests/test_control_ui_static_contract.py`
- Create: `YouTubeBridge/tests/test_session_routes.py`
- Create: `YouTubeBridge/tests/test_topic_pack_routes.py`
- Create: `YouTubeBridge/tests/test_episode_plan_routes.py`
- Create: `YouTubeBridge/tests/test_chat_preview_routes.py`

- [x] **Step 1: Run server split collect-only**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_server_auth_loopback.py YouTubeBridge\tests\test_launcher_contract.py YouTubeBridge\tests\test_control_ui_static_contract.py YouTubeBridge\tests\test_session_routes.py YouTubeBridge\tests\test_topic_pack_routes.py YouTubeBridge\tests\test_episode_plan_routes.py YouTubeBridge\tests\test_chat_preview_routes.py --collect-only -q --basetemp=.pyTestTemp\basetemp-server-split-collect
```

Expected result includes:

```text
119 tests collected
```

- [x] **Step 2: Run one focused case from each group**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_server_auth_loopback.py::test_bridge_key_is_required_even_for_loopback YouTubeBridge\tests\test_launcher_contract.py::test_bridge_launchers_write_process_logs_under_runtime_log YouTubeBridge\tests\test_control_ui_static_contract.py::test_live_chat_uses_immediate_sse_refresh_for_chat_payloads YouTubeBridge\tests\test_session_routes.py::test_delete_session_endpoint_returns_deleted_session_id YouTubeBridge\tests\test_topic_pack_routes.py::test_topic_pack_delete_endpoint_removes_pack_and_related_rows YouTubeBridge\tests\test_episode_plan_routes.py::test_episode_plan_import_and_bind_endpoints YouTubeBridge\tests\test_chat_preview_routes.py::test_chat_preview_message_sanitizer_removes_debug_info -q --basetemp=.pyTestTemp\basetemp-server-split-focused
```

Expected result:

```text
7 passed
```

---

### Task 5: Final Verification

**Files:**
- Verify: `YouTubeBridge/tests/test_director_*.py`
- Verify: `YouTubeBridge/tests/test_auto_inject_*.py`
- Verify: `YouTubeBridge/tests/test_inject_recent.py`
- Verify: `YouTubeBridge/tests/test_super_chat_interrupt_policy.py`
- Verify: `YouTubeBridge/tests/test_audience_preprocessing_queue.py`
- Verify: `YouTubeBridge/tests/test_server_auth_loopback.py`
- Verify: `YouTubeBridge/tests/test_launcher_contract.py`
- Verify: `YouTubeBridge/tests/test_control_ui_static_contract.py`
- Verify: `YouTubeBridge/tests/test_session_routes.py`
- Verify: `YouTubeBridge/tests/test_topic_pack_routes.py`
- Verify: `YouTubeBridge/tests/test_episode_plan_routes.py`
- Verify: `YouTubeBridge/tests/test_chat_preview_routes.py`

- [x] **Step 1: Run full YouTubeBridge collect-only**

Run:

```powershell
python -m pytest YouTubeBridge\tests --collect-only -q --basetemp=.pyTestTemp\basetemp-three-large-split-final-collect
```

Expected result:

```text
669 tests collected
```

- [x] **Step 2: Run all split groups**

Run:

```powershell
python -m pytest YouTubeBridge\tests\test_director_turn_context.py YouTubeBridge\tests\test_director_audience_preprocessing.py YouTubeBridge\tests\test_director_prefetch_chain.py YouTubeBridge\tests\test_director_loop_idle.py YouTubeBridge\tests\test_inject_recent.py YouTubeBridge\tests\test_auto_inject_legacy.py YouTubeBridge\tests\test_auto_inject_director_owned.py YouTubeBridge\tests\test_super_chat_interrupt_policy.py YouTubeBridge\tests\test_audience_preprocessing_queue.py YouTubeBridge\tests\test_server_auth_loopback.py YouTubeBridge\tests\test_launcher_contract.py YouTubeBridge\tests\test_control_ui_static_contract.py YouTubeBridge\tests\test_session_routes.py YouTubeBridge\tests\test_topic_pack_routes.py YouTubeBridge\tests\test_episode_plan_routes.py YouTubeBridge\tests\test_chat_preview_routes.py -q --basetemp=.pyTestTemp\basetemp-three-large-split-final
```

Expected result:

```text
281 passed
```

- [x] **Step 3: Run diff check**

Run:

```powershell
git diff --check -- YouTubeBridge\tests docs\superpowers\plans\2026-05-18-youtubebridge-three-large-test-split.md
```

Expected result: no whitespace errors.

---

## Self-Review

- Spec coverage: Covers the three requested optimization steps: director, injection, and server auth split.
- Placeholder scan: No task contains placeholder wording; all verification commands and expected counts are concrete.
- Type consistency: Target filenames match commands and classification rules. Parametrized tests explain the collected counts: injection has 46 functions but 47 collected cases; server split has 117 functions but 119 collected cases.
