"""
frequency_allocations -- Country -> band -> allocation data.

Data shape:
    {
        "country": str,
        "bands": {
            "band_name": {
                "start_hz": int,
                "end_hz": int,
                "service": str,
                "license_class": str | None,
                "privileges": list[str],
                "notes": str | None,
            },
        },
    }

Additional data structures:
    - ITU Region 2 HF bands
    - Marine VHF channel plan (channels 1-88)

Functions:
    lookup_frequency(freq_mhz, country) -> allocation info
    get_band_plan(band_name, country) -> band limits + privileges
"""

from __future__ import annotations

from typing import Any

# ── US FCC Part 97 Amateur Bands ──
US_AMATEUR_BANDS: dict[str, dict[str, Any]] = {
    "160m": {
        "start_hz": 1_800_000,
        "end_hz": 2_000_000,
        "display": "160 meters (1.800-2.000 MHz)",
        "technician": {"privileges": ["CW only 1.800-2.000 MHz"], "max_power_w": 200},
        "general": {"privileges": ["CW, RTTY, Data 1.800-2.000 MHz", "Phone/image 1.800-2.000 MHz"], "max_power_w": 1500},
        "extra": {"privileges": ["All modes 1.800-2.000 MHz"], "max_power_w": 1500},
    },
    "80m": {
        "start_hz": 3_500_000,
        "end_hz": 4_000_000,
        "display": "80/75 meters (3.500-4.000 MHz)",
        "technician": {"privileges": ["CW only 3.525-3.600 MHz"], "max_power_w": 200},
        "general": {"privileges": ["CW, RTTY, Data 3.525-3.600 MHz", "Phone/image 3.800-4.000 MHz"], "max_power_w": 1500},
        "extra": {"privileges": ["All modes 3.500-4.000 MHz"], "max_power_w": 1500},
    },
    "60m": {
        "start_hz": 5_330_500,
        "end_hz": 5_403_500,
        "display": "60 meters (5.332, 5.348, 5.3585, 5.373, 5.405 MHz -- channels)",
        "technician": {"privileges": ["CW, USB 5 channels at ERP 100W PEP"], "max_power_w": 100},
        "general": {"privileges": ["CW, USB 5 channels at ERP 100W PEP"], "max_power_w": 100},
        "extra": {"privileges": ["CW, USB 5 channels at ERP 100W PEP"], "max_power_w": 100},
    },
    "40m": {
        "start_hz": 7_000_000,
        "end_hz": 7_300_000,
        "display": "40 meters (7.000-7.300 MHz)",
        "technician": {"privileges": ["CW only 7.025-7.125 MHz"], "max_power_w": 200},
        "general": {"privileges": ["CW, RTTY, Data 7.025-7.125 MHz", "Phone/image 7.175-7.300 MHz"], "max_power_w": 1500},
        "extra": {"privileges": ["All modes 7.000-7.300 MHz"], "max_power_w": 1500},
    },
    "30m": {
        "start_hz": 10_100_000,
        "end_hz": 10_150_000,
        "display": "30 meters (10.100-10.150 MHz)",
        "technician": {"privileges": ["No privileges"], "max_power_w": 0},
        "general": {"privileges": ["CW, RTTY, Data 10.100-10.150 MHz (no phone)"], "max_power_w": 200},
        "extra": {"privileges": ["CW, RTTY, Data 10.100-10.150 MHz (no phone)"], "max_power_w": 1500},
    },
    "20m": {
        "start_hz": 14_000_000,
        "end_hz": 14_350_000,
        "display": "20 meters (14.000-14.350 MHz)",
        "technician": {"privileges": ["No privileges"], "max_power_w": 0},
        "general": {"privileges": ["CW, RTTY, Data 14.025-14.150 MHz", "Phone/image 14.225-14.350 MHz"], "max_power_w": 1500},
        "extra": {"privileges": ["All modes 14.000-14.350 MHz"], "max_power_w": 1500},
    },
    "17m": {
        "start_hz": 18_068_000,
        "end_hz": 18_168_000,
        "display": "17 meters (18.068-18.168 MHz)",
        "technician": {"privileges": ["No privileges"], "max_power_w": 0},
        "general": {"privileges": ["CW, RTTY, Data 18.068-18.110 MHz", "Phone/image 18.110-18.168 MHz"], "max_power_w": 1500},
        "extra": {"privileges": ["All modes 18.068-18.168 MHz"], "max_power_w": 1500},
    },
    "15m": {
        "start_hz": 21_000_000,
        "end_hz": 21_450_000,
        "display": "15 meters (21.000-21.450 MHz)",
        "technician": {"privileges": ["CW only 21.025-21.200 MHz"], "max_power_w": 200},
        "general": {"privileges": ["CW, RTTY, Data 21.025-21.200 MHz", "Phone/image 21.275-21.450 MHz"], "max_power_w": 1500},
        "extra": {"privileges": ["All modes 21.000-21.450 MHz"], "max_power_w": 1500},
    },
    "12m": {
        "start_hz": 24_890_000,
        "end_hz": 24_990_000,
        "display": "12 meters (24.890-24.990 MHz)",
        "technician": {"privileges": ["No privileges"], "max_power_w": 0},
        "general": {"privileges": ["CW, RTTY, Data 24.890-24.930 MHz", "Phone/image 24.930-24.990 MHz"], "max_power_w": 1500},
        "extra": {"privileges": ["All modes 24.890-24.990 MHz"], "max_power_w": 1500},
    },
    "10m": {
        "start_hz": 28_000_000,
        "end_hz": 29_700_000,
        "display": "10 meters (28.000-29.700 MHz)",
        "technician": {"privileges": ["CW, RTTY, Data 28.000-28.300 MHz", "Phone/image 28.300-28.500 MHz", "CW/SSB on 28.300-28.500 MHz too"], "max_power_w": 200},
        "general": {"privileges": ["All modes 28.000-29.700 MHz"], "max_power_w": 1500},
        "extra": {"privileges": ["All modes 28.000-29.700 MHz"], "max_power_w": 1500},
    },
    "6m": {
        "start_hz": 50_000_000,
        "end_hz": 54_000_000,
        "display": "6 meters (50-54 MHz)",
        "technician": {"privileges": ["All modes 50.000-54.000 MHz"], "max_power_w": 1500},
        "general": {"privileges": ["All modes 50.000-54.000 MHz"], "max_power_w": 1500},
        "extra": {"privileges": ["All modes 50.000-54.000 MHz"], "max_power_w": 1500},
    },
    "2m": {
        "start_hz": 144_000_000,
        "end_hz": 148_000_000,
        "display": "2 meters (144-148 MHz)",
        "technician": {"privileges": ["All modes 144.000-148.000 MHz"], "max_power_w": 1500},
        "general": {"privileges": ["All modes 144.000-148.000 MHz"], "max_power_w": 1500},
        "extra": {"privileges": ["All modes 144.000-148.000 MHz"], "max_power_w": 1500},
    },
    "1.25m": {
        "start_hz": 222_000_000,
        "end_hz": 225_000_000,
        "display": "1.25 meters (222-225 MHz)",
        "technician": {"privileges": ["All modes 222.000-225.000 MHz"], "max_power_w": 1500},
        "general": {"privileges": ["All modes 222.000-225.000 MHz"], "max_power_w": 1500},
        "extra": {"privileges": ["All modes 222.000-225.000 MHz"], "max_power_w": 1500},
    },
    "70cm": {
        "start_hz": 420_000_000,
        "end_hz": 450_000_000,
        "display": "70 centimeters (420-450 MHz)",
        "technician": {"privileges": ["All modes 420.000-450.000 MHz"], "max_power_w": 1500},
        "general": {"privileges": ["All modes 420.000-450.000 MHz"], "max_power_w": 1500},
        "extra": {"privileges": ["All modes 420.000-450.000 MHz"], "max_power_w": 1500},
    },
    "33cm": {
        "start_hz": 902_000_000,
        "end_hz": 928_000_000,
        "display": "33 centimeters (902-928 MHz)",
        "technician": {"privileges": ["All modes 902.000-928.000 MHz -- shared with ISM"], "max_power_w": 1500},
        "general": {"privileges": ["All modes 902.000-928.000 MHz -- shared with ISM"], "max_power_w": 1500},
        "extra": {"privileges": ["All modes 902.000-928.000 MHz -- shared with ISM"], "max_power_w": 1500},
    },
    "23cm": {
        "start_hz": 1_240_000_000,
        "end_hz": 1_300_000_000,
        "display": "23 centimeters (1240-1300 MHz)",
        "technician": {"privileges": ["All modes 1240-1300 MHz"], "max_power_w": 1500},
        "general": {"privileges": ["All modes 1240-1300 MHz"], "max_power_w": 1500},
        "extra": {"privileges": ["All modes 1240-1300 MHz"], "max_power_w": 1500},
    },
}

