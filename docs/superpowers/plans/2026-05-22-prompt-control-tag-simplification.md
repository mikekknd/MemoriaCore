# Prompt Control Tag Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Shorten dynamic prompt control tags and guarantee the real latest user input is wrapped in `<user_input>` at the final-chat prompt tail.

**Architecture:** Keep prompt templates in `prompts_default.json`, keep final message assembly in `core/prompt_utils.py`, and keep opening-penalty/tool-control insertion before the trailing user-input block. Do not change DB persistence, memory extraction, router inputs, or group follow-up control turns.

**Tech Stack:** Python 3, pytest, centralized prompt templates through `PromptManager`, XML-like prompt helpers in `core/xml_prompt.py`.

---

## File Structure

- Modify `prompts_default.json`: shorten only these default templates:
  - `environment_context_block`: remove `source="system_control"` but keep `<environment_context>` and existing `<current_time>` / `{weather_block}` content.
  - `user_identity_block`: replace wrapper with single `<user user_name="{user_name}" />`.
  - `emotional_trajectory_block`: replace wrapper with single `<previous_internal_thought>{internal_thought}</previous_internal_thought>`.
  - `opening_penalty_instruction`: remove `source="system_control"`.
- Modify `core/prompt_utils.py`: make `format_latest_user_message_for_llm()` always return a `<user_input>` block; add a helper that inserts later control blocks before a trailing `<user_input>`.
- Modify `core/opening_penalty.py`: use the helper so opening-penalty instructions do not move after `<user_input>`.
- Modify `core/chat_orchestrator/persona_agent.py`: use the helper so tool result context does not move after `<user_input>`.
- Modify tests:
  - `tests/test_prompt_utils.py`
  - `tests/test_opening_penalty.py`
  - `tests/test_chat_external_context.py`
  - `tests/test_chat_orchestrator_unit/test_memory_context.py`
  - `tests/test_chat_orchestrator_unit/test_coordinator.py`

No commit step is included because the user requested implementation and verification, not a commit. Keep all edits scoped to the listed files.

---

### Task 1: Write Failing Prompt Template Tests

**Files:**
- Modify: `tests/test_prompt_utils.py`
- Test: `tests/test_prompt_utils.py`

- [ ] **Step 1: Update fake prompt templates to the intended compact shape**

In `_FakePromptManager.get()`, use:

```python
return {
    "environment_context_block": (
        "<environment_context>\n"
        "<current_time>{current_time}</current_time>{weather_block}\n"
        "</environment_context>"
    ),
    "user_identity_block": '<user user_name="{user_name}" />',
    "external_chat_context_block": (
        '<external_chat_context source="{source}" trusted="false">\n'
        "{context_text}\n"
        "</external_chat_context>"
    ),
    "director_external_context_block": (
        '<director_context source="{source}" trust_boundary="system_generated">\n'
        "{context_text}\n"
        "</director_context>"
    ),
    "runtime_context_block": (
        "<runtime_context>\n"
        "{context_text}\n"
        "</runtime_context>"
    ),
    "emotional_trajectory_block": (
        "<previous_internal_thought>{internal_thought}</previous_internal_thought>"
    ),
}.get(key, "")
```

- [ ] **Step 2: Add default-template contract test**

Add:

```python
def test_default_prompt_control_blocks_use_compact_tags():
    from core.prompt_manager import get_prompt_manager

    pm = get_prompt_manager()

    env_template = pm.get_default("environment_context_block")
    assert env_template == (
        "<environment_context>\n"
        "<current_time>{current_time}</current_time>{weather_block}\n"
        "</environment_context>"
    )

    assert pm.get_default("user_identity_block") == '<user user_name="{user_name}" />'
    assert pm.get_default("emotional_trajectory_block") == (
        "<previous_internal_thought>{internal_thought}</previous_internal_thought>"
    )
    assert pm.get_default("opening_penalty_instruction").startswith(
        "<opening_penalty_instruction>\n"
    )
    assert 'source="system_control"' not in pm.get_default("opening_penalty_instruction")
```

- [ ] **Step 3: Update existing identity/emotional assertions**

Change user-identity assertions to look for `<user user_name="..." />` and no `<user_identity>`. Keep the existing assertions that backend IDs are absent.

Change emotional-trajectory assertions to look for `<previous_internal_thought>...` and keep the existing same-character / YouTube-live omission checks.

- [ ] **Step 4: Run failing test slice**

Run:

```powershell
python -m pytest tests/test_prompt_utils.py --basetemp=.pyTestTemp/basetemp
```

Expected before implementation: failures showing default templates still include old wrappers and `format_latest_user_message_for_llm()` has not yet wrapped single-session input in `<user_input>`.

---

### Task 2: Add User Input Tail Contract Tests

**Files:**
- Modify: `tests/test_prompt_utils.py`
- Modify: `tests/test_chat_external_context.py`
- Modify: `tests/test_chat_orchestrator_unit/test_memory_context.py`
- Modify: `tests/test_chat_orchestrator_unit/test_coordinator.py`

