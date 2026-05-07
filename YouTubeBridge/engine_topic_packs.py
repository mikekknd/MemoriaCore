"""YouTubeBridge topic pack 與 fact card manager mixin。"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from bridge_contracts import (
    FACT_CARDS_PACK_DESCRIPTION,
    FACT_CARDS_PACK_TITLE,
)
from fact_cards import (
    DEFAULT_FACT_CARDS_DIR,
    FACT_CARD_SOURCE_TYPE,
    iter_fact_card_files,
    parse_fact_card_markdown,
)
from topic_graph import (
    build_topic_graph_payload,
    topic_graph_role_from_tags,
    topic_graph_role_from_source_name,
    topic_graph_role_tag,
)


logger = logging.getLogger("youtube_bridge")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOPIC_SEQUENCE_TURNS_PER_ENTRY = 3


def _generate_fact_card_markdown(**kwargs):
    # 保留舊測試與外部 monkeypatch bridge_engine.generate_fact_card_markdown_with_gemini 的相容性。
    import bridge_engine

    return bridge_engine.generate_fact_card_markdown_with_gemini(**kwargs)


class TopicPackManagerMixin:
    def _embed_text(self, text: str, *, timeout_seconds: float | None = None) -> dict[str, Any]:
        clean = str(text or "").strip()
        if not clean:
            raise ValueError("embedding text 不可為空")
        if timeout_seconds is None:
            client = self._memoria_client()
        else:
            try:
                client = self.memoria_client_factory(timeout=float(timeout_seconds))
            except TypeError:
                client = self._memoria_client()
        return client.embed_text(clean)

    @staticmethod
    def _topic_entry_embedding_text(entry: dict[str, Any]) -> str:
        return f"{entry.get('title') or ''}\n{entry.get('body') or ''}".strip()

    def index_topic_pack_entry(self, entry_id: int) -> dict[str, Any]:
        entry = self.storage.get_topic_pack_entry(int(entry_id))
        if not entry:
            raise ValueError("topic pack entry 不存在")
        result = self._embed_text(self._topic_entry_embedding_text(entry))
        vector = result.get("dense") if isinstance(result, dict) else None
        if not isinstance(vector, list) or not vector:
            raise RuntimeError("MemoriaCore embedding 回傳空向量")
        return self.storage.upsert_topic_pack_entry_embedding(
            int(entry_id),
            vector,
            model=str(result.get("model") or "memoriacore-embedding"),
            content_hash=self.storage.topic_entry_content_hash(entry),
        )

    def rebuild_topic_pack_embeddings(self, pack_id: int, *, limit: int = 200) -> dict[str, Any]:
        entries = self.storage.list_topic_pack_entries(int(pack_id), limit=limit)
        indexed: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for entry in entries:
            try:
                indexed.append(self.index_topic_pack_entry(int(entry["id"])))
            except Exception as exc:
                failed.append({"entry_id": entry["id"], "error": str(exc)[:300]})
        return {
            "pack_id": int(pack_id),
            "indexed_count": len(indexed),
            "failed_count": len(failed),
            "indexed": indexed,
            "failed": failed,
        }

    def _ensure_session_topic_pack_embeddings(self, session_id: str) -> None:
        for pack in self.storage.list_session_topic_packs(session_id):
            missing = self.storage.list_topic_pack_entries_missing_embeddings(int(pack["id"]), limit=50)
            for entry in missing:
                try:
                    self.index_topic_pack_entry(int(entry["id"]))
                except Exception as exc:
                    logger.warning(
                        "topic pack embedding failed session_id=%s entry_id=%s error=%s",
                        session_id,
                        entry.get("id"),
                        exc,
                    )

    def _topic_pack_context_for_query(
        self,
        session_id: str,
        query_text: str,
        *,
        limit: int = 6,
        usage_source: str = "external_context",
        replenish_reason: str = "",
        min_score: float = 0.05,
        allow_fallback: bool = True,
    ) -> str:
        entries, _status = self._topic_pack_entries_for_query(
            session_id,
            query_text,
            limit=limit,
            min_score=min_score,
            allow_fallback=allow_fallback,
        )
        self._record_topic_pack_usage(session_id, entries, query_text, usage_source, replenish_reason)
        return self._topic_pack_context_text(entries)

    def _topic_graph_context_entries_for_hits(
        self,
        session_id: str,
        entries: list[dict[str, Any]],
        query_text: str,
        usage_source: str,
        *,
        max_entries: int = 4,
    ) -> list[dict[str, Any]]:
        selected, trace = self._expand_topic_graph_entries(entries, max_entries=max_entries)
        if not selected:
            selected = entries[:max(1, min(max_entries, len(entries)))]
        self._record_topic_pack_usage(session_id, selected, query_text, usage_source)
        if trace and selected:
            try:
                pack_id = int(selected[0].get("pack_id") or 0)
                if pack_id:
                    self.storage.record_topic_graph_retrieval_trace(
                        session_id,
                        pack_id,
                        {
                            **trace,
                            "source": usage_source,
                            "query_text": query_text,
                            "context_text_preview": self._topic_pack_context_text(selected)[:2000],
                        },
                    )
            except Exception as exc:
                logger.warning("topic graph trace record failed session_id=%s error=%s", session_id, exc)
        return selected

    def _entry_topic_graph_role(
        self,
        entry: dict[str, Any],
        *,
        graph_role_by_entry_id: dict[int, str] | None = None,
    ) -> str:
        try:
            entry_id = int(entry.get("id") or entry.get("entry_id") or 0)
        except (TypeError, ValueError):
            entry_id = 0
        graph_role = (graph_role_by_entry_id or {}).get(entry_id, "")
        if graph_role:
            return graph_role
        tags = entry.get("tags") if isinstance(entry.get("tags"), list) else []
        tag_role = topic_graph_role_from_tags(tags)
        if tag_role:
            return tag_role
        if str(entry.get("source_type") or "") == FACT_CARD_SOURCE_TYPE:
            if any(str(tag).strip().lower().startswith("index") for tag in tags):
                return "entry"
            return "detail"
        return "entry"

    def _topic_graph_entry_roles_for_pack(self, pack_id: int) -> dict[int, str]:
        graph = self.storage.get_topic_graph(int(pack_id))
        roles: dict[int, str] = {}
        for node in graph.get("nodes") or []:
            try:
                entry_id = int(node.get("entry_id") or 0)
            except (TypeError, ValueError):
                entry_id = 0
            if entry_id <= 0:
                continue
            node_type = str(node.get("node_type") or "").strip().lower()
            if node_type == "detail":
                roles[entry_id] = "detail"
            elif node_type == "topic":
                roles[entry_id] = "entry"
        return roles

    def _topic_pack_entry_points(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        role_cache: dict[int, dict[int, str]] = {}
        entry_points: list[dict[str, Any]] = []
        for entry in entries:
            try:
                pack_id = int(entry.get("pack_id") or 0)
            except (TypeError, ValueError):
                pack_id = 0
            roles = role_cache.setdefault(pack_id, self._topic_graph_entry_roles_for_pack(pack_id) if pack_id else {})
            role = self._entry_topic_graph_role(entry, graph_role_by_entry_id=roles)
            if role == "detail":
                continue
            entry_points.append(entry)
        return entry_points or entries

    def _expand_topic_graph_entries(
        self,
        entries: list[dict[str, Any]],
        *,
        max_entries: int = 4,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        if not entries:
            return [], None
        selected: list[dict[str, Any]] = []
        selected_entry_ids: set[int] = set()
        selected_node_ids: list[int] = []
        entry_node_ids: list[int] = []
        expanded_node_ids: list[int] = []
        rejected_nodes: list[dict[str, Any]] = []
        graph_used = False

        def add_entry(entry: dict[str, Any], node: dict[str, Any] | None, role: str) -> None:
            if len(selected) >= max(1, int(max_entries or 4)):
                if node:
                    rejected_nodes.append({"node_id": node["id"], "reason": "entry_budget"})
                return
            try:
                entry_id = int(entry.get("id") or entry.get("entry_id") or 0)
            except (TypeError, ValueError):
                entry_id = 0
            if entry_id <= 0 or entry_id in selected_entry_ids:
                return
            selected_entry_ids.add(entry_id)
            item = dict(entry)
            item["topic_graph_role"] = role
            if node:
                item["topic_graph_node_id"] = int(node["id"])
                selected_node_ids.append(int(node["id"]))
            selected.append(item)

        for entry in entries[:2]:
            if len(selected) >= max_entries:
                break
            try:
                pack_id = int(entry.get("pack_id") or 0)
                entry_id = int(entry.get("id") or 0)
            except (TypeError, ValueError):
                continue
            graph = self.storage.get_topic_graph(pack_id) if pack_id else {"nodes": [], "edges": []}
            nodes = graph.get("nodes") or []
            edges = graph.get("edges") or []
            if not nodes:
                add_entry(entry, None, "entry")
                continue
            node_by_id = {int(node["id"]): node for node in nodes}
            entry_node = next((node for node in nodes if int(node.get("entry_id") or 0) == entry_id), None)
            if not entry_node:
                add_entry(entry, None, "entry")
                continue
            graph_used = True
            anchor_node = entry_node
            hit_is_detail = str(entry_node.get("node_type") or "").strip().lower() == "detail"
            if hit_is_detail:
                parent_edges = sorted(
                    [
                        edge for edge in edges
                        if edge.get("edge_type") == "detail_of"
                        and int(edge.get("source_node_id") or 0) == int(entry_node["id"])
                    ],
                    key=lambda edge: float(edge.get("weight") or 0.0),
                    reverse=True,
                )
                parent_node = None
                for edge in parent_edges:
                    candidate = node_by_id.get(int(edge.get("target_node_id") or 0))
                    if candidate and str(candidate.get("node_type") or "") == "topic" and candidate.get("entry_id"):
                        parent_node = candidate
                        break
                if parent_node:
                    parent_entry = self.storage.get_topic_pack_entry(int(parent_node["entry_id"]))
                    if parent_entry:
                        entry_node_ids.append(int(parent_node["id"]))
                        add_entry(parent_entry, parent_node, "entry")
                        anchor_node = parent_node
                expanded_node_ids.append(int(entry_node["id"]))
                add_entry(entry, entry_node, "detail")
            else:
                entry_node_ids.append(int(entry_node["id"]))
                add_entry(entry, entry_node, "entry")

            detail_edges = sorted(
                [
                    edge for edge in edges
                    if edge.get("edge_type") == "detail_of"
                    and int(edge.get("target_node_id") or 0) == int(anchor_node["id"])
                ],
                key=lambda edge: float(edge.get("weight") or 0.0),
                reverse=True,
            )
            detail_count = 0
            for edge in detail_edges:
                if detail_count >= 2:
                    break
                node = node_by_id.get(int(edge.get("source_node_id") or 0))
                if not node or not node.get("entry_id"):
                    continue
                expanded_node_ids.append(int(node["id"]))
                detail_entry = self.storage.get_topic_pack_entry(int(node["entry_id"]))
                if detail_entry:
                    detail_entry["similarity"] = float(edge.get("weight") or 0.0)
                    before = len(selected)
                    add_entry(detail_entry, node, "detail")
                    if len(selected) > before:
                        detail_count += 1

            relation_edges = sorted(
                [
                    edge for edge in edges
                    if edge.get("edge_type") in {"compare_with", "mentions"}
                    and int(edge.get("source_node_id") or 0) == int(anchor_node["id"])
                ],
                key=lambda edge: (0 if edge.get("edge_type") == "compare_with" else 1, -float(edge.get("weight") or 0.0)),
            )
            relation_count = 0
            for edge in relation_edges:
                if relation_count >= 1:
                    break
                node = node_by_id.get(int(edge.get("target_node_id") or 0))
                if not node or not node.get("entry_id"):
                    continue
                expanded_node_ids.append(int(node["id"]))
                related_entry = self.storage.get_topic_pack_entry(int(node["entry_id"]))
                if related_entry:
                    related_entry["similarity"] = float(edge.get("weight") or 0.0)
                    before = len(selected)
                    add_entry(related_entry, node, "related")
                    if len(selected) > before:
                        relation_count += 1

        if not graph_used:
            return selected, None
        strategy = self._topic_graph_strategy_text(selected)
        for item in selected:
            item["topic_graph_strategy"] = strategy
        return selected, {
            "entry_node_ids": entry_node_ids,
            "expanded_node_ids": expanded_node_ids,
            "selected_node_ids": selected_node_ids,
            "rejected_nodes": rejected_nodes,
        }

    @staticmethod
    def _topic_graph_strategy_text(entries: list[dict[str, Any]]) -> str:
        entry_title = next((str(item.get("title") or "") for item in entries if item.get("topic_graph_role") == "entry"), "")
        detail_title = next((str(item.get("title") or "") for item in entries if item.get("topic_graph_role") == "detail"), "")
        related_title = next((str(item.get("title") or "") for item in entries if item.get("topic_graph_role") == "related"), "")
        parts = []
        if entry_title:
            parts.append(f"先討論「{entry_title[:60]}」")
        if detail_title:
            parts.append(f"再延伸「{detail_title[:60]}」")
        if related_title:
            parts.append(f"若自然比較，可短提「{related_title[:60]}」")
        return "；".join(parts) + "。" if parts else ""

    def _topic_pack_sequence_context_for_session(
        self,
        session_id: str,
        query_text: str,
        *,
        usage_source: str,
        turns_per_entry: int = TOPIC_SEQUENCE_TURNS_PER_ENTRY,
    ) -> str:
        entries = self._topic_pack_sequence_entries_for_session(
            session_id,
            turns_per_entry=turns_per_entry,
        )
        entries = self._topic_graph_context_entries_for_hits(
            session_id,
            entries,
            query_text,
            usage_source,
            max_entries=4,
        )
        return self._topic_pack_context_text(entries)

    def _topic_pack_sequence_entries_for_session(
        self,
        session_id: str,
        *,
        turns_per_entry: int = TOPIC_SEQUENCE_TURNS_PER_ENTRY,
    ) -> list[dict[str, Any]]:
        entries = self.storage.list_session_topic_pack_entries(session_id, limit=200)
        if not entries:
            return []
        entries = self._topic_pack_entry_points(entries)
        threshold = max(1, int(turns_per_entry or TOPIC_SEQUENCE_TURNS_PER_ENTRY))
        stats = self.storage.get_topic_pack_usage_stats(session_id, recent_limit=50)
        usage_counts = {
            int(item["entry_id"]): int(item.get("usage_count") or 0)
            for item in stats.get("entries", [])
            if isinstance(item, dict) and item.get("entry_id")
        }
        for entry in entries:
            if usage_counts.get(int(entry["id"]), 0) < threshold:
                return [entry]
        min_usage = min(usage_counts.get(int(entry["id"]), 0) for entry in entries)
        for entry in entries:
            if usage_counts.get(int(entry["id"]), 0) == min_usage:
                return [entry]
        return [entries[0]]

    def _topic_pack_entries_for_query(
        self,
        session_id: str,
        query_text: str,
        *,
        limit: int = 6,
        min_score: float = 0.05,
        allow_fallback: bool = True,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        if not str(query_text or "").strip():
            entries = self.storage.list_session_topic_pack_entries(session_id, limit=limit)
            return entries, {
                "mode": "empty_query_fallback" if entries else "empty_query_no_entries",
                "top_similarity": None,
                "entry_count": len(entries),
            }
        try:
            self._ensure_session_topic_pack_embeddings(session_id)
            query_result = self._embed_text(query_text)
            vector = query_result.get("dense") if isinstance(query_result, dict) else None
            if isinstance(vector, list) and vector:
                entries = self.storage.search_session_topic_pack_entries(
                    session_id,
                    vector,
                    limit=limit,
                    min_score=min_score,
                )
                if entries:
                    return entries, {
                        "mode": "vector",
                        "top_similarity": float(entries[0].get("similarity") or 0.0),
                        "entry_count": len(entries),
                    }
        except Exception as exc:
            logger.warning("topic pack vector retrieval failed session_id=%s error=%s", session_id, exc)
            if not allow_fallback:
                return [], {
                    "mode": "vector_failed",
                    "top_similarity": None,
                    "entry_count": 0,
                    "error": str(exc)[:300],
                }
        if allow_fallback:
            entries = self.storage.list_session_topic_pack_entries(session_id, limit=limit)
            return entries, {
                "mode": "fallback_list",
                "top_similarity": None,
                "entry_count": len(entries),
            }
        return [], {
            "mode": "no_answerable_match",
            "top_similarity": None,
            "entry_count": 0,
        }

    def _record_topic_pack_usage(
        self,
        session_id: str,
        entries: list[dict[str, Any]],
        query_text: str,
        usage_source: str,
        replenish_reason: str = "",
    ) -> None:
        if not entries:
            return
        try:
            self.storage.record_topic_pack_entry_usages(
                session_id,
                entries,
                query_text=query_text,
                usage_source=usage_source,
            )
        except Exception as exc:
            logger.warning("topic pack usage record failed session_id=%s error=%s", session_id, exc)

    def get_topic_pack_usage_status(self, session_id: str) -> dict[str, Any]:
        stats = self.storage.get_topic_pack_usage_stats(session_id)
        entries = self.storage.list_session_topic_pack_entries(session_id, limit=200)
        research_requests = self.storage.list_research_requests(session_id, limit=100)
        state = self.storage.get_director_state(session_id)
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        replenishment = metadata.get("fact_card_replenishment") if isinstance(metadata.get("fact_card_replenishment"), dict) else {}
        worker_status = str(replenishment.get("last_status") or "")
        return {
            **stats,
            "last_replenished_at": str(replenishment.get("last_replenished_at") or ""),
            "last_replenish_reason": str(replenishment.get("last_reason") or ""),
            "last_replenish_status": worker_status,
            "worker_status": worker_status,
            "last_replenish_error": str(replenishment.get("last_error") or ""),
            "last_replenish_fallback_mode": str(replenishment.get("last_fallback_mode") or ""),
            "replenishment_in_progress": bool(replenishment.get("in_progress")),
            "research_gate": self._research_gate_usage_status(entries, research_requests),
        }

    def maybe_replenish_fact_cards(
        self,
        session_id: str,
        *,
        reason: str = "",
        topic_hint: str = "",
        run_inline: bool = True,
    ) -> dict[str, Any]:
        return {
            "triggered": False,
            "reason": "fact_card_replenishment_removed",
            "session_id": session_id,
        }

    def _run_fact_card_replenishment_worker_process(
        self,
        session_id: str,
        *,
        topic: str,
        pack_id: int,
        output_name: str,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        worker_path = Path(__file__).with_name("fact_card_worker.py")
        timeout = max(30, min(int(timeout_seconds or 300), 900))
        command = [
            sys.executable,
            str(worker_path),
            "--db-path",
            str(self.storage.db_path),
            "--session-id",
            session_id,
            "--topic",
            str(topic or ""),
            "--pack-id",
            str(int(pack_id)),
            "--output-name",
            str(output_name or ""),
            "--timeout-seconds",
            str(timeout),
        ]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        try:
            completed = subprocess.run(
                command,
                cwd=str(PROJECT_ROOT),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout + 90,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"FactCard worker timeout after {timeout + 90}s") from exc
        payload = self._parse_fact_card_worker_payload(completed.stdout)
        if completed.returncode != 0:
            error = str(payload.get("error") or completed.stderr or completed.stdout or "FactCard worker failed")
            raise RuntimeError(error[:500])
        if str(payload.get("status") or "") == "failed":
            raise RuntimeError(str(payload.get("error") or "FactCard worker failed")[:500])
        return payload

    @staticmethod
    def _parse_fact_card_worker_payload(stdout: str) -> dict[str, Any]:
        lines = [line.strip() for line in str(stdout or "").splitlines() if line.strip()]
        for line in reversed(lines):
            if not (line.startswith("{") and line.endswith("}")):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        raise RuntimeError("FactCard worker did not return JSON status")

    def _ensure_fact_cards_pack(self, session_id: str, pack_id: int | None = None) -> int:
        if pack_id is not None:
            self.storage.link_topic_pack_to_session(session_id, int(pack_id))
            return int(pack_id)
        packs = self.storage.list_session_topic_packs(session_id)
        for pack in packs:
            if self._is_fact_cards_pack(pack):
                return int(pack["id"])
        for pack in self.storage.list_topic_packs(limit=500):
            if self._is_fact_cards_pack(pack):
                self.storage.link_topic_pack_to_session(session_id, int(pack["id"]))
                return int(pack["id"])
        pack = self.storage.create_topic_pack({
            "title": FACT_CARDS_PACK_TITLE,
            "description": FACT_CARDS_PACK_DESCRIPTION,
        })
        self.storage.link_topic_pack_to_session(session_id, int(pack["id"]))
        return int(pack["id"])

    @staticmethod
    def _is_fact_cards_pack(pack: dict[str, Any]) -> bool:
        return str(pack.get("title") or "").strip() == FACT_CARDS_PACK_TITLE

    def import_fact_cards_folder(
        self,
        session_id: str,
        *,
        fact_cards_dir: str | Path | None = None,
        pack_id: int | None = None,
        max_files: int = 50,
    ) -> dict[str, Any]:
        paths = iter_fact_card_files(fact_cards_dir or DEFAULT_FACT_CARDS_DIR, max_files=max_files)
        return self._import_fact_card_paths(session_id, paths, pack_id=pack_id)

    def import_fact_cards_folder_to_pack(
        self,
        *,
        fact_cards_dir: str | Path | None = None,
        pack_id: int | None = None,
        max_files: int = 50,
    ) -> dict[str, Any]:
        paths = iter_fact_card_files(fact_cards_dir or DEFAULT_FACT_CARDS_DIR, max_files=max_files)
        target_pack_id = self._ensure_fact_cards_standalone_pack(pack_id)
        return self._import_fact_card_paths_to_pack(paths, pack_id=target_pack_id)

    def import_fact_card_file(
        self,
        session_id: str,
        path: str | Path,
        *,
        pack_id: int | None = None,
    ) -> dict[str, Any]:
        return self._import_fact_card_paths(session_id, [Path(path)], pack_id=pack_id)

    def _import_fact_card_paths(
        self,
        session_id: str,
        paths: list[Path],
        *,
        pack_id: int | None = None,
    ) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        target_pack_id = self._ensure_fact_cards_pack(session_id, pack_id)
        result = self._import_fact_card_paths_to_pack(paths, pack_id=target_pack_id)
        result["session_id"] = session_id
        return result

    def _ensure_fact_cards_standalone_pack(self, pack_id: int | None = None) -> int:
        if pack_id is not None:
            if not self.storage.get_topic_pack(int(pack_id)):
                raise ValueError("topic pack 不存在")
            return int(pack_id)
        pack = self.storage.create_topic_pack({
            "title": FACT_CARDS_PACK_TITLE,
            "description": FACT_CARDS_PACK_DESCRIPTION,
        })
        return int(pack["id"])

    def rebuild_topic_graph_for_pack(self, pack_id: int) -> dict[str, Any]:
        entries = self.storage.list_topic_pack_entries(int(pack_id), limit=500)
        if not entries and not self.storage.get_topic_pack(int(pack_id)):
            raise ValueError("topic pack 不存在")
        existing_roles = self._topic_graph_entry_roles_for_pack(int(pack_id))
        documents = [
            {
                "title": entry.get("pack_title") or "Topic Pack",
                "summary": "",
                "source_name": entry.get("source_url") or entry.get("source_type") or "topic_pack",
                "facts": [
                    {
                        "entry_id": int(entry["id"]),
                        "title": entry.get("title") or "",
                        "body": entry.get("body") or "",
                        "topic_graph_role": self._entry_topic_graph_role(entry, graph_role_by_entry_id=existing_roles),
                    }
                ],
            }
            for entry in entries
        ]
        return self._rebuild_topic_graph_from_documents(int(pack_id), documents)

    def _import_fact_card_paths_to_pack(
        self,
        paths: list[Path],
        *,
        pack_id: int,
    ) -> dict[str, Any]:
        target_pack_id = int(pack_id)
        created_entries: list[dict[str, Any]] = []
        embeddings: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        graph_documents: list[dict[str, Any]] = []
        parsed_files = 0
        for path in paths:
            try:
                document = parse_fact_card_markdown(path.read_text(encoding="utf-8"), source_name=path.name)
                parsed_files += 1
            except Exception as exc:
                failures.append({"file": str(path), "error": str(exc)[:300]})
                continue
            graph_facts: list[dict[str, Any]] = []
            role = topic_graph_role_from_source_name(path.name) or "detail"
            for payload in document.to_topic_pack_entries(extra_tags=[topic_graph_role_tag(role)]):
                try:
                    entry = self.storage.create_topic_pack_entry(int(target_pack_id), payload)
                    created_entries.append(entry)
                    graph_facts.append({
                        "entry_id": int(entry["id"]),
                        "title": entry.get("title") or payload.get("title") or "",
                        "body": entry.get("body") or payload.get("body") or "",
                        "topic_graph_role": role,
                    })
                    try:
                        embeddings.append(self.index_topic_pack_entry(int(entry["id"])))
                    except Exception as exc:
                        failures.append({
                            "file": str(path),
                            "entry_id": entry["id"],
                            "error": str(exc)[:300],
                        })
                except Exception as exc:
                    failures.append({"file": str(path), "title": payload.get("title"), "error": str(exc)[:300]})
            if graph_facts:
                graph_documents.append({
                    "title": document.title,
                    "summary": document.summary,
                    "source_name": document.source_name or path.name,
                    "facts": graph_facts,
                })
        graph_status = self._rebuild_topic_graph_from_documents(
            int(target_pack_id),
            graph_documents,
        )
        return {
            "status": "completed",
            "pack_id": int(target_pack_id),
            "file_count": len(paths),
            "parsed_file_count": parsed_files,
            "created_count": len(created_entries),
            "embedding_count": len(embeddings),
            "failed_count": len(failures),
            "entries": created_entries,
            "embeddings": embeddings,
            "failures": failures,
            "graph": graph_status,
        }

    def _rebuild_topic_graph_from_documents(
        self,
        pack_id: int,
        documents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not documents:
            self.storage.replace_topic_graph(int(pack_id), [], [])
            return {"status": "completed", "node_count": 0, "edge_count": 0}
        try:
            payload = build_topic_graph_payload(documents)
            graph = self.storage.replace_topic_graph(
                int(pack_id),
                payload.get("nodes", []),
                payload.get("edges", []),
            )
            return {
                "status": "completed",
                "node_count": len(graph.get("nodes") or []),
                "edge_count": len(graph.get("edges") or []),
            }
        except Exception as exc:
            logger.warning("topic graph rebuild failed pack_id=%s error=%s", pack_id, exc)
            return {
                "status": "failed",
                "node_count": 0,
                "edge_count": 0,
                "error": str(exc)[:300],
            }

    def generate_fact_cards_with_gemini(
        self,
        session_id: str,
        *,
        topic: str,
        pack_id: int | None = None,
        output_name: str | None = None,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        session = self.storage.get_session(session_id)
        if not session:
            raise ValueError("live session 不存在")
        clean_topic = str(topic or "").strip() or "動畫新番最新一話細節討論"
        generated = _generate_fact_card_markdown(
            topic=clean_topic,
            output_dir=DEFAULT_FACT_CARDS_DIR,
            output_name=output_name,
            session_title=str(session.get("display_name") or session_id),
            director_guidance=str(session.get("director_guidance") or "固定討論動畫新番。"),
            timeout_seconds=timeout_seconds,
            memoria_client=self._memoria_client(),
        )
        import_result = self.import_fact_card_file(
            session_id,
            generated["path"],
            pack_id=pack_id,
        )
        return {
            "status": "completed",
            "session_id": session_id,
            "topic": clean_topic,
            "file_name": generated["file_name"],
            "fallback_mode": generated.get("fallback_mode", ""),
            "stdout_tail": generated.get("stdout_tail", ""),
            "stderr_tail": generated.get("stderr_tail", ""),
            "import": import_result,
        }

    def generate_fact_cards_with_gemini_to_pack(
        self,
        *,
        topic: str,
        pack_id: int | None = None,
        output_name: str | None = None,
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        clean_topic = str(topic or "").strip()
        if not clean_topic:
            raise ValueError("Fact Cards 生成主題不可為空")
        generated = _generate_fact_card_markdown(
            topic=clean_topic,
            output_dir=DEFAULT_FACT_CARDS_DIR,
            output_name=output_name,
            session_title="動畫新番 FactCards",
            director_guidance="固定討論動畫新番，補充最新話劇情細節、作畫品質、演出超展開與社群討論。",
            timeout_seconds=timeout_seconds,
            memoria_client=self._memoria_client(),
        )
        target_pack_id = self._ensure_fact_cards_standalone_pack(pack_id)
        import_result = self._import_fact_card_paths_to_pack(
            [Path(generated["path"])],
            pack_id=target_pack_id,
        )
        return {
            "status": "completed",
            "topic": clean_topic,
            "file_name": generated["file_name"],
            "fallback_mode": generated.get("fallback_mode", ""),
            "stdout_tail": generated.get("stdout_tail", ""),
            "stderr_tail": generated.get("stderr_tail", ""),
            "import": import_result,
        }
