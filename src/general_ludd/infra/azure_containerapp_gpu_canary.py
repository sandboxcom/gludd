"""Immutable CUDA startup command for the exact Azure model revision."""

from __future__ import annotations

import textwrap
from collections.abc import Sequence
from typing import Final

CUDA_STARTUP_CANARY: Final = """import os
import sys

import torch

if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
    raise SystemExit(70)
left = torch.full((64, 64), 2.0, device="cuda")
product = torch.mm(left, left)
torch.cuda.synchronize()
if product[0, 0].item() != 256.0:
    raise SystemExit(71)
os.execvp("vllm", ["vllm", "serve", *sys.argv[1:]])
"""
CUDA_STARTUP_COMMAND: Final = ("python3", "-c", CUDA_STARTUP_CANARY)
_FAILURE_REASONS = frozenset(
    {
        "cuda_startup_command_executable",
        "cuda_startup_command_indented",
        "cuda_startup_command_mismatch",
        "cuda_startup_command_shape",
        "cuda_startup_command_wrapped",
    }
)


class CUDAStartupCommandError(ValueError):
    """Fixed diagnostic for a command that cannot prove CUDA startup."""

    def __init__(self, reason: str) -> None:
        """Validate and retain one content-free CUDA failure reason."""
        if reason not in _FAILURE_REASONS:
            raise ValueError("reason must be a fixed CUDA command failure")
        super().__init__(reason)
        self.reason = reason


def _normalized_source(source: str) -> str:
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip(" \t") for line in lines).strip("\n")


def validate_cuda_startup_command(command: object) -> None:
    """Require the one command whose readiness proves a completed CUDA kernel."""
    if (
        not isinstance(command, Sequence)
        or isinstance(command, (str, bytes))
        or len(command) != 3
        or any(not isinstance(part, str) for part in command)
    ):
        raise CUDAStartupCommandError("cuda_startup_command_shape")
    if tuple(command[:2]) != CUDA_STARTUP_COMMAND[:2]:
        raise CUDAStartupCommandError("cuda_startup_command_executable")
    source = command[2]
    expected = _normalized_source(CUDA_STARTUP_CANARY)
    observed = _normalized_source(source)
    if observed == expected:
        return
    if _normalized_source(textwrap.dedent(source)) == expected:
        raise CUDAStartupCommandError("cuda_startup_command_indented")
    if expected in observed:
        raise CUDAStartupCommandError("cuda_startup_command_wrapped")
    raise CUDAStartupCommandError("cuda_startup_command_mismatch")


__all__ = (
    "CUDA_STARTUP_CANARY",
    "CUDA_STARTUP_COMMAND",
    "CUDAStartupCommandError",
    "validate_cuda_startup_command",
)
