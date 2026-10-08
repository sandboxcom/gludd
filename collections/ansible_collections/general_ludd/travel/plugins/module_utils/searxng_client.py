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
import re as _re
from collections.abc import Callable
from typing import Any

TRAVEL_INDEX_ENGINES: list[str] = [
    "google_flights",
    "kayak",
    "skyscanner",
    "booking",
    "tripadvisor",
    "expedia",
]

_INDEX_RE = _re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_ENGINE_RE = _re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_MAX_INDICES = 32
_MAX_ENGINES = 20
_MAX_RESULTS = 100
_MAX_QUERY_CHARS = 2048

SearchBackend = Callable[..., list[dict[str, Any]]]


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
        if _INDEX_RE.fullmatch(self.name) is None:
            raise ValueError(
                "index name must be 1-64 characters using letters, digits, '.', '_', or '-'"
            )
        selected = list(engines) if engines is not None else list(TRAVEL_INDEX_ENGINES)
        if not selected or len(selected) > _MAX_ENGINES:
            raise ValueError(f"engines must contain between 1 and {_MAX_ENGINES} names")
        if any(_ENGINE_RE.fullmatch(engine) is None for engine in selected):
            raise ValueError("engines contains an invalid name")
        self.engines = selected
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
    def __init__(self, *, search_backend: SearchBackend | None = None) -> None:
        self.indices: dict[str, SearXNGIndex] = {}
        self._search_backend = search_backend

    def create(
        self,
        name: str,
        engines: list[str] | None = None,
    ) -> dict[str, Any]:
        idx = self.indices.get(name)
        if idx is not None:
            return {**idx.serialise(), "existed": True}
        if len(self.indices) >= _MAX_INDICES:
            raise SearXNGCreateIndexError(
                f"travel index registry is limited to {_MAX_INDICES} entries"
            )
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
        *,
        search_backend: SearchBackend | None = None,
    ) -> list[dict[str, Any]]:
        idx = self.indices.get(name)
        if idx is None:
            raise SearXNGIndexNotFoundError(f"index '{name}' not found")
        if not isinstance(query_text, str) or not query_text.strip():
            return []
        if "\x00" in query_text or len(query_text) > _MAX_QUERY_CHARS:
            raise ValueError(
                f"query must be at most {_MAX_QUERY_CHARS} characters without NUL bytes"
            )
        if isinstance(max_results, bool) or not isinstance(max_results, int):
            raise ValueError(f"max_results must be between 1 and {_MAX_RESULTS}")
        if not 1 <= max_results <= _MAX_RESULTS:
            raise ValueError(f"max_results must be between 1 and {_MAX_RESULTS}")
        backend = search_backend or self._search_backend
        if backend is None:
            raise SearXNGCreateIndexError(
                "a controller-native SearXNG search backend is required"
            )
        results = backend(
            query_text,
            engines=list(idx.engines),
            max_results=max_results,
        )
        if not isinstance(results, list) or any(
            not isinstance(result, dict) for result in results
        ):
            raise SearXNGCreateIndexError(
                "SearXNG search backend returned an invalid result list"
            )
        return [dict(result) for result in results[:max_results]]

    def __repr__(self) -> str:
        return f"TravelIndexManager(indices={list(self.indices.keys())!r})"
