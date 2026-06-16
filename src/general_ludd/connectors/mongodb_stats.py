"""MongoDB observability connector — a *metrics* source over admin commands.

The connector turns the output of the MongoDB admin commands
``serverStatus()``, ``currentOp()`` and ``replSetGetStatus()`` into normalized
metric records (connections, opcounters, replication oplog lag, WiredTiger
cache).

Design (matches the package contract):

  - The command executor is INJECTABLE via ``executor=``. It is a callable
    ``(command: str) -> Mapping[str, Any]`` that returns the raw document a
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
from typing import Any

logger = logging.getLogger(__name__)

# An executor maps a mongo admin command name to its raw result document.
Executor = Callable[[str], Mapping[str, Any]]

_DRIVER_UNAVAILABLE = "driver unavailable"


class MongoDbStatsSource:
    """Normalize MongoDB admin-command output into metric records."""

    KIND = "metrics"

    def __init__(self, config: Mapping[str, Any] | None = None, executor: Executor | None = None) -> None:
        cfg: dict[str, Any] = dict(config or {})
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
            import pymongo  # type: ignore[import-not-found]  # guarded optional dependency
        except Exception as exc:  # pragma: no cover - exercised via health test with injected error
            self._driver_error = _DRIVER_UNAVAILABLE
            logger.debug("pymongo import failed: %s", exc)
            return None

        client = pymongo.MongoClient(uri)

        def _run(command: str) -> Mapping[str, Any]:
            result = client.admin.command(command)
            return dict(result)

        return _run

    # -- health ------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Probe the source. Never raises."""
        executor = self._get_executor()
        if executor is None:
            return {"ok": False, "detail": self._driver_error or _DRIVER_UNAVAILABLE}
        try:
            executor("serverStatus")
        except Exception as exc:
            return {"ok": False, "detail": f"serverStatus failed: {exc}"}
        return {"ok": True, "detail": "ok"}

    # -- query -------------------------------------------------------------

    def query(self, spec: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """Collect metric records. Returns ``[]`` on any executor failure."""
        executor = self._get_executor()
        if executor is None:
            return []

        records: list[dict[str, Any]] = []
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
        labels: dict[str, Any],
        raw: Any,
        status: str = "ok",
    ) -> dict[str, Any]:
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

    def _server_status_records(self, executor: Executor, ts: float) -> list[dict[str, Any]]:
        try:
            doc = executor("serverStatus")
        except Exception as exc:
            logger.debug("serverStatus failed: %s", exc)
            return []

        out: list[dict[str, Any]] = []

        connections = doc.get("connections") or {}
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

        opcounters = doc.get("opcounters") or {}
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

        cache = ((doc.get("wiredTiger") or {}).get("cache")) or {}
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

    def _current_op_records(self, executor: Executor, ts: float) -> list[dict[str, Any]]:
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

    def _repl_status_records(self, executor: Executor, ts: float) -> list[dict[str, Any]]:
        try:
            doc = executor("replSetGetStatus")
        except Exception as exc:
            logger.debug("replSetGetStatus failed: %s", exc)
            return []

        members = doc.get("members")
        if not isinstance(members, list) or not members:
            return []

        # Primary optime is the reference for oplog replication lag.
        primary_ts = _member_optime(members, want_primary=True)
        out: list[dict[str, Any]] = []
        for member in members:
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


def _num(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _member_optime(members: list[Mapping[str, Any]], want_primary: bool) -> float | None:
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
