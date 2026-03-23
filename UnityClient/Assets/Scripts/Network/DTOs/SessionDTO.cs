using System;
using System.Collections.Generic;
using Newtonsoft.Json;

namespace LLMMemory.Network.DTOs
{
    [Serializable]
    public class SessionMessageDTO
    {
        [JsonProperty("role")] public string Role;
        [JsonProperty("content")] public string Content;
    }

    [Serializable]
    public class SessionDTO
    {
        [JsonProperty("session_id")] public string SessionId;
        [JsonProperty("messages")] public List<SessionMessageDTO> Messages = new();
        [JsonProperty("last_entities")] public List<string> LastEntities = new();
        [JsonProperty("created_at")] public string CreatedAt;
        [JsonProperty("last_active")] public string LastActive;
    }
}
