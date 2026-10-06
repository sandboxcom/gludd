#!/usr/bin/env python3
"""Run every named CI shard in a fresh process and aggregate release coverage."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any, Protocol, TextIO

from coverage import CoverageData
from coverage.exceptions import CoverageException

if TYPE_CHECKING:
    from scripts.ci_named_shard_files import ISOLATED_TESTS, SHARDS, expand_shard
    from scripts.gate_status_attestation import repository_state_id
    from scripts.resource_arbiter import resource_root as project_resource_root
    from scripts.run_ci_shards_parallel import _env_for_shard, _parse_shards
    from scripts.uv_cache_lease import shared_uv_cache_lease
else:
    from ci_named_shard_files import ISOLATED_TESTS, SHARDS, expand_shard
    from gate_status_attestation import repository_state_id
    from resource_arbiter import resource_root as project_resource_root
    from run_ci_shards_parallel import _env_for_shard, _parse_shards
    from uv_cache_lease import shared_uv_cache_lease

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DEFAULT_SHARDS = tuple(SHARDS)
_CANCELLATION_RETURN_CODES = frozenset({128 + int(signal.SIGINT), 128 + int(signal.SIGTERM)})
_COLLECT_ALL_PYTEST_RETURN_CODES = frozenset({1, 2, 5, 6})
ATTESTATION_SCHEMA_VERSION = 3
RELEASE_PYTEST_ARGS = ("-W", "error")
RELEASE_PYTHON_VERSION = "3.11"
RELEASE_PYTHON_IMPLEMENTATION = "cpython"
MAKE_RECURSION_ENV_VARS = (
    "MAKEFLAGS",
    "MFLAGS",
    "MAKELEVEL",
    "MAKEOVERRIDES",
    "GNUMAKEFLAGS",
)
DEFAULT_SHARED_UV_CACHE_ROOT = Path("/tmp/gludd-uv-cache-public-v2")


def _shared_uv_cache_root() -> Path:
    """Resolve the exact host cache protected for the shard lifetime."""
    configured = os.environ.get("UV_CACHE_DIR", "").strip()
    return Path(configured).expanduser().resolve() if configured else DEFAULT_SHARED_UV_CACHE_ROOT


def canonical_json_sha256(payload: object) -> str:
    """Return a stable SHA-256 digest for JSON-compatible evidence."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def execution_policy(pytest_args: list[str]) -> dict[str, object]:
    """Describe the semantic pytest policy shared by local and hosted lanes."""
    return {
        "schema_version": 1,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "python_implementation": sys.implementation.name,
        "pytest_args": list(pytest_args),
        "xdist_workers": 0,
        "max_processes": 1,
        "distribution": "none",
        "max_worker_restart": None,
        "coverage_config": ".coveragerc-greenlet",
    }


def release_execution_policy() -> dict[str, object]:
    """Return the canonical hosted release policy independent of this process."""
    policy = execution_policy(list(RELEASE_PYTEST_ARGS))
    policy["python_version"] = RELEASE_PYTHON_VERSION
    policy["python_implementation"] = RELEASE_PYTHON_IMPLEMENTATION
    return policy


def _owned_test_environment(inherited: dict[str, str]) -> dict[str, str]:
    """Detach owned pytest children from an enclosing Make jobserver."""
    child = inherited.copy()
    for name in MAKE_RECURSION_ENV_VARS:
        child.pop(name, None)
    return child


DISK_HEADROOM_EXIT_CODE = 73
CLEANUP_FAILURE_EXIT_CODE = 74
DEFAULT_MIN_FREE_DISK_BYTES = 2 * 1024**3


class _DiskUsage(Protocol):
    """Minimal disk-usage observation required by the shard preflight."""

    @property
    def free(self) -> int:
        """Return the currently available bytes."""


class _DiskUsageReader(Protocol):
    """Read one filesystem's current disk usage."""

    def __call__(self, path: Path) -> _DiskUsage:
        """Return a disk-usage observation for the path."""


def _owned_terraform_environment(
    inherited: dict[str, str],
    workspace: Path,
) -> dict[str, str]:
    """Bind Terraform provider downloads to one disposable shard cache."""
    child = inherited.copy()
    cache = workspace / "terraform-plugin-cache"
    cache.mkdir(parents=True, exist_ok=True)
    child["TF_PLUGIN_CACHE_DIR"] = str(cache)
    return child


def _read_disk_usage(path: Path) -> _DiskUsage:
    """Adapt the stdlib observation to the injectable disk-usage protocol."""
    return shutil.disk_usage(path)


def _disk_headroom_available(
    path: Path,
    *,
    minimum_free_bytes: int = DEFAULT_MIN_FREE_DISK_BYTES,
    disk_usage: _DiskUsageReader = _read_disk_usage,
    context: str,
) -> bool:
    """Report whether the next owned batch has enough filesystem headroom."""
    try:
        free_bytes = disk_usage(path).free
    except OSError as exc:
        print(
            f"SHARD-DISK-PREFLIGHT status=error context={context} path={path} error={type(exc).__name__}:{exc}",
            flush=True,
        )
        return False
    status = "available" if free_bytes >= minimum_free_bytes else "insufficient"
    print(
        f"SHARD-DISK-PREFLIGHT status={status} context={context} path={path} "
        f"free_bytes={free_bytes} minimum_free_bytes={minimum_free_bytes}",
        flush=True,
    )
    return status == "available"


@dataclass(frozen=True)
class ResourcePaths:
    """External runtime paths owned by one local or hosted shard invocation."""

    root: Path
    coverage_shards: Path
    coverage_json: Path
    coverage_audit: Path
    attestation: Path
    resume: Path | None = None


def _resource_paths() -> ResourcePaths:
    """Resolve mutable shard evidence outside the tested checkout."""
    root = project_resource_root(ROOT) / "ci-shards"
    return ResourcePaths(
        root=root,
        coverage_shards=root / "coverage-fragments",
        coverage_json=root / "coverage.json",
        coverage_audit=root / "coverage-audit.json",
        attestation=root / "attestation.json",
        resume=root / "resume.json",
    )


def _batch_key(shard: str, batch_index: int, files: list[str]) -> str:
    """Return a stable key for one batch of test files."""
    return f"{shard}:batch-{batch_index:03d}:{canonical_json_sha256(files)}"


