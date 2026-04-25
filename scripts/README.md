# Scripts 說明

此目錄用於一次性資料生成與實驗性腳本，不屬於正式 pipeline。

---

## `seed_persona_traits_prototype.py`

**用途**：以手工劇本（不呼叫 LLM）寫入 Path D 測試資料，專門用於驗證前端視覺化（`persona_tree.html`）的節點、Edges、與 B' 休眠行為。

**不使用 LLM**，直接寫入 `persona_snapshots.db`，UUID 為預先定義的短碼（t1~t13）。

```bash
python scripts/seed_persona_traits_prototype.py
```

產出 `character_id = catgirl-traits-proto`，7 個版本，13 個 traits（含 2 個已休眠）。

---

## `generate_path_d_snapshots.py`

**用途**：從 `PersonaProbe/result/fragment-*/` 的既有資料出發，以真實 LLM（Ollama）萃取產生具有正確 UUID（`uuid4().hex`）的 Path D snapshot。

**需要 Ollama 正常運作**。每個 fragment 執行 2 次 LLM 呼叫（trait 萃取 + 敘事報告）。

```bash
python scripts/generate_path_d_snapshots.py --model llama3.2 --reset

# 自訂參數
python scripts/generate_path_d_snapshots.py --model llama3.2 --character-id my-char --ollama-base-url http://localhost:11434 --reset
```

---

## 兩個腳本的差異

| 項目 | `seed_persona_traits_prototype.py` | `generate_path_d_snapshots.py` |
|------|---|---|
| LLM 呼叫 | ❌ 無 | ✅ 每版 2 次 |
| UUID 來源 | 劇本預先定義（t1~t13） | 自動 `uuid4().hex` |
| `parent_key` | 劇本寫死 | LLM 決定 + cosine fallback |
| `evolved_prompt` | 從 `fragment-*/persona.md` 讀入 | 從 `fragment-*/persona.md` 讀入 |
| `summary` | 劇本寫死 | 從 `fragment-*/probe-report.md` 讀入 |
| 使用場景 | 前端視覺化快速驗證 | 正式 Path D 流程生成真實 UUID 資料 |

---

## 資料庫

兩個腳本都寫入根目錄的 `persona_snapshots.db`。

- `character_id = catgirl-traits-proto`（seed 腳本）
- `character_id = catgirl-fragment`（generate 腳本，預設值）

可用 `--reset` 清除舊資料後重新生成。
不加 `--reset` 會接續舊版本往上遞增（version 3, 4, 5...），適合增補新 fragment。