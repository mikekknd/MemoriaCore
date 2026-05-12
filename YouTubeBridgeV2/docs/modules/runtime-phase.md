# Runtime Phase Module Design

本文件定義 YouTubeBridgeV2 的 phase lifecycle 核心狀態機。它是 module design，不是 implementation plan；本階段不宣告任何已存在的 Python source symbol。

## Purpose

Runtime Phase 負責用一致、可測試的規則判斷 live session 下一步 phase。它提供 V2 其他模組共用的 phase contract，讓 LiveEpisodePlan、Aftertalk、Storage、API、後台 UI、直播 Chat 顯示與 Observability 都能使用同一套 lifecycle 語意。

核心 lifecycle 固定為：

```text
planned_show -> aftertalk -> closing -> ended
```

Runtime Phase 的主要價值是把「何時轉 phase」集中在一個純決策層，而不是分散在 YouTube polling、MemoriaCore adapter、UI handler 或 closing 流程裡。

## Ownership

Runtime Phase 擁有：

- `LiveSessionPhase` 的合法值與 transition 規則。
- `AftertalkPolicy` 與 `DurationPolicy` 對 phase transition 的影響。
- `PhaseTransition` decision 的結構、reason 與 metadata summary。
- invalid state 的保守恢復方向。
- 供測試使用的狀態表與決策準則。

Runtime Phase 不擁有：

- Storage write 或 transaction。
- MemoriaCore HTTP call 或 group chat payload。
- YouTube live chat polling、Super Chat normalization 或直播狀態查詢。
- 後台 UI / 直播 Chat UI rendering。
- TTS、presentation event delivery 或語音播放。
- LiveEpisodePlan turn 內容生成、Aftertalk cue 內容生成、closing 台詞生成。

呼叫端可以根據 `PhaseTransition` 寫入 storage、發送事件或呼叫 adapter，但這些 side effects 必須留在 application service 或對應 module 內。

## Inputs

Runtime Phase 的決策輸入應是已整理好的 session snapshot，而不是直接讀取資料庫或外部 API。

必要輸入：

- `current_phase`：目前 `LiveSessionPhase`。
- `now`：判斷 duration 與 transition 時使用的目前時間。
- `session_started_at`：直播 session 起始時間。
- `plan_completion_status`：LiveEpisodePlan runner 提供的完成狀態。
- `aftertalk_policy`：`disabled` 或 `auto`。
- `duration_policy`：直播時間上限、是否自動收尾、剩餘時間判斷規則。
- `manual_close_requested`：操作者或外部控制 API 是否要求進入 closing。
- `closing_completion_status`：closing module 是否已完成 finalization。

可選輸入：

- `phase_entered_at`：目前 phase 的進入時間，用於 metadata 與觀測，不直接取代 duration policy。
- `last_transition_id`：用於 idempotency 或 observability 的參考 id。
- `runtime_blockers`：呼叫端整理出的暫時不可推進原因，例如 adapter 暫時不可用。Runtime Phase 只回報 wait 類 next action，不處理 blocker 本身。

## Outputs

Runtime Phase 回傳一個 `PhaseTransition` decision。它描述決策結果，不直接執行結果。

`PhaseTransition` 應包含：

- `current_phase`：輸入時的 phase。
- `next_phase`：決策後的 phase。
- `changed`：phase 是否改變。
- `reason`：`PhaseTransitionReason`。
- `metadata`：可寫入 transition log 的摘要，不包含 raw prompt、raw Topic Pack、hidden context 或大型 payload。
- `next_action`：呼叫端下一步應執行的高層動作。

建議的 `next_action` 值：

- `run_planned_show`：維持或開始執行 LiveEpisodePlan runner。
- `start_aftertalk`：進入 Aftertalk module，由它準備 group chat cue。
- `continue_aftertalk`：維持 aftertalk，由 Aftertalk module 決定下一輪互動。
- `start_closing`：進入 closing 流程。
- `mark_ended`：closing 完成後標記 session ended。
- `wait`：目前資訊不足或被 runtime blocker 暫停，呼叫端應等待下一個事件或輪詢。

