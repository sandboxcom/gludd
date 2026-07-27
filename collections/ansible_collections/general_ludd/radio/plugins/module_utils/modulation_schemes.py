"""
modulation_schemes -- Enumeration and properties of common RF modulation modes.

Each entry:
    {
        "scheme": str,
        "category": "analog" | "digital",
        "symbol_rate_baud_min": int | None,
        "symbol_rate_baud_max": int | None,
        "bandwidth_hz_typical": int,
        "bandwidth_hz_min": int | None,
        "bandwidth_hz_max": int | None,
        "spectrum_shape": str,
        "spectral_efficiency_bps_hz": float | None,
        "typical_use": str,
    }

Functions:
    classify_signal(bandwidth, symbol_rate, spectrum_shape) -> list of likely modulations
"""

from __future__ import annotations

from typing import Any

MODULATION_SCHEMES: list[dict[str, Any]] = [
    # ── Analog ──
    {
        "scheme": "AM",
        "category": "analog",
        "symbol_rate_baud_min": None,
        "symbol_rate_baud_max": None,
        "bandwidth_hz_typical": 10_000,
        "bandwidth_hz_min": 6_000,
        "bandwidth_hz_max": 20_000,
        "spectrum_shape": "double_sideband_with_carrier",
        "spectral_efficiency_bps_hz": 0.5,
        "typical_use": "Broadcast radio (MW/SW); legacy amateur AM",
    },
    {
        "scheme": "FM",
        "category": "analog",
        "symbol_rate_baud_min": None,
        "symbol_rate_baud_max": None,
        "bandwidth_hz_typical": 12_500,
        "bandwidth_hz_min": 5_000,
        "bandwidth_hz_max": 25_000,
        "spectrum_shape": "constant_envelope_fm",
        "spectral_efficiency_bps_hz": 0.8,
        "typical_use": "VHF/UHF voice (repeaters, simplex); commercial broadcast FM",
    },
    {
        "scheme": "NBFM",
        "category": "analog",
        "symbol_rate_baud_min": None,
        "symbol_rate_baud_max": None,
        "bandwidth_hz_typical": 6_250,
        "bandwidth_hz_min": 3_000,
        "bandwidth_hz_max": 12_500,
        "spectrum_shape": "constant_envelope_nbfm",
        "spectral_efficiency_bps_hz": None,
        "typical_use": "VHF/UHF amateur voice with 2.5 kHz deviation; business band PMR",
    },
    {
        "scheme": "SSB-USB",
        "category": "analog",
        "symbol_rate_baud_min": None,
        "symbol_rate_baud_max": None,
        "bandwidth_hz_typical": 2_700,
        "bandwidth_hz_min": 2_400,
        "bandwidth_hz_max": 3_000,
        "spectrum_shape": "single_sideband_suppressed_carrier",
        "spectral_efficiency_bps_hz": 1.0,
        "typical_use": "HF voice (14 MHz and above); amateur, maritime, aeronautical",
    },
    {
        "scheme": "SSB-LSB",
        "category": "analog",
        "symbol_rate_baud_min": None,
        "symbol_rate_baud_max": None,
        "bandwidth_hz_typical": 2_700,
        "bandwidth_hz_min": 2_400,
        "bandwidth_hz_max": 3_000,
        "spectrum_shape": "single_sideband_suppressed_carrier",
        "spectral_efficiency_bps_hz": 1.0,
        "typical_use": "HF voice (below 10 MHz); amateur, maritime",
    },
    {
        "scheme": "CW",
        "category": "analog",
        "symbol_rate_baud_min": None,
        "symbol_rate_baud_max": None,
        "bandwidth_hz_typical": 150,
        "bandwidth_hz_min": 50,
        "bandwidth_hz_max": 500,
        "spectrum_shape": "carrier_on_off_keying",
        "spectral_efficiency_bps_hz": None,
        "typical_use": "Morse code communication; amateur HF; beacons; weak-signal DX",
    },
    # ── Digital -- HF narrow-band ──
    {
        "scheme": "RTTY",
        "category": "digital",
        "symbol_rate_baud_min": 45,
        "symbol_rate_baud_max": 100,
        "bandwidth_hz_typical": 250,
        "bandwidth_hz_min": 150,
        "bandwidth_hz_max": 500,
        "spectrum_shape": "two_tone_fsk",
        "spectral_efficiency_bps_hz": 0.2,
        "typical_use": "Radioteletype; amateur HF digital; maritime weather fax",
    },
    {
        "scheme": "PSK31",
        "category": "digital",
        "symbol_rate_baud_min": 31,
        "symbol_rate_baud_max": 31,
        "bandwidth_hz_typical": 62,
        "bandwidth_hz_min": 31,
        "bandwidth_hz_max": 100,
        "spectrum_shape": "binary_phase_shift_keying_narrow",
        "spectral_efficiency_bps_hz": 0.5,
        "typical_use": "Narrow-band amateur HF keyboard-to-keyboard; DXpedition chat",
    },
    {
        "scheme": "PSK63",
        "category": "digital",
        "symbol_rate_baud_min": 63,
        "symbol_rate_baud_max": 63,
        "bandwidth_hz_typical": 125,
        "bandwidth_hz_min": 80,
        "bandwidth_hz_max": 200,
        "spectrum_shape": "binary_phase_shift_keying_narrow",
        "spectral_efficiency_bps_hz": 0.5,
        "typical_use": "Faster PSK variant for HF; twice the speed of PSK31",
    },
    {
        "scheme": "FT8",
        "category": "digital",
        "symbol_rate_baud_min": None,
        "symbol_rate_baud_max": None,
        "bandwidth_hz_typical": 50,
        "bandwidth_hz_min": 50,
        "bandwidth_hz_max": 50,
        "spectrum_shape": "eight_tone_fsk_narrow",
        "spectral_efficiency_bps_hz": 0.3,
        "typical_use": "Weak-signal HF communication; 15-second T/R cycles; WSJT-X mode",
    },
    {
        "scheme": "FT4",
        "category": "digital",
        "symbol_rate_baud_min": None,
        "symbol_rate_baud_max": None,
        "bandwidth_hz_typical": 90,
        "bandwidth_hz_min": 90,
        "bandwidth_hz_max": 90,
        "spectrum_shape": "four_tone_fsk_narrow",
        "spectral_efficiency_bps_hz": 0.3,
        "typical_use": "Rapid-cycling weak-signal HF; 7.5-second T/R cycles; contesting",
    },
    {
        "scheme": "JT65",
        "category": "digital",
        "symbol_rate_baud_min": None,
        "symbol_rate_baud_max": None,
        "bandwidth_hz_typical": 175,
        "bandwidth_hz_min": 175,
        "bandwidth_hz_max": 175,
        "spectrum_shape": "65_tone_mfsk",
        "spectral_efficiency_bps_hz": 0.05,
        "typical_use": "Very weak-signal EME (moon-bounce) and HF; deep decode at -25 dB SNR",
    },
    {
        "scheme": "JT9",
        "category": "digital",
        "symbol_rate_baud_min": None,
        "symbol_rate_baud_max": None,
        "bandwidth_hz_typical": 16,
        "bandwidth_hz_min": 16,
        "bandwidth_hz_max": 16,
        "spectrum_shape": "nine_tone_fsk_ultra_narrow",
        "spectral_efficiency_bps_hz": None,
        "typical_use": "Ultra-narrow weak-signal HF; companion to JT65",
    },
    {
        "scheme": "WSPR",
        "category": "digital",
        "symbol_rate_baud_min": None,
        "symbol_rate_baud_max": None,
        "bandwidth_hz_typical": 6,
        "bandwidth_hz_min": 6,
        "bandwidth_hz_max": 6,
        "spectrum_shape": "four_tone_fsk_ultra_narrow",
        "spectral_efficiency_bps_hz": None,
        "typical_use": "Propagation beacon network; reports SNR/drift/grid to wsprnet.org",
    },
    {
        "scheme": "OLIVIA",
        "category": "digital",
        "symbol_rate_baud_min": 16,
        "symbol_rate_baud_max": 32,
        "bandwidth_hz_typical": 500,
        "bandwidth_hz_min": 125,
        "bandwidth_hz_max": 1_000,
        "spectrum_shape": "mfsk_with_fec",
        "spectral_efficiency_bps_hz": None,
        "typical_use": "HF keyboard-to-keyboard with FEC; good under poor conditions; multi-tone",
    },
    # ── Digital -- VHF/UHF digital voice ──
    {
        "scheme": "D-STAR",
        "category": "digital",
        "symbol_rate_baud_min": 4_800,
        "symbol_rate_baud_max": 128_000,
        "bandwidth_hz_typical": 6_250,
        "bandwidth_hz_min": 6_000,
        "bandwidth_hz_max": 130_000,
        "spectrum_shape": "gmsk_digital_voice",
        "spectral_efficiency_bps_hz": 0.8,
        "typical_use": "Icom digital voice + low-speed data; VHF/UHF amateur; 128 kbps on 1.2 GHz",
    },
    {
        "scheme": "DMR",
        "category": "digital",
        "symbol_rate_baud_min": 4_800,
        "symbol_rate_baud_max": 4_800,
        "bandwidth_hz_typical": 6_250,
        "bandwidth_hz_min": 6_250,
        "bandwidth_hz_max": 12_500,
        "spectrum_shape": "four_level_fsk_tdma",
        "spectral_efficiency_bps_hz": 1.54,
        "typical_use": "Commercial and amateur digital voice; 2-slot TDMA; ETSI standard; Tier I/II/III",
    },
    {
        "scheme": "P25_Phase1",
        "category": "digital",
        "symbol_rate_baud_min": 4_800,
        "symbol_rate_baud_max": 9_600,
        "bandwidth_hz_typical": 12_500,
        "bandwidth_hz_min": 12_500,
        "bandwidth_hz_max": 12_500,
        "spectrum_shape": "c4fm_cqpsk",
        "spectral_efficiency_bps_hz": 0.77,
        "typical_use": "Public safety digital voice (APCO Project 25 Phase 1); VHF/UHF/700/800 MHz",
    },
    {
        "scheme": "P25_Phase2",
        "category": "digital",
        "symbol_rate_baud_min": 6_000,
        "symbol_rate_baud_max": 6_000,
        "bandwidth_hz_typical": 12_500,
        "bandwidth_hz_min": 12_500,
        "bandwidth_hz_max": 12_500,
        "spectrum_shape": "hdqpsk_tdma",
        "spectral_efficiency_bps_hz": 1.54,
        "typical_use": "Public safety digital voice; 2-slot TDMA; improved spectral efficiency over Phase 1",
    },
    {
        "scheme": "NXDN",
        "category": "digital",
        "symbol_rate_baud_min": 2_400,
        "symbol_rate_baud_max": 4_800,
        "bandwidth_hz_typical": 6_250,
        "bandwidth_hz_min": 3_125,
        "bandwidth_hz_max": 12_500,
        "spectrum_shape": "c4fm_very_narrow",
        "spectral_efficiency_bps_hz": 0.77,
        "typical_use": "Kenwood/Icom narrowband digital; 6.25 kHz and 12.5 kHz FDMA variants",
    },
    {
        "scheme": "YSF",
        "category": "digital",
        "symbol_rate_baud_min": 4_800,
        "symbol_rate_baud_max": 4_800,
        "bandwidth_hz_typical": 12_500,
        "bandwidth_hz_min": 12_500,
        "bandwidth_hz_max": 12_500,
        "spectrum_shape": "c4fm_digital_voice",
        "spectral_efficiency_bps_hz": 0.77,
        "typical_use": "Yaesu System Fusion; amateur VHF/UHF digital voice; C4FM modulation",
    },
    # ── Digital -- data/packet ──
    {
        "scheme": "APRS",
        "category": "digital",
        "symbol_rate_baud_min": 1_200,
        "symbol_rate_baud_max": 9_600,
        "bandwidth_hz_typical": 12_500,
        "bandwidth_hz_min": 6_000,
        "bandwidth_hz_max": 16_000,
        "spectrum_shape": "bell_202_afsk_on_fm",
        "spectral_efficiency_bps_hz": 0.1,
        "typical_use": "Automatic Packet Reporting System; position/weather/telemetry; 144.390 MHz (NA)",
    },
    {
        "scheme": "Packet_1200",
        "category": "digital",
        "symbol_rate_baud_min": 1_200,
        "symbol_rate_baud_max": 1_200,
        "bandwidth_hz_typical": 12_500,
        "bandwidth_hz_min": 6_250,
        "bandwidth_hz_max": 25_000,
        "spectrum_shape": "bell_202_afsk",
        "spectral_efficiency_bps_hz": 0.1,
        "typical_use": "AX.25 packet radio on VHF/UHF; 1200 bps AFSK; Winlink; BBS; DX cluster",
    },
    {
        "scheme": "Packet_9600",
        "category": "digital",
        "symbol_rate_baud_min": 9_600,
        "symbol_rate_baud_max": 9_600,
        "bandwidth_hz_typical": 4_800,
        "bandwidth_hz_min": 4_800,
        "bandwidth_hz_max": 20_000,
        "spectrum_shape": "g3ruh_scrambled_fsk",
        "spectral_efficiency_bps_hz": 2.0,
        "typical_use": "AX.25 packet radio on UHF (scratchy discriminator tap); 9600 bps",
    },
    # ── Digital -- spread spectrum ──
    {
        "scheme": "LoRa",
        "category": "digital",
        "symbol_rate_baud_min": 122,
        "symbol_rate_baud_max": 11_000,
        "bandwidth_hz_typical": 125_000,
        "bandwidth_hz_min": 7_800,
        "bandwidth_hz_max": 500_000,
        "spectrum_shape": "chirp_spread_spectrum",
        "spectral_efficiency_bps_hz": 0.08,
        "typical_use": "LoRaWAN IoT; long-range low-power; ISM bands (868/915 MHz); SF7-SF12",
    },
]


