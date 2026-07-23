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


def _configured_trace_path() -> Path:
    return Path(os.environ.get("GLUDD_XDIST_TRACE_LOG", DEFAULT_TRACE_LOG))


def _trace_path() -> Path:
    return _ACTIVE_TRACE_PATH or _configured_trace_path()


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
    path = _trace_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "event": event,
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
    global _ACTIVE_TRACE_PATH
    config = session.config
    _ACTIVE_TRACE_PATH = _configured_trace_path()
    path = _trace_path()
    if _is_controller(config) and os.environ.get("GLUDD_XDIST_TRACE_TRUNCATE") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    write_event("RUN_START", extra={"role": "controller" if _is_controller(config) else "worker"})


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    role = "controller" if _is_controller(session.config) else "worker"
    write_event("RUN_FINISH", extra={"exitstatus": exitstatus, "role": role})


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Any:
    write_event("START", nodeid=item.nodeid)
    outcome = yield
    extra: dict[str, Any] = {"outcome": "done"}
    if outcome.excinfo is not None:
        exc_type, exc_value, _traceback = outcome.excinfo
        extra = {"outcome": "exception", "exc_type": exc_type.__name__, "exc_message": str(exc_value)}
    write_event("FINISH", nodeid=item.nodeid, extra=extra)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
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
