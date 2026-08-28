"""Tests: SSRF redirect guard — pagerduty, opsgenie, monday transports.

Verifies that:
* PagerDuty and Opsgenie _DefaultTransport.get passes follow_redirects=False
  to httpx.get, so the library never silently follows a 3xx redirect.
* Monday _default_transport raises urllib.error.HTTPError on a 302 instead of
  following it (via _NoRedirectHandler).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# PagerDuty
# ---------------------------------------------------------------------------


def test_pagerduty_transport_passes_follow_redirects_false() -> None:
    """_DefaultTransport.get must forward follow_redirects=False to httpx.get."""
    from general_ludd.connectors.pagerduty import _DefaultTransport

    captured: dict[str, Any] = {}

    def fake_httpx_get(url: str, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        resp = SimpleNamespace(status_code=200)
        cast(Any, resp).json = lambda: {}
        return resp

    transport = _DefaultTransport()
    with patch("httpx.get", side_effect=fake_httpx_get):
        transport.get(
            "https://api.pagerduty.com/incidents",
            headers={"Authorization": "Token tok"},
            params={"limit": 100},
            timeout=10.0,
        )

    assert "follow_redirects" in captured, (
        "httpx.get was not called with follow_redirects kwarg"
    )
    assert captured["follow_redirects"] is False, (
        f"follow_redirects should be False, got {captured['follow_redirects']!r}"
    )


# ---------------------------------------------------------------------------
# Opsgenie
# ---------------------------------------------------------------------------


def test_opsgenie_transport_passes_follow_redirects_false() -> None:
    """_DefaultTransport.get must forward follow_redirects=False to httpx.get."""
    from general_ludd.connectors.opsgenie import _DefaultTransport

    captured: dict[str, Any] = {}

    def fake_httpx_get(url: str, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        resp = SimpleNamespace(status_code=200)
        cast(Any, resp).json = lambda: {}
        return resp

    transport = _DefaultTransport()
    with patch("httpx.get", side_effect=fake_httpx_get):
        transport.get(
            "https://api.opsgenie.com/v2/alerts",
            headers={"Authorization": "GenieKey key"},
            params={"limit": 100},
            timeout=10.0,
        )

    assert "follow_redirects" in captured, (
        "httpx.get was not called with follow_redirects kwarg"
    )
    assert captured["follow_redirects"] is False, (
        f"follow_redirects should be False, got {captured['follow_redirects']!r}"
    )


# ---------------------------------------------------------------------------
# Monday
# ---------------------------------------------------------------------------


def test_monday_no_redirect_handler_raises_on_302() -> None:
    """_NoRedirectHandler must raise HTTPError instead of following a 302."""
    from general_ludd.issue_sources.monday import _NoRedirectHandler

    handler = _NoRedirectHandler()
    fake_headers = MagicMock()
    fake_req = urllib.request.Request("https://api.monday.com/v2")

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        handler.redirect_request(
            req=fake_req,
            fp=None,
            code=302,
            msg="Found",
            headers=fake_headers,
            newurl="https://internal.corp/secret",
        )

    error = exc_info.value
    try:
        assert error.code == 302
        assert "redirect blocked" in str(error.reason or error.msg)
    finally:
        error.close()


def test_monday_default_transport_uses_no_redirect_opener() -> None:
    """_default_transport must pass _NoRedirectHandler to build_opener.

    We patch urllib.request.build_opener to capture what handler class is
    passed; the result of build_opener returns a successful fake response so
    the rest of the function completes normally.
    """
    from general_ludd.issue_sources.monday import (
        _default_transport,
        _NoRedirectHandler,
    )

    handler_classes_seen: list[type] = []
    fake_body = json.dumps({"data": {"boards": []}}).encode()

    class _FakeResp:
        status = 200

        def getcode(self) -> int:
            return 200

        def read(self) -> bytes:
            return fake_body

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *args: object) -> None:
            pass

    class _FakeOpener:
        def open(self, req: Any, timeout: Any = None) -> _FakeResp:
            return _FakeResp()

    def capturing_build_opener(*handlers: Any) -> _FakeOpener:
        for h in handlers:
            handler_classes_seen.append(type(h))
        return _FakeOpener()

    with patch("urllib.request.build_opener", side_effect=capturing_build_opener):
        status, _payload = _default_transport(
            method="POST",
            url="https://api.monday.com/v2",
            headers={"Content-Type": "application/json"},
            json_body={"query": "{ boards { id } }"},
            timeout=10.0,
        )

    assert _NoRedirectHandler in handler_classes_seen, (
        f"_NoRedirectHandler not found in handlers passed to build_opener; "
        f"got: {handler_classes_seen}"
    )
    assert status == 200


# ---------------------------------------------------------------------------
# Additional connectors — SSRF redirect guard (follow_redirects=False)
#
# Each connector ships a default httpx-backed transport used only when no
# transport is injected. A 3xx redirect must never be silently followed, or a
# 30x could pivot the request off the configured endpoint to an internal /
# metadata address (SSRF). These tests patch the relevant httpx entry point,
# capture the kwargs, and assert follow_redirects=False is forwarded.
# ---------------------------------------------------------------------------


def _fake_response() -> SimpleNamespace:
    resp = SimpleNamespace(status_code=200)
    cast(Any, resp).json = lambda: {}
    return resp


def _assert_no_redirects(captured: dict[str, Any]) -> None:
    assert "follow_redirects" in captured, (
        "outbound httpx call was not made with a follow_redirects kwarg"
    )
    assert captured["follow_redirects"] is False, (
        f"follow_redirects should be False, got {captured['follow_redirects']!r}"
    )


def test_elasticsearch_transport_passes_follow_redirects_false() -> None:
    from general_ludd.connectors.elasticsearch import _default_http_request

    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return _fake_response()

    with patch("httpx.request", side_effect=fake_request):
        _default_http_request(
            "GET", "https://es.example/_search", {"Authorization": "x"}, b"{}"
        )

    _assert_no_redirects(captured)


def test_okta_transport_passes_follow_redirects_false() -> None:
    from general_ludd.connectors.okta import _default_transport

    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return _fake_response()

    with patch("httpx.request", side_effect=fake_request):
        _default_transport(
            "GET",
            "https://example.okta.com/api/v1/logs",
            headers={"Authorization": "SSWS tok"},
            params={"limit": "100"},
            timeout=10.0,
        )

    _assert_no_redirects(captured)


def test_graylog_transport_passes_follow_redirects_false() -> None:
    from general_ludd.connectors.graylog import _default_transport

    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return _fake_response()

    with patch("httpx.get", side_effect=fake_get):
        _default_transport(
            "https://graylog.example/api/search/universal/relative",
            headers={"Authorization": "Basic x"},
            params={"query": "*"},
            timeout=10.0,
        )

    _assert_no_redirects(captured)


def test_azure_monitor_transport_passes_follow_redirects_false() -> None:
    from general_ludd.connectors.azure_monitor import _default_transport

    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return _fake_response()

    with patch("httpx.request", side_effect=fake_request):
        _default_transport(
            "POST",
            "https://api.loganalytics.io/v1/workspaces/w/query",
            {"Authorization": "Bearer x"},
            {"query": "Heartbeat"},
            10.0,
        )

    _assert_no_redirects(captured)


def test_cloudflare_transport_passes_follow_redirects_false() -> None:
    from general_ludd.connectors.cloudflare import _default_transport

    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return _fake_response()

    with patch("httpx.request", side_effect=fake_request):
        _default_transport(
            "GET",
            "https://api.cloudflare.com/client/v4/zones",
            headers={"Authorization": "Bearer x"},
            params={"per_page": "50"},
            timeout=10.0,
        )

    _assert_no_redirects(captured)


def test_jaeger_transport_passes_follow_redirects_false() -> None:
    from general_ludd.connectors.jaeger import _HttpxTransport

    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return _fake_response()

    with patch("httpx.get", side_effect=fake_get):
        _HttpxTransport().get(
            "https://jaeger.example/api/traces",
            params={"service": "svc"},
            timeout=10.0,
            headers={"Accept": "application/json"},
        )

    _assert_no_redirects(captured)


def test_victoriametrics_transport_passes_follow_redirects_false() -> None:
    from general_ludd.connectors.victoriametrics import _default_transport

    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return _fake_response()

    with patch("httpx.request", side_effect=fake_request):
        _default_transport(
            "GET",
            "https://vm.example/api/v1/query",
            headers={"Authorization": "Bearer x"},
            params={"query": "up"},
            json=None,
            timeout=10.0,
        )

    _assert_no_redirects(captured)


def test_entra_signin_transport_passes_follow_redirects_false() -> None:
    from general_ludd.connectors.entra_signin import _default_transport

    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return _fake_response()

    with patch("httpx.request", side_effect=fake_request):
        _default_transport(
            "GET",
            "https://graph.microsoft.com/v1.0/auditLogs/signIns",
            headers={"Authorization": "Bearer x"},
            params={"$top": "100"},
            timeout=10.0,
        )

    _assert_no_redirects(captured)


def test_zabbix_transport_passes_follow_redirects_false() -> None:
    from general_ludd.connectors.zabbix import _default_transport

    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return _fake_response()

    with patch("httpx.request", side_effect=fake_request):
        _default_transport(
            "POST",
            "https://zabbix.example/api_jsonrpc.php",
            headers={"Content-Type": "application/json-rpc"},
            params=None,
            json={"jsonrpc": "2.0"},
            timeout=10.0,
        )

    _assert_no_redirects(captured)


def test_parca_transport_client_constructed_with_follow_redirects_false() -> None:
    """parca sets follow_redirects=False on the httpx.Client CONSTRUCTOR."""
    from general_ludd.connectors.parca import _HttpxTransport

    captured: dict[str, Any] = {}

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(self, url: str, **kwargs: Any) -> SimpleNamespace:
            return _fake_response()

    with patch("httpx.Client", _FakeClient):
        _HttpxTransport().post(
            "https://parca.example/parca.query.v1alpha1.QueryService/QueryRange",
            json={"q": "x"},
            headers={"Authorization": "Bearer x"},
            timeout=10.0,
        )

    _assert_no_redirects(captured)


def test_opentsdb_transport_client_constructed_with_follow_redirects_false() -> None:
    """opentsdb sets follow_redirects=False on the httpx.Client CONSTRUCTOR."""
    from general_ludd.connectors.opentsdb import _HttpxTransport

    captured: dict[str, Any] = {}

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def request(self, method: str, url: str, **kwargs: Any) -> SimpleNamespace:
            resp = _fake_response()
            cast(Any, resp).text = "[]"
            return resp

    with patch("httpx.Client", _FakeClient):
        _HttpxTransport().request(
            "POST",
            "https://tsdb.example/api/query",
            headers={"Content-Type": "application/json"},
            body=b"{}",
            timeout=10.0,
        )

    _assert_no_redirects(captured)


def test_jenkins_transport_passes_follow_redirects_false() -> None:
    """jenkins _default_http_get must forward follow_redirects=False to httpx.get."""
    from general_ludd.connectors.jenkins import _default_http_get

    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        resp = _fake_response()
        cast(Any, resp).content = b"{}"
        return resp

    with patch("httpx.get", side_effect=fake_get):
        _default_http_get("https://ci.example/api/json", {"Accept": "application/json"})

    _assert_no_redirects(captured)


def test_kubernetes_transport_passes_follow_redirects_false() -> None:
    """kubernetes _default_transport must forward follow_redirects=False to httpx.request."""
    from general_ludd.connectors.kubernetes import _default_transport

    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return _fake_response()

    with patch("httpx.request", side_effect=fake_request):
        _default_transport(
            "GET",
            "https://k8s.example/api/v1",
            headers={"Authorization": "Bearer x"},
            timeout=10.0,
        )

    _assert_no_redirects(captured)


# ---------------------------------------------------------------------------
# travis / argo_workflows / buildkite — httpx.Client CONSTRUCTOR must carry
# follow_redirects=False (SSRF: prevent redirect-to-metadata pivot).
#
# These three connectors share the _httpx_transport shape:
#   (method, url, headers, timeout) -> (status_code, content)
# using `with httpx.Client(timeout=..., follow_redirects=False) as client:`.
# ---------------------------------------------------------------------------


def test_travis_transport_client_constructed_with_follow_redirects_false() -> None:
    """travis _httpx_transport must construct httpx.Client with follow_redirects=False."""
    from general_ludd.connectors.travis import _httpx_transport

    captured: dict[str, Any] = {}

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def request(self, method: str, url: str, **kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(status_code=200, content=b"{}")

    with patch("httpx.Client", _FakeClient):
        _httpx_transport("GET", "https://api.travis-ci.com/repo/x/builds", {}, 10.0)

    _assert_no_redirects(captured)


def test_argo_workflows_transport_client_constructed_with_follow_redirects_false() -> None:
    """argo_workflows _httpx_transport must construct httpx.Client with follow_redirects=False."""
    from general_ludd.connectors.argo_workflows import _httpx_transport

    captured: dict[str, Any] = {}

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def request(self, method: str, url: str, **kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(status_code=200, content=b"{}")

    with patch("httpx.Client", _FakeClient):
        _httpx_transport(
            "GET",
            "https://argo.example/api/v1/workflows/argo",
            {},
            10.0,
        )

    _assert_no_redirects(captured)


def test_buildkite_transport_client_constructed_with_follow_redirects_false() -> None:
    """buildkite _httpx_transport must construct httpx.Client with follow_redirects=False."""
    from general_ludd.connectors.buildkite import _httpx_transport

    captured: dict[str, Any] = {}

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def request(self, method: str, url: str, **kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(status_code=200, content=b"{}")

    with patch("httpx.Client", _FakeClient):
        _httpx_transport(
            "GET",
            "https://api.buildkite.com/v2/organizations/o/pipelines/p/builds",
            {},
            10.0,
        )

    _assert_no_redirects(captured)
