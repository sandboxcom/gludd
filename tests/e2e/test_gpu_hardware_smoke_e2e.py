"""CLI E2E contracts for real-device GPU smoke runs."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gpu_hardware_smoke.py"
SPEC = importlib.util.spec_from_file_location("gpu_hardware_smoke_e2e", SCRIPT)
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = smoke
SPEC.loader.exec_module(smoke)


def test_local_gpu_dry_run_emits_a_reproducible_workload(monkeypatch, capsys) -> None:
    monkeypatch.setattr(smoke, "_host_diagnostics", lambda: {"os": "linux", "machine": "x86_64"})
    assert smoke.main(["--backend", "auto", "--size", "64", "--iterations", "2", "--sparsity", "0.75"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workload"]["kind"] == "sparse-linear-inference"
    assert payload["workload"]["nonzero_elements"] == 1024
    assert payload["supported"]["hardware"]


def test_local_gpu_live_path_fails_without_device(monkeypatch, capsys) -> None:
    class Cuda:
        @staticmethod
        def is_available() -> bool:
            return False

    class Torch:
        cuda = Cuda()

    monkeypatch.setattr(smoke, "importlib", type("Import", (), {"import_module": staticmethod(lambda _: Torch())}))
    assert smoke.main(["--live", "--iterations", "1"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["code"] == 3
