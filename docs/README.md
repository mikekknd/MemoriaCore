# MemoriaCore 文件索引

本目錄只保留可作為長期參考的文件。一次性執行計畫、已完成 roadmap、除錯紀錄與過期實作草案不要長期放在 `docs/`；若需要暫存，完成後應刪除或轉成下列穩定文件的一小段維護規則。

## 主要入口

- [架構說明.md](架構說明.md)：高層系統圖、主要 Module、請求流程與服務端口。
- [codebase-structure.md](codebase-structure.md)：Agent 查找檔案職責、package 切分與 SECTION 原則時使用。
- [API_使用說明書.md](API_使用說明書.md)：FastAPI REST / WebSocket / SSE 對外 Interface。

## Runtime 與維運

- [runtime-log-policy.md](runtime-log-policy.md)：runtime log 放置與清理規則。
- [SECURITY.md](SECURITY.md)：正式上線前的安全檢查。

## 記憶與人格架構

- [memory-isolation-architecture.md](memory-isolation-architecture.md)：`user_id × character_id × visibility` 記憶隔離規則。
- [persona-tree-architecture.md](persona-tree-architecture.md)：Path D trait evolution 架構。
- [personality-api-modernization.md](personality-api-modernization.md)：人格 API 去除 `active_character_id` 隱性目標的規則。
- [proactive-topic-architecture.md](proactive-topic-architecture.md)：背景話題 global pool 與角色領取規則。
- [weather-cache-architecture.md](weather-cache-architecture.md)：SU private face 專用 weather prompt cache。

## UI 與整合

- [i18n-maintenance-guide.md](i18n-maintenance-guide.md)：修改 UI 可見文字前必讀的維護檢查清單。
- [i18n-ready-backlog.md](i18n-ready-backlog.md)：i18n 後續工作清單。
- [Discord_Bot_使用說明.md](Discord_Bot_使用說明.md)：Discord Bot 建立、掛載與排查。
- [superpowers/specs/2026-05-08-youtubebridge-live-episode-plan-design.md](superpowers/specs/2026-05-08-youtubebridge-live-episode-plan-design.md)：LiveEpisodePlan 長期 schema 與導播契約設計。

## 文件維護規則

- `CLAUDE.md` 是 agent 規則入口；本目錄不要重複維護相同規則，只保留可展開的細節文件。
- `架構說明.md` 保持高層；檔案責任細節放 `codebase-structure.md`。
- Backlog 與檢查清單分開：長期待辦放 backlog，操作前檢查放 guide。
- 文件若自稱 legacy、deprecated 或只服務已完成工作，優先刪除；若仍有價值，改寫成現行規則或放進本索引的穩定分類。
