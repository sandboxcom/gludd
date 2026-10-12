"""Acquire pinned Windows NSIS with bounded, observable transient-feed retries.

Chocolatey remains the downloader, package verifier, and installer. Tenacity
owns retry/backoff; psutil owns the spawned process identities and teardown.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import psutil
from tenacity import RetryCallState, Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

PINNED_COMMAND = (
    "choco", "install", "nsis", "--version=3.12.0", "-y", "--no-progress", "--execution-timeout=180",
)
MAX_ATTEMPTS = 3
ATTEMPT_SECONDS = 180
HEARTBEAT_SECONDS = 15
MAX_LOG_BYTES = 4 * 1024 * 1024
_FEED_FAILURE = re.compile(
    r"Failed to fetch results from V2 feed at\s+['\"]https://community\.chocolatey\.org/api/v2/"
    r"[^\r\n]*?Response status code does not indicate success:\s*(\d{3})\b",
    re.IGNORECASE,
)
_PERMANENT_FAILURE = re.compile(
    r"checksum[^\r\n]*(?:did not match|does not match|mismatch|invalid|fail)"
    r"|hashes do not match|access[^\r\n]*denied"
    r"|unable to (?:create|establish)[^\r\n]*(?:ssl|tls|secure channel)"
    r"|(?:ssl|tls)[^\r\n]*(?:fail|invalid)"
    r"|error while running[^\r\n]*chocolateyinstall"
    r"|installation (?:failed|failure)|installer failed"
    r"|Response status code does not indicate success:\s*(?!502\b|503\b|504\b)[45]\d\d\b",
    re.IGNORECASE,
)


class NsisAcquisitionError(RuntimeError):
    """A failed acquisition or compiler pin check, never a successful install."""

    def __init__(self, message: str, returncode: int = 1) -> None:
        super().__init__(message)
        self.returncode = returncode


class _TransientFeedFailure(NsisAcquisitionError):
    """An explicit retryable failure of the official Chocolatey feed."""


def _emit(message: str) -> None:
    print(message, flush=True)


def _is_transient_feed_failure(output: str) -> bool:
    statuses = _FEED_FAILURE.findall(output)
    return (
        bool(statuses)
        and all(status in {"502", "503", "504"} for status in statuses)
        and _PERMANENT_FAILURE.search(output) is None
    )


def _cleanup_owned_tree(process: psutil.Popen, emit: Callable[[str], None]) -> None:
    """Reap only identity-checked descendants of this still-owned child."""
    owned = [*reversed(process.children(recursive=True)), process]
    emit(f"GLUDD-NSIS-CLEANUP pid={process.pid} owned_processes={len(owned)}")
    for child in owned:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            emit(f"GLUDD-NSIS-CLEANUP-ALREADY-EXITED pid={child.pid}")
    _, alive = psutil.wait_procs(owned, timeout=10)
    if alive:
        raise NsisAcquisitionError("owned NSIS acquisition process cleanup failed")


def _install(command: Sequence[str], log: Path, emit: Callable[[str], None]) -> int:
    """Keep Chocolatey's live console while bounding one owned install tree."""
    process = psutil.Popen([*command, f"--log-file={log}"])
    try:
        for elapsed in range(HEARTBEAT_SECONDS, ATTEMPT_SECONDS + 1, HEARTBEAT_SECONDS):
            try:
                return process.wait(timeout=HEARTBEAT_SECONDS)
            except (subprocess.TimeoutExpired, psutil.TimeoutExpired):
                emit(f"GLUDD-NSIS-HEARTBEAT pid={process.pid} elapsed_seconds={elapsed}")
        raise NsisAcquisitionError(f"NSIS acquisition exceeded {ATTEMPT_SECONDS} seconds", returncode=124)
    finally:
        if process.poll() is None:
            _cleanup_owned_tree(process, emit)


