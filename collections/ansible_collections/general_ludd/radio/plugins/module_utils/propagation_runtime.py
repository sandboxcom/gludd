#!/usr/bin/env python3
"""
propagation_model -- RF path-loss model dispatcher and JSON verdict writer.

Usage:
    python propagation_model.py --model free_space --freq-hz 433000000 --distance-m 1000
        [--tx-height 30] [--rx-height 1.5] [--output-dir DIR]

Imports the underlying models from module_utils.propagation_models and exposes
them as a standalone CLI so the role's tasks/main.yml can invoke a file backend
(same pattern as sdr_capture and antenna_design).
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from typing import Any

from ansible_collections.general_ludd.radio.plugins.module_utils.propagation_models import (
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


@dataclass
class PropagationVerdict:
    model: str
    freq_hz: int
    distance_m: float
    tx_height_m: float
    rx_height_m: float
    loss_db: float | None = None
    model_name: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "model": self.model,
            "model_name": self.model_name,
            "freq_hz": self.freq_hz,
            "freq_mhz": round(self.freq_hz / 1_000_000.0, 6),
            "distance_m": self.distance_m,
            "distance_km": round(self.distance_m / 1000.0, 6),
            "tx_height_m": self.tx_height_m,
            "rx_height_m": self.rx_height_m,
            "loss_db": self.loss_db,
            "verdict": "success" if self.loss_db is not None else "skipped",
        }
        if self.extra:
            d["extra"] = self.extra
        return d


def compute_path_loss(
    model: str,
    freq_hz: int,
    distance_m: float,
    tx_height_m: float = 30.0,
    rx_height_m: float = 1.5,
    **kwargs: Any,
) -> PropagationVerdict:
    if model not in VALID_MODELS:
        return PropagationVerdict(
            model=model,
            freq_hz=freq_hz,
            distance_m=distance_m,
            tx_height_m=tx_height_m,
            rx_height_m=rx_height_m,
            model_name=f"unknown ({model})",
            extra={"error": f"Unknown model: {model}", "valid_models": list(VALID_MODELS)},
        )

    distance_km = distance_m / 1000.0
    frequency_mhz = freq_hz / 1_000_000.0

    if model == "rain":
        freq_ghz = freq_hz / 1e9
        result = rain_attenuation(
            freq_ghz=freq_ghz,
            rain_rate_mmh=kwargs.get("rain_rate_mmh", 5.0),
            distance_km=distance_km,
            polarization=kwargs.get("polarization", "horizontal"),
        )
        return PropagationVerdict(
            model=model,
            freq_hz=freq_hz,
            distance_m=distance_m,
            tx_height_m=tx_height_m,
            rx_height_m=rx_height_m,
            loss_db=result.get("total_attenuation_db"),
            model_name=result.get("model", model),
            extra=result,
        )

    result = predict_path_loss(
        model=model,
        distance_km=distance_km,
        frequency_mhz=frequency_mhz,
        tx_height_m=tx_height_m,
        rx_height_m=rx_height_m,
        **kwargs,
    )

    if "error" in result:
        return PropagationVerdict(
            model=model,
            freq_hz=freq_hz,
            distance_m=distance_m,
            tx_height_m=tx_height_m,
            rx_height_m=rx_height_m,
            model_name=f"error ({model})",
            extra=result,
        )

    return PropagationVerdict(
        model=model,
        freq_hz=freq_hz,
        distance_m=distance_m,
        tx_height_m=tx_height_m,
        rx_height_m=rx_height_m,
        loss_db=result.get("loss_db"),
        model_name=result.get("model", model),
        extra=result,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="RF propagation path-loss dispatcher")
    parser.add_argument(
        "--model",
        choices=VALID_MODELS,
        required=True,
        help="Path-loss model name",
    )
    parser.add_argument("--freq-hz", type=int, required=True, help="Frequency in Hz")
    parser.add_argument("--distance-m", type=float, required=True, help="Distance in meters")
    parser.add_argument("--tx-height", type=float, default=30.0, help="Transmitter height in meters")
    parser.add_argument("--rx-height", type=float, default=1.5, help="Receiver height in meters")
    parser.add_argument("--terrain-irregularity", type=float, default=30.0, help="Delta-h in meters (ITM)")
    parser.add_argument("--climate", type=int, default=5, help="Radio climate code (ITM)")
    parser.add_argument("--refractivity", type=float, default=301.0, help="Surface refractivity (ITM)")
    parser.add_argument("--permittivity", type=float, default=15.0, help="Ground permittivity (ITM)")
    parser.add_argument("--conductivity", type=float, default=0.005, help="Ground conductivity (ITM)")
    parser.add_argument(
        "--polarization",
        choices=["horizontal", "vertical"],
        default="horizontal",
        help="Signal polarization",
    )
    parser.add_argument("--rain-rate", type=float, default=5.0, help="Rain rate in mm/h (rain model)")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="/tmp/gludd-propagation-model",
        help="Output directory for JSON verdict",
    )
    args = parser.parse_args()

    kwargs: dict[str, Any] = {}
    if args.model == "itm":
        kwargs.update(
            terrain_irregularity_m=args.terrain_irregularity,
            climate=args.climate,
            refractivity=args.refractivity,
            permittivity=args.permittivity,
            conductivity=args.conductivity,
            polarization=args.polarization,
        )
    elif args.model == "rain":
        kwargs.update(
            rain_rate_mmh=args.rain_rate,
            polarization=args.polarization,
        )

    verdict = compute_path_loss(
        model=args.model,
        freq_hz=args.freq_hz,
        distance_m=args.distance_m,
        tx_height_m=args.tx_height,
        rx_height_m=args.rx_height,
        **kwargs,
    )

    os.makedirs(args.output_dir, exist_ok=True)
    output_path = os.path.join(args.output_dir, "propagation_model.json")
    payload = json.dumps(verdict.to_dict(), indent=2)
    try:
        with open(output_path, "w") as f:
            f.write(payload)
    except OSError:
        pass

    print(payload)


if __name__ == "__main__":
    main()