## Dependencies

Runtime Phase 依賴其他模組提供抽象訊號，但不依賴它們的實作細節。

- LiveEpisodePlan runner 提供 `plan_completion_status`，至少要能區分未完成、已完成、不可用。
- Aftertalk module 提供 `AftertalkPolicy` 的可用值與 operator-facing 設定語意。
- Duration policy 提供直播時間上限、是否自動收尾、剩餘時間計算。
- Closing module 提供 `closing_completion_status`，表示 finalization 是否已完成。
- Storage module 提供 session snapshot 與 transition write 介面，但 Runtime Phase 不直接呼叫 storage。
- Observability module 消費 `PhaseTransition` metadata，但 Runtime Phase 不直接寫 log。

## Out Of Scope

以下設計不屬於 Runtime Phase：

- 如何匯入、驗證或執行 LiveEpisodePlan turn。
- Aftertalk 如何選角色、建立 cue、呼叫 MemoriaCore group chat 或處理角色自由發揮內容。
- Closing 如何處理 final closing、Super Chat 收尾、摘要、直播停止或 finalization 寫入。
- YouTube polling 的頻率、重試、event ordering 或 Super Chat 查詢。
- 後台 UI 控制項、直播 Chat 顯示布局、SSE event 具體欄位。
- TTS queue、語音合成 timeout、presentation ack。
- Legacy no-plan director 的任何相容層。

## Public Entrypoints

本階段只描述預期 entrypoint，不宣稱 source symbol 已存在。

### Phase Advance Decision

Purpose:
根據 session snapshot 與目前時間，回傳一個 `PhaseTransition` decision。

Expected Params:

- `session_snapshot`：已整理好的 V2 session phase/policy/completion state。
- `now`：目前時間。

Expected Returns:

- `PhaseTransition`：下一個 phase、reason、metadata summary 與 next action。

Expected Side Effects:

- 無。此 entrypoint 應維持 pure decision；storage write、SSE publish、adapter call 由呼叫端執行。

### Duration Evaluation

Purpose:
用 `DurationPolicy` 判斷 session 是否已到達時間上限，以及 Aftertalk 是否仍可開始或繼續。

Expected Params:

- `session_started_at`：session 起始時間。
- `now`：目前時間。
- `duration_policy`：時間上限與自動收尾規則。

Expected Returns:

- duration summary：是否到達時間上限、剩餘秒數、是否允許自動進入 aftertalk。

Expected Side Effects:

- 無。

## Transition Rules

Runtime Phase 應採用保守且可預測的轉換規則。

| Current Phase | Condition | Next Phase | Reason | Next Action |
| --- | --- | --- | --- | --- |
| `planned_show` | `manual_close_requested` | `closing` | `manual_close` | `start_closing` |
| `planned_show` | duration reached and auto finalize enabled | `closing` | `duration_reached` | `start_closing` |
| `planned_show` | plan completed, aftertalk disabled | `closing` | `plan_completed` | `start_closing` |
| `planned_show` | plan completed, aftertalk auto, aftertalk allowed by duration policy | `aftertalk` | `aftertalk_enabled` | `start_aftertalk` |
| `planned_show` | plan completed, aftertalk auto, no remaining time | `closing` | `duration_reached` | `start_closing` |
| `planned_show` | plan not completed | `planned_show` | `no_change` | `run_planned_show` |
| `aftertalk` | `manual_close_requested` | `closing` | `manual_close` | `start_closing` |
| `aftertalk` | duration reached and auto finalize enabled | `closing` | `duration_reached` | `start_closing` |
| `aftertalk` | duration not reached or duration is manual/unbounded | `aftertalk` | `no_change` | `continue_aftertalk` |
| `closing` | closing completed | `ended` | `closing_completed` | `mark_ended` |
| `closing` | closing not completed | `closing` | `no_change` | `start_closing` |
| `ended` | any normal input | `ended` | `no_change` | `wait` |
| invalid phase | any input | `closing` | `invalid_state_recovery` | `start_closing` |

## DurationPolicy

