#!/usr/bin/env python3
"""Run one observable Terraform phase inside an owned Container App root."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import IO, Any

from general_ludd.config.binary_paths import BinaryPathResolver

_DEFAULT_ALLOWED_ROOT = Path("/tmp/gludd-azure-containerapp-live-proof")
_MARKER = ".gludd-azure-containerapp-live-proof.json"
_PROTOCOL = "gludd-azure-containerapp-live-proof-v1"
_MAX_MARKER_BYTES = 4096
_MAX_JSON_BYTES = 2 * 1024 * 1024
_PHASES = (
    "init",
    "validate",
    "plan",
    "show-plan",
    "apply",
    "output",
    "destroy",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one owned Azure Container App Terraform phase.",
        allow_abbrev=False,
    )
    parser.add_argument("--phase", required=True, choices=_PHASES)
    parser.add_argument("--terraform-dir", required=True)
    parser.add_argument("--plan-file", required=True)
    parser.add_argument("--json-file", required=True)
    return parser


def _default_binary_resolver() -> str:
    return BinaryPathResolver().get_infra_binary()


def _regular_file(path: Path, *, required: bool = True) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return not required
    return stat.S_ISREG(metadata.st_mode)


def _owned_paths(
    terraform_dir: Path,
    plan_file: Path,
    json_file: Path,
    allowed_root: Path,
    phase: str,
) -> None:
    resolved_root = allowed_root.resolve()
    resolved_dir = terraform_dir.resolve()
    if (
        resolved_dir.parent != resolved_root
        or re.fullmatch(r"[0-9a-f]{24}", resolved_dir.name) is None
        or not resolved_dir.is_dir()
        or terraform_dir.is_symlink()
    ):
        raise ValueError
    if plan_file.parent.resolve() != resolved_dir or plan_file.name != "gludd.tfplan":
        raise ValueError
    if (
        json_file.parent.resolve() != resolved_dir
        or json_file.name not in {"gludd.plan.json", "gludd.output.json"}
    ):
        raise ValueError
    for name in ("main.tf", "variables.tf", "outputs.tf"):
        if not _regular_file(resolved_dir / name):
            raise ValueError
    marker_path = resolved_dir / _MARKER
    if not _regular_file(marker_path):
        raise ValueError
    marker_raw = marker_path.read_bytes()
    if not marker_raw or len(marker_raw) > _MAX_MARKER_BYTES:
        raise ValueError
    marker = json.loads(marker_raw.decode("utf-8"))
    if (
        not isinstance(marker, dict)
        or set(marker) != {"operation_digest", "protocol"}
        or marker.get("protocol") != _PROTOCOL
        or not isinstance(marker.get("operation_digest"), str)
        or re.fullmatch(r"[0-9a-f]{64}", marker["operation_digest"]) is None
        or not cast_string(marker["operation_digest"]).startswith(resolved_dir.name)
    ):
        raise ValueError
    if phase in {"apply", "show-plan"} and (
        not _regular_file(plan_file) or plan_file.stat().st_size <= 0
    ):
        raise ValueError


def cast_string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError
    return value


def _command(phase: str, binary: str, plan_file: Path) -> list[str]:
    commands = {
        "init": [binary, "init", "-backend=false", "-input=false", "-no-color"],
        "validate": [binary, "validate", "-no-color"],
        "plan": [
            binary,
            "plan",
            "-input=false",
            "-no-color",
            f"-out={plan_file}",
        ],
        "show-plan": [binary, "show", "-json", str(plan_file)],
        "apply": [
            binary,
            "apply",
            "-input=false",
            "-no-color",
            "-auto-approve",
            str(plan_file),
        ],
        "output": [binary, "output", "-json"],
        "destroy": [
            binary,
            "destroy",
            "-input=false",
            "-no-color",
            "-auto-approve",
        ],
    }
    return commands[phase]


def _trace(phase: str, state: str, started: float, monotonic: Callable[[], float]) -> None:
    elapsed = max(0, int(monotonic() - started))
    print(
        "AZURE_CONTAINERAPP_TERRAFORM_TRACE "
        f"phase={phase} state={state} elapsed_seconds={elapsed} "
        "secret_output=false",
        flush=True,
    )


def _wait_observably(
    process: Any,
    *,
    phase: str,
    started: float,
    heartbeat_seconds: float,
    poll_seconds: float,
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
) -> int:
    last_heartbeat = started
    while True:
        returncode = process.poll()
        if returncode is not None:
            return int(returncode)
        now = monotonic()
        if now - last_heartbeat >= heartbeat_seconds:
            _trace(phase, "heartbeat", started, monotonic)
            last_heartbeat = now
        sleeper(poll_seconds)


def _terminate(process: Any) -> None:
    try:
        process.terminate()
        process.wait(timeout=5.0)
    except Exception:
        try:
            process.kill()
            process.wait(timeout=5.0)
        except Exception:
            return


def main(
    argv: Sequence[str] | None = None,
    *,
    allowed_root: Path = _DEFAULT_ALLOWED_ROOT,
    binary_resolver: Callable[[], str] = _default_binary_resolver,
    process_factory: Callable[..., Any] = subprocess.Popen,
    heartbeat_seconds: float = 10.0,
    poll_seconds: float = 0.25,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    """Validate one owned root, execute one list-argv phase, and trace progress."""

    args = _parser().parse_args(argv)
    phase = str(args.phase)
    terraform_dir = Path(str(args.terraform_dir))
    plan_file = Path(str(args.plan_file))
    json_file = Path(str(args.json_file))
    process: Any | None = None
    temporary_json: Path | None = None
    output_handle: IO[str] | None = None
    try:
        if heartbeat_seconds <= 0 or poll_seconds <= 0:
            raise ValueError
        _owned_paths(
            terraform_dir,
            plan_file,
            json_file,
            allowed_root,
            phase,
        )
        binary = binary_resolver()
        if not isinstance(binary, str) or not binary:
            raise ValueError
        if phase in {"show-plan", "output"}:
            temporary_json = json_file.with_name(f".{json_file.name}.{os.getpid()}.tmp")
            temporary_json.unlink(missing_ok=True)
            descriptor = os.open(
                temporary_json,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            output_handle = os.fdopen(descriptor, "w", encoding="utf-8")
        started = monotonic()
        _trace(phase, "started", started, monotonic)
        process = process_factory(
            _command(phase, binary, plan_file),
            cwd=str(terraform_dir.resolve()),
            stdout=output_handle,
            text=True,
            close_fds=True,
            shell=False,
        )
        returncode = _wait_observably(
            process,
            phase=phase,
            started=started,
            heartbeat_seconds=float(heartbeat_seconds),
            poll_seconds=float(poll_seconds),
            monotonic=monotonic,
            sleeper=sleeper,
        )
        if output_handle is not None:
            output_handle.close()
            output_handle = None
        if returncode != 0:
            raise RuntimeError
        if temporary_json is not None:
            if (
                not _regular_file(temporary_json)
                or temporary_json.stat().st_size > _MAX_JSON_BYTES
            ):
                raise ValueError
            os.replace(temporary_json, json_file)
            temporary_json = None
        _trace(phase, "succeeded", started, monotonic)
        return 0
    except KeyboardInterrupt:
        if process is not None:
            _terminate(process)
    except Exception:
        pass
    finally:
        if output_handle is not None:
            output_handle.close()
        if temporary_json is not None:
            temporary_json.unlink(missing_ok=True)
    print(
        f"AZURE_CONTAINERAPP_TERRAFORM_INVALID phase={phase} secret_output=false",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
