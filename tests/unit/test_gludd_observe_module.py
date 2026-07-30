"""Behavioral tests for the ``gludd_observe`` Ansible module."""

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


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gludd_observe", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeAnsibleModule:
    def __init__(self, params: dict[str, Any], *, check_mode: bool = False) -> None:
        self.params = params
        self.check_mode = check_mode
        self.exited: dict[str, Any] | None = None
        self.failed: dict[str, Any] | None = None

    def exit_json(self, **kwargs: Any) -> None:
        self.exited = kwargs

    def fail_json(self, **kwargs: Any) -> None:
        self.failed = kwargs


class _FakeClient:
    def __init__(
        self,
        *,
        sources: list[dict[str, Any]] | None = None,
        get_response: dict[str, Any] | None = None,
        query_responses: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._get_response = get_response or {
            "sources": sources or [],
            "count": len(sources or []),
            "_status": 200,
        }
        self._query_responses = query_responses or {}
        self.get_calls: list[tuple[str, Any]] = []
        self.post_calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str, params: Any = None) -> dict[str, Any]:
        self.get_calls.append((path, params))
        return dict(self._get_response)

    def post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = dict(body or {})
        self.post_calls.append((path, request))
        return dict(
            self._query_responses.get(
                str(request.get("source")),
                {"records": [], "count": 0, "_status": 200},
            )
        )


def _params(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "op": "query_sources",
        "role": "molecule_observe_probe",
        "kinds": ["logs"],
        "window": [],
        "seed": {},
        "by": "trace_id",
        "window_s": 300.0,
        "spec": {},
        "start": None,
        "end": None,
        "correlate_by": "",
        "daemon_url": "http://localhost:8000",
        "psk": "",
        "timeout": 30,
    }
    params.update(overrides)
    return params


def _record(
    source: str,
    kind: str,
    ts: float,
    *,
    trace_id: str = "",
    service: str = "",
    host: str = "",
) -> dict[str, Any]:
    labels = {
        key: value
        for key, value in {
            "trace_id": trace_id,
            "service": service,
            "host": host,
        }.items()
        if value
    }
    return {
        "source": source,
        "kind": kind,
        "ts": ts,
        "level_or_status": "info",
        "message": f"{source}-{ts}",
        "value": None,
        "labels": labels,
        "raw": {},
    }


def _run(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    params: dict[str, Any],
    client: _FakeClient,
    *,
    check_mode: bool = False,
) -> _FakeAnsibleModule:
    fake = _FakeAnsibleModule(params, check_mode=check_mode)
    monkeypatch.setattr(module, "AnsibleModule", lambda **_: fake)
    monkeypatch.setattr(module, "GluddClient", lambda **_: client)
    module.main()
    return fake


@pytest.fixture
def module() -> ModuleType:
    return _load_module()


