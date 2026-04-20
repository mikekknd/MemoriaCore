"""使用者畫像測試：驗證 fact 提取、Key 收束、墓碑化刪除與語意搜尋"""
import pytest
from tests.test_config import requires_ollama
from tests.ollama_sim import generate_persona_conversation


@requires_ollama
class TestUserProfileExtraction:

    def test_extract_basic_info(self, analyzer, memory_system, router):
        """對話中提及姓名和地點應提取為 basic_info"""
        messages = [
            {"role": "user", "content": "嗨！我叫小明，住在台北的大安區"},
            {"role": "assistant", "content": "你好小明！台北大安區是個很方便的地方呢"},
            {"role": "user", "content": "對啊，我是軟體工程師，每天都搭捷運上班"},
            {"role": "assistant", "content": "軟體工程師在台北確實蠻方便的，科技業很密集"},
        ]
        current_profile = memory_system.storage.load_all_profiles(memory_system.db_path)
        facts = analyzer.extract_user_facts(messages, current_profile, router)

        assert len(facts) >= 1, "應至少提取到一個事實"
        categories = [f["category"] for f in facts]
        assert "basic_info" in categories, "應包含 basic_info 類別"

        # 檢查是否提取到姓名相關事實
        all_values = " ".join([f.get("fact_value", "") for f in facts])
        all_keys = " ".join([f.get("fact_key", "") for f in facts])
        assert "小明" in all_values or "name" in all_keys, "應提取到使用者姓名"

    def test_extract_explicit_preference(self, analyzer, memory_system, router):
        """使用者明確宣告的偏好應被提取為 explicit_preference"""
        messages = [
            {"role": "user", "content": "我超愛吃壽司，壽司是我最愛的食物"},
            {"role": "assistant", "content": "壽司真的很美味！你喜歡什麼口味的？"},
            {"role": "user", "content": "鮭魚壽司是我的最愛，每週至少吃一次"},
            {"role": "assistant", "content": "鮭魚壽司確實是經典口味"},
        ]
        current_profile = memory_system.storage.load_all_profiles(memory_system.db_path)
        facts = analyzer.extract_user_facts(messages, current_profile, router)

        pref_facts = [f for f in facts if f["category"] == "explicit_preference"]
        assert len(pref_facts) >= 1, "應至少提取到一個 explicit_preference"

        all_values = " ".join([f.get("fact_value", "") for f in pref_facts])
        assert "壽司" in all_values or "鮭魚" in all_values, "應包含壽司或鮭魚相關偏好"

    def test_no_extract_from_casual_chat(self, analyzer, memory_system, router):
        """純閒聊不含個人資訊時，不應提取或僅提取極少事實"""
        messages = [
            {"role": "user", "content": "今天天氣真不錯"},
            {"role": "assistant", "content": "是啊，陽光很舒服"},
            {"role": "user", "content": "嗯，適合出去走走"},
            {"role": "assistant", "content": "散步對身心都很好呢"},
        ]
        current_profile = memory_system.storage.load_all_profiles(memory_system.db_path)
        facts = analyzer.extract_user_facts(messages, current_profile, router)
        assert len(facts) <= 1, f"閒聊不應產出大量事實，但得到 {len(facts)} 筆"

    def test_apply_profile_key_convergence(self, memory_system):
        """語意相似的 fact_key 應收束為同一個 key"""
        # 先寫入 favorite_food
        facts_1 = [{"action": "INSERT", "fact_key": "favorite_food", "fact_value": "壽司",
                     "category": "explicit_preference", "justification": "使用者明確表示"}]
        memory_system.apply_profile_facts(facts_1, memory_system.embed_model)

        profiles_before = memory_system.storage.load_all_profiles(memory_system.db_path)
        keys_before = [p['fact_key'] for p in profiles_before]
        assert len(profiles_before) == 1
        assert "favorite_food" in keys_before

        # 再寫入語意高度相似的 key（僅 key 名不同，value 相同）
        # 注意：因主鍵是 (fact_key, fact_value)，value 相同才能真正 UPDATE
        facts_2 = [{"action": "UPDATE", "fact_key": "fav_food", "fact_value": "壽司",
                     "category": "explicit_preference", "justification": "使用者更新偏好"}]
        memory_system.apply_profile_facts(facts_2, memory_system.embed_model)

        profiles_after = memory_system.storage.load_all_profiles(memory_system.db_path)
        keys_after = [p['fact_key'] for p in profiles_after]

        # 驗證 key convergence 發生：fav_food 被收束到 favorite_food
        assert len(profiles_after) == 1, \
            f"Key 收束失敗：預期 1 筆，但有 {len(profiles_after)} 筆 ({keys_after})"
        assert "fav_food" not in keys_after, "fav_food 應被收束到 favorite_food"
        assert "favorite_food" in keys_after, "應保留 favorite_food 這個 key"

    def test_tombstone_delete(self, memory_system):
        """DELETE 操作應設 confidence=-1（墓碑化），而非硬刪"""
        # 先插入
        facts_insert = [{"action": "INSERT", "fact_key": "pet_name", "fact_value": "小白",
                         "category": "relationship", "justification": "使用者提到寵物"}]
        memory_system.apply_profile_facts(facts_insert, memory_system.embed_model)

        profiles = memory_system.storage.load_all_profiles(memory_system.db_path)
        assert len(profiles) == 1

        # 執行 DELETE
        facts_delete = [{"action": "DELETE", "fact_key": "pet_name", "fact_value": "",
                         "category": "relationship", "justification": "使用者說寵物已不在了"}]
        memory_system.apply_profile_facts(facts_delete, memory_system.embed_model)

        # 預設不含墓碑
        profiles_no_tomb = memory_system.storage.load_all_profiles(memory_system.db_path, include_tombstones=False)
        assert len(profiles_no_tomb) == 0, "預設查詢不應包含墓碑記錄"

        # 含墓碑
        profiles_with_tomb = memory_system.storage.load_all_profiles(memory_system.db_path, include_tombstones=True)
        assert len(profiles_with_tomb) == 1, "含墓碑查詢應找到記錄"
        assert profiles_with_tomb[0]["confidence"] < 0, "墓碑記錄 confidence 應 < 0"

    def test_profile_semantic_search(self, memory_system):
        """語意搜尋使用者畫像應回傳相關事實"""
        facts = [
            {"action": "INSERT", "fact_key": "favorite_food", "fact_value": "壽司",
             "category": "explicit_preference", "justification": "使用者宣告"},
            {"action": "INSERT", "fact_key": "hobby", "fact_value": "彈吉他",
             "category": "explicit_preference", "justification": "使用者宣告"},
        ]
        memory_system.apply_profile_facts(facts, memory_system.embed_model)

        results = memory_system.search_profile_by_query("喜歡吃什麼食物", top_k=2)
        assert len(results) >= 1, "語意搜尋應找到食物相關畫像"
        assert "壽司" in results[0]["fact_value"] or "food" in results[0]["fact_key"]

    def test_ollama_generated_persona_extraction(self, analyzer, memory_system, router):
        """使用 Ollama 生成人設對話 → 提取畫像事實"""
        try:
            messages = generate_persona_conversation(
                router,
                persona_desc="使用者叫做阿凱，今年 28 歲，是一名遊戲設計師，住在高雄，最愛吃鹹酥雞",
                topic="聊聊最近的工作與生活",
                turns=4
            )
        except RuntimeError:
            pytest.skip("Ollama 人設對話生成失敗")

        current_profile = memory_system.storage.load_all_profiles(memory_system.db_path)
        facts = analyzer.extract_user_facts(messages, current_profile, router)

        assert len(facts) >= 1, "應至少提取到一個人設事實"
        all_values = " ".join([f.get("fact_value", "") for f in facts])
        all_keys = " ".join([f.get("fact_key", "") for f in facts])
        # 至少應提取到姓名或職業
        has_persona_info = ("阿凱" in all_values or "name" in all_keys
                            or "遊戲" in all_values or "designer" in all_keys
                            or "高雄" in all_values)
        assert has_persona_info, f"應提取到人設中的事實，但僅得到: {facts}"
