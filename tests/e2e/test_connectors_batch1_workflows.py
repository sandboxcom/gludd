"""E2E tests for connectors batch 1 — RabbitMQ, NATS, Postgres, Redis, Kafka,
Prometheus, PromScrape, LocalFiles, GrafanaLoki, and utility modules.

Uses mock transports/executors so no real network I/O or external services are
required. Tests the full connector lifecycle: config validation, SSRF guards,
credential resolution, query normalization, health checks, and error resilience.
"""

from __future__ import annotations

import os
import tempfile
from typing import cast

import pytest

# ============================================================================
# Test helpers — shared mock transports
# ============================================================================


def _make_http_transport(
    responses: dict[str | None, tuple[int, object]] | None = None,
    default_status: int = 200,
    default_body: object = None,
):
    """Factory for an injectable HTTP transport matching `(url, params, headers, timeout) -> (status, body)`.

    The returned callable records every call in `.calls` for later assertion.
    """

    class Transport:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def __call__(
            self,
            url: str,
            params: dict[str, object] | None = None,
            headers: dict[str, str] | None = None,
            timeout: float | None = None,
        ) -> tuple[int, object]:
            self.calls.append(
                {
                    "url": url,
                    "params": params,
                    "headers": headers,
                    "timeout": timeout,
                }
            )
            if responses:
                for key, (status, body) in responses.items():
                    if key is not None and key in url:
                        return status, body
                if None in responses:
                    return cast(tuple[int, object], responses[None])
            return default_status, default_body

    return Transport()


def _make_prom_transport(
    status: int = 200,
    body: object = None,
):
    """Injectable transport returning (status, json-body) for PrometheusSource."""
    transport = _make_http_transport(default_status=status, default_body=body or {})
    return transport


# ============================================================================
# 1. RabbitMQ Connector
# ============================================================================


class TestRabbitMqConnector:
    def test_constructs_with_valid_config(self):
        from general_ludd.connectors.rabbitmq import RabbitMqSource

        transport = _make_http_transport()
        source = RabbitMqSource(
            {"name": "rmq-prod", "base_url": "https://mq.example.com"},
            http_get=transport,
        )
        assert source.name == "rmq-prod"
        assert source.KIND == "metrics"

    def test_constructs_auto_name_from_host(self):
        from general_ludd.connectors.rabbitmq import RabbitMqSource

        transport = _make_http_transport()
        source = RabbitMqSource(
            {"base_url": "https://mq.example.com"}, http_get=transport
        )
        assert "mq.example.com" in source.name

    def test_appends_default_mgmt_port(self):
        from general_ludd.connectors.rabbitmq import RabbitMqSource

        transport = _make_http_transport()
        source = RabbitMqSource(
            {"base_url": "https://mq.example.com"}, http_get=transport
        )
        source.health()
        url = transport.calls[0]["url"]
        assert "15672" in url

    def test_rejects_loopback_base_url(self):
        from general_ludd.connectors.rabbitmq import RabbitMqSource

        with pytest.raises(ValueError, match="blocked"):
            RabbitMqSource(
                {"base_url": "http://127.0.0.1:15672"}, http_get=_make_http_transport()
            )

    def test_rejects_metadata_ip(self):
        from general_ludd.connectors.rabbitmq import RabbitMqSource

        with pytest.raises(ValueError, match="blocked"):
            RabbitMqSource(
                {"base_url": "http://169.254.169.254:15672"},
                http_get=_make_http_transport(),
            )

    def test_rejects_empty_base_url(self):
        from general_ludd.connectors.rabbitmq import RabbitMqSource

        with pytest.raises(ValueError, match="base_url is required"):
            RabbitMqSource({"base_url": ""}, http_get=_make_http_transport())

    def test_health_ok_when_overview_responds(self):
        from general_ludd.connectors.rabbitmq import RabbitMqSource

        transport = _make_http_transport(
            default_status=200,
            default_body={"rabbitmq_version": "3.12.0", "cluster_name": "prod"},
        )
        source = RabbitMqSource(
            {"base_url": "https://mq.example.com"}, http_get=transport
        )
        result = source.health()
        assert result["ok"] is True
        assert "3.12.0" in str(result["detail"])

    def test_health_false_on_500(self):
        from general_ludd.connectors.rabbitmq import RabbitMqSource

        transport = _make_http_transport(default_status=500, default_body={})
        source = RabbitMqSource(
            {"base_url": "https://mq.example.com"}, http_get=transport
        )
        result = source.health()
        assert result["ok"] is False

    def test_health_false_on_transport_error(self):
        from general_ludd.connectors.rabbitmq import RabbitMqSource

        def failing_transport(*args: object, **kwargs: object) -> tuple[int, object]:
            raise ConnectionError("boom")

        source = RabbitMqSource(
            {"base_url": "https://mq.example.com"}, http_get=failing_transport
        )
        result = source.health()
        assert result["ok"] is False

    def test_query_returns_queue_metrics(self):
        from general_ludd.connectors.rabbitmq import RabbitMqSource

        transport = _make_http_transport(
            default_status=200,
            default_body=[],
            responses={
                "/api/queues": (
                    200,
                    [{"name": "tasks", "vhost": "/", "messages": 42,
                      "messages_ready": 40, "messages_unacknowledged": 2,
                      "memory": 1048576}],
                ),
                "/api/overview": (
                    200,
                    {"queue_totals": {"messages": 42, "messages_unacknowledged": 2},
                     "object_totals": {"connections": 10}},
                ),
                "/api/nodes": (
                    200,
                    [{"name": "rabbit@node1", "mem_used": 50000000,
                      "fd_used": 64, "sockets_used": 4, "running": True}],
                ),
            },
        )
        source = RabbitMqSource(
            {"name": "rmq", "base_url": "https://mq.example.com"}, http_get=transport
        )
        records = source.query({})
        assert len(records) >= 5

    def test_query_filters_endpoints(self):
        from general_ludd.connectors.rabbitmq import RabbitMqSource

        transport = _make_http_transport(
            default_status=200,
            default_body=[],
            responses={
                "/api/queues": (
                    200,
                    [{"name": "q1", "vhost": "/", "messages": 10,
                      "messages_ready": 5, "messages_unacknowledged": 5,
                      "memory": 500000}],
                ),
            },
        )
        source = RabbitMqSource(
            {"base_url": "https://mq.example.com"}, http_get=transport
        )
        records = source.query({"endpoints": ("queues",)})
        assert len(records) >= 1
        for r in records:
            assert "queue" in str(r.get("labels", {}))

    def test_query_error_resilience(self):
        from general_ludd.connectors.rabbitmq import RabbitMqSource

        def transport(url: str, **kwargs: object) -> tuple[int, object]:
            raise OSError("network down")

        source = RabbitMqSource(
            {"base_url": "https://mq.example.com"}, http_get=transport
        )
        records = source.query({})
        assert len(records) >= 1
        assert records[0]["level_or_status"] == "error"

    def test_basic_auth_headers(self, monkeypatch: pytest.MonkeyPatch):
        from general_ludd.connectors.rabbitmq import RabbitMqSource

        monkeypatch.setenv("RMQ_USER", "admin")
        monkeypatch.setenv("RMQ_PASS", "secret123")
        transport = _make_http_transport(
            default_status=200,
            default_body={"rabbitmq_version": "3.12.0"},
        )
        source = RabbitMqSource(
            {"base_url": "https://mq.example.com", "user_env": "RMQ_USER",
             "password_env": "RMQ_PASS"},  # pragma: allowlist secret
            http_get=transport,
        )
        source.health()
        headers = transport.calls[0]["headers"]
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Basic ")


