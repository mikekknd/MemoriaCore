using System;
using System.Collections.Generic;
using Newtonsoft.Json;

namespace LLMMemory.Network.DTOs
{
    [Serializable]
    public class PreferenceTagDTO
    {
        [JsonProperty("tag")] public string Tag;
        [JsonProperty("intensity")] public float Intensity = 0.5f;
    }

    [Serializable]
    public class DialogueMessageDTO
    {
        [JsonProperty("role")] public string Role;
        [JsonProperty("content")] public string Content;
    }

    [Serializable]
    public class MemoryBlockDTO
    {
        [JsonProperty("block_id")] public string BlockId;
        [JsonProperty("timestamp")] public string Timestamp;
        [JsonProperty("overview")] public string Overview;
        [JsonProperty("is_consolidated")] public bool IsConsolidated;
        [JsonProperty("encounter_count")] public float EncounterCount = 1.0f;
        [JsonProperty("potential_preferences")] public List<PreferenceTagDTO> PotentialPreferences = new();
        [JsonProperty("raw_dialogues")] public List<DialogueMessageDTO> RawDialogues = new();
    }

    [Serializable]
    public class SearchResultDTO : MemoryBlockDTO
    {
        [JsonProperty("_debug_score")] public float DebugScore;
        [JsonProperty("_debug_recency")] public float DebugRecency;
        [JsonProperty("_debug_raw_sim")] public float DebugRawSim;
        [JsonProperty("_debug_sparse_raw")] public float DebugSparseRaw;
        [JsonProperty("_debug_hard_base")] public float DebugHardBase;
        [JsonProperty("_debug_importance")] public float DebugImportance;
    }
}
