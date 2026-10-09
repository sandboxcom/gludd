"""Focused contracts for the local remote-SearXNG compatibility adapter."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from general_ludd.searx.errors import (
    SearxError,
    SearxLifecycleError,
    SearxSearchError,
    SearxUnavailableError,
)
from general_ludd.searx.remote import RemoteSearxAdapter


@dataclass
class _Result:
    title: str
    url: str


class _Connector:
    def __init__(self, *, healthy: bool = True) -> None:
        self.healthy = healthy
        self.queries: list[tuple[str, int, str]] = []

    def health(self) -> dict[str, object]:
        return {"ok": self.healthy}

    def search(self, query: str, **kwargs: object) -> list[_Result]:
        page = kwargs.get("page")
        categories = kwargs.get("categories")
        assert isinstance(page, int)
        assert isinstance(categories, str)
        self.queries.append((query, page, categories))
        return [
            _Result(title="first", url="https://example.test/1"),
            _Result(title="second", url="https://example.test/2"),
        ]


def test_remote_adapter_has_bounded_idempotent_lifecycle() -> None:
    connector = _Connector()
    created: list[dict[str, object]] = []

    def factory(config: dict[str, object]) -> _Connector:
        created.append(config)
        return connector

    adapter = RemoteSearxAdapter(
        base_url="https://search.example/",
        timeout=3.0,
        connector_factory=factory,
    )

    assert adapter.instance_uri == "https://search.example"
    assert adapter.process_pid is None
    assert adapter.start() is True
    assert adapter.start() is False
    assert adapter.is_running() is True
    assert adapter.search("rail", categories=("news",), page=2, max_results=1) == {
        "query": "rail",
        "results": [{"title": "first", "url": "https://example.test/1"}],
        "number_of_results": 1,
    }
    assert connector.queries == [("rail", 2, "news")]
    assert created == [{"base_url": "https://search.example", "timeout": 3.0}]
    assert adapter.stop() is True
    assert adapter.stop() is False
    assert adapter.is_running() is False


def test_remote_adapter_fails_closed_before_healthy_start() -> None:
    connector = _Connector(healthy=False)
    adapter = RemoteSearxAdapter(
        base_url="https://search.example",
        connector_factory=lambda _config: connector,
    )

    with pytest.raises(SearxLifecycleError, match="unhealthy"):
        adapter.start()
    with pytest.raises(SearxLifecycleError, match="not started"):
        adapter.search("query")


def test_remote_adapter_rejects_blank_endpoint_and_errors_share_a_base() -> None:
    with pytest.raises(ValueError, match="base_url"):
        RemoteSearxAdapter(base_url=" ", connector_factory=lambda _config: _Connector())

    assert issubclass(SearxUnavailableError, SearxError)
    assert issubclass(SearxLifecycleError, SearxError)
    assert issubclass(SearxSearchError, SearxError)