# ============================================================================
# 2. NATS Connector
# ============================================================================


class TestNatsConnector:
    def test_constructs_with_valid_config(self):
        from general_ludd.connectors.nats import NatsSource

        transport = _make_http_transport()
        source = NatsSource(
            {"name": "nats-prod", "base_url": "https://nats.example.com"},
            http_get=transport,
        )
        assert source.name == "nats-prod"
        assert source.KIND == "metrics"

    def test_rejects_loopback(self):
        from general_ludd.connectors.nats import NatsSource

        with pytest.raises(ValueError, match="blocked"):
            NatsSource({"base_url": "http://127.0.0.1"}, http_get=_make_http_transport())

    def test_appends_default_monitor_port(self):
        from general_ludd.connectors.nats import NatsSource

        transport = _make_http_transport()
        source = NatsSource(
            {"base_url": "https://nats.example.com"}, http_get=transport
        )
        source.health()
        url = transport.calls[0]["url"]
        assert ":8222" in url

    def test_health_ok(self):
        from general_ludd.connectors.nats import NatsSource

        transport = _make_http_transport(
            default_status=200, default_body={"version": "2.10.0", "server_id": "abc"}
        )
        source = NatsSource(
            {"base_url": "https://nats.example.com"}, http_get=transport
        )
        result = source.health()
        assert result["ok"] is True

    def test_health_false_on_transport_error(self):
        from general_ludd.connectors.nats import NatsSource

        def transport(*args: object, **kwargs: object) -> tuple[int, object]:
            raise RuntimeError("broken")

        source = NatsSource(
            {"base_url": "https://nats.example.com"}, http_get=transport
        )
        result = source.health()
        assert result["ok"] is False

    def test_query_varz_normalizes_metrics(self):
        from general_ludd.connectors.nats import NatsSource

        transport = _make_http_transport(
            default_status=200,
            default_body={},
            responses={
                "/varz": (
                    200,
                    {"server_id": "s1", "connections": 10, "total_connections": 500,
                     "in_msgs": 1000, "out_msgs": 2000, "in_bytes": 50000,
                     "out_bytes": 100000, "slow_consumers": 2, "subscriptions": 50,
                     "mem": 32000000},
                ),
            },
        )
        source = NatsSource(
            {"base_url": "https://nats.example.com"}, http_get=transport
        )
        records = source.query({"endpoints": ("varz",)})
        metrics = [r for r in records if r["level_or_status"] != "error"]
        assert len(metrics) >= 5

    def test_query_connz_normalizes(self):
        from general_ludd.connectors.nats import NatsSource

        transport = _make_http_transport(
            default_status=200,
            default_body={},
            responses={
                "/connz": (
                    200,
                    {"server_id": "s1", "num_connections": 25, "total": 300},
                ),
            },
        )
        source = NatsSource(
            {"base_url": "https://nats.example.com"}, http_get=transport
        )
        records = source.query({"endpoints": ("connz",)})
        assert len([r for r in records if r["level_or_status"] != "error"]) >= 2

    def test_query_subsz_normalizes(self):
        from general_ludd.connectors.nats import NatsSource

        transport = _make_http_transport(
            default_status=200,
            default_body={},
            responses={
                "/subsz": (
                    200,
                    {"server_id": "s1", "num_subscriptions": 42,
                     "num_inserts": 100, "num_matches": 99},
                ),
            },
        )
        source = NatsSource(
            {"base_url": "https://nats.example.com"}, http_get=transport
        )
        records = source.query({"endpoints": ("subsz",)})
        assert len([r for r in records if r["level_or_status"] != "error"]) >= 3

    def test_query_errors_500(self):
        from general_ludd.connectors.nats import NatsSource

        transport = _make_http_transport(default_status=500, default_body={})
        source = NatsSource(
            {"base_url": "https://nats.example.com"}, http_get=transport
        )
        records = source.query({})
        assert len(records) >= 1
        assert all(r["level_or_status"] == "error" for r in records)


