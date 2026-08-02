"""Generic capability router.

Given a capability request and a payload, finds matching collections via
the CapabilityRegistry and routes the request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from general_ludd.dispatch.capabilities import CapabilityRegistry, CollectionMeta

logger = logging.getLogger(__name__)


@dataclass
class RouteResult:
    """Result of routing a capability request to matching collections."""

    ok: bool
    capability: str = ""
    matches: list[RouteResult.RouteMatch] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @dataclass
    class RouteMatch:
        collection: CollectionMeta
        score: float = 1.0

        @property
        def name(self) -> str:
            return self.collection.name

        def __repr__(self) -> str:
            return f"RouteMatch(collection={self.collection.namespace}.{self.collection.name}, score={self.score:.2f})"


class CapabilityRouter:
    """Routes capability requests to matching Ansible collections.

    Uses a ``CapabilityRegistry`` to look up which collections declare a given
    capability (via their galaxy.yml tags).
    """

    def __init__(self, registry: CapabilityRegistry) -> None:
        self._registry = registry

    def route(
        self,
        capability: str,
        payload: dict[str, Any] | None = None,
    ) -> RouteResult:
        if not capability:
            return RouteResult(
                ok=False,
                capability=capability,
                error="empty capability string",
            )
        if payload is None:
            payload = {}

        collection_names = self._registry.lookup_by_tag(capability)
        if not collection_names:
            return RouteResult(
                ok=False,
                capability=capability,
                payload=payload,
                error=f"no collection found for capability: {capability}",
            )

        matches: list[RouteResult.RouteMatch] = []
        for name in sorted(collection_names):
            meta = self._registry.collections.get(name)
            if meta is not None:
                score = 1.0 if capability in meta.tags else 0.5
                matches.append(RouteResult.RouteMatch(collection=meta, score=score))

        return RouteResult(
            ok=True,
            capability=capability,
            matches=matches,
            payload=payload,
        )

    def route_by_collection(
        self,
        collection: str,
        payload: dict[str, Any] | None = None,
    ) -> RouteResult:
        if payload is None:
            payload = {}

        meta = self._registry.collections.get(collection)
        if meta is None:
            return RouteResult(
                ok=False,
                capability=collection,
                payload=payload,
                error=f"collection not found: {collection}",
            )

        return RouteResult(
            ok=True,
            capability=collection,
            matches=[RouteResult.RouteMatch(collection=meta, score=1.0)],
            payload=payload,
        )

    def get_collection(self, name: str) -> CollectionMeta | None:
        return self._registry.collections.get(name)

    def list_capabilities(self) -> list[str]:
        return sorted(self._registry.tag_index.keys())
