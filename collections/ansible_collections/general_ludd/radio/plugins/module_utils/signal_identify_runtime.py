"""
signal_identify -- Identify modulation type, protocol, and confidence from signal parameters.

Usage:
    python signal_identify.py [--input-file FILE] [--sample-rate RATE]
        [--center-freq FREQ] [--bandwidth BW] [--symbol-rate SR]
        [--spectrum-shape SHAPE] [--threshold THRESH]

Output: JSON object with classification results.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from ansible_collections.general_ludd.radio.plugins.module_utils.modulation_schemes import (
    classify_signal,
)


def signal_identify(
    input_file: str | None = None,
    sample_rate: int = 2_048_000,
    center_freq_hz: int = 100_000_000,
    bandwidth_hz: float | None = None,
    symbol_rate_baud: float | None = None,
    spectrum_shape: str = "",
    threshold_db: float = 10.0,
    method: str = "fft",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "role": "signal_identify",
        "method": method,
        "input": {
            "input_file": input_file,
            "sample_rate": sample_rate,
            "center_freq_hz": center_freq_hz,
            "provided_bandwidth_hz": bandwidth_hz,
            "provided_symbol_rate_baud": symbol_rate_baud,
            "provided_spectrum_shape": spectrum_shape,
        },
    }

    estimated_bandwidth = bandwidth_hz
    estimated_symbol_rate = symbol_rate_baud
    estimated_shape = spectrum_shape
    freq_mhz = center_freq_hz / 1_000_000.0

    peaks_detected = 0

    if input_file and os.path.isfile(input_file):
        try:
            import numpy as np
            from scipy.signal import spectrogram

            data = np.fromfile(input_file, dtype=np.int16)
            if len(data) % 2 != 0:
                data = data[:-1]

            iq = data[::2].astype(np.float64) + 1j * data[1::2].astype(np.float64)
            magnitude = np.abs(iq)

            if estimated_bandwidth is None or estimated_shape == "":
                f, _t, Sxx = spectrogram(
                    magnitude,
                    sample_rate,
                    nperseg=min(1024, len(magnitude) // 4),
                    noverlap=min(512, len(magnitude) // 8),
                )

                mag_db = 10.0 * np.log10(Sxx + 1e-12)
                peak_mask = mag_db > (np.max(mag_db) - threshold_db)

                if np.any(peak_mask):
                    freq_indices = np.where(np.max(peak_mask, axis=1))[0]
                    if len(freq_indices) > 0:
                        peaks_detected = int(np.sum(np.max(peak_mask, axis=1)))
                        f_min = float(f[freq_indices[0]])
                        f_max = float(f[freq_indices[-1]])
                        if estimated_bandwidth is None:
                            estimated_bandwidth = max(f_max - f_min, 50.0)

                if estimated_shape == "":
                    mean_spectrum = np.mean(mag_db, axis=1)
                    if len(mean_spectrum) > 0 and np.max(mean_spectrum) > 0:
                        above_3db = mean_spectrum > (np.max(mean_spectrum) - 3.0)
                        transitions = np.diff(above_3db.astype(int))
                        num_peaks = max(int(np.sum(transitions > 0)), 0)
                        if num_peaks == 0:
                            estimated_shape = "single_carrier"
                        elif num_peaks == 2:
                            estimated_shape = "two_tone_fsk"
                        elif 3 <= num_peaks <= 8:
                            estimated_shape = "multi_tone_fsk"
                        elif num_peaks > 8:
                            estimated_shape = "many_tone_fsk"
                        else:
                            estimated_shape = "unknown"

            result["signal_analysis"] = {
                "iq_samples": len(data),
                "duration_ms": round(len(data) * 1000.0 / sample_rate, 1),
                "estimated_bandwidth_hz": round(estimated_bandwidth, 1) if estimated_bandwidth else None,
                "estimated_shape": estimated_shape,
                "peaks_detected": peaks_detected,
                "rms_magnitude": round(float(np.sqrt(np.mean(magnitude**2))), 4),
            }

        except ImportError:
            result["warning"] = "numpy/scipy not available; using provided params only"
            if input_file:
                result["signal_analysis"] = {
                    "error": "Cannot load IQ data without numpy/scipy",
                }

    bw_for_classify = estimated_bandwidth if estimated_bandwidth is not None else 12_500.0
    sr_for_classify = estimated_symbol_rate if estimated_symbol_rate is not None else None

    candidates = classify_signal(
        bandwidth_hz=bw_for_classify,
        symbol_rate_baud=sr_for_classify,
        spectrum_shape=estimated_shape,
        frequency_mhz=freq_mhz,
    )

    results = []
    for c in candidates[:10]:
        results.append(
            {
                "scheme": c["scheme"],
                "category": c["category"],
                "score": c["score"],
                "bandwidth_hz_typical": c["bandwidth_hz_typical"],
                "spectrum_shape": c["spectrum_shape"],
                "confidence": round(c["score"] / 8.0, 3),
                "typical_use": c["typical_use"],
            }
        )

    protocol_candidates = _deduce_protocols(results, freq_mhz)

    min_score = 3
    meaningful = [r for r in results if r["score"] >= min_score]
    result["classification"] = {
        "candidates": results,
        "protocol_candidates": protocol_candidates,
        "top_hit": meaningful[0] if meaningful else None,
    }
    result["verdict"] = "identified" if meaningful else "unknown"

    return result


def _deduce_protocols(results: list[dict[str, Any]], freq_mhz: float) -> list[dict[str, Any]]:
    providers: dict[str, str] = {
        "DMR": "ETSI TS 102 361 (DMR Tier I/II/III)",
        "D-STAR": "JARL D-STAR specification",
        "P25_Phase1": "TIA-102 (APCO P25 Phase 1)",
        "P25_Phase2": "TIA-102 (APCO P25 Phase 2)",
        "NXDN": "NXDN Forum CAI",
        "APRS": "APRS Protocol Reference 1.0.1",
        "FT8": "WSJT-X FT8 (K1JT, G4WJS)",
        "FT4": "WSJT-X FT4 (K1JT, G4WJS)",
        "JT65": "WSJT-X JT65 (K1JT)",
        "WSPR": "WSJT-X WSPR (K1JT)",
        "OLIVIA": "OLIVIA MFSK specification",
        "RTTY": "ITA2 Baudot via FSK",
        "Packet_1200": "AX.25 2.2",
        "Packet_9600": "AX.25 2.2 G3RUH",
        "LoRa": "Semtech LoRa specification",
        "AM": "ITU-R Broadcast",
        "FM": "ITU-R FM Broadcast / Land Mobile",
        "NBFM": "Land Mobile NBFM",
        "SSB-USB": "ITU-R SSB",
        "SSB-LSB": "ITU-R SSB",
        "CW": "International Morse Code",
    }

    protocols = []
    seen = set()
    for r in results:
        scheme = r["scheme"]
        if scheme in providers and scheme not in seen:
            seen.add(scheme)
            proto: dict[str, Any] = {
                "protocol": scheme,
                "reference": providers.get(scheme, "Unknown"),
                "confidence": r["confidence"],
            }
            if freq_mhz < 30.0:
                proto["likely_band"] = "HF"
            elif freq_mhz < 300.0:
                proto["likely_band"] = "VHF"
            else:
                proto["likely_band"] = "UHF"
            protocols.append(proto)

    return protocols


def main() -> None:
    parser = argparse.ArgumentParser(description="RF signal classification")
    parser.add_argument("--input-file", type=str, help="IQ sample binary file")
    parser.add_argument("--sample-rate", type=int, default=2_048_000)
    parser.add_argument("--center-freq", type=int, default=100_000_000)
    parser.add_argument("--bandwidth", type=float)
    parser.add_argument("--symbol-rate", type=float)
    parser.add_argument("--spectrum-shape", type=str, default="")
    parser.add_argument("--threshold", type=float, default=10.0)
    parser.add_argument("--method", type=str, default="fft")
    parser.add_argument("--json", action="store_true", default=True)
    args = parser.parse_args()

    result = signal_identify(
        input_file=args.input_file,
        sample_rate=args.sample_rate,
        center_freq_hz=args.center_freq,
        bandwidth_hz=args.bandwidth,
        symbol_rate_baud=args.symbol_rate,
        spectrum_shape=args.spectrum_shape,
        threshold_db=args.threshold,
        method=args.method,
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
