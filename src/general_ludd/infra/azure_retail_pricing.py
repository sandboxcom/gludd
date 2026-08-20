"""Exact Azure Container Apps serverless-GPU retail-price estimates.

The public Azure Retail Prices API is unauthenticated, region-specific, and
paginated.  This module deliberately resolves each billable Container Apps
component by exact region, SKU name, consumption price type, currency, unit,
and effective date.  It never substitutes an expired cache entry after a
refresh failure because an underestimated pre-deploy price defeats the spend
gate it is meant to protect.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import TYPE_CHECKING, cast
from urllib.parse import urlencode, urlparse
from urllib.request import Request

from general_ludd.security.url_fetch import FetchPolicy, secure_fetch

if TYPE_CHECKING:
    from general_ludd.infra.azure_cost_reconciliation import AzureCostPrediction

_API_ENDPOINT = "https://prices.azure.com/api/retail/prices"
_API_VERSION = "2023-01-01-preview"
_API_HOST = "prices.azure.com"
_SERVICE_NAME = "Azure Container Apps"
_PRICE_TYPE = "Consumption"
_SOURCE = (
    "Azure Retail Prices API 2023-01-01-preview — "
    "https://learn.microsoft.com/en-us/rest/api/cost-management/"
    "retail-prices/azure-retail-prices"
)

_GPU_METER_BY_TYPE = {
    "t4": "Standard NC T4 v3 GPU Usage",
    "a100_40": "Standard NC A100 v4 GPU Usage",
    "a100_80": "Standard NC A100 v4 GPU Usage",
}
_PROFILE_BY_GPU_TYPE = {
    "t4": ("Consumption-GPU-NC8as-T4", 8.0, 56.0),
    "a100_40": ("Consumption-GPU-NC24-A100", 24.0, 220.0),
    "a100_80": ("Consumption-GPU-NC24-A100", 24.0, 220.0),
}
_VCPU_SKU = "Standard vCPU Active Usage"
_MEMORY_SKU = "Standard Memory Active Usage"
_STANDARD_SKU = "Standard"
_REGION_PATTERN = re.compile(r"^[a-z0-9-]+$")
_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9._ /()-]+$")
_MONTHLY_BILLING_HOURS = 730.0
_MAX_API_RESPONSE_BYTES = 8 * 1024 * 1024

_VM_SHAPE_BY_GPU_TYPE = {
    "t4": (
        "Standard_NC8as_T4_v3",
        "NC8as T4 v3",
        "NC8as T4 v3",
        "Virtual Machines NCasT4 v3 Series",
    ),
    "a100_40": (
        "Standard_NC24ads_A100_v4",
        "Standard_NC24ads_A100_v4",
        "NC24ads_A100_v4",
        "NCads A100 v4 Series Linux",
    ),
    "a100_80": (
        "Standard_NC24ads_A100_v4",
        "Standard_NC24ads_A100_v4",
        "NC24ads_A100_v4",
        "NCads A100 v4 Series Linux",
    ),
}
_STANDARD_SSD_TIERS = (
    (4, "E1"),
    (8, "E2"),
    (16, "E3"),
    (32, "E4"),
    (64, "E6"),
    (128, "E10"),
    (256, "E15"),
    (512, "E20"),
    (1024, "E30"),
    (2048, "E40"),
    (4096, "E50"),
    (8192, "E60"),
    (16384, "E70"),
    (32767, "E80"),
)


class AzureRetailPricingError(RuntimeError):
    """Raised when an exact, current Azure retail meter cannot be proven."""


def _secure_urlopen(request: Request, timeout: float) -> BytesIO:
    """Compatibility-shaped adapter over the central SSRF-safe fetcher."""
    result = secure_fetch(
        request.full_url,
        policy=FetchPolicy(
            allowed_hosts=frozenset({_API_HOST}),
            allowed_schemes=frozenset({"https"}),
            max_bytes=_MAX_API_RESPONSE_BYTES,
            timeout_seconds=timeout,
            dns_timeout_seconds=min(timeout, 2.0),
            max_redirects=0,
        ),
        headers=dict(request.header_items()),
    )
    if not 200 <= result.status_code < 300:
        raise AzureRetailPricingError(
            f"Azure Retail Prices API returned HTTP {result.status_code}"
        )
    return BytesIO(result.content)


# Retain the established injectable seam used by offline tests while routing
# production calls through DNS-pinned, allowlisted ``secure_fetch``.
urlopen: Callable[[Request, float], BytesIO] = _secure_urlopen


@dataclass(frozen=True)
class AzureRetailMeter:
    """One exact, effective Azure Retail Prices API meter."""

    region: str
    sku_name: str
    price_type: str
    meter_id: str
    meter_name: str
    retail_price: float
    unit_of_measure: str
    effective_start_date: datetime
    fetched_at: datetime
    source: str = _SOURCE


@dataclass(frozen=True)
class AzureRetailMeterSelector:
    """Exact server-side and client-side identity for one Azure meter."""

    service_name: str
    product_name: str
    sku_name: str
    meter_name: str
    unit_of_measure: str
    arm_sku_name: str | None = None

    def __post_init__(self) -> None:
        """Validate the initialized instance."""
        for name in (
            "service_name",
            "product_name",
            "sku_name",
            "meter_name",
            "unit_of_measure",
        ):
            value = getattr(self, name)
            if not value or not _IDENTITY_PATTERN.fullmatch(value):
                raise AzureRetailPricingError(
                    f"invalid Azure retail selector {name}={value!r}"
                )
        if self.arm_sku_name is not None and (
            not self.arm_sku_name
            or not _IDENTITY_PATTERN.fullmatch(self.arm_sku_name)
        ):
            raise AzureRetailPricingError(
                f"invalid Azure retail selector arm_sku_name={self.arm_sku_name!r}"
            )

    @property
    def cache_identity(self) -> tuple[str, ...]:
        """Execute ``cache_identity``."""
        return (
            self.service_name,
            self.product_name,
            self.arm_sku_name or "",
            self.sku_name,
            self.meter_name,
            self.unit_of_measure,
        )


@dataclass(frozen=True)
class AzureContainerAppsCostEstimate:
    """Billable active-use components for one serverless-GPU replica."""

    region: str
    workload_profile: str
    duration_seconds: float
    vcpu: float
    memory_gib: float
    gpu_meter: AzureRetailMeter
    vcpu_meter: AzureRetailMeter
    memory_meter: AzureRetailMeter

    @property
    def gpu_cost_usd(self) -> float:
        """Execute ``gpu_cost_usd``."""
        return self.gpu_meter.retail_price * self.duration_seconds

    @property
    def vcpu_cost_usd(self) -> float:
        """Execute ``vcpu_cost_usd``."""
        return self.vcpu_meter.retail_price * self.vcpu * self.duration_seconds

    @property
    def memory_cost_usd(self) -> float:
        """Execute ``memory_cost_usd``."""
        return (
            self.memory_meter.retail_price
            * self.memory_gib
            * self.duration_seconds
        )

    @property
    def total_cost_usd(self) -> float:
        """Execute ``total_cost_usd``."""
        return self.gpu_cost_usd + self.vcpu_cost_usd + self.memory_cost_usd

    @property
    def hourly_rate_usd(self) -> float:
        """Execute ``hourly_rate_usd``."""
        return (
            self.gpu_meter.retail_price
            + self.vcpu_meter.retail_price * self.vcpu
            + self.memory_meter.retail_price * self.memory_gib
        ) * 3600.0

    @property
    def meter_ids(self) -> tuple[str, str, str]:
        """Execute ``meter_ids``."""
        return (
            self.gpu_meter.meter_id,
            self.vcpu_meter.meter_id,
            self.memory_meter.meter_id,
        )


@dataclass(frozen=True)
class _CacheEntry:
    cached_at: float
    meter: AzureRetailMeter


_FetchJSON = Callable[[str, float], Mapping[str, object]]


class AzureContainerAppsRetailPricing:
    """Resolve exact public retail meters and estimate one active replica.

    Cache entries are immutable and guarded by a lock.  Once an entry passes
    ``cache_ttl_seconds``, callers must obtain a fresh API response; a failed
    refresh raises :class:`AzureRetailPricingError` rather than returning stale
    pricing.
    """

    def __init__(
        self,
        *,
        fetch_json: _FetchJSON | None = None,
        cache_ttl_seconds: float = 3600.0,
        timeout_seconds: float = 10.0,
        max_pages: int = 10,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """Initialize a ``AzureContainerAppsRetailPricing`` instance."""
        if not math.isfinite(cache_ttl_seconds) or cache_ttl_seconds <= 0:
            raise ValueError("cache_ttl_seconds must be finite and > 0")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and > 0")
        if max_pages <= 0:
            raise ValueError("max_pages must be > 0")
        self._fetch_json = fetch_json or self._default_fetch_json
        self._cache_ttl_seconds = cache_ttl_seconds
        self._timeout_seconds = timeout_seconds
        self._max_pages = max_pages
        self._monotonic = monotonic
        self._now = now or (lambda: datetime.now(UTC))
        self._cache: dict[tuple[str, ...], _CacheEntry] = {}
        self._lock = threading.Lock()

    def estimate_for_gpu(
        self,
        gpu_type: str,
        *,
        region: str,
        duration_seconds: float,
        vcpu: float | None = None,
        memory_gib: float | None = None,
    ) -> AzureContainerAppsCostEstimate:
        """Estimate exact active GPU, vCPU, and memory usage for one replica."""
        normalized_gpu = gpu_type.lower()
        profile = _PROFILE_BY_GPU_TYPE.get(normalized_gpu)
        if profile is None:
            supported = ", ".join(sorted(_PROFILE_BY_GPU_TYPE))
            raise AzureRetailPricingError(
                "unsupported Azure Container Apps GPU "
                f"{gpu_type!r}; expected one of: {supported}"
            )
        self._validate_region(region)
        self._validate_positive_finite("duration_seconds", duration_seconds)

        workload_profile, default_vcpu, default_memory_gib = profile
        selected_vcpu = default_vcpu if vcpu is None else vcpu
        selected_memory = default_memory_gib if memory_gib is None else memory_gib
        self._validate_positive_finite("vcpu", selected_vcpu)
        self._validate_positive_finite("memory_gib", selected_memory)

        gpu_meter = self.resolve_meter(
            region=region,
            sku_name=_STANDARD_SKU,
            meter_name=_GPU_METER_BY_TYPE[normalized_gpu],
            price_type=_PRICE_TYPE,
            unit_of_measure="1 Second",
        )
        vcpu_meter = self.resolve_meter(
            region=region,
            sku_name=_STANDARD_SKU,
            meter_name=_VCPU_SKU,
            price_type=_PRICE_TYPE,
            unit_of_measure="1 Second",
        )
        memory_meter = self.resolve_meter(
            region=region,
            sku_name=_STANDARD_SKU,
            meter_name=_MEMORY_SKU,
            price_type=_PRICE_TYPE,
            unit_of_measure="1 GiB Second",
        )
        return AzureContainerAppsCostEstimate(
            region=region,
            workload_profile=workload_profile,
            duration_seconds=duration_seconds,
            vcpu=selected_vcpu,
            memory_gib=selected_memory,
            gpu_meter=gpu_meter,
            vcpu_meter=vcpu_meter,
            memory_meter=memory_meter,
        )

    def resolve_meter(
        self,
        *,
        region: str,
        sku_name: str,
        meter_name: str,
        price_type: str,
        unit_of_measure: str,
    ) -> AzureRetailMeter:
        """Return one exact Container Apps meter from a still-fresh cache."""
        return self.resolve_exact_meter(
            region=region,
            selector=AzureRetailMeterSelector(
                service_name=_SERVICE_NAME,
                product_name="Azure Container Apps",
                sku_name=sku_name,
                meter_name=meter_name,
                unit_of_measure=unit_of_measure,
            ),
            price_type=price_type,
            require_product_name=False,
        )

    def resolve_exact_meter(
        self,
        *,
        region: str,
        selector: AzureRetailMeterSelector,
        price_type: str = _PRICE_TYPE,
        require_product_name: bool = True,
    ) -> AzureRetailMeter:
        """Resolve one fully specified current retail meter or fail closed.

        ``require_product_name=False`` exists only for the legacy Container Apps
        records whose product-name field has varied while their service, SKU,
        meter, and unit identities remained stable. VM and ancillary forecasts
        always require the product identity as well.
        """
        self._validate_region(region)
        if price_type != _PRICE_TYPE:
            raise AzureRetailPricingError(
                f"unsupported Azure price type {price_type!r}; expected {_PRICE_TYPE!r}"
            )
        cache_key = (
            region,
            price_type,
            "product-required" if require_product_name else "product-ignored",
            *selector.cache_identity,
        )

        with self._lock:
            current_tick = self._monotonic()
            cached = self._cache.get(cache_key)
            if (
                cached is not None
                and current_tick - cached.cached_at < self._cache_ttl_seconds
            ):
                return cached.meter

            try:
                meter = self._fetch_exact_meter(
                    region=region,
                    selector=selector,
                    price_type=price_type,
                    require_product_name=require_product_name,
                )
            except AzureRetailPricingError:
                raise
            except Exception as exc:
                raise AzureRetailPricingError(
                    "unable to obtain a fresh Azure retail price for "
                    f"{region}/{selector.sku_name}/{selector.meter_name}: "
                    f"{type(exc).__name__}"
                ) from exc

            self._cache[cache_key] = _CacheEntry(
                cached_at=self._monotonic(),
                meter=meter,
            )
            return meter

    def _fetch_exact_meter(
        self,
        *,
        region: str,
        selector: AzureRetailMeterSelector,
        price_type: str,
        require_product_name: bool,
    ) -> AzureRetailMeter:
        filter_parts = [
            f"armRegionName eq '{region}'",
            f"serviceName eq '{selector.service_name}'",
            f"priceType eq '{price_type}'",
        ]
        if selector.arm_sku_name is not None:
            filter_parts.append(f"armSkuName eq '{selector.arm_sku_name}'")
        elif selector.service_name != "Virtual Network":
            filter_parts.extend(
                (
                    f"skuName eq '{selector.sku_name}'",
                    f"meterName eq '{selector.meter_name}'",
                )
            )
        query_filter = " and ".join(filter_parts)
        query = urlencode(
            {
                "api-version": _API_VERSION,
                "currencyCode": "'USD'",
                "$filter": query_filter,
            }
        )
        url: str | None = f"{_API_ENDPOINT}?{query}"
        raw_items: list[Mapping[str, object]] = []

        for _page_number in range(self._max_pages):
            if url is None:
                break
            try:
                payload = self._fetch_json(url, self._timeout_seconds)
            except Exception as exc:
                raise AzureRetailPricingError(
                    "unable to obtain a fresh Azure retail price for "
                    f"{region}/{selector.sku_name}/{selector.meter_name}: "
                    f"{type(exc).__name__}"
                ) from exc
            items = payload.get("Items")
            if not isinstance(items, list):
                raise AzureRetailPricingError(
                    "Azure Retail Prices response has no Items list"
                )
            for item in items:
                if isinstance(item, Mapping):
                    raw_items.append(cast(Mapping[str, object], item))

            raw_next = payload.get("NextPageLink")
            if raw_next in (None, ""):
                url = None
                break
            if not isinstance(raw_next, str) or not self._safe_next_page(raw_next):
                raise AzureRetailPricingError(
                    "Azure Retail Prices response contained an unsafe NextPageLink"
                )
            url = raw_next
        else:
            raise AzureRetailPricingError(
                f"Azure Retail Prices response exceeded {self._max_pages} pages"
            )

        return self._select_effective_meter(
            raw_items,
            region=region,
            selector=selector,
            price_type=price_type,
            require_product_name=require_product_name,
        )

    def _select_effective_meter(
        self,
        raw_items: list[Mapping[str, object]],
        *,
        region: str,
        selector: AzureRetailMeterSelector,
        price_type: str,
        require_product_name: bool,
    ) -> AzureRetailMeter:
        fetched_at = self._now()
        if fetched_at.tzinfo is None:
            raise AzureRetailPricingError("pricing clock must return a timezone-aware time")

        candidates: list[AzureRetailMeter] = []
        for item in raw_items:
            if not self._has_exact_identity(
                item,
                region=region,
                selector=selector,
                price_type=price_type,
                require_product_name=require_product_name,
            ):
                continue
            effective = self._parse_effective_date(item.get("effectiveStartDate"))
            if effective > fetched_at:
                continue
            raw_price = item.get("retailPrice")
            if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float, str)):
                raise AzureRetailPricingError(
                    "invalid retail price for "
                    f"{region}/{selector.sku_name}/{selector.meter_name}: {raw_price!r}"
                )
            try:
                retail_price = float(raw_price)
            except (TypeError, ValueError) as exc:
                raise AzureRetailPricingError(
                    "invalid retail price for "
                    f"{region}/{selector.sku_name}/{selector.meter_name}: {raw_price!r}"
                ) from exc
            if not math.isfinite(retail_price) or retail_price <= 0:
                raise AzureRetailPricingError(
                    "invalid retail price for "
                    f"{region}/{selector.sku_name}/{selector.meter_name}: {raw_price!r}"
                )
            meter_id = str(item.get("meterId") or "")
            resolved_meter_name = str(item.get("meterName") or "")
            if not meter_id or not resolved_meter_name:
                raise AzureRetailPricingError(
                    "incomplete meter identity for "
                    f"{region}/{selector.sku_name}/{selector.meter_name}"
                )
            candidates.append(
                AzureRetailMeter(
                    region=region,
                    sku_name=selector.sku_name,
                    price_type=price_type,
                    meter_id=meter_id,
                    meter_name=resolved_meter_name,
                    retail_price=retail_price,
                    unit_of_measure=selector.unit_of_measure,
                    effective_start_date=effective,
                    fetched_at=fetched_at,
                )
            )

        if not candidates:
            ordered_items = sorted(
                raw_items,
                key=lambda item: (
                    item.get("productName") != selector.product_name,
                    str(item.get("productName") or ""),
                    str(item.get("meterName") or ""),
                ),
            )
            observed = list(
                dict.fromkeys(
                    "/".join(
                        str(item.get(field) or "")
                        for field in (
                            "productName",
                            "armSkuName",
                            "skuName",
                            "meterName",
                            "unitOfMeasure",
                        )
                    )
                    for item in ordered_items
                )
            )[:8]
            observed_summary = "; ".join(observed) if observed else "none"
            raise AzureRetailPricingError(
                "no exact current Azure retail meter for "
                f"region={region!r}, service={selector.service_name!r}, "
                f"sku={selector.sku_name!r}, meter={selector.meter_name!r}, "
                f"priceType={price_type!r}, unit={selector.unit_of_measure!r}; "
                f"observed={observed_summary}"
            )

        latest_date = max(candidate.effective_start_date for candidate in candidates)
        latest = [
            candidate
            for candidate in candidates
            if candidate.effective_start_date == latest_date
        ]
        if len(latest) != 1:
            meter_ids = ", ".join(sorted(candidate.meter_id for candidate in latest))
            raise AzureRetailPricingError(
                "ambiguous Azure retail meter for "
                f"{region}/{selector.sku_name}/{selector.meter_name} at "
                f"{latest_date.isoformat()}: {meter_ids}"
            )
        return latest[0]

    @staticmethod
    def _has_exact_identity(
        item: Mapping[str, object],
        *,
        region: str,
        selector: AzureRetailMeterSelector,
        price_type: str,
        require_product_name: bool,
    ) -> bool:
        return (
            item.get("armRegionName") == region
            and item.get("skuName") == selector.sku_name
            and item.get("meterName") == selector.meter_name
            and item.get("type") == price_type
            and item.get("serviceName") == selector.service_name
            and (
                not require_product_name
                or item.get("productName") == selector.product_name
            )
            and (
                selector.arm_sku_name is None
                or item.get("armSkuName") == selector.arm_sku_name
            )
            and item.get("currencyCode") == "USD"
            and item.get("unitOfMeasure") == selector.unit_of_measure
            and item.get("isPrimaryMeterRegion") is True
        )

    @staticmethod
    def _parse_effective_date(raw: object) -> datetime:
        if not isinstance(raw, str) or not raw:
            raise AzureRetailPricingError(
                f"invalid Azure meter effectiveStartDate: {raw!r}"
            )
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise AzureRetailPricingError(
                f"invalid Azure meter effectiveStartDate: {raw!r}"
            ) from exc
        if parsed.tzinfo is None:
            raise AzureRetailPricingError(
                f"invalid Azure meter effectiveStartDate: {raw!r}"
            )
        return parsed.astimezone(UTC)

    @staticmethod
    def _safe_next_page(url: str) -> bool:
        parsed = urlparse(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == _API_HOST
            and parsed.path == "/api/retail/prices"
            and parsed.username is None
            and parsed.password is None
        )

    @staticmethod
    def _validate_region(region: str) -> None:
        if region != "Global" and not _REGION_PATTERN.fullmatch(region):
            raise AzureRetailPricingError(f"invalid Azure region {region!r}")

    @staticmethod
    def _validate_positive_finite(name: str, value: float) -> None:
        if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and > 0, got {value!r}")

    @staticmethod
    def _default_fetch_json(url: str, timeout_seconds: float) -> Mapping[str, object]:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "general-ludd/azure-retail-pricing",
            },
        )
        with urlopen(request, timeout_seconds) as response:
            payload = json.load(response)
        if not isinstance(payload, Mapping):
            raise AzureRetailPricingError(
                "Azure Retail Prices response must be a JSON object"
            )
        return cast(Mapping[str, object], payload)


@dataclass(frozen=True)
class AzureVmBillingPhases:
    """Elapsed VM lifecycle segments billed until deallocation completes."""

    warmup_seconds: float
    runtime_seconds: float
    shutdown_seconds: float

    def __post_init__(self) -> None:
        """Validate the initialized instance."""
        for name in ("warmup_seconds", "shutdown_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and >= 0 seconds")
        if (
            isinstance(self.runtime_seconds, bool)
            or not math.isfinite(self.runtime_seconds)
            or self.runtime_seconds <= 0
        ):
            raise ValueError("runtime_seconds must be finite and > 0 seconds")

    @property
    def total_seconds(self) -> float:
        """Execute ``total_seconds``."""
        return self.warmup_seconds + self.runtime_seconds + self.shutdown_seconds


@dataclass(frozen=True)
class AzureVirtualMachineCostEstimate:
    """Exact VM compute plus retained disk and public-IP retail forecast."""

    region: str
    gpu_type: str
    arm_sku_name: str
    purchase_option: str
    phases: AzureVmBillingPhases
    disk_size_gib: int
    disk_tier: str
    compute_meter: AzureRetailMeter
    disk_meter: AzureRetailMeter
    public_ip_meter: AzureRetailMeter

    @property
    def elapsed_hours(self) -> float:
        """Execute ``elapsed_hours``."""
        return self.phases.total_seconds / 3600.0

    @property
    def compute_cost_usd(self) -> float:
        """Execute ``compute_cost_usd``."""
        return self.compute_meter.retail_price * self.elapsed_hours

    @property
    def disk_cost_usd(self) -> float:
        """Execute ``disk_cost_usd``."""
        return (
            self.disk_meter.retail_price
            * self.elapsed_hours
            / _MONTHLY_BILLING_HOURS
        )

    @property
    def public_ip_cost_usd(self) -> float:
        """Execute ``public_ip_cost_usd``."""
        return self.public_ip_meter.retail_price * self.elapsed_hours

    @property
    def total_cost_usd(self) -> float:
        """Execute ``total_cost_usd``."""
        return self.compute_cost_usd + self.disk_cost_usd + self.public_ip_cost_usd

    @property
    def phase_costs_usd(self) -> Mapping[str, float]:
        """Execute ``phase_costs_usd``."""
        rate = self.compute_meter.retail_price / 3600.0
        return {
            "warmup": rate * self.phases.warmup_seconds,
            "runtime": rate * self.phases.runtime_seconds,
            "shutdown": rate * self.phases.shutdown_seconds,
        }

    @property
    def meter_ids(self) -> tuple[str, str, str]:
        """Execute ``meter_ids``."""
        return (
            self.compute_meter.meter_id,
            self.disk_meter.meter_id,
            self.public_ip_meter.meter_id,
        )

    def to_cost_prediction(
        self,
        *,
        prediction_id: str,
        todo_id: str,
        subscription_id: str,
        resource_group: str,
        resource_ids: tuple[str, ...],
        workload: str,
        usage_started_at: datetime,
        conservative_multiplier: float = 1.15,
        tags: Mapping[str, str] | None = None,
    ) -> AzureCostPrediction:
        """Materialize the immutable work-item identity used by reconciliation."""
        if (
            isinstance(conservative_multiplier, bool)
            or not math.isfinite(conservative_multiplier)
            or conservative_multiplier < 1
        ):
            raise ValueError("conservative_multiplier must be finite and >= 1")
        from general_ludd.infra.azure_cost_reconciliation import AzureCostPrediction

        prediction_tags = dict(tags or {})
        prediction_tags.update(
            {
                "gludd-pricing-source": "azure-retail-prices",
                "gludd-purchase-option": self.purchase_option,
                "gludd-disk-tier": self.disk_tier,
            }
        )
        return AzureCostPrediction(
            prediction_id=prediction_id,
            todo_id=todo_id,
            subscription_id=subscription_id,
            resource_group=resource_group,
            resource_ids=resource_ids,
            meter_ids=self.meter_ids,
            region=self.region,
            sku=f"{self.arm_sku_name}:{self.purchase_option}",
            workload=workload,
            predicted_cost_usd=self.total_cost_usd,
            conservative_ceiling_usd=(
                self.total_cost_usd * conservative_multiplier
            ),
            usage_started_at=usage_started_at,
            usage_ended_at=usage_started_at
            + timedelta(seconds=self.phases.total_seconds),
            tags=prediction_tags,
        )


class AzureVirtualMachineRetailPricing:
    """Fail-closed exact retail pricing for elastic Linux GPU VMs.

    A VM estimate always carries the compute meter and the two common resources
    that continue billing during deallocation: its managed OS disk and static
    public IP. No static hourly fallback is available.
    """

    def __init__(
        self,
        *,
        retail_client: AzureContainerAppsRetailPricing | None = None,
        fetch_json: _FetchJSON | None = None,
        cache_ttl_seconds: float = 3600.0,
        timeout_seconds: float = 10.0,
        max_pages: int = 10,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """Initialize a ``AzureVirtualMachineRetailPricing`` instance."""
        if retail_client is not None and fetch_json is not None:
            raise ValueError("retail_client and fetch_json are mutually exclusive")
        self._retail = retail_client or AzureContainerAppsRetailPricing(
            fetch_json=fetch_json,
            cache_ttl_seconds=cache_ttl_seconds,
            timeout_seconds=timeout_seconds,
            max_pages=max_pages,
            monotonic=monotonic,
            now=now,
        )

    def estimate_for_gpu(
        self,
        gpu_type: str,
        *,
        region: str,
        phases: AzureVmBillingPhases,
        purchase_option: str = "on_demand",
        disk_size_gib: int = 128,
    ) -> AzureVirtualMachineCostEstimate:
        """Resolve exact Linux VM, Standard SSD, and static IPv4 meters."""
        normalized_gpu = gpu_type.lower()
        shape = _VM_SHAPE_BY_GPU_TYPE.get(normalized_gpu)
        if shape is None:
            supported = ", ".join(sorted(_VM_SHAPE_BY_GPU_TYPE))
            raise AzureRetailPricingError(
                f"unsupported Azure VM GPU {gpu_type!r}; expected one of: {supported}"
            )
        if purchase_option not in {"on_demand", "spot"}:
            raise AzureRetailPricingError(
                "purchase_option must be exactly 'on_demand' or 'spot'"
            )
        if (
            isinstance(disk_size_gib, bool)
            or not isinstance(disk_size_gib, int)
            or disk_size_gib <= 0
            or disk_size_gib > _STANDARD_SSD_TIERS[-1][0]
        ):
            raise ValueError("disk_size_gib must be an integer from 1 to 32767")

        arm_sku_name, base_sku_name, base_meter_name, product_name = shape
        suffix = " Spot" if purchase_option == "spot" else ""
        compute_sku = f"{base_sku_name}{suffix}"
        compute_name = f"{base_meter_name}{suffix}"
        disk_tier = next(
            tier
            for maximum_gib, tier in _STANDARD_SSD_TIERS
            if disk_size_gib <= maximum_gib
        )
        compute_meter = self._retail.resolve_exact_meter(
            region=region,
            selector=AzureRetailMeterSelector(
                service_name="Virtual Machines",
                product_name=product_name,
                arm_sku_name=arm_sku_name,
                sku_name=compute_sku,
                meter_name=compute_name,
                unit_of_measure="1 Hour",
            ),
        )
        disk_meter = self._retail.resolve_exact_meter(
            region=region,
            selector=AzureRetailMeterSelector(
                service_name="Storage",
                product_name="Standard SSD Managed Disks",
                sku_name=f"{disk_tier} LRS",
                meter_name=f"{disk_tier} LRS Disk",
                unit_of_measure="1/Month",
            ),
        )
        public_ip_meter = self._retail.resolve_exact_meter(
            region="Global",
            selector=AzureRetailMeterSelector(
                service_name="Virtual Network",
                product_name="IP Addresses",
                sku_name="Standard",
                meter_name="Standard IPv4 Static Public IP",
                unit_of_measure="1 Hour",
            ),
        )
        return AzureVirtualMachineCostEstimate(
            region=region,
            gpu_type=normalized_gpu,
            arm_sku_name=arm_sku_name,
            purchase_option=purchase_option,
            phases=phases,
            disk_size_gib=disk_size_gib,
            disk_tier=disk_tier,
            compute_meter=compute_meter,
            disk_meter=disk_meter,
            public_ip_meter=public_ip_meter,
        )
