"""Unit tests for the K8sEventsSource connector (mocked HTTP transport)."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.k8s_events import (
    K8sEventsSource,
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
            "reason": "BackOff",
            "message": "Back-off restarting failed container",
            "count": 7,
            "lastTimestamp": "2026-06-16T17:00:00Z",
            "involvedObject": {
                "kind": "Pod",
                "name": "web-abc",
                "namespace": "prod",
            },
            "source": {"component": "kubelet"},
            "metadata": {"namespace": "prod"},
        },
        {
            "type": "Normal",
            "reason": "Scheduled",
            "message": "Successfully assigned prod/web-abc",
            "eventTime": "2026-06-16T16:59:00Z",
            "involvedObject": {
                "kind": "Pod",
                "name": "web-abc",
                "namespace": "prod",
            },
            "source": {"component": "default-scheduler"},
            "metadata": {"namespace": "prod"},
        },
    ],
}


class _CannedResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _CannedTransport:
    def __init__(self, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self.payload = payload if payload is not None else {"items": []}
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        verify: str | bool = True,
        timeout: float | None = None,
    ) -> _CannedResponse:
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "params": params,
                "verify": verify,
                "timeout": timeout,
            }
        )
        return _CannedResponse(self.status_code, self.payload)


def _public_config(**extra: Any) -> dict[str, Any]:
    cfg = {"base_url": "https://api.cluster.example.com:6443", "name": "k8s"}
    cfg.update(extra)
    return cfg


class TestNormalization:
    def test_canned_events_normalize(self) -> None:
        transport = _CannedTransport(payload=CANNED_EVENTS)
        src = K8sEventsSource(config=_public_config(), transport=transport)
        records = src.query({})

        assert len(records) == 2
        warn = records[0]
        assert warn["kind"] == "events"
        assert warn["source"] == "k8s"
        assert warn["level_or_status"] == "warning"
        assert warn["message"].startswith("Back-off")
        assert warn["value"] == 7
        assert warn["ts"] == "2026-06-16T17:00:00Z"
        assert warn["labels"] == {
            "namespace": "prod",
            "kind": "Pod",
            "name": "web-abc",
            "reason": "BackOff",
            "component": "kubelet",
        }
        assert records[1]["level_or_status"] == "info"
        assert records[1]["ts"] == "2026-06-16T16:59:00Z"

    def test_kind_class_attr(self) -> None:
        assert K8sEventsSource.KIND == "events"


class TestTimeBoundAndWatch:
    def test_query_is_bounded_not_watch(self) -> None:
        transport = _CannedTransport(payload=CANNED_EVENTS)
        src = K8sEventsSource(
            config=_public_config(limit=10, timeout_seconds=5), transport=transport
        )
        src.query({})
        params = transport.calls[0]["params"]
        assert params["limit"] == 10
        assert params["timeoutSeconds"] == 5
        # Never a watch.
        assert "watch" not in params

    def test_namespaced_url(self) -> None:
        transport = _CannedTransport(payload=CANNED_EVENTS)
        src = K8sEventsSource(
            config=_public_config(namespace="prod"), transport=transport
        )
        src.query({})
        assert transport.calls[0]["url"].endswith(
            "/api/v1/namespaces/prod/events"
        )

    def test_invalid_namespace_rejected(self) -> None:
        transport = _CannedTransport(payload=CANNED_EVENTS)
        src = K8sEventsSource(
            config=_public_config(namespace="../etc"), transport=transport
        )
        with pytest.raises(ValueError):
            src.query({})


class TestSSRF:
    @pytest.mark.parametrize(
        "host",
        [
            "https://127.0.0.1:6443",
            "https://10.1.2.3:6443",
            "https://192.168.0.5:6443",
            "https://172.16.4.4:6443",
            "https://169.254.169.254/latest",  # cloud metadata
            "http://localhost:8080",
        ],
    )
    def test_internal_host_rejected_by_default(self, host: str) -> None:
        with pytest.raises(SSRFError):
            K8sEventsSource(
                config={"base_url": host}, transport=_CannedTransport()
            )

    def test_internal_host_allowed_with_opt_in(self) -> None:
        # Clusters are internal; allow_private must let them through.
        src = K8sEventsSource(
            config={"base_url": "https://10.1.2.3:6443", "allow_private": True},
            transport=_CannedTransport(payload=CANNED_EVENTS),
        )
        assert src.query({}) == src.query({})  # no SSRFError raised

    def test_public_host_allowed(self) -> None:
        assert_url_allowed(
            "https://api.cluster.example.com:6443", allow_private=False
        )

    def test_host_is_blocked_no_dns(self) -> None:
        # A hostname that is not literally in the block list is NOT resolved.
        assert host_is_blocked("api.cluster.example.com") is False
        assert host_is_blocked("127.0.0.1") is True
        assert host_is_blocked("localhost") is True

    def test_bad_scheme_rejected(self) -> None:
        with pytest.raises(SSRFError):
            assert_url_allowed("ftp://example.com", allow_private=False)

    # Canonical SSRF guard coverage — 100.100.100.200 is the Alibaba metadata IP
    # the shared general_ludd.security.ssrf.is_url_blocked guarantees.
    @pytest.mark.parametrize(
        "bad_url",
        [
            "http://localhost/",
            "http://metadata.google.internal/",
            "http://169.254.169.254/",
            "http://100.100.100.200/",
        ],
    )
    def test_canonical_ssrf_urls_rejected(self, bad_url: str) -> None:
        with pytest.raises(SSRFError):
            K8sEventsSource(config={"base_url": bad_url}, transport=_CannedTransport())

    def test_allow_private_permits_localhost(self) -> None:
        # Clusters are internal; allow_private must let a loopback API server in.
        src = K8sEventsSource(
            config={"base_url": "http://localhost:6443", "allow_private": True},
            transport=_CannedTransport(payload=CANNED_EVENTS),
        )
        assert src.query({}) == src.query({})  # no SSRFError raised

    def test_public_base_url_constructs_after_consolidation(self) -> None:
        src = K8sEventsSource(config=_public_config(), transport=_CannedTransport())
        assert src.name == "k8s"


class TestToken:
    def test_token_from_env_used_as_bearer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("K8S_TOKEN", "tok-123")
        transport = _CannedTransport(payload=CANNED_EVENTS)
        src = K8sEventsSource(
            config=_public_config(token_env="K8S_TOKEN"), transport=transport
        )
        src.query({})
        assert transport.calls[0]["headers"]["Authorization"] == "Bearer tok-123"

    def test_no_token_no_auth_header(self) -> None:
        transport = _CannedTransport(payload=CANNED_EVENTS)
        src = K8sEventsSource(config=_public_config(), transport=transport)
        src.query({})
        assert "Authorization" not in transport.calls[0]["headers"]

    def test_token_from_env_helper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("X_TOK", "v")
        assert token_from_env("X_TOK") == "v"
        assert token_from_env(None) is None
        assert token_from_env("NOPE_NOT_SET") is None

    def test_ca_cert_forwarded_as_verify(self) -> None:
        transport = _CannedTransport(payload=CANNED_EVENTS)
        src = K8sEventsSource(
            config=_public_config(ca_cert="/etc/ca.pem"), transport=transport
        )
        src.query({})
        assert transport.calls[0]["verify"] == "/etc/ca.pem"


class TestHealth:
    def test_health_ok(self) -> None:
        src = K8sEventsSource(
            config=_public_config(), transport=_CannedTransport(status_code=200)
        )
        h = src.health()
        assert h["ok"] is True

    def test_health_not_ok_on_http_error(self) -> None:
        src = K8sEventsSource(
            config=_public_config(), transport=_CannedTransport(status_code=403)
        )
        h = src.health()
        assert h["ok"] is False
        assert h["detail"] == "events API returned HTTP 403"

    def test_health_never_raises(self) -> None:
        class _Exploding:
            def get(self, *a: Any, **k: Any) -> Any:
                raise ConnectionError("dns fail")

        src = K8sEventsSource(config=_public_config(), transport=_Exploding())
        h = src.health()
        assert h["ok"] is False
        assert h["detail"] == "health check failed"

    def test_health_no_transport(self) -> None:
        src = K8sEventsSource(config=_public_config())
        h = src.health()
        assert h["ok"] is False


class TestQueryRobustness:
    def test_http_error_returns_empty(self) -> None:
        src = K8sEventsSource(
            config=_public_config(),
            transport=_CannedTransport(status_code=500, payload=CANNED_EVENTS),
        )
        assert src.query({}) == []

    def test_query_without_transport_raises(self) -> None:
        src = K8sEventsSource(config=_public_config())
        with pytest.raises(RuntimeError):
            src.query({})
