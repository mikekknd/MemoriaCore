"""人格演化 Path D — 薄殼組合層。

職責：
- 接收 ``TraitDiff``（Vn）或 ``list[NewTrait]``（V1），做 ``parent_key`` 驗證 /
  fallback cosine 推斷 / trait_key 生成，委由 ``StorageManager.save_trait_snapshot``
  原子寫入。
- 提供 ``list_active_traits`` / ``get_trait_timeline`` / ``get_tree`` / ``get_latest_tree``
  供 PersonaSyncManager 與 API 層讀取。

本模組刻意不直接使用 ``sqlite3``，所有持久化必經 StorageManager
（符合 CLAUDE.md 規定）。
"""
import uuid
from datetime import datetime
from typing import Callable

from core.persona_evolution.constants import (
    DORMANCY_CONFIDENCE_THRESHOLD,
    DORMANCY_IDLE_VERSIONS,
    MAX_ACTIVE_TRAITS_IN_PROMPT,
)
from core.persona_evolution.extractor import CONFIDENCE_MAP, to_float_confidence
from core.persona_evolution.lineage import infer_single_parent
from core.persona_evolution.trait_diff import NewTrait, TraitDiff, TraitUpdate


class PersonaSnapshotStore:
    def __init__(
        self,
        storage,
        embedder: Callable[[str], list[float]] | None = None,
        dormancy_idle_versions: int = DORMANCY_IDLE_VERSIONS,
        dormancy_confidence_threshold: float = DORMANCY_CONFIDENCE_THRESHOLD,
    ):
        """``storage``：StorageManager 實例。

        ``embedder``：選填 ``Callable[[str], list[float]]``；未提供時由
        ``lineage.infer_single_parent`` 內部 fallback 到 BGE-M3 ONNX。
        測試時可注入假 embedder 解耦 ONNX 依賴。

        ``dormancy_*``：B' 休眠參數，預設取 ``constants`` 模組值；整合測試
        可覆寫以縮短觀察版本數。
        """
        self.storage = storage
        self.embedder = embedder
        self.dormancy_idle_versions = dormancy_idle_versions
        self.dormancy_confidence_threshold = dormancy_confidence_threshold

    # ── 公開 API ─────────────────────────────────────────────

    def save_snapshot(
        self,
        character_id: str,
        trait_diff: TraitDiff,
        summary: str,
        evolved_prompt: str,
        timestamp: str | None = None,
    ) -> int:
        """寫入一筆 snapshot；回傳 snapshot_id。

        Path D 流程：
        1. 查 ``active_traits``（當前活躍清單，用於 parent_key 驗證）。
        2. 處理 ``trait_diff.updates``：查表驗證 trait_key 存在於本角色的
           ``persona_traits``，不存在則略過（Vn LLM 可能提供過期 key）。
        3. 處理 ``trait_diff.new_traits``：為每筆生成 ``trait_key = uuid4().hex``；
           若 LLM 提供的 ``parent_key`` 不存在，fallback 到
           ``infer_single_parent`` cosine 匹配。
        4. 委由 ``StorageManager.save_trait_snapshot`` 原子寫入 + 尾端 B' sweep。

        Args:
            character_id: 角色 ID。
            trait_diff: Vn 的 updates + new_traits；V1 情境下 updates 為空。
            summary: 演化摘要（取 probe-report 首段或 LLM 輸出）。
            evolved_prompt: 完整 persona.md 內容。
            timestamp: ISO 8601；未提供時用 ``datetime.now().isoformat()``。
        """
        ts = timestamp or datetime.now().isoformat()
        if not trait_diff.updates and not trait_diff.new_traits:
            raise ValueError(
                f"save_snapshot({character_id}): trait_diff 為空，拒絕寫入孤兒 snapshot。"
                " 請確認 LLM 萃取結果非空後再呼叫。"
            )
        # 活躍清單：供 updates 驗證 + fallback cosine 的候選池（只拿活躍的做 embedding 比對）
        active_traits = self.storage.get_active_traits(character_id)
        active_by_key = {t["trait_key"]: t for t in active_traits}
        # 全部 trait（含 sweep）：供 parent_key 存在性驗證——已休眠 trait 被引用可復活
        all_traits = self.storage.get_all_traits(character_id)
        all_by_key = {t["trait_key"]: t for t in all_traits}
        key_to_name = {tk: t["name"] for tk, t in all_by_key.items()}

        # ── 1) updates：只接受活躍 trait（LLM 看不到 sweep 的，不應該 update 它們） ──
        updates_payload: list[dict] = []
        for u in trait_diff.updates:
            if u.trait_key not in active_by_key:
                continue
            trait = active_by_key[u.trait_key]
            parent_key = trait.get("parent_key")
            updates_payload.append({
                "trait_key": u.trait_key,
                "name": trait["name"],
                "description": trait.get("last_description", ""),
                "confidence": to_float_confidence(u.confidence),
                "confidence_label": u.confidence,
                "parent_name": key_to_name.get(parent_key) if parent_key else None,
            })

        # ── 2) new_traits：parent_key 可指向任何歷史 trait（含已 sweep）──
        new_payload: list[dict] = []
        for n in trait_diff.new_traits:
            parent_key: str | None = None
            if n.parent_key and n.parent_key in all_by_key:
                # 存在於歷史（不論 active 或 sweep）→ 直接採用；save_trait_snapshot
                # 會自動 reactivate sweep 過的 parent
                parent_key = n.parent_key
            elif n.parent_key:
                # LLM 填了 key 但 DB 不存在 → fallback cosine 對活躍清單匹配
                parent_key = infer_single_parent(
                    {"name": n.name, "description": n.description},
                    active_traits,
                    embedder=self.embedder,
                )
            # n.parent_key is None → 明確的 root trait，保持 None
            new_payload.append({
                "trait_key": uuid.uuid4().hex,
                "name": n.name,
                "description": n.description,
                "confidence": to_float_confidence(n.confidence),
                "confidence_label": n.confidence,
                "parent_key": parent_key,
                "parent_name": key_to_name.get(parent_key) if parent_key else None,
            })

        # ── 3) 原子寫入 + B' sweep ──
        return self.storage.save_trait_snapshot(
            character_id=character_id,
            timestamp=ts,
            summary=summary,
            evolved_prompt=evolved_prompt,
            updates=updates_payload,
            new_traits=new_payload,
            dormancy_idle_versions=self.dormancy_idle_versions,
            dormancy_confidence_threshold=self.dormancy_confidence_threshold,
        )

    def list_active_traits(
        self,
        character_id: str,
        limit: int | None = MAX_ACTIVE_TRAITS_IN_PROMPT,
    ) -> list[dict]:
        """回傳活躍 trait 清單（按 ``last_active_version DESC``）。

        ``limit`` 預設為 ``MAX_ACTIVE_TRAITS_IN_PROMPT``（20）。傳 None 可取全部。
        """
        return self.storage.get_active_traits(character_id, limit=limit)

    def get_trait_timeline(self, character_id: str, trait_key: str) -> list:
        """代理 StorageManager — 某 trait 在所有版本的 confidence 變化序列。"""
        return self.storage.get_trait_timeline(character_id, trait_key)

    def get_tree(self, character_id: str, version: int) -> dict | None:
        """回傳指定版本的樹狀結構（Force-Directed Graph 用）。"""
        snap = self.storage.get_persona_snapshot(character_id, version)
        if snap is None:
            return None
        return self._snap_to_tree(snap)

    def get_latest_tree(self, character_id: str) -> dict | None:
        """回傳最新版 snapshot 的樹狀結構；無紀錄回 ``None``。"""
        snap = self.storage.get_latest_persona_snapshot(character_id)
        if snap is None:
            return None
        return self._snap_to_tree(snap)

    # ── 內部工具 ─────────────────────────────────────────────

    @staticmethod
    def _snap_to_tree(snap: dict) -> dict:
        """將 DB 層 snapshot 轉為前端 Force Graph 友善結構。

        ``nodes`` 的每筆 dict 包含 ``dimension_key``（= trait_key）、``parent_key``
        （真實血統，來自 persona_traits）、``parent_name``（denormalised 顯示）、
        ``is_active``（真實狀態）等欄位——``_load_dimensions_for`` 已 JOIN 好。
        """
        return {
            "version": snap["version"],
            "timestamp": snap["timestamp"],
            "summary": snap["summary"],
            "nodes": snap["dimensions"],
        }
