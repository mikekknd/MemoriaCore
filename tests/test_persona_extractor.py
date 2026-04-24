"""人格演化 Path D — extractor 單元測試（純資料層，不需 Ollama / ONNX）。

覆蓋：
- ``CONFIDENCE_MAP`` / ``to_float_confidence``（字串→浮點容錯映射）
- ``TRAIT_V1_SCHEMA`` / ``TRAIT_VN_SCHEMA`` 結構
- ``parse_trait_v1``：V1 回應解析（強制 parent_key=None，單筆容錯）
- ``parse_trait_vn``：Vn 回應解析（updates + new_traits 雙欄位容錯）
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.persona_evolution.extractor import (
    CONFIDENCE_MAP,
    TRAIT_V1_SCHEMA,
    TRAIT_VN_SCHEMA,
    parse_trait_v1,
    parse_trait_vn,
    to_float_confidence,
)


# ──────────────────────────────────────────────
# ConfidenceMap
# ──────────────────────────────────────────────

class TestConfidenceMap:
    def test_all_four_levels(self):
        assert CONFIDENCE_MAP["high"] == 8.0
        assert CONFIDENCE_MAP["medium"] == 5.0
        assert CONFIDENCE_MAP["low"] == 2.5
        assert CONFIDENCE_MAP["none"] == 0.0

    @pytest.mark.parametrize("label,expected", [
        ("high", 8.0),
        ("HIGH", 8.0),
        ("  medium  ", 5.0),
        ("low", 2.5),
        ("none", 0.0),
        ("unknown", 0.0),
        ("", 0.0),
    ])
    def test_to_float_confidence_variants(self, label, expected):
        assert to_float_confidence(label) == expected

    def test_to_float_confidence_non_string(self):
        assert to_float_confidence(None) == 0.0
        assert to_float_confidence(3) == 0.0
        assert to_float_confidence([]) == 0.0


# ──────────────────────────────────────────────
# JSON Schema 結構
# ──────────────────────────────────────────────

class TestSchemas:
    def test_v1_schema_requires_new_traits_array(self):
        assert TRAIT_V1_SCHEMA["type"] == "object"
        assert "new_traits" in TRAIT_V1_SCHEMA["required"]
        props = TRAIT_V1_SCHEMA["properties"]["new_traits"]
        assert props["type"] == "array"
        item = props["items"]
        assert set(item["required"]) == {"name", "description", "confidence"}
        # V1 confidence 只能是 high/medium/low（不允許 none — 新建 trait 至少是 low）
        assert set(item["properties"]["confidence"]["enum"]) == {"high", "medium", "low"}

    def test_vn_schema_has_updates_and_new_traits(self):
        assert set(TRAIT_VN_SCHEMA["required"]) == {"updates", "new_traits"}
        # updates.confidence 允許 none（降至不表現但 LLM 仍注意）
        upd_conf = TRAIT_VN_SCHEMA["properties"]["updates"]["items"]["properties"]["confidence"]
        assert set(upd_conf["enum"]) == {"high", "medium", "low", "none"}
        # new_traits.parent_key 可 null
        parent_key = TRAIT_VN_SCHEMA["properties"]["new_traits"]["items"]["properties"]["parent_key"]
        assert parent_key["type"] == ["string", "null"]


# ──────────────────────────────────────────────
# parse_trait_v1 — V1 首版解析
# ──────────────────────────────────────────────

class TestParseTraitV1:
    def test_parse_valid_dict(self):
        raw = {"new_traits": [
            {"name": "依戀", "description": "尋求情感錨定", "confidence": "high"},
            {"name": "避風港", "description": "以對方為情緒錨點", "confidence": "medium"},
        ]}
        result = parse_trait_v1(raw)
        assert len(result) == 2
        assert result[0].name == "依戀"
        assert result[0].confidence == "high"
        assert result[0].parent_key is None  # V1 強制為 None
        assert result[1].name == "避風港"

    def test_parse_valid_json_string(self):
        raw = json.dumps({"new_traits": [
            {"name": "A", "description": "a", "confidence": "low"}
        ]}, ensure_ascii=False)
        result = parse_trait_v1(raw)
        assert len(result) == 1
        assert result[0].confidence == "low"

    def test_parse_forces_parent_key_none(self):
        """LLM 誤在 V1 填 parent_key 時應一律置 None。"""
        raw = {"new_traits": [
            {"name": "X", "description": "x", "confidence": "high",
             "parent_key": "some-invalid-key"},
        ]}
        result = parse_trait_v1(raw)
        assert result[0].parent_key is None

    def test_parse_none_returns_empty(self):
        assert parse_trait_v1(None) == []

    def test_parse_empty_returns_empty(self):
        assert parse_trait_v1("") == []
        assert parse_trait_v1({}) == []

    def test_parse_invalid_json_returns_empty(self):
        assert parse_trait_v1("{not json") == []

    def test_parse_non_dict_returns_empty(self):
        assert parse_trait_v1('["not", "a", "dict"]') == []

    def test_invalid_item_is_skipped_not_batch_fail(self):
        """單筆欄位缺失/型別錯 → 略過該筆，其他合法筆仍保留。"""
        raw = {"new_traits": [
            {"name": "合法", "description": "d", "confidence": "high"},
            {"name": "缺欄位"},  # 沒 description / confidence
            {"name": "X", "description": "x", "confidence": "super_high"},  # confidence 不在 enum
            "not-a-dict",
        ]}
        result = parse_trait_v1(raw)
        assert len(result) == 1
        assert result[0].name == "合法"

    def test_new_traits_not_array_returns_empty(self):
        assert parse_trait_v1({"new_traits": "not array"}) == []


# ──────────────────────────────────────────────
# parse_trait_vn — Vn 增量 diff 解析
# ──────────────────────────────────────────────

class TestParseTraitVn:
    def test_parse_updates_and_new_traits(self):
        raw = {
            "updates": [
                {"trait_key": "key-a", "confidence": "high"},
                {"trait_key": "key-b", "confidence": "none"},
            ],
            "new_traits": [
                {"name": "新 trait", "description": "d", "parent_key": "key-a", "confidence": "medium"},
                {"name": "根 trait", "description": "d", "parent_key": None, "confidence": "low"},
            ],
        }
        result = parse_trait_vn(raw)
        assert len(result.updates) == 2
        assert result.updates[0].trait_key == "key-a"
        assert result.updates[0].confidence == "high"
        assert result.updates[1].confidence == "none"

        assert len(result.new_traits) == 2
        assert result.new_traits[0].parent_key == "key-a"
        assert result.new_traits[1].parent_key is None

    def test_parse_json_string(self):
        raw = json.dumps({
            "updates": [{"trait_key": "k", "confidence": "low"}],
            "new_traits": [],
        }, ensure_ascii=False)
        result = parse_trait_vn(raw)
        assert len(result.updates) == 1
        assert result.updates[0].confidence == "low"
        assert result.new_traits == []

    def test_updates_ignore_extra_fields(self):
        """Path D 規格：updates 只改 confidence；LLM 夾帶 name/description 應被忽略。"""
        raw = {
            "updates": [{
                "trait_key": "k1",
                "confidence": "high",
                "name": "應被忽略",          # 不該被解析
                "description": "應被忽略",    # 不該被解析
            }],
            "new_traits": [],
        }
        result = parse_trait_vn(raw)
        assert len(result.updates) == 1
        u = result.updates[0]
        assert u.trait_key == "k1"
        assert u.confidence == "high"
        # TraitUpdate 只有 trait_key + confidence 兩個欄位
        assert set(u.model_dump().keys()) == {"trait_key", "confidence"}

    def test_invalid_update_item_is_skipped(self):
        raw = {
            "updates": [
                {"trait_key": "ok", "confidence": "high"},
                {"confidence": "high"},           # 缺 trait_key
                {"trait_key": "x", "confidence": "invalid"},  # confidence 不在 enum
                "not-a-dict",
            ],
            "new_traits": [],
        }
        result = parse_trait_vn(raw)
        assert len(result.updates) == 1
        assert result.updates[0].trait_key == "ok"

    def test_invalid_new_trait_is_skipped(self):
        raw = {
            "updates": [],
            "new_traits": [
                {"name": "ok", "description": "d", "confidence": "medium"},
                {"name": "缺欄位"},
                {"description": "no name", "confidence": "high"},
                "not-a-dict",
            ],
        }
        result = parse_trait_vn(raw)
        assert len(result.new_traits) == 1
        assert result.new_traits[0].name == "ok"

    def test_missing_fields_default_to_empty_lists(self):
        """updates 或 new_traits 完全缺席 → 視為空陣列，不拋例外。"""
        assert parse_trait_vn({"updates": [{"trait_key": "k", "confidence": "low"}]}).new_traits == []
        assert parse_trait_vn({"new_traits": []}).updates == []

    def test_parse_none_returns_empty_diff(self):
        result = parse_trait_vn(None)
        assert result.updates == []
        assert result.new_traits == []

    def test_parse_invalid_json_returns_empty_diff(self):
        result = parse_trait_vn("{broken")
        assert result.updates == []
        assert result.new_traits == []

    def test_non_list_updates_returns_empty_updates(self):
        result = parse_trait_vn({"updates": "not a list", "new_traits": []})
        assert result.updates == []
