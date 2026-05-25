# YouTubeBridgeV2 Documentation Guidelines

本文件定義 V2 文件分層與撰寫規範。目標是讓後續 agent 不必一次讀完整專案，也能從穩定入口找到正確模組。

## 文件分層

### Project Index

位置：`YouTubeBridgeV2/README.md`

用途：提供 V2 專案目標、讀取順序、Legacy 邊界與模組索引。README 應保持短而穩定，不放低階設計與施工步驟。

### Agent Rules

位置：`YouTubeBridgeV2/CLAUDE.md`

用途：提供本子專案的 agent 工作規則。它只保留接手順序、禁止事項與必要 gate，不重複 module design 細節。

### Architecture Index

位置：`YouTubeBridgeV2/docs/architecture-index.md`

用途：描述高層級架構、phase lifecycle、模組責任與模組間依賴。此文件可以指出後續需要補的 module design，但不展開細部 class/function 設計。

### API Reference Index

位置：`YouTubeBridgeV2/docs/api-reference-index.md`

用途：記錄 public/stable entrypoint 的使用方式。它是查找入口，不是完整實作說明。

### Documentation Guidelines

位置：`YouTubeBridgeV2/docs/documentation-guidelines.md`

用途：定義文件分層、更新規則與 Red-Green-Refactor 施工 gate。

### Module Design

位置：`YouTubeBridgeV2/docs/modules/<module-name>.md`

用途：逐一設計模組。每份 module design 應只處理一個模組或一條可驗證的縱切。

建議章節：

```markdown
# <Module Name>

## Purpose

## Ownership

## Inputs

## Outputs

## Dependencies

## Out Of Scope

## Public Entrypoints

## Failure Modes

## Test Strategy

## Open Questions
```

### Implementation Plan

位置：`YouTubeBridgeV2/docs/implementation-plans/<module-name>.md`

用途：在 module design 定稿後，拆成可執行的測試與實作步驟。Implementation plan 可以包含檔案、函式、測試與 commit 建議；Project Index 不應包含這些內容。

### 文件位置規則

- 子專案根目錄只保留 `README.md` 與 `CLAUDE.md` 這類入口文件。
- 長期設計文件集中在 `YouTubeBridgeV2/docs/`。
- 模組設計集中在 `YouTubeBridgeV2/docs/modules/`。
- 模組實作計畫集中在 `YouTubeBridgeV2/docs/implementation-plans/`。
- 後續若新增 runtime code，應使用獨立程式碼目錄，不把 code 與 design docs 混在同一層。

## Red-Green-Refactor 施工門檻

Red-Green-Refactor 是 V2 的實作流程 gate，不是 runtime 功能模組，也不屬於 phase lifecycle。

每個模組從 module design 進入實作前，必須完成下列流程：

1. Module Design：先定義 ownership、inputs、outputs、failure modes 與 test strategy。
2. Red Test Plan：從 module design 推出至少一組會先失敗的 contract test 或 regression test。
3. Red：新增測試並確認它因預期原因失敗。
4. Green：寫最小實作讓 red tests 通過。
5. Refactor：整理實作，但不得改變已定義 contract。
6. Docs Sync：若 public behavior、資料形狀、side effects 或架構邊界改變，同步更新相關 V2 文件。

Implementation plan 必須明確列出：

- Red cases：要先失敗的行為、fixture、assertion 與預期失敗原因。
- Green scope：讓測試通過的最小實作範圍。
- Refactor boundary：允許整理的範圍，以及不得觸碰的模組。
- Adapter strategy：外部系統使用 fake/mock、contract fixture 或 integration test 的條件。
- Docs sync：實作後需要更新的 module design、API reference 或 architecture index。

測試邊界：

- Runtime core 優先使用 pure function / state table tests。
- Adapter module 的 unit test 不直接呼叫真實 YouTube 或 MemoriaCore；真實外部連線只放在明確標示的 integration test。
- UI module design 必須先定義可測的 API/event contract，再設計瀏覽器或 DOM smoke test。
- Docs-only 變更不需要 runtime red test，但必須執行文件一致性檢查。

## API 文件規範

- public function/class/endpoint 必須有用途、參數、回傳、錯誤與 side effects。
- private helper 不需要進 API reference。
- docstring 與 API index 不一致時，以 docstring 為準，並更新 API index。
- 若 entrypoint 會呼叫外部 API、寫 storage、發 SSE、觸發 TTS，必須列在 side effects。
- stability 建議使用：
  - `stable`：外部或跨模組可依賴。
  - `provisional`：設計已定，但可能在 MVP 期間調整。
  - `internal`：只供模組內使用，不應出現在 public API index，除非用來標示不應依賴。

## Docs As Contract

V2 文件是架構 contract，不是事後補充說明。任何程式或架構變更若改變 public 行為、跨模組依賴、資料形狀、phase transition、adapter side effect、UI/API 入口，必須在同一批變更中更新對應文件。

變更對應規則：

- 改變 phase lifecycle、Legacy 邊界、模組責任或模組依賴：更新 `README.md` 與 `docs/architecture-index.md`。
- 新增、移除或調整 public function/class/endpoint/event payload：更新 docstring 與 `docs/api-reference-index.md`。
- 調整單一模組內部設計、ownership、inputs、outputs、failure modes 或 test strategy：更新 `docs/modules/<module-name>.md`。
- 調整 Server/API Surface、auth/security、HTTP/SSE payload 或 API 存取控制：更新對應 module design 與 `docs/api-reference-index.md`。
- 調整後台控制 UI 或直播 Chat 顯示介面的資訊架構、事件來源、操作入口或可見狀態：更新對應 UI module design 與 API reference event entry。
- 調整 MemoriaCore、YouTube、storage、presentation/TTS 等 adapter 的外部 side effects：更新對應 module design 與 API reference entry。
- 後續 implementation plan 若和 module design 不一致，必須先修正 module design，再執行或更新 plan。

若變更只影響 private helper，且不改變 public 行為、資料形狀或 side effects，可以不更新 API reference；但如果 helper 的存在會影響 agent 查找入口，應在 module design 補充說明。

## 文件語言

- 文件使用繁體中文。
- symbol、type、endpoint、event type 保持原文。
- 程式 docstring 可以使用繁體中文；型別與參數名稱保持原文。
- 若需要引用舊 `YouTubeBridge/` 行為，應明確標示為 reference，不可寫成 V2 必須相容。

## 更新規則

- 新增模組前，先在 `docs/architecture-index.md` 補模組定位。
- 設計模組時，新增或更新 `docs/modules/<module-name>.md`。
- 實作前，先在 `docs/implementation-plans/<module-name>.md` 定義 red test cases、green scope 與 refactor boundary。
- 實作 public entrypoint 後，更新 `docs/api-reference-index.md`。
- 若 module design 改變 phase lifecycle 或 Legacy 邊界，必須同步更新 README 與 architecture index。
- 每次提交前檢查本次變更是否需要同步更新 V2 文件；若不需要，commit 或 PR 說明中應明確標示原因。
- 文件不得留下未決定的低階施工待辦標記；尚未決策的內容應放在 module design 的 Open Questions。
