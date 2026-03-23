using System;
using System.Collections.Generic;
using Cysharp.Threading.Tasks;
using LLMMemory.Network.DTOs;
using UnityEngine;

namespace LLMMemory.Network
{
    /// <summary>
    /// 網路管理器 MonoBehaviour 單例。
    /// 持有 RestClient + WebSocketClient，透過 C# 事件向 Controller 層派發資料。
    /// 在 Update() 中排空 WebSocket 佇列以確保主執行緒安全。
    /// </summary>
    public class NetworkManager : MonoBehaviour
    {
        public static NetworkManager Instance { get; private set; }

        [SerializeField] private NetworkConfig config;

        // ── 公開事件（Controller 訂閱） ──────────────────
        public event Action<TokenFrame> OnTokenReceived;
        public event Action<ChatDoneFrame> OnChatDone;
        public event Action<RetrievalContextFrame> OnRetrievalContext;
        public event Action<SystemEventFrame> OnSystemEvent;
        public event Action<ErrorFrame> OnError;
        public event Action<SessionInitFrame> OnSessionInit;
        public event Action OnWsConnected;
        public event Action<string> OnWsDisconnected;

        private RestClient _rest;
        private WebSocketClient _ws;

        public string CurrentSessionId { get; private set; }

        private void Awake()
        {
            if (Instance != null && Instance != this)
            {
                Destroy(gameObject);
                return;
            }
            Instance = this;
            DontDestroyOnLoad(gameObject);

            _rest = new RestClient(config.BaseUrl, config.TimeoutMs);
            _ws = new WebSocketClient(config.WsUrl, config.ReconnectBaseDelayMs, config.ReconnectMaxDelayMs);

            _ws.OnConnected += () => OnWsConnected?.Invoke();
            _ws.OnDisconnected += sid => OnWsDisconnected?.Invoke(sid);
        }

        private void Update()
        {
            // 主執行緒排空 WebSocket 入站佇列
            if (_ws == null) return;
            while (_ws.IncomingQueue.TryDequeue(out var frame))
            {
                DispatchFrame(frame);
            }
        }

        private void DispatchFrame(WsFrame frame)
        {
            switch (frame)
            {
                case SessionInitFrame f:
                    CurrentSessionId = f.SessionId;
                    OnSessionInit?.Invoke(f);
                    break;
                case TokenFrame f:
                    OnTokenReceived?.Invoke(f);
                    break;
                case ChatDoneFrame f:
                    OnChatDone?.Invoke(f);
                    break;
                case RetrievalContextFrame f:
                    OnRetrievalContext?.Invoke(f);
                    break;
                case SystemEventFrame f:
                    OnSystemEvent?.Invoke(f);
                    break;
                case ErrorFrame f:
                    OnError?.Invoke(f);
                    break;
            }
        }

        private void OnDestroy()
        {
            _ws?.Dispose();
        }

        // ── REST 高階方法 ────────────────────────────────
        public UniTask<HealthDTO> GetHealth()
            => _rest.GetAsync<HealthDTO>("/health");

        public UniTask<List<MemoryBlockDTO>> FetchMemoryBlocks()
            => _rest.GetAsync<List<MemoryBlockDTO>>("/memory/blocks");

        public UniTask<List<CoreMemoryDTO>> FetchCoreMemories()
            => _rest.GetAsync<List<CoreMemoryDTO>>("/memory/core");

        public UniTask<List<ProfileFactDTO>> FetchProfile()
            => _rest.GetAsync<List<ProfileFactDTO>>("/profile");

        public UniTask<GraphDTO> FetchGraph(float similarityThreshold = 0.6f)
            => _rest.GetAsync<GraphDTO>($"/memory/graph?similarity_threshold={similarityThreshold}");

        public UniTask<SessionDTO> CreateSession()
            => _rest.PostAsync<object, SessionDTO>("/session", new { });

        // ── WebSocket 高階方法 ───────────────────────────
        public async UniTask StartChat(string sessionId = null)
        {
            await _ws.ConnectAsync(sessionId);
        }

        public async UniTask SendChatMessage(string text)
        {
            await _ws.SendChatMessage(text);
        }

        public async UniTask ClearContext()
        {
            await _ws.SendClearContext();
        }
    }
}