- [ ] **Step 1: Add prompt-utils tests for `<user_input>`**

Replace `test_latest_user_message_single_session_stays_plain()` with:

```python
def test_latest_user_message_single_session_wraps_user_input():
    wrapped = prompt_utils.format_latest_user_message_for_llm(
        "嗚嗚，可可都無視我拉!",
        session_ctx={"session_mode": "single", "active_character_ids": ["char-a"]},
    )

    assert wrapped == "<user_input>\n嗚嗚，可可都無視我拉!\n</user_input>"
```

Update `test_latest_user_message_wraps_group_human_speaker()` to expect:

```python
assert '<user_input speaker="human_user" user_name="mikekknd">' in wrapped
assert "<latest_user_message" not in wrapped
assert "user-1" not in wrapped
assert "嗚嗚，可可都無視我拉!" in wrapped
assert wrapped.strip().endswith("</user_input>")
```

- [ ] **Step 2: Add helper test for control insertion before user input**

Add:

```python
def test_append_control_before_user_input_tail_keeps_user_input_last():
    content = (
        "<environment_context>\n"
        "<current_time>2026-05-22 13:46:28 CST</current_time>\n"
        "</environment_context>\n\n"
        "<user_input>\n"
        "今天喝什麼？\n"
        "</user_input>"
    )
    control = "<opening_penalty_instruction>\n禁止用舊開頭。\n</opening_penalty_instruction>"

    merged = prompt_utils.append_control_before_user_input_tail(content, control)

    assert merged.index("<opening_penalty_instruction>") < merged.index("<user_input>")
    assert merged.strip().endswith("</user_input>")
```

- [ ] **Step 3: Strengthen final-chat prompt order tests**

In `tests/test_chat_orchestrator_unit/test_memory_context.py`, update the tests that inspect `latest_user` to assert:

```python
assert latest_user.strip().endswith("</user_input>")
assert latest_user.index("<retrieved_memory_context>") < latest_user.index("<user_input>")
```

For runtime context test, assert:

```python
assert latest_user.index("<runtime_context>") < latest_user.index("<user_input>")
```

In `tests/test_chat_orchestrator_unit/test_coordinator.py`, replace the group assertion for `<latest_user_message...>` with:

```python
assert "<latest_user_message" not in messages[-1]["content"]
```

In `tests/test_chat_external_context.py`, update static hidden-prompt fixture examples to use `<environment_context>` without `source="system_control"`.

- [ ] **Step 4: Run failing slices**

Run:

```powershell
python -m pytest tests/test_prompt_utils.py tests/test_chat_external_context.py tests/test_chat_orchestrator_unit/test_memory_context.py tests/test_chat_orchestrator_unit/test_coordinator.py --basetemp=.pyTestTemp/basetemp
```

Expected before implementation: failures around user-input wrapping and prompt tail ordering.

---

### Task 3: Implement Compact Templates and User Input Wrapping

**Files:**
- Modify: `prompts_default.json`
- Modify: `core/prompt_utils.py`

- [ ] **Step 1: Update default prompt templates**

Set these exact template strings:

```json
"environment_context_block": {
  "template": "<environment_context>\n<current_time>{current_time}</current_time>{weather_block}\n</environment_context>"
}
```

```json
"user_identity_block": {
  "template": "<user user_name=\"{user_name}\" />"
}
```

```json
"emotional_trajectory_block": {
  "template": "<previous_internal_thought>{internal_thought}</previous_internal_thought>"
}
```

```json
"opening_penalty_instruction": {
  "template": "<opening_penalty_instruction>\n你最近的 `reply` 開頭已重複使用以下片段，這一輪禁止用它們起手：\n{blocked_openings_json}\n\n請保持原本人格與語氣，但改用不同的第一句開場方式。這只限制 `reply` 欄位的最前面，不限制後文自然使用相同詞彙。\n</opening_penalty_instruction>"
}
```

- [ ] **Step 2: Add prompt-utils helper and user-input wrapper**

In `core/prompt_utils.py`, add a tail regex near the top-level helpers:

```python
_USER_INPUT_TAIL = re.compile(r'\s*<user_input\b[^>]*>.*?</user_input>\s*\Z', re.DOTALL)
```

Add:

```python
def append_control_before_user_input_tail(content: str, control: str) -> str:
    text = str(content or "")
    control_text = str(control or "").strip()
    if not control_text:
        return text

    match = _USER_INPUT_TAIL.search(text)
    if not match:
        return text + "\n\n" + control_text

    before = text[:match.start()].rstrip()
    user_input = match.group(0).lstrip().rstrip()
    return "\n\n".join(part for part in (before, control_text, user_input) if part)
```

Replace `format_latest_user_message_for_llm()` with:

