"""Daemon PID, validation, and process-launch helpers."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import signal
import sys
import time
from typing import Any

from general_ludd.db.session import get_default_db_url, is_sqlite_url

_BUNDLED_GUNICORN_FLAG = "--_gludd-bundled-gunicorn"


def _write_daemon_pid_file(pid_file: str, pid: int, daemon_url: str) -> None:
    os.makedirs(os.path.dirname(pid_file), exist_ok=True)
    data = {"pid": pid, "daemon_url": daemon_url}
    with open(pid_file, "w") as f:
        json.dump(data, f)


def _read_daemon_pid_file(pid_file: str) -> dict[str, Any] | None:
    try:
        with open(pid_file) as f:
            data = json.load(f)
            if isinstance(data, dict) and "pid" in data:
                return data
    except (json.JSONDecodeError, FileNotFoundError, OSError):
        return None
    return None


def _is_daemon_pid_alive(pid_file: str) -> bool:
    data = _read_daemon_pid_file(pid_file)
    if data is None:
        return False
    try:
        os.kill(data["pid"], 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _stop_daemon_via_pid_file(pid_file: str) -> bool:
    data = _read_daemon_pid_file(pid_file)
    if data is None:
        return False
    pid = data["pid"]
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            try:
                os.kill(pid, 0)
                time.sleep(0.1)
            except (OSError, ProcessLookupError):
                break
        else:
            os.kill(pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    with contextlib.suppress(OSError):
        os.unlink(pid_file)
    return True


# --------------------------------------------------------------------------- #
# Daemon-spawn input hardening
#
# _cmd_daemon spawns the daemon via subprocess.Popen(cmd, start_new_session=True,
# close_fds=True). Even though Popen is given a *list* argv (no shell), the host
# and port flow into the "--bind HOST:PORT" token and the log-level / path args
# flow into the child's environment. None of those are trusted (they come from
# CLI args), so each is validated against a strict whitelist BEFORE it can reach
# the spawned process. A bad value fails closed with ValueError rather than
# smuggling shell metacharacters, NUL bytes, extra argv flags, or out-of-range
# values into the daemon.
# --------------------------------------------------------------------------- #

_LOG_LEVEL_ALLOWLIST = frozenset({"debug", "info", "warning", "error"})

# A hostname label per RFC 952/1123: alphanumerics and hyphens, not starting or
# ending with a hyphen, 1-63 chars; labels joined by dots. We also accept bare
# IPv4 / IPv6 literals (validated via ipaddress below).
_HOSTNAME_LABEL_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")


def _validate_daemon_host(host: str) -> str:
    """Return ``host`` if it is a safe hostname / IP literal, else raise.

    Rejects anything that is not a plain hostname or IP: shell metacharacters,
    whitespace, embedded argv flags, NUL bytes, etc. cannot pass.
    """
    import ipaddress

    if not isinstance(host, str) or not host:
        raise ValueError("daemon host must be a non-empty string")
    if len(host) > 255:
        raise ValueError("daemon host is too long")
    # IPv4 / IPv6 literal?
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    # Otherwise must be a dotted hostname of valid labels. This forbids spaces,
    # ';', '&', '|', '$', '`', '(', ')', newlines, leading '-' (argv flag), etc.
    labels = host.split(".")
    if all(_HOSTNAME_LABEL_RE.match(label) for label in labels):
        return host
    raise ValueError(f"invalid daemon host: {host!r}")


def _validate_daemon_port(port: int) -> int:
    """Return ``port`` as an int in the TCP range 1-65535, else raise.

    A bool, a non-numeric string, or an out-of-range value fails closed.
    """
    # bool is an int subclass; reject it explicitly so True/False can't slip in.
    if isinstance(port, bool):
        raise ValueError(f"invalid daemon port: {port!r}")
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        raise ValueError(f"invalid daemon port: {port!r}") from None
    # int("80a0") already raises; but int(8000.9) would truncate, so require the
    # original to be an int when it isn't a clean decimal string.
    if isinstance(port, str) and not port.isdigit():
        raise ValueError(f"invalid daemon port: {port!r}")
    if not (1 <= port_int <= 65535):
        raise ValueError(f"daemon port out of range (1-65535): {port_int}")
    return port_int


def _validate_daemon_log_level(log_level: str) -> str:
    """Return the normalized (lowercase) log level if allowlisted, else raise."""
    if not isinstance(log_level, str):
        raise ValueError(f"invalid daemon log-level: {log_level!r}")
    normalized = log_level.lower()
    if normalized not in _LOG_LEVEL_ALLOWLIST:
        raise ValueError(
            f"invalid daemon log-level: {log_level!r} (allowed: {', '.join(sorted(_LOG_LEVEL_ALLOWLIST))})"
        )
    return normalized


def _validate_daemon_path(value: str, *, name: str) -> str:
    """Return ``value`` if it is a safe path arg, else raise.

    The path is passed to the child via an environment variable, so it must not
    contain NUL bytes, newlines/carriage returns (which could forge additional
    env entries), or shell-command-substitution metacharacters. The path is not
    required to exist, but it must be a single confined token.
    """
    if not isinstance(value, str):
        raise ValueError(f"invalid {name} path: {value!r}")
    if "\x00" in value:
        raise ValueError(f"{name} path contains a NUL byte")
    if any(ch in value for ch in ("\n", "\r")):
        raise ValueError(f"{name} path contains a newline")
    if any(ch in value for ch in (";", "`", "$", "|", "&")):
        raise ValueError(f"{name} path contains a forbidden metacharacter: {value!r}")
    return value


def _build_daemon_env(
    config_dir: str | None = None,
    templates_dir: str | None = None,
    playbooks_dir: str | None = None,
    tick_interval: float = 1.0,
    log_level: str = "info",
    psk: str = "",
) -> dict[str, str]:
    env: dict[str, str] = {}
    if config_dir:
        env["GLUDD_CONFIG_DIR"] = _validate_daemon_path(config_dir, name="config-dir")
    if templates_dir:
        env["GLUDD_TEMPLATES_DIR"] = _validate_daemon_path(templates_dir, name="templates-dir")
    if playbooks_dir:
        env["GLUDD_PLAYBOOKS_DIR"] = _validate_daemon_path(playbooks_dir, name="playbooks-dir")
    if tick_interval != 1.0:
        env["GLUDD_TICK_INTERVAL"] = str(tick_interval)
    normalized_level = _validate_daemon_log_level(log_level)
    if normalized_level != "info":
        env["GLUDD_LOG_LEVEL"] = normalized_level
    env["GLUDD_AUTH_PSK"] = psk
    return env


def _clamp_workers_for_sqlite(
    workers: int | None,
    *,
    database_url: str | None = None,
) -> int:
    """Clamp SQLite to one worker and permit bounded PostgreSQL concurrency.

    Each gunicorn worker spawns its own event loop + in-memory stores; with a
    single SQLite file there is no cross-process claim coordination, so N>1 is
    dishonest (duplicate dispatch, racing writers). PostgreSQL claims use row
    locking plus guarded updates, so explicit worker counts are preserved and
    the default is bounded to four workers to keep connection usage predictable.
    """
    resolved_url = database_url or os.environ.get("DATABASE_URL") or get_default_db_url()
    if not is_sqlite_url(resolved_url):
        requested = workers if workers is not None else min(4, os.cpu_count() or 1)
        return max(1, requested)
    if workers is None:
        return 1
    if workers > 1:
        logging.getLogger(__name__).warning(
            "Requested %d workers but general_ludd is SQLite-only "
            "(no cross-process claim coordination); clamping to 1 worker.",
            workers,
        )
        return 1
    return max(1, workers)


def _run_bundled_gunicorn_if_requested() -> bool:
    """Run Gunicorn inside a frozen bundle when its private flag is present."""
    if not bool(getattr(sys, "frozen", False)):
        return False
    if len(sys.argv) < 2 or sys.argv[1] != _BUNDLED_GUNICORN_FLAG:
        return False

    from gunicorn.app import wsgiapp

    sys.argv = [sys.argv[0], *sys.argv[2:]]
    wsgiapp.run()
    return True


def _daemon_child_stdio() -> tuple[int | None, int | None]:
    """Return observable stdio for a frozen child and quiet source defaults."""
    import subprocess

    if bool(getattr(sys, "frozen", False)):
        return None, None
    return subprocess.DEVNULL, subprocess.DEVNULL


def _build_daemon_start_cmd(
    host: str = "127.0.0.1",
    port: int = 8000,
    workers: int | None = None,
) -> list[str]:
    # Harden every CLI-derived token before it can reach the spawned process.
    # host/port feed the "--bind HOST:PORT" argv token; validate them so a
    # malicious --host/--port cannot inject shell metacharacters or extra argv
    # flags (Popen gets a list, but a value like "1.2.3.4 --bind 0.0.0.0:80"
    # would still split into rogue tokens via the bind string otherwise).
    safe_host = _validate_daemon_host(host)
    safe_port = _validate_daemon_port(port)
    workers = _clamp_workers_for_sqlite(workers)
    launcher = [sys.executable, _BUNDLED_GUNICORN_FLAG] if bool(getattr(sys, "frozen", False)) else ["gunicorn"]
    argv: list[str] = [
        *launcher,
        "general_ludd.daemon:create_daemon_app()",
        "--worker-class",
        "uvicorn_worker.UvicornWorker",
        "--workers",
        str(workers),
        "--bind",
        f"{safe_host}:{safe_port}",
    ]
    return argv

write_daemon_pid_file = _write_daemon_pid_file
read_daemon_pid_file = _read_daemon_pid_file
is_daemon_pid_alive = _is_daemon_pid_alive
stop_daemon_via_pid_file = _stop_daemon_via_pid_file
validate_daemon_host = _validate_daemon_host
validate_daemon_port = _validate_daemon_port
validate_daemon_log_level = _validate_daemon_log_level
validate_daemon_path = _validate_daemon_path
build_daemon_env = _build_daemon_env
clamp_workers_for_sqlite = _clamp_workers_for_sqlite
run_bundled_gunicorn_if_requested = _run_bundled_gunicorn_if_requested
daemon_child_stdio = _daemon_child_stdio
build_daemon_start_cmd = _build_daemon_start_cmd
