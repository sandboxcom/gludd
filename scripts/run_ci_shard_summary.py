"""Run one named CI shard with signal-aware pytest supervision."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from ci_named_shard_files import expand_shard


def _emit(line: str) -> None:
    print(line, flush=True)


def _refresh_stop_state_if_present(
    path: Path | None = None,
    *,
    now_ms: int | None = None,
) -> bool:
    state_path = path or Path(os.environ.get("GLUDD_STOP_STATE_FILE", "/tmp/gludd-stop-state.json"))
    if not state_path.exists():
        return False
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(data, dict):
        return False
    data["ts"] = now_ms if now_ms is not None else int(time.time() * 1000)
    try:
        state_path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        return False
    return True


def _xdist_args() -> list[str]:
    raw = os.environ.get("GLUDD_XDIST")
    if raw:
        workers = int(raw)
    else:
        workers = max(1, (os.cpu_count() or 1) // 4)
    if workers <= 0:
        return []
    return ["-n", str(workers), "--dist", "loadgroup"]


def _install_sigterm_guard() -> tuple[signal.Handlers, threading.Event]:
    previous = signal.getsignal(signal.SIGTERM)
    received = threading.Event()

    def _handler(_signum: int, _frame: object) -> None:
        received.set()
        _emit(
            "ci-shard-summary: received unexpected SIGTERM; "
            "pytest child is isolated and will continue"
        )

    signal.signal(signal.SIGTERM, _handler)
    return previous, received


def _read_stream(stream: TextIO, log_path: Path) -> None:
    with log_path.open("w", encoding="utf-8") as log:
        for raw in stream:
            log.write(raw)
            log.flush()
            print(raw, end="", flush=True)


def run_shard(
    shard: str,
    *,
    pytest_args: str,
    heartbeat_interval: float,
    log_dir: Path,
) -> int:
    paths = expand_shard(shard)
    if not paths:
        _emit(f"ERROR: shard {shard} expanded to no test paths")
        return 2

    log_dir.mkdir(parents=True, exist_ok=True)
    base_temp = Path(tempfile.mkdtemp(prefix=f"gludd-ci-shard-summary-{shard}-", dir="/tmp"))
    log_path = log_dir / f"{shard}.log"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *paths,
        *_xdist_args(),
        "-q",
        "--tb=no",
        "--disable-warnings",
        *shlex.split(pytest_args),
        f"--basetemp={base_temp}",
    ]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    previous_sigterm, sigterm_received = _install_sigterm_guard()
    proc: subprocess.Popen[str] | None = None
    reader: threading.Thread | None = None
    started = time.monotonic()
    last_heartbeat = started
    _refresh_stop_state_if_present()
    try:
        _emit(
            f"ci-shard-summary: starting {shard} with {len(paths)} paths; "
            f"log={log_path}"
        )
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            start_new_session=True,
        )
        if proc.stdout is None:
            raise RuntimeError("pytest stdout pipe was not created")
        reader = threading.Thread(
            target=_read_stream,
            args=(proc.stdout, log_path),
            name=f"ci-shard-summary-log-{shard}",
        )
        reader.start()
        _emit(f"ci-shard-summary: launched {shard} pid={proc.pid}")
        wait_tick = threading.Event()
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            now = time.monotonic()
            if now - last_heartbeat >= heartbeat_interval:
                elapsed = int(now - started)
                _emit(f"ci-shard-summary: {shard} still running pid={proc.pid} elapsed={elapsed}s")
                _refresh_stop_state_if_present()
                last_heartbeat = now
            wait_tick.wait(1.0)
        rc = int(proc.wait())
        if reader is not None:
            reader.join()
        _emit(f"ci-shard-summary: {shard} exited rc={rc} log={log_path}")
        if sigterm_received.is_set():
            _emit("ci-shard-summary: unexpected SIGTERM observed; marking run failed")
            return 2
        if rc == -signal.SIGTERM:
            _emit("ci-shard-summary: pytest exited from SIGTERM; marking run failed")
            return 2
        return rc
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        shutil.rmtree(base_temp, ignore_errors=True)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard", required=True)
    parser.add_argument("--pytest-args", default="")
    parser.add_argument("--heartbeat-interval", type=float, default=30.0)
    parser.add_argument("--log-dir", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    run_id = f"{int(time.time())}-{os.getpid()}"
    log_dir = args.log_dir or Path(".gate-logs") / "ci-shards" / run_id
    return run_shard(
        args.shard,
        pytest_args=args.pytest_args,
        heartbeat_interval=args.heartbeat_interval,
        log_dir=log_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())