```python
def format_latest_user_message_for_llm(content: str, session_ctx: dict | None = None) -> str:
    """以明確的 user_input 區塊標示最新真人輸入，避免控制區塊與人類輸入混淆。"""
    attrs = None
    if _is_group_prompt_context(session_ctx):
        ctx = session_ctx or {}
        attrs = {
            "speaker": "human_user",
            "user_name": ctx.get("user_name") or "",
        }
    return xml_block("user_input", content, attrs=attrs)
```

- [ ] **Step 3: Run prompt-utils tests**

Run:

```powershell
python -m pytest tests/test_prompt_utils.py --basetemp=.pyTestTemp/basetemp
```

Expected: all tests in `tests/test_prompt_utils.py` pass.

---

### Task 4: Keep Later Control Blocks Before `<user_input>`

**Files:**
- Modify: `core/opening_penalty.py`
- Modify: `core/chat_orchestrator/persona_agent.py`
- Modify: `tests/test_opening_penalty.py`

- [ ] **Step 1: Add opening-penalty test**

Add to `tests/test_opening_penalty.py`:

```python
def test_apply_instruction_keeps_user_input_at_tail():
    mgr = OpeningPenaltyManager()
    messages = [
        {
            "role": "user",
            "content": (
                "<environment_context>\n"
                "<current_time>2026-05-22 13:46:28 CST</current_time>\n"
                "</environment_context>\n\n"
                "<user_input>\n"
                "今天喝什麼？\n"
                "</user_input>"
            ),
        }
    ]
    instruction = "<opening_penalty_instruction>\n禁止用舊開頭。\n</opening_penalty_instruction>"

    mgr.apply_instruction_to_messages(messages, instruction)

    content = messages[-1]["content"]
    assert content.index("<opening_penalty_instruction>") < content.index("<user_input>")
    assert content.strip().endswith("</user_input>")
```

- [ ] **Step 2: Update opening penalty insertion**

In `core/opening_penalty.py`, import:

```python
from core.prompt_utils import append_control_before_user_input_tail
```

Then replace the last-user append body with:

```python
messages[-1] = {
    **messages[-1],
    "content": append_control_before_user_input_tail(
        str(messages[-1].get("content", "")),
        instruction,
    ),
}
```

- [ ] **Step 3: Update tool-context insertion**

In `core/chat_orchestrator/persona_agent.py`, import:

```python
from core.prompt_utils import append_control_before_user_input_tail
```

Then replace:

```python
"content": final_messages[-1]["content"] + tool_notice,
```

with:

```python
"content": append_control_before_user_input_tail(
    final_messages[-1]["content"],
    tool_notice,
),
```

- [ ] **Step 4: Run opening penalty and persona tests**

Run:

```powershell
python -m pytest tests/test_opening_penalty.py tests/test_chat_orchestrator_unit/test_persona_agent.py --basetemp=.pyTestTemp/basetemp
```

Expected: all selected tests pass.

---

### Task 5: Final Verification

**Files:**
- Verify only.

- [ ] **Step 1: Run focused prompt-contract suite**

Run:

```powershell
python -m pytest tests/test_prompt_utils.py tests/test_opening_penalty.py tests/test_chat_external_context.py tests/test_chat_orchestrator_unit/test_memory_context.py tests/test_chat_orchestrator_unit/test_coordinator.py tests/test_chat_orchestrator_unit/test_persona_agent.py --basetemp=.pyTestTemp/basetemp
```

Expected: all selected tests pass.

- [ ] **Step 2: Check formatting / whitespace**

Run:

```powershell
git diff --check -- prompts_default.json core/prompt_utils.py core/opening_penalty.py core/chat_orchestrator/persona_agent.py tests/test_prompt_utils.py tests/test_opening_penalty.py tests/test_chat_external_context.py tests/test_chat_orchestrator_unit/test_memory_context.py tests/test_chat_orchestrator_unit/test_coordinator.py
```

Expected: exit code 0. CRLF warnings are acceptable; whitespace errors are not.

- [ ] **Step 3: Inspect final diff scope**

Run:

```powershell
git diff -- prompts_default.json core/prompt_utils.py core/opening_penalty.py core/chat_orchestrator/persona_agent.py tests/test_prompt_utils.py tests/test_opening_penalty.py tests/test_chat_external_context.py tests/test_chat_orchestrator_unit/test_memory_context.py tests/test_chat_orchestrator_unit/test_coordinator.py
git status -sb
```

Expected: only the plan file and the listed implementation/test files are newly changed by this task, aside from pre-existing unrelated working-tree modifications.

---

## Self-Review

- Spec coverage: covers environment wrapper simplification while preserving weather, compact user identity, same-character internal thought, opening penalty tag simplification, user input wrapping, and user input tail ordering.
- Placeholder scan: no TBD/TODO/fill-later placeholders.
- Type consistency: helper name `append_control_before_user_input_tail` is used consistently in tests, opening penalty, and persona agent.
