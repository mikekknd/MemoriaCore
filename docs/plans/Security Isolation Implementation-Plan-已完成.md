# 系統權限與隔離修復 (Security & Isolation) Implementation Plan

本計畫旨在解決系統中的四個安全性與權限隔離問題，確保多使用者環境下（尤其是群組對話與背景任務）不會發生資料串音與越權操作。

## User Review Required

> [!IMPORTANT]  
> 關於「背景話題搜尋」：根據計畫，我們將把 `background_gatherer` 限制為**僅針對首位 Admin (SU) 執行**，並且以 `visibility="private"` 寫入話題快取。這表示一般使用者的興趣將不會觸發背景爬蟲，背景話題將成為 SU 專屬功能。請確認此改動是否符合預期。

> [!WARNING]
> 關於「天氣快取」：我們將重構 `weather_cache.json` 的結構，改為以 `city` (城市名稱) 作為 Key 的字典，以便支援多個城市的獨立快取。這會讓既有的快取失效，系統將在更新後自動重新抓取。

## Open Questions

無。若上述行為確認無誤即可開始實作。

## Proposed Changes

---

### 人格演化系統 (Persona Evolution)

解決群組對話中「其他 AI 的訊息被混入目標 AI 的歷史對話中進行反思」的問題，並修正「未活躍助手也會觸發反思」的錯誤。

#### [MODIFY] [probe_engine.py](file:///g:/ClaudeProject/MemoriaCore/PersonaProbe/probe_engine.py)
- **修改 `load_fragments_from_db`**: 
  - 目前的 SQL 查詢會撈出該 session 內的所有訊息。我們將在 Python 端加入過濾：當 `cm.role == 'assistant'` 時，只保留 `cm.character_id == character_id` 的訊息，藉此排除群組中其他 AI 的發言。這能確保 AI 只能根據自己與使用者的對話進行演化。

#### [MODIFY] [storage_manager.py](file:///g:/ClaudeProject/MemoriaCore/core/storage_manager.py)
- **修改 `get_last_message_time_by_character_and_channel_class`**:
  - 將過濾條件從 `cs.character_id` (session 建立者) 改為 `cm.character_id = ? AND cm.role = 'assistant'`。這樣才能準確抓出該 AI 最後一次發言的時間，避免 AI 根本沒講話卻被認為「活躍」。
- **修改 `count_messages_since_by_character_and_channel_class`**:
  - 同上，將過濾條件加上 `cm.character_id = ? AND cm.role = 'assistant'`，確保 `new_count` 只計算該 AI 自己的發言數量。這樣未活躍的 AI 就不會觸發反思條件。

---

### 工具權限與隔離 (Tool Isolation)

#### [MODIFY] [bash_tool.py](file:///g:/ClaudeProject/MemoriaCore/tools/bash_tool.py)
- **修改 `run_bash`**:
  - 新增 `runtime_context: dict | None = None` 參數。
  - 在執行指令前，從 `runtime_context` 中提取 `session_ctx["user_id"]`。
  - 透過 `StorageManager.get_user_by_id(user_id)` 確認該使用者的 `role` 是否為 `"admin"`。
  - 若非 admin，則直接回傳 JSON 錯誤訊息：`{"error": "權限不足，僅系統管理員(SU)可執行系統指令。"}`。

#### [MODIFY] [background_gatherer.py](file:///g:/ClaudeProject/MemoriaCore/core/background_gatherer.py)
- **修改 `start_background_gather_loop`**:
  - 移除預設的 `user_id="default"`。
  - 在迴圈內部，使用 `storage.get_first_admin_user()` 取得首位管理員 (SU)。
  - 若無管理員則跳過；若有，則使用該管理員的 `user_id`，並設定 `visibility="private"` 來呼叫 `run_background_topic_gather`。

#### [MODIFY] [main.py](file:///g:/ClaudeProject/MemoriaCore/api/main.py)
- **修改 `start_background_gather_loop` 呼叫**:
  - 移除呼叫時可能殘留的寫死參數，使其依賴 `background_gatherer.py` 內部的 Admin 查找邏輯。

---

### 快取架構調整 (Cache Architecture)

#### [MODIFY] [weather_cache.py](file:///g:/ClaudeProject/MemoriaCore/tools/weather_cache.py)
- **修改 `WeatherCache` 類別**:
  - 將 `weather_cache.json` 的資料結構從單一陣列 `[slots...]` 變更為以城市為鍵的字典：`{"Taipei": {"country": "TW", "slots": [...], "fetched_at": "..."}, ...}`。
  - 修改 `get_cache` 以根據 `city` 尋找對應的快取資料。
  - 修改 `update_cache` 以更新字典中特定城市的內容並寫回檔案，避免不同使用者的城市查詢互相覆蓋。

## Verification Plan

### Automated Tests
- 執行 `pytest tests/test_chat_orchestrator_unit/` 確保工具執行框架 (Middleware) 能正確傳遞 `runtime_context`。

### Manual Verification
1. **Persona Evolution**: 在具有兩個 AI 的群組對話中對話，確認 `persona_sync.py` 觸發時，`probe-report.md` 中的「原始對話參考」沒有混入另一個 AI 的回覆。
2. **Bash 權限**: 用一般使用者帳號發送 bash 執行指令（如 `查詢系統記憶體`），預期被系統明確拒絕；改用 admin 發送相同指令，預期成功執行。
3. **天氣快取**: 查詢「台北天氣」，再查詢「東京天氣」，確認 `weather_cache.json` 中同時保存了這兩個城市的快取資料，且第二次查詢沒有覆蓋掉台北的資料。
