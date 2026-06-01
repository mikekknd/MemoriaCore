"""Free Talk topic pack loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


MAX_TITLE_CHARS = 120
MAX_PROMPT_CHARS = 1000
MAX_TOPICS_PER_PACK = 200


def load_free_talk_topic_library(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)

    packs: list[dict[str, Any]] = []
    warnings: list[str] = []
    for path in sorted(root.glob("*.json"), key=lambda item: item.name):
        pack = _load_pack_file(path)
        if pack is None:
            warnings.append(f"{path.name}: JSON 讀取失敗或格式無效")
            continue
        warnings.extend(pack["warnings"])
        if pack["topic_count"] > 0:
            packs.append(pack)

    return {
        "topic_dir": str(root),
        "packs": packs,
        "total_topic_count": sum(pack["topic_count"] for pack in packs),
        "warnings": warnings,
    }


def load_free_talk_sidecar(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {
            "found": False,
            "topic_count": 0,
            "topics": [],
            "warnings": [],
        }

    pack = _load_pack_file(path)
    if pack is None:
        return {
            "found": True,
            "topic_count": 0,
            "topics": [],
            "warnings": [f"{path.name}: JSON 讀取失敗或格式無效"],
        }

    return {
        "found": True,
        "topic_count": pack["topic_count"],
        "topics": pack["topics"],
        "warnings": pack["warnings"],
    }


def _load_pack_file(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None

    topics_data: Any
    display_name = path.stem
    if isinstance(data, dict):
        display_name = _clean_text(data.get("name"), max_chars=MAX_TITLE_CHARS) or path.stem
        topics_data = data.get("topics")
    elif isinstance(data, list):
        topics_data = data
    else:
        return None

    if not isinstance(topics_data, list):
        return None

    valid_topics: list[dict[str, str]] = []
    warnings: list[str] = []
    for index, raw_topic in enumerate(topics_data, start=1):
        topic = _clean_topic(raw_topic)
        if topic is None:
            warnings.append(f"{path.name}: 第 {index} 筆 topic 缺少有效 title 或 prompt")
            continue
        valid_topics.append(topic)

    topics = valid_topics[:MAX_TOPICS_PER_PACK]
    if len(valid_topics) > MAX_TOPICS_PER_PACK:
        warnings.append(f"{path.name}: topic 數量超過 {MAX_TOPICS_PER_PACK}，已忽略多餘項目")

    return {
        "pack_id": path.stem,
        "display_name": display_name,
        "filename": path.name,
        "topic_count": len(topics),
        "topics": topics,
        "warnings": warnings,
    }


def _clean_topic(raw_topic: Any) -> dict[str, str] | None:
    if not isinstance(raw_topic, dict):
        return None

    title = _clean_text(raw_topic.get("title"), max_chars=MAX_TITLE_CHARS)
    prompt = _clean_text(raw_topic.get("prompt"), max_chars=MAX_PROMPT_CHARS)
    if not title or not prompt:
        return None
    return {"title": title, "prompt": prompt}


def _clean_text(value: Any, *, max_chars: int) -> str:
    if value is None:
        return ""
    return str(value).strip()[:max_chars]
