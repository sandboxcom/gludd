"""Tests for Kubernetes provider generation in terraform.py."""

from __future__ import annotations

import re

from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType
from general_ludd.infra.terraform import TerraformGenerator

_INLINE_RESOURCE_RE = re.compile(r'^\s*resource\s+"', re.MULTILINE)


def _build_k8s_config() -> ComputeConfig:
    return ComputeConfig(
        provider=ComputeProvider.KUBERNETES,
        gpu_type=GPUType.T4,
        model_name="test-model",
        allowed_cidr="127.0.0.1/32",
    )


class TestKubernetesDispatchMap:
    def test_kubernetes_in_dispatch_not_generic(self):
        hcl = TerraformGenerator().generate(_build_k8s_config())
        assert "hashicorp/kubernetes" in hcl
        assert 'source  = "hashicorp/kubernetes"' in hcl

    def test_kubernetes_emits_kubernetes_provider_block(self):
        hcl = TerraformGenerator().generate(_build_k8s_config())
        assert re.search(r'provider\s+"kubernetes"', hcl)


class TestKubernetesModuleEmission:
    def test_emits_module_not_inline_resource(self):
        hcl = TerraformGenerator().generate(_build_k8s_config())
        assert 'module "inference_server"' in hcl
        assert not _INLINE_RESOURCE_RE.search(
            hcl
        ), "kubernetes generator must not emit inline resource blocks"

    def test_module_references_kubernetes_deploy(self):
        hcl = TerraformGenerator().generate(_build_k8s_config())
        assert "kubernetes-deploy" in hcl

    def test_module_passes_mapped_vars(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.KUBERNETES,
            gpu_type=GPUType.L4,
            model_name="llama-7b",
            gpu_count=2,
            engine="llamacpp",
            allowed_cidr="127.0.0.1/32",
        )
        hcl = TerraformGenerator().generate(cfg)
        assert "model_name" in hcl
        assert "gpu_count" in hcl
        assert "engine" in hcl
        assert "replicas" in hcl
        assert "service_port" in hcl
        assert "image" in hcl


class TestKubernetesLegacyOutputs:
    def test_instance_ip_output_present(self):
        hcl = TerraformGenerator().generate(_build_k8s_config())
        assert 'output "instance_ip"' in hcl

    def test_endpoint_url_output_present(self):
        hcl = TerraformGenerator().generate(_build_k8s_config())
        assert 'output "endpoint_url"' in hcl

    def test_instance_ip_maps_to_service_endpoint(self):
        hcl = TerraformGenerator().generate(_build_k8s_config())
        assert "module.inference_server.service_endpoint" in hcl

    def test_endpoint_url_contains_http_prefix(self):
        hcl = TerraformGenerator().generate(_build_k8s_config())
        assert 'http://${module.inference_server.service_endpoint}/v1' in hcl


class TestKubernetesStructuralValidity:
    def test_contains_terraform_block(self):
        hcl = TerraformGenerator().generate(_build_k8s_config())
        assert re.search(r'^terraform\s+\{', hcl, re.MULTILINE)

    def test_contains_required_providers(self):
        hcl = TerraformGenerator().generate(_build_k8s_config())
        assert "required_providers" in hcl

    def test_contains_kubernetes_provider(self):
        hcl = TerraformGenerator().generate(_build_k8s_config())
        assert re.search(r'provider\s+"kubernetes"\s+\{\}', hcl)

    def test_contains_module_block(self):
        hcl = TerraformGenerator().generate(_build_k8s_config())
        assert re.search(r'module\s+"inference_server"\s+\{', hcl)

    def test_no_shell_metachar_injection(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.KUBERNETES,
            gpu_type=GPUType.T4,
            model_name="safe-model",
            allowed_cidr="127.0.0.1/32",
        )
        hcl = TerraformGenerator().generate(cfg)
        forbidden = (";", "&&", "|", "`", "$(", "||")
        for ch in forbidden:
            assert ch not in hcl, f"forbidden metacharacter {ch!r} found in HCL output"


class TestKubernetesPhase4Compliance:
    def test_no_inline_resource_blocks(self):
        hcl = TerraformGenerator().generate(_build_k8s_config())
        assert not _INLINE_RESOURCE_RE.search(hcl), (
            "Phase 4 violation: kubernetes generator emits inline resource blocks"
        )
