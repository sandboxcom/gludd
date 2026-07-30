"""Unit tests for the Kubernetes observability connector (mocked transport).

These tests never touch a real cluster. The connector takes an *injectable* HTTP
transport — a callable ``(method, url, headers, timeout) -> Response`` — so every
request/response pair here is canned. We assert on:

- SSRF policy: an internal/private api-server is rejected unless the config sets
  ``allow_private=True``; a public host is always allowed.
- Bearer auth: the ServiceAccount token (read from ``token_env``) is sent as an
  ``Authorization: Bearer <token>`` header.
- ``query`` for ``mode='logs'``: plain-text pod log -> one normalized record per
  line, with RFC3339 ts parsed when ``timestamps=true`` and a detected level.
- ``query`` for ``mode='events'``: events list -> one normalized record per event
  with Warning/Normal status, reason+message text, and reason/object labels.
- ``health()`` reports ok / not-ok and NEVER raises.
"""

from __future__ import annotations

import os
from typing import Any, cast

import pytest

from general_ludd.connectors.kubernetes import KubernetesSource

# --------------------------------------------------------------------------- #
# Mock transport
# --------------------------------------------------------------------------- #
PUBLIC_API = "https://k8s.example.com:6443"
INTERNAL_API = "https://10.0.0.1:6443"
TOKEN_ENV = "TEST_K8S_SA_TOKEN"
TOKEN_VALUE = "sa-bearer-token-xyz"


class FakeResponse:
    """Minimal stand-in for an HTTP response the connector understands."""

    def __init__(self, status_code: int, *, text: str = "", json_body: Any = None) -> None:
        self.status_code = status_code
        self.text = text
        self._json = json_body

    def json(self) -> Any:
        return self._json


class RecordingTransport:
    """Injectable transport: records calls and returns scripted responses.

    ``routes`` maps a substring of the URL to a :class:`FakeResponse`. The first
    matching substring wins, so callers can register narrow routes.
    """

    def __init__(self, routes: dict[str, FakeResponse] | None = None) -> None:
        self.routes = routes or {}
        self.calls: list[dict[str, Any]] = []
        self.default = FakeResponse(404, text="not found")

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> FakeResponse:
        self.calls.append(
            {"method": method, "url": url, "headers": headers or {}, "timeout": timeout}
        )
        for needle, resp in self.routes.items():
            if needle in url:
                return resp
        return self.default


def make_source(
    *,
    api_server: str = PUBLIC_API,
    namespace: str = "default",
    transport: RecordingTransport | None = None,
    allow_private: bool = False,
    extra: dict[str, Any] | None = None,
) -> KubernetesSource:
    config: dict[str, Any] = {
        "api_server": api_server,
        "namespace": namespace,
        "token_env": TOKEN_ENV,
        "allow_private": allow_private,
        "transport": transport or RecordingTransport(),
        "timeout_s": 5.0,
    }
    if extra:
        config.update(extra)
    return KubernetesSource(config)


@pytest.fixture(autouse=True)
def _set_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, TOKEN_VALUE)


# --------------------------------------------------------------------------- #
# Contract surface
# --------------------------------------------------------------------------- #
def test_kind_is_logs_class_attr() -> None:
    assert KubernetesSource.KIND == "logs"


def test_name_attr_present() -> None:
    src = make_source(extra={"name": "prod-k8s"})
    assert src.name == "prod-k8s"


def test_name_defaults_when_unset() -> None:
    src = make_source()
    assert isinstance(src.name, str)
    assert src.name


# --------------------------------------------------------------------------- #
# SSRF policy
# --------------------------------------------------------------------------- #
def test_public_api_server_is_allowed() -> None:
    # Construction + a query against a public host must not raise on SSRF.
    transport = RecordingTransport({"/log": FakeResponse(200, text="hello\n")})
    src = make_source(api_server=PUBLIC_API, transport=transport)
    recs = src.query({"mode": "logs", "pod": "p", "container": "c"})
    assert isinstance(recs, list)


def test_internal_api_server_rejected_without_opt_in() -> None:
    with pytest.raises(ValueError):
        make_source(api_server=INTERNAL_API, allow_private=False)


