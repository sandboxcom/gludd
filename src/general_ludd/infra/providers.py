"""Provider registry with GPU types and pricing."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, field_validator

from general_ludd.infra.compute import ComputeProvider, GPUType


class ProviderInfo(BaseModel):
    provider: ComputeProvider
    display_name: str
    terraform_provider: str
    supports_spot: bool
    sub_hour_billing: bool
    min_gpu: GPUType
    max_gpu: GPUType
    pricing: dict[str, float] = {}

    @field_validator("display_name", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("display_name must not be empty")
        return v


_BUILTIN_PROVIDERS: list[dict[str, Any]] = [
    {
        "provider": ComputeProvider.AWS,
        "display_name": "Amazon Web Services",
        "terraform_provider": "hashicorp/aws",
        "supports_spot": True,
        "sub_hour_billing": False,
        "min_gpu": GPUType.T4,
        "max_gpu": GPUType.A100_80,
        "pricing": {"t4": 0.20, "a10g": 0.40, "a100_80": 10.00},
    },
    {
        "provider": ComputeProvider.GCP,
        "display_name": "Google Cloud Platform",
        "terraform_provider": "hashicorp/google",
        "supports_spot": True,
        "sub_hour_billing": True,
        "min_gpu": GPUType.L4,
        "max_gpu": GPUType.A100_80,
        "pricing": {"l4": 0.22, "a100_80": 3.67},
    },
    {
        "provider": ComputeProvider.AZURE,
        "display_name": "Microsoft Azure",
        "terraform_provider": "hashicorp/azurerm",
        "supports_spot": True,
        "sub_hour_billing": False,
        "min_gpu": GPUType.T4,
        "max_gpu": GPUType.A100_80,
        "pricing": {"t4": 0.53, "a100_80": 3.67},
    },
    {
        "provider": ComputeProvider.RUNPOD,
        "display_name": "RunPod",
        "terraform_provider": "runpod/runpod",
        "supports_spot": True,
        "sub_hour_billing": True,
        "min_gpu": GPUType.L4,
        "max_gpu": GPUType.A100_80,
        "pricing": {"l4": 0.39, "a100_80": 1.39},
    },
    {
        "provider": ComputeProvider.VAST_AI,
        "display_name": "Vast.ai",
        "terraform_provider": "vast-ai/vast-ai",
        "supports_spot": True,
        "sub_hour_billing": True,
        "min_gpu": GPUType.RTX_4090,
        "max_gpu": GPUType.A100_80,
        "pricing": {"rtx_4090": 0.40, "a100_80": 1.20},
    },
    {
        "provider": ComputeProvider.LAMBDA_LABS,
        "display_name": "Lambda Labs",
        "terraform_provider": "lambda-labs/lambda",
        "supports_spot": False,
        "sub_hour_billing": True,
        "min_gpu": GPUType.A100_80,
        "max_gpu": GPUType.A100_80,
        "pricing": {"a100_80": 2.79},
    },
    {
        "provider": ComputeProvider.MODAL,
        "display_name": "Modal",
        "terraform_provider": "modal/modal",
        "supports_spot": False,
        "sub_hour_billing": True,
        "min_gpu": GPUType.T4,
        "max_gpu": GPUType.A100_80,
        "pricing": {"t4": 0.59, "a100_80": 2.50},
    },
    {
        "provider": ComputeProvider.COREWEAVE,
        "display_name": "CoreWeave",
        "terraform_provider": "coreweave/coreweave",
        "supports_spot": True,
        "sub_hour_billing": True,
        "min_gpu": GPUType.L40S,
        "max_gpu": GPUType.A100_80,
        "pricing": {"l40s": 2.25, "a100_80": 2.70},
    },
    {
        "provider": ComputeProvider.DIGITAL_OCEAN,
        "display_name": "DigitalOcean",
        "terraform_provider": "digitalocean/digitalocean",
        "supports_spot": False,
        "sub_hour_billing": True,
        "min_gpu": GPUType.RTX_6000_ADA,
        "max_gpu": GPUType.H100,
        "pricing": {"rtx_6000_ada": 1.49, "h100": 7.99},
    },
    {
        "provider": ComputeProvider.ORACLE,
        "display_name": "Oracle Cloud",
        "terraform_provider": "hashicorp/oci",
        "supports_spot": True,
        "sub_hour_billing": False,
        "min_gpu": GPUType.A10,
        "max_gpu": GPUType.A100_80,
        "pricing": {"a10": 0.75, "a100_80": 25.00},
    },
]


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[ComputeProvider, ProviderInfo] = {}
        for entry in _BUILTIN_PROVIDERS:
            info = ProviderInfo(**entry)
            self._providers[info.provider] = info

    def get(self, provider: ComputeProvider) -> ProviderInfo:
        return self._providers[provider]

    def list_providers(self) -> list[ProviderInfo]:
        return list(self._providers.values())

    def get_cheapest_for_gpu(self, gpu_type: GPUType) -> ProviderInfo:
        candidates: list[tuple[float, ProviderInfo]] = []
        gpu_key = gpu_type.value
        for info in self._providers.values():
            if gpu_key in info.pricing:
                candidates.append((info.pricing[gpu_key], info))
        if not candidates:
            raise KeyError(f"No provider supports GPU type: {gpu_type}")
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    def list_by_price(self) -> list[tuple[ComputeProvider, float]]:
        result: list[tuple[ComputeProvider, float]] = []
        for info in self._providers.values():
            prices = list(info.pricing.values())
            min_price = min(prices) if prices else float("inf")
            result.append((info.provider, min_price))
        result.sort(key=lambda x: x[1])
        return result
