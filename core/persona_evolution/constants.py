"""人格演化 Path D — 常數參數。

集中定義 B' 休眠規則與 prompt 層節流閾值；不進 ``user_prefs.json``，
因為這些是演化系統本身的結構參數，不是使用者可調偏好。
"""

# B' 休眠規則：連續 N 版未 update 且最近一次 confidence <= threshold → is_active=0
# 5.0 = medium 的浮點值（對應 CONFIDENCE_MAP["medium"]）
DORMANCY_IDLE_VERSIONS: int = 3
DORMANCY_CONFIDENCE_THRESHOLD: float = 5.0

# 活躍 trait 注入 prompt 時的數量上限；超過以 last_active_version DESC 取前 N 個，
# 其餘不餵 LLM（DB 中仍 active，只是本輪略過）。4KB 附近避免 ctx 爆掉。
MAX_ACTIVE_TRAITS_IN_PROMPT: int = 20

# BGE-M3 cosine 相似度閾值：LLM 填錯 parent_key 時的 fallback 匹配標準
LINEAGE_SIMILARITY_THRESHOLD: float = 0.82
