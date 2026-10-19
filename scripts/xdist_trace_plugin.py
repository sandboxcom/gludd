"""Pytest hooks for append-only, run-isolated progress trace events."""

from __future__ import annotations

import json
import os
import resource
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

DEFAULT_TRACE_LOG = "/tmp/gludd-xdist-progress.log"
_ACTIVE_TRACE_PATH: Path | None = None
_ACTIVE_RUN_ID: str | None = None


if TYPE_CHECKING:
    import cProfile

_PROFILE_RAW_LIMIT = 16 * 1024 * 1024
_PROFILE_REPORT_LIMIT = 1024 * 1024


class _ProfileState:
    def __init__(self, directory: Path, root_fd: int, directory_fd: int) -> None:
        self.directory = directory
        self.root_fd = root_fd
        self.directory_fd = directory_fd
        self.pid = os.getpid()
        self.profiler: cProfile.Profile | None = None
        self.basetemp: Path | None = None
        self.finished = False
        self.previous_trace = {
            key: os.environ.get(key) for key in (
                "GLUDD_XDIST_TRACE_LOG", "GLUDD_XDIST_TRACE_RUN_ID", "GLUDD_XDIST_TRACE_TRUNCATE",
            )
        }
        self.previous_active = (_ACTIVE_TRACE_PATH, _ACTIVE_RUN_ID)


_PROFILE_STATE: pytest.StashKey[_ProfileState] = pytest.StashKey()


def _profile_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _profile_state(config: pytest.Config) -> _ProfileState | None:
    stash = getattr(config, "stash", None)
    state = stash.get(_PROFILE_STATE, None) if stash is not None else None
    return state if isinstance(state, _ProfileState) else None


def _profile_directory(parent_fd: int, name: str, *, create: bool = False) -> int:
    import stat
    from contextlib import suppress

    if create:
        with suppress(FileExistsError):
            os.mkdir(name, 0o700, dir_fd=parent_fd)
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o022:
            raise ValueError("profile directory is not an owned unwritable-by-others directory")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _new_profile_state() -> _ProfileState:
    import secrets

    root = _profile_root()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    opened: list[int] = []
    try:
        metadata = os.fstat(root_fd)
        cwd = os.stat(".")
        if (metadata.st_dev, metadata.st_ino) != (cwd.st_dev, cwd.st_ino) or metadata.st_uid != os.getuid():
            raise ValueError("profile request must run in its owning checkout")
        parent = root_fd
        for component in (".gate-logs", "test-files-cprofile"):
            parent = _profile_directory(parent, component, create=True)
            opened.append(parent)
        name = f"profile-{os.getpid()}-{secrets.token_hex(8)}"
        os.mkdir(name, 0o700, dir_fd=parent)
        leaf = _profile_directory(parent, name)
        opened.append(leaf)
        state = _ProfileState(root / ".gate-logs" / "test-files-cprofile" / name, root_fd, leaf)
        opened.pop()  # The state now owns the leaf through finalization.
        return state
    except BaseException:
        os.close(root_fd)
        raise
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def _consume_profile_request(config: pytest.Config) -> None:
    request = os.environ.pop("_TEST_FILES_CPROFILE", None)
    if request is None:
        return
    if request != "1" or not _is_controller(config):
        raise pytest.UsageError("pytest-cprofile: invalid one-shot request")
    try:
        state = _new_profile_state()
    except (OSError, ValueError) as exc:
        raise pytest.UsageError(f"pytest-cprofile: unsafe artifact directory: {exc}") from exc
    config.stash[_PROFILE_STATE] = state
    os.environ["GLUDD_XDIST_TRACE_LOG"] = str(state.directory / "trace.jsonl")
    os.environ["GLUDD_XDIST_TRACE_RUN_ID"] = state.directory.name
    os.environ["GLUDD_XDIST_TRACE_TRUNCATE"] = "0"
    config.add_cleanup(lambda: pytest_unconfigure(config))


def pytest_load_initial_conftests(early_config: pytest.Config, parser: pytest.Parser, args: list[str]) -> None:
    """Consume only the one-shot request before conftests can create children."""
    _consume_profile_request(early_config)


def _start_profile(config: pytest.Config) -> None:
    state = _profile_state(config)
    if state is None:
        return
    import cProfile
    import sys

    if state.pid != os.getpid() or not _is_controller(config) or sys.getprofile() is not None:
        raise pytest.UsageError("pytest-cprofile: an active or foreign profiler is not replaceable")
    basetemp = config.getoption("basetemp")
    if not isinstance(basetemp, (str, Path)):
        raise pytest.UsageError("pytest-cprofile: native basetemp is required")
    state.basetemp = Path(basetemp)
    state.profiler = cProfile.Profile()
    state.profiler.enable()


