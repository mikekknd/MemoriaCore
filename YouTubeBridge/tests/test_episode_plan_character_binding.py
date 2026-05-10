import sys
from pathlib import Path

import pytest

BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from episode_plan_character_binding import (
    EpisodePlanCharacterBindingError,
    resolve_episode_plan_character_ids,
)
from test_live_episode_plan_contract import sample_plan


def test_resolves_participants_by_display_name_not_plan_participant_id():
    plan = sample_plan()
    plan["participants"][0]["participant_id"] = "not-the-real-character-id"
    plan["participants"][0]["display_name"] = "可可"
    plan["participants"][1]["display_name"] = "白蓮"
    plan["participants"][2]["display_name"] = "旁白"
    characters = [
        {"character_id": "koko", "name": "可可"},
        {"character_id": "byakuren", "name": "白蓮"},
        {"character_id": "narrator", "name": "旁白"},
    ]

    resolved = resolve_episode_plan_character_ids(plan, characters)

    assert resolved == ["koko", "byakuren", "narrator"]


def test_missing_character_name_reports_clear_error():
    plan = sample_plan()
    characters = [
        {"character_id": "host-a", "name": "主持A"},
        {"character_id": "analyst-b", "name": "分析B"},
    ]

    with pytest.raises(EpisodePlanCharacterBindingError, match="找不到企劃角色「質疑C」"):
        resolve_episode_plan_character_ids(plan, characters)


def test_duplicate_character_name_reports_clear_error():
    plan = sample_plan()
    characters = [
        {"character_id": "host-a", "name": "主持A"},
        {"character_id": "analyst-b", "name": "分析B"},
        {"character_id": "skeptic-a", "name": "質疑C"},
        {"character_id": "skeptic-b", "name": "質疑C"},
    ]

    with pytest.raises(EpisodePlanCharacterBindingError, match="對應到多個 MemoriaCore 角色"):
        resolve_episode_plan_character_ids(plan, characters)


def test_empty_character_list_reports_clear_error():
    with pytest.raises(EpisodePlanCharacterBindingError, match="角色清單為空"):
        resolve_episode_plan_character_ids(sample_plan(), [])
