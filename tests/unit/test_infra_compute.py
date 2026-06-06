"""Unit tests for infra compute models, provider registry, and terraform generator."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from general_ludd.infra.compute import ComputeConfig, ComputeInstance, ComputeProvider, GPUType, InferenceEngine
from general_ludd.infra.providers import ProviderInfo, ProviderRegistry
from general_ludd.infra.terraform import TerraformGenerator


class TestComputeProvider:
    def test_aws_value(self):
        assert ComputeProvider.AWS == "aws"

    def test_all_providers_present(self):
        expected = {
            "aws", "azure", "gcp", "runpod", "vast_ai",
            "lambda_labs", "modal", "coreweave", "digital_ocean", "oracle",
        }
        actual = {p.value for p in ComputeProvider}
        assert actual == expected


class TestGPUType:
    def test_t4_value(self):
        assert GPUType.T4 == "t4"

    def test_all_gpus_present(self):
        expected = {
            "t4", "a10g", "l4", "a10", "rtx_4090", "rtx_6000_ada",
            "a40", "l40s", "a100_40", "a100_80", "h100", "h200",
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
        assert len(providers) == 10
        names = {p.provider for p in providers}
        assert ComputeProvider.AWS in names
        assert ComputeProvider.ORACLE in names

    def test_get_cheapest_for_gpu_a100_80(self):
        reg = ProviderRegistry()
        cheapest = reg.get_cheapest_for_gpu(GPUType.A100_80)
        assert cheapest.provider == ComputeProvider.VAST_AI
        assert cheapest.pricing["a100_80"] == 1.20

    def test_get_cheapest_for_gpu_t4(self):
        reg = ProviderRegistry()
        cheapest = reg.get_cheapest_for_gpu(GPUType.T4)
        assert cheapest.provider == ComputeProvider.AWS
        assert cheapest.pricing["t4"] == 0.20

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
        assert len(by_price) == 10

    def test_get_raises_for_unknown(self):
        reg = ProviderRegistry()
        with pytest.raises(KeyError):
            reg.get("nonexistent")  # type: ignore[arg-type]


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
        tf = self.gen.generate(self.cfg)
        assert 'resource "aws_instance"' in tf

    def test_contains_gpu_type(self):
        tf = self.gen.generate(self.cfg)
        assert "a10g" in tf.lower() or "g5" in tf.lower()

    def test_contains_model_name(self):
        tf = self.gen.generate(self.cfg)
        assert "meta-llama/Meta-Llama-3-8B-Instruct" in tf

    def test_contains_region(self):
        tf = self.gen.generate(self.cfg)
        assert "us-east-1" in tf

    def test_spot_instance_config(self):
        tf = self.gen.generate(self.cfg)
        assert "spot" in tf.lower()

    def test_contains_port_8000(self):
        tf = self.gen.generate(self.cfg)
        assert "8000" in tf

    def test_contains_user_data(self):
        tf = self.gen.generate(self.cfg)
        assert "user_data" in tf or "user_data" in tf.replace(" ", "")

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
        tf = self.gen.generate(self.cfg)
        assert 'google_compute_instance' in tf

    def test_contains_gpu_accelerator(self):
        tf = self.gen.generate(self.cfg)
        assert "guest_accelerator" in tf or "gpu" in tf.lower()

    def test_contains_model_name(self):
        tf = self.gen.generate(self.cfg)
        assert "google/gemma-7b" in tf

    def test_spot_preemptible(self):
        tf = self.gen.generate(self.cfg)
        assert "preemptible" in tf.lower() or "spot" in tf.lower()


class TestTerraformGeneratorAzure:
    def setup_method(self):
        self.gen = TerraformGenerator()

    def test_generates_hcl(self):
        cfg = ComputeConfig(provider=ComputeProvider.AZURE, gpu_type=GPUType.T4, model_name="m")
        tf = self.gen.generate(cfg)
        assert 'terraform {' in tf
        assert '"hashicorp/azurerm"' in tf

    def test_contains_resource(self):
        cfg = ComputeConfig(provider=ComputeProvider.AZURE, gpu_type=GPUType.T4, model_name="m")
        tf = self.gen.generate(cfg)
        assert "azurerm" in tf


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
        assert "azurerm_container_app" in tf

    def test_contains_container_app_environment(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            deploy_type="containerapp",
        )
        tf = self.gen.generate(cfg)
        assert "azurerm_container_app_environment" in tf

    def test_contains_model_name(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.A100_80,
            model_name="llama-3-70b",
            deploy_type="containerapp",
        )
        tf = self.gen.generate(cfg)
        assert "llama-3-70b" in tf

    def test_contains_resource_group(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.L4,
            model_name="m",
            deploy_type="containerapp",
        )
        tf = self.gen.generate(cfg)
        assert "azurerm_resource_group" in tf

    def test_contains_ingress(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            deploy_type="containerapp",
        )
        tf = self.gen.generate(cfg)
        assert "ingress" in tf
        assert "target_port = 8000" in tf

    def test_contains_output_endpoint_url(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            deploy_type="containerapp",
        )
        tf = self.gen.generate(cfg)
        assert "output" in tf
        assert "latest_revision_fqdn" in tf

    def test_custom_region(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            deploy_type="containerapp",
            region="westus2",
        )
        tf = self.gen.generate(cfg)
        assert "westus2" in tf

    def test_vm_deploy_type_still_uses_vm_generator(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
            deploy_type="vm",
        )
        tf = self.gen.generate(cfg)
        assert "azurerm_virtual_machine" in tf
        assert "azurerm_container_app" not in tf

    def test_default_deploy_type_uses_vm(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            model_name="m",
        )
        tf = self.gen.generate(cfg)
        assert "azurerm_virtual_machine" in tf


class TestTerraformGeneratorRunPod:
    def setup_method(self):
        self.gen = TerraformGenerator()

    def test_generates_hcl(self):
        cfg = ComputeConfig(provider=ComputeProvider.RUNPOD, gpu_type=GPUType.A100_80, model_name="m")
        tf = self.gen.generate(cfg)
        assert 'terraform {' in tf
        assert "runpod" in tf.lower()

    def test_contains_pod_resource(self):
        cfg = ComputeConfig(provider=ComputeProvider.RUNPOD, gpu_type=GPUType.A100_80, model_name="test-model")
        tf = self.gen.generate(cfg)
        assert "runpod_pod" in tf
        assert "test-model" in tf

    def test_contains_container_image(self):
        cfg = ComputeConfig(
            provider=ComputeProvider.RUNPOD,
            gpu_type=GPUType.A100_80,
            model_name="m",
            container_image="vllm/vllm-openai:latest",
        )
        tf = self.gen.generate(cfg)
        assert "vllm/vllm-openai:latest" in tf


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
        gen = TerraformGenerator()
        cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, model_name="my-special-model")
        tf = gen.generate(cfg)
        assert "my-special-model" in tf

    def test_gpu_type_in_output(self):
        gen = TerraformGenerator()
        cfg = ComputeConfig(provider=ComputeProvider.GCP, gpu_type=GPUType.H100, model_name="m")
        tf = gen.generate(cfg)
        assert "h100" in tf.lower()

    def test_container_image_override(self):
        gen = TerraformGenerator()
        cfg = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
            model_name="m",
            container_image="my-custom/image:v2",
        )
        tf = gen.generate(cfg)
        assert "my-custom/image:v2" in tf

    def test_spot_true_includes_spot_config(self):
        gen = TerraformGenerator()
        cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, model_name="m", spot=True)
        tf = gen.generate(cfg)
        assert "spot" in tf.lower()

    def test_vllm_engine_default(self):
        gen = TerraformGenerator()
        cfg = ComputeConfig(provider=ComputeProvider.RUNPOD, gpu_type=GPUType.A100_80, model_name="m")
        tf = gen.generate(cfg)
        assert "vllm" in tf.lower()

    def test_llamacpp_engine(self):
        gen = TerraformGenerator()
        cfg = ComputeConfig(
            provider=ComputeProvider.RUNPOD,
            gpu_type=GPUType.A100_80,
            model_name="m",
            engine=InferenceEngine.LLAMACPP,
        )
        tf = gen.generate(cfg)
        assert "llama" in tf.lower() or "llamacpp" in tf.lower()

    def test_cost_limit_label(self):
        gen = TerraformGenerator()
        cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, model_name="m", max_cost_usd=25.0)
        tf = gen.generate(cfg)
        assert "25" in tf

    def test_timeout_label(self):
        gen = TerraformGenerator()
        cfg = ComputeConfig(provider=ComputeProvider.AWS, gpu_type=GPUType.T4, model_name="m", timeout_minutes=30.0)
        tf = gen.generate(cfg)
        assert "30" in tf
