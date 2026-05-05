from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any

import storage_mappers as mappers
from storage_constants import DEFAULT_CONNECTOR_ID, DEFAULT_CONNECTOR_NAME
from storage_event_utils import infer_super_chat_tier


class TopicPackRepositoryMixin:
    @classmethod
    def _row_to_topic_pack(cls, row: sqlite3.Row | None) -> dict | None:
        return mappers.row_to_topic_pack(row)

    @classmethod
    def _row_to_topic_pack_entry(cls, row: sqlite3.Row | None) -> dict | None:
        return mappers.row_to_topic_pack_entry(row)

    @classmethod
    def _row_to_topic_pack_entry_embedding(cls, row: sqlite3.Row | None) -> dict | None:
        return mappers.row_to_topic_pack_entry_embedding(row)

    def create_topic_pack(self, data: dict[str, Any]) -> dict:
        now = datetime.now().isoformat()
        title = str(data.get("title", "") or "").strip()
        if not title:
            raise ValueError("topic pack title 不可為空")
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO topic_packs (title, description, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (title[:200], str(data.get("description", "") or "")[:1000], now, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM topic_packs WHERE id = ?", (cursor.lastrowid,)).fetchone()
        pack = self._row_to_topic_pack(row)
        if not pack:
            raise RuntimeError("topic pack 建立失敗")
        return pack

    def update_topic_pack(self, pack_id: int, data: dict[str, Any]) -> dict:
        now = datetime.now().isoformat()
        title = str(data.get("title", "") or "").strip()
        if not title:
            raise ValueError("topic pack title 不可為空")
        description = str(data.get("description", "") or "").strip()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE topic_packs
                SET title = ?, description = ?, updated_at = ?
                WHERE id = ?
                """,
                (title[:200], description[:1000], now, int(pack_id)),
            )
            if int(cursor.rowcount or 0) <= 0:
                raise ValueError("topic pack 不存在")
            conn.commit()
            row = conn.execute("SELECT * FROM topic_packs WHERE id = ?", (int(pack_id),)).fetchone()
        pack = self._row_to_topic_pack(row)
        if not pack:
            raise RuntimeError("topic pack 更新失敗")
        return pack

    def list_topic_packs(self, *, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit or 100), 500))
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM topic_packs ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [pack for row in rows if (pack := self._row_to_topic_pack(row))]

    def get_topic_pack(self, pack_id: int) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM topic_packs WHERE id = ?", (int(pack_id),)).fetchone()
        return self._row_to_topic_pack(row)

    def create_topic_pack_entry(self, pack_id: int, data: dict[str, Any]) -> dict:
        now = datetime.now().isoformat()
        if not self.get_topic_pack(int(pack_id)):
            raise ValueError("topic pack 不存在")
        title = str(data.get("title", "") or "").strip()
        body = str(data.get("body", "") or "").strip()
        if not title or not body:
            raise ValueError("topic pack entry 需要 title 與 body")
        tags = data.get("tags") if isinstance(data.get("tags"), list) else []
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO topic_pack_entries (
                    pack_id, title, body, source_url, source_type, tags_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(pack_id),
                    title[:200],
                    body[:4000],
                    str(data.get("source_url", "") or "")[:1000],
                    str(data.get("source_type", "manual") or "manual")[:80],
                    self._json_dump([str(tag).strip() for tag in tags if str(tag).strip()]),
                    now,
                ),
            )
            conn.execute("UPDATE topic_packs SET updated_at = ? WHERE id = ?", (now, int(pack_id)))
            conn.commit()
            row = conn.execute("SELECT * FROM topic_pack_entries WHERE id = ?", (cursor.lastrowid,)).fetchone()
        entry = self._row_to_topic_pack_entry(row)
        if not entry:
            raise RuntimeError("topic pack entry 建立失敗")
        return entry

    def update_topic_pack_entry(self, entry_id: int, data: dict[str, Any]) -> dict:
        existing = self.get_topic_pack_entry(int(entry_id))
        if not existing:
            raise ValueError("topic pack entry 不存在")
        title = str(data.get("title", "") or "").strip()
        body = str(data.get("body", "") or "").strip()
        if not title or not body:
            raise ValueError("topic pack entry 需要 title 與 body")
        tags = data.get("tags") if isinstance(data.get("tags"), list) else []
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE topic_pack_entries
                SET title = ?, body = ?, source_url = ?, source_type = ?, tags_json = ?
                WHERE id = ?
                """,
                (
                    title[:200],
                    body[:4000],
                    str(data.get("source_url", "") or "")[:1000],
                    str(data.get("source_type", "manual") or "manual")[:80],
                    self._json_dump([str(tag).strip() for tag in tags if str(tag).strip()]),
                    int(entry_id),
                ),
            )
            if int(cursor.rowcount or 0) <= 0:
                raise ValueError("topic pack entry 不存在")
            conn.execute("DELETE FROM topic_pack_entry_embeddings WHERE entry_id = ?", (int(entry_id),))
            conn.execute("UPDATE topic_packs SET updated_at = ? WHERE id = ?", (now, int(existing["pack_id"])))
            conn.commit()
            row = conn.execute("SELECT * FROM topic_pack_entries WHERE id = ?", (int(entry_id),)).fetchone()
        entry = self._row_to_topic_pack_entry(row)
        if not entry:
            raise RuntimeError("topic pack entry 更新失敗")
        return entry

    def delete_topic_pack_entry(self, entry_id: int) -> bool:
        existing = self.get_topic_pack_entry(int(entry_id))
        if not existing:
            return False
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM topic_pack_entry_embeddings WHERE entry_id = ?", (int(entry_id),))
            conn.execute("DELETE FROM topic_pack_entry_usages WHERE entry_id = ?", (int(entry_id),))
            conn.execute("UPDATE research_requests SET result_entry_id = NULL WHERE result_entry_id = ?", (int(entry_id),))
            cursor = conn.execute("DELETE FROM topic_pack_entries WHERE id = ?", (int(entry_id),))
            conn.execute("UPDATE topic_packs SET updated_at = ? WHERE id = ?", (now, int(existing["pack_id"])))
            conn.commit()
        return int(cursor.rowcount or 0) > 0

    def delete_topic_pack(self, pack_id: int) -> dict[str, Any]:
        pack_id = int(pack_id)
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT id FROM topic_packs WHERE id = ?", (pack_id,)).fetchone()
            if not row:
                return {"deleted": False, "pack_id": pack_id, "entry_count": 0}
            entry_rows = conn.execute(
                "SELECT id FROM topic_pack_entries WHERE pack_id = ?",
                (pack_id,),
            ).fetchall()
            entry_ids = [int(item["id"]) for item in entry_rows]
            entry_count = len(entry_ids)
            if entry_ids:
                placeholders = ",".join("?" for _ in entry_ids)
                conn.execute(
                    f"UPDATE research_requests SET result_entry_id = NULL WHERE result_entry_id IN ({placeholders})",
                    entry_ids,
                )
                conn.execute(
                    f"DELETE FROM topic_pack_entry_embeddings WHERE entry_id IN ({placeholders})",
                    entry_ids,
                )
                conn.execute(
                    f"DELETE FROM topic_pack_entry_usages WHERE entry_id IN ({placeholders})",
                    entry_ids,
                )
            conn.execute("DELETE FROM topic_pack_entry_embeddings WHERE pack_id = ?", (pack_id,))
            conn.execute("DELETE FROM topic_pack_entry_usages WHERE pack_id = ?", (pack_id,))
            conn.execute("DELETE FROM live_session_topic_packs WHERE pack_id = ?", (pack_id,))
            conn.execute("DELETE FROM topic_pack_entries WHERE pack_id = ?", (pack_id,))
            cursor = conn.execute("DELETE FROM topic_packs WHERE id = ?", (pack_id,))
            conn.commit()
        return {
            "deleted": int(cursor.rowcount or 0) > 0,
            "pack_id": pack_id,
            "entry_count": entry_count,
        }

    def delete_all_topic_packs(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            pack_row = conn.execute("SELECT COUNT(*) AS count FROM topic_packs").fetchone()
            entry_row = conn.execute("SELECT COUNT(*) AS count FROM topic_pack_entries").fetchone()
            pack_count = int(pack_row["count"] or 0) if pack_row else 0
            entry_count = int(entry_row["count"] or 0) if entry_row else 0
            conn.execute(
                """
                UPDATE research_requests
                SET result_entry_id = NULL
                WHERE result_entry_id IN (SELECT id FROM topic_pack_entries)
                """
            )
            conn.execute("DELETE FROM topic_pack_entry_embeddings")
            conn.execute("DELETE FROM topic_pack_entry_usages")
            conn.execute("DELETE FROM live_session_topic_packs")
            conn.execute("DELETE FROM topic_pack_entries")
            conn.execute("DELETE FROM topic_packs")
            conn.commit()
        return {
            "deleted": pack_count > 0,
            "pack_count": pack_count,
            "entry_count": entry_count,
        }

    def list_topic_pack_entries(self, pack_id: int, *, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit or 100), 500))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM topic_pack_entries WHERE pack_id = ? ORDER BY id DESC LIMIT ?",
                (int(pack_id), limit),
            ).fetchall()
        entries = [entry for row in rows if (entry := self._row_to_topic_pack_entry(row))]
        entries.reverse()
        return entries

    def get_topic_pack_entry(self, entry_id: int) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM topic_pack_entries WHERE id = ?", (int(entry_id),)).fetchone()
        return self._row_to_topic_pack_entry(row)

    def upsert_topic_pack_entry_embedding(
        self,
        entry_id: int,
        embedding: list[float],
        *,
        model: str,
        content_hash: str = "",
    ) -> dict:
        entry = self.get_topic_pack_entry(int(entry_id))
        if not entry:
            raise ValueError("topic pack entry 不存在")
        vector = [float(value) for value in embedding if isinstance(value, int | float)]
        if not vector:
            raise ValueError("embedding 不可為空")
        now = datetime.now().isoformat()
        existing = self.get_topic_pack_entry_embedding(int(entry_id))
        created_at = existing["created_at"] if existing else now
        content_hash = content_hash or self.topic_entry_content_hash(entry)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO topic_pack_entry_embeddings (
                    entry_id, pack_id, embedding_model, embedding_dim, embedding_blob,
                    content_hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entry_id) DO UPDATE SET
                    pack_id=excluded.pack_id,
                    embedding_model=excluded.embedding_model,
                    embedding_dim=excluded.embedding_dim,
                    embedding_blob=excluded.embedding_blob,
                    content_hash=excluded.content_hash,
                    updated_at=excluded.updated_at
                """,
                (
                    int(entry_id),
                    int(entry["pack_id"]),
                    str(model or "unknown")[:120],
                    len(vector),
                    self._vector_to_blob(vector),
                    content_hash,
                    created_at,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM topic_pack_entry_embeddings WHERE entry_id = ?",
                (int(entry_id),),
            ).fetchone()
        saved = self._row_to_topic_pack_entry_embedding(row)
        if not saved:
            raise RuntimeError("topic pack entry embedding 儲存失敗")
        return saved

    def get_topic_pack_entry_embedding(self, entry_id: int) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM topic_pack_entry_embeddings WHERE entry_id = ?",
                (int(entry_id),),
            ).fetchone()
        return self._row_to_topic_pack_entry_embedding(row)

    def list_topic_pack_entries_missing_embeddings(self, pack_id: int, *, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit or 100), 500))
        return [
            entry for entry in self.list_topic_pack_entries(pack_id, limit=limit)
            if not (embedding := self.get_topic_pack_entry_embedding(entry["id"]))
            or embedding.get("content_hash") != self.topic_entry_content_hash(entry)
        ]

    def search_session_topic_pack_entries(
        self,
        session_id: str,
        query_embedding: list[float],
        *,
        limit: int = 6,
        min_score: float = 0.0,
    ) -> list[dict]:
        limit = max(1, min(int(limit or 6), 50))
        query_vector = [float(value) for value in query_embedding if isinstance(value, int | float)]
        if not query_vector:
            return []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.*, p.title AS pack_title, emb.embedding_model, emb.embedding_dim,
                       emb.embedding_blob, emb.content_hash AS embedding_content_hash,
                       emb.updated_at AS embedding_updated_at
                FROM live_session_topic_packs sp
                JOIN topic_packs p ON p.id = sp.pack_id
                JOIN topic_pack_entries e ON e.pack_id = p.id
                JOIN topic_pack_entry_embeddings emb ON emb.entry_id = e.id
                WHERE sp.session_id = ?
                """,
                (session_id,),
            ).fetchall()
        scored: list[dict] = []
        for row in rows:
            entry = self._row_to_topic_pack_entry(row)
            if not entry:
                continue
            vector = self._blob_to_vector(row["embedding_blob"], int(row["embedding_dim"] or 0))
            score = self._cosine_similarity(query_vector, vector)
            if score < float(min_score or 0.0):
                continue
            entry.update({
                "similarity": score,
                "embedding_model": row["embedding_model"] or "",
                "embedding_content_hash": row["embedding_content_hash"] or "",
                "embedding_updated_at": row["embedding_updated_at"] or "",
            })
            scored.append(entry)
        scored.sort(key=lambda item: item.get("similarity", 0.0), reverse=True)
        return scored[:limit]

    def search_topic_pack_entries(
        self,
        pack_id: int,
        query_embedding: list[float],
        *,
        limit: int = 6,
        min_score: float = 0.0,
    ) -> list[dict]:
        limit = max(1, min(int(limit or 6), 50))
        query_vector = [float(value) for value in query_embedding if isinstance(value, int | float)]
        if not query_vector:
            return []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.*, p.title AS pack_title, emb.embedding_model, emb.embedding_dim,
                       emb.embedding_blob, emb.content_hash AS embedding_content_hash,
                       emb.updated_at AS embedding_updated_at
                FROM topic_packs p
                JOIN topic_pack_entries e ON e.pack_id = p.id
                JOIN topic_pack_entry_embeddings emb ON emb.entry_id = e.id
                WHERE p.id = ?
                """,
                (int(pack_id),),
            ).fetchall()
        scored: list[dict] = []
        for row in rows:
            entry = self._row_to_topic_pack_entry(row)
            if not entry:
                continue
            vector = self._blob_to_vector(row["embedding_blob"], int(row["embedding_dim"] or 0))
            score = self._cosine_similarity(query_vector, vector)
            if score < float(min_score or 0.0):
                continue
            entry.update({
                "similarity": score,
                "embedding_model": row["embedding_model"] or "",
                "embedding_content_hash": row["embedding_content_hash"] or "",
                "embedding_updated_at": row["embedding_updated_at"] or "",
            })
            scored.append(entry)
        scored.sort(key=lambda item: item.get("similarity", 0.0), reverse=True)
        return scored[:limit]

    def record_topic_pack_entry_usages(
        self,
        session_id: str,
        entries: list[dict[str, Any]],
        *,
        query_text: str = "",
        usage_source: str = "external_context",
        interaction_id: str | int | None = None,
    ) -> list[dict[str, Any]]:
        now = datetime.now().isoformat()
        clean_session_id = str(session_id or "").strip()
        if not clean_session_id or not entries:
            return []
        clean_source = str(usage_source or "external_context").strip()[:80] or "external_context"
        clean_query = str(query_text or "").replace("\r", " ").replace("\n", " ").strip()[:1000]
        clean_interaction_id = str(interaction_id or "").strip()[:120]
        rows: list[tuple[str, int, int, str, float, str, str, str]] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            try:
                entry_id = int(item.get("entry_id") or item.get("id") or 0)
                pack_id = int(item.get("pack_id") or 0)
            except (TypeError, ValueError):
                continue
            if entry_id <= 0:
                continue
            if pack_id <= 0:
                entry = self.get_topic_pack_entry(entry_id)
                if not entry:
                    continue
                pack_id = int(entry["pack_id"])
            try:
                similarity = float(item.get("similarity") or 0.0)
            except (TypeError, ValueError):
                similarity = 0.0
            rows.append((
                clean_session_id,
                entry_id,
                pack_id,
                clean_query,
                similarity,
                clean_source,
                clean_interaction_id,
                now,
            ))
        if not rows:
            return []
        with self._lock, self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO topic_pack_entry_usages (
                    session_id, entry_id, pack_id, query_text, similarity,
                    usage_source, interaction_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
        return [
            {
                "session_id": row[0],
                "entry_id": row[1],
                "pack_id": row[2],
                "query_text": row[3],
                "similarity": row[4],
                "usage_source": row[5],
                "interaction_id": row[6],
                "created_at": row[7],
            }
            for row in rows
        ]

    def get_topic_pack_usage_stats(
        self,
        session_id: str,
        *,
        recent_limit: int = 8,
        low_unused_threshold: int = 3,
        repeat_threshold: int = 3,
    ) -> dict[str, Any]:
        recent_limit = max(1, min(int(recent_limit or 8), 100))
        low_unused_threshold = max(0, int(low_unused_threshold or 0))
        repeat_threshold = max(1, int(repeat_threshold or 1))
        entries = self.list_session_topic_pack_entries(session_id, limit=500)
        usage_by_entry: dict[int, dict[str, Any]] = {}
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT entry_id,
                       COUNT(*) AS usage_count,
                       AVG(similarity) AS avg_similarity,
                       MAX(created_at) AS last_used_at
                FROM topic_pack_entry_usages
                WHERE session_id = ?
                GROUP BY entry_id
                """,
                (session_id,),
            ).fetchall()
            source_rows = conn.execute(
                """
                SELECT entry_id, usage_source
                FROM topic_pack_entry_usages
                WHERE session_id = ?
                ORDER BY created_at ASC
                """,
                (session_id,),
            ).fetchall()
            recent_rows = conn.execute(
                """
                SELECT session_id, entry_id, pack_id, query_text, similarity,
                       usage_source, interaction_id, created_at
                FROM topic_pack_entry_usages
                WHERE session_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (session_id, recent_limit),
            ).fetchall()
        for row in rows:
            usage_by_entry[int(row["entry_id"])] = {
                "usage_count": int(row["usage_count"] or 0),
                "avg_similarity": float(row["avg_similarity"] or 0.0),
                "last_used_at": row["last_used_at"] or "",
                "usage_sources": [],
            }
        for row in source_rows:
            entry_id = int(row["entry_id"])
            usage = usage_by_entry.setdefault(
                entry_id,
                {"usage_count": 0, "avg_similarity": 0.0, "last_used_at": "", "usage_sources": []},
            )
            source = str(row["usage_source"] or "").strip()
            if source and source not in usage["usage_sources"]:
                usage["usage_sources"].append(source)

        stats_entries: list[dict[str, Any]] = []
        for entry in entries:
            entry_id = int(entry["id"])
            usage = usage_by_entry.get(
                entry_id,
                {"usage_count": 0, "avg_similarity": 0.0, "last_used_at": "", "usage_sources": []},
            )
            stats_entries.append({
                "entry_id": entry_id,
                "pack_id": int(entry["pack_id"]),
                "title": entry.get("title", ""),
                "source_type": entry.get("source_type", ""),
                "usage_count": int(usage.get("usage_count") or 0),
                "avg_similarity": float(usage.get("avg_similarity") or 0.0),
                "last_used_at": str(usage.get("last_used_at") or ""),
                "usage_sources": list(usage.get("usage_sources") or []),
            })

        recent_usage = [
            {
                "session_id": row["session_id"],
                "entry_id": int(row["entry_id"]),
                "pack_id": int(row["pack_id"]),
                "query_text": row["query_text"] or "",
                "similarity": float(row["similarity"] or 0.0),
                "usage_source": row["usage_source"] or "",
                "interaction_id": row["interaction_id"] or "",
                "created_at": row["created_at"],
            }
            for row in recent_rows
        ]
        recent_counts: dict[int, int] = {}
        for item in recent_usage:
            entry_id = int(item["entry_id"])
            recent_counts[entry_id] = recent_counts.get(entry_id, 0) + 1
        repeated_entry = None
        for entry_id, count in sorted(recent_counts.items(), key=lambda pair: pair[1], reverse=True):
            if count < repeat_threshold:
                continue
            entry = next((item for item in stats_entries if item["entry_id"] == entry_id), None)
            repeated_entry = {
                "entry_id": entry_id,
                "recent_count": count,
                "title": entry.get("title", "") if entry else "",
            }
            break
        used_entry_count = sum(1 for item in stats_entries if int(item["usage_count"] or 0) > 0)
        unused_entry_count = max(0, len(stats_entries) - used_entry_count)
        return {
            "session_id": session_id,
            "total_entries": len(stats_entries),
            "used_entry_count": used_entry_count,
            "unused_entry_count": unused_entry_count,
            "low_unused": unused_entry_count < low_unused_threshold,
            "repeated_entry": repeated_entry,
            "entries": stats_entries,
            "recent_usage": recent_usage,
        }

    def link_topic_pack_to_session(self, session_id: str, pack_id: int) -> dict:
        if not self.get_session(session_id):
            raise ValueError("live session 不存在")
        if not self.get_topic_pack(int(pack_id)):
            raise ValueError("topic pack 不存在")
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO live_session_topic_packs (session_id, pack_id, created_at)
                VALUES (?, ?, ?)
                """,
                (session_id, int(pack_id), now),
            )
            conn.commit()
        return {"session_id": session_id, "pack_id": int(pack_id), "created_at": now}

    def list_session_topic_packs(self, session_id: str) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*
                FROM live_session_topic_packs sp
                JOIN topic_packs p ON p.id = sp.pack_id
                WHERE sp.session_id = ?
                ORDER BY sp.created_at ASC, p.id ASC
                """,
                (session_id,),
            ).fetchall()
        return [pack for row in rows if (pack := self._row_to_topic_pack(row))]

    def list_session_topic_pack_entries(self, session_id: str, *, limit: int = 20) -> list[dict]:
        limit = max(1, min(int(limit or 20), 200))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.*, p.title AS pack_title
                FROM live_session_topic_packs sp
                JOIN topic_packs p ON p.id = sp.pack_id
                JOIN topic_pack_entries e ON e.pack_id = p.id
                WHERE sp.session_id = ?
                ORDER BY e.id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        entries = [entry for row in rows if (entry := self._row_to_topic_pack_entry(row))]
        entries.reverse()
        return entries

    def create_research_request(
        self,
        session_id: str,
        query: str,
        *,
        status: str = "completed",
        result_entry_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO research_requests (
                    session_id, query, status, result_entry_id, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, query[:500], status, result_entry_id, now, self._json_dump(metadata or {})),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM research_requests WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return {
            "id": int(row["id"]),
            "session_id": row["session_id"],
            "query": row["query"],
            "status": row["status"],
            "result_entry_id": row["result_entry_id"],
            "created_at": row["created_at"],
            "metadata": self._json_load(row["metadata_json"], {}),
        }

    def count_research_requests(self, session_id: str, *, since_iso: str = "") -> int:
        where = "session_id = ?"
        params: list[Any] = [session_id]
        if since_iso:
            where += " AND created_at >= ?"
            params.append(since_iso)
        with self._lock, self._connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM research_requests WHERE {where}", params).fetchone()
        return int(row["count"] or 0) if row else 0

    def list_research_requests(self, session_id: str, *, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit or 50), 200))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM research_requests
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "session_id": row["session_id"],
                "query": row["query"],
                "status": row["status"],
                "result_entry_id": row["result_entry_id"],
                "created_at": row["created_at"],
                "metadata": self._json_load(row["metadata_json"], {}),
            }
            for row in rows
        ]

