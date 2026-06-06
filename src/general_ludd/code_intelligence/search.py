"""Code search — searches extracted code blocks by name, type, and content."""

from __future__ import annotations

from typing import Any


class CodeSearch:
    """Searches extracted code blocks with type filtering."""

    def __init__(self, blocks: list[dict[str, Any]]) -> None:
        self._blocks = blocks

    def search(
        self,
        query: str = "",
        type_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        results = self._blocks
        if type_filter:
            results = [b for b in results if b.get("type") == type_filter]
        if query:
            query_lower = query.lower()
            results = [
                b
                for b in results
                if query_lower in b.get("name", "").lower()
                or query_lower in (b.get("docstring") or "").lower()
                or query_lower in b.get("source", "").lower()
            ]
        return results

    def list_types(self) -> list[str]:
        types: set[str] = set()
        for b in self._blocks:
            t = b.get("type")
            if t:
                types.add(t)
        return sorted(types)
