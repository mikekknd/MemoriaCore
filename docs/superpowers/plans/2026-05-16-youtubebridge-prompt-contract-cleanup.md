# YouTubeBridge Prompt Contract Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 降低 live prompt 重複與衝突，並把 `internal_thought` 長度限制從純 prompt 建議提升為 schema/解析層合約。

**Architecture:** 將 group router 的 closing-only 規則移出一般 router template，只在 `closing_mode != "none"` 時附加；保留 YouTube live 基礎規則但避免每次非收尾路由都帶完整道別規則。另在 chat response schema 加入 `maxLength`，並在 response parsing 時正規化 `internal_thought`，避免 provider 未遵守 schema 時把過長 hidden thought 寫入 debug/persona state。

**Tech Stack:** `prompts_default.json`、PromptManager、group router、chat response schema、pytest。

---

## File Structure

- Modify `prompts_default.json`: 新增 `group_router_youtube_live_closing_rules`，縮短 `group_router_system` 的 closing 細節。
- Modify `core/chat_orchestrator/group_router.py`: 只在 closing mode 時附加 closing-only prompt。
- Modify `core/chat_orchestrator/generation_context.py`: schema 中加入 `internal_thought.maxLength = 40`，並提供 normalization helper。
- Modify `api/routers/chat/orchestration.py`: response parsing 後套用 `normalize_internal_thought()`。
- Modify `tests/test_chat_orchestrator_unit/test_group_router.py`: 覆蓋非收尾 prompt 不含 group closing 細節、收尾 prompt 才含。
- Modify `tests/test_chat_orchestrator_unit/test_memory_context.py`: 覆蓋 schema maxLength。
- Modify `tests/test_chat_orchestrator_unit/test_coordinator.py`: 覆蓋過長 `internal_thought` 會被截斷。

---

### Task 1: Split Closing-Only Router Prompt

**Files:**
- Modify: `prompts_default.json`
- Modify: `core/chat_orchestrator/group_router.py`
- Test: `tests/test_chat_orchestrator_unit/test_group_router.py`

- [ ] **Step 1: Write failing prompt split tests**

Append these tests to `tests/test_chat_orchestrator_unit/test_group_router.py`:

```python
def test_youtube_live_non_closing_router_prompt_omits_closing_only_rules():
    router = _Router({
        "conversation_intent": "single_response",
        "action": "stop_no_new_value",
        "target_character_id": None,
        "reason": "無新增價值",
    })

    run_group_router(
        [{"role": "user", "content": "這段榜單分析先到這裡。"}],
        _chars(),
        router,
        honor_mentions=False,
        bot_turn_index=0,
        max_bot_turns=2,
        discussion_mode="youtube_live",
    )

    prompt_text = "\n".join(str(m.get("content", "")) for m in router.args[1])
    assert "closing_mode 表示收尾路由目標" not in prompt_text
    assert "每位可用角色最多一次簡短道別" not in prompt_text
    assert "youtube_live_closing_rules" not in prompt_text


def test_youtube_live_closing_router_prompt_appends_closing_only_rules():
    router = _Router({
        "conversation_intent": "continue_group_discussion",
        "action": "new_speaker_reply_to_ai",
        "target_character_id": "char-b",
        "reason": "群組收尾仍有未發言角色",
    })

    run_group_router(
        [
            {"role": "user", "content": "請做本場最後收尾，正式道別，不要開新話題。"},
            {"role": "assistant", "content": "A 已道別。", "character_id": "char-a", "character_name": "角色A"},
        ],
        _chars(),
        router,
        last_speaker_id="char-a",
        honor_mentions=False,
        bot_turn_index=1,
        max_bot_turns=2,
        discussion_mode="youtube_live",
    )

    prompt_text = "\n".join(str(m.get("content", "")) for m in router.args[1])
    assert "youtube_live_closing_rules" in prompt_text
    assert "closing_mode=group_closing" in prompt_text
    assert "仍有未發言角色尚未完成簡短道別" in prompt_text
```

- [ ] **Step 2: Run the prompt split tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_chat_orchestrator_unit/test_group_router.py::test_youtube_live_non_closing_router_prompt_omits_closing_only_rules tests/test_chat_orchestrator_unit/test_group_router.py::test_youtube_live_closing_router_prompt_appends_closing_only_rules --basetemp=.pyTestTemp/basetemp-prompt-contract -q
```

Expected: FAIL because closing rules are still inside the base prompt and no separate prompt key exists.

- [ ] **Step 3: Add the new prompt key**

In `prompts_default.json`, add a sibling key after `group_router_system`:

```json
"group_router_youtube_live_closing_rules": {
  "label": "YouTube Live 收尾路由規則",
  "params": ["closing_mode"],
  "template": "<youtube_live_closing_rules>\nclosing_mode={closing_mode}\n- single_closing：一位角色完成回顧與道別即可停止。\n- group_closing：每位可用角色最多一次簡短道別；若仍有未發言角色尚未完成簡短道別，不得只因最近一則 AI 已道別而停止。\n- group_closing 的下一位角色只能接住上一位的收尾並完成自己的道別，不得開新話題，不得提出問題。\n</youtube_live_closing_rules>"
}
```

Keep valid JSON punctuation when inserting the new key.

- [ ] **Step 4: Shorten the base router prompt**

In `prompts_default.json` `group_router_system.template`, remove these base-prompt details:

```text
- closing_mode 表示收尾路由目標：single_closing 一位角色完成即可；group_closing 則每位可用角色最多一次簡短道別。
```

Replace the current closing-specific `stop_gate` item with:

```text
2. original_user_request 要求最後收尾、正式道別、結束本場或不要開新話題，且最近一則 AI 已完成回顧與道別：action=stop_no_new_value。若有 youtube_live_closing_rules，依該區塊覆寫本條。
```

Remove the current closing-specific speaker selection item and keep normal speaker selection numbering stable.

- [ ] **Step 5: Append closing prompt only when needed**

In `core/chat_orchestrator/group_router.py`, after appending `_youtube_live_group_router_rules(live_hosting)`, add:

```python
if normalized_discussion_mode == "youtube_live" and closing_mode != "none":
    prompt += "\n\n" + get_prompt_manager().get("group_router_youtube_live_closing_rules").format(
        closing_mode=closing_mode,
    )
