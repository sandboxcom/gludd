#!/usr/bin/env python3
"""Memory-bounded pytest runner: size xdist workers by AVAILABLE RAM.

The default ``make test`` used ``pytest -n auto`` which spawns one worker PER CPU
CORE. Each gludd worker resident set is ~1.2-1.5 GiB, so ``cores x RSS`` routinely
exceeds physical memory and the kernel OOM-kills the run (SIGKILL / exit 137).

This runner instead computes the worker count from *available* memory:

    n = max(1, min(cpu_count, available_gb // PER_WORKER_GB))

so the total working set stays within RAM. It also DETECTS an OOM-shaped exit
(negative signal -9, exit 137, or an xdist "worker crashed / node down" line) and
RETRIES with the worker count HALVED, down to ``-n 1``, before giving up. This
keeps the local ``make test`` / ``make ci-test`` runs (which Claude Code drives)
from being OOM-killed.

Env knobs (all optional):
  PER_WORKER_GB / GLUDD_PER_WORKER_GB   GiB budgeted per worker (default 1.5)
  NPROC                                 explicit worker count override (wins)
  GLUDD_XDIST                           explicit worker count override (wins)

An override that is not a positive integer (e.g. the CI-faithfulness value
``GLUDD_XDIST=auto``) is ignored, so the adaptive computation still applies.

Usage:  adaptive_test.py [pytest args...]   e.g.  adaptive_test.py tests/unit -q
Only stdlib is required; ``psutil`` is used when importable for the RAM reading.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence

DEFAULT_PER_WORKER_GB = 1.5
# Exit codes that indicate the OS killed the process (or a worker) — OOM-shaped.
_OOM_EXIT_CODES = frozenset({-9, 137})
_OOM_OUTPUT_MARKERS = (
    "worker crashed",
    "node down",
    "crashed while running",
    "replacing crashed worker",
)


def per_worker_gb(env: Mapping[str, str] | None = None) -> float:
    """Per-worker memory budget in GiB (env-tunable, default 1.5)."""
    env = os.environ if env is None else env
    for key in ("PER_WORKER_GB", "GLUDD_PER_WORKER_GB"):
        raw = env.get(key)
        if raw:
            try:
                val = float(raw)
            except ValueError:
                continue
            if val > 0:
                return val
    return DEFAULT_PER_WORKER_GB


def env_override(env: Mapping[str, str] | None = None) -> int | None:
    """Return an explicit worker-count override from NPROC / GLUDD_XDIST.

    Only a POSITIVE INTEGER counts as an override; anything else (empty,
    ``auto``, non-numeric) returns ``None`` so the adaptive path is used.
    """
    env = os.environ if env is None else env
    for key in ("NPROC", "GLUDD_XDIST"):
        raw = env.get(key)
        if raw is None:
            continue
        try:
            val = int(raw)
        except (ValueError, TypeError):
            continue
        if val >= 1:
            return val
    return None


def available_gb() -> float | None:
    """Available RAM in GiB via psutil, falling back to total; None if unknown."""
    try:
        import psutil
    except ImportError:
        return None
    try:
        vm = psutil.virtual_memory()
    except Exception:
        return None
    avail = getattr(vm, "available", None)
    if not avail:
        avail = getattr(vm, "total", None)
    if not avail:
        return None
    return float(avail) / (1024.0**3)


def compute_nproc(
    avail_gb: float | None,
    cpu_count: int,
    gb_per_worker: float = DEFAULT_PER_WORKER_GB,
) -> int:
    """Worker count sized by available RAM, floored at 1, capped at cpu_count.

    When ``avail_gb`` is None (no psutil) or ``gb_per_worker`` is non-positive,
    fall back to a CPU-only count (still floored at 1).
    """
    cpu_count = max(1, cpu_count)
    if avail_gb is None or gb_per_worker <= 0:
        return cpu_count
    by_mem = int(avail_gb // gb_per_worker)
    return max(1, min(cpu_count, by_mem))


def decide_nproc(env: Mapping[str, str] | None = None) -> int:
    """Resolve the starting worker count: explicit override else adaptive."""
    override = env_override(env)
    if override is not None:
        return override
    cpu_count = os.cpu_count() or 1
    return compute_nproc(available_gb(), cpu_count, per_worker_gb(env))


def is_oom_exit(returncode: int, output: str = "") -> bool:
    """True when the exit looks like an OOM kill (signal/137 or crashed worker)."""
    if returncode in _OOM_EXIT_CODES:
        return True
    low = output.lower()
    return any(marker in low for marker in _OOM_OUTPUT_MARKERS)


def _stream_run(cmd: Sequence[str]) -> tuple[int, str]:
    """Run ``cmd``, tee output to our stdout live, and capture it for OOM sniffing."""
    proc = subprocess.Popen(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    chunks: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        chunks.append(line)
    proc.wait()
    return proc.returncode, "".join(chunks)


def build_pytest_cmd(pytest_args: Sequence[str], nproc: int) -> list[str]:
    """``python -m pytest <args> -n <nproc> --dist loadgroup`` (no dup -n/--dist)."""
    args = list(pytest_args)
    cmd = [sys.executable, "-m", "pytest", *args]
    if not any(a == "-n" or a.startswith("-n") for a in args):
        cmd += ["-n", str(nproc)]
    if "--dist" not in args and not any(a.startswith("--dist") for a in args):
        cmd += ["--dist", "loadgroup"]
    return cmd


def run(
    pytest_args: Sequence[str],
    env: Mapping[str, str] | None = None,
    runner=_stream_run,
) -> int:
    """Run pytest at the adaptive worker count, halving + retrying on OOM exits."""
    nproc = decide_nproc(env)
    while True:
        cmd = build_pytest_cmd(pytest_args, nproc)
        print(
            f"[adaptive-test] running with -n {nproc} "
            f"(cmd: {' '.join(cmd[2:])})",
            flush=True,
        )
        returncode, output = runner(cmd)
        if not is_oom_exit(returncode, output):
            return returncode
        if nproc <= 1:
            print(
                "[adaptive-test] OOM-shaped exit at -n 1 (rc="
                f"{returncode}); cannot reduce workers further — giving up.",
                flush=True,
            )
            return returncode
        new_nproc = max(1, nproc // 2)
        print(
            f"[adaptive-test] OOM-shaped exit (rc={returncode}) at -n {nproc}; "
            f"retrying with -n {new_nproc}.",
            flush=True,
        )
        nproc = new_nproc


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
