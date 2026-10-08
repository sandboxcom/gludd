"""Contract tests for the in-process SearXNG integration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from general_ludd.searx.native import (
    NativeSearxRuntime,
    RemoteSearxAdapter,
    SearxLifecycleError,
    SearxSearchError,
    SearxUnavailableError,
    default_namespace,
)


class _Response:
    def __init__(self, status: int, payload: object | None = None) -> None:
        self.status_code = status
        self._payload = payload

    def get_json(self, *, silent: bool = False) -> object:
        del silent
        return self._payload


class _Client:
    def __init__(
        self,
        *,
        search_response: _Response | None = None,
        health_status: int = 200,
        events: list[str] | None = None,
        label: str = "client",
    ) -> None:
        self.search_response = search_response or _Response(200, {"results": []})
        self.health_status = health_status
        self.events = events if events is not None else []
        self.label = label
        self.closed = False
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str, **kwargs: Any) -> _Response:
        self.calls.append((path, kwargs))
        self.events.append(f"{self.label}:get:{path}")
        if path == "/healthz":
            return _Response(self.health_status)
        return self.search_response

    def close(self) -> None:
        self.closed = True
        self.events.append(f"{self.label}:close")


class _App:
    def __init__(self, clients: list[_Client]) -> None:
        self._clients = clients
        self.calls = 0

    def test_client(self) -> _Client:
        client = self._clients[self.calls]
        self.calls += 1
        return client


@dataclass
class _RemoteResult:
    title: str
    url: str


class _RemoteConnector:
    def __init__(self, health: bool = True) -> None:
        self.healthy = health
        self.queries: list[tuple[str, int, str]] = []

    def health(self) -> dict[str, object]:
        return {"ok": self.healthy}

    def search(self, query: str, *, page: int, categories: str) -> list[_RemoteResult]:
        self.queries.append((query, page, categories))
        return [_RemoteResult(title="remote", url="https://example.test")]


def _settings(tmp_path: Path) -> Path:
    path = tmp_path / "settings.yml"
    path.write_text("search:\n  formats: [html, json]\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _loader(app: _App, imported: list[str]):
    def load(name: str) -> object:
        imported.append(name)
        if name != "searx.webapp":
            raise AssertionError(f"unexpected upstream import: {name}")
        return SimpleNamespace(app=app)

    return load


def test_native_start_search_and_cleanup_use_wsgi_without_a_url(tmp_path: Path) -> None:
    result = {
        "query": "privacy tools",
        "results": [
            {
                "title": "SearXNG",
                "url": "https://docs.searxng.org/",
                "content": "Private metasearch",
                "engine": "duckduckgo",
            }
        ],
    }
    client = _Client(search_response=_Response(200, result))
    app = _App([client])
    imported: list[str] = []
    runtime = NativeSearxRuntime(
        settings_path=_settings(tmp_path),
        namespace="gludd-tests",
        module_loader=_loader(app, imported),
    )

    assert runtime.start() is True
    assert runtime.start() is False
    payload = runtime.search("privacy tools", categories=["general"], max_results=5)
    assert payload["results"] == result["results"]
    assert imported == ["searx.webapp"]
    assert client.calls[1][0] == "/search"
    assert client.calls[1][1]["query_string"]["format"] == "json"
    assert "url" not in client.calls[1][1]
    assert runtime.instance_uri == "searx+python://gludd-tests"
    assert runtime.process_pid is None

    assert runtime.stop() is True
    assert runtime.stop() is False
    assert client.closed is True


def test_context_manager_cleans_up_after_search_error(tmp_path: Path) -> None:
    client = _Client(search_response=_Response(503, {"error": "unavailable"}))
    runtime = NativeSearxRuntime(
        settings_path=_settings(tmp_path),
        namespace="error-cleanup",
        module_loader=_loader(_App([client]), []),
    )

    with pytest.raises(SearxSearchError, match="status 503"), runtime:
        runtime.search("do not include this query in the error")

    assert client.closed is True
    assert "do not include" not in str(runtime.last_error)


def test_failed_zero_downtime_restart_keeps_old_client_live(tmp_path: Path) -> None:
    events: list[str] = []
    current = _Client(events=events, label="current")
    replacement = _Client(health_status=500, events=events, label="replacement")
    runtime = NativeSearxRuntime(
        settings_path=_settings(tmp_path),
        namespace="zdd-restart",
        module_loader=_loader(_App([current, replacement]), []),
    )
    runtime.start()

    with pytest.raises(SearxLifecycleError, match="health check"):
        runtime.restart()

    assert runtime.is_running() is True
    assert current.closed is False
    assert replacement.closed is True
    runtime.stop()


def test_zero_downtime_restart_swaps_before_old_cleanup(tmp_path: Path) -> None:
    events: list[str] = []
    current = _Client(events=events, label="current")
    replacement = _Client(events=events, label="replacement")
    runtime = NativeSearxRuntime(
        settings_path=_settings(tmp_path),
        namespace="zdd-success",
        module_loader=_loader(_App([current, replacement]), []),
    )
    runtime.start()

    assert runtime.restart() is True
    assert runtime.is_running() is True
    assert current.closed is True
    assert events.index("replacement:get:/healthz") < events.index("current:close")
    runtime.stop()


@pytest.mark.parametrize("namespace", ["", "../escape", "spaces are unsafe", "a" * 65])
def test_namespace_is_a_bounded_safe_resource_component(
    tmp_path: Path,
    namespace: str,
) -> None:
    with pytest.raises(ValueError, match="namespace"):
        NativeSearxRuntime(settings_path=_settings(tmp_path), namespace=namespace)


def test_group_writable_settings_are_rejected(tmp_path: Path) -> None:
    path = _settings(tmp_path)
    path.chmod(0o620)
    runtime = NativeSearxRuntime(settings_path=path, namespace="secure-settings")

    with pytest.raises(SearxLifecycleError, match="owner-only writable"):
        runtime.start()


def test_symlink_settings_are_rejected(tmp_path: Path) -> None:
    target = _settings(tmp_path)
    link = tmp_path / "linked-settings.yml"
    link.symlink_to(target)
    runtime = NativeSearxRuntime(settings_path=link, namespace="secure-settings")

    with pytest.raises(SearxLifecycleError, match="symlink"):
        runtime.start()


def test_settings_environment_is_restored_after_upstream_import(tmp_path: Path) -> None:
    previous = os.environ.get("SEARXNG_SETTINGS_PATH")
    os.environ["SEARXNG_SETTINGS_PATH"] = "/operator/original.yml"
    imported_value: list[str | None] = []
    app = _App([_Client()])

    def loader(name: str) -> object:
        assert name == "searx.webapp"
        imported_value.append(os.environ.get("SEARXNG_SETTINGS_PATH"))
        return SimpleNamespace(app=app)

    try:
        runtime = NativeSearxRuntime(
            settings_path=_settings(tmp_path),
            namespace="environment",
            module_loader=loader,
        )
        runtime.start()
        runtime.stop()
        assert imported_value == [str((tmp_path / "settings.yml").resolve())]
        assert os.environ["SEARXNG_SETTINGS_PATH"] == "/operator/original.yml"
    finally:
        if previous is None:
            os.environ.pop("SEARXNG_SETTINGS_PATH", None)
        else:
            os.environ["SEARXNG_SETTINGS_PATH"] = previous


def test_default_namespace_honours_explicit_resource_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GLUDD_RESOURCE_NAMESPACE", "project-alpha")

    assert default_namespace() == "project-alpha"


def test_missing_or_invalid_upstream_application_fails_closed(tmp_path: Path) -> None:
    def missing(_name: str) -> object:
        raise ModuleNotFoundError("searx")

    missing_runtime = NativeSearxRuntime(
        settings_path=_settings(tmp_path),
        namespace="missing-upstream",
        module_loader=missing,
    )
    with pytest.raises(SearxUnavailableError, match="official SearXNG"):
        missing_runtime.start()

    invalid_runtime = NativeSearxRuntime(
        settings_path=_settings(tmp_path),
        namespace="invalid-upstream",
        module_loader=lambda _name: SimpleNamespace(),
    )
    with pytest.raises(SearxUnavailableError, match="WSGI app"):
        invalid_runtime.start()


def test_startup_propagates_client_creation_and_health_failures(tmp_path: Path) -> None:
    class BrokenApp:
        def test_client(self) -> _Client:
            raise RuntimeError("client creation")

    broken_runtime = NativeSearxRuntime(
        settings_path=_settings(tmp_path),
        namespace="broken-client",
        module_loader=lambda _name: SimpleNamespace(app=BrokenApp()),
    )
    with pytest.raises(SearxLifecycleError, match="create native"):
        broken_runtime.start()

    unhealthy = _Client(health_status=503)
    unhealthy_runtime = NativeSearxRuntime(
        settings_path=_settings(tmp_path),
        namespace="unhealthy-client",
        module_loader=_loader(_App([unhealthy]), []),
    )
    with pytest.raises(SearxLifecycleError, match="health check"):
        unhealthy_runtime.start()
    assert unhealthy.closed is True


@pytest.mark.parametrize(
    ("query", "max_results", "message"),
    [
        ("", 1, "non-empty"),
        ("contains\x00nul", 1, "without NUL"),
        ("valid", 0, "between 1 and 100"),
        ("valid", 101, "between 1 and 100"),
        ("valid", True, "between 1 and 100"),
    ],
)
def test_query_and_result_bounds_are_enforced(
    tmp_path: Path,
    query: str,
    max_results: int,
    message: str,
) -> None:
    runtime = NativeSearxRuntime(
        settings_path=_settings(tmp_path),
        namespace="query-validation",
    )

    with pytest.raises(ValueError, match=message):
        runtime.search(query, max_results=max_results)


def test_search_option_validation_and_not_started_error(tmp_path: Path) -> None:
    runtime = NativeSearxRuntime(
        settings_path=_settings(tmp_path),
        namespace="option-validation",
    )

    with pytest.raises(ValueError, match="safe_search"):
        runtime.search("query", safe_search=3)
    with pytest.raises(ValueError, match="page"):
        runtime.search("query", page=0)
    with pytest.raises(ValueError, match="time_range"):
        runtime.search("query", time_range="decade")
    with pytest.raises(SearxLifecycleError, match="not started"):
        runtime.search("query")


def test_search_wraps_transport_and_response_errors(tmp_path: Path) -> None:
    class RaisingClient(_Client):
        def get(self, path: str, **kwargs: Any) -> _Response:
            if path == "/healthz":
                return super().get(path, **kwargs)
            raise RuntimeError("transport")

    raising = RaisingClient()
    transport_runtime = NativeSearxRuntime(
        settings_path=_settings(tmp_path),
        namespace="transport-error",
        module_loader=_loader(_App([raising]), []),
    )
    transport_runtime.start()
    with pytest.raises(SearxSearchError, match="search call failed"):
        transport_runtime.search("private query")
    transport_runtime.stop()

    invalid = _Client(search_response=_Response(200, {"wrong": []}))
    invalid_runtime = NativeSearxRuntime(
        settings_path=_settings(tmp_path),
        namespace="schema-error",
        module_loader=_loader(_App([invalid]), []),
    )
    invalid_runtime.start()
    with pytest.raises(SearxSearchError, match="result schema"):
        invalid_runtime.search("private query")
    invalid_runtime.stop()


def test_remote_adapter_is_explicit_idempotent_and_bounded() -> None:
    connector = _RemoteConnector()
    created: list[dict[str, object]] = []

    def factory(config: dict[str, object]) -> _RemoteConnector:
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
    payload = adapter.search("rail", categories=("news",), page=2, max_results=1)
    assert payload["results"] == [
        {"title": "remote", "url": "https://example.test"}
    ]
    assert connector.queries == [("rail", 2, "news")]
    assert created == [{"base_url": "https://search.example", "timeout": 3.0}]
    assert adapter.stop() is True
    assert adapter.stop() is False


def test_remote_adapter_requires_health_and_started_state() -> None:
    connector = _RemoteConnector(health=False)
    adapter = RemoteSearxAdapter(
        base_url="https://search.example",
        connector_factory=lambda _config: connector,
    )

    with pytest.raises(SearxLifecycleError, match="unhealthy"):
        adapter.start()
    with pytest.raises(SearxLifecycleError, match="not started"):
        adapter.search("query")
