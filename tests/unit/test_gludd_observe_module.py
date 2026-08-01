"""Unit contracts for the cross-source ``gludd_observe`` Ansible module."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "plugins"
    / "modules"
    / "gludd_observe.py"
)


class _FakeAnsibleModule:
    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params
        self.check_mode = False
        self.exited: dict[str, Any] | None = None
        self.failed: dict[str, Any] | None = None

    def exit_json(self, **kwargs: Any) -> None:
        self.exited = kwargs

    def fail_json(self, **kwargs: Any) -> None:
        self.failed = kwargs


class _FakeClient:
    def __init__(self, *, source_status: int = 200) -> None:
        self.source_status = source_status
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str) -> dict[str, Any]:
        assert path == "/api/observe/sources"
        return {
            "_status": self.source_status,
            "sources": [
                {"name": "prod-logs", "kind": "logs"},
                {"name": "prod-metrics", "kind": "metrics"},
            ],
        }

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        assert path == "/api/observe/query"
        self.posts.append((path, body))
        records = {
            "prod-logs": [
                {
                    "ts": 20.0,
                    "source": "prod-logs",
                    "kind": "logs",
                    "labels": {"trace_id": "trace-a", "service": "api", "host": "web-1"},
                }
            ],
            "prod-metrics": [
                {
                    "ts": 10.0,
                    "source": "prod-metrics",
                    "kind": "metrics",
                    "labels": {"trace_id": "trace-a", "service": "api", "host": "web-1"},
                }
            ],
        }
        return {"_status": 200, "records": records[body["source"]]}


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gludd_observe", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _params(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "op": "query_sources",
        "role": "observe_test",
        "seed": {},
        "kinds": ["logs", "metrics"],
        "by": "trace_id",
        "window_s": 300.0,
        "spec": {"query": "errors"},
        "start": 5.0,
        "end": 25.0,
        "daemon_url": "http://daemon:8000",
        "psk": "secret",
        "timeout": 30,
    }
    params.update(overrides)
    return params


def _run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    client: Any | None = None,
    **params: Any,
) -> tuple[_FakeAnsibleModule, _FakeClient]:
    module = _load_module()
    fake_module = _FakeAnsibleModule(_params(**params))
    fake_client = client or _FakeClient()
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake_module)
    monkeypatch.setattr(module, "GluddClient", lambda **_: fake_client)
    module.main()
    return fake_module, fake_client


def test_query_sources_fans_out_by_kind_and_preserves_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    ansible, client = _run(monkeypatch)

    assert ansible.failed is None
    facts = ansible.exited["ansible_facts"]["gludd_observe"]
    assert [record["ts"] for record in facts["records"]] == [10.0, 20.0]
    assert facts["errors"] == []
    assert [body["source"] for _, body in client.posts] == ["prod-logs", "prod-metrics"]
    assert all(body["spec"]["start"] == 5.0 for _, body in client.posts)
    assert all(body["spec"]["end"] == 25.0 for _, body in client.posts)


def test_correlate_incident_returns_trace_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    seed = {"ts": 15.0, "labels": {"trace_id": "trace-a"}, "kind": "events"}
    ansible, _ = _run(
        monkeypatch,
        op="correlate_incident",
        seed=seed,
        start=None,
        end=None,
    )

    assert ansible.failed is None
    groups = ansible.exited["ansible_facts"]["gludd_observe"]["groups"]
    assert len(groups["trace-a"]) == 3


def test_topology_is_json_safe_and_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    ansible, _ = _run(monkeypatch, op="topology", start=None, end=None)

    topology = ansible.exited["ansible_facts"]["gludd_observe"]["topology"]
    assert topology == {"services": {"api": ["web-1"]}, "hosts": {"web-1": ["api"]}}


def test_source_discovery_auth_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    ansible, _ = _run(monkeypatch, client=_FakeClient(source_status=401))

    assert ansible.exited is None
    assert ansible.failed["msg"] == "unauthorized (bad or missing PSK)"


def test_timeline_uses_the_same_bounded_fanout(monkeypatch: pytest.MonkeyPatch) -> None:
    ansible, _ = _run(monkeypatch, op="timeline")

    records = ansible.exited["ansible_facts"]["gludd_observe"]["records"]
    assert [record["ts"] for record in records] == [10.0, 20.0]


def test_correlate_requires_a_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    ansible, _ = _run(monkeypatch, op="correlate_incident", seed={})

    assert ansible.exited is None
    assert ansible.failed["msg"] == "correlate_incident requires a non-empty seed"


def test_source_discovery_transport_failure_is_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    ansible, _ = _run(monkeypatch, client=_FakeClient(source_status=503))

    assert ansible.exited is None
    assert ansible.failed["msg"] == "unable to discover registered observability sources"


@pytest.mark.parametrize(
    "sources",
    [
        "not-a-list",
        ["not-a-dict"],
        [{"name": "", "kind": "logs"}],
        [{"name": "same", "kind": "logs"}, {"name": "same", "kind": "metrics"}],
    ],
)
def test_invalid_source_catalog_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    sources: Any,
) -> None:
    class InvalidCatalogClient(_FakeClient):
        def get(self, path: str) -> dict[str, Any]:
            assert path == "/api/observe/sources"
            return {"_status": 200, "sources": sources}

    ansible, _ = _run(monkeypatch, client=InvalidCatalogClient())

    assert ansible.exited is None
    assert ansible.failed["msg"] == "invalid observability response or request"


@pytest.mark.parametrize(
    "response",
    [
        {"_status": 500},
        {"_status": 200, "records": "not-a-list"},
    ],
)
def test_remote_source_rejects_invalid_query_response(response: dict[str, Any]) -> None:
    module = _load_module()

    class QueryClient:
        def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
            return response

    source = module._RemoteSource(name="logs", kind="logs", client=QueryClient())
    with pytest.raises((RuntimeError, TypeError)):
        source.query({"query": "errors"})