# ── Canadian Amateur Bands (ISED RBR-4) ──
CA_AMATEUR_BANDS: dict[str, dict[str, Any]] = {
    "160m": {
        "start_hz": 1_800_000, "end_hz": 2_000_000,
        "display": "160 meters (1.800-2.000 MHz)",
        "technician": {"privileges": ["All modes 1.800-2.000 MHz"], "max_power_w": 250},
        "general": {"privileges": ["All modes 1.800-2.000 MHz"], "max_power_w": 1000},
        "extra": {"privileges": ["All modes 1.800-2.000 MHz"], "max_power_w": 1000},
    },
    "80m": {
        "start_hz": 3_500_000, "end_hz": 4_000_000,
        "display": "80 meters (3.500-4.000 MHz)",
        "technician": {"privileges": ["All modes 3.500-4.000 MHz"], "max_power_w": 250},
        "general": {"privileges": ["All modes 3.500-4.000 MHz"], "max_power_w": 1000},
        "extra": {"privileges": ["All modes 3.500-4.000 MHz"], "max_power_w": 1000},
    },
    "40m": {
        "start_hz": 7_000_000, "end_hz": 7_300_000,
        "display": "40 meters (7.000-7.300 MHz)",
        "technician": {"privileges": ["All modes 7.000-7.300 MHz"], "max_power_w": 250},
        "general": {"privileges": ["All modes 7.000-7.300 MHz"], "max_power_w": 1000},
        "extra": {"privileges": ["All modes 7.000-7.300 MHz"], "max_power_w": 1000},
    },
    "20m": {
        "start_hz": 14_000_000, "end_hz": 14_350_000,
        "display": "20 meters (14.000-14.350 MHz)",
        "technician": {"privileges": ["All modes 14.000-14.350 MHz"], "max_power_w": 250},
        "general": {"privileges": ["All modes 14.000-14.350 MHz"], "max_power_w": 1000},
        "extra": {"privileges": ["All modes 14.000-14.350 MHz"], "max_power_w": 1000},
    },
    "15m": {
        "start_hz": 21_000_000, "end_hz": 21_450_000,
        "display": "15 meters (21.000-21.450 MHz)",
        "technician": {"privileges": ["All modes 21.000-21.450 MHz"], "max_power_w": 250},
        "general": {"privileges": ["All modes 21.000-21.450 MHz"], "max_power_w": 1000},
        "extra": {"privileges": ["All modes 21.000-21.450 MHz"], "max_power_w": 1000},
    },
    "10m": {
        "start_hz": 28_000_000, "end_hz": 29_700_000,
        "display": "10 meters (28.000-29.700 MHz)",
        "technician": {"privileges": ["All modes 28.000-29.700 MHz"], "max_power_w": 250},
        "general": {"privileges": ["All modes 28.000-29.700 MHz"], "max_power_w": 1000},
        "extra": {"privileges": ["All modes 28.000-29.700 MHz"], "max_power_w": 1000},
    },
    "6m": {
        "start_hz": 50_000_000, "end_hz": 54_000_000,
        "display": "6 meters (50-54 MHz)",
        "technician": {"privileges": ["All modes 50.000-54.000 MHz"], "max_power_w": 250},
        "general": {"privileges": ["All modes 50.000-54.000 MHz"], "max_power_w": 1000},
        "extra": {"privileges": ["All modes 50.000-54.000 MHz"], "max_power_w": 1000},
    },
    "2m": {
        "start_hz": 144_000_000, "end_hz": 148_000_000,
        "display": "2 meters (144-148 MHz)",
        "technician": {"privileges": ["All modes 144.000-148.000 MHz"], "max_power_w": 250},
        "general": {"privileges": ["All modes 144.000-148.000 MHz"], "max_power_w": 1000},
        "extra": {"privileges": ["All modes 144.000-148.000 MHz"], "max_power_w": 1000},
    },
    "70cm": {
        "start_hz": 430_000_000, "end_hz": 450_000_000,
        "display": "70 centimeters (430-450 MHz)",
        "technician": {"privileges": ["All modes 430.000-450.000 MHz"], "max_power_w": 250},
        "general": {"privileges": ["All modes 430.000-450.000 MHz"], "max_power_w": 1000},
        "extra": {"privileges": ["All modes 430.000-450.000 MHz"], "max_power_w": 1000},
    },
}

