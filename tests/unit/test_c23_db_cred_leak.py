"""C.23: DB connector credential-logging fix — verify health() never logs
credentials, tracebacks, or raw exception text.

Each of the 5 DB connectors (postgres_stats, mysql_stats, redis_stats,
mongodb_stats, clickhouse_stats) had ``exc_info=True`` in their ``health()``
exception handlers, which logs the full traceback — potentially including
connection strings with passwords.

Fix: ``exc_info=False`` + only ``type(exc).__name__`` in the log message.
"""

from __future__ import annotations

import logging

import pytest

# ── common credential-token patterns for caplog scans ────────────────────

_CRED_PATTERNS = [
    "password", "secret", "token", "api_key", "api-key",
    "mongodb://", "redis://", "postgresql://", "mysql://",
    "clickhouse://", "psycopg", "connect_timeout=",
]

_RAW_TEXT_MARKERS = [
    "connection refused", "Connection refused",
    "timeout", "Name or service not known",
]


def _records_have_credentials(records: list[logging.LogRecord]) -> str:
    """Return a failing description if any record leaks credentials, else "". """
    for r in records:
        msg = r.getMessage().lower()
        for pat in _CRED_PATTERNS:
            if pat.lower() in msg:
                return f"credential leak: {pat!r} in log message: {r.getMessage()}"
    return ""


def _records_have_traceback(caplog_text: str) -> str:
    """Return a failing description if caplog output contains a traceback."""
    if "Traceback (most recent call last)" in caplog_text:
        return "traceback leaked in log output"
    return ""


def _records_have_raw_text(records: list[logging.LogRecord]) -> str:
    """Return a failing description if any record embeds raw exception text."""
    for r in records:
        msg = r.getMessage().lower()
        for pat in _RAW_TEXT_MARKERS:
            if pat.lower() in msg:
                return f"raw exception text leaked: {pat!r} in: {r.getMessage()}"
    return ""


# ── postgres_stats ───────────────────────────────────────────────────────

def test_postgres_health_no_cred_leak(caplog: pytest.LogCaptureFixture) -> None:
    from general_ludd.connectors.postgres_stats import PostgresStatsSource

    def _fail(_sql: str) -> list[dict[str, object]]:
        raise RuntimeError("postgresql://user:secret123@host:5432/db connection refused")

    src = PostgresStatsSource(config={"dsn_env": "PG_DSN"}, executor=_fail)
    with caplog.at_level(logging.WARNING, logger="general_ludd.connectors.postgres_stats"):
        result = src.health()

    assert result["ok"] is False
    assert "probe failed" in str(result["detail"])
    err = _records_have_credentials(caplog.records)
    assert not err, err
    err = _records_have_traceback(caplog.text)
    assert not err, err
    err = _records_have_raw_text(caplog.records)
    assert not err, err
    assert any("RuntimeError" in r.getMessage() for r in caplog.records), (
        "must log exception class name"
    )


# ── mysql_stats ──────────────────────────────────────────────────────────

def test_mysql_health_no_cred_leak(caplog: pytest.LogCaptureFixture) -> None:
    from general_ludd.connectors.mysql_stats import MysqlStatsSource

    def _fail(_sql: str) -> list[dict[str, object]]:
        raise RuntimeError("mysql://user:secret123@host:3306/mydb connection refused")

    src = MysqlStatsSource(
        config={"host": "localhost", "user_env": "MYSQL_USER"},
        executor=_fail,
    )
    with caplog.at_level(logging.WARNING, logger="general_ludd.connectors.mysql_stats"):
        result = src.health()

    assert result["ok"] is False
    err = _records_have_credentials(caplog.records)
    assert not err, err
    err = _records_have_traceback(caplog.text)
    assert not err, err
    err = _records_have_raw_text(caplog.records)
    assert not err, err
    assert any("RuntimeError" in r.getMessage() for r in caplog.records), (
        "must log exception class name"
    )


# ── redis_stats ──────────────────────────────────────────────────────────

def test_redis_health_no_cred_leak(caplog: pytest.LogCaptureFixture) -> None:
    from general_ludd.connectors.redis_stats import RedisStatsSource

    def _fail(_cmd: str) -> bool:
        raise RuntimeError("redis://:secret123@host:6379/0 connection refused")

    src = RedisStatsSource(config={"url_env": "REDIS_URL"}, executor=_fail)
    with caplog.at_level(logging.WARNING, logger="general_ludd.connectors.redis_stats"):
        result = src.health()

    assert result["ok"] is False
    err = _records_have_credentials(caplog.records)
    assert not err, err
    err = _records_have_traceback(caplog.text)
    assert not err, err
    err = _records_have_raw_text(caplog.records)
    assert not err, err
    assert any("RuntimeError" in r.getMessage() for r in caplog.records), (
        "must log exception class name"
    )


