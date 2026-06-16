"""Unit tests for the self-contained ContainerdSource connector.

Mocked runner + path-confined pod-log reads; no real subprocess, no real socket.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from general_ludd.connectors.containerd import ContainerdConfig, ContainerdSource

# ---- canned crictl JSON payloads ------------------------------------------

PS_JSON = json.dumps(
    {
        "containers": [
            {
                "id": "abc123",
                "metadata": {"name": "nginx"},
                "state": "CONTAINER_RUNNING",
                "createdAt": "1700000000000000000",
                "labels": {
                    "io.kubernetes.pod.name": "web-0",
                    "io.kubernetes.pod.namespace": "prod",
                },
            }
        ]
    }
)

STATS_JSON = json.dumps(
    {
        "stats": [
            {
                "attributes": {
                    "metadata": {"name": "nginx"},
                    "labels": {
                        "io.kubernetes.pod.name": "web-0",
                        "io.kubernetes.pod.namespace": "prod",
                    },
                },
                "cpu": {
                    "timestamp": "1700000001000000000",
                    "usageCoreNanoSeconds": {"value": "123456789"},
                },
                "memory": {
                    "timestamp": "1700000001000000000",
                    "workingSetBytes": {"value": "8388608"},
                },
            }
        ]
    }
)

LOGS_TEXT = (
    "2024-01-01T00:00:00.000000000Z stdout F hello world\n"
    "2024-01-01T00:00:01.000000000Z stderr F boom failure\n"
)

VERSION_JSON = json.dumps({"version": "0.1.0", "runtimeName": "containerd"})


class RecordingRunner:
    """Argv-list runner that returns canned output keyed by the crictl subcmd."""

    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], *, timeout: float | None = None) -> str:
        assert isinstance(argv, list), "runner must receive an argv LIST, not a string"
        assert argv[0] == "crictl"
        self.calls.append(argv)
        sub = argv[3] if len(argv) > 3 else ""
        return self.responses.get(sub, "")


def _runner() -> RecordingRunner:
    return RecordingRunner(
        {
            "ps": PS_JSON,
            "stats": STATS_JSON,
            "logs": LOGS_TEXT,
            "version": VERSION_JSON,
        }
    )


# ---- contract surface ------------------------------------------------------


def test_kind_and_name_class_attrs() -> None:
    src = ContainerdSource({})
    assert ContainerdSource.KIND == "logs"
    assert src.name == "containerd"


def test_query_ps_normalizes_state_and_labels() -> None:
    src = ContainerdSource({}, runner=_runner())
    recs = src.query({"what": "ps"})
    assert len(recs) == 1
    r = recs[0]
    assert set(r) == {
        "ts",
        "source",
        "kind",
        "level_or_status",
        "message",
        "value",
        "labels",
        "raw",
    }
    assert r["kind"] == "logs"
    assert r["level_or_status"] == "CONTAINER_RUNNING"
    assert r["labels"] == {"pod": "web-0", "container": "nginx", "namespace": "prod"}
    assert r["raw"]["id"] == "abc123"


def test_query_stats_yields_cpu_and_memory_metrics() -> None:
    src = ContainerdSource({}, runner=_runner())
    recs = src.query({"what": "stats"})
    metrics = {r["labels"]["metric"]: r for r in recs}
    assert metrics["cpu_usage_core_ns"]["value"] == 123456789.0
    assert metrics["memory_working_set_bytes"]["value"] == 8388608.0
    assert all(r["kind"] == "metrics" for r in recs)
    assert metrics["cpu_usage_core_ns"]["labels"]["pod"] == "web-0"


def test_query_logs_parses_stream_and_level() -> None:
    src = ContainerdSource({}, runner=_runner())
    recs = src.query({"what": "logs", "container": "abc123"})
    assert [r["message"] for r in recs] == ["hello world", "boom failure"]
    assert recs[0]["level_or_status"] == "info"
    assert recs[1]["level_or_status"] == "error"
    assert recs[0]["labels"]["stream"] == "stdout"
    assert recs[0]["labels"]["container"] == "abc123"


def test_runner_receives_argv_list_no_shell() -> None:
    runner = _runner()
    src = ContainerdSource({}, runner=runner)
    src.query({"what": "ps"})
    assert runner.calls, "runner should have been invoked"
    for argv in runner.calls:
        assert isinstance(argv, list)
        assert argv[0] == "crictl"
        assert "--runtime-endpoint" in argv
        # No shell metacharacters smuggled into a single arg.
        assert not any(";" in a or "&&" in a or "|" in a for a in argv)


# ---- secret resolution -----------------------------------------------------


def test_auth_token_pulled_from_env() -> None:
    cfg = ContainerdConfig(auth_token_env="CRICTL_TOKEN")
    runner = _runner()
    src = ContainerdSource(cfg, runner=runner, env={"CRICTL_TOKEN": "s3cr3t"})
    src.query({"what": "ps"})
    argv = runner.calls[0]
    assert "--auth-token" in argv
    assert argv[argv.index("--auth-token") + 1] == "s3cr3t"


def test_no_auth_token_when_env_absent() -> None:
    cfg = ContainerdConfig(auth_token_env="CRICTL_TOKEN")
    runner = _runner()
    src = ContainerdSource(cfg, runner=runner, env={})
    src.query({"what": "ps"})
    assert "--auth-token" not in runner.calls[0]


# ---- socket confinement ----------------------------------------------------


def test_socket_outside_allowlist_rejected() -> None:
    cfg = ContainerdConfig(runtime_endpoint="/etc/evil.sock")
    src = ContainerdSource(cfg, runner=_runner())
    with pytest.raises(ValueError, match="outside allowed dirs"):
        src.query({"what": "ps"})


def test_socket_traversal_escape_rejected() -> None:
    cfg = ContainerdConfig(runtime_endpoint="/run/../etc/evil.sock")
    src = ContainerdSource(cfg, runner=_runner())
    with pytest.raises(ValueError):
        src.query({"what": "ps"})


def test_relative_socket_rejected() -> None:
    cfg = ContainerdConfig(runtime_endpoint="containerd.sock")
    src = ContainerdSource(cfg, runner=_runner())
    with pytest.raises(ValueError, match="absolute"):
        src.query({"what": "ps"})


def test_unix_scheme_socket_accepted_and_confined() -> None:
    cfg = ContainerdConfig(runtime_endpoint="unix:///run/containerd/containerd.sock")
    runner = _runner()
    src = ContainerdSource(cfg, runner=runner)
    src.query({"what": "ps"})
    argv = runner.calls[0]
    assert "unix:///run/containerd/containerd.sock" in argv


# ---- pod-log path confinement ---------------------------------------------


def test_pod_log_read_confined(tmp_path: Any) -> None:
    root = tmp_path / "pods"
    logdir = root / "prod_web-0_uid" / "nginx"
    logdir.mkdir(parents=True)
    (logdir / "0.log").write_text(LOGS_TEXT, encoding="utf-8")
    cfg = ContainerdConfig(pod_log_root=str(root), use_runner=False)
    src = ContainerdSource(cfg)  # no runner -> pod-log path
    recs = src.query(
        {
            "what": "pod_logs",
            "pod_log_rel": "prod_web-0_uid/nginx/0.log",
            "pod": "web-0",
            "container": "nginx",
            "namespace": "prod",
        }
    )
    assert [r["message"] for r in recs] == ["hello world", "boom failure"]
    assert recs[1]["level_or_status"] == "error"


def test_pod_log_traversal_rejected(tmp_path: Any) -> None:
    cfg = ContainerdConfig(pod_log_root=str(tmp_path / "pods"), use_runner=False)
    src = ContainerdSource(cfg)
    with pytest.raises(ValueError, match="escapes root"):
        src.query({"what": "pod_logs", "pod_log_rel": "../../etc/passwd"})


# ---- health ----------------------------------------------------------------


def test_health_ok_with_runner() -> None:
    src = ContainerdSource({}, runner=_runner())
    h = src.health()
    assert h["ok"] is True
    assert "crictl reachable" in h["detail"]


def test_health_not_ok_on_bad_socket() -> None:
    cfg = ContainerdConfig(runtime_endpoint="/etc/evil.sock")
    src = ContainerdSource(cfg, runner=_runner())
    h = src.health()
    assert h["ok"] is False
    assert "socket-confinement" in h["detail"]


def test_health_never_raises_on_runner_explosion() -> None:
    def boom(argv: list[str], *, timeout: float | None = None) -> str:
        raise RuntimeError("connection refused")

    src = ContainerdSource({}, runner=boom)
    h = src.health()
    assert h["ok"] is False
    assert "unreachable" in h["detail"]


def test_health_pod_log_root_present(tmp_path: Any) -> None:
    cfg = ContainerdConfig(pod_log_root=str(tmp_path), use_runner=False)
    src = ContainerdSource(cfg)
    h = src.health()
    assert h["ok"] is True


def test_health_pod_log_root_missing() -> None:
    cfg = ContainerdConfig(pod_log_root="/no/such/dir/here", use_runner=False)
    src = ContainerdSource(cfg)
    h = src.health()
    assert h["ok"] is False
    assert "missing" in h["detail"]