# ============================================================================
# 3. PostgreSQL Stats Connector
# ============================================================================


class TestPostgresStatsConnector:
    def test_constructs_with_no_config(self):
        from general_ludd.connectors.postgres_stats import PostgresStatsSource

        source = PostgresStatsSource()
        assert source.name == "postgres_stats"
        assert source.KIND == "metrics"

    def test_constructs_with_config(self):
        from general_ludd.connectors.postgres_stats import PostgresStatsSource

        source = PostgresStatsSource({"dsn_env": "PG_DSN"})
        assert source.config["dsn_env"] == "PG_DSN"

    def test_health_ok_with_injected_executor(self):
        from general_ludd.connectors.postgres_stats import PostgresStatsSource

        def executor(query: str) -> list[dict[str, object]]:
            return [{"xact_commit": 100}]

        source = PostgresStatsSource(executor=executor)
        result = source.health()
        assert result["ok"] is True

    def test_health_false_when_executor_fails(self):
        from general_ludd.connectors.postgres_stats import PostgresStatsSource

        def executor(query: str) -> list[dict[str, object]]:
            raise RuntimeError("db down")

        source = PostgresStatsSource(executor=executor)
        result = source.health()
        assert result["ok"] is False

    def test_health_false_when_config_missing_dsn(self, monkeypatch: pytest.MonkeyPatch):
        from general_ludd.connectors.postgres_stats import PostgresStatsSource

        monkeypatch.delenv("PG_DSN", raising=False)
        source = PostgresStatsSource({"dsn_env": "PG_DSN"})
        result = source.health()
        assert result["ok"] is False

    def test_query_activity_normalizes(self):
        from general_ludd.connectors.postgres_stats import PostgresStatsSource

        def executor(query: str) -> list[dict[str, object]]:
            return [
                {"state": "active", "value": 5, "datname": "mydb"},
                {"state": "idle", "value": 15, "datname": "mydb"},
            ]

        source = PostgresStatsSource(executor=executor)
        records = source.query("activity")
        assert len(records) == 2
        for r in records:
            assert r["kind"] == "metrics"
            assert isinstance(r["labels"], dict)

    def test_query_replication_normalizes(self):
        from general_ludd.connectors.postgres_stats import PostgresStatsSource

        def executor(query: str) -> list[dict[str, object]]:
            return [
                {"application_name": "replica1", "value": 0.5},
                {"application_name": "replica2", "value": 1.2},
            ]

        source = PostgresStatsSource(executor=executor)
        records = source.query("replication")
        assert len(records) == 2

    def test_query_database_normalizes(self):
        from general_ludd.connectors.postgres_stats import PostgresStatsSource

        def executor(query: str) -> list[dict[str, object]]:
            return [
                {"datname": "mydb", "xact_commit": 1000, "xact_rollback": 10,
                 "blks_read": 500, "blks_hit": 10000},
            ]

        source = PostgresStatsSource(executor=executor)
        records = source.query("database")
        assert len(records) == 4

    def test_query_statements_normalizes(self):
        from general_ludd.connectors.postgres_stats import PostgresStatsSource

        def executor(query: str) -> list[dict[str, object]]:
            return [
                {"query_id": 12345, "value": 2.5, "calls": 100},
            ]

        source = PostgresStatsSource(executor=executor)
        records = source.query("statements")
        assert len(records) == 1

    def test_query_unknown_spec_raises(self):
        from general_ludd.connectors.postgres_stats import PostgresStatsSource

        source = PostgresStatsSource()
        with pytest.raises(ValueError, match="unknown spec"):
            source.query("bogus")

    def test_credential_resolution(self, monkeypatch: pytest.MonkeyPatch):
        from general_ludd.connectors.postgres_stats import PostgresStatsSource

        monkeypatch.setenv("MY_PG_DSN", "postgresql://...")
        source = PostgresStatsSource({"dsn_env": "MY_PG_DSN"})
        resolved = source._resolve_secret("dsn_env")
        assert resolved == "postgresql://..."


# ============================================================================
# 4. Redis Stats Connector
# ============================================================================


