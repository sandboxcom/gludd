"""Unit tests for the OpenShiftSource connector (mocked transport + runner)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from general_ludd.connectors.openshift import (
    OpenShiftSource,
    SSRFError,
    assert_url_allowed,
    host_is_blocked,
    token_from_env,
)

CANNED_EVENTS = {
    "kind": "EventList",
    "items": [
        {
            "type": "Warning",
            "reason": "Unhealthy",
            "message": "Readiness probe failed",
            "count": 3,
            "lastTimestamp": "2026-06-16T17:05:00Z",
            "involvedObject": {
                "kind": "Pod",
                "name": "api-1",
                "namespace": "shop",
            },
            "metadata": {"namespace": "shop"},
        }
    ],
}


class _CannedResponse:
    def __init__(self, status_code: int, payload: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        return self._payload


class _CannedTransport:
    def __init__(
        self, status_code: int = 200, payload: Any = None, text: str = ""
    ) -> None:
        self.status_code = status_code
        self.payload = payload if payload is not None else {"items": []}
        self.text = text
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        verify: str | bool = True,
    ) -> _CannedResponse:
        self.calls.append(
            {"url": url, "headers": headers, "params": params, "verify": verify}
        )
        return _CannedResponse(self.status_code, self.payload, self.text)


class _CannedRunner:
    def __init__(self, rc: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.rc = rc
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        self.calls.append(argv)
        return self.rc, self.stdout, self.stderr


def _public_config(**extra: Any) -> dict[str, Any]:
    cfg = {"base_url": "https://ocp.example.com:6443", "name": "ocp"}
    cfg.update(extra)
    return cfg


class TestHttpEvents:
    def test_canned_events_normalize(self) -> None:
        transport = _CannedTransport(payload=CANNED_EVENTS)
        src = OpenShiftSource(config=_public_config(), transport=transport)
        records = src.query({"resource": "events"})

        assert len(records) == 1
        ev = records[0]
        assert ev["kind"] == "events"
        assert ev["source"] == "ocp"
        assert ev["level_or_status"] == "warning"
        assert ev["value"] == 3
        assert ev["labels"] == {
            "namespace": "shop",
            "kind": "Pod",
            "name": "api-1",
            "reason": "Unhealthy",
        }

    def test_kind_class_attr(self) -> None:
        assert OpenShiftSource.KIND == "events"

    def test_events_time_bound_params(self) -> None:
        transport = _CannedTransport(payload=CANNED_EVENTS)
        src = OpenShiftSource(
            config=_public_config(limit=20, timeout_seconds=8), transport=transport
        )
        src.query({})
        params = transport.calls[0]["params"]
        assert params["limit"] == 20
        assert params["timeoutSeconds"] == 8
        assert "watch" not in params


class TestHttpPodLogs:
    def test_pod_logs_normalize_to_logs_kind(self) -> None:
        transport = _CannedTransport(text="line one\nline two\n")
        src = OpenShiftSource(
            config=_public_config(namespace="shop"), transport=transport
        )
        records = src.query({"resource": "logs", "pod": "api-1"})
        assert [r["message"] for r in records] == ["line one", "line two"]
        assert all(r["kind"] == "logs" for r in records)
        assert records[0]["labels"] == {
            "namespace": "shop",
            "kind": "Pod",
            "name": "api-1",
        }
        url = transport.calls[0]["url"]
        assert url.endswith("/api/v1/namespaces/shop/pods/api-1/log")

    def test_pod_logs_are_size_and_time_bound(self) -> None:
        transport = _CannedTransport(text="x")
        src = OpenShiftSource(
            config=_public_config(namespace="shop"), transport=transport
        )
        src.query(
            {"resource": "logs", "pod": "api-1", "since_seconds": 60, "tail_lines": 50}
        )
        params = transport.calls[0]["params"]
        assert params["sinceSeconds"] == 60
        assert params["tailLines"] == 50

    def test_pod_logs_missing_pod_returns_empty(self) -> None:
        transport = _CannedTransport(text="line")
        src = OpenShiftSource(
            config=_public_config(namespace="shop"), transport=transport
        )
        assert src.query({"resource": "logs"}) == []

    def test_invalid_pod_name_rejected(self) -> None:
        transport = _CannedTransport(text="line")
        src = OpenShiftSource(
            config=_public_config(namespace="shop"), transport=transport
        )
        with pytest.raises(ValueError):
            src.query({"resource": "logs", "pod": "../secret"})


class TestRunnerMode:
    def test_events_via_oc_runner(self) -> None:
        runner = _CannedRunner(stdout=json.dumps(CANNED_EVENTS))
        src = OpenShiftSource(
            config={"name": "ocp", "namespace": "shop"}, runner=runner
        )
        records = src.query({"resource": "events"})
        assert len(records) == 1
        assert records[0]["labels"]["reason"] == "Unhealthy"
        # argv is a list, no shell, namespace flagged.
        argv = runner.calls[0]
        assert isinstance(argv, list)
        assert argv[:3] == ["oc", "get", "events"]
        assert "-n" in argv and "shop" in argv

    def test_pod_logs_via_oc_runner(self) -> None:
        runner = _CannedRunner(stdout="a\nb\n")
        src = OpenShiftSource(
            config={"name": "ocp", "namespace": "shop"}, runner=runner
        )
        records = src.query({"resource": "logs", "pod": "api-1"})
        assert [r["message"] for r in records] == ["a", "b"]
        argv = runner.calls[0]
        assert argv[:2] == ["oc", "logs"]
        assert "api-1" in argv
        assert any(part.startswith("--since=") for part in argv)
        assert any(part.startswith("--tail=") for part in argv)

    def test_runner_mode_invalid_namespace_rejected(self) -> None:
        runner = _CannedRunner(stdout=json.dumps(CANNED_EVENTS))
        src = OpenShiftSource(config={"namespace": "Bad NS"}, runner=runner)
        with pytest.raises(ValueError):
            src.query({"resource": "events"})


class TestSSRF:
    @pytest.mark.parametrize(
        "host",
        [
            "https://127.0.0.1:6443",
            "https://10.0.0.9:6443",
            "https://169.254.169.254/x",
            "http://localhost:6443",
        ],
    )
    def test_internal_host_rejected_by_default(self, host: str) -> None:
        with pytest.raises(SSRFError):
            OpenShiftSource(config={"base_url": host}, transport=_CannedTransport())

    def test_internal_host_allowed_opt_in(self) -> None:
        src = OpenShiftSource(
            config={"base_url": "https://10.0.0.9:6443", "allow_private": True},
            transport=_CannedTransport(payload=CANNED_EVENTS),
        )
        assert len(src.query({})) == 1

    def test_helpers(self) -> None:
        assert host_is_blocked("10.0.0.9") is True
        assert host_is_blocked("ocp.example.com") is False
        assert_url_allowed("https://ocp.example.com", allow_private=False)
        assert token_from_env(None) is None


class TestToken:
    def test_bearer_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCP_TOKEN", "sha256~abc")
        transport = _CannedTransport(payload=CANNED_EVENTS)
        src = OpenShiftSource(
            config=_public_config(token_env="OCP_TOKEN"), transport=transport
        )
        src.query({})
        assert transport.calls[0]["headers"]["Authorization"] == "Bearer sha256~abc"

    def test_no_token_no_header(self) -> None:
        transport = _CannedTransport(payload=CANNED_EVENTS)
        src = OpenShiftSource(config=_public_config(), transport=transport)
        src.query({})
        assert "Authorization" not in transport.calls[0]["headers"]


class TestHealth:
    def test_http_health_ok(self) -> None:
        src = OpenShiftSource(
            config=_public_config(), transport=_CannedTransport(status_code=200)
        )
        assert src.health()["ok"] is True

    def test_http_health_not_ok(self) -> None:
        src = OpenShiftSource(
            config=_public_config(), transport=_CannedTransport(status_code=401)
        )
        h = src.health()
        assert h["ok"] is False
        assert "401" in h["detail"]

    def test_runner_health_ok(self) -> None:
        src = OpenShiftSource(config={"name": "ocp"}, runner=_CannedRunner(rc=0))
        assert src.health()["ok"] is True

    def test_runner_health_not_ok(self) -> None:
        src = OpenShiftSource(
            config={"name": "ocp"}, runner=_CannedRunner(rc=1, stderr="no auth")
        )
        h = src.health()
        assert h["ok"] is False
        assert "no auth" in h["detail"]

    def test_health_never_raises(self) -> None:
        class _Exploding:
            def get(self, *a: Any, **k: Any) -> Any:
                raise ConnectionError("boom")

        src = OpenShiftSource(config=_public_config(), transport=_Exploding())
        h = src.health()
        assert h["ok"] is False
        assert "probe error" in h["detail"]

    def test_health_no_transport(self) -> None:
        src = OpenShiftSource(config=_public_config())
        assert src.health()["ok"] is False


class TestQueryRobustness:
    def test_http_error_returns_empty(self) -> None:
        src = OpenShiftSource(
            config=_public_config(),
            transport=_CannedTransport(status_code=500, payload=CANNED_EVENTS),
        )
        assert src.query({}) == []

    def test_runner_nonzero_returns_empty(self) -> None:
        runner = _CannedRunner(rc=2, stdout=json.dumps(CANNED_EVENTS))
        src = OpenShiftSource(config={"name": "ocp"}, runner=runner)
        assert src.query({"resource": "events"}) == []
