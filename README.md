# 🧠 MemoriaCore: 高度演進的 AI 情境記憶引擎

**MemoriaCore** 是一個專為「深度陪伴」與「長效記憶」設計的 AI 後端系統。它結合了最新的向量檢索技術與人格側寫引擎，能讓 AI 具備真正理解使用者、記住過去細節、並隨著對話不斷演進人格的能力。

---

## ✨ 核心特性

- **長效情境記憶 (Long-term Memory)**：利用 BGE-M3 向量模型與 SQLite，實現高精度的對話檢索與話題偏移偵測。
- **動態人格引擎 (Personality Engine)**：AI 會自我觀察對話過程，並反思自己的行為，從而動態調整說話語氣與性格傾向。
- **使用者畫像 (User Profile)**：自動從對話中提取使用者的偏好、事實與關鍵資訊（如：喜好的食物、面試日期）。
- **多平台支援 (Multi-platform)**：原生支援 **Unity (WebSocket)**、**Telegram Bot**，並可輕鬆擴展至 Discord 等平台。
- **極致效能**：核心運算已遷移至 ONNX Runtime，擺脫大型 Python 庫依賴，適合邊緣推論。

---

## 🚀 快速啟動

### 1. 環境需求
- Python 3.10+
- 已安裝 Git

### 2. 安裝步驟
1. 複製本專案到本地端。
2. 執行 `setup.bat`：系統會自動建立虛擬環境 (`venv_ai_memory`) 並安裝所有必要型依賴。

### 3. 配置模型 (重要！)
由於 GitHub 檔案大小限制，**核心嵌入模型 (`model_quantized.onnx`) 未包含在儲存庫中**。
> [!CAUTION]
> 您必須手動下載 `model_quantized.onnx` 檔案，並將其放置於以下目錄：
> `StreamingAssets/Models/model_quantized.onnx`

### 4. 啟動服務
點擊 `start.bat`。這將會在一台終端機中同時啟動以下服務：
- **FastAPI 後端** (Port: 8088)
- **Streamlit 管理後台** (Port: 8501)
- **Telegram Bot** (若已在 `user_prefs.json` 配置 Token)

---

## 🔒 安全性說明

本專案已配置嚴格的 `.gitignore` 規則：
- **憑證保護**：`user_prefs.json` 包含您的 API Keys 與 Bot Tokens，**不會**被上傳至 Git。
- **資料隱私**：所有的 `.db` 資料庫與 `chat_history.json` 均不會上傳，確保您的本地對話紀錄隱私。
- **大型檔案**：超過 100MB 的 ONNX 模型檔案已被排除。

---

## 🛠 技術架構 (Technical Architecture)

本專案在移植至 Unity/C# 時遵循以下關鍵原則：
1. **絕對泛化 (Zero-Hardcoding)**：嚴禁使用應編碼標籤，所有實體提取均依賴 LLM 的零樣本歸納。
2. **語意閘道 (Semantic-Gated Recency)**：結合時間權重與向量相似度，防止無關的近期記憶干擾 AI 決策。
3. **雙軌檢索 (Hybrid Retrieval)**：同時使用 Dense Vector (語意) 與 Sparse Vector (關鍵字) 進行混合檢索。

*(更多技術實作詳情請參閱 `docs/` 目錄下的文件)*

---

## 📄 授權條款 (License)

本專案採用 **自主評估中**。 (建議採用 MIT 或 AGPL v3)