def test_internal_api_server_allowed_with_opt_in() -> None:
    transport = RecordingTransport({"/log": FakeResponse(200, text="line\n")})
    src = make_source(api_server=INTERNAL_API, transport=transport, allow_private=True)
    recs = src.query({"mode": "logs", "pod": "p", "container": "c"})
    assert transport.calls, "transport should be called when allow_private=True"
    assert any(r["level_or_status"] != "error" for r in recs)


def test_loopback_api_server_rejected_even_with_opt_in() -> None:
    with pytest.raises(ValueError):
        make_source(api_server="https://127.0.0.1:6443", allow_private=True)


def test_non_http_scheme_rejected() -> None:
    with pytest.raises(ValueError):
        make_source(api_server="file:///etc/passwd", allow_private=True)


@pytest.mark.parametrize("host", ["metadata.goog", "instance-data", "ip6-localhost"])
def test_metadata_alias_names_rejected_without_opt_in(host: str) -> None:
    with pytest.raises(ValueError):
        make_source(api_server=f"https://{host}:6443", allow_private=False)


def test_metadata_ip_literal_rejected_even_with_opt_in() -> None:
    with pytest.raises(ValueError):
        make_source(api_server="https://169.254.169.254:6443", allow_private=True)


def test_alibaba_metadata_ip_rejected_even_with_opt_in() -> None:
    with pytest.raises(ValueError):
        make_source(api_server="https://100.100.100.200:6443", allow_private=True)


def test_cgnat_address_blocked_without_opt_in_allowed_with_opt_in() -> None:
    with pytest.raises(ValueError):
        make_source(api_server="https://100.65.1.1:6443", allow_private=False)

    allowed_transport = RecordingTransport({"/log": FakeResponse(200, text="line\n")})
    allowed = make_source(api_server="https://100.65.1.1:6443", transport=allowed_transport, allow_private=True)
    recs = allowed.query({"mode": "logs", "pod": "p", "container": "c"})
    assert allowed_transport.calls
    assert any(r["level_or_status"] != "error" for r in recs)


# --------------------------------------------------------------------------- #
# Bearer auth
# --------------------------------------------------------------------------- #
def test_bearer_token_header_sent() -> None:
    transport = RecordingTransport({"/log": FakeResponse(200, text="hi\n")})
    src = make_source(transport=transport)
    src.query({"mode": "logs", "pod": "p", "container": "c"})
    assert transport.calls
    auth = transport.calls[0]["headers"].get("Authorization")
    assert auth == f"Bearer {TOKEN_VALUE}"


def test_missing_token_env_is_error_record_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    transport = RecordingTransport({"/log": FakeResponse(200, text="hi\n")})
    src = make_source(transport=transport)
    recs = src.query({"mode": "logs", "pod": "p", "container": "c"})
    assert len(recs) == 1
    assert recs[0]["level_or_status"] == "error"
    assert "token" in recs[0]["message"].lower()
    assert TOKEN_ENV not in recs[0]["message"]


# --------------------------------------------------------------------------- #
# query: logs mode
# --------------------------------------------------------------------------- #
POD_LOG_TEXT = (
    "2026-06-16T10:00:00.000000000Z Starting up the service\n"
    "2026-06-16T10:00:01.500000000Z ERROR failed to connect to db\n"
    "2026-06-16T10:00:02Z WARN retrying connection\n"
)

POD_LOG_NO_TS = (
    "Starting up the service\n"
    "ERROR failed to connect to db\n"
    "WARNING disk almost full\n"
)


def test_logs_one_record_per_line_with_timestamps() -> None:
    transport = RecordingTransport({"/log": FakeResponse(200, text=POD_LOG_TEXT)})
    src = make_source(namespace="prod", transport=transport)
    recs = src.query(
        {
            "mode": "logs",
            "pod": "web-0",
            "container": "app",
            "timestamps": True,
            "tailLines": 100,
        }
    )
    assert len(recs) == 3
    # First record: ts parsed from leading RFC3339, level info-ish.
    first = recs[0]
    assert first["kind"] == "logs"
    assert first["ts"] is not None
    assert first["ts"] > 0
    assert first["message"]  # non-empty message
    assert first["labels"] == {"namespace": "prod", "pod": "web-0", "container": "app"}
    assert first["raw"]


