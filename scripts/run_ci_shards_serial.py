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
from typing import TYPE_CHECKING, Any, TextIO

if TYPE_CHECKING:
    from scripts.ci_named_shard_files import ISOLATED_TESTS, SHARDS, expand_shard
    from scripts.resource_arbiter import resource_root as project_resource_root
    from scripts.run_ci_shards_parallel import _env_for_shard, _parse_shards
else:
    from ci_named_shard_files import ISOLATED_TESTS, SHARDS, expand_shard
    from resource_arbiter import resource_root as project_resource_root
    from run_ci_shards_parallel import _env_for_shard, _parse_shards

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DEFAULT_SHARDS = tuple(SHARDS)
_CANCELLATION_RETURN_CODES = frozenset(
    {128 + int(signal.SIGINT), 128 + int(signal.SIGTERM)}
)
ATTESTATION_SCHEMA_VERSION = 3
RELEASE_PYTEST_ARGS = ("-W", "error")


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
        "xdist_workers": 1,
        "max_processes": 1,
        "distribution": "loadgroup",
        "max_worker_restart": 0,
        "coverage_config": ".coveragerc-greenlet",
    }


@dataclass(frozen=True)
class ResourcePaths:
    """External runtime paths owned by one local or hosted shard invocation."""

    root: Path
    coverage_shards: Path
    coverage_json: Path
    coverage_audit: Path
    attestation: Path


def _resource_paths() -> ResourcePaths:
    """Resolve mutable shard evidence outside the tested checkout."""
    root = project_resource_root(ROOT) / "ci-shards"
    return ResourcePaths(
        root=root,
        coverage_shards=root / "coverage-fragments",
        coverage_json=root / "coverage.json",
        coverage_audit=root / "coverage-audit.json",
        attestation=root / "attestation.json",
    )


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
    return bool(
        identity.get("queries_ok", True)
        and identity.get("clean")
        and identity.get("exact_sha")
    )


def _is_cancellation_returncode(returncode: int) -> bool:
    """Return whether a child result represents operator cancellation."""
    return returncode in _CANCELLATION_RETURN_CODES


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
        "lane": "hosted"
        if os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
        else "local",
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
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
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
GOVERNANCE_MODULE_UTILS = (
    "collections/ansible_collections/general_ludd/governance/plugins/module_utils"
)
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
COVERAGE_FRAGMENT_NAME = re.compile(
    r"^\.coverage\.[A-Za-z0-9_-]+\.batch-\d{3}$"
)


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
    if not isinstance(payload, dict) or set(payload) != {
        "implementation",
        "version",
        "executable",
    } or not all(isinstance(value, str) and value for value in payload.values()):
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
    return bool(
        XDIST_NODE_DOWN_LINE.fullmatch(normalized)
        or XDIST_FATAL_SUMMARY_LINE.fullmatch(normalized)
    )


def _run_command(command: list[str], *, env: dict[str, str] | None = None) -> int:
    print(f"$ {_quote(command)}", flush=True)
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


def _pytest_command(
    shard: str,
    files: list[str],
    basetemp: Path,
    pytest_args: list[str],
) -> list[str]:
    # Coverage.py refuses to combine statement-only and branch-aware data.
    # Every batch therefore uses the same branch-aware concurrency config,
    # including shards that do not themselves import greenlet-backed code.
    coverage_config = GREENLET_COVERAGE_CONFIG
    return [
        sys.executable,
        "-m",
        "pytest",
        *files,
        "--cov=general_ludd",
        f"--cov={GOVERNANCE_MODULE_UTILS}",
        f"--cov-config={coverage_config}",
        "--cov-report=",
        "--cov-fail-under=0",
        "-v",
        *pytest_args,
        "-n",
        "1",
        "--maxprocesses",
        "1",
        "--dist",
        "loadgroup",
        "--max-worker-restart=0",
        f"--basetemp={basetemp / 'pytest'}",
    ]


