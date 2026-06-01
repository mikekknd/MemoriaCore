import json
import sys
from pathlib import Path


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from free_talk_topics import load_free_talk_sidecar, load_free_talk_topic_library


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_load_free_talk_topic_library_reads_object_pack_format(tmp_path):
    root = tmp_path / "topics"
    root.mkdir()
    _write_json(root / "anime.json", {
        "name": "動畫雜談",
        "topics": [
            {"title": "最近看的作品", "prompt": "請聊最近看的作品。"},
        ],
    })

    result = load_free_talk_topic_library(root)

    assert result["topic_dir"] == str(root)
    assert result["total_topic_count"] == 1
    assert result["warnings"] == []
    assert result["packs"] == [{
        "pack_id": "anime",
        "display_name": "動畫雜談",
        "filename": "anime.json",
        "topic_count": 1,
        "topics": [{"title": "最近看的作品", "prompt": "請聊最近看的作品。"}],
        "warnings": [],
    }]


def test_load_free_talk_topic_library_reads_array_pack_format(tmp_path):
    root = tmp_path / "topics"
    root.mkdir()
    _write_json(root / "creative.json", [
        {"title": "創作近況", "prompt": "請聊聊最近創作時遇到的事情。"},
    ])

    result = load_free_talk_topic_library(root)

    assert result["total_topic_count"] == 1
    assert result["packs"][0]["pack_id"] == "creative"
    assert result["packs"][0]["display_name"] == "creative"
    assert result["packs"][0]["topics"] == [
        {"title": "創作近況", "prompt": "請聊聊最近創作時遇到的事情。"},
    ]


def test_load_free_talk_topic_library_warns_for_bad_json_without_blocking_valid_pack(tmp_path):
    root = tmp_path / "topics"
    root.mkdir()
    (root / "00-bad.json").write_text("{bad json", encoding="utf-8")
    _write_json(root / "01-valid.json", [
        {"title": "創作近況", "prompt": "請聊聊最近創作時遇到的事情。"},
    ])

    result = load_free_talk_topic_library(root)

    assert result["total_topic_count"] == 1
    assert [pack["filename"] for pack in result["packs"]] == ["01-valid.json"]
    assert len(result["warnings"]) == 1
    assert "00-bad.json" in result["warnings"][0]


def test_load_free_talk_topic_library_skips_invalid_topics_with_warning(tmp_path):
    root = tmp_path / "topics"
    root.mkdir()
    _write_json(root / "mixed.json", [
        {"title": "  ", "prompt": "有 prompt 但 title 空白"},
        {"title": "有標題", "prompt": ""},
        {"title": "  有效標題  ", "prompt": "  有效 prompt  "},
    ])

    result = load_free_talk_topic_library(root)

    assert result["total_topic_count"] == 1
    assert result["packs"][0]["topics"] == [{"title": "有效標題", "prompt": "有效 prompt"}]
    assert len(result["packs"][0]["warnings"]) == 2
    assert len(result["warnings"]) == 2


def test_load_free_talk_topic_library_creates_missing_root_and_supports_bom(tmp_path):
    root = tmp_path / "missing"
    assert not root.exists()

    empty_result = load_free_talk_topic_library(root)

    assert root.exists()
    assert empty_result["topic_dir"] == str(root)
    assert empty_result["packs"] == []
    assert empty_result["total_topic_count"] == 0

    bom_file = root / "bom.json"
    bom_file.write_text(
        json.dumps([{"title": "BOM 標題", "prompt": "BOM prompt"}], ensure_ascii=False),
        encoding="utf-8-sig",
    )

    result = load_free_talk_topic_library(root)

    assert result["total_topic_count"] == 1
    assert result["packs"][0]["topics"] == [{"title": "BOM 標題", "prompt": "BOM prompt"}]


def test_load_free_talk_topic_library_limits_text_and_topic_count(tmp_path):
    root = tmp_path / "topics"
    root.mkdir()
    _write_json(root / "limits.json", [
        {"title": "標" * 121, "prompt": "文" * 1001}
        for _ in range(201)
    ])

    result = load_free_talk_topic_library(root)

    assert result["total_topic_count"] == 200
    assert len(result["packs"][0]["topics"]) == 200
    assert result["packs"][0]["topics"][0] == {
        "title": "標" * 120,
        "prompt": "文" * 1000,
    }
    assert any("200" in warning for warning in result["warnings"])


def test_load_free_talk_topic_library_limits_valid_topics_after_validation(tmp_path):
    root = tmp_path / "topics"
    root.mkdir()
    invalid_topics = [
        {"title": "", "prompt": "invalid prompt"}
        for _ in range(200)
    ]
    _write_json(root / "late-valid.json", [
        *invalid_topics,
        {"title": "第 201 筆有效題目", "prompt": "這筆有效題目不可被 raw topic 上限截掉。"},
    ])

    result = load_free_talk_topic_library(root)

    assert result["total_topic_count"] == 1
    assert result["packs"][0]["topics"] == [
        {"title": "第 201 筆有效題目", "prompt": "這筆有效題目不可被 raw topic 上限截掉。"},
    ]
    assert len(result["warnings"]) == 200


def test_load_free_talk_topic_library_excludes_all_invalid_pack_but_keeps_warnings(tmp_path):
    root = tmp_path / "topics"
    root.mkdir()
    _write_json(root / "all-invalid.json", [
        {"title": "", "prompt": "invalid prompt"},
        {"title": "invalid title", "prompt": " "},
    ])

    result = load_free_talk_topic_library(root)

    assert result["packs"] == []
    assert result["total_topic_count"] == 0
    assert len(result["warnings"]) == 2
    assert all("all-invalid.json" in warning for warning in result["warnings"])


def test_load_free_talk_sidecar_returns_not_found_shape_for_missing_path():
    result = load_free_talk_sidecar(None)

    assert result == {
        "found": False,
        "topic_count": 0,
        "topics": [],
        "warnings": [],
    }
