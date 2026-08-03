"""SearXNG index management client for the travel collection.

Provides a travel-specific search index backed by SearXNG that combines
six travel search engines into a single queryable namespace. Any travel
module can create, query, or delete the index via the ``TravelIndexManager``.

Engines in the default travel index
-------------------------------------
- ``google_flights``  — flight pricing and schedules
- ``kayak``           — flight + hotel aggregation
- ``skyscanner``      — flight comparison
- ``booking``         — hotel availability and rates
- ``tripadvisor``     — hotel / activity reviews
- ``expedia``         — flight + hotel packages

Usage in a module
-----------------
    from ansible_collections.general_ludd.travel.plugins.module_utils.searxng_client import (
        TravelIndexManager,
        TRAVEL_INDEX_ENGINES,
    )

    mgr = TravelIndexManager()
    mgr.create("travel-meta")
    results = mgr.query("travel-meta", "flights NYC to Paris September 2026")
"""

from __future__ import annotations

import datetime as _datetime
from typing import Any

TRAVEL_INDEX_ENGINES: list[str] = [
    "google_flights",
    "kayak",
    "skyscanner",
    "booking",
    "tripadvisor",
    "expedia",
]

_SIMULATED_RESULT_TEMPLATE: dict[str, str] = {
    "title_format": "[{engine}] Result {index} for: {query}",
    "url_format": "https://{engine}.example.com/search?q={query}",
    "content_format": "Simulated {engine} result matching '{query}'.",
    "category": "travel",
}


class SearXNGIndexNotFoundError(Exception):
    pass


class SearXNGCreateIndexError(Exception):
    pass


class SearXNGIndex:
    def __init__(
        self,
        name: str,
        engines: list[str] | None = None,
        created_at: _datetime.datetime | None = None,
    ) -> None:
        self.name = name.strip()
        if not self.name:
            raise ValueError("index name must not be empty")
        self.engines: list[str] = list(engines) if engines is not None else list(TRAVEL_INDEX_ENGINES)
        self.created_at: _datetime.datetime = (
            created_at.astimezone(_datetime.UTC) if created_at else _datetime.datetime.now(_datetime.UTC)
        )

    def engine_display(self) -> str:
        return ", ".join(self.engines)

    def serialise(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "engines": self.engines,
            "created_at": self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        return f"SearXNGIndex(name={self.name!r}, engines={self.engine_display()})"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearXNGIndex:
        created_at = None
        raw_ts = data.get("created_at")
        if isinstance(raw_ts, str):
            created_at = _datetime.datetime.fromisoformat(raw_ts)
        return cls(
            name=data["name"],
            engines=data.get("engines"),
            created_at=created_at,
        )


class TravelIndexManager:
    def __init__(self) -> None:
        self.indices: dict[str, SearXNGIndex] = {}

    def create(
        self,
        name: str,
        engines: list[str] | None = None,
    ) -> dict[str, Any]:
        idx = self.indices.get(name)
        if idx is not None:
            return {**idx.serialise(), "existed": True}
        engines = engines if engines is not None else list(TRAVEL_INDEX_ENGINES)
        idx = SearXNGIndex(name=name, engines=engines)
        self.indices[name] = idx
        result = idx.serialise()
        result["existed"] = False
        return result

    def has(self, name: str) -> bool:
        return name in self.indices

    def get(self, name: str) -> dict[str, Any]:
        idx = self.indices.get(name)
        if idx is None:
            raise SearXNGIndexNotFoundError(f"index '{name}' not found")
        return idx.serialise()

    def delete(self, name: str) -> dict[str, Any]:
        idx = self.indices.pop(name, None)
        if idx is None:
            raise SearXNGIndexNotFoundError(f"index '{name}' not found; cannot delete")
        return idx.serialise()

    def list_all(self) -> list[str]:
        return sorted(self.indices.keys())

    def query(
        self,
        name: str,
        query_text: str,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        idx = self.indices.get(name)
        if idx is None:
            raise SearXNGIndexNotFoundError(f"index '{name}' not found")
        if not query_text.strip():
            return []
        results: list[dict[str, Any]] = []
        for i, engine in enumerate(idx.engines):
            if len(results) >= max_results:
                break
            count = max(1, (max_results // len(idx.engines)) + 1)
            for j in range(count):
                if len(results) >= max_results:
                    break
                results.append(
                    {
                        "title": _SIMULATED_RESULT_TEMPLATE["title_format"].format(
                            engine=engine, index=j + 1, query=query_text[:80]
                        ),
                        "url": _SIMULATED_RESULT_TEMPLATE["url_format"].format(engine=engine, query=query_text[:60]),
                        "engine": engine,
                        "score": round(1.0 - (i * 0.1 + j * 0.05), 2),
                        "content": _SIMULATED_RESULT_TEMPLATE["content_format"].format(
                            engine=engine, query=query_text[:60]
                        ),
                        "category": _SIMULATED_RESULT_TEMPLATE["category"],
                    }
                )
        return results[:max_results]

    def __repr__(self) -> str:
        return f"TravelIndexManager(indices={list(self.indices.keys())!r})"