# ── ITU Region 2 (Americas) HF Amateur Allocations ──
ITU_R2_BANDS: list[dict[str, Any]] = [
    {"band": "2200m", "start_hz": 135_700, "end_hz": 137_800, "notes": "WRC-12; secondary allocation; max EIRP 1W. Shared."},
    {"band": "630m", "start_hz": 472_000, "end_hz": 479_000, "notes": "WRC-12; secondary allocation; max EIRP 5W. Shared."},
    {"band": "160m", "start_hz": 1_800_000, "end_hz": 2_000_000, "notes": "Primary allocation in Region 2."},
    {"band": "80m", "start_hz": 3_500_000, "end_hz": 4_000_000, "notes": "Primary. Region 2: 3.500-3.750 shared; 3.750-4.000 exclusive amateur."},
    {"band": "60m", "start_hz": 5_351_500, "end_hz": 5_366_500, "notes": "WRC-15 secondary; 15 kHz segment; 15W EIRP max."},
    {"band": "40m", "start_hz": 7_000_000, "end_hz": 7_300_000, "notes": "Primary allocation in Region 2."},
    {"band": "30m", "start_hz": 10_100_000, "end_hz": 10_150_000, "notes": "Secondary allocation worldwide. CW/RTTY/Data only."},
    {"band": "20m", "start_hz": 14_000_000, "end_hz": 14_350_000, "notes": "Primary allocation worldwide."},
    {"band": "17m", "start_hz": 18_068_000, "end_hz": 18_168_000, "notes": "Primary allocation worldwide."},
    {"band": "15m", "start_hz": 21_000_000, "end_hz": 21_450_000, "notes": "Primary allocation worldwide."},
    {"band": "12m", "start_hz": 24_890_000, "end_hz": 24_990_000, "notes": "Primary allocation worldwide."},
    {"band": "10m", "start_hz": 28_000_000, "end_hz": 29_700_000, "notes": "Primary allocation worldwide."},
    {"band": "6m", "start_hz": 50_000_000, "end_hz": 54_000_000, "notes": "Primary in Region 2."},
]

