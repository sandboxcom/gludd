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
    * ``INFO``    -> a ``RedisInfo`` (Mapping[str, object]) of ``field -> value``
      (redis-py ``decode_responses`` style, optionally with ``# Section``
      markers folded in as a ``__section__`` hint per field).
    * ``SLOWLOG GET`` -> a Sequence of :class:`RedisSlowlogEntry` dicts with
      keys like ``id``, ``start_time``, ``duration``, ``command``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import TypedDict, cast

from general_ludd.connectors.base import NormalizedRecord, normalized_record

logger = logging.getLogger(__name__)


# Redis INFO replies are a dynamic, heterogeneous field->value mapping (the
# field set is not enumerable across Redis versions), so this is a type alias
# rather than a TypedDict.
RedisInfo = Mapping[str, object]


class RedisSlowlogEntry(TypedDict, total=False):
    """A single ``SLOWLOG GET`` entry.

    Fields are ``total=False`` because Redis versions differ in which keys
    they emit (e.g. ``command`` may be a list of args or a joined string).
    """

    id: int | str
    start_time: int
    duration: int
    command: str | list[str]


class RedisConfig(TypedDict, total=False):
    """Typed shape for the ``config`` mapping accepted by RedisStatsSource.

    Operators may type their config dict as ``RedisConfig``; the class itself
    accepts any ``Mapping[str, object]`` so unknown keys pass through.
    """

    url_env: str


# The Redis executor returns one of three shapes depending on the command:
#   * ``INFO``         -> a RedisInfo mapping
#   * ``SLOWLOG GET``  -> a Sequence of RedisSlowlogEntry
#   * ``PING``         -> a bool (probe result, consumed by health())
ReplyValue = RedisInfo | Sequence[RedisSlowlogEntry] | bool
Executor = Callable[[str], ReplyValue]

_INFO_COMMAND = "INFO"
_SLOWLOG_COMMAND = "SLOWLOG GET"

_SPECS: tuple[str, ...] = ("info", "slowlog")


def _utc_now_epoch() -> float:
    """Current UTC time as epoch seconds (float), matching the NormalizedRecord ts contract."""
    return datetime.now(UTC).timestamp()


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
        config: Mapping[str, object] | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.config: dict[str, object] = dict(config or {})
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
            import importlib

            redis = importlib.import_module("redis")  # lazy guarded import
        except ImportError as exc:  # pragma: no cover - exercised via health()
            raise RuntimeError("driver unavailable: redis not installed") from exc

        url = self._resolve_secret("url_env")
        if not url:
            raise RuntimeError("missing URL: set config['url_env'] to an env var name")

        client = redis.Redis.from_url(url, decode_responses=True)  # pragma: no cover

        def _run(command: str) -> ReplyValue:  # pragma: no cover - needs real Redis
            cmd = command.strip().upper()
            if cmd == _INFO_COMMAND:
                return cast(ReplyValue, client.info())
            if cmd == _SLOWLOG_COMMAND:
                return cast(ReplyValue, client.slowlog_get())
            if cmd == "PING":
                return cast(bool, client.ping())
            raise ValueError(f"unsupported command: {command!r}")

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
        except Exception as exc:  # health must never raise (driver/config init errors)
            logger.warning(
                "redis_stats executor init failed: %s", type(exc).__name__,
                exc_info=False,
            )
            return {"ok": False, "detail": "executor init failed"}

        try:
            executor("PING")
        except Exception as exc:  # health must never raise
            logger.warning(
                "redis_stats probe failed: %s", type(exc).__name__,
                exc_info=False,
            )
            return {"ok": False, "detail": "probe failed"}
        return {"ok": True, "detail": "redis reachable"}

    # -- normalization ---------------------------------------------------

    def _record(
        self,
        *,
        message: str,
        value: float | None,
        labels: Mapping[str, object],
        raw: Mapping[str, object],
        status: str = "ok",
    ) -> NormalizedRecord:
        clean_labels = {k: str(v) for k, v in labels.items() if v is not None}
        # Route through normalized_record so a non-finite (NaN / +Inf / -Inf)
        # ``value`` is coerced to None per the boundary numeric policy, and ``ts``
        # is an epoch float (not an ISO string) consistent with _sort_by_ts /
        # associate() — an ISO-string ts would TypeError in the find/sort path.
        return normalized_record(
            source=self.name,
            kind=self.KIND,
            message=message,
            ts=_utc_now_epoch(),
            level_or_status=status,
            value=value,
            labels=clean_labels,
            raw=dict(raw),
        )

    def _normalize_info(self, info: RedisInfo) -> list[NormalizedRecord]:
        out: list[NormalizedRecord] = []
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
        self, entries: Sequence[RedisSlowlogEntry]
    ) -> list[NormalizedRecord]:
        out: list[NormalizedRecord] = []
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

    def query(self, spec: str | None = None) -> list[NormalizedRecord]:
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
