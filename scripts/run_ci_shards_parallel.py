#!/usr/bin/env python3
"""Run named GitHub Actions CI test shards in parallel locally."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree

if TYPE_CHECKING:
    from scripts.ci_named_shard_files import expand_shard
else:
    from ci_named_shard_files import expand_shard

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

@dataclass
class RunningShard:
    name: str
    process: subprocess.Popen[bytes]
    basetemp: Path
    command: list[str]
    junit_report: Path


def _parse_shards(raw: str) -> list[str]:
    shards = [item for item in raw.replace(",", " ").split() if item]
    if not shards:
        raise SystemExit("no shards supplied")
    return shards


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


def _command_for_shard(shard: str, pytest_args: list[str], workers_per_shard: int) -> tuple[list[str], Path]:
    files = expand_shard(shard)
    if not files:
        raise SystemExit(f"shard {shard!r} expanded to no files")
    basetemp = Path(f"/tmp/gludd-ci-shard-{shard}-{os.getpid()}")
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
            value
            for value in (testcase.attrib.get("classname"), testcase.attrib.get("name"))
            if value
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


def run(shards: list[str], pytest_args: list[str], workers_per_shard: int, heartbeat_seconds: int) -> int:
    running: list[RunningShard] = []
    summary_dir = Path(os.environ.get("GLUDD_SHARD_SUMMARY_DIR", ".gate-logs/ci-shards"))
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
    next_heartbeat = time.monotonic() + max(5, heartbeat_seconds)
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
            if pending and now >= next_heartbeat:
                print(f"SHARD-HEARTBEAT pending={sorted(pending)} completed={results}", flush=True)
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shards", required=True, help="space or comma separated shard names")
    parser.add_argument("--pytest-args", default="", help="extra pytest args passed to every shard")
    parser.add_argument("--workers-per-shard", type=int, default=1)
    parser.add_argument("--heartbeat-seconds", type=int, default=30)
    args = parser.parse_args()
    return run(
        _parse_shards(args.shards),
        shlex.split(args.pytest_args),
        args.workers_per_shard,
        args.heartbeat_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
