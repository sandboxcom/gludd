"""Pytest hooks for append-only, run-isolated progress trace events."""

from __future__ import annotations

import json
import os
import resource
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

DEFAULT_TRACE_LOG = "/tmp/gludd-xdist-progress.log"
_ACTIVE_TRACE_PATH: Path | None = None
_ACTIVE_RUN_ID: str | None = None


def _configured_trace_path() -> Path:
    return Path(os.environ.get("GLUDD_XDIST_TRACE_LOG", DEFAULT_TRACE_LOG))


def _trace_path() -> Path:
    return _ACTIVE_TRACE_PATH or _configured_trace_path()


def _configured_run_id() -> str:
    return os.environ.get("GLUDD_XDIST_TRACE_RUN_ID", "legacy")


def _run_id() -> str:
    return _ACTIVE_RUN_ID or _configured_run_id()


def _worker_id() -> str:
    return os.environ.get("PYTEST_XDIST_WORKER", "controller")


def _loadavg() -> list[float]:
    try:
        return [round(value, 3) for value in os.getloadavg()]
    except OSError:
        return []


def _rss_kb() -> int:
    try:
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (OSError, ValueError):
        return 0


def _disk_free_bytes() -> int:
    try:
        return int(shutil.disk_usage("/tmp").free)
    except OSError:
        return 0


def _is_controller(config: Any) -> bool:
    return getattr(config, "workerinput", None) is None


def write_event(event: str, *, nodeid: str | None = None, extra: dict[str, Any] | None = None) -> None:
    """Append one resource-enriched event under the active observed run ID."""
    path = _trace_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "event": event,
        "run_id": _run_id(),
        "timestamp": time.time(),
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "worker": _worker_id(),
        "nodeid": nodeid,
        "loadavg": _loadavg(),
        "rss_kb": _rss_kb(),
        "disk_free_bytes": _disk_free_bytes(),
    }
    if extra:
        payload.update(extra)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + os.linesep)


def pytest_sessionstart(session: pytest.Session) -> None:
    """Pin run identity and initialize a controller-owned trace file."""
    global _ACTIVE_RUN_ID, _ACTIVE_TRACE_PATH
    config = session.config
    _ACTIVE_TRACE_PATH = _configured_trace_path()
    _ACTIVE_RUN_ID = _configured_run_id()
    path = _trace_path()
    if _is_controller(config) and os.environ.get("GLUDD_XDIST_TRACE_TRUNCATE") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    write_event("RUN_START", extra={"role": "controller" if _is_controller(config) else "worker"})


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Append a terminal event for a controller or worker session."""
    role = "controller" if _is_controller(session.config) else "worker"
    write_event("RUN_FINISH", extra={"exitstatus": exitstatus, "role": role})


def pytest_collection_finish(session: pytest.Session) -> None:
    """Record a bounded collection result without serializing every node ID."""
    write_event("COLLECTION_FINISH", extra={"collected": len(session.items)})


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Any:
    """Bracket each test protocol with durable start and finish events."""
    write_event("START", nodeid=item.nodeid)
    outcome = yield
    extra: dict[str, Any] = {"outcome": "done"}
    if outcome.excinfo is not None:
        exc_type, exc_value, _traceback = outcome.excinfo
        extra = {"outcome": "exception", "exc_type": exc_type.__name__, "exc_message": str(exc_value)}
    write_event("FINISH", nodeid=item.nodeid, extra=extra)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Persist bounded failure detail for each failed pytest phase."""
    if report.failed:
        write_event(
            "REPORT",
            nodeid=report.nodeid,
            extra={
                "when": report.when,
                "outcome": report.outcome,
                "duration": report.duration,
                "longrepr": str(report.longrepr)[:4000],
            },
        )