def test_query_sources_returns_sorted_records_and_optional_groups(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = [
        {"name": "loki", "kind": "logs", "family": "pull"},
        {"name": "prometheus", "kind": "metrics", "family": "pull"},
    ]
    client = _FakeClient(
        sources=sources,
        query_responses={
            "loki": {
                "records": [_record("loki", "logs", 20.0, trace_id="T1")],
                "_status": 200,
            },
            "prometheus": {
                "records": [
                    _record("prometheus", "metrics", 10.0, trace_id="T1")
                ],
                "_status": 200,
            },
        },
    )

    fake = _run(
        module,
        monkeypatch,
        _params(
            kinds=["logs", "metrics"],
            spec={"service": "checkout"},
            start=5.0,
            end=25.0,
            correlate_by="trace_id",
        ),
        client,
    )

    assert fake.failed is None
    assert fake.exited is not None
    assert fake.exited["changed"] is False
    facts = fake.exited["ansible_facts"]["gludd_observe"]
    assert [record["source"] for record in facts["records"]] == [
        "prometheus",
        "loki",
    ]
    assert len(facts["groups"]["T1"]) == 2
    assert facts["source_count"] == 2
    assert facts["role"] == "molecule_observe_probe"
    assert client.get_calls == [("/api/observe/sources", None)]
    assert {call[1]["source"] for call in client.post_calls} == {
        "loki",
        "prometheus",
    }
    assert all(call[1]["spec"]["start"] == 5.0 for call in client.post_calls)
    assert all(call[1]["spec"]["end"] == 25.0 for call in client.post_calls)


def test_timeline_uses_window_kinds_and_filters_bounds(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        sources=[
            {"name": "loki", "kind": "logs"},
            {"name": "prometheus", "kind": "metrics"},
        ],
        query_responses={
            "prometheus": {
                "records": [
                    _record("prometheus", "metrics", 1.0),
                    _record("prometheus", "metrics", 15.0),
                    _record("prometheus", "metrics", 30.0),
                ],
                "_status": 200,
            }
        },
    )

    fake = _run(
        module,
        monkeypatch,
        _params(op="timeline", window=["metrics"], start=10.0, end=20.0),
        client,
    )

    assert fake.failed is None
    assert fake.exited is not None
    facts = fake.exited["ansible_facts"]["gludd_observe"]
    assert [record["ts"] for record in facts["records"]] == [15.0]
    assert [call[1]["source"] for call in client.post_calls] == ["prometheus"]


def test_topology_returns_json_safe_sorted_adjacency(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        sources=[{"name": "prometheus", "kind": "metrics"}],
        query_responses={
            "prometheus": {
                "records": [
                    _record(
                        "prometheus",
                        "metrics",
                        1.0,
                        service="checkout",
                        host="web-02",
                    ),
                    _record(
                        "prometheus",
                        "metrics",
                        2.0,
                        service="checkout",
                        host="web-01",
                    ),
                ],
                "_status": 200,
            }
        },
    )

    fake = _run(
        module,
        monkeypatch,
        _params(op="topology", kinds=["metrics"]),
        client,
    )

    assert fake.failed is None
    assert fake.exited is not None
    topology = fake.exited["ansible_facts"]["gludd_observe"]["topology"]
    assert topology == {
        "services": {"checkout": ["web-01", "web-02"]},
        "hosts": {
            "web-01": ["checkout"],
            "web-02": ["checkout"],
        },
    }


def test_correlate_incident_includes_seed_in_group(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = _record("pagerduty", "incidents", 10.0, trace_id="T7")
    client = _FakeClient(
        sources=[{"name": "loki", "kind": "logs"}],
        query_responses={
            "loki": {
                "records": [_record("loki", "logs", 11.0, trace_id="T7")],
                "_status": 200,
            }
        },
    )

    fake = _run(
        module,
        monkeypatch,
        _params(
            op="correlate_incident",
            seed=seed,
            kinds=["logs"],
            by="trace_id",
            window_s=60.0,
        ),
        client,
    )

    assert fake.failed is None
    assert fake.exited is not None
    group = fake.exited["ansible_facts"]["gludd_observe"]["groups"]["T7"]
    assert {record["source"] for record in group} == {"pagerduty", "loki"}
    assert client.post_calls[0][1]["spec"]["start"] == -50.0
    assert client.post_calls[0][1]["spec"]["end"] == 70.0


def test_one_source_failure_is_isolated_and_redacted(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        sources=[
            {"name": "loki", "kind": "logs"},
            {"name": "broken", "kind": "logs"},
        ],
        query_responses={
            "loki": {
                "records": [_record("loki", "logs", 1.0)],
                "_status": 200,
            },
            "broken": {
                "_error": "https://secret.internal/token=very-secret",
                "_status": 0,
            },
        },
    )

    fake = _run(module, monkeypatch, _params(), client)

    assert fake.failed is None
    assert fake.exited is not None
    facts = fake.exited["ansible_facts"]["gludd_observe"]
    assert len(facts["errors"]) == 1
    error = facts["errors"][0]
    assert error["source"] == "broken"
    assert error["message"] == "query failed"
    assert "secret" not in str(error)


@pytest.mark.parametrize(
    "get_response, expected",
    [
        (
            {"_error": "connection refused", "_status": 0},
            "connection refused",
        ),
        (
            {"detail": "unauthorized", "_status": 401},
            "unauthorized",
        ),
        (
            {"sources": {"loki": "not-a-list"}, "_status": 200},
            "invalid sources payload",
        ),
        (
            {"sources": [{"name": "", "kind": "logs"}], "_status": 200},
            "invalid source metadata",
        ),
    ],
)
def test_discovery_errors_fail_closed(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    get_response: dict[str, Any],
    expected: str,
) -> None:
    fake = _run(
        module,
        monkeypatch,
        _params(),
        _FakeClient(get_response=get_response),
    )

    assert fake.exited is None
    assert fake.failed is not None
    assert expected in fake.failed["msg"].lower()


def test_invalid_query_records_are_isolated_as_source_failure(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        sources=[{"name": "bad-shape", "kind": "logs"}],
        query_responses={
            "bad-shape": {
                "records": {"not": "a list"},
                "_status": 200,
            }
        },
    )

    fake = _run(module, monkeypatch, _params(), client)

    assert fake.failed is None
    assert fake.exited is not None
    facts = fake.exited["ansible_facts"]["gludd_observe"]
    assert facts["records"][0]["level_or_status"] == "error"
    assert facts["errors"][0]["source"] == "bad-shape"


def test_empty_registry_returns_empty_read_only_facts_in_check_mode(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(sources=[])

    fake = _run(
        module,
        monkeypatch,
        _params(),
        client,
        check_mode=True,
    )

    assert fake.failed is None
    assert fake.exited is not None
    assert fake.exited["changed"] is False
    facts = fake.exited["ansible_facts"]["gludd_observe"]
    assert facts["records"] == []
    assert facts["errors"] == []
    assert client.post_calls == []


def test_unknown_operation_fails_defensively(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _run(
        module,
        monkeypatch,
        _params(op="unknown"),
        _FakeClient(sources=[]),
    )

    assert fake.exited is None
    assert fake.failed is not None
    assert "unsupported operation" in fake.failed["msg"]


@pytest.mark.parametrize(
    "role",
    [
        "observe_incident_triage",
        "observe_latency_regression",
        "observe_error_spike_rca",
        "observe_deploy_correlator",
        "observe_saturation_capacity",
        "observe_security_signal",
        "molecule_observe_probe",
    ],
)
def test_observe_roles_have_local_only_network_grants(
    module: ModuleType,
    role: str,
) -> None:
    policy = module.for_role(role)

    assert policy.check_network_host("localhost") == "localhost"
    assert policy.check_network_host("127.0.0.1") == "127.0.0.1"
    with pytest.raises(module.CapabilityError):
        policy.check_network_host("telemetry.example.com")


def test_unknown_role_is_denied_before_source_discovery(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(sources=[])

    fake = _run(
        module,
        monkeypatch,
        _params(role="not-granted"),
        client,
    )

    assert fake.exited is None
    assert fake.failed is not None
    assert "capability policy" in fake.failed["msg"]
    assert client.get_calls == []


def test_remote_daemon_is_denied_before_source_discovery(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(sources=[])

    fake = _run(
        module,
        monkeypatch,
        _params(daemon_url="https://telemetry.example.com"),
        client,
    )

    assert fake.exited is None
    assert fake.failed is not None
    assert "capability policy" in fake.failed["msg"]
    assert client.get_calls == []
