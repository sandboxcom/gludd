"""Unit tests for CassandraStatsSource — canned executor + JMX scrape parsing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from general_ludd.connectors.cassandra_stats import (
    CassandraStatsSource,
    _parse_prometheus,
    _split_sample,
)

_ROWS: dict[str, list[dict[str, Any]]] = {
    "compactionstats": [
        {"metric": "pending_compactions", "value": 4, "keyspace": "app", "table": "events"},
    ],
    "tablestats": [
        {"metric": "read_latency_ms", "value": 1.5, "keyspace": "app", "table": "events"},
        {"metric": "write_latency_ms", "value": 0.8, "keyspace": "app", "table": "events"},
    ],
    "tpstats": [
        {"metric": "hints_total", "value": 17, "keyspace": "", "table": ""},
    ],
}


def _canned_executor(command: str) -> Sequence[Mapping[str, Any]]:
    return _ROWS.get(command, [])


def test_contract_attrs() -> None:
    src = CassandraStatsSource({"name": "cass-1"}, executor=_canned_executor)
    assert src.KIND == "metrics"
    assert src.name == "cass-1"


def test_query_normalizes_records() -> None:
    src = CassandraStatsSource({"name": "cass-1"}, executor=_canned_executor)
    records = src.query({})
    assert records
    required = {"ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"}
    for rec in records:
        assert required <= set(rec)
        assert rec["kind"] == "metrics"
        assert rec["source"] == "cass-1"


def test_metric_values_and_labels() -> None:
    src = CassandraStatsSource({}, executor=_canned_executor)
    records = src.query({})
    by = {r["message"]: r for r in records}

    assert by["pending_compactions"]["value"] == 4
    assert by["pending_compactions"]["labels"]["keyspace"] == "app"
    assert by["pending_compactions"]["labels"]["table"] == "events"
    assert by["read_latency_ms"]["value"] == 1.5
    assert by["write_latency_ms"]["value"] == 0.8
    assert by["hints_total"]["value"] == 17
    assert by["pending_compactions"]["labels"]["command"] == "compactionstats"


def test_health_ok_with_executor() -> None:
    src = CassandraStatsSource({}, executor=_canned_executor)
    assert src.health()["ok"] is True


def test_health_never_raises_on_bad_executor() -> None:
    def boom(_command: str) -> Sequence[Mapping[str, Any]]:
        raise RuntimeError("node down")

    src = CassandraStatsSource({}, executor=boom)
    health = src.health()
    assert health["ok"] is False
    assert "probe failed" in health["detail"]
    assert src.query({}) == []


def test_prometheus_scrape_parsing() -> None:
    text = (
        "# HELP cassandra_compaction_pending pending\n"
        '# TYPE cassandra_compaction_pending gauge\n'
        'cassandra_compaction_pending_compactions{keyspace="app",table="events"} 4.0\n'
        'cassandra_table_read_latency{keyspace="app",table="events"} 1.5\n'
        'cassandra_threadpool_hints_total 17\n'
        "unrelated_metric 99\n"
    )
    comp = _parse_prometheus(text, "compactionstats")
    assert len(comp) == 1
    assert comp[0]["metric"] == "cassandra_compaction_pending_compactions"
    assert comp[0]["value"] == 4.0
    assert comp[0]["keyspace"] == "app"
    assert comp[0]["table"] == "events"

    tp = _parse_prometheus(text, "tpstats")
    assert any(r["metric"] == "cassandra_threadpool_hints_total" and r["value"] == 17 for r in tp)

    # Unrelated lines never leak into any group.
    assert all("unrelated" not in r["metric"] for r in comp + tp)


def test_nan_inf_sample_value_sanitized_to_none() -> None:
    # JMX/Prometheus samples whose value is NaN/Inf must parse to value None
    # (unified sanitize_metric_value policy) and must not raise. Prometheus text
    # spells these "NaN", "+Inf", "-Inf".
    text = (
        'cassandra_compaction_pending_compactions{keyspace="app",table="events"} NaN\n'
        'cassandra_compaction_pending_other{keyspace="app",table="events"} +Inf\n'
        'cassandra_compaction_pending_third{keyspace="app",table="events"} -Inf\n'
        'cassandra_compaction_pending_real{keyspace="app",table="events"} 0.0\n'
    )
    rows = _parse_prometheus(text, "compactionstats")
    by = {r["metric"]: r for r in rows}
    # NaN/Inf samples are dropped by _parse_prometheus (value is None -> skipped).
    assert "cassandra_compaction_pending_compactions" not in by
    assert "cassandra_compaction_pending_other" not in by
    assert "cassandra_compaction_pending_third" not in by
    # A finite 0.0 sample is a real value and is kept.
    assert by["cassandra_compaction_pending_real"]["value"] == 0.0


def test_split_sample_nan_inf_returns_none() -> None:
    # _split_sample routes the value through sanitize_metric_value: NaN/Inf -> None,
    # never raising. 0.0 is a real sample and is preserved.
    assert _split_sample("metric_nan NaN")[2] is None
    assert _split_sample("metric_inf +Inf")[2] is None
    assert _split_sample("metric_ninf -Inf")[2] is None
    assert _split_sample("metric_zero 0.0")[2] == 0.0
    assert _split_sample("metric_bad notanumber")[2] is None


def test_no_hardcoded_credentials_or_shell() -> None:
    import inspect

    from general_ludd.connectors import cassandra_stats

    text = inspect.getsource(cassandra_stats)
    # Secret comes from an env var, never an inline literal.
    assert "token_env" in text
    assert 'token = "' not in text.lower()
    # No subprocess / shell execution anywhere (transport is the executor's job).
    assert "shell=True" not in text.replace(" ", "")
    assert "import subprocess" not in text
    assert "subprocess." not in text
