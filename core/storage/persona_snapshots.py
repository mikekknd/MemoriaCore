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


class PersonaSnapshotRepositoryMixin:
    # SECTION: 人格演化 Snapshots — 版本儲存 / 血統查詢 / 時間序列
    # ════════════════════════════════════════════════════════════

    def _init_persona_snapshot_db(self):
        """初始化人格演化 snapshot 資料表（PRAGMA user_version 驅動 schema migration）。

        版本歷史：
        - user_version == 0 — 空 DB 或舊 6 維 prototype → 重建為 v3。
        - user_version == 2 — Path D trait tree（無 persona_face）→ 遷移至 v3。
        - user_version == 3 — 正式雙 face schema（有 persona_face）。
        - 其他值 — 拒絕啟動。
        """
        conn = sqlite3.connect(self.persona_snapshot_db_path, timeout=15.0)
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA user_version")
        user_version = cur.fetchone()[0]

        if user_version == 0:
            try:
                cur.execute("BEGIN IMMEDIATE")
                cur.execute("PRAGMA user_version")
                if cur.fetchone()[0] == 0:
                    cur.execute("DROP TABLE IF EXISTS persona_dimensions")
                    cur.execute("DROP TABLE IF EXISTS persona_traits")
                    cur.execute("DROP TABLE IF EXISTS persona_snapshots")
                    self._create_persona_v3_schema(cur)
                    cur.execute("PRAGMA user_version = 3")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        elif user_version == 2:
            try:
                # FK 必須在 transaction 外關閉，否則 SQLite 忽略此 PRAGMA
                cur.execute("PRAGMA foreign_keys=OFF")
                cur.execute("BEGIN IMMEDIATE")
                cur.execute("PRAGMA user_version")
                if cur.fetchone()[0] == 2:
                    self._migrate_persona_v2_to_v3(cur)
                    cur.execute("PRAGMA user_version = 3")
                conn.commit()
                cur.execute("PRAGMA foreign_keys=ON")
            except Exception:
                conn.rollback()
                cur.execute("PRAGMA foreign_keys=ON")
                raise
        elif user_version == 3:
            # 健康 v3 DB 啟動不應該動寫鎖。僅當偵測到 persona_dimensions 缺失或
            # 舊版 v2→v3 migration 留下指向 _persona_snapshots_v2 的壞 FK 時，
            # 才進入修補流程（重建 child table 必須先關 FK）。
            cur.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='persona_dimensions'"
            )
            needs_repair = cur.fetchone() is None
            if not needs_repair:
                cur.execute("PRAGMA foreign_key_list(persona_dimensions)")
                fk_rows = cur.fetchall()
                needs_repair = not any(row[2] == "persona_snapshots" for row in fk_rows)
            if needs_repair:
                try:
                    cur.execute("PRAGMA foreign_keys=OFF")
                    cur.execute("BEGIN IMMEDIATE")
                    self._create_persona_v3_schema(cur)
                    conn.commit()
                    cur.execute("PRAGMA foreign_keys=ON")
                    print("[StorageManager] persona_snapshots.db: 偵測到壞 FK，已重建 v3 schema。")
                except Exception:
                    conn.rollback()
                    cur.execute("PRAGMA foreign_keys=ON")
                    raise
        else:
            raise RuntimeError(
                f"persona_snapshots.db 發現無法識別的 user_version={user_version}"
                f"（預期 0、2 或 3）— 拒絕啟動以防半毀 DB"
            )
        return conn

    def _migrate_persona_v2_to_v3(self, cur):
        """v2 → v3：為 persona_snapshots 和 persona_traits 加入 persona_face 欄位。

        persona_snapshots：rename + rebuild（UNIQUE 從 (character_id, version)
        改為 (character_id, persona_face, version)）。
        persona_traits：ALTER ADD COLUMN + drop/create unique index。
        """
        # persona_snapshots rebuild（persona_dimensions FK 指向 id，id 值不變，FK 依然有效）
        cur.execute("ALTER TABLE persona_snapshots RENAME TO _persona_snapshots_v2")
        cur.execute('''
            CREATE TABLE persona_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id TEXT NOT NULL,
                persona_face TEXT NOT NULL DEFAULT 'public',
                version INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                summary TEXT,
                evolved_prompt TEXT,
                UNIQUE(character_id, persona_face, version)
            )
        ''')
        cur.execute(
            "INSERT INTO persona_snapshots "
            "(id, character_id, persona_face, version, timestamp, summary, evolved_prompt) "
            "SELECT id, character_id, 'public', version, timestamp, summary, evolved_prompt "
            "FROM _persona_snapshots_v2"
        )

        # SQLite 會在 RENAME parent table 時同步改寫既有 FK。
        # 因此 persona_dimensions 的 FK 可能暫時指向 _persona_snapshots_v2，
        # 必須重建回 persona_snapshots，否則 drop 暫存表後寫入會失敗。
        self._repair_persona_dimensions_fk_if_needed(cur)
        cur.execute("DROP TABLE _persona_snapshots_v2")

        # persona_traits：加欄位 + 更換 unique index
        cur.execute(
            "ALTER TABLE persona_traits ADD COLUMN persona_face TEXT NOT NULL DEFAULT 'public'"
        )
        cur.execute("DROP INDEX IF EXISTS idx_trait_char_key")
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_trait_char_face_key "
            "ON persona_traits(character_id, persona_face, trait_key)"
        )

        # 更新 snapshot 相關 index
        cur.execute("DROP INDEX IF EXISTS idx_persona_snap_char_ver")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_persona_snap_char_ver "
            "ON persona_snapshots(character_id, persona_face, version DESC)"
        )

        # 重建 idx_trait_char_active 加入 persona_face 欄位
        cur.execute("DROP INDEX IF EXISTS idx_trait_char_active")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_trait_char_active "
            "ON persona_traits(character_id, persona_face, is_active, last_active_version DESC)"
        )

    def _create_persona_dimensions_table(self, cur):
        cur.execute('''
            CREATE TABLE IF NOT EXISTS persona_dimensions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                dimension_key TEXT NOT NULL,
                name TEXT NOT NULL,
                confidence REAL NOT NULL,
                confidence_label TEXT,
                description TEXT NOT NULL,
                parent_name TEXT,
                is_active INTEGER DEFAULT 1,
                FOREIGN KEY (snapshot_id) REFERENCES persona_snapshots(id) ON DELETE CASCADE
            )
        ''')

    def _repair_persona_dimensions_fk_if_needed(self, cur):
        """修復 v3 DB 中 persona_dimensions 指向舊暫存表的 FK。

        v2 → v3 migration 會 rename persona_snapshots；SQLite 可能把 child table
        FK 也改成指向暫存表 _persona_snapshots_v2。由於 SQLite 不支援 ALTER FK，
        只能重建 persona_dimensions 並搬回既有資料。
        """
        cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'persona_dimensions'"
        )
        if cur.fetchone() is None:
            self._create_persona_dimensions_table(cur)
            return

        cur.execute("PRAGMA foreign_key_list(persona_dimensions)")
        fk_rows = cur.fetchall()
        if any(row[2] == "persona_snapshots" for row in fk_rows):
            return

        cur.execute("DROP TABLE IF EXISTS _persona_dimensions_rebuild")
        cur.execute("ALTER TABLE persona_dimensions RENAME TO _persona_dimensions_rebuild")
        self._create_persona_dimensions_table(cur)
        cur.execute(
            "INSERT INTO persona_dimensions "
            "(id, snapshot_id, dimension_key, name, confidence, confidence_label, "
            " description, parent_name, is_active) "
            "SELECT id, snapshot_id, dimension_key, name, confidence, confidence_label, "
            "       description, parent_name, is_active "
            "FROM _persona_dimensions_rebuild"
        )
        cur.execute("DROP TABLE _persona_dimensions_rebuild")

    def _create_persona_v3_schema(self, cur):
        """Path D v3 schema：含 persona_face 的雙 face 架構。

        persona_dimensions.is_active 欄位永久寫 1（歷史 artefact，
        實際活躍狀態由 persona_traits.is_active 持有，讀取時 JOIN 取真值）。
        """
        cur.execute('''
            CREATE TABLE IF NOT EXISTS persona_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id TEXT NOT NULL,
                persona_face TEXT NOT NULL DEFAULT 'public',
                version INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                summary TEXT,
                evolved_prompt TEXT,
                UNIQUE(character_id, persona_face, version)
            )
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS persona_traits (
                trait_key TEXT PRIMARY KEY,
                character_id TEXT NOT NULL,
                persona_face TEXT NOT NULL DEFAULT 'public',
                name TEXT NOT NULL,
                created_version INTEGER NOT NULL,
                last_active_version INTEGER NOT NULL,
                parent_key TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY (parent_key) REFERENCES persona_traits(trait_key) ON DELETE SET NULL
            )
        ''')
        self._create_persona_dimensions_table(cur)
        self._repair_persona_dimensions_fk_if_needed(cur)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_persona_dim_snapshot "
            "ON persona_dimensions(snapshot_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_persona_snap_char_ver "
            "ON persona_snapshots(character_id, persona_face, version DESC)"
        )
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_trait_char_face_key "
            "ON persona_traits(character_id, persona_face, trait_key)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_trait_char_active "
            "ON persona_traits(character_id, persona_face, is_active, last_active_version DESC)"
        )

    def get_next_persona_version(
        self,
        character_id: str,
        persona_face: str = "public",
    ) -> int:
        """回傳該角色 + face 下一個應使用的 version 號；無紀錄則為 1。"""
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) FROM persona_snapshots "
                "WHERE character_id = ? AND persona_face = ?",
                (character_id, persona_face),
            )
            row = cur.fetchone()
            return int(row[0]) + 1 if row else 1
        finally:
            conn.close()

    def _row_to_snapshot(self, row, dimensions):
        return {
            "id": row[0],
            "character_id": row[1],
            "persona_face": row[2],
            "version": row[3],
            "timestamp": row[4],
            "summary": row[5],
            "evolved_prompt": row[6],
            "dimensions": dimensions,
        }

    def _load_dimensions_for(
        self,
        cursor,
        snapshot_id: int,
        character_id: str | None = None,
        version: int | None = None,
        persona_face: str | None = None,
    ) -> list:
        """讀指定 snapshot 的所有維度明細；is_active / parent_key 來自 persona_traits。

        當傳入 character_id + version 時，額外補入「存在於 persona_traits
        但此版 snapshot 沒有 dimension 記錄」的 trait，以最近已知 confidence 顯示。
        persona_face 用於過濾補充查詢；None 表示不限（向後相容）。
        """
        cursor.execute(
            "SELECT d.dimension_key, d.name, d.confidence, d.confidence_label, "
            "       d.description, d.parent_name, "
            "       COALESCE(t.is_active, 1) AS is_active, "
            "       t.parent_key "
            "FROM persona_dimensions d "
            "LEFT JOIN persona_traits t ON t.trait_key = d.dimension_key "
            "WHERE d.snapshot_id = ? ORDER BY d.id",
            (snapshot_id,),
        )
        result = [
            {
                "dimension_key": r[0],
                "name": r[1],
                "confidence": float(r[2]),
                "confidence_label": r[3],
                "description": r[4],
                "parent_name": r[5],
                "is_active": bool(r[6]),
                "parent_key": r[7],
            }
            for r in cursor.fetchall()
        ]

        if character_id is None or version is None:
            return result

        # 補入此版 snapshot 沒有 dimension 記錄的歷史 trait
        have_keys = {item["dimension_key"] for item in result}

        # 找出這版有 bump last_active_version 但沒寫 dim row 的 trait
        # （即 confidence="none" 的 update），這些不應被補入歷史值
        if persona_face is not None:
            cursor.execute(
                "SELECT trait_key FROM persona_traits "
                "WHERE character_id = ? AND persona_face = ? AND last_active_version = ?",
                (character_id, persona_face, version),
            )
        else:
            cursor.execute(
                "SELECT trait_key FROM persona_traits "
                "WHERE character_id = ? AND last_active_version = ?",
                (character_id, version),
            )
        visited_none_keys = {r[0] for r in cursor.fetchall()} - have_keys

        if persona_face is not None:
            cursor.execute(
                "SELECT t.trait_key, t.name, t.is_active, t.parent_key "
                "FROM persona_traits t "
                "WHERE t.character_id = ? AND t.persona_face = ? AND t.created_version <= ?",
                (character_id, persona_face, version),
            )
        else:
            cursor.execute(
                "SELECT t.trait_key, t.name, t.is_active, t.parent_key "
                "FROM persona_traits t "
                "WHERE t.character_id = ? AND t.created_version <= ?",
                (character_id, version),
            )
        missing = [
            r for r in cursor.fetchall()
            if r[0] not in have_keys and r[0] not in visited_none_keys
        ]
        for r in missing:
            trait_key = r[0]
            if persona_face is not None:
                cursor.execute(
                    "SELECT pd.confidence, pd.confidence_label, pd.description, pd.parent_name "
                    "FROM persona_dimensions pd "
                    "JOIN persona_snapshots ps ON ps.id = pd.snapshot_id "
                    "WHERE pd.dimension_key = ? AND ps.character_id = ? "
                    "  AND ps.persona_face = ? AND ps.version <= ? "
                    "ORDER BY ps.version DESC LIMIT 1",
                    (trait_key, character_id, persona_face, version),
                )
            else:
                cursor.execute(
                    "SELECT pd.confidence, pd.confidence_label, pd.description, pd.parent_name "
                    "FROM persona_dimensions pd "
                    "JOIN persona_snapshots ps ON ps.id = pd.snapshot_id "
                    "WHERE pd.dimension_key = ? AND ps.character_id = ? AND ps.version <= ? "
                    "ORDER BY ps.version DESC LIMIT 1",
                    (trait_key, character_id, version),
                )
            last = cursor.fetchone()
            result.append({
                "dimension_key": trait_key,
                "name": r[1],
                "confidence": float(last[0]) if last else 0.0,
                "confidence_label": last[1] if last else "none",
                "description": last[2] if last else "",
                "parent_name": last[3] if last else None,
                "is_active": bool(r[2]),
                "parent_key": r[3],
            })

        return result

    def get_latest_persona_snapshot(
        self,
        character_id: str,
        persona_face: str = "public",
    ) -> dict | None:
        """回傳該角色 + face 最新一筆 snapshot（含 dimensions）；無紀錄回 None。"""
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, character_id, persona_face, version, timestamp, summary, evolved_prompt "
                "FROM persona_snapshots WHERE character_id = ? AND persona_face = ? "
                "ORDER BY version DESC LIMIT 1",
                (character_id, persona_face),
            )
            row = cur.fetchone()
            if not row:
                return None
            dims = self._load_dimensions_for(cur, row[0], row[1], row[3], row[2])
            return self._row_to_snapshot(row, dims)
        finally:
            conn.close()

    def get_persona_snapshot(
        self,
        character_id: str,
        version: int,
        persona_face: str = "public",
    ) -> dict | None:
        """回傳指定版本的 snapshot（含 dimensions）；找不到回 None。"""
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, character_id, persona_face, version, timestamp, summary, evolved_prompt "
                "FROM persona_snapshots "
                "WHERE character_id = ? AND persona_face = ? AND version = ?",
                (character_id, persona_face, version),
            )
            row = cur.fetchone()
            if not row:
                return None
            dims = self._load_dimensions_for(cur, row[0], row[1], row[3], row[2])
            return self._row_to_snapshot(row, dims)
        finally:
            conn.close()

    def list_persona_snapshots(
        self,
        character_id: str,
        persona_face: str = "public",
    ) -> list:
        """回傳該角色 + face 所有 snapshot 的摘要（不含 dimensions 內容），版本遞增。"""
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT s.id, s.version, s.timestamp, s.summary, "
                "       (SELECT COUNT(*) FROM persona_dimensions d WHERE d.snapshot_id = s.id) "
                "FROM persona_snapshots s "
                "WHERE s.character_id = ? AND s.persona_face = ? "
                "ORDER BY s.version ASC",
                (character_id, persona_face),
            )
            return [
                {
                    "id": r[0],
                    "version": r[1],
                    "timestamp": r[2],
                    "summary": r[3],
                    "dimensions_count": int(r[4]),
                }
                for r in cur.fetchall()
            ]
        finally:
            conn.close()

    def delete_persona_snapshots_by_character(
        self,
        character_id: str,
        persona_face: str | None = None,
    ) -> int:
        """清空指定角色的 snapshot（含 dimensions，靠 CASCADE）。

        persona_face=None → 刪除所有 face；指定 face → 只刪該 face。
        回傳刪除列數。
        """
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            if persona_face is None:
                cur.execute(
                    "DELETE FROM persona_snapshots WHERE character_id = ?",
                    (character_id,),
                )
            else:
                cur.execute(
                    "DELETE FROM persona_snapshots WHERE character_id = ? AND persona_face = ?",
                    (character_id, persona_face),
                )
            deleted = cur.rowcount
            conn.commit()
            return int(deleted or 0)
        finally:
            conn.close()

    def save_trait_snapshot(
        self,
        character_id: str,
        timestamp: str,
        summary: str,
        evolved_prompt: str,
        updates: list,
        new_traits: list,
        persona_face: str = "public",
        dormancy_idle_versions: int = 3,
        dormancy_confidence_threshold: float = 5.0,
    ) -> int:
        """Path D 原子寫入：一筆 snapshot + updates/new_traits 同交易 + 尾端 B' sweep。

        updates 每筆格式（對既有 trait）::
            {
                "trait_key": str,
                "name": str,
                "description": str,
                "confidence": float,        # 0.0~10.0
                "confidence_label": str,    # high/medium/low/none
                "parent_name": str | None,
            }
          confidence_label=="none" 時不寫 persona_dimensions 列，但仍 bump last_active_version。

        new_traits 每筆格式（新建 trait）::
            {
                "trait_key": str,           # uuid4().hex，由呼叫端生成
                "name": str,
                "description": str,
                "confidence": float,
                "confidence_label": str,
                "parent_key": str | None,
                "parent_name": str | None,
            }

        B' 休眠規則（同交易尾端 sweep）：
          (current_version - last_active_version) >= dormancy_idle_versions
          AND 最近一次 confidence <= dormancy_confidence_threshold → is_active=0。
          本輪觸及的 trait 不受影響（last_active_version==current_version，差值 0）。

        回傳：snapshot_id。
        """
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")

            # 版本號在寫鎖內計算，避免併發 sync 搶同一版本
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) FROM persona_snapshots "
                "WHERE character_id = ? AND persona_face = ?",
                (character_id, persona_face),
            )
            current_version = int(cur.fetchone()[0]) + 1

            cur.execute(
                "INSERT INTO persona_snapshots "
                "(character_id, persona_face, version, timestamp, summary, evolved_prompt) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (character_id, persona_face, current_version, timestamp, summary, evolved_prompt),
            )
            sid = cur.lastrowid

            # ── updates：既有 trait 的 confidence 變動 ──
            for u in updates:
                trait_key = str(u["trait_key"])
                cur.execute(
                    "UPDATE persona_traits "
                    "SET last_active_version = ?, is_active = 1 "
                    "WHERE trait_key = ? AND character_id = ? AND persona_face = ?",
                    (current_version, trait_key, character_id, persona_face),
                )
                if u.get("confidence_label") != "none":
                    cur.execute(
                        "INSERT INTO persona_dimensions "
                        "(snapshot_id, dimension_key, name, confidence, "
                        " confidence_label, description, parent_name, is_active) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                        (
                            sid,
                            trait_key,
                            str(u["name"]),
                            float(u["confidence"]),
                            u.get("confidence_label"),
                            str(u.get("description", "")),
                            u.get("parent_name"),
                        ),
                    )

            # ── new_traits：本版新建 trait（INSERT 血統表 + 明細表） ──
            for n in new_traits:
                trait_key = str(n["trait_key"])
                parent_key = n.get("parent_key")
                cur.execute(
                    "INSERT INTO persona_traits "
                    "(trait_key, character_id, persona_face, name, "
                    " created_version, last_active_version, parent_key, is_active, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
                    (
                        trait_key,
                        character_id,
                        persona_face,
                        str(n["name"]),
                        current_version,
                        current_version,
                        parent_key,
                        timestamp,
                    ),
                )
                cur.execute(
                    "INSERT INTO persona_dimensions "
                    "(snapshot_id, dimension_key, name, confidence, "
                    " confidence_label, description, parent_name, is_active) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                    (
                        sid,
                        trait_key,
                        str(n["name"]),
                        float(n["confidence"]),
                        n.get("confidence_label"),
                        str(n.get("description", "")),
                        n.get("parent_name"),
                    ),
                )
                # 被引用為 parent 的 trait 自動 reactivate + bump
                if parent_key:
                    cur.execute(
                        "UPDATE persona_traits "
                        "SET last_active_version = ?, is_active = 1 "
                        "WHERE trait_key = ? AND character_id = ? AND persona_face = ?",
                        (current_version, parent_key, character_id, persona_face),
                    )

            # ── B' sweep：同交易尾端掃描休眠候選（限定 persona_face 範圍）──
            cur.execute(
                "SELECT t.trait_key FROM persona_traits t "
                "WHERE t.character_id = ? AND t.persona_face = ? AND t.is_active = 1 "
                "  AND (? - t.last_active_version) >= ? "
                "  AND COALESCE(("
                "    SELECT d.confidence FROM persona_dimensions d "
                "    JOIN persona_snapshots s ON s.id = d.snapshot_id "
                "    WHERE s.character_id = ? AND s.persona_face = ? "
                "          AND d.dimension_key = t.trait_key "
                "    ORDER BY s.version DESC LIMIT 1"
                "  ), 0.0) <= ?",
                (
                    character_id, persona_face,
                    current_version,
                    dormancy_idle_versions,
                    character_id, persona_face,
                    dormancy_confidence_threshold,
                ),
            )
            dormant_keys = [r[0] for r in cur.fetchall()]
            for tk in dormant_keys:
                cur.execute(
                    "UPDATE persona_traits SET is_active = 0 "
                    "WHERE trait_key = ? AND character_id = ? AND persona_face = ?",
                    (tk, character_id, persona_face),
                )

            conn.commit()
            return sid
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_active_traits(
        self,
        character_id: str,
        persona_face: str = "public",
        limit: int | None = None,
    ) -> list:
        """回傳該角色 + face 當前活躍 trait（is_active=1）清單，按 last_active_version DESC。"""
        return self._get_traits(character_id, persona_face=persona_face, active_only=True, limit=limit)

    def get_all_traits(
        self,
        character_id: str,
        persona_face: str = "public",
        limit: int | None = None,
    ) -> list:
        """回傳該角色 + face 所有 trait（含已休眠）清單。"""
        return self._get_traits(character_id, persona_face=persona_face, active_only=False, limit=limit)

    def _get_traits(
        self,
        character_id: str,
        persona_face: str = "public",
        active_only: bool = True,
        limit: int | None = None,
    ) -> list:
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            sql = (
                "SELECT "
                "  t.trait_key, t.name, t.created_version, t.last_active_version, "
                "  t.parent_key, t.is_active, "
                "  COALESCE(("
                "    SELECT d.description FROM persona_dimensions d "
                "    JOIN persona_snapshots s ON s.id = d.snapshot_id "
                "    WHERE s.character_id = t.character_id AND s.persona_face = t.persona_face "
                "          AND d.dimension_key = t.trait_key "
                "    ORDER BY s.version DESC LIMIT 1"
                "  ), '') AS last_description "
                "FROM persona_traits t "
                "WHERE t.character_id = ? AND t.persona_face = ?"
            )
            if active_only:
                sql += " AND t.is_active = 1"
            sql += " ORDER BY t.last_active_version DESC"

            params: tuple = (character_id, persona_face)
            if limit is not None:
                sql += " LIMIT ?"
                params = (character_id, persona_face, int(limit))
            cur.execute(sql, params)
            return [
                {
                    "trait_key": r[0],
                    "name": r[1],
                    "created_version": int(r[2]),
                    "last_active_version": int(r[3]),
                    "parent_key": r[4],
                    "is_active": bool(r[5]),
                    "last_description": r[6],
                }
                for r in cur.fetchall()
            ]
        finally:
            conn.close()

    def get_trait_timeline(
        self,
        character_id: str,
        trait_key: str,
        persona_face: str = "public",
    ) -> list:
        """回傳指定 trait 在所有版本的 confidence 變化序列（折線圖用）。

        confidence 為 none 的版本因不寫 persona_dimensions 列，在此序列中會缺席。
        """
        conn = self._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT s.version, s.timestamp, d.confidence, d.confidence_label "
                "FROM persona_snapshots s "
                "JOIN persona_dimensions d ON d.snapshot_id = s.id "
                "WHERE s.character_id = ? AND s.persona_face = ? AND d.dimension_key = ? "
                "ORDER BY s.version ASC",
                (character_id, persona_face, trait_key),
            )
            return [
                {
                    "version": int(r[0]),
                    "timestamp": r[1],
                    "confidence": float(r[2]),
                    "confidence_label": r[3],
                }
                for r in cur.fetchall()
            ]
        finally:
            conn.close()


__all__ = ["PersonaSnapshotRepositoryMixin"]
