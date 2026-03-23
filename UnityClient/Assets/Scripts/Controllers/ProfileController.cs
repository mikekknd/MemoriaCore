using System.Collections.Generic;
using Cysharp.Threading.Tasks;
using LLMMemory.Network;
using LLMMemory.Network.DTOs;
using UnityEngine;

namespace LLMMemory.Controllers
{
    /// <summary>
    /// 使用者畫像控制器 — 從 API 取得並快取使用者畫像事實。
    /// 訂閱 profile_updated 系統事件以自動刷新。
    /// </summary>
    public class ProfileController : MonoBehaviour
    {
        /// <summary>當前快取的使用者畫像</summary>
        public List<ProfileFactDTO> CurrentProfile { get; private set; } = new();

        /// <summary>畫像資料更新時觸發</summary>
        public event System.Action<List<ProfileFactDTO>> OnProfileUpdated;

        private void Start()
        {
            var nm = NetworkManager.Instance;
            if (nm == null) return;

            nm.OnSystemEvent += OnSystemEvent;
            nm.OnWsConnected += () => RefreshProfile().Forget();

            RefreshProfile().Forget();
        }

        private void OnSystemEvent(SystemEventFrame frame)
        {
            if (frame.Action == "profile_updated" || frame.Action == "preferences_aggregated")
            {
                RefreshProfile().Forget();
            }
        }

        public async UniTaskVoid RefreshProfile()
        {
            var nm = NetworkManager.Instance;
            if (nm == null) return;

            try
            {
                CurrentProfile = await nm.FetchProfile();
                Debug.Log($"[Profile] Loaded {CurrentProfile.Count} facts");
                OnProfileUpdated?.Invoke(CurrentProfile);
            }
            catch (System.Exception ex)
            {
                Debug.LogError($"[Profile] Failed to fetch profile: {ex.Message}");
            }
        }

        private void OnDestroy()
        {
            var nm = NetworkManager.Instance;
            if (nm == null) return;
            nm.OnSystemEvent -= OnSystemEvent;
        }
    }
}
