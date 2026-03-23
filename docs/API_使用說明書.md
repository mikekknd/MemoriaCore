# LLM Memory System — FastAPI 後端 API 使用說明書

> **Base URL**: `http://localhost:8000/api/v1`
> **協議**: REST (JSON) + WebSocket
> **版本**: v1

---

## 目錄

1. [快速開始](#1-快速開始)
2. [健康檢查](#2-健康檢查)
3. [Session 管理](#3-session-管理)
4. [對話 — 同步 REST](#4-對話--同步-rest)
5. [對話 — WebSocket 串流](#5-對話--websocket-串流)
6. [記憶區塊 (Memory Blocks)](#6-記憶區塊-memory-blocks)
7. [核心認知 (Core Memories)](#7-核心認知-core-memories)
8. [使用者畫像 (Profile)](#8-使用者畫像-profile)
9. [力導向圖 (Graph)](#9-力導向圖-graph)
10. [系統管理](#10-系統管理)
11. [日誌查詢](#11-日誌查詢)
12. [錯誤處理](#12-錯誤處理)
13. [WebSocket 訊框協議總覽](#13-websocket-訊框協議總覽)

---

## 1. 快速開始

### 啟動伺服器

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

### 最簡對話流程（三步驟）

```bash
# 步驟 1：建立 Session
curl -X POST http://localhost:8000/api/v1/session

# 步驟 2：發送訊息（替換 SESSION_ID）
curl -X POST http://localhost:8000/api/v1/chat/sync \
  -H "Content-Type: application/json" \
  -d '{"content": "你好，我叫小明", "session_id": "SESSION_ID"}'

# 步驟 3：查看回覆
# 回應中的 reply 欄位即為 AI 回覆
```

---

## 2. 健康檢查

### `GET /health`

確認伺服器、ONNX 模型與資料庫是否正常運作。

**回應範例**:
```json
{
  "onnx_loaded": true,
  "db_accessible": true,
  "uptime_seconds": 123.45
}
```

**呼叫範例**:
```bash
curl http://localhost:8000/api/v1/health
```

```python
import requests
r = requests.get("http://localhost:8000/api/v1/health")
print(r.json())
```

---

## 3. Session 管理

Session 是對話狀態的容器，所有對話端點都透過 `session_id` 追蹤上下文。

### `POST /session` — 建立新 Session

**回應範例**:
```json
{
  "session_id": "a1b2c3d4-e5f6-...",
  "messages": [],
  "last_entities": [],
  "created_at": "2026-03-20T10:00:00",
  "last_active": "2026-03-20T10:00:00"
}
```

```bash
curl -X POST http://localhost:8000/api/v1/session
```

### `GET /session/{session_id}` — 取得 Session 狀態

```bash
curl http://localhost:8000/api/v1/session/a1b2c3d4-e5f6-...
```

### `DELETE /session/{session_id}` — 刪除 Session

```bash
curl -X DELETE http://localhost:8000/api/v1/session/a1b2c3d4-e5f6-...
```

### `POST /session/{session_id}/bridge` — 話題橋接

話題偏移後保留最後實體，清除舊對話歷史。

```bash
curl -X POST http://localhost:8000/api/v1/session/a1b2c3d4-e5f6-.../bridge
```

---

## 4. 對話 — 同步 REST

### `POST /chat/sync`

Streamlit 等不支援 WebSocket 的客戶端使用此端點。一次呼叫完成完整對話流程（話題偵測 → 記憶管線 → 檢索 → LLM 生成）。

**Request Body**:
| 欄位 | 型別 | 必填 | 預設值 | 說明 |
|------|------|------|--------|------|
| `content` | string | ✅ | — | 使用者訊息 |
| `session_id` | string | ❌ | null | 不帶則自動建立新 Session |

**呼叫範例**:
```bash
curl -X POST http://localhost:8000/api/v1/chat/sync \
  -H "Content-Type: application/json" \
  -d '{"content": "我最近在學鋼琴", "session_id": "a1b2c3d4-..."}'
```

```python
import requests

resp = requests.post("http://localhost:8000/api/v1/chat/sync", json={
    "content": "我最近在學鋼琴",
    "session_id": "a1b2c3d4-..."
})
data = resp.json()
print("AI:", data["reply"])
print("實體:", data["extracted_entities"])
print("記憶命中:", data["retrieval_context"]["block_count"])
```

**回應範例**:
```json
{
  "reply": "學鋼琴很棒！你目前學到什麼程度了呢？",
  "extracted_entities": ["鋼琴", "學習"],
  "retrieval_context": {
    "original_query": "我最近在學鋼琴",
    "expanded_keywords": "鋼琴 音樂 樂器 練習",
    "inherited_tags": [],
    "has_memory": true,
    "block_count": 1,
    "threshold": 0.5,
    "hard_base": 0.55,
    "confidence": 0.8,
    "block_details": [
      {"id": 1, "overview": "[核心實體]: 鋼琴", "hybrid": 0.72, "dense": 0.68, "sparse": 0.15, "recency": 0.03, "importance": 1.0}
    ],
    "core_debug_text": "未觸發核心認知。",
    "profile_debug_text": "觸發 1 筆偏好: 音樂偏好=喜歡古典樂 (0.621)",
    "dynamic_prompt": "..."
  }
}
```

---

## 5. 對話 — WebSocket 串流

### `WS /chat/stream?session_id={uuid}`

Unity 等需要即時串流的客戶端使用 WebSocket。連線後伺服器會自動推送 `session_init` 訊框。

**連線範例**:
```
ws://localhost:8000/api/v1/chat/stream?session_id=a1b2c3d4-...
```

不帶 `session_id` 則自動建立新 Session。

### 客戶端可發送的訊框

**發送聊天訊息**:
```json
{"type": "chat_message", "content": "你好"}
```

**心跳 Ping**:
```json
{"type": "ping"}
```

**清除上下文（重新開始）**:
```json
{"type": "clear_context"}
```

### 伺服器推送的訊框類型

| type | 說明 | 關鍵欄位 |
|------|------|----------|
| `session_init` | 連線或清除上下文後推送 | `session_id` |
| `retrieval_context` | 檢索偵錯資訊 | `data` (包含 block_details 等) |
| `token` | 串流 token（目前整段推送） | `content` |
| `chat_done` | 生成完畢 | `reply`, `extracted_entities` |
| `system_event` | 管線事件通知 | `action`, 依事件不同附加欄位 |
| `error` | 錯誤 | `code`, `message` |
| `pong` | 心跳回應 | — |

### system_event 的 action 類型

| action | 觸發時機 | 附加欄位 |
|--------|----------|----------|
| `topic_shift` | 偵測到話題偏移 | `cohesion_score` |
| `pipeline_complete` | 記憶管線執行完畢 | `new_blocks` |
| `profile_updated` | 使用者畫像更新 | `facts_count` |
| `preferences_aggregated` | 偏好聚合完成 | `promoted_count` |
| `graph_updated` | 圖結構改變 | `entity` |

### Python WebSocket 範例

```python
import asyncio
import json
import websockets

async def chat():
    uri = "ws://localhost:8000/api/v1/chat/stream"
    async with websockets.connect(uri) as ws:
        # 接收 session_init
        init = json.loads(await ws.recv())
        print(f"Session: {init['session_id']}")

        # 發送訊息
        await ws.send(json.dumps({
            "type": "chat_message",
            "content": "你好，我叫小明"
        }))

        # 持續接收直到 chat_done
        while True:
            frame = json.loads(await ws.recv())
            if frame["type"] == "token":
                print(f"AI: {frame['content']}")
            elif frame["type"] == "chat_done":
                print(f"完成。實體: {frame['extracted_entities']}")
                break
            elif frame["type"] == "system_event":
                print(f"[事件] {frame['action']}")
            elif frame["type"] == "retrieval_context":
                print(f"[檢索] 命中 {frame['data']['block_count']} 個記憶")

asyncio.run(chat())
```

---

## 6. 記憶區塊 (Memory Blocks)

### `GET /memory/blocks` — 取得所有記憶區塊

| 參數 | 型別 | 預設 | 說明 |
|------|------|------|------|
| `include_vectors` | bool | false | 是否包含嵌入向量 |

```bash
curl "http://localhost:8000/api/v1/memory/blocks"
curl "http://localhost:8000/api/v1/memory/blocks?include_vectors=true"
```

### `GET /memory/blocks/{block_id}` — 取得單一記憶區塊

```bash
curl http://localhost:8000/api/v1/memory/blocks/blk_abc123
```

### `PUT /memory/blocks/{block_id}` — 更新區塊概覽

```bash
curl -X PUT http://localhost:8000/api/v1/memory/blocks/blk_abc123 \
  -H "Content-Type: application/json" \
  -d '{"new_overview": "[核心實體]: 鋼琴, 練習\n[情境摘要]: 使用者學鋼琴已半年"}'
```

### `DELETE /memory/blocks/{block_id}` — 刪除記憶區塊

```bash
curl -X DELETE http://localhost:8000/api/v1/memory/blocks/blk_abc123
```

### `POST /memory/search` — 混合語意搜尋

此端點使用 Dense + Sparse 雙軌搜尋搭配 MMR 多樣性演算法。

**Request Body**:
| 欄位 | 型別 | 預設 | 說明 |
|------|------|------|------|
| `query` | string | (必填) | 搜尋語句 |
| `combined_keywords` | string | `""` | 稀疏檢索關鍵字 |
| `top_k` | int | 2 | 回傳數量 |
| `alpha` | float | 0.6 | Dense/Sparse 權重 (1.0=全 Dense) |
| `threshold` | float | 0.5 | 最低分數門檻 |
| `hard_base` | float | 0.55 | Hard-Base 防誤觸門檻 |

```bash
curl -X POST http://localhost:8000/api/v1/memory/search \
  -H "Content-Type: application/json" \
  -d '{"query": "鋼琴練習", "combined_keywords": "鋼琴 音樂", "top_k": 3}'
```

```python
resp = requests.post(f"{API}/memory/search", json={
    "query": "鋼琴練習",
    "combined_keywords": "鋼琴 音樂 古典",
    "top_k": 3,
    "alpha": 0.6,
    "hard_base": 0.55
})
for result in resp.json():
    print(f"[{result['block_id']}] {result['overview']}")
```

### `POST /memory/expand-query` — 查詢擴展

```bash
curl -X POST http://localhost:8000/api/v1/memory/expand-query \
  -H "Content-Type: application/json" \
  -d '{"query": "今天天氣好", "recent_history": [{"role": "user", "content": "你好"}]}'
```

---

## 7. 核心認知 (Core Memories)

核心認知是從多次對話中蒸餾出的高層洞察。

### `GET /memory/core` — 取得所有核心認知

```bash
curl http://localhost:8000/api/v1/memory/core
```

**回應範例**:
```json
[
  {
    "core_id": "core_001",
    "timestamp": "2026-03-20T10:00:00",
    "insight": "使用者是一位軟體工程師，專注於遊戲開發",
    "encounter_count": 3.0
  }
]
```

### `POST /memory/core/search` — 語意搜尋核心認知

```bash
curl -X POST http://localhost:8000/api/v1/memory/core/search \
  -H "Content-Type: application/json" \
  -d '{"query": "工程師", "top_k": 1, "threshold": 0.45}'
```

### `DELETE /memory/core/{core_id}` — 刪除核心認知

```bash
curl -X DELETE http://localhost:8000/api/v1/memory/core/core_001
```

---

## 8. 使用者畫像 (Profile)

### `GET /profile` — 取得所有偏好事實

```bash
curl http://localhost:8000/api/v1/profile
curl "http://localhost:8000/api/v1/profile?include_tombstones=true"
```

**回應範例**:
```json
[
  {
    "fact_key": "喜歡的音樂類型",
    "fact_value": "古典樂",
    "category": "preference",
    "confidence": 0.95,
    "timestamp": "2026-03-20T10:00:00",
    "source_context": "使用者提到喜歡聽蕭邦"
  }
]
```

### `GET /profile/{fact_key}` — 取得單一事實

```bash
curl http://localhost:8000/api/v1/profile/喜歡的音樂類型
```

### `PUT /profile/{fact_key}` — 新增或更新事實

```bash
curl -X PUT http://localhost:8000/api/v1/profile/喜歡的音樂類型 \
  -H "Content-Type: application/json" \
  -d '{
    "fact_value": "爵士樂",
    "category": "preference",
    "source_context": "使用者改變了音樂偏好",
    "confidence": 0.9
  }'
```

### `DELETE /profile/{fact_key}` — 刪除事實

```bash
curl -X DELETE http://localhost:8000/api/v1/profile/喜歡的音樂類型
```

### `POST /profile/search` — 語意搜尋偏好

```bash
curl -X POST http://localhost:8000/api/v1/profile/search \
  -H "Content-Type: application/json" \
  -d '{"query": "音樂", "top_k": 3, "threshold": 0.5}'
```

### `GET /profile/static-prompt` — 取得靜態畫像 Prompt

回傳已格式化的使用者畫像，可直接嵌入系統 Prompt。

```bash
curl http://localhost:8000/api/v1/profile/static-prompt
```

---

## 9. 力導向圖 (Graph)

### `GET /memory/graph` — 取得圖結構資料

伺服器端計算所有節點間的餘弦相似度，回傳超過門檻的邊。適用於 Unity 力導向圖可視化。

| 參數 | 型別 | 預設 | 說明 |
|------|------|------|------|
| `similarity_threshold` | float | 0.6 | 邊的相似度門檻 |

```bash
curl "http://localhost:8000/api/v1/memory/graph?similarity_threshold=0.5"
```

**回應範例**:
```json
{
  "nodes": [
    {"id": "blk_001", "type": "block", "label": "[核心實體]: 鋼琴", "weight": 1.0},
    {"id": "core_001", "type": "core", "label": "使用者喜歡音樂", "weight": 2.0},
    {"id": "prof_音樂", "type": "profile", "label": "音樂偏好=古典樂", "weight": 1.0}
  ],
  "edges": [
    {"source": "blk_001", "target": "core_001", "weight": 0.78}
  ]
}
```

---

## 10. 系統管理

### `GET /system/config` — 取得系統設定

```bash
curl http://localhost:8000/api/v1/system/config
```

**回應範例**:
```json
{
  "routing_config": {"chat": "openai:gpt-4o-mini", "pipeline": "ollama:llama3.2"},
  "temperature": 0.7,
  "ui_alpha": 0.6,
  "memory_threshold": 0.5,
  "memory_hard_base": 0.55,
  "shift_threshold": 0.55,
  "cluster_threshold": 0.75,
  "embed_model": "bge-m3:latest",
  "openai_key": "",
  "or_key": ""
}
```

### `PUT /system/config` — 部分更新設定

只需傳送要修改的欄位，支援熱重載 LLM Router。

```bash
curl -X PUT http://localhost:8000/api/v1/system/config \
  -H "Content-Type: application/json" \
  -d '{"temperature": 0.5, "memory_hard_base": 0.6}'
```

### `GET /system/prompt` — 取得系統 Prompt

```bash
curl http://localhost:8000/api/v1/system/prompt
```

### `PUT /system/prompt` — 更新系統 Prompt

```bash
curl -X PUT http://localhost:8000/api/v1/system/prompt \
  -H "Content-Type: application/json" \
  -d '{"prompt": "你是一位友善的 AI 助手..."}'
```

### `POST /system/consolidate` — 執行記憶合併

在背景執行記憶蒸餾（核心認知 + 情境融合），不阻塞請求。

```bash
curl -X POST http://localhost:8000/api/v1/system/consolidate \
  -H "Content-Type: application/json" \
  -d '{"cluster_threshold": 0.75, "min_group_size": 2}'
```

### `POST /system/preference-aggregate` — 執行偏好聚合

```bash
curl -X POST http://localhost:8000/api/v1/system/preference-aggregate \
  -H "Content-Type: application/json" \
  -d '{"score_threshold": 3.0}'
```

### `POST /system/synthetic` — 產生合成測試資料

```bash
curl -X POST http://localhost:8000/api/v1/system/synthetic \
  -H "Content-Type: application/json" \
  -d '{"topic": "旅行回憶", "turns": 6}'
```

---

## 11. 日誌查詢

### `GET /logs` — 分頁查詢 LLM 追蹤日誌

| 參數 | 型別 | 預設 | 說明 |
|------|------|------|------|
| `limit` | int | 100 | 每頁筆數 (1-1000) |
| `offset` | int | 0 | 偏移量 |
| `type` | string | null | 篩選日誌類型 |
| `category` | string | null | 篩選類別 |

```bash
curl "http://localhost:8000/api/v1/logs?limit=20&offset=0&type=llm_call"
```

### `DELETE /logs` — 清除所有日誌

```bash
curl -X DELETE http://localhost:8000/api/v1/logs
```

---

## 12. 錯誤處理

所有 REST 端點在發生錯誤時回傳統一格式：

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "query field is required"
  }
}
```

### HTTP 狀態碼對照

| 狀態碼 | 對應例外 | 說明 |
|--------|----------|------|
| 400 | `ValueError` | 參數驗證失敗 |
| 404 | `FileNotFoundError` | 資源不存在 |
| 422 | Pydantic 驗證失敗 | Request Body 格式錯誤 |
| 500 | 其他例外 | 內部伺服器錯誤 |

### WebSocket 錯誤訊框

| code | 說明 |
|------|------|
| `INVALID_JSON` | 客戶端發送的不是合法 JSON |
| `UNKNOWN_FRAME` | 未知的 type 欄位 |
| `EMPTY_MESSAGE` | 空白訊息 |
| `SESSION_LOST` | Session 過期或遺失 |
| `INTERNAL` | 伺服器內部錯誤 |

---

## 13. WebSocket 訊框協議總覽

### 客戶端 → 伺服器

```
┌─────────────────┬──────────────────────────────┐
│ type            │ 欄位                         │
├─────────────────┼──────────────────────────────┤
│ chat_message    │ content: string              │
│ ping            │ (無)                         │
│ clear_context   │ (無)                         │
└─────────────────┴──────────────────────────────┘
```

### 伺服器 → 客戶端

```
┌─────────────────────┬────────────────────────────────────┐
│ type                │ 欄位                               │
├─────────────────────┼────────────────────────────────────┤
│ session_init        │ session_id: string                 │
│ retrieval_context   │ data: RetrievalContextDTO          │
│ token               │ content: string                    │
│ chat_done           │ reply: string,                     │
│                     │ extracted_entities: string[]        │
│ system_event        │ action: string, + 動態欄位         │
│ error               │ code: string, message: string      │
│ pong                │ (無)                               │
└─────────────────────┴────────────────────────────────────┘
```

### 一次完整對話的訊框時序

```
客戶端                          伺服器
  │                               │
  │◄── session_init ──────────────│  (連線後立即推送)
  │                               │
  │── chat_message ──────────────►│
  │                               │  (執行對話編排...)
  │◄── system_event(topic_shift) ─│  (若偵測到話題偏移)
  │◄── system_event(pipeline_*) ──│  (管線事件)
  │◄── retrieval_context ─────────│  (檢索偵錯資訊)
  │◄── token ─────────────────────│  (AI 回覆內容)
  │◄── chat_done ─────────────────│  (完成信號)
  │                               │
  │── ping ──────────────────────►│
  │◄── pong ──────────────────────│
  │                               │
```

---

## 附錄：Python 完整對話範例

```python
import requests

API = "http://localhost:8000/api/v1"

# 1. 建立 Session
session = requests.post(f"{API}/session").json()
sid = session["session_id"]
print(f"Session: {sid}")

# 2. 多輪對話
messages = ["你好，我叫小明", "我最近在學鋼琴", "有什麼曲子推薦嗎？"]

for msg in messages:
    resp = requests.post(f"{API}/chat/sync", json={
        "content": msg,
        "session_id": sid
    }).json()

    print(f"User: {msg}")
    print(f"AI:   {resp['reply']}")
    print(f"  實體: {resp['extracted_entities']}")
    print(f"  記憶: {resp['retrieval_context']['block_count']} 個區塊命中")
    print()

# 3. 查看記憶區塊
blocks = requests.get(f"{API}/memory/blocks").json()
print(f"目前共 {len(blocks)} 個記憶區塊")

# 4. 搜尋記憶
results = requests.post(f"{API}/memory/search", json={
    "query": "鋼琴",
    "top_k": 2
}).json()
for r in results:
    print(f"  [{r['block_id']}] {r['overview'][:50]}...")

# 5. 查看使用者畫像
profile = requests.get(f"{API}/profile").json()
for fact in profile:
    print(f"  {fact['fact_key']}: {fact['fact_value']}")

# 6. 清理 Session
requests.delete(f"{API}/session/{sid}")
```
