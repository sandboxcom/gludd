#!/usr/bin/env python3
"""Run every named CI shard in a fresh process and aggregate release coverage."""

from __future__ import annotations

import argparse
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
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any, TextIO

if TYPE_CHECKING:
    from scripts.ci_named_shard_files import ISOLATED_TESTS, SHARDS, expand_shard
    from scripts.run_ci_shards_parallel import _env_for_shard, _parse_shards
else:
    from ci_named_shard_files import ISOLATED_TESTS, SHARDS, expand_shard
    from run_ci_shards_parallel import _env_for_shard, _parse_shards

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DEFAULT_SHARDS = tuple(SHARDS)
COVERAGE_SHARDS = ROOT / ".coverage-shards-local"
COVERAGE_JSON = ROOT / "coverage.json"
COVERAGE_AUDIT = ROOT / ".gate-logs" / "coverage-local.json"
DEFAULT_COVERAGE_CONFIG = ROOT / "pyproject.toml"
GREENLET_COVERAGE_CONFIG = ROOT / ".coveragerc-greenlet"
GOVERNANCE_MODULE_UTILS = (
    "collections/ansible_collections/general_ludd/governance/plugins/module_utils"
)
MAX_FILES_PER_BATCH = 64
DEFAULT_HEARTBEAT_SECONDS = 30.0
DEFAULT_NO_PROGRESS_SECONDS = 10.0 * 60.0
WORKER_DEATH_EXIT_CODE = 70
NO_PROGRESS_EXIT_CODE = 124
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


def _quote(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


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
    coverage_config = (
        GREENLET_COVERAGE_CONFIG if shard == "unit-3" else DEFAULT_COVERAGE_CONFIG
    )
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


def _partition_test_paths(
    paths: list[str],
    *,
    max_files: int,
    root: Path = ROOT,
) -> list[list[str]]:
    """Expand directory arguments and return deterministic bounded file batches."""
    if max_files < 1:
        raise ValueError("max_files must be positive")

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

    return [
        expanded[index : index + max_files]
        for index in range(0, len(expanded), max_files)
    ]


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
    while _owned_process_group_alive(process) and time.monotonic() < deadline:
        time.sleep(0.05)
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
    if not coverage_file.is_file():
        # pytest-cov under xdist can leave parallel data. `coverage combine`
        # canonicalizes it into the shard-specific COVERAGE_FILE.
        _run_command(
            [sys.executable, "-m", "coverage", "combine", str(basetemp)],
            env=env,
        )
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


def run(
    shards: list[str],
    pytest_args: list[str],
    *,
    max_files_per_batch: int = MAX_FILES_PER_BATCH,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    no_progress_seconds: float = DEFAULT_NO_PROGRESS_SECONDS,
) -> int:
    """Run bounded batches serially and aggregate their coverage fragments."""
    if max_files_per_batch < 1:
        raise ValueError("max_files_per_batch must be positive")
    shutil.rmtree(COVERAGE_SHARDS, ignore_errors=True)
    COVERAGE_SHARDS.mkdir(parents=True)
    COVERAGE_AUDIT.parent.mkdir(parents=True, exist_ok=True)
    erase_rc = _run_command([sys.executable, "-m", "coverage", "erase"])
    if erase_rc:
        print(f"COVERAGE-ERASE-FAIL rc={erase_rc}", flush=True)
        shutil.rmtree(COVERAGE_SHARDS, ignore_errors=True)
        return erase_rc

    failures: dict[str, int] = {}
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

    for index, shard in enumerate(shards, start=1):
        batches = _partition_test_paths(
            expand_shard(shard),
            max_files=max_files_per_batch,
        )
        if not batches:
            print(f"SHARD-EMPTY shard={shard}", flush=True)
            failures[shard] = 2
            continue

        workspace = Path(
            tempfile.mkdtemp(prefix=f"gludd-gate-{shard}-", dir="/tmp")
        )
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
                env["COVERAGE_FILE"] = str(batchtemp / ".coverage")
                print(
                    f"SHARD-BATCH shard={shard} batch={batch_index}/{len(batches)} "
                    f"files={len(files)} basetemp={batchtemp / 'pytest'}",
                    flush=True,
                )
                rc = _run_owned_pytest(
                    _pytest_command(shard, files, batchtemp, pytest_args),
                    env=env,
                    label=f"{shard}:batch-{batch_index:03d}",
                    heartbeat_seconds=heartbeat_seconds,
                    no_progress_seconds=no_progress_seconds,
                )
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
                    break
                print(
                    f"SHARD-BATCH-PASS shard={shard} batch={batch_index} rc=0",
                    flush=True,
                )
            if not shard_failed:
                print(f"SHARD-PASS shard={shard} rc=0", flush=True)
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    coverage_rc = _aggregate_coverage()
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
    args = parser.parse_args()
    return run(
        _parse_shards(args.shards),
        shlex.split(args.pytest_args),
        max_files_per_batch=args.max_files_per_batch,
        heartbeat_seconds=args.heartbeat_seconds,
        no_progress_seconds=args.no_progress_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
