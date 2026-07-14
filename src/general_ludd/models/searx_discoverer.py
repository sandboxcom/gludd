"""SearX model discoverer — bridges SearXNG model search results into ModelGateway profiles."""

from __future__ import annotations

import logging
import os
import time
from typing import Protocol

from general_ludd.infra.model_search import (
    INDEX_CACHE_DIR,
    ModelIndex,
    ModelSearchResult,
    SearXModelSearch,
)
from general_ludd.models.gateway import ModelGateway

logger = logging.getLogger(__name__)

SEARX_DISCOVER_TTL_DEFAULT = int(
    os.environ.get("GLUDD_SEARX_DISCOVER_TTL", "3600")
)


class _ModelGatewayProtocol(Protocol):
    def add_profile(self, model_id: str, provider: str = ..., model: str = ..., **kwargs: object) -> object: ...


class SearxModelDiscoverer:
    def __init__(
        self,
        gateway: ModelGateway,
        searx_url: str | None = None,
        *,
        cache_dir: str | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        self._gateway = gateway
        self._searx_url = searx_url
        self._searcher = SearXModelSearch(base_url=searx_url)
        self._index = ModelIndex(cache_dir=cache_dir or str(INDEX_CACHE_DIR))
        self._ttl = ttl_seconds if ttl_seconds is not None else SEARX_DISCOVER_TTL_DEFAULT
        self._last_sync: float = 0.0

    def _profile_id(self, model_name: str) -> str:
        return f"searx-{model_name}"

    def _result_to_profile_kwargs(self, result: ModelSearchResult) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "model_profile_id": self._profile_id(result.name),
            "model_name": result.name,
            "provider": "searx-discovered",
            "enabled": False,
        }
        if result.description:
            kwargs["description"] = result.description[:500]
        if result.params_count:
            kwargs["cost_per_input_token"] = 0.0
            kwargs["cost_per_output_token"] = 0.0
        return kwargs

    def _add_profile_from_result(self, result: ModelSearchResult) -> bool:
        try:
            self._gateway.add_profile(
                model_id=self._profile_id(result.name),
                provider="searx-discovered",
                model=result.name,
                enabled=False,
                cost_per_input_token=0.0,
                cost_per_output_token=0.0,
            )
            return True
        except Exception:
            logger.debug("Failed to add profile for %s", result.name, exc_info=True)
            return False

    def sync_models(self, *, force: bool = False) -> int:
        now = time.monotonic()
        if not force and (now - self._last_sync) < self._ttl:
            logger.debug("SearX discoverer TTL not expired (%.0fs remaining)", self._ttl - (now - self._last_sync))
            return 0

        self._last_sync = now
        added = 0
        try:
            results = self._searcher.search_models("LLM")
        except Exception:
            logger.info("SearX unreachable, falling back to ModelIndex cache")
            results = self._index.list_all()

        if not results:
            cached = self._index.list_all()
            results = cached

        for result in results:
            if self._index.get(result.name) is None:
                self._index.put(result)
            if self._add_profile_from_result(result):
                added += 1

        logger.info("SearX discoverer synced %d models (total index: %d)", added, self._index.size())
        return added

    def discover_now(self, query: str) -> int:
        added = 0
        try:
            results = self._searcher.search_models(query)
        except Exception:
            results = self._index.search(query)

        for result in results:
            if self._index.get(result.name) is None:
                self._index.put(result)
            if self._add_profile_from_result(result):
                added += 1

        return added

    @property
    def index_size(self) -> int:
        return self._index.size()

    @property
    def last_sync_time(self) -> float:
        return self._last_sync

    @property
    def ttl_seconds(self) -> int:
        return self._ttl
