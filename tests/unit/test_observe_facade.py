"""Unit tests for the :class:`GluddObserve` cross-source debugging facade.

The facade fans queries across a set of configured connectors (any object
satisfying the ``Source`` structural contract: ``name``, ``KIND``, ``query``),
correlates incidents, builds merged timelines, and derives a service/host
topology. Every source here is a tiny in-memory fake that emits canned
normalized records — no network, no real connectors, no daemon.

Covered:
- ``query_sources`` fan-out across the KINDs requested,
- per-source error isolation (one raising source never aborts the fan-out and
  is surfaced as an error record),
- KIND filtering (a source of an unrequested KIND is not queried),
- ``correlate_incident`` grouping related records around a seed via
  ``normalize.correlate`` (trace_id join key),
- ``timeline`` merged time-ordering (None-ts records sort last),
- ``topology`` service/host map derived from canonical join keys,
- accepting both a dict provider and a callable provider, and a
  registry-like provider exposing ``by_kind`` (composes with
  ConnectorRegistry WITHOUT importing it).
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.observe.facade import GluddObserve


# --------------------------------------------------------------------------- #
# Test doubles: in-memory sources emitting canned normalized records.
# --------------------------------------------------------------------------- #
class FakeSource:
    """A Source-shaped fake that returns a fixed list of records from query()."""

    def __init__(self, name: str, kind: str, records: list[dict[str, Any]]) -> None:
        self.name = name
        self.KIND = kind
        self._records = records
        self.queried_with: list[dict[str, Any]] = []

    def health(self) -> dict[str, Any]:
        return {"ok": True, "source": self.name}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        self.queried_with.append(spec)
        return list(self._records)


class ExplodingSource:
    """A Source whose query() always raises — to prove error isolation."""

    def __init__(self, name: str, kind: str) -> None:
        self.name = name
        self.KIND = kind

    def health(self) -> dict[str, Any]:
        return {"ok": False, "source": self.name}

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        raise RuntimeError("backend down")


def _rec(
    *,
    source: str,
    kind: str,
    ts: float | None,
    message: str = "",
    labels: dict[str, Any] | None = None,
    level: str = "info",
) -> dict[str, Any]:
    return {
        "ts": ts,
        "source": source,
        "kind": kind,
        "level_or_status": level,
        "message": message,
        "value": None,
        "labels": labels or {},
        "raw": {},
    }


# --------------------------------------------------------------------------- #
# query_sources: fan-out, KIND filtering, error isolation
# --------------------------------------------------------------------------- #
class TestQuerySources:
    def test_fans_out_across_requested_kinds(self) -> None:
        logs = FakeSource(
            "loki", "logs", [_rec(source="loki", kind="logs", ts=10.0, message="a")]
        )
        metrics = FakeSource(
            "prom",
            "metrics",
            [_rec(source="prom", kind="metrics", ts=20.0, message="b")],
        )
        obs = GluddObserve({"loki": logs, "prom": metrics})

        out = obs.query_sources(["logs", "metrics"], {"q": "x"})

        sources = {r["source"] for r in out}
        assert sources == {"loki", "prom"}
        # spec was forwarded to each matching source
        assert logs.queried_with == [{"q": "x"}]
        assert metrics.queried_with == [{"q": "x"}]

    def test_filters_out_unrequested_kinds(self) -> None:
        logs = FakeSource(
            "loki", "logs", [_rec(source="loki", kind="logs", ts=1.0)]
        )
        traces = FakeSource(
            "jaeger", "traces", [_rec(source="jaeger", kind="traces", ts=2.0)]
        )
        obs = GluddObserve({"loki": logs, "jaeger": traces})

        out = obs.query_sources(["logs"], {})

        assert {r["source"] for r in out} == {"loki"}
        # the unrequested-kind source was never queried
        assert traces.queried_with == []

    def test_single_source_failure_is_isolated_as_error_record(self) -> None:
        good = FakeSource(
            "loki", "logs", [_rec(source="loki", kind="logs", ts=5.0, message="ok")]
        )
        bad = ExplodingSource("broken", "logs")
        obs = GluddObserve({"loki": good, "broken": bad})

        out = obs.query_sources(["logs"], {})

        # The good source's record is present...
        assert any(r["source"] == "loki" for r in out)
        # ...and the failure became an error record, never an exception.
        errors = [r for r in out if r["level_or_status"] == "error"]
        assert len(errors) == 1
        assert errors[0]["source"] == "broken"
        assert "backend down" in errors[0]["message"]
        # The facade also exposes collected errors separately.
        assert obs.errors and obs.errors[0]["source"] == "broken"

    def test_results_are_time_ordered(self) -> None:
        a = FakeSource("a", "logs", [_rec(source="a", kind="logs", ts=30.0)])
        b = FakeSource("b", "logs", [_rec(source="b", kind="logs", ts=10.0)])
        c = FakeSource(
            "c", "logs", [_rec(source="c", kind="logs", ts=None)]
        )  # untimed -> last
        obs = GluddObserve({"a": a, "b": b, "c": c})

        out = obs.query_sources(["logs"], {})

        order = [r["source"] for r in out]
        assert order == ["b", "a", "c"]

    def test_time_bounds_injected_into_spec(self) -> None:
        src = FakeSource("loki", "logs", [])
        obs = GluddObserve({"loki": src})

        obs.query_sources(["logs"], {"q": "z"}, start=100.0, end=200.0)

        spec = src.queried_with[0]
        assert spec["q"] == "z"
        assert spec["start"] == 100.0
        assert spec["end"] == 200.0


# --------------------------------------------------------------------------- #
# provider shapes: dict, callable, registry-like
# --------------------------------------------------------------------------- #
class TestProviderShapes:
    def test_callable_provider(self) -> None:
        logs = FakeSource("loki", "logs", [_rec(source="loki", kind="logs", ts=1.0)])

        def provider() -> list[Any]:
            return [logs]

        obs = GluddObserve(provider)
        out = obs.query_sources(["logs"], {})
        assert {r["source"] for r in out} == {"loki"}

    def test_registry_like_provider_with_by_kind(self) -> None:
        logs = FakeSource("loki", "logs", [_rec(source="loki", kind="logs", ts=1.0)])
        metrics = FakeSource(
            "prom", "metrics", [_rec(source="prom", kind="metrics", ts=2.0)]
        )

        class FakeRegistry:
            """Duck-types ConnectorRegistry's by_kind/list_sources surface."""

            def by_kind(self, kind: str) -> list[Any]:
                return [s for s in (logs, metrics) if kind == s.KIND]

            def list_sources(self) -> list[Any]:
                return [logs, metrics]

        obs = GluddObserve(FakeRegistry())
        out = obs.query_sources(["metrics"], {})
        assert {r["source"] for r in out} == {"prom"}


