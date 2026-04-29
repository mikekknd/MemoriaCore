# 多重 AI 群組對話 (Multi-Agent Group Chat) 實作計畫

這個計畫旨在實作讓多位不同人格的 AI 能夠在同一個 Session 內共同參與對話的機制。

## User Review Required

> [!IMPORTANT]
> **中控路由層（Central Orchestrator）的架構選擇**
> 1. **獨立的 Group Router**：在現有的 `Router Agent`（判斷工具）之前，增加一層輕量的 LLM 呼叫來決定「誰該說話」。
> 2. **合併在 Router Agent 中**：修改原有的 `Router Agent` 邏輯，讓它不僅回傳 `tool_calls`，也能透過另一個 dummy tool（如 `target_speaker`）決定發話角色。
> 建議採用**方案 1（獨立 Group Router）**，這樣職責分離較清楚，且單機對話時不會增加額外負擔。

> [!WARNING]
> **記憶查詢效能與成本**
> 當多位 AI 在同一個群組時，每次發話都需要針對「即將發話的 AI」提取其獨立的 Core Memory 與 Profile。若頻繁互相對話，記憶檢索次數會倍增。這部分將延續現有的 `ThreadPoolExecutor` 平行處理機制，但需要確認伺服器負載上限。

## Open Questions

1. **AI 互聊的終止條件**：
   系統應如何限制 AI 連續對話？
   - 方案 A：固定次數上限（例如最多連續 3 次 AI 發言後，強制等待 User）。
   - 方案 B：由 Group Router 動態決定，並設定硬性上限（如 5 次）以防無限迴圈。
   - **確認事項**：偏好哪種方案？

2. **前端 UI 設計**：
   - 使用者要如何「建立群組」？是在 Dashboard 左側清單新增一種「Group Session」類型，還是單純在目前的 Chat 介面中提供一個「邀請加入」的按鈕？
   - 前端發送的訊息格式是否需要更改（例如明確指定 `@某人`，或是由系統自動判斷）？

---

## Proposed Changes

### `api/models/` 與 Session 管理
擴充 Session 狀態以支援多位角色。

#### [MODIFY] API Models & Session State
- 修改 `session_ctx`，將原本的單一 `character_id` 擴充為支援 `active_characters: list[str]`。
- FastAPI 的建立 Session 端點需支援傳入多個 `character_ids`。

---

### `core/chat_orchestrator/`
實作中控分派層與 Bot-to-Bot 互動迴圈。

#### [NEW] `group_router.py`
- 實作 `run_group_router(session_messages, active_characters)`。
- **職責**：分析最新的對話上下文（包括 User 與各個 AI 的發言），判斷下一位最適合接話的 AI 角色 ID，若判斷不需接話則回傳 `None`（等待 User）。

#### [MODIFY] `coordinator.py`
- 修改 `run_dual_layer_orchestration`，使其能接收指定的 `target_character_id` 來進行該角色的 Persona 生成。
- 將原本固定讀取 `session_ctx["character_id"]` 的邏輯改為使用傳入的 `target_character_id`，確保檢索到正確的 Profile 與 Core Memory。

#### [MODIFY] `api/routers/chat_ws.py` / `api/routers/chat_rest.py` (迴圈控制)
- 在 WS / REST 端點中實作**對話迴圈**：
  1. 接收 User 訊息。
  2. 呼叫 `group_router` 決定 `target_character_id`。
  3. 呼叫 `coordinator` 執行對話並回傳該角色的回覆。
  4. 將該回覆加入 `session_messages`（標註角色名稱）。
  5. 再次呼叫 `group_router`，判斷是否有其他 AI 想要補充或回應。
  6. 若有，重複步驟 3-5；若無或達到 `max_bot_turns` 上限，則中斷迴圈等待 User 下一次輸入。

---

### `core/` (底層記憶與提示詞)

#### [MODIFY] `storage_manager.py` & `core_memory.py`
- 確保所有檢索與儲存方法都能準確透過 `(user_id, target_character_id, visibility)` 提取資料。
- 更新寫入邏輯，確保在群組對話中，各 AI 產生的記憶不會互相污染。

#### [MODIFY] `prompts_default.json`
- 新增 `group_router_system` 提示詞模板。
- 修改對話紀錄的組裝邏輯，將歷史對話中的 `assistant` 訊息明確標示為 `[BotName]: ...`，讓各 AI 知道哪句話是哪位同伴說的。

---

### 前端 UI (Dashboard & Streamlit)

#### [MODIFY] `static/dashboard.html` & `static/chat.html`
- 新增群組管理介面（如：「新增群組對話」、「邀請角色加入」的按鈕）。
- 聊天視窗需要顯示不同的 AI 大頭貼或名稱（依賴 Roadmap 中「對話介面識別」的實作）。

#### [MODIFY] `ui/` (Streamlit)
- 同步更新 Streamlit 介面，支援建立群組並選擇多位參與角色。

## Verification Plan

### Automated Tests
- 新增 `pytest` 測試，驗證 `group_router` 在給定多人對話上下文時，是否能正確選出應該發話的角色或回傳 None。
- 測試記憶儲存模組，確保群組對話中產生的記憶區塊會正確綁定到對應的發話 AI 上，不會產生串音。

### Manual Verification
- 啟動伺服器，在 Dashboard 建立一個包含兩位不同個性（例如：一個極端理性、一個極端感性）AI 的群組。
- 發送一句有爭議的話題，觀察兩位 AI 是否能輪流回應，並互相針對對方的論點進行反駁或補充。
- 確認對話超過 3 次（或設定的上限）後會自動停止，不會產生無限迴圈。
- 檢查 SQLite 資料庫，確認兩者的 Profile 與 Core Memories 依然是互相獨立的。
