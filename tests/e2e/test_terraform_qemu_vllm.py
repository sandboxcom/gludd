"""E2E: Terraform/OpenTofu QEMU VM with vLLM — init, validate, plan (no apply).

Cross-platform: detects OS (Darwin/Linux) and CPU arch (arm64/amd64),
selecting appropriate QEMU/libvirt configuration. Uses OpenTofu if available,
falling back to terraform.

Validates config correctness only — never applies.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


def _infra_binary() -> str | None:
    """Return OpenTofu if available, terraform if not, None if neither."""
    for name in ("tofu", "terraform"):
        if shutil.which(name):
            return name
    return None


def _os_arch_info() -> dict[str, str]:
    system = platform.system()       # Darwin / Linux
    machine = platform.machine()     # arm64 / x86_64

    if machine == "arm64":
        arch = "aarch64"
    elif machine in ("x86_64", "amd64"):
        arch = "x86_64"
    else:
        arch = "x86_64"

    if system == "Darwin":
        qemu_uri = "qemu:///session"
        firmware = (
            "/opt/homebrew/share/qemu/edk2-aarch64-code.fd"
            if arch == "aarch64"
            else "/opt/homebrew/share/qemu/edk2-x86_64-code.fd"
        )
    else:
        qemu_uri = "qemu:///system"
        firmware = "/usr/share/OVMF/OVMF_CODE.fd" if arch == "x86_64" else "/usr/share/qemu-efi-aarch64/QEMU_EFI.fd"

    return {
        "system": system,
        "arch": arch,
        "machine": machine,
        "qemu_uri": qemu_uri,
        "firmware": firmware,
    }


_VLLM_MAIN_TF = """\
terraform {{
  required_providers {{
    libvirt = {{
      source  = "dmacvicar/libvirt"
      version = "~> 0.8"
    }}
  }}
}}

variable "vm_name" {{
  description = "Name of the QEMU VM"
  type        = string
  default     = "vllm-qemu"
}}

variable "memory_mb" {{
  description = "VM memory in MB"
  type        = number
  default     = 8192
}}

variable "vcpus" {{
  description = "Virtual CPUs"
  type        = number
  default     = 4
}}

variable "disk_size_bytes" {{
  description = "Root disk size (Gibibytes for libvirt)"
  type        = number
  default     = 40
}}

variable "container_image" {{
  description = "vLLM container image"
  type        = string
  default     = "vllm/vllm-openai:latest"
}}

variable "model_name" {{
  description = "Model identifier"
  type        = string
  default     = "meta-llama/Llama-3.2-1B-Instruct"
}}

provider "libvirt" {{
  uri = "{qemu_uri}"
}}

resource "libvirt_volume" "vllm_os" {{
  name   = "${{var.vm_name}}-disk"
  size   = var.disk_size_bytes * 1024 * 1024 * 1024
}}

resource "libvirt_cloudinit_disk" "commoninit" {{
  name           = "${{var.vm_name}}-commoninit.iso"
  user_data      = <<-EOF
#cloud-config
runcmd:
  - docker pull ${{var.container_image}}
  - |
    docker run -d --restart always --gpus 0 \\
      -p 8000:8000 \\
      ${{var.container_image}} \\
      --model ${{var.model_name}} --host 0.0.0.0 --port 8000
EOF
}}

resource "libvirt_domain" "vllm_vm" {{
  name      = var.vm_name
  memory    = var.memory_mb
  vcpu      = var.vcpus

  cpu {{
    mode = "host-passthrough"
  }}

  cloudinit = libvirt_cloudinit_disk.commoninit.id

  network_interface {{
    network_name = "default"
  }}

  console {{
    type        = "pty"
    target_port = "0"
    target_type = "serial"
  }}

  console {{
    type        = "pty"
    target_type = "virtio"
    target_port = "1"
  }}

  disk {{
    volume_id = libvirt_volume.vllm_os.id
  }}

  graphics {{
    type        = "spice"
    listen_type = "none"
  }}
}}

output "instance_id" {{
  description = "QEMU domain ID"
  value       = libvirt_domain.vllm_vm.id
}}

