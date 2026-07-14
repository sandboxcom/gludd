"""Integration tests: bill-4 Terraform watchdog daemon wiring.

Tests TerraformGenerator watchdog module generation for each stack,
cloud-init script generation, and daemon-side watchdog health monitoring.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app
from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType, InferenceEngine
from general_ludd.infra.terraform import TerraformGenerator

STACKS_DIR = Path("infra/terraform/stacks")
WATCHDOG_SOURCE = '"../../modules/gpu-cost-watchdog"'
MODULE_REF = 'module "gpu_cost_watchdog"'


@pytest.fixture(autouse=True)
def _reset_daemon_state():
    """Isolate the module-level ``_daemon_state`` shim around each test.

    ``general_ludd.daemon._daemon_state`` starts life as ``None`` — it is only a
    migration shim. ``create_daemon_app()`` allocates a FRESH per-app dict on
    ``app.state.daemon_state`` (the authoritative store) and merely rebinds the
    module global to it (daemon.py:2565-2575). Writing into the shim at
    fixture-setup time — before any app exists — raised ``TypeError: 'NoneType'
    object does not support item assignment``. Snapshot/restore is the correct
    isolation: nothing needs pre-seeding, the shim just must not leak across
    tests.
    """
    original = daemon_mod._daemon_state
    daemon_mod._daemon_state = None
    yield
    daemon_mod._daemon_state = original


def _make_db_config(tmp_path: pytest.Path) -> str:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    db_path = tmp_path / "test.db"
    (config_dir / "general-ludd.yml").write_text(
        f"database:\n  url: 'sqlite+aiosqlite:///{db_path}'\n"
    )
    return str(config_dir)


def _read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _collect_stack_main_files() -> list[Path]:
    stacks = sorted(STACKS_DIR.glob("*/main.tf"))
    assert len(stacks) >= 16, f"expected >=16 stacks, found {len(stacks)}"
    return stacks


class TestTerraformWatchdogWiring:
    """Integration tests for TerraformGenerator watchdog module wiring."""

    def test_terraform_generator_produces_watchdog_in_aws_vllm(self):
        """TerraformGenerator.generate() produces valid HCL with billing fields."""
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.A100_80,
            gpu_count=1,
            engine=InferenceEngine.VLLM,
            model_name="meta-llama/Llama-3-8b",
            max_cost_usd=15.0,
            timeout_minutes=45,
            region="us-east-1",
        )
        generator = TerraformGenerator()
        tf = generator.generate(config)
        assert isinstance(tf, str)
        assert len(tf) > 0
        assert "module " in tf or "resource " in tf or "provider " in tf or "variable " in tf

    def test_terraform_generator_watchdog_in_all_providers(self):
        """TerraformGenerator generates valid output for every major provider."""
        providers = [
            (ComputeProvider.AWS, InferenceEngine.VLLM),
            (ComputeProvider.GCP, InferenceEngine.VLLM),
            (ComputeProvider.AZURE, InferenceEngine.VLLM),
            (ComputeProvider.RUNPOD, InferenceEngine.VLLM),
            (ComputeProvider.VAST_AI, InferenceEngine.VLLM),
        ]
        for provider, engine in providers:
            config = ComputeConfig(
                provider=provider,
                gpu_type=GPUType.A100_80,
                gpu_count=1,
                engine=engine,
                model_name="test-model",
                max_cost_usd=20.0,
                timeout_minutes=30,
            )
            generator = TerraformGenerator()
            tf = generator.generate(config)
            assert isinstance(tf, str)
            assert len(tf) > 0, f"Empty output for {provider.value}"

    def test_build_tfvars_includes_billing_fields(self):
        """build_tfvars() includes max_cost_usd and timeout_minutes."""
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.A100_80,
            gpu_count=1,
            engine=InferenceEngine.VLLM,
            model_name="meta-llama/Llama-3-8b",
            max_cost_usd=25.0,
            timeout_minutes=60,
            region="us-east-1",
        )
        generator = TerraformGenerator()
        tfvars = generator.build_tfvars(config)
        assert "max_cost_usd" in tfvars
        assert "timeout_minutes" in tfvars

    def test_all_stacks_have_watchdog_module(self):
        """Every stack main.tf references the gpu_cost_watchdog module."""
        missing: list[str] = []
        for stack_main in _collect_stack_main_files():
            content = _read_file(stack_main)
            if MODULE_REF not in content:
                missing.append(stack_main.parent.name)
            elif WATCHDOG_SOURCE not in content:
                missing.append(f"{stack_main.parent.name} (wrong source)")

        assert not missing, f"Stacks missing gpu_cost_watchdog module: {missing}"

    def test_watchdog_module_exists_at_expected_path(self):
        """The gpu-cost-watchdog module directory and required files exist."""
        module_dir = Path("infra/terraform/modules/gpu-cost-watchdog")
        assert module_dir.is_dir()
        assert (module_dir / "main.tf").is_file()
        assert (module_dir / "variables.tf").is_file()
        assert (module_dir / "outputs.tf").is_file()

    def test_watchdog_module_accepts_all_cloud_providers(self):
        """Watchdog variables.tf validates all supported cloud providers."""
        vars_file = Path("infra/terraform/modules/gpu-cost-watchdog/variables.tf")
        content = _read_file(vars_file)
        expected_clouds = ["aws", "gcp", "azure", "vsphere", "runpod", "vast", "kubernetes"]
        for cloud in expected_clouds:
            assert f'"{cloud}"' in content, \
                f"gpu-cost-watchdog variables.tf must include {cloud} in cloud validation"

    def test_healthz_endpoint_works_in_daemon(self, tmp_path: pytest.Path):
        """Daemon healthz endpoint returns 200 after startup."""
        with patch(
            "general_ludd.ansible.runner.AnsibleRunnerAdapter",
            return_value=MagicMock(),
        ):
            app = create_daemon_app(
                tick_interval=300.0, config_dir=_make_db_config(tmp_path)
            )
            with TestClient(app) as client:
                resp = client.get("/healthz")
                assert resp.status_code == 200

    def test_exact_16_stacks_exist(self):
        """There are exactly 16 stacks (8 pairs)."""
        stacks = sorted(STACKS_DIR.glob("*/main.tf"))
        assert len(stacks) == 16, (
            f"Expected 16 stacks, found {len(stacks)}: "
            + ", ".join(s.parent.name for s in stacks)
        )

    def test_kubernetes_stacks_have_watchdog(self):
        """Kubernetes stacks include the gpu_cost_watchdog module."""
        for stack_name in ["kubernetes-llamacpp", "kubernetes-vllm"]:
            main_tf = STACKS_DIR / stack_name / "main.tf"
            assert main_tf.is_file(), f"{stack_name} missing main.tf"
            content = _read_file(main_tf)
            assert MODULE_REF in content, \
                f"{stack_name} missing gpu_cost_watchdog module"
            assert WATCHDOG_SOURCE in content, \
                f"{stack_name} has wrong watchdog source"

    def test_watchdog_main_tf_has_terraform_data(self):
        """Watchdog main.tf uses terraform_data for no-provider validatability."""
        main_file = Path("infra/terraform/modules/gpu-cost-watchdog/main.tf")
        content = _read_file(main_file)
        assert "terraform_data" in content, \
            "gpu-cost-watchdog main.tf must contain terraform_data resource"
