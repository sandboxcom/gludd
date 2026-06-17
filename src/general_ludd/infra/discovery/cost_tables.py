"""Static cost/spec fallback tables for cloud GPU instance families.

These are deliberately small, UPPER-BOUND estimates used when a live pricing
API is unavailable (no SDK, unresolved creds, or a pricing call timed out). A
table-sourced resource is stamped ``meta['price_source']='static_table'`` and
the discovery status is downgraded to PARTIAL, so a stale/unpopulated table is
visible rather than silently shrinking the candidate pool.

Costs are on-demand USD/hour for the region the family is most commonly priced
in; treat them as estimates reconciled against SpendLimiter actuals post-deploy.
"""

from __future__ import annotations

from dataclasses import dataclass

from general_ludd.infra.compute import GPUType


@dataclass(frozen=True)
class InstanceSpec:
    instance_type: str
    gpu_type: GPUType | None
    gpu_count: int
    vcpu: int
    mem_gb: float
    on_demand_usd_hr: float
    spot_usd_hr: float | None = None


# AWS EC2 GPU families (subset). Prices are representative us-east-1 on-demand.
AWS_SPECS: tuple[InstanceSpec, ...] = (
    InstanceSpec("g4dn.xlarge", GPUType.T4, 1, 4, 16.0, 0.526, 0.158),
    InstanceSpec("g4dn.12xlarge", GPUType.T4, 4, 48, 192.0, 3.912, 1.174),
    InstanceSpec("g5.xlarge", GPUType.A10G, 1, 4, 16.0, 1.006, 0.302),
    InstanceSpec("g5.12xlarge", GPUType.A10G, 4, 48, 192.0, 5.672, 1.702),
    InstanceSpec("p4d.24xlarge", GPUType.A100_40, 8, 96, 1152.0, 32.773, 9.832),
    InstanceSpec("p5.48xlarge", GPUType.H100, 8, 192, 2048.0, 98.32, 29.50),
)

# GCP accelerator-attached machine families (subset). Representative us-central1.
GCP_SPECS: tuple[InstanceSpec, ...] = (
    InstanceSpec("g2-standard-4", GPUType.L4, 1, 4, 16.0, 0.85, 0.30),
    InstanceSpec("g2-standard-48", GPUType.L4, 4, 48, 192.0, 4.70, 1.65),
    InstanceSpec("a2-highgpu-1g", GPUType.A100_40, 1, 12, 85.0, 3.67, 1.10),
    InstanceSpec("a2-ultragpu-8g", GPUType.A100_80, 8, 96, 1360.0, 40.55, 12.16),
)

# Azure NC/ND GPU families (subset). Representative eastus.
AZURE_SPECS: tuple[InstanceSpec, ...] = (
    InstanceSpec("Standard_NC4as_T4_v3", GPUType.T4, 1, 4, 28.0, 0.526, 0.16),
    InstanceSpec("Standard_NC24ads_A100_v4", GPUType.A100_80, 1, 24, 220.0, 3.67, 1.10),
    InstanceSpec("Standard_ND96asr_v4", GPUType.A100_40, 8, 96, 900.0, 27.20, 8.16),
    InstanceSpec("Standard_NC40ads_H100_v5", GPUType.H100, 1, 40, 320.0, 6.98, 2.10),
)


def specs_for(source_value: str) -> tuple[InstanceSpec, ...]:
    return {
        "aws": AWS_SPECS,
        "gcp": GCP_SPECS,
        "azure": AZURE_SPECS,
    }.get(source_value, ())
