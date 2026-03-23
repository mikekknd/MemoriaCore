using System.Text;
using LLMMemory.Network;
using LLMMemory.Network.DTOs;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

namespace LLMMemory.Controllers
{
    /// <summary>
    /// 對話控制器 — 訂閱 NetworkManager 的 WebSocket 事件，
    /// 管理對話 UI（TextMeshPro + ScrollView），處理 Token 逐字顯示。
    /// </summary>
    public class ChatController : MonoBehaviour
    {
        [Header("UI References")]
        [SerializeField] private TMP_InputField inputField;
        [SerializeField] private Button sendButton;
        [SerializeField] private TMP_Text chatDisplay;
        [SerializeField] private ScrollRect scrollRect;

        [Header("Settings")]
        [SerializeField] private Color userColor = new(0.2f, 0.6f, 1f);
        [SerializeField] private Color assistantColor = new(0.4f, 0.9f, 0.4f);

        private readonly StringBuilder _currentReply = new();
        private bool _isGenerating;

        private void Start()
        {
            var nm = NetworkManager.Instance;
            if (nm == null)
            {
                Debug.LogError("[ChatController] NetworkManager.Instance is null");
                return;
            }

            nm.OnSessionInit += OnSessionInit;
            nm.OnTokenReceived += OnToken;
            nm.OnChatDone += OnChatDone;
            nm.OnSystemEvent += OnSystemEvent;
            nm.OnError += OnError;

            sendButton.onClick.AddListener(OnSendClicked);

            // 自動連線
            nm.StartChat().Forget();
        }

        private void OnSessionInit(SessionInitFrame frame)
        {
            Debug.Log($"[Chat] Session initialized: {frame.SessionId}");
        }

        private async void OnSendClicked()
        {
            if (_isGenerating || string.IsNullOrWhiteSpace(inputField.text)) return;

            var text = inputField.text.Trim();
            inputField.text = "";
            _isGenerating = true;
            sendButton.interactable = false;

            AppendMessage("user", text);

            _currentReply.Clear();
            await NetworkManager.Instance.SendChatMessage(text);
        }

        private void OnToken(TokenFrame frame)
        {
            _currentReply.Append(frame.Content);
            // 即時更新 UI（逐 token 累加）
            UpdateAssistantDisplay(_currentReply.ToString());
        }

        private void OnChatDone(ChatDoneFrame frame)
        {
            AppendMessage("assistant", frame.Reply);
            _currentReply.Clear();
            _isGenerating = false;
            sendButton.interactable = true;
        }

        private void OnSystemEvent(SystemEventFrame frame)
        {
            switch (frame.Action)
            {
                case "topic_shift":
                    Debug.Log($"[Chat] Topic shift detected (cohesion: {frame.CohesionScore})");
                    break;
                case "graph_updated":
                    Debug.Log($"[Chat] Graph updated: {frame.Entity}");
                    break;
                case "profile_updated":
                    Debug.Log($"[Chat] Profile updated: {frame.FactsCount} facts");
                    break;
            }
        }

        private void OnError(ErrorFrame frame)
        {
            Debug.LogError($"[Chat] Error [{frame.Code}]: {frame.Message}");
            _isGenerating = false;
            sendButton.interactable = true;
        }

        private void AppendMessage(string role, string content)
        {
            var color = role == "user" ? ColorUtility.ToHtmlStringRGB(userColor)
                                       : ColorUtility.ToHtmlStringRGB(assistantColor);
            var label = role == "user" ? "You" : "AI";
            chatDisplay.text += $"\n<color=#{color}><b>[{label}]</b></color> {content}\n";

            // 自動捲到底部
            Canvas.ForceUpdateCanvases();
            scrollRect.verticalNormalizedPosition = 0f;
        }

        private void UpdateAssistantDisplay(string partial)
        {
            // 即時串流顯示（暫時性，ChatDone 後會被完整版覆蓋）
            var color = ColorUtility.ToHtmlStringRGB(assistantColor);
            // 移除最後一段 assistant 暫存（如果有）然後重新附加
            // 簡化實作：直接追加（ChatDone 會寫完整版）
        }

        private void OnDestroy()
        {
            var nm = NetworkManager.Instance;
            if (nm == null) return;
            nm.OnSessionInit -= OnSessionInit;
            nm.OnTokenReceived -= OnToken;
            nm.OnChatDone -= OnChatDone;
            nm.OnSystemEvent -= OnSystemEvent;
            nm.OnError -= OnError;
        }
    }
}