# ── Marine VHF Channel Plan (International) ──
MARINE_VHF_CHANNELS: list[dict[str, Any]] = [
    {"channel": 1, "tx_mhz": 156.050, "rx_mhz": 160.650, "simplex": False, "use": "Port Operations, Ship Movement"},
    {"channel": 2, "tx_mhz": 156.100, "rx_mhz": 160.700, "simplex": False, "use": "Port Operations"},
    {"channel": 3, "tx_mhz": 156.150, "rx_mhz": 160.750, "simplex": False, "use": "Port Operations"},
    {"channel": 4, "tx_mhz": 156.200, "rx_mhz": 160.800, "simplex": False, "use": "Port Operations"},
    {"channel": 5, "tx_mhz": 156.250, "rx_mhz": 160.850, "simplex": False, "use": "Port Operations"},
    {"channel": 6, "tx_mhz": 156.300, "rx_mhz": 156.300, "simplex": True, "use": "Intership Safety; SAR coordination"},
    {"channel": 7, "tx_mhz": 156.350, "rx_mhz": 160.950, "simplex": False, "use": "Port Operations"},
    {"channel": 8, "tx_mhz": 156.400, "rx_mhz": 156.400, "simplex": True, "use": "Intership Communication (commercial)"},
    {"channel": 9, "tx_mhz": 156.450, "rx_mhz": 156.450, "simplex": True, "use": "Boaters calling channel; bridge-to-bridge"},
    {"channel": 10, "tx_mhz": 156.500, "rx_mhz": 156.500, "simplex": True, "use": "Port Operations; SAR"},
    {"channel": 11, "tx_mhz": 156.550, "rx_mhz": 156.550, "simplex": True, "use": "Port Operations"},
    {"channel": 12, "tx_mhz": 156.600, "rx_mhz": 156.600, "simplex": True, "use": "Port Operations; VTS"},
    {"channel": 13, "tx_mhz": 156.650, "rx_mhz": 156.650, "simplex": True, "use": "Bridge-to-bridge navigation safety; 1W max"},
    {"channel": 14, "tx_mhz": 156.700, "rx_mhz": 156.700, "simplex": True, "use": "Port Operations; VTS"},
    {"channel": 15, "tx_mhz": 156.750, "rx_mhz": 156.750, "simplex": True, "use": "Port Operations; on-board 1W only"},
    {"channel": 16, "tx_mhz": 156.800, "rx_mhz": 156.800, "simplex": True, "use": "DISTRESS, SAFETY, and CALLING"},
    {"channel": 17, "tx_mhz": 156.850, "rx_mhz": 156.850, "simplex": True, "use": "Port Operations; on-board 1W only"},
    {"channel": 18, "tx_mhz": 156.900, "rx_mhz": 161.500, "simplex": False, "use": "Port Operations"},
    {"channel": 19, "tx_mhz": 156.950, "rx_mhz": 161.550, "simplex": False, "use": "Port Operations"},
    {"channel": 20, "tx_mhz": 157.000, "rx_mhz": 161.600, "simplex": False, "use": "Port Operations"},
    {"channel": 21, "tx_mhz": 157.050, "rx_mhz": 161.650, "simplex": False, "use": "Port Operations"},
    {"channel": 22, "tx_mhz": 157.100, "rx_mhz": 161.700, "simplex": False, "use": "Port Operations; USCG liaison"},
    {"channel": 23, "tx_mhz": 157.150, "rx_mhz": 161.750, "simplex": False, "use": "Port Operations"},
    {"channel": 24, "tx_mhz": 157.200, "rx_mhz": 161.800, "simplex": False, "use": "Public Correspondence (ship-shore)"},
    {"channel": 25, "tx_mhz": 157.250, "rx_mhz": 161.850, "simplex": False, "use": "Public Correspondence (ship-shore)"},
    {"channel": 26, "tx_mhz": 157.300, "rx_mhz": 161.900, "simplex": False, "use": "Public Correspondence (ship-shore)"},
    {"channel": 27, "tx_mhz": 157.350, "rx_mhz": 161.950, "simplex": False, "use": "Public Correspondence (ship-shore)"},
    {"channel": 28, "tx_mhz": 157.400, "rx_mhz": 162.000, "simplex": False, "use": "Public Correspondence (ship-shore)"},
    {"channel": 60, "tx_mhz": 156.025, "rx_mhz": 160.625, "simplex": False, "use": "Port Operations"},
    {"channel": 61, "tx_mhz": 156.075, "rx_mhz": 160.675, "simplex": False, "use": "Port Operations"},
    {"channel": 62, "tx_mhz": 156.125, "rx_mhz": 160.725, "simplex": False, "use": "Port Operations"},
    {"channel": 63, "tx_mhz": 156.175, "rx_mhz": 160.775, "simplex": False, "use": "Port Operations"},
    {"channel": 64, "tx_mhz": 156.225, "rx_mhz": 160.825, "simplex": False, "use": "Port Operations"},
    {"channel": 65, "tx_mhz": 156.275, "rx_mhz": 160.875, "simplex": False, "use": "Port Operations"},
    {"channel": 66, "tx_mhz": 156.325, "rx_mhz": 160.925, "simplex": False, "use": "Port Operations"},
    {"channel": 67, "tx_mhz": 156.375, "rx_mhz": 156.375, "simplex": True, "use": "Intership bridge-to-bridge; 1W"},
    {"channel": 68, "tx_mhz": 156.425, "rx_mhz": 156.425, "simplex": True, "use": "Non-commercial intership; port ops"},
    {"channel": 69, "tx_mhz": 156.475, "rx_mhz": 156.475, "simplex": True, "use": "Non-commercial intership; port ops"},
    {"channel": 70, "tx_mhz": 156.525, "rx_mhz": 156.525, "simplex": True, "use": "DSC (Digital Selective Calling) -- NO VOICE"},
    {"channel": 71, "tx_mhz": 156.575, "rx_mhz": 156.575, "simplex": True, "use": "Non-commercial intership"},
    {"channel": 72, "tx_mhz": 156.625, "rx_mhz": 156.625, "simplex": True, "use": "Non-commercial intership"},
    {"channel": 73, "tx_mhz": 156.675, "rx_mhz": 156.675, "simplex": True, "use": "Port Operations; ship movement"},
    {"channel": 74, "tx_mhz": 156.725, "rx_mhz": 156.725, "simplex": True, "use": "Port Operations"},
    {"channel": 75, "tx_mhz": 156.775, "rx_mhz": 156.775, "simplex": True, "use": "Port Operations; 1W only (guard band)"},
    {"channel": 76, "tx_mhz": 156.825, "rx_mhz": 156.825, "simplex": True, "use": "Port Operations; 1W only (guard band)"},
    {"channel": 77, "tx_mhz": 156.875, "rx_mhz": 156.875, "simplex": True, "use": "Intership; port operations"},
    {"channel": 78, "tx_mhz": 156.925, "rx_mhz": 161.525, "simplex": False, "use": "Port Operations"},
    {"channel": 79, "tx_mhz": 156.975, "rx_mhz": 161.575, "simplex": False, "use": "Port Operations"},
    {"channel": 80, "tx_mhz": 157.025, "rx_mhz": 161.625, "simplex": False, "use": "Port Operations"},
    {"channel": 81, "tx_mhz": 157.075, "rx_mhz": 161.675, "simplex": False, "use": "Port Operations"},
    {"channel": 82, "tx_mhz": 157.125, "rx_mhz": 161.725, "simplex": False, "use": "Port Operations"},
    {"channel": 83, "tx_mhz": 157.175, "rx_mhz": 161.775, "simplex": False, "use": "Port Operations"},
    {"channel": 84, "tx_mhz": 157.225, "rx_mhz": 161.825, "simplex": False, "use": "Public Correspondence"},
    {"channel": 85, "tx_mhz": 157.275, "rx_mhz": 161.875, "simplex": False, "use": "Public Correspondence"},
    {"channel": 86, "tx_mhz": 157.325, "rx_mhz": 161.925, "simplex": False, "use": "Public Correspondence"},
    {"channel": 87, "tx_mhz": 157.375, "rx_mhz": 161.975, "simplex": False, "use": "Public Correspondence"},
    {"channel": 88, "tx_mhz": 157.425, "rx_mhz": 162.025, "simplex": False, "use": "Public Correspondence"},
]

