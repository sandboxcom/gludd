"""Peak/off-peak provider pricing loaded from config/pricing/ YAML files.

Provides:
- ``load_provider_rates()`` — rate card with peak/off-peak windows for each provider
- ``load_compute_instances()`` — GPU instance pricing with spot/preemptible discounts
- ``ProviderRate`` — dataclass for a single model's rate card
- ``ComputeInstance`` — dataclass for a single GPU instance type
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ProviderRate:
    """Token pricing for one model from one provider."""

    provider: str
    model_id: str
    input_usd_per_1k: float
    output_usd_per_1k_peak: float
    output_usd_per_1k_offpeak: float
    context_window: int | None = None
    flat: bool = False


@dataclass
class ProviderPricing:
    """Complete provider rate card with billing metadata and off-peak windows."""

    provider: str
    display_name: str
    billing: str
    source: str
    flat: bool
    rates: list[ProviderRate] = field(default_factory=list)
    off_peak_windows: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ComputeInstance:
    """GPU instance pricing from compute.yml."""

    provider: str
    key: str
    gpu: str
    gpu_count: int
    on_demand_usd_hr: float
    vcpus: int | None = None
    memory_gb: int | None = None
    spot_discount: float | None = None


# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------


def _config_dir() -> Path:
    return Path(__file__).parent.parent.parent / "config" / "pricing"


def load_provider_rates() -> list[ProviderPricing]:
    """Load provider rate cards from ``config/pricing/providers.yml``."""
    config_path = _config_dir() / "providers.yml"
    if not config_path.is_file():
        return []

    with open(config_path) as fh:
        data = yaml.safe_load(fh)

    results: list[ProviderPricing] = []
    for provider_slug, provider_data in data.get("pricing", {}).items():
        flat = bool(provider_data.get("flat", False))
        pp = ProviderPricing(
            provider=provider_slug,
            display_name=str(provider_data.get("display_name", provider_slug)),
            billing=str(provider_data.get("billing", "unknown")),
            source=str(provider_data.get("source", "")),
            flat=flat,
            off_peak_windows=provider_data.get("off_peak_windows", []),
        )
        for rate in provider_data.get("rates", []):
            pp.rates.append(
                ProviderRate(
                    provider=provider_slug,
                    model_id=str(rate["model_id"]),
                    input_usd_per_1k=float(rate.get("input_usd_per_1k", 0)),
                    output_usd_per_1k_peak=float(rate.get("output_usd_per_1k_peak", 0)),
                    output_usd_per_1k_offpeak=float(rate.get("output_usd_per_1k_offpeak", 0)),
                    context_window=rate.get("context_window"),
                    flat=flat,
                )
            )
        results.append(pp)
    return results


def load_compute_instances() -> list[ComputeInstance]:
    """Load GPU instance pricing from ``config/pricing/compute.yml``."""
    config_path = _config_dir() / "compute.yml"
    if not config_path.is_file():
        return []

    with open(config_path) as fh:
        data = yaml.safe_load(fh)

    results: list[ComputeInstance] = []
    for provider_slug, provider_data in data.get("instances", {}).items():
        for entry in provider_data.get("entries", []):
            results.append(
                ComputeInstance(
                    provider=provider_slug,
                    key=str(entry["key"]),
                    gpu=str(entry["gpu"]),
                    gpu_count=int(entry["gpu_count"]),
                    on_demand_usd_hr=float(entry["on_demand_usd_hr"]),
                    vcpus=entry.get("vcpus"),
                    memory_gb=entry.get("memory_gb"),
                    spot_discount=entry.get("spot_discount"),
                )
            )
    return results


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def provider_rate_dict(
    providers: list[ProviderPricing],
) -> dict[tuple[str, str], ProviderRate]:
    """Build a ``(provider, model_id) -> ProviderRate`` lookup."""
    return {(rate.provider, rate.model_id): rate for pp in providers for rate in pp.rates}


def build_provider_billing_table(
    providers: list[ProviderPricing],
) -> dict[str, dict[str, Any]]:
    """Return ``{provider_slug: {display_name, billing, source, flat}}``."""
    return {
        pp.provider: {
            "display_name": pp.display_name,
            "billing": pp.billing,
            "source": pp.source,
            "flat": pp.flat,
        }
        for pp in providers
    }
