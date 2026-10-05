#!/usr/bin/env python3
"""Run named GitHub Actions CI test shards in parallel locally."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree

if TYPE_CHECKING:
    from scripts.ci_named_shard_files import expand_shard
    from scripts.resource_arbiter import resource_root as project_resource_root
else:
    from ci_named_shard_files import expand_shard
    from resource_arbiter import resource_root as project_resource_root

DEFAULT_MAX_RUNTIME_SECONDS = 3600
TIMEOUT_RETURN_CODE = 124
MAX_FILES_PER_BATCH = 16

SHARD_STATE_ENV_VARS = (
    "GLUDD_STOP_STATE_FILE",
    "GLUDD_STREAK_FILE",
    "GLUDD_TASK_DEADLINE_STATE",
    "GLUDD_TASK_DEADLINE_WARNINGS",
    "GLUDD_TASK_STALE_FILE",
    "GLUDD_SESSION_STATE",
    "GLUDD_MAINTHREAD_STREAK_FILE",
    "GLUDD_FORCE_DELEGATE_STATE",
    "GLUDD_MODEL_UTIL_STATE",
    "GLUDD_READ_GRIND_FILE",
    "GLUDD_SONNET_TARGET_CONFIG",
    "GLUDD_MAIN_MODEL_FILE",
    "GLUDD_STOP_TEXT_COMPLETE_COUNT",
    "GLUDD_FLOOR_TEXT_COMPLETE_COUNT",
    "GLUDD_BLOCK_COUNTER_FILE",
    "GLUDD_BLOCK_REASON_FILE",
    "GLUDD_PERSIST_STOP_BLOCK_FILE",
    "GLUDD_FORCE_DISPATCH_PATH",
    "GLUDD_RELEASE_COMPLETENESS_FILE",
    "GLUDD_LAST_TEST_RESULT_FILE",
    "GLUDD_MULTITASK_STATE_FILE",
    "GLUDD_POST_RESULTS_STATE_FILE",
    "GLUDD_TEXT_ONLY_STATE_FILE",
    "GLUDD_WATCHDOG_CI_FILE",
    "GLUDD_STOP_TOOL_COUNTS_FILE",
    "GLUDD_WATCHDOG_PID_FILE",
    "GLUDD_ENHANCEMENT_RATIO_STATE",
    "GLUDD_FALSE_DONE_STATE_FILE",
    "GLUDD_TODOWRITE_STATE",
    "GLUDD_CI_STATE_FILE",
    "GLUDD_DISENGAGE_PATH",
    "GLUDD_DISENGAGE_AUDIT_PATH",
    "GLUDD_ALIVE_PATH",
)


@dataclass(frozen=True)
class ResourcePaths:
    """External runtime paths owned by one parallel shard invocation."""

    root: Path
    coverage_shards: Path
    resume: Path


def _resource_paths() -> ResourcePaths:
    """Resolve mutable shard evidence outside the tested checkout."""
    root = project_resource_root() / "ci-shards-parallel"
    return ResourcePaths(
        root=root,
        coverage_shards=root / "coverage-fragments",
        resume=root / "resume.json",
    )


def canonical_json_sha256(payload: object) -> str:
    """Return a stable SHA-256 digest for JSON-compatible evidence."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(state, sort_keys=True, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _file_sha256(path: Path) -> str:
    """Return a bounded-memory SHA-256 digest for one evidence file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expand_test_paths(paths: list[str], *, root: Path) -> list[str]:
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
    root: Path | None = None,
) -> list[list[str]]:
    """Expand directory arguments and return deterministic bounded file batches."""
    if max_files < 1:
        raise ValueError("max_files must be positive")
    resolved_root = root or Path.cwd()
    expanded = _expand_test_paths(paths, root=resolved_root)
    return [expanded[index : index + max_files] for index in range(0, len(expanded), max_files)]


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


def _git_candidate_sha() -> str:
    """Return the current repository HEAD SHA for resume identity, or empty."""
    root = Path(__file__).resolve().parents[1]
    with suppress(subprocess.CalledProcessError, OSError):
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            timeout=5,
        ).strip()
    return ""


@dataclass
class RunningShard:
    name: str
    process: subprocess.Popen[bytes]
    basetemp: Path
    command: list[str]
    junit_report: Path
    batch_index: int = 0
    files: list[str] | None = None
    key: str | None = None
    coverage_file: Path | None = None


def _parse_shards(raw: str) -> list[str]:
    shards = [item for item in raw.replace(",", " ").split() if item]
    if not shards:
        raise SystemExit("no shards supplied")
    return shards


def parse_positive_int(raw: str) -> int:
    """Parse one finite positive command-line bound."""
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer") from None
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def _has_xdist_worker_arg(args: list[str]) -> bool:
    for index, item in enumerate(args):
        if item == "-n" and index + 1 < len(args):
            return True
        if item.startswith("-n") and len(item) > 2:
            return True
        if item.startswith("--numprocesses"):
            return True
    return False


def _pytest_basetemp(workspace: Path) -> Path:
    """Return pytest-owned scratch nested beneath the stable shard workspace."""
    return workspace / "pytest"


def _command_for_shard(
    shard: str,
    pytest_args: list[str],
    workers_per_shard: int,
    *,
    files: list[str] | None = None,
    batch_index: int = 0,
    coverage: bool = False,
) -> tuple[list[str], Path]:
    if files is None:
        files = expand_shard(shard)
    if not files:
        raise SystemExit(f"shard {shard!r} expanded to no files")
    batch_suffix = f"-batch-{batch_index:03d}" if batch_index > 0 else ""
    basetemp = Path(f"/tmp/gludd-ci-shard-{shard}{batch_suffix}-{os.getpid()}")
    shutil.rmtree(basetemp, ignore_errors=True)
    worker_args: list[str] = []
    if workers_per_shard > 0 and not _has_xdist_worker_arg(pytest_args):
        worker_args = ["-n", str(workers_per_shard), "--dist", "loadgroup"]
    pytest_basetemp = _pytest_basetemp(basetemp)
    command = [
        sys.executable,
        "-m",
        "pytest",
        *files,
        *worker_args,
        "-v",
        *pytest_args,
        f"--basetemp={pytest_basetemp}",
        f"--junitxml={basetemp / 'junit.xml'}",
    ]
    if coverage:
        command.extend(
            [
                "--cov",
                "--cov-config=.coveragerc-greenlet",
                "--cov-report=",
                "--cov-fail-under=0",
            ]
        )
    return command, basetemp


def _read_junit_summary(report: Path) -> dict[str, object]:
    """Return compact testcase counts and the first failing testcase IDs."""
    try:
        root = ElementTree.parse(report).getroot()
    except (ElementTree.ParseError, OSError):
        return {"passed": 0, "failed": 0, "skipped": 0, "first_failure_ids": []}

    passed = failed = skipped = 0
    first_failure_ids: list[str] = []
    for testcase in root.iter("testcase"):
        testcase_id = "::".join(
            value for value in (testcase.attrib.get("classname"), testcase.attrib.get("name")) if value
        )
        if testcase.find("skipped") is not None:
            skipped += 1
        elif testcase.find("failure") is not None or testcase.find("error") is not None:
            failed += 1
            if testcase_id and len(first_failure_ids) < 5:
                first_failure_ids.append(testcase_id)
        else:
            passed += 1
    return {
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "first_failure_ids": first_failure_ids,
    }


def _persist_shard_summary(
    summary_dir: Path,
    shard: str,
    returncode: int,
    counts: dict[str, object],
) -> Path:
    """Persist one JSON summary per shard so results survive temp cleanup."""
    summary_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(char if char.isalnum() or char in "-_" else "_" for char in shard)
    path = summary_dir / f"{safe_name}.json"
    payload = {"shard": shard, "returncode": returncode, **counts}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _state_file_for_env(state_dir: Path, name: str) -> Path:
    suffix = ".jsonl" if name.endswith("AUDIT_PATH") else ".json"
    if name.endswith("WARNINGS"):
        suffix = ".log"
    return state_dir / f"{name.lower()}{suffix}"


def _env_for_shard(shard: str, basetemp: Path) -> dict[str, str]:
    state_dir = basetemp / "state"
    tmp_dir = basetemp / "tmp"
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["TMPDIR"] = str(tmp_dir)
    env["GLUDD_GATE_BASETEMP"] = str(basetemp / "gate-basetemp")
    env["GLUDD_HOT_MODULE_PREFIX"] = str(state_dir / "hot-")
    for name in SHARD_STATE_ENV_VARS:
        env[name] = str(_state_file_for_env(state_dir, name))
    env["GLUDD_SHARD_NAME"] = shard
    env["GLUDD_SHARD_STATE_DIR"] = str(state_dir)
    return env


def _quote(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def _save_batch_coverage(
    shard: str,
    batch_index: int,
    coverage_file: Path,
    coverage_fragments: Path,
) -> Path | None:
    """Copy one batch's coverage database into the reusable fragments directory."""
    try:
        if not coverage_file.is_file() or coverage_file.stat().st_size == 0:
            print(f"SHARD-COVERAGE-MISSING shard={shard} batch={batch_index}", flush=True)
            return None
    except OSError:
        print(f"SHARD-COVERAGE-MISSING shard={shard} batch={batch_index}", flush=True)
        return None
    destination = coverage_fragments / f".coverage.{shard}.batch-{batch_index:03d}"
    try:
        shutil.copy2(coverage_file, destination)
        size = destination.stat().st_size
        digest = _file_sha256(destination)
    except (OSError, ValueError) as exc:
        print(
            f"SHARD-COVERAGE-SAVE-FAIL shard={shard} batch={batch_index} error={type(exc).__name__}:{exc}",
            flush=True,
        )
        return None
    print(
        f"SHARD-COVERAGE-SAVED shard={shard} batch={batch_index} bytes={size} sha256={digest}",
        flush=True,
    )
    return destination


