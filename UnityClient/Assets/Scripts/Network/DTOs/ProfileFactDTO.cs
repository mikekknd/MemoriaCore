using System;
using Newtonsoft.Json;

namespace LLMMemory.Network.DTOs
{
    [Serializable]
    public class ProfileFactDTO
    {
        [JsonProperty("fact_key")] public string FactKey;
        [JsonProperty("fact_value")] public string FactValue;
        [JsonProperty("category")] public string Category;
        [JsonProperty("confidence")] public float Confidence = 1.0f;
        [JsonProperty("timestamp")] public string Timestamp;
        [JsonProperty("source_context")] public string SourceContext;
    }
}
