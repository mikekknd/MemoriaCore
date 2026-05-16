# YouTubeBridge Router Post Policy Trace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 `runtime/llm_trace.jsonl` 能看出 group router 的 raw LLM route 與 post-policy normalized route 差異，避免把 policy 改寫誤判成 router 忽略 `stop_no_new_value`。

**Architecture:** 保留現有 `group_router` prompt/response 記錄，另外在 post-policy 改寫發生時寫入一筆 `system_event`。事件內容包含 raw action/target、validated action/target、final action/target、closing mode、policy name 與原因；不把完整 prompt、角色 prompt 或 hidden context 放進新事件。

**Tech Stack:** `SystemLogger` JSONL logging、`GroupRouterResult`、pytest。

---

## File Structure

- Modify `core/system_logger.py`: 讓 `log_system_event()` 支援 optional `details`。
- Modify `core/chat_orchestrator/group_router.py`: 在 route 被 enforcement/policy 改寫時寫入 post-policy trace event。
- Modify `tests/test_system_logger.py`: 覆蓋 `log_system_event(..., details=...)`。
- Modify `tests/test_chat_orchestrator_unit/test_group_router.py`: 覆蓋 group closing raw stop 被 post-policy 轉成 unspoken speaker 時的 trace event。

---

### Task 1: Structured Details for System Events

**Files:**
- Modify: `core/system_logger.py`
- Test: `tests/test_system_logger.py`

- [ ] **Step 1: Write the failing system event details test**

Append this test to `tests/test_system_logger.py`:

```python
def test_system_logger_system_event_accepts_details(monkeypatch):
    log_path = os.path.join(os.getcwd(), ".pyTestTemp", "system_logger_event_details_test.jsonl")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    if os.path.exists(log_path):
        os.remove(log_path)
    monkeypatch.setattr("core.system_logger._LOG_FILE", str(log_path))
    SystemLogger._reset_for_tests()

    SystemLogger.log_system_event(
        "group_router_post_policy",
        "route adjusted by youtube_live_group_closing",
        details={
            "raw_action": "stop_no_new_value",
            "final_action": "new_speaker_reply_to_ai",
        },
    )

    with open(log_path, "r", encoding="utf-8") as f:
        entry = json.loads(f.read().strip())
    assert entry["category"] == "group_router_post_policy"
    assert entry["details"] == {
        "raw_action": "stop_no_new_value",
        "final_action": "new_speaker_reply_to_ai",
    }
```

- [ ] **Step 2: Run the new logger test and verify it fails**

Run:

```powershell
python -m pytest tests/test_system_logger.py::test_system_logger_system_event_accepts_details --basetemp=.pyTestTemp/basetemp-router-trace -q
```

Expected: FAIL with `TypeError` because `log_system_event()` does not accept `details`.

- [ ] **Step 3: Implement `details` support**

Change `core/system_logger.py`:

```python
@staticmethod
def log_system_event(category, message, details: dict | None = None):
    """通用系統關鍵事件紀錄"""
    ts = SystemLogger._get_time()
    _console_print(f"\n[{ts}] 系統事件 [{category}]")
    _console_print(f"  -> {message}")
    _console_print(f"{'-'*60}\n")

    entry = {
        "timestamp": SystemLogger._get_iso_time(),
        "type": "system_event",
        "category": category,
        "message": message,
    }
    if details:
        entry["details"] = details
    SystemLogger._write_entry(entry)
```

- [ ] **Step 4: Run logger tests**

Run:

```powershell
python -m pytest tests/test_system_logger.py --basetemp=.pyTestTemp/basetemp-router-trace -q
```

Expected: PASS.

---

### Task 2: Log Router Post-Policy Adjustments

**Files:**
- Modify: `core/chat_orchestrator/group_router.py`
- Test: `tests/test_chat_orchestrator_unit/test_group_router.py`

- [ ] **Step 1: Write the failing post-policy trace test**

Append this test to `tests/test_chat_orchestrator_unit/test_group_router.py`:

