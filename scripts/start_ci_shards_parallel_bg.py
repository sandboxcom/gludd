"""Start the CI shard parallel supervisor as a detached background process."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shards", required=True)
    parser.add_argument("--pytest-args", default="")
    parser.add_argument("--workers-per-shard", type=int, default=1)
    parser.add_argument("--root", type=Path, default=Path(".gate-logs") / "ci-shards")
    parser.add_argument("--heartbeat-interval", type=float, default=30.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    args.root.mkdir(parents=True, exist_ok=True)
    run_id = f"{int(time.time())}-{os.getpid()}"
    run_dir = args.root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "supervisor.log"
    exitcode_path = run_dir / "exitcode"

    cmd = [
        sys.executable,
        "scripts/run_ci_shards_parallel.py",
        "--shards",
        args.shards,
        "--pytest-args",
        args.pytest_args,
        "--workers-per-shard",
        str(args.workers_per_shard),
        "--log-dir",
        str(run_dir),
        "--heartbeat-interval",
        str(args.heartbeat_interval),
    ]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["GLUDD_CI_SHARDS_EXITCODE_FILE"] = str(exitcode_path)
    log = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
        text=True,
    )
    log.close()

    (args.root / "latest.pid").write_text(str(proc.pid) + chr(10), encoding="utf-8")
    (args.root / "latest.dir").write_text(str(run_dir) + chr(10), encoding="utf-8")
    print(f"ci-shards-parallel-bg: pid={proc.pid}")
    print(f"ci-shards-parallel-bg: run_dir={run_dir}")
    print(f"ci-shards-parallel-bg: log={log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
