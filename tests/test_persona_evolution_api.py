"""人格演化 REST API 端點測試（Path D）。

為避免啟動完整 FastAPI lifespan（會觸發 ONNX 載入與 Ollama warmup），
本測試只掛 ``persona_evolution`` 這支 router，並直接寫 ``api.dependencies``
的 module-level singleton 注入測試用 StorageManager + Store。

覆蓋：
- ``GET /snapshots``                — 清單摘要
- ``GET /snapshots/latest``         — 最新版完整內容
- ``GET /snapshots/{version}``      — 指定版本
- ``GET /snapshots/{version}/tree`` — Force Graph 結構（含 parent_key → links）
- ``GET /snapshots/latest/tree``    — 最新版 tree
- ``GET /traits``                   — trait 清單（active_only flag）
- ``GET /traits/timeline``          — 指定 trait_key 的 confidence 序列
"""
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.persona_evolution.snapshot_store import PersonaSnapshotStore
from core.persona_evolution.trait_diff import NewTrait, TraitDiff, TraitUpdate
from core.storage_manager import StorageManager

import api.dependencies as deps
from api.routers.persona_evolution import router as persona_router


# ── 假 embedder：避免 ONNX 依賴 ─────────────────────────────
def _fake_embedder(text: str) -> list[float]:
    if "依戀" in text:
        return [1.0, 0.0, 0.0]
    if "依附" in text:
        return [0.95, 0.31, 0.0]
    if "避風港" in text:
        return [0.0, 1.0, 0.0]
    return [0.0, 0.0, 1.0]


@pytest.fixture
def client(tmp_path, monkeypatch):
    """建立獨立 FastAPI app + mount persona router + 注入 test singleton。

    回傳 ``(TestClient, StorageManager, PersonaSnapshotStore)`` 三元組，
    讓測試可以直接用 store 預 seed trait 後打 API 驗證。
    """
    storage = StorageManager(
        prefs_file=str(tmp_path / "prefs.json"),
        history_file=str(tmp_path / "hist.json"),
        persona_snapshot_db_path=str(tmp_path / "persona.db"),
    )
    store = PersonaSnapshotStore(storage, embedder=_fake_embedder)

    monkeypatch.setattr(deps, "storage", storage)
    monkeypatch.setattr(deps, "persona_snapshot_store", store)

    app = FastAPI()
    app.include_router(persona_router, prefix="/api/v1")
    return TestClient(app), storage, store


CHAR = "char-api-1"


def _diff_new(traits: list[dict]) -> TraitDiff:
    """快速建構只含 new_traits 的 TraitDiff。"""
    return TraitDiff(new_traits=[NewTrait(**t) for t in traits])


# ──────────────────────────────────────────────
# GET /snapshots
# ──────────────────────────────────────────────

class TestListSnapshots:
    def test_empty_returns_empty_list(self, client):
        c, _, _ = client
        resp = c.get("/api/v1/system/personality/snapshots", params={"character_id": CHAR})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_summaries_ascending(self, client):
        c, _, store = client
        store.save_snapshot(
            CHAR,
            _diff_new([{"name": "依戀", "description": "d1", "confidence": "high"}]),
            "s1", "p1",
        )
        store.save_snapshot(
            CHAR,
            _diff_new([{"name": "避風港", "description": "d2", "confidence": "medium"}]),
            "s2", "p2",
        )
        resp = c.get("/api/v1/system/personality/snapshots", params={"character_id": CHAR})
        assert resp.status_code == 200
        data = resp.json()
        assert [r["version"] for r in data] == [1, 2]
        # V1: 1 new trait, V2: 1 new trait（無 updates）
        assert data[0]["dimensions_count"] == 1
        assert data[1]["dimensions_count"] == 1


# ──────────────────────────────────────────────
# GET /snapshots/latest
# ──────────────────────────────────────────────

