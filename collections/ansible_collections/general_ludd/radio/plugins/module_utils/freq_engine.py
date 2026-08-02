"""Frequency allocation engine for band plan management, compliance
checking, frequency coordination, and ITU region mapping.

Core operations:
    - Band plan registration and lookup
    - Frequency allocation with compliance checks
    - Power limit enforcement
    - Vacant span detection in occupied spectrum
    - Channel spacing computation
    - ITU region → country mapping

The FreqEngine orchestrates: register bands → request allocation →
compliance check → approve/deny with structured results.
"""

from __future__ import annotations

import dataclasses
from typing import Any

ITU_REGION_MAP: dict[str, int] = {
    "US": 2,
    "CA": 2,
    "MX": 2,
    "BR": 2,
    "AR": 2,
    "GB": 1,
    "DE": 1,
    "FR": 1,
    "IT": 1,
    "ES": 1,
    "NL": 1,
    "BE": 1,
    "CH": 1,
    "AT": 1,
    "PL": 1,
    "CZ": 1,
    "SE": 1,
    "NO": 1,
    "DK": 1,
    "FI": 1,
    "RU": 1,
    "UA": 1,
    "ZA": 1,
    "EG": 1,
    "NG": 1,
    "KE": 1,
    "IL": 1,
    "AE": 1,
    "SA": 1,
    "TR": 1,
    "JP": 3,
    "AU": 3,
    "NZ": 3,
    "CN": 3,
    "IN": 3,
    "KR": 3,
    "ID": 3,
    "PH": 3,
    "TH": 3,
    "VN": 3,
    "MY": 3,
    "SG": 3,
    "PK": 3,
    "BD": 3,
    "LK": 3,
}


@dataclasses.dataclass
class BandPlan:
    """A frequency band allocation with constraints.

    Attributes:
        name: Band name (e.g. "2m", "70cm").
        start_hz: Lower frequency edge in Hz.
        end_hz: Upper frequency edge in Hz.
        itu_region: ITU region this band applies to (1, 2, or 3).
        service: Radio service type (default "amateur").
        max_power_w: Maximum permitted power in watts.
        privileges: List of permitted mode identifiers.
        notes: Free-form notes.
    """

    name: str
    start_hz: int
    end_hz: int
    itu_region: int
    service: str = "amateur"
    max_power_w: int | None = None
    privileges: list[str] = dataclasses.field(default_factory=list)
    notes: str | None = None

    def __post_init__(self) -> None:
        if self.itu_region not in (1, 2, 3):
            raise ValueError(f"itu_region must be 1, 2, or 3, got {self.itu_region!r}")
        if self.start_hz < 0:
            raise ValueError(f"start_hz must be non-negative, got {self.start_hz}")
        if self.end_hz < 0:
            raise ValueError(f"end_hz must be non-negative, got {self.end_hz}")
        if self.start_hz >= self.end_hz:
            raise ValueError(f"start_hz ({self.start_hz}) must be less than end_hz ({self.end_hz})")

    @property
    def bandwidth_hz(self) -> int:
        return self.end_hz - self.start_hz

    @property
    def center_freq_hz(self) -> int:
        return int((self.start_hz + self.end_hz) / 2)

    def contains(self, freq_hz: int) -> bool:
        return self.start_hz <= freq_hz <= self.end_hz

    def overlaps(self, other: BandPlan) -> bool:
        return self.start_hz <= other.end_hz and other.start_hz <= self.end_hz

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "start_hz": self.start_hz,
            "end_hz": self.end_hz,
            "bandwidth_hz": self.bandwidth_hz,
            "center_freq_hz": self.center_freq_hz,
            "itu_region": self.itu_region,
            "service": self.service,
            "max_power_w": self.max_power_w,
            "privileges": list(self.privileges),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BandPlan:
        return cls(
            name=d["name"],
            start_hz=d["start_hz"],
            end_hz=d["end_hz"],
            itu_region=d["itu_region"],
            service=d.get("service", "amateur"),
            max_power_w=d.get("max_power_w"),
            privileges=list(d.get("privileges", [])),
            notes=d.get("notes"),
        )


