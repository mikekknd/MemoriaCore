using UnityEngine;

namespace LLMMemory.Network
{
    /// <summary>可在 Inspector 中配置的網路設定 ScriptableObject</summary>
    [CreateAssetMenu(fileName = "NetworkConfig", menuName = "LLMMemory/Network Config")]
    public class NetworkConfig : ScriptableObject
    {
        [Header("Server")]
        [Tooltip("FastAPI 後端基礎 URL")]
        public string BaseUrl = "http://localhost:8088/api/v1";

        [Tooltip("WebSocket 連線 URL")]
        public string WsUrl = "ws://localhost:8088/api/v1/chat/stream";

        [Header("Timeout")]
        [Tooltip("REST 請求超時時間 (毫秒)")]
        public int TimeoutMs = 30000;

        [Tooltip("WebSocket 重連基礎延遲 (毫秒)")]
        public int ReconnectBaseDelayMs = 1000;

        [Tooltip("WebSocket 重連最大延遲 (毫秒)")]
        public int ReconnectMaxDelayMs = 30000;
    }
}
