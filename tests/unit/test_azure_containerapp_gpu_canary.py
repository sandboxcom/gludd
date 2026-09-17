"""Exact startup CUDA canary contracts for Azure Container Apps."""

from __future__ import annotations

import pytest

from general_ludd.infra.azure_containerapp_gpu_canary import (
    CUDA_STARTUP_CANARY,
    CUDA_STARTUP_COMMAND,
    CUDAStartupCommandError,
    validate_cuda_startup_command,
)


def test_startup_command_executes_cuda_kernel_before_exact_vllm_server() -> None:
    """A ready revision proves CUDA allocation and a completed GPU kernel."""
    assert CUDA_STARTUP_COMMAND == ("python3", "-c", CUDA_STARTUP_CANARY)
    assert "torch.cuda.is_available()" in CUDA_STARTUP_CANARY
    assert "torch.cuda.device_count() != 1" in CUDA_STARTUP_CANARY
    assert "torch.mm(left, left)" in CUDA_STARTUP_CANARY
    assert "torch.cuda.synchronize()" in CUDA_STARTUP_CANARY
    assert 'os.execvp("vllm", ["vllm", "serve", *sys.argv[1:]])' in (
        CUDA_STARTUP_CANARY
    )
    validate_cuda_startup_command(list(CUDA_STARTUP_COMMAND))
    validate_cuda_startup_command(
        ["python3", "-c", CUDA_STARTUP_CANARY.rstrip("\n")]
    )
    validate_cuda_startup_command(
        ["python3", "-c", CUDA_STARTUP_CANARY.replace("\n", "\r\n")]
    )


@pytest.mark.parametrize(
    "command",
    [
        None,
        "python3 -c unsafe",
        ["python3", "-c"],
        ["python3", "-c", "import torch"],
        ["sh", "-c", CUDA_STARTUP_CANARY],
        [*CUDA_STARTUP_COMMAND, "extra"],
    ],
)
def test_startup_command_rejects_every_ambiguous_or_weakened_form(
    command: object,
) -> None:
    with pytest.raises(CUDAStartupCommandError):
        validate_cuda_startup_command(command)


def test_startup_command_reports_only_fixed_shape_diagnostics() -> None:
    with pytest.raises(CUDAStartupCommandError) as missing:
        validate_cuda_startup_command(None)
    with pytest.raises(CUDAStartupCommandError) as indented:
        validate_cuda_startup_command(
            ["python3", "-c", "  " + CUDA_STARTUP_CANARY.replace("\n", "\n  ")]
        )

    assert missing.value.reason == "cuda_startup_command_shape"
    assert indented.value.reason == "cuda_startup_command_indented"
