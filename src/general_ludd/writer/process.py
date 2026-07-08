"""Parent-side lifecycle for the writer subprocess (B3.1.3 Slice 1 — WP-B1).

``WriterProcess`` owns the start / readiness-handshake / stop / health-poll of
the writer child. The child itself is a stub for Slice 1 — it writes the
parent-generated readiness nonce and sleeps; the real EventLoop integration
lands in Slice 3.

Design mirrors the A/B-test runner's subprocess pattern
(:mod:`general_ludd.abtest.runner`): the parent generates a fresh random
nonce per spawn, hands it AND a dedicated readiness-file path to the child,
and only considers the child "ready" once the child has written that exact
nonce back into the readiness file. ``exit 0`` alone is never a readiness
signal — a child that segfaults before writing the nonce is not ready, even
if the parent happens to observe a clean exit later.

Stop escalation: SIGTERM, wait up to ``sigterm_timeout`` seconds (default
10s), then SIGKILL. ``stop()`` is idempotent.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["WriterProcess"]

# Default wall-clock the parent waits for the child's readiness nonce. 30s
# matches the work-package spec; generous enough to absorb cold-cache
# interpreter + import time, tight enough that a hung child is caught.
DEFAULT_READY_TIMEOUT = 30.0

# Default grace period after SIGTERM before escalating to SIGKILL. The child
# gets this long to drain its WriteQueue + flush its DB session on a clean
# shutdown; past it, the parent forces the issue so a wedged child cannot
# pin the daemon.
DEFAULT_SIGTERM_TIMEOUT = 10.0

# Polling cadence for both the readiness file and the SIGTERM wait. Coarse on
# purpose — finer-grained polling buys nothing here and burns CPU.
_POLL_INTERVAL = 0.05


class WriterProcess:
    """Owns the lifecycle of one writer subprocess.

    Lifecycle::

        wp = WriterProcess(config={...})
        wp.start()         # spawn + readiness handshake
        ...
        wp.is_alive()      # health check
        ...
        wp.stop()          # SIGTERM -> wait -> SIGKILL (idempotent)

    Not thread-safe: one ``WriterProcess`` per child. If you need concurrent
    writers, instantiate one ``WriterProcess`` per.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        if not isinstance(config, dict):
            raise TypeError("config must be a dict")
        self._config: dict[str, Any] = dict(config)

        # Per-spawn UNFORGEABLE readiness token. Fresh random nonce; the child
        # writes it to ``_ready_path`` only after it has finished booting. A
        # child that crashes before booting never writes the nonce, so the
        # parent never claims readiness on a broken/hung child.
        self._readiness_nonce: str = secrets.token_hex(32)

        self._proc: subprocess.Popen[bytes] | None = None
        self._argv: list[str] = []
        self._started: bool = False
        self._stopped: bool = False
        self._ready: bool = False

        # Temp paths owned by the parent; cleaned up on stop / GC. Allocated
        # lazily in ``start()`` so an unused WriterProcess leaves no FS litter.
        self._config_path: str | None = None
        self._ready_path: str | None = None

    # ------------------------------------------------------------------ #
    # Properties (read-only views)
    # ------------------------------------------------------------------ #
    @property
    def pid(self) -> int | None:
        """Live child PID, or None if not running."""
        if self._proc is None:
            return None
        if self._proc.poll() is not None:
            return None
        return self._proc.pid

    @property
    def config(self) -> dict[str, Any]:
        """The caller-supplied config dict (a defensive copy)."""
        return dict(self._config)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start(self, timeout: float = DEFAULT_READY_TIMEOUT) -> bool:
        """Spawn the writer child and block until it signals readiness.

        Readiness is the parent-generated nonce written by the child into a
        parent-controlled temp file. A child that dies before writing the
        nonce causes ``start()`` to kill the orphan and raise
        :class:`TimeoutError`.

        Returns True on success. Raises :class:`RuntimeError` if called on an
        already-started instance (double-start is a programming error, not a
        silent no-op). Raises :class:`TimeoutError` if the child does not
        complete the handshake within ``timeout`` seconds.
        """
        if self._started:
            raise RuntimeError(
                "WriterProcess.start() called twice — spawn a new instance "
                "instead of reusing one"
            )

        # Persist the config to a temp file the child can read by path.
        # mkstemp rather than NamedTemporaryFile so the child can re-open it
        # cross-process on every platform without the parent holding it open.
        cfg_fd, cfg_path = tempfile.mkstemp(prefix="writer_cfg_", suffix=".json")
        try:
            with os.fdopen(cfg_fd, "w", encoding="utf-8") as fh:
                json.dump(self._config, fh)
        except Exception:
            os.unlink(cfg_path)
            raise
        self._config_path = cfg_path

        # Parent-owned, parent-named readiness file. The child framework
        # writes the nonce here; only the parent ever reads it back.
        ready_fd, ready_path = tempfile.mkstemp(prefix="writer_ready_", suffix=".json")
        os.close(ready_fd)
        # Truncate to empty so a stale-write detector sees no spurious nonce.
        with contextlib.suppress(OSError):
            os.truncate(ready_path, 0)
        self._ready_path = ready_path

        argv = [
            sys.executable,
            "-m",
            "general_ludd.writer._child",
            cfg_path,
            ready_path,
            self._readiness_nonce,
        ]
        self._argv = argv

        logger.info("WriterProcess.start(): spawning child pid=? argv=%s", argv)
        # shell=False (default) is required: argv is a flat list, no shell
        # parses it, so any string in the config is inert.
        proc: subprocess.Popen[bytes] = subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        self._proc = proc
        self._started = True
        logger.info("WriterProcess.start(): child spawned pid=%d", proc.pid)

        try:
            ready = self.wait_ready(timeout=timeout)
        except Exception:
            self._kill_child(force=True)
            self._proc = None
            raise

        if not ready:
            # Child never wrote the nonce within the deadline. Kill it
            # (otherwise it would leak as an orphan), then raise — never
            # silently claim ready=True.
            self._kill_child(force=True)
            self._proc = None
            raise TimeoutError(
                f"writer child pid={proc.pid} did not signal readiness within "
                f"{timeout}s — killed"
            )

        self._ready = True
        logger.info("WriterProcess.start(): child pid=%d ready", proc.pid)
        return True

    def wait_ready(self, timeout: float) -> bool:
        """Poll the readiness file until the child writes our exact nonce.

        Returns True when the nonce matches; False if the deadline elapses
        without a match. Also returns False if the child exits before writing
        the nonce (a crash mid-boot is not readiness).
        """
        if self._proc is None or self._ready_path is None:
            return False

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # If the child died mid-handshake, no amount of waiting will
            # produce a nonce — bail out immediately.
            if self._proc.poll() is not None:
                logger.warning(
                    "WriterProcess: child pid=%d exited before readiness nonce",
                    self._proc.pid,
                )
                return False
            if self._readiness_file_matches():
                return True
            time.sleep(_POLL_INTERVAL)

        return False

    def stop(self, sigterm_timeout: float = DEFAULT_SIGTERM_TIMEOUT) -> bool:
        """Terminate the writer child: SIGTERM, wait, escalate to SIGKILL.

        Idempotent — calling ``stop()`` repeatedly never raises and always
        returns True. Safe to call on an instance whose ``start()`` raised
        or was never invoked.
        """
        if self._proc is None:
            # Already stopped, or never started — both are success.
            self._stopped = True
            return True

        if self._stopped and self._proc.poll() is not None:
            return True

        proc = self._proc
        # Try SIGTERM first.
        self._terminate_child(sigterm_timeout)

        # If SIGTERM didn't reap it, force SIGKILL.
        if proc.poll() is None:
            self._kill_child(force=True)

        # Wait deterministically for the OS to reap.
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5.0)

        self._stopped = True
        self._cleanup_files()
        logger.info("WriterProcess.stop(): child pid=%d reaped", proc.pid)
        return True

    def is_alive(self) -> bool:
        """True iff the child process is currently running."""
        if self._proc is None:
            return False
        return self._proc.poll() is None

    def is_ready(self) -> bool:
        """True iff the child completed the readiness handshake successfully."""
        return self._ready

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _readiness_file_matches(self) -> bool:
        """Constant-time compare the readiness file against the parent nonce.

        The readiness payload is JSON ``{"nonce": "<hex>"}`` (mirroring the
        A/B runner's result-file shape). A missing / malformed / wrong-nonce
        file is a non-match — fail-closed.
        """
        assert self._ready_path is not None
        try:
            with open(self._ready_path, "rb") as fh:
                raw = fh.read()
        except OSError:
            return False
        if not raw:
            return False
        try:
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
        except (ValueError, UnicodeError):
            return False
        if not isinstance(parsed, dict):
            return False
        got = parsed.get("nonce")
        if not isinstance(got, str):
            return False
        return secrets.compare_digest(got, self._readiness_nonce)

    def _terminate_child(self, sigterm_timeout: float) -> None:
        """Send SIGTERM and wait up to ``sigterm_timeout`` for exit."""
        assert self._proc is not None
        if self._proc.poll() is not None:
            return
        with contextlib.suppress(ProcessLookupError, OSError):
            self._proc.send_signal(signal.SIGTERM)
        deadline = time.monotonic() + sigterm_timeout
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                return
            time.sleep(_POLL_INTERVAL)

    def _kill_child(self, force: bool = False) -> None:
        """SIGKILL the child if it's still alive. Safe to call after exit."""
        assert self._proc is not None
        if self._proc.poll() is not None:
            return
        sig = signal.SIGKILL if force else signal.SIGTERM
        with contextlib.suppress(ProcessLookupError, OSError):
            self._proc.send_signal(sig)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self._proc.wait(timeout=5.0)

    def _cleanup_files(self) -> None:
        for path in (self._config_path, self._ready_path):
            if path is not None:
                with contextlib.suppress(OSError):
                    os.unlink(path)
        self._config_path = None
        self._ready_path = None

    def __del__(self) -> None:
        # Best-effort: never leak a child or temp files if the parent was
        # abandoned without calling stop(). Re-raises are swallowed because
        # __del__ must never raise.
        with contextlib.suppress(Exception):
            if self._proc is not None and self._proc.poll() is None:
                self._kill_child(force=True)
        with contextlib.suppress(Exception):
            self._cleanup_files()
