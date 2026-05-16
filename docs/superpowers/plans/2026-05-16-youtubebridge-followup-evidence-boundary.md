# YouTubeBridge Follow-up Evidence Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 planned LiveEpisodePlan 的第二位以上角色接話時，仍明確收到本輪可補角度、禁止新增未驗證事實、避免重複主張等邊界。

**Architecture:** 目前 follow-up prompt 已有 compact `live_reply_context` 與 `live_episode_reply_task`，但 task block 主要描述 reply stage 與 previous claims。此計畫把 `focus_policy.must_cover`、`forbidden_repetition`、`evidence_policy.allow_unverified_claims` 等 turn-level 邊界投影到 `live_episode_reply_task`，並在 follow-up prompt 中用短規則呈現，避免把完整 director context 或 raw Topic Card 洩漏給角色。

**Tech Stack:** `live_episode_plan` external context、`group_loop` turn task projection、`group_followup` prompt rendering、pytest。

---

## File Structure

- Modify `api/routers/chat/group_loop.py`: `_build_live_episode_reply_task()` 投影 turn-level focus/evidence/repetition 邊界。
- Modify `core/chat_orchestrator/group_followup.py`: render compact boundary lines inside `live_episode_reply_task` block。
- Modify `tests/test_chat_orchestrator_unit/test_group_loop.py`: 覆蓋 task projection。
- Modify `tests/test_chat_external_context.py`: 覆蓋 prompt 中有 compact boundary、沒有 raw context。

---

### Task 1: Project Turn-Level Boundaries Into Reply Task

**Files:**
- Modify: `api/routers/chat/group_loop.py`
- Test: `tests/test_chat_orchestrator_unit/test_group_loop.py`

- [ ] **Step 1: Write the failing projection test**

Extend `test_group_loop_adds_live_episode_reply_task_to_session_context_and_followup` in `tests/test_chat_orchestrator_unit/test_group_loop.py` by adding these fields to the `live_episode_plan` fixture:

```python
"focus_policy": {
    "must_cover": ["台灣平台播出狀況", "續作季數脈絡", "觀眾補番成本"],
},
"evidence_policy": {
    "allow_unverified_claims": False,
},
"forbidden_repetition": {
    "claims": ["不要再次說週榜只是即時快照"],
    "phrases": ["大風吹"],
},
```

Then add these assertions after `second_task = captured_session_ctx[1]["live_episode_reply_task"]`:

```python
assert second_task["must_cover"] == ["台灣平台播出狀況", "續作季數脈絡", "觀眾補番成本"]
assert second_task["allow_unverified_claims"] is False
assert second_task["forbidden_claims"] == ["不要再次說週榜只是即時快照"]
assert second_task["forbidden_phrases"] == ["大風吹"]
```

- [ ] **Step 2: Run the projection test and verify it fails**

Run:

```powershell
python -m pytest tests/test_chat_orchestrator_unit/test_group_loop.py::test_group_loop_adds_live_episode_reply_task_to_session_context_and_followup --basetemp=.pyTestTemp/basetemp-followup-boundary -q
```

Expected: FAIL because the new fields are not projected.

- [ ] **Step 3: Add compact list helper**

In `api/routers/chat/group_loop.py`, add:

```python
def _compact_prompt_list(value: Any, *, limit: int = 4) -> list[str]:
    items = value if isinstance(value, list) else []
    return [
        " ".join(str(item or "").split())
        for item in items
        if str(item or "").strip()
    ][:limit]
```

- [ ] **Step 4: Project boundaries in `_build_live_episode_reply_task()`**

Inside `_build_live_episode_reply_task()`, after `turn_contract`, add:

```python
focus_policy = (
    live_episode_plan.get("focus_policy")
    if isinstance(live_episode_plan.get("focus_policy"), dict)
    else {}
)
evidence_policy = (
    live_episode_plan.get("evidence_policy")
    if isinstance(live_episode_plan.get("evidence_policy"), dict)
    else {}
)
forbidden_repetition = (
    live_episode_plan.get("forbidden_repetition")
    if isinstance(live_episode_plan.get("forbidden_repetition"), dict)
    else {}
)
```

After the base `task` dict is created, add:

```python
must_cover = _compact_prompt_list(focus_policy.get("must_cover"), limit=4)
if must_cover:
    task["must_cover"] = must_cover
if "allow_unverified_claims" in evidence_policy:
    task["allow_unverified_claims"] = bool(evidence_policy.get("allow_unverified_claims"))
forbidden_claims = _compact_prompt_list(forbidden_repetition.get("claims"), limit=4)
if forbidden_claims:
    task["forbidden_claims"] = forbidden_claims
forbidden_phrases = _compact_prompt_list(forbidden_repetition.get("phrases"), limit=6)
if forbidden_phrases:
    task["forbidden_phrases"] = forbidden_phrases
```

- [ ] **Step 5: Run the projection test**

Run:

```powershell
python -m pytest tests/test_chat_orchestrator_unit/test_group_loop.py::test_group_loop_adds_live_episode_reply_task_to_session_context_and_followup --basetemp=.pyTestTemp/basetemp-followup-boundary -q
```

Expected: PASS.

---

### Task 2: Render Compact Boundary Rules in Follow-up Prompt

