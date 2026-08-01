#!/usr/bin/env python3
"""Run security-audit phases with bounded, secret-safe progress events."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TextIO

PHASE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@dataclass(frozen=True)
class PhaseResult:
    """Non-sensitive execution metadata retained by the aggregate audit."""

    name: str
    status: str
    exit_code: int
    elapsed_seconds: float


def _event(
    phase: str,
    status: str,
    elapsed_seconds: float,
    *,
    stream: TextIO = sys.stdout,
    exit_code: int | None = None,
    heartbeat: int | None = None,
) -> None:
    payload: dict[str, object] = {
        "schema_version": 1,
        "event": "security_audit_phase",
        "phase": phase,
        "status": status,
        "elapsed_seconds": round(elapsed_seconds, 3),
    }
    if heartbeat is not None:
        payload["heartbeat"] = heartbeat
    if exit_code is not None:
        payload["exit_code"] = exit_code
    print(json.dumps(payload, sort_keys=True), file=stream, flush=True)


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    """Terminate a phase and its process group without an unbounded wait."""
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:  # pragma: no cover - Windows behavior is exercised in CI there
            process.terminate()
        process.wait(timeout=2)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:  # pragma: no cover - Windows behavior is exercised in CI there
                process.kill()
            process.wait(timeout=2)


def run_phase(
    *,
    name: str,
    command: list[str],
    heartbeat_seconds: float,
    timeout_seconds: float,
    sensitive: bool,
    stream: TextIO = sys.stdout,
) -> PhaseResult:
    """Run one phase, emitting metadata only at a bounded interval."""
    if not PHASE_NAME.fullmatch(name):
        raise ValueError(f"invalid phase name: {name!r}")
    if not command:
        raise ValueError("phase command must not be empty")
    if heartbeat_seconds <= 0:
        raise ValueError("heartbeat_seconds must be positive")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    started = time.monotonic()
    _event(name, "started", 0.0, stream=stream)
    sink: int | None = subprocess.DEVNULL if sensitive else None
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL if sensitive else None,
            stdout=sink,
            stderr=sink,
            start_new_session=os.name == "posix",
        )
    except OSError:
        elapsed = time.monotonic() - started
        _event(name, "failed", elapsed, stream=stream, exit_code=127)
        return PhaseResult(name, "failed", 127, round(elapsed, 3))

    deadline = started + timeout_seconds
    next_heartbeat = started + heartbeat_seconds
    heartbeat = 0
    try:
        while True:
            now = time.monotonic()
            remaining = min(next_heartbeat, deadline) - now
            try:
                exit_code = process.wait(timeout=max(0.001, remaining))
                break
            except subprocess.TimeoutExpired:
                now = time.monotonic()
                if now >= deadline:
                    _stop_process(process)
                    elapsed = now - started
                    _event(name, "timed_out", elapsed, stream=stream, exit_code=124)
                    return PhaseResult(name, "timed_out", 124, round(elapsed, 3))
                if now >= next_heartbeat:
                    heartbeat += 1
                    _event(
                        name,
                        "running",
                        now - started,
                        stream=stream,
                        heartbeat=heartbeat,
                    )
                    next_heartbeat = now + heartbeat_seconds
    except KeyboardInterrupt:
        _stop_process(process)
        raise

    elapsed = time.monotonic() - started
    status = "passed" if exit_code == 0 else "failed"
    _event(name, status, elapsed, stream=stream, exit_code=exit_code)
    return PhaseResult(name, status, exit_code, round(elapsed, 3))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _audit_commands(args: argparse.Namespace) -> list[tuple[str, list[str], bool]]:
    if args.validate_only:
        noop = [sys.executable, "-c", "pass"]
        return [
            (name, noop, name == "secrets-scan")
            for name in (
                "secrets-scan",
                "sast",
                "pip-audit",
                "npm-audit",
                "security-backlog",
            )
        ]

    make = [args.make_command, "--no-print-directory"]
    return [
        ("secrets-scan", [*make, "secrets-scan", "ARGS="], True),
        (
            "sast",
            [
                *make,
                "sast",
                f"SAST_REPORT={args.sast_report}",
                f"SAST_SUMMARY={args.sast_summary}",
                f"SAST_BASELINE={args.sast_baseline or ''}",
            ],
            False,
        ),
        ("pip-audit", [*make, "pip-audit-gate"], False),
        (
            "npm-audit",
            [
                *make,
                "node-deps-audit",
                "NODE_DEPS_VALIDATE_ONLY=0",
                "NODE_DEPS_NPM_USERCONFIG=/dev/null",
                "NODE_DEPS_NPM_CACHE=/tmp/gludd-npm-cache-public-v1",
                "NODE_DEPS_NPM_REGISTRY=https://registry.npmjs.org",
                "NODE_DEPS_AUDIT_LEVEL=moderate",
            ],
            False,
        ),
        ("security-backlog", [*make, "security-backlog-gate"], False),
    ]


def run_audit(args: argparse.Namespace) -> int:
    """Run every audit phase and retain only bounded execution metadata."""
    started = time.monotonic()
    results = [
        run_phase(
            name=name,
            command=command,
            heartbeat_seconds=args.heartbeat_seconds,
            timeout_seconds=args.timeout_seconds,
            sensitive=sensitive,
        )
        for name, command, sensitive in _audit_commands(args)
    ]
    elapsed = round(time.monotonic() - started, 3)
    passed = all(result.exit_code == 0 for result in results)
    payload = {
        "schema_version": 1,
        "status": "passed" if passed else "failed",
        "elapsed_seconds": elapsed,
        "phases": [asdict(result) for result in results],
        "sast_summary": str(args.sast_summary),
    }
    _write_json(args.summary, payload)
    print(
        json.dumps(
            {
                "schema_version": 1,
                "event": "security_audit_complete",
                "status": payload["status"],
                "elapsed_seconds": elapsed,
                "failed_phases": [
                    result.name for result in results if result.exit_code != 0
                ],
                "summary": str(args.summary),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if passed else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)

    phase = subparsers.add_parser("phase", help="run one observable phase")
    phase.add_argument("--name", required=True)
    phase.add_argument("--heartbeat-seconds", type=float, required=True)
    phase.add_argument("--timeout-seconds", type=float, required=True)
    phase.add_argument("--sensitive", action="store_true")
    phase.add_argument("command", nargs=argparse.REMAINDER)

    audit = subparsers.add_parser("audit", help="run the complete security audit")
    audit.add_argument("--heartbeat-seconds", type=float, required=True)
    audit.add_argument("--timeout-seconds", type=float, required=True)
    audit.add_argument("--summary", type=Path, required=True)
    audit.add_argument("--sast-report", type=Path, required=True)
    audit.add_argument("--sast-summary", type=Path, required=True)
    audit.add_argument("--sast-baseline", default="")
    audit.add_argument("--make-command", default="make")
    audit.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.action == "phase":
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        result = run_phase(
            name=args.name,
            command=command,
            heartbeat_seconds=args.heartbeat_seconds,
            timeout_seconds=args.timeout_seconds,
            sensitive=args.sensitive,
        )
        return result.exit_code
    return run_audit(args)


if __name__ == "__main__":
    raise SystemExit(main())
