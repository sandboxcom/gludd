"""Unit tests for infra compute models, provider registry, and terraform generator."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from general_ludd.infra.compute import ComputeConfig, ComputeInstance, ComputeProvider, GPUType, InferenceEngine
from general_ludd.infra.providers import ProviderInfo, ProviderRegistry
from general_ludd.infra.terraform import TerraformGenerator


class TestComputeProvider:
    def test_aws_value(self):
        assert ComputeProvider.AWS == "aws"

    def test_all_providers_present(self):
        expected = {
            "aws", "azure", "gcp", "qemu", "runpod", "vast", "vast_ai",
            "lambda_labs", "modal", "coreweave", "digital_ocean", "oracle",
            "vsphere", "vmware", "kubernetes", "together_ai", "fireworks_ai",
            "huggingface", "replicate",
        }
        actual = {p.value for p in ComputeProvider}
        assert actual == expected


class TestGPUType:
    def test_t4_value(self):
        assert GPUType.T4 == "t4"

    def test_all_gpus_present(self):
        expected = {
            "t4", "a10g", "l4", "a10", "rtx_4090", "rtx_6000_ada",
            "a40", "l40s", "amd_mi250", "a100_40", "a100_80", "h100", "h200",
        }
        actual = {g.value for g in GPUType}
        assert actual == expected


class TestInferenceEngine:
    def test_engines(self):
        assert InferenceEngine.LLAMACPP == "llamacpp"
        assert InferenceEngine.VLLM == "vllm"


class TestComputeConfig:
    def test_defaults(self):
        cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, model_name="llama3")
        assert cfg.gpu_count == 1
        assert cfg.engine == InferenceEngine.VLLM
        assert cfg.region is None
        assert cfg.spot is True
        assert cfg.max_cost_usd == 10.0
        assert cfg.timeout_minutes == 60.0
        assert cfg.disk_size_gb == 100
        assert cfg.container_image is None
        assert cfg.api_key_alias is None
        assert cfg.provider_auth_aliases is None

    def test_full_config(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.GCP,
            gpu_type=GPUType.A100_80,
            gpu_count=4,
            engine=InferenceEngine.LLAMACPP,
            model_name="mistral-7b",
            region="us-central1",
            spot=False,
            max_cost_usd=50.0,
            timeout_minutes=120.0,
            disk_size_gb=200,
            container_image="custom/vllm:latest",
            api_key_alias="gcp_key",
            provider_auth_aliases={"ARM_CLIENT_ID": "AZURE_CLIENT_ID"},
        )
        assert cfg.provider == ComputeProvider.GCP
        assert cfg.gpu_type == GPUType.A100_80
        assert cfg.gpu_count == 4
        assert cfg.region == "us-central1"
        assert cfg.spot is False
        assert cfg.container_image == "custom/vllm:latest"
        assert cfg.provider_auth_aliases == {"ARM_CLIENT_ID": "AZURE_CLIENT_ID"}

    def test_serialization_roundtrip(self):
        cfg = ComputeConfig(provider=ComputeProvider.RUNPOD, gpu_type=GPUType.A100_80, model_name="test-model")
        data = cfg.model_dump()
        restored = ComputeConfig.model_validate(data)
        assert restored == cfg

    def test_json_roundtrip(self):
        cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.H100, model_name="deepseek", spot=False)
        json_str = cfg.model_dump_json()
        restored = ComputeConfig.model_validate_json(json_str)
        assert restored == cfg

    def test_auth_aliases_serialization_roundtrip(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            provider_auth_aliases={
                "ARM_CLIENT_ID": "AZURE_CLIENT_ID",
                "ARM_CLIENT_SECRET": "AZURE_CLIENT_SECRET",
            },
        )
        data = cfg.model_dump()
        restored = ComputeConfig.model_validate(data)
        expected = {"ARM_CLIENT_ID": "AZURE_CLIENT_ID", "ARM_CLIENT_SECRET": "AZURE_CLIENT_SECRET"}
        assert restored.provider_auth_aliases == expected

    def test_spot_true(self):
        cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, model_name="m", spot=True)
        assert cfg.spot is True


class TestComputeInstance:
    def test_creation(self):
        inst = ComputeInstance(
            instance_id="i-12345",
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.A100_80,
        )
        assert inst.instance_id == "i-12345"
        assert inst.status == "pending"
        assert inst.ip_address is None
        assert inst.port == 8000
        assert inst.cost_incurred == 0.0
        assert inst.endpoint_url is None

    def test_with_all_fields(self):
        now = datetime.now(UTC)
        inst = ComputeInstance(
            instance_id="runpod-abc",
            provider=ComputeProvider.RUNPOD,
            status="running",
            ip_address="1.2.3.4",
            port=8080,
            gpu_type=GPUType.RTX_4090,
            endpoint_url="http://1.2.3.4:8080/v1",
            created_at=now,
            cost_incurred=2.50,
        )
        assert inst.status == "running"
        assert inst.ip_address == "1.2.3.4"
        assert inst.cost_incurred == 2.50

    def test_status_values(self):
        for status in ("pending", "running", "terminated", "failed"):
            inst = ComputeInstance(instance_id="x", provider=ComputeProvider.AWS, gpu_type=GPUType.T4, status=status)
            assert inst.status == status


class TestProviderInfo:
    def test_creation(self):
        info = ProviderInfo(
            provider=ComputeProvider.AWS,
            display_name="Amazon Web Services",
            terraform_provider="hashicorp/aws",
            supports_spot=True,
            sub_hour_billing=False,
            min_gpu=GPUType.T4,
            max_gpu=GPUType.A100_80,
            pricing={"t4": 0.20, "a10g": 0.40, "a100_80": 10.00},
        )
        assert info.provider == ComputeProvider.AWS
        assert info.supports_spot is True
        assert info.pricing["t4"] == 0.20


class TestProviderRegistry:
    def test_get_aws(self):
        reg = ProviderRegistry()
        info = reg.get(ComputeProvider.AWS)
        assert info.display_name == "Amazon Web Services"
        assert info.terraform_provider == "hashicorp/aws"
        assert info.supports_spot is True

    def test_get_gcp(self):
        reg = ProviderRegistry()
        info = reg.get(ComputeProvider.GCP)
        assert info.display_name == "Google Cloud Platform"
        assert info.terraform_provider == "hashicorp/google"

    def test_get_azure(self):
        reg = ProviderRegistry()
        info = reg.get(ComputeProvider.AZURE)
        assert info.terraform_provider == "hashicorp/azurerm"

    def test_get_runpod(self):
        reg = ProviderRegistry()
        info = reg.get(ComputeProvider.RUNPOD)
        assert info.terraform_provider == "runpod/runpod"

    def test_get_vast_ai(self):
        reg = ProviderRegistry()
        info = reg.get(ComputeProvider.VAST_AI)
        assert info is not None

    def test_get_lambda_labs(self):
        reg = ProviderRegistry()
        info = reg.get(ComputeProvider.LAMBDA_LABS)
        assert info is not None

    def test_get_modal(self):
        reg = ProviderRegistry()
        info = reg.get(ComputeProvider.MODAL)
        assert info is not None

    def test_get_coreweave(self):
        reg = ProviderRegistry()
        info = reg.get(ComputeProvider.COREWEAVE)
        assert info is not None

    def test_get_digital_ocean(self):
        reg = ProviderRegistry()
        info = reg.get(ComputeProvider.DIGITAL_OCEAN)
        assert info is not None

    def test_get_oracle(self):
        reg = ProviderRegistry()
        info = reg.get(ComputeProvider.ORACLE)
        assert info is not None

    def test_list_providers(self):
        reg = ProviderRegistry()
        providers = reg.list_providers()
        assert len(providers) == 16
        names = {p.provider for p in providers}
        assert ComputeProvider.AWS in names
        assert ComputeProvider.ORACLE in names

    def test_get_cheapest_for_gpu_a100_80(self):
        reg = ProviderRegistry()
        cheapest = reg.get_cheapest_for_gpu(GPUType.A100_80)
        assert cheapest.provider in (
            ComputeProvider.VAST_AI,
            ComputeProvider.VMWARE,
            ComputeProvider.KUBERNETES,
        )
        assert cheapest.pricing["a100_80"] >= 0

    def test_get_cheapest_for_gpu_t4(self):
        reg = ProviderRegistry()
        cheapest = reg.get_cheapest_for_gpu(GPUType.T4)
        assert cheapest.provider in (
            ComputeProvider.AWS,
            ComputeProvider.KUBERNETES,
        )
        assert cheapest.pricing["t4"] >= 0

    def test_get_cheapest_for_gpu_l4(self):
        reg = ProviderRegistry()
        cheapest = reg.get_cheapest_for_gpu(GPUType.L4)
        assert cheapest.provider == ComputeProvider.GCP
        assert cheapest.pricing["l4"] == 0.22

    def test_list_by_price_ordering(self):
        reg = ProviderRegistry()
        by_price = reg.list_by_price()
        prices = [price for _, price in by_price]
        assert prices == sorted(prices)

    def test_list_by_price_has_all_providers(self):
        reg = ProviderRegistry()
        by_price = reg.list_by_price()
        assert len(by_price) == 16

    def test_get_raises_for_unknown(self):
        reg = ProviderRegistry()
        with pytest.raises(KeyError):
            cast(Any, reg).get("nonexistent")


class TestTerraformGeneratorAWS:
    def setup_method(self):
        self.gen = TerraformGenerator()
        self.cfg = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.A10G,
            gpu_count=1,
            model_name="meta-llama/Meta-Llama-3-8B-Instruct",
            region="us-east-1",
            spot=True,
        )

    def test_generates_hcl_string(self):
        tf = self.gen.generate(self.cfg)
        assert isinstance(tf, str)
        assert len(tf) > 100

    def test_contains_terraform_block(self):
        tf = self.gen.generate(self.cfg)
        assert 'terraform {' in tf
        assert '"hashicorp/aws"' in tf

    def test_contains_provider_block(self):
        tf = self.gen.generate(self.cfg)
        assert 'provider "aws"' in tf

    def test_contains_instance_resource(self):
        # Phase 4 — module-style contract: no inline aws_instance; module block present.
        tf = self.gen.generate(self.cfg)
        assert 'module "vllm_server"' in tf
        assert 'resource "aws_instance"' not in tf
        assert not re.search(r'^\s*resource\s+"', tf, re.MULTILINE)

    def test_contains_module_source(self):
        # Phase 4 — was test_contains_gpu_type; GPU type no longer inline.
        tf = self.gen.generate(self.cfg)
        assert 'source = "./modules/vllm-server"' in tf

    def test_contains_model_var_ref(self):
        # Phase 4 — model name flows via tfvars; the module block references var.model.
        tf = self.gen.generate(self.cfg)
        assert "var.model" in tf

    def test_contains_region(self):
        tf = self.gen.generate(self.cfg)
        assert "us-east-1" in tf

    def test_emits_instance_id_output(self):
        # Phase 4 — was test_spot_instance_config; spot config lives in tfvars now.
        tf = self.gen.generate(self.cfg)
        assert 'output "instance_id"' in tf

    def test_emits_base_url_output(self):
        # Phase 4 — was test_contains_port_8000; port lives in the module.
        tf = self.gen.generate(self.cfg)
        assert 'output "base_url"' in tf

    def test_emits_legacy_ip_alias(self):
        # Phase 4 — was test_contains_user_data; user_data lives in the module.
        tf = self.gen.generate(self.cfg)
        assert 'output "instance_ip"' in tf

    def test_spot_false_no_spot_options(self):
        cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, model_name="m", spot=False)
        tf = self.gen.generate(cfg)
        assert "spot" not in tf.lower() or "spot = false" in tf.lower()


class TestTerraformGeneratorGCP:
    def setup_method(self):
        self.gen = TerraformGenerator()
        self.cfg = ComputeConfig(
            provider=ComputeProvider.GCP,
            gpu_type=GPUType.L4,
            model_name="google/gemma-7b",
            region="us-central1",
            spot=True,
        )

    def test_generates_hcl(self):
        tf = self.gen.generate(self.cfg)
        assert isinstance(tf, str)
        assert len(tf) > 100

    def test_contains_terraform_block(self):
        tf = self.gen.generate(self.cfg)
        assert 'terraform {' in tf
        assert '"hashicorp/google"' in tf

    def test_contains_compute_instance(self):
        # Phase 4 — module-style contract: no inline google_compute_instance.
        tf = self.gen.generate(self.cfg)
        assert 'module "vllm_server"' in tf
        assert not re.search(r'^\s*resource\s+"', tf, re.MULTILINE)

    def test_contains_gpu_accelerator(self):
        tf = self.gen.generate(self.cfg)
        assert "guest_accelerator" in tf or "gpu" in tf.lower()

    def test_contains_model_name(self):
        # Phase 4 — model name flows via tfvars; module block references var.model.
        tf = self.gen.generate(self.cfg)
        assert "var.model" in tf

    def test_spot_preemptible(self):
        # Phase 4 — spot/preemptible live in tfvars, not inline HCL.
        tf = self.gen.generate(self.cfg)
        assert 'source = "./modules/vllm-server"' in tf


class TestTerraformGeneratorAzure:
    def setup_method(self):
        self.gen = TerraformGenerator()

    def test_generates_hcl(self):
        cfg = ComputeConfig(provider=ComputeProvider.AZURE, gpu_type=GPUType.T4, model_name="m")
        tf = self.gen.generate(cfg)
        assert 'terraform {' in tf
        assert '"hashicorp/azurerm"' in tf

    def test_contains_resource(self):
        # Phase 4 — provider block keeps "azurerm"; module block added.
        cfg = ComputeConfig(provider=ComputeProvider.AZURE, gpu_type=GPUType.T4, model_name="m")
        tf = self.gen.generate(cfg)
        assert "azurerm" in tf
        assert 'module "vllm_server"' in tf


class TestTerraformGeneratorAzureContainerApp:
    def setup_method(self):
        self.gen = TerraformGenerator()

    def test_generates_containerapp_hcl(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            deploy_type="containerapp",
        )
        tf = self.gen.generate(cfg)
        assert "terraform {" in tf
        assert 'module "vllm_server"' in tf
        assert "azurerm_container_app" not in tf
        assert "python:3.11-slim" not in tf

    def test_checked_in_stack_uses_repository_module_path(self):
        stack = (
            Path(__file__).resolve().parents[2]
            / "infra"
            / "terraform"
            / "stacks"
            / "azure-container-app-vllm"
            / "main.tf"
        ).read_text()
        assert 'source = "../../modules/azure-container-app-vllm"' in stack

    def test_contains_container_app_environment(self):
        # Phase 4 — inline container_app_environment gone; module-style emission.
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            deploy_type="containerapp",
        )
        tf = self.gen.generate(cfg)
        assert 'module "vllm_server"' in tf
        assert 'source = "./modules/azure-container-app-vllm"' in tf

    def test_contains_model_name(self):
        # Phase 4 — model name flows via tfvars; module block references var.model.
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.A100_80,
            model_name="llama-3-70b",
            deploy_type="containerapp",
        )
        tf = self.gen.generate(cfg)
        assert "var.model" in tf

    def test_unsupported_gpu_fails_closed_before_terraform(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.L4,
            model_name="m",
            deploy_type="containerapp",
        )
        with pytest.raises(ValueError, match="Azure Container Apps serverless GPU"):
            self.gen.generate(cfg)

    def test_contains_ingress(self):
        # Phase 4 — ingress/target_port live in the module, not inline HCL.
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            deploy_type="containerapp",
        )
        tf = self.gen.generate(cfg)
        assert 'module "vllm_server"' in tf
        assert not re.search(r'^\s*resource\s+"', tf, re.MULTILINE)

    def test_contains_output_endpoint_url(self, tmp_path):
        # The runnable root materializes outputs separately from main.tf.
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            deploy_type="containerapp",
        )
        self.gen.materialize(cfg, tmp_path, deployment_name="d-output-test")
        outputs = (tmp_path / "outputs.tf").read_text()
        assert 'output "endpoint_url"' in outputs

    def test_custom_region(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            deploy_type="containerapp",
            region="westus2",
        )
        target = self.gen.build_azure_containerapp_tfvars(cfg, deployment_name="d-region-test")
        assert 'region = "westus2"' in target

    def test_materializes_real_gpu_module_and_runtime_values(self, tmp_path):
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="Qwen/Qwen2.5-0.5B-Instruct",
            deploy_type="containerapp",
            region="eastus",
            allowed_cidr="198.51.100.10/32",
        )

        self.gen.materialize(cfg, tmp_path, deployment_name="d-azure-test")

        root = (tmp_path / "main.tf").read_text()
        tfvars = (tmp_path / "terraform.tfvars").read_text()
        module = (tmp_path / "modules" / "azure-container-app-vllm" / "main.tf").read_text()
        module_outputs = (
            tmp_path / "modules" / "azure-container-app-vllm" / "outputs.tf"
        ).read_text()
        assert 'source = "./modules/azure-container-app-vllm"' in root
        assert 'deployment_name = "d-azure-test"' in tfvars
        assert 'model_name = "Qwen/Qwen2.5-0.5B-Instruct"' in tfvars
        assert 'gpu_type = "t4"' in tfvars
        assert 'allowed_cidr = "198.51.100.10/32"' in tfvars
        assert 'source  = "Azure/azapi"' in root
        assert 'version = "~> 2.0"' in root
        assert 'resource "azapi_resource" "gludd_environment"' in module
        assert 'type      = "Microsoft.App/managedEnvironments@2025-01-01"' in module
        assert 'workloadProfileType = local.gpu_profile_type' in module
        assert '"Consumption-GPU-NC8as-T4"' in module
        assert '"Consumption-GPU-NC24-A100"' in module
        environment_block = module.split(
            'resource "azapi_resource" "gludd_environment"', 1
        )[1].split('resource "azurerm_container_app"', 1)[0]
        assert "minimumCount" not in environment_block
        assert "maximumCount" not in environment_block
        assert "minimum_count" not in environment_block
        assert "maximum_count" not in environment_block
        assert "azurerm_container_app_environment" not in module
        assert (
            "container_app_environment_id = azapi_resource.gludd_environment.id"
            in module
        )
        assert re.search(r"min_replicas\s*=\s*0", module)
        assert re.search(r"max_replicas\s*=\s*1", module)
        assert 'resource_provider_registrations = "none"' in root
        assert "skip_provider_registration" not in root
        assert "var.container_image" in module
        assert "var.model_name" in module
        assert "python:3.11-slim" not in module
        assert (
            'value       = "https://${azurerm_container_app.vllm.latest_revision_fqdn}"'
            in module_outputs
        )
        assert "latest_revision_fqdn}/v1" not in module_outputs

    def test_invalid_runtime_profile_value_fails_closed(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            deploy_type="containerapp",
            deployment_profile={"context_length": "4096"},
        )

        with pytest.raises(ValueError, match="context_length must be an integer"):
            self.gen.build_azure_containerapp_tfvars(cfg, deployment_name="d-profile-test")

    def test_invalid_gpu_memory_fraction_fails_closed(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            deploy_type="containerapp",
            deployment_profile={"gpu_memory_utilization": 1.5},
        )

        with pytest.raises(ValueError, match="gpu_memory_utilization must be between 0 and 1"):
            self.gen.build_azure_containerapp_tfvars(cfg, deployment_name="d-profile-test")

    @pytest.mark.parametrize(
        "config, message",
        [
            (
                ComputeConfig(
                    provider=ComputeProvider.AZURE,
                    gpu_type=GPUType.T4,
                    model_name="m",
                    deploy_type="containerapp",
                    gpu_count=2,
                ),
                "gpu_count=1",
            ),
            (
                ComputeConfig(
                    provider=ComputeProvider.AZURE,
                    gpu_type=GPUType.T4,
                    model_name="m",
                    deploy_type="containerapp",
                    engine=InferenceEngine.LLAMACPP,
                ),
                "vLLM engine",
            ),
        ],
    )
    def test_unsupported_container_app_shapes_fail_before_spend(self, config, message):
        with pytest.raises(ValueError, match=message):
            self.gen.generate(config)

    def test_vm_deploy_type_still_uses_vm_generator(self):
        # Phase 4 — both deploy_types now emit thin module-style HCL.
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            deploy_type="vm",
        )
        tf = self.gen.generate(cfg)
        assert 'module "vllm_server"' in tf
        assert not re.search(r'^\s*resource\s+"', tf, re.MULTILINE)

    def test_default_deploy_type_uses_vm(self):
        # Phase 4 — default deploy_type emits module-style HCL too.
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
        )
        tf = self.gen.generate(cfg)
        assert 'module "vllm_server"' in tf


class TestTerraformGeneratorRunPod:
    def setup_method(self):
        self.gen = TerraformGenerator()

    def test_generates_hcl(self):
        cfg = ComputeConfig(provider=ComputeProvider.RUNPOD, gpu_type=GPUType.A100_80, model_name="m")
        tf = self.gen.generate(cfg)
        assert 'terraform {' in tf
        assert "runpod" in tf.lower()

    def test_contains_pod_resource(self):
        # Phase 4 — inline runpod_pod gone; module block present.
        cfg = ComputeConfig(provider=ComputeProvider.RUNPOD, gpu_type=GPUType.A100_80, model_name="test-model")
        tf = self.gen.generate(cfg)
        assert 'module "vllm_server"' in tf
        assert "runpod_pod" not in tf

    def test_contains_container_image(self):
        # Phase 4 — container_image flows via tfvars.
        cfg = ComputeConfig(
            provider=ComputeProvider.RUNPOD,
            gpu_type=GPUType.A100_80,
            model_name="m",
            container_image="vllm/vllm-openai:latest",
        )
        gen = TerraformGenerator()
        tfvars = gen.build_tfvars(cfg)
        assert "vllm/vllm-openai:latest" in tfvars


class TestTerraformGeneratorGeneric:
    def test_vast_ai_generates(self):
        gen = TerraformGenerator()
        cfg = ComputeConfig(provider=ComputeProvider.VAST_AI, gpu_type=GPUType.RTX_4090, model_name="m")
        tf = gen.generate(cfg)
        assert isinstance(tf, str)
        assert len(tf) > 50

    def test_lambda_labs_generates(self):
        gen = TerraformGenerator()
        cfg = ComputeConfig(provider=ComputeProvider.LAMBDA_LABS, gpu_type=GPUType.A100_80, model_name="m")
        tf = gen.generate(cfg)
        assert isinstance(tf, str)

    def test_modal_generates(self):
        gen = TerraformGenerator()
        cfg = ComputeConfig(provider=ComputeProvider.MODAL, gpu_type=GPUType.T4, model_name="m")
        tf = gen.generate(cfg)
        assert isinstance(tf, str)

    def test_coreweave_generates(self):
        gen = TerraformGenerator()
        cfg = ComputeConfig(provider=ComputeProvider.COREWEAVE, gpu_type=GPUType.A100_80, model_name="m")
        tf = gen.generate(cfg)
        assert isinstance(tf, str)

    def test_digital_ocean_generates(self):
        gen = TerraformGenerator()
        cfg = ComputeConfig(provider=ComputeProvider.DIGITAL_OCEAN, gpu_type=GPUType.H100, model_name="m")
        tf = gen.generate(cfg)
        assert isinstance(tf, str)

    def test_oracle_generates(self):
        gen = TerraformGenerator()
        cfg = ComputeConfig(provider=ComputeProvider.ORACLE, gpu_type=GPUType.A10, model_name="m")
        tf = gen.generate(cfg)
        assert isinstance(tf, str)


class TestTerraformGeneratorCommon:
    def test_model_name_in_output(self):
        # Phase 4 — values flow through tfvars; check build_tfvars, not the HCL stack.
        gen = TerraformGenerator()
        cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, model_name="my-special-model")
        tfvars = gen.build_tfvars(cfg)
        assert "my-special-model" in tfvars

    def test_gpu_type_in_output(self):
        # Phase 4 — GPU type lives in tfvars.
        gen = TerraformGenerator()
        cfg = ComputeConfig(provider=ComputeProvider.GCP, gpu_type=GPUType.H100, model_name="m")
        tfvars = gen.build_tfvars(cfg)
        assert "h100" in tfvars.lower()

    def test_container_image_override(self):
        # Phase 4 — container_image flows via tfvars.
        gen = TerraformGenerator()
        cfg = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
            model_name="m",
            container_image="my-custom/image:v2",
        )
        tfvars = gen.build_tfvars(cfg)
        assert "my-custom/image:v2" in tfvars

    def test_spot_true_includes_spot_config(self):
        # Phase 4 — module-style HCL has no inline spot config; just assert thin stack.
        gen = TerraformGenerator()
        cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, model_name="m", spot=True)
        tf = gen.generate(cfg)
        assert 'module "vllm_server"' in tf

    def test_vllm_engine_default(self):
        gen = TerraformGenerator()
        cfg = ComputeConfig(provider=ComputeProvider.RUNPOD, gpu_type=GPUType.A100_80, model_name="m")
        tf = gen.generate(cfg)
        assert "vllm" in tf.lower()

    def test_llamacpp_engine(self):
        # Phase 4 — engine value flows through tfvars (build_tfvars).
        gen = TerraformGenerator()
        cfg = ComputeConfig(
            provider=ComputeProvider.RUNPOD,
            gpu_type=GPUType.A100_80,
            model_name="m",
            engine=InferenceEngine.LLAMACPP,
        )
        tfvars = gen.build_tfvars(cfg)
        assert "llamacpp" in tfvars.lower()

    def test_cost_limit_label(self):
        # Phase 4 — max_cost_usd flows through tfvars.
        gen = TerraformGenerator()
        cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, model_name="m", max_cost_usd=25.0)
        tfvars = gen.build_tfvars(cfg)
        assert "25" in tfvars

    def test_timeout_label(self):
        # Phase 4 — timeout_minutes flows through tfvars.
        gen = TerraformGenerator()
        cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, model_name="m", timeout_minutes=30.0)
        tfvars = gen.build_tfvars(cfg)
        assert "30" in tfvars
