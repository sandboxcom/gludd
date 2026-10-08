"""Explicit compatibility adapter for operator-managed remote SearXNG."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict
from typing import Any, Protocol, cast

from general_ludd.searx.errors import SearxLifecycleError


class _RemoteConnector(Protocol):
    def health(self) -> dict[str, object]: ...

    def search(self, query: str, **kwargs: Any) -> Sequence[Any]: ...


ConnectorFactory = Callable[..., _RemoteConnector]


class RemoteSearxAdapter:
    """Explicit compatibility adapter for an operator-managed HTTP instance."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float = 10.0,
        connector_factory: ConnectorFactory | None = None,
    ) -> None:
        """Configure an explicit operator-managed remote SearXNG endpoint."""
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url is required for remote SearXNG transport")
        if connector_factory is None:
            from general_ludd.connectors.searx import SearXConnector

            selected_factory = cast(ConnectorFactory, SearXConnector)
        else:
            selected_factory = connector_factory
        self.base_url = base_url.rstrip("/")
        self.instance_uri = self.base_url
        self.process_pid = None
        self._connector: _RemoteConnector = selected_factory(
            {"base_url": self.base_url, "timeout": timeout}
        )
        self._started = False

    def start(self) -> bool:
        """Health-check and mark the remote adapter as started."""
        if self._started:
            return False
        health = self._connector.health()
        if health.get("ok") is not True:
            raise SearxLifecycleError("remote SearXNG compatibility endpoint is unhealthy")
        self._started = True
        return True

    def stop(self) -> bool:
        """Mark the compatibility adapter stopped idempotently."""
        changed = self._started
        self._started = False
        return changed

    def is_running(self) -> bool:
        """Return whether remote startup completed successfully."""
        return self._started

    def search(
        self,
        query: str,
        *,
        categories: Sequence[str] | None = None,
        page: int = 1,
        max_results: int = 10,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """Execute one bounded search through the remote connector."""
        if not self._started:
            raise SearxLifecycleError("remote SearXNG adapter is not started")
        results = self._connector.search(
            query,
            page=page,
            categories=",".join(categories or ("general",)),
        )
        serialised = [asdict(result) for result in results[:max_results]]
        return {"query": query, "results": serialised, "number_of_results": len(serialised)}
