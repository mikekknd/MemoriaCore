# YouTubeBridge Live Query Resolver 執行方案

## 目標

將 Research Gate 重新定位為「不可信觀眾查詢與外部搜尋結果的安全閘門」，而不是管理者手動查資料的同義詞。FactCards 保持為直播前準備與直播中補卡的參考資料庫；觀眾提問先走本地可回答性判定，本地資料不足時才使用 Research Gate。

## 架構

1. 觀眾留言先經 SafetyLLM，只有 `clean` 且已完成安全檢查的文字可進入 Live Query Resolver。
2. Live Query Resolver 從安全文字判定是否為 factual question。
3. 若不是 factual question，沿用原本 Topic Pack 召回作為背景資料。
4. 若是 factual question，先用本場綁定 Topic Pack 做向量召回，但不允許 fallback 成任意列出資料包內容。
5. 若召回結果達到可回答性門檻，將命中的 FactCards 放入角色 external context。
6. 若召回不足且本場啟用 Research Gate，使用安全後的觀眾問題作為查詢，建立 Research Gate Fact Card，並只把整理後的 Fact Card 內容交給角色。
7. 若 Research Gate 關閉、冷卻中或失敗，角色只看到安全後觀眾留言，不會看到不相關 FactCards 或 raw 搜尋結果。

## 實作落點

- `YouTubeBridge/bridge_engine.py`
  - 新增本地召回 helper，將「搜尋 entries」與「格式化 context」拆開。
  - 新增 audience query 判定與 answerability gate。
  - 新增同步 Research Gate 核心 `_research_request_sync()`，讓角色注入前可先取得整理後資料卡。
  - `research_request()` 保留原 API 行為，改為背景 thread 執行同步核心後再 broadcast。
  - `build_external_context()` 改為透過 Live Query Resolver 決定要放入 FactCards、Research Gate 結果或只放留言。

- `YouTubeBridge/tests/test_bridge_engine.py`
  - 新增低相似度不 fallback unrelated FactCard 的測試。
  - 新增本地資料不足且 Research Gate 啟用時，會使用安全查詢產生整理後資料卡的測試。

## Pass 條件

- 觀眾問題與資料包語意不相近時，不再把最接近但不相關的 Fact Card 塞給角色。
- Research Gate 啟用且本地資料不足時，角色 external context 只包含整理後 Fact Card，不包含 raw search dump。
- SafetyLLM 仍是觀眾文字進入查詢流程前的第一道門。
- 既有 Topic Pack usage、FactCards、Safety、Research Gate API 測試通過。

## 後續可拆工作

- 將 factual question 判定從 deterministic heuristic 升級成 prompt/schema contract。
- 將外部搜尋 worker 化，避免同步等待搜尋 provider 影響 8091 event loop。
- UI 將「Research Gate」文案拆成「觀眾查詢安全閘門」與「管理者補資料」兩種狀態。
