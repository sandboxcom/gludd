#!/usr/bin/env python3
"""Developer/CI wrapper for Gludd's production Terraform phase executor."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from general_ludd.infra.azure_containerapp_terraform_executor import (
    TERRAFORM_PHASES,
    AzureContainerAppTerraformPhaseError,
    AzureContainerAppTerraformPhaseExecutor,
    TerraformRuntimeState,
    terraform_process_environment,
)
from general_ludd.infra.azure_containerapp_terraform_executor import (
    _regular_file as _regular_file,
)

_DEFAULT_ALLOWED_ROOT = Path("/tmp/gludd-azure-containerapp-live-proof")
_PHASES = TERRAFORM_PHASES
_ARM_ENVIRONMENT_NAMES = frozenset(
    {
        "ARM_CLIENT_ID",
        "ARM_CLIENT_SECRET",
        "ARM_SUBSCRIPTION_ID",
        "ARM_TENANT_ID",
        "CHECKPOINT_DISABLE",
        "TF_IN_AUTOMATION",
        "TF_INPUT",
    }
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


def _wrapper_environment() -> dict[str, str]:
    overrides = {
        key: value
        for key, value in os.environ.items()
        if key in _ARM_ENVIRONMENT_NAMES
    }
    return terraform_process_environment(overrides)


def _print_progress(
    phase: str,
    state: TerraformRuntimeState,
    elapsed_seconds: int,
) -> None:
    print(
        "AZURE_CONTAINERAPP_TERRAFORM_TRACE "
        f"phase={phase} state={state.value} elapsed_seconds={elapsed_seconds} "
        "secret_output=false",
        flush=True,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    allowed_root: Path = _DEFAULT_ALLOWED_ROOT,
    binary_resolver: Callable[[], str] | None = None,
    process_factory: Callable[..., Any] | None = None,
    heartbeat_seconds: float = 10.0,
    poll_seconds: float = 0.25,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    """Parse the compatibility CLI and delegate one phase to production code."""
    args = _parser().parse_args(argv)
    phase = str(args.phase)
    executor_arguments: dict[str, object] = {
        "heartbeat_seconds": heartbeat_seconds,
        "poll_seconds": poll_seconds,
        "monotonic": monotonic,
        "sleeper": sleeper,
    }
    if binary_resolver is not None:
        executor_arguments["binary_resolver"] = binary_resolver
    if process_factory is not None:
        executor_arguments["process_factory"] = process_factory
    try:
        executor = AzureContainerAppTerraformPhaseExecutor(**executor_arguments)
        executor.run(
            phase=phase,
            terraform_dir=Path(str(args.terraform_dir)),
            plan_file=Path(str(args.plan_file)),
            json_file=Path(str(args.json_file)),
            allowed_root=allowed_root,
            environment=_wrapper_environment(),
            timeout_seconds=86_400,
            progress=_print_progress,
        )
    except (AzureContainerAppTerraformPhaseError, ValueError, TypeError):
        print(
            f"AZURE_CONTAINERAPP_TERRAFORM_INVALID phase={phase} secret_output=false",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
