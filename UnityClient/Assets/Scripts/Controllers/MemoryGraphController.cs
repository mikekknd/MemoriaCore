using System.Collections.Generic;
using Cysharp.Threading.Tasks;
using LLMMemory.Network;
using LLMMemory.Network.DTOs;
using UnityEngine;

namespace LLMMemory.Controllers
{
    /// <summary>
    /// 記憶圖譜控制器 — 訂閱 graph_updated 系統事件，
    /// 從 API 拉取節點與邊資料，供力導向圖 (Force-Directed Graph) 渲染器使用。
    /// </summary>
    public class MemoryGraphController : MonoBehaviour
    {
        [Header("Settings")]
        [SerializeField] private float similarityThreshold = 0.6f;

        /// <summary>當前快取的圖譜資料，供外部渲染器存取</summary>
        public GraphDTO CurrentGraph { get; private set; }

        /// <summary>圖譜資料更新時觸發</summary>
        public event System.Action<GraphDTO> OnGraphUpdated;

        private void Start()
        {
            var nm = NetworkManager.Instance;
            if (nm == null) return;

            nm.OnSystemEvent += OnSystemEvent;
            nm.OnWsConnected += () => RefreshGraph().Forget();

            // 初始載入
            RefreshGraph().Forget();
        }

        private void OnSystemEvent(SystemEventFrame frame)
        {
            if (frame.Action == "graph_updated" || frame.Action == "pipeline_complete")
            {
                RefreshGraph().Forget();
            }
        }

        public async UniTaskVoid RefreshGraph()
        {
            var nm = NetworkManager.Instance;
            if (nm == null) return;

            try
            {
                CurrentGraph = await nm.FetchGraph(similarityThreshold);
                Debug.Log($"[MemoryGraph] Loaded {CurrentGraph.Nodes.Count} nodes, {CurrentGraph.Edges.Count} edges");
                OnGraphUpdated?.Invoke(CurrentGraph);
            }
            catch (System.Exception ex)
            {
                Debug.LogError($"[MemoryGraph] Failed to fetch graph: {ex.Message}");
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