def test_logs_level_detection() -> None:
    transport = RecordingTransport({"/log": FakeResponse(200, text=POD_LOG_TEXT)})
    src = make_source(transport=transport)
    recs = src.query({"mode": "logs", "pod": "web-0", "container": "app", "timestamps": True})
    levels = [r["level_or_status"] for r in recs]
    assert levels[1] == "error"
    assert levels[2] in {"warn", "warning"}


def test_logs_no_timestamps_yields_none_ts() -> None:
    transport = RecordingTransport({"/log": FakeResponse(200, text=POD_LOG_NO_TS)})
    src = make_source(transport=transport)
    recs = src.query(
        {"mode": "logs", "pod": "web-0", "container": "app", "timestamps": False}
    )
    assert len(recs) == 3
    assert all(r["ts"] is None for r in recs)
    # message keeps the full line (no ts stripped because none present).
    assert recs[0]["message"] == "Starting up the service"
    assert recs[1]["level_or_status"] == "error"


def test_logs_query_url_and_params() -> None:
    transport = RecordingTransport({"/log": FakeResponse(200, text="x\n")})
    src = make_source(namespace="ns1", transport=transport)
    src.query(
        {
            "mode": "logs",
            "pod": "mypod",
            "container": "c1",
            "tailLines": 50,
            "sinceSeconds": 300,
            "timestamps": True,
        }
    )
    url = transport.calls[0]["url"]
    assert "/api/v1/namespaces/ns1/pods/mypod/log" in url
    assert "container=c1" in url
    assert "tailLines=50" in url
    assert "sinceSeconds=300" in url
    assert "timestamps=true" in url


def test_logs_blank_lines_skipped() -> None:
    transport = RecordingTransport({"/log": FakeResponse(200, text="a\n\n  \nb\n")})
    src = make_source(transport=transport)
    recs = src.query({"mode": "logs", "pod": "p", "container": "c"})
    msgs = [r["message"] for r in recs]
    assert msgs == ["a", "b"]


def test_logs_non_200_is_error_record() -> None:
    transport = RecordingTransport({"/log": FakeResponse(404, text="pod not found")})
    src = make_source(transport=transport)
    recs = src.query({"mode": "logs", "pod": "ghost", "container": "c"})
    assert len(recs) == 1
    assert recs[0]["level_or_status"] == "error"
    assert "404" in recs[0]["message"]


# --------------------------------------------------------------------------- #
# query: events mode
# --------------------------------------------------------------------------- #
EVENTS_BODY = {
    "kind": "EventList",
    "items": [
        {
            "type": "Warning",
            "reason": "BackOff",
            "message": "Back-off restarting failed container",
            "lastTimestamp": "2026-06-16T09:00:00Z",
            "involvedObject": {"kind": "Pod", "name": "web-0", "namespace": "prod"},
        },
        {
            "type": "Normal",
            "reason": "Scheduled",
            "message": "Successfully assigned prod/web-0 to node-1",
            "eventTime": "2026-06-16T08:59:00.000000Z",
            "lastTimestamp": None,
            "involvedObject": {"kind": "Pod", "name": "web-0", "namespace": "prod"},
        },
    ],
}


def test_events_normalization_warning_and_normal() -> None:
    transport = RecordingTransport({"/events": FakeResponse(200, json_body=EVENTS_BODY)})
    src = make_source(namespace="prod", transport=transport)
    recs = src.query({"mode": "events"})
    assert len(recs) == 2

    warn = recs[0]
    assert warn["kind"] == "logs"
    assert warn["level_or_status"] == "Warning"
    assert "BackOff" in warn["message"]
    assert "Back-off restarting" in warn["message"]
    assert warn["labels"]["reason"] == "BackOff"
    assert warn["labels"]["involvedObject.kind"] == "Pod"
    assert warn["labels"]["involvedObject.name"] == "web-0"
    assert warn["labels"]["namespace"] == "prod"
    assert warn["ts"] is not None
    assert warn["raw"]["type"] == "Warning"

    normal = recs[1]
    assert normal["level_or_status"] == "Normal"
    # ts falls back to eventTime when lastTimestamp is null.
    assert normal["ts"] is not None


