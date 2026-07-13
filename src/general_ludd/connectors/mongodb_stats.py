"""MongoDB observability connector — a *metrics* source over admin commands.

The connector turns the output of the MongoDB admin commands
``serverStatus()``, ``currentOp()`` and ``replSetGetStatus()`` into normalized
metric records (connections, opcounters, replication oplog lag, WiredTiger
cache).

Design (matches the package contract):

  - The command executor is INJECTABLE via ``executor=``. It is a callable
    ``(command: str) -> Mapping[str, object]`` that returns the raw document a
    MongoDB admin command yields. Tests inject a canned executor so no real
    database (or ``pymongo`` driver) is ever required.
  - When no executor is given, a default one is built LAZILY on first use. It
    imports ``pymongo`` behind a guard; if the driver is absent the connector
    stays importable and ``health()`` reports ``"driver unavailable"``.
  - Credentials are taken from an environment variable named by
    ``config['uri_env']`` (default ``MONGODB_URI``). The URI itself is never
    logged or embedded in records.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Mapping
from typing import TypedDict, cast

logger = logging.getLogger(__name__)


# --- TypedDicts for MongoDB admin-command result documents -------------------
# These model the subset of keys this connector consumes. ``total=False`` because
# every field is optional at the MongoDB wire level. They double as living
# documentation of the shapes produced by serverStatus / currentOp /
# replSetGetStatus, and underpin the executor signature (which stays the more
# permissive ``Mapping[str, object]`` so per-command ``.get()`` stays ergonomic).


class MongoOpTime(TypedDict, total=False):
    """The ``optime`` sub-document of a replica-set member."""

    ts: int | float


class MongoMember(TypedDict, total=False):
    """One element of ``replSetGetStatus().members``."""

    name: str
    stateStr: str
    optimeDate: int | float
    optime: MongoOpTime
    optime_seconds: int | float


class MongoReplSetDoc(TypedDict, total=False):
    """Result of ``replSetGetStatus``."""

    members: list[MongoMember]


class MongoCurrentOpDoc(TypedDict, total=False):
    """Result of ``currentOp``."""

    inprog: list[object]


class MongoServerStatusDoc(TypedDict, total=False):
    """Result of ``serverStatus`` (the subset of keys we consume)."""

    connections: Mapping[str, int | float]
    opcounters: Mapping[str, int | float]
    wiredTiger: Mapping[str, Mapping[str, object]]


# Union of the three admin-command documents the executor may return. Kept as a
# named alias for callers that want to discriminate on command name; the
# executor signature itself uses the wider ``Mapping[str, object]`` so that
# per-key ``.get()`` remains ergonomic.
MongoAdminDoc = MongoServerStatusDoc | MongoCurrentOpDoc | MongoReplSetDoc


class MongoConfig(TypedDict, total=False):
    """Constructor config accepted by :class:`MongoDbStatsSource`."""

    name: str
    uri_env: str


class MongoQuerySpec(TypedDict, total=False):
    """Query spec — ignored by this connector (``query()`` reads everything)."""


class MongoRecord(TypedDict):
    """The 8-key normalized record shape produced by :meth:`MongoDbStatsSource._record`."""

    ts: float
    source: str
    kind: str
    level_or_status: str
    message: str
    value: float | int | None
    labels: dict[str, object]
    raw: object


# An executor maps a mongo admin command name to its raw result document.
Executor = Callable[[str], Mapping[str, object]]

_DRIVER_UNAVAILABLE = "driver unavailable"


class MongoDbStatsSource:
    """Normalize MongoDB admin-command output into metric records."""

    KIND = "metrics"

    def __init__(self, config: MongoConfig | None = None, executor: Executor | None = None) -> None:
        cfg: dict[str, object] = dict(config or {})
        self.name: str = str(cfg.get("name", "mongodb"))
        self._config = cfg
        # Env var NAME holding the connection URI (never the secret itself).
        self._uri_env: str = str(cfg.get("uri_env", "MONGODB_URI"))
        self._executor = executor
        self._driver_error: str | None = None

    # -- executor wiring ---------------------------------------------------

    def _get_executor(self) -> Executor | None:
        """Return the active executor, lazily building the default if needed.

        Returns ``None`` (and records ``self._driver_error``) when the driver is
        unavailable, so callers can fail soft.
        """
        if self._executor is not None:
            return self._executor
        executor = self._build_default_executor()
        if executor is not None:
            self._executor = executor
        return executor

    def _build_default_executor(self) -> Executor | None:
        uri = os.environ.get(self._uri_env)
        if not uri:
            self._driver_error = f"missing env {self._uri_env}"
            return None
        try:
            import importlib

            pymongo = importlib.import_module("pymongo")  # guarded optional dependency
        except Exception as exc:  # pragma: no cover - exercised via health test with injected error
            self._driver_error = _DRIVER_UNAVAILABLE
            logger.debug("pymongo import failed: %s", exc)
            return None

        client = pymongo.MongoClient(uri)

        def _run(command: str) -> Mapping[str, object]:
            result = client.admin.command(command)
            return dict(result)

        return _run

    # -- health ------------------------------------------------------------

    def health(self) -> dict[str, object]:
        """Probe the source. Never raises."""
        executor = self._get_executor()
        if executor is None:
            return {"ok": False, "detail": self._driver_error or _DRIVER_UNAVAILABLE}
        try:
            executor("serverStatus")
        except Exception as exc:
            logger.warning(
                "mongodb_stats serverStatus probe failed: %s", type(exc).__name__,
                exc_info=False,
            )
            return {"ok": False, "detail": "serverStatus failed"}
        return {"ok": True, "detail": "ok"}

    # -- query -------------------------------------------------------------

    def query(self, spec: MongoQuerySpec | None = None) -> list[MongoRecord]:
        """Collect metric records. Returns ``[]`` on any executor failure."""
        executor = self._get_executor()
        if executor is None:
            return []

        records: list[MongoRecord] = []
        ts = time.time()

        records.extend(self._server_status_records(executor, ts))
        records.extend(self._current_op_records(executor, ts))
        records.extend(self._repl_status_records(executor, ts))
        return records

    # -- normalization helpers --------------------------------------------

    def _record(
        self,
        ts: float,
        message: str,
        value: float | int | None,
        labels: dict[str, object],
        raw: object,
        status: str = "ok",
    ) -> MongoRecord:
        return {
            "ts": ts,
            "source": self.name,
            "kind": self.KIND,
            "level_or_status": status,
            "message": message,
            "value": value,
            "labels": labels,
            "raw": raw,
        }

    def _server_status_records(self, executor: Executor, ts: float) -> list[MongoRecord]:
        try:
            doc = executor("serverStatus")
        except Exception as exc:
            logger.debug("serverStatus failed: %s", exc)
            return []

        out: list[MongoRecord] = []

        connections = _as_mapping(doc.get("connections"))
        for key in ("current", "available", "active"):
            if key in connections:
                out.append(
                    self._record(
                        ts,
                        f"connections.{key}",
                        _num(connections.get(key)),
                        {"section": "connections", "metric": key},
                        connections,
                    )
                )

        opcounters = _as_mapping(doc.get("opcounters"))
        for key, val in opcounters.items():
            out.append(
                self._record(
                    ts,
                    f"opcounters.{key}",
                    _num(val),
                    {"section": "opcounters", "metric": key},
                    opcounters,
                )
            )

        wired_tiger = _as_mapping(doc.get("wiredTiger"))
        cache = _as_mapping(wired_tiger.get("cache"))
        for key in ("bytes currently in the cache", "maximum bytes configured"):
            if key in cache:
                out.append(
                    self._record(
                        ts,
                        f"wiredTiger.cache.{key}",
                        _num(cache.get(key)),
                        {"section": "wiredTiger", "metric": key},
                        cache,
                    )
                )

        return out

    def _current_op_records(self, executor: Executor, ts: float) -> list[MongoRecord]:
        try:
            doc = executor("currentOp")
        except Exception as exc:
            logger.debug("currentOp failed: %s", exc)
            return []

        inprog = doc.get("inprog")
        active = inprog if isinstance(inprog, list) else []
        return [
            self._record(
                ts,
                "currentOp.active",
                len(active),
                {"section": "currentOp", "metric": "active"},
                {"count": len(active)},
            )
        ]

    def _repl_status_records(self, executor: Executor, ts: float) -> list[MongoRecord]:
        try:
            doc = executor("replSetGetStatus")
        except Exception as exc:
            logger.debug("replSetGetStatus failed: %s", exc)
            return []

        members_raw = doc.get("members")
        if not isinstance(members_raw, list) or not members_raw:
            return []

        # Primary optime is the reference for oplog replication lag.
        primary_ts = _member_optime(members_raw, want_primary=True)
        out: list[MongoRecord] = []
        for member in members_raw:
            name = str(member.get("name", "?"))
            state = str(member.get("stateStr", "?"))
            member_ts = _member_optime([member], want_primary=False)
            lag: float | None = None
            if primary_ts is not None and member_ts is not None:
                lag = max(0.0, primary_ts - member_ts)
            out.append(
                self._record(
                    ts,
                    "replication.oplog_lag_seconds",
                    lag,
                    {"section": "replication", "member": name, "state": state},
                    member,
                    status=state.lower(),
                )
            )
        return out


def _as_mapping(value: object) -> Mapping[str, object]:
    """Narrow ``value`` to ``Mapping[str, object]`` for typed access.

    Preserves the original ``value or {}`` semantics: a falsy value (None,
    empty) collapses to ``{}``; a truthy value is returned as-is, INCLUDING a
    truthy non-mapping (malformed input), which will raise on the subsequent
    ``.get()`` call — matching the pre-refactor crash-on-garbage behavior. The
    ``cast`` is type-only (a runtime no-op); no behavior change.
    """
    return cast("Mapping[str, object]", value or {})


def _num(value: object) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _member_optime(members: list[MongoMember], want_primary: bool) -> float | None:
    """Extract an optime (seconds) from member docs.

    When ``want_primary`` the primary member (``stateStr == 'PRIMARY'``) is used;
    otherwise the first member's optime is returned.
    """
    for member in members:
        if want_primary and str(member.get("stateStr")) != "PRIMARY":
            continue
        date = member.get("optimeDate")
        if isinstance(date, (int, float)):
            return float(date)
        optime = member.get("optime")
        if isinstance(optime, Mapping):
            tstamp = optime.get("ts")
            if isinstance(tstamp, (int, float)):
                return float(tstamp)
        seconds = member.get("optime_seconds")
        if isinstance(seconds, (int, float)):
            return float(seconds)
        if not want_primary:
            return None
    return None
