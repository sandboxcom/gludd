"""Run unit tests in bounded, serial shards."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import signal
import subprocess
import sys
from pathlib import Path


def discover_tests(tests_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in tests_dir.glob("test_*.py")
        if path.is_file() and "__pycache__" not in path.parts
    )


def shard_files(files: list[Path], shard_count: int, index: int) -> list[Path]:
    if shard_count < 1:
        raise ValueError("shard count must be >= 1")
    if index < 1 or index > shard_count:
        raise ValueError("shard index must be between 1 and shard count")
    return [path for offset, path in enumerate(files) if offset % shard_count == index - 1]


def _terminate(proc: subprocess.Popen[object]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)


def run_shard(
    *,
    files: list[Path],
    shard_count: int,
    index: int,
    timeout: int,
    pytest_args: list[str],
    verbosity: str,
) -> int:
    selected = shard_files(files, shard_count, index)
    print(f"=== unit shard {index}/{shard_count}: {len(selected)} files ===", flush=True)
    if not selected:
        return 0

    basetemp = Path(f"/tmp/gludd-unit-shard-{index}-{os.getpid()}")
    shutil.rmtree(basetemp, ignore_errors=True)
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *(str(path) for path in selected),
        verbosity,
        *pytest_args,
        f"--basetemp={basetemp}",
    ]
    print(" ".join(shlex.quote(part) for part in cmd), flush=True)
    proc = subprocess.Popen(cmd, start_new_session=True)
    try:
        hard_timeout = int(os.environ.get("SHARD_HARD_TIMEOUT", "3600"))
        wait_timeout = hard_timeout if timeout <= 0 else timeout
        return proc.wait(timeout=wait_timeout)
    except subprocess.TimeoutExpired:
        effective_timeout = int(os.environ.get("SHARD_HARD_TIMEOUT", "3600")) if timeout <= 0 else timeout
        print(
            f"TIMEOUT: unit shard {index}/{shard_count} exceeded {effective_timeout}s",
            flush=True,
        )
        _terminate(proc)
        return 124
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tests-dir", default="tests/unit")
    parser.add_argument("--shards", type=int, default=int(os.environ.get("SHARDS", "12")))
    parser.add_argument("--index", type=int)
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("SHARD_TIMEOUT", "300")))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tests_dir = Path(args.tests_dir)
    files = discover_tests(tests_dir)
    if not files:
        print(f"No unit test files found in {tests_dir}", flush=True)
        return 1

    pytest_args = shlex.split(os.environ.get("PYTEST_ARGS", "--tb=short -rf"))
    verbosity = os.environ.get("PYTEST_VERBOSITY", "-q")
    indices = [args.index] if args.index is not None else list(range(1, args.shards + 1))

    for index in indices:
        rc = run_shard(
            files=files,
            shard_count=args.shards,
            index=index,
            timeout=args.timeout,
            pytest_args=pytest_args,
            verbosity=verbosity,
        )
        if rc != 0:
            print(f"FAILED: unit shard {index}/{args.shards} exited {rc}", flush=True)
            return rc

    print(f"PASS: {len(indices)} unit shard(s), {len(files)} test files discovered", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