# --------------------------------------------------------------------------- #
# correlate_incident
# --------------------------------------------------------------------------- #
class TestCorrelateIncident:
    def test_groups_related_records_by_trace_id(self) -> None:
        # The seed incident carries trace_id=T1. Logs/traces/metrics across
        # sources share that trace_id and must group together.
        log = FakeSource(
            "loki",
            "logs",
            [
                _rec(
                    source="loki",
                    kind="logs",
                    ts=11.0,
                    message="error in checkout",
                    labels={"trace_id": "T1", "service": "checkout"},
                    level="error",
                ),
                _rec(
                    source="loki",
                    kind="logs",
                    ts=12.0,
                    message="unrelated",
                    labels={"trace_id": "T2", "service": "billing"},
                ),
            ],
        )
        trace = FakeSource(
            "jaeger",
            "traces",
            [
                _rec(
                    source="jaeger",
                    kind="traces",
                    ts=11.5,
                    message="span",
                    labels={"trace_id": "T1", "service": "checkout"},
                ),
            ],
        )
        obs = GluddObserve({"loki": log, "jaeger": trace})

        seed = _rec(
            source="pagerduty",
            kind="incidents",
            ts=10.0,
            message="checkout 500s",
            labels={"trace_id": "T1", "service": "checkout"},
        )

        result = obs.correlate_incident(
            seed, kinds=["logs", "traces"], by="trace_id"
        )

        # The result groups by the canonical join key. Only the T1 group is
        # the incident's group; it must contain the seed + both T1 records.
        assert "T1" in result
        t1_sources = {r["source"] for r in result["T1"]}
        assert {"pagerduty", "loki", "jaeger"} <= t1_sources
        # The unrelated T2 record is in its own group, not T1's.
        t1_msgs = {r["message"] for r in result["T1"]}
        assert "unrelated" not in t1_msgs

    def test_correlate_incident_isolates_source_failures(self) -> None:
        good = FakeSource(
            "loki",
            "logs",
            [_rec(source="loki", kind="logs", ts=2.0, labels={"trace_id": "T9"})],
        )
        bad = ExplodingSource("broken", "traces")
        obs = GluddObserve({"loki": good, "broken": bad})

        seed = _rec(
            source="pd", kind="incidents", ts=1.0, labels={"trace_id": "T9"}
        )
        # Must not raise despite the exploding traces source.
        result = obs.correlate_incident(seed, kinds=["logs", "traces"], by="trace_id")
        assert "T9" in result
        assert obs.errors and obs.errors[0]["source"] == "broken"


