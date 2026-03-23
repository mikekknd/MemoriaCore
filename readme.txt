🧠 AI 情境記憶系統：Unity (C#) 移植與擴充技術指引

1. 核心設計底線 (Absolute Directives)
絕對泛化原則 (Zero-Hardcoding)：系統必須適應未知玩家的自由輸入。在任何標籤生成、意圖分類或圖譜建構環節，嚴禁使用硬編碼 (Hardcoding)、白名單 (Whitelist) 或列舉清單 (Enum)。所有分類維度必須依賴大模型的零樣本 (Zero-Shot) 歸納能力與底層數學向量對齊。

2. 儲存與檢索層 (Storage & Indexing)
二進位資料庫對齊：Python 端已使用 sqlite3 將 1024 維 Dense 陣列壓縮為 BLOB 格式寫入 .db。
C# 實作注意：在 Unity 端讀取時，使用 Microsoft.Data.Sqlite。取出 BLOB 後，應使用 Buffer.BlockCopy 或 MemoryMarshal.Cast<byte, float> 將 Byte Array 直接映射為 float[]，絕對禁止轉為字串處理，以維持 0 解析延遲。
檢索層解耦 (HNSW 導入)：目前 Python 使用 $O(N)$ 暴力掃描。
C# 實作注意：在 Unity 啟動時，將 SQLite 中的 BLOB 載入記憶體，並餵給 C# 的 HNSW 函式庫（如 HNSW.Net），建立 $O(\log N)$ 的圖形索引。未來的檢索必須直接查詢 HNSW 圖，而非遍歷資料庫。

3. 邊緣推論引擎 (Edge Inference)
ONNX INT8 量化模型：系統已解除對 Python 機器學習框架 (PyTorch/HuggingFace) 的依賴，完全過渡至 ONNX Runtime。
C# 實作注意：Unity 端需導入 Microsoft.ML.OnnxRuntime 庫。必須確保 C# 端的 Tokenizer (如 Microsoft.ML.Tokenizers) 斷詞結果與 Python 端 AutoTokenizer.from_pretrained("BAAI/bge-m3") 產生的 input_ids 陣列 100% 完全一致。否則將導致 Sparse 字典權重錯位。

4. 演算法數學防線 (Algorithm Defenses)
移植至 C# 時，以下兩個數學機制一行代碼都不能漏掉，否則會引發災難性的記憶幻覺：
動態特徵遮罩 (Dynamic Feature Masking)：在計算 Sparse 餘弦相似度時，分母 (Query Norm) 只能計算「成功與記憶庫產生交集的特徵長度」。用以防禦 LLM 在 Query 階段擴充同義詞所導致的分數分散 (Dilution) 問題。
語意閘道近期加成 (Semantic-Gated Recency)：時間權重公式必須是非線性的：actual_boost = recency_boost_base * (hybrid_score ** 2)。用以防禦不相關的低分記憶，單純因為「剛剛發生」就搭便車越過 0.50 喚醒閾值。

5. 客戶端架構與 UX (UX Architecture)
非同步生產者-消費者佇列 (Async Producer-Consumer Queue)：
C# 實作注意：當偵測到話題偏移 (is_shift == true) 時，絕對禁止在主執行緒中等待記憶打包。必須立刻將舊對話 List 壓入後台佇列 (Queue)，瞬間清空 UI 讓玩家繼續對話。由背景 Worker Thread 負責向 LLM 請求摘要、呼叫 ONNX 計算特徵，並靜默寫入 SQLite。

6. 擴充系統：羈絆天賦樹 (Cognitive Web)
這是一套將冷冰冰的檢索數據，轉化為遊戲視覺回饋的無監督知識圖譜。
6.1 運作原理 (無硬編碼的動態生長)
資料來源：直接提取 JSON/SQLite 中 overview 欄位的大模型動態泛化標籤（包含微觀實體與宏觀分類）。
共現性矩陣 (Co-occurrence Matrix)：掃描所有記憶區塊。若「深月」與「傲嬌」在同一個區塊的 Entities 中同時出現一次，則這兩個節點 (Nodes) 之間的連線權重 (Edge Weight) +1。
離線字典收束：利用背景閒置算力，將字面上不同但 BGE-M3 向量極近的節點（如：Coding、程式設計）在圖譜底層進行 ID 綁定，消滅詞彙碎片化。
6.2 Unity 端實作指南
資料拉取：C# 讀取 SQLite 建立上述的 Nodes 與 Edges 清單。
力導向圖演算法 (Force-Directed Graph)：為每個 Node 實例化一個 UI Prefab（例如發光的圓球，顯示實體名稱）。為每條 Edge 實例化 UI Line Renderer。在 Update() 迴圈中套用物理力學：連線權重越高的節點，互相吸引的彈簧力 (Spring Force) 越強；所有節點彼此之間存在庫倫排斥力 (Repulsion Force)。
視覺回饋：當玩家與 AI 聊天並觸發特定記憶時，天賦樹上對應的節點與連線會隨之閃爍，讓玩家實質感受到「AI 的大腦結構正在因為我的對話而改變」。

7. 進階效能最佳化建議 (Advanced Performance Suggestions)
【定位聲明】：以下內容為「建議架構 (Suggested Architecture)」，而非絕對的改寫方案。在 C# 移植初期，可先採用標準的物件導向與 JSON 解析函式庫。若在遊戲運行時遭遇嚴重的 GC (Garbage Collection) 掉幀瓶頸，再考慮導入此架構。
7.1 零記憶體配置 (Zero-Allocation) 串流解析
資料導向思維 (DOTS)：捨棄傳統反序列化工具（如 Newtonsoft.Json 或 System.Text.Json 產生 string 的操作），避免字串在函式間傳遞產生大量的堆積 (Heap) 配置。
原生記憶體操作：當接收到 LLM 回傳的 JSON 串流 (Byte Array) 時，利用 C# 的 ReadOnlySpan<byte> 與 Utf8JsonReader，直接在底層記憶體中掃描實體標籤的邊界並進行擷取。
7.2 Unity 底層加速
Burst Compiler 與 NativeArray：在背景 Worker 中，利用 NativeArray 暫存串流資料，並透過 Burst Compiler 將解析邏輯編譯為極速原生代碼，徹底消滅 GC 卡頓。
無縫對接 ONNX：擷取出的 Byte 特徵不需轉回字串，直接透過指針 (Pointers) 傳入 Microsoft.ML.OnnxRuntime 的張量 (Tensor) 中進行 BGE-M3 數學運算，達成極致的資料流吞吐量。