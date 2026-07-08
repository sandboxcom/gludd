"""SSRF redirect-to-metadata guard — 25 connectors (httpx migration).

Attack vector
-------------
A connector issues an HTTP request to a configured *external* endpoint.  A
malicious or compromised endpoint returns a ``30x`` redirect to
``169.254.169.254`` (the AWS / GCP / Azure cloud-metadata IP),
``metadata.google.internal``, or another internal host.  If ``httpx`` silently
follows the redirect, the connector reads the metadata response (instance
credentials, IAM tokens, etc.) and leaks it into observability records — a
classic SSRF exfiltration.

Mitigation under test
---------------------
Every connector's default transport must pass ``follow_redirects=False`` to
``httpx`` — either on the ``httpx.Client`` constructor or on the
``httpx.request()`` call.  A ``30x`` response is then surfaced as-is and
rejected by the caller's ``2xx`` check; the redirect is never followed.

Scope
-----
This file extends ``tests/unit/test_connector_redirect_guard.py`` (14
already-converted connectors) to cover 25 additional connectors migrated from
``urllib`` to ``httpx``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _fake_response() -> SimpleNamespace:
    """A minimal fake ``httpx.Response`` covering every attribute transports access."""
    resp = SimpleNamespace(status_code=200, content=b"{}", text="{}")
    cast(Any, resp).json = lambda: {}
    return resp


def _assert_no_redirects(captured: dict[str, Any]) -> None:
    """Assert ``follow_redirects`` was present in captured httpx kwargs and False."""
    assert "follow_redirects" in captured, (
        "outbound httpx call was not made with a follow_redirects kwarg"
    )
    assert captured["follow_redirects"] is False, (
        f"follow_redirects should be False, got {captured['follow_redirects']!r}"
    )


_captured: dict[str, Any] = {}


class _CapturingClient:
    """Context-manager stand-in for ``httpx.Client`` capturing constructor kwargs.

    Supports ``.get()``, ``.request()``, and ``.post()`` so it works with every
    connector transport pattern (client-as-context-manager + method call).
    """

    def __init__(self, **kwargs: Any) -> None:
        _captured.update(kwargs)

    def __enter__(self) -> _CapturingClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(self, url: str, **kwargs: Any) -> SimpleNamespace:
        return _fake_response()

    def request(self, method: str, url: str, **kwargs: Any) -> SimpleNamespace:
        return _fake_response()

    def post(self, url: str, **kwargs: Any) -> SimpleNamespace:
        return _fake_response()


def _import_attr(module_path: str, attr: str) -> Any:
    """Lazy-import a name from a connector module."""
    module = __import__(module_path, fromlist=[attr])
    return getattr(module, attr)


# ===========================================================================
# Group 1 — ``_default_http_get(url, headers) -> (status, json)``
# Pattern: ``with httpx.Client(...) as client: client.get(url, headers=...)``
#
# Converted:  github_actions, circleci, gitlab_ci
# ===========================================================================

_GROUP1_SIMPLE_GET = ["github_actions", "circleci", "gitlab_ci"]


@pytest.mark.parametrize("connector", _GROUP1_SIMPLE_GET)
def test_simple_get_transport_passes_follow_redirects_false(
    connector: str,
) -> None:
    """``_default_http_get(url, headers)`` must set ``follow_redirects=False``."""
    transport = _import_attr(
        f"general_ludd.connectors.{connector}", "_default_http_get"
    )

    _captured.clear()
    with patch("httpx.Client", _CapturingClient):
        transport(
            "https://example.com/api/v1/test",
            {"Accept": "application/json"},
        )

    _assert_no_redirects(_captured)


# ===========================================================================
# Group 2 — ``_default_http_get(url, params, headers, timeout) -> (status, body)``
# Pattern: ``with httpx.Client(...) as client: client.get(url, params=..., headers=...)``
#
# All converted:  nagios, thanos, rabbitmq, nats, kafka_exporter
# ===========================================================================

_GROUP2_PARAM_GET = ["nagios", "thanos", "rabbitmq", "nats", "kafka_exporter"]


@pytest.mark.parametrize("connector", _GROUP2_PARAM_GET)
def test_param_get_transport_passes_follow_redirects_false(
    connector: str,
) -> None:
    """``_default_http_get(url, params, headers, timeout)`` must set ``follow_redirects=False``."""
    transport = _import_attr(
        f"general_ludd.connectors.{connector}", "_default_http_get"
    )

    _captured.clear()
    with patch("httpx.Client", _CapturingClient):
        transport(
            "https://example.com/api/v1/test",
            params={"q": "1"},
            headers={"Accept": "application/json"},
            timeout=10.0,
        )

    _assert_no_redirects(_captured)


# ===========================================================================
# Group 3 — ``_httpx_transport(method, url, headers, timeout) -> (status, bytes)``
# Pattern: ``with httpx.Client(...) as client: client.request(method, url, headers=...)``
#
# All converted:  travis, argo_workflows, buildkite
# ===========================================================================

_GROUP3_HTTPX_FUNC = ["travis", "argo_workflows", "buildkite"]


@pytest.mark.parametrize("connector", _GROUP3_HTTPX_FUNC)
def test_httpx_transport_func_passes_follow_redirects_false(
    connector: str,
) -> None:
    """``_httpx_transport(method, url, headers, timeout)`` must set ``follow_redirects=False``."""
    transport = _import_attr(
        f"general_ludd.connectors.{connector}", "_httpx_transport"
    )

    _captured.clear()
    with patch("httpx.Client", _CapturingClient):
        transport("GET", "https://example.com/api/v1/test", {}, 10.0)

    _assert_no_redirects(_captured)


# ===========================================================================
# Group 4 — ``_HttpxTransport().get(url, *, headers, timeout)``
# Pattern: ``with httpx.Client(...) as client: client.get(url, headers=...)``
#
# Converted:  appdynamics, prom_scrape, tempo, zipkin (renamed to _HttpxTransport)
#            sentry, dynatrace (still named _UrllibTransport but use httpx internally)
# ===========================================================================

_GROUP4_HTTPX_CLASS = [
    "appdynamics",
    "prom_scrape",
    "sentry",
    "dynatrace",
    "tempo",
    "zipkin",
]


def _resolve_transport_class(connector: str) -> Any:
    """Find the default transport class, trying both common names."""
    module = __import__(f"general_ludd.connectors.{connector}", fromlist=[""])
    for name in ("_HttpxTransport", "_UrllibTransport"):
        cls = getattr(module, name, None)
        if cls is not None:
            return cls
    raise AttributeError(
        f"No transport class (_HttpxTransport or _UrllibTransport) in {connector}"
    )


@pytest.mark.parametrize("connector", _GROUP4_HTTPX_CLASS)
def test_httpx_transport_class_get_passes_follow_redirects_false(
    connector: str,
) -> None:
    """``_HttpxTransport.get(url, headers, timeout)`` must set ``follow_redirects=False``."""
    cls = _resolve_transport_class(connector)

    _captured.clear()
    with patch("httpx.Client", _CapturingClient):
        cls().get(
            "https://example.com/api/v1/test",
            headers={"Accept": "application/json"},
            timeout=10.0,
        )

    _assert_no_redirects(_captured)


# ===========================================================================
# Group 5 — ``_default_http_request(method, url, *, params, json, headers, timeout)``
# Pattern: ``with httpx.Client(...) as client: client.request(method, url, ...)``
#
# Converted:  datadog, splunk_observability
# ===========================================================================

_GROUP5_REQUEST_FUNC = ["datadog", "splunk_observability"]


@pytest.mark.parametrize("connector", _GROUP5_REQUEST_FUNC)
def test_request_transport_passes_follow_redirects_false(
    connector: str,
) -> None:
    """``_default_http_request(...)`` must set ``follow_redirects=False``."""
    transport = _import_attr(
        f"general_ludd.connectors.{connector}", "_default_http_request"
    )

    _captured.clear()
    with patch("httpx.Client", _CapturingClient):
        transport(
            "GET",
            "https://example.com/api/v1/test",
            params=None,
            json=None,
            headers={"Accept": "application/json"},
            timeout=10.0,
        )

    _assert_no_redirects(_captured)


# ===========================================================================
# Individual: prometheus — ``_default_http_get(url, params, headers)``
# No ``timeout`` parameter (module-level ``_DEFAULT_TIMEOUT``).
# ===========================================================================


def test_prometheus_transport_passes_follow_redirects_false() -> None:
    """prometheus ``_default_http_get`` must set ``follow_redirects=False``."""
    transport = _import_attr(
        "general_ludd.connectors.prometheus", "_default_http_get"
    )

    _captured.clear()
    with patch("httpx.Client", _CapturingClient):
        transport(
            "https://prometheus.example/api/v1/query",
            params={"query": "up"},
            headers={"Accept": "application/json"},
        )

    _assert_no_redirects(_captured)


# ===========================================================================
# Individual: azure_devops — uses ``httpx.request()`` (module-level function,
# not ``httpx.Client`` context manager)
# ===========================================================================


def test_azure_devops_transport_passes_follow_redirects_false() -> None:
    """azure_devops ``_default_http_get`` must pass ``follow_redirects=False`` to ``httpx.request``."""
    transport = _import_attr(
        "general_ludd.connectors.azure_devops", "_default_http_get"
    )

    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return _fake_response()

    with patch("httpx.request", side_effect=fake_request):
        transport(
            "https://dev.azure.com/org/proj/_apis/build/builds",
            {"Accept": "application/json"},
        )

    _assert_no_redirects(captured)


# ===========================================================================
# Individual: nomad — ``_urllib_transport(method, url, headers, params)``
# Despite the legacy name, this connector already uses ``httpx.Client``.
# ===========================================================================


def test_nomad_transport_passes_follow_redirects_false() -> None:
    """nomad ``_urllib_transport`` must set ``follow_redirects=False``."""
    transport = _import_attr(
        "general_ludd.connectors.nomad", "_urllib_transport"
    )

    _captured.clear()
    with patch("httpx.Client", _CapturingClient):
        transport(
            "GET",
            "https://nomad.example/v1/agent/health",
            {},
            None,
        )

    _assert_no_redirects(_captured)


# ===========================================================================
# Individual: kubernetes — ``_default_transport(method, url, *, headers, timeout)``
# Uses ``httpx.request()`` (module-level function, not ``httpx.Client``)
# ===========================================================================


def test_kubernetes_transport_passes_follow_redirects_false() -> None:
    """kubernetes ``_default_transport`` must pass ``follow_redirects=False`` to ``httpx.request``."""
    transport = _import_attr(
        "general_ludd.connectors.kubernetes", "_default_transport"
    )

    captured: dict[str, Any] = {}

    def fake_request(method: str, url: str, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return _fake_response()

    with patch("httpx.request", side_effect=fake_request):
        transport(
            "GET",
            "https://k8s.example/api/v1/namespaces/default/pods",
            headers={"Authorization": "Bearer x"},
            timeout=10.0,
        )

    _assert_no_redirects(captured)


# ===========================================================================
# Individual: jenkins — ``_default_http_get(url, headers)``
# Uses ``httpx.get()`` (module-level function, not ``httpx.Client``)
# ===========================================================================


def test_jenkins_transport_passes_follow_redirects_false() -> None:
    """jenkins ``_default_http_get`` must pass ``follow_redirects=False`` to ``httpx.get``."""
    transport = _import_attr(
        "general_ludd.connectors.jenkins", "_default_http_get"
    )

    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return _fake_response()

    with patch("httpx.get", side_effect=fake_get):
        transport(
            "https://jenkins.example/api/json",
            {"Accept": "application/json"},
        )

    _assert_no_redirects(captured)


# ===========================================================================
# Individual: opentsdb — ``_HttpxTransport().request(method, url, headers, body, timeout)``
# Converted:  uses ``httpx.Client(follow_redirects=False)``
# ===========================================================================


def test_opentsdb_transport_passes_follow_redirects_false() -> None:
    """opentsdb ``_HttpxTransport.request`` must set ``follow_redirects=False``."""
    cls = _import_attr("general_ludd.connectors.opentsdb", "_HttpxTransport")

    _captured.clear()
    with patch("httpx.Client", _CapturingClient):
        cls().request(
            "POST",
            "https://opentsdb.example/api/query",
            headers={"Content-Type": "application/json"},
            body=b"{}",
            timeout=30.0,
        )

    _assert_no_redirects(_captured)
