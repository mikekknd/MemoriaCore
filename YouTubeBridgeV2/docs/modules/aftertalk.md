# Aftertalk Module Design

## Purpose

Aftertalk 負責節目完成後的雜談 phase。它由 Runtime Phase 觸發，根據 `AftertalkPolicy`、剩餘時間與 session context 產生精簡 cue，交給 MemoriaCore group chat 讓角色自由延伸討論。

## Ownership

- 擁有 aftertalk entry condition 的模組內補充檢查。
- 擁有 aftertalk cue、speaker rotation hint 與 stop reason。
- 擁有 aftertalk turn request 的語意，不擁有 LLM output。
- 擁有避免使用 Legacy no-plan director 的邊界。
- 擁有雜談內容的 public/private metadata 最小化規則。

## Inputs

- Runtime Phase 的 `start_aftertalk` 或 `continue_aftertalk` next action。
- LiveEpisodePlan completion summary。
- session topic summary 與公開可見的 show context。
- duration summary 與 manual close state。
- operator 的 `AftertalkPolicy`。

## Outputs

- `AftertalkCue`：交給 MemoriaCore Adapter 的精簡雜談 cue。
- `AftertalkTurnRequest`：角色接力、group chat mode 與可見 metadata。
- `AftertalkStopReason`：`duration_reached | manual_close | disabled | invalid_policy | adapter_error | completed_by_policy`。
- `AftertalkSessionSummary`：供 storage、UI 與 observability 使用的摘要。

## Dependencies

- Runtime Phase 決定是否進入或持續 aftertalk。
- MemoriaCore Adapter 執行 group chat request。
- Storage 保存 aftertalk turn request、response summary 與 stop reason。
- Operator Console UI 提供 aftertalk 開關與狀態顯示。
- Observability 記錄 cue summary 與 adapter correlation id。

## Out Of Scope

- 角色人格與 LLM 內容生成。
- MemoriaCore HTTP transport。
- YouTube polling。
- LiveEpisodePlan turn 推進。
- UI rendering。
- TTS 或 presentation queue。
- Legacy no-plan director。

## Public Entrypoints

本節描述已存在的 Aftertalk public contracts。實作位於 `YouTubeBridgeV2/runtime/aftertalk.py`。

- `AftertalkCue`：aftertalk group chat 的精簡提示資料。
- `AftertalkTurnRequest`：交給 MemoriaCore Adapter 的 group chat request intent。
- `AftertalkStopReason`：aftertalk 停止原因。
- `AftertalkSessionSummary`：aftertalk 對 storage/UI/observability 的摘要。
- `build_aftertalk_turn_request(aftertalk_context)`：依 policy / duration / manual close 產生 group chat intent 或 stop decision。
- `summarize_aftertalk_result(aftertalk_result)`：將 adapter/module result 摘要成 public-safe aftertalk summary。

## Aftertalk Flow Rules

| Situation | Required Behavior |
| --- | --- |
| Runtime Phase 未輸出 `start_aftertalk` 或 `continue_aftertalk` | Aftertalk 不得自行啟動。 |
| `AftertalkPolicy.disabled` | 回傳 `disabled` stop reason，不產生 group chat request。 |
| 剩餘時間不足 | 回傳 `duration_reached` stop reason。 |
| manual close 已要求 | manual close 優先，回傳 `manual_close` stop reason。 |
| cue 建立成功 | cue 只包含 public show summary、角色接力 hint、session metadata。 |
| MemoriaCore Adapter 失敗 | Aftertalk 保存 stop/retry summary，不直接處理 transport retry。 |
| aftertalk 持續中 | 每輪 request 必須能被 storage/observability 以 correlation id 追蹤。 |

## Failure Modes

- Runtime Phase 未給 aftertalk action 時不得自行啟動。
- duration 已到達時不得產生新的 aftertalk cue。
- aftertalk disabled 時不得呼叫 MemoriaCore Adapter。
- cue 不得包含 hidden prompt、raw Topic Pack 或 raw MemoriaCore payload。
- adapter failure 應輸出 stop 或 retry summary，不在本模組重試 transport。
- manual close 優先於 aftertalk continuation。

## Test Strategy

- entry tests：符合 policy 與 duration 時產生 cue。
- disabled tests：policy disabled 時不產生 request。
- duration tests：剩餘時間不足時輸出 stop reason。
- manual close tests：manual close 立即停止。
- cue minimization tests：不含 raw prompt 或 raw cards。
- legacy boundary tests：不使用 Legacy director。
- side effect tests：不直接呼叫 MemoriaCore、YouTube、storage 或 UI。

## Open Questions

- speaker rotation hint 的預設規則需與 MemoriaCore group chat 能力對齊。
- aftertalk cue 是否包含上一輪角色回覆摘要，需與 Storage 和 MemoriaCore Adapter 設計對齊。
- operator console 是否允許 live 中改變 aftertalk policy，需由 Server/API Surface 定義。
