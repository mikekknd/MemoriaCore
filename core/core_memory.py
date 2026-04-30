# 【環境假設】：Python 3.12, numpy 庫可用。ONNX 引擎支援。
import copy
import json
import math
import uuid
import numpy as np
import re
from datetime import datetime
from core.storage_manager import StorageManager
from core.system_logger import SystemLogger
from core.prompt_manager import get_prompt_manager
from core.xml_prompt import xml_attr

class MemorySystem:
    def __init__(self):
        self.storage = StorageManager()
        self.embed_provider = None
        self.embed_model = ""
        self.db_path = ""
        # Keyed caches — (user_id, character_id, visibility) → list[dict]
        self._memory_blocks_cache: dict[tuple[str, str, str], list] = {}
        self._core_memories_cache: dict[tuple[str, str, str], list] = {}
        # user_id → list[dict]
        self._user_profiles_cache: dict[str, list] = {}

    # ── 向後相容屬性（指向 default 使用者 public 快取）─────────────────
    @property
    def memory_blocks(self) -> list:
        return self._get_memory_blocks("default", "default", "public")

    @memory_blocks.setter
    def memory_blocks(self, value: list):
        self._memory_blocks_cache[("default", "default", "public")] = value

    @property
    def core_memories(self) -> list:
        return self._get_core_memories("default", "default", "public")

    @core_memories.setter
    def core_memories(self, value: list):
        self._core_memories_cache[("default", "default", "public")] = value

    @property
    def user_profiles(self) -> list:
        return self._get_user_profiles("default")

    @user_profiles.setter
    def user_profiles(self, value: list):
        self._user_profiles_cache["default"] = value

    # ── 懶載入內部方法 ─────────────────────────────────────────────────

    def _get_memory_blocks(
        self, user_id: str, character_id: str, visibility: str = "public"
    ) -> list:
        key = (user_id, character_id, visibility)
        if key not in self._memory_blocks_cache:
            self._memory_blocks_cache[key] = (
                self.storage.load_db(
                    self.db_path, user_id=user_id, character_id=character_id,
                    visibility_filter=[visibility],
                )
                if self.db_path else []
            )
        return self._memory_blocks_cache[key]

    def _get_core_memories(
        self, user_id: str, character_id: str, visibility: str = "public"
    ) -> list:
        key = (user_id, character_id, visibility)
        if key not in self._core_memories_cache:
            self._core_memories_cache[key] = (
                self.storage.load_core_db(
                    self.db_path, user_id=user_id, character_id=character_id,
                    visibility_filter=[visibility],
                )
                if self.db_path else []
            )
        return self._core_memories_cache[key]

    def _get_user_profiles(self, user_id: str) -> list:
        if user_id not in self._user_profiles_cache:
            self._user_profiles_cache[user_id] = (
                self.storage.load_all_profiles(self.db_path, user_id=user_id)
                if self.db_path else []
            )
        return self._user_profiles_cache[user_id]

    # ════════════════════════════════════════════════════════════
    # SECTION: Embedding 模型切換 / 相似度工具
    # ════════════════════════════════════════════════════════════

    def switch_embedding_model(self, provider, model_name):
        self.embed_provider = provider
        self.embed_model = model_name
        self.db_path = self.storage.get_db_path(model_name)
        # 清空所有快取，懶載入會在首次存取時自動觸發
        self._memory_blocks_cache.clear()
        self._core_memories_cache.clear()
        self._user_profiles_cache.clear()
        # 預先載入 default 使用者 public 資料，嚴格限定 visibility 以防 private 資料洩入 public cache
        self._memory_blocks_cache[("default", "default", "public")] = self.storage.load_db(
            self.db_path, visibility_filter=["public"]
        )
        self._core_memories_cache[("default", "default", "public")] = self.storage.load_core_db(
            self.db_path, visibility_filter=["public"]
        )
        self._user_profiles_cache["default"] = self.storage.load_all_profiles(self.db_path)

    def cosine_similarity(self, v1, v2):
        vec1 = v1.get("dense", []) if isinstance(v1, dict) else v1
        vec2 = v2.get("dense", []) if isinstance(v2, dict) else v2
        if len(vec1) != len(vec2) or len(vec1) == 0: return 0.0
        dot_product = np.dot(vec1, vec2)
        norm_v1 = np.linalg.norm(vec1)
        norm_v2 = np.linalg.norm(vec2)
        if norm_v1 == 0 or norm_v2 == 0: return 0.0
        return dot_product / (norm_v1 * norm_v2)

    def sparse_cosine_similarity(self, dict1, dict2):
        if not dict1 or not dict2: return 0.0
        intersection = set(dict1.keys()) & set(dict2.keys())
        if not intersection: return 0.0
        dot = sum(dict1[k] * dict2[k] for k in intersection)
        norm1_masked = math.sqrt(sum(dict1[k] * dict1[k] for k in intersection))
        norm2 = math.sqrt(sum(v * v for v in dict2.values()))
        if norm1_masked == 0 or norm2 == 0: return 0.0
        return dot / (norm1_masked * norm2)

    # ════════════════════════════════════════════════════════════
    # SECTION: 查詢擴展（LLM Query Expansion）
    # ════════════════════════════════════════════════════════════

    def expand_query(self, user_query, recent_history, router, task_key="expand", force_group: bool = False):
        # 群組對話會帶 [name|character_id]: 前綴；內部標記與接力指令會被清洗。
        from core.chat_orchestrator.dialogue_format import format_dialogue_for_analysis
        history_text = format_dialogue_for_analysis(recent_history[-6:], force_group=force_group)
        prompt = get_prompt_manager().get("query_expand").format(
            history_text=history_text, user_query=user_query
        )
        try:
            api_messages = [{"role": "user", "content": prompt}]
            parsed_data = router.generate_json(task_key, api_messages, temperature=0.0)
            conf = float(parsed_data.get("entity_confidence", 0.0))
            keywords = [k for k in parsed_data.get("expanded_keywords", []) if len(k) < 15 and "標籤" not in k]
            return {"original_query": user_query, "expanded_keywords": " ".join(keywords), "entity_confidence": conf}
        except Exception:
            return {"original_query": user_query, "expanded_keywords": "", "entity_confidence": 0.0}

    # ════════════════════════════════════════════════════════════
    # SECTION: 新增 Memory Block — 含去重 / 對話壓縮 / 核心認知蒸餾
    # ════════════════════════════════════════════════════════════

    def add_memory_block(
        self, overview, raw_dialogues, duplicate_threshold=0.85, router=None,
        sim_timestamp=None, potential_preferences=None,
        user_id="default", character_id="default", visibility="public",
    ):
        if not self.embed_provider: return
        new_features = self.embed_provider.get_embedding(text=overview, model=self.embed_model)
        if not new_features.get("dense"): return
        effective_timestamp = sim_timestamp if sim_timestamp else datetime.now().isoformat()

        # ==========================================
        # 記憶權重初始及防膨脹計算：基於 User 輪數的 S 型曲線
        # ==========================================
        user_turns = sum(1 for m in raw_dialogues if m.get("role") == "user")
        MAX_WEIGHT = 2.0
        MID_POINT = 3.0
        STEEPNESS = 1.2
        BASE_WEIGHT = 0.2

        if user_turns == 0:
            new_weight = 1.0 # 如果沒有 user，預設給 1.0
        else:
            logistic_weight = MAX_WEIGHT / (1 + math.exp(-STEEPNESS * (user_turns - MID_POINT)))
            new_weight = max(BASE_WEIGHT, logistic_weight)

        new_weight = max(0.5, round(new_weight, 1))

        memory_blocks = self._get_memory_blocks(user_id, character_id, visibility)

        for block in memory_blocks:
            if len(new_features["dense"]) == len(block.get("overview_vector", [])):
                if self.cosine_similarity(new_features["dense"], block["overview_vector"]) >= duplicate_threshold:
                    block["timestamp"] = effective_timestamp
                    block["encounter_count"] = round(float(block.get("encounter_count", 0.0)) + new_weight, 1)

                    # 合併潛在偏好標籤（去重）
                    if potential_preferences:
                        existing_prefs = block.get("potential_preferences", [])
                        seen_tags = set()
                        merged_prefs = []
                        for p in existing_prefs + potential_preferences:
                            tag_key = p["tag"] if isinstance(p, dict) else str(p)
                            if tag_key not in seen_tags:
                                seen_tags.add(tag_key)
                                merged_prefs.append(p)
                        block["potential_preferences"] = merged_prefs

                    # 用新的整合版 overview 覆蓋舊的（pipeline 已看過舊概覽+新對話，產出的是整合版）
                    block["overview"] = overview
                    block["overview_vector"] = new_features["dense"]
                    if new_features.get("sparse"):
                        block["sparse_vector"] = new_features["sparse"]

                    # 追加新對話，用 role:content 去重防止完全相同的訊息重複
                    existing_keys = set()
                    for msg in block.get("raw_dialogues", []):
                        existing_keys.add(f"{msg.get('role', '')}:{msg.get('content', '')}")
                    for msg in raw_dialogues:
                        dedup_key = f"{msg.get('role', '')}:{msg.get('content', '')}"
                        if dedup_key not in existing_keys:
                            block["raw_dialogues"].append(msg)
                            existing_keys.add(dedup_key)

                    # ==========================================
                    # 對話壓縮閘道：合併後若對話輪數超過閾值，壓縮舊對話為編年史
                    # 一輪 = 一組 user + assistant，閾值 10 輪
                    # ==========================================
                    COMPRESS_TURN_LIMIT = 10
                    actual_turns = sum(1 for m in block["raw_dialogues"] if m.get("role") == "user")

                    if actual_turns > COMPRESS_TURN_LIMIT and router:
                        block["raw_dialogues"] = self._compress_old_dialogues(
                            block["raw_dialogues"], COMPRESS_TURN_LIMIT, router
                        )

                    self.storage.save_db(self.db_path, memory_blocks,
                                         user_id=user_id, character_id=character_id, visibility=visibility)
                    SystemLogger.log_system_event("情境記憶合併", f"{overview.split(chr(10))[0]} (遭遇: {block['encounter_count']})")

                    # ==========================================
                    # 核心認知提煉：合併後用更新的 overview 嘗試提煉長期 Insight
                    # encounter_count > 1 才有足夠信號（LLM prompt 內也有 ≤1 則 NULL 的保護）
                    # ==========================================
                    if router and block["encounter_count"] > 1.0:
                        context_text = f"時間: {block['timestamp']}\n概覽: {block['overview']}"
                        self._distill_core_memory(context_text, block["encounter_count"], router,
                                                  user_id=user_id, character_id=character_id, visibility=visibility)

                    return block

        block_item = {
            "block_id": str(uuid.uuid4()),
            "timestamp": effective_timestamp,
            "overview": overview,
            "overview_vector": new_features["dense"],
            "sparse_vector": new_features.get("sparse", {}),
            "raw_dialogues": raw_dialogues,
            "is_consolidated": False,
            "encounter_count": new_weight,
            "potential_preferences": potential_preferences or []
        }
        memory_blocks.append(block_item)
        self.storage.save_db(self.db_path, memory_blocks,
                              user_id=user_id, character_id=character_id, visibility=visibility)

        SystemLogger.log_system_event("情境記憶寫入", f"{overview.split(chr(10))[0]} (新建)")
        return block_item

    def _compress_old_dialogues(self, dialogues, keep_recent_turns, router):
        """將超出閾值的舊對話壓縮為編年史摘要，保留最近 keep_recent_turns 輪原文。
        一輪 = 一組 user 訊息（及其對應的 assistant 回覆）。
        """
        # 將對話分為：system 編年史標記 vs 實際對話（user/assistant）
        existing_chronicles = []
        actual_messages = []
        for msg in dialogues:
            if msg.get("role") == "system":
                existing_chronicles.append(msg)
            else:
                actual_messages.append(msg)

        # 計算要保留多少則實際訊息（從尾端往回數 keep_recent_turns 個 user）
        user_indices = [i for i, m in enumerate(actual_messages) if m.get("role") == "user"]
        if len(user_indices) <= keep_recent_turns:
            return dialogues  # 不需壓縮

        # 切分點：保留最後 keep_recent_turns 輪的起始位置
        split_idx = user_indices[-keep_recent_turns]
        old_messages = actual_messages[:split_idx]
        recent_messages = actual_messages[split_idx:]

        if not old_messages:
            return dialogues  # 無舊對話需壓縮

        # 用 LLM 將舊對話壓縮為編年史
        from core.chat_orchestrator.dialogue_format import format_dialogue_for_analysis
        old_dialogue_text = format_dialogue_for_analysis(
            old_messages,
            force_group=any(m.get("role") == "assistant" and m.get("character_id") for m in old_messages),
        )

        compress_prompt = get_prompt_manager().get("dialogue_compress").format(
            old_dialogue_text=old_dialogue_text
        )

        try:
            comp_msg = [{"role": "user", "content": compress_prompt}]
            parsed = router.generate_json("compress", comp_msg, temperature=0.1)
            summary = parsed.get("summary", "")
            if summary:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                chronicle_entry = {"role": "system", "content": f"[編年史紀錄 {timestamp}]:\n{summary}"}
                compressed = existing_chronicles + [chronicle_entry] + recent_messages
                SystemLogger.log_system_event("記憶壓縮閘道",
                    f"已將 {len(old_messages)} 則舊對話壓縮為編年史，保留最近 {keep_recent_turns} 輪原文。")
                return compressed

        except Exception as e:
            SystemLogger.log_error("記憶壓縮閘道失敗", str(e))

        # 壓縮失敗時回傳原始資料，不做任何變更
        return dialogues

    def _distill_core_memory(
        self, context_text, total_weight, router, task_key="distill", fusion_threshold=0.72,
        user_id="default", character_id="default", visibility="public",
    ):
        """從情境記憶概覽提煉核心認知 Insight，並寫入/融合 core_memories。
        context_text: 一或多筆記憶概覽的文字（\n\n 分隔）
        total_weight: 此次提煉的權重積分（通常為 encounter_count）
        """
        if not self.embed_provider or not router:
            return

        block_count = context_text.count("概覽:") or 1

        core_memories = self._get_core_memories(user_id, character_id, visibility)

        existing_cores = []
        for c in core_memories:
            existing_cores.append(f"- ID: {c['core_id']}\n  內容: {c['insight']}")
        existing_cores_text = "\n".join(existing_cores) if existing_cores else "目前尚無核心記憶。"

        distill_prompt = get_prompt_manager().get("core_distill").format(
            block_count=block_count, total_weight=total_weight,
            context_text=context_text, existing_cores_text=existing_cores_text
        )

        try:
            api_messages = [{"role": "user", "content": distill_prompt}]
            raw_generated = router.generate(task_key, api_messages, temperature=0.1).strip()
            clean_generated = re.sub(r'<think>.*?</think>', '', raw_generated, flags=re.DOTALL).strip()

            _start = clean_generated.find('{')
            if _start == -1:
                new_insight = clean_generated
                target_core_id = None
            else:
                parsed_data, _ = json.JSONDecoder().raw_decode(clean_generated, _start)
                new_insight = parsed_data.get("insight", "NULL").strip()
                target_core_id = parsed_data.get("target_core_id")

            if not new_insight:
                new_insight = "NULL"
        except Exception as e:
            SystemLogger.log_error("核心認知提煉失敗", str(e))
            return

        if new_insight.upper() == "NULL" or "NULL" in new_insight.upper():
            SystemLogger.log_system_event("核心認知提煉", "權重積分不足或內容瑣碎，已跳過。")
            return

        new_features = self.embed_provider.get_embedding(text=new_insight, model=self.embed_model)
        if not new_features.get("dense"):
            return

        new_dense = new_features["dense"]
        timestamp = datetime.now().isoformat()

        best_match = None

        if target_core_id:
            for core in core_memories:
                if core["core_id"] == target_core_id:
                    best_match = core
                    break

        if not best_match:
            highest_sim = 0.0
            for core in core_memories:
                sim = self.cosine_similarity(new_dense, core.get("insight_vector", []))
                if sim > highest_sim:
                    highest_sim = sim
            if highest_sim >= fusion_threshold:
                best_match = next((c for c in core_memories if self.cosine_similarity(new_dense, c.get("insight_vector", [])) == highest_sim), None)

        if best_match:
            old_weight = float(best_match.get("encounter_count", 1.0))
            new_core_weight = round(old_weight + total_weight, 1)
            old_time_str = best_match.get('timestamp', '未知時間')[:10]
            new_time_str = timestamp[:10]

            fusion_prompt = get_prompt_manager().get("core_fusion").format(
                old_time_str=old_time_str, old_weight=old_weight,
                old_insight=best_match['insight'],
                new_time_str=new_time_str, total_weight=total_weight,
                new_insight=new_insight
            )
            try:
                fuse_messages = [{"role": "user", "content": fusion_prompt}]
                raw_fused = router.generate(task_key, fuse_messages, temperature=0.1).strip()
                fused_insight = re.sub(r'<think>.*?</think>', '', raw_fused, flags=re.DOTALL).strip()
                if not fused_insight:
                    fused_insight = raw_fused

                fused_features = self.embed_provider.get_embedding(text=fused_insight, model=self.embed_model)
                best_match["insight"] = fused_insight
                if fused_features.get("dense"):
                    best_match["insight_vector"] = fused_features["dense"]
                best_match["timestamp"] = timestamp
                best_match["encounter_count"] = new_core_weight
                self.storage.save_core_memory(
                    self.db_path, best_match["core_id"], timestamp, fused_insight,
                    best_match.get("insight_vector", []), new_core_weight,
                    user_id=user_id, character_id=character_id, visibility=visibility,
                )
                SystemLogger.log_system_event("核心認知提煉", f"時間權重融合成功: {fused_insight} (總積分: {new_core_weight})")
            except Exception as e:
                SystemLogger.log_error("核心認知融合失敗", str(e))
        else:
            new_core_weight = total_weight
            core_id = str(uuid.uuid4())
            core_item = {"core_id": core_id, "timestamp": timestamp, "insight": new_insight,
                         "insight_vector": new_dense, "encounter_count": new_core_weight}
            core_memories.append(core_item)
            self.storage.save_core_memory(
                self.db_path, core_id, timestamp, new_insight, new_dense, new_core_weight,
                user_id=user_id, character_id=character_id, visibility=visibility,
            )
            SystemLogger.log_system_event("核心認知提煉", f"新增成功: {new_insight} (初始積分: {new_core_weight})")

    # ════════════════════════════════════════════════════════════
    # SECTION: Memory Block 更新 / 叢集偵測 / 融合壓縮
    # ════════════════════════════════════════════════════════════

    def update_memory_block(
        self, block_id, new_overview,
        user_id="default", character_id="default", visibility="public",
    ):
        if not self.embed_provider: return False
        memory_blocks = self._get_memory_blocks(user_id, character_id, visibility)
        for block in memory_blocks:
            if block["block_id"] == block_id:
                new_features = self.embed_provider.get_embedding(text=new_overview, model=self.embed_model)
                if new_features.get("dense"):
                    block["overview"] = new_overview
                    block["overview_vector"] = new_features["dense"]
                    block["sparse_vector"] = new_features.get("sparse", {})
                    self.storage.save_db(self.db_path, memory_blocks,
                                         user_id=user_id, character_id=character_id, visibility=visibility)
                    return True
        return False

    def find_pending_clusters(
        self, cluster_threshold=0.75, min_group_size=2,
        user_id="default", character_id="default", visibility="public",
    ):
        memory_blocks = self._get_memory_blocks(user_id, character_id, visibility)
        clusters = []
        visited = set()
        for i, b1 in enumerate(memory_blocks):
            if i in visited: continue
            current_cluster = [b1]
            visited.add(i)
            for j, b2 in enumerate(memory_blocks[i+1:], start=i+1):
                if j in visited: continue
                if self.cosine_similarity(b1["overview_vector"], b2["overview_vector"]) >= cluster_threshold:
                    current_cluster.append(b2)
                    visited.add(j)
            if len(current_cluster) >= min_group_size:
                clusters.append(current_cluster)
        return clusters

    def consolidate_and_fuse(
        self, related_blocks, router, task_key="compress", fusion_threshold=0.72,
        user_id="default", character_id="default", visibility="public",
    ):
        if not related_blocks or not self.embed_provider: return "無需處理"

        sorted_blocks = sorted(related_blocks, key=lambda x: x.get("timestamp", ""))

        # ==========================================
        # 記憶累積權重計算 (S 型曲線已移至 add_memory_block 計算保障)
        # ==========================================
        total_weight = 0.0
        last_valid_time = None
        session_gap_seconds = 1200 # 20分鐘

        for b in sorted_blocks:
            base_count = float(b.get("encounter_count", 1.0))

            try:
                b_time = datetime.fromisoformat(b["timestamp"])
                if last_valid_time is None or (b_time - last_valid_time).total_seconds() > session_gap_seconds:
                    contribution = base_count
                    last_valid_time = b_time
                else:
                    # 時間過近，同一次 Session 產生的多個 block 在寫入時已根據各自 user_turns 計算權重
                    # 這裡我們信任已計算的權重，不做惡意扣減。S曲線天生防切碎膨脹。
                    contribution = base_count
            except ValueError:
                contribution = base_count

            total_weight += contribution

        # 最終權重四捨五入至小數點第一位，保底至少 0.5 確保不會歸零
        total_weight = max(0.5, round(total_weight, 1))

        SystemLogger.log_system_event("大腦反芻啟動", f"開始融合 {len(sorted_blocks)} 筆區塊，校準後權重積分: {total_weight}...")

        timestamp = datetime.now().isoformat()

        # ==========================================
        # 階段一：核心認知提煉（委託 _distill_core_memory 共用方法）
        # ==========================================
        context_text = "\n\n".join([f"時間: {b['timestamp']}\n概覽: {b['overview']}" for b in sorted_blocks])
        self._distill_core_memory(context_text, total_weight, router, task_key="distill",
                                   fusion_threshold=fusion_threshold,
                                   user_id=user_id, character_id=character_id, visibility=visibility)

        # ==========================================
        # 階段二：情境記憶縫合 (Episodic Fusion) - 近因保留與歷史編年史化
        # ==========================================
        latest_block = sorted_blocks[-1]
        older_blocks = sorted_blocks[:-1]
        combined_dialogues = []

        if older_blocks:
            old_dialogues_pool = []
            seen_dialogues = set()
            for b in older_blocks:
                b_time_str = b.get("timestamp", "未知時間")
                try:
                    dt = datetime.fromisoformat(b_time_str)
                    time_str = dt.strftime("%Y-%m-%d %H:%M")
                except:
                    time_str = b_time_str

                block_dialogues = []
                for msg in b.get("raw_dialogues", []):
                    dedup_key = f"{msg.get('role', '')}:{msg.get('content', '')}"
                    if dedup_key not in seen_dialogues:
                        seen_dialogues.add(dedup_key)
                        block_dialogues.append(msg)

                if block_dialogues:
                    old_dialogues_pool.append({"role": "system", "content": f"[系統標記：以下對話發生於 {time_str}]"})
                    old_dialogues_pool.extend(block_dialogues)

            if old_dialogues_pool:
                SystemLogger.log_system_event("記憶壓縮閘道", f"啟動歷史記憶編年史化 (共 {len(older_blocks)} 筆舊區塊)。")
                from core.chat_orchestrator.dialogue_format import format_dialogue_for_analysis
                old_dialogue_text = format_dialogue_for_analysis(
                    old_dialogues_pool,
                    force_group=any(
                        m.get("role") == "assistant" and m.get("character_id")
                        for m in old_dialogues_pool
                    ),
                )

                compress_prompt = get_prompt_manager().get("history_compress").format(
                    old_dialogue_text=old_dialogue_text
                )

                try:
                    comp_msg = [{"role": "user", "content": compress_prompt}]
                    raw_compressed = router.generate(task_key, comp_msg, temperature=0.1)

                    _start = raw_compressed.find('[')
                    if _start == -1:
                        SystemLogger.log_error("編年史化失敗", f"LLM 回傳無 JSON 陣列: {raw_compressed[:200]}")
                        combined_dialogues.extend(old_dialogues_pool)
                    else:
                        try:
                            chronicles, _ = json.JSONDecoder().raw_decode(raw_compressed, _start)
                        except Exception as _je:
                            SystemLogger.log_error("編年史化失敗", f"JSON 解析錯誤: {_je} | 原文: {raw_compressed[:200]}")
                            combined_dialogues.extend(old_dialogues_pool)
                            chronicles = []

                        if not chronicles:
                            SystemLogger.log_error("編年史化失敗", "chronicles 陣列為空，回退使用原始對話")
                            combined_dialogues.extend(old_dialogues_pool)
                        else:
                            merged_chronicles = {}
                            for c in chronicles:
                                ts = c.get("timestamp", "未知時間")
                                summ = c.get("summary", "")
                                if ts in merged_chronicles:
                                    merged_chronicles[ts] += " " + summ
                                else:
                                    merged_chronicles[ts] = summ

                            for ts, summ in merged_chronicles.items():
                                combined_dialogues.append({"role": "system", "content": f"[編年史紀錄 {ts}]:\n{summ}"})
                except Exception as e:
                    SystemLogger.log_error("歷史記憶編年史化失敗", str(e))
                    combined_dialogues.extend(old_dialogues_pool)

        if latest_block:
            latest_time_str = latest_block.get("timestamp", "未知時間")
            try:
                dt = datetime.fromisoformat(latest_time_str)
                time_str = dt.strftime("%Y-%m-%d %H:%M")
            except:
                time_str = latest_time_str

            combined_dialogues.append({"role": "system", "content": f"[系統標記：近期原始對話發生於 {time_str}]"})
            seen_latest = set()
            for msg in latest_block.get("raw_dialogues", []):
                dedup_key = f"{msg.get('role', '')}:{msg.get('content', '')}"
                if dedup_key not in seen_latest:
                    seen_latest.add(dedup_key)
                    combined_dialogues.append(msg)

        from core.chat_orchestrator.dialogue_format import format_dialogue_for_analysis
        dialogue_text = format_dialogue_for_analysis(
            combined_dialogues,
            force_group=any(
                m.get("role") == "assistant" and m.get("character_id")
                for m in combined_dialogues
            ),
        )

        episodic_prompt = get_prompt_manager().get("episodic_overview").format(
            dialogue_text=dialogue_text
        )

        try:
            ep_messages = [{"role": "user", "content": episodic_prompt}]
            raw_overview = router.generate("ep_fuse", ep_messages, temperature=0.1).strip()
            _start = raw_overview.find('{')
            if _start != -1:
                parsed_data, _ = json.JSONDecoder().raw_decode(raw_overview, _start)
                entities_str = ", ".join(parsed_data.get("entities", []))
                summary_str = parsed_data.get("summary", "")
                merged_overview = f"[核心實體]: {entities_str}\n[情境摘要]: {summary_str}"
            else:
                merged_overview = f"[核心實體]: 記憶縫合, 綜合事件\n[情境摘要]: 系統自動合併的多段相關記憶。"
        except Exception:
            merged_overview = f"[核心實體]: 記憶縫合, 綜合事件\n[情境摘要]: 系統自動合併的多段相關記憶。"

        merged_features = self.embed_provider.get_embedding(text=merged_overview, model=self.embed_model)

        if merged_features.get("dense"):
            # 從快取移除已融合的舊區塊
            block_ids = {b["block_id"] for b in sorted_blocks}
            cache_key = (user_id, character_id, visibility)
            self._memory_blocks_cache[cache_key] = [
                b for b in self._get_memory_blocks(user_id, character_id, visibility)
                if b["block_id"] not in block_ids
            ]
            memory_blocks = self._memory_blocks_cache[cache_key]

            # 從所有來源區塊收集潛在偏好（去重）
            merged_prefs = []
            seen_pref_tags = set()
            for b in sorted_blocks:
                for p in b.get("potential_preferences", []):
                    tag_key = p["tag"] if isinstance(p, dict) else str(p)
                    if tag_key not in seen_pref_tags:
                        seen_pref_tags.add(tag_key)
                        merged_prefs.append(p)

            merged_block = {
                "block_id": str(uuid.uuid4()),
                "timestamp": timestamp,
                "overview": merged_overview,
                "overview_vector": merged_features["dense"],
                "sparse_vector": merged_features.get("sparse", {}),
                "raw_dialogues": combined_dialogues,
                "is_consolidated": False,
                "encounter_count": total_weight,
                "potential_preferences": merged_prefs
            }
            memory_blocks.append(merged_block)
            self.storage.save_db(self.db_path, memory_blocks,
                                  user_id=user_id, character_id=character_id, visibility=visibility)

            SystemLogger.log_system_event("大腦反芻 - 情境階段", f"超級區塊縫合完成並寫入:\n{merged_overview.split(chr(10))[0]} (總積分: {total_weight})")
            return f"完成：{len(sorted_blocks)} 筆區塊縫合，總積分 {total_weight}。"

        return "情境縫合失敗（向量化錯誤）"

    # ════════════════════════════════════════════════════════════
    # SECTION: 三軌檢索 — 核心認知 / 情境 Block / 使用者偏好
    # ════════════════════════════════════════════════════════════

    def search_core_memories(
        self, query, top_k=1, threshold=0.45,
        user_id="default", character_id="default", visibility_filter=None,
    ):
        if not self.embed_provider: return []

        if visibility_filter is None:
            core_memories = self._get_core_memories(user_id, character_id, "public")
        else:
            core_memories = []
            for vis in visibility_filter:
                core_memories.extend(self._get_core_memories(user_id, character_id, vis))

        if not core_memories: return []

        q_feat = self.embed_provider.get_embedding(text=query, model=self.embed_model)
        q_dense = q_feat.get("dense", [])
        if not q_dense: return []

        results = []
        for core in core_memories:
            sim = self.cosine_similarity(q_dense, core.get("insight_vector", []))
            if sim >= threshold:
                results.append({
                    "insight": core["insight"],
                    "score": sim
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def search_blocks(
        self, original_query, combined_keywords, top_k=2, alpha=0.5, lambda_mult=0.9,
        threshold=0.5, hard_base=0.55,
        user_id="default", character_id="default", visibility_filter=None,
    ):
        if not self.embed_provider: return []

        if visibility_filter is None:
            memory_blocks = self._get_memory_blocks(user_id, character_id, "public")
        else:
            memory_blocks = []
            for vis in visibility_filter:
                memory_blocks.extend(self._get_memory_blocks(user_id, character_id, vis))

        if not memory_blocks: return []
        now = datetime.now()
        full_query = f"{original_query} {combined_keywords}".strip()
        q_full_feat = self.embed_provider.get_embedding(text=full_query, model=self.embed_model)
        q_orig_feat = self.embed_provider.get_embedding(text=original_query, model=self.embed_model)
        q_exp_feat = self.embed_provider.get_embedding(text=combined_keywords, model=self.embed_model) if combined_keywords else {"dense": [], "sparse": {}}
        if not q_full_feat.get("dense"): return []

        dense_raw = []
        sparse_raw = []
        q_orig_sparse = q_orig_feat.get("sparse", {})
        q_exp_sparse = q_exp_feat.get("sparse", {})

        for b in memory_blocks:
            d_score = self.cosine_similarity(q_full_feat["dense"], b.get("overview_vector", []))
            dense_raw.append(d_score)
            s_orig = self.sparse_cosine_similarity(q_orig_sparse, b.get("sparse_vector", {}))
            s_exp = self.sparse_cosine_similarity(q_exp_sparse, b.get("sparse_vector", {}))
            sparse_raw.append((s_orig * 0.70) + (s_exp * 0.30))

        dense_norm = [max(0.0, s) for s in dense_raw]
        sparse_norm = [min(1.0, max(0.0, s) * 2.5) for s in sparse_raw]

        final_candidates = []
        for i, block in enumerate(memory_blocks):
            try:
                block_time = datetime.fromisoformat(block["timestamp"])
                delta_days = (now - block_time).total_seconds() / 86400.0
            except Exception: delta_days = 30.0

            # ==========================================
            # 綜合計分與斬殺線判定 (修正字面暴衝漏洞)
            # ==========================================
            recency_boost_base = 0.15 * math.exp(-0.35 * max(0.0, delta_days))
            hybrid_score = alpha * dense_norm[i] + (1 - alpha) * sparse_norm[i]
            is_killed = False

            # 設定絕對語意底線 (比 hard_base 低 0.1)
            absolute_bottom = hard_base - 0.10

            if dense_raw[i] < hard_base:
                # 【嚴格豁免條件】：
                # 1. 稀疏分數必須極高 (>= 0.35，代表有多個關鍵字完全命中)
                # 2. 語意分數不能低於絕對底線 (避免純字面巧合，如「遊戲」)
                if sparse_raw[i] >= 0.35 and dense_raw[i] >= absolute_bottom:
                    pass # 允許字面高度重合的區塊殘留
                else:
                    hybrid_score = 0.0
                    is_killed = True
                    recency_boost_base = 0.0

            actual_boost = 0.0
            importance_boost = 0.0
            if not is_killed:
                encounter_weight = float(block.get("encounter_count", 1.0))
                importance_boost = 0.05 * math.log(encounter_weight) if encounter_weight > 1.0 else 0.0

                actual_boost = recency_boost_base * (hybrid_score ** 2)
                hybrid_score += actual_boost
                hybrid_score += importance_boost

            if hybrid_score >= threshold and not is_killed:
                b = copy.deepcopy(block)
                b.update({
                    "_debug_score": hybrid_score, "_debug_recency": actual_boost,
                    "_debug_raw_sim": dense_raw[i], "_debug_sparse_raw": sparse_raw[i],
                    "_debug_hard_base": hard_base, "_debug_sparse_norm": sparse_norm[i],
                    "_debug_importance": importance_boost
                })
                final_candidates.append(b)

        if not final_candidates: return []
        final_candidates.sort(key=lambda x: x["_debug_score"], reverse=True)
        selected = [final_candidates.pop(0)]

        while len(selected) < top_k and final_candidates:
            mmr_scores = []
            for item in final_candidates:
                max_sim = max([self.cosine_similarity(item["overview_vector"], sel["overview_vector"]) for sel in selected] + [0.0])
                mmr_score = lambda_mult * item["_debug_score"] - (1 - lambda_mult) * max_sim
                mmr_scores.append((mmr_score, item))
            mmr_scores.sort(key=lambda x: x[0], reverse=True)
            best_item = mmr_scores[0][1]
            selected.append(best_item)
            final_candidates.remove(best_item)

        return selected

    # ════════════════════════════════════════════════════════════
    # SECTION: 使用者偏好 Profile — 載入 / 套用 / 查詢 / Prompt 組裝
    # ════════════════════════════════════════════════════════════

    def load_user_profile(self, user_id="default"):
        """從 DB 載入使用者事實到記憶體快取"""
        if not self.db_path:
            self._user_profiles_cache[user_id] = []
            return
        self._user_profiles_cache[user_id] = self.storage.load_all_profiles(self.db_path, user_id=user_id)

    def apply_profile_facts(self, facts, embed_model, user_id="default", visibility="public"):
        """接收 extractor 回傳的 facts 列表，執行向量收束 + 墓碑化刪除 + upsert 並更新快取

        核心機制：
        1. 向量收束 (Key Convergence)：新 key 若與既有 key 的語意相似度 >= 0.88，
           強制塌縮到既有 key，防止 fact_key 發散 (fav_food / favorite_food / food_pref)。
        2. 墓碑模式 (Tombstone)：DELETE 不硬刪，改為 confidence=-1.0，
           保留「使用者明確否定」的記錄供未來查詢。
        """
        if not facts or not self.db_path:
            return

        # 一次性載入所有現有 profile 向量，避免迴圈內反覆查 DB
        existing_profiles = self.storage.load_profile_vectors(self.db_path, user_id=user_id) if self.embed_provider else []
        DEDUP_THRESHOLD = 0.88

        for fact in facts:
            action = fact.get("action", "").upper()
            fact_key = fact.get("fact_key", "")
            fact_value = fact.get("fact_value", "")
            category = fact.get("category", "explicit_preference")
            justification = fact.get("justification", "")

            if not fact_key:
                continue

            # === 向量收束：新 key 若與舊 key 語意重複，強制塌縮到舊 key ===
            resolved_key = fact_key
            if self.embed_provider and existing_profiles:
                new_vec_text = f"{fact_key}: {fact_value} ({category})"
                new_vec = self.embed_provider.get_embedding(text=new_vec_text, model=embed_model)
                new_dense = new_vec.get("dense", [])

                if new_dense:
                    best_sim, best_match = 0.0, None
                    for ep in existing_profiles:
                        if not ep.get("fact_vector"):
                            continue
                        sim = self.cosine_similarity(new_dense, ep["fact_vector"])
                        if sim > best_sim:
                            best_sim, best_match = sim, ep

                    if best_match and best_sim >= DEDUP_THRESHOLD and best_match["fact_key"] != fact_key:
                        old_key = best_match["fact_key"]
                        resolved_key = old_key
                        SystemLogger.log_system_event("畫像-Key收束",
                            f"'{fact_key}' → '{old_key}' (sim={best_sim:.3f})")

            if action == "DELETE":
                # 墓碑模式：查出該 key 下所有值，逐筆標記 confidence=-1.0
                existing_rows = self.storage.get_profile_by_key(self.db_path, resolved_key, user_id=user_id)
                if existing_rows:
                    for existing in existing_rows:
                        self.storage.upsert_profile(
                            self.db_path, resolved_key,
                            existing["fact_value"], existing["category"],
                            justification, confidence=-1.0,
                            user_id=user_id, visibility=visibility)
                    # 從 existing_profiles 快取中移除（墓碑不參與後續收束比對）
                    existing_profiles = [ep for ep in existing_profiles if ep["fact_key"] != resolved_key]
                    SystemLogger.log_system_event("使用者畫像-墓碑",
                        f"{resolved_key} x{len(existing_rows)} (原因: {justification})")
                else:
                    SystemLogger.log_system_event("使用者畫像-刪除跳過",
                        f"找不到 key: {resolved_key}")

            elif action in ("INSERT", "UPDATE"):
                self.storage.upsert_profile(self.db_path, resolved_key, fact_value, category, justification,
                                             user_id=user_id, visibility=visibility)

                # 向量化事實描述，供語意搜尋使用
                if self.embed_provider:
                    vec_text = f"{resolved_key}: {fact_value} ({category})"
                    vec = self.embed_provider.get_embedding(text=vec_text, model=embed_model)
                    if vec.get("dense"):
                        self.storage.upsert_profile_vector(self.db_path, resolved_key, fact_value, vec["dense"],
                                                            user_id=user_id)
                        # 更新記憶體快取，確保同批次後續 fact 能正確收束
                        # 只移除完全相同的 (fact_key, fact_value) 組合，保留同 key 不同 value 的記錄
                        existing_profiles = [ep for ep in existing_profiles
                                             if not (ep["fact_key"] == resolved_key and ep["fact_value"] == fact_value)]
                        existing_profiles.append({
                            "fact_key": resolved_key, "fact_value": fact_value,
                            "category": category, "confidence": 1.0, "fact_vector": vec["dense"]
                        })

                SystemLogger.log_system_event("使用者畫像-寫入",
                    f"[{action}] {resolved_key} = {fact_value} ({category})")

        # 重新載入快取
        self.load_user_profile(user_id=user_id)

    def search_profile_by_query(
        self, query, top_k=3, threshold=0.5, user_id="default",
        visibility_filter: "list[str] | None" = None,
    ):
        """語意搜尋使用者畫像中的偏好類事實。

        visibility_filter: None → 不限；['public'] → 只含公開事實（公開頻道聊天時使用）。
        """
        if not self.embed_provider or not self.db_path:
            return []

        profile_vecs = self.storage.load_profile_vectors(
            self.db_path, user_id=user_id, visibility_filter=visibility_filter
        )
        if not profile_vecs:
            return []

        q_feat = self.embed_provider.get_embedding(text=query, model=self.embed_model)
        q_dense = q_feat.get("dense", [])
        if not q_dense:
            return []

        results = []
        for pv in profile_vecs:
            if not pv.get("fact_vector"):
                continue
            # 只語意搜尋非靜態注入的分類（靜態注入的已在 System Prompt 中）
            if pv.get("category") in ("basic_info", "critical_rule"):
                continue
            sim = self.cosine_similarity(q_dense, pv["fact_vector"])
            if sim >= threshold:
                results.append({
                    "fact_key": pv["fact_key"],
                    "fact_value": pv["fact_value"],
                    "category": pv["category"],
                    "score": sim
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def get_static_profile_prompt(self, user_id="default", visibility_filter=None):
        """回傳 basic_info 和 critical_rule 分類的格式化文字（供靜態注入 System Prompt）"""
        if not self.db_path:
            return ""

        basic_facts = self.storage.load_profiles_by_category(
            self.db_path, "basic_info", user_id=user_id, visibility_filter=visibility_filter)
        critical_facts = self.storage.load_profiles_by_category(
            self.db_path, "critical_rule", user_id=user_id, visibility_filter=visibility_filter)

        if not basic_facts and not critical_facts:
            return ""

        lines = ["<user_static_profile>"]
        if basic_facts:
            lines.append("<basic_info>")
            for f in basic_facts:
                lines.append(f'<fact key="{xml_attr(f["fact_key"])}">{f["fact_value"]}</fact>')
            lines.append("</basic_info>")

        if critical_facts:
            lines.append("<critical_rules>")
            for f in critical_facts:
                lines.append(f'<rule key="{xml_attr(f["fact_key"])}">{f["fact_value"]}</rule>')
            lines.append("</critical_rules>")

        lines.append("</user_static_profile>")
        return "\n".join(lines)

    # ════════════════════════════════════════════════════════════
    # SECTION: 主動話題 Prompt 注入
    # ════════════════════════════════════════════════════════════

    def get_proactive_topics_prompt(
        self, limit=1, user_id="default", character_id="default", visibility_filter=None
    ):
        """從 topic_cache 撈取未提過的話題轉為 Prompt，並標記為已使用"""
        if not self.db_path:
            return ""

        topics = self.storage.get_unmentioned_topics(
            self.db_path, limit=limit,
            user_id=user_id, character_id=character_id,
            visibility_filter=visibility_filter,
            include_global=True,
        )
        if not topics:
            return ""

        lines = [
            "<proactive_topics>",
            "<instruction>以下是系統背景蒐集到、使用者可能感興趣的資訊。請視上下文自然融合，不要說「我查到了」或「根據背景資訊」。</instruction>",
        ]
        for t in topics:
            lines.append(f'<topic keyword="{xml_attr(t["interest_keyword"])}">{t["summary_content"]}</topic>')
            self.storage.mark_topic_mentioned(self.db_path, t['topic_id'])

        lines.append("</proactive_topics>")
        return "\n".join(lines)
