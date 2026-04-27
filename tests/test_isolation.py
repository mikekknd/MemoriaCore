"""記憶隔離測試套件 — Phase 7。

所有測試完全離線（MockEmbedProvider），不需要 Ollama。

驗證屬性：
1. user_id 隔離 — 記憶區塊不跨用戶洩漏、不跨用戶合併
2. visibility 非對稱讀取 — private face 讀兩層，public face 只讀 public
3. 跨用戶 cluster 合併防護
4. 雙 persona_face 獨立演化
5. SU private fact 不出現在 public face prompt
6. 向後相容（不帶 user_id/visibility 的呼叫落到 'default'/'public'）
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.core_memory import MemorySystem
from core.storage_manager import StorageManager
from core.persona_evolution.snapshot_store import PersonaSnapshotStore
from core.persona_evolution.trait_diff import NewTrait, TraitDiff
from tests.mock_llm import MockEmbedProvider


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

@pytest.fixture
def storage(tmp_path):
    return StorageManager(
        prefs_file=str(tmp_path / "prefs.json"),
        history_file=str(tmp_path / "history.json"),
        persona_snapshot_db_path=str(tmp_path / "persona.db"),
    )


@pytest.fixture
def ms(tmp_path, storage):
    """全離線 MemorySystem：MockEmbedProvider + tmp_path DB"""
    db_path = str(tmp_path / "test_memory.db")
    m = MemorySystem()
    m.embed_provider = MockEmbedProvider()
    m.embed_model = "bge-m3:latest"
    m.db_path = db_path
    m.storage = storage
    storage._init_db(db_path)
    m.memory_blocks = []
    m.core_memories = []
    m.user_profiles = []
    return m


def _fake_embedder(text: str) -> list[float]:
    if "依戀" in text:
        return [1.0, 0.0, 0.0]
    if "自律" in text:
        return [0.0, 0.0, 1.0]
    return [0.3, 0.3, 0.3]


@pytest.fixture
def store(storage):
    return PersonaSnapshotStore(
        storage, embedder=_fake_embedder,
        dormancy_idle_versions=2, dormancy_confidence_threshold=5.0,
    )


CHAR = "char-iso-test"


# ══════════════════════════════════════════════
# 1. user_id 隔離
# ══════════════════════════════════════════════

class TestUserIsolation:

    def test_memory_isolation_by_user(self, ms):
        """兩組 user_id 各寫記憶，_get_memory_blocks 只取回自己的"""
        ms.add_memory_block(
            "[核心實體]: 拉麵\n[情境摘要]: user_a 喜歡拉麵",
            [{"role": "user", "content": "我愛拉麵"}],
            user_id="user_a", character_id="char1", visibility="public",
        )
        ms.add_memory_block(
            "[核心實體]: 壽司\n[情境摘要]: user_b 喜歡壽司",
            [{"role": "user", "content": "我愛壽司"}],
            user_id="user_b", character_id="char1", visibility="public",
        )

        blocks_a = ms._get_memory_blocks("user_a", "char1", "public")
        blocks_b = ms._get_memory_blocks("user_b", "char1", "public")

        assert all("壽司" not in b["overview"] for b in blocks_a), \
            "user_a 的記憶不應含 user_b 的壽司"
        assert all("拉麵" not in b["overview"] for b in blocks_b), \
            "user_b 的記憶不應含 user_a 的拉麵"

    def test_merge_within_user_not_across(self, ms):
        """相同內容：同 user 兩次 → 合併；不同 user → 各自獨立，不跨用戶合併"""
        overview = "[核心實體]: 拉麵\n[情境摘要]: 喜歡拉麵"
        dlg = [{"role": "user", "content": "我愛拉麵"}]

        # user_a 第一次寫入，記錄初始 encounter_count
        ms.add_memory_block(overview, dlg, user_id="user_a", character_id="c", visibility="public")
        initial_count = ms._get_memory_blocks("user_a", "c", "public")[0]["encounter_count"]

        # user_a 第二次寫入相同內容 → 應合併，encounter_count 增加
        ms.add_memory_block(overview, dlg, user_id="user_a", character_id="c", visibility="public")

        # user_b 相同內容 → 獨立寫入，不被 user_a 的 block 吸收
        ms.add_memory_block(overview, dlg, user_id="user_b", character_id="c", visibility="public")

        blocks_a = ms._get_memory_blocks("user_a", "c", "public")
        blocks_b = ms._get_memory_blocks("user_b", "c", "public")

        assert len(blocks_a) == 1, "user_a 的兩筆相同內容應合併為一"
        assert blocks_a[0]["encounter_count"] > initial_count, \
            f"合併後 encounter_count 應增加（{initial_count} → {blocks_a[0]['encounter_count']}）"
        assert len(blocks_b) == 1, "user_b 的 block 獨立"
        assert blocks_b[0]["encounter_count"] <= initial_count, \
            "user_b 的 encounter_count 不應被 user_a 的相同 block bump"

    def test_legacy_default_call_works(self, ms):
        """不帶 user_id/visibility 的呼叫應落到 ('default', 'default', 'public')"""
        ms.add_memory_block(
            "[核心實體]: 向後相容\n[情境摘要]: 預設值測試",
            [{"role": "user", "content": "向後相容"}],
            # 刻意不傳 user_id / character_id / visibility → 使用預設值
        )
        blocks = ms._get_memory_blocks("default", "default", "public")
        assert len(blocks) == 1, "預設呼叫應寫入 ('default','default','public') 槽位"


# ══════════════════════════════════════════════
# 2. visibility 非對稱讀取
# ══════════════════════════════════════════════

class TestVisibilityFilter:

    def test_visibility_asymmetric_read(self, ms):
        """SU 寫 private/public 各一筆；public face 只讀 public，private face 讀兩層"""
        ms.add_memory_block(
            "[核心實體]: 私密\n[情境摘要]: 私訊內容",
            [{"role": "user", "content": "私訊"}],
            user_id="su", character_id="c", visibility="private",
        )
        ms.add_memory_block(
            "[核心實體]: 公開\n[情境摘要]: 直播留言",
            [{"role": "user", "content": "直播"}],
            user_id="su", character_id="c", visibility="public",
        )

        # public face 只讀 public
        pub_blocks = ms._get_memory_blocks("su", "c", "public")
        assert len(pub_blocks) == 1, "public face 應只看到 1 筆"
        assert "私密" not in pub_blocks[0]["overview"], \
            "public face 不應看到 visibility=private 的記憶"

        # private face 需讀兩層（private + public）
        priv_blocks = ms._get_memory_blocks("su", "c", "private")
        pub_in_priv = ms._get_memory_blocks("su", "c", "public")
        all_su_blocks = priv_blocks + pub_in_priv
        assert len(all_su_blocks) == 2, "private face 合計應看到 2 筆（含 public 那層）"

    def test_profile_private_not_in_public_query(self, ms):
        """private profile fact 不出現在 public visibility_filter 查詢中"""
        ms.apply_profile_facts(
            [{"action": "INSERT", "fact_key": "secret_name", "fact_value": "私密名稱",
              "category": "basic_info", "justification": "私訊提到"}],
            ms.embed_model, user_id="su", visibility="private",
        )
        ms.apply_profile_facts(
            [{"action": "INSERT", "fact_key": "public_name", "fact_value": "公開名稱",
              "category": "basic_info", "justification": "直播提到"}],
            ms.embed_model, user_id="su", visibility="public",
        )

        public_prompt = ms.get_static_profile_prompt(user_id="su", visibility_filter=["public"])
        assert "私密名稱" not in public_prompt, \
            "public face prompt 不應含 visibility=private 的事實"
        assert "公開名稱" in public_prompt, \
            "public face prompt 應含 visibility=public 的事實"

    def test_su_private_fact_not_in_public_prompt(self, ms):
        """SU 私訊抽出的私密 fact 在 public face prompt 中不出現（端對端驗證）"""
        ms.apply_profile_facts(
            [{"action": "INSERT", "fact_key": "home_address",
              "fact_value": "某某市某某路 123 號",
              "category": "basic_info", "justification": "私訊提及住址"}],
            ms.embed_model, user_id="su", visibility="private",
        )

        public_prompt = ms.get_static_profile_prompt(user_id="su", visibility_filter=["public"])
        assert "某某路" not in public_prompt and "123" not in public_prompt, \
            "私訊住址不應出現在 public face prompt"


# ══════════════════════════════════════════════
# 3. 跨用戶 cluster 合併防護
# ══════════════════════════════════════════════

class TestClusterIsolation:

    def test_no_cross_user_cluster_merge(self, ms):
        """find_pending_clusters 嚴格限定於同 user_id，不跨用戶合組"""
        ov_a1 = "[核心實體]: 拉麵, 豚骨\n[情境摘要]: 喜歡豚骨拉麵（a-1）"
        ov_a2 = "[核心實體]: 拉麵, 豚骨湯頭\n[情境摘要]: 豚骨拉麵好喝（a-2）"
        ov_b1 = "[核心實體]: 壽司, 鮭魚\n[情境摘要]: 喜歡鮭魚壽司（b-1）"
        ov_b2 = "[核心實體]: 壽司, 鮪魚\n[情境摘要]: 鮪魚壽司很鮮（b-2）"

        for ov in [ov_a1, ov_a2]:
            ms.add_memory_block(ov, [{"role": "user", "content": "拉麵"}],
                                user_id="user_a", character_id="c", visibility="public")
        for ov in [ov_b1, ov_b2]:
            ms.add_memory_block(ov, [{"role": "user", "content": "壽司"}],
                                user_id="user_b", character_id="c", visibility="public")

        clusters_a = ms.find_pending_clusters(
            cluster_threshold=0.5, min_group_size=2,
            user_id="user_a", character_id="c", visibility="public",
        )
        clusters_b = ms.find_pending_clusters(
            cluster_threshold=0.5, min_group_size=2,
            user_id="user_b", character_id="c", visibility="public",
        )

        overviews_in_a = {b["overview"] for grp in clusters_a for b in grp}
        overviews_in_b = {b["overview"] for grp in clusters_b for b in grp}

        assert not (overviews_in_a & {ov_b1, ov_b2}), \
            "user_a 的 cluster 不應包含 user_b 的記憶"
        assert not (overviews_in_b & {ov_a1, ov_a2}), \
            "user_b 的 cluster 不應包含 user_a 的記憶"


# ══════════════════════════════════════════════
# 4. 雙 persona_face 獨立演化
# ══════════════════════════════════════════════

class TestPersonaFaceIsolation:

    def test_persona_face_independent_evolution(self, store):
        """public 與 private face 各自 save_snapshot，active_traits 不相互影響"""
        diff_pub = TraitDiff(new_traits=[
            NewTrait(name="依戀傾向", description="依戀", confidence="high")
        ])
        store.save_snapshot(CHAR, diff_pub, "v1-pub", "prompt-pub", persona_face="public")

        diff_priv = TraitDiff(new_traits=[
            NewTrait(name="自律習慣", description="自律", confidence="high")
        ])
        store.save_snapshot(CHAR, diff_priv, "v1-priv", "prompt-priv", persona_face="private")

        pub_names = {t["name"] for t in store.list_active_traits(CHAR, persona_face="public")}
        priv_names = {t["name"] for t in store.list_active_traits(CHAR, persona_face="private")}

        assert "依戀傾向" in pub_names, "public face 應有「依戀傾向」"
        assert "自律習慣" not in pub_names, "public face 不應有 private 的「自律習慣」"
        assert "自律習慣" in priv_names, "private face 應有「自律習慣」"
        assert "依戀傾向" not in priv_names, "private face 不應有 public 的「依戀傾向」"

    def test_persona_face_independent_trait_keys(self, store):
        """同名 trait 在不同 face 下產生不同的 trait_key（彼此互不影響）"""
        diff = TraitDiff(new_traits=[
            NewTrait(name="依戀傾向", description="依戀", confidence="high")
        ])
        store.save_snapshot(CHAR, diff, "v1-pub", "prompt", persona_face="public")
        store.save_snapshot(CHAR, diff, "v1-priv", "prompt", persona_face="private")

        pub_key = store.list_active_traits(CHAR, persona_face="public")[0]["trait_key"]
        priv_key = store.list_active_traits(CHAR, persona_face="private")[0]["trait_key"]

        assert pub_key != priv_key, \
            "同名 trait 在不同 face 下應是獨立的 trait_key，不應共用"
