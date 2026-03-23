using System;
using System.Collections.Generic;
using Newtonsoft.Json;

namespace LLMMemory.Network.DTOs
{
    [Serializable]
    public class GraphNodeDTO
    {
        [JsonProperty("id")] public string Id;
        [JsonProperty("type")] public string Type; // "block" | "core" | "profile"
        [JsonProperty("label")] public string Label;
        [JsonProperty("weight")] public float Weight = 1.0f;
    }

    [Serializable]
    public class GraphEdgeDTO
    {
        [JsonProperty("source")] public string Source;
        [JsonProperty("target")] public string Target;
        [JsonProperty("weight")] public float Weight;
    }

    [Serializable]
    public class GraphDTO
    {
        [JsonProperty("nodes")] public List<GraphNodeDTO> Nodes = new();
        [JsonProperty("edges")] public List<GraphEdgeDTO> Edges = new();
    }
}