class TestRedisStatsConnector:
    def test_constructs_with_no_config(self):
        from general_ludd.connectors.redis_stats import RedisStatsSource

        source = RedisStatsSource()
        assert source.name == "redis_stats"
        assert source.KIND == "metrics"

    def test_health_ok_with_injected_executor(self):
        from general_ludd.connectors.redis_stats import RedisStatsSource

        def executor(command: str) -> object:
            return True

        source = RedisStatsSource(executor=executor)
        result = source.health()
        assert result["ok"] is True

    def test_health_false_when_executor_fails(self):
        from general_ludd.connectors.redis_stats import RedisStatsSource

        def executor(command: str) -> object:
            raise RuntimeError("redis down")

        source = RedisStatsSource(executor=executor)
        result = source.health()
        assert result["ok"] is False

    def test_query_info_normalizes_numeric_fields(self):
        from general_ludd.connectors.redis_stats import RedisStatsSource

        def executor(command: str) -> object:
            if command == "INFO":
                return {
                    "used_memory": 1048576,
                    "connected_clients": 10,
                    "total_connections_received": 500,
                    "instantaneous_ops_per_sec": 100,
                    "rdb_last_save_time": 1712345678,
                    "redis_version": "7.0.0",
                }
            return []

        source = RedisStatsSource(executor=executor)
        records = source.query("info")
        numeric = [r for r in records if r["value"] is not None]
        assert len(numeric) >= 4

    def test_query_info_skips_non_numeric(self):
        from general_ludd.connectors.redis_stats import RedisStatsSource

        def executor(command: str) -> object:
            if command == "INFO":
                return {
                    "redis_version": "7.2.0",
                    "redis_mode": "standalone",
                    "os": "Linux",
                }
            return []

        source = RedisStatsSource(executor=executor)
        records = source.query("info")
        assert len(records) == 0

    def test_query_slowlog_normalizes(self):
        from general_ludd.connectors.redis_stats import RedisStatsSource

        def executor(command: str) -> object:
            if command == "SLOWLOG GET":
                return [
                    {"id": 1, "start_time": 1000, "duration": 5000,
                     "command": ["GET", "mykey"]},
                ]
            return []

        source = RedisStatsSource(executor=executor)
        records = source.query("slowlog")
        assert len(records) == 1
        assert records[0]["labels"].get("section") == "slowlog"
        assert "GET mykey" in str(records[0]["labels"].get("command"))

    def test_query_unknown_spec_raises(self):
        from general_ludd.connectors.redis_stats import RedisStatsSource

        source = RedisStatsSource()
        with pytest.raises(ValueError, match="unknown spec"):
            source.query("bogus")

    def test_section_classification(self):
        from general_ludd.connectors.redis_stats import _section_for_field

        assert _section_for_field("used_memory_rss") == "memory"
        assert _section_for_field("rdb_changes_since_last_save") == "persistence"
        assert _section_for_field("connected_clients") == "clients"
        assert _section_for_field("total_connections_received") == "stats"
        assert _section_for_field("evicted_keys") == "stats"
        assert _section_for_field("master_repl_offset") == "replication"
        assert _section_for_field("redis_version") == "server"


# ============================================================================
# 5. Kafka Exporter Connector
# ============================================================================