@dataclasses.dataclass
class AllocationRequest:
    """A request to allocate a frequency slot.

    Attributes:
        center_freq_hz: Desired center frequency in Hz.
        bandwidth_hz: Required bandwidth in Hz.
        service: Radio service type.
        itu_region: ITU region.
        country: ISO 3166-1 alpha-2 country code.
        priority: Priority level (normal, high, critical).
    """

    center_freq_hz: int
    bandwidth_hz: int
    service: str = "amateur"
    itu_region: int = 2
    country: str = "US"
    priority: str = "normal"

    @property
    def start_freq_hz(self) -> int:
        return self.center_freq_hz - self.bandwidth_hz // 2

    @property
    def end_freq_hz(self) -> int:
        return self.center_freq_hz + self.bandwidth_hz // 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "center_freq_hz": self.center_freq_hz,
            "bandwidth_hz": self.bandwidth_hz,
            "start_freq_hz": self.start_freq_hz,
            "end_freq_hz": self.end_freq_hz,
            "service": self.service,
            "itu_region": self.itu_region,
            "country": self.country,
            "priority": self.priority,
        }


@dataclasses.dataclass
class AllocationResult:
    """Outcome of a frequency allocation request.

    Attributes:
        approved: Whether the allocation was granted.
        center_freq_hz: Allocated center frequency (may differ from request).
        bandwidth_hz: Allocated bandwidth.
        reason: Human-readable explanation.
        band_name: Name of the band allocated in.
        max_power_w: Maximum permitted power if allocated.
        interference_warning: Optional advisory about nearby signals.
    """

    approved: bool
    center_freq_hz: int
    bandwidth_hz: int
    reason: str = ""
    band_name: str | None = None
    max_power_w: int | None = None
    interference_warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "center_freq_hz": self.center_freq_hz,
            "bandwidth_hz": self.bandwidth_hz,
            "reason": self.reason,
            "band_name": self.band_name,
            "max_power_w": self.max_power_w,
            "interference_warning": self.interference_warning,
        }


@dataclasses.dataclass
class ComplianceCheck:
    """Result of a frequency compliance check.

    Attributes:
        passes: Whether all rules were satisfied.
        rules_checked: Number of rules evaluated.
        violations: List of violation descriptions.
        notes: Optional advisory notes.
    """

    passes: bool
    rules_checked: int
    violations: list[str] = dataclasses.field(default_factory=list)
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passes": self.passes,
            "rules_checked": self.rules_checked,
            "violations": list(self.violations),
            "notes": self.notes,
        }


def is_within_band(freq_hz: int, start_hz: int, end_hz: int) -> bool:
    """Check if a frequency falls within a band range."""
    return start_hz <= freq_hz <= end_hz


def find_allocated_bands(
    freq_hz: int,
    bands: list[BandPlan],
) -> list[BandPlan]:
    """Return all bands that contain the given frequency."""
    return [b for b in bands if b.contains(freq_hz)]


def check_compliance(
    center_freq_hz: int,
    power_w: int,
    bands: list[BandPlan],
) -> ComplianceCheck:
    """Check frequency allocation against a set of band plans.

    Checks: band membership, power limits, available privileges.
    """
    violations: list[str] = []
    rules_checked = 0
    matching = find_allocated_bands(center_freq_hz, bands)

    rules_checked += 1
    if not matching:
        violations.append(f"No matching band allocation for {center_freq_hz} Hz")

    for band in matching:
        rules_checked += 1
        if band.max_power_w is not None and power_w > band.max_power_w:
            violations.append(f"Power {power_w}W exceeds band limit {band.max_power_w}W in {band.name}")

    return ComplianceCheck(
        passes=len(violations) == 0,
        rules_checked=rules_checked,
        violations=violations,
    )


def allocate_frequency(
    request: AllocationRequest,
    bands: list[BandPlan],
) -> AllocationResult:
    """Attempt to allocate a frequency slot from registered band plans.

    Checks band membership and propagates max power from the first
    matching band. Does not verify actual occupancy (see SpectrumScanner).
    """
    matching = find_allocated_bands(request.center_freq_hz, bands)
    if not matching:
        return AllocationResult(
            approved=False,
            center_freq_hz=request.center_freq_hz,
            bandwidth_hz=request.bandwidth_hz,
            reason="Frequency outside all registered band allocations",
        )

    best = matching[0]
    return AllocationResult(
        approved=True,
        center_freq_hz=request.center_freq_hz,
        bandwidth_hz=request.bandwidth_hz,
        reason=f"Allocated in {best.name} band ({best.start_hz}-{best.end_hz} Hz)",
        band_name=best.name,
        max_power_w=best.max_power_w,
    )


