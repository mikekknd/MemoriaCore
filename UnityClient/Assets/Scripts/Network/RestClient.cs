using System;
using System.Text;
using System.Threading;
using Cysharp.Threading.Tasks;
using Newtonsoft.Json;
using UnityEngine.Networking;

namespace LLMMemory.Network
{
    /// <summary>泛用非同步 REST Client，基於 UnityWebRequest + UniTask。</summary>
    public class RestClient
    {
        private readonly string _baseUrl;
        private readonly int _timeoutSec;

        public RestClient(string baseUrl, int timeoutMs = 30000)
        {
            _baseUrl = baseUrl.TrimEnd('/');
            _timeoutSec = timeoutMs / 1000;
        }

        public async UniTask<T> GetAsync<T>(string path, CancellationToken ct = default)
        {
            var url = $"{_baseUrl}{path}";
            using var req = UnityWebRequest.Get(url);
            req.timeout = _timeoutSec;
            await req.SendWebRequest().WithCancellation(ct);

            if (req.result != UnityWebRequest.Result.Success)
                throw new Exception($"GET {url} failed: {req.error}");

            return JsonConvert.DeserializeObject<T>(req.downloadHandler.text);
        }

        public async UniTask<TRes> PostAsync<TReq, TRes>(string path, TReq body, CancellationToken ct = default)
        {
            var url = $"{_baseUrl}{path}";
            var json = JsonConvert.SerializeObject(body);
            using var req = new UnityWebRequest(url, "POST");
            req.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(json));
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            req.timeout = _timeoutSec;
            await req.SendWebRequest().WithCancellation(ct);

            if (req.result != UnityWebRequest.Result.Success)
                throw new Exception($"POST {url} failed: {req.error}");

            return JsonConvert.DeserializeObject<TRes>(req.downloadHandler.text);
        }

        public async UniTask<TRes> PutAsync<TReq, TRes>(string path, TReq body, CancellationToken ct = default)
        {
            var url = $"{_baseUrl}{path}";
            var json = JsonConvert.SerializeObject(body);
            using var req = new UnityWebRequest(url, "PUT");
            req.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(json));
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            req.timeout = _timeoutSec;
            await req.SendWebRequest().WithCancellation(ct);

            if (req.result != UnityWebRequest.Result.Success)
                throw new Exception($"PUT {url} failed: {req.error}");

            return JsonConvert.DeserializeObject<TRes>(req.downloadHandler.text);
        }

        public async UniTask DeleteAsync(string path, CancellationToken ct = default)
        {
            var url = $"{_baseUrl}{path}";
            using var req = UnityWebRequest.Delete(url);
            req.timeout = _timeoutSec;
            await req.SendWebRequest().WithCancellation(ct);

            if (req.result != UnityWebRequest.Result.Success)
                throw new Exception($"DELETE {url} failed: {req.error}");
        }
    }
}