```python
def test_youtube_live_group_closing_logs_post_policy_route_adjustment(monkeypatch):
    events = []

    def capture_event(category, message, details=None):
        events.append({"category": category, "message": message, "details": details or {}})

    monkeypatch.setattr(
        "core.chat_orchestrator.group_router.SystemLogger.log_system_event",
        capture_event,
    )
    router = _Router({
        "conversation_intent": "single_response",
        "action": "stop_no_new_value",
        "target_character_id": None,
        "reason": "最近一則 AI 已完成回顧與道別",
    })

    result = run_group_router(
        [
            {"role": "user", "content": "請做本場最後收尾，正式道別，不要開新話題。"},
            {"role": "assistant", "content": "A 已回顧重點並正式道別。", "character_id": "char-a", "character_name": "角色A"},
        ],
        _chars(),
        router,
        last_speaker_id="char-a",
        honor_mentions=False,
        bot_turn_index=1,
        max_bot_turns=2,
        discussion_mode="youtube_live",
    )

    assert result.should_respond is True
    assert result.target_character_id == "char-b"
    assert events == [
        {
            "category": "group_router_post_policy",
            "message": "route adjusted by youtube_live_group_closing",
            "details": {
                "policy": "youtube_live_group_closing",
                "closing_mode": "group_closing",
                "raw_action": "stop_no_new_value",
                "raw_target_character_id": None,
                "final_action": "new_speaker_reply_to_ai",
                "final_target_character_id": "char-b",
                "final_conversation_intent": "continue_group_discussion",
            },
        }
    ]
```

- [ ] **Step 2: Run the post-policy trace test and verify it fails**

Run:

```powershell
python -m pytest tests/test_chat_orchestrator_unit/test_group_router.py::test_youtube_live_group_closing_logs_post_policy_route_adjustment --basetemp=.pyTestTemp/basetemp-router-post-policy -q
```

Expected: FAIL because no system event is logged.

- [ ] **Step 3: Add a route-change helper**

In `core/chat_orchestrator/group_router.py`, add helpers near the YouTube live policy helpers:

```python
def _route_identity(result: GroupRouterResult) -> tuple[bool, str | None, str, str]:
    return (
        bool(result.should_respond),
        result.target_character_id,
        str(result.action or ""),
        str(result.conversation_intent or ""),
    )


def _log_group_router_policy_adjustment(
    *,
    policy: str,
    closing_mode: str,
    raw_result: GroupRouterResult,
    final_result: GroupRouterResult,
) -> None:
    if _route_identity(raw_result) == _route_identity(final_result):
        return
    SystemLogger.log_system_event(
        "group_router_post_policy",
        f"route adjusted by {policy}",
        details={
            "policy": policy,
            "closing_mode": closing_mode,
            "raw_action": raw_result.action,
            "raw_target_character_id": raw_result.target_character_id,
            "final_action": final_result.action,
            "final_target_character_id": final_result.target_character_id,
            "final_conversation_intent": final_result.conversation_intent,
        },
    )
```

- [ ] **Step 4: Call the helper only around policy output**

In `run_group_router()`, keep named variables for each stage:

```python
raw_result = _validate_action_result(...)
enforced_result = _enforce_youtube_live_speaker_rules(...)
final_result = _apply_youtube_live_continuation_policy(...)
if normalized_discussion_mode == "youtube_live":
    _log_group_router_policy_adjustment(
        policy="youtube_live_group_closing" if closing_mode == "group_closing" else "youtube_live_continuation_policy",
        closing_mode=closing_mode,
        raw_result=raw_result,
        final_result=final_result,
    )
return final_result
```

If enforcement changes a non-closing route, the policy name should be `"youtube_live_continuation_policy"` and the same event shape should be used.

- [ ] **Step 5: Run router tests**

Run:

```powershell
python -m pytest tests/test_chat_orchestrator_unit/test_group_router.py --basetemp=.pyTestTemp/basetemp-router-post-policy -q
```

Expected: PASS.

---

### Task 3: Final Verification and Commit

**Files:**
- Verify: `core/system_logger.py`
- Verify: `core/chat_orchestrator/group_router.py`
- Verify: `tests/test_system_logger.py`
- Verify: `tests/test_chat_orchestrator_unit/test_group_router.py`

- [ ] **Step 1: Run focused regression set**

Run:

```powershell
python -m pytest tests/test_system_logger.py tests/test_chat_orchestrator_unit/test_group_router.py --basetemp=.pyTestTemp/basetemp-router-trace-final -q
```

Expected: PASS.

- [ ] **Step 2: Run diff check**

Run:

```powershell
git diff --check
```

Expected: exit code 0. Windows LF/CRLF reminders are acceptable if no whitespace error is reported.

- [ ] **Step 3: Commit**

Run:

```powershell
git add core/system_logger.py core/chat_orchestrator/group_router.py tests/test_system_logger.py tests/test_chat_orchestrator_unit/test_group_router.py
git commit -m "Log group router post-policy adjustments"
```

Expected: commit succeeds with only the four listed files.