# --------------------------------------------------------------------------- #
# timeline
# --------------------------------------------------------------------------- #
class TestTimeline:
    def test_merges_and_orders_records_by_time(self) -> None:
        a = FakeSource(
            "loki",
            "logs",
            [
                _rec(source="loki", kind="logs", ts=300.0, message="late"),
                _rec(source="loki", kind="logs", ts=100.0, message="early"),
            ],
        )
        b = FakeSource(
            "prom",
            "metrics",
            [_rec(source="prom", kind="metrics", ts=200.0, message="mid")],
        )
        obs = GluddObserve({"loki": a, "prom": b})

        tl = obs.timeline(window=("logs", "metrics"))

        assert [r["message"] for r in tl] == ["early", "mid", "late"]

    def test_timeline_respects_start_end_filter(self) -> None:
        a = FakeSource(
            "loki",
            "logs",
            [
                _rec(source="loki", kind="logs", ts=50.0, message="before"),
                _rec(source="loki", kind="logs", ts=150.0, message="inside"),
                _rec(source="loki", kind="logs", ts=500.0, message="after"),
            ],
        )
        obs = GluddObserve({"loki": a})

        tl = obs.timeline(window=("logs",), start=100.0, end=200.0)

        assert [r["message"] for r in tl] == ["inside"]


# --------------------------------------------------------------------------- #
# topology
# --------------------------------------------------------------------------- #
class TestTopology:
    def test_builds_service_and_host_map_from_join_keys(self) -> None:
        src = FakeSource(
            "loki",
            "logs",
            [
                _rec(
                    source="loki",
                    kind="logs",
                    ts=1.0,
                    labels={"service": "checkout", "host": "web-01:8080"},
                ),
                _rec(
                    source="loki",
                    kind="logs",
                    ts=2.0,
                    labels={"service": "checkout", "host": "web-02"},
                ),
                _rec(
                    source="loki",
                    kind="logs",
                    ts=3.0,
                    labels={"service": "billing", "host": "web-01"},
                ),
            ],
        )
        obs = GluddObserve({"loki": src})

        topo = obs.topology(kinds=["logs"])

        # service -> set of hosts (host normalized: web-01:8080 -> web-01)
        assert topo["services"]["checkout"] == {"web-01", "web-02"}
        assert topo["services"]["billing"] == {"web-01"}
        # host -> set of services
        assert topo["hosts"]["web-01"] == {"checkout", "billing"}
        assert topo["hosts"]["web-02"] == {"checkout"}

    def test_topology_ignores_records_without_join_keys(self) -> None:
        src = FakeSource(
            "loki",
            "logs",
            [_rec(source="loki", kind="logs", ts=1.0, labels={})],
        )
        obs = GluddObserve({"loki": src})
        topo = obs.topology(kinds=["logs"])
        assert topo["services"] == {}
        assert topo["hosts"] == {}


# --------------------------------------------------------------------------- #
# construction guards
# --------------------------------------------------------------------------- #
class TestConstruction:
    def test_rejects_unusable_provider(self) -> None:
        with pytest.raises(TypeError):
            GluddObserve(object())  # not a dict, callable, or registry-like
