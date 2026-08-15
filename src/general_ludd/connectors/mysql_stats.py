"""MySQL statistics observability connector.

Read-only metrics source that runs ``SHOW GLOBAL STATUS``,
``performance_schema`` and ``SHOW REPLICA STATUS`` style queries via an
injected query-executor and normalizes the rows into a uniform metric-record
shape.

Design contract:
  * No real database is required to import or unit-test this module.
  * The pymysql driver import is guarded so the module imports cleanly even
    when pymysql is absent; in that case ``health()`` reports the driver as
    unavailable instead of raising.
  * Credentials are read from environment variables named by ``*_env`` config
    keys. They are NEVER hardcoded.
  * ``query()`` returns a list of normalized dicts; ``health()`` never raises.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, TypedDict, cast

logger = logging.getLogger(__name__)


class MysqlStatusRow(TypedDict, total=False):
    """Row shape from ``SHOW GLOBAL STATUS`` (MySQL 5.x and 8.x spellings)."""

    Variable_name: str
    Value: str | int | float
    variable_name: str
    value: str | int | float


class MysqlPerformanceRow(TypedDict, total=False):
    """Row shape from ``performance_schema.events_statements_summary_global_by_event_name``."""

    event_name: str
    count_star: int
    sum_timer_wait: int | float


class MysqlReplicaRow(TypedDict, total=False):
    """Row shape from ``SHOW REPLICA STATUS`` / ``SHOW SLAVE STATUS``."""

    Replica_IO_Running: str
    Slave_IO_Running: str
    Seconds_Behind_Source: int | float
    Seconds_Behind_Master: int | float
    Source_Host: str
    Master_Host: str


MysqlRow = MysqlStatusRow | MysqlPerformanceRow | MysqlReplicaRow
Executor = Callable[[str], Sequence[MysqlRow]]


class MysqlConfig(TypedDict, total=False):
    """Optional constructor config for :class:`MysqlStatsSource`."""

    host_env: str
    user_env: str
    password_env: str
    database_env: str
    host: str
    database: str


class MysqlRecord(TypedDict):
    """Normalized metric-record emitted by this source.

    Distinct from :class:`general_ludd.connectors.base.NormalizedRecord` because
    ``ts`` here is an ISO-8601 string (via :func:`_utc_now_iso`) rather than a
    POSIX ``float``. Changing the ``ts`` shape would be a runtime behavior
    change in downstream consumers, so we keep a local record type.
    """

    ts: str
    source: str
    kind: str
    level_or_status: str
    message: str
    value: float | None
    labels: dict[str, str]
    raw: dict[str, object]


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _to_float(value: object) -> float | None:
    """Best-effort numeric coercion that never raises."""
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


# Static, read-only, parameter-free SQL kept as auditable constants.
_SQL_GLOBAL_STATUS = "SHOW GLOBAL STATUS"
_SQL_PERFORMANCE = (
    "SELECT event_name, count_star, sum_timer_wait "
    "FROM performance_schema.events_statements_summary_global_by_event_name "
    "WHERE count_star > 0 ORDER BY sum_timer_wait DESC LIMIT 50"
)
_SQL_REPLICA = "SHOW REPLICA STATUS"

_SPECS: dict[str, str] = {
    "status": _SQL_GLOBAL_STATUS,
    "performance": _SQL_PERFORMANCE,
    "replica": _SQL_REPLICA,
}


class MysqlStatsSource:
    """Observability source for MySQL global status / perf-schema / replica."""

    KIND = "metrics"
    name = "mysql_stats"

    def __init__(
        self,
        config: Mapping[str, object] | None = None,
        executor: Executor | None = None,
        *,
        cursor: object | None = None,
    ) -> None:
        """Build the source from connector config; executor and cursor are mutually exclusive."""
        if executor is not None and cursor is not None:
            raise ValueError("provide exactly one of executor or cursor, not both")
        self.config: dict[str, object] = dict(config or {})
        self._executor: Executor | None
        if cursor is not None:

            def _cursor_executor(query: str) -> Sequence[MysqlRow]:
                cursor_obj = cast(Any, cursor)
                cursor_obj.execute(query)
                return list(cursor_obj)

            self._executor = _cursor_executor
        else:
            self._executor = executor

    # -- credential / driver plumbing ------------------------------------

    def _resolve_secret(self, key: str) -> str | None:
        """Resolve a secret from the env var named by ``config[key]``."""
        env_name = self.config.get(key)
        if not env_name:
            return None
        return os.environ.get(str(env_name))

    def _default_executor(self) -> Executor:
        """Build a pymysql-backed executor with a guarded driver import."""
        try:
            import importlib

            pymysql = importlib.import_module("pymysql")  # pymysql: optional driver, guarded by try/except
            importlib.import_module("pymysql.cursors")  # pymysql: optional driver
        except ImportError as exc:  # pragma: no cover - exercised via health()
            raise RuntimeError("driver unavailable: pymysql not installed") from exc

        host = self._resolve_secret("host_env") or self.config.get("host") or "localhost"
        user = self._resolve_secret("user_env")
        password = self._resolve_secret("password_env")
        database = self._resolve_secret("database_env") or self.config.get("database")
        if not user:
            raise RuntimeError("missing user: set config['user_env'] to an env var name")

        def _run(query: str) -> Sequence[MysqlRow]:  # pragma: no cover - needs real DB
            conn = pymysql.connect(
                host=str(host),
                user=str(user),
                password=str(password or ""),
                database=str(database) if database else None,
                cursorclass=pymysql.cursors.DictCursor,
            )
            try:
                with conn.cursor() as cur:
                    cur.execute(query)  # static, parameter-free read-only SQL
                    return list(cur.fetchall())
            finally:
                conn.close()

        return _run

    def _get_executor(self) -> Executor:
        if self._executor is not None:
            return self._executor
        return self._default_executor()

    # -- health ----------------------------------------------------------

    def health(self) -> dict[str, object]:
        """Report connectivity health. Never raises."""
        try:
            executor = self._get_executor()
        except RuntimeError as exc:
            logger.warning(
                "mysql_stats executor unavailable: %s",
                type(exc).__name__,
                exc_info=False,
            )
            return {"ok": False, "detail": "executor unavailable"}
        except Exception as exc:  # health must never raise
            logger.warning(
                "mysql_stats executor init failed: %s",
                type(exc).__name__,
                exc_info=False,
            )
            return {"ok": False, "detail": "executor init failed"}

        try:
            executor("SELECT 1")
        except Exception as exc:  # health must never raise
            logger.warning(
                "mysql_stats probe failed: %s",
                type(exc).__name__,
                exc_info=False,
            )
            return {"ok": False, "detail": "probe failed"}
        return {"ok": True, "detail": "mysql reachable"}

    # -- normalization ---------------------------------------------------

    def _record(
        self,
        *,
        message: str,
        value: float | None,
        labels: Mapping[str, object],
        raw: MysqlRow,
        status: str = "ok",
    ) -> MysqlRecord:
        clean_labels = {k: str(v) for k, v in labels.items() if v is not None}
        return {
            "ts": _utc_now_iso(),
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": status,
            "message": message,
            "value": value,
            "labels": clean_labels,
            "raw": dict(raw),
        }

    def _normalize_status(self, rows: Sequence[MysqlRow]) -> list[MysqlRecord]:
        """``SHOW GLOBAL STATUS`` rows are ``{Variable_name, Value}`` pairs."""
        out: list[MysqlRecord] = []
        for row in rows:
            var = row.get("Variable_name") or row.get("variable_name")
            raw_value = row.get("Value") if "Value" in row else row.get("value")
            num = _to_float(raw_value)
            if num is None:
                # Non-numeric status (e.g. a version string) is skipped as a
                # metric but still labeled for traceability.
                continue
            out.append(
                self._record(
                    message=f"global status {var}",
                    value=num,
                    labels={"variable": var},
                    raw=row,
                )
            )
        return out

    def _normalize_performance(self, rows: Sequence[MysqlRow]) -> list[MysqlRecord]:
        out: list[MysqlRecord] = []
        for row in rows:
            event = row.get("event_name")
            out.append(
                self._record(
                    message=f"perf_schema {event} sum_timer_wait",
                    value=_to_float(row.get("sum_timer_wait")),
                    labels={
                        "event_name": event,
                        "count_star": row.get("count_star"),
                    },
                    raw=row,
                )
            )
        return out

    def _normalize_replica(self, rows: Sequence[MysqlRow]) -> list[MysqlRecord]:
        out: list[MysqlRecord] = []
        for row in rows:
            running = row.get("Replica_IO_Running") or row.get("Slave_IO_Running")
            ok = str(running).strip().lower() == "yes"
            out.append(
                self._record(
                    message="replica seconds behind source",
                    value=_to_float(
                        row.get("Seconds_Behind_Source")
                        if "Seconds_Behind_Source" in row
                        else row.get("Seconds_Behind_Master")
                    ),
                    labels={
                        "io_running": running,
                        "source_host": row.get("Source_Host") or row.get("Master_Host"),
                    },
                    raw=row,
                    status="ok" if ok else "degraded",
                )
            )
        return out

    # -- query -----------------------------------------------------------

    def query(self, spec: str | None = None) -> list[MysqlRecord]:
        """Run the selected stat query and return normalized records.

        ``spec`` selects: ``status`` (default), ``performance`` or ``replica``.
        """
        which = (spec or "status").strip().lower()
        sql = _SPECS.get(which)
        if sql is None:
            raise ValueError(f"unknown spec {which!r}; expected one of {sorted(_SPECS)}")

        executor = self._get_executor()
        rows = executor(sql)

        normalizers: dict[str, Callable[[Sequence[MysqlRow]], list[MysqlRecord]]] = {
            "status": self._normalize_status,
            "performance": self._normalize_performance,
            "replica": self._normalize_replica,
        }
        return normalizers[which](rows)
