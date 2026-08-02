"""Radio-domain contracts for frequency allocation, modulation schemes,
signal strength measurements, and spectrum analysis.

Schema version: 1.0

Contracts:
    FrequencyAllocation  — band allocation (country, ITU region, privileges)
    ModulationScheme     — modulation mode properties
    SignalStrength       — RSSI, SNR, noise floor, quality rating
    SpectrumBand         — frequency range with label
    SpectrumPeak         — detected peak (freq, power, bandwidth, modulation guess)
    SpectrumAnalysis     — full spectrum sweep with peaks, bands, and metadata

Validation is fail-closed: every .from_dict() and __post_init__
raises ValueError on invalid data. All contracts serialize/deserialize via
to_dict() / from_dict() for JSON-safe transport.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any

SCHEMA_VERSION = "1.0"


def validate_contracts_schema_version(version: str) -> bool:
    if not isinstance(version, str):
        raise TypeError(f"schema version must be a str, got {type(version)}")
    return version == SCHEMA_VERSION


# =============================================================================
# FrequencyAllocation
# =============================================================================


@dataclasses.dataclass
class FrequencyAllocation:
    """A frequency band allocation for a country and ITU region.

    Args:
        band_name: Human-readable band name (e.g. "20m", "2m").
        start_hz: Lower edge of the allocation in Hz.
        end_hz: Upper edge of the allocation in Hz (inclusive).
        country: ISO 3166-1 alpha-2 country code.
        itu_region: ITU region (1, 2, or 3).
        service: Radio service (default "amateur").
        license_class: License class required (e.g. "General", "Extra").
        privileges: Permitted modes of operation.
        max_power_w: Maximum power in watts.
        notes: Free-form notes.
    """

    band_name: str
    start_hz: int
    end_hz: int
    country: str
    itu_region: int
    service: str = "amateur"
    license_class: str | None = None
    privileges: list[str] = dataclasses.field(default_factory=list)
    max_power_w: int | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if self.start_hz < 0:
            raise ValueError(f"start_hz must be non-negative, got {self.start_hz}")
        if self.end_hz < 0:
            raise ValueError(f"end_hz must be non-negative, got {self.end_hz}")
        if self.start_hz >= self.end_hz:
            raise ValueError(f"start_hz ({self.start_hz}) must be less than end_hz ({self.end_hz})")
        if self.itu_region not in (1, 2, 3):
            raise ValueError(f"itu_region must be 1, 2, or 3, got {self.itu_region}")

    @property
    def bandwidth_hz(self) -> int:
        return self.end_hz - self.start_hz

    @property
    def center_freq_hz(self) -> int:
        return int((self.start_hz + self.end_hz) / 2)

    @property
    def display(self) -> str:
        start_mhz = self.start_hz / 1_000_000
        end_mhz = self.end_hz / 1_000_000
        return f"{self.band_name} ({start_mhz:.1f}-{end_mhz:.1f} MHz) — {self.country} Region {self.itu_region}"

    def contains_freq(self, freq_hz: int) -> bool:
        return self.start_hz <= freq_hz <= self.end_hz

    def to_dict(self) -> dict[str, Any]:
        return {
            "band_name": self.band_name,
            "start_hz": self.start_hz,
            "end_hz": self.end_hz,
            "bandwidth_hz": self.bandwidth_hz,
            "center_freq_hz": self.center_freq_hz,
            "country": self.country,
            "itu_region": self.itu_region,
            "service": self.service,
            "license_class": self.license_class,
            "privileges": list(self.privileges),
            "max_power_w": self.max_power_w,
            "notes": self.notes,
            "display": self.display,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FrequencyAllocation:
        required = {"band_name", "start_hz", "end_hz", "country", "itu_region"}
        missing = required - set(d.keys())
        if missing:
            raise ValueError(f"FrequencyAllocation.from_dict missing required keys: {missing}")
        return cls(
            band_name=d["band_name"],
            start_hz=d["start_hz"],
            end_hz=d["end_hz"],
            country=d["country"],
            itu_region=d["itu_region"],
            service=d.get("service", "amateur"),
            license_class=d.get("license_class"),
            privileges=list(d.get("privileges", [])),
            max_power_w=d.get("max_power_w"),
            notes=d.get("notes"),
        )


# =============================================================================
# ModulationScheme
# =============================================================================


@dataclasses.dataclass
class ModulationScheme:
    """Properties of a radio modulation mode.

    Args:
        scheme: Modulation name (e.g. "AM", "QPSK", "OFDM").
        category: "analog" or "digital".
        bandwidth_hz_typical: Typical occupied bandwidth in Hz.
        symbol_rate_baud_min: Minimum symbol rate in baud (digital only).
        symbol_rate_baud_max: Maximum symbol rate in baud (digital only).
        bandwidth_hz_min: Minimum occupied bandwidth in Hz.
        bandwidth_hz_max: Maximum occupied bandwidth in Hz.
        spectrum_shape: Descriptor of the spectral shape.
        spectral_efficiency_bps_hz: Bits per second per Hz.
        typical_use: Common applications.
    """

    scheme: str
    category: str
    bandwidth_hz_typical: int
    symbol_rate_baud_min: int | None = None
    symbol_rate_baud_max: int | None = None
    bandwidth_hz_min: int | None = None
    bandwidth_hz_max: int | None = None
    spectrum_shape: str = "unknown"
    spectral_efficiency_bps_hz: float | None = None
    typical_use: str = ""

    def __post_init__(self) -> None:
        if self.category not in ("analog", "digital"):
            raise ValueError(f"category must be 'analog' or 'digital', got {self.category!r}")
        if self.bandwidth_hz_typical <= 0:
            raise ValueError(f"bandwidth_hz_typical must be positive, got {self.bandwidth_hz_typical}")
        if (
            self.symbol_rate_baud_min is not None
            and self.symbol_rate_baud_max is not None
            and self.symbol_rate_baud_min > self.symbol_rate_baud_max
        ):
            raise ValueError(
                f"symbol_rate_baud_min ({self.symbol_rate_baud_min}) "
                f"must be <= symbol_rate_baud_max ({self.symbol_rate_baud_max})"
            )
        if (
            self.bandwidth_hz_min is not None
            and self.bandwidth_hz_max is not None
            and self.bandwidth_hz_min > self.bandwidth_hz_max
        ):
            raise ValueError(
                f"bandwidth_hz_min ({self.bandwidth_hz_min}) must be <= bandwidth_hz_max ({self.bandwidth_hz_max})"
            )

    @property
    def is_digital(self) -> bool:
        return self.category == "digital"

    @property
    def bandwidth_range(self) -> tuple[int | None, int | None]:
        return (self.bandwidth_hz_min, self.bandwidth_hz_max)

    @property
    def symbol_rate_range(self) -> tuple[int | None, int | None]:
        return (self.symbol_rate_baud_min, self.symbol_rate_baud_max)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme,
            "category": self.category,
            "is_digital": self.is_digital,
            "bandwidth_hz_typical": self.bandwidth_hz_typical,
            "symbol_rate_baud_min": self.symbol_rate_baud_min,
            "symbol_rate_baud_max": self.symbol_rate_baud_max,
            "bandwidth_hz_min": self.bandwidth_hz_min,
            "bandwidth_hz_max": self.bandwidth_hz_max,
            "spectrum_shape": self.spectrum_shape,
            "spectral_efficiency_bps_hz": self.spectral_efficiency_bps_hz,
            "typical_use": self.typical_use,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ModulationScheme:
        required = {"scheme", "category", "bandwidth_hz_typical"}
        missing = required - set(d.keys())
        if missing:
            raise ValueError(f"ModulationScheme.from_dict missing required keys: {missing}")
        return cls(
            scheme=d["scheme"],
            category=d["category"],
            bandwidth_hz_typical=d["bandwidth_hz_typical"],
            symbol_rate_baud_min=d.get("symbol_rate_baud_min"),
            symbol_rate_baud_max=d.get("symbol_rate_baud_max"),
            bandwidth_hz_min=d.get("bandwidth_hz_min"),
            bandwidth_hz_max=d.get("bandwidth_hz_max"),
            spectrum_shape=d.get("spectrum_shape", "unknown"),
            spectral_efficiency_bps_hz=d.get("spectral_efficiency_bps_hz"),
            typical_use=d.get("typical_use", ""),
        )


# =============================================================================
# SignalStrength
# =============================================================================


@dataclasses.dataclass
class SignalStrength:
    """Received signal strength measurement.

    Args:
        rssi_dbm: Received Signal Strength Indicator in dBm (<= 0).
        noise_floor_dbm: Noise floor in dBm (<= 0).
        signal_db: Explicit signal-to-noise ratio if known.
            Computed as rssi_dbm - noise_floor_dbm if omitted.
        timestamp: Unix epoch seconds when the measurement was taken.
    """

    rssi_dbm: float
    noise_floor_dbm: float
    signal_db: float | None = None
    timestamp: float | None = None

    _RSSI_MIN: float = -200.0

    def __post_init__(self) -> None:
        if self.rssi_dbm > 0:
            raise ValueError(f"rssi_dbm must be <= 0, got {self.rssi_dbm}")
        if self.rssi_dbm < self._RSSI_MIN:
            raise ValueError(f"rssi_dbm must be >= {self._RSSI_MIN} (RSSI floor), got {self.rssi_dbm}")
        if self.noise_floor_dbm > 0:
            raise ValueError(f"noise_floor_dbm must be <= 0, got {self.noise_floor_dbm}")
        if self.signal_db is not None and self.signal_db < 0:
            raise ValueError(f"signal_db must be >= 0, got {self.signal_db}")

    @property
    def snr_db(self) -> float:
        if self.signal_db is not None:
            return self.signal_db
        return self.rssi_dbm - self.noise_floor_dbm

    @property
    def quality_rating(self) -> str:
        snr = self.snr_db
        if snr >= 30:
            return "excellent"
        if snr >= 20:
            return "good"
        if snr >= 10:
            return "fair"
        return "poor"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rssi_dbm": self.rssi_dbm,
            "noise_floor_dbm": self.noise_floor_dbm,
            "signal_db": self.signal_db,
            "snr_db": self.snr_db,
            "quality_rating": self.quality_rating,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SignalStrength:
        required = {"rssi_dbm", "noise_floor_dbm"}
        missing = required - set(d.keys())
        if missing:
            raise ValueError(f"SignalStrength.from_dict missing required keys: {missing}")
        return cls(
            rssi_dbm=d["rssi_dbm"],
            noise_floor_dbm=d["noise_floor_dbm"],
            signal_db=d.get("signal_db"),
            timestamp=d.get("timestamp"),
        )


# =============================================================================
# SpectrumBand
# =============================================================================


@dataclasses.dataclass
class SpectrumBand:
    """A labelled frequency range within a spectrum scan.

    Args:
        start_hz: Lower edge in Hz.
        end_hz: Upper edge in Hz (inclusive).
        label: Human-readable label for the band.
    """

    start_hz: int
    end_hz: int
    label: str

    def __post_init__(self) -> None:
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
        return int((self.start_hz + self.end_hz) // 2)

    def contains(self, freq_hz: int) -> bool:
        return self.start_hz <= freq_hz <= self.end_hz

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_hz": self.start_hz,
            "end_hz": self.end_hz,
            "bandwidth_hz": self.bandwidth_hz,
            "center_freq_hz": self.center_freq_hz,
            "label": self.label,
        }


# =============================================================================
# SpectrumPeak
# =============================================================================


@dataclasses.dataclass
class SpectrumPeak:
    """A detected peak in a spectrum scan.

    Args:
        freq_hz: Center frequency in Hz.
        power_dbm: Peak power in dBm (<= 0).
        bandwidth_hz: Estimated occupied bandwidth in Hz.
        snr_db: Signal-to-noise ratio at the peak.
        modulation_guess: Best-guess modulation type.
    """

    freq_hz: int
    power_dbm: float
    bandwidth_hz: int
    snr_db: float | None = None
    modulation_guess: str | None = None

    def __post_init__(self) -> None:
        if self.freq_hz < 0:
            raise ValueError(f"freq_hz must be non-negative, got {self.freq_hz}")
        if self.power_dbm > 0:
            raise ValueError(f"power_dbm must be <= 0, got {self.power_dbm}")
        if self.bandwidth_hz <= 0:
            raise ValueError(f"bandwidth_hz must be positive, got {self.bandwidth_hz}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "freq_hz": self.freq_hz,
            "power_dbm": self.power_dbm,
            "bandwidth_hz": self.bandwidth_hz,
            "snr_db": self.snr_db,
            "modulation_guess": self.modulation_guess,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SpectrumPeak:
        required = {"freq_hz", "power_dbm", "bandwidth_hz"}
        missing = required - set(d.keys())
        if missing:
            raise ValueError(f"SpectrumPeak.from_dict missing required keys: {missing}")
        return cls(
            freq_hz=d["freq_hz"],
            power_dbm=d["power_dbm"],
            bandwidth_hz=d["bandwidth_hz"],
            snr_db=d.get("snr_db"),
            modulation_guess=d.get("modulation_guess"),
        )


# =============================================================================
# SpectrumAnalysis
# =============================================================================


@dataclasses.dataclass
class SpectrumAnalysis:
    """Complete spectrum sweep with peaks, bands, and metadata.

    Args:
        freq_start_hz: Lower bound of the sweep in Hz.
        freq_end_hz: Upper bound of the sweep in Hz.
        resolution_bin_hz: Bin width in Hz.
        peaks: Detected peaks in the sweep.
        bands: Labelled bands overlapping the sweep.
        noise_floor_dbm: Measured or estimated noise floor in dBm.
        scan_timestamp: Unix epoch when the sweep was captured.
    """

    freq_start_hz: int
    freq_end_hz: int
    resolution_bin_hz: int
    peaks: list[SpectrumPeak] = dataclasses.field(default_factory=list)
    bands: list[SpectrumBand] = dataclasses.field(default_factory=list)
    noise_floor_dbm: float | None = None
    scan_timestamp: float | None = None

    def __post_init__(self) -> None:
        if self.freq_start_hz < 0:
            raise ValueError(f"freq_start_hz must be non-negative, got {self.freq_start_hz}")
        if self.freq_end_hz < 0:
            raise ValueError(f"freq_end_hz must be non-negative, got {self.freq_end_hz}")
        if self.freq_start_hz >= self.freq_end_hz:
            raise ValueError(f"freq_start_hz ({self.freq_start_hz}) must be less than freq_end_hz ({self.freq_end_hz})")
        if self.resolution_bin_hz <= 0:
            raise ValueError(f"resolution_bin_hz must be positive, got {self.resolution_bin_hz}")
        span = self.freq_end_hz - self.freq_start_hz
        if self.resolution_bin_hz > span:
            raise ValueError(
                f"resolution_bin_hz ({self.resolution_bin_hz}) must be less than or equal to the span ({span})"
            )

    @property
    def bandwidth_hz(self) -> int:
        return self.freq_end_hz - self.freq_start_hz

    @property
    def center_freq_hz(self) -> int:
        return int((self.freq_start_hz + self.freq_end_hz) // 2)

    @property
    def num_bins(self) -> int:
        return math.ceil(self.bandwidth_hz / self.resolution_bin_hz)

    def add_peak(
        self,
        freq_hz: int,
        power_dbm: float,
        bandwidth_hz: int,
        snr_db: float | None = None,
        modulation_guess: str | None = None,
    ) -> SpectrumPeak:
        peak = SpectrumPeak(
            freq_hz=freq_hz,
            power_dbm=power_dbm,
            bandwidth_hz=bandwidth_hz,
            snr_db=snr_db,
            modulation_guess=modulation_guess,
        )
        self.peaks.append(peak)
        return peak

    def add_band(self, start_hz: int, end_hz: int, label: str) -> SpectrumBand:
        band = SpectrumBand(start_hz=start_hz, end_hz=end_hz, label=label)
        self.bands.append(band)
        return band

    def peaks_above_threshold(self, threshold_dbm: float) -> list[SpectrumPeak]:
        return [p for p in self.peaks if p.power_dbm >= threshold_dbm]

    def bands_containing(self, freq_hz: int) -> list[SpectrumBand]:
        return [b for b in self.bands if b.contains(freq_hz)]

    def summary(self) -> dict[str, Any]:
        return {
            "num_peaks": len(self.peaks),
            "num_bands": len(self.bands),
            "bandwidth_hz": self.bandwidth_hz,
            "center_freq_hz": self.center_freq_hz,
            "num_bins": self.num_bins,
            "resolution_bin_hz": self.resolution_bin_hz,
            "noise_floor_dbm": self.noise_floor_dbm,
            "scan_timestamp": self.scan_timestamp,
            "peak_freqs": [p.freq_hz for p in self.peaks],
            "strongest_peak_dbm": max((p.power_dbm for p in self.peaks), default=None),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "freq_start_hz": self.freq_start_hz,
            "freq_end_hz": self.freq_end_hz,
            "resolution_bin_hz": self.resolution_bin_hz,
            "peaks": [p.to_dict() for p in self.peaks],
            "bands": [b.to_dict() for b in self.bands],
            "noise_floor_dbm": self.noise_floor_dbm,
            "scan_timestamp": self.scan_timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SpectrumAnalysis:
        required = {"freq_start_hz", "freq_end_hz", "resolution_bin_hz"}
        missing = required - set(d.keys())
        if missing:
            raise ValueError(f"SpectrumAnalysis.from_dict missing required keys: {missing}")
        return cls(
            freq_start_hz=d["freq_start_hz"],
            freq_end_hz=d["freq_end_hz"],
            resolution_bin_hz=d["resolution_bin_hz"],
            peaks=[SpectrumPeak.from_dict(p) for p in d.get("peaks", [])],
            bands=[
                SpectrumBand(start_hz=b["start_hz"], end_hz=b["end_hz"], label=b.get("label", ""))
                for b in d.get("bands", [])
            ],
            noise_floor_dbm=d.get("noise_floor_dbm"),
            scan_timestamp=d.get("scan_timestamp"),
        )


# =============================================================================
# __all__
# =============================================================================


__all__ = [
    "SCHEMA_VERSION",
    "FrequencyAllocation",
    "ModulationScheme",
    "SignalStrength",
    "SpectrumAnalysis",
    "SpectrumBand",
    "SpectrumPeak",
    "validate_contracts_schema_version",
]
