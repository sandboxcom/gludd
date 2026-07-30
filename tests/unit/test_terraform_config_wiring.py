"""TDD tests for terraform config wiring across stack/model/CLI boundaries.

1. TerraformConfig model exists with all required fields
2. TerraformConfig is wired into TerraformGenerator
3. CLI ``gludd config terraform get/set`` subcommands exist
4. DeploymentManager plan() and validate() methods exist
5. QEMU virtualization uses the maintained libvirt provider in versions.tf
6. QemuConfig/detect returns correct platform/arch on current machine
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------
# 1. TerraformConfig model
# ------------------------------------------------------------------

class TestTerraformConfigModel:
    """TerraformConfig exists in user_config with str-based fields."""

    def test_terraform_config_is_importable(self) -> None:
        from general_ludd.config.user_config import TerraformConfig
        assert TerraformConfig is not None

    def test_terraform_config_is_pydantic_model(self) -> None:
        from pydantic import BaseModel

        from general_ludd.config.user_config import TerraformConfig
        assert issubclass(TerraformConfig, BaseModel)

    def test_default_provider_is_aws_str(self) -> None:
        from general_ludd.config.user_config import TerraformConfig
        cfg = TerraformConfig()
        assert cfg.provider == "aws"
        assert cfg.gpu_type == "t4"

    def test_gpu_count_default_is_1(self) -> None:
        from general_ludd.config.user_config import TerraformConfig
        cfg = TerraformConfig()
        assert cfg.gpu_count == 1

    def test_region_default_is_us_east_1(self) -> None:
        from general_ludd.config.user_config import TerraformConfig
        cfg = TerraformConfig()
        assert cfg.region == "us-east-1"

    def test_all_expected_fields_exist(self) -> None:
        from general_ludd.config.user_config import TerraformConfig
        expected = {
            "container_image", "model_name", "gpu_count", "extra_args",
            "region", "instance_type", "max_cost_usd", "timeout_minutes",
            "disk_size_gb", "allowed_cidr", "guided_decoding_backend",
            "enable_structured_outputs", "grammar_file", "provider",
            "gpu_type", "engine",
        }
        actual = set(TerraformConfig.model_fields.keys())
        assert expected <= actual, f"Missing fields: {expected - actual}"

    def test_instantiate_with_override(self) -> None:
        from general_ludd.config.user_config import TerraformConfig
        cfg = TerraformConfig(
            provider="gcp",
            gpu_type="a100_80",
            gpu_count=2,
            region="us-west1",
            instance_type="a2-highgpu-2g",
        )
        assert cfg.provider == "gcp"
        assert cfg.gpu_type == "a100_80"
        assert cfg.gpu_count == 2
        assert cfg.region == "us-west1"
        assert cfg.instance_type == "a2-highgpu-2g"


# ------------------------------------------------------------------
# 2. TerraformConfig wired into TerraformGenerator
# ------------------------------------------------------------------

class TestTerraformConfigWiredIntoTerraformGenerator:
    """TerraformGenerator accepts terraform_config and stores it."""

    def test_generator_imports_terraform_config(self) -> None:
        from general_ludd.infra.terraform import TerraformGenerator
        assert callable(TerraformGenerator)

    def test_generator_accepts_terraform_config(self) -> None:
        from general_ludd.config.user_config import TerraformConfig
        from general_ludd.infra.terraform import TerraformGenerator
        tfc = TerraformConfig(provider="aws", gpu_type="t4")
        gen = TerraformGenerator(terraform_config=tfc)
        assert gen._terraform_config is tfc

    def test_generator_generate_works_with_no_config(self) -> None:
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType
        from general_ludd.infra.terraform import TerraformGenerator
        gen = TerraformGenerator()
        cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4)
        hcl = gen.generate(cfg)
        assert isinstance(hcl, str)
        assert len(hcl) > 0

    def test_generator_build_tfvars_respects_config(self) -> None:
        from general_ludd.config.user_config import TerraformConfig
        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType
        from general_ludd.infra.terraform import TerraformGenerator
        tfc = TerraformConfig(region="eu-west-1", gpu_count=4)
        gen = TerraformGenerator(terraform_config=tfc)
        cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4)
        tfvars = gen.build_tfvars(cfg)
        assert 'region' in tfvars
        assert 'gpu_count' in tfvars


# ------------------------------------------------------------------
# 3. CLI ``gludd config terraform`` subcommand
# ------------------------------------------------------------------

class TestCLIConfigTerraformSubcommand:
    """``gludd config terraform`` parses as a CLI subcommand with get/set."""

    @staticmethod
    def _build_parser() -> Any:
        from general_ludd.cli import build_parser
        parser, _ = build_parser()
        return parser

    def test_config_is_registered_subcommand(self) -> None:
        parser = self._build_parser()
        ns = parser.parse_args(["config", "terraform"])
        assert ns.command == "config"
        assert ns.config_command == "terraform"

    def test_config_terraform_get_has_func(self) -> None:
        parser = self._build_parser()
        ns = parser.parse_args(["config", "terraform", "get"])
        assert ns.func is not None
        assert callable(ns.func)

    def test_config_terraform_get_default_no_field(self) -> None:
        parser = self._build_parser()
        ns = parser.parse_args(["config", "terraform", "get"])
        assert ns.field is None

    def test_config_terraform_get_with_field(self) -> None:
        parser = self._build_parser()
        ns = parser.parse_args(["config", "terraform", "get", "--field", "region"])
        assert ns.field == "region"

    def test_config_terraform_set_value(self) -> None:
        parser = self._build_parser()
        ns = parser.parse_args(["config", "terraform", "set",
                                  "region", "us-east-2"])
        assert ns.terraform_command == "set"
        assert ns.field == "region"
        assert ns.value == "us-east-2"
        assert ns.func is not None
        assert callable(ns.func)

    def test_config_terraform_get_func_is_wired(self) -> None:
        from general_ludd.cli import _cmd_config_terraform_get
        parser = self._build_parser()
        ns = parser.parse_args(["config", "terraform", "get", "--field", "region"])
        assert ns.func is _cmd_config_terraform_get

    def test_config_terraform_set_func_is_wired(self) -> None:
        from general_ludd.cli import _cmd_config_terraform_set
        parser = self._build_parser()
        ns = parser.parse_args(["config", "terraform", "set", "region", "eu-west-1"])
        assert ns.func is _cmd_config_terraform_set


# ------------------------------------------------------------------
# 4. DeploymentManager plan() and validate() methods
# ------------------------------------------------------------------

class TestDeploymentManagerPlanValidate:
    """DeploymentManager has plan() and validate() async methods."""

    def test_plan_method_exists(self) -> None:
        from general_ludd.infra.deployment import DeploymentManager
        dm = DeploymentManager()
        assert callable(dm.plan)

    def test_validate_method_exists(self) -> None:
        from general_ludd.infra.deployment import DeploymentManager
        dm = DeploymentManager()
        assert callable(dm.validate)

    def test_plan_is_async_coroutine(self) -> None:
        import inspect

        from general_ludd.infra.deployment import DeploymentManager
        dm = DeploymentManager()
        assert inspect.iscoroutinefunction(dm.plan)

    def test_validate_is_async_coroutine(self) -> None:
        import inspect

        from general_ludd.infra.deployment import DeploymentManager
        dm = DeploymentManager()
        assert inspect.iscoroutinefunction(dm.validate)

    def test_plan_signature_takes_compute_config(self) -> None:
        import inspect

        from general_ludd.infra.deployment import DeploymentManager
        sig = inspect.signature(DeploymentManager.plan)
        params = list(sig.parameters.keys())
        assert "config" in params

    def test_validate_signature_takes_compute_config(self) -> None:
        import inspect

        from general_ludd.infra.deployment import DeploymentManager
        sig = inspect.signature(DeploymentManager.validate)
        params = list(sig.parameters.keys())
        assert "config" in params

    def test_plan_returns_dict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType
        from general_ludd.infra.deployment import DeploymentManager
        dm = DeploymentManager()
        monkeypatch.setattr(
            dm, "_run_terraform",
            AsyncMock(return_value={"stdout": "", "stderr": "", "returncode": 0}),
        )
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0
        monkeypatch.setattr(
            asyncio, "create_subprocess_exec",
            AsyncMock(return_value=mock_proc),
        )
        cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4)
        result = asyncio.run(dm.plan(cfg))
        assert isinstance(result, dict)
        assert "stdout" in result or "changes_present" in result

    def test_validate_returns_dict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio
        from unittest.mock import AsyncMock

        from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType
        from general_ludd.infra.deployment import DeploymentManager
        dm = DeploymentManager()
        monkeypatch.setattr(
            dm, "_run_terraform",
            AsyncMock(return_value={"stdout": "", "stderr": "", "returncode": 0}),
        )
        cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4)
        result = asyncio.run(dm.validate(cfg))
        assert isinstance(result, dict)


# ------------------------------------------------------------------
# 5. QEMU virtualization provider in versions.tf
# ------------------------------------------------------------------

class TestQemuVirtualizationProviderInVersionsTf:
    """The canonical versions.tf uses libvirt for QEMU virtualization."""

    @property
    def versions_path(self) -> Path:
        return ROOT / "infra" / "terraform" / "versions.tf"

    def test_versions_tf_exists(self) -> None:
        assert self.versions_path.is_file(), (
            f"versions.tf missing at {self.versions_path}"
        )

    def test_has_required_providers_block(self) -> None:
        content = self.versions_path.read_text()
        assert "required_providers" in content

    def test_libvirt_is_registered(self) -> None:
        content = self.versions_path.read_text()
        assert "libvirt" in content.lower()

    def test_libvirt_source_is_maintained(self) -> None:
        content = self.versions_path.read_text()
        assert 'source  = "dmacvicar/libvirt"' in content
        assert "qemu =" not in content.lower()


# ------------------------------------------------------------------
# 6. QemuConfig / detect — platform / arch
# ------------------------------------------------------------------

class TestQemuDetection:
    """QemuConfig and detect() return platform and arch for the current machine."""

    def test_qemu_detect_is_importable(self) -> None:
        from general_ludd.infra.qemu_detect import QemuConfig, detect
        assert detect is not None
        assert QemuConfig is not None

    def test_detect_returns_qemu_config(self) -> None:
        from general_ludd.infra.qemu_detect import QemuConfig, detect
        result = detect()
        assert isinstance(result, QemuConfig)

    def test_platform_is_valid_literal(self) -> None:
        from general_ludd.infra.qemu_detect import detect
        qc = detect()
        assert qc.platform in ("darwin", "linux", "unknown")

    def test_arch_is_valid_literal(self) -> None:
        from general_ludd.infra.qemu_detect import detect
        qc = detect()
        assert qc.arch in ("arm64", "amd64", "unknown")

    def test_platform_matches_system(self) -> None:
        from general_ludd.infra.qemu_detect import detect
        qc = detect()
        system = platform.system()
        if system == "Darwin":
            assert qc.platform == "darwin"
        elif system == "Linux":
            assert qc.platform == "linux"
        else:
            assert qc.platform == "unknown"

    def test_arch_matches_machine(self) -> None:
        from general_ludd.infra.qemu_detect import detect
        qc = detect()
        machine = platform.machine()
        valid: dict[str, tuple[str, ...]] = {
            "x86_64": ("amd64",),
            "AMD64": ("amd64",),
            "arm64": ("arm64",),
            "aarch64": ("arm64",),
        }
        expected = valid.get(machine, ("unknown",))
        assert qc.arch in expected, (
            f"detect().arch={qc.arch!r} not in {expected} "
            f"(platform.machine={machine!r})"
        )

    def test_acceleration_is_valid(self) -> None:
        from general_ludd.infra.qemu_detect import detect
        qc = detect()
        assert qc.acceleration in ("hvf", "kvm", "none")

    def test_binary_path_is_str_or_none(self) -> None:
        from general_ludd.infra.qemu_detect import detect
        qc = detect()
        assert qc.binary_path is None or isinstance(qc.binary_path, str)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
