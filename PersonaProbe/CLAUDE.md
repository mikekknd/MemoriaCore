# PersonaProbe — CLAUDE.md

## 專案概述

PersonaProbe 是一個**人格採集與分析工具**，透過結構化訪談或對話片段分析，生成可供 LLM 角色扮演使用的人格規格書（persona.md）與完整心智模型報告（probe-report.md）。

## 檔案結構

```
PersonaProbe/
├── probe_engine.py   # 核心邏輯（純 Python，禁止 import streamlit）
├── app.py            # Streamlit UI（port 8502）
├── server.py         # FastAPI API server（port 8089）
├── llm_client.py     # LLM 抽象層（Ollama / OpenRouter）
├── requirements.txt
└── result/           # 輸出目錄，各案例放在子目錄
```

## 架構原則

**`probe_engine.py` 是純 Python 模組，禁止引入 Streamlit。**
所有 prompt 建構、狀態機、資料解析邏輯都在這裡。`app.py` 和 `server.py` 只負責 IO 與呼叫。

## 五種運作模式

| 模式 | 入口 | 說明 |
|------|------|------|
| 真人採集 | `app.py` sidebar | 使用者手動回答 6 個維度的問題 |
| LLM 人格生成 | `app.py` sidebar | 兩個 LLM 分別扮演提問者與受訪者，全自動運行 |
| 僅生成人格種子 | `app.py` checkbox | 只跑校準 5 題 + 重構，輸出新種子 |
| 快速人格生成 | `app.py` checkbox | 校準 5 題後一次呼叫 LLM 填寫行為模板 |
| 片段分析 | `app.py` + `server.py` | 輸入已有對話記錄，自動提取 6 維度並生成報告 |

## 關鍵常數（probe_engine.py）

### `FAST_PERSONA_BEHAVIORAL_TEMPLATE`
**唯一的 System Prompt 結構定義**。`REPORT_SCHEMA` 透過字串拼接引用它，`build_fast_persona_complete_prompt` 也引用它。**修改模板只需改這一個地方**，兩條路徑自動同步。

### `DIMENSION_SPECS`
6 個維度的完整規格（name、core_question、template、followup_layers）。互動式採集用它生成問題，片段分析用它提取證據。**是整個系統的核心 schema，新增維度邏輯前必須先更新這裡。**

### `REPORT_SCHEMA`
報告的 Markdown 格式模板，包含 `{date}`、`{mode}`、`{n_rounds}` 三個 format 佔位符。System Prompt 區塊由 `FAST_PERSONA_BEHAVIORAL_TEMPLATE` 拼入，**不含其他 `{...}` 佔位符**，呼叫 `.format()` 時不需傳入額外參數。

## 互動式採集流程（ProbeState 狀態機）

```
phase=0（校準 5 題）
  → persona_recon（LLM 重構人格種子）
  → phase=1~6（各維度：opening → followup × 3）
  → phase=7（build_profile_prompt → 生成完整報告）
```

- `state.conversation` — 完整對話記錄（提問者 + 受訪者）
- `state.respondent_memory` — 受訪者記憶，存短句事實而非完整訊息（防格式污染）
- `state.current_dim_qa` — 當前維度 Q&A，維度切換時重置

## 片段分析流程（非互動式）

```
輸入片段（純文字或 DB）
  → parse_fragment_input_text / load_fragments_from_db
  → build_fragment_extraction_prompt × 6（各維度，輸出 JSON）
  → build_fragment_aggregation_prompt（生成完整報告，含分析區塊）
  → build_persona_md_prompt（以報告的分析區塊更新現有 Persona）
```

**LLM 呼叫次數：6（提取）+ 1（聚合）+ 1（persona 更新）= 8 次**

### 重要：persona.md 更新邏輯

`build_persona_md_prompt` 的更新依據是**報告的分析區塊**，不是報告末尾的 System Prompt 區塊（後者可能只是舊 Persona 的複製）：

| 現有 Persona 節點 | 更新依據 |
|---|---|
| 情緒反應模式 | 核心動機與信念 + 矛盾地圖 |
| 決策邊界 | 決策邏輯 |
| 對話行為模式 | 表達風格 + 行動模式 |
| 強度校準 | 觀察到的模式 |

### 維度提取結果格式（JSON）

```json
{
  "evidence": ["原文引用1", "原文引用2"],
  "mechanism": "底層機制描述",
  "confidence": "high|medium|low|none"
}
```

confidence == "none" 的維度在聚合時略過；若有提供 existing_persona，則從中補全。

## LLM 介面

```python
LLMClient(config).chat(messages, stream=False) -> str | Iterator
```

- 支援 Ollama（本地）和 OpenRouter（線上）
- 預設 `max_tokens=8192`（片段分析報告較長，不可調低）
- `stream=True` 只用於互動式採集的即時顯示

## 片段分析 API（server.py）

```
POST http://localhost:8089/analyze-fragments
GET  http://localhost:8089/health
```

請求 body 重要欄位：
- `source`: `"text"` 或 `"db"`
- `existing_persona`: 現有 Persona 文字（選填，用於整合更新）
- `llm_provider` / `llm_model` / `api_key`

## 輸出結構

```
result/
  fragment-{YYYYMMDD-HHMMSS}/   # 片段分析
    probe-report.md              # 完整心智模型報告
    persona.md                   # LLM 行為模板（# Persona 開頭）
    fragment-input.md            # 原始輸入片段

  {case-name}/                   # 互動式採集（自訂命名）
    session-log.md               # 對話記錄
    profile.md / nuwa-profile.md # 心智模型報告
```

## 常見陷阱

1. **修改 System Prompt 結構**：只改 `FAST_PERSONA_BEHAVIORAL_TEMPLATE`，不要動 `REPORT_SCHEMA` 的 System Prompt 區塊（那段是自動拼入的）。

2. **新增片段分析的 prompt builder**：輸出必須是 `list[dict]`（messages 格式），由呼叫端的 `LLMClient.chat()` 執行，不在 probe_engine 內呼叫 LLM。

3. **conversation.db 讀取**：使用 Python 內建 `sqlite3`，讀 `conversation_messages` 表（欄位：`msg_id`、`session_id`、`role`、`content`），按 `msg_id` 排序。

4. **probe_engine.py 不拆檔**：兩個功能層（互動式 + 片段分析）共用 `DIMENSION_SPECS`、`REPORT_SCHEMA`、`FAST_PERSONA_BEHAVIORAL_TEMPLATE`，維持單檔降低依賴複雜度。