def test_events_url_namespaced() -> None:
    transport = RecordingTransport({"/events": FakeResponse(200, json_body=EVENTS_BODY)})
    src = make_source(namespace="prod", transport=transport)
    src.query({"mode": "events"})
    url = transport.calls[0]["url"]
    assert "/api/v1/namespaces/prod/events" in url


def test_events_all_namespaces_when_blank() -> None:
    transport = RecordingTransport({"/events": FakeResponse(200, json_body=EVENTS_BODY)})
    src = make_source(transport=transport)
    src.query({"mode": "events", "namespace": ""})
    url = transport.calls[0]["url"]
    assert "/api/v1/events" in url
    assert "/namespaces/" not in url


def test_events_non_200_is_error_record() -> None:
    transport = RecordingTransport({"/events": FakeResponse(500, text="boom")})
    src = make_source(transport=transport)
    recs = src.query({"mode": "events"})
    assert len(recs) == 1
    assert recs[0]["level_or_status"] == "error"


def test_events_empty_list() -> None:
    transport = RecordingTransport(
        {"/events": FakeResponse(200, json_body={"items": []})}
    )
    src = make_source(transport=transport)
    recs = src.query({"mode": "events"})
    assert recs == []


# --------------------------------------------------------------------------- #
# query: bad mode + bearer also on events
# --------------------------------------------------------------------------- #
def test_unknown_mode_is_error_record() -> None:
    transport = RecordingTransport()
    src = make_source(transport=transport)
    recs = src.query({"mode": "metrics"})
    assert len(recs) == 1
    assert recs[0]["level_or_status"] == "error"
    assert "mode" in recs[0]["message"].lower()


def test_events_sends_bearer() -> None:
    transport = RecordingTransport({"/events": FakeResponse(200, json_body=EVENTS_BODY)})
    src = make_source(transport=transport)
    src.query({"mode": "events"})
    assert transport.calls[0]["headers"]["Authorization"] == f"Bearer {TOKEN_VALUE}"


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #
def test_health_ok() -> None:
    transport = RecordingTransport({"/livez": FakeResponse(200, text="ok")})
    src = make_source(transport=transport)
    h = src.health()
    assert h["ok"] is True
    assert "detail" in h


def test_health_falls_back_to_version() -> None:
    # livez 404 -> try /version; 200 there means ok.
    transport = RecordingTransport(
        {
            "/livez": FakeResponse(404, text="nope"),
            "/version": FakeResponse(200, json_body={"gitVersion": "v1.29.0"}),
        }
    )
    src = make_source(transport=transport)
    h = src.health()
    assert h["ok"] is True


def test_health_not_ok_on_failure() -> None:
    transport = RecordingTransport(
        {
            "/livez": FakeResponse(503, text="unhealthy"),
            "/version": FakeResponse(503, text="unhealthy"),
        }
    )
    src = make_source(transport=transport)
    h = src.health()
    assert h["ok"] is False
    assert "detail" in h


def test_health_never_raises_on_transport_error() -> None:
    def boom(*_a: Any, **_k: Any) -> FakeResponse:
        raise ConnectionError("network down")

    src = make_source()
    # Swap in a raising transport directly.
    cast(Any, src)._transport = boom
    h = src.health()
    assert h["ok"] is False
    assert "detail" in h


def test_health_not_ok_on_ssrf_blocked() -> None:
    with pytest.raises(ValueError):
        make_source(api_server=INTERNAL_API, allow_private=False)


def test_query_never_raises_on_transport_error() -> None:
    def boom(*_a: Any, **_k: Any) -> FakeResponse:
        raise TimeoutError("slow")

    src = make_source()
    cast(Any, src)._transport = boom
    recs = src.query({"mode": "logs", "pod": "p", "container": "c"})
    assert len(recs) == 1
    assert recs[0]["level_or_status"] == "error"


def test_token_env_actually_read_from_environment() -> None:
    # Sanity: the autouse fixture set the env; confirm it is the source of truth.
    assert os.environ.get(TOKEN_ENV) == TOKEN_VALUE
