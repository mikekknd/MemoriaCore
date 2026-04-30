# LLM 無關文件長文輸出除錯紀錄

日期：2026-04-30

## 症狀

群組聊天接力時，最終對話 LLM 偶發輸出與目前對話完全無關的教材型 Markdown 長文，例如：

- `# Object Oriented Programming in Java`
- `# 浏览器事件模型`

前端/後端雖然能透過非 JSON 攔截與重試拿到可用回覆，但模型已先生成大量無意義文字，會增加延遲與 token 成本。

## 本次判斷

這次事件不是記憶、工具結果或 prompt template 把教材內容帶進上下文。

檢查 `llm_trace.jsonl` 後可確認：

- 異常回覆發生在 `category=chat` 的最終角色生成。
- 對應 prompt 中沒有 `Java`、`OOP`、`Object Oriented`、`浏览器事件模型` 等關鍵字。
- 同一類異常在不同角色、不同無關主題中出現，內容像模型訓練資料殘片。
- 異常發生於群組接力 turn 1+，當時 final messages 的最後一則 role 是 `assistant`，而不是 `user`。
- 當時 `<group_followup_instruction>` 只放在 system prompt，沒有作為最後一則 control user message，因此模型缺少「現在輪到你接話」的最後使用者錨點。

推論：部分雲端代理模型在 `response_format` 約束不穩時，遇到 assistant-ended prompt 容易發散成無關文件續寫。系統原本的 `document_dump` 重試只能在完整回覆收完後攔截，無法節省第一次失控生成的成本。

## 已套用修正

1. 群組接力指令不再附加到 system prompt，避免接力 turn 破壞 system prefix cache。
2. 接力指令追加為最後一則單層 XML-like user control message：

```xml
<group_followup_instruction source="system_control">
請以你自己的角色身份接話。只有在你能提供新的觀點、補充、反駁或自然追問時才回應；避免重複上一位的內容。
</group_followup_instruction>
```

3. `task_key="chat"` 且帶 `response_format`、`log_context` 的最終角色 JSON 生成加上輸出上限：
   - Ollama：`options.num_predict = 768`
   - OpenAI-compatible / llama.cpp：`max_tokens = 768`
4. 保留既有 `document_dump` / speaker leak 非 JSON 重試，作為第二層防線。

## 未來重現時的檢查路線

1. 先查 `llm_trace.jsonl` 中同時間的 `type=error`、`category=LLMRouter/chat`。
2. 看 `details.retry_reason`：
   - `document_dump`：模型輸出無關長文件。
   - `group_speaker_leak`：模型複製或代替其他 AI 發言。
   - `format_only`：內容可用但沒有包成 JSON。
3. 檢查 `details.original_messages`：
   - `original_messages[-1].role` 是否為 `assistant`。
   - prompt 是否真的包含異常文件關鍵字；若沒有，優先判定為模型發散。
   - system prompt 不應包含 `<group_followup_instruction>`，避免每個接力 turn 改變 system prefix。
   - 最後一則 user message 是否有 `<group_followup_instruction source="system_control">`。
4. 檢查 provider 是否有收到輸出上限：
   - Ollama 應有 `num_predict`。
   - OpenAI-compatible 應有 `max_tokens`。
5. 若仍有長文輸出，下一步優先考慮：
   - 降低最終 chat JSON 生成溫度。
   - 對串流輸出做早停偵測。
   - 將高風險模型從 chat 路由移出，改用 schema compliance 較穩的模型。
