"""Redis statistics observability connector.

Read-only metrics source that runs ``INFO`` (all sections) and
``SLOWLOG GET`` via an injected command-executor and normalizes the result
into a uniform metric-record shape.

Design contract:
  * No real Redis is required to import or unit-test this module.
  * The redis driver import is guarded so the module imports cleanly even when
    redis is absent; in that case ``health()`` reports the driver as
    unavailable instead of raising.
  * Credentials are read from environment variables named by ``*_env`` config
    keys. They are NEVER hardcoded.
  * ``query()`` returns a list of normalized dicts; ``health()`` never raises.

Executor contract:
  ``executor(command)`` runs a Redis command string and returns its parsed
  reply:
    * ``INFO``    -> a Mapping[str, Any] of ``field -> value`` (redis-py
      ``decode_responses`` style, optionally with ``# Section`` markers folded
      in as a ``__section__`` hint per field).
    * ``SLOWLOG GET`` -> a Sequence[Mapping] of slowlog entries with keys like
      ``id``, ``start_time``, ``duration``, ``command``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# The Redis executor returns either an INFO mapping or a sequence of slowlog
# entries, so its return type is intentionally broad.
ReplyValue = Any
Executor = Callable[[str], ReplyValue]

_INFO_COMMAND = "INFO"
_SLOWLOG_COMMAND = "SLOWLOG GET"

_SPECS: tuple[str, ...] = ("info", "slowlog")


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


def _section_for_field(field: str) -> str:
    """Heuristic section bucket for a redis INFO field name."""
    f = field.lower()
    if f.startswith("used_memory") or "mem" in f:
        return "memory"
    if f.startswith("rdb_") or f.startswith("aof_") or "persistence" in f:
        return "persistence"
    if f.startswith("connected") or f.startswith("blocked") or "clients" in f:
        return "clients"
    if (
        f.startswith("total_")
        or f.startswith("instantaneous_")
        or f.startswith("keyspace_")
        or f.startswith("expired_")
        or f.startswith("evicted_")
        or f.startswith("rejected_")
    ):
        return "stats"
    if "repl" in f or "slave" in f or "master" in f:
        return "replication"
    return "server"


class RedisStatsSource:
    """Observability source for Redis ``INFO`` and ``SLOWLOG``."""

    KIND = "metrics"
    name = "redis_stats"

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self._executor = executor

    # -- credential / driver plumbing ------------------------------------

    def _resolve_secret(self, key: str) -> str | None:
        """Resolve a secret from the env var named by ``config[key]``."""
        env_name = self.config.get(key)
        if not env_name:
            return None
        return os.environ.get(str(env_name))

    def _default_executor(self) -> Executor:
        """Build a redis-py backed executor with a guarded driver import."""
        try:
            import redis  # type: ignore[import-not-found]  # lazy guarded import
        except ImportError as exc:  # pragma: no cover - exercised via health()
            raise RuntimeError("driver unavailable: redis not installed") from exc

        url = self._resolve_secret("url_env")
        if not url:
            raise RuntimeError("missing URL: set config['url_env'] to an env var name")

        client = redis.Redis.from_url(url, decode_responses=True)  # pragma: no cover

        def _run(command: str) -> ReplyValue:  # pragma: no cover - needs real Redis
            cmd = command.strip().upper()
            if cmd == _INFO_COMMAND:
                return client.info()
            if cmd == _SLOWLOG_COMMAND:
                return client.slowlog_get()
            if cmd == "PING":
                return client.ping()
            raise ValueError(f"unsupported command: {command!r}")

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
            # Never echo str(exc): a driver/config error can embed the URL or
            # credentials. Log the real detail; return a static generic marker.
            logger.warning("redis_stats executor init failed", exc_info=True)
            return {"ok": False, "detail": "executor init failed"}

        try:
            executor("PING")
        except Exception:  # health must never raise
            logger.warning("redis_stats probe failed", exc_info=True)
            return {"ok": False, "detail": "probe failed"}
        return {"ok": True, "detail": "redis reachable"}

    # -- normalization ---------------------------------------------------

    def _record(
        self,
        *,
        message: str,
        value: float | None,
        labels: Mapping[str, Any],
        raw: Mapping[str, Any],
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

    def _normalize_info(self, info: Mapping[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for field, raw_value in info.items():
            num = _to_float(raw_value)
            if num is None:
                # Non-numeric INFO fields (e.g. redis_version) are not metrics.
                continue
            section = _section_for_field(str(field))
            out.append(
                self._record(
                    message=f"info {field}",
                    value=num,
                    labels={"section": section, "field": field},
                    raw={"field": field, "value": raw_value},
                )
            )
        return out

    def _normalize_slowlog(
        self, entries: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for entry in entries:
            command = entry.get("command")
            if isinstance(command, (list, tuple)):
                command_str = " ".join(str(part) for part in command)
            else:
                command_str = str(command) if command is not None else ""
            out.append(
                self._record(
                    message="slowlog entry duration microseconds",
                    value=_to_float(entry.get("duration")),
                    labels={
                        "section": "slowlog",
                        "id": entry.get("id"),
                        "command": command_str or None,
                    },
                    raw=dict(entry),
                )
            )
        return out

    # -- query -----------------------------------------------------------

    def query(self, spec: str | None = None) -> list[dict[str, Any]]:
        """Run the selected command and return normalized records.

        ``spec`` selects: ``info`` (default) or ``slowlog``.
        """
        which = (spec or "info").strip().lower()
        if which not in _SPECS:
            raise ValueError(
                f"unknown spec {which!r}; expected one of {list(_SPECS)}"
            )

        executor = self._get_executor()

        if which == "info":
            reply = executor(_INFO_COMMAND)
            if not isinstance(reply, Mapping):
                raise TypeError("INFO executor must return a mapping")
            return self._normalize_info(reply)

        reply = executor(_SLOWLOG_COMMAND)
        if not isinstance(reply, Sequence) or isinstance(reply, (str, bytes)):
            raise TypeError("SLOWLOG executor must return a sequence of entries")
        return self._normalize_slowlog(reply)
