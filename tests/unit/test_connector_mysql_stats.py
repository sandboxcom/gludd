"""Structural TDD unit tests for mysql_stats connector."""

import importlib

import pytest

_MODULE = "general_ludd.connectors.mysql_stats"


# ------------------------------------------------------------------ import
def test_module_imports_without_pymysql() -> None:
    mod = importlib.import_module(_MODULE)
    assert mod is not None


# ------------------------------------------------------------- TypedDicts
@pytest.mark.parametrize(
    "cls_name,expected_fields",
    [
        ("MysqlStatusRow", {"Variable_name", "Value", "variable_name", "value"}),
        (
            "MysqlPerformanceRow",
            {"event_name", "count_star", "sum_timer_wait"},
        ),
        (
            "MysqlReplicaRow",
            {
                "Replica_IO_Running",
                "Slave_IO_Running",
                "Seconds_Behind_Source",
                "Seconds_Behind_Master",
                "Source_Host",
                "Master_Host",
            },
        ),
    ],
)
def test_typeddict_fields(cls_name: str, expected_fields: set[str]) -> None:
    mod = importlib.import_module(_MODULE)
    cls = getattr(mod, cls_name)
    assert issubclass(cls, dict)
    anns = cls.__annotations__
    assert set(anns) >= expected_fields


# ---------------------------------------------------------- SQL constants
@pytest.mark.parametrize(
    "name,expected_substring",
    [
        ("_SQL_GLOBAL_STATUS", "SHOW GLOBAL STATUS"),
        ("_SQL_PERFORMANCE", "performance_schema"),
        ("_SQL_REPLICA", "SHOW REPLICA STATUS"),
    ],
)
def test_sql_constants_exist(name: str, expected_substring: str) -> None:
    mod = importlib.import_module(_MODULE)
    val = getattr(mod, name)
    assert isinstance(val, str)
    assert expected_substring in val


# --------------------------------------------------------- _utc_now_iso
def test_utc_now_iso_returns_string() -> None:
    mod = importlib.import_module(_MODULE)
    result = mod._utc_now_iso()
    assert isinstance(result, str)
    assert result.endswith("+00:00") or "T" in result


# ------------------------------------------------------------- _to_float
@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        (0, 0.0),
        (42, 42.0),
        (-1, -1.0),
        (3.14, 3.14),
        (True, 1.0),
        (False, 0.0),
        ("123", 123.0),
        ("3.14", 3.14),
        ("  42 ", 42.0),
        ("not_a_number", None),
        ([], None),
        ({}, None),
    ],
)
def test_to_float(value: object, expected: float | None) -> None:
    mod = importlib.import_module(_MODULE)
    result = mod._to_float(value)
    assert result == expected


def test_to_float_returns_float_not_bool() -> None:
    mod = importlib.import_module(_MODULE)
    result = mod._to_float(True)
    assert isinstance(result, float)
    assert not isinstance(result, bool)


# ----------------------------------------------- MysqlStatsSource basics
def test_class_has_name_and_kind() -> None:
    mod = importlib.import_module(_MODULE)
    cls = mod.MysqlStatsSource
    assert cls.name == "mysql_stats"
    assert cls.KIND == "metrics"


def test_constructor_defaults() -> None:
    mod = importlib.import_module(_MODULE)
    source = mod.MysqlStatsSource()
    assert source.config == {}
    assert source._executor is None


def test_constructor_accepts_config() -> None:
    mod = importlib.import_module(_MODULE)
    config = {"host_env": "MYSQL_HOST"}
    source = mod.MysqlStatsSource(config=config)
    assert source.config == config
    assert source.config is not config  # copy, not same object


def test_constructor_accepts_executor() -> None:
    mod = importlib.import_module(_MODULE)

    def dummy(query: str) -> list:
        return [{"test": 1}]

    source = mod.MysqlStatsSource(executor=dummy)
    assert source._executor is dummy


def test_constructor_adapts_database_cursor() -> None:
    mod = importlib.import_module(_MODULE)

    class Cursor:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def execute(self, query: str) -> None:
            self.calls.append(query)

        def __iter__(self):
            return iter([{"Variable_name": "Threads_connected", "Value": "4"}])

    cursor = Cursor()
    source = mod.MysqlStatsSource(cursor=cursor)

    records = source.query()

    assert cursor.calls == ["SHOW GLOBAL STATUS"]
    assert records[0]["message"] == "global status Threads_connected"
    assert records[0]["value"] == 4.0


