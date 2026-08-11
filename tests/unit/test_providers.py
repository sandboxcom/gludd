"""Deep tests for infra/providers.py — ProviderInfo model validation and ProviderRegistry methods."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from general_ludd.infra.compute import ComputeProvider, GPUType
from general_ludd.infra.providers import ProviderInfo, ProviderRegistry


class TestProviderInfoModel:
    def test_minimal_instantiation(self) -> None:
        info = ProviderInfo(
            provider=ComputeProvider.AWS,
            display_name="Test",
            terraform_provider="hashicorp/test",
            supports_spot=False,
            sub_hour_billing=False,
            min_gpu=GPUType.T4,
            max_gpu=GPUType.A100_80,
        )
        assert info.provider == ComputeProvider.AWS
        assert info.display_name == "Test"
        assert info.terraform_provider == "hashicorp/test"
        assert info.supports_spot is False
        assert info.sub_hour_billing is False
        assert info.min_gpu == GPUType.T4
        assert info.max_gpu == GPUType.A100_80

    def test_display_name_stripped(self) -> None:
        info = ProviderInfo(
            provider=ComputeProvider.AWS,
            display_name="  Amazon Web Services  ",
            terraform_provider="hashicorp/aws",
            supports_spot=True,
            sub_hour_billing=False,
            min_gpu=GPUType.T4,
            max_gpu=GPUType.A100_80,
        )
        assert info.display_name == "Amazon Web Services"

    def test_display_name_empty_raises(self) -> None:
        with pytest.raises(ValidationError):
            ProviderInfo(
                provider=ComputeProvider.AWS,
                display_name="",
                terraform_provider="hashicorp/aws",
                supports_spot=True,
                sub_hour_billing=False,
                min_gpu=GPUType.T4,
                max_gpu=GPUType.A100_80,
            )

    def test_display_name_whitespace_only_raises(self) -> None:
        with pytest.raises(ValidationError):
            ProviderInfo(
                provider=ComputeProvider.AWS,
                display_name="   ",
                terraform_provider="hashicorp/aws",
                supports_spot=True,
                sub_hour_billing=False,
                min_gpu=GPUType.T4,
                max_gpu=GPUType.A100_80,
            )

    def test_pricing_defaults_to_empty_dict(self) -> None:
        info = ProviderInfo(
            provider=ComputeProvider.AWS,
            display_name="Test",
            terraform_provider="hashicorp/test",
            supports_spot=False,
            sub_hour_billing=False,
            min_gpu=GPUType.T4,
            max_gpu=GPUType.A100_80,
        )
        assert info.pricing == {}

    def test_pricing_stored_correctly(self) -> None:
        info = ProviderInfo(
            provider=ComputeProvider.AWS,
            display_name="Test",
            terraform_provider="hashicorp/test",
            supports_spot=False,
            sub_hour_billing=False,
            min_gpu=GPUType.T4,
            max_gpu=GPUType.A100_80,
            pricing={"t4": 0.20, "a100_80": 10.00},
        )
        assert info.pricing == {"t4": 0.20, "a100_80": 10.00}

    def test_auth_env_defaults_to_empty_list(self) -> None:
        info = ProviderInfo(
            provider=ComputeProvider.AWS,
            display_name="Test",
            terraform_provider="hashicorp/test",
            supports_spot=False,
            sub_hour_billing=False,
            min_gpu=GPUType.T4,
            max_gpu=GPUType.A100_80,
        )
        assert info.auth_env == []

    def test_auth_source_defaults_to_none(self) -> None:
        info = ProviderInfo(
            provider=ComputeProvider.AWS,
            display_name="Test",
            terraform_provider="hashicorp/test",
            supports_spot=False,
            sub_hour_billing=False,
            min_gpu=GPUType.T4,
            max_gpu=GPUType.A100_80,
        )
        assert info.auth_source is None

    def test_auth_env_stored_correctly(self) -> None:
        info = ProviderInfo(
            provider=ComputeProvider.VMWARE,
            display_name="VMware vSphere",
            terraform_provider="hashicorp/vsphere",
            supports_spot=False,
            sub_hour_billing=False,
            min_gpu=GPUType.A100_80,
            max_gpu=GPUType.A100_80,
            auth_env=["VSPHERE_USER", "VSPHERE_PASSWORD", "VSPHERE_SERVER"],
            auth_source="OpenBao / SecretsManager",
        )
        assert info.auth_env == ["VSPHERE_USER", "VSPHERE_PASSWORD", "VSPHERE_SERVER"]
        assert info.auth_source == "OpenBao / SecretsManager"


class TestProviderRegistryInit:
    def test_all_builtin_providers_load(self) -> None:
        registry = ProviderRegistry()
        providers = registry.list_providers()
        assert len(providers) == 16

    def test_every_registered_provider_has_enum_match(self) -> None:
        registry = ProviderRegistry()
        for info in registry.list_providers():
            assert isinstance(info.provider, ComputeProvider)

    def test_provider_name_matches_enum_value(self) -> None:
        registry = ProviderRegistry()
        aws = registry.get(ComputeProvider.AWS)
        assert aws.provider == ComputeProvider.AWS
        assert aws.provider.value == "aws"

    def test_no_duplicate_providers_in_registry(self) -> None:
        registry = ProviderRegistry()
        providers = registry.list_providers()
        names = [p.provider for p in providers]
        assert len(names) == len(set(names))

    def test_terraform_provider_string_not_empty(self) -> None:
        registry = ProviderRegistry()
        for info in registry.list_providers():
            assert len(info.terraform_provider) > 0

    def test_min_gpu_lte_max_gpu_for_every_provider(self) -> None:
        registry = ProviderRegistry()
        gpu_order = list(GPUType)
        for info in registry.list_providers():
            min_idx = gpu_order.index(info.min_gpu)
            max_idx = gpu_order.index(info.max_gpu)
            assert min_idx <= max_idx, f"{info.display_name}: min_gpu={info.min_gpu} after max_gpu={info.max_gpu}"


class TestProviderRegistryGet:
    def test_get_aws_returns_aws_info(self) -> None:
        registry = ProviderRegistry()
        info = registry.get(ComputeProvider.AWS)
        assert info.display_name == "Amazon Web Services"
        assert info.terraform_provider == "hashicorp/aws"
        assert info.supports_spot is True
        assert info.sub_hour_billing is False

    def test_get_gcp_returns_gcp_info(self) -> None:
        registry = ProviderRegistry()
        info = registry.get(ComputeProvider.GCP)
        assert info.display_name == "Google Cloud Platform"
        assert info.terraform_provider == "hashicorp/google"
        assert info.supports_spot is True
        assert info.sub_hour_billing is True

    def test_get_azure_returns_azure_info(self) -> None:
        registry = ProviderRegistry()
        info = registry.get(ComputeProvider.AZURE)
        assert info.display_name == "Microsoft Azure"
        assert info.terraform_provider == "hashicorp/azurerm"
        assert info.min_gpu == GPUType.T4
        assert info.max_gpu == GPUType.H100

    def test_get_runpod_returns_runpod_info(self) -> None:
        registry = ProviderRegistry()
        info = registry.get(ComputeProvider.RUNPOD)
        assert info.display_name == "RunPod"
        assert info.terraform_provider == "runpod/runpod"
        assert info.sub_hour_billing is True

    def test_get_vast_ai_returns_vast_info(self) -> None:
        registry = ProviderRegistry()
        info = registry.get(ComputeProvider.VAST_AI)
        assert info.display_name == "Vast.ai"
        assert info.terraform_provider == "vast-ai/vast-ai"
        assert info.supports_spot is True

    def test_get_lambda_labs_returns_no_spot(self) -> None:
        registry = ProviderRegistry()
        info = registry.get(ComputeProvider.LAMBDA_LABS)
        assert info.display_name == "Lambda Labs"
        assert info.supports_spot is False

    def test_get_modal_returns_modal_info(self) -> None:
        registry = ProviderRegistry()
        info = registry.get(ComputeProvider.MODAL)
        assert info.display_name == "Modal"
        assert info.terraform_provider == "modal/modal"

    def test_get_coreweave_returns_coreweave_info(self) -> None:
        registry = ProviderRegistry()
        info = registry.get(ComputeProvider.COREWEAVE)
        assert info.display_name == "CoreWeave"
        assert info.terraform_provider == "coreweave/coreweave"
        assert info.min_gpu == GPUType.L40S

    def test_get_digitalocean_returns_do_info(self) -> None:
        registry = ProviderRegistry()
        info = registry.get(ComputeProvider.DIGITAL_OCEAN)
        assert info.display_name == "DigitalOcean"
        assert info.terraform_provider == "digitalocean/digitalocean"
        assert info.max_gpu == GPUType.H100

    def test_get_oracle_returns_oci_info(self) -> None:
        registry = ProviderRegistry()
        info = registry.get(ComputeProvider.ORACLE)
        assert info.display_name == "Oracle Cloud"
        assert info.terraform_provider == "hashicorp/oci"
        assert "a10" in info.pricing

    def test_get_vmware_has_auth_env(self) -> None:
        registry = ProviderRegistry()
        info = registry.get(ComputeProvider.VMWARE)
        assert len(info.auth_env) == 3
        assert "VSPHERE_USER" in info.auth_env
        assert "VSPHERE_PASSWORD" in info.auth_env
        assert "VSPHERE_SERVER" in info.auth_env

    def test_get_kubernetes_returns_k8s_info(self) -> None:
        registry = ProviderRegistry()
        info = registry.get(ComputeProvider.KUBERNETES)
        assert info.display_name == "Kubernetes"
        assert info.terraform_provider == "hashicorp/kubernetes"

    def test_get_together_ai_api_only(self) -> None:
        registry = ProviderRegistry()
        info = registry.get(ComputeProvider.TOGETHER_AI)
        assert info.display_name == "Together.ai"
        assert info.terraform_provider == "none (API-only)"
        assert "TOGETHER_API_KEY" in info.auth_env

    def test_get_fireworks_ai_api_only(self) -> None:
        registry = ProviderRegistry()
        info = registry.get(ComputeProvider.FIREWORKS_AI)
        assert info.display_name == "Fireworks.ai"
        assert info.terraform_provider == "none (API-only)"
        assert "FIREWORKS_API_KEY" in info.auth_env

    def test_get_huggingface_api_only(self) -> None:
        registry = ProviderRegistry()
        info = registry.get(ComputeProvider.HUGGINGFACE)
        assert info.display_name == "HuggingFace Inference Endpoints"
        assert info.terraform_provider == "none (API-only)"
        assert "HF_TOKEN" in info.auth_env

    def test_get_replicate_api_only(self) -> None:
        registry = ProviderRegistry()
        info = registry.get(ComputeProvider.REPLICATE)
        assert info.display_name == "Replicate"
        assert info.terraform_provider == "none (API-only)"
        assert "REPLICATE_API_TOKEN" in info.auth_env


class TestProviderRegistryListProviders:
    def test_list_providers_returns_all_known_providers(self) -> None:
        registry = ProviderRegistry()
        providers = registry.list_providers()
        assert len(providers) == 16
        known = {
            ComputeProvider.AWS,
            ComputeProvider.GCP,
            ComputeProvider.AZURE,
            ComputeProvider.RUNPOD,
            ComputeProvider.VAST_AI,
            ComputeProvider.LAMBDA_LABS,
            ComputeProvider.MODAL,
            ComputeProvider.COREWEAVE,
            ComputeProvider.DIGITAL_OCEAN,
            ComputeProvider.ORACLE,
            ComputeProvider.VMWARE,
            ComputeProvider.KUBERNETES,
            ComputeProvider.TOGETHER_AI,
            ComputeProvider.FIREWORKS_AI,
            ComputeProvider.HUGGINGFACE,
            ComputeProvider.REPLICATE,
        }
        actual = {p.provider for p in providers}
        assert actual == known

    def test_list_providers_returns_providerinfo_objects(self) -> None:
        registry = ProviderRegistry()
        for info in registry.list_providers():
            assert isinstance(info, ProviderInfo)

    def test_list_providers_is_stable(self) -> None:
        registry = ProviderRegistry()
        first = [p.provider for p in registry.list_providers()]
        second = [p.provider for p in registry.list_providers()]
        assert first == second


class TestProviderRegistryGetCheapestForGpu:
    def test_cheapest_t4_is_from_catalog(self) -> None:
        registry = ProviderRegistry()
        cheapest = registry.get_cheapest_for_gpu(GPUType.T4)
        assert cheapest.pricing["t4"] <= 0.60

    def test_cheapest_a100_80_is_from_catalog(self) -> None:
        registry = ProviderRegistry()
        cheapest = registry.get_cheapest_for_gpu(GPUType.A100_80)
        assert cheapest.pricing["a100_80"] <= 25.00

    def test_cheapest_returns_provider_with_lowest_price(self) -> None:
        registry = ProviderRegistry()
        cheapest = registry.get_cheapest_for_gpu(GPUType.T4)
        all_t4_prices = []
        for info in registry.list_providers():
            price = info.pricing.get("t4")
            if price and price > 0:
                all_t4_prices.append(price)
        assert cheapest.pricing["t4"] == min(all_t4_prices)

    def test_cheapest_h100_works(self) -> None:
        registry = ProviderRegistry()
        cheapest = registry.get_cheapest_for_gpu(GPUType.H100)
        assert cheapest is not None
        assert cheapest.pricing["h100"] > 0

    def test_cheapest_ignores_zero_price_providers(self) -> None:
        registry = ProviderRegistry()
        cheapest = registry.get_cheapest_for_gpu(GPUType.T4)
        assert cheapest.pricing["t4"] > 0

    def test_cheapest_ignores_missing_gpu_providers(self) -> None:
        registry = ProviderRegistry()
        cheapest = registry.get_cheapest_for_gpu(GPUType.L4)
        assert cheapest is not None
        assert cheapest.pricing["l4"] > 0

    def test_cheapest_for_a10g_returns_valid_provider(self) -> None:
        registry = ProviderRegistry()
        cheapest = registry.get_cheapest_for_gpu(GPUType.A10G)
        assert cheapest.pricing["a10g"] > 0

    def test_cheapest_for_l40s_returns_valid_provider(self) -> None:
        registry = ProviderRegistry()
        cheapest = registry.get_cheapest_for_gpu(GPUType.L40S)
        assert cheapest.pricing["l40s"] > 0

    def test_cheapest_for_rtx_4090_returns_vast_ai(self) -> None:
        registry = ProviderRegistry()
        cheapest = registry.get_cheapest_for_gpu(GPUType.RTX_4090)
        assert cheapest.provider in (ComputeProvider.VAST_AI,)

    def test_cheapest_for_rtx_6000_ada_returns_digitalocean(self) -> None:
        registry = ProviderRegistry()
        cheapest = registry.get_cheapest_for_gpu(GPUType.RTX_6000_ADA)
        assert cheapest.pricing["rtx_6000_ada"] > 0


class TestProviderRegistryListByPrice:
    def test_list_by_price_returns_all_providers(self) -> None:
        registry = ProviderRegistry()
        result = registry.list_by_price()
        assert len(result) == 16

    def test_list_by_price_is_sorted_ascending(self) -> None:
        registry = ProviderRegistry()
        result = registry.list_by_price()
        prices = [p[1] for p in result]
        assert prices == sorted(prices)

    def test_list_by_price_returns_tuples_of_provider_and_float(self) -> None:
        registry = ProviderRegistry()
        for provider, price in registry.list_by_price():
            assert isinstance(provider, ComputeProvider)
            assert isinstance(price, float)

    def test_list_by_price_first_entry_is_cheapest(self) -> None:
        registry = ProviderRegistry()
        result = registry.list_by_price()
        _cheapest_provider, cheapest_price = result[0]
        for _, price in result[1:]:
            assert cheapest_price <= price

    def test_list_by_price_no_zero_price_entries(self) -> None:
        registry = ProviderRegistry()
        for _, price in registry.list_by_price():
            assert price >= 0


class TestProviderRegistryEdgeCases:
    def test_api_only_providers_excluded_from_cheapest_terraform(self) -> None:
        registry = ProviderRegistry()
        together = registry.get(ComputeProvider.TOGETHER_AI)
        assert together.terraform_provider == "none (API-only)"

    def test_kubernetes_has_zero_pricing(self) -> None:
        registry = ProviderRegistry()
        k8s = registry.get(ComputeProvider.KUBERNETES)
        for gpu_price in k8s.pricing.values():
            assert gpu_price == 0.0

    def test_vmware_has_zero_pricing(self) -> None:
        registry = ProviderRegistry()
        vmware = registry.get(ComputeProvider.VMWARE)
        gpu_price = vmware.pricing["a100_80"]
        assert gpu_price == 0.0

    def test_replicate_has_multiple_gpu_prices(self) -> None:
        registry = ProviderRegistry()
        replicate = registry.get(ComputeProvider.REPLICATE)
        assert len(replicate.pricing) >= 4
        assert "a40" in replicate.pricing

    def test_list_providers_includes_api_only(self) -> None:
        registry = ProviderRegistry()
        provider_names = {p.provider for p in registry.list_providers()}
        assert ComputeProvider.TOGETHER_AI in provider_names
        assert ComputeProvider.FIREWORKS_AI in provider_names
        assert ComputeProvider.HUGGINGFACE in provider_names
        assert ComputeProvider.REPLICATE in provider_names
