from __future__ import annotations

from collections import defaultdict
from typing import Any


class EpisodePlanCharacterBindingError(ValueError):
    """Raised when a LiveEpisodePlan cannot be mapped to MemoriaCore characters."""


def _normalize_name(value: Any) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split()).casefold()


def _participant_display_names(plan: dict[str, Any]) -> list[str]:
    participants = plan.get("participants") if isinstance(plan.get("participants"), list) else []
    names = [
        str(item.get("display_name") or "").strip()
        for item in participants
        if isinstance(item, dict)
    ]
    names = [name for name in names if name]
    if not names:
        raise EpisodePlanCharacterBindingError("企劃沒有可用的參與者名稱，無法自動對應角色。")
    return names


def _character_names(character: dict[str, Any]) -> set[str]:
    names = {
        str(character.get(key) or "").strip()
        for key in ("name", "display_name", "nickname")
        if str(character.get(key) or "").strip()
    }
    aliases = character.get("aliases")
    if isinstance(aliases, list):
        names.update(str(item).strip() for item in aliases if str(item).strip())
    return names


def resolve_episode_plan_character_ids(
    plan: dict[str, Any],
    characters: list[dict[str, Any]],
) -> list[str]:
    """Resolve plan participants to real character_ids by display name.

    The plan's participant_id is intentionally ignored; it is only a local
    director-contract identifier and may differ from MemoriaCore character IDs.
    """
    participant_names = _participant_display_names(plan)
    if not characters:
        raise EpisodePlanCharacterBindingError("MemoriaCore 角色清單為空，無法依企劃角色名稱對應實際角色。")

    by_name: dict[str, list[dict[str, str]]] = defaultdict(list)
    for character in characters:
        if not isinstance(character, dict):
            continue
        character_id = str(character.get("character_id") or "").strip()
        if not character_id:
            continue
        for name in _character_names(character):
            normalized = _normalize_name(name)
            if normalized:
                by_name[normalized].append({
                    "character_id": character_id,
                    "name": name,
                })
    if not by_name:
        raise EpisodePlanCharacterBindingError("MemoriaCore 角色清單沒有可用角色名稱，無法對應企劃參與者。")

    resolved: list[str] = []
    for participant_name in participant_names:
        matches = by_name.get(_normalize_name(participant_name), [])
        unique_matches = {
            str(match["character_id"]): match
            for match in matches
        }
        if not unique_matches:
            raise EpisodePlanCharacterBindingError(
                f"找不到企劃角色「{participant_name}」對應的 MemoriaCore 角色；請確認角色名稱一致。"
            )
        if len(unique_matches) > 1:
            candidates = ", ".join(sorted(unique_matches))
            raise EpisodePlanCharacterBindingError(
                f"企劃角色「{participant_name}」對應到多個 MemoriaCore 角色：{candidates}；請調整角色名稱避免重複。"
            )
        character_id = next(iter(unique_matches))
        if character_id not in resolved:
            resolved.append(character_id)
    return resolved
