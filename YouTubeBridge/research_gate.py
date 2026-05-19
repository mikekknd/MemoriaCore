"""Research Gate Module for YouTubeBridge live context."""
from __future__ import annotations

from typing import Any, Callable, Protocol

from bridge_runtime import LiveRuntime


class ResearchSearchAdapter(Protocol):
    def search(self, query: str) -> Any:
        """Return raw search result data for a Research Gate query."""


class TavilyResearchSearchAdapter:
    def search(self, query: str) -> Any:
        from tools.tavily import search_web

        return search_web(query=query, topic="general")


class ResearchGateModule:
    def __init__(
        self,
        *,
        storage,
        runtime_lookup: Callable[[str], LiveRuntime],
        topic_pack_context_text: Callable[[list[dict[str, Any]]], str],
        record_topic_pack_usage: Callable[[str, list[dict[str, Any]], str, str], Any],
        index_topic_pack_entry: Callable[[int], Any],
        search_adapter: ResearchSearchAdapter | None = None,
    ) -> None:
        self.storage = storage
        self._runtime_lookup = runtime_lookup
        self._topic_pack_context_text = topic_pack_context_text
        self._record_topic_pack_usage = record_topic_pack_usage
        self._index_topic_pack_entry = index_topic_pack_entry
        self._search_adapter = search_adapter or TavilyResearchSearchAdapter()

    def request_sync(
        self,
        session_id: str,
        query: str,
        *,
        pack_id: int | None = None,
        enforce_cooldown: bool = True,
    ) -> dict[str, Any]:
        raise NotImplementedError("ResearchGateModule.request_sync is implemented in Task 2")
