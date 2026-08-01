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
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

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


class AzureRetailPricingError(RuntimeError):
    """Raised when an exact, current Azure retail meter cannot be proven."""


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
        return self.gpu_meter.retail_price * self.duration_seconds

    @property
    def vcpu_cost_usd(self) -> float:
        return self.vcpu_meter.retail_price * self.vcpu * self.duration_seconds

    @property
    def memory_cost_usd(self) -> float:
        return (
            self.memory_meter.retail_price
            * self.memory_gib
            * self.duration_seconds
        )

    @property
    def total_cost_usd(self) -> float:
        return self.gpu_cost_usd + self.vcpu_cost_usd + self.memory_cost_usd

    @property
    def hourly_rate_usd(self) -> float:
        return (
            self.gpu_meter.retail_price
            + self.vcpu_meter.retail_price * self.vcpu
            + self.memory_meter.retail_price * self.memory_gib
        ) * 3600.0

    @property
    def meter_ids(self) -> tuple[str, str, str]:
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
        self._cache: dict[tuple[str, str, str, str, str], _CacheEntry] = {}
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
        """Return one exact current meter, using only a still-fresh cache."""
        self._validate_region(region)
        if price_type != _PRICE_TYPE:
            raise AzureRetailPricingError(
                f"unsupported Azure price type {price_type!r}; expected {_PRICE_TYPE!r}"
            )
        cache_key = (region, sku_name, meter_name, price_type, unit_of_measure)

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
                    sku_name=sku_name,
                    meter_name=meter_name,
                    price_type=price_type,
                    unit_of_measure=unit_of_measure,
                )
            except AzureRetailPricingError:
                raise
            except Exception as exc:
                raise AzureRetailPricingError(
                    "unable to obtain a fresh Azure retail price for "
                    f"{region}/{sku_name}/{meter_name}: {type(exc).__name__}"
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
        sku_name: str,
        meter_name: str,
        price_type: str,
        unit_of_measure: str,
    ) -> AzureRetailMeter:
        selector = (
            f"armRegionName eq '{region}' and "
            f"serviceName eq '{_SERVICE_NAME}' and "
            f"skuName eq '{sku_name}' and "
            f"meterName eq '{meter_name}' and "
            f"priceType eq '{price_type}'"
        )
        query = urlencode(
            {
                "api-version": _API_VERSION,
                "currencyCode": "'USD'",
                "$filter": selector,
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
                    f"{region}/{sku_name}/{meter_name}: {type(exc).__name__}"
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
            sku_name=sku_name,
            meter_name=meter_name,
            price_type=price_type,
            unit_of_measure=unit_of_measure,
        )

    def _select_effective_meter(
        self,
        raw_items: list[Mapping[str, object]],
        *,
        region: str,
        sku_name: str,
        meter_name: str,
        price_type: str,
        unit_of_measure: str,
    ) -> AzureRetailMeter:
        fetched_at = self._now()
        if fetched_at.tzinfo is None:
            raise AzureRetailPricingError("pricing clock must return a timezone-aware time")

        candidates: list[AzureRetailMeter] = []
        for item in raw_items:
            if not self._has_exact_identity(
                item,
                region=region,
                sku_name=sku_name,
                meter_name=meter_name,
                price_type=price_type,
                unit_of_measure=unit_of_measure,
            ):
                continue
            effective = self._parse_effective_date(item.get("effectiveStartDate"))
            if effective > fetched_at:
                continue
            raw_price = item.get("retailPrice")
            if isinstance(raw_price, bool):
                raise AzureRetailPricingError(
                    f"invalid retail price for {region}/{sku_name}/{meter_name}: {raw_price!r}"
                )
            try:
                retail_price = float(cast(Any, raw_price))
            except (TypeError, ValueError) as exc:
                raise AzureRetailPricingError(
                    f"invalid retail price for {region}/{sku_name}/{meter_name}: {raw_price!r}"
                ) from exc
            if not math.isfinite(retail_price) or retail_price <= 0:
                raise AzureRetailPricingError(
                    f"invalid retail price for {region}/{sku_name}/{meter_name}: {raw_price!r}"
                )
            meter_id = str(item.get("meterId") or "")
            meter_name = str(item.get("meterName") or "")
            if not meter_id or not meter_name:
                raise AzureRetailPricingError(
                    f"incomplete meter identity for {region}/{sku_name}/{meter_name}"
                )
            candidates.append(
                AzureRetailMeter(
                    region=region,
                    sku_name=sku_name,
                    price_type=price_type,
                    meter_id=meter_id,
                    meter_name=meter_name,
                    retail_price=retail_price,
                    unit_of_measure=unit_of_measure,
                    effective_start_date=effective,
                    fetched_at=fetched_at,
                )
            )

        if not candidates:
            raise AzureRetailPricingError(
                "no exact current Azure retail meter for "
                f"region={region!r}, sku={sku_name!r}, "
                f"meter={meter_name!r}, priceType={price_type!r}, "
                f"unit={unit_of_measure!r}"
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
                f"{region}/{sku_name}/{meter_name} at "
                f"{latest_date.isoformat()}: {meter_ids}"
            )
        return latest[0]

    @staticmethod
    def _has_exact_identity(
        item: Mapping[str, object],
        *,
        region: str,
        sku_name: str,
        meter_name: str,
        price_type: str,
        unit_of_measure: str,
    ) -> bool:
        return (
            item.get("armRegionName") == region
            and item.get("skuName") == sku_name
            and item.get("meterName") == meter_name
            and item.get("type") == price_type
            and item.get("serviceName") == _SERVICE_NAME
            and item.get("currencyCode") == "USD"
            and item.get("unitOfMeasure") == unit_of_measure
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
        if not _REGION_PATTERN.fullmatch(region):
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
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.load(response)
        if not isinstance(payload, Mapping):
            raise AzureRetailPricingError(
                "Azure Retail Prices response must be a JSON object"
            )
        return cast(Mapping[str, object], payload)