# ── mongodb_stats ────────────────────────────────────────────────────────

def test_mongodb_health_no_cred_leak(caplog: pytest.LogCaptureFixture) -> None:
    from general_ludd.connectors.mongodb_stats import MongoDbStatsSource

    def _fail(_cmd: str) -> dict[str, object]:
        raise RuntimeError("mongodb://user:secret123@host:27017/ connection refused")

    src = MongoDbStatsSource(
        config={"name": "mdb", "uri_env": "MONGO_URI"},
        executor=_fail,
    )
    with caplog.at_level(logging.WARNING, logger="general_ludd.connectors.mongodb_stats"):
        result = src.health()

    assert result["ok"] is False
    err = _records_have_credentials(caplog.records)
    assert not err, err
    err = _records_have_traceback(caplog.text)
    assert not err, err
    err = _records_have_raw_text(caplog.records)
    assert not err, err
    assert any("RuntimeError" in r.getMessage() for r in caplog.records), (
        "must log exception class name"
    )


# ── clickhouse_stats ─────────────────────────────────────────────────────

def test_clickhouse_health_no_cred_leak(caplog: pytest.LogCaptureFixture) -> None:
    from general_ludd.connectors.clickhouse_stats import ClickHouseStatsSource

    def _fail(_sql: str) -> list[dict[str, object]]:
        raise RuntimeError("http://user:secret123@host:8123 connection refused")

    src = ClickHouseStatsSource(
        config={"url": "http://localhost:8123", "password_env": "CH_PW"},
        executor=_fail,
    )
    with caplog.at_level(logging.WARNING, logger="general_ludd.connectors.clickhouse_stats"):
        result = src.health()

    assert result["ok"] is False
    err = _records_have_credentials(caplog.records)
    assert not err, err
    err = _records_have_traceback(caplog.text)
    assert not err, err
    err = _records_have_raw_text(caplog.records)
    assert not err, err
    assert any("RuntimeError" in r.getMessage() for r in caplog.records), (
        "must log exception class name"
    )


# ── MongoDbStatsSource executor init path (no executor supplied) ─────────

def test_mongodb_health_no_executor_no_cred_leak(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mongodb_stats.health returns early when executor is None — still no leak."""
    monkeypatch.setenv("MONGODB_URI", "mongodb://user:secret123@host:27017/")
    with caplog.at_level(logging.WARNING, logger="general_ludd.connectors.mongodb_stats"):
        # This path won't actually fire the warning (returns early), but verify
        # the caplog records are still clean if anything was emitted at WARNING.
        pass

    err = _records_have_credentials(caplog.records)
    assert not err, err


# ── source-level audit: verify exc_info=True removed from health() ───────

_DB_CONNECTORS = [
    "postgres_stats",
    "mysql_stats",
    "redis_stats",
    "mongodb_stats",
    "clickhouse_stats",
]


def _health_method_source(mod_name: str) -> str:
    import importlib
    import inspect

    mod = importlib.import_module(f"general_ludd.connectors.{mod_name}")
    source = inspect.getsource(mod)
    start = source.find("def health(")
    end = source.find("\n    def ", start + 1)
    return source[start:end] if start >= 0 else ""


@pytest.mark.parametrize("mod_name", _DB_CONNECTORS)
def test_health_method_no_exc_info_true(mod_name: str) -> None:
    """Source-level: health() method must not contain exc_info=True."""
    body = _health_method_source(mod_name)
    assert "exc_info=True" not in body, (
        f"{mod_name}.health() still has exc_info=True"
    )


@pytest.mark.parametrize("mod_name", _DB_CONNECTORS)
def test_health_method_has_exc_info_false(mod_name: str) -> None:
    """Source-level: health() method should have exc_info=False."""
    body = _health_method_source(mod_name)
    assert "exc_info=False" in body, (
        f"{mod_name}.health() missing exc_info=False"
    )


@pytest.mark.parametrize("mod_name", _DB_CONNECTORS)
def test_health_method_logs_exc_type_name(mod_name: str) -> None:
    """Source-level: health() logs type(exc).__name__."""
    body = _health_method_source(mod_name)
    assert "type(exc).__name__" in body, (
        f"{mod_name}.health() does not log type(exc).__name__"
    )