**Files:**
- Modify: `core/chat_orchestrator/group_followup.py`
- Test: `tests/test_chat_external_context.py`

- [ ] **Step 1: Write the failing prompt rendering test**

Append this test to `tests/test_chat_external_context.py`:

```python
def test_youtube_live_episode_followup_task_renders_turn_boundaries_without_raw_context():
    instruction = build_group_followup_instruction(
        {
            "user_prompt_original": (
                "Beat shape: taiwan_lineup_context.\n\n"
                "<live_episode_turn_context>\n"
                "這段 raw context 不應出現在 follow-up。\n"
                "</live_episode_turn_context>"
            ),
            "last_character_name": "可可",
            "last_reply": "台灣平台上的選擇變多了，但補番壓力也變重。",
            "conversation_intent": "continue_group_discussion",
            "routing_action": "new_speaker_reply_to_ai",
            "live_episode_reply_task": {
                "stage": "reaction_translate_or_new_angle",
                "turn_reply_index": 2,
                "max_role_replies": 2,
                "previous_claims": ["可可已說出台灣平台選擇變多"],
                "must_cover": ["續作季數脈絡", "觀眾補番成本"],
                "allow_unverified_claims": False,
                "forbidden_claims": ["不要再次說台灣平台選擇變多"],
                "forbidden_phrases": ["補番壓力"],
            },
        },
        "請自然延續直播。",
        {
            "external_chat_context": {
                "source": "youtube_live_director",
                "live_episode_plan": {
                    "turn_id": "seg_02_turn_02",
                    "turn_type": "analysis",
                    "evidence_brief": {
                        "facts_to_state": ["本輪只確認台灣平台與續作季數脈絡。"],
                        "source_boundaries": ["不能推論作品品質排名。"],
                        "do_not_delegate_to_character": True,
                    },
                },
            },
        },
    )

    assert "本輪可補角度：續作季數脈絡；觀眾補番成本" in instruction
    assert "不得新增未由 live_reply_context 支撐的事實或數字" in instruction
    assert "禁止重複主張：不要再次說台灣平台選擇變多" in instruction
    assert "避免沿用詞句：補番壓力" in instruction
    assert "這段 raw context 不應出現在 follow-up" not in instruction
    assert "<live_episode_turn_context>" not in instruction
```

- [ ] **Step 2: Run the prompt rendering test and verify it fails**

Run:

```powershell
python -m pytest tests/test_chat_external_context.py::test_youtube_live_episode_followup_task_renders_turn_boundaries_without_raw_context --basetemp=.pyTestTemp/basetemp-followup-boundary-prompt -q
```

Expected: FAIL because the boundary lines are not rendered.

- [ ] **Step 3: Render the new task fields**

In `core/chat_orchestrator/group_followup.py`, inside `_live_episode_reply_task_context()` after previous claims rendering, add:

```python
must_cover = [
    str(item).strip()
    for item in task.get("must_cover") or []
    if str(item).strip()
] if isinstance(task.get("must_cover"), list) else []
if must_cover:
    lines.append("本輪可補角度：" + "；".join(must_cover[:4]))

if task.get("allow_unverified_claims") is False:
    lines.append("不得新增未由 live_reply_context 支撐的事實或數字。")

forbidden_claims = [
    str(item).strip()
    for item in task.get("forbidden_claims") or []
    if str(item).strip()
] if isinstance(task.get("forbidden_claims"), list) else []
if forbidden_claims:
    lines.append("禁止重複主張：" + "；".join(forbidden_claims[:4]))

forbidden_phrases = [
    str(item).strip()
    for item in task.get("forbidden_phrases") or []
    if str(item).strip()
] if isinstance(task.get("forbidden_phrases"), list) else []
if forbidden_phrases:
    lines.append("避免沿用詞句：" + "；".join(forbidden_phrases[:6]))
```

- [ ] **Step 4: Run prompt tests**

Run:

```powershell
python -m pytest tests/test_chat_external_context.py::test_youtube_live_episode_followup_task_renders_turn_boundaries_without_raw_context tests/test_chat_external_context.py::test_youtube_live_episode_followup_uses_compact_live_reply_context tests/test_chat_external_context.py::test_youtube_live_episode_followup_injection_suppresses_full_director_context --basetemp=.pyTestTemp/basetemp-followup-boundary-prompt -q
```

Expected: PASS.

---

### Task 3: Final Verification and Commit

**Files:**
- Verify: `api/routers/chat/group_loop.py`
- Verify: `core/chat_orchestrator/group_followup.py`
- Verify: `tests/test_chat_orchestrator_unit/test_group_loop.py`
- Verify: `tests/test_chat_external_context.py`

- [ ] **Step 1: Run focused regression set**

Run:

```powershell
python -m pytest tests/test_chat_orchestrator_unit/test_group_loop.py tests/test_chat_external_context.py --basetemp=.pyTestTemp/basetemp-followup-evidence-boundary -q
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
git add api/routers/chat/group_loop.py core/chat_orchestrator/group_followup.py tests/test_chat_orchestrator_unit/test_group_loop.py tests/test_chat_external_context.py
git commit -m "Carry live episode boundaries into follow-up prompts"
```

Expected: commit succeeds with only the four listed files.
