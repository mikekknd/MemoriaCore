# 【環境假設】：Python 3.12。純數學偏好聚合引擎，絕不呼叫 LLM generate_chat。
# 取代舊版 run_preference_deduction.py 的 LLM-based 叢集偏好推導。
import math
from datetime import datetime
from core.system_logger import SystemLogger


class PreferenceAggregator:
    """
    純數學偏好聚合引擎。
    從 memory_blocks 中的 potential_preferences 欄位收集暫態偏好標籤，
    透過 Embedding 向量空間聚類 + 時間衰減公式進行降噪與收斂，
    將高分標籤升格為長期使用者畫像。

    核心公式：
    S_agg(T) = Σ (intensity × e^(-λΔt) × encounter_count)
    其中僅計入與目標標籤 cosine similarity >= similarity_threshold 且同極性的標籤。
    """

    def __init__(self, memory_sys):
        self.memory_sys = memory_sys
        self.tag_embedding_cache = {}  # Dict[str, List[float]] — 記憶體內快取

    def _embed_tag(self, tag):
        """帶快取的標籤向量化。僅使用 embed_provider.get_embedding，絕不呼叫 generate_chat。"""
        if tag in self.tag_embedding_cache:
            return self.tag_embedding_cache[tag]
        if not self.memory_sys.embed_provider:
            return []
        vec = self.memory_sys.embed_provider.get_embedding(
            text=tag, model=self.memory_sys.embed_model
        )
        dense = vec.get("dense", [])
        self.tag_embedding_cache[tag] = dense
        return dense

    @staticmethod
    def _same_polarity(tag_a, tag_b):
        """極性檢查：防止「喜歡X」和「討厭X」因 embedding 相似而被錯誤合併。"""
        a_like = tag_a.startswith("喜歡")
        a_dislike = tag_a.startswith("討厭")
        b_like = tag_b.startswith("喜歡")
        b_dislike = tag_b.startswith("討厭")
        if a_like and b_dislike:
            return False
        if a_dislike and b_like:
            return False
        return True

    def aggregate(self, decay_lambda=0.02, similarity_threshold=0.85, score_threshold=3.0):
        """
        核心聚合演算法：
        1. 收集所有含 potential_preferences 的記憶區塊
        2. 提取所有 (tag, intensity, timestamp, encounter_count) 元組
        3. Greedy clustering（cosine >= threshold 且同極性）
        4. 對每個 cluster 計算時間衰減加權積分
        5. 回傳超過 score_threshold 的 cluster

        Parameters:
            decay_lambda: 時間衰減係數，越大衰減越快（建議 0.02）
            similarity_threshold: 標籤聚類的 cosine similarity 門檻（建議 0.85）
            score_threshold: 升格為長期畫像的最低積分（建議 3.0）

        Returns:
            list[dict]: 每個 dict 包含 tag, score, cluster_size, all_tags, representative_vector
        """
        # Step 1: 過濾有偏好的區塊
        blocks_with_prefs = [
            b for b in self.memory_sys.memory_blocks
            if b.get("potential_preferences")
        ]
        if not blocks_with_prefs:
            return []

        # Step 2: 收集所有標籤實例
        tag_instances = []  # (tag_str, tag_vector, intensity, timestamp, encounter_count)
        for block in blocks_with_prefs:
            ts = block.get("timestamp", "")
            enc = float(block.get("encounter_count", 1.0))
            for pref in block.get("potential_preferences", []):
                if isinstance(pref, dict):
                    tag = pref.get("tag", "")
                    intensity = float(pref.get("intensity", 0.5))
                else:
                    tag = str(pref)
                    intensity = 0.5
                if not tag:
                    continue
                vec = self._embed_tag(tag)
                if vec:
                    tag_instances.append((tag, vec, intensity, ts, enc))

        if not tag_instances:
            return []

        # Step 3: Greedy clustering（cosine >= threshold 且同極性）
        clusters = []  # 每個 cluster: list of (tag, vec, intensity, ts, enc)
        used = [False] * len(tag_instances)
        for i in range(len(tag_instances)):
            if used[i]:
                continue
            cluster = [tag_instances[i]]
            used[i] = True
            for j in range(i + 1, len(tag_instances)):
                if used[j]:
                    continue
                sim = self.memory_sys.cosine_similarity(
                    tag_instances[i][1], tag_instances[j][1]
                )
                if sim >= similarity_threshold and self._same_polarity(tag_instances[i][0], tag_instances[j][0]):
                    cluster.append(tag_instances[j])
                    used[j] = True
            clusters.append(cluster)

        # Step 4: 對每個 cluster 計算時間衰減加權積分
        now = datetime.now()
        results = []
        for cluster in clusters:
            total_score = 0.0
            representative_tag = cluster[0][0]
            for (tag, vec, intensity, ts, enc) in cluster:
                try:
                    block_time = datetime.fromisoformat(ts)
                    age_days = (now - block_time).total_seconds() / 86400.0
                except (ValueError, TypeError):
                    age_days = 30.0  # 無法解析時預設 30 天
                decay = math.exp(-decay_lambda * age_days)
                total_score += intensity * decay * enc

            if total_score >= score_threshold:
                results.append({
                    "tag": representative_tag,
                    "score": round(total_score, 2),
                    "cluster_size": len(cluster),
                    "all_tags": list(set(t[0] for t in cluster)),
                    "representative_vector": cluster[0][1]
                })

        return results

    def write_to_profile(self, aggregated_results):
        """
        將高分偏好寫入使用者畫像，寫入前進行語義去重。
        若已存在語義相似（cosine >= 0.85）的 preference 類畫像，則跳過避免重複。
        """
        if not self.memory_sys.db_path:
            return

        existing_profiles = self.memory_sys.storage.load_profile_vectors(
            self.memory_sys.db_path
        )
        # 僅取 preference 類別的既有畫像
        existing_pref_profiles = [
            ep for ep in existing_profiles if ep.get("category") == "preference"
        ]

        written_count = 0
        for result in aggregated_results:
            tag = result["tag"]
            tag_vec = result["representative_vector"]

            # 語義去重：檢查是否已有相似偏好
            is_duplicate = False
            for ep in existing_pref_profiles:
                if not ep.get("fact_vector"):
                    continue
                sim = self.memory_sys.cosine_similarity(tag_vec, ep["fact_vector"])
                if sim >= 0.85:
                    is_duplicate = True
                    break

            if not is_duplicate:
                fact_key = f"pref_agg_{tag.replace(' ', '_')}"
                self.memory_sys.storage.upsert_profile(
                    self.memory_sys.db_path,
                    fact_key=fact_key,
                    fact_value=tag,
                    category="preference",
                    source_context=f"自動聚合 (score={result['score']}, 來自 {result['cluster_size']} 個標籤實例)",
                    confidence=min(1.0, result["score"] / 5.0)
                )
                self.memory_sys.storage.upsert_profile_vector(
                    self.memory_sys.db_path, fact_key, tag_vec
                )
                written_count += 1

        # 重新載入畫像快取
        if written_count > 0:
            self.memory_sys.load_user_profile()
            SystemLogger.log_system_event(
                "偏好聚合寫入",
                f"成功升格 {written_count} 個偏好標籤至使用者畫像。"
            )

        return written_count
