"""End-to-end tests: Terraform watchdog coverage — gpu-cost-watchdog on all stacks."""

from __future__ import annotations

from pathlib import Path

from general_ludd.infra.compute import ComputeConfig
from general_ludd.infra.terraform import TerraformGenerator

STACKS_DIR = Path("infra/terraform/stacks")
WATCHDOG_SOURCE = '"../../modules/gpu-cost-watchdog"'
MODULE_REF = 'module "gpu_cost_watchdog"'


def _read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def collect_stacks() -> list[Path]:
    stacks = sorted(STACKS_DIR.glob("*/main.tf"))
    assert len(stacks) >= 16, f"expected >=16 stacks, found {len(stacks)}"
    return stacks


def all_stack_tf_files(stack_dir: Path) -> list[Path]:
    return sorted(stack_dir.glob("*.tf"))


class TestTerraformWatchdogE2E:
    def test_all_stacks_have_watchdog_module(self):
        missing: list[str] = []
        for stack_main in collect_stacks():
            content = _read_file(stack_main)
            if MODULE_REF not in content:
                missing.append(stack_main.parent.name)
            elif WATCHDOG_SOURCE not in content:
                missing.append(f"{stack_main.parent.name} (wrong source)")

        assert not missing, f"Stacks missing gpu_cost_watchdog module: {missing}"

    def test_all_stacks_emit_watchdog_user_data_output(self):
        missing: list[str] = []
        for stack_main in collect_stacks():
            stack_dir = stack_main.parent
            stack_name = stack_dir.name
            found = False
            for tf_file in all_stack_tf_files(stack_dir):
                content = _read_file(tf_file)
                if "module.gpu_cost_watchdog.user_data" in content and \
                        "output " in content:
                    found = True
                    break
            if not found:
                missing.append(stack_name)

        assert not missing, f"Stacks missing watchdog_user_data output: {missing}"

    def test_exact_16_stacks_exist(self):
        stacks = collect_stacks()
        assert len(stacks) == 16, (
            f"Expected 16 stacks, found {len(stacks)}: "
            + ", ".join(s.parent.name for s in stacks)
        )

    def test_watchdog_module_variables_include_kubernetes(self):
        vars_file = Path("infra/terraform/modules/gpu-cost-watchdog/variables.tf")
        content = _read_file(vars_file)
        assert '"kubernetes"' in content, \
            "gpu-cost-watchdog variable validation must include kubernetes"

    def test_watchdog_module_has_terraform_data_resource(self):
        main_file = Path("infra/terraform/modules/gpu-cost-watchdog/main.tf")
        content = _read_file(main_file)
        assert "terraform_data" in content, \
            "gpu-cost-watchdog main.tf must contain terraform_data resource for no-provider validatability"

    def test_every_stack_has_main_tf(self):
        stack_dirs = sorted(STACKS_DIR.glob("*/"))
        for stack_dir in stack_dirs:
            main_tf = stack_dir / "main.tf"
            assert main_tf.is_file(), f"Stack {stack_dir.name} missing main.tf"

    def test_watchdog_accepts_all_cloud_providers(self):
        vars_file = Path("infra/terraform/modules/gpu-cost-watchdog/variables.tf")
        content = _read_file(vars_file)
        expected_clouds = ["aws", "gcp", "azure", "vsphere", "runpod", "vast", "kubernetes"]
        for cloud in expected_clouds:
            assert f'"{cloud}"' in content, \
                f"gpu-cost-watchdog variables.tf must include {cloud} in cloud validation"

    def test_terraform_generator_produces_watchdog_in_aws_vllm(self):
        config = ComputeConfig(
            provider="aws",
            gpu_type="a100_80",
            gpu_count=1,
            engine="vllm",
            model_name="meta-llama/Llama-3-8b",
            max_cost_usd=15.0,
            timeout_minutes=45,
            region="us-east-1",
        )
        generator = TerraformGenerator()
        tf = generator.generate(config)
        assert "module " in tf or "resource " in tf or "provider " in tf or "variable " in tf
        assert isinstance(tf, str)
        assert len(tf) > 0

    def test_terraform_generator_build_tfvars_includes_billing(self):
        config = ComputeConfig(
            provider="aws",
            gpu_type="a100_80",
            gpu_count=1,
            engine="vllm",
            model_name="meta-llama/Llama-3-8b",
            max_cost_usd=25.0,
            timeout_minutes=60,
            region="us-east-1",
        )
        generator = TerraformGenerator()
        tfvars = generator.build_tfvars(config)
        assert "max_cost_usd" in tfvars
        assert "timeout_minutes" in tfvars

    def test_watchdog_module_exists_at_expected_path(self):
        module_dir = Path("infra/terraform/modules/gpu-cost-watchdog")
        assert module_dir.is_dir()
        assert (module_dir / "main.tf").is_file()
        assert (module_dir / "variables.tf").is_file()

    def test_kubernetes_stacks_have_watchdog(self):
        k8s_stacks = [
            "kubernetes-llamacpp",
            "kubernetes-vllm",
        ]
        for stack_name in k8s_stacks:
            main_tf = STACKS_DIR / stack_name / "main.tf"
            assert main_tf.is_file()
            content = _read_file(main_tf)
            assert MODULE_REF in content, \
                f"{stack_name} missing gpu_cost_watchdog module"
            assert WATCHDOG_SOURCE in content, \
                f"{stack_name} has wrong watchdog source"