def test_constructor_rejects_executor_and_cursor_together() -> None:
    mod = importlib.import_module(_MODULE)

    with pytest.raises(ValueError, match="executor or cursor"):
        mod.MysqlStatsSource(executor=lambda _: [], cursor=object())


def test_constructor_config_is_mutable_dict() -> None:
    mod = importlib.import_module(_MODULE)
    source = mod.MysqlStatsSource()
    source.config["new_key"] = "value"
    assert source.config["new_key"] == "value"


# -------------------------------------------------------- executor injection
def test_get_executor_uses_custom_when_provided() -> None:
    mod = importlib.import_module(_MODULE)
    calls: list[str] = []

    def fake(query: str) -> list:
        calls.append(query)
        return []

    source = mod.MysqlStatsSource(executor=fake)
    result = source._get_executor()
    assert result is fake
    assert calls == []


def test_query_uses_injected_executor() -> None:
    mod = importlib.import_module(_MODULE)
    calls: list[str] = []

    def fake(query: str) -> list:
        calls.append(query)
        return [
            {"Variable_name": "Uptime", "Value": "1234"},
        ]

    source = mod.MysqlStatsSource(executor=fake)
    records = source.query("status")
    assert len(calls) == 1
    assert calls[0] == "SHOW GLOBAL STATUS"
    assert len(records) == 1
    assert records[0]["value"] == 1234.0


# ---------------------------------------------------------------- health
def test_health_ok_true_when_probe_succeeds() -> None:
    mod = importlib.import_module(_MODULE)

    def fake(query: str) -> list:
        return [{"1": 1}]

    source = mod.MysqlStatsSource(executor=fake)
    result = source.health()
    assert result == {"ok": True, "detail": "mysql reachable"}


def test_health_ok_false_on_probe_failure() -> None:
    mod = importlib.import_module(_MODULE)

    def fake(query: str) -> list:
        raise OSError("connection refused")

    source = mod.MysqlStatsSource(executor=fake)
    result = source.health()
    assert result["ok"] is False
    assert "probe" in str(result["detail"])


def test_health_ok_false_on_executor_init_failure() -> None:
    mod = importlib.import_module(_MODULE)

    # Without env vars set and no executor injected, _default_executor
    # raises RuntimeError("missing user...")
    source = mod.MysqlStatsSource(
        config={"user_env": "NONEXISTENT_ENV_VAR_XYZ"},
    )
    result = source.health()
    assert result["ok"] is False
    assert "executor" in str(result["detail"]).lower()


def test_health_never_raises() -> None:
    mod = importlib.import_module(_MODULE)

    class Kaboom(Exception):
        pass

    def fake(query: str) -> list:
        raise Kaboom("boom")

    source = mod.MysqlStatsSource(executor=fake)
    result = source.health()
    assert result["ok"] is False


# ---------------------------------------------------------------- query
def test_query_defaults_to_status() -> None:
    mod = importlib.import_module(_MODULE)
    calls: list[str] = []

    def fake(query: str) -> list:
        calls.append(query)
        return [
            {"Variable_name": "Threads_connected", "Value": "5"},
        ]

    source = mod.MysqlStatsSource(executor=fake)
    records = source.query()
    assert calls[0] == "SHOW GLOBAL STATUS"
    assert len(records) == 1


def test_query_spec_status() -> None:
    mod = importlib.import_module(_MODULE)
    calls: list[str] = []

    def fake(query: str) -> list:
        calls.append(query)
        return []

    source = mod.MysqlStatsSource(executor=fake)
    source.query("status")
    assert calls[0] == "SHOW GLOBAL STATUS"


def test_query_spec_performance() -> None:
    mod = importlib.import_module(_MODULE)
    calls: list[str] = []

    def fake(query: str) -> list:
        calls.append(query)
        return []

    source = mod.MysqlStatsSource(executor=fake)
    source.query("performance")
    assert "performance_schema" in calls[0]


def test_query_spec_replica() -> None:
    mod = importlib.import_module(_MODULE)
    calls: list[str] = []

    def fake(query: str) -> list:
        calls.append(query)
        return []

    source = mod.MysqlStatsSource(executor=fake)
    source.query("replica")
    assert calls[0] == "SHOW REPLICA STATUS"


