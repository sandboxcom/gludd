"""Integration tests for TerraformWatchdog — stack cost monitoring."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from general_ludd.infra.terraform_watchdog import (
    TerraformWatchdog,
    WatchdogFinding,
)


class TestTerraformWatchdogInit:
    def test_watchdog_initialization_with_temp_stacks_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stacks = Path(tmpdir)
            watchdog = TerraformWatchdog(stacks_dir=str(stacks))
            assert watchdog is not None
            assert watchdog._stacks_dir == stacks
            assert watchdog.check_all_stacks() == []

    def test_watchdog_detects_applied_stack(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stacks = Path(tmpdir)
            stack_dir = stacks / "aws-vllm"
            stack_dir.mkdir(parents=True)
            (stack_dir / "terraform.tfstate").write_text("{}")

            watchdog = TerraformWatchdog(stacks_dir=str(stacks))
            assert watchdog.is_applied("aws-vllm") is True
            assert watchdog.is_applied("nonexistent") is False


class TestTerraformWatchdogCostDetection:
    def test_cost_detection_with_mock_tfstate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stacks = Path(tmpdir)
            stack_dir = stacks / "aws-vllm"
            stack_dir.mkdir(parents=True)

            # Write a mock terraform state with cost estimates
            tfstate = {
                "version": 4,
                "resources": [
                    {
                        "type": "aws_instance",
                        "instances": [
                            {
                                "attributes": {
                                    "monthly_cost_estimate": "75.5",
                                    "instance_type": "p4d.24xlarge",
                                }
                            }
                        ],
                    }
                ],
            }
            (stack_dir / "terraform.tfstate").write_text(json.dumps(tfstate))
            (stack_dir / "budget.json").write_text(json.dumps({"monthly_limit_usd": 100.0}))

            watchdog = TerraformWatchdog(stacks_dir=str(stacks))
            findings = watchdog.check_all_stacks()

            assert len(findings) == 1
            f = findings[0]
            assert f.stack_name == "aws-vllm"
            assert f.current_cost == 75.5
            assert f.budget_limit == 100.0
            assert f.exceeded_budget is False

    def test_cost_exceeds_budget(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stacks = Path(tmpdir)
            stack_dir = stacks / "gcp-vllm"
            stack_dir.mkdir(parents=True)

            tfstate = {
                "resources": [
                    {
                        "type": "google_compute_instance",
                        "instances": [
                            {"attributes": {"cost_estimate": "150.0"}}
                        ],
                    }
                ],
            }
            (stack_dir / "terraform.tfstate").write_text(json.dumps(tfstate))
            (stack_dir / "budget.json").write_text(json.dumps({"monthly_limit_usd": 100.0}))

            watchdog = TerraformWatchdog(stacks_dir=str(stacks))
            findings = watchdog.check_all_stacks()

            assert len(findings) == 1
            assert findings[0].exceeded_budget is True
            assert findings[0].current_cost == 150.0

    def test_missing_budget_file_uses_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stacks = Path(tmpdir)
            stack_dir = stacks / "azure-vllm"
            stack_dir.mkdir(parents=True)

            tfstate = {
                "resources": [
                    {
                        "type": "azurerm_linux_virtual_machine",
                        "instances": [
                            {"attributes": {"monthly_cost_estimate": "55.0"}}
                        ],
                    }
                ],
            }
            (stack_dir / "terraform.tfstate").write_text(json.dumps(tfstate))

            watchdog = TerraformWatchdog(stacks_dir=str(stacks))
            findings = watchdog.check_all_stacks()

            assert len(findings) == 1
            assert findings[0].budget_limit == 0.0
            assert findings[0].exceeded_budget is False  # budget=0 means no budget configured

    def test_watchdog_finding_dataclass(self):
        f = WatchdogFinding(
            stack_name="test-stack",
            exceeded_budget=False,
            current_cost=15.0,
            budget_limit=50.0,
        )
        assert f.stack_name == "test-stack"
        assert f.exceeded_budget is False
        assert f.current_cost == 15.0
        assert f.budget_limit == 50.0

    def test_watchdog_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            watchdog = TerraformWatchdog(stacks_dir=tmpdir)
            assert watchdog.check_all_stacks() == []
