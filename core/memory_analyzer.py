# 【環境假設】：Python 3.12。管線分析器，負責話題偏移偵測與語意分群圖譜修復。
import json
import re
from datetime import datetime
from core.system_logger import SystemLogger
from core.prompt_manager import get_prompt_manager

class MemoryAnalyzer:
    def __init__(self, memory_sys):
        self.memory_sys = memory_sys

    def detect_topic_shift(self, messages, embed_model, threshold=0.55, min_history_len=5, max_history_len=20):
        if len(messages) >= max_history_len:
            SystemLogger.log_shift_trigger(-1.0, threshold, "(強制脈絡深度切斷)")
            return True, -1.0

        if len(messages) < min_history_len: return False, 1.0
        
        # 確保最後一句是使用者發言
        if messages[-1]["role"] != "user":
            return False, 1.0
            
        recent = messages[-1]["content"]
        
        # 【核心修正】：橋接語境 (Bridging Context)
        # 提取倒數第二與第三句 (通常是 User上一句 + Assistant提問) 作為比對基準
        # 這能防止使用者在回答 AI 問題時，因為省略代名詞而導致的語意斷層
        previous_context = " ".join([m["content"] for m in messages[-3:-1]])
        
        if not previous_context.strip(): return False, 1.0
        
        if not self.memory_sys.embed_provider: return False, 1.0
        
        v1 = self.memory_sys.embed_provider.get_embedding(text=recent, model=embed_model)
        v2 = self.memory_sys.embed_provider.get_embedding(text=previous_context, model=embed_model)
        
        score = self.memory_sys.cosine_similarity(v1["dense"], v2["dense"])
        
        # ==========================================
        # 【核心修正】：QA 語境豁免機制 (Question-Answer Exemption)
        # ==========================================
        dynamic_threshold = threshold
        if len(messages) >= 2 and messages[-2]["role"] == "assistant":
            # 如果 AI 上一句是提問，使用者的回答通常缺乏實體名詞（如：不知道、好啊）
            # 此時動態放寬語意相似度的底線要求
            if "？" in messages[-2]["content"] or "?" in messages[-2]["content"]:
                dynamic_threshold = threshold - 0.20 # 預設從 0.55 降至 0.35 左右
                
        is_shift = score < dynamic_threshold
        
        if is_shift:
            SystemLogger.log_shift_trigger(score, dynamic_threshold, recent)
            
        return is_shift, score

    def process_memory_pipeline(self, messages_to_extract, last_block, router, embed_model, task_key="pipeline"):
        if not messages_to_extract:
            return {"new_memories": [], "error": "無新對話可供提取。"}

        dialogue_text = ""
        for m in messages_to_extract:
            dialogue_text += f"{m['role']}: {m['content']}\n"
            
        last_overview = "無"
        if last_block:
            last_overview = f"時間: {last_block['timestamp']}\n概覽: {last_block['overview']}"
            
        # 【核心修正】：導入「話題強制聚合」Prompt，取代容易引發過度碎裂的舊規則
        prompt = get_prompt_manager().get("memory_pipeline").format(
            current_time=datetime.now().strftime('%Y-%m-%d %H:%M'),
            last_overview=last_overview, dialogue_text=dialogue_text
        )

        try:
            api_messages = [{"role": "user", "content": prompt}]
            raw_text = router.generate(task_key, api_messages, temperature=0.1)
            
            _start = raw_text.find('{')
            if _start == -1:
                return {"error": f"找不到 JSON -> {raw_text}"}
            try:
                parsed, _ = json.JSONDecoder().raw_decode(raw_text, _start)
            except Exception as _je:
                return {"error": f"JSON 解析失敗: {_je} -> {raw_text[:200]}"}
            new_mems = parsed.get("new_memories", [])
            
            if not new_mems or not self.memory_sys.embed_provider:
                parsed["new_memories"] = []
                SystemLogger.log_pipeline_result(parsed)
                return parsed

            # ==========================================
            # 階段一：預處理與初始向量化
            # ==========================================
            for mem in new_mems:
                if not mem.get("entities") or len(mem["entities"]) == 0:
                    mem["entities"] = ["日常交流", "綜合主題"]
                    
                entities_str = ", ".join(mem.get("entities", []))
                summary_str = mem.get("summary", "")
                overview_text = f"[核心實體]: {entities_str}\n[情境摘要]: {summary_str}"
                
                vec = self.memory_sys.embed_provider.get_embedding(text=overview_text, model=embed_model)
                mem["_centroid"] = vec.get("dense", [])
                mem["message_indices"] = []

            # ==========================================
            # 階段二：【防護網】Python端強制縫合 (Greedy Merge)
            # ==========================================
            merge_threshold = 0.82 # 相似度大於 0.82 視為模型過度切碎的同一話題
            while len(new_mems) > 1:
                best_sim = -1.0
                best_pair = None
                
                # 尋找全域最相似的兩個區塊
                for i in range(len(new_mems)):
                    for j in range(i + 1, len(new_mems)):
                        sim = self.memory_sys.cosine_similarity(new_mems[i]["_centroid"], new_mems[j]["_centroid"])
                        if sim > best_sim:
                            best_sim = sim
                            best_pair = (i, j)
                            
                if best_sim >= merge_threshold and best_pair:
                    i, j = best_pair
                    # 實體去重聯集 (維持順序)
                    merged_entities = list(dict.fromkeys(new_mems[i]["entities"] + new_mems[j]["entities"]))
                    # 摘要直接拼接
                    merged_summary = new_mems[i]["summary"] + " " + new_mems[j]["summary"]
                    
                    new_mems[i]["entities"] = merged_entities
                    new_mems[i]["summary"] = merged_summary

                    # 偏好標籤去重聯集
                    prefs_i = new_mems[i].get("potential_preferences", [])
                    prefs_j = new_mems[j].get("potential_preferences", [])
                    seen_tags = set()
                    deduped_prefs = []
                    for p in prefs_i + prefs_j:
                        tag_key = p["tag"] if isinstance(p, dict) else str(p)
                        if tag_key not in seen_tags:
                            seen_tags.add(tag_key)
                            deduped_prefs.append(p)
                    new_mems[i]["potential_preferences"] = deduped_prefs

                    # 重新計算合併後的質心向量
                    overview_text = f"[核心實體]: {', '.join(merged_entities)}\n[情境摘要]: {merged_summary}"
                    vec = self.memory_sys.embed_provider.get_embedding(text=overview_text, model=embed_model)
                    new_mems[i]["_centroid"] = vec.get("dense", [])
                    
                    # 移除被合併的碎片 (由後往前刪除確保 index 安全)
                    new_mems.pop(j)
                    SystemLogger.log_system_event("記憶管線-強制縫合", f"攔截到過度碎裂！已合併相似度 {best_sim:.2f} 的子話題區塊。")
                else:
                    break # 無達標的配對，結束縫合

            # ==========================================
            # 階段三：歷史對話吸附 (Message Indexing)
            # ==========================================
            for i, msg in enumerate(messages_to_extract):
                msg_vec = self.memory_sys.embed_provider.get_embedding(text=msg["content"], model=embed_model)
                msg_dense = msg_vec.get("dense", [])
                if not msg_dense: continue
                
                best_idx = 0
                best_sim = -1.0
                for j, mem in enumerate(new_mems):
                    if not mem.get("_centroid"): continue
                    sim = self.memory_sys.cosine_similarity(msg_dense, mem["_centroid"])
                    if sim > best_sim:
                        best_sim = sim
                        best_idx = j
                        
                if new_mems:
                    new_mems[best_idx]["message_indices"].append(i)

            clean_memories = []
            for mem in new_mems:
                if "_centroid" in mem:
                    del mem["_centroid"]
                if mem.get("message_indices"):
                    clean_memories.append(mem)

            parsed["new_memories"] = clean_memories
            parsed["healed_entities"] = parsed.get("healed_entities", None)
            
            SystemLogger.log_pipeline_result(parsed)
            return parsed
            
        except Exception as e:
            return {"error": str(e)}

    def extract_multiple_memories(self, messages_to_extract, router, embed_model, task_key="pipeline"):
        return self.process_memory_pipeline(messages_to_extract, None, router, embed_model, task_key).get("new_memories", [])

    def extract_user_facts(self, messages, current_profile, router, task_key="profile"):
        """從對話中提取使用者的客觀事實資訊 (姓名、偏好、禁忌等)"""
        if not messages:
            return []

        dialogue_text = ""
        for m in messages:  # 訊息已由話題偏移偵測的 get_pipeline_context() 界定範圍，無需再截斷
            dialogue_text += f"{m['role']}: {m['content']}\n"

        profile_json = "無已知事實"
        if current_profile:
            profile_entries = [f"- {p['fact_key']}: {p['fact_value']} ({p['category']})" for p in current_profile]
            profile_json = "\n".join(profile_entries)

        # 【Strict JSON Schema】：從 API 底層封殺模型越界發明 Category
        PROFILE_FACTS_SCHEMA = {
            "type": "object",
            "properties": {
                "facts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["INSERT", "UPDATE", "DELETE"]},
                            "fact_key": {"type": "string"},
                            "fact_value": {"type": "string"},
                            "category": {"type": "string", "enum": ["basic_info", "relationship", "critical_rule", "explicit_preference"]},
                            "justification": {"type": "string"}
                        },
                        "required": ["action", "fact_key", "fact_value", "category", "justification"]
                    }
                }
            },
            "required": ["facts"]
        }

        prompt = get_prompt_manager().get("user_facts_extract").format(
            profile_json=profile_json, dialogue_text=dialogue_text
        )

        try:
            api_messages = [{"role": "user", "content": prompt}]

            # 嘗試帶入 JSON Schema 強制結構化輸出；若後端不支援則降級
            try:
                raw_text = router.generate(task_key, api_messages, temperature=0.1, response_format=PROFILE_FACTS_SCHEMA)
            except Exception:
                raw_text = router.generate(task_key, api_messages, temperature=0.1)

            _start = raw_text.find('{')
            if _start == -1:
                return []

            try:
                parsed, _ = json.JSONDecoder().raw_decode(raw_text, _start)
            except Exception:
                return []
            facts = parsed.get("facts", [])

            # 【強化驗證】：category 白名單 + fact_value 非空檢查
            VALID_CATEGORIES = {"basic_info", "relationship", "critical_rule", "explicit_preference"}
            valid_facts = []
            for f in facts:
                if (f.get("fact_key")
                    and f.get("action") in ("INSERT", "UPDATE", "DELETE")
                    and f.get("category") in VALID_CATEGORIES
                    and (f.get("action") == "DELETE" or f.get("fact_value"))):
                    valid_facts.append(f)

            return valid_facts

        except Exception as e:
            SystemLogger.log_error("使用者事實提取", str(e))
            return []