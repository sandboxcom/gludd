#!/usr/bin/env python3
"""Run named GitHub Actions CI test shards in parallel locally."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ci_named_shard_files import expand_shard


@dataclass
class RunningShard:
    name: str
    process: subprocess.Popen[object]
    basetemp: Path
    command: list[str]


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


def _command_for_shard(shard: str, pytest_args: list[str], workers_per_shard: int) -> tuple[list[str], Path]:
    files = expand_shard(shard)
    if not files:
        raise SystemExit(f"shard {shard!r} expanded to no files")
    basetemp = Path(f"/tmp/gludd-ci-shard-{shard}-{os.getpid()}")
    shutil.rmtree(basetemp, ignore_errors=True)
    worker_args: list[str] = []
    if workers_per_shard > 0 and not _has_xdist_worker_arg(pytest_args):
        worker_args = ["-n", str(workers_per_shard), "--dist", "loadgroup"]
    command = [
        sys.executable,
        "-m",
        "pytest",
        *files,
        *worker_args,
        "-v",
        *pytest_args,
        f"--basetemp={basetemp}",
    ]
    return command, basetemp


def _quote(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def _terminate_all(running: list[RunningShard]) -> None:
    for item in running:
        if item.process.poll() is not None:
            continue
        try:
            os.killpg(item.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    deadline = time.monotonic() + 10
    for item in running:
        while item.process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
        if item.process.poll() is None:
            try:
                os.killpg(item.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def _cleanup(running: list[RunningShard]) -> None:
    for item in running:
        shutil.rmtree(item.basetemp, ignore_errors=True)


def run(shards: list[str], pytest_args: list[str], workers_per_shard: int, heartbeat_seconds: int) -> int:
    running: list[RunningShard] = []
    for shard in shards:
        command, basetemp = _command_for_shard(shard, pytest_args, workers_per_shard)
        print(f"=== ci shard {shard}: launch ===", flush=True)
        print(_quote(command), flush=True)
        process = subprocess.Popen(command, start_new_session=True)
        running.append(RunningShard(shard, process, basetemp, command))

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
                if rc < 0:
                    signum = -rc
                    signal_name = signal.Signals(signum).name if signum in signal.Signals.__members__.values() else str(signum)
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
        _terminate_all(running)
        return 130
    finally:
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
