# YouTubeBridge Topic Pack 向量檢索落地計畫

## 目標

Topic Pack / fact card 是直播用的外部知識資料庫，不寫入 `memory_blocks`，也不視為角色或使用者私人記憶。
每張 fact card 會建立 TextEmbedding，直播互動時由 Bridge 依目前留言、導播方向與注入內容進行相似度檢索，挑出相關資料注入上下文。

## 資料模型

- `topic_packs`
  - 直播資料包，例如「四月新番資料包」。
- `topic_pack_entries`
  - fact card 本文，包含 `title`、`body`、`source_type`、`tags_json`。
  - `source_url` 保留為可選溯源欄位，但 UI 不要求手動輸入，也不注入對話上下文。
- `topic_pack_entry_embeddings`
  - 每張 fact card 對應一筆向量。
  - 欄位包含 `entry_id`、`pack_id`、`embedding_model`、`embedding_dim`、`embedding_blob`、`content_hash`。
- `live_session_topic_packs`
  - 將資料包綁定到直播 session。

## 流程

1. 建立或選擇 Live Session。
2. 建立 Topic Pack，或使用自動資料卡建立功能。
3. 建立 fact card 後立即呼叫 MemoriaCore `/llm/embed` 產生向量。
4. 直播注入、導播發話、手動向量檢索時，Bridge 會將查詢文字 embedding 後比對綁定資料包。
5. 只把相關 fact card 的精簡內容注入上下文，不寫入 `memory_blocks`。

## API

- MemoriaCore
  - `POST /api/v1/llm/embed`
    - admin-only。
    - 回傳 BGE-M3 dense embedding。
- YouTubeBridge
  - `POST /topic-packs/{pack_id}/entries`
    - 建立 fact card 並嘗試立即索引。
  - `POST /topic-packs/{pack_id}/embeddings/rebuild`
    - 重建資料包內所有 fact card 向量。
  - `GET /sessions/{session_id}/topic-packs/search`
    - 對本場直播綁定資料包做向量檢索。
  - `POST /sessions/{session_id}/topic-packs/auto-build`
    - 依直播方向或指定主題自動建立 fact cards，並建立向量。

## Research Gate

Research Gate 是 Bridge 控制的外部查詢閘門，不讓角色自由搜尋網頁。

- 通過 gate 的查詢會整理成 fact card。
- fact card 建立後會進入 Topic Pack 向量索引。
- 查詢結果只作為直播共通知識，不寫入私人記憶。
- 第一版仍保留查詢 quota 與 cooldown，避免直播中無限制搜尋。

## UI

- Topic Pack 頁籤提供：
  - 建立資料包。
  - 新增 fact card。
  - 自動建立資料卡。
  - 重建向量索引。
  - 測試向量檢索。
  - Research Gate 查詢。
- 手動 fact card 不再要求 `source_url`。
- 向量檢索結果會顯示相似度。

## 驗證重點

- fact card 建立後會產生 embedding。
- 重建索引能更新缺漏或內容變更的 embedding。
- 綁定直播資料包後，session search 只檢索該 session 的資料包。
- 對話注入與導播發話使用相似 fact card，不使用整包資料。
- Topic Pack 不寫入 `memory_blocks`。
- `source_url` 不會出現在注入內容。
