"""Unit tests for AzureMonitorSource (mocked transport, no network)."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.azure_monitor import AzureMonitorSource

TOKEN_ENV = "GLUDD_TEST_AAD_TOKEN"
WORKSPACE = "ws-1234"


class FakeResponse:
    """Minimal stand-in for httpx.Response: status_code + json()."""

    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class RecordingTransport:
    """Injectable transport that records calls and returns a queued response.

    No network is touched. ``calls`` captures (method, url, headers, body, timeout).
    """

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict[str, str], Any, float]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: Any,
        json_body: Any,
        timeout: float,
    ) -> FakeResponse:
        self.calls.append((method, url, dict(headers), json_body, timeout))
        return self.response


def _canned_logs_payload() -> dict[str, Any]:
    return {
        "tables": [
            {
                "name": "PrimaryResult",
                "columns": [
                    {"name": "TimeGenerated", "type": "datetime"},
                    {"name": "Level", "type": "string"},
                    {"name": "Message", "type": "string"},
                    {"name": "Computer", "type": "string"},
                    {"name": "Value", "type": "real"},
                ],
                "rows": [
                    ["2026-06-12T10:00:00Z", "Error", "disk full", "host-a", 99.5],
                    ["2026-06-12T10:05:00Z", "Information", "ok now", "host-b", 12.0],
                ],
            }
        ]
    }


def _make_source(
    monkeypatch: pytest.MonkeyPatch,
    transport: RecordingTransport,
    **overrides: Any,
) -> AzureMonitorSource:
    monkeypatch.setenv(TOKEN_ENV, "secret-aad-token")
    config: dict[str, Any] = {
        "name": "azmon",
        "workspace_id": WORKSPACE,
        "token_env": TOKEN_ENV,
        "transport": transport,
    }
    config.update(overrides)
    return AzureMonitorSource(config)


# -- contract / construction -----------------------------------------------------------


def test_kind_is_logs() -> None:
    assert AzureMonitorSource.KIND == "logs"


def test_requires_workspace_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, "t")
    with pytest.raises(ValueError, match="workspace_id"):
        AzureMonitorSource({"token_env": TOKEN_ENV})


def test_requires_token_env() -> None:
    with pytest.raises(ValueError, match="token_env"):
        AzureMonitorSource({"workspace_id": WORKSPACE})


# -- query normalization ---------------------------------------------------------------


def test_query_normalizes_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, _canned_logs_payload()))
    src = _make_source(monkeypatch, transport)

    records = src.query("AzureDiagnostics | take 2")

    assert len(records) == 2
    first = records[0]
    assert set(first) == {
        "ts",
        "source",
        "kind",
        "level_or_status",
        "message",
        "value",
        "labels",
        "raw",
    }
    assert first["source"] == "azmon"
    assert first["kind"] == "logs"
    assert first["level_or_status"] == "Error"
    assert first["message"] == "disk full"
    assert first["value"] == pytest.approx(99.5)
    # ts parsed from TimeGenerated ISO-8601 -> float epoch.
    assert isinstance(first["ts"], float)
    assert first["ts"] > 0
    # non-message/level/ts columns land in labels.
    assert first["labels"] == {"Computer": "host-a"}
    # raw preserves the full row mapping.
    assert first["raw"]["Message"] == "disk full"
    assert first["raw"]["Computer"] == "host-a"


def test_query_falls_back_to_severitylevel(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "tables": [
            {
                "columns": [
                    {"name": "TimeGenerated"},
                    {"name": "SeverityLevel"},
                    {"name": "Message"},
                ],
                "rows": [["2026-06-12T00:00:00Z", 3, "warn-ish"]],
            }
        ]
    }
    transport = RecordingTransport(FakeResponse(200, payload))
    src = _make_source(monkeypatch, transport)
    rec = src.query("x")[0]
    assert rec["level_or_status"] == 3
    assert rec["message"] == "warn-ish"


def test_query_unparseable_ts_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "tables": [
            {
                "columns": [{"name": "TimeGenerated"}, {"name": "Message"}],
                "rows": [["not-a-date", "hello"]],
            }
        ]
    }
    transport = RecordingTransport(FakeResponse(200, payload))
    src = _make_source(monkeypatch, transport)
    rec = src.query("x")[0]
    assert rec["ts"] is None
    assert rec["message"] == "hello"


# -- transport wiring: endpoint, body, bearer header -----------------------------------


def test_query_posts_to_loganalytics_with_bearer_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, _canned_logs_payload()))
    src = _make_source(monkeypatch, transport, timespan="PT2H")

    src.query("Heartbeat | take 1")

    assert len(transport.calls) == 1
    method, url, headers, body, _timeout = transport.calls[0]
    assert method == "POST"
    assert url == f"https://api.loganalytics.io/v1/workspaces/{WORKSPACE}/query"
    # Bearer token sourced from the configured env var, never hardcoded.
    assert headers["Authorization"] == "Bearer secret-aad-token"
    assert body == {"query": "Heartbeat | take 1", "timespan": "PT2H"}


def test_missing_env_token_makes_query_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, _canned_logs_payload()))
    src = _make_source(monkeypatch, transport)
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    with pytest.raises(RuntimeError, match="missing bearer token"):
        src.query("x")


def test_non_200_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(403, {}))
    src = _make_source(monkeypatch, transport)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        src.query("x")


# -- SSRF: internal / metadata base_url rejected ---------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1",
        "https://localhost",
        "http://10.0.0.5",
        "http://192.168.1.10",
        "http://172.16.0.1",
        "http://169.254.169.254",
        "http://100.100.100.200",  # Alibaba/Azure metadata (canonical guard catches it)
        "https://metadata.google.internal",
        "https://metadata.azure.com",  # Azure metadata name (connector-specific extra)
        "http://metadata.goog/",  # GCP alias missed by the old per-connector blocklist
        "http://foo.localhost/",  # RFC 6761 .localhost TLD missed by the old guard
        "http://internalhost",  # single-label, no dots
    ],
)
def test_internal_base_url_rejected(monkeypatch: pytest.MonkeyPatch, bad_url: str) -> None:
    monkeypatch.setenv(TOKEN_ENV, "t")
    with pytest.raises(ValueError):
        AzureMonitorSource(
            {
                "workspace_id": WORKSPACE,
                "token_env": TOKEN_ENV,
                "base_url": bad_url,
            }
        )


def test_public_base_url_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, _canned_logs_payload()))
    src = _make_source(monkeypatch, transport, base_url="https://api.loganalytics.io")
    # Construction succeeded; query routes to the overridden (public) host.
    src.query("x")
    assert transport.calls[0][1].startswith("https://api.loganalytics.io/")


# -- health ----------------------------------------------------------------------------


def test_health_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"tables": [{"columns": [{"name": "print_0"}], "rows": [[1]]}]}
    transport = RecordingTransport(FakeResponse(200, payload))
    src = _make_source(monkeypatch, transport)
    result = src.health()
    assert result["ok"] is True
    assert "row" in result["detail"]
    # health uses the trivial KQL probe.
    assert transport.calls[0][3]["query"] == "print 1"


def test_health_not_ok_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(500, {}))
    src = _make_source(monkeypatch, transport)
    result = src.health()
    assert result["ok"] is False
    assert "500" in result["detail"]


def test_health_never_raises_on_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = RecordingTransport(FakeResponse(200, {}))
    src = _make_source(monkeypatch, transport)
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    result = src.health()
    assert result["ok"] is False
    assert "missing bearer token" in result["detail"]