def test_query_unknown_spec_raises_valueerror() -> None:
    mod = importlib.import_module(_MODULE)

    def fake(query: str) -> list:
        return []

    source = mod.MysqlStatsSource(executor=fake)
    with pytest.raises(ValueError, match="unknown spec"):
        source.query("imaginary")


def test_query_spec_is_case_and_whitespace_insensitive() -> None:
    mod = importlib.import_module(_MODULE)
    calls: list[str] = []

    def fake(query: str) -> list:
        calls.append(query)
        return []

    source = mod.MysqlStatsSource(executor=fake)
    source.query("  STATUS  ")
    assert calls[0] == "SHOW GLOBAL STATUS"


# ------------------------------------------------------ normalize_status
def test_normalize_status_variable_name_pascal() -> None:
    mod = importlib.import_module(_MODULE)
    source = mod.MysqlStatsSource()
    rows = [{"Variable_name": "Threads_connected", "Value": "42"}]
    result = source._normalize_status(rows)
    assert len(result) == 1
    assert result[0]["value"] == 42.0
    assert result[0]["message"] == "global status Threads_connected"
    assert result[0]["labels"] == {"variable": "Threads_connected"}
    assert result[0]["source"] == "mysql_stats"
    assert result[0]["kind"] == "metrics"
    assert result[0]["level_or_status"] == "ok"


def test_normalize_status_variable_name_lowercase() -> None:
    mod = importlib.import_module(_MODULE)
    source = mod.MysqlStatsSource()
    rows = [{"variable_name": "Com_select", "value": "9999"}]
    result = source._normalize_status(rows)
    assert len(result) == 1
    assert result[0]["value"] == 9999.0
    assert result[0]["message"] == "global status Com_select"


def test_normalize_status_skips_non_numeric() -> None:
    mod = importlib.import_module(_MODULE)
    source = mod.MysqlStatsSource()
    rows = [{"Variable_name": "version", "Value": "8.0.30"}]
    result = source._normalize_status(rows)
    assert result == []


def test_normalize_status_with_int_value() -> None:
    mod = importlib.import_module(_MODULE)
    source = mod.MysqlStatsSource()
    rows = [{"Variable_name": "Uptime", "Value": 100500}]
    result = source._normalize_status(rows)
    assert result[0]["value"] == 100500.0


# -------------------------------------------------- normalize_performance
def test_normalize_performance_rows() -> None:
    mod = importlib.import_module(_MODULE)
    source = mod.MysqlStatsSource()
    rows = [
        {
            "event_name": "statement/sql/select",
            "count_star": 100,
            "sum_timer_wait": 5000000000,
        },
        {
            "event_name": "statement/sql/insert",
            "count_star": 50,
            "sum_timer_wait": 2000000000,
        },
    ]
    result = source._normalize_performance(rows)
    assert len(result) == 2
    assert result[0]["value"] == 5000000000.0
    assert result[0]["message"] == "perf_schema statement/sql/select sum_timer_wait"
    assert result[0]["labels"] == {
        "event_name": "statement/sql/select",
        "count_star": "100",
    }
    assert result[1]["labels"]["event_name"] == "statement/sql/insert"


def test_normalize_performance_none_value() -> None:
    mod = importlib.import_module(_MODULE)
    source = mod.MysqlStatsSource()
    rows = [
        {
            "event_name": "wait/synch/mutex/sql/THD::LOCK_query_plan",
            "count_star": 5,
            "sum_timer_wait": None,
        },
    ]
    result = source._normalize_performance(rows)
    assert result[0]["value"] is None


# ------------------------------------------------------ normalize_replica
def test_normalize_replica_io_running_yes() -> None:
    mod = importlib.import_module(_MODULE)
    source = mod.MysqlStatsSource()
    rows = [
        {
            "Replica_IO_Running": "Yes",
            "Seconds_Behind_Source": 0,
            "Source_Host": "primary.example.com",
        }
    ]
    result = source._normalize_replica(rows)
    assert len(result) == 1
    assert result[0]["value"] == 0.0
    assert result[0]["labels"]["io_running"] == "Yes"
    assert result[0]["labels"]["source_host"] == "primary.example.com"
    assert result[0]["level_or_status"] == "ok"


