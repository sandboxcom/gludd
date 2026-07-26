"""Tests for the local Apple unified-memory sparse-model smoke harness."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "mac_unified_memory_smoke.py"
SPEC = importlib.util.spec_from_file_location("mac_unified_memory_smoke", SCRIPT)
assert SPEC and SPEC.loader
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def test_dry_run_is_network_free_and_reports_platform_contract() -> None:
    result = harness.run_smoke(env={"GLUDD_SMOKE_BACKEND": "auto"}, live=False)

    assert result["ok"] is True
    assert result["mode"] == "dry-run"
    assert result["network"] == {"used": False}
    assert result["model"]["sparsity"] == pytest.approx(0.8)
    assert result["capabilities"]["backend_requested"] == "auto"


def test_live_requires_torch_and_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(harness, "_load_torch", lambda: None)

    with pytest.raises(harness.SmokeCapabilityError, match="PyTorch"):
        harness.run_smoke(env={"GLUDD_SMOKE_BACKEND": "mps"}, live=True)


def test_live_rejects_unavailable_mps(monkeypatch: pytest.MonkeyPatch) -> None:
    class Backends:
        class mps:
            @staticmethod
            def is_built() -> bool:
                return True

            @staticmethod
            def is_available() -> bool:
                return False

    class FakeTorch:
        backends = Backends()

    monkeypatch.setattr(harness, "_load_torch", lambda: FakeTorch())

    with pytest.raises(harness.SmokeCapabilityError, match="MPS"):
        harness.run_smoke(env={"GLUDD_SMOKE_BACKEND": "mps"}, live=True)


def test_validate_config_rejects_unbounded_values() -> None:
    with pytest.raises(harness.SmokeConfigError, match="sparsity"):
        harness.run_smoke(env={"GLUDD_SMOKE_SPARSITY": "1.0"}, live=False)
    with pytest.raises(harness.SmokeConfigError, match="STEPS"):
        harness.run_smoke(env={"GLUDD_SMOKE_STEPS": "0"}, live=False)


def test_memory_budget_is_checked_before_model_load(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeTorch:
        class backends:
            class mps:
                @staticmethod
                def is_built() -> bool:
                    return True

                @staticmethod
                def is_available() -> bool:
                    return True

    monkeypatch.setattr(harness, "_load_torch", lambda: FakeTorch())
    monkeypatch.setattr(harness, "_system_memory_bytes", lambda: 2 * 1024**3)

    with pytest.raises(harness.SmokeCapabilityError, match="memory budget"):
        harness.run_smoke(
            env={
                "GLUDD_SMOKE_BACKEND": "mps",
                "GLUDD_SMOKE_MAX_MEMORY_GB": "4",
                "GLUDD_SMOKE_MODEL_PARAMS": "2000000000",
            },
            live=True,
        )


def test_model_fit_policy_accepts_borderline_fp32_model() -> None:
    config = harness._config(
        {
            "GLUDD_SMOKE_MODEL_PARAMS": "100",
            "GLUDD_SMOKE_MAX_MEMORY_GB": "0.000001",
            "GLUDD_SMOKE_HEADROOM": "0.1",
        }
    )
    fit = harness._model_fit(config, {"capacity_bytes": 1024**3})

    assert fit["fits"] is True
    assert fit["dense_storage_bytes_fp32"] == 400
    assert fit["recommendation"].startswith("run only")


def test_model_fit_policy_rejects_oversized_model() -> None:
    config = harness._config(
        {
            "GLUDD_SMOKE_MODEL_PARAMS": "1000000",
            "GLUDD_SMOKE_MAX_MEMORY_GB": "0.001",
        }
    )
    fit = harness._model_fit(config, {"capacity_bytes": 1024**3})

    assert fit["fits"] is False
    assert "do not run" in fit["recommendation"]


def test_cli_dry_run_emits_json() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run"],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["mode"] == "dry-run"