def _compiler_path() -> Path:
    """Use the pinned package's vendor directory, never inherited PATH."""
    directory = os.environ.get("PROGRAMFILES(X86)")
    if not directory or not Path(directory).is_absolute():
        raise NsisAcquisitionError("ProgramFiles(x86) must name the absolute vendor compiler directory")
    compiler = Path(directory) / "NSIS" / "makensis.exe"
    if not compiler.is_file():
        raise NsisAcquisitionError(f"pinned vendor compiler is missing: {compiler}")
    return compiler


def _verify_version(emit: Callable[[str], None]) -> None:
    """Reject a missing, failed, or non-pinned compiler before installer build."""
    compiler = _compiler_path()
    result = subprocess.run(
        [str(compiler), "/VERSION"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8-sig",
        timeout=15,
        check=False,
    )
    output = result.stdout.strip()
    emit(f"GLUDD-NSIS-VERSION compiler={compiler} returncode={result.returncode} output={output!r}")
    if result.returncode != 0 or re.fullmatch(r"v?3\.12(?:\.0)?", output) is None:
        raise NsisAcquisitionError(f"expected NSIS 3.12.0 compiler; got {output!r} (exit {result.returncode})")


def install_nsis(
    command: Sequence[str],
    *,
    sleep: Callable[[float], None] = time.sleep,
    emit: Callable[[str], None] = _emit,
) -> None:
    """Admit only the pinned command and finite, classified acquisition retries."""
    if tuple(command) != PINNED_COMMAND:
        raise NsisAcquisitionError("only the exact pinned NSIS acquisition command is accepted", returncode=2)

    def before_sleep(state: RetryCallState) -> None:
        if state.next_action is None:
            raise NsisAcquisitionError("retry callback is missing its bounded next action")
        emit(f"GLUDD-NSIS-RETRY attempt={state.attempt_number} wait_seconds={state.next_action.sleep:g}")

    retryer = Retrying(
        retry=retry_if_exception_type(_TransientFeedFailure),
        stop=stop_after_attempt(MAX_ATTEMPTS),
        wait=wait_exponential(multiplier=5, min=5, max=10),
        before_sleep=before_sleep,
        sleep=sleep,
        reraise=True,
    )
    with tempfile.TemporaryDirectory(prefix="gludd-windows-nsis-") as directory:
        for attempt in retryer:
            with attempt:
                number = attempt.retry_state.attempt_number
                emit(f"GLUDD-NSIS-ATTEMPT attempt={number}/{MAX_ATTEMPTS} package=nsis version=3.12.0")
                log = Path(directory) / f"attempt-{number}.log"
                returncode = _install(command, log, emit)
                if returncode != 0:
                    if log.is_file() and log.stat().st_size > MAX_LOG_BYTES:
                        raise NsisAcquisitionError("NSIS attempt log exceeds the 4 MiB diagnostic bound")
                    output = log.read_text(encoding="utf-8-sig") if log.is_file() else ""
                    message = f"Chocolatey failed to install pinned NSIS 3.12.0 (exit {returncode})"
                    if returncode in {-1, 1} and _is_transient_feed_failure(output):
                        raise _TransientFeedFailure(message, returncode=returncode)
                    raise NsisAcquisitionError(message, returncode=returncode)
        _verify_version(emit)
    emit("GLUDD-NSIS-ACQUISITION-PASS package=nsis version=3.12.0")


def main(argv: list[str] | None = None) -> int:
    """Return a strict acquisition result to the existing PowerShell guard."""
    try:
        install_nsis(sys.argv[1:] if argv is None else argv)
    except NsisAcquisitionError as error:
        _emit(f"::error::GLUDD-NSIS-ACQUISITION-FAILED {error}")
        return error.returncode
    except (OSError, UnicodeError, subprocess.SubprocessError, psutil.Error) as error:
        _emit(f"::error::GLUDD-NSIS-ACQUISITION-FAILED {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
