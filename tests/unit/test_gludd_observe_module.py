"""HTTP compatibility contracts for the ``gludd_observe`` Ansible module."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT
    / "collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_observe.py"
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
    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self.response = response or {
            "_status": 200,
            "result": {"records": [{"ts": 1.0}], "errors": [], "role": "observe_test"},
        }
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.posts.append((path, body))
        return self.response


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
    client: _FakeClient | None = None,
    **params: Any,
) -> tuple[_FakeAnsibleModule, _FakeClient]:
    module = _load_module()
    ansible = _FakeAnsibleModule(_params(**params))
    fake_client = client or _FakeClient()
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: ansible)
    monkeypatch.setattr(module, "GluddClient", lambda **_: fake_client)
    module.main()
    return ansible, fake_client


def test_query_sources_uses_one_authenticated_facade_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ansible, client = _run(monkeypatch)

    assert ansible.failed is None
    assert ansible.exited is not None
    assert ansible.exited["ansible_facts"]["gludd_observe"]["records"] == [{"ts": 1.0}]
    assert client.posts == [
        (
            "/api/observe/facade",
            {
                "operation": "query_sources",
                "role": "observe_test",
                "seed": {},
                "kinds": ["logs", "metrics"],
                "by": "trace_id",
                "window_s": 300.0,
                "spec": {"query": "errors"},
                "start": 5.0,
                "end": 25.0,
                "timeout_seconds": 30,
            },
        )
    ]


def test_correlate_requires_seed_before_http(monkeypatch: pytest.MonkeyPatch) -> None:
    ansible, client = _run(monkeypatch, op="correlate_incident", seed={})

    assert ansible.exited is None
    assert ansible.failed is not None
    assert ansible.failed["msg"] == "correlate_incident requires a non-empty seed"
    assert client.posts == []


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({"_status": 401}, "unauthorized (bad or missing PSK)"),
        ({"_status": 503, "detail": "offline"}, "offline"),
        ({"_status": 200, "result": []}, "invalid observability response or request"),
    ],
)
def test_http_failures_are_schema_stable(
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, Any],
    message: str,
) -> None:
    ansible, _ = _run(monkeypatch, client=_FakeClient(response))

    assert ansible.exited is None
    assert ansible.failed is not None
    assert ansible.failed["msg"] == message


def test_topology_payload_is_forwarded_without_local_core_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    topology = {"services": {"api": ["web-1"]}, "hosts": {"web-1": ["api"]}}
    ansible, client = _run(
        monkeypatch,
        op="topology",
        client=_FakeClient({"_status": 200, "result": {"topology": topology, "errors": []}}),
    )

    assert ansible.failed is None
    assert ansible.exited is not None
    assert ansible.exited["ansible_facts"]["gludd_observe"]["topology"] == topology
    assert client.posts[0][1]["operation"] == "topology"
