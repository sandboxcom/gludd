"""Unit tests for the connector-layer contract, registry, and Observability facade.

These tests pin the *contract* of the pluggable observability/pipeline integration
layer (general_ludd.connectors.base) without depending on any concrete connector:
- SourceRegistry: register / get / by_kind / all
- Observability.find(): resilient fan-out across registered sources (one source
  failing is captured as an error record, never aborts the whole find), merged +
  sorted by ts.
- Observability.associate(): correlation by shared key (trace_id) and by time window.
- is_safe_endpoint(): literal-host SSRF guard (rejects loopback / RFC-1918 /
  link-local / metadata; allows a public host; permits http AND https).
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from general_ludd.connectors import base
from general_ludd.connectors.base import (
    LogSource,
    MetricSource,
    Observability,
    PipelineSource,
    Source,
    SourceRegistry,
    TraceSource,
    is_safe_endpoint,
    normalized_record,
)


# --------------------------------------------------------------------------- #
# Fakes — implement the Source Protocol structurally (no inheritance required).
# --------------------------------------------------------------------------- #
class _FakeSource:
    """A minimal in-memory source that satisfies the Source Protocol."""

    def __init__(self, name: str, kind: str, records: list[dict[str, Any]]) -> None:
        self.name = name
        self.KIND = kind
        self._records = records

    def health(self) -> dict[str, Any]:
        return {"ok": True, "name": self.name}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        return list(self._records)


class _ExplodingSource:
    """A source whose query() always raises — used to prove find() is resilient."""

    name = "boom"
    KIND = "logs"

    def health(self) -> dict[str, Any]:
        return {"ok": False, "error": "down"}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        raise RuntimeError("backend exploded")


def _rec(**overrides: Any) -> dict[str, Any]:
    """Build a normalized record with sensible defaults, overriding any field."""
    base_rec: dict[str, Any] = {
        "ts": 100.0,
        "source": "fake",
        "kind": "logs",
        "level_or_status": "info",
        "message": "hello",
        "value": None,
        "labels": {},
        "raw": None,
    }
    base_rec.update(overrides)
    return base_rec


# --------------------------------------------------------------------------- #
# Normalized record contract
# --------------------------------------------------------------------------- #
def test_normalized_record_has_all_contract_keys() -> None:
    rec = normalized_record(source="s", kind="logs", message="m")
    for key in ("ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"):
        assert key in rec
    assert rec["source"] == "s"
    assert rec["kind"] == "logs"
    assert rec["message"] == "m"
    # Defaults are well-formed for sorting / grouping.
    assert rec["labels"] == {}


# --------------------------------------------------------------------------- #
# Protocol marker subtypes
# --------------------------------------------------------------------------- #
def test_marker_protocols_are_distinct() -> None:
    markers = {PipelineSource, LogSource, MetricSource, TraceSource}
    assert len(markers) == 4
    # A structural Source fake is recognized as a Source at runtime.
    assert isinstance(_FakeSource("x", "logs", []), Source)


# --------------------------------------------------------------------------- #
# SourceRegistry
# --------------------------------------------------------------------------- #
def test_registry_register_get_by_kind_all() -> None:
    reg = SourceRegistry()
    a = _FakeSource("a", "logs", [])
    b = _FakeSource("b", "metrics", [])
    c = _FakeSource("c", "logs", [])
    reg.register(a)
    reg.register(b)
    reg.register(c)

    assert reg.get("a") is a
    assert reg.get("missing") is None

    logs = reg.by_kind("logs")
    assert {s.name for s in logs} == {"a", "c"}
    assert [s.name for s in reg.by_kind("metrics")] == ["b"]
    assert reg.by_kind("traces") == []

    assert {s.name for s in reg.all()} == {"a", "b", "c"}


def test_registry_register_duplicate_name_overwrites() -> None:
    reg = SourceRegistry()
    first = _FakeSource("dup", "logs", [])
    second = _FakeSource("dup", "logs", [])
    reg.register(first)
    reg.register(second)
    assert reg.get("dup") is second
    assert len(reg.all()) == 1


# --------------------------------------------------------------------------- #
# Observability.find — resilient fan-out
# --------------------------------------------------------------------------- #
def test_find_fans_across_two_sources_merged_and_sorted() -> None:
    reg = SourceRegistry()
    reg.register(_FakeSource("s1", "logs", [_rec(ts=300.0, source="s1", message="late")]))
    reg.register(_FakeSource("s2", "logs", [_rec(ts=100.0, source="s2", message="early")]))
    obs = Observability(reg)

    results = obs.find({"q": "anything"})
    assert [r["message"] for r in results] == ["early", "late"]
    assert [r["ts"] for r in results] == [100.0, 300.0]


def test_find_captures_failing_source_as_error_record_without_aborting() -> None:
    reg = SourceRegistry()
    reg.register(_FakeSource("good", "logs", [_rec(ts=10.0, source="good", message="ok")]))
    reg.register(_ExplodingSource())
    obs = Observability(reg)

    results = obs.find({"q": "x"})
    # The good source's record survives.
    good = [r for r in results if r["message"] == "ok"]
    assert len(good) == 1

    # The failing source is captured as an error record, not dropped, not raised.
    errors = [r for r in results if r["level_or_status"] == "error"]
    assert len(errors) == 1
    err = errors[0]
    assert err["source"] == "boom"
    # Redacted: the raw exception detail must NOT leak to the caller (it is
    # logged server-side instead), and the raw exception object is not embedded.
    assert err["message"] == "query failed"
    assert "backend exploded" not in err["message"]
    assert err.get("raw") is None
    assert err["kind"] == "logs"


def test_find_kinds_filter_restricts_sources() -> None:
    reg = SourceRegistry()
    reg.register(_FakeSource("logsrc", "logs", [_rec(source="logsrc", kind="logs")]))
    reg.register(_FakeSource("metsrc", "metrics", [_rec(source="metsrc", kind="metrics", value=1.0)]))
    obs = Observability(reg)

    only_metrics = obs.find({"q": "x"}, kinds=["metrics"])
    assert {r["source"] for r in only_metrics} == {"metsrc"}


def test_find_sorts_none_ts_last() -> None:
    reg = SourceRegistry()
    reg.register(
        _FakeSource(
            "s",
            "logs",
            [
                _rec(ts=None, source="s", message="no-ts"),
                _rec(ts=5.0, source="s", message="has-ts"),
            ],
        )
    )
    obs = Observability(reg)
    results = obs.find({"q": "x"})
    # Records with a ts come first (sorted), None-ts pushed to the end.
    assert results[0]["message"] == "has-ts"
    assert results[-1]["message"] == "no-ts"


# --------------------------------------------------------------------------- #
# Observability.associate — correlation
# --------------------------------------------------------------------------- #
def test_associate_groups_by_trace_id() -> None:
    records = [
        _rec(ts=1.0, source="a", labels={"trace_id": "T1"}, message="a1"),
        _rec(ts=2.0, source="b", labels={"trace_id": "T1"}, message="b1"),
        _rec(ts=3.0, source="c", labels={"trace_id": "T2"}, message="c1"),
    ]
    groups = Observability.associate(records, by="trace_id")
    # Two groups: T1 (2 records), T2 (1 record).
    keyed = {g["key"]: g for g in groups}
    assert set(keyed) == {"T1", "T2"}
    assert {r["message"] for r in keyed["T1"]["records"]} == {"a1", "b1"}
    assert {r["message"] for r in keyed["T2"]["records"]} == {"c1"}


def test_associate_groups_by_commit() -> None:
    records = [
        _rec(source="a", labels={"commit": "deadbeef"}, message="build"),
        _rec(source="b", labels={"commit": "deadbeef"}, message="deploy"),
        _rec(source="c", labels={"commit": "cafef00d"}, message="other"),
    ]
    groups = Observability.associate(records, by="commit")
    keyed = {g["key"]: g for g in groups}
    assert set(keyed) == {"deadbeef", "cafef00d"}
    assert len(keyed["deadbeef"]["records"]) == 2


def test_associate_groups_by_time_window() -> None:
    records = [
        _rec(ts=100.0, source="a", message="r1"),
        _rec(ts=104.0, source="b", message="r2"),  # within 10s window of r1
        _rec(ts=500.0, source="c", message="r3"),  # far away -> own window
    ]
    groups = Observability.associate(records, by="time_window", window_s=10.0)
    # r1 + r2 cluster together; r3 is separate.
    sizes = sorted(len(g["records"]) for g in groups)
    assert sizes == [1, 2]
    clustered = next(g for g in groups if len(g["records"]) == 2)
    assert {r["message"] for r in clustered["records"]} == {"r1", "r2"}


def test_associate_ignores_records_missing_key() -> None:
    records = [
        _rec(source="a", labels={"trace_id": "T1"}, message="keyed"),
        _rec(source="b", labels={}, message="unkeyed"),
    ]
    groups = Observability.associate(records, by="trace_id")
    # Only the keyed record forms a group.
    assert len(groups) == 1
    assert groups[0]["key"] == "T1"


# --------------------------------------------------------------------------- #
# is_safe_endpoint — literal-host SSRF guard
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9090/api",
        "http://localhost:3000",
        "https://localhost/q",
        "http://10.0.0.5/metrics",
        "http://192.168.1.10:9200",
        "http://172.16.0.4",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://[::1]:8080/",  # ipv6 loopback
        "http://metadata.google.internal/computeMetadata/v1/",
        "ftp://example.com/x",  # disallowed scheme
        "not a url",
        "",
    ],
)
def test_is_safe_endpoint_rejects_internal_and_metadata(url: str) -> None:
    assert is_safe_endpoint(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "http://loki.observability.svc.example.com:3100/loki/api",
        "https://grafana.example.com/api/datasources",
        "http://prometheus.public.example.org:9090/api/v1/query",
    ],
)
def test_is_safe_endpoint_allows_public_http_and_https(url: str) -> None:
    assert is_safe_endpoint(url) is True


def test_is_safe_endpoint_does_no_dns() -> None:
    # A literal public-looking hostname that does NOT resolve must still pass:
    # the guard is a literal-host check, never a DNS lookup.
    assert is_safe_endpoint("http://this-host-does-not-resolve.example.test:8086/") is True


def test_base_module_exports_helper() -> None:
    # is_safe_endpoint is importable from the base module for connectors to reuse.
    assert callable(base.is_safe_endpoint)


# --------------------------------------------------------------------------- #
# Boundary numeric policy — NaN / Inf coercion at normalized_record()
# --------------------------------------------------------------------------- #
def test_normalized_record_coerces_nan_value_to_none() -> None:
    rec = normalized_record(source="s", kind="metrics", value=float("nan"))
    # NaN would poison aggregation/sorting; policy coerces it to the None sentinel.
    assert rec["value"] is None


def test_normalized_record_coerces_inf_value_to_none() -> None:
    pos = normalized_record(source="s", kind="metrics", value=float("inf"))
    neg = normalized_record(source="s", kind="metrics", value=float("-inf"))
    assert pos["value"] is None
    assert neg["value"] is None


def test_normalized_record_finite_value_unchanged() -> None:
    rec = normalized_record(source="s", kind="metrics", value=42.5)
    assert rec["value"] == 42.5
    zero = normalized_record(source="s", kind="metrics", value=0.0)
    assert zero["value"] == 0.0
    # Explicit None stays None (no payload).
    none = normalized_record(source="s", kind="metrics", value=None)
    assert none["value"] is None


def test_normalized_record_coerces_nan_and_inf_ts_to_none() -> None:
    # A non-finite ts would corrupt the ts-sort (NaN compares False) — coerce it
    # to None so the record sorts last like any other untimed record.
    assert normalized_record(source="s", kind="logs", ts=float("nan"))["ts"] is None
    assert normalized_record(source="s", kind="logs", ts=float("inf"))["ts"] is None
    assert normalized_record(source="s", kind="logs", ts=-12.5)["ts"] == -12.5


def test_normalized_record_logs_warning_on_non_finite(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from unittest.mock import patch

    base_logger = logging.getLogger("general_ludd.connectors.base")
    with patch.object(base_logger, "warning", wraps=base_logger.warning) as mock_warning:
        normalized_record(source="s", kind="metrics", value=float("nan"))
    assert mock_warning.call_count >= 1, \
        f"logger.warning not called. calls: {mock_warning.mock_calls}"
    warn_msgs = [call.args[0] for call in mock_warning.mock_calls if call.args]
    assert any("non-finite" in str(msg) for msg in warn_msgs), \
        f"'non-finite' not in any warning. Messages: {warn_msgs}"


def test_finite_or_none_direct() -> None:
    assert base._finite_or_none(None) is None
    assert base._finite_or_none(3.0) == 3.0
    assert base._finite_or_none(float("nan")) is None
    assert base._finite_or_none(float("inf")) is None
    assert base._finite_or_none(float("-inf")) is None


# --------------------------------------------------------------------------- #
# find() result-set cap — bounded fan-out
# --------------------------------------------------------------------------- #
def test_find_caps_unbounded_result_set_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from unittest.mock import patch

    cap = Observability.MAX_FIND_RESULTS
    huge = [_rec(ts=float(i), source="flood", message=str(i)) for i in range(cap + 50)]
    reg = SourceRegistry()
    reg.register(_FakeSource("flood", "logs", huge))
    obs = Observability(reg)

    base_logger = logging.getLogger("general_ludd.connectors.base")
    with patch.object(base_logger, "warning", wraps=base_logger.warning) as mock_warning:
        results = obs.find({"q": "x"})

    assert len(results) == 10_000
    warn_msgs = [call.args[0] for call in mock_warning.mock_calls if call.args]
    assert any("truncated" in str(m) for m in warn_msgs), \
        f"No 'truncated' in warning args: {warn_msgs}"


def test_find_caps_across_multiple_sources(caplog: pytest.LogCaptureFixture) -> None:
    from unittest.mock import patch

    cap = Observability.MAX_FIND_RESULTS
    half = cap // 2 + 100
    reg = SourceRegistry()
    reg.register(_FakeSource("a", "logs", [_rec(source="a") for _ in range(half)]))
    reg.register(_FakeSource("b", "logs", [_rec(source="b") for _ in range(half)]))
    obs = Observability(reg)

    base_logger = logging.getLogger("general_ludd.connectors.base")
    with patch.object(base_logger, "warning", wraps=base_logger.warning) as mock_warning:
        results = obs.find({"q": "x"})

    assert len(results) == 20_000
    warn_msgs = [call.args[0] for call in mock_warning.mock_calls if call.args]
    assert any("truncated" in str(m) for m in warn_msgs), \
        f"No 'truncated' in warning args: {warn_msgs}"


def test_find_under_cap_returns_all_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from unittest.mock import patch

    reg = SourceRegistry()
    reg.register(_FakeSource("s", "logs", [_rec(ts=float(i), source="s") for i in range(5)]))
    obs = Observability(reg)

    base_logger = logging.getLogger("general_ludd.connectors.base")
    with patch.object(base_logger, "warning", wraps=base_logger.warning) as mock_warning:
        results = obs.find({"q": "x"})

    assert len(results) == 5
    warn_msgs = [call.args[0] for call in mock_warning.mock_calls if call.args]
    assert not any("truncated" in str(m) for m in warn_msgs)
