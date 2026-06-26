"""PostgreSQL statistics observability connector.

Read-only metrics source that runs ``pg_stat_*`` queries via an injected
query-executor and normalizes the rows into a uniform metric-record shape.

Design contract:
  * No real database is required to import or unit-test this module.
  * The psycopg driver import is guarded so the module imports cleanly even
    when psycopg is absent; in that case ``health()`` reports the driver as
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
from typing import Any

logger = logging.getLogger(__name__)

# A query-executor takes a SQL string and returns rows as a sequence of
# mappings (column-name -> value).
Row = Mapping[str, Any]
Executor = Callable[[str], Sequence[Row]]


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _to_float(value: Any) -> float | None:
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


# SQL statements are static, read-only, and parameter-free. They are kept as
# module constants so they are auditable and never built from user input.
_SQL_ACTIVITY = (
    "SELECT state, count(*) AS value, datname "
    "FROM pg_stat_activity GROUP BY state, datname"
)
_SQL_REPLICATION = (
    "SELECT application_name, "
    "EXTRACT(EPOCH FROM replay_lag) AS value "
    "FROM pg_stat_replication"
)
_SQL_DATABASE = (
    "SELECT datname, xact_commit, xact_rollback, blks_read, blks_hit "
    "FROM pg_stat_database WHERE datname IS NOT NULL"
)
_SQL_STATEMENTS = (
    "SELECT queryid AS query_id, mean_exec_time AS value, calls "
    "FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 20"
)

_SPECS: dict[str, str] = {
    "activity": _SQL_ACTIVITY,
    "replication": _SQL_REPLICATION,
    "database": _SQL_DATABASE,
    "statements": _SQL_STATEMENTS,
}


class PostgresStatsSource:
    """Observability source for PostgreSQL ``pg_stat_*`` views.

    The executor is injectable for testing; the default lazily builds a psycopg
    connection from environment-sourced credentials.
    """

    KIND = "metrics"
    name = "postgres_stats"

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self._executor = executor

    # -- credential / driver plumbing ------------------------------------

    def _resolve_secret(self, key: str) -> str | None:
        """Resolve a secret from the env var named by ``config[key]``.

        ``config`` carries the *name* of the env var (e.g. ``dsn_env``), never
        the secret value itself.
        """
        env_name = self.config.get(key)
        if not env_name:
            return None
        return os.environ.get(str(env_name))

    def _default_executor(self) -> Executor:
        """Build a psycopg-backed executor.

        The driver import is guarded: if psycopg is not installed this raises a
        ``RuntimeError`` that callers (``health``) translate into an
        unavailable status. Import errors never propagate as ImportError.
        """
        try:
            import psycopg  # intentional lazy, guarded driver import
        except ImportError as exc:  # pragma: no cover - exercised via health()
            raise RuntimeError("driver unavailable: psycopg not installed") from exc

        dsn = self._resolve_secret("dsn_env")
        if not dsn:
            raise RuntimeError("missing DSN: set config['dsn_env'] to an env var name")

        def _run(query: str) -> Sequence[Row]:  # pragma: no cover - needs real DB
            conn = psycopg.connect(dsn, autocommit=True)
            try:
                with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
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

    def health(self) -> dict[str, Any]:
        """Report connectivity health. Never raises."""
        try:
            executor = self._get_executor()
        except Exception:  # health must never raise (driver/config init errors)
            # Never echo str(exc): a driver/config error can embed the DSN or
            # credentials. Log the real detail; return a static generic marker.
            logger.warning("postgres_stats executor init failed", exc_info=True)
            return {"ok": False, "detail": "executor init failed"}

        try:
            executor("SELECT 1")
        except Exception:  # health must never raise
            logger.warning("postgres_stats probe failed", exc_info=True)
            return {"ok": False, "detail": "probe failed"}
        return {"ok": True, "detail": "postgres reachable"}

    # -- normalization ---------------------------------------------------

    def _record(
        self,
        *,
        message: str,
        value: float | None,
        labels: Mapping[str, Any],
        raw: Row,
        status: str = "ok",
    ) -> dict[str, Any]:
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

    def _normalize_activity(self, rows: Sequence[Row]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in rows:
            state = row.get("state")
            datname = row.get("datname")
            out.append(
                self._record(
                    message="pg_stat_activity connection count",
                    value=_to_float(row.get("value")),
                    labels={"datname": datname, "state": state},
                    raw=row,
                )
            )
        return out

    def _normalize_replication(self, rows: Sequence[Row]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                self._record(
                    message="pg_stat_replication replay lag seconds",
                    value=_to_float(row.get("value")),
                    labels={"application_name": row.get("application_name")},
                    raw=row,
                )
            )
        return out

    def _normalize_database(self, rows: Sequence[Row]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        fields = ("xact_commit", "xact_rollback", "blks_read", "blks_hit")
        for row in rows:
            datname = row.get("datname")
            for field in fields:
                if field not in row:
                    continue
                out.append(
                    self._record(
                        message=f"pg_stat_database {field}",
                        value=_to_float(row.get(field)),
                        labels={"datname": datname, "metric": field},
                        raw=row,
                    )
                )
        return out

    def _normalize_statements(self, rows: Sequence[Row]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                self._record(
                    message="pg_stat_statements mean exec time ms",
                    value=_to_float(row.get("value")),
                    labels={
                        "query_id": row.get("query_id"),
                        "calls": row.get("calls"),
                    },
                    raw=row,
                )
            )
        return out

    # -- query -----------------------------------------------------------

    def query(self, spec: str | None = None) -> list[dict[str, Any]]:
        """Run the selected stat query and return normalized records.

        ``spec`` selects which view to read: ``activity`` (default),
        ``replication``, ``database`` or ``statements``.
        """
        which = (spec or "activity").strip().lower()
        sql = _SPECS.get(which)
        if sql is None:
            raise ValueError(
                f"unknown spec {which!r}; expected one of {sorted(_SPECS)}"
            )

        executor = self._get_executor()
        rows = executor(sql)

        normalizers: dict[str, Callable[[Sequence[Row]], list[dict[str, Any]]]] = {
            "activity": self._normalize_activity,
            "replication": self._normalize_replication,
            "database": self._normalize_database,
            "statements": self._normalize_statements,
        }
        return normalizers[which](rows)