def _terminate_all(running: list[RunningShard]) -> None:
    for item in running:
        if item.process.poll() is not None:
            continue
        try:
            os.killpg(item.process.pid, signal.SIGINT)
        except ProcessLookupError:
            continue
    deadline = time.monotonic() + 10
    for item in running:
        while item.process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
        if item.process.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(item.process.pid, signal.SIGKILL)


def _cleanup(running: list[RunningShard]) -> None:
    for item in running:
        shutil.rmtree(item.basetemp, ignore_errors=True)


def run(
    shards: list[str],
    pytest_args: list[str],
    workers_per_shard: int,
    heartbeat_seconds: int,
    max_runtime_seconds: int = DEFAULT_MAX_RUNTIME_SECONDS,
    max_files_per_batch: int = MAX_FILES_PER_BATCH,
    resume_path: Path | None = None,
) -> int:
    """Run shards concurrently and reap every process at the finite deadline.

    When ``resume_path`` is provided, shards are split into bounded batches.
    Previously passing batches are skipped and their coverage fragments reused;
    only failed or missing batches are executed.
    """
    if max_runtime_seconds <= 0:
        raise ValueError("max_runtime_seconds must be positive")
    if max_files_per_batch < 1:
        raise ValueError("max_files_per_batch must be positive")

    summary_dir = Path(os.environ.get("GLUDD_SHARD_SUMMARY_DIR", ".gate-logs/ci-shards"))
    running: list[RunningShard] = []

    if resume_path is None:
        # Legacy whole-shard path: preserve existing behavior and test contracts.
        for shard in shards:
            command, basetemp = _command_for_shard(shard, pytest_args, workers_per_shard)
            print(f"=== ci shard {shard}: launch ===", flush=True)
            print(_quote(command), flush=True)
            env = _env_for_shard(shard, basetemp)
            tmpdir = env["TMPDIR"]
            state_dir = env["GLUDD_SHARD_STATE_DIR"]
            print(f"SHARD-ISOLATION shard={shard} tmpdir={tmpdir} state_dir={state_dir}", flush=True)
            process = subprocess.Popen(command, start_new_session=True, env=env)
            running.append(RunningShard(shard, process, basetemp, command, basetemp / "junit.xml"))

        pending = {item.name for item in running}
        results: dict[str, int] = {}
        started_at = time.monotonic()
        next_heartbeat = started_at + max(5, heartbeat_seconds)
        try:
            while pending:
                for item in running:
                    if item.name not in pending:
                        continue
                    rc = item.process.poll()
                    if rc is None:
                        continue
                    pending.remove(item.name)
                    results[item.name] = rc
                    counts = _read_junit_summary(item.junit_report)
                    summary_path = _persist_shard_summary(summary_dir, item.name, rc, counts)
                    print(
                        "SHARD-RESULT "
                        f"shard={item.name} passed={counts['passed']} "
                        f"failed={counts['failed']} skipped={counts['skipped']} "
                        f"first_failures={counts['first_failure_ids']} summary={summary_path}",
                        flush=True,
                    )
                    if rc < 0:
                        signum = -rc
                        signal_name = (
                            signal.Signals(signum).name
                            if signum in signal.Signals.__members__.values()
                            else str(signum)
                        )
                        print(f"SHARD-SIGNAL shard={item.name} signal={signal_name} rc={rc}", flush=True)
                    elif rc == 0:
                        print(f"SHARD-PASS shard={item.name} rc=0", flush=True)
                    else:
                        print(f"SHARD-FAIL shard={item.name} rc={rc}", flush=True)
                now = time.monotonic()
                elapsed = max(0.0, now - started_at)
                if pending and elapsed >= max_runtime_seconds:
                    for item in running:
                        if item.name not in pending:
                            continue
                        results[item.name] = TIMEOUT_RETURN_CODE
                        counts = _read_junit_summary(item.junit_report)
                        summary_path = _persist_shard_summary(
                            summary_dir,
                            item.name,
                            TIMEOUT_RETURN_CODE,
                            counts,
                        )
                        print(
                            "SHARD-TIMEOUT "
                            f"shard={item.name} elapsed_seconds={int(elapsed)} "
                            f"limit_seconds={max_runtime_seconds} summary={summary_path}",
                            flush=True,
                        )
                    pending.clear()
                elif pending and now >= next_heartbeat:
                    print(
                        f"SHARD-HEARTBEAT pending={sorted(pending)} completed={results} "
                        f"elapsed_seconds={int(elapsed)} limit_seconds={max_runtime_seconds}",
                        flush=True,
                    )
                    next_heartbeat = now + max(5, heartbeat_seconds)
                if pending:
                    time.sleep(1)
        except KeyboardInterrupt:
            print("SHARD-INTERRUPTED terminating children", flush=True)
            return 130
        finally:
            _terminate_all(running)
            _cleanup(running)

        failed = {name: rc for name, rc in results.items() if rc != 0}
        print(f"SHARD-SUMMARY total={len(shards)} failed={len(failed)} results={results}", flush=True)
        if failed:
            return max((128 + -rc) if rc < 0 else rc for rc in failed.values())
        return 0

    # Resume path: plan batches, skip passes, run missing/failed in parallel.
    resources = _resource_paths()
    coverage_fragments = resources.coverage_shards
    candidate_sha = _git_candidate_sha()

    resume_state = _load_resume_state(resume_path)
    resume_valid = bool(
        resume_state.get("schema_version") == 1
        and resume_state.get("candidate_sha") == candidate_sha
        and resume_state.get("runner") == "scripts/run_ci_shards_parallel.py"
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
            "runner": "scripts/run_ci_shards_parallel.py",
        }
    coverage_fragments.mkdir(parents=True, exist_ok=True)

    planned: list[tuple[str, int, list[str], str]] = []
    batch_by_key: dict[str, tuple[str, int, list[str]]] = {}
    for shard in shards:
        files = expand_shard(shard)
        if not files:
            raise SystemExit(f"shard {shard!r} expanded to no files")
        batches = _partition_test_paths(files, max_files=max_files_per_batch)
        for batch_index, batch_files in enumerate(batches, start=1):
            key = _batch_key(shard, batch_index, batch_files)
            planned.append((shard, batch_index, batch_files, key))
            batch_by_key[key] = (shard, batch_index, batch_files)

    skipped_keys: list[str] = []
    for shard, batch_index, files, key in planned:
        if _resume_skip_batch(
            resume_state,
            shard,
            batch_index,
            files,
            coverage_fragments,
            resources.root,
        ):
            skipped_keys.append(key)
            print(
                f"RESUME-SKIP shard={shard} batch={batch_index} files={len(files)} key={key}",
                flush=True,
            )

    pending_keys: set[str] = set()
    results = {}
    for key in skipped_keys:
        results[key] = 0

    for shard, batch_index, files, key in planned:
        if key in results:
            continue
        command, basetemp = _command_for_shard(
            shard,
            pytest_args,
            workers_per_shard,
            files=files,
            batch_index=batch_index,
            coverage=True,
        )
        print(f"=== ci shard {shard} batch {batch_index}: launch ===", flush=True)
        print(_quote(command), flush=True)
        env = _env_for_shard(f"{shard}-batch-{batch_index:03d}", basetemp)
        env["COVERAGE_FILE"] = str(basetemp / ".coverage")
        env["GLUDD_SHARD_NAME"] = shard
        tmpdir = env["TMPDIR"]
        state_dir = env["GLUDD_SHARD_STATE_DIR"]
        print(
            f"SHARD-ISOLATION shard={shard} batch={batch_index} tmpdir={tmpdir} state_dir={state_dir}",
            flush=True,
        )
        process = subprocess.Popen(command, start_new_session=True, env=env)
        running.append(
            RunningShard(
                name=shard,
                process=process,
                basetemp=basetemp,
                command=command,
                junit_report=basetemp / "junit.xml",
                batch_index=batch_index,
                files=files,
                key=key,
                coverage_file=basetemp / ".coverage",
            )
        )
        pending_keys.add(key)

    started_at = time.monotonic()
    next_heartbeat = started_at + max(5, heartbeat_seconds)
    try:
        while pending_keys:
            for item in running:
                if item.key not in pending_keys:
                    continue
                rc = item.process.poll()
                if rc is None:
                    continue
                pending_keys.discard(item.key)
                results[item.key] = rc
                counts = _read_junit_summary(item.junit_report)
                summary_path = _persist_shard_summary(
                    summary_dir,
                    f"{item.name}-batch-{item.batch_index:03d}",
                    rc,
                    counts,
                )
                print(
                    "SHARD-RESULT "
                    f"shard={item.name} batch={item.batch_index} passed={counts['passed']} "
                    f"failed={counts['failed']} skipped={counts['skipped']} "
                    f"first_failures={counts['first_failure_ids']} summary={summary_path}",
                    flush=True,
                )
                if rc < 0:
                    signum = -rc
                    signal_name = (
                        signal.Signals(signum).name if signum in signal.Signals.__members__.values() else str(signum)
                    )
                    print(
                        f"SHARD-SIGNAL shard={item.name} batch={item.batch_index} signal={signal_name} rc={rc}",
                        flush=True,
                    )
                elif rc == 0:
                    print(f"SHARD-PASS shard={item.name} batch={item.batch_index} rc=0", flush=True)
                else:
                    print(f"SHARD-FAIL shard={item.name} batch={item.batch_index} rc={rc}", flush=True)

                if rc == 0 and item.coverage_file is not None:
                    saved_fragment = _save_batch_coverage(
                        item.name,
                        item.batch_index,
                        item.coverage_file,
                        coverage_fragments,
                    )
                    if saved_fragment is not None:
                        resume_state[item.key] = {
                            "rc": 0,
                            "coverage_fragment": f"coverage-fragments/{saved_fragment.name}",
                        }
                        _save_resume_state(resume_path, resume_state)
            now = time.monotonic()
            elapsed = max(0.0, now - started_at)
            if pending_keys and elapsed >= max_runtime_seconds:
                for item in running:
                    if item.key not in pending_keys:
                        continue
                    results[item.key] = TIMEOUT_RETURN_CODE
                    counts = _read_junit_summary(item.junit_report)
                    summary_path = _persist_shard_summary(
                        summary_dir,
                        f"{item.name}-batch-{item.batch_index:03d}",
                        TIMEOUT_RETURN_CODE,
                        counts,
                    )
                    print(
                        "SHARD-TIMEOUT "
                        f"shard={item.name} batch={item.batch_index} elapsed_seconds={int(elapsed)} "
                        f"limit_seconds={max_runtime_seconds} summary={summary_path}",
                        flush=True,
                    )
                pending_keys.clear()
            elif pending_keys and now >= next_heartbeat:
                print(
                    f"SHARD-HEARTBEAT pending={sorted(item.name for item in running if item.key in pending_keys)} "
                    f"completed={results} elapsed_seconds={int(elapsed)} limit_seconds={max_runtime_seconds}",
                    flush=True,
                )
                next_heartbeat = now + max(5, heartbeat_seconds)
            if pending_keys:
                time.sleep(1)
    except KeyboardInterrupt:
        print("SHARD-INTERRUPTED terminating children", flush=True)
        return 130
    finally:
        _terminate_all(running)
        _cleanup(running)

    shard_results: dict[str, int] = {}
    for key, rc in results.items():
        shard, _, _ = batch_by_key[key]
        shard_results[shard] = max(shard_results.get(shard, 0), rc)

    failed = {name: rc for name, rc in shard_results.items() if rc != 0}
    print(f"SHARD-SUMMARY total={len(shards)} failed={len(failed)} results={shard_results}", flush=True)
    if failed:
        return max((128 + -rc) if rc < 0 else rc for rc in failed.values())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shards", required=True, help="space or comma separated shard names")
    parser.add_argument("--pytest-args", default="", help="extra pytest args passed to every shard")
    parser.add_argument("--workers-per-shard", type=int, default=1)
    parser.add_argument("--heartbeat-seconds", type=int, default=30)
    parser.add_argument(
        "--max-runtime-seconds",
        type=parse_positive_int,
        default=DEFAULT_MAX_RUNTIME_SECONDS,
    )
    parser.add_argument(
        "--max-files-per-batch",
        type=int,
        default=MAX_FILES_PER_BATCH,
        help="maximum collected test files in one owned pytest process (used with --resume)",
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
        help="path to the resume state file (default: <resource-root>/ci-shards-parallel/resume.json)",
    )
    args = parser.parse_args()
    resume_path: Path | None = None
    if args.resume:
        resume_path = args.resume_file or _resource_paths().resume
    return run(
        _parse_shards(args.shards),
        shlex.split(args.pytest_args),
        args.workers_per_shard,
        args.heartbeat_seconds,
        args.max_runtime_seconds,
        max_files_per_batch=args.max_files_per_batch,
        resume_path=resume_path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