def _owned_socket_safe_tmpdir(label: str) -> Path:
    """Create a compact owned temp root safe for POSIX AF_UNIX endpoints."""
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:4]
    system_tmp = Path("/tmp") if os.name == "posix" else Path(tempfile.gettempdir())
    system_tmp.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"gludd-{digest}-", dir=system_tmp))


def _cleanup_owned_tmpdir(path: Path) -> int:
    """Remove one owned root and return a deferred cancellation status."""
    expected_parent = Path("/tmp") if os.name == "posix" else Path(tempfile.gettempdir())
    resolved = path.resolve()
    if resolved.parent != expected_parent.resolve() or re.fullmatch(
        r"gludd-[0-9a-f]{4}-[a-z0-9_]+", resolved.name
    ) is None:
        raise ValueError(f"refusing to remove unowned shard temp root: {resolved}")

    with (
        _defer_termination_signals() as deferred_signals,
        contextlib.suppress(FileNotFoundError),
    ):
        shutil.rmtree(resolved)

    if deferred_signals:
        returncode = 128 + deferred_signals[0]
        print(
            f"OWNED-TMPDIR-CLEANUP-SIGNAL path={resolved} "
            f"signal={deferred_signals[0]} rc={returncode}",
            flush=True,
        )
        return returncode
    return 0


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


def _isolated_pytest_command(pytest_args: list[str]) -> list[str]:
    """Run process-heavy tests outside the long-lived coverage workers."""
    return [
        sys.executable,
        "-m",
        "pytest",
        *ISOLATED_TESTS,
        "-v",
        *pytest_args,
    ]


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
    return [
        expanded[index : index + max_files]
        for index in range(0, len(expanded), max_files)
    ]


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


def _signal_owned_process_group(
    process: subprocess.Popen[str], signum: signal.Signals
) -> None:
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


