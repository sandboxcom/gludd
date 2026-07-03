"""Unit tests for PromScrapeSource — generic Prometheus exposition scraper.

TDD with a MOCKED HTTP transport (no network, no DNS). A canned
Prometheus-text payload (counter + gauge + histogram with labels, plus
HELP/TYPE comments) drives per-sample normalization assertions.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.prom_scrape import PromScrapeSource

# --- Canned Prometheus exposition payload -----------------------------------
# Mixes HELP/TYPE comments, a counter, a labelled gauge, a histogram (with
# _bucket le=... + _sum + _count), an explicit-timestamp sample, and two
# malformed lines that must be skipped.
PROM_TEXT = """\
# HELP http_requests_total The total number of HTTP requests.
# TYPE http_requests_total counter
http_requests_total{method="post",code="200"} 1027 1395066363000
http_requests_total{method="post",code="400"} 3
# HELP node_memory_bytes Memory in bytes.
# TYPE node_memory_bytes gauge
node_memory_bytes 5.36870912e+08
# A bare comment that is not HELP/TYPE — must be ignored.
# TYPE request_duration_seconds histogram
request_duration_seconds_bucket{le="0.1"} 24054
request_duration_seconds_bucket{le="0.5"} 33444
request_duration_seconds_bucket{le="+Inf"} 144320
request_duration_seconds_sum 53423
request_duration_seconds_count 144320
this_line_has_no_value{broken="yes"}
not_a_metric_at_all !!!
node_memory_bytes{host="db-1"} NaN
"""


class _Resp:
    def __init__(self, status: int, body: str, *, headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self.text = body
        self.headers = headers or {}


class _FakeTransport:
    """Injectable transport: records calls, returns scripted responses."""

    def __init__(self, response: Any | Exception) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, *, headers: dict[str, str] | None = None, timeout: float | None = None) -> Any:
        self.calls.append({"url": url, "headers": headers or {}, "timeout": timeout})
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _make(transport: Any, **cfg: Any) -> PromScrapeSource:
    base = {"base_url": "http://10.0.0.5:9100", "allow_private": True}
    base.update(cfg)
    return PromScrapeSource(base, transport=transport)


# --- Contract / attributes ---------------------------------------------------


def test_kind_is_metrics_class_attr() -> None:
    assert PromScrapeSource.KIND == "metrics"


def test_has_name_attr() -> None:
    src = _make(_FakeTransport(_Resp(200, "")))
    assert isinstance(src.name, str)
    assert src.name


# --- SSRF guard --------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9100",
        "http://localhost:9100",
        "http://10.1.2.3:9100",
        "http://192.168.1.1/metrics",
        "http://172.16.0.9:9100",
        "http://169.254.169.254/latest/meta-data",  # cloud metadata IP
        "http://[::1]:9100",
        # Metadata NAME coverage added by routing through the canonical guard.
        "http://metadata.google.internal/metrics",
    ],
)
def test_private_base_rejected_without_optin(url: str) -> None:
    with pytest.raises(ValueError):
        PromScrapeSource({"base_url": url}, transport=_FakeTransport(_Resp(200, "")))


def test_private_base_allowed_with_optin() -> None:
    # allow_private=True opts into internal exporters (the common case).
    src = PromScrapeSource(
        {"base_url": "http://10.0.0.5:9100", "allow_private": True},
        transport=_FakeTransport(_Resp(200, "")),
    )
    assert src is not None


def test_localhost_allowed_with_optin() -> None:
    # allow_private=True must also permit a named loopback host (localhost).
    src = PromScrapeSource(
        {"base_url": "http://localhost:9100", "allow_private": True},
        transport=_FakeTransport(_Resp(200, "")),
    )
    assert src is not None


def test_public_base_allowed() -> None:
    src = PromScrapeSource(
        {"base_url": "http://93.184.216.34:9100"},
        transport=_FakeTransport(_Resp(200, "")),
    )
    assert src is not None


def test_spec_allow_private_overrides_for_query() -> None:
    # base is public; spec allow_private should be harmless / accepted.
    t = _FakeTransport(_Resp(200, "node_up 1\n"))
    src = PromScrapeSource({"base_url": "http://93.184.216.34:9100"}, transport=t)
    out = src.query({"allow_private": True})
    assert isinstance(out, list)


# --- health() ----------------------------------------------------------------


def test_health_ok() -> None:
    src = _make(_FakeTransport(_Resp(200, "node_up 1\n")))
    h = src.health()
    assert h["ok"] is True
    assert "detail" in h


def test_health_not_ok_on_http_error() -> None:
    src = _make(_FakeTransport(_Resp(503, "down")))
    h = src.health()
    assert h["ok"] is False
    assert "detail" in h


def test_health_never_raises_on_transport_exception() -> None:
    src = _make(_FakeTransport(RuntimeError("boom")))
    h = src.health()
    assert h["ok"] is False
    assert "boom" in h["detail"] or "error" in h["detail"].lower()


# --- query(): normalization --------------------------------------------------


def test_query_normalizes_counter_sample() -> None:
    t = _FakeTransport(_Resp(200, PROM_TEXT))
    src = _make(t)
    rows = src.query({})
    counters = [
        r for r in rows if r["message"] == "http_requests_total" and r["labels"].get("code") == "200"
    ]
    assert len(counters) == 1
    r = counters[0]
    assert r["kind"] == "metrics"
    assert r["source"] == src.name
    assert r["value"] == pytest.approx(1027.0)
    assert r["labels"] == {"method": "post", "code": "200"}
    assert r["level_or_status"] == ""
    assert "raw" in r


def test_query_explicit_timestamp_used() -> None:
    t = _FakeTransport(_Resp(200, PROM_TEXT))
    rows = _make(t).query({})
    r = next(
        x for x in rows if x["message"] == "http_requests_total" and x["labels"].get("code") == "200"
    )
    # 1395066363000 ms -> 1395066363.0 s
    assert r["ts"] == pytest.approx(1395066363.0)


def test_query_default_timestamp_is_now_when_absent() -> None:
    t = _FakeTransport(_Resp(200, "node_up 1\n"))
    rows = _make(t).query({})
    assert len(rows) == 1
    assert rows[0]["ts"] > 0


def test_query_parses_gauge_scientific_notation() -> None:
    t = _FakeTransport(_Resp(200, PROM_TEXT))
    rows = _make(t).query({})
    g = next(x for x in rows if x["message"] == "node_memory_bytes" and not x["labels"])
    assert g["value"] == pytest.approx(5.36870912e08)


def test_query_parses_histogram_buckets() -> None:
    t = _FakeTransport(_Resp(200, PROM_TEXT))
    rows = _make(t).query({})
    buckets = [r for r in rows if r["message"] == "request_duration_seconds_bucket"]
    assert len(buckets) == 3
    le_inf = next(b for b in buckets if b["labels"].get("le") == "+Inf")
    assert le_inf["value"] == pytest.approx(144320.0)
    le_01 = next(b for b in buckets if b["labels"].get("le") == "0.1")
    assert le_01["value"] == pytest.approx(24054.0)
    # _sum and _count present too
    assert any(r["message"] == "request_duration_seconds_sum" for r in rows)
    assert any(r["message"] == "request_duration_seconds_count" for r in rows)


def test_query_skips_malformed_lines() -> None:
    t = _FakeTransport(_Resp(200, PROM_TEXT))
    rows = _make(t).query({})
    msgs = {r["message"] for r in rows}
    assert "this_line_has_no_value" not in msgs
    assert "not_a_metric_at_all" not in msgs


def test_query_skips_nan_value() -> None:
    t = _FakeTransport(_Resp(200, PROM_TEXT))
    rows = _make(t).query({})
    # the NaN-valued node_memory_bytes{host=db-1} must be dropped (unparseable value)
    assert not [r for r in rows if r["labels"].get("host") == "db-1"]


def test_query_comments_not_emitted_as_records() -> None:
    t = _FakeTransport(_Resp(200, PROM_TEXT))
    rows = _make(t).query({})
    for r in rows:
        assert not r["message"].startswith("#")


def test_query_metric_prefix_filter() -> None:
    t = _FakeTransport(_Resp(200, PROM_TEXT))
    rows = _make(t).query({"metric_prefix": "request_duration_seconds"})
    assert rows
    assert all(r["message"].startswith("request_duration_seconds") for r in rows)


def test_query_hits_metrics_endpoint() -> None:
    t = _FakeTransport(_Resp(200, "node_up 1\n"))
    _make(t).query({})
    assert t.calls
    assert t.calls[0]["url"].endswith("/metrics")
    assert t.calls[0]["timeout"] is not None  # time-bound


def test_query_returns_empty_on_http_error() -> None:
    t = _FakeTransport(_Resp(500, "oops"))
    rows = _make(t).query({})
    assert rows == []


# --- bearer token from env ---------------------------------------------------


def test_bearer_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROM_TOKEN", "s3cr3t")
    t = _FakeTransport(_Resp(200, "node_up 1\n"))
    src = _make(t, token_env="PROM_TOKEN")
    src.query({})
    auth = t.calls[0]["headers"].get("Authorization", "")
    assert auth == "Bearer s3cr3t"


def test_no_auth_header_when_no_token_env() -> None:
    t = _FakeTransport(_Resp(200, "node_up 1\n"))
    src = _make(t)
    src.query({})
    assert "Authorization" not in t.calls[0]["headers"]


def test_missing_env_token_no_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ABSENT_TOKEN", raising=False)
    t = _FakeTransport(_Resp(200, "node_up 1\n"))
    src = _make(t, token_env="ABSENT_TOKEN")
    src.query({})
    assert "Authorization" not in t.calls[0]["headers"]