def schemes_by_category(category: str) -> list[dict[str, Any]]:
    """Return all schemes of the given category (analog / digital)."""
    return [s for s in MODULATION_SCHEMES if s["category"] == category]


def scheme_info(name: str) -> dict[str, Any] | None:
    """Return the entry for a named scheme, or None."""
    for s in MODULATION_SCHEMES:
        if s["scheme"] == name:
            return s
    return None


def all_scheme_names() -> list[str]:
    """Return the names of all known modulation schemes."""
    return [s["scheme"] for s in MODULATION_SCHEMES]


def classify_signal(
    bandwidth_hz: float,
    symbol_rate_baud: float | None = None,
    spectrum_shape: str = "",
    frequency_mhz: float | None = None,
) -> list[dict[str, Any]]:
    """
    Given observed signal parameters, return list of likely modulation schemes,
    ranked by best match (most criteria met).

    Matching criteria (weighted):
      - Bandwidth within [min, max] range: 2 points
      - Symbol rate within [min, max] range (if provided): 2 points
      - Spectrum shape substring match: 3 points
      - Typical frequency band context: 1 point
    """
    scored: list[tuple[int, dict[str, Any]]] = []

    for s in MODULATION_SCHEMES:
        score = 0

        bw_min = s.get("bandwidth_hz_min")
        bw_max = s.get("bandwidth_hz_max")
        if bw_min is not None and bw_max is not None:
            if bw_min <= bandwidth_hz <= bw_max:
                score += 2
            elif bw_min * 0.5 <= bandwidth_hz <= bw_max * 2.0:
                score += 1

        if symbol_rate_baud is not None:
            sr_min = s.get("symbol_rate_baud_min")
            sr_max = s.get("symbol_rate_baud_max")
            if sr_min is not None and sr_max is not None:
                if sr_min <= symbol_rate_baud <= sr_max:
                    score += 2
                elif sr_min * 0.5 <= symbol_rate_baud <= sr_max * 2.0:
                    score += 1

        if spectrum_shape:
            shape_lower = s.get("spectrum_shape", "").lower()
            input_lower = spectrum_shape.lower()
            if any(word in shape_lower for word in input_lower.split("_") if len(word) > 1):
                score += 3

        if frequency_mhz is not None:
            use_lower = s.get("typical_use", "").lower()
            hf_band = frequency_mhz < 30 and "hf" in use_lower
            vhf_uhf_band = frequency_mhz >= 30 and ("vhf" in use_lower or "uhf" in use_lower)
            if hf_band or vhf_uhf_band:
                score += 1

        if score > 0:
            scored.append((score, s))

    scored.sort(key=lambda x: x[0], reverse=True)

    result = []
    for sc, entry in scored:
        result.append({"score": sc, **entry})

    return result
