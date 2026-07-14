"""Integration tests: bill-5 spot/preemptible VM daemon wiring.

Tests AWS spot, GCP preemptible, and Azure Spot VM configurations through
the daemon, verifying use_spot defaults and TerraformGenerator spot-aware output.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import general_ludd.daemon as daemon_mod
from general_ludd.daemon import create_daemon_app
from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType, InferenceEngine
from general_ludd.infra.pricing import INFRA_PRICING, InfraTracker
from general_ludd.infra.terraform import TerraformGenerator
from general_ludd.pricing_intel.models import (
    BillingGranularity,
    BillingTerms,
    ComputePrice,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
STACKS_DIR = REPO_ROOT / "infra" / "terraform" / "stacks"

AWS_STACKS = ("aws-vllm", "aws-llamacpp")
GCP_STACKS = ("gcp-vllm", "gcp-llamacpp")
AZURE_VM_STACKS = ("azure-vllm", "azure-llamacpp")
AZURE_CONTAINER_APP_STACKS = ("azure-container-app-vllm", "azure-container-app-llamacpp")


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


def _read(stack_name: str, filename: str) -> str:
    return (STACKS_DIR / stack_name / filename).read_text()


class TestSpotPreemptibleWiring:
    """Integration tests for spot/preemptible VM wiring and defaults."""

    def test_aws_spot_instance_market_options_present(self):
        """AWS stacks have dynamic instance_market_options with market_type=spot."""
        for stack in AWS_STACKS:
            text = _read(stack, "main.tf")
            assert 'dynamic "instance_market_options"' in text, \
                f"{stack}: missing dynamic instance_market_options"
            assert 'market_type = "spot"' in text, \
                f"{stack}: missing market_type = 'spot'"
            assert "var.use_spot" in text, \
                f"{stack}: missing var.use_spot reference"

    def test_gcp_preemptible_scheduling_block_present(self):
        """GCP stacks have scheduling block with preemptible and automatic_restart."""
        for stack in GCP_STACKS:
            text = _read(stack, "main.tf")
            match = re.search(r'scheduling\s*\{(.*?)\}', text, re.DOTALL)
            assert match, f"{stack}: missing scheduling block"
            block = match.group(1)
            assert "preemptible" in block, f"{stack}: missing preemptible"
            assert "automatic_restart" in block, f"{stack}: missing automatic_restart"
            assert "var.use_spot" in text, f"{stack}: missing var.use_spot reference"

    def test_azure_spot_vm_priority_and_eviction_policy(self):
        """Azure VM stacks have priority=Spot and eviction_policy=Delete."""
        for stack in AZURE_VM_STACKS:
            text = _read(stack, "main.tf")
            assert "priority" in text, f"{stack}: missing priority"
            assert "Spot" in text, f"{stack}: missing Spot"
            assert "eviction_policy" in text, f"{stack}: missing eviction_policy"
            assert "Delete" in text, f"{stack}: missing Delete"

    @pytest.mark.parametrize(
        "stack",
        sorted(AWS_STACKS + GCP_STACKS + AZURE_VM_STACKS),
    )
    def test_all_vm_stacks_use_spot_defaults_true(self, stack: str):
        """Every VM stack has use_spot variable defaulting to true."""
        text = _read(stack, "variables.tf")
        match = re.search(r'variable\s+"use_spot"\s*\{(.*?)\n\}', text, re.DOTALL)
        assert match, f"{stack}: missing use_spot variable"
        block = match.group(1)
        assert re.search(r'default\s*=\s*true', block), \
            f"{stack}: use_spot should default to true"

    @pytest.mark.parametrize("stack", sorted(AZURE_CONTAINER_APP_STACKS))
    def test_container_app_stacks_use_spot_defaults_false(self, stack: str):
        """Container App stacks have use_spot defaulting to false (not supported)."""
        text = _read(stack, "variables.tf")
        match = re.search(r'variable\s+"use_spot"\s*\{(.*?)\n\}', text, re.DOTALL)
        assert match, f"{stack}: missing use_spot variable"
        block = match.group(1)
        assert re.search(r'default\s*=\s*false', block), \
            f"{stack}: use_spot should default to false"

    def test_terraform_generator_spot_config(self):
        """TerraformGenerator with spot=True on a spot-enabled provider works."""
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.A100_80,
            gpu_count=1,
            engine=InferenceEngine.VLLM,
            model_name="test-model",
            spot=True,
            max_cost_usd=10.0,
            timeout_minutes=30,
            region="us-east-1",
        )
        generator = TerraformGenerator()
        tf = generator.generate(config)
        assert isinstance(tf, str)
        assert len(tf) > 0

    def test_infra_tracker_spot_discount_via_catalog(self):
        """InfraTracker uses spot=True to query PricingCatalog for lower pricing."""
        catalog = MagicMock()
        spot_price = ComputePrice(
            provider="runpod",
            sku="A100-SXM4-80GB-1x",
            usd_per_unit=0.0004,
            granularity=BillingGranularity.per_second,
            spot=True,
            terms=BillingTerms.postpaid_per_use,
            source="test",
        )
        catalog.compute_price.return_value = spot_price

        tracker = InfraTracker(catalog=catalog)
        tracker.record_gpu_seconds("runpod", "A100-SXM4-80GB-1x", 1000.0, spot=True)

        catalog.compute_price.assert_called_once_with(
            "runpod", "A100-SXM4-80GB-1x", spot=True
        )
        assert tracker.get_total_infra_cost() == pytest.approx(0.0004 * 1000.0)

    def test_infra_tracker_non_spot_uses_regular_price(self):
        """InfraTracker with spot=False uses regular pricing from catalog."""
        catalog = MagicMock()
        regular_price = ComputePrice(
            provider="aws",
            sku="A100-SXM4-80GB-1x",
            usd_per_unit=0.00083,
            granularity=BillingGranularity.per_second,
            spot=False,
            terms=BillingTerms.postpaid_per_use,
            source="test",
        )
        catalog.compute_price.return_value = regular_price

        tracker = InfraTracker(catalog=catalog)
        tracker.record_gpu_seconds("aws", "A100-SXM4-80GB-1x", 500.0, spot=False)

        catalog.compute_price.assert_called_once_with(
            "aws", "A100-SXM4-80GB-1x", spot=False
        )
        assert tracker.get_total_infra_cost() == pytest.approx(0.00083 * 500.0)

    def test_infra_tracker_fallback_when_catalog_misses(self):
        """InfraTracker falls back to static INFRA_PRICING when catalog returns None."""
        catalog = MagicMock()
        catalog.compute_price.return_value = None

        tracker = InfraTracker(catalog=catalog)
        tracker.record_gpu_seconds("unknown", "some-gpu", 100.0, spot=True)

        expected = INFRA_PRICING["gpu_second"] * 100.0
        assert tracker.get_total_infra_cost() == pytest.approx(expected)

    def test_spot_and_non_spot_mixed_accumulation(self):
        """InfraTracker correctly accumulates mixed spot and non-spot costs."""
        catalog = MagicMock()
        spot_p = ComputePrice(
            provider="runpod",
            sku="A100",
            usd_per_unit=0.0004,
            granularity=BillingGranularity.per_second,
            spot=True,
            terms=BillingTerms.postpaid_per_use,
            source="test",
        )
        regular_p = ComputePrice(
            provider="runpod",
            sku="A100",
            usd_per_unit=0.00083,
            granularity=BillingGranularity.per_second,
            spot=False,
            terms=BillingTerms.postpaid_per_use,
            source="test",
        )
        catalog.compute_price.side_effect = [spot_p, regular_p]

        tracker = InfraTracker(catalog=catalog)
        tracker.record_gpu_seconds("runpod", "A100", 100.0, spot=True)
        tracker.record_gpu_seconds("runpod", "A100", 100.0, spot=False)

        expected = 0.0004 * 100.0 + 0.00083 * 100.0
        assert tracker.get_total_infra_cost() == pytest.approx(expected)

    def test_healthz_available_in_daemon_with_spot_config(
        self, tmp_path: pytest.Path
    ):
        """Daemon starts and serves healthz with spot-aware configuration."""
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
