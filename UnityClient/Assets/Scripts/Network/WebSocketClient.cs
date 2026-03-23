using System;
using System.Collections.Concurrent;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using Cysharp.Threading.Tasks;
using LLMMemory.Network.DTOs;
using UnityEngine;

namespace LLMMemory.Network
{
    /// <summary>
    /// WebSocket Client — 負責與 FastAPI WS /chat/stream 端點通訊。
    /// 接收到的幀放入 ConcurrentQueue，由 MessageQueue 在主執行緒排空。
    /// 支援指數退避自動重連。
    /// </summary>
    public class WebSocketClient : IDisposable
    {
        public event Action<WsFrame> OnFrameReceived;
        public event Action OnConnected;
        public event Action<string> OnDisconnected;

        private ClientWebSocket _ws;
        private CancellationTokenSource _cts;
        private readonly ConcurrentQueue<WsFrame> _incomingQueue = new();

        private readonly string _baseWsUrl;
        private readonly int _reconnectBaseMs;
        private readonly int _reconnectMaxMs;

        private string _sessionId;
        private bool _autoReconnect = true;
        private int _reconnectAttempt;

        public bool IsConnected => _ws?.State == WebSocketState.Open;
        public ConcurrentQueue<WsFrame> IncomingQueue => _incomingQueue;

        public WebSocketClient(string baseWsUrl, int reconnectBaseMs = 1000, int reconnectMaxMs = 30000)
        {
            _baseWsUrl = baseWsUrl.TrimEnd('/');
            _reconnectBaseMs = reconnectBaseMs;
            _reconnectMaxMs = reconnectMaxMs;
        }

        public async UniTask ConnectAsync(string sessionId = null)
        {
            _sessionId = sessionId;
            _cts?.Cancel();
            _cts = new CancellationTokenSource();

            var url = string.IsNullOrEmpty(sessionId)
                ? _baseWsUrl
                : $"{_baseWsUrl}?session_id={sessionId}";

            _ws = new ClientWebSocket();

            try
            {
                await _ws.ConnectAsync(new Uri(url), _cts.Token);
                _reconnectAttempt = 0;
                OnConnected?.Invoke();
                Debug.Log($"[WS] Connected to {url}");

                // 啟動接收迴圈
                ReceiveLoop(_cts.Token).Forget();
            }
            catch (Exception ex)
            {
                Debug.LogError($"[WS] Connect failed: {ex.Message}");
                if (_autoReconnect)
                    ScheduleReconnect().Forget();
            }
        }

        public async UniTask SendAsync(string json)
        {
            if (_ws?.State != WebSocketState.Open) return;
            var bytes = Encoding.UTF8.GetBytes(json);
            await _ws.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, _cts.Token);
        }

        public async UniTask SendChatMessage(string content)
        {
            var json = Newtonsoft.Json.JsonConvert.SerializeObject(new { type = "chat_message", content });
            await SendAsync(json);
        }

        public async UniTask SendPing()
        {
            await SendAsync("{\"type\":\"ping\"}");
        }

        public async UniTask SendClearContext()
        {
            await SendAsync("{\"type\":\"clear_context\"}");
        }

        private async UniTaskVoid ReceiveLoop(CancellationToken ct)
        {
            var buffer = new byte[8192];

            try
            {
                while (_ws.State == WebSocketState.Open && !ct.IsCancellationRequested)
                {
                    var sb = new StringBuilder();
                    WebSocketReceiveResult result;

                    do
                    {
                        result = await _ws.ReceiveAsync(new ArraySegment<byte>(buffer), ct);
                        sb.Append(Encoding.UTF8.GetString(buffer, 0, result.Count));
                    } while (!result.EndOfMessage);

                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        Debug.Log("[WS] Server closed connection");
                        break;
                    }

                    var json = sb.ToString();
                    try
                    {
                        var frame = WsFrame.Deserialize(json);
                        _incomingQueue.Enqueue(frame);
                        OnFrameReceived?.Invoke(frame);
                    }
                    catch (Exception ex)
                    {
                        Debug.LogWarning($"[WS] Frame parse error: {ex.Message}");
                    }
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception ex)
            {
                Debug.LogError($"[WS] Receive error: {ex.Message}");
            }

            OnDisconnected?.Invoke(_sessionId);

            if (_autoReconnect && !ct.IsCancellationRequested)
                ScheduleReconnect().Forget();
        }

        private async UniTaskVoid ScheduleReconnect()
        {
            _reconnectAttempt++;
            var delay = Math.Min(_reconnectBaseMs * (1 << _reconnectAttempt), _reconnectMaxMs);
            Debug.Log($"[WS] Reconnecting in {delay}ms (attempt {_reconnectAttempt})...");
            await UniTask.Delay(delay);
            await ConnectAsync(_sessionId);
        }

        public void Dispose()
        {
            _autoReconnect = false;
            _cts?.Cancel();
            _ws?.Dispose();
        }
    }
}
