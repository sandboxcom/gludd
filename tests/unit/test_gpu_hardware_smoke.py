"""Deterministic contracts for the local CUDA/ROCm smoke harness."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gpu_hardware_smoke.py"
SPEC = importlib.util.spec_from_file_location("gpu_hardware_smoke", SCRIPT)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = smoke
SPEC.loader.exec_module(smoke)


def test_dry_run_is_bounded_and_lists_supported_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "_host_diagnostics", lambda: {"os": "linux"})
    result = smoke.plan_smoke(smoke.SmokeArgs(size=64, iterations=2, sparsity=0.75))
    assert result["mode"] == "dry-run"
    assert result["workload"]["nonzero_elements"] == 1024
    assert result["supported"]["linux"] == ["cuda", "rocm"]
    assert result["supported"]["windows"] == ["cuda", "rocm"]


def test_invalid_bounds_fail_closed() -> None:
    with pytest.raises(smoke.HardwareSmokeError, match="size"):
        smoke.plan_smoke(smoke.SmokeArgs(size=16))
    with pytest.raises(smoke.HardwareSmokeError, match="iterations"):
        smoke.plan_smoke(smoke.SmokeArgs(iterations=21))
    with pytest.raises(smoke.HardwareSmokeError, match="sparsity"):
        smoke.plan_smoke(smoke.SmokeArgs(sparsity=0.2))


def test_torch_backend_identifies_cuda_and_rocm() -> None:
    class Cuda:
        @staticmethod
        def is_available() -> bool:
            return True

    class Version:
        hip: str | None = None

    class Torch:
        cuda = Cuda()
        version = Version()

    assert smoke._torch_backend(Torch()) == "cuda"
    Torch.version.hip = "6.1"
    assert smoke._torch_backend(Torch()) == "rocm"


def test_torch_backend_returns_none_without_device() -> None:
    class Cuda:
        @staticmethod
        def is_available() -> bool:
            return False

    class Torch:
        cuda = Cuda()

    assert smoke._torch_backend(Torch()) is None


def test_live_run_reports_missing_gpu_without_running_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    class Cuda:
        @staticmethod
        def is_available() -> bool:
            return False

    class Torch:
        cuda = Cuda()

    monkeypatch.setattr(smoke, "_host_diagnostics", lambda: {"os": "windows"})
    with pytest.raises(smoke.HardwareSmokeError, match="no CUDA/ROCm GPU"):
        smoke.run_live(smoke.SmokeArgs(), torch_module=Torch())


def test_cli_dry_run_is_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(smoke, "_host_diagnostics", lambda: {"os": "linux"})
    assert smoke.main(["--size", "32", "--iterations", "1"]) == 0
    output = capsys.readouterr().out
    assert '"mode": "dry-run"' in output
    assert '"ok": true' in output
