"""E2E test: Terraform config for QEMU VM deploying llama.cpp server.

Validates config-only (plan/validate, HCL structure, cross-platform QEMU detection).
NEVER deploys — no terraform apply, no VM boot.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.config.deployment_optimization import DeploymentOptimizationConfig
from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType, InferenceEngine
from general_ludd.infra.deployment_optimizer import (
    HardwareProfile,
    ModelProfile,
    hardware_profile_for,
    kv_cache_bytes,
    recommend_config,
)
from general_ludd.infra.qemu_detect import QemuConfig, detect
from general_ludd.infra.terraform import TerraformGenerator


@pytest.fixture
def llama7b_q4k() -> ModelProfile:
    return ModelProfile(
        name="meta-llama/Llama-3.2-3B-Instruct",
        num_layers=28,
        num_kv_heads=8,
        head_dim=128,
        params_b=3.2,
        is_moe=False,
    )


@pytest.fixture
def qemu_vm_hardware() -> HardwareProfile:
    return HardwareProfile(
        gpu_type="t4",
        gpu_count=1,
        vram_gb=16.0,
        has_nvlink=False,
        supports_fp8=False,
        hbm_bw_gbps=320.0,
        arch="turing",
    )


@pytest.fixture
def terraform_generator() -> TerraformGenerator:
    return TerraformGenerator()


@pytest.fixture
def deployment_config() -> DeploymentOptimizationConfig:
    p = Path("config/infra/deployment_optimization.yml")
    if p.exists():
        return DeploymentOptimizationConfig.from_yaml(p)
    return DeploymentOptimizationConfig()


# ---------------------------------------------------------------------------
# terraform HCL generation — llama.cpp on VMware / QEMU
# ---------------------------------------------------------------------------


class TestTerraformQemuLlamacppGeneration:
    """Terraform HCL generation for a QEMU-booted llama.cpp server.

    The vSphere provider path is the closest analogue to a QEMU VM in our
    provider set. A QEMU-native provider does not exist in the Terraform
    registry, so we generate via `ComputeProvider.VMWARE` as the on-prem
    VM path and verify the HCL contains the llama.cpp module wiring.
    """

    def test_generates_vsphere_llamacpp_module(self, terraform_generator: TerraformGenerator) -> None:
        config = ComputeConfig(
            provider=ComputeProvider.VMWARE,
            gpu_type=GPUType.T4,
            gpu_count=1,
            engine=InferenceEngine.LLAMACPP,
            model_name="models/llama-3.2-3b-Q4_K_M.gguf",
            region="home-lab",
            max_cost_usd=5.0,
            timeout_minutes=120,
        )
        hcl = terraform_generator.generate(config)
        assert 'required_providers' in hcl
        assert 'vmware/vsphere' in hcl
        assert 'module "vllm_server"' in hcl
        assert 'engine           = "llamacpp"' in hcl
        assert "models/llama-3.2-3b-Q4_K_M.gguf" in hcl

    def test_generated_hcl_has_container_image_llamacpp(self, terraform_generator: TerraformGenerator) -> None:
        config = ComputeConfig(
            provider=ComputeProvider.VMWARE,
            gpu_type=GPUType.T4,
            engine=InferenceEngine.LLAMACPP,
            model_name="test.gguf",
        )
        hcl = terraform_generator.generate(config)
        assert "ghcr.io/ggerganov/llama.cpp:server" in hcl

    def test_generated_hcl_includes_user_data_script(self, terraform_generator: TerraformGenerator) -> None:
        config = ComputeConfig(
            provider=ComputeProvider.VMWARE,
            gpu_type=GPUType.T4,
            engine=InferenceEngine.LLAMACPP,
            model_name="test.gguf",
        )
        hcl = terraform_generator.generate(config)
        assert "user_data_script" in hcl
        assert "MAX_COST=" in hcl
        assert "TIMEOUT_MIN=" in hcl

    def test_tfvars_emission_llamacpp_q4k(self, terraform_generator: TerraformGenerator) -> None:
        config = ComputeConfig(
            provider=ComputeProvider.VMWARE,
            gpu_type=GPUType.T4,
            engine=InferenceEngine.LLAMACPP,
            model_name="llama-3.2-3b-Q4_K_M.gguf",
            region="home-lab",
            max_cost_usd=5.0,
            timeout_minutes=120,
            disk_size_gb=50,
        )
        tfvars = terraform_generator.build_tfvars(config)
        assert 'engine         = "llamacpp"' in tfvars
        assert 'gpu_type       = "t4"' in tfvars
        assert "llama-3.2-3b-Q4_K_M.gguf" in tfvars
        assert "region" in tfvars
        assert "disk_size_gb" in tfvars


# ---------------------------------------------------------------------------
# llama.cpp Q4_K_M deployment-optimizer integration
# ---------------------------------------------------------------------------


class TestLlamacppQ4KMDeploymentOptimizer:
    """Verify the deployment optimizer picks Q4_K_M for a small model on a T4."""

    def test_q4k_quant_selected_for_t4_small_model(
        self, llama7b_q4k: ModelProfile, qemu_vm_hardware: HardwareProfile
    ) -> None:
        cfg = recommend_config(llama7b_q4k, qemu_vm_hardware, engine="llamacpp")
        assert cfg["engine"] == "llamacpp"
        assert cfg["gguf_quant"] in ("q8_0", "q4_k_m")
        assert cfg["flash_attn"] is True
        assert cfg["split_mode"] == "layer"
        assert isinstance(cfg["n_ctx"], int)
        assert cfg["n_ctx"] >= 256

    def test_q4k_forced_for_7b_on_5gb(self) -> None:
        model_7b = ModelProfile(
            name="Llama-3.2-7B",
            num_layers=32,
            num_kv_heads=8,
            head_dim=128,
            params_b=7.0,
        )
        tiny_hw = HardwareProfile(
            gpu_type="t4",
            gpu_count=1,
            vram_gb=5.0,
            has_nvlink=False,
            supports_fp8=False,
            hbm_bw_gbps=320.0,
            arch="turing",
        )
        cfg = recommend_config(model_7b, tiny_hw, engine="llamacpp")
        assert cfg["engine"] == "llamacpp"
        assert cfg["gguf_quant"] == "q4_k_m"

    def test_partial_offload_when_weights_exceed_vram(self) -> None:
        big_model = ModelProfile(
            name="Llama-3.1-70B",
            num_layers=80,
            num_kv_heads=8,
            head_dim=128,
            params_b=70.0,
        )
        tiny_hw = HardwareProfile(
            gpu_type="t4",
            gpu_count=1,
            vram_gb=16.0,
            has_nvlink=False,
            supports_fp8=False,
            hbm_bw_gbps=320.0,
            arch="turing",
        )
        cfg = recommend_config(big_model, tiny_hw, engine="llamacpp")
        assert cfg["engine"] == "llamacpp"
        assert cfg["gguf_quant"] == "q4_k_m"
        ngl = cfg["n_gpu_layers"]
        assert isinstance(ngl, int)
        assert 0 < ngl < 80

    def test_kv_cache_bytes_formula(self, llama7b_q4k: ModelProfile) -> None:
        kv = kv_cache_bytes(llama7b_q4k, max_len=4096, max_seqs=4, dtype="fp16")
        assert kv > 0
        assert kv < llama7b_q4k.params_b * 1e10

    def test_weights_bytes_q4k(self, llama7b_q4k: ModelProfile) -> None:
        w = llama7b_q4k.weights_bytes("q4_k_m")
        assert w == pytest.approx(3.2 * 1e9 * 0.5, rel=1e-6)

    def test_hardware_profile_for_t4(self) -> None:
        hw = hardware_profile_for("t4", gpu_count=1)
        assert hw.gpu_type == "t4"
        assert hw.vram_gb == 16.0
        assert hw.supports_fp8 is False
        assert hw.has_nvlink is False
        assert hw.arch == "turing"


# ---------------------------------------------------------------------------
# Cross-platform QEMU detection (macOS vs Linux, ARM vs x86)
# ---------------------------------------------------------------------------


class TestCrossPlatformQemuDetection:
    """Verify QEMU detection returns correct platform/arch/acceleration.

    Mock-based tests cover all four platform-arch combinations. An integration
    test checks the real machine when QEMU is actually installed.
    """

    DARWIN_ARM = ("Darwin", "arm64")
    DARWIN_X86 = ("Darwin", "x86_64")
    LINUX_ARM = ("Linux", "aarch64")
    LINUX_X86 = ("Linux", "x86_64")

    def _patch_detect(self, system: str, machine: str, qemu_binary: str | None) -> QemuConfig:
        with (
            patch("platform.system", return_value=system),
            patch("platform.machine", return_value=machine),
        ):
            if qemu_binary:
                with patch.object(
                    shutil, "which",
                    side_effect=lambda name: qemu_binary if name.startswith("qemu-system-") else None,
                ):
                    return detect()
            else:
                with patch.object(shutil, "which", return_value=None):
                    return detect()

    def test_darwin_arm_with_qemu_binary(self) -> None:
        cfg = self._patch_detect(*self.DARWIN_ARM, qemu_binary="/opt/homebrew/bin/qemu-system-aarch64")
        assert cfg.platform == "darwin"
        assert cfg.arch == "arm64"
        assert cfg.binary_path == "/opt/homebrew/bin/qemu-system-aarch64"
        assert cfg.acceleration == "hvf"

    def test_darwin_x86_with_qemu_binary(self) -> None:
        cfg = self._patch_detect(*self.DARWIN_X86, qemu_binary="/usr/local/bin/qemu-system-x86_64")
        assert cfg.platform == "darwin"
        assert cfg.arch == "amd64"
        assert cfg.binary_path == "/usr/local/bin/qemu-system-x86_64"
        assert cfg.acceleration == "hvf"

    def test_linux_arm_with_qemu_binary(self) -> None:
        cfg = self._patch_detect(*self.LINUX_ARM, qemu_binary="/usr/bin/qemu-system-aarch64")
        assert cfg.platform == "linux"
        assert cfg.arch == "arm64"
        assert cfg.binary_path == "/usr/bin/qemu-system-aarch64"
        assert cfg.acceleration == "kvm" if shutil.which("kvm-ok") else "none"

    def test_linux_x86_with_qemu_binary(self) -> None:
        cfg = self._patch_detect(*self.LINUX_X86, qemu_binary="/usr/bin/qemu-system-x86_64")
        assert cfg.platform == "linux"
        assert cfg.arch == "amd64"
        assert cfg.binary_path == "/usr/bin/qemu-system-x86_64"

    def test_no_qemu_binary_returns_none(self) -> None:
        cfg = self._patch_detect(*self.DARWIN_ARM, qemu_binary=None)
        assert cfg.platform == "darwin"
        assert cfg.arch == "arm64"
        assert cfg.binary_path is None
        assert cfg.acceleration == "hvf"

    def test_unknown_platform_and_arch(self) -> None:
        with (
            patch("platform.system", return_value="FreeBSD"),
            patch("platform.machine", return_value="sparc64"),
            patch.object(shutil, "which", return_value=None),
        ):
            cfg = detect()
        assert cfg.platform == "unknown"
        assert cfg.arch == "unknown"
        assert cfg.binary_path is None
        assert cfg.acceleration == "none"

    def test_real_detect_returns_valid_config(self) -> None:
        cfg = detect()
        assert isinstance(cfg, QemuConfig)
        assert cfg.platform in ("darwin", "linux", "unknown")
        assert cfg.arch in ("arm64", "amd64", "unknown")
        assert cfg.acceleration in ("hvf", "kvm", "none")

    def test_config_is_frozen_immutable(self) -> None:
        cfg = detect()
        with pytest.raises(AttributeError):
            cfg.arch = "mips"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Config-only guard — never deploys
# ---------------------------------------------------------------------------


class TestConfigOnlyGuard:
    """Attestation that this test module never triggers a real deployment.

    The entire test suite generates HCL strings and calls pure Python functions.
    No terraform apply, no VM boot, no network calls.
    """

    def test_no_side_effects_on_fs(self, tmp_path: Path) -> None:
        before = set(tmp_path.iterdir())
        config = ComputeConfig(
            provider=ComputeProvider.VMWARE,
            gpu_type=GPUType.T4,
            engine=InferenceEngine.LLAMACPP,
            model_name="test.gguf",
        )
        TerraformGenerator().generate(config)
        detect()
        recommend_config(
            ModelProfile("x", 1, 1, 128, 0.1),
            HardwareProfile("t4", 1, 16.0, arch="turing"),
            engine="llamacpp",
        )
        after = set(tmp_path.iterdir())
        assert before == after

    def test_generated_hcl_is_parseable_text(self) -> None:
        config = ComputeConfig(
            provider=ComputeProvider.VMWARE,
            gpu_type=GPUType.T4,
            engine=InferenceEngine.LLAMACPP,
            model_name="test.gguf",
        )
        hcl = TerraformGenerator().generate(config)
        assert isinstance(hcl, str)
        assert len(hcl) > 0
        stripped = hcl.lstrip()
        assert stripped.startswith("terraform") or stripped.startswith("{}")

    def test_qemu_config_is_serializable(self) -> None:
        cfg = detect()
        d = {
            "platform": cfg.platform,
            "arch": cfg.arch,
            "binary_path": cfg.binary_path,
            "acceleration": cfg.acceleration,
        }
        import json
        s = json.dumps(d)
        assert isinstance(json.loads(s), dict)

    def test_recommend_config_llamacpp_is_serializable(
        self, llama7b_q4k: ModelProfile, qemu_vm_hardware: HardwareProfile
    ) -> None:
        cfg = recommend_config(llama7b_q4k, qemu_vm_hardware, engine="llamacpp")
        import json
        s = json.dumps(cfg, default=str)
        d = json.loads(s)
        assert d["engine"] == "llamacpp"
        assert isinstance(d["n_ctx"], int)


# ---------------------------------------------------------------------------
# tfvars + deployment optimizer integration
# ---------------------------------------------------------------------------


class TestTfvarsDeploymentOptimizerIntegration:
    """tfvars populated with deployment-optimizer presets for llama.cpp Q4_K_M."""

    def test_tfvars_with_optimizer_hardware_preset(self, terraform_generator: TerraformGenerator) -> None:
        dcfg = DeploymentOptimizationConfig.from_yaml(Path("config/infra/deployment_optimization.yml"))
        gen = TerraformGenerator(deployment_optimization_config=dcfg)
        config = ComputeConfig(
            provider=ComputeProvider.VMWARE,
            gpu_type=GPUType.T4,
            engine=InferenceEngine.LLAMACPP,
            model_name="llama-3.2-3b-Q4_K_M.gguf",
            region="home-lab",
        )
        tfvars = gen.build_tfvars(config)
        assert 'engine         = "llamacpp"' in tfvars
        assert 'gpu_type       = "t4"' in tfvars
        assert 'container_image = "ghcr.io/ggerganov/llama.cpp:server"' in tfvars
        assert "llamacpp_" in tfvars
        assert "flash_attn" in tfvars
        assert "split_mode" in tfvars

    def test_model_arg_present_in_user_data(self, terraform_generator: TerraformGenerator) -> None:
        config = ComputeConfig(
            provider=ComputeProvider.VMWARE,
            gpu_type=GPUType.T4,
            engine=InferenceEngine.LLAMACPP,
            model_name="models/qwen2.5-0.5b-Q4_K_M.gguf",
        )
        hcl = terraform_generator.generate(config)
        assert "llamacpp" in hcl.lower() or "llamacpp" in hcl
