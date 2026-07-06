"""InfraCostTracker: comprehensive cloud infrastructure cost tracking.

Tracks spend across AWS, GCP, Azure, RunPod, Vast.ai, and Terraform-provisioned
resources. Uses PricingCatalog as the primary rate source with built-in fallback
rate tables. Thread-safe accumulation per provider, resource type, and project.

Integration with SpendLimiter: total spend = model API costs + infra costs.
The InfraCostTracker accumulates infra spend independently; the SpendLimiter's
record() method accepts kind="infra" to include infra costs in its rolling window.
Callers should record infra spend to BOTH the InfraCostTracker (for breakdown)
AND the SpendLimiter (for cap enforcement).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from general_ludd.pricing_intel.catalog import PricingCatalog

logger = logging.getLogger(__name__)


class CloudProvider(StrEnum):
    AWS = "aws"
    GCP = "gcp"
    AZURE = "azure"
    RUNPOD = "runpod"
    VAST_AI = "vast_ai"
    LAMBDA_LABS = "lambda_labs"
    TERRAFORM = "terraform"


class ResourceType(StrEnum):
    GPU_INSTANCE = "gpu_instance"
    CPU_INSTANCE = "cpu_instance"
    STORAGE = "storage"
    NETWORK = "network"
    KUBERNETES = "kubernetes"
    MANAGED_SERVICE = "managed_service"
    TERRAFORM_RESOURCE = "terraform_resource"


@dataclass
class InfraCostRecord:
    provider: str
    resource_type: str
    resource_id: str
    cost_usd: float
    sku: str | None = None
    gpu_type: str | None = None
    gpu_count: int | None = None
    region: str | None = None
    project_id: str | None = None
    spot: bool = False
    notes: str = ""


# Hourly USD rates per provider + SKU. Used as fallback when PricingCatalog
# misses or is unavailable. Sources documented inline.
# Keys: "<provider>/<sku>" -> USD/hour

_INFRA_RATES: dict[str, float] = {
    # ── AWS EC2 GPU instances (us-east-1) ──
    # Source: https://aws.amazon.com/ec2/pricing/on-demand/ (2025-Q4)
    "aws/p3.2xlarge": 3.06,
    "aws/p3.8xlarge": 12.24,
    "aws/p3.16xlarge": 24.48,
    "aws/p4d.24xlarge": 32.77,
    "aws/p5.48xlarge": 98.32,
    "aws/g5.xlarge": 1.006,
    "aws/g5.2xlarge": 1.212,
    "aws/g5.12xlarge": 5.672,
    "aws/g5.48xlarge": 16.288,
    "aws/g6.xlarge": 0.804,
    "aws/g6e.xlarge": 1.156,
    # AWS EC2 CPU instances (general-purpose)
    "aws/m7i.large": 0.1008,
    "aws/m7i.xlarge": 0.2016,
    "aws/m7i.2xlarge": 0.4032,
    "aws/c7i.large": 0.0893,
    "aws/c7i.xlarge": 0.1785,
    "aws/r7i.large": 0.1330,
    # ── GCP Compute Engine GPU instances (us-central1) ──
    # Source: https://cloud.google.com/compute/gpus-pricing (2025-Q4)
    "gcp/a2-highgpu-1g": 3.673,
    "gcp/a2-highgpu-2g": 7.346,
    "gcp/a2-highgpu-4g": 14.692,
    "gcp/a2-highgpu-8g": 29.384,
    "gcp/a2-ultragpu-1g": 5.033,
    "gcp/a2-ultragpu-4g": 20.132,
    "gcp/a2-ultragpu-8g": 40.265,
    "gcp/a3-highgpu-8g": 98.328,
    "gcp/g2-standard-4": 0.700,
    "gcp/g2-standard-48": 2.800,
    # GCP CPU instances
    "gcp/n2-standard-2": 0.194,
    "gcp/n2-standard-4": 0.388,
    "gcp/n2-standard-8": 0.776,
    "gcp/c2-standard-4": 0.235,
    "gcp/c2-standard-8": 0.470,
    # ── Azure GPU instances (East US) ──
    # Source: https://azure.microsoft.com/en-us/pricing/details/virtual-machines/linux/ (2025-Q4)
    "azure/Standard_NC6s_v3": 3.06,
    "azure/Standard_NC12s_v3": 6.12,
    "azure/Standard_NC24s_v3": 12.24,
    "azure/Standard_NC24rs_v3": 16.00,
    "azure/Standard_NC96ads_A100_v4": 36.00,
    "azure/Standard_ND96asr_v4": 27.20,
    "azure/Standard_ND96amsr_A100_v4": 32.77,
    "azure/Standard_NC40ads_H100_v5": 39.33,
    # Azure CPU instances
    "azure/Standard_D2s_v5": 0.096,
    "azure/Standard_D4s_v5": 0.192,
    "azure/Standard_D8s_v5": 0.384,
    "azure/Standard_F2s_v2": 0.085,
    "azure/Standard_F4s_v2": 0.170,
    # ── RunPod GPU (Secure Cloud on-demand, USD/hr) ──
    # Source: https://www.runpod.io/gpu-instance/pricing (2025-Q4)
    "runpod/RTX-4090-1x": 0.74,
    "runpod/RTX-4090-2x": 1.48,
    "runpod/A100-SXM4-80GB-1x": 2.49,
    "runpod/A100-SXM4-80GB-8x": 16.00,
    "runpod/H100-SXM5-80GB-1x": 4.69,
    "runpod/H100-SXM5-80GB-8x": 32.69,
    "runpod/A40-1x": 0.54,
    "runpod/A6000-1x": 0.79,
    "runpod/L40-1x": 1.14,
    "runpod/3090-1x": 0.44,
    # ── Vast.ai GPU (typical community prices, volatile) ──
    # Source: https://vast.ai/pricing (2025-Q4, typical on-demand)
    "vast_ai/RTX-3090-1x": 0.30,
    "vast_ai/RTX-4090-1x": 0.45,
    "vast_ai/A100-PCIE-80GB-1x": 1.20,
    "vast_ai/A100-SXM4-80GB-1x": 1.80,
    "vast_ai/H100-PCIe-80GB-1x": 2.50,
    "vast_ai/H100-SXM5-80GB-1x": 3.00,
    "vast_ai/A6000-1x": 0.55,
    "vast_ai/A40-1x": 0.40,
    "vast_ai/L40S-1x": 0.80,
    # ── Terraform-provisioned resources (estimated monthly ÷ 730) ──
    "terraform/aws_eks_cluster": 0.10,
    "terraform/aws_rds_db_t3_medium": 0.07,
    "terraform/gcp_gke_cluster": 0.10,
    "terraform/gcp_cloud_sql": 0.05,
    "terraform/azure_aks_cluster": 0.10,
    "terraform/azure_postgresql_flexible": 0.06,
}


def _rate_key(provider: str, sku: str) -> str:
    return f"{provider}/{sku}"


class InfraCostTracker:
    """Comprehensive cloud infrastructure cost tracker.

    Tracks per-provider, per-resource-type, and per-project spend across
    AWS, GCP, Azure, RunPod, Vast.ai, and Terraform-provisioned resources.

    Pricing resolution order:
      1. PricingCatalog (primary) — queried via compute_price(provider, sku)
      2. Built-in _INFRA_RATES table (fallback)
      3. gpu_second rate from infra/pricing.py (last resort)

    Args:
        catalog: Optional PricingCatalog for live rates. When omitted, the
                 built-in rate table is used exclusively.
    """

    def __init__(
        self,
        catalog: PricingCatalog | None = None,
    ) -> None:
        self._catalog = catalog
        self._lock = threading.Lock()

        self._total_cost: float = 0.0
        self._cost_by_provider: dict[str, float] = {}
        self._cost_by_resource_type: dict[str, float] = {}
        self._cost_by_project: dict[str, float] = {}
        self._records: list[InfraCostRecord] = []

    # ------------------------------------------------------------------
    # Rate lookup
    # ------------------------------------------------------------------

    def hourly_rate_usd(
        self, provider: str, sku: str, spot: bool = False
    ) -> float:
        """Return the USD/hour rate for a provider+SKU combination.

        Resolution order:
          1. PricingCatalog.compute_price(provider, sku) → usd_per_hour()
          2. Built-in _INFRA_RATES table
          3. gpu_second default rate * 3600

        Args:
            provider: Provider slug (e.g. "aws", "gcp", "runpod").
            sku:      SKU or instance type (e.g. "p4d.24xlarge").
            spot:     If True, prefer spot pricing from catalog.

        Returns:
            USD per hour rate as a float.
        """
        rate = self._catalog_rate(provider, sku, spot)
        if rate is not None:
            return rate
        rate = _INFRA_RATES.get(_rate_key(provider, sku))
        if rate is not None:
            return rate
        from general_ludd.infra.pricing import INFRA_PRICING

        return INFRA_PRICING["gpu_second"] * 3600.0

    def cost_for_duration(
        self,
        provider: str,
        sku: str,
        duration_hours: float,
        *,
        spot: bool = False,
    ) -> float:
        """Cost in USD for using a resource for a given duration.

        Args:
            provider:       Provider slug.
            sku:            Instance type / SKU.
            duration_hours: Duration in hours.
            spot:           If True, use spot pricing.

        Returns:
            Total cost in USD.
        """
        rate = self.hourly_rate_usd(provider, sku, spot=spot)
        return rate * duration_hours

    # ------------------------------------------------------------------
    # Cost accumulation
    # ------------------------------------------------------------------

    def record(
        self,
        provider: str,
        resource_type: str,
        resource_id: str,
        cost_usd: float,
        *,
        sku: str | None = None,
        gpu_type: str | None = None,
        gpu_count: int | None = None,
        region: str | None = None,
        project_id: str | None = None,
        spot: bool = False,
        notes: str = "",
    ) -> InfraCostRecord:
        """Record an infrastructure cost event.

        Args:
            provider:      Provider slug (e.g. "aws", "runpod").
            resource_type: Type of resource (gpu_instance, cpu_instance, etc.).
            resource_id:   Unique identifier for this resource.
            cost_usd:      Cost in USD to record. Must be >= 0.
            sku:           Instance type / SKU.
            gpu_type:      GPU model if applicable.
            gpu_count:     Number of GPUs if applicable.
            region:        Cloud region.
            project_id:    Project this cost is attributed to.
            spot:          Whether spot pricing was used.
            notes:         Free-text notes.

        Returns:
            The created InfraCostRecord.

        Raises:
            ValueError: If cost_usd is negative or non-finite.
        """
        import math

        if not math.isfinite(cost_usd) or cost_usd < 0:
            raise ValueError(
                f"InfraCostTracker.record(): cost_usd must be finite and >= 0, "
                f"got {cost_usd!r}"
            )

        rec = InfraCostRecord(
            provider=provider,
            resource_type=resource_type,
            resource_id=resource_id,
            cost_usd=cost_usd,
            sku=sku,
            gpu_type=gpu_type,
            gpu_count=gpu_count,
            region=region,
            project_id=project_id,
            spot=spot,
            notes=notes,
        )

        with self._lock:
            self._records.append(rec)
            self._total_cost += cost_usd
            self._cost_by_provider[provider] = (
                self._cost_by_provider.get(provider, 0.0) + cost_usd
            )
            self._cost_by_resource_type[resource_type] = (
                self._cost_by_resource_type.get(resource_type, 0.0) + cost_usd
            )
            if project_id is not None:
                self._cost_by_project[project_id] = (
                    self._cost_by_project.get(project_id, 0.0) + cost_usd
                )

        logger.debug(
            "InfraCostTracker: recorded $%s for %s/%s (%s)",
            cost_usd,
            provider,
            resource_id,
            resource_type,
        )
        return rec

    def record_by_duration(
        self,
        provider: str,
        sku: str,
        duration_hours: float,
        resource_type: str = ResourceType.GPU_INSTANCE,
        *,
        resource_id: str | None = None,
        gpu_type: str | None = None,
        gpu_count: int | None = None,
        region: str | None = None,
        project_id: str | None = None,
        spot: bool = False,
        notes: str = "",
    ) -> InfraCostRecord:
        """Record cost for a resource used for a duration at a known rate.

        Convenience wrapper: looks up the hourly rate and computes cost
        before recording.

        Args:
            provider:       Provider slug.
            sku:            Instance type / SKU.
            duration_hours: Duration in hours.
            resource_type:  Type of resource.
            resource_id:    Unique resource ID (auto-generated if None).
            gpu_type:       GPU model.
            gpu_count:      GPU count.
            region:         Cloud region.
            project_id:     Project scope.
            spot:           Spot pricing flag.
            notes:          Free-text notes.

        Returns:
            The created InfraCostRecord.
        """
        cost = self.cost_for_duration(provider, sku, duration_hours, spot=spot)
        if resource_id is None:
            import uuid

            resource_id = f"{provider}-{sku}-{uuid.uuid4().hex[:8]}"
        return self.record(
            provider=provider,
            resource_type=resource_type,
            resource_id=resource_id,
            cost_usd=cost,
            sku=sku,
            gpu_type=gpu_type,
            gpu_count=gpu_count,
            region=region,
            project_id=project_id,
            spot=spot,
            notes=notes,
        )

    def record_terraform(
        self,
        resource_address: str,
        monthly_cost_usd: float,
        *,
        project_id: str | None = None,
        provider_hint: str = "terraform",
        notes: str = "",
    ) -> InfraCostRecord:
        """Record the cost of a Terraform-provisioned resource.

        Args:
            resource_address: Terraform resource address
                              (e.g. "aws_instance.gpu_node").
            monthly_cost_usd: Estimated monthly cost in USD.
            project_id:       Project scope.
            provider_hint:    Underlying cloud provider hint.
            notes:            Free-text notes.

        Returns:
            The created InfraCostRecord.
        """
        return self.record(
            provider=provider_hint,
            resource_type=ResourceType.TERRAFORM_RESOURCE,
            resource_id=resource_address,
            cost_usd=monthly_cost_usd,
            project_id=project_id,
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def total_cost(self) -> float:
        with self._lock:
            return self._total_cost

    def cost_by_provider(self) -> dict[str, float]:
        with self._lock:
            return dict(self._cost_by_provider)

    def cost_by_resource_type(self) -> dict[str, float]:
        with self._lock:
            return dict(self._cost_by_resource_type)

    def cost_by_project(self) -> dict[str, float]:
        with self._lock:
            return dict(self._cost_by_project)

    def records(self) -> list[InfraCostRecord]:
        with self._lock:
            return list(self._records)

    def provider_breakdown(
        self, provider: str
    ) -> dict[str, float]:
        """Breakdown of cost for a single provider by resource type."""
        with self._lock:
            result: dict[str, float] = {}
            for rec in self._records:
                if rec.provider == provider:
                    result[rec.resource_type] = (
                        result.get(rec.resource_type, 0.0) + rec.cost_usd
                    )
            return result

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable summary snapshot.

        Returns dict with keys: total_cost, by_provider, by_resource_type,
        by_project, record_count.
        """
        with self._lock:
            return {
                "total_cost": self._total_cost,
                "by_provider": dict(self._cost_by_provider),
                "by_resource_type": dict(self._cost_by_resource_type),
                "by_project": dict(self._cost_by_project),
                "record_count": len(self._records),
            }

    # ------------------------------------------------------------------
    # Internal: PricingCatalog query
    # ------------------------------------------------------------------

    def _catalog_rate(
        self, provider: str, sku: str, spot: bool
    ) -> float | None:
        """Query the PricingCatalog for an hourly rate. Returns None on miss."""
        catalog = self._catalog
        if catalog is None:
            return None
        try:
            price = catalog.compute_price(provider, sku, spot=spot)
        except Exception as exc:
            logger.warning(
                "InfraCostTracker: catalog.compute_price(%s, %s) raised %s",
                provider, sku, exc,
            )
            return None
        if price is None:
            return None
        try:
            return price.usd_per_hour()
        except Exception:
            return None
