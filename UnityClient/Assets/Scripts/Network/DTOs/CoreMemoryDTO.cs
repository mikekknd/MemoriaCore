using System;
using Newtonsoft.Json;

namespace LLMMemory.Network.DTOs
{
    [Serializable]
    public class CoreMemoryDTO
    {
        [JsonProperty("core_id")] public string CoreId;
        [JsonProperty("timestamp")] public string Timestamp;
        [JsonProperty("insight")] public string Insight;
        [JsonProperty("encounter_count")] public float EncounterCount = 1.0f;
    }
}
