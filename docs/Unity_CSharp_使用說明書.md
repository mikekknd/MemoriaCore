# LLM Memory System — Unity C# 客戶端使用說明書

> **目標平台**: Unity 2021.3+
> **依賴**: UniTask, Newtonsoft.Json
> **架構**: 事件驅動 + Producer-Consumer 主執行緒安全

---

## 目錄

1. [專案結構](#1-專案結構)
2. [環境設置](#2-環境設置)
3. [NetworkConfig 設定](#3-networkconfig-設定)
4. [NetworkManager — 核心入口](#4-networkmanager--核心入口)
5. [REST 呼叫範例](#5-rest-呼叫範例)
6. [WebSocket 對話範例](#6-websocket-對話範例)
7. [ChatController — 對話 UI](#7-chatcontroller--對話-ui)
8. [MemoryGraphController — 力導向圖](#8-memorygraphcontroller--力導向圖)
9. [ProfileController — 使用者畫像](#9-profilecontroller--使用者畫像)
10. [DTO 資料結構](#10-dto-資料結構)
11. [WebSocket 訊框處理](#11-websocket-訊框處理)
12. [自訂擴展指南](#12-自訂擴展指南)
13. [常見問題](#13-常見問題)

---

## 1. 專案結構

```
UnityClient/Assets/Scripts/
├── Network/
│   ├── DTOs/
│   │   ├── MemoryBlockDTO.cs      # 記憶區塊、搜尋結果、偏好標籤、對話訊息
│   │   ├── CoreMemoryDTO.cs       # 核心認知
│   │   ├── ProfileFactDTO.cs      # 使用者畫像事實
│   │   ├── SessionDTO.cs          # Session 與訊息
│   │   ├── GraphDTO.cs            # 力導向圖節點與邊
│   │   └── WebSocketFrames.cs     # WS 訊框基類與所有子類型
│   ├── NetworkConfig.cs           # ScriptableObject 網路設定
│   ├── RestClient.cs              # 通用非同步 REST 客戶端
│   ├── WebSocketClient.cs         # WebSocket 客戶端（自動重連）
│   └── NetworkManager.cs          # MonoBehaviour 單例，統一管理
├── Controllers/
│   ├── ChatController.cs          # 對話 UI 控制器
│   ├── MemoryGraphController.cs   # 記憶圖可視化控制器
│   └── ProfileController.cs       # 使用者畫像控制器
```

---

## 2. 環境設置

### 2.1 安裝 UniTask

1. 開啟 Unity Package Manager (`Window > Package Manager`)
2. 點擊 `+` > `Add package from git URL`
3. 輸入: `https://github.com/Cysharp/UniTask.git?path=src/UniTask/Assets/Plugins/UniTask`

### 2.2 安裝 Newtonsoft.Json

1. Package Manager > `+` > `Add package from git URL`
2. 輸入: `com.unity.nuget.newtonsoft-json`

### 2.3 設定場景

1. 建立空 GameObject，命名為 `NetworkManager`
2. 掛載 `NetworkManager.cs` 腳本
3. 建立 NetworkConfig 資產：`Assets > Create > LLMMemory > Network Config`
4. 將 NetworkConfig 資產拖曳到 NetworkManager 的 Inspector 欄位

---

## 3. NetworkConfig 設定

`NetworkConfig` 是 ScriptableObject，可在 Inspector 中調整：

| 欄位 | 預設值 | 說明 |
|------|--------|------|
| `BaseUrl` | `http://localhost:8088/api/v1` | FastAPI REST 基礎 URL |
| `WsUrl` | `ws://localhost:8088/api/v1/chat/stream` | WebSocket 端點 |
| `TimeoutMs` | `30000` | REST 請求超時 (ms) |
| `ReconnectBaseDelayMs` | `1000` | WebSocket 斷線重連基礎延遲 |
| `ReconnectMaxDelayMs` | `30000` | WebSocket 斷線重連最大延遲 |

**建立方式**：
```
在 Project 視窗右鍵 > Create > LLMMemory > Network Config
```

---

## 4. NetworkManager — 核心入口

`NetworkManager` 是 MonoBehaviour 單例，提供所有網路操作的統一介面。

### 4.1 存取方式

```csharp
// 在任何 MonoBehaviour 中
var net = NetworkManager.Instance;
```

### 4.2 公開事件

```csharp
// WebSocket 對話事件
public event Action<string> OnTokenReceived;        // 收到串流 token
public event Action<ChatDoneFrame> OnChatDone;      // 對話完成
public event Action<RetrievalContextFrame> OnRetrievalContext; // 檢索上下文
public event Action<SystemEventFrame> OnSystemEvent; // 系統事件
public event Action<ErrorFrame> OnError;             // 錯誤
public event Action<string> OnSessionInit;           // Session 初始化

// WebSocket 連線狀態
public event Action OnWsConnected;
public event Action<string> OnWsDisconnected;
```

### 4.3 生命週期

```csharp
void Awake()   → 初始化 RestClient + WebSocketClient，設為 DontDestroyOnLoad
void Update()  → 從 ConcurrentQueue 排出 WS 訊框，在主執行緒分發事件
void OnDestroy() → 關閉 WebSocket 連線
```

> **主執行緒安全**：WebSocket 接收在背景執行緒執行，訊框被推入 `ConcurrentQueue`，由 `Update()` 在主執行緒逐一取出並觸發事件，確保 Unity API 呼叫安全。

---

## 5. REST 呼叫範例

### 5.1 健康檢查

```csharp
using Cysharp.Threading.Tasks;

public class MyScript : MonoBehaviour
{
    async void Start()
    {
        var health = await NetworkManager.Instance.GetHealth();
        if (health != null)
        {
            Debug.Log($"ONNX: {health.onnx_loaded}");
            Debug.Log($"DB: {health.db_accessible}");
            Debug.Log($"Uptime: {health.uptime_seconds}s");
        }
    }
}
```

### 5.2 取得記憶區塊

```csharp
async UniTaskVoid LoadMemories()
{
    var blocks = await NetworkManager.Instance.FetchMemoryBlocks();
    if (blocks != null)
    {
        foreach (var block in blocks)
        {
            Debug.Log($"[{block.block_id}] {block.overview}");
            Debug.Log($"  對話數: {block.raw_dialogues.Count}");
            Debug.Log($"  偏好: {string.Join(", ", block.potential_preferences.Select(p => p.tag))}");
        }
    }
}
```

### 5.3 取得力導向圖

```csharp
async UniTaskVoid LoadGraph()
{
    var graph = await NetworkManager.Instance.FetchGraph(similarityThreshold: 0.5f);
    if (graph != null)
    {
        Debug.Log($"節點: {graph.nodes.Count}, 邊: {graph.edges.Count}");
        foreach (var node in graph.nodes)
            Debug.Log($"  Node: {node.id} ({node.type}) - {node.label}");
        foreach (var edge in graph.edges)
            Debug.Log($"  Edge: {edge.source} → {edge.target} ({edge.weight:F3})");
    }
}
```

### 5.4 取得使用者畫像

```csharp
async UniTaskVoid LoadProfile()
{
    var facts = await NetworkManager.Instance.FetchProfile();
    if (facts != null)
    {
        foreach (var fact in facts)
        {
            Debug.Log($"{fact.fact_key}: {fact.fact_value} " +
                      $"(category={fact.category}, confidence={fact.confidence})");
        }
    }
}
```

### 5.5 建立 Session

```csharp
async UniTaskVoid CreateNewSession()
{
    var session = await NetworkManager.Instance.CreateSession();
    if (session != null)
    {
        Debug.Log($"新 Session: {session.session_id}");
        Debug.Log($"建立時間: {session.created_at}");
    }
}
```

### 5.6 直接使用 RestClient（進階）

```csharp
// 如果需要呼叫 NetworkManager 未封裝的端點
var rest = NetworkManager.Instance.Rest; // 取得 RestClient 實例

// POST 搜尋記憶
var searchReq = new { query = "鋼琴", top_k = 3 };
var results = await rest.PostAsync<object, List<SearchResultDTO>>(
    "/memory/search", searchReq
);

// PUT 更新設定
var configUpdate = new { temperature = 0.5f };
await rest.PutAsync<object, object>("/system/config", configUpdate);

// DELETE 刪除日誌
await rest.DeleteAsync("/logs");
```

---

## 6. WebSocket 對話範例

### 6.1 啟動 WebSocket 連線

```csharp
async void Start()
{
    // 訂閱事件
    var net = NetworkManager.Instance;
    net.OnSessionInit += sid => Debug.Log($"Session: {sid}");
    net.OnTokenReceived += token => Debug.Log($"Token: {token}");
    net.OnChatDone += frame => Debug.Log($"完成: {frame.reply}");
    net.OnSystemEvent += evt => Debug.Log($"事件: {evt.action}");
    net.OnError += err => Debug.LogError($"錯誤 [{err.code}]: {err.message}");

    // 建立連線（可選帶 session_id）
    await net.StartChat(sessionId: null);
}
```

### 6.2 發送訊息

```csharp
public async void OnSendButtonClick()
{
    string userMessage = inputField.text;
    inputField.text = "";

    await NetworkManager.Instance.SendChatMessage(userMessage);
}
```

### 6.3 清除上下文

```csharp
public async void OnClearButtonClick()
{
    await NetworkManager.Instance.ClearContext();
    // 會收到新的 OnSessionInit 事件
}
```

### 6.4 完整對話 UI 範例

```csharp
using UnityEngine;
using UnityEngine.UI;
using TMPro;
using Cysharp.Threading.Tasks;
using System.Text;

public class SimpleChatUI : MonoBehaviour
{
    [SerializeField] TMP_InputField inputField;
    [SerializeField] TMP_Text chatDisplay;
    [SerializeField] Button sendButton;
    [SerializeField] ScrollRect scrollRect;

    StringBuilder _chatLog = new StringBuilder();
    StringBuilder _currentAssistant = new StringBuilder();

    async void Start()
    {
        var net = NetworkManager.Instance;

        // 訂閱事件
        net.OnTokenReceived += OnToken;
        net.OnChatDone += OnDone;
        net.OnSystemEvent += OnEvent;
        net.OnError += OnErr;

        sendButton.onClick.AddListener(OnSend);

        // 連線
        await net.StartChat();
    }

    void OnToken(string content)
    {
        _currentAssistant.Append(content);
        // 即時更新顯示（token-by-token）
        chatDisplay.text = _chatLog.ToString() + $"\n<color=#4FC3F7>AI: {_currentAssistant}</color>";
    }

    void OnDone(ChatDoneFrame frame)
    {
        _chatLog.AppendLine($"<color=#4FC3F7>AI: {frame.reply}</color>");
        _chatLog.AppendLine($"<color=#888888>  實體: [{string.Join(", ", frame.extracted_entities)}]</color>");
        _currentAssistant.Clear();
        chatDisplay.text = _chatLog.ToString();
    }

    void OnEvent(SystemEventFrame evt)
    {
        _chatLog.AppendLine($"<color=#FFA726>[系統] {evt.action}</color>");
        chatDisplay.text = _chatLog.ToString();
    }

    void OnErr(ErrorFrame err)
    {
        _chatLog.AppendLine($"<color=#EF5350>[錯誤] {err.code}: {err.message}</color>");
        chatDisplay.text = _chatLog.ToString();
    }

    async void OnSend()
    {
        string msg = inputField.text.Trim();
        if (string.IsNullOrEmpty(msg)) return;

        inputField.text = "";
        _chatLog.AppendLine($"<color=#FFFFFF>你: {msg}</color>");
        chatDisplay.text = _chatLog.ToString();

        await NetworkManager.Instance.SendChatMessage(msg);
    }

    void OnDestroy()
    {
        var net = NetworkManager.Instance;
        if (net != null)
        {
            net.OnTokenReceived -= OnToken;
            net.OnChatDone -= OnDone;
            net.OnSystemEvent -= OnEvent;
            net.OnError -= OnErr;
        }
    }
}
```

---

## 7. ChatController — 對話 UI

內建的 `ChatController` 提供開箱即用的對話 UI 控制。

### 設置步驟

1. 建立 UI Canvas，包含：
   - `TMP_Text` — 對話顯示區
   - `TMP_InputField` — 輸入框
   - `Button` — 發送按鈕
   - `ScrollRect` — 滾動容器
2. 掛載 `ChatController.cs`
3. 在 Inspector 中拖曳 UI 元件

### 功能

- 自動訂閱 `NetworkManager` 的 `OnTokenReceived` / `OnChatDone` / `OnSystemEvent` / `OnError`
- 即時逐 token 顯示 AI 回覆
- 自動滾動到底部
- 使用者/AI 訊息顏色區分
- 系統事件（話題偏移、管線完成等）顯示

---

## 8. MemoryGraphController — 力導向圖

接收 `system_event` 中的 `graph_updated` 和 `pipeline_complete` 事件，自動重新拉取圖資料。

### 設置步驟

1. 掛載 `MemoryGraphController.cs` 到任意 GameObject
2. 訂閱 `OnGraphUpdated` 事件來更新可視化

### 使用範例

```csharp
public class GraphVisualizer : MonoBehaviour
{
    [SerializeField] MemoryGraphController graphCtrl;

    void Start()
    {
        graphCtrl.OnGraphUpdated += OnGraphChanged;
    }

    void OnGraphChanged(GraphDTO graph)
    {
        // 清除舊的視覺元素
        ClearNodes();

        // 建立節點
        foreach (var node in graph.nodes)
        {
            var go = Instantiate(nodePrefab);
            go.name = node.id;

            // 依類型設定顏色
            switch (node.type)
            {
                case "block":   SetColor(go, Color.cyan); break;
                case "core":    SetColor(go, Color.yellow); break;
                case "profile": SetColor(go, Color.green); break;
            }

            // 設定標籤
            go.GetComponentInChildren<TMP_Text>().text = node.label;
        }

        // 建立邊
        foreach (var edge in graph.edges)
        {
            DrawLine(edge.source, edge.target, edge.weight);
        }

        // 套用力導向演算法...
    }
}
```

### 可配置參數

```csharp
// 在 Inspector 中調整
public float SimilarityThreshold = 0.6f;  // 邊的門檻
```

---

## 9. ProfileController — 使用者畫像

接收 `profile_updated` 和 `preferences_aggregated` 事件，自動重新拉取畫像。

### 使用範例

```csharp
public class ProfileDisplay : MonoBehaviour
{
    [SerializeField] ProfileController profileCtrl;
    [SerializeField] Transform contentParent;
    [SerializeField] GameObject factItemPrefab;

    void Start()
    {
        profileCtrl.OnProfileUpdated += OnProfileChanged;
    }

    void OnProfileChanged(List<ProfileFactDTO> facts)
    {
        // 清除舊列表
        foreach (Transform child in contentParent)
            Destroy(child.gameObject);

        // 建立事實項目
        foreach (var fact in facts)
        {
            var item = Instantiate(factItemPrefab, contentParent);
            item.GetComponentInChildren<TMP_Text>().text =
                $"{fact.fact_key}: {fact.fact_value} ({fact.category})";
        }
    }
}
```

---

## 10. DTO 資料結構

### MemoryBlockDTO

```csharp
public class MemoryBlockDTO
{
    public string block_id;
    public string timestamp;
    public string overview;
    public bool is_consolidated;
    public float encounter_count;
    public List<PreferenceTagDTO> potential_preferences;  // [{tag, intensity}]
    public List<DialogueMessageDTO> raw_dialogues;        // [{role, content}]
}
```

### SearchResultDTO（繼承 MemoryBlockDTO）

```csharp
public class SearchResultDTO : MemoryBlockDTO
{
    public float _debug_score;         // 最終混合分數
    public float _debug_recency;       // 時間衰減加成
    public float _debug_raw_sim;       // Dense 餘弦相似度
    public float _debug_sparse_raw;    // Sparse BM25-like 分數
    public float _debug_hard_base;     // Hard-Base 門檻值
    public float _debug_sparse_norm;   // 稀疏正規化分數
    public float _debug_importance;    // 重要性權重
}
```

### CoreMemoryDTO

```csharp
public class CoreMemoryDTO
{
    public string core_id;
    public string timestamp;
    public string insight;            // 蒸餾後的核心洞察
    public float encounter_count;
}
```

### ProfileFactDTO

```csharp
public class ProfileFactDTO
{
    public string fact_key;           // 如 "喜歡的音樂類型"
    public string fact_value;         // 如 "古典樂"
    public string category;           // 如 "preference"
    public float confidence;
    public string timestamp;
    public string source_context;     // 來源上下文
}
```

### GraphDTO

```csharp
public class GraphDTO
{
    public List<GraphNodeDTO> nodes;
    public List<GraphEdgeDTO> edges;
}

public class GraphNodeDTO
{
    public string id;
    public string type;     // "block" | "core" | "profile"
    public string label;
    public float weight;
}

public class GraphEdgeDTO
{
    public string source;
    public string target;
    public float weight;    // 餘弦相似度
}
```

### SessionDTO

```csharp
public class SessionDTO
{
    public string session_id;
    public List<SessionMessageDTO> messages;
    public List<string> last_entities;
    public string created_at;
    public string last_active;
}
```

---

## 11. WebSocket 訊框處理

### WsFrame 基類

所有 WebSocket 訊框都繼承自 `WsFrame`。使用靜態方法 `Deserialize()` 自動解析：

```csharp
// WebSocketClient 內部自動呼叫
WsFrame frame = WsFrame.Deserialize(jsonString);

// 依型別分派
switch (frame)
{
    case SessionInitFrame init:
        Debug.Log($"Session: {init.session_id}");
        break;

    case TokenFrame token:
        Debug.Log($"Token: {token.content}");
        break;

    case ChatDoneFrame done:
        Debug.Log($"Reply: {done.reply}");
        Debug.Log($"Entities: {string.Join(", ", done.extracted_entities)}");
        break;

    case RetrievalContextFrame ctx:
        Debug.Log($"搜尋命中: {ctx.data.block_count} 區塊");
        break;

    case SystemEventFrame evt:
        HandleSystemEvent(evt);
        break;

    case ErrorFrame err:
        Debug.LogError($"[{err.code}] {err.message}");
        break;
}
```

### SystemEventFrame 處理

```csharp
void HandleSystemEvent(SystemEventFrame evt)
{
    switch (evt.action)
    {
        case "topic_shift":
            Debug.Log($"話題偏移！一致性: {evt.cohesion_score}");
            break;

        case "pipeline_complete":
            Debug.Log($"管線完成，新增 {evt.new_blocks} 個記憶區塊");
            break;

        case "profile_updated":
            Debug.Log($"畫像更新，{evt.facts_count} 筆事實");
            // ProfileController 會自動重新拉取
            break;

        case "preferences_aggregated":
            Debug.Log($"偏好聚合，{evt.promoted_count} 筆升級");
            break;

        case "graph_updated":
            Debug.Log($"圖更新：{evt.entity}");
            // MemoryGraphController 會自動重新拉取
            break;
    }
}
```

---

## 12. 自訂擴展指南

### 12.1 新增 REST 端點呼叫

如需呼叫說明書中但 `NetworkManager` 未封裝的端點：

```csharp
// 範例：搜尋記憶
[Serializable]
public class SearchRequest
{
    public string query;
    public string combined_keywords = "";
    public int top_k = 2;
    public float alpha = 0.6f;
    public float threshold = 0.5f;
    public float hard_base = 0.55f;
}

// 在你的腳本中
async UniTaskVoid SearchMemories(string query)
{
    var rest = NetworkManager.Instance.Rest;
    var results = await rest.PostAsync<SearchRequest, List<SearchResultDTO>>(
        "/memory/search",
        new SearchRequest { query = query, top_k = 5 }
    );

    foreach (var r in results)
    {
        Debug.Log($"[{r._debug_score:F3}] {r.overview}");
    }
}
```

### 12.2 新增自訂 Controller

遵循既有 Controller 模式：

```csharp
using UnityEngine;
using Cysharp.Threading.Tasks;

public class MyCustomController : MonoBehaviour
{
    void OnEnable()
    {
        var net = NetworkManager.Instance;
        net.OnSystemEvent += HandleEvent;
        net.OnWsConnected += OnConnected;
    }

    void OnDisable()
    {
        var net = NetworkManager.Instance;
        if (net != null)
        {
            net.OnSystemEvent -= HandleEvent;
            net.OnWsConnected -= OnConnected;
        }
    }

    void HandleEvent(SystemEventFrame evt)
    {
        if (evt.action == "pipeline_complete")
            RefreshData().Forget();
    }

    void OnConnected() => RefreshData().Forget();

    async UniTaskVoid RefreshData()
    {
        // 你的 REST 拉取邏輯
    }
}
```

### 12.3 建立新的 DTO

對應 API 新增的資料結構：

```csharp
using System;
using Newtonsoft.Json;

namespace LLMMemory.Network.DTOs
{
    [Serializable]
    public class MyNewDTO
    {
        [JsonProperty("field_name")]
        public string FieldName;

        [JsonProperty("numeric_value")]
        public float NumericValue;
    }
}
```

---

## 13. 常見問題

### Q: WebSocket 斷線後會自動重連嗎？

**A**: 會。`WebSocketClient` 內建指數退避重連機制，從 `ReconnectBaseDelayMs`（預設 1 秒）開始，逐次倍增直到 `ReconnectMaxDelayMs`（預設 30 秒）。重連成功後自動觸發 `OnWsConnected`，所有 Controller 會自動重新拉取資料。

### Q: 為什麼要用 ConcurrentQueue？

**A**: WebSocket 的接收迴圈跑在背景執行緒，而 Unity API（如 UI 更新、GameObject 操作）只能在主執行緒呼叫。訊框先推入 `ConcurrentQueue`，由 `Update()` 每幀取出並在主執行緒分發事件。

### Q: 如何處理伺服器未啟動的情況？

**A**: `RestClient` 的所有方法都有 try-catch，失敗時回傳 `default(T)`（通常是 `null`）。建議在業務層檢查回傳值：

```csharp
var health = await NetworkManager.Instance.GetHealth();
if (health == null)
{
    ShowError("無法連線到伺服器");
    return;
}
```

### Q: 記憶標籤（tags）是否有固定列表？

**A**: **沒有**。本系統遵循「零硬編碼」原則，所有記憶標籤、實體、偏好分類都是 LLM 動態生成的。C# 端使用 `string` 而非 `enum`，不需要維護標籤清單。

### Q: 如何在正式環境中部署？

**A**: 修改 `NetworkConfig` ScriptableObject 中的 URL：

```
BaseUrl: https://your-server.com/api/v1
WsUrl:   wss://your-server.com/api/v1/chat/stream
```

### Q: 力導向圖的節點位置由誰計算？

**A**: 伺服器只提供節點和邊的資料（含權重），力導向布局演算法需要在 Unity 端實作。你可以使用：
- 自製 Fruchterman-Reingold 演算法
- 第三方套件如 Unity Graph Visualizer
- 基於 `edge.weight` 計算吸引力、基於距離計算排斥力

---

## 附錄：完整初始化流程範例

```csharp
using UnityEngine;
using Cysharp.Threading.Tasks;

/// <summary>
/// 遊戲啟動時的完整初始化流程。
/// 掛載在場景中第一個載入的 GameObject 上。
/// </summary>
public class GameBootstrap : MonoBehaviour
{
    async void Start()
    {
        var net = NetworkManager.Instance;

        // 1. 健康檢查
        var health = await net.GetHealth();
        if (health == null || !health.onnx_loaded)
        {
            Debug.LogError("伺服器未就緒，請確認 FastAPI 已啟動");
            return;
        }
        Debug.Log($"伺服器就緒，運行 {health.uptime_seconds:F0} 秒");

        // 2. 訂閱事件
        net.OnSessionInit += sid => Debug.Log($"[Session] {sid}");
        net.OnTokenReceived += t => { /* UI 即時更新 */ };
        net.OnChatDone += d => Debug.Log($"[完成] {d.reply.Substring(0, Mathf.Min(50, d.reply.Length))}...");
        net.OnSystemEvent += e => Debug.Log($"[事件] {e.action}");

        // 3. 預載資料
        var blocks = await net.FetchMemoryBlocks();
        Debug.Log($"載入 {blocks?.Count ?? 0} 個記憶區塊");

        var profile = await net.FetchProfile();
        Debug.Log($"載入 {profile?.Count ?? 0} 筆使用者畫像");

        // 4. 啟動 WebSocket 對話
        await net.StartChat();
        Debug.Log("WebSocket 對話已就緒");

        // 5. 發送第一句
        await net.SendChatMessage("你好！");
    }
}
```
