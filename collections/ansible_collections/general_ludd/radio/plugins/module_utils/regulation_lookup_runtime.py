#!/usr/bin/env python3
"""
regulation_lookup -- frequency allocation, band plan, and license-class lookup.

Usage:
    python regulation_lookup.py --country US --freq-mhz 146.52
    python regulation_lookup.py --country US --band 20m
    python regulation_lookup.py --country US --license-class extra

Imports module_utils.frequency_allocations and exposes a JSON verdict so the
role's tasks/main.yml can invoke a standalone file backend.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from typing import Any, cast

from ansible_collections.general_ludd.radio.plugins.module_utils.frequency_allocations import (
    bands_by_privilege,
    get_band_plan,
    get_itu_bands,
    get_marine_channel,
    lookup_frequency,
)

SUPPORTED_COUNTRIES = ("US", "CA")


@dataclass
class RegulationVerdict:
    country: str
    freq_mhz: float | None = None
    band_name: str | None = None
    license_class: str | None = None
    frequency_lookup: dict[str, Any] | None = None
    band_plan: dict[str, Any] | None = None
    license_privileges: list[dict[str, Any]] = field(default_factory=list)
    marine_channel: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "country": self.country,
            "country_supported": self.country in SUPPORTED_COUNTRIES,
            "freq_mhz": self.freq_mhz,
            "band_name": self.band_name,
            "license_class": self.license_class,
            "verdict": "skipped" if self.error and not self.frequency_lookup and not self.band_plan else "success",
        }
        if self.frequency_lookup is not None:
            d["frequency_lookup"] = self.frequency_lookup
        if self.band_plan is not None:
            d["band_plan"] = self.band_plan
        if self.license_privileges:
            d["license_privileges"] = self.license_privileges
        if self.marine_channel is not None:
            d["marine_channel"] = self.marine_channel
        if self.error:
            d["error"] = self.error
        return d


def lookup(
    country: str,
    freq_mhz: float | None = None,
    band_name: str | None = None,
    license_class: str | None = None,
    marine_channel: int | None = None,
) -> RegulationVerdict:
    verdict = RegulationVerdict(
        country=country,
        freq_mhz=freq_mhz,
        band_name=band_name,
        license_class=license_class,
    )

    if freq_mhz is not None:
        result = lookup_frequency(freq_mhz, country)
        if result is None:
            verdict.error = f"No allocation found for {freq_mhz} MHz in {country}"
        else:
            verdict.frequency_lookup = result

    if band_name is not None:
        plan = get_band_plan(band_name, country)
        if plan is None:
            verdict.error = (verdict.error or "") + "; " if verdict.error else ""
            verdict.error = (verdict.error or "") + f"No band plan found for '{band_name}' in {country}"
        else:
            verdict.band_plan = {
                "band_name": band_name,
                "start_hz": plan["start_hz"],
                "end_hz": plan["end_hz"],
                "display": plan.get("display", band_name),
            }
            for cls in ("technician", "general", "extra"):
                if cls in plan:
                    verdict.band_plan[cls] = plan[cls]

    if license_class is not None:
        privs = bands_by_privilege(country, license_class)
        verdict.license_privileges = [
            {
                "band_name": p.get("band_name", "unknown"),
                "display": p.get("display", p.get("band_name", "unknown")),
                "max_power_w": p.get("max_power_w", 0),
                "privileges": p.get("privileges", []),
            }
            for p in privs
        ]

    if marine_channel is not None:
        ch = get_marine_channel(marine_channel)
        if ch is not None:
            verdict.marine_channel = ch

    return verdict


def itu_bands(region: int = 2) -> list[dict[str, Any]]:
    """Return ITU amateur band allocations for a region (1, 2, or 3)."""
    return cast(list[dict[str, Any]], get_itu_bands(region))


def main() -> None:
    parser = argparse.ArgumentParser(description="Frequency allocation and band plan lookup")
    parser.add_argument("--country", type=str, required=True, help="2-letter ISO country code")
    parser.add_argument("--freq-mhz", type=float, default=None, help="Frequency in MHz to look up")
    parser.add_argument("--band", type=str, default=None, help="Band name (e.g. 20m, 2m, 70cm)")
    parser.add_argument("--license-class", type=str, default=None, help="License class (technician/general/extra)")
    parser.add_argument("--marine-channel", type=int, default=None, help="Marine VHF channel number")
    parser.add_argument(
        "--list-itu",
        action="store_true",
        help="Print ITU band allocations. Use with --itu-region to pick a region.",
    )
    parser.add_argument(
        "--itu-region",
        type=int,
        default=2,
        choices=(1, 2, 3),
        help="ITU region for --list-itu: 1 (Europe/Africa/ME/N-Asia), "
        "2 (Americas, default), 3 (Asia-Pacific/Oceania).",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="/tmp/gludd-regulation-lookup",
        help="Output directory for JSON verdict",
    )
    args = parser.parse_args()

    if args.list_itu:
        print(json.dumps({f"itu_region{args.itu_region}_bands": itu_bands(args.itu_region)}, indent=2))
        return

    if (
        args.freq_mhz is None
        and args.band is None
        and args.license_class is None
        and args.marine_channel is None
    ):
        parser.error("at least one of --freq-mhz, --band, --license-class, --marine-channel required")

    verdict = lookup(
        country=args.country.upper(),
        freq_mhz=args.freq_mhz,
        band_name=args.band,
        license_class=args.license_class,
        marine_channel=args.marine_channel,
    )

    os.makedirs(args.output_dir, exist_ok=True)
    output_path = os.path.join(args.output_dir, "regulation_lookup.json")
    payload = json.dumps(verdict.to_dict(), indent=2)
    try:
        with open(output_path, "w") as f:
            f.write(payload)
    except OSError:
        pass

    print(payload)


if __name__ == "__main__":
    main()