def test_normalize_replica_io_running_no() -> None:
    mod = importlib.import_module(_MODULE)
    source = mod.MysqlStatsSource()
    rows = [
        {
            "Replica_IO_Running": "No",
            "Seconds_Behind_Source": None,
            "Source_Host": None,
        }
    ]
    result = source._normalize_replica(rows)
    assert result[0]["level_or_status"] == "degraded"


def test_normalize_replica_slave_compat() -> None:
    mod = importlib.import_module(_MODULE)
    source = mod.MysqlStatsSource()
    rows = [
        {
            "Slave_IO_Running": "Yes",
            "Seconds_Behind_Master": 5,
            "Master_Host": "master.old.example.com",
        }
    ]
    result = source._normalize_replica(rows)
    assert result[0]["value"] == 5.0
    assert result[0]["labels"]["io_running"] == "Yes"
    assert result[0]["labels"]["source_host"] == "master.old.example.com"
    assert result[0]["level_or_status"] == "ok"


def test_normalize_replica_prefers_source_over_master() -> None:
    mod = importlib.import_module(_MODULE)
    source = mod.MysqlStatsSource()
    rows = [
        {
            "Replica_IO_Running": "Yes",
            "Slave_IO_Running": "No",
            "Seconds_Behind_Source": 3,
            "Seconds_Behind_Master": 999,
            "Source_Host": "primary.example.com",
            "Master_Host": "old.example.com",
        }
    ]
    result = source._normalize_replica(rows)
    assert result[0]["value"] == 3.0
    assert result[0]["labels"]["io_running"] == "Yes"
    assert result[0]["labels"]["source_host"] == "primary.example.com"


def test_normalize_replica_io_running_case_insensitive() -> None:
    mod = importlib.import_module(_MODULE)
    source = mod.MysqlStatsSource()
    rows = [
        {
            "Replica_IO_Running": "yes",
            "Seconds_Behind_Source": 0,
            "Source_Host": "primary.example.com",
        }
    ]
    result = source._normalize_replica(rows)
    assert result[0]["level_or_status"] == "ok"


def test_normalize_replica_empty_rows() -> None:
    mod = importlib.import_module(_MODULE)
    source = mod.MysqlStatsSource()
    result = source._normalize_replica([])
    assert result == []


# ---------------------------------------------------------------- record
def test_record_shape() -> None:
    mod = importlib.import_module(_MODULE)
    source = mod.MysqlStatsSource()
    raw_row: dict[str, object] = {"Variable_name": "X", "Value": "1"}
    rec = source._record(message="test msg", value=1.0, labels={"k": "v"}, raw=raw_row)
    assert rec["ts"].endswith("+00:00") or "T" in rec["ts"]
    assert rec["source"] == "mysql_stats"
    assert rec["kind"] == "metrics"
    assert rec["level_or_status"] == "ok"
    assert rec["message"] == "test msg"
    assert rec["value"] == 1.0
    assert rec["labels"] == {"k": "v"}
    assert rec["raw"] == raw_row


def test_record_strips_none_labels() -> None:
    mod = importlib.import_module(_MODULE)
    source = mod.MysqlStatsSource()
    rec = source._record(
        message="test", value=0.0, labels={"a": "1", "b": None, "c": "3"}, raw={}
    )
    assert rec["labels"] == {"a": "1", "c": "3"}


def test_record_custom_status() -> None:
    mod = importlib.import_module(_MODULE)
    source = mod.MysqlStatsSource()
    rec = source._record(
        message="degraded", value=None, labels={}, raw={}, status="degraded"
    )
    assert rec["level_or_status"] == "degraded"


# ------------------------------------------------------- resolve_secret
def test_resolve_secret_returns_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module(_MODULE)
    monkeypatch.setenv("TEST_MYSQL_HOST", "db.example.com")
    source = mod.MysqlStatsSource(config={"host_env": "TEST_MYSQL_HOST"})
    assert source._resolve_secret("host_env") == "db.example.com"


def test_resolve_secret_returns_none_when_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module(_MODULE)
    monkeypatch.delenv("TEST_MISSING", raising=False)
    source = mod.MysqlStatsSource(config={"host_env": "TEST_MISSING"})
    assert source._resolve_secret("host_env") is None


def test_resolve_secret_returns_none_when_key_absent() -> None:
    mod = importlib.import_module(_MODULE)
    source = mod.MysqlStatsSource()
    assert source._resolve_secret("host_env") is None
