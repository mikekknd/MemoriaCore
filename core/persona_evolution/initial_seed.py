"""人格演化樹初始 snapshot 建立工具。

設計重點：
- 對同一角色只萃取一次 ``TraitDiff``，再寫入所有缺 snapshot 的 face；
  避免 public/private 各跑一次 LLM 造成成本浪費。
- 萃取流程刻意對齊 ``scripts/generate_path_d_snapshots.py``：使用
  PersonaProbe 的 V1 prompt + schema，產出 3-5 個 root traits（皆無
  ``parent_key``），讓初始 snapshot 也是有語意的 Path D 樹而非單一節點。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Iterable

from core.persona_evolution.extractor import TRAIT_V1_SCHEMA, parse_trait_v1
from core.persona_evolution.trait_diff import NewTrait, TraitDiff
from core.system_logger import SystemLogger


INITIAL_TRAIT_NAME = "初始人設"
INITIAL_PERSONA_FACES = ("public", "private")
DEFAULT_TASK_KEY = "persona_seed"


def _ensure_probe_on_path() -> None:
    """確保 PersonaProbe 目錄在 sys.path 上（lazy，只在需要 LLM 時呼叫）。"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    probe_dir = os.path.join(root, "PersonaProbe")
    if probe_dir not in sys.path:
        sys.path.insert(0, probe_dir)


def build_initial_trait_source(character: dict) -> str:
    """把角色 profile 轉成 V1 trait 萃取用的初始文本。"""
    name = str(character.get("name") or character.get("character_id") or "未命名角色").strip()
    summary = " ".join(str(character.get("character_summary") or "").split())
    system_prompt = str(character.get("system_prompt") or "").strip()
    reply_rules = str(character.get("reply_rules") or "").strip()
    tts_rules = str(character.get("tts_rules") or "").strip()

    parts = [
        "【角色初始設定】",
        f"角色名稱：{name}",
    ]
    if summary:
        parts.append(f"角色簡介：{summary}")
    if system_prompt:
        parts.extend(["核心人格設定：", system_prompt])
    if reply_rules:
        parts.extend(["回覆規則：", reply_rules])
    if tts_rules:
        parts.extend(["TTS 表現規則：", tts_rules])
    return "\n".join(parts)


def build_initial_trait_description(character: dict) -> str:
    """LLM 不可用時的保底單節點描述。"""
    name = str(character.get("name") or character.get("character_id") or "未命名角色").strip()
    summary = " ".join(str(character.get("character_summary") or "").split())
    system_prompt = " ".join(str(character.get("system_prompt") or "").split())
    reply_rules = " ".join(str(character.get("reply_rules") or "").split())

    parts = [f"這是角色「{name}」建立時的基準人格節點。"]
    if summary:
        parts.append(f"角色簡介：{summary}")
    if system_prompt:
        parts.append(f"核心設定：{system_prompt[:900]}")
    if reply_rules:
        parts.append(f"回覆規則：{reply_rules[:240]}")
    return "\n".join(parts)


def build_initial_trait_diff(
    character: dict,
    router=None,
    task_key: str = DEFAULT_TASK_KEY,
) -> TraitDiff:
    """用 PersonaProbe V1 prompt 從角色初始設定萃取 root traits。

    回傳的 ``TraitDiff.new_traits`` 必為 root traits（``parent_key=None``），
    讓寫入時無需 embedder 推斷血統，可在 ONNX 尚未 warmup 前安全執行。
    """
    if router is not None:
        try:
            _ensure_probe_on_path()
            from probe_engine import build_trait_v1_prompt

            source_text = build_initial_trait_source(character)
            existing_persona = str(character.get("system_prompt") or "")
            messages = build_trait_v1_prompt(source_text, existing_persona=existing_persona)
            raw = router.generate_json(
                task_key,
                messages,
                schema=TRAIT_V1_SCHEMA,
                temperature=0.7,
            )
            traits = parse_trait_v1(raw)
            if traits:
                return TraitDiff(updates=[], new_traits=traits)
            SystemLogger.log_error(
                "persona_initial_snapshot",
                f"LLM did not return parseable initial traits: character_id={character.get('character_id')}",
            )
        except Exception as exc:
            SystemLogger.log_error(
                "persona_initial_snapshot",
                f"LLM initial trait extraction failed: character_id={character.get('character_id')}, error={exc}",
            )

    return TraitDiff(new_traits=[
        NewTrait(
            name=INITIAL_TRAIT_NAME,
            description=build_initial_trait_description(character),
            confidence="medium",
        )
    ])


def ensure_initial_persona_snapshots(
    store,
    characters: Iterable[dict],
    persona_faces: Iterable[str] = INITIAL_PERSONA_FACES,
    *,
    router=None,
    task_key: str = DEFAULT_TASK_KEY,
) -> dict[str, list[str]]:
    """替所有缺少 snapshot 的角色補初始人格樹，回傳 ``{character_id: [face, ...]}``。

    對同一角色只跑一次 LLM 萃取，再把同一份 ``TraitDiff`` 寫入所有缺 snapshot
    的 face；既省成本，也讓 public / private 從一致的起點演化。
    """
    seeded: dict[str, list[str]] = {}
    for character in characters:
        character_id = str(character.get("character_id") or "").strip()
        if not character_id:
            continue
        missing_faces = [
            face for face in persona_faces
            if store.get_latest_tree(character_id, persona_face=face) is None
        ]
        if not missing_faces:
            continue

        try:
            trait_diff = build_initial_trait_diff(character, router=router, task_key=task_key)
        except Exception as exc:
            SystemLogger.log_error(
                "persona_initial_snapshot",
                f"trait extraction crashed: character_id={character_id}, error={exc}",
            )
            continue

        evolved_prompt = str(character.get("system_prompt") or "")
        summary_name = str(character.get("name") or character_id)
        summary_text = f"角色建立時的初始人格：{summary_name}"
        ts = datetime.now().isoformat()

        for face in missing_faces:
            try:
                store.save_snapshot(
                    character_id=character_id,
                    trait_diff=trait_diff,
                    summary=summary_text,
                    evolved_prompt=evolved_prompt,
                    timestamp=ts,
                    persona_face=face,
                )
                seeded.setdefault(character_id, []).append(face)
            except Exception as exc:
                SystemLogger.log_error(
                    "persona_initial_snapshot",
                    f"save failed: character_id={character_id}, face={face}, error={exc}",
                )
    return seeded
