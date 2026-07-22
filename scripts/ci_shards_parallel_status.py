"""Report status for the detached CI shard parallel supervisor."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(".gate-logs") / "ci-shards")
    parser.add_argument("--lines", type=int, default=80)
    return parser.parse_args(argv)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _tail(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return [f"missing log: {path}"]
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    latest_dir = args.root / "latest.dir"
    latest_pid = args.root / "latest.pid"
    if not latest_dir.exists():
        print(f"ci-shards-parallel-status: no run recorded under {args.root}")
        return 1

    run_dir = Path(latest_dir.read_text(encoding="utf-8").strip())
    pid_text = latest_pid.read_text(encoding="utf-8").strip() if latest_pid.exists() else ""
    pid = int(pid_text) if pid_text else 0
    exitcode_path = run_dir / "exitcode"
    log_path = run_dir / "supervisor.log"
    exitcode = None
    if exitcode_path.exists():
        exitcode = int(exitcode_path.read_text(encoding="utf-8").strip())

    alive = bool(pid and _pid_alive(pid))
    if exitcode is None and alive:
        print(f"ci-shards-parallel-status: RUNNING pid={pid} run_dir={run_dir}")
    elif exitcode is None:
        print(f"ci-shards-parallel-status: STALE pid={pid} run_dir={run_dir}")
    else:
        print(f"ci-shards-parallel-status: EXITED rc={exitcode} pid={pid} run_dir={run_dir}")

    print(f"ci-shards-parallel-status: log={log_path}")
    print("ci-shards-parallel-status: tail")
    for line in _tail(log_path, args.lines):
        print(line)

    if exitcode is None:
        return 0 if alive else 2
    return exitcode


if __name__ == "__main__":
    raise SystemExit(main())