class TestGetLatest:
    def test_no_data_404(self, client):
        c, _, _ = client
        resp = c.get("/api/v1/system/personality/snapshots/latest", params={"character_id": CHAR})
        assert resp.status_code == 404

    def test_returns_highest_version_with_trait_key_field(self, client):
        c, _, store = client
        store.save_snapshot(
            CHAR,
            _diff_new([{"name": "依戀", "description": "d", "confidence": "high"}]),
            "s1", "p1",
        )
        existing_key = store.list_active_traits(CHAR)[0]["trait_key"]

        # V2: update 既有 + 新增一個
        td2 = TraitDiff(
            updates=[TraitUpdate(trait_key=existing_key, confidence="medium")],
            new_traits=[NewTrait(name="避風港", description="d2", confidence="high")],
        )
        store.save_snapshot(CHAR, td2, "s2", "p2")

        resp = c.get(
            "/api/v1/system/personality/snapshots/latest",
            params={"character_id": CHAR},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == 2
        assert data["summary"] == "s2"
        # 應含 2 筆 dim：既有 update + 新 trait
        assert len(data["dimensions"]) == 2
        # 每個 dim 都要帶 trait_key 別名 + dimension_key（同值）
        for d in data["dimensions"]:
            assert d["trait_key"] == d["dimension_key"]
            assert "parent_key" in d
            assert "is_active" in d


# ──────────────────────────────────────────────
# GET /snapshots/{version}
# ──────────────────────────────────────────────

class TestGetByVersion:
    def test_missing_404(self, client):
        c, _, _ = client
        resp = c.get(
            "/api/v1/system/personality/snapshots/99",
            params={"character_id": CHAR},
        )
        assert resp.status_code == 404

    def test_returns_specific_version(self, client):
        c, _, store = client
        store.save_snapshot(
            CHAR,
            _diff_new([{"name": "A", "description": "a", "confidence": "high"}]),
            "s1", "p1",
        )
        store.save_snapshot(
            CHAR,
            _diff_new([{"name": "B", "description": "b", "confidence": "low"}]),
            "s2", "p2",
        )
        resp = c.get(
            "/api/v1/system/personality/snapshots/1",
            params={"character_id": CHAR},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == 1
        assert data["summary"] == "s1"
        assert data["evolved_prompt"] == "p1"


# ──────────────────────────────────────────────
# GET /snapshots/{version}/tree
# ──────────────────────────────────────────────

class TestGetTree:
    def test_missing_404(self, client):
        c, _, _ = client
        resp = c.get(
            "/api/v1/system/personality/snapshots/1/tree",
            params={"character_id": CHAR},
        )
        assert resp.status_code == 404

    def test_returns_nodes_with_parent_key(self, client):
        """V2 的新 trait 指向 V1 trait → tree 回傳 parent_key 正確。"""
        c, _, store = client
        store.save_snapshot(
            CHAR,
            _diff_new([{"name": "依戀", "description": "d1", "confidence": "high"}]),
            "s1", "p1",
        )
        root_key = store.list_active_traits(CHAR)[0]["trait_key"]

        td2 = TraitDiff(
            updates=[TraitUpdate(trait_key=root_key, confidence="medium")],
            new_traits=[NewTrait(
                name="依附自我",
                description="衍生自依戀",
                parent_key=root_key,
                confidence="high",
            )],
        )
        store.save_snapshot(CHAR, td2, "s2", "p2")

        resp = c.get(
            "/api/v1/system/personality/snapshots/2/tree",
            params={"character_id": CHAR},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == 2
        by_name = {n["name"]: n for n in data["nodes"]}
        assert by_name["依附自我"]["parent_key"] == root_key
        assert by_name["依戀"]["parent_key"] is None

    def test_links_derived_from_parent_key(self, client):
        """tree DTO 的 links 應以 parent_key（非 parent_name）為準。

        Path D 下 parent_key 是真血統，parent_name 只是 denormalised cache。
        """
        c, _, store = client
        store.save_snapshot(
            CHAR,
            _diff_new([{"name": "依戀", "description": "d1", "confidence": "high"}]),
            "s1", "p1",
        )
        root_key = store.list_active_traits(CHAR)[0]["trait_key"]

        td2 = TraitDiff(
            updates=[TraitUpdate(trait_key=root_key, confidence="medium")],
            new_traits=[NewTrait(
                name="依附自我", description="衍生", parent_key=root_key, confidence="high",
            )],
        )
        store.save_snapshot(CHAR, td2, "s2", "p2")

        resp = c.get(
            "/api/v1/system/personality/snapshots/2/tree",
            params={"character_id": CHAR},
        )
        assert resp.status_code == 200
        data = resp.json()

        # 每個 node 的 id = dimension_key = trait_key
        for n in data["nodes"]:
            assert n["id"] == n["dimension_key"] == n["trait_key"]

        # 依附自我 node → 依戀 node 應有一條 link
        child_key = next(n["trait_key"] for n in data["nodes"] if n["name"] == "依附自我")
        expected = {"source": root_key, "target": child_key}
        assert expected in data["links"]
        # 沒有孤兒邊（parent=None 的根節點不產生 link）
        assert len(data["links"]) == 1

    def test_orphan_parent_not_linked(self, client):
        """parent_key 指向不在本版的 trait → 不產生 link（避免孤立邊）。

        V1 建 root；V2 做 new_trait 指 root；V3 某 trait 仍引用那個 root 為 parent，
        但若該版 dims 中沒有 root（confidence=none 不寫 dim）→ link 被跳過。
        """
        c, _, store = client
        store.save_snapshot(
            CHAR,
            _diff_new([{"name": "依戀", "description": "d1", "confidence": "high"}]),
            "s1", "p1",
        )
        root_key = store.list_active_traits(CHAR)[0]["trait_key"]

        # V2: root 做 update=none（不寫 dim row），new trait 指 root
        td2 = TraitDiff(
            updates=[TraitUpdate(trait_key=root_key, confidence="none")],
            new_traits=[NewTrait(
                name="孤兒子", description="指向不在本版的 root", parent_key=root_key, confidence="high",
            )],
        )
        store.save_snapshot(CHAR, td2, "s2", "p2")

        resp = c.get(
            "/api/v1/system/personality/snapshots/2/tree",
            params={"character_id": CHAR},
        )
        assert resp.status_code == 200
        data = resp.json()
        # 只有 "孤兒子" 在本版 dims（root 被 none 濾掉）
        names = [n["name"] for n in data["nodes"]]
        assert names == ["孤兒子"]
        # 其 parent_key 指向不在本版的 root → 不 link
        assert data["links"] == []


# ──────────────────────────────────────────────
# GET /snapshots/latest/tree
# ──────────────────────────────────────────────

class TestGetLatestTree:
    def test_no_snapshot_returns_404(self, client):
        c, _, _ = client
        resp = c.get(
            "/api/v1/system/personality/snapshots/latest/tree",
            params={"character_id": CHAR},
        )
        assert resp.status_code == 404

    def test_returns_highest_version_tree(self, client):
        c, _, store = client
        store.save_snapshot(
            CHAR,
            _diff_new([{"name": "A", "description": "a", "confidence": "high"}]),
            "s1", "p1",
        )
        store.save_snapshot(
            CHAR,
            _diff_new([{"name": "B", "description": "b", "confidence": "medium"}]),
            "s2", "p2",
        )
        resp = c.get(
            "/api/v1/system/personality/snapshots/latest/tree",
            params={"character_id": CHAR},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == 2
        assert data["summary"] == "s2"
        assert all("id" in n and "trait_key" in n for n in data["nodes"])
        assert isinstance(data["links"], list)

    def test_latest_not_shadowed_by_version_route(self, client):
        """路由順序：/latest/tree 不能被 /{version}/tree 當成 version=latest 吃掉。"""
        c, _, store = client
        store.save_snapshot(
            CHAR,
            _diff_new([{"name": "A", "description": "a", "confidence": "high"}]),
            "s1", "p1",
        )
        resp = c.get(
            "/api/v1/system/personality/snapshots/latest/tree",
            params={"character_id": CHAR},
        )
        assert resp.status_code != 422
        assert resp.status_code == 200


# ──────────────────────────────────────────────
# GET /traits — 新端點
# ──────────────────────────────────────────────

class TestListTraits:
    def test_empty_returns_empty_list(self, client):
        c, _, _ = client
        resp = c.get("/api/v1/system/personality/traits", params={"character_id": CHAR})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_default_active_only(self, client):
        c, _, store = client
        store.save_snapshot(
            CHAR,
            _diff_new([
                {"name": "A", "description": "a", "confidence": "high"},
                {"name": "B", "description": "b", "confidence": "medium"},
            ]),
            "s1", "p1",
        )
        resp = c.get(
            "/api/v1/system/personality/traits",
            params={"character_id": CHAR},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        names = {r["name"] for r in data}
        assert names == {"A", "B"}
        # 必要欄位都在
        for r in data:
            assert "trait_key" in r
            assert "last_description" in r
            assert "created_version" in r
            assert "last_active_version" in r
            assert "parent_key" in r
            assert r["is_active"] is True

    def test_active_only_false_includes_dormant(self, client):
        """active_only=false → 含已休眠 trait。

        觸發休眠：B trait 從 V1 起連續 idle_version 版無 update 且 confidence 低 → sweep。
        """
        c, storage, _ = client
        # 用短 idle 版本的 store 覆寫（本 fixture 預設無此參數，手動建）
        store_short = PersonaSnapshotStore(storage, embedder=_fake_embedder, dormancy_idle_versions=1)
        store_short.save_snapshot(
            CHAR,
            _diff_new([
                {"name": "A", "description": "a", "confidence": "high"},
                {"name": "B", "description": "b", "confidence": "low"},  # low 會觸發 sweep
            ]),
            "s1", "p1",
        )
        # V2：只 update A，B 無人理 → sweep
        a_key = next(t["trait_key"] for t in store_short.list_active_traits(CHAR) if t["name"] == "A")
        td2 = TraitDiff(
            updates=[TraitUpdate(trait_key=a_key, confidence="high")],
            new_traits=[],
        )
        store_short.save_snapshot(CHAR, td2, "s2", "p2")

        # active_only=true 只回 A
        resp_active = c.get(
            "/api/v1/system/personality/traits",
            params={"character_id": CHAR, "active_only": "true"},
        )
        assert resp_active.status_code == 200
        assert [r["name"] for r in resp_active.json()] == ["A"]

        # active_only=false 回 A + B（B is_active=False）
        resp_all = c.get(
            "/api/v1/system/personality/traits",
            params={"character_id": CHAR, "active_only": "false"},
        )
        assert resp_all.status_code == 200
        all_data = resp_all.json()
        assert len(all_data) == 2
        by_name = {r["name"]: r for r in all_data}
        assert by_name["A"]["is_active"] is True
        assert by_name["B"]["is_active"] is False


# ──────────────────────────────────────────────
# GET /traits/timeline — 新端點
# ──────────────────────────────────────────────

class TestTraitTimeline:
    def test_unknown_trait_returns_empty_points(self, client):
        c, _, _ = client
        resp = c.get(
            "/api/v1/system/personality/traits/timeline",
            params={"character_id": CHAR, "trait_key": "nonexistent-key-hex"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["trait_key"] == "nonexistent-key-hex"
        assert data["points"] == []

    def test_multi_version_confidence_series(self, client):
        c, _, store = client
        store.save_snapshot(
            CHAR,
            _diff_new([{"name": "A", "description": "a", "confidence": "low"}]),
            "s1", "p1",
        )
        a_key = store.list_active_traits(CHAR)[0]["trait_key"]

        store.save_snapshot(
            CHAR,
            TraitDiff(updates=[TraitUpdate(trait_key=a_key, confidence="medium")]),
            "s2", "p2",
        )
        store.save_snapshot(
            CHAR,
            TraitDiff(updates=[TraitUpdate(trait_key=a_key, confidence="high")]),
            "s3", "p3",
        )

        resp = c.get(
            "/api/v1/system/personality/traits/timeline",
            params={"character_id": CHAR, "trait_key": a_key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["trait_key"] == a_key
        versions = [p["version"] for p in data["points"]]
        confidences = [p["confidence"] for p in data["points"]]
        labels = [p["confidence_label"] for p in data["points"]]
        assert versions == [1, 2, 3]
        assert confidences == [pytest.approx(2.5), pytest.approx(5.0), pytest.approx(8.0)]
        assert labels == ["low", "medium", "high"]

    def test_confidence_none_skips_point(self, client):
        """update 到 confidence=none 不寫 dim row → 該版不在 points 序列中。"""
        c, _, store = client
        store.save_snapshot(
            CHAR,
            _diff_new([{"name": "A", "description": "a", "confidence": "high"}]),
            "s1", "p1",
        )
        a_key = store.list_active_traits(CHAR)[0]["trait_key"]

        store.save_snapshot(
            CHAR,
            TraitDiff(updates=[TraitUpdate(trait_key=a_key, confidence="none")]),
            "s2", "p2",
        )
        store.save_snapshot(
            CHAR,
            TraitDiff(updates=[TraitUpdate(trait_key=a_key, confidence="medium")]),
            "s3", "p3",
        )

        resp = c.get(
            "/api/v1/system/personality/traits/timeline",
            params={"character_id": CHAR, "trait_key": a_key},
        )
        assert resp.status_code == 200
        versions = [p["version"] for p in resp.json()["points"]]
        assert versions == [1, 3]  # v2 因 confidence=none 缺席
