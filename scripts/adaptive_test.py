#!/usr/bin/env python3
"""Memory-bounded pytest runner: size xdist workers by AVAILABLE RAM.

The default ``make test`` used ``pytest -n auto`` which spawns one worker PER CPU
CORE. Each gludd worker resident set is ~1.2-1.5 GiB, so ``cores x RSS`` routinely
exceeds physical memory and the kernel OOM-kills the run (SIGKILL / exit 137).

This runner instead computes the worker count from *available* memory AND the
current system LOAD, so a run never over-commits RAM and never drives the box's
5-minute load average above ~2.5x the core count:

    by_mem  = available_gb // PER_WORKER_GB
    by_load = 2.5 * cores - load5          # load headroom
    n       = max(1, min(cores, by_mem, by_load))

so the total working set stays within RAM and the machine stays responsive
(this is the local-OOM fix — a heavily-loaded laptop no longer piles a full
``-n auto`` fan-out on top of existing load). On CI (``CI=true``) the load cap
is BYPASSED and workers are sized by ``min(cores, by_mem)`` only: a CI shard is
a fresh, isolated single-purpose runner with ample per-shard RAM and no
competing load (and is sharded so it won't OOM), where the load cap only
over-throttles — on a 2-core runner it collapsed a shard to ``-n 1`` and ran
30+ min. The RAM cap and the OOM-halve-retry backstop still apply on CI. It also
DETECTS an OOM-shaped exit
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

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress

DEFAULT_PER_WORKER_GB = 1.5
# Load headroom: never let the run drive the 5-minute load average above this
# multiple of the core count. Matches system.monitor.can_start_process's default
# ``threshold_multiplier`` so the two capacity guards agree.
LOAD_HEADROOM_MULTIPLIER = 2.5
# Exit codes that indicate the OS killed the process (or a worker) — OOM-shaped.
_OOM_EXIT_CODES = frozenset({-9, 137})
_OOM_DIAGNOSTIC_LINES = (
    re.compile(
        r"^\s*\[gw\d+\]\s+node down:\s+Not properly terminated\s*$",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*replacing crashed worker\s+gw\d+\s*$", re.IGNORECASE),
    re.compile(
        r"^\s*worker\s+gw\d+\s+crashed while running(?:\s+\S.*)?\s*$",
        re.IGNORECASE,
    ),
)
DEFAULT_HEARTBEAT_SECS = 30.0


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
    """Worker count sized by available RAM AND system load, floored at 1, capped at cpu_count.

    Three independent caps are taken, and the smallest wins:

    * ``by_mem``  — one worker per ``gb_per_worker`` GiB of *available* RAM (the
      original behaviour). When ``avail_gb`` is None (no psutil) or
      ``gb_per_worker`` is non-positive this cap falls back to ``cpu_count``.
    * ``by_load`` — load headroom: ``LOAD_HEADROOM_MULTIPLIER * cores - load5``,
      so the run does not push the 5-minute load average past ~2.5x cores. This
      is read from ``general_ludd.system.monitor``; if that package is not
      importable (e.g. the script is run standalone outside the venv) the load
      cap is simply skipped (fail-open to RAM-only sizing) rather than crashing.
    * ``cpu_count`` — never more workers than cores.

    The result is always ``>= 1``.

    On CI (``CI=true``) the load cap is BYPASSED and sizing is
    ``min(cpu_count, by_mem)`` only. The load cap (#45) exists to keep a shared
    LOCAL box responsive by not piling a full ``-n auto`` fan-out on top of the
    developer's existing load. A CI shard is the opposite case: a fresh, isolated
    single-purpose runner with ample per-shard RAM and no competing load, split so
    it does not OOM — there the load cap only over-throttles (e.g. on a 2-core
    runner it collapses to ``-n 1`` and a shard runs 30+ min). The RAM cap and the
    OOM-halve-retry backstop still apply on CI, so an isolated shard is never
    left unbounded.
    """
    cpu_count = max(1, cpu_count)

    # RAM cap (original behaviour).
    by_mem = (
        cpu_count
        if avail_gb is None or gb_per_worker <= 0
        else max(1, int(avail_gb // gb_per_worker))
    )

    # CI shards are isolated (fresh, single-purpose runner, ample per-shard RAM,
    # no competing load) and don't OOM, so the shared-LOCAL-box load cap only
    # over-throttles them. Size by RAM + cores only; the RAM cap and the
    # OOM-halve-retry backstop below still guard against over-commit.
    if os.environ.get("CI") == "true":
        return max(1, min(cpu_count, by_mem))

    # Load cap. Fail-open (no load cap) if the system monitor is unavailable so
    # the script still runs standalone / outside the general_ludd venv.
    by_load = cpu_count
    try:
        from general_ludd.system.monitor import get_load_average
    except ImportError:
        pass
    else:
        load5 = get_load_average()[1]  # 5-minute load average
        by_load = max(1, int(LOAD_HEADROOM_MULTIPLIER * cpu_count - load5))

    return max(1, min(cpu_count, by_mem, by_load))


def decide_nproc(env: Mapping[str, str] | None = None) -> int:
    """Resolve the starting worker count: explicit override else adaptive."""
    override = env_override(env)
    if override is not None:
        return override
    cpu_count = os.cpu_count() or 1
    return compute_nproc(available_gb(), cpu_count, per_worker_gb(env))


def is_oom_exit(returncode: int, output: str = "") -> bool:
    """True only for an OOM signal/code or a complete xdist crash diagnostic."""
    if returncode in _OOM_EXIT_CODES:
        return True
    if returncode == 0:
        return False
    return any(
        pattern.fullmatch(line)
        for line in output.splitlines()
        for pattern in _OOM_DIAGNOSTIC_LINES
    )


def heartbeat_interval_seconds(env: Mapping[str, str] | None = None) -> float:
    """Return the quiet-period heartbeat interval, defaulting on bad input."""
    env = os.environ if env is None else env
    raw = env.get("GLUDD_ADAPTIVE_HEARTBEAT_SECS")
    if raw:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = DEFAULT_HEARTBEAT_SECS
        if value > 0:
            return value
    return DEFAULT_HEARTBEAT_SECS


def progress_file_path(env: Mapping[str, str] | None = None) -> str:
    """Resolve the durable progress path, isolated per adaptive runner process."""
    env = os.environ if env is None else env
    configured = env.get("GLUDD_ADAPTIVE_PROGRESS_FILE")
    if configured:
        return configured
    root = env.get("TMPDIR") or tempfile.gettempdir()
    return os.path.join(root, f"gludd-adaptive-test-{os.getpid()}.json")


def _persist_progress(path: str, payload: Mapping[str, object]) -> None:
    """Atomically persist progress; telemetry failures never affect test runs."""
    temporary: str | None = None
    try:
        parent = os.path.dirname(path) or "."
        os.makedirs(parent, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".adaptive-progress-", dir=parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(dict(payload), stream, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
        temporary = None
    except Exception:
        pass
    finally:
        if temporary is not None:
            with suppress(OSError):
                os.unlink(temporary)


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
    progress_path = progress_file_path()
    started = time.monotonic()
    progress_lock = threading.Lock()
    progress: dict[str, object] = {
        "pid": os.getpid(),
        "status": "running",
        "started_at": time.time(),
        "updated_at": time.time(),
        "lines": 0,
        "bytes": 0,
        "heartbeat_count": 0,
    }
    _persist_progress(progress_path, progress)
    stop_heartbeat = threading.Event()

    def emit_heartbeat() -> None:
        interval = heartbeat_interval_seconds()
        while not stop_heartbeat.wait(interval):
            with progress_lock:
                progress["heartbeat_count"] = int(progress["heartbeat_count"]) + 1
                progress["updated_at"] = time.time()
                snapshot = dict(progress)
                lines = int(progress["lines"])
                elapsed = time.monotonic() - started
            _persist_progress(progress_path, snapshot)
            print(
                f"[adaptive-test] heartbeat elapsed={elapsed:.0f}s lines={lines}",
                flush=True,
            )

    heartbeat = threading.Thread(target=emit_heartbeat, name="adaptive-test-heartbeat", daemon=True)
    heartbeat.start()
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            chunks.append(line)
            with progress_lock:
                progress["lines"] = int(progress["lines"]) + 1
                progress["bytes"] = int(progress["bytes"]) + len(line.encode("utf-8"))
                progress["updated_at"] = time.time()
        proc.wait()
    finally:
        stop_heartbeat.set()
        heartbeat.join(timeout=1.0)
        with progress_lock:
            progress["status"] = "finished"
            progress["returncode"] = proc.returncode
            progress["elapsed_seconds"] = round(time.monotonic() - started, 3)
            progress["updated_at"] = time.time()
            snapshot = dict(progress)
        _persist_progress(progress_path, snapshot)
    return proc.returncode, "".join(chunks)


def has_basetemp(args: Sequence[str]) -> bool:
    """True if the caller already pinned pytest's ``--basetemp`` (either form)."""
    return any(a == "--basetemp" or a.startswith("--basetemp=") for a in args)


def unique_basetemp() -> str:
    """A short, process-unique pytest ``--basetemp`` path.

    Returns a path (NOT created here — pytest creates and, if it exists, wipes
    the basetemp at startup) that no other pytest process can share. POSIX runs
    use canonical ``/tmp`` instead of an inherited, potentially deep ``TMPDIR``,
    preserving headroom for xdist and AF_UNIX socket suffixes.

    WHY THIS EXISTS — the CI shared-tmp-root race that produced ~86
    ``FileNotFoundError: /tmp/pytest-of-<user>/pytest-0/popen-gwN`` errors:

    With no ``--basetemp``, every pytest process (this shard's outer run AND any
    NESTED pytest a test spawns as a subprocess — e.g. ``MakeRunner.spawn`` ->
    ``make test-specific`` -> ``python -m pytest`` via
    ``runner.background_test_runner``) computes the SAME default numbered-tmp
    root ``<gettempdir()>/pytest-of-<user>`` and inherits the same ``TMPDIR``.
    pytest's ``tmp_path_factory`` keeps only the last N (default 3)
    ``pytest-<N>`` roots and garbage-collects older ones at startup (rename to
    ``garbage-<uuid>`` then ``rm_rf``). A nested pytest starting mid-run creates
    new ``pytest-<N>`` dirs and, once the keep-window is exceeded, DELETES the
    outer run's ``pytest-0`` — the very directory this shard's live xdist
    workers hold ``popen-gwN`` under — yielding ``FileNotFoundError`` for every
    subsequent ``tmp_path`` request. A serial run cannot reproduce it because it
    needs a SECOND concurrent pytest process under the shared root.

    Pinning a unique ``--basetemp`` moves this run's worker dirs OUT of the
    shared ``pytest-of-<user>`` root, so no sibling pytest's numbered-dir GC can
    ever reach them — the race is structurally impossible regardless of which
    test spawns a nested pytest. This is the same isolation ``scripts/run_gate.sh``
    already applies to the local gate (unique ``mktemp -d`` basetemp).
    """
    root = (
        "/tmp"
        if os.name == "posix" and os.path.isdir("/tmp")
        else tempfile.gettempdir()
    )
    token = uuid.uuid4().hex[:8]
    return os.path.join(root, f"gludd-at-{os.getpid()}-{token}")


def build_pytest_cmd(pytest_args: Sequence[str], nproc: int) -> list[str]:
    """``python -m pytest <args> -n <nproc> --maxprocesses <nproc> --dist loadgroup``.

    ``--maxprocesses`` bounds the number of live xdist worker processes even
    across respawns (xdist replaces a crashed worker), so an OOM/crash loop can
    never exceed the RAM/load-sized worker count. Existing ``-n`` / ``--maxprocesses``
    / ``--dist`` in ``pytest_args`` are respected (no duplicates appended).
    """
    args = list(pytest_args)
    cmd = [sys.executable, "-m", "pytest", *args]
    if not any(a == "-n" or a.startswith("-n") for a in args):
        cmd += ["-n", str(nproc)]
    if not any(a == "--maxprocesses" or a.startswith("--maxprocesses") for a in args):
        cmd += ["--maxprocesses", str(nproc)]
    if "--dist" not in args and not any(a.startswith("--dist") for a in args):
        cmd += ["--dist", "loadgroup"]
    return cmd


def run(
    pytest_args: Sequence[str],
    env: Mapping[str, str] | None = None,
    runner=_stream_run,
) -> int:
    """Run pytest at the adaptive worker count, halving + retrying on OOM exits."""
    # Isolate this run's tmp tree from every other pytest process on the box.
    # Without a pinned basetemp the shard's outer run shares pytest's default
    # ``pytest-of-<user>`` numbered-tmp root with any nested pytest a test
    # spawns, whose startup GC can delete this run's live ``popen-gwN`` worker
    # dirs mid-flight (see ``unique_basetemp`` for the full mechanism). Computed
    # ONCE and reused across OOM-halving retries: each retry wipes+recreates the
    # same basetemp, and it stays unique to THIS process.
    args = list(pytest_args)
    if not has_basetemp(args):
        args.append(f"--basetemp={unique_basetemp()}")
    nproc = decide_nproc(env)
    while True:
        cmd = build_pytest_cmd(args, nproc)
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