def _try_signal_owned_process_group(
    process: subprocess.Popen[str], signum: signal.Signals
) -> bool:
    """Contain normal process-group disappearance or access races."""
    try:
        _signal_owned_process_group(process, signum)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _terminate_owned_process(
    process: subprocess.Popen[str], *, grace_seconds: float = 5.0
) -> None:
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
                        f"WORKER-DEATH label={label} rc={WORKER_DEATH_EXIT_CODE}; "
                        "restarts=disabled cleanup=TERM->KILL",
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
            f"SHARD-SIGNAL label={label} signal={exc.signum} "
            "cleanup=TERM->KILL",
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
        else process.returncode if process.returncode is not None else 1
    )
    print(f"OWNED-PYTEST-RESULT label={label} rc={returncode}", flush=True)
    return returncode


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
    if not coverage_file.is_file() or coverage_file.stat().st_size == 0:
        print(
            f"SHARD-COVERAGE-MISSING shard={shard} batch={batch_index}",
            flush=True,
        )
        return False

    destination = COVERAGE_SHARDS / f".coverage.{shard}.batch-{batch_index:03d}"
    shutil.copy2(coverage_file, destination)
    print(
        f"SHARD-COVERAGE-SAVED shard={shard} batch={batch_index} "
        f"bytes={destination.stat().st_size}",
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
            "--threshold=75",
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
    fragments = sorted(
        path
        for path in COVERAGE_SHARDS.iterdir()
        if COVERAGE_FRAGMENT_NAME.fullmatch(path.name)
    )
    if not fragments:
        print(
            f"SHARD-COVERAGE-FRAGMENTS-MISSING path={COVERAGE_SHARDS}",
            flush=True,
        )
        return 1
    for fragment in fragments:
        if fragment.is_symlink() or not fragment.is_file() or fragment.stat().st_size == 0:
            print(f"SHARD-COVERAGE-FRAGMENT-INVALID path={fragment}", flush=True)
            return 1

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    staged: list[Path] = []
    try:
        for index, fragment in enumerate(fragments, start=1):
            alias = COVERAGE_SHARDS / f"{destination.name}.fragment-{index:03d}"
            alias.unlink(missing_ok=True)
            staged.append(alias)
            shutil.copy2(fragment, alias)
            source_size = fragment.stat().st_size
            if alias.stat().st_size != source_size:
                print(
                    f"SHARD-COVERAGE-TRANSFER-MISMATCH source={fragment} "
                    f"destination={alias}",
                    flush=True,
                )
                return 1
            print(
                f"SHARD-COVERAGE-TRANSFER source={fragment.name} "
                f"destination={alias.name} bytes={source_size}",
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
        if (
            destination.is_symlink()
            or not destination.is_file()
            or destination.stat().st_size == 0
        ):
            print(f"SHARD-COVERAGE-OUTPUT-MISSING path={destination}", flush=True)
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
    if (
        destination.is_symlink()
        or not destination.is_file()
        or destination.stat().st_size == 0
    ):
        raise ValueError(f"coverage output is missing or invalid: {destination}")
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {
        "artifact": destination.name,
        "bytes": destination.stat().st_size,
        "sha256": digest,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


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
) -> int:
    """Run bounded batches serially and aggregate their coverage fragments."""
    if max_files_per_batch < 1:
        raise ValueError("max_files_per_batch must be positive")
    expected_interpreter = _interpreter_identity()
    shutil.rmtree(COVERAGE_SHARDS, ignore_errors=True)
    COVERAGE_SHARDS.mkdir(parents=True)
    COVERAGE_AUDIT.parent.mkdir(parents=True, exist_ok=True)
    erase_rc = _run_command([sys.executable, "-m", "coverage", "erase"])
    if erase_rc:
        print(f"COVERAGE-ERASE-FAIL rc={erase_rc}", flush=True)
        shutil.rmtree(COVERAGE_SHARDS, ignore_errors=True)
        return erase_rc

    failures: dict[str, int] = {}
    cancellation_rc = 0
    if run_isolated:
        isolated_rc = _run_owned_pytest(
            _isolated_pytest_command(pytest_args),
            env=os.environ.copy(),
            label="isolated",
            heartbeat_seconds=heartbeat_seconds,
            no_progress_seconds=no_progress_seconds,
        )
        if isolated_rc:
            failures["isolated"] = isolated_rc
            print(f"ISOLATED-TESTS-FAIL rc={isolated_rc}", flush=True)
        else:
            print("ISOLATED-TESTS-PASS rc=0", flush=True)

    if failures:
        print(
            f"SERIAL-ISOLATED-FAILED rc={failures['isolated']}; "
            "later-shards=not-started",
            flush=True,
        )
        print(
            f"SERIAL-SHARD-SUMMARY total={len(shards)} "
            f"failed={len(failures)} failures={failures}",
            flush=True,
        )
        shutil.rmtree(COVERAGE_SHARDS, ignore_errors=True)
        return max(failures.values())

    for index, shard in enumerate(shards, start=1):
        batches = _partition_test_paths(
            expand_shard(shard),
            max_files=max_files_per_batch,
        )
        if not batches:
            print(f"SHARD-EMPTY shard={shard}", flush=True)
            failures[shard] = 2
            print(
                f"SERIAL-SHARD-FAILED shard={shard} rc=2; "
                "later-shards=not-started",
                flush=True,
            )
            break

        workspace_parent = _resource_paths().root / "workspaces"
        workspace_parent.mkdir(parents=True, exist_ok=True)
        workspace = Path(
            tempfile.mkdtemp(prefix=f"gludd-gate-{shard}-", dir=workspace_parent)
        )
        owned_tmpdirs: list[Path] = []
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
                batchtemp = workspace / f"batch-{batch_index:03d}"
                batchtemp.mkdir(parents=True)
                batch_name = f"{shard}-batch-{batch_index:03d}"
                env = _env_for_shard(batch_name, batchtemp)
                owned_tmpdir = _owned_socket_safe_tmpdir(batch_name)
                owned_tmpdirs.append(owned_tmpdir)
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
                    failures[shard] = INTERPRETER_DRIFT_EXIT_CODE
                    shard_failed = True
                    break
                rc = _run_owned_pytest(
                    _pytest_command(shard, files, owned_tmpdir, pytest_args),
                    env=env,
                    label=f"{shard}:batch-{batch_index:03d}",
                    heartbeat_seconds=heartbeat_seconds,
                    no_progress_seconds=no_progress_seconds,
                )
                if not _interpreter_is_unchanged(
                    expected_interpreter,
                    context=f"{shard}:batch-{batch_index:03d}:after",
                ):
                    rc = INTERPRETER_DRIFT_EXIT_CODE
                coverage_saved = _save_shard_coverage(
                    shard,
                    batch_index,
                    batchtemp,
                    env,
                )
                if rc != 0 or not coverage_saved:
                    failures[shard] = rc or 1
                    print(
                        f"SHARD-FAIL shard={shard} batch={batch_index} rc={rc}; "
                        "later-batches=not-started",
                        flush=True,
                    )
                    shard_failed = True
                    if _is_cancellation_returncode(rc):
                        cancellation_rc = rc
                    break
                print(
                    f"SHARD-BATCH-PASS shard={shard} batch={batch_index} rc=0",
                    flush=True,
                )
            if not shard_failed:
                print(f"SHARD-PASS shard={shard} rc=0", flush=True)
        finally:
            for owned_tmpdir in owned_tmpdirs:
                cleanup_rc = max(
                    cleanup_rc,
                    _cleanup_owned_tmpdir(owned_tmpdir) or 0,
                )
            shutil.rmtree(workspace, ignore_errors=True)
        if cleanup_rc:
            failures[shard] = max(failures.get(shard, 0), cleanup_rc)
            print(
                f"SHARD-CLEANUP-SIGNAL shard={shard} rc={cleanup_rc}",
                flush=True,
            )
            if _is_cancellation_returncode(cleanup_rc):
                cancellation_rc = cleanup_rc
        if cancellation_rc:
            print(
                f"SERIAL-SHARD-CANCELLED shard={shard} rc={cancellation_rc}; "
                "later-shards=not-started",
                flush=True,
            )
            break
        if shard in failures:
            print(
                f"SERIAL-SHARD-FAILED shard={shard} rc={failures[shard]}; "
                "later-shards=not-started",
                flush=True,
            )
            break

    if failures:
        coverage_rc = 0
    elif coverage_output is not None:
        coverage_rc = _combine_coverage_output(coverage_output)
    elif aggregate_coverage:
        coverage_rc = _aggregate_coverage()
    else:
        coverage_rc = 0
    if coverage_rc:
        failures["coverage"] = coverage_rc
    print(
        f"SERIAL-SHARD-SUMMARY total={len(shards)} "
        f"failed={len(failures)} failures={failures}",
        flush=True,
    )
    shutil.rmtree(COVERAGE_SHARDS, ignore_errors=True)
    return max(failures.values(), default=0)


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
        help="maximum collected test files in one xdist worker",
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
    args = parser.parse_args()
    shards = _parse_shards(args.shards)
    pytest_args = shlex.split(args.pytest_args)
    if args.validate_only:
        return _validate_only_plan(
            shards,
            pytest_args,
            max_files_per_batch=args.max_files_per_batch,
            attestation_output=args.attestation_output,
        )
    pairing = _attestation_pairing(shards, pytest_args=pytest_args)
    started_at = _utc_now()
    identity = _repository_identity(
        expected_sha=os.environ.get("GLUDD_CANDIDATE_SHA")
        or os.environ.get("GITHUB_SHA")
    )
    if _identity_is_release_eligible(identity):
        try:
            returncode = run(
                shards,
                pytest_args,
                max_files_per_batch=args.max_files_per_batch,
                heartbeat_seconds=args.heartbeat_seconds,
                no_progress_seconds=args.no_progress_seconds,
                run_isolated=not args.skip_isolated,
                aggregate_coverage=not args.skip_aggregate,
                coverage_output=args.coverage_output,
            )
            error = None
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
            f"TERMINAL-ATTESTATION-SIGNAL signal={deferred_signals[0]} "
            f"rc={returncode}",
            flush=True,
        )
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