class TestKafkaExporterConnector:
    def test_constructs_with_valid_config(self):
        from general_ludd.connectors.kafka_exporter import KafkaExporterSource

        transport = _make_http_transport(default_status=200, default_body="")
        source = KafkaExporterSource(
            {"name": "kafka-metrics", "base_url": "https://kafka.example.com"},
            http_get=transport,
        )
        assert source.name == "kafka-metrics"
        assert source.KIND == "metrics"

    def test_rejects_internal_host(self):
        from general_ludd.connectors.kafka_exporter import KafkaExporterSource

        with pytest.raises(ValueError, match="blocked"):
            KafkaExporterSource(
                {"base_url": "http://10.0.0.1:9308"},
                http_get=_make_http_transport(),
            )

    def test_health_ok_on_2xx_text(self):
        from general_ludd.connectors.kafka_exporter import KafkaExporterSource

        transport = _make_http_transport(default_status=200, default_body="some text")
        source = KafkaExporterSource(
            {"base_url": "https://kafka.example.com"}, http_get=transport
        )
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_on_500(self):
        from general_ludd.connectors.kafka_exporter import KafkaExporterSource

        transport = _make_http_transport(default_status=500, default_body="")
        source = KafkaExporterSource(
            {"base_url": "https://kafka.example.com"}, http_get=transport
        )
        result = source.health()
        assert result["ok"] is False

    def test_query_parses_prometheus_exposition(self):
        from general_ludd.connectors.kafka_exporter import KafkaExporterSource

        body = (
            "# HELP kafka_consumergroup_lag Consumer group lag\n"
            "kafka_consumergroup_lag{consumergroup=\"cg1\",topic=\"t1\",partition=\"0\"} 5.0 1712345678\n"
            "# HELP go_gc_duration garbage collection\n"
            "go_gc_duration_seconds 0.001\n"
        )
        transport = _make_http_transport(default_status=200, default_body=body)
        source = KafkaExporterSource(
            {"base_url": "https://kafka.example.com"}, http_get=transport
        )
        records = source.query({})
        assert len(records) == 1
        assert records[0]["value"] == 5.0
        assert records[0]["labels"]["consumergroup"] == "cg1"

    def test_query_filters_wanted_metrics_only(self):
        from general_ludd.connectors.kafka_exporter import KafkaExporterSource

        body = (
            "kafka_brokers 3\n"
            "process_cpu_seconds_total 123.0\n"
        )
        transport = _make_http_transport(default_status=200, default_body=body)
        source = KafkaExporterSource(
            {"base_url": "https://kafka.example.com"}, http_get=transport
        )
        records = source.query({})
        kafka_metrics = [r for r in records if r["level_or_status"] != "error"]
        assert all(r["message"].startswith("kafka_") for r in kafka_metrics)

    def test_query_custom_metrics_allowlist(self):
        from general_ludd.connectors.kafka_exporter import KafkaExporterSource

        body = (
            "kafka_brokers 3\n"
            "kafka_consumergroup_lag{topic=\"t1\"} 10\n"
        )
        transport = _make_http_transport(default_status=200, default_body=body)
        source = KafkaExporterSource(
            {"base_url": "https://kafka.example.com"}, http_get=transport
        )
        records = source.query({"metrics": ("kafka_brokers",)})
        data = [r for r in records if r["level_or_status"] != "error"]
        assert len(data) == 1
        assert data[0]["message"] == "kafka_brokers"

    def test_query_error_on_transport_failure(self):
        from general_ludd.connectors.kafka_exporter import KafkaExporterSource

        def transport(*args: object, **kwargs: object) -> tuple[int, str]:
            raise OSError("timeout")

        source = KafkaExporterSource(
            {"base_url": "https://kafka.example.com"}, http_get=transport
        )
        records = source.query({})
        assert len(records) == 1
        assert records[0]["level_or_status"] == "error"

    def test_parse_label_block_simple(self):
        from general_ludd.connectors.kafka_exporter import _parse_label_block

        labels = _parse_label_block('k="v"')
        assert labels == {"k": "v"}

    def test_parse_label_block_multi(self):
        from general_ludd.connectors.kafka_exporter import _parse_label_block

        labels = _parse_label_block('consumergroup="cg",topic="t"')
        assert labels == {"consumergroup": "cg", "topic": "t"}

    def test_parse_label_block_escaped_quotes(self):
        from general_ludd.connectors.kafka_exporter import _parse_label_block

        labels = _parse_label_block(r'msg="hello \"world\""')
        assert labels == {"msg": 'hello "world"'}

    def test_parse_metric_line_with_labels(self):
        from general_ludd.connectors.kafka_exporter import _parse_metric_line

        result = _parse_metric_line('kafka_lag{consumergroup="cg1"} 5.0')
        assert result is not None
        name, labels, value = result
        assert name == "kafka_lag"
        assert labels == {"consumergroup": "cg1"}
        assert value == 5.0

    def test_parse_metric_line_no_labels(self):
        from general_ludd.connectors.kafka_exporter import _parse_metric_line

        result = _parse_metric_line("up 1")
        assert result is not None
        name, labels, value = result
        assert name == "up"
        assert labels == {}
        assert value == 1.0

    def test_parse_metric_line_comment_returns_none(self):
        from general_ludd.connectors.kafka_exporter import _parse_metric_line

        assert _parse_metric_line("# HELP up Whether up") is None


# ============================================================================
# 6. Prometheus Connector
# ============================================================================


