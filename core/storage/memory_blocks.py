# 【環境假設】：Python 3.12, numpy 庫可用。使用內建 sqlite3。支援 Schema Evolution。
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta

import numpy as np

from core.runtime_paths import runtime_file
from core.storage.constants import (
    DEFAULT_SYSTEM_PROMPT,
    GLOBAL_TOPIC_CHARACTER_ID,
    MAINTENANCE_DROP_TABLE_ALLOWLIST,
    SHARED_MEMORY_CHARACTER_ID,
    SHARED_MEMORY_USER_ID,
)


class MemoryBlockRepositoryMixin:
    # SECTION: 情境記憶 Memory Blocks — 載入 / 儲存
    # ════════════════════════════════════════════════════════════

    def load_db(
        self,
        db_path,
        user_id: str = "default",
        character_id: str = "default",
        visibility_filter=None,
    ):
        """載入記憶區塊。

        visibility_filter: list[str] | None
            None → 全部（給 private face 的 keyed cache 使用）
            ['public'] → 只 public
            ['private', 'public'] → private face 讀全量
        """
        if not os.path.exists(db_path):
            return []

        conn = self._init_db(db_path)
        cursor = conn.cursor()

        where_parts = ["user_id = ?", "character_id = ?"]
        params: list = [user_id, character_id]
        if visibility_filter is not None:
            placeholders = ','.join('?' * len(visibility_filter))
            where_parts.append(f"visibility IN ({placeholders})")
            params.extend(visibility_filter)
        where_clause = " WHERE " + " AND ".join(where_parts)

        cursor.execute(
            "SELECT block_id, timestamp, overview, overview_vector, sparse_vector, "
            "raw_dialogues, is_consolidated, encounter_count, potential_preferences "
            f"FROM memory_blocks{where_clause}",
            params
        )
        rows = cursor.fetchall()

        memory_blocks = []
        for row in rows:
            (block_id, timestamp, overview, overview_vector_blob,
             sparse_vector_json, raw_dialogues_json,
             is_consolidated, encounter_count, potential_preferences_json) = row
            overview_vector = np.frombuffer(overview_vector_blob, dtype=np.float32).tolist()
            memory_blocks.append({
                "block_id": block_id,
                "timestamp": timestamp,
                "overview": overview,
                "overview_vector": overview_vector,
                "sparse_vector": json.loads(sparse_vector_json) if sparse_vector_json else {},
                "raw_dialogues": json.loads(raw_dialogues_json) if raw_dialogues_json else [],
                "is_consolidated": bool(is_consolidated),
                "encounter_count": float(encounter_count) if encounter_count is not None else 1.0,
                "potential_preferences": json.loads(potential_preferences_json) if potential_preferences_json else [],
            })

        conn.close()
        return memory_blocks

    def load_shared_memory_blocks(
        self,
        db_path,
        character_id: str,
        visibility_filter=None,
    ) -> list[dict]:
        """讀取指定角色可見的 shared public memory blocks。"""
        if not os.path.exists(db_path):
            return []
        if visibility_filter is not None and "public" not in visibility_filter:
            return []
        character_id = str(character_id or "").strip()
        if not character_id:
            return []

        conn = self._init_db(db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT mb.block_id, mb.timestamp, mb.overview, mb.overview_vector,
                   mb.sparse_vector, mb.raw_dialogues, mb.is_consolidated,
                   mb.encounter_count, mb.potential_preferences,
                   COALESCE(mbm.metadata_json, '{}') AS metadata_json,
                   mba.source AS audience_source
            FROM memory_blocks mb
            JOIN memory_block_audience mba ON mba.block_id = mb.block_id
            LEFT JOIN memory_block_metadata mbm ON mbm.block_id = mb.block_id
            WHERE mb.user_id = ?
              AND mb.character_id = ?
              AND mb.visibility = 'public'
              AND mba.character_id = ?
            """,
            (SHARED_MEMORY_USER_ID, SHARED_MEMORY_CHARACTER_ID, character_id),
        )
        rows = cursor.fetchall()
        memory_blocks = []
        for row in rows:
            (
                block_id, timestamp, overview, overview_vector_blob,
                sparse_vector_json, raw_dialogues_json, is_consolidated,
                encounter_count, potential_preferences_json,
                metadata_json, audience_source,
            ) = row
            overview_vector = np.frombuffer(overview_vector_blob, dtype=np.float32).tolist()
            block = {
                "block_id": block_id,
                "timestamp": timestamp,
                "overview": overview,
                "overview_vector": overview_vector,
                "sparse_vector": json.loads(sparse_vector_json) if sparse_vector_json else {},
                "raw_dialogues": json.loads(raw_dialogues_json) if raw_dialogues_json else [],
                "is_consolidated": bool(is_consolidated),
                "encounter_count": float(encounter_count) if encounter_count is not None else 1.0,
                "potential_preferences": json.loads(potential_preferences_json) if potential_preferences_json else [],
                "shared_memory": True,
                "audience_source": audience_source or "",
                "metadata": json.loads(metadata_json) if metadata_json else {},
            }
            memory_blocks.append(block)
        conn.close()
        return memory_blocks

    def set_memory_block_audience(
        self,
        db_path,
        block_id: str,
        character_ids: list[str],
        *,
        source: str = "",
        metadata: dict | None = None,
    ) -> None:
        """設定 shared memory block 可見角色與 metadata。"""
        clean_ids = [str(cid).strip() for cid in character_ids if str(cid).strip()]
        clean_ids = list(dict.fromkeys(clean_ids))
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        try:
            cursor.execute("BEGIN")
            existing_ids = [
                row[0] for row in cursor.execute(
                    "SELECT character_id FROM memory_block_audience WHERE block_id = ?",
                    (block_id,),
                ).fetchall()
            ]
            for cid in list(dict.fromkeys(existing_ids + clean_ids)):
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO memory_block_audience (block_id, character_id, source, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (block_id, cid, source, now),
                )
            if metadata is not None:
                cursor.execute(
                    """
                    INSERT INTO memory_block_metadata (block_id, metadata_json, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(block_id) DO UPDATE SET
                        metadata_json=excluded.metadata_json,
                        updated_at=excluded.updated_at
                    """,
                    (block_id, json.dumps(metadata, ensure_ascii=False), now),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save_db(
        self,
        db_path,
        memory_blocks,
        user_id: str = "default",
        character_id: str = "default",
        visibility: str = "public",
    ):
        """寫回記憶區塊（scoped DELETE + INSERT）。

        ⚠️ 高風險操作：DELETE 限定 (user_id, character_id, visibility) 範圍，
        絕對不能移除 WHERE 條件，否則會清掉其他用戶的資料。
        """
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("BEGIN")
            cursor.execute(
                "DELETE FROM memory_blocks "
                "WHERE user_id = ? AND character_id = ? AND visibility = ?",
                (user_id, character_id, visibility)
            )

            for block in memory_blocks:
                vector_blob = np.array(block["overview_vector"], dtype=np.float32).tobytes()
                sparse_json = json.dumps(block.get("sparse_vector", {}), ensure_ascii=False)
                dialogues_json = json.dumps(block.get("raw_dialogues", []), ensure_ascii=False)
                is_cons = 1 if block.get("is_consolidated", False) else 0
                enc_count = float(block.get("encounter_count", 1.0))
                prefs_json = json.dumps(block.get("potential_preferences", []), ensure_ascii=False)
                cursor.execute(
                    "INSERT INTO memory_blocks "
                    "(block_id, timestamp, overview, overview_vector, sparse_vector, "
                    " raw_dialogues, is_consolidated, encounter_count, potential_preferences, "
                    " user_id, character_id, visibility) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (block["block_id"], block["timestamp"], block["overview"],
                     vector_blob, sparse_json, dialogues_json,
                     is_cons, enc_count, prefs_json,
                     user_id, character_id, visibility)
                )

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def delete_memory_block(
        self,
        db_path,
        user_id: str,
        character_id: str,
        visibility: str,
        block_id: str,
    ) -> int:
        """精準刪除單一情境記憶，限定完整隔離 scope。"""
        conn = self._init_db(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM memory_blocks "
            "WHERE block_id = ? AND user_id = ? AND character_id = ? AND visibility = ?",
            (block_id, user_id, character_id, visibility),
        )
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        return int(deleted if deleted is not None else 0)

    # ════════════════════════════════════════════════════════════


__all__ = ["MemoryBlockRepositoryMixin"]
