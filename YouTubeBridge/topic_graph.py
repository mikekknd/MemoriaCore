"""FactCards Topic Graph 建圖與召回 helper。"""
from __future__ import annotations

import re
from typing import Any


DETAIL_DOCUMENT_MARKERS = ("深挖", "細節", "補充", "detail", "deep")
TOPIC_GRAPH_ROLE_TAG_PREFIX = "topic_graph_role:"
COMPARISON_MARKERS = (
    "拉下來",
    "反超",
    "對比",
    "比較",
    "霸權",
    "排名",
    "榜單",
    "攻頂",
    "壓過",
    "挑戰",
)


def normalize_node_key(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[《》「」『』（）()\[\]{}:：,，.。;；/\\|]+", "-", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:180] or "node"


def extract_entities(text: str) -> list[str]:
    seen: set[str] = set()
    entities: list[str] = []
    for match in re.finditer(r"《([^》]{1,120})》", str(text or "")):
        entity = match.group(1).strip()
        if not entity or entity in seen:
            continue
        seen.add(entity)
        entities.append(entity)
    return entities


def is_detail_document(source_name: str, title: str) -> bool:
    haystack = f"{source_name}\n{title}".lower()
    return any(marker.lower() in haystack for marker in DETAIL_DOCUMENT_MARKERS)


def is_index_source_name(source_name: str) -> bool:
    stem = re.sub(r"\.[^.\\/]+$", "", str(source_name or "").strip()).lower()
    return bool(stem) and stem.startswith("index")


def topic_graph_role_from_source_name(source_name: str) -> str:
    clean = str(source_name or "").strip()
    if not clean:
        return ""
    return "entry" if is_index_source_name(clean) else "detail"


def normalize_topic_graph_role(role: str) -> str:
    clean = str(role or "").strip().lower()
    if clean in {"entry", "topic", "main", "overview"}:
        return "entry"
    if clean in {"detail", "deep", "deep_dive", "supplement"}:
        return "detail"
    return ""


def topic_graph_role_tag(role: str) -> str:
    clean = normalize_topic_graph_role(role) or "entry"
    return f"{TOPIC_GRAPH_ROLE_TAG_PREFIX}{clean}"


def topic_graph_role_from_tags(tags: list[Any] | tuple[Any, ...] | None) -> str:
    for tag in tags or []:
        text = str(tag or "").strip().lower()
        if not text.startswith(TOPIC_GRAPH_ROLE_TAG_PREFIX):
            continue
        role = normalize_topic_graph_role(text[len(TOPIC_GRAPH_ROLE_TAG_PREFIX):])
        if role:
            return role
    return ""


def has_comparison_context(text: str) -> bool:
    return any(marker in str(text or "") for marker in COMPARISON_MARKERS)


def build_topic_graph_payload(documents: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    node_keys: set[str] = set()
    edge_keys: set[tuple[str, str, str]] = set()
    entity_node_by_name: dict[str, str] = {}
    topic_node_by_entity: dict[str, str] = {}
    fact_nodes: list[dict[str, Any]] = []

    def add_node(payload: dict[str, Any]) -> str:
        node_key = str(payload.get("node_key") or "").strip()
        if not node_key:
            raise ValueError("topic graph node_key 不可為空")
        if node_key in node_keys:
            return node_key
        node_keys.add(node_key)
        nodes.append(payload)
        return node_key

    def add_edge(source_key: str, target_key: str, edge_type: str, *, weight: float = 1.0, evidence: str = "") -> None:
        if not source_key or not target_key or source_key == target_key:
            return
        key = (source_key, target_key, edge_type)
        if key in edge_keys:
            return
        edge_keys.add(key)
        edges.append({
            "source_node_key": source_key,
            "target_node_key": target_key,
            "edge_type": edge_type,
            "weight": float(weight),
            "evidence": str(evidence or "")[:1000],
        })

    def ensure_entity(entity_name: str, *, source_name: str = "") -> str:
        clean = str(entity_name or "").strip()
        if not clean:
            return ""
        existing = entity_node_by_name.get(clean)
        if existing:
            return existing
        node_key = f"entity:{normalize_node_key(clean)}"
        entity_node_by_name[clean] = node_key
        return add_node({
            "node_key": node_key,
            "node_type": "entity",
            "title": f"《{clean}》",
            "summary": "",
            "source_name": source_name,
            "source_heading": "",
            "metadata": {"entity": clean},
        })

    for document in documents:
        source_name = str(document.get("source_name") or "").strip()
        doc_title = str(document.get("title") or source_name or "FactCards").strip()
        doc_summary = str(document.get("summary") or "").strip()
        document_key = add_node({
            "node_key": f"document:{normalize_node_key(source_name or doc_title)}",
            "node_type": "document",
            "title": doc_title,
            "summary": doc_summary,
            "source_name": source_name,
            "source_heading": "",
            "metadata": {"source_name": source_name},
        })
        document_role = topic_graph_role_from_source_name(source_name)
        detail_document = document_role == "detail" or (not document_role and is_detail_document(source_name, doc_title))
        category_key = ""
        if not detail_document:
            category_key = add_node({
                "node_key": f"category:{normalize_node_key(doc_title)}",
                "node_type": "category",
                "title": doc_title,
                "summary": doc_summary,
                "source_name": source_name,
                "source_heading": "",
                "metadata": {"source_name": source_name},
            })
            add_edge(document_key, category_key, "contains", evidence=f"{source_name} summary category")

        document_entities = extract_entities(f"{doc_title}\n{doc_summary}")
        document_primary_entity = document_entities[0] if document_entities else ""
        for entity in document_entities:
            ensure_entity(entity, source_name=source_name)

        for fact in document.get("facts") or []:
            title = str(fact.get("title") or "").strip()
            body = str(fact.get("body") or "").strip()
            if not title or not body:
                continue
            entry_id = fact.get("entry_id")
            try:
                clean_entry_id = int(entry_id) if entry_id is not None else None
            except (TypeError, ValueError):
                clean_entry_id = None
            fact_role = normalize_topic_graph_role(
                str(fact.get("topic_graph_role") or fact.get("node_type") or "")
            )
            node_type = "detail" if fact_role == "detail" or (not fact_role and detail_document) else "topic"
            node_key = f"entry:{clean_entry_id}" if clean_entry_id else f"{node_type}:{normalize_node_key(source_name)}:{normalize_node_key(title)}"
            fact_entities = extract_entities(f"{title}\n{body}")
            title_entities = extract_entities(title)
            primary_entity = (title_entities[0] if title_entities else "") or document_primary_entity or (fact_entities[0] if fact_entities else "")
            add_node({
                "node_key": node_key,
                "entry_id": clean_entry_id,
                "node_type": node_type,
                "title": title,
                "summary": body,
                "source_name": source_name,
                "source_heading": title,
                "metadata": {
                    "primary_entity": primary_entity,
                    "entities": fact_entities,
                    "topic_graph_role": "detail" if node_type == "detail" else "entry",
                },
            })
            add_edge(category_key or document_key, node_key, "contains", evidence=f"{source_name} contains {title}")
            for entity in fact_entities:
                ensure_entity(entity, source_name=source_name)
            if primary_entity:
                entity_key = ensure_entity(primary_entity, source_name=source_name)
                add_edge(node_key, entity_key, "same_entity", weight=0.85, evidence=f"{title} primary entity")
                if node_type == "topic":
                    topic_node_by_entity.setdefault(primary_entity, node_key)
            fact_nodes.append({
                "node_key": node_key,
                "node_type": node_type,
                "title": title,
                "body": body,
                "source_name": source_name,
                "primary_entity": primary_entity,
                "entities": fact_entities,
            })

    for fact in fact_nodes:
        source_key = fact["node_key"]
        primary_entity = fact.get("primary_entity") or ""
        if fact["node_type"] == "detail" and primary_entity:
            target_key = topic_node_by_entity.get(primary_entity) or entity_node_by_name.get(primary_entity, "")
            add_edge(
                source_key,
                target_key,
                "detail_of",
                weight=0.95,
                evidence=f"{fact['source_name']} detail references {primary_entity}",
            )
        comparison_text = f"{fact['title']}\n{fact['body']}"
        for entity in fact.get("entities") or []:
            if entity == primary_entity:
                continue
            target_key = topic_node_by_entity.get(entity) or entity_node_by_name.get(entity, "")
            if not target_key:
                continue
            add_edge(source_key, target_key, "mentions", weight=0.65, evidence=f"{fact['title']} mentions {entity}")
            if has_comparison_context(comparison_text):
                add_edge(
                    source_key,
                    target_key,
                    "compare_with",
                    weight=0.75,
                    evidence=f"{fact['title']} compares with {entity}",
                )

    return {"nodes": nodes, "edges": edges}
