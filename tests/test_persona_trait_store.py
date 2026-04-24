"""人格演化 Path D — PersonaSnapshotStore / save_trait_snapshot 整合測試。

策略：
- 真的 StorageManager + ``tmp_path`` 注入 DB（user_version=0 → 自動升級到 2）。
- 假 embedder：依描述關鍵字回傳固定向量，控制 ``infer_single_parent`` 結果。
- 覆蓋：V1 / Vn updates / Vn new_traits / invalid parent_key fallback / B' sweep /
  reactivate / 同版剛建立 trait 不被掃 / updates 夾雜 name/description 被忽略。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.persona_evolution.snapshot_store import PersonaSnapshotStore
from core.persona_evolution.trait_diff import NewTrait, TraitDiff, TraitUpdate
from core.storage_manager import StorageManager


# ──────────────────────────────────────────────
# Fixtures / 假 embedder
# ──────────────────────────────────────────────

def fake_embedder(text: str) -> list[float]:
    """關鍵字 → 向量映射，方便控制 cosine 結果。

    - 含「依戀」 → [1, 0, 0]
    - 含「避風港」→ [0, 1, 0]
    - 含「依附」 → [0.95, 0.31, 0]（與「依戀」cosine≈0.95，>0.82 → 衍生）
    - 含「自律」 → [0, 0, 1]（與其他都正交，不衍生）
    - 其他 → [0.3, 0.3, 0.3]
    """
    if "依戀" in text:
        return [1.0, 0.0, 0.0]
    if "避風港" in text:
        return [0.0, 1.0, 0.0]
    if "依附" in text:
        return [0.95, 0.31, 0.0]
    if "自律" in text:
        return [0.0, 0.0, 1.0]
    return [0.3, 0.3, 0.3]


@pytest.fixture
def storage(tmp_path):
    return StorageManager(
        prefs_file=str(tmp_path / "prefs.json"),
        history_file=str(tmp_path / "history.json"),
        persona_snapshot_db_path=str(tmp_path / "persona.db"),
    )


@pytest.fixture
def store(storage):
    # idle=2 讓測試用更短版本數就能觀察 sweep
    return PersonaSnapshotStore(
        storage,
        embedder=fake_embedder,
        dormancy_idle_versions=2,
        dormancy_confidence_threshold=5.0,
    )


CHAR = "char-trait-test"


def _new(name, description, confidence="high", parent_key=None):
    return NewTrait(name=name, description=description, confidence=confidence, parent_key=parent_key)


def _upd(trait_key, confidence):
    return TraitUpdate(trait_key=trait_key, confidence=confidence)


# ──────────────────────────────────────────────
# Migration — 新 tmp_path DB 啟動後 PRAGMA user_version 應升到 2
# ──────────────────────────────────────────────

class TestMigration:
    def test_fresh_db_upgrades_to_v2(self, storage):
        # 觸發 _init_persona_snapshot_db
        storage.get_active_traits(CHAR)
        import sqlite3
        conn = sqlite3.connect(storage.persona_snapshot_db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA user_version")
        assert cur.fetchone()[0] == 2
        # persona_traits 表存在
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='persona_traits'")
        assert cur.fetchone() is not None
        conn.close()


# ──────────────────────────────────────────────
# V1（首版）— updates 為空，全是 new_traits
# ──────────────────────────────────────────────

class TestFirstVersion:
    def test_v1_inserts_root_traits(self, store, storage):
        diff = TraitDiff(
            updates=[],
            new_traits=[
                _new("依戀錨定", "把情感寄託在穩定對象，透過持續接觸獲得安全感"),
                _new("避風港機制", "壓力下會主動尋找熟悉環境來回血", confidence="medium"),
            ],
        )
        sid = store.save_snapshot(CHAR, diff, "v1 summary", "# Persona v1")
        assert sid >= 1

        snap = storage.get_latest_persona_snapshot(CHAR)
        assert snap["version"] == 1
        assert len(snap["dimensions"]) == 2
        names = {d["name"] for d in snap["dimensions"]}
        assert names == {"依戀錨定", "避風港機制"}
        # 全是 root（parent_key=None）
        for d in snap["dimensions"]:
            assert d["parent_key"] is None
            assert d["is_active"] is True

    def test_v1_active_traits_list(self, store, storage):
        diff = TraitDiff(new_traits=[_new("依戀錨定", "描述 A")])
        store.save_snapshot(CHAR, diff, "s", "p")
        active = store.list_active_traits(CHAR)
        assert len(active) == 1
        assert active[0]["name"] == "依戀錨定"
        assert active[0]["last_active_version"] == 1


# ──────────────────────────────────────────────
# Vn updates — bump last_active_version + 寫 dim row
# ──────────────────────────────────────────────

class TestVnUpdates:
    def test_update_bumps_last_active_version(self, store, storage):
        d1 = TraitDiff(new_traits=[_new("依戀錨定", "描述 A")])
        store.save_snapshot(CHAR, d1, "v1", "p1")
        tk = store.list_active_traits(CHAR)[0]["trait_key"]

        d2 = TraitDiff(updates=[_upd(tk, "medium")])
        store.save_snapshot(CHAR, d2, "v2", "p2")

        v2 = storage.get_latest_persona_snapshot(CHAR)
        assert v2["version"] == 2
        assert len(v2["dimensions"]) == 1
        dim = v2["dimensions"][0]
        assert dim["confidence_label"] == "medium"
        assert dim["confidence"] == pytest.approx(5.0)

        active = store.list_active_traits(CHAR)
        assert active[0]["last_active_version"] == 2

    def test_update_none_bumps_active_but_no_dim_row(self, store, storage):
        """confidence=none 仍 bump last_active_version，但不寫 persona_dimensions。"""
        d1 = TraitDiff(new_traits=[_new("依戀錨定", "描述 A")])
        store.save_snapshot(CHAR, d1, "v1", "p1")
        tk = store.list_active_traits(CHAR)[0]["trait_key"]

        d2 = TraitDiff(updates=[_upd(tk, "none")])
        store.save_snapshot(CHAR, d2, "v2", "p2")

        v2 = storage.get_latest_persona_snapshot(CHAR)
        # 沒有 dim row（confidence=none 不寫）
        assert len(v2["dimensions"]) == 0

        # 但 trait 仍活躍、last_active_version=2
        active = store.list_active_traits(CHAR)
        assert len(active) == 1
        assert active[0]["last_active_version"] == 2

    def test_update_to_nonexistent_trait_is_skipped(self, store, storage):
        """LLM 回傳不存在的 trait_key（例如早已被 sweep 的）→ 後端略過，不拋錯。"""
        d1 = TraitDiff(new_traits=[_new("依戀錨定", "描述 A")])
        store.save_snapshot(CHAR, d1, "v1", "p1")

        d2 = TraitDiff(updates=[_upd("nonexistent_key_1234", "high")])
        store.save_snapshot(CHAR, d2, "v2", "p2")
        v2 = storage.get_latest_persona_snapshot(CHAR)
        # v2 無 dim row（update 被略過）
        assert len(v2["dimensions"]) == 0


# ──────────────────────────────────────────────
# Vn new_traits — parent_key 驗證 / fallback / reactivate
# ──────────────────────────────────────────────

class TestVnNewTraits:
    def test_new_trait_with_valid_parent_key(self, store, storage):
        d1 = TraitDiff(new_traits=[_new("依戀錨定", "依戀描述 A")])
        store.save_snapshot(CHAR, d1, "v1", "p")
        parent_tk = store.list_active_traits(CHAR)[0]["trait_key"]

        d2 = TraitDiff(new_traits=[_new("依附性自我定義", "依附描述 B", parent_key=parent_tk)])
        store.save_snapshot(CHAR, d2, "v2", "p")

        active = store.list_active_traits(CHAR)
        assert len(active) == 2
        child = next(t for t in active if t["name"] == "依附性自我定義")
        assert child["parent_key"] == parent_tk

    def test_invalid_parent_key_falls_back_to_cosine(self, store, storage):
        """LLM 填錯 parent_key（不在活躍清單）→ fake_embedder 讓「依附」對「依戀」cosine≈0.95 → 推得 parent。"""
        d1 = TraitDiff(new_traits=[_new("依戀錨定", "依戀描述 A")])
        store.save_snapshot(CHAR, d1, "v1", "p")
        parent_tk = store.list_active_traits(CHAR)[0]["trait_key"]

        d2 = TraitDiff(new_traits=[_new("依附性自我定義", "依附描述 B", parent_key="wrong_key_xxxx")])
        store.save_snapshot(CHAR, d2, "v2", "p")

        active = store.list_active_traits(CHAR)
        child = next(t for t in active if t["name"] == "依附性自我定義")
        # fallback cosine 推斷 → parent 為 依戀錨定
        assert child["parent_key"] == parent_tk

    def test_no_cosine_match_falls_back_to_none(self, store, storage):
        """LLM 填錯 parent_key 且 cosine 無匹配（正交）→ parent_key=None。"""
        d1 = TraitDiff(new_traits=[_new("依戀錨定", "依戀描述 A")])
        store.save_snapshot(CHAR, d1, "v1", "p")

        # 「自律」對「依戀」向量正交 → 無匹配
        d2 = TraitDiff(new_traits=[_new("自律強度", "自律描述", parent_key="wrong_key")])
        store.save_snapshot(CHAR, d2, "v2", "p")

        active = store.list_active_traits(CHAR)
        new_trait = next(t for t in active if t["name"] == "自律強度")
        assert new_trait["parent_key"] is None

    def test_parent_being_referenced_reactivates(self, store, storage):
        """V2 的 new_trait 指向某 trait 後，該 trait 的 last_active_version 應被 bump。"""
        d1 = TraitDiff(new_traits=[_new("依戀錨定", "依戀描述 A")])
        store.save_snapshot(CHAR, d1, "v1", "p")
        parent_tk = store.list_active_traits(CHAR)[0]["trait_key"]

        # V2 不動依戀錨定，但新 trait 指向它
        d2 = TraitDiff(new_traits=[_new("依附性自我定義", "描述 B", parent_key=parent_tk)])
        store.save_snapshot(CHAR, d2, "v2", "p")

        active = store.list_active_traits(CHAR)
        parent = next(t for t in active if t["trait_key"] == parent_tk)
        # 被引用 → last_active_version 應 bump 到 2
        assert parent["last_active_version"] == 2


# ──────────────────────────────────────────────
# B' 休眠 sweep — 閒置 N 版 + confidence ≤ threshold
# ──────────────────────────────────────────────

class TestDormancySweep:
    def test_trait_sweeps_after_idle(self, store, storage):
        """V1 建 2 trait（均 medium=5.0） → V2、V3 都只 update 一個 →
        另一個閒置 2 版（idle=2），confidence=medium ≤ threshold=5.0 → sweep。"""
        d1 = TraitDiff(new_traits=[
            _new("依戀錨定", "依戀描述", confidence="medium"),
            _new("自律強度", "自律描述", confidence="medium"),
        ])
        store.save_snapshot(CHAR, d1, "v1", "p")

        active = store.list_active_traits(CHAR)
        dependent_tk = next(t["trait_key"] for t in active if t["name"] == "依戀錨定")

        # V2、V3：只 update 依戀錨定，自律強度閒置
        store.save_snapshot(
            CHAR, TraitDiff(updates=[_upd(dependent_tk, "high")]), "v2", "p"
        )
        store.save_snapshot(
            CHAR, TraitDiff(updates=[_upd(dependent_tk, "high")]), "v3", "p"
        )

        # 自律強度：(3 - 1) = 2 >= idle=2，confidence=5.0 <= threshold=5.0 → sweep
        active_after = store.list_active_traits(CHAR)
        assert len(active_after) == 1
        assert active_after[0]["name"] == "依戀錨定"

    def test_not_swept_in_same_version(self, store, storage):
        """邊界：剛建立的 trait 差值為 0，絕不能被掃。"""
        d1 = TraitDiff(new_traits=[_new("依戀錨定", "描述", confidence="low")])
        store.save_snapshot(CHAR, d1, "v1", "p")
        # V1 後立即查活躍，trait 必須在
        active = store.list_active_traits(CHAR)
        assert len(active) == 1

    def test_high_confidence_not_swept(self, store, storage):
        """high (8.0) > threshold(5.0)，即使閒置也不 sweep。"""
        d1 = TraitDiff(new_traits=[
            _new("依戀錨定", "描述", confidence="high"),
            _new("自律強度", "自律", confidence="medium"),
        ])
        store.save_snapshot(CHAR, d1, "v1", "p")
        dep_tk = next(t["trait_key"] for t in store.list_active_traits(CHAR) if t["name"] == "自律強度")

        # 連續 3 版只動自律，依戀閒置但是 high
        for v in ("v2", "v3", "v4"):
            store.save_snapshot(CHAR, TraitDiff(updates=[_upd(dep_tk, "high")]), v, "p")

        names = {t["name"] for t in store.list_active_traits(CHAR)}
        # 依戀高 confidence → 不 sweep；自律一直被 update → bump，也在
        assert "依戀錨定" in names
        assert "自律強度" in names

    def test_swept_trait_reactivates_via_parent_reference(self, store, storage):
        """被 sweep 的 trait 在後續版本被 new_trait.parent_key 引用 → 復活。"""
        d1 = TraitDiff(new_traits=[
            _new("依戀錨定", "依戀描述", confidence="medium"),
            _new("自律強度", "自律描述", confidence="medium"),
        ])
        store.save_snapshot(CHAR, d1, "v1", "p")

        active_v1 = store.list_active_traits(CHAR)
        self_tk = next(t["trait_key"] for t in active_v1 if t["name"] == "自律強度")
        dep_tk = next(t["trait_key"] for t in active_v1 if t["name"] == "依戀錨定")

        # V2, V3：只 update 依戀 → 自律閒置 2 版 → V3 後 sweep
        store.save_snapshot(CHAR, TraitDiff(updates=[_upd(dep_tk, "high")]), "v2", "p")
        store.save_snapshot(CHAR, TraitDiff(updates=[_upd(dep_tk, "high")]), "v3", "p")

        active_v3 = store.list_active_traits(CHAR)
        assert self_tk not in {t["trait_key"] for t in active_v3}

        # V4：新 trait 引用已 sweep 的自律為 parent → 自律自動 reactivate
        store.save_snapshot(
            CHAR,
            TraitDiff(new_traits=[_new("延伸自律", "延伸描述", parent_key=self_tk)]),
            "v4",
            "p",
        )
        active_v4 = store.list_active_traits(CHAR)
        assert self_tk in {t["trait_key"] for t in active_v4}


# ──────────────────────────────────────────────
# 讀取：get_tree / get_trait_timeline
# ──────────────────────────────────────────────

class TestReads:
    def test_get_tree_shape(self, store, storage):
        d = TraitDiff(new_traits=[_new("依戀錨定", "描述 A")])
        store.save_snapshot(CHAR, d, "v1", "p")
        tree = store.get_latest_tree(CHAR)
        assert tree is not None
        assert tree["version"] == 1
        assert len(tree["nodes"]) == 1
        node = tree["nodes"][0]
        assert node["name"] == "依戀錨定"
        assert node["is_active"] is True
        assert node["parent_key"] is None

    def test_get_tree_nonexistent_version(self, store):
        assert store.get_tree(CHAR, 99) is None

    def test_get_latest_tree_empty(self, store):
        assert store.get_latest_tree(CHAR) is None

    def test_trait_timeline_confidence_progression(self, store, storage):
        d1 = TraitDiff(new_traits=[_new("依戀錨定", "描述", confidence="low")])
        store.save_snapshot(CHAR, d1, "v1", "p")
        tk = store.list_active_traits(CHAR)[0]["trait_key"]

        store.save_snapshot(CHAR, TraitDiff(updates=[_upd(tk, "medium")]), "v2", "p")
        store.save_snapshot(CHAR, TraitDiff(updates=[_upd(tk, "high")]), "v3", "p")

        tl = store.get_trait_timeline(CHAR, tk)
        assert [p["confidence"] for p in tl] == [2.5, 5.0, 8.0]
        assert [p["version"] for p in tl] == [1, 2, 3]

    def test_list_active_traits_ordering_and_limit(self, store, storage):
        d1 = TraitDiff(new_traits=[
            _new("依戀錨定", "A"),
            _new("避風港機制", "B"),
        ])
        store.save_snapshot(CHAR, d1, "v1", "p")

        tks = {t["name"]: t["trait_key"] for t in store.list_active_traits(CHAR)}
        # V2 只 update 避風港 → 它 last_active=2，依戀 last_active=1
        store.save_snapshot(
            CHAR, TraitDiff(updates=[_upd(tks["避風港機制"], "high")]), "v2", "p"
        )

        # 按 last_active_version DESC：避風港 在前
        active = store.list_active_traits(CHAR)
        assert [t["name"] for t in active] == ["避風港機制", "依戀錨定"]

        # limit 測試
        assert len(store.list_active_traits(CHAR, limit=1)) == 1


# ──────────────────────────────────────────────
# 版本號自動遞增：多次連續 save 不衝突
# ──────────────────────────────────────────────

class TestVersionAutoIncrement:
    def test_consecutive_saves(self, store, storage):
        for i in range(1, 4):
            store.save_snapshot(
                CHAR, TraitDiff(new_traits=[_new(f"T{i}", f"D{i}")]), f"v{i}", "p"
            )
        snaps = storage.list_persona_snapshots(CHAR)
        assert [s["version"] for s in snaps] == [1, 2, 3]

    def test_character_isolation(self, store, storage):
        store.save_snapshot("char-a", TraitDiff(new_traits=[_new("Ta", "A")]), "s", "p")
        store.save_snapshot("char-b", TraitDiff(new_traits=[_new("Tb", "B")]), "s", "p")
        store.save_snapshot("char-a", TraitDiff(new_traits=[_new("Ta2", "A2")]), "s", "p")

        a_snaps = storage.list_persona_snapshots("char-a")
        b_snaps = storage.list_persona_snapshots("char-b")
        assert [s["version"] for s in a_snaps] == [1, 2]
        assert [s["version"] for s in b_snaps] == [1]
