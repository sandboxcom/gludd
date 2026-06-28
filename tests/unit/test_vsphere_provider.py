"""Unit tests for VMware/vSphere provider support (Terraform Phase 2).

Implements TDD coverage for docs/design/TERRAFORM_INFRA_STRUCTURE.md §6+§7 Phase 2.
"""

from __future__ import annotations

import ast
from pathlib import Path

from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType
from general_ludd.infra.providers import ProviderRegistry
from general_ludd.infra.terraform import TerraformGenerator

TERRAFORM_PY = Path(__file__).resolve().parents[2] / "src" / "general_ludd" / "infra" / "terraform.py"


class TestVsphereEnumAndRegistry:
    def test_compute_provider_has_vmware_member(self):
        assert hasattr(ComputeProvider, "VMWARE")
        assert ComputeProvider.VMWARE.value == "vmware"

    def test_provider_registry_has_vmware_entry(self):
        registry = ProviderRegistry()
        info = registry.get(ComputeProvider.VMWARE)
        assert info is not None
        assert info.terraform_provider == "hashicorp/vsphere"

    def test_vmware_provider_auth_env_contains_required_keys(self):
        registry = ProviderRegistry()
        info = registry.get(ComputeProvider.VMWARE)
        assert info.auth_env is not None
        for key in ("VSPHERE_USER", "VSPHERE_PASSWORD", "VSPHERE_SERVER"):
            assert key in info.auth_env, f"auth_env missing {key}"

    def test_vmware_provider_auth_source_documents_secret_backend(self):
        registry = ProviderRegistry()
        info = registry.get(ComputeProvider.VMWARE)
        assert info.auth_source is not None
        assert "OpenBao" in info.auth_source or "SecretsManager" in info.auth_source


class TestTerraformGeneratorVsphere:
    def _make_config(self) -> ComputeConfig:
        return ComputeConfig(
            provider=ComputeProvider.VMWARE,
            gpu_type=GPUType.A100_80,
            model_name="test-model",
        )

    def test_generate_vsphere_method_exists(self):
        gen = TerraformGenerator()
        assert hasattr(gen, "_generate_vsphere")
        assert callable(gen._generate_vsphere)

    def test_generate_vsphere_emits_vsphere_and_module_blocks(self):
        gen = TerraformGenerator()
        hcl = gen._generate_vsphere(self._make_config())
        assert isinstance(hcl, str)
        assert "vsphere" in hcl.lower()
        assert "module" in hcl.lower()

    def test_generate_vsphere_uses_vllm_server_module_source(self):
        gen = TerraformGenerator()
        hcl = gen._generate_vsphere(self._make_config())
        assert "modules/vllm-server" in hcl

    def test_generate_vsphere_does_not_inline_vm_resource(self):
        gen = TerraformGenerator()
        hcl = gen._generate_vsphere(self._make_config())
        # Phase 2 contract: the VM is composed via a module, not inlined as a
        # vsphere_virtual_machine resource in the generated stack.
        assert "resource \"vsphere_virtual_machine\"" not in hcl

    def test_generate_dispatches_to_vsphere_for_vmware_provider(self):
        gen = TerraformGenerator()
        hcl = gen.generate(self._make_config())
        assert "vsphere" in hcl.lower()


class TestPyvmomiLazyImport:
    def test_pyvmomi_not_imported_at_module_top_level(self):
        source = TERRAFORM_PY.read_text()
        tree = ast.parse(source)
        top_level_imports: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level_imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                top_level_imports.append(node.module)
        pyvmomi_top_level = [n for n in top_level_imports if n == "pyvmomi" or n.startswith("pyvmomi.")]
        assert not pyvmomi_top_level, (
            f"pyvmomi must not be a top-level import in terraform.py; found: {pyvmomi_top_level}"
        )