class TestPrometheusConnector:
    def test_constructs_with_valid_config(self):
        from general_ludd.connectors.prometheus import PrometheusSource

        transport = _make_prom_transport()
        source = PrometheusSource(
            {"name": "prom-prod", "base_url": "https://prom.example.com"},
            http_get=transport,
        )
        assert source.name == "prom-prod"
        assert source.KIND == "metrics"

    def test_rejects_internal_address(self):
        from general_ludd.connectors.prometheus import PrometheusSource

        with pytest.raises(ValueError, match="SSRF"):
            PrometheusSource(
                {"base_url": "http://10.0.0.1:9090"}, http_get=_make_prom_transport()
            )

    def test_health_ok_on_successful_query(self):
        from general_ludd.connectors.prometheus import PrometheusSource

        transport = _make_prom_transport(
            status=200, body={"status": "success", "data": {"resultType": "vector", "result": []}}
        )
        source = PrometheusSource(
            {"base_url": "https://prom.example.com"}, http_get=transport
        )
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok_on_error_status(self):
        from general_ludd.connectors.prometheus import PrometheusSource

        transport = _make_prom_transport(
            status=200, body={"status": "error", "error": "parse error"}
        )
        source = PrometheusSource(
            {"base_url": "https://prom.example.com"}, http_get=transport
        )
        result = source.health()
        assert result["ok"] is False

    def test_query_vector_result(self):
        from general_ludd.connectors.prometheus import PrometheusSource

        transport = _make_prom_transport(
            status=200,
            body={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {"metric": {"__name__": "up", "job": "node"},
                         "value": [1712345678, "1"]}
                    ],
                },
            },
        )
        source = PrometheusSource(
            {"name": "prom", "base_url": "https://prom.example.com"},
            http_get=transport,
        )
        records = source.query({"promql": "up"})
        assert len(records) == 1
        assert records[0]["value"] == 1.0

    def test_query_matrix_result(self):
        from general_ludd.connectors.prometheus import PrometheusSource

        transport = _make_prom_transport(
            status=200,
            body={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {"metric": {"__name__": "cpu"},
                         "values": [[1712345600, "0.5"], [1712345601, "0.6"]]}
                    ],
                },
            },
        )
        source = PrometheusSource(
            {"base_url": "https://prom.example.com"}, http_get=transport
        )
        records = source.query({"promql": "cpu", "start": 0, "end": 1, "step": "1s"})
        assert len(records) == 2

    def test_query_scalar_result(self):
        from general_ludd.connectors.prometheus import PrometheusSource

        transport = _make_prom_transport(
            status=200,
            body={
                "status": "success",
                "data": {"resultType": "scalar", "result": [1712345678, "42"]},
            },
        )
        source = PrometheusSource(
            {"base_url": "https://prom.example.com"}, http_get=transport
        )
        records = source.query({"promql": "count(up)"})
        assert len(records) == 1

    def test_query_missing_promql(self):
        from general_ludd.connectors.prometheus import PrometheusSource

        source = PrometheusSource(
            {"base_url": "https://prom.example.com"}, http_get=_make_prom_transport()
        )
        records = source.query({})  # type: ignore[arg-type]
        assert len(records) == 1
        assert records[0]["level_or_status"] == "error"

    def test_query_transport_error(self):
        from general_ludd.connectors.prometheus import PrometheusSource

        def transport(*args: object, **kwargs: object) -> tuple[int, object]:
            raise OSError("refused")

        source = PrometheusSource(
            {"base_url": "https://prom.example.com"}, http_get=transport
        )
        records = source.query({"promql": "up"})
        assert len(records) == 1
        assert records[0]["level_or_status"] == "error"

    def test_query_truncates_at_max_result_size(self):
        from general_ludd.connectors.prometheus import MAX_RESULT_SIZE, PrometheusSource

        transport = _make_prom_transport(
            status=200,
            body={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {"metric": {"__name__": "cpu"},
                         "values": [[float(i), "0.5"] for i in range(MAX_RESULT_SIZE + 100)]}
                    ],
                },
            },
        )
        source = PrometheusSource(
            {"base_url": "https://prom.example.com"}, http_get=transport
        )
        records = source.query({"promql": "cpu", "start": 0, "end": 1800, "step": "1s"})
        assert records[-1]["level_or_status"] == "error"


# ============================================================================
# 7. Prom Scrape (Generic /metrics Scraper)
# ============================================================================


class TestPromScrapeConnector:
    """Generic Prometheus text exposition scraper."""

    def test_constructs_with_valid_config(self):
        from general_ludd.connectors.prom_scrape import PromScrapeSource

        # We need a minimal transport that exposes .status_code and .text
        class MiniTransport:
            def get(self, url: str, *, headers: dict[str, str] | None = None,
                    timeout: float | None = None) -> object:
                return _MockResponse(200, "cpu_seconds_total 1.0\n")

        source = PromScrapeSource(
            {"name": "node-exporter", "base_url": "http://10.0.0.1:9100",
             "allow_private": True},
            transport=MiniTransport(),
        )
        assert source.name == "node-exporter"
        assert source.KIND == "metrics"

    def test_rejects_private_without_opt_in(self):
        from general_ludd.connectors.prom_scrape import PromScrapeSource

        with pytest.raises(ValueError, match="refusing private"):
            PromScrapeSource({"base_url": "http://192.168.1.1:9100"})

    def test_allows_private_with_opt_in(self):
        from general_ludd.connectors.prom_scrape import PromScrapeSource

        class QuietTransport:
            def get(
                self,
                url: str,
                *,
                headers: dict[str, str] | None = None,
                timeout: float | None = None,
            ) -> object:
                return _MockResponse(200, "up 1\n")

        source = PromScrapeSource(
            {"base_url": "http://10.0.0.1:9100", "allow_private": True},
            transport=QuietTransport(),
        )
        assert source._allow_private is True

    def test_health_ok(self):
        from general_ludd.connectors.prom_scrape import PromScrapeSource

        class OkTransport:
            def get(
                self,
                url: str,
                *,
                headers: dict[str, str] | None = None,
                timeout: float | None = None,
            ) -> object:
                return _MockResponse(200, "up 1\n")

        source = PromScrapeSource(
            {"base_url": "https://prom.example.com"}, transport=OkTransport()
        )
        result = source.health()
        assert result["ok"] is True

    def test_health_not_ok(self):
        from general_ludd.connectors.prom_scrape import PromScrapeSource

        class ErrTransport:
            def get(
                self,
                url: str,
                *,
                headers: dict[str, str] | None = None,
                timeout: float | None = None,
            ) -> object:
                return _MockResponse(500, "")

        source = PromScrapeSource(
            {"base_url": "https://prom.example.com"}, transport=ErrTransport()
        )
        result = source.health()
        assert result["ok"] is False

    def test_query_normalizes_samples(self):
        from general_ludd.connectors.prom_scrape import PromScrapeSource

        class MetricsTransport:
            def get(
                self,
                url: str,
                *,
                headers: dict[str, str] | None = None,
                timeout: float | None = None,
            ) -> object:
                return _MockResponse(
                    200,
                    "# HELP up Whether up\n"
                    "up{instance=\"host1\"} 1\n"
                    "cpu_seconds_total{instance=\"host1\"} 123.45 1712345678901\n"
                    "# TYPE cpu_seconds_total counter\n",
                )

        source = PromScrapeSource(
            {"base_url": "https://prom.example.com"}, transport=MetricsTransport()
        )
        records = source.query()
        assert len(records) == 2
        for r in records:
            assert r["kind"] == "metrics"

    def test_query_filters_by_prefix(self):
        from general_ludd.connectors.prom_scrape import PromScrapeSource

        class MetricsTransport:
            def get(
                self,
                url: str,
                *,
                headers: dict[str, str] | None = None,
                timeout: float | None = None,
            ) -> object:
                return _MockResponse(
                    200,
                    "node_cpu_seconds_total 100\n"
                    "node_memory_bytes 50000\n"
                    "prometheus_http_requests_total 10\n",
                )

        source = PromScrapeSource(
            {"base_url": "https://prom.example.com"}, transport=MetricsTransport()
        )
        records = source.query({"metric_prefix": "node_cpu"})
        assert len(records) == 1
        assert records[0]["message"] == "node_cpu_seconds_total"


