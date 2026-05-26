# Group Router Action Policy

本文件記錄 MemoriaCore 群組對話中 `group_router` 的 action 判定、後處理與 fallback 規則。目標是避免只看 `runtime/llm_trace.jsonl` 的 router raw response 時，誤以為該 JSON 就是最終採用的發話決策。

## 核心責任

`group_router` 只負責決定本輪群組對話是否繼續、使用哪個 group turn action、以及由哪位角色發話。它不產生角色台詞。

實際發話流程分成兩層：

1. `core/chat_orchestrator/group_router.py::run_group_router()` 產生並驗證 route。
2. `api/routers/chat/group_loop.py::run_group_chat_loop()` 依最終 route 決定是否呼叫角色生成。

`llm_trace.jsonl` 中 `category=group_router`、`direction=response` 的內容是 router LLM 的原始 JSON 回覆。MemoriaCore 仍會在回傳給 group loop 前做 validation、speaker guard 與 fallback，因此 raw response 不一定等於最終 route。

## 發話狀態的權威來源

router prompt 中的 `turn_state_json` 是本輪發話狀態的權威來源，尤其是：

- `already_spoken_this_turn`
- `not_yet_spoken_this_turn`
- `all_participants_already_spoke_this_turn`
- `bot_turn_index`
- `max_bot_turns`
- `remaining_bot_turns_including_next`

`previous_context` 與 `external_turn_context_json` 只能提供語義背景或本輪外部事件脈絡；它們不能覆寫本輪誰已發話、誰尚未發話。

## Stop Action 差異

`stop_no_new_value` 和 `stop_all_spoken` 都會讓 route 表達「不要再產生下一句」，但語義不同，validation 也不同。

### `stop_no_new_value`

語義：下一句只會重述、附和、道別或拉長收尾，沒有新增互動價值。

合法條件：

- `target_character_id` 必須是 `null`。
- 即使 `not_yet_spoken_this_turn` 仍有人，也可以合法停止。

這代表「本輪不需要補滿所有角色」。

### `stop_all_spoken`

語義：所有可用角色都已完成本輪任務，且沒有新增互動價值。

合法條件：

- `target_character_id` 必須是 `null`。
- `not_yet_spoken_this_turn` 必須是空的。

若仍有未發話角色，`stop_all_spoken` 會被視為和 turn state 矛盾，因為它聲稱所有角色已完成，但狀態仍顯示有人未發話。

## Validation Fallback

`run_group_router()` 會先解析 router LLM 的 JSON，再由 `_validate_action_result()` 檢查 action / target 是否和 turn state 相容。

以下情況會 fallback 到未發話角色：

- action 不在合法 action enum。
- `stop_all_spoken` 但 `not_yet_spoken_this_turn` 非空。
- `stop_all_spoken` 或 `stop_no_new_value` 卻帶了 `target_character_id`。
- `new_speaker_*` action 指向已發話或不存在的角色。
- `repeat_speaker_*` action 指向未發話或不存在的角色。
- `explicit_user_request` 指向不存在的角色。

fallback 會優先選擇 `not_yet_spoken_this_turn` 中不是 `last_speaker` 的角色；若沒有，再退回其他可用角色。

因此，若第二輪 router raw response 是：

```json
{
  "conversation_intent": "single_response",
  "action": "stop_all_spoken",
  "target_character_id": null
}
```

但同一個 prompt 的 `turn_state_json.not_yet_spoken_this_turn` 仍包含 `default`，最終 route 可能會被改成讓 `default` 發話。這不是另一個 router 決策，而是 validation fallback。

## Group Loop First-Turn Fallback

`run_group_chat_loop()` 還有一層 first-turn fallback：如果第 0 輪 route 表示不回應或沒有 target，預設群組流程仍會嘗試選一位角色發話，避免一次群組 request 完全沒有 assistant output。

第 1 輪之後，如果 route 是合法 stop，group loop 會停止，不會再強制補角色。

例外：YouTube Live director / planned turn 會套用額外的 director intent 與 speaker guard；這些規則不代表一般群組流程。

## 為什麼不是每次都補滿所有角色

群組流程不保證每次 request 都讓所有角色發話。是否補下一位角色取決於：

- `max_bot_turns` 是否還有剩餘額度。
- router raw action 是 `stop_no_new_value` 還是 `stop_all_spoken`。
- stop action 是否和 `not_yet_spoken_this_turn` 相容。
- 是否是第 0 輪 first-turn fallback。
- 是否套用 YouTube Live director / planned turn 特例。

常見結果：

- `max_bot_turns = 1`：最多只會有一位角色。
- 第 1 輪後 `stop_no_new_value`：合法停止，即使仍有未發話角色。
- 第 1 輪後 `stop_all_spoken` 且仍有未發話角色：非法 stop，fallback 到未發話角色。
- 第 1 輪後所有角色都已發話，`stop_all_spoken`：合法停止。

## Debug Checklist

排查群組路由時，不要只看 router response。請同時確認：

1. 同一個 `llm_call_id` 的 router prompt 內 `turn_state_json`。
2. router raw response 的 `action`、`target_character_id`。
3. `not_yet_spoken_this_turn` 是否為空。
4. `bot_turn_index` 與 `max_bot_turns`。
5. 是否存在 YouTube Live director / planned turn context。
6. 後續是否出現 chat prompt，且 `current_character_id` 是哪位角色。

若 raw response 和後續 chat prompt 不一致，優先檢查 validation fallback，而不是假設有另一個 router 決策。