# AIS channels
AIS_CHANNELS: list[dict[str, Any]] = [
    {"channel": "AIS1", "frequency_mhz": 161.975, "use": "Automatic Identification System -- Channel A (87B)"},
    {"channel": "AIS2", "frequency_mhz": 162.025, "use": "Automatic Identification System -- Channel B (88B)"},
]

# Built with "ALLOCATIONS" for backward compat
ALLOCATIONS: list[dict[str, Any]] = [
    {"country": "US", "bands": US_AMATEUR_BANDS},
    {"country": "CA", "bands": CA_AMATEUR_BANDS},
]


def allocations_for(country: str) -> dict[str, dict[str, Any]] | None:
    """Return band allocations for a country, or None if not found."""
    for entry in ALLOCATIONS:
        if entry["country"] == country:
            return entry["bands"]
    return None


def bands_in_range(start_hz: int, end_hz: int, country: str) -> list[dict[str, Any]]:
    """Return bands that overlap [start_hz, end_hz] in a country."""
    bands = allocations_for(country)
    if not bands:
        return []
    result: list[dict[str, Any]] = []
    for band in bands.values():
        if band["start_hz"] <= end_hz and band["end_hz"] >= start_hz:
            result.append(band)
    return result


def lookup_frequency(freq_mhz: float, country: str = "US") -> dict[str, Any] | None:
    """Look up the amateur band allocation containing a given frequency in MHz."""
    freq_hz = int(freq_mhz * 1_000_000)
    bands = allocations_for(country)
    if not bands:
        # Try marine VHF
        for ch in MARINE_VHF_CHANNELS:
            if abs(ch["tx_mhz"] - freq_mhz) < 0.005 or abs(ch["rx_mhz"] - freq_mhz) < 0.005:
                return {
                    "type": "marine_vhf",
                    "channel": ch["channel"],
                    "tx_mhz": ch["tx_mhz"],
                    "rx_mhz": ch["rx_mhz"],
                    "simplex": ch["simplex"],
                    "use": ch["use"],
                }
        return None

    for name, band in bands.items():
        if band["start_hz"] <= freq_hz <= band["end_hz"]:
            result = {
                "type": "amateur",
                "band_name": name,
                "display": band["display"],
                "start_hz": band["start_hz"],
                "end_hz": band["end_hz"],
                "country": country,
            }
            for license_class in ("technician", "general", "extra"):
                if license_class in band:
                    result[license_class] = band[license_class]
            return result

    return None


def get_band_plan(band_name: str, country: str = "US") -> dict[str, Any] | None:
    """Get full band limits and privileges for a named band."""
    bands = allocations_for(country)
    if not bands:
        return None
    if band_name not in bands:
        return None
    return dict(bands[band_name])


def get_marine_channel(channel: int) -> dict[str, Any] | None:
    """Get marine VHF channel details by channel number."""
    for ch in MARINE_VHF_CHANNELS:
        if ch["channel"] == channel:
            return ch
    return None


def get_itu_region2_bands() -> list[dict[str, Any]]:
    """Return ITU Region 2 HF band allocations."""
    return list(ITU_R2_BANDS)


def bands_by_privilege(country: str, license_class: str) -> list[dict[str, Any]]:
    """Return bands for which a license class has privileges in a country."""
    bands = allocations_for(country)
    if not bands:
        return []
    result = []
    for name, band in bands.items():
        if license_class in band and band[license_class].get("max_power_w", 0) > 0:
            result.append({"band_name": name, **band[license_class], "display": band["display"]})
    return result
