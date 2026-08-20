#!/usr/bin/env python3
"""
link_budget -- RF link budget calculator (EIRP, RX signal, fade margin, viability).

Usage:
    python link_budget.py --freq-hz 144000000 --distance-m 10000
        [--tx-power 30] [--model free_space]
        [--tx-antenna-type dipole_half_wave] [--rx-antenna-type dipole_half_wave]
        [--rain-enabled] [--output-dir DIR]

Imports path-loss models from module_utils.propagation_models and antenna gain
data from module_utils.antenna_types. Writes a JSON verdict to
<output-dir>/link_budget.json and stdout.

Equations:
    EIRP (dBm)       = tx_power_dbm + tx_gain_dbi - tx_line_loss_db
    RX signal (dBm)  = EIRP - path_loss_db + rx_gain_dbi - rx_line_loss_db
    Fade margin (dB) = RX signal - rx_sensitivity_dbm
    Viable           = fade_margin_db >= required_snr_db
    Rain (optional)  = additional attenuation subtracted from fade margin
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from ansible_collections.general_ludd.radio.plugins.module_utils.antenna_types import (
    antenna_info,
)
from ansible_collections.general_ludd.radio.plugins.module_utils.propagation_models import (
    free_space_loss,
    predict_path_loss,
    rain_attenuation,
)

VALID_MODELS = (
    "free_space",
    "hata_urban",
    "hata_suburban",
    "hata_rural",
    "two_ray",
    "itm",
    "rain",
)


def _resolve_antenna(
    antenna_type: str | None,
    explicit_gain_dbi: float,
) -> tuple[float, str, float]:
    """Resolve (gain_dbi, polarization, impedance_ohms) for an antenna spec."""
    if antenna_type:
        info = antenna_info(antenna_type)
        if info is not None:
            return (
                float(info["gain_dbi"]),
                str(info.get("polarization", "unknown")),
                float(info.get("impedance_ohms", 50.0)),
            )
    return (float(explicit_gain_dbi), "unknown", 50.0)


def compute_link_budget(
    tx_power_dbm: float,
    freq_hz: int,
    distance_m: float,
    model: str = "free_space",
    tx_antenna_type: str | None = "dipole_half_wave",
    tx_antenna_gain_dbi: float = 2.15,
    tx_line_loss_db: float = 1.0,
    tx_height_m: float = 30.0,
    rx_antenna_type: str | None = "dipole_half_wave",
    rx_antenna_gain_dbi: float = 2.15,
    rx_line_loss_db: float = 1.0,
    rx_height_m: float = 1.5,
    rx_sensitivity_dbm: float = -120.0,
    required_snr_db: float = 10.0,
    rain_enabled: bool = False,
    rain_rate_mmh: float = 5.0,
    rain_polarization: str = "horizontal",
) -> dict[str, Any]:
    """Compute a complete RF link budget and return a JSON-serializable dict."""
    if distance_m <= 0:
        raise ValueError(f"distance_m must be > 0, got {distance_m}")
    if freq_hz <= 0:
        raise ValueError(f"freq_hz must be > 0, got {freq_hz}")
    if model not in VALID_MODELS:
        raise ValueError(f"Unknown model: {model}; valid: {VALID_MODELS}")

    tx_gain, tx_pol, tx_z = _resolve_antenna(tx_antenna_type, tx_antenna_gain_dbi)
    rx_gain, rx_pol, rx_z = _resolve_antenna(rx_antenna_type, rx_antenna_gain_dbi)

    distance_km = distance_m / 1000.0
    freq_mhz = freq_hz / 1_000_000.0

    # "rain" as a primary model uses free-space baseline + auto rain attenuation
    effective_rain = rain_enabled or model == "rain"
    base_model = "free_space" if model == "rain" else model

    if base_model == "free_space":
        # Direct call avoids predict_path_loss wrapping and gives us raw FSPL.
        path_loss_db = free_space_loss(distance_m, float(freq_hz))
        path_loss_model = "Free-Space Path Loss"
    else:
        result = predict_path_loss(
            model=base_model,
            distance_km=distance_km,
            frequency_mhz=freq_mhz,
            tx_height_m=tx_height_m,
            rx_height_m=rx_height_m,
        )
        if "error" in result:
            raise ValueError(str(result["error"]))
        path_loss_db = float(result["loss_db"])
        path_loss_model = str(result.get("model", base_model))

    if effective_rain and model == "rain":
        path_loss_model = f"{path_loss_model} + Rain (ITU-R P.838)"

    eirp_dbm = tx_power_dbm + tx_gain - tx_line_loss_db
    rx_signal_dbm = eirp_dbm - path_loss_db + rx_gain - rx_line_loss_db
    fade_margin_db = rx_signal_dbm - rx_sensitivity_dbm
    viable = fade_margin_db >= required_snr_db

    payload: dict[str, Any] = {
        "viable": viable,
        "required_snr_db": required_snr_db,
        "fade_margin_db": round(fade_margin_db, 2),
        "rx_signal_dbm": round(rx_signal_dbm, 2),
        "rx_sensitivity_dbm": rx_sensitivity_dbm,
        "path_loss_db": round(path_loss_db, 2),
        "path_loss_model": path_loss_model,
        "path_loss_input": {
            "distance_m": distance_m,
            "distance_km": round(distance_km, 4),
            "frequency_hz": freq_hz,
            "frequency_mhz": round(freq_mhz, 4),
        },
        "eirp_dbm": round(eirp_dbm, 2),
        "tx": {
            "power_dbm": tx_power_dbm,
            "antenna_type": tx_antenna_type,
            "antenna_gain_dbi": round(tx_gain, 4),
            "line_loss_db": tx_line_loss_db,
            "polarization": tx_pol,
            "impedance_ohms": tx_z,
        },
        "rx": {
            "antenna_type": rx_antenna_type,
            "antenna_gain_dbi": round(rx_gain, 4),
            "line_loss_db": rx_line_loss_db,
            "sensitivity_dbm": rx_sensitivity_dbm,
            "polarization": rx_pol,
            "impedance_ohms": rx_z,
        },
    }

    if effective_rain:
        freq_ghz = freq_hz / 1e9
        rain_result = rain_attenuation(
            freq_ghz=freq_ghz,
            rain_rate_mmh=rain_rate_mmh,
            distance_km=distance_km,
            polarization=rain_polarization,
        )
        rain_db = float(rain_result["total_attenuation_db"])
        margin_with_rain = fade_margin_db - rain_db
        payload["rain_attenuation_db"] = rain_db
        payload["fade_margin_with_rain_db"] = round(margin_with_rain, 2)
        payload["viable_with_rain"] = margin_with_rain >= required_snr_db

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="RF link budget calculator")
    parser.add_argument("--tx-power", type=float, default=30.0, help="TX power in dBm")
    parser.add_argument("--freq-hz", type=int, required=True, help="Frequency in Hz")
    parser.add_argument("--distance-m", type=float, required=True, help="Path distance in meters")
    parser.add_argument(
        "--model",
        choices=VALID_MODELS,
        default="free_space",
        help="Path-loss propagation model",
    )
    parser.add_argument("--tx-antenna-type", type=str, default="dipole_half_wave")
    parser.add_argument("--tx-antenna-gain-dbi", type=float, default=2.15)
    parser.add_argument("--tx-line-loss-db", type=float, default=1.0)
    parser.add_argument("--tx-height-m", type=float, default=30.0)
    parser.add_argument("--rx-antenna-type", type=str, default="dipole_half_wave")
    parser.add_argument("--rx-antenna-gain-dbi", type=float, default=2.15)
    parser.add_argument("--rx-line-loss-db", type=float, default=1.0)
    parser.add_argument("--rx-height-m", type=float, default=1.5)
    parser.add_argument("--rx-sensitivity-dbm", type=float, default=-120.0)
    parser.add_argument("--required-snr-db", type=float, default=10.0)
    parser.add_argument(
        "--rain-enabled",
        action="store_true",
        help="Apply ITU-R P.838 rain attenuation to the fade margin",
    )
    parser.add_argument("--rain-rate", type=float, default=5.0, help="Rain rate in mm/h")
    parser.add_argument(
        "--rain-polarization",
        choices=["horizontal", "vertical"],
        default="horizontal",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="/tmp/gludd-link-budget",
        help="Output directory for JSON verdict",
    )
    args = parser.parse_args()

    payload = compute_link_budget(
        tx_power_dbm=args.tx_power,
        freq_hz=args.freq_hz,
        distance_m=args.distance_m,
        model=args.model,
        tx_antenna_type=args.tx_antenna_type,
        tx_antenna_gain_dbi=args.tx_antenna_gain_dbi,
        tx_line_loss_db=args.tx_line_loss_db,
        tx_height_m=args.tx_height_m,
        rx_antenna_type=args.rx_antenna_type,
        rx_antenna_gain_dbi=args.rx_antenna_gain_dbi,
        rx_line_loss_db=args.rx_line_loss_db,
        rx_height_m=args.rx_height_m,
        rx_sensitivity_dbm=args.rx_sensitivity_dbm,
        required_snr_db=args.required_snr_db,
        rain_enabled=args.rain_enabled,
        rain_rate_mmh=args.rain_rate,
        rain_polarization=args.rain_polarization,
    )

    os.makedirs(args.output_dir, exist_ok=True)
    output_path = os.path.join(args.output_dir, "link_budget.json")
    text = json.dumps(payload, indent=2)
    try:
        with open(output_path, "w") as f:
            f.write(text)
    except OSError:
        pass

    print(text)


if __name__ == "__main__":
    main()