class _MockResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


# ============================================================================
# 8. Local Files Connectors
# ============================================================================


class TestJsonlLogSource:
    def test_constructs_with_single_path(self):
        from general_ludd.connectors.local_files import JsonlLogSource

        with tempfile.TemporaryDirectory() as tmpdir:
            logfile = os.path.join(tmpdir, "app.log")
            with open(logfile, "w") as f:
                f.write('{"ts":"2025-01-01T00:00:00","level":"info","message":"hello"}\n')
            source = JsonlLogSource({"path": logfile, "root": tmpdir})
            assert source.name == "jsonl_log_source"
            assert source.KIND == "logs"

    def test_rejects_path_outside_root(self):
        from general_ludd.connectors.local_files import JsonlLogSource

        with tempfile.TemporaryDirectory() as tmpdir:
            outside = "/etc/passwd"
            with pytest.raises(ValueError, match="outside"):
                JsonlLogSource({"path": outside, "root": tmpdir})

    def test_health_reports_files(self):
        from general_ludd.connectors.local_files import JsonlLogSource

        with tempfile.TemporaryDirectory() as tmpdir:
            logfile = os.path.join(tmpdir, "app.log")
            with open(logfile, "w") as f:
                f.write('{"ts":"1","message":"ok"}\n')
            source = JsonlLogSource({"path": logfile, "root": tmpdir})
            result = source.health()
            assert result["healthy"] is True

    def test_query_reads_and_normalizes(self):
        from general_ludd.connectors.local_files import JsonlLogSource

        with tempfile.TemporaryDirectory() as tmpdir:
            logfile = os.path.join(tmpdir, "app.log")
            with open(logfile, "w") as f:
                f.write(
                    '{"ts":"2025-01-01T00:00:00","level":"info","message":"start"}\n'
                    '{"ts":"2025-01-01T00:01:00","level":"error","message":"fail","value":42}\n'
                )
            source = JsonlLogSource({"path": logfile, "root": tmpdir})
            records = source.query({})
            assert len(records) == 2
            assert records[0]["ts"] == "2025-01-01T00:00:00"
            assert records[0]["level_or_status"] == "info"
            assert records[1]["message"] == "fail"
            assert records[1]["value"] == 42.0

    def test_query_filters_by_level(self):
        from general_ludd.connectors.local_files import JsonlLogSource

        with tempfile.TemporaryDirectory() as tmpdir:
            logfile = os.path.join(tmpdir, "app.log")
            with open(logfile, "w") as f:
                f.write(
                    '{"level":"info","message":"a"}\n'
                    '{"level":"error","message":"b"}\n'
                    '{"level":"warn","message":"c"}\n'
                )
            source = JsonlLogSource({"path": logfile, "root": tmpdir})
            records = source.query({"level": "error"})
            assert len(records) == 1
            assert records[0]["message"] == "b"

    def test_query_filters_by_pattern(self):
        from general_ludd.connectors.local_files import JsonlLogSource

        with tempfile.TemporaryDirectory() as tmpdir:
            logfile = os.path.join(tmpdir, "app.log")
            with open(logfile, "w") as f:
                f.write(
                    '{"message":"user login"}\n'
                    '{"message":"database error"}\n'
                    '{"message":"user logout"}\n'
                )
            source = JsonlLogSource({"path": logfile, "root": tmpdir})
            records = source.query({"pattern": "user"})
            assert len(records) == 2

    def test_query_respects_limit(self):
        from general_ludd.connectors.local_files import JsonlLogSource

        with tempfile.TemporaryDirectory() as tmpdir:
            logfile = os.path.join(tmpdir, "app.log")
            with open(logfile, "w") as f:
                for i in range(10):
                    f.write(f'{{"message":"msg{i}"}}\n')
            source = JsonlLogSource({"path": logfile, "root": tmpdir})
            records = source.query({"limit": 3})
            assert len(records) == 3

    def test_query_handles_malformed_lines(self):
        from general_ludd.connectors.local_files import JsonlLogSource

        with tempfile.TemporaryDirectory() as tmpdir:
            logfile = os.path.join(tmpdir, "app.log")
            with open(logfile, "w") as f:
                f.write('not json\n{"message": "valid"}\n')
            source = JsonlLogSource({"path": logfile, "root": tmpdir})
            records = source.query({})
            assert len(records) == 1
            assert source.last_malformed_count == 1


