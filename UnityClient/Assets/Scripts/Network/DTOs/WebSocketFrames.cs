using System;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace LLMMemory.Network.DTOs
{
    /// <summary>WebSocket 幀基底類別，用 type 欄位做辨識器</summary>
    [Serializable]
    public class WsFrame
    {
        [JsonProperty("type")] public string Type;

        /// <summary>從原始 JSON 反序列化為型別安全的子類別</summary>
        public static WsFrame Deserialize(string json)
        {
            var jObj = JObject.Parse(json);
            var type = jObj["type"]?.ToString() ?? "";

            return type switch
            {
                "session_init" => jObj.ToObject<SessionInitFrame>(),
                "token" => jObj.ToObject<TokenFrame>(),
                "chat_done" => jObj.ToObject<ChatDoneFrame>(),
                "retrieval_context" => jObj.ToObject<RetrievalContextFrame>(),
                "system_event" => jObj.ToObject<SystemEventFrame>(),
                "error" => jObj.ToObject<ErrorFrame>(),
                "pong" => new WsFrame { Type = "pong" },
                _ => JsonConvert.DeserializeObject<WsFrame>(json),
            };
        }
    }

    [Serializable]
    public class SessionInitFrame : WsFrame
    {
        [JsonProperty("session_id")] public string SessionId;
    }

    [Serializable]
    public class TokenFrame : WsFrame
    {
        [JsonProperty("content")] public string Content;
    }

    [Serializable]
    public class ChatDoneFrame : WsFrame
    {
        [JsonProperty("reply")] public string Reply;
        [JsonProperty("extracted_entities")] public List<string> ExtractedEntities = new();
    }

    [Serializable]
    public class RetrievalContextFrame : WsFrame
    {
        [JsonProperty("data")] public RetrievalContextData Data;
    }

    [Serializable]
    public class RetrievalContextData
    {
        [JsonProperty("original_query")] public string OriginalQuery;
        [JsonProperty("expanded_keywords")] public string ExpandedKeywords;
        [JsonProperty("inherited_tags")] public List<string> InheritedTags = new();
        [JsonProperty("has_memory")] public bool HasMemory;
        [JsonProperty("block_count")] public int BlockCount;
        [JsonProperty("block_details")] public List<JObject> BlockDetails = new();
    }

    [Serializable]
    public class SystemEventFrame : WsFrame
    {
        [JsonProperty("action")] public string Action;
        [JsonProperty("entity")] public string Entity;
        [JsonProperty("cohesion_score")] public float CohesionScore;
        [JsonProperty("new_blocks")] public int NewBlocks;
        [JsonProperty("facts_count")] public int FactsCount;
        [JsonProperty("promoted_count")] public int PromotedCount;
    }

    [Serializable]
    public class ErrorFrame : WsFrame
    {
        [JsonProperty("code")] public string Code;
        [JsonProperty("message")] public string Message;
    }

    [Serializable]
    public class HealthDTO
    {
        [JsonProperty("onnx_loaded")] public bool OnnxLoaded;
        [JsonProperty("db_accessible")] public bool DbAccessible;
        [JsonProperty("uptime_seconds")] public float UptimeSeconds;
    }
}