```

- [ ] **Step 6: Run router prompt tests**

Run:

```powershell
python -m pytest tests/test_chat_orchestrator_unit/test_group_router.py --basetemp=.pyTestTemp/basetemp-prompt-contract-router -q
```

Expected: PASS.

---

### Task 2: Enforce `internal_thought` Length

**Files:**
- Modify: `core/chat_orchestrator/generation_context.py`
- Modify: `api/routers/chat/orchestration.py`
- Test: `tests/test_chat_orchestrator_unit/test_memory_context.py`
- Test: `tests/test_chat_orchestrator_unit/test_coordinator.py`

- [ ] **Step 1: Write the schema test**

Append this test to `tests/test_chat_orchestrator_unit/test_memory_context.py`:

```python
def test_chat_response_schema_limits_internal_thought_length():
    from core.chat_orchestrator.generation_context import build_chat_response_schema

    schema = build_chat_response_schema()

    assert schema["properties"]["internal_thought"]["type"] == "string"
    assert schema["properties"]["internal_thought"]["maxLength"] == 40
```

- [ ] **Step 2: Write the normalization helper test**

Append this test to `tests/test_chat_orchestrator_unit/test_memory_context.py`:

```python
def test_normalize_internal_thought_trims_to_40_characters():
    from core.chat_orchestrator.generation_context import normalize_internal_thought

    text = "這是一段超過四十個字的內心獨白，用來確認解析層會穩定截斷多餘內容"

    assert normalize_internal_thought(text) == text[:40]
    assert len(normalize_internal_thought(text)) == 40
    assert normalize_internal_thought(None) is None
```

- [ ] **Step 3: Run the new schema tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_chat_orchestrator_unit/test_memory_context.py::test_chat_response_schema_limits_internal_thought_length tests/test_chat_orchestrator_unit/test_memory_context.py::test_normalize_internal_thought_trims_to_40_characters --basetemp=.pyTestTemp/basetemp-internal-thought -q
```

Expected: FAIL because schema lacks `maxLength` and helper does not exist.

- [ ] **Step 4: Implement schema and helper**

In `core/chat_orchestrator/generation_context.py`, update the schema and add the helper:

```python
def normalize_internal_thought(value: object, *, max_chars: int = 40) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return ""
    return text[:max_chars]


def build_chat_response_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "internal_thought": {"type": "string", "maxLength": 40},
            "reply": {"type": "string"},
            "extracted_entities": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["internal_thought", "reply", "extracted_entities"],
    }
```

- [ ] **Step 5: Apply normalization in response parsing**

In `api/routers/chat/orchestration.py`, import the helper:

```python
from core.chat_orchestrator.generation_context import normalize_internal_thought
```

Replace:

```python
inner_thought = parsed.get("internal_thought")
```

with:

```python
inner_thought = normalize_internal_thought(parsed.get("internal_thought"))
```

- [ ] **Step 6: Run schema/helper tests**

Run:

```powershell
python -m pytest tests/test_chat_orchestrator_unit/test_memory_context.py::test_chat_response_schema_limits_internal_thought_length tests/test_chat_orchestrator_unit/test_memory_context.py::test_normalize_internal_thought_trims_to_40_characters --basetemp=.pyTestTemp/basetemp-internal-thought -q
```

Expected: PASS.

---

### Task 3: Final Verification and Commit

**Files:**
- Verify: `prompts_default.json`
- Verify: `core/chat_orchestrator/group_router.py`
- Verify: `core/chat_orchestrator/generation_context.py`
- Verify: `api/routers/chat/orchestration.py`
- Verify: `tests/test_chat_orchestrator_unit/test_group_router.py`
- Verify: `tests/test_chat_orchestrator_unit/test_memory_context.py`
- Verify: `tests/test_chat_orchestrator_unit/test_coordinator.py`

- [ ] **Step 1: Run focused regression set**

Run:

```powershell
python -m pytest tests/test_chat_orchestrator_unit/test_group_router.py tests/test_chat_orchestrator_unit/test_memory_context.py tests/test_chat_orchestrator_unit/test_coordinator.py --basetemp=.pyTestTemp/basetemp-prompt-contract-final -q
```

Expected: PASS.

- [ ] **Step 2: Run JSON parse check for prompts**

Run:

```powershell
python -m json.tool prompts_default.json > $null
```

Expected: exit code 0.

- [ ] **Step 3: Run diff check**

Run:

```powershell
git diff --check
```

Expected: exit code 0. Windows LF/CRLF reminders are acceptable if no whitespace error is reported.

- [ ] **Step 4: Commit**

Run:

```powershell
git add prompts_default.json core/chat_orchestrator/group_router.py core/chat_orchestrator/generation_context.py api/routers/chat/orchestration.py tests/test_chat_orchestrator_unit/test_group_router.py tests/test_chat_orchestrator_unit/test_memory_context.py tests/test_chat_orchestrator_unit/test_coordinator.py
git commit -m "Tighten YouTube live prompt contracts"
```

Expected: commit succeeds with only the seven listed files.
