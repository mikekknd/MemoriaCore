# YouTubeBridgeV2 Project Index

YouTubeBridgeV2 是新版 YouTube live runtime 的子專案入口。它會承接新的直播架構、phase controller、LiveEpisodePlan runner、Aftertalk、MemoriaCore group chat 整合、控制台與觀測設計。

本目錄已開始包含 V2 runtime code；後續每個模組仍需先補 module design，再各自產生 implementation plan，並以 Red-Green-Refactor 作為實作 gate。

## 先讀順序

Agent session 接手時先讀 `CLAUDE.md`。一般文件讀取順序如下：

1. `README.md`：確認 V2 目標、邊界與文件入口。
2. `docs/architecture-index.md`：理解高層級架構、phase lifecycle、模組責任、module design 流程與設計清單。
3. `docs/roadmap.md`：確認 `/goal` 長期進度、下一個未完成 wave、required skills 與驗證命令。
4. `docs/api-reference-index.md`：查 public entrypoint 文件格式與索引規則。
5. `docs/documentation-guidelines.md`：確認後續文件分層、命名、紅綠測試與更新規範。

## 文件位置

子專案根目錄只保留入口文件：

- `README.md`：人類與 agent 的第一入口。
- `CLAUDE.md`：本子專案的 agent 工作規則。

長期設計文件集中在 `docs/`：

- `docs/architecture-index.md`
- `docs/roadmap.md`
- `docs/api-reference-index.md`
- `docs/documentation-guidelines.md`
- `docs/modules/<module-name>.md`
- `docs/implementation-plans/<module-name>.md`

## 專案定位

YouTubeBridgeV2 是全新子專案，不是在舊 `YouTubeBridge/` 上持續補丁。舊 `YouTubeBridge/` 保留為現行相容系統與參考來源；V2 的文件、設計與後續程式碼應放在 `YouTubeBridgeV2/` 底下。

V2 的第一階段目標是建立清楚的架構骨架：

- 用明確 phase lifecycle 取代舊導播流程補丁。
- 以 LiveEpisodePlan 驅動正式節目段。
- 以 Aftertalk phase 支援節目後雜談，底層復用 MemoriaCore group chat 能力。
- 將 YouTube、MemoriaCore、server/API surface、access control、storage、後台控制 UI、直播 Chat 顯示介面、presentation/TTS 都視為 adapter 或邊界模組。
- 用 public entrypoint API 文件降低後續 agent 查找成本。

## Legacy 邊界

V2 不承接下列舊行為作為正式架構：

- Legacy no-plan director。
- 舊 `program_segment_plan` 導播路徑。
- 舊 Topic Pack / FactCard raw prompt injection。
- 舊 `bridge_engine.py` facade / mixin 相容包袱。
- 為了相容舊 root-level import 而保留的大型 facade 結構。

若後續需要從舊系統搬遷能力，必須先寫入對應 module design，明確說明要搬的是業務能力、資料格式、測試 fixture，還是 adapter 行為；不得直接複製舊 runtime 流程。

## V2 模組索引

第一層模組如下，細節文件會逐一建立在 `docs/modules/`：

- Runtime Phase：管理 `planned_show -> aftertalk -> closing -> ended`。
- Runtime Application Service：協調 session command、phase decision、storage write、adapter dispatch、event publish 與 recovery。
- LiveEpisodePlan Runner：執行正式節目段的計畫 turn。
- Aftertalk：節目結束後、直播時間尚未用完時的雜談 phase。
- MemoriaCore Adapter：呼叫 MemoriaCore chat/group chat 能力。
- Closing：處理 `closing` phase 的 final message、Super Chat 收尾與 finalization。
- Storage：透過主專案 `StorageManager` 邊界保存 V2 session、phase、events、interaction metadata。
- Server/API Surface：提供後台 UI、chat display、observer 與外部工具使用的 HTTP/SSE 入口。
- Access Control / Security：定義 V2 API 存取控制、loopback/API key、MemoriaCore auth delegation 與不可信輸入邊界。
- Observability：提供 trace、event log、phase transition record。
- Operator Console UI：提供後台操作者控制、phase 狀態、Aftertalk 開關。
- Chat Display UI：提供直播畫面使用的 chat 顯示、角色發言呈現與狀態提示。
- YouTube Adapter：讀取 live chat、Super Chat 與直播狀態。
- Presentation/TTS：承接可選的展示與語音輸出。

## 概念接口

初始文件階段先建立概念接口；目前 runtime core 模組已逐步落地，實際 public source 以 `docs/api-reference-index.md` 的 Source 欄位為準：

- `LiveSessionPhase`: `planned_show | aftertalk | closing | ended`
- `AftertalkPolicy`: `disabled | auto`
- `DurationPolicy`: planned duration、auto finalize flag、remaining time 判斷規則
- `PhaseTransition`: current phase、next phase、reason、metadata、next action
- `PhaseTransitionReason`: plan completed、aftertalk enabled、duration reached、manual close、invalid state recovery
- `RuntimeCommand`: session command、command id、permission context、payload
- `ClosingCompletionStatus`: complete、incomplete、failed retryable、failed terminal
- `API Reference Entry`: `purpose / params / returns / raises / side effects / since / stability / source`
- `Module Design Skeleton`: `purpose / ownership / inputs / outputs / dependencies / out of scope / open questions`

## 後續工作規則

- 先補 module design，再寫 implementation plan，再以 Red-Green-Refactor 實作。
- implementation plan 必須從 module design 的 `Test Strategy` 推出 red test cases，先建立失敗測試，再寫最小實作讓測試通過。
- 模組設計順序以 runtime core 與正式節目/雜談行為優先，UI 和真實外部 adapter 後接。
- 每個 module design 只處理一個模組或一條可驗證的縱切。
- API 文件只收 public/stable entrypoint，不記錄 private helper。
- 實作時 docstring 是 API 文件的真相來源，Markdown index 是快速查找入口。
- 修改 V2 文件時，優先保持入口文件短而清楚；詳細決策下放到模組文件。
