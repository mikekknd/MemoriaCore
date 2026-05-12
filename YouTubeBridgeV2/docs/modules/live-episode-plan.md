# LiveEpisodePlan Runner Module Design

## Purpose

LiveEpisodePlan Runner 負責將已匯入的節目企劃轉成可執行的 planned show intent。它只處理正式節目段的計畫推進、目前 turn、speaker policy、audience event handling policy 與 completion signal，並把完成狀態交回 Runtime Phase。

## Ownership

- 擁有 LiveEpisodePlan contract 的讀取、驗證與 cursor 推進。
- 擁有 planned turn 的執行意圖描述，但不產生 LLM prompt。
- 擁有 plan completion signal，供 Runtime Phase 判斷是否離開 `planned_show`。
- 擁有觀眾插入事件是否可進入目前 planned turn 的政策輸出。
- 不承接 Legacy no-plan director、舊 `program_segment_plan` 或 raw Topic Pack prompt injection。

## Inputs

- V2 session id 與目前 `planned_show` phase snapshot。
- 已匯入或綁定的 LiveEpisodePlan document。
- plan cursor：目前 segment、turn index、已完成 turn id。
- audience event summary：可選的觀眾留言或 Super Chat 摘要。
- runtime policy：speaker policy、audience insertion policy、plan completion criteria。

## Outputs

- `PlannedTurnIntent`：目前 turn 的目的、speaker ids、topic cue、audience handling hint。
- `PlanExecutionStatus`：`not_started | running | completed | invalid`。
- `PlannedTurnResult`：turn 是否完成、下一個 cursor、可寫入 storage 的摘要。
- `PlanCompletionSignal`：交給 Runtime Phase 的完成狀態。

## Dependencies

- Runtime Phase 消費 completion signal。
- Storage 保存 plan cursor、turn result 與 validation result。
- MemoriaCore Adapter 消費 planned turn intent 並轉成 chat payload。
- Observability 消費 plan validation 與 turn progression summary。
- Server/API Surface 提供 plan import/bind 入口。

## Out Of Scope

- MemoriaCore HTTP 呼叫與 response normalization。
- YouTube polling 與 live chat normalization。
- Aftertalk cue generation。
- Closing finalization。
- UI rendering。
- raw Topic Pack / FactCard 全文注入 prompt。

## Public Entrypoints

本階段只描述 planned public contracts，不宣稱 source symbol 已存在。

- `LiveEpisodePlanContract`：匯入計畫的穩定欄位集合。
- `PlannedTurnIntent`：交給 MemoriaCore Adapter 的 planned show 執行意圖。
- `PlanExecutionStatus`：plan runner 對目前企劃狀態的判斷。
- `PlannedTurnResult`：單一 planned turn 的執行後摘要。
- `PlanCompletionSignal`：Runtime Phase 使用的 completion input。

## Execution Rules

| Situation | Required Behavior |
| --- | --- |
| plan 尚未開始 | cursor 指向第一個 planned turn，status 為 `running`。 |
| current turn speaker policy 為 fixed | `PlannedTurnIntent` 必須只包含該 turn 允許的 speaker ids。 |
| current turn 允許觀眾插入 | 只輸出 audience summary，不輸出 raw YouTube event。 |
| current turn 禁止觀眾插入 | audience summary 必須被忽略並記錄為 skipped reason。 |
| Super Chat 在 planned turn 期間進入 | 只輸出是否允許處理與 display-safe summary；具體 acknowledgement 屬於 Closing 或 Chat Display。 |
| turn 完成 | `PlannedTurnResult` 推進 cursor 並保留 redacted turn summary。 |
| 最後一個 turn 完成 | 輸出 `PlanCompletionSignal.completed = true`。 |
| plan invalid | 不產生 `PlannedTurnIntent`，回傳 validation summary。 |
| plan completed | 不進入 Legacy no-plan director；Runtime Phase 決定 aftertalk 或 closing。 |

## Failure Modes

- plan 缺少必要欄位，回傳 `invalid` 並附 validation summary。
- cursor 指向不存在的 turn，回傳 recoverable plan contract error。
- speaker ids 不存在或無法對應角色，回傳 invalid turn intent。
- audience insertion policy 未定義時，預設不插入觀眾事件。
- plan completed 後不得回到舊 director fallback。
- raw Topic Pack / FactCard 不得出現在 output payload。

## Test Strategy

- contract validation tests：有效與無效 LiveEpisodePlan document。
- cursor tests：從第一個 turn 推進到完成。
- speaker policy tests：fixed speaker 與多 speaker turn 的輸出。
- audience insertion tests：允許、禁止與 Super Chat 優先順序。
- completion tests：最後一個 turn 完成後輸出 completion signal。
- prompt boundary tests：output 不包含 raw Topic Pack / FactCard 全文。
- side effect tests：runner 不呼叫 MemoriaCore、YouTube、storage write 或 UI。

## Open Questions

- LiveEpisodePlan schema 的最終欄位名稱由後續 runtime implementation plan 鎖定。
- audience event summary 的精簡格式需與 YouTube Adapter 和 Storage 設計對齊。
- planned turn result 是否需要保留 display metadata，需與 Chat Display UI 設計對齊。
