"""Cycle 4: cassandra/clickhouse connectors must SSRF-guard their default endpoint."""
from general_ludd.connectors.cassandra_stats import CassandraStatsSource
from general_ludd.connectors.clickhouse_stats import ClickHouseStatsSource


def test_cassandra_localhost_blocked():
    s = CassandraStatsSource(config={"jmx_url": "http://localhost:7070/metrics"})
    assert s._build_default_executor() is None
    assert s._driver_error == "unsafe endpoint"


def test_clickhouse_localhost_blocked():
    s = ClickHouseStatsSource(config={"url": "http://localhost:8123"})
    assert s._build_default_executor() is None
    assert s._driver_error == "unsafe endpoint"


def test_cassandra_injected_executor_bypasses_guard():
    def fake(cmd: str) -> list:
        return []

    s = CassandraStatsSource(config={"jmx_url": "http://localhost:7070/metrics"}, executor=fake)
    assert s._get_executor() is fake


def test_clickhouse_injected_executor_bypasses_guard():
    def fake(sql: str) -> list:
        return []

    s = ClickHouseStatsSource(config={"url": "http://localhost:8123"}, executor=fake)
    assert s._get_executor() is fake