`DurationPolicy` 是 Runtime Phase 使用的概念 contract，實作時可轉成 Python type。

必要欄位：

- `planned_duration_seconds`：直播計畫時間上限。有限正數代表可計算剩餘時間；未設定或非正值代表不以時間自動結束。
- `auto_finalize_on_duration`：到達時間上限時是否自動進入 closing。
- `remaining_time_seconds`：由 duration evaluation 計算出的剩餘時間摘要。
- `aftertalk_requires_remaining_time`：`AftertalkPolicy.auto` 是否需要有限且大於零的剩餘時間才可進入 aftertalk。

建議規則：

- `auto_finalize_on_duration = true` 且剩餘時間小於等於零時，Runtime Phase 應進入 `closing`。
- `auto_finalize_on_duration = false` 時，duration 不會自行觸發 closing；aftertalk 可持續到手動 closing 或後續政策觸發。
- 若 `aftertalk_requires_remaining_time = true`，plan completed 時只有剩餘時間大於零才可進入 `aftertalk`。
- 若 duration 無法計算，metadata 應標記 duration summary 不完整，並使用保守轉換：不因缺少 duration 而自動延長已要求 closing 的流程。

## Failure Modes

Runtime Phase 應明確處理以下失敗模式：

- invalid phase：回傳 `invalid_state_recovery` 並導向 `closing`，讓系統可以保守收尾。
- missing required session fields：回傳 contract error，由 application service 決定是否阻擋寫入或回報 API 錯誤。
- completed plan without policy：若 LiveEpisodePlan 已完成但缺少 `aftertalk_policy`，視為 contract error，不默默套用 Legacy director fallback。
- duration edge cases：負數 duration、起始時間晚於目前時間、時區資訊不一致時，回傳可診斷的 duration metadata 或 contract error。
- repeated transition request：相同 snapshot 應回傳相同 decision，避免重複呼叫導致不同 phase。
- ended phase mutation：`ended` 不應因一般輸入被重新打開；若需要重開 session，應由獨立 session/recovery 流程設計。

## Test Strategy

Runtime Phase 應以 pure function / state table 測試為主，不依賴 HTTP server、YouTube、MemoriaCore 或真實 storage。

測試類型：

- State table tests：覆蓋每個 phase 的主要 transition。
- Duration boundary tests：剩餘時間大於零、等於零、小於零、無上限、manual duration。
- Policy tests：`AftertalkPolicy.disabled` 與 `AftertalkPolicy.auto` 在 plan completed 後的差異。
- Manual close tests：`planned_show` 與 `aftertalk` 都能立即導向 `closing`。
- Closing finalization tests：`closing` only moves to `ended` when closing completion status is complete。
- Invalid state tests：未知 phase 導向 `closing` 並帶 `invalid_state_recovery`。
- Idempotency tests：相同 snapshot 與 `now` 產生相同 `PhaseTransition`。
- Side effect tests：phase decision 不寫 storage、不呼叫 adapter、不產生 UI rendering。

## Observability Metadata

`PhaseTransition.metadata` 應支援後續診斷，但保持精簡。

建議包含：

- `previous_phase`
- `next_phase`
- `reason`
- `plan_completion_status`
- `aftertalk_policy`
- `duration_summary`
- `manual_close_requested`
- `closing_completion_status`
- `correlation_id` 或 `transition_id`

不應包含：

- raw hidden prompt
- raw Topic Pack / FactCard 全文
- MemoriaCore raw request payload
- YouTube raw event 全量內容
- 使用者或角色的非必要敏感內容

## Open Questions

以下問題由後續模組設計承接，不在 Runtime Phase 內展開：

- LiveEpisodePlan runner 需要定義 `plan_completion_status` 的正式欄位與完成條件。
- Aftertalk module 需要定義 `aftertalk_requires_remaining_time` 的 operator-facing 預設值與控制台呈現。
- Closing module 需要定義 `closing_completion_status` 如何由 finalization result 映射而來。
- Storage module 需要定義 session snapshot 與 transition write 的 repository contract。
- Server/API Surface 需要定義手動 closing request 的權限、payload 與錯誤回應。
