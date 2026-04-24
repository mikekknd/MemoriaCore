"""人格演化 Path D — 擴取層。

職責：
- 定義 LLM 兩種回應（V1 首版 / Vn 增量 diff）的 JSON Schema。
- 集中管理 confidence 字串→浮點映射尺規。
- 提供 ``parse_trait_v1`` / ``parse_trait_vn`` 容錯解析，吸收 LLM 格式漂移。

LLM 仍維持 4 級字串輸出（high/medium/low/none）；浮點映射僅用於 SQL 儲存
與前端視覺節點強度補間。
"""
import json

from pydantic import ValidationError

from core.persona_evolution.trait_diff import NewTrait, TraitDiff, TraitUpdate


CONFIDENCE_MAP: dict[str, float] = {
    "high": 8.0,
    "medium": 5.0,
    "low": 2.5,
    "none": 0.0,
}


def to_float_confidence(label: str | None) -> float:
    """將 LLM 字串映射為浮點值；未識別或 None 一律視為 0.0。"""
    if not isinstance(label, str):
        return 0.0
    return CONFIDENCE_MAP.get(label.strip().lower(), 0.0)


# ──────────────────────────────────────────────────────────────────────
# JSON Schema — 餵給 LLMClient.chat(response_format=...)
# ──────────────────────────────────────────────────────────────────────

TRAIT_V1_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "new_traits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                },
                "required": ["name", "description", "confidence"],
            },
        },
    },
    "required": ["new_traits"],
}

TRAIT_VN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "updates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "trait_key": {"type": "string"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low", "none"],
                    },
                },
                "required": ["trait_key", "confidence"],
            },
        },
        "new_traits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "parent_key": {"type": ["string", "null"]},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                },
                "required": ["name", "description", "confidence"],
            },
        },
    },
    "required": ["updates", "new_traits"],
}


# ──────────────────────────────────────────────────────────────────────
# 解析 — 皆採容錯策略：格式漂移不拋例外，回傳盡量合理的空/部分結果
# ──────────────────────────────────────────────────────────────────────

def _coerce_to_dict(raw: str | dict | None) -> dict | None:
    """把 ``str``/``dict``/``None`` 正規化為 dict；失敗回 None。"""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None
    return None


def parse_trait_v1(raw: str | dict | None) -> list[NewTrait]:
    """解析 V1（首版）LLM 回應為 ``list[NewTrait]``。

    期望格式：``{"new_traits": [{"name", "description", "confidence"}, ...]}``。
    ``parent_key`` 在 V1 一律為 None（LLM 不需要提供，此處強制）。
    單筆無效（缺欄位 / confidence 不在允許值）會被略過，不整批失敗。
    """
    data = _coerce_to_dict(raw)
    if not data:
        return []
    items = data.get("new_traits")
    if not isinstance(items, list):
        return []

    result: list[NewTrait] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item = dict(item)
        item["parent_key"] = None  # V1 一律 root；忽略 LLM 可能誤填的值
        try:
            result.append(NewTrait.model_validate(item))
        except ValidationError:
            continue
    return result


def parse_trait_vn(raw: str | dict | None) -> TraitDiff:
    """解析 Vn（增量）LLM 回應為 ``TraitDiff``。

    期望格式：``{"updates": [{"trait_key", "confidence"}], "new_traits": [...]}``。
    單筆無效條目略過；整體無法解析時回傳空 ``TraitDiff``（不拋例外）。
    ``updates`` 中若 LLM 夾帶 name/description 等欄位一律忽略（Path D 規格：
    updates 只改 confidence）。
    """
    data = _coerce_to_dict(raw)
    if not data:
        return TraitDiff()

    raw_updates = data.get("updates") or []
    raw_new_traits = data.get("new_traits") or []

    updates: list[TraitUpdate] = []
    if isinstance(raw_updates, list):
        for item in raw_updates:
            if not isinstance(item, dict):
                continue
            try:
                updates.append(TraitUpdate.model_validate({
                    "trait_key": item.get("trait_key"),
                    "confidence": item.get("confidence"),
                }))
            except ValidationError:
                continue

    new_traits: list[NewTrait] = []
    if isinstance(raw_new_traits, list):
        for item in raw_new_traits:
            if not isinstance(item, dict):
                continue
            try:
                new_traits.append(NewTrait.model_validate({
                    "name": item.get("name"),
                    "description": item.get("description"),
                    "parent_key": item.get("parent_key"),
                    "confidence": item.get("confidence"),
                }))
            except ValidationError:
                continue

    return TraitDiff(updates=updates, new_traits=new_traits)
