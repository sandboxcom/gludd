"""Unit tests for the New Relic NerdGraph (NRQL) connector.

All HTTP is mocked: a canned transport returns deterministic NerdGraph
payloads, so these tests never touch the network and require no API key
beyond what the test sets in ``monkeypatch``.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.newrelic import NewRelicSource


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class FakeResponse:
    """Minimal stand-in for a requests/httpx response."""

    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._payload


class RecordingTransport:
    """Callable transport that records its call and returns a canned response."""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, url: str, *, headers: dict, json: Any, timeout: float
    ) -> FakeResponse:
        self.calls.append(
            {"url": url, "headers": headers, "json": json, "timeout": timeout}
        )
        return self.response


def _nerdgraph_payload(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "data": {"actor": {"account": {"nrql": {"results": results}}}}
    }


def _make_source(
    transport: Any,
    *,
    config: dict[str, Any] | None = None,
) -> NewRelicSource:
    base = {
        "name": "nr-test",
        "account_id": 12345,
        "api_key_env": "TEST_NR_KEY",
    }
    if config:
        base.update(config)
    return NewRelicSource(base, transport=transport)


# --------------------------------------------------------------------------- #
# Contract / class-attr tests
# --------------------------------------------------------------------------- #
def test_kind_class_attribute() -> None:
    assert NewRelicSource.KIND == "metrics"


def test_default_api_url_is_public_graphql() -> None:
    src = _make_source(RecordingTransport(FakeResponse(_nerdgraph_payload([]))))
    assert src.api_url == "https://api.newrelic.com/graphql"


def test_name_attribute_from_config() -> None:
    src = _make_source(RecordingTransport(FakeResponse(_nerdgraph_payload([]))))
    assert src.name == "nr-test"


def test_name_defaults_when_absent() -> None:
    src = NewRelicSource(
        {"account_id": 1, "api_key_env": "TEST_NR_KEY"},
        transport=RecordingTransport(FakeResponse(_nerdgraph_payload([]))),
    )
    assert src.name == "newrelic"


# --------------------------------------------------------------------------- #
# Normalization: numeric -> value, ts parse, labels, raw
# --------------------------------------------------------------------------- #
def test_query_normalizes_numeric_aggregate_and_ts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_NR_KEY", "secret-key")
    results = [
        {"timestamp": 1718000000000, "count": 42, "host": "web-1", "region": "us"},
    ]
    transport = RecordingTransport(FakeResponse(_nerdgraph_payload(results)))
    src = _make_source(transport)

    records = src.query({"nrql": "SELECT count(*) FROM Transaction TIMESERIES"})

    assert len(records) == 1
    rec = records[0]
    assert rec["ts"] == 1718000000000
    assert rec["value"] == 42  # first numeric aggregate
    assert rec["source"] == "nr-test"
    assert rec["kind"] == "metrics"
    assert rec["level_or_status"] is None
    # non-numeric, non-timestamp fields become labels
    assert rec["labels"] == {"host": "web-1", "region": "us"}
    # raw preserves the original row verbatim
    assert rec["raw"] == results[0]
    # message is a stringified row
    assert isinstance(rec["message"], str)
    assert "web-1" in rec["message"]


def test_query_uses_begintimeseconds_when_no_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_NR_KEY", "secret-key")
    results = [{"beginTimeSeconds": 1718000000.5, "average.duration": 1.25}]
    transport = RecordingTransport(FakeResponse(_nerdgraph_payload(results)))
    src = _make_source(transport)

    records = src.query({"nrql": "SELECT average(duration) FROM Transaction"})

    assert records[0]["ts"] == 1718000000.5
    assert records[0]["value"] == 1.25


def test_query_value_none_when_no_numeric(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_NR_KEY", "secret-key")
    results = [{"timestamp": 1, "facet": "abc", "label": "xyz"}]
    transport = RecordingTransport(FakeResponse(_nerdgraph_payload(results)))
    src = _make_source(transport)

    records = src.query({"nrql": "SELECT * FROM Log"})

    assert records[0]["value"] is None
    assert records[0]["labels"] == {"facet": "abc", "label": "xyz"}


def test_bool_is_not_treated_as_numeric_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_NR_KEY", "secret-key")
    results = [{"timestamp": 1, "ok": True, "count": 3}]
    transport = RecordingTransport(FakeResponse(_nerdgraph_payload(results)))
    src = _make_source(transport)

    records = src.query({"nrql": "SELECT count(*) FROM X"})

    assert records[0]["value"] == 3  # bool skipped, int picked
    assert records[0]["labels"] == {"ok": True}


def test_nan_inf_aggregate_sanitized_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # A numeric aggregate that is NaN/Inf must normalize to value is None
    # (unified sanitize_metric_value policy) and must not raise.
    monkeypatch.setenv("TEST_NR_KEY", "secret-key")
    for bad in (float("nan"), float("inf"), float("-inf")):
        results = [{"timestamp": 1718000000000, "average.duration": bad, "host": "web-1"}]
        transport = RecordingTransport(FakeResponse(_nerdgraph_payload(results)))
        src = _make_source(transport)
        records = src.query({"nrql": "SELECT average(duration) FROM Transaction"})
        assert records[0]["value"] is None
    # A finite 0.0 aggregate is a real sample, never forged to None.
    results = [{"timestamp": 1718000000000, "average.duration": 0.0}]
    transport = RecordingTransport(FakeResponse(_nerdgraph_payload(results)))
    src = _make_source(transport)
    records = src.query({"nrql": "SELECT average(duration) FROM Transaction"})
    assert records[0]["value"] == 0.0


def test_empty_results_returns_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_NR_KEY", "secret-key")
    transport = RecordingTransport(FakeResponse(_nerdgraph_payload([])))
    src = _make_source(transport)
    assert src.query({"nrql": "SELECT count(*) FROM X"}) == []


# --------------------------------------------------------------------------- #
# API-Key header from env + body shape + timeout
# --------------------------------------------------------------------------- #
def test_api_key_header_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_NR_KEY", "header-secret")
    transport = RecordingTransport(FakeResponse(_nerdgraph_payload([])))
    src = _make_source(transport)

    src.query({"nrql": "SELECT 1"})

    sent = transport.calls[0]
    assert sent["headers"]["API-Key"] == "header-secret"
    assert sent["headers"]["Content-Type"] == "application/json"
    # account id embedded in the GraphQL body; nrql wrapped as a string literal
    assert "account(id: 12345)" in sent["json"]["query"]
    assert 'nrql(query: "SELECT 1")' in sent["json"]["query"]
    assert sent["timeout"] == src.timeout


def test_missing_api_key_raises_in_query(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_NR_KEY", raising=False)
    transport = RecordingTransport(FakeResponse(_nerdgraph_payload([])))
    src = _make_source(transport)
    with pytest.raises(RuntimeError, match="TEST_NR_KEY"):
        src.query({"nrql": "SELECT 1"})


def test_nrql_quotes_are_escaped_in_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_NR_KEY", "k")
    transport = RecordingTransport(FakeResponse(_nerdgraph_payload([])))
    src = _make_source(transport)

    src.query({"nrql": 'SELECT * FROM Log WHERE msg = "hi"'})

    query = transport.calls[0]["json"]["query"]
    assert '\\"hi\\"' in query  # embedded quotes escaped, document intact


# --------------------------------------------------------------------------- #
# SSRF: internal api_url rejected (no DNS resolution)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad_url",
    [
        "http://localhost/graphql",
        "http://127.0.0.1/graphql",
        "http://169.254.169.254/latest/meta-data",  # cloud metadata
        "http://10.0.0.5/graphql",
        "http://192.168.1.1/graphql",
        "http://[::1]/graphql",
        "http://metadata.google.internal/computeMetadata",
        "ftp://api.newrelic.com/graphql",  # bad scheme
    ],
)
def test_internal_or_bad_api_url_rejected(bad_url: str) -> None:
    with pytest.raises(ValueError):
        NewRelicSource(
            {"account_id": 1, "api_key_env": "TEST_NR_KEY", "api_url": bad_url},
            transport=RecordingTransport(FakeResponse(_nerdgraph_payload([]))),
        )


def test_public_api_url_allowed() -> None:
    src = NewRelicSource(
        {
            "account_id": 1,
            "api_key_env": "TEST_NR_KEY",
            "api_url": "https://api.eu.newrelic.com/graphql",
        },
        transport=RecordingTransport(FakeResponse(_nerdgraph_payload([]))),
    )
    assert src.api_url == "https://api.eu.newrelic.com/graphql"


# --------------------------------------------------------------------------- #
# health(): ok and not-ok-on-error, never raises
# --------------------------------------------------------------------------- #
def test_health_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_NR_KEY", "k")
    transport = RecordingTransport(FakeResponse(_nerdgraph_payload([{"1": 1}])))
    src = _make_source(transport)

    result = src.health()

    assert result == {"ok": True, "detail": "ok"}
    # health runs a trivial SELECT 1
    assert 'nrql(query: "SELECT 1")' in transport.calls[0]["json"]["query"]


def test_health_not_ok_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_NR_KEY", "k")
    transport = RecordingTransport(FakeResponse({}, status_code=500))
    src = _make_source(transport)

    result = src.health()

    assert result["ok"] is False
    assert "500" in result["detail"]


def test_health_not_ok_on_graphql_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_NR_KEY", "k")
    payload = {"errors": [{"message": "bad nrql"}]}
    transport = RecordingTransport(FakeResponse(payload))
    src = _make_source(transport)

    result = src.health()

    assert result["ok"] is False
    assert "bad nrql" in result["detail"]


def test_health_not_ok_when_transport_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_NR_KEY", "k")

    def boom(url: str, *, headers: dict, json: Any, timeout: float) -> FakeResponse:
        raise ConnectionError("network down")

    src = _make_source(boom)

    result = src.health()  # must not raise

    assert result["ok"] is False
    assert "network down" in result["detail"]


def test_health_not_ok_when_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_NR_KEY", raising=False)
    transport = RecordingTransport(FakeResponse(_nerdgraph_payload([])))
    src = _make_source(transport)

    result = src.health()  # must not raise even with missing key

    assert result["ok"] is False
    assert "TEST_NR_KEY" in result["detail"]


# --------------------------------------------------------------------------- #
# Malformed responses / spec validation
# --------------------------------------------------------------------------- #
def test_missing_nrql_in_spec_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_NR_KEY", "k")
    src = _make_source(RecordingTransport(FakeResponse(_nerdgraph_payload([]))))
    with pytest.raises(ValueError):
        src.query({})


def test_missing_account_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_NR_KEY", "k")
    src = NewRelicSource(
        {"api_key_env": "TEST_NR_KEY"},
        transport=RecordingTransport(FakeResponse(_nerdgraph_payload([]))),
    )
    with pytest.raises(RuntimeError, match="account_id"):
        src.query({"nrql": "SELECT 1"})


def test_malformed_payload_missing_results_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_NR_KEY", "k")
    transport = RecordingTransport(FakeResponse({"data": {"actor": {}}}))
    src = _make_source(transport)
    with pytest.raises(RuntimeError, match="nrql"):
        src.query({"nrql": "SELECT 1"})