def _profile_route(state: _ProfileState) -> None:
    opened: list[int] = []
    parent = state.root_fd
    try:
        root = os.stat(_profile_root(), follow_symlinks=False)
        pinned = os.fstat(parent)
        if (root.st_dev, root.st_ino) != (pinned.st_dev, pinned.st_ino):
            raise ValueError("owning checkout route changed")
        for component in (".gate-logs", "test-files-cprofile", state.directory.name):
            parent = _profile_directory(parent, component)
            opened.append(parent)
        actual, expected = os.fstat(parent), os.fstat(state.directory_fd)
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError("profile artifact route changed")
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def _profile_bytes(state: _ProfileState, name: str, data: bytes) -> None:
    descriptor = os.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
        dir_fd=state.directory_fd,
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _publish_profile(state: _ProfileState, profiler: cProfile.Profile, exitstatus: int | None) -> None:
    import io
    import pstats
    import stat
    import tempfile

    if state.basetemp is None:
        raise ValueError("profile staging root is missing")
    base = state.basetemp.lstat()
    if not stat.S_ISDIR(base.st_mode) or base.st_uid != os.getuid() or base.st_mode & 0o022:
        raise ValueError("profile staging root is unsafe")
    with tempfile.TemporaryDirectory(prefix=".native-cprofile-", dir=state.basetemp) as staged:
        raw = Path(staged) / "pytest.pstats"
        profiler.dump_stats(str(raw))
        descriptor = os.open(raw, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > _PROFILE_RAW_LIMIT:
                raise ValueError("native profile exceeds the 16 MiB regular-file bound")
            raw_bytes = handle.read(_PROFILE_RAW_LIMIT + 1)
        if len(raw_bytes) > _PROFILE_RAW_LIMIT:
            raise ValueError("native profile grew beyond its bound")
        output = io.StringIO()
        output.write("Process-local profile after pytest session start; child CPU and cold start are unobserved.\n")
        output.write(f"pid={state.pid} pytest_exitstatus={exitstatus if exitstatus is not None else 'unknown'}\n")
        stats = pstats.Stats(profiler, stream=output)
        for label, key in (("CUMULATIVE", pstats.SortKey.CUMULATIVE), ("TIME", pstats.SortKey.TIME)):
            output.write(f"\n{label}: full paths, at most 40 rows; cumulative rows overlap.\n")
            stats.sort_stats(key).print_stats(40)
        report = output.getvalue().encode("utf-8")
        if len(report) > _PROFILE_REPORT_LIMIT:
            raise ValueError("native profile report exceeds the 1 MiB bound")
        _profile_route(state)
        _profile_bytes(state, "pytest.pstats", raw_bytes)
        _profile_bytes(state, "report.txt", report)
        _profile_route(state)
        print(f"\npytest-cprofile: directory={state.directory}", flush=True)
        print(report.decode("utf-8"), end="", flush=True)


def _finish_profile(config: pytest.Config, exitstatus: int | None) -> bool:
    global _ACTIVE_TRACE_PATH, _ACTIVE_RUN_ID
    state = _profile_state(config)
    if state is None or state.finished:
        return True
    state.finished = True
    profiler, state.profiler = state.profiler, None
    try:
        if profiler is not None:
            profiler.disable()
            if state.pid != os.getpid():
                raise ValueError("profile process identity changed")
            _publish_profile(state, profiler, exitstatus)
        return True
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"pytest-cprofile: publication refused: {exc}", flush=True)
        return False
    finally:
        for name in ("directory_fd", "root_fd"):
            descriptor = getattr(state, name)
            if descriptor >= 0:
                os.close(descriptor)
                setattr(state, name, -1)
        for key, previous in state.previous_trace.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        _ACTIVE_TRACE_PATH, _ACTIVE_RUN_ID = state.previous_active


def pytest_unconfigure(config: pytest.Config) -> None:
    """Finalize a started profile once even when session completion is interrupted."""
    if not _finish_profile(config, None):
        raise pytest.UsageError("pytest-cprofile: incomplete diagnostic publication")


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
    _start_profile(config)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Append a terminal event for a controller or worker session."""
    role = "controller" if _is_controller(session.config) else "worker"
    try:
        write_event("RUN_FINISH", extra={"exitstatus": exitstatus, "role": role})
    finally:
        if not _finish_profile(session.config, exitstatus) and exitstatus == 0:
            session.exitstatus = pytest.ExitCode.INTERNAL_ERROR


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
