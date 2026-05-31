"""OPA policy validation tests."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
OPA_DIR = ROOT / "config" / "opa"


class TestOPAPolicies:
    def test_rego_files_exist(self) -> None:
        rego_files = list(OPA_DIR.glob("*.rego"))
        assert len(rego_files) >= 2, "Expected at least 2 .rego files"

    def test_config_policy_rego_exists(self) -> None:
        assert (OPA_DIR / "config_policy.rego").exists()

    def test_config_policy_test_rego_exists(self) -> None:
        assert (OPA_DIR / "config_policy_test.rego").exists()

    def test_rego_policies_parse(self) -> None:
        if not shutil.which("opa"):
            pytest.skip("OPA not installed")
        for rego_file in OPA_DIR.glob("*.rego"):
            result = subprocess.run(
                ["opa", "parse", str(rego_file)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, f"OPA parse failed for {rego_file.name}: {result.stderr}"

    def test_rego_test_runs(self) -> None:
        if not shutil.which("opa"):
            pytest.skip("OPA not installed")
        result = subprocess.run(
            ["opa", "test", str(OPA_DIR)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, f"OPA tests failed: {result.stderr}"

    def test_guardrail_policy_accepts_valid_config(self) -> None:
        rego = (OPA_DIR / "config_policy.rego").read_text()
        assert "guardrail_layers_valid" in rego
        assert "config_layer" in rego
        assert "hook_layer" in rego
        assert "prompt_layer" in rego