class TestSyslogGrepSource:
    def test_constructs_with_valid_path(self):
        from general_ludd.connectors.local_files import SyslogGrepSource

        with tempfile.TemporaryDirectory() as tmpdir:
            syslog = os.path.join(tmpdir, "syslog")
            with open(syslog, "w") as f:
                f.write("Jun 15 10:00:00 myhost sshd[1234]: accepted\n")
            source = SyslogGrepSource({"path": syslog, "root": tmpdir})
            assert source.name == "syslog_grep_source"
            assert source.KIND == "logs"

    def test_parses_rfc3164_line(self):
        from general_ludd.connectors.local_files import SyslogGrepSource

        with tempfile.TemporaryDirectory() as tmpdir:
            syslog = os.path.join(tmpdir, "syslog")
            with open(syslog, "w") as f:
                f.write("Jun 15 10:00:00 myhost sshd[1234]: accepted publickey\n")
            source = SyslogGrepSource({"path": syslog, "root": tmpdir})
            records = source.query({"pattern": "publickey"})
            assert len(records) == 1
            assert records[0]["labels"]["host"] == "myhost"
            assert records[0]["labels"]["process"] == "sshd"
            assert records[0]["labels"]["pid"] == "1234"
            assert "accepted publickey" in records[0]["message"]

    def test_query_with_limit(self):
        from general_ludd.connectors.local_files import SyslogGrepSource

        with tempfile.TemporaryDirectory() as tmpdir:
            syslog = os.path.join(tmpdir, "syslog")
            with open(syslog, "w") as f:
                for i in range(5):
                    f.write(f"Jun 15 10:00:0{i} host{i} app[{i}]: message {i}\n")
            source = SyslogGrepSource({"path": syslog, "root": tmpdir})
            records = source.query({"limit": 2})
            assert len(records) == 2

    def test_file_not_found_returns_empty(self):
        from general_ludd.connectors.local_files import SyslogGrepSource

        with tempfile.TemporaryDirectory() as tmpdir:
            source = SyslogGrepSource(
                {"path": os.path.join(tmpdir, "nonexistent"), "root": tmpdir}
            )
            records = source.query({})
            assert records == []

    def test_parse_ts_returns_iso_format(self):
        from general_ludd.connectors.local_files import SyslogGrepSource

        with tempfile.TemporaryDirectory() as tmpdir:
            syslog = os.path.join(tmpdir, "syslog")
            with open(syslog, "w") as f:
                f.write("Jan 01 00:00:00 host app: start\n")
            source = SyslogGrepSource({"path": syslog, "root": tmpdir})
            records = source.query({"pattern": "start"})
            assert len(records) == 1
            ts = records[0]["ts"]
            assert "T" in ts


# ============================================================================
# 9. Grafana Loki Connector
# ============================================================================


class TestGrafanaLokiConnector:
    def test_constructs_with_valid_config(self):
        from general_ludd.connectors.grafana_loki import GrafanaLokiSource

        class MiniTransport:
            def request(self, method: str, url: str, *,
                        headers: dict[str, str] | None = ...,
                        json: object = ..., params: dict[str, object] | None = ...,
                        timeout: float | None = ...) -> tuple[int, object]:
                return 200, {"status": "success"}

        source = GrafanaLokiSource(
            {"base_url": "https://loki.example.com"}, transport=MiniTransport()
        )
        assert source.name == "grafana_loki"
        assert source.KIND == "logs"

    def test_rejects_loopback(self):
        from general_ludd.connectors.grafana_loki import GrafanaLokiSource

        class MiniTransport:
            def request(self, **kwargs: object) -> tuple[int, object]:
                return 200, {}

        with pytest.raises(ValueError, match="refusing"):
            GrafanaLokiSource(
                {"base_url": "http://127.0.0.1:3100"},
                transport=MiniTransport(),
            )


# ============================================================================
# 10. _util — validate_base_url and parse_timestamp
# ============================================================================


class TestConnectorUtil:
    def test_validate_base_url_strips_trailing_slash(self):
        from general_ludd.connectors._util import validate_base_url

        result = validate_base_url("https://api.example.com/")
        assert result == "https://api.example.com"

    def test_validate_base_url_rejects_internal(self):
        from general_ludd.connectors._util import validate_base_url

        with pytest.raises(ValueError, match="blocked"):
            validate_base_url("http://169.254.169.254/")

    def test_parse_timestamp_rfc3339(self):
        from general_ludd.connectors._util import parse_timestamp

        ts = parse_timestamp("2025-01-01T00:00:00Z")
        assert ts is not None
        assert ts > 0

    def test_parse_timestamp_rfc3339_offset(self):
        from general_ludd.connectors._util import parse_timestamp

        ts = parse_timestamp("2025-01-01T00:00:00+00:00")
        assert ts is not None

    def test_parse_timestamp_iso_no_tz(self):
        from general_ludd.connectors._util import parse_timestamp

        ts = parse_timestamp("2025-01-01T00:00:00")
        assert ts is not None

    def test_parse_timestamp_bogus_string(self):
        from general_ludd.connectors._util import parse_timestamp

        assert parse_timestamp("not a date") is None

    def test_parse_timestamp_none(self):
        from general_ludd.connectors._util import parse_timestamp

        assert parse_timestamp(None) is None