def _load_resume_state(path: Path) -> dict[str, object]:
    """Load durable per-batch results from a previous interrupted run."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _save_resume_state(
    path: Path,
    state: dict[str, object],
) -> None:
    """Atomically persist per-batch results so a restart can skip passes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(state, sort_keys=True, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _git_output(*arguments: str) -> tuple[int, str]:
    """Return one bounded Git query without mutating repository state."""
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, completed.stdout.strip()


def _repository_identity(*, expected_sha: str | None) -> dict[str, object]:
    """Capture the exact commit and worktree state represented by this run."""
    head_rc, head_sha = _git_output("rev-parse", "HEAD")
    branch_rc, branch = _git_output("branch", "--show-current")
    status_rc, status = _git_output("status", "--porcelain", "--untracked-files=all")
    expected = expected_sha or head_sha
    return {
        "head_sha": head_sha,
        "expected_sha": expected,
        "branch": branch,
        "clean": status_rc == 0 and not status,
        "exact_sha": head_rc == 0 and bool(head_sha) and head_sha == expected,
        "queries_ok": head_rc == branch_rc == status_rc == 0,
    }


def _identity_is_release_eligible(identity: dict[str, object]) -> bool:
    """Return whether an attestation identifies one clean immutable commit."""
    return bool(identity.get("queries_ok", True) and identity.get("clean") and identity.get("exact_sha"))


def _identity_is_execution_eligible(
    identity: dict[str, object],
    *,
    allow_dirty_worktree: bool,
) -> bool:
    """Allow a dirty commit-preflight run without weakening release evidence."""
    return bool(
        identity.get("queries_ok", True)
        and identity.get("exact_sha")
        and (identity.get("clean") or allow_dirty_worktree)
    )


def _worktree_state_id() -> str:
    """Return the content identity used to detect mutation during a dirty gate."""
    return repository_state_id(ROOT, source="worktree")


def _is_cancellation_returncode(returncode: int) -> bool:
    """Return whether a child result represents operator cancellation."""
    return returncode in _CANCELLATION_RETURN_CODES


def _is_collect_all_pytest_returncode(returncode: int) -> bool:
    """Return whether a pytest result is evidence to retain while continuing."""
    return returncode in _COLLECT_ALL_PYTEST_RETURN_CODES


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _write_terminal_attestation(
    destination: Path,
    *,
    identity: dict[str, object],
    shards: list[str],
    returncode: int,
    started_at: str,
    completed_at: str,
    error: str | None = None,
    coverage: dict[str, object] | None = None,
    pytest_args: list[str] | None = None,
    pairing: dict[str, object] | None = None,
) -> None:
    """Atomically publish terminal exact-SHA shard evidence."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    pairing_payload = (
        _attestation_pairing(
            shards,
            pytest_args=list(RELEASE_PYTEST_ARGS) if pytest_args is None else pytest_args,
        )
        if pairing is None
        else pairing
    )
    payload = {
        "schema_version": ATTESTATION_SCHEMA_VERSION,
        "lane": "hosted" if os.environ.get("GITHUB_ACTIONS", "").lower() == "true" else "local",
        "identity": identity,
        "shards": shards,
        "status": "pass" if returncode == 0 else "fail",
        "returncode": returncode,
        "started_at": started_at,
        "completed_at": completed_at,
        "runner": "scripts/run_ci_shards_serial.py",
        "python": sys.version,
        **pairing_payload,
    }
    if error is not None:
        payload["error"] = error
    if coverage is not None:
        payload["coverage"] = coverage
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


_RESOURCE_PATHS = _resource_paths()
COVERAGE_SHARDS = _RESOURCE_PATHS.coverage_shards
COVERAGE_JSON = _RESOURCE_PATHS.coverage_json
COVERAGE_AUDIT = _RESOURCE_PATHS.coverage_audit
GREENLET_COVERAGE_CONFIG = ROOT / ".coveragerc-greenlet"
MAX_FILES_PER_BATCH = 16
DEFAULT_HEARTBEAT_SECONDS = 30.0
DEFAULT_NO_PROGRESS_SECONDS = 10.0 * 60.0
WORKER_DEATH_EXIT_CODE = 70
NO_PROGRESS_EXIT_CODE = 124
RUNNER_EXCEPTION_EXIT_CODE = 125
INTERPRETER_DRIFT_EXIT_CODE = 78
ANSI_ESCAPE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
XDIST_NODE_DOWN_LINE = re.compile(
    r"^\[gw\d+\]\s+node down:\s+\S.*$",
    re.IGNORECASE,
)
XDIST_FATAL_SUMMARY_LINE = re.compile(
    r"^(?:worker gw\d+ crashed and worker restarting disabled|"
    r"maximum crashed workers reached:\s*\d+)$",
    re.IGNORECASE,
)
XDIST_TERMINAL_SUMMARY_LINE = re.compile(
    r"^=+\s+xdist:\s+(?P<message>.+?)\s+=+$",
    re.IGNORECASE,
)
COVERAGE_FRAGMENT_NAME = re.compile(r"^\.coverage\.[A-Za-z0-9_-]+\.batch-\d{3}$")


def _quote(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def _interpreter_identity() -> dict[str, str]:
    """Return the executable identity that a newly launched batch will use."""
    probe = (
        "import json, pathlib, sys; "
        "print(json.dumps({"
        "'implementation': sys.implementation.name, "
        "'version': '.'.join(map(str, sys.version_info[:3])), "
        "'executable': str(pathlib.Path(sys.executable).resolve(strict=True))"
        "}, sort_keys=True))"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "probe failed").strip()
        raise RuntimeError(f"interpreter identity probe failed: {detail}")
    payload = json.loads(completed.stdout)
    if (
        not isinstance(payload, dict)
        or set(payload)
        != {
            "implementation",
            "version",
            "executable",
        }
        or not all(isinstance(value, str) and value for value in payload.values())
    ):
        raise RuntimeError("interpreter identity probe returned malformed evidence")
    return payload


def _interpreter_is_unchanged(
    expected: dict[str, str],
    *,
    context: str,
) -> bool:
    """Fail closed when the shared interpreter path changes during a run."""
    try:
        observed = _interpreter_identity()
    except (OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(
            f"SHARD-INTERPRETER-PROBE-FAIL context={context} error={exc}",
            flush=True,
        )
        return False
    if observed == expected:
        return True
    print(
        "SHARD-INTERPRETER-DRIFT "
        f"context={context} "
        f"expected={json.dumps(expected, sort_keys=True)} "
        f"observed={json.dumps(observed, sort_keys=True)}",
        flush=True,
    )
    return False


def _is_xdist_worker_death_line(line: str) -> bool:
    """Accept only complete xdist controller diagnostics, never payload text."""
    normalized = ANSI_ESCAPE.sub("", line).strip()
    terminal_summary = XDIST_TERMINAL_SUMMARY_LINE.fullmatch(normalized)
    if terminal_summary is not None:
        normalized = terminal_summary.group("message").strip()
    return bool(XDIST_NODE_DOWN_LINE.fullmatch(normalized) or XDIST_FATAL_SUMMARY_LINE.fullmatch(normalized))


def _run_command(command: list[str], *, env: dict[str, str] | None = None) -> int:
    print(f"$ {_quote(command)}", flush=True)
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


def _pytest_command(
    shard: str,
    files: list[str],
    basetemp: Path,
    pytest_args: list[str],
    *,
    watchdog_owned_gate: bool = False,
) -> list[str]:
    # Coverage.py refuses to combine statement-only and branch-aware data.
    # Every batch therefore uses the same branch-aware concurrency config,
    # including shards that do not themselves import greenlet-backed code.
    coverage_config = GREENLET_COVERAGE_CONFIG
    command = [
        sys.executable,
        "-m",
        "pytest",
        *files,
        "--cov",
        f"--cov-config={coverage_config}",
        "--cov-report=",
        "--cov-fail-under=0",
        "-v",
        *pytest_args,
        f"--basetemp={basetemp / 'pytest'}",
    ]
    if watchdog_owned_gate:
        command.extend(
            [
                "--override-ini",
                f"cache_dir={basetemp / 'watchdog-owned-gate' / 'pytest-cache'}",
            ]
        )
    return command


def _owned_socket_safe_tmpdir(label: str) -> Path:
    """Create a compact owned temp root safe for POSIX AF_UNIX endpoints."""
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:4]
    system_tmp = Path("/tmp") if os.name == "posix" else Path(tempfile.gettempdir())
    system_tmp.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"gludd-{digest}-", dir=system_tmp))


def _remove_owned_tree(path: Path, *, context: str) -> int:
    """Remove one caller-owned tree and return a classified cleanup status."""
    try:
        with (
            _defer_termination_signals() as deferred_signals,
            contextlib.suppress(FileNotFoundError),
        ):
            shutil.rmtree(path)
    except OSError as exc:
        print(
            f"SHARD-CLEANUP-FAIL context={context} path={path} "
            f"error={type(exc).__name__}:{exc} rc={CLEANUP_FAILURE_EXIT_CODE}",
            flush=True,
        )
        return CLEANUP_FAILURE_EXIT_CODE

    if deferred_signals:
        returncode = 128 + deferred_signals[0]
        print(
            f"OWNED-TREE-CLEANUP-SIGNAL context={context} path={path} signal={deferred_signals[0]} rc={returncode}",
            flush=True,
        )
        return returncode
    return 0


def _cleanup_owned_tree(path: Path, *, context: str) -> int:
    """Expose semantically named teardown evidence to the ownership scanner."""
    return _remove_owned_tree(path, context=context)


def _cleanup_owned_tmpdir(path: Path) -> int:
    """Remove one socket-safe owned root and return its cleanup status."""
    expected_parent = Path("/tmp") if os.name == "posix" else Path(tempfile.gettempdir())
    resolved = path.resolve()
    if (
        resolved.parent != expected_parent.resolve()
        or re.fullmatch(r"gludd-[0-9a-f]{4}-[a-z0-9_]+", resolved.name) is None
    ):
        raise ValueError(f"refusing to remove unowned shard temp root: {resolved}")
    return _cleanup_owned_tree(resolved, context="owned-tmpdir")


def _cleanup_owned_tmpdir_safely(path: Path, *, context: str) -> int:
    """Convert cleanup ownership and filesystem failures into terminal evidence."""
    try:
        return _cleanup_owned_tmpdir(path) or 0
    except (OSError, ValueError) as exc:
        print(
            f"SHARD-CLEANUP-FAIL context={context} path={path} "
            f"error={type(exc).__name__}:{exc} rc={CLEANUP_FAILURE_EXIT_CODE}",
            flush=True,
        )
        return CLEANUP_FAILURE_EXIT_CODE


@contextlib.contextmanager
def _defer_termination_signals() -> Iterator[list[int]]:
    """Defer SIGINT/SIGTERM through one bounded owner-finalization phase."""
    deferred_signals: list[int] = []
    previous_handlers: dict[
        signal.Signals,
        signal.Handlers | int | Callable[[int, FrameType | None], Any] | None,
    ] = {}

    def defer(signum: int, _frame: FrameType | None) -> None:
        deferred_signals.append(signum)

    if threading.current_thread() is threading.main_thread():
        for watched_signal in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[watched_signal] = signal.getsignal(watched_signal)
            signal.signal(watched_signal, defer)
    try:
        yield deferred_signals
    finally:
        for watched_signal, previous_handler in previous_handlers.items():
            signal.signal(watched_signal, previous_handler)


def _isolated_pytest_command(
    pytest_args: list[str], *, watchdog_owned_gate: bool = False
) -> list[str]:
    """Run process-heavy tests outside the long-lived coverage workers."""
    command = [
        sys.executable,
        "-m",
        "pytest",
        *ISOLATED_TESTS,
        "-v",
        *pytest_args,
    ]
    if watchdog_owned_gate:
        command.extend(
            [
                "--override-ini",
                f"cache_dir={_resource_paths().root / 'watchdog-owned-gate' / 'pytest-cache'}",
            ]
        )
    return command


def _expand_test_paths(paths: list[str], *, root: Path = ROOT) -> list[str]:
    """Expand directory arguments into deterministic unique test-file paths."""
    expanded: list[str] = []
    seen: set[str] = set()
    for value in paths:
        candidate = root / value
        selected: list[str]
        if candidate.is_dir():
            selected = sorted(
                {
                    match.relative_to(root).as_posix()
                    for pattern in ("test_*.py", "*_test.py")
                    for match in candidate.rglob(pattern)
                }
            )
        else:
            selected = [value]
        for selected_path in selected:
            if selected_path in seen:
                continue
            seen.add(selected_path)
            expanded.append(selected_path)
    return expanded


def _partition_test_paths(
    paths: list[str],
    *,
    max_files: int,
    root: Path = ROOT,
) -> list[list[str]]:
    """Expand directory arguments and return deterministic bounded file batches."""
    if max_files < 1:
        raise ValueError("max_files must be positive")

    expanded = _expand_test_paths(paths, root=root)
    return [expanded[index : index + max_files] for index in range(0, len(expanded), max_files)]


def _attestation_pairing(
    shards: list[str],
    *,
    pytest_args: list[str],
) -> dict[str, object]:
    """Build canonical per-shard plans and their shared semantic policy."""
    plans: dict[str, object] = {}
    for shard in shards:
        paths = _expand_test_paths(expand_shard(shard))
        plans[shard] = {
            "paths": paths,
            "path_count": len(paths),
            "sha256": canonical_json_sha256(paths),
        }
    policy = execution_policy(pytest_args)
    return {
        "shard_plans": plans,
        "execution_policy": policy,
        "execution_policy_sha256": canonical_json_sha256(policy),
    }


def _plan_shards(
    shards: list[str],
    *,
    max_files_per_batch: int,
) -> list[tuple[str, list[list[str]]]]:
    """Resolve canonical shard ownership into deterministic bounded batches."""
    if max_files_per_batch < 1:
        raise ValueError("max_files_per_batch must be positive")
    return [
        (
            shard,
            _partition_test_paths(
                expand_shard(shard),
                max_files=max_files_per_batch,
            ),
        )
        for shard in shards
    ]


def _validate_only_plan(
    shards: list[str],
    pytest_args: list[str],
    *,
    max_files_per_batch: int,
    attestation_output: Path | None,
) -> int:
    """Print the side-effect-free canonical execution plan for Make contracts."""
    if not shards:
        print("SERIAL-SHARD-VALIDATE-FAIL empty=<plan>", flush=True)
        return 2
    plans = _plan_shards(shards, max_files_per_batch=max_files_per_batch)
    empty = [shard for shard, batches in plans if not batches]
    if empty:
        print(
            f"SERIAL-SHARD-VALIDATE-FAIL empty={','.join(empty)}",
            flush=True,
        )
        return 2
    destination = attestation_output or _resource_paths().attestation
    print(
        f"SERIAL-SHARD-VALIDATE shards={','.join(shards)} "
        f"files={sum(len(batch) for _, batches in plans for batch in batches)} "
        f"batches={sum(len(batches) for _, batches in plans)} worker=1 "
        f"max_files_per_batch={max_files_per_batch} "
        f"pytest_args={shlex.join(pytest_args) or '<none>'} "
        f"attestation={destination}",
        flush=True,
    )
    return 0


def _signal_owned_process_group(process: subprocess.Popen[str], signum: signal.Signals) -> None:
    """Signal only the process group created for this runner invocation."""
    if os.name == "posix":
        os.killpg(process.pid, signum)
    elif signum == signal.SIGTERM:
        process.terminate()
    else:
        process.kill()


def _owned_process_group_alive(process: subprocess.Popen[str]) -> bool:
    """Return whether this runner's process group still has a live member."""
    if os.name != "posix":
        return process.poll() is None
    try:
        os.killpg(process.pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _try_signal_owned_process_group(process: subprocess.Popen[str], signum: signal.Signals) -> bool:
    """Contain normal process-group disappearance or access races."""
    try:
        _signal_owned_process_group(process, signum)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _terminate_owned_process(process: subprocess.Popen[str], *, grace_seconds: float = 5.0) -> None:
    """Terminate the owned group, then kill survivors after a bounded grace."""
    if _owned_process_group_alive(process):
        _try_signal_owned_process_group(process, signal.SIGTERM)

    deadline = time.monotonic() + max(0.0, grace_seconds)
    descendant_exit_wait = threading.Event()
    while _owned_process_group_alive(process) and time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        wait_seconds = min(0.05, max(0.0, remaining))
        try:
            process.wait(timeout=wait_seconds)
        except subprocess.TimeoutExpired:
            continue
        descendant_exit_wait.wait(wait_seconds)
    if _owned_process_group_alive(process):
        _try_signal_owned_process_group(process, signal.SIGKILL)
    try:
        process.wait(timeout=max(1.0, grace_seconds))
    except subprocess.TimeoutExpired:
        return


def _read_process_output(stream: TextIO, events: queue.Queue[str | None]) -> None:
    """Transfer child output into the owner loop and always publish EOF."""
    try:
        for line in stream:
            events.put(line)
    finally:
        events.put(None)


class _OwnedProcessInterrupted(Exception):
    """Raised by the runner's signal handler so the child group is reaped."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


def _run_owned_pytest(
    command: list[str],
    *,
    env: dict[str, str],
    label: str,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    no_progress_seconds: float = DEFAULT_NO_PROGRESS_SECONDS,
) -> int:
    """Stream one pytest group and fail closed on death, silence, or signals."""
    print(f"$ {_quote(command)}", flush=True)
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    assert process.stdout is not None
    events: queue.Queue[str | None] = queue.Queue()
    reader = threading.Thread(
        target=_read_process_output,
        args=(process.stdout, events),
        name=f"gludd-shard-output-{process.pid}",
    )
    reader.start()
    started = last_output = time.monotonic()
    next_heartbeat = started + max(0.1, heartbeat_seconds)
    forced_returncode: int | None = None
    eof = False
    previous_handlers: dict[
        signal.Signals,
        signal.Handlers | int | Callable[[int, FrameType | None], Any] | None,
    ] = {}

    def interrupt(signum: int, _frame: FrameType | None) -> None:
        raise _OwnedProcessInterrupted(signum)

    if threading.current_thread() is threading.main_thread():
        for watched_signal in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[watched_signal] = signal.getsignal(watched_signal)
            signal.signal(watched_signal, interrupt)

    try:
        while True:
            try:
                line = events.get(timeout=0.1)
            except queue.Empty:
                line = ""
            if line is None:
                eof = True
            elif line:
                sys.stdout.write(line)
                sys.stdout.flush()
                last_output = time.monotonic()
                if forced_returncode is None and _is_xdist_worker_death_line(line):
                    forced_returncode = WORKER_DEATH_EXIT_CODE
                    print(
                        f"WORKER-DEATH label={label} rc={WORKER_DEATH_EXIT_CODE}; restarts=disabled cleanup=TERM->KILL",
                        flush=True,
                    )
                    _terminate_owned_process(process)

            now = time.monotonic()
            if now >= next_heartbeat:
                print(
                    f"SHARD-HEARTBEAT label={label} elapsed={now - started:.0f}s "
                    f"quiet={now - last_output:.0f}s pid={process.pid}",
                    flush=True,
                )
                next_heartbeat = now + max(0.1, heartbeat_seconds)
            if forced_returncode is None and now - last_output >= no_progress_seconds:
                forced_returncode = NO_PROGRESS_EXIT_CODE
                print(
                    f"SHARD-NO-PROGRESS label={label} quiet={now - last_output:.0f}s "
                    f"rc={NO_PROGRESS_EXIT_CODE} cleanup=TERM->KILL",
                    flush=True,
                )
                _terminate_owned_process(process)

            if eof and events.empty() and process.poll() is not None:
                break
    except _OwnedProcessInterrupted as exc:
        forced_returncode = 128 + exc.signum
        print(
            f"SHARD-SIGNAL label={label} signal={exc.signum} cleanup=TERM->KILL",
            flush=True,
        )
    finally:
        if _owned_process_group_alive(process) or reader.is_alive():
            _terminate_owned_process(process)
        reader.join(timeout=1.0)
        process.stdout.close()
        for watched_signal, previous_handler in previous_handlers.items():
            signal.signal(watched_signal, previous_handler)

    returncode = (
        forced_returncode
        if forced_returncode is not None
        else process.returncode
        if process.returncode is not None
        else 1
    )
    print(f"OWNED-PYTEST-RESULT label={label} rc={returncode}", flush=True)
    return returncode


def _file_sha256(path: Path) -> str:
    """Return a bounded-memory SHA-256 digest for one evidence file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _coverage_data_error(path: Path) -> str | None:
    """Return why a coverage database is unsafe to publish, if applicable."""
    if path.is_symlink():
        return "symbolic links are not accepted"
    try:
        stat = path.stat()
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    if not path.is_file() or stat.st_size == 0:
        return "file is missing or empty"

    try:
        before = _file_sha256(path)
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    data = CoverageData(basename=str(path))
    try:
        data.read()
        data.measured_files()
    except (CoverageException, OSError, ValueError) as exc:
        return f"{type(exc).__name__}: {exc}"
    finally:
        data.close()
    try:
        after = _file_sha256(path)
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    if after != before:
        return "validation mutated the coverage database"
    return None


def _resume_skip_batch(
    resume_state: dict[str, object],
    shard: str,
    batch_index: int,
    files: list[str],
    coverage_shards: Path,
    resource_root: Path,
) -> bool:
    """Reuse one previously-passing batch's coverage fragment if it still exists."""
    key = _batch_key(shard, batch_index, files)
    entry = resume_state.get(key)
    if not isinstance(entry, dict):
        return False
    if entry.get("rc") != 0:
        return False
    fragment_name = entry.get("coverage_fragment")
    if not isinstance(fragment_name, str):
        return False
    source = resource_root / fragment_name
    try:
        if not source.is_file() or source.stat().st_size == 0:
            return False
        source_digest = _file_sha256(source)
        destination = coverage_shards / source.name
        if destination.resolve() != source.resolve():
            destination.unlink(missing_ok=True)
            shutil.copy2(source, destination)
        destination_digest = _file_sha256(destination)
        if destination_digest != source_digest:
            destination.unlink(missing_ok=True)
            return False
    except (OSError, ValueError):
        return False
    return True


def _save_shard_coverage(
    shard: str,
    batch_index: int,
    basetemp: Path,
    env: dict[str, str],
) -> bool:
    coverage_file = Path(env["COVERAGE_FILE"])
    worker_fragments = sorted(basetemp.glob(f"{coverage_file.name}.*"))
    if worker_fragments or not coverage_file.is_file():
        # With xdist and ``parallel = True``, pytest-cov can write both a
        # controller data file and suffixed worker files. The controller file
        # existing does not mean the worker data has been combined. Append
        # every owned fragment before publishing the batch artifact.
        combine_rc = _run_command(
            [
                sys.executable,
                "-m",
                "coverage",
                "combine",
                "--append",
                "--keep",
                f"--data-file={coverage_file}",
                str(basetemp),
            ],
            env=env,
        )
        if combine_rc:
            print(
                f"SHARD-COVERAGE-COMBINE-FAIL shard={shard} "
                f"batch={batch_index} fragments={len(worker_fragments)} "
                f"rc={combine_rc}",
                flush=True,
            )
            return False
    try:
        coverage_missing = not coverage_file.is_file() or coverage_file.stat().st_size == 0
    except OSError:
        coverage_missing = True
    if coverage_missing:
        print(
            f"SHARD-COVERAGE-MISSING shard={shard} batch={batch_index}",
            flush=True,
        )
        return False
    coverage_error = _coverage_data_error(coverage_file)
    if coverage_error is not None:
        print(
            f"SHARD-COVERAGE-INVALID shard={shard} batch={batch_index} path={coverage_file} error={coverage_error}",
            flush=True,
        )
        return False

    destination = COVERAGE_SHARDS / f".coverage.{shard}.batch-{batch_index:03d}"
    try:
        source_digest = _file_sha256(coverage_file)
        shutil.copy2(coverage_file, destination)
        destination_error = _coverage_data_error(destination)
        destination_digest = _file_sha256(destination)
        current_source_digest = _file_sha256(coverage_file)
        destination_size = destination.stat().st_size
    except (OSError, ValueError) as exc:
        cleanup_error = ""
        try:
            destination.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            cleanup_error = f" cleanup_error={type(cleanup_exc).__name__}:{cleanup_exc}"
        print(
            f"SHARD-COVERAGE-TRANSFER-FAIL shard={shard} batch={batch_index} "
            f"error={type(exc).__name__}:{exc}{cleanup_error}",
            flush=True,
        )
        return False
    if destination_error is not None or destination_digest != source_digest or current_source_digest != source_digest:
        cleanup_error = ""
        try:
            destination.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            cleanup_error = f" cleanup_error={type(cleanup_exc).__name__}:{cleanup_exc}"
        print(
            f"SHARD-COVERAGE-TRANSFER-MISMATCH shard={shard} batch={batch_index} "
            f"source_sha256={source_digest} destination_sha256={destination_digest} "
            f"error={destination_error or 'digest mismatch'}{cleanup_error}",
            flush=True,
        )
        return False
    print(
        f"SHARD-COVERAGE-SAVED shard={shard} batch={batch_index} bytes={destination_size} sha256={destination_digest}",
        flush=True,
    )
    return True


def _aggregate_coverage() -> int:
    """Run `coverage combine`/`coverage report` and the 75% per-file audit."""
    commands = [
        [
            sys.executable,
            "-m",
            "coverage",
            "combine",
            "--keep",
            str(COVERAGE_SHARDS),
        ],
        [sys.executable, "-m", "coverage", "xml"],
        [sys.executable, "-m", "coverage", "json", "-o", str(COVERAGE_JSON)],
        [
            sys.executable,
            "-m",
            "coverage",
            "report",
            "--skip-covered",
            "--show-missing",
            "--fail-under=85",
        ],
        [
            sys.executable,
            str(SCRIPTS / "audit_coverage.py"),
            f"--json-file={COVERAGE_JSON}",
            "--threshold=85",
            "--per-file-threshold=75",
            "--source=src/general_ludd",
            f"--json-out={COVERAGE_AUDIT}",
        ],
    ]
    result = 0
    for command in commands:
        result = max(result, _run_command(command))
    return result


def _combine_coverage_output(destination: Path) -> int:
    """Combine bounded batch fragments into one uploadable shard data file."""
    if not destination.is_absolute():
        destination = ROOT / destination
    if COVERAGE_SHARDS.is_symlink() or not COVERAGE_SHARDS.is_dir():
        print(
            f"SHARD-COVERAGE-FRAGMENTS-MISSING path={COVERAGE_SHARDS}",
            flush=True,
        )
        return 1
    fragments = sorted(path for path in COVERAGE_SHARDS.iterdir() if COVERAGE_FRAGMENT_NAME.fullmatch(path.name))
    if not fragments:
        print(
            f"SHARD-COVERAGE-FRAGMENTS-MISSING path={COVERAGE_SHARDS}",
            flush=True,
        )
        return 1
    for fragment in fragments:
        fragment_error = _coverage_data_error(fragment)
        if fragment_error is not None:
            print(
                f"SHARD-COVERAGE-FRAGMENT-INVALID path={fragment} error={fragment_error}",
                flush=True,
            )
            return 1

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.unlink(missing_ok=True)
    except OSError as exc:
        print(
            f"SHARD-COVERAGE-OUTPUT-SETUP-FAIL path={destination} error={type(exc).__name__}:{exc}",
            flush=True,
        )
        return 1
    staged: list[Path] = []
    try:
        for index, fragment in enumerate(fragments, start=1):
            alias = COVERAGE_SHARDS / f"{destination.name}.fragment-{index:03d}"
            alias.unlink(missing_ok=True)
            staged.append(alias)
            try:
                source_digest = _file_sha256(fragment)
                shutil.copy2(fragment, alias)
                destination_digest = _file_sha256(alias)
                source_size = fragment.stat().st_size
                alias_size = alias.stat().st_size
                current_source_digest = _file_sha256(fragment)
            except OSError as exc:
                print(
                    f"SHARD-COVERAGE-TRANSFER-FAIL source={fragment} "
                    f"destination={alias} error={type(exc).__name__}:{exc}",
                    flush=True,
                )
                return 1
            if (
                alias_size != source_size
                or destination_digest != source_digest
                or current_source_digest != source_digest
            ):
                print(
                    f"SHARD-COVERAGE-TRANSFER-MISMATCH source={fragment} "
                    f"destination={alias} source_sha256={source_digest} "
                    f"destination_sha256={destination_digest}",
                    flush=True,
                )
                return 1
            print(
                f"SHARD-COVERAGE-TRANSFER source={fragment.name} destination={alias.name} bytes={source_size}",
                flush=True,
            )
        rc = _run_command(
            [
                sys.executable,
                "-m",
                "coverage",
                "combine",
                "--keep",
                f"--data-file={destination}",
                str(COVERAGE_SHARDS),
            ]
        )
        if rc:
            print(
                f"SHARD-COVERAGE-COMBINE-FAIL fragments={len(fragments)} rc={rc}",
                flush=True,
            )
            return rc
        if destination.is_symlink() or not destination.is_file() or destination.stat().st_size == 0:
            print(f"SHARD-COVERAGE-OUTPUT-MISSING path={destination}", flush=True)
            return 1
        coverage_error = _coverage_data_error(destination)
        if coverage_error is not None:
            print(
                f"SHARD-COVERAGE-OUTPUT-INVALID path={destination} error={coverage_error}",
                flush=True,
            )
            return 1
        evidence = _coverage_output_evidence(destination)
        print(
            f"SHARD-COVERAGE-OUTPUT path={destination} "
            f"fragments={len(fragments)} bytes={evidence['bytes']} "
            f"sha256={evidence['sha256']}",
            flush=True,
        )
        return 0
    finally:
        for alias in staged:
            alias.unlink(missing_ok=True)


def _coverage_output_evidence(destination: Path) -> dict[str, object]:
    """Return portable, hash-bound evidence for one durable coverage artifact."""
    if not destination.is_absolute():
        destination = ROOT / destination
    if destination.is_symlink() or not destination.is_file() or destination.stat().st_size == 0:
        raise ValueError(f"coverage output is missing or invalid: {destination}")
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {
        "artifact": destination.name,
        "bytes": destination.stat().st_size,
        "sha256": digest,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def _record_phase_result(
    phase_results: dict[str, int | str],
    phase: str,
    result: int | str,
) -> None:
    """Publish one durable, machine-readable phase result."""
    phase_results[phase] = result
    print(f"SERIAL-SHARD-PHASE phase={phase} result={result}", flush=True)


def _print_serial_summary(
    shards: list[str],
    failures: dict[str, int],
    phase_results: dict[str, int | str],
) -> None:
    """Print one complete terminal summary for every reached runner phase."""
    print(
        f"SERIAL-SHARD-SUMMARY total={len(shards)} "
        f"failed={len(failures)} failures={failures} "
        f"phases={json.dumps(phase_results, sort_keys=True)}",
        flush=True,
    )


def run(
    shards: list[str],
    pytest_args: list[str],
    *,
    max_files_per_batch: int = MAX_FILES_PER_BATCH,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    no_progress_seconds: float = DEFAULT_NO_PROGRESS_SECONDS,
    run_isolated: bool = True,
    aggregate_coverage: bool = True,
    coverage_output: Path | None = None,
    resume_path: Path | None = None,
    watchdog_owned_gate: bool = False,
) -> int:
    """Run bounded batches serially and aggregate their coverage fragments."""
    if max_files_per_batch < 1:
        raise ValueError("max_files_per_batch must be positive")
    phase_results: dict[str, int | str] = {}
    if not shards:
        print("SERIAL-SHARD-PLAN-EMPTY rc=2", flush=True)
        _record_phase_result(phase_results, "plan", 2)
        _print_serial_summary(shards, {"plan": 2}, phase_results)
        return 2
    expected_interpreter = _interpreter_identity()
    resume_state: dict[str, object] = {}
    resume_valid = False
    candidate_sha = _git_output("rev-parse", "HEAD")[1]
    if resume_path is not None:
        resume_state = _load_resume_state(resume_path)
        resume_valid = bool(
            resume_state.get("schema_version") == 1
            and resume_state.get("candidate_sha") == candidate_sha
            and resume_state.get("runner") == "scripts/run_ci_shards_serial.py"
        )
        if resume_valid:
            print(
                f"RESUME-RECOVER candidate_sha={candidate_sha} batches={len(resume_state) - 3}",
                flush=True,
            )
        else:
            resume_state = {
                "schema_version": 1,
                "candidate_sha": candidate_sha,
                "runner": "scripts/run_ci_shards_serial.py",
            }
    reset_rc = 0
    if resume_valid:
        COVERAGE_SHARDS.mkdir(parents=True, exist_ok=True)
        COVERAGE_AUDIT.parent.mkdir(parents=True, exist_ok=True)
        _record_phase_result(phase_results, "coverage:reset", "skipped-resume")
    else:
        reset_rc = _cleanup_owned_tree(COVERAGE_SHARDS, context="coverage:reset")
        _record_phase_result(phase_results, "coverage:reset", reset_rc)
        if reset_rc:
            _print_serial_summary(shards, {"coverage:reset": reset_rc}, phase_results)
            return reset_rc
        try:
            COVERAGE_SHARDS.mkdir(parents=True)
            COVERAGE_AUDIT.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(
                f"SHARD-RESOURCE-SETUP-FAIL error={type(exc).__name__}:{exc} rc={CLEANUP_FAILURE_EXIT_CODE}",
                flush=True,
            )
            _record_phase_result(
                phase_results,
                "coverage:setup",
                CLEANUP_FAILURE_EXIT_CODE,
            )
            cleanup_rc = _cleanup_owned_tree(
                COVERAGE_SHARDS,
                context="coverage:fragments-cleanup",
            )
            _record_phase_result(
                phase_results,
                "coverage:fragments-cleanup",
                cleanup_rc,
            )
            setup_failures = {"coverage:setup": CLEANUP_FAILURE_EXIT_CODE}
            if cleanup_rc:
                setup_failures["coverage:fragments-cleanup"] = cleanup_rc
            _print_serial_summary(
                shards,
                setup_failures,
                phase_results,
            )
            return cleanup_rc or CLEANUP_FAILURE_EXIT_CODE
    erase_rc = _run_command([sys.executable, "-m", "coverage", "erase"])
    _record_phase_result(phase_results, "coverage:erase", erase_rc)
    if erase_rc:
        print(f"COVERAGE-ERASE-FAIL rc={erase_rc}", flush=True)
        cleanup_rc = _cleanup_owned_tree(
            COVERAGE_SHARDS,
            context="coverage:fragments-cleanup",
        )
        _record_phase_result(
            phase_results,
            "coverage:fragments-cleanup",
            cleanup_rc,
        )
        erase_failures = {"coverage:erase": erase_rc}
        if cleanup_rc:
            erase_failures["coverage:fragments-cleanup"] = cleanup_rc
        _print_serial_summary(shards, erase_failures, phase_results)
        return cleanup_rc or erase_rc

    failures: dict[str, int] = {}
    cancellation_rc = 0
    terminal_rc = 0
    if run_isolated:
        isolated_rc = _run_owned_pytest(
            _isolated_pytest_command(
                pytest_args, watchdog_owned_gate=watchdog_owned_gate
            ),
            env=_owned_test_environment(os.environ.copy()),
            label="isolated",
            heartbeat_seconds=heartbeat_seconds,
            no_progress_seconds=no_progress_seconds,
        )
        _record_phase_result(phase_results, "isolated", isolated_rc)
        if isolated_rc:
            failures["isolated"] = isolated_rc
            continuation = (
                "later-shards=continuing"
                if _is_collect_all_pytest_returncode(isolated_rc)
                else "later-shards=not-started"
            )
            print(
                f"ISOLATED-TESTS-FAIL rc={isolated_rc}; {continuation}",
                flush=True,
            )
        else:
            print("ISOLATED-TESTS-PASS rc=0", flush=True)

    if failures and not _is_collect_all_pytest_returncode(failures["isolated"]):
        print(
            f"SERIAL-ISOLATED-FAILED rc={failures['isolated']}; later-shards=not-started",
            flush=True,
        )
        _record_phase_result(phase_results, "coverage", "not-started")
        cleanup_rc = _cleanup_owned_tree(
            COVERAGE_SHARDS,
            context="coverage:fragments-cleanup",
        )
        _record_phase_result(
            phase_results,
            "coverage:fragments-cleanup",
            cleanup_rc,
        )
        if cleanup_rc:
            failures["coverage:fragments-cleanup"] = cleanup_rc
        _print_serial_summary(shards, failures, phase_results)
        if _is_cancellation_returncode(cleanup_rc):
            return cleanup_rc
        return failures["isolated"]

    for index, shard in enumerate(shards, start=1):
        safety_stop_rc = 0
        shard_failure_rc = 0
        batches = _partition_test_paths(
            expand_shard(shard),
            max_files=max_files_per_batch,
        )
        if not batches:
            print(f"SHARD-EMPTY shard={shard}", flush=True)
            failures[shard] = 2
            _record_phase_result(phase_results, f"{shard}:plan", 2)
            shard_failure_rc = 2
            safety_stop_rc = 2
            terminal_rc = 2
            print(
                f"SERIAL-SHARD-FAILED shard={shard} rc=2; later-shards=not-started",
                flush=True,
            )
            break

        workspace_setup_phase = f"{shard}:workspace-setup"
        workspace_parent = _resource_paths().root / "workspaces"
        try:
            workspace_parent.mkdir(parents=True, exist_ok=True)
            workspace = Path(tempfile.mkdtemp(prefix=f"gludd-gate-{shard}-", dir=workspace_parent))
        except OSError as exc:
            print(
                f"SHARD-RESOURCE-SETUP-FAIL phase={workspace_setup_phase} "
                f"error={type(exc).__name__}:{exc} rc={CLEANUP_FAILURE_EXIT_CODE}",
                flush=True,
            )
            failures[workspace_setup_phase] = CLEANUP_FAILURE_EXIT_CODE
            _record_phase_result(
                phase_results,
                workspace_setup_phase,
                CLEANUP_FAILURE_EXIT_CODE,
            )
            terminal_rc = CLEANUP_FAILURE_EXIT_CODE
            break
        _record_phase_result(phase_results, workspace_setup_phase, 0)
        owned_tmpdirs: list[tuple[Path, str]] = []
        cleanup_rc = 0
        try:
            print(
                f"=== GATE TEST SHARD {index}/{len(shards)}: {shard} "
                f"files={sum(len(batch) for batch in batches)} "
                f"batches={len(batches)} max-files={max_files_per_batch} ===",
                flush=True,
            )
            shard_failed = False
            for batch_index, files in enumerate(batches, start=1):
                batch_name = f"{shard}-batch-{batch_index:03d}"
                failure_phase = f"{shard}:batch-{batch_index:03d}"
                batch_setup_phase = f"{failure_phase}:setup"
                if resume_path is not None and _resume_skip_batch(
                    resume_state,
                    shard,
                    batch_index,
                    files,
                    COVERAGE_SHARDS,
                    _RESOURCE_PATHS.root,
                ):
                    _record_phase_result(phase_results, batch_setup_phase, 0)
                    _record_phase_result(phase_results, f"{failure_phase}:tmpdir-setup", 0)
                    _record_phase_result(phase_results, failure_phase, 0)
                    _record_phase_result(phase_results, f"{failure_phase}:coverage", 0)
                    _record_phase_result(phase_results, f"{failure_phase}:cleanup", 0)
                    print(
                        f"RESUME-SKIP shard={shard} batch={batch_index} files={len(files)}",
                        flush=True,
                    )
                    continue
                batchtemp = workspace / f"batch-{batch_index:03d}"
                try:
                    batchtemp.mkdir(parents=True)
                except OSError as exc:
                    print(
                        f"SHARD-RESOURCE-SETUP-FAIL phase={batch_setup_phase} "
                        f"error={type(exc).__name__}:{exc} "
                        f"rc={CLEANUP_FAILURE_EXIT_CODE}",
                        flush=True,
                    )
                    failures[batch_setup_phase] = CLEANUP_FAILURE_EXIT_CODE
                    _record_phase_result(
                        phase_results,
                        batch_setup_phase,
                        CLEANUP_FAILURE_EXIT_CODE,
                    )
                    _record_phase_result(
                        phase_results,
                        f"{failure_phase}:coverage",
                        "not-started",
                    )
                    _record_phase_result(
                        phase_results,
                        f"{failure_phase}:cleanup",
                        "not-started",
                    )
                    shard_failure_rc = CLEANUP_FAILURE_EXIT_CODE
                    safety_stop_rc = CLEANUP_FAILURE_EXIT_CODE
                    shard_failed = True
                    break
                _record_phase_result(phase_results, batch_setup_phase, 0)
                env = _owned_terraform_environment(
                    _env_for_shard(batch_name, batchtemp),
                    workspace,
                )
                if not _disk_headroom_available(
                    workspace,
                    context=f"{shard}:batch-{batch_index:03d}:before",
                ):
                    _record_phase_result(
                        phase_results,
                        failure_phase,
                        DISK_HEADROOM_EXIT_CODE,
                    )
                    _record_phase_result(
                        phase_results,
                        f"{failure_phase}:coverage",
                        "not-started",
                    )
                    _record_phase_result(
                        phase_results,
                        f"{failure_phase}:cleanup",
                        "not-started",
                    )
                    failures[failure_phase] = DISK_HEADROOM_EXIT_CODE
                    shard_failure_rc = max(
                        shard_failure_rc,
                        DISK_HEADROOM_EXIT_CODE,
                    )
                    safety_stop_rc = DISK_HEADROOM_EXIT_CODE
                    print(
                        f"SHARD-DISK-FAIL shard={shard} batch={batch_index} "
                        f"rc={DISK_HEADROOM_EXIT_CODE}; later-batches=not-started",
                        flush=True,
                    )
                    shard_failed = True
                    break
                tmpdir_setup_phase = f"{failure_phase}:tmpdir-setup"
                try:
                    owned_tmpdir = _owned_socket_safe_tmpdir(batch_name)
                except OSError as exc:
                    print(
                        f"SHARD-RESOURCE-SETUP-FAIL phase={tmpdir_setup_phase} "
                        f"error={type(exc).__name__}:{exc} "
                        f"rc={CLEANUP_FAILURE_EXIT_CODE}",
                        flush=True,
                    )
                    failures[tmpdir_setup_phase] = CLEANUP_FAILURE_EXIT_CODE
                    _record_phase_result(
                        phase_results,
                        tmpdir_setup_phase,
                        CLEANUP_FAILURE_EXIT_CODE,
                    )
                    _record_phase_result(
                        phase_results,
                        f"{failure_phase}:coverage",
                        "not-started",
                    )
                    _record_phase_result(
                        phase_results,
                        f"{failure_phase}:cleanup",
                        "not-started",
                    )
                    shard_failure_rc = CLEANUP_FAILURE_EXIT_CODE
                    safety_stop_rc = CLEANUP_FAILURE_EXIT_CODE
                    shard_failed = True
                    break
                _record_phase_result(phase_results, tmpdir_setup_phase, 0)
                owned_entry = (owned_tmpdir, failure_phase)
                owned_tmpdirs.append(owned_entry)
                env["TMPDIR"] = str(owned_tmpdir)
                env["COVERAGE_FILE"] = str(batchtemp / ".coverage")
                print(
                    f"SHARD-BATCH shard={shard} batch={batch_index}/{len(batches)} "
                    f"files={len(files)} basetemp={owned_tmpdir / 'pytest'}",
                    flush=True,
                )
                if not _interpreter_is_unchanged(
                    expected_interpreter,
                    context=f"{shard}:batch-{batch_index:03d}:before",
                ):
                    _record_phase_result(
                        phase_results,
                        failure_phase,
                        INTERPRETER_DRIFT_EXIT_CODE,
                    )
                    _record_phase_result(
                        phase_results,
                        f"{failure_phase}:coverage",
                        "not-started",
                    )
                    failures[failure_phase] = INTERPRETER_DRIFT_EXIT_CODE
                    shard_failure_rc = max(
                        shard_failure_rc,
                        INTERPRETER_DRIFT_EXIT_CODE,
                    )
                    safety_stop_rc = INTERPRETER_DRIFT_EXIT_CODE
                    shard_failed = True
                    break
                rc = _run_owned_pytest(
                    _pytest_command(
                        shard,
                        files,
                        owned_tmpdir,
                        pytest_args,
                        watchdog_owned_gate=watchdog_owned_gate,
                    ),
                    env=_owned_test_environment(env),
                    label=f"{shard}:batch-{batch_index:03d}",
                    heartbeat_seconds=heartbeat_seconds,
                    no_progress_seconds=no_progress_seconds,
                )
                if not _interpreter_is_unchanged(
                    expected_interpreter,
                    context=f"{shard}:batch-{batch_index:03d}:after",
                ):
                    rc = INTERPRETER_DRIFT_EXIT_CODE
                _record_phase_result(phase_results, failure_phase, rc)
                coverage_saved = _save_shard_coverage(
                    shard,
                    batch_index,
                    batchtemp,
                    env,
                )
                _record_phase_result(
                    phase_results,
                    f"{failure_phase}:coverage",
                    0 if coverage_saved else 1,
                )
                if resume_path is not None and rc == 0 and coverage_saved:
                    fragment_name = f".coverage.{shard}.batch-{batch_index:03d}"
                    resume_state[_batch_key(shard, batch_index, files)] = {
                        "rc": 0,
                        "coverage_fragment": f"coverage-fragments/{fragment_name}",
                    }
                    _save_resume_state(resume_path, resume_state)
                batch_cleanup_rc = _cleanup_owned_tmpdir_safely(
                    owned_tmpdir,
                    context=f"{failure_phase}:cleanup",
                )
                _record_phase_result(
                    phase_results,
                    f"{failure_phase}:cleanup",
                    batch_cleanup_rc,
                )
                if not owned_tmpdir.exists():
                    owned_tmpdirs.remove(owned_entry)
                cleanup_rc = max(cleanup_rc, batch_cleanup_rc)
                if rc != 0:
                    failures[failure_phase] = rc
                    shard_failure_rc = max(shard_failure_rc, rc)
                    if _is_cancellation_returncode(rc):
                        cancellation_rc = rc
                if batch_cleanup_rc:
                    print(
                        f"SHARD-CLEANUP-SIGNAL shard={shard} batch={batch_index} rc={batch_cleanup_rc}",
                        flush=True,
                    )
                    shard_failed = True
                    if not cancellation_rc and _is_cancellation_returncode(batch_cleanup_rc):
                        cancellation_rc = batch_cleanup_rc
                if cancellation_rc:
                    if rc != 0:
                        print(
                            f"SHARD-FAIL shard={shard} batch={batch_index} rc={rc}; later-batches=not-started",
                            flush=True,
                        )
                    shard_failed = True
                    safety_stop_rc = cancellation_rc
                    break
                if batch_cleanup_rc:
                    safety_stop_rc = rc if rc != 0 and not _is_collect_all_pytest_returncode(rc) else batch_cleanup_rc
                    break
                if not coverage_saved:
                    failures[f"{failure_phase}:coverage"] = 1
                    shard_failure_rc = max(shard_failure_rc, 1)
                    safety_stop_rc = rc or 1
                    print(
                        f"SHARD-COVERAGE-INTEGRITY-FAIL shard={shard} "
                        f"batch={batch_index} rc=1; later-batches=not-started",
                        flush=True,
                    )
                    shard_failed = True
                    break
                if rc != 0:
                    if _is_collect_all_pytest_returncode(rc) and batch_cleanup_rc == 0:
                        print(
                            f"SHARD-FAIL shard={shard} batch={batch_index} rc={rc}; later-batches=continuing",
                            flush=True,
                        )
                        shard_failed = True
                        continue
                    print(
                        f"SHARD-FAIL shard={shard} batch={batch_index} rc={rc}; later-batches=not-started",
                        flush=True,
                    )
                    shard_failed = True
                    safety_stop_rc = batch_cleanup_rc if _is_collect_all_pytest_returncode(rc) else rc
                    break
                print(
                    f"SHARD-BATCH-PASS shard={shard} batch={batch_index} rc=0",
                    flush=True,
                )
            if not shard_failed:
                print(f"SHARD-PASS shard={shard} rc=0", flush=True)
        finally:
            for owned_tmpdir, cleanup_phase in owned_tmpdirs:
                deferred_cleanup_rc = _cleanup_owned_tmpdir_safely(
                    owned_tmpdir,
                    context=f"{cleanup_phase}:cleanup",
                )
                cleanup_rc = max(cleanup_rc, deferred_cleanup_rc)
                cleanup_result_phase = f"{cleanup_phase}:cleanup"
                if cleanup_result_phase in phase_results:
                    cleanup_result_phase = f"{cleanup_result_phase}:retry"
                _record_phase_result(
                    phase_results,
                    cleanup_result_phase,
                    deferred_cleanup_rc,
                )
            workspace_cleanup_rc = _cleanup_owned_tree(
                workspace,
                context=f"{shard}:workspace-cleanup",
            )
            cleanup_rc = max(cleanup_rc, workspace_cleanup_rc)
            _record_phase_result(
                phase_results,
                f"{shard}:workspace-cleanup",
                workspace_cleanup_rc,
            )
        if cleanup_rc:
            failures[f"{shard}:cleanup"] = cleanup_rc
            shard_failure_rc = max(shard_failure_rc, cleanup_rc)
            print(
                f"SHARD-CLEANUP-SIGNAL shard={shard} rc={cleanup_rc}",
                flush=True,
            )
            if _is_cancellation_returncode(cleanup_rc):
                cancellation_rc = cancellation_rc or cleanup_rc
            if not safety_stop_rc:
                safety_stop_rc = cleanup_rc
        if cancellation_rc:
            terminal_rc = cancellation_rc
            print(
                f"SERIAL-SHARD-CANCELLED shard={shard} rc={cancellation_rc}; later-shards=not-started",
                flush=True,
            )
            break
        if safety_stop_rc:
            terminal_rc = safety_stop_rc
            print(
                f"SERIAL-SHARD-FAILED shard={shard} rc={safety_stop_rc}; later-shards=not-started",
                flush=True,
            )
            break
        if shard_failure_rc:
            print(
                f"SERIAL-SHARD-COLLECTED shard={shard} rc={shard_failure_rc}; later-shards=continuing",
                flush=True,
            )

    coverage_phase: str | None = None
    if coverage_output is not None:
        coverage_phase = "coverage:combine"
    elif aggregate_coverage:
        coverage_phase = "coverage:aggregate"
    if terminal_rc:
        coverage_rc = 0
        if coverage_phase is not None:
            _record_phase_result(phase_results, coverage_phase, "not-started")
    elif coverage_output is not None:
        coverage_rc = _combine_coverage_output(coverage_output)
        _record_phase_result(phase_results, "coverage:combine", coverage_rc)
    elif aggregate_coverage:
        coverage_rc = _aggregate_coverage()
        _record_phase_result(phase_results, "coverage:aggregate", coverage_rc)
    else:
        coverage_rc = 0
    if coverage_rc:
        failures["coverage"] = coverage_rc
    fragments_cleanup_rc = _cleanup_owned_tree(
        COVERAGE_SHARDS,
        context="coverage:fragments-cleanup",
    )
    _record_phase_result(
        phase_results,
        "coverage:fragments-cleanup",
        fragments_cleanup_rc,
    )
    if fragments_cleanup_rc:
        failures["coverage:fragments-cleanup"] = fragments_cleanup_rc
        if _is_cancellation_returncode(fragments_cleanup_rc) or not terminal_rc:
            terminal_rc = fragments_cleanup_rc
    _print_serial_summary(shards, failures, phase_results)
    return terminal_rc or max(failures.values(), default=0)


def main() -> int:
    """Parse command-line options and execute the bounded shard plan."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--shards",
        default=" ".join(DEFAULT_SHARDS),
        help="space or comma separated shard names",
    )
    parser.add_argument("--pytest-args", default="", help="extra pytest arguments")
    parser.add_argument(
        "--max-files-per-batch",
        type=int,
        default=MAX_FILES_PER_BATCH,
        help="maximum collected test files in one owned pytest process",
    )
    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=DEFAULT_HEARTBEAT_SECONDS,
        help="visible owned-process heartbeat interval",
    )
    parser.add_argument(
        "--no-progress-seconds",
        type=float,
        default=DEFAULT_NO_PROGRESS_SECONDS,
        help="quiet-output deadline before owned TERM-to-KILL cleanup",
    )
    parser.add_argument(
        "--skip-isolated",
        action="store_true",
        help="skip the separately scheduled process-heavy test",
    )
    parser.add_argument(
        "--skip-aggregate",
        action="store_true",
        help="defer the 85/75 aggregate coverage gate to a downstream job",
    )
    parser.add_argument(
        "--coverage-output",
        type=Path,
        help="combine this invocation's batch coverage into one data file",
    )
    parser.add_argument(
        "--attestation-output",
        type=Path,
        help="also publish the terminal exact-SHA attestation at this path",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="print the bounded canonical plan without executing tests or writing evidence",
    )
    parser.add_argument(
        "--require-release-policy",
        action="store_true",
        help="reject noncanonical pytest policy before release-attestation work",
    )
    parser.add_argument(
        "--allow-dirty-worktree",
        action="store_true",
        help=(
            "permit a stable dirty worktree for commit-preflight testing; "
            "the resulting attestation remains ineligible for release"
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "skip batches that already passed in a previous run for the same "
            "candidate SHA and reuse their coverage fragments"
        ),
    )
    parser.add_argument(
        "--resume-file",
        type=Path,
        default=None,
        help="path to the resume state file (default: <resource-root>/ci-shards/resume.json)",
    )
    parser.add_argument(
        "--watchdog-owned-gate",
        action="store_true",
        help="mark the gate runner and pytest children for legacy watchdog exclusion",
    )
    args = parser.parse_args()
    if args.allow_dirty_worktree and args.require_release_policy:
        print(
            "SERIAL-SHARD-POLICY-REJECTED dirty worktrees cannot produce release evidence",
            flush=True,
        )
        return 2
    shards = _parse_shards(args.shards)
    pytest_args = shlex.split(args.pytest_args)
    observed_policy = execution_policy(pytest_args)
    expected_policy = release_execution_policy()
    if args.require_release_policy and observed_policy != expected_policy:
        print(
            "SERIAL-SHARD-POLICY-REJECTED "
            f"expected={shlex.join(RELEASE_PYTEST_ARGS)} "
            f"observed={shlex.join(pytest_args) or '<none>'} "
            f"expected_policy={json.dumps(expected_policy, sort_keys=True)} "
            f"observed_policy={json.dumps(observed_policy, sort_keys=True)}",
            flush=True,
        )
        return 2
    if args.validate_only:
        return _validate_only_plan(
            shards,
            pytest_args,
            max_files_per_batch=args.max_files_per_batch,
            attestation_output=args.attestation_output,
        )
    resume_path: Path | None = None
    if args.resume:
        resume_path = args.resume_file or _resource_paths().resume
    pairing = _attestation_pairing(shards, pytest_args=pytest_args)
    started_at = _utc_now()
    identity = _repository_identity(expected_sha=os.environ.get("GLUDD_CANDIDATE_SHA") or os.environ.get("GITHUB_SHA"))
    if _identity_is_execution_eligible(
        identity,
        allow_dirty_worktree=args.allow_dirty_worktree,
    ):
        try:
            initial_worktree_state = None
            if args.allow_dirty_worktree:
                initial_worktree_state = _worktree_state_id()
                identity["worktree_state_id"] = initial_worktree_state
            with shared_uv_cache_lease(
                _shared_uv_cache_root(),
                owner_root=ROOT,
            ) as cache_lease:
                if not cache_lease.acquired:
                    raise RuntimeError("shared uv cache lease acquisition timed out")
                returncode = run(
                    shards,
                    pytest_args,
                    max_files_per_batch=args.max_files_per_batch,
                    heartbeat_seconds=args.heartbeat_seconds,
                    no_progress_seconds=args.no_progress_seconds,
                    run_isolated=not args.skip_isolated,
                    aggregate_coverage=not args.skip_aggregate,
                    coverage_output=args.coverage_output,
                    resume_path=resume_path,
                    watchdog_owned_gate=args.watchdog_owned_gate,
                )
            error = None
            if (
                returncode == 0
                and initial_worktree_state is not None
                and _worktree_state_id() != initial_worktree_state
            ):
                returncode = RUNNER_EXCEPTION_EXIT_CODE
                error = "repository state changed during dirty gate"
                print(f"SHARD-WORKTREE-MUTATED error={error}", flush=True)
        except Exception as exc:
            returncode = RUNNER_EXCEPTION_EXIT_CODE
            error = f"{type(exc).__name__}: {exc}"
            print(f"SHARD-RUNNER-EXCEPTION error={error}", flush=True)
    else:
        returncode = 2
        error = "repository identity is not release eligible"
        print(f"SHARD-IDENTITY-REJECTED identity={identity}", flush=True)
    coverage_evidence: dict[str, object] | None = None
    if returncode == 0 and args.coverage_output is not None:
        try:
            coverage_evidence = _coverage_output_evidence(args.coverage_output)
        except (OSError, ValueError) as exc:
            returncode = RUNNER_EXCEPTION_EXIT_CODE
            error = f"{type(exc).__name__}: {exc}"
            print(f"SHARD-COVERAGE-ATTESTATION-FAIL error={error}", flush=True)
    destinations = {_resource_paths().attestation}
    if args.attestation_output is not None:
        destinations.add(args.attestation_output)

    def publish_terminal() -> None:
        for destination in destinations:
            _write_terminal_attestation(
                destination,
                identity=identity,
                shards=shards,
                returncode=returncode,
                started_at=started_at,
                completed_at=_utc_now(),
                error=error,
                coverage=coverage_evidence,
                pytest_args=pytest_args,
                pairing=pairing,
            )

    with _defer_termination_signals() as deferred_signals:
        publish_terminal()
    if deferred_signals:
        signal_returncode = 128 + deferred_signals[0]
        if not _is_cancellation_returncode(returncode):
            returncode = signal_returncode
            error = f"signal {deferred_signals[0]} received during terminal attestation"
            with _defer_termination_signals():
                publish_terminal()
        print(
            f"TERMINAL-ATTESTATION-SIGNAL signal={deferred_signals[0]} rc={returncode}",
            flush=True,
        )
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
