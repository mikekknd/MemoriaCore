from __future__ import annotations

import importlib.util
from pathlib import Path


VALIDATOR_PATH = (
    Path(__file__).resolve().parents[1]
    / ".agents"
    / "skills"
    / "topic-evidence-card"
    / "scripts"
    / "validate_topic_evidence_card.py"
)
LIVE_PLANNER_SKILL_PATH = (
    Path(__file__).resolve().parents[1]
    / ".agents"
    / "skills"
    / "live-episode-planner"
    / "SKILL.md"
)


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_topic_evidence_card", VALIDATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_topic_evidence_card_validator_accepts_evidence_labels(tmp_path):
    validator = _load_validator()
    card = tmp_path / "evidence-card.md"
    card.write_text(
        "\n".join([
            "# Topic Evidence Card：春番公開排名",
            "",
            "## Summary",
            "本卡只整理可查證事實與網路意見看法，供導播在內容段落選擇資料，不替角色決定正反立場。",
            "",
            "## Facts",
            "",
            "### 公開週榜：新作攻頂是否值得開場後討論",
            "- 可驗證事實：Anime Corner 第 4 週公開榜單顯示特定新作取得週榜第一，這只能代表該週投票結果。",
            "- 網路意見看法：榜單留言與社群轉貼常把新作聲量視為看點，但這仍只是公開討論氛圍，不能當成全平台評價。",
        ]),
        encoding="utf-8",
    )

    assert validator.validate(card, min_sections=1, max_sections=3) == []


def test_topic_evidence_card_validator_rejects_director_viewpoint_labels(tmp_path):
    validator = _load_validator()
    card = tmp_path / "bad-fuel-card.md"
    card.write_text(
        "\n".join([
            "# Topic Evidence Card：春番公開排名",
            "",
            "## Summary",
            "本卡看似 evidence card，但混入導播或辯論用的立場欄位，應該被 validator 擋下來。",
            "",
            "## Facts",
            "",
            "### 公開週榜：新作攻頂是否值得開場後討論",
            "- 基礎背景：Anime Corner 第 4 週公開榜單顯示特定新作取得週榜第一。",
            "- 正方觀點：這代表新作已經贏過所有續作。",
            "- 反方觀點：這只是單週波動。",
            "- 第三種觀點：它比較像觀眾口味變化的溫度計。",
            "- 觀眾互動問題：你會把它當本季代表作嗎。",
            "- 爆點句：今年春番最有趣的不是誰最強。",
        ]),
        encoding="utf-8",
    )

    errors = validator.validate(card, min_sections=1, max_sections=3)

    assert any("director viewpoint label" in error for error in errors)


def test_topic_evidence_card_skill_contract_uses_two_runtime_labels():
    skill_text = VALIDATOR_PATH.parents[1].joinpath("SKILL.md").read_text(encoding="utf-8")

    contract = skill_text[skill_text.index("## Output Contract"):]

    assert "- 可驗證事實：" in contract
    assert "- 網路意見看法：" in contract
    for old_label in ("- 資料邊界：", "- 可用切角：", "- 不可主張：", "- 來源提示："):
        assert old_label not in contract


def test_live_episode_planner_auto_generates_evidence_cards_for_planned_queries():
    skill_text = LIVE_PLANNER_SKILL_PATH.read_text(encoding="utf-8")

    assert "Automatically generate Topic Evidence Cards" in skill_text
    assert "evidence_policy.queries" in skill_text
    assert "factcards/" in skill_text
    assert "validate_topic_evidence_card.py" in skill_text