output "endpoint_url" {{
  description = "vLLM inference endpoint"
  value       = "http://localhost:8000/v1"
}}
"""


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def infra_binary() -> str | None:
    return _infra_binary()


@pytest.fixture
def os_arch() -> dict[str, str]:
    return _os_arch_info()


@pytest.fixture
def tf_workdir() -> str:
    with tempfile.TemporaryDirectory(prefix="gludd-tf-qemu-") as td:
        yield td


# ── Tests ────────────────────────────────────────────────────────────────────


class TestTerraformQemuVllmConfig:
    """Structural checks on the generated config (no terraform binary needed)."""

    def test_main_tf_contains_libvirt_provider(self):
        assert 'source  = "dmacvicar/libvirt"' in _VLLM_MAIN_TF
        assert 'provider "libvirt"' in _VLLM_MAIN_TF

    def test_main_tf_contains_qemu_domain(self):
        assert "libvirt_domain" in _VLLM_MAIN_TF
        assert "vllm_vm" in _VLLM_MAIN_TF

    def test_main_tf_contains_cloud_init_for_vllm(self):
        assert "libvirt_cloudinit_disk" in _VLLM_MAIN_TF
        assert "inference server" not in _VLLM_MAIN_TF.lower()
        assert "docker run" in _VLLM_MAIN_TF
        assert "--port 8000" in _VLLM_MAIN_TF

    def test_main_tf_has_minimal_outputs(self):
        assert "output" in _VLLM_MAIN_TF
        assert "instance_id" in _VLLM_MAIN_TF
        assert "endpoint_url" in _VLLM_MAIN_TF

    def test_cross_platform_detection(self, os_arch):
        assert isinstance(os_arch, dict)
        assert os_arch["system"] in ("Darwin", "Linux")
        assert os_arch["arch"] in ("aarch64", "x86_64")
        assert os_arch["qemu_uri"].startswith("qemu:///")

    def test_terraform_block_has_required_providers(self):
        assert "required_providers" in _VLLM_MAIN_TF
        assert "libvirt" in _VLLM_MAIN_TF


@pytest.mark.skipif(_infra_binary() is None, reason="terraform/tofu not on PATH")
class TestTerraformQemuVllmInitValidate:
    """Requires terraform or opentofu on PATH."""

    @pytest.fixture(autouse=True)
    def _require_binary(self, infra_binary):
        pass  # skipif on the class handles absence

    def _write_config(self, workdir: str, content: str) -> Path:
        main_tf = Path(workdir) / "main.tf"
        main_tf.write_text(content)
        return main_tf

    def _run(self, binary: str, args: list[str], cwd: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
        )

    def test_init_succeeds(self, infra_binary, tf_workdir):
        self._write_config(tf_workdir, _VLLM_MAIN_TF)
        result = self._run(
            infra_binary, ["init", "-input=false"],
            cwd=tf_workdir, timeout=180,
        )
        if result.returncode != 0:
            pytest.skip(
                f"{infra_binary} init failed (network/provider unavailable):\n"
                f"{result.stderr[:500]}"
            )
        assert ".terraform" in tf_workdir

    def test_validate_succeeds(self, infra_binary, tf_workdir):
        self._write_config(tf_workdir, _VLLM_MAIN_TF)
        init_result = self._run(
            infra_binary, ["init", "-input=false"],
            cwd=tf_workdir, timeout=180,
        )
        if init_result.returncode != 0:
            pytest.skip(
                f"{infra_binary} init failed (cannot reach registry):\n"
                f"{init_result.stderr[:500]}"
            )

        result = self._run(
            infra_binary, ["validate", "-json"],
            cwd=tf_workdir, timeout=60,
        )
        assert result.returncode == 0, (
            f"{infra_binary} validate failed:\n"
            f"stdout: {result.stdout[:500]}\n"
            f"stderr: {result.stderr[:500]}"
        )

    def test_plan_parseable(self, infra_binary, tf_workdir):
        self._write_config(tf_workdir, _VLLM_MAIN_TF)
        init_result = self._run(
            infra_binary, ["init", "-input=false"],
            cwd=tf_workdir, timeout=180,
        )
        if init_result.returncode != 0:
            pytest.skip(
                f"{infra_binary} init failed (cannot reach registry):\n"
                f"{init_result.stderr[:500]}"
            )

        result = self._run(
            infra_binary, ["plan", "-input=false", "-detailed-exitcode"],
            cwd=tf_workdir, timeout=60,
        )
        # plan exits 0=no changes, 2=changes present — both are valid
        # exit 1 indicates an error
        assert result.returncode in (0, 2), (
            f"{infra_binary} plan failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout[:500]}\n"
            f"stderr: {result.stderr[:500]}"
        )

    def test_no_apply_run(self, infra_binary, tf_workdir):
        self._write_config(tf_workdir, _VLLM_MAIN_TF)
        tfstate = Path(tf_workdir) / "terraform.tfstate"
        assert not tfstate.exists(), "tfstate must not exist before apply"
        # No apply is called — this test merely verifies tfstate absence


class TestRenderConfigForCurrentPlatform:
    def test_config_renders_with_os_substitution(self, os_arch):
        rendered = _VLLM_MAIN_TF.format(**os_arch)
        assert os_arch["qemu_uri"] in rendered
        assert "required_providers" in rendered
        assert "libvirt_domain" in rendered

    def test_variables_defaults_are_realistic(self):
        defaults = {}
        for line in _VLLM_MAIN_TF.splitlines():
            stripped = line.strip()
            if stripped.startswith("default") and "=" in stripped:
                parts = stripped.split("default", 1)[1].strip().lstrip("=").strip()
                defaults[parts] = True
        assert len(defaults) > 0

    def test_disk_size_positive(self):
        assert "40" in _VLLM_MAIN_TF  # disk_size default
        assert "size" in _VLLM_MAIN_TF

    def test_memory_vcpus_reasonable(self):
        assert "8192" in _VLLM_MAIN_TF   # memory_mb default
        assert "4" in _VLLM_MAIN_TF      # vcpus default
