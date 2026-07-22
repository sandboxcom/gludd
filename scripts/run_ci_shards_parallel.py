"""Run CI test shards concurrently without terminating siblings on failure."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


@dataclass(frozen=True)
class ShardResult:
    shard: str
    returncode: int
    log_path: Path


def build_shard_command(shard: str, pytest_args: str) -> list[str]:
    return [
        "make",
        "--no-print-directory",
        "test-ci-shard-summary",
        f"SHARD={shard}",
        f"PYTEST_ARGS={pytest_args}",
    ]


def child_env(workers_per_shard: int) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["GLUDD_XDIST"] = str(workers_per_shard)
    return env


def _emit(lock: threading.Lock, line: str) -> None:
    with lock:
        print(line, flush=True)


def _read_output(shard: str, stream: TextIO, log_path: Path, lock: threading.Lock) -> None:
    with log_path.open("w", encoding="utf-8") as log:
        for raw in stream:
            log.write(raw)
            log.flush()
            _emit(lock, f"[{shard}] {raw.rstrip(chr(10))}")


def _install_sigterm_guard(lock: threading.Lock) -> tuple[signal.Handlers, threading.Event]:
    previous = signal.getsignal(signal.SIGTERM)
    received = threading.Event()

    def _handler(_signum, _frame) -> None:
        received.set()
        _emit(
            lock,
            "ci-shards-parallel: received unexpected SIGTERM; continuing to supervise child shards",
        )

    signal.signal(signal.SIGTERM, _handler)
    return previous, received


def run_parallel(
    shards: Sequence[str],
    *,
    pytest_args: str,
    workers_per_shard: int,
    log_dir: Path,
    heartbeat_interval: float = 30.0,
    popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
    install_signal_guard: bool = True,
) -> int:
    if not shards:
        raise ValueError("at least one shard is required")
    if workers_per_shard < 1:
        raise ValueError("workers_per_shard must be >= 1")

    log_dir.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    previous_sigterm = None
    sigterm_received = threading.Event()
    if install_signal_guard:
        previous_sigterm, sigterm_received = _install_sigterm_guard(lock)
    env = child_env(workers_per_shard)
    procs: dict[str, subprocess.Popen] = {}
    logs: dict[str, Path] = {}
    readers: list[threading.Thread] = []

    try:
        _emit(
            lock,
            "ci-shards-parallel: starting "
            + ", ".join(shards)
            + f" with GLUDD_XDIST={workers_per_shard}",
        )
        for shard in shards:
            log_path = log_dir / f"{shard}.log"
            cmd = build_shard_command(shard, pytest_args)
            proc = popen_factory(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
                start_new_session=True,
            )
            if proc.stdout is None:
                raise RuntimeError(f"shard {shard} stdout pipe was not created")
            procs[shard] = proc
            logs[shard] = log_path
            reader = threading.Thread(
                target=_read_output,
                args=(shard, proc.stdout, log_path, lock),
                name=f"ci-shard-log-{shard}",
            )
            reader.start()
            readers.append(reader)
            _emit(lock, f"ci-shards-parallel: launched {shard} pid={proc.pid}")

        pending = set(shards)
        results: list[ShardResult] = []
        wait_tick = threading.Event()
        last_heartbeat = time.monotonic()
        while pending:
            for shard in list(pending):
                rc = procs[shard].poll()
                if rc is None:
                    continue
                procs[shard].wait()
                pending.remove(shard)
                results.append(ShardResult(shard, int(rc), logs[shard]))
                _emit(lock, f"ci-shards-parallel: {shard} exited rc={rc}")

            now = time.monotonic()
            if pending and now - last_heartbeat >= heartbeat_interval:
                _emit(lock, "ci-shards-parallel: still running " + ", ".join(sorted(pending)))
                last_heartbeat = now
            if pending:
                wait_tick.wait(1.0)

        for reader in readers:
            reader.join()

        failed = [result for result in results if result.returncode != 0]
        unexpected_sigterm = sigterm_received.is_set()
        _emit(lock, "ci-shards-parallel: logs in " + str(log_dir))
        for result in sorted(results, key=lambda item: item.shard):
            _emit(
                lock,
                f"ci-shards-parallel: result {result.shard} rc={result.returncode} log={result.log_path}",
            )
        if unexpected_sigterm:
            _emit(lock, "ci-shards-parallel: unexpected SIGTERM observed; marking run failed")
            return 2
        return 1 if failed else 0
    finally:
        if install_signal_guard:
            signal.signal(signal.SIGTERM, previous_sigterm)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shards", required=True, help="space-separated shard names")
    parser.add_argument("--pytest-args", default="")
    parser.add_argument("--workers-per-shard", type=int, default=1)
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--heartbeat-interval", type=float, default=30.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    shards = [part for part in args.shards.split() if part]
    run_id = f"{int(time.time())}-{os.getpid()}"
    log_dir = args.log_dir or Path(".gate-logs") / "ci-shards" / run_id
    rc = run_parallel(
        shards,
        pytest_args=args.pytest_args,
        workers_per_shard=args.workers_per_shard,
        log_dir=log_dir,
        heartbeat_interval=args.heartbeat_interval,
    )
    exitcode_file = os.environ.get("GLUDD_CI_SHARDS_EXITCODE_FILE")
    if exitcode_file:
        Path(exitcode_file).write_text(str(rc) + chr(10), encoding="utf-8")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