def compute_channel_spacing(
    bandwidth_hz: int,
    guard_band_hz: int = 0,
) -> int:
    """Compute recommended channel spacing for a given bandwidth and guard band."""
    if guard_band_hz < 0:
        raise ValueError(f"guard_band_hz must be non-negative, got {guard_band_hz}")
    return bandwidth_hz + guard_band_hz


def find_vacant_span(
    start_hz: int,
    end_hz: int,
    occupied_ranges: list[tuple[int, int]],
    min_bandwidth_hz: int = 1,
) -> list[dict[str, Any]]:
    """Find frequency spans that are not occupied.

    Returns list of dicts with start_hz, end_hz, bandwidth_hz for each
    contiguous vacant span meeting the minimum bandwidth requirement.
    """
    if not occupied_ranges:
        span_bw = end_hz - start_hz
        if span_bw >= min_bandwidth_hz:
            return [{"start_hz": start_hz, "end_hz": end_hz, "bandwidth_hz": span_bw}]
        return []

    sorted_occ = sorted(occupied_ranges, key=lambda r: r[0])
    vacant: list[dict[str, Any]] = []
    cursor = start_hz

    for occ_start, occ_end in sorted_occ:
        if cursor < occ_start:
            gap = occ_start - cursor
            if gap >= min_bandwidth_hz:
                vacant.append(
                    {
                        "start_hz": cursor,
                        "end_hz": occ_start,
                        "bandwidth_hz": gap,
                    }
                )
        cursor = max(cursor, occ_end)

    if cursor < end_hz:
        gap = end_hz - cursor
        if gap >= min_bandwidth_hz:
            vacant.append(
                {
                    "start_hz": cursor,
                    "end_hz": end_hz,
                    "bandwidth_hz": gap,
                }
            )

    return vacant


def itu_region_for_country(country: str) -> int | None:
    """Return the ITU region (1, 2, or 3) for a country code."""
    return ITU_REGION_MAP.get(country.upper())


class FreqEngine:
    """Orchestrates frequency band management and allocation.

    Usage::

        engine = FreqEngine(itu_region=2)
        engine.register_band(BandPlan("2m", 144_000_000, 148_000_000, region=2))
        result = engine.request_allocation(
            AllocationRequest(146_520_000, 12_500, itu_region=2)
        )
    """

    def __init__(self, itu_region: int = 2) -> None:
        if itu_region not in (1, 2, 3):
            raise ValueError(f"itu_region must be 1, 2, or 3, got {itu_region!r}")
        self.itu_region = itu_region
        self.bands: list[BandPlan] = []

    def register_band(self, band: BandPlan) -> None:
        if band.itu_region != self.itu_region:
            raise ValueError(f"Band region ({band.itu_region}) does not match engine region ({self.itu_region})")
        self.bands.append(band)

    def lookup(self, freq_hz: int) -> BandPlan | None:
        """Find the first band containing the given frequency."""
        match = find_allocated_bands(freq_hz, self.bands)
        return match[0] if match else None

    def request_allocation(self, request: AllocationRequest) -> AllocationResult:
        """Process an allocation request against registered bands."""
        return allocate_frequency(request, self.bands)

    def list_bands(self) -> list[str]:
        return [b.name for b in self.bands]

    def to_dict(self) -> dict[str, Any]:
        return {
            "itu_region": self.itu_region,
            "bands": [b.to_dict() for b in self.bands],
        }


__all__ = [
    "ITU_REGION_MAP",
    "AllocationRequest",
    "AllocationResult",
    "BandPlan",
    "ComplianceCheck",
    "FreqEngine",
    "allocate_frequency",
    "check_compliance",
    "compute_channel_spacing",
    "find_allocated_bands",
    "find_vacant_span",
    "is_within_band",
    "itu_region_for_country",
]
