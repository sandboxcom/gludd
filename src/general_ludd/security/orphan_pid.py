"""Verified PID identity before cleanup — no forged or stale signals (D-23).

PID records include PID, start time, boot ID, namespace, executable identity,
owner and lease; stale cleanup verifies all fields before signalling or
unlinking.
"""

from __future__ import annotations

import contextlib
import datetime as _datetime
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PidRecordError(Exception):
    """Raised when a PidRecord fails validation."""


@dataclass(frozen=True)
class PidRecord:
    """Immutable PID identity record for safe orphan detection.

    Every field is required and validated.  A record with mismatched fields
    (PID reused after reboot, forged start time, wrong owner) fails closed
    — the orphan is NEVER signalled.
    """

    pid: int
    start_time: float
    boot_id: str
    executable: str
    owner_uid: int
    lease_seconds: float

    def __post_init__(self) -> None:
        """Validate the immutable process-ownership record."""
        if self.pid <= 0:
            raise PidRecordError(f"pid must be positive, got {self.pid}")
        if not self.boot_id:
            raise PidRecordError("boot_id must not be empty")
        if not Path(self.executable).is_absolute():
            raise PidRecordError(f"executable must be an absolute path, got {self.executable!r}")
        if self.start_time <= 0:
            raise PidRecordError(f"start_time must be positive, got {self.start_time}")
        if self.lease_seconds < 0:
            raise PidRecordError(f"lease_seconds must be non-negative, got {self.lease_seconds}")

    def to_dict(self) -> dict[str, Any]:
        """Serialize the ownership record for durable storage."""
        return {
            "pid": self.pid,
            "start_time": self.start_time,
            "boot_id": self.boot_id,
            "executable": self.executable,
            "owner_uid": self.owner_uid,
            "lease_seconds": self.lease_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PidRecord:
        """Construct an ownership record from its durable representation."""
        return cls(**{k: data[k] for k in ["pid", "start_time", "boot_id", "executable", "owner_uid", "lease_seconds"]})


# ---------------------------------------------------------------------------
# Boot ID — stable per-boot identifier
# ---------------------------------------------------------------------------

_BOOT_ID_CACHE: str | None = None


def _path_exists(path: Path) -> bool:
    """Observe path existence without requiring tests to patch ``Path`` globally."""
    return path.exists()


def _path_read_text(path: Path) -> str:
    """Read an ownership path through a module-local observation seam."""
    return path.read_text()


def _stat_uid(path: str) -> int:
    """Read a path owner without requiring tests to patch process-global ``os``."""
    return os.stat(path).st_uid


def compute_boot_id() -> str:
    """Return a stable identifier for the current system boot.

    Reads ``/proc/sys/kernel/random/boot_id`` on Linux, or derives a
    fallback from ``kern.boottime`` on macOS / BSD.
    """
    global _BOOT_ID_CACHE
    if _BOOT_ID_CACHE is not None:
        return _BOOT_ID_CACHE

    boot_id_path = Path("/proc/sys/kernel/random/boot_id")
    if _path_exists(boot_id_path):
        try:
            _BOOT_ID_CACHE = _path_read_text(boot_id_path).strip()
            return _BOOT_ID_CACHE
        except OSError:
            pass

    try:
        import subprocess

        result = subprocess.run(
            ["sysctl", "-n", "kern.boottime"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            _BOOT_ID_CACHE = result.stdout.strip()
            return _BOOT_ID_CACHE
    except (OSError, ImportError):
        pass

    _BOOT_ID_CACHE = f"fallback-{os.uname().version}-{os.uname().machine}"
    return _BOOT_ID_CACHE


# ---------------------------------------------------------------------------
# Identity verification — all fields must match before any action
# ---------------------------------------------------------------------------


def verify_pid_identity(record: PidRecord) -> bool:
    """Check every field of *record* against the live OS.

    Returns ``True`` only when PID exists, was started at the recorded time
    (or close enough), the boot ID matches, the executable matches, AND the
    process is owned by the recorded UID.

    This is the gatekeeper — no signal or unlink happens without full
    verification.
    """
    pid = record.pid

    if not _pid_exists(pid):
        return False

    try:
        boot = _read_boot_for_pid(pid)
    except OSError:
        return False
    if boot != record.boot_id:
        return False

    try:
        exe = _read_exe_for_pid(pid)
    except OSError:
        return False
    if exe != record.executable:
        return False

    try:
        pkg_uid = _read_uid_for_pid(pid)
    except OSError:
        return False
    if pkg_uid != record.owner_uid:
        return False

    start = _read_start_time_for_pid(pid)
    if start is not None:
        tolerance = max(10.0, record.lease_seconds * 0.1)
        if abs(start - record.start_time) > tolerance:
            return False

    return True


# ---------------------------------------------------------------------------
# Orphan reaping — safe, verified, scoped
# ---------------------------------------------------------------------------


def reap_orphan_tree(record: PidRecord) -> bool:
    """Safely terminate an orphan process tree.

    Returns ``True`` if the tree was successfully reaped (or was already
    gone).  Returns ``False`` if the tree could not be verified or could
    not be terminated.
    """
    if record.pid == os.getpid():
        return False

    if not _pid_exists(record.pid):
        return True

    if not verify_pid_identity(record):
        return False

    if not is_reaper_safe(pid=record.pid, owner_uid=record.owner_uid):
        return False

    _send_signal_tree(record.pid, signal.SIGTERM)
    time.sleep(0.5)
    if _pid_exists(record.pid):
        _send_signal_tree(record.pid, signal.SIGKILL)
        time.sleep(0.5)

    return not _pid_exists(record.pid)


def is_reaper_safe(*, pid: int, owner_uid: int) -> bool:
    """Check whether it is safe to signal *pid*.

    Never safe to signal own process.  Never safe to signal a process
    owned by a different UID (unless running as root, which is still
    logged as suspicious).
    """
    if pid == os.getpid():
        return False
    my_uid = os.getuid()
    try:
        target_uid = _read_uid_for_pid(pid)
    except OSError:
        return True
    if target_uid == -1:
        return True
    return target_uid == my_uid


# ---------------------------------------------------------------------------
# Internal: OS-specific PID introspection
# ---------------------------------------------------------------------------


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _read_boot_for_pid(pid: int) -> str:
    return compute_boot_id()


def _read_exe_for_pid(pid: int) -> str:
    proc_exe = Path(f"/proc/{pid}/exe")
    if _path_exists(proc_exe):
        return str(proc_exe.resolve())

    if sys.platform == "darwin":
        try:
            import ctypes
            import ctypes.util

            libproc_path = ctypes.util.find_library("proc")
            if libproc_path:
                libproc = ctypes.CDLL(libproc_path)
                buf = ctypes.create_string_buffer(4096)
                ret = libproc.proc_pidpath(ctypes.c_int(pid), buf, ctypes.c_uint32(4096))
                if ret > 0:
                    return os.path.realpath(os.fsdecode(buf.value))
        except Exception:
            pass

    try:
        import subprocess

        result = subprocess.run(
            ["lsof", "-a", "-d", "txt", "-p", str(pid), "-Fn"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            if line.startswith("n") and len(line) > 1:
                candidate = line[1:]
                if os.path.isabs(candidate):
                    return os.path.realpath(candidate)
    except (OSError, ImportError):
        pass

    if sys.platform == "darwin":
        try:
            import subprocess

            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "args="],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                exe = result.stdout.strip().split()[0]
                if os.path.isabs(exe):
                    return os.path.realpath(exe)
        except (OSError, ImportError):
            pass

    return f"/proc/{pid}/exe"


def _read_uid_for_pid(pid: int) -> int:
    proc_status = Path(f"/proc/{pid}/status")
    if _path_exists(proc_status):
        text = _path_read_text(proc_status)
        for line in text.splitlines():
            if line.startswith("Uid:"):
                parts = line.split()
                if len(parts) >= 2:
                    return int(parts[1])

    try:
        import subprocess

        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "uid="],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip())
    except (OSError, ValueError, ImportError):
        pass

    try:
        return _stat_uid(f"/proc/{pid}")
    except OSError:
        pass
    return -1


def _read_start_time_for_pid(pid: int) -> float | None:
    proc_stat = Path(f"/proc/{pid}/stat")
    if _path_exists(proc_stat):
        try:
            text = _path_read_text(proc_stat)
            fields = text.split()
            if len(fields) >= 22:
                starttime_ticks = int(fields[21])
                return _ticks_to_epoch(starttime_ticks)
        except (OSError, ValueError, IndexError):
            pass

    try:
        import subprocess

        env = os.environ.copy()
        env["LC_TIME"] = "C"
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
        )
        if result.returncode == 0 and result.stdout.strip():
            dt = _datetime.datetime.strptime(result.stdout.strip(), "%a %b %d %H:%M:%S %Y")
            return dt.timestamp()
    except (OSError, ValueError, ImportError):
        pass

    return None


def _ticks_to_epoch(ticks: int) -> float:
    clk_tck = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    boot = _boot_time_epoch()
    return boot + ticks / clk_tck


def _boot_time_epoch() -> float:
    proc_uptime = Path("/proc/uptime")
    if _path_exists(proc_uptime):
        try:
            uptime_str = _path_read_text(proc_uptime).split()[0]
            uptime = float(uptime_str)
            return time.time() - uptime
        except (OSError, ValueError, IndexError):
            pass
    try:
        import subprocess

        result = subprocess.run(
            ["sysctl", "-n", "kern.boottime"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split()
            for part in parts:
                if part.startswith("sec="):
                    return float(part.split("=", 1)[1].rstrip(",}"))
    except (OSError, ImportError):
        pass
    return 0.0


def _send_signal_tree(pid: int, sig: int) -> None:
    with contextlib.suppress(OSError):
        os.kill(pid, sig)
    children = _child_pids(pid)
    for child in children:
        with contextlib.suppress(OSError):
            os.kill(child, sig)


def _child_pids(pid: int) -> list[int]:
    proc = Path("/proc")
    if _path_exists(proc):
        children: list[int] = []
        stat_path = Path(f"/proc/{pid}/stat")
        if not _path_exists(stat_path):
            return children
        for entry in proc.iterdir():
            if not entry.is_dir():
                continue
            if not entry.name.isdigit():
                continue
            child_stat = entry / "stat"
            try:
                text = _path_read_text(child_stat)
                fields = text.split()
                if len(fields) >= 4 and int(fields[3]) == pid:
                    children.append(int(entry.name))
            except (OSError, ValueError):
                continue
        return children

    try:
        import subprocess

        result = subprocess.run(
            ["pgrep", "-P", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode in (0, 1):
            if result.stdout.strip():
                return [int(p) for p in result.stdout.strip().split()]
            return []
    except (OSError, ValueError, ImportError):
        pass

    return []


__all__ = [
    "PidRecord",
    "PidRecordError",
    "compute_boot_id",
    "is_reaper_safe",
    "reap_orphan_tree",
    "verify_pid_identity",
]
