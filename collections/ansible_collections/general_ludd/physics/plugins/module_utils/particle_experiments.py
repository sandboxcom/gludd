"""Particle experiment reference data and lightweight analysis helpers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast


class ParticleConfig:
    def __init__(
        self,
        beam_energy_GeV: float = 13.6,
        target: str = "proton",
        beam: str = "proton",
        detector: str = "generic_4pi",
        luminosity_inv_fb: float = 139.0,
        analysis_channel: str = "inclusive",
    ) -> None:
        self.beam_energy_GeV = beam_energy_GeV
        self.target = target
        self.beam = beam
        self.detector = detector
        self.luminosity_inv_fb = luminosity_inv_fb
        self.analysis_channel = analysis_channel


ACCELERATORS: dict[str, dict[str, Any]] = {
    "LHC": {
        "full_name": "Large Hadron Collider",
        "type": "proton proton and heavy-ion collider",
        "location": "CERN, Geneva",
        "status": "operational",
        "beam_energies": {"pp": 6.8, "PbPb": 2.68},
        "experiments": ["ATLAS", "CMS", "LHCb", "ALICE"],
        "key_discoveries": ["Higgs boson discovery"],
    },
    "HL_LHC": {
        "full_name": "High-Luminosity LHC",
        "type": "proton luminosity upgrade",
        "location": "CERN, Geneva",
        "status": "under_construction",
        "beam_energies": {"pp": 7.0},
        "experiments": ["ATLAS", "CMS"],
        "key_discoveries": [],
    },
    "Tevatron": {
        "full_name": "Tevatron",
        "type": "proton antiproton collider",
        "location": "Fermilab",
        "status": "decommissioned",
        "beam_energies": {"ppbar": 0.98},
        "experiments": ["CDF", "D0"],
        "key_discoveries": ["top quark"],
    },
    "RHIC": {
        "full_name": "Relativistic Heavy Ion Collider",
        "type": "heavy-ion and polarized proton collider",
        "location": "Brookhaven National Laboratory",
        "status": "operational",
        "beam_energies": {"AuAu": 0.1, "pp": 0.255},
        "experiments": ["STAR", "PHENIX"],
        "key_discoveries": ["quark gluon plasma"],
    },
    "ILC": {
        "full_name": "International Linear Collider",
        "type": "electron positron linear collider",
        "location": "site proposed",
        "status": "proposed",
        "beam_energies": {"ee": 0.25},
        "experiments": [],
        "key_discoveries": [],
    },
    "CLIC": {
        "full_name": "Compact Linear Collider",
        "type": "electron positron linear collider",
        "location": "CERN design study",
        "status": "proposed",
        "beam_energies": {"ee": 3.0},
        "experiments": [],
        "key_discoveries": [],
    },
    "EIC": {
        "full_name": "Electron Ion Collider",
        "type": "electron ion collider",
        "location": "Brookhaven National Laboratory",
        "status": "under_construction",
        "beam_energies": {"ep": 0.14},
        "experiments": ["ePIC"],
        "key_discoveries": [],
    },
    "FCC": {
        "full_name": "Future Circular Collider",
        "type": "proton and electron collider",
        "location": "CERN feasibility study",
        "status": "feasibility_study",
        "beam_energies": {"pp": 50.0},
        "experiments": [],
        "key_discoveries": [],
    },
    "SuperKEKB": {
        "full_name": "SuperKEKB",
        "type": "electron positron flavor factory",
        "location": "KEK, Tsukuba",
        "status": "operational",
        "beam_energies": {"ee": 0.011},
        "integrated_luminosity_target": 50,
        "experiments": ["Belle II"],
        "key_discoveries": [],
    },
    "KEKB": {
        "full_name": "KEKB",
        "type": "electron positron flavor factory",
        "location": "KEK, Tsukuba",
        "status": "decommissioned",
        "beam_energies": {"ee": 0.01058},
        "experiments": ["Belle"],
        "key_discoveries": [],
    },
    "muon_collider": {
        "full_name": "Muon Collider",
        "type": "muon collider",
        "location": "concept study",
        "status": "concept_study",
        "beam_energy_tev": [3, 10],
        "beam_energies": {"mumu": 10.0},
        "experiments": [],
        "key_discoveries": [],
    },
}

DETECTORS: dict[str, dict[str, Any]] = {
    "ATLAS": {
        "full_name": "ATLAS detector",
        "collider": "LHC",
        "type": "general purpose",
        "subdetectors": ["inner_tracker", "calorimeters", "muon_spectrometer"],
        "magnet_system": {"solenoid": "2 T"},
        "physics_program": ["Higgs", "SUSY", "exotics"],
        "notable_results": ["Higgs boson observation"],
    },
    "CMS": {
        "full_name": "Compact Muon Solenoid",
        "name": "CMS",
        "collider": "LHC",
        "type": "general purpose",
        "subdetectors": ["tracker", "ecal", "hcal", "muon"],
        "magnet_system": {"solenoid": "3.8 T"},
        "physics_program": ["Higgs", "top", "heavy ions"],
        "notable_results": ["Higgs boson observation"],
    },
"LHCb": {
        "full_name": "LHCb",
        "collider": "LHC",
        "type": "flavor physics",
        "physics_program": ["b physics"],
    },
    "ALICE": {
        "full_name": "ALICE",
        "collider": "LHC",
        "type": "heavy-ion dedicated",
        "physics_program": ["heavy ions"],
    },
    "Belle_II": {
        "full_name": "Belle II",
        "collider": "SuperKEKB",
        "type": "flavor factory",
        "physics_program": ["B physics"],
    },
    "CDF": {
        "full_name": "Collider Detector at Fermilab",
        "collider": "Tevatron",
        "type": "general purpose",
        "physics_program": ["top"],
    },
    "D0": {
        "full_name": "DZero",
        "collider": "Tevatron",
        "type": "general purpose",
        "physics_program": ["top"],
    },
    "STAR": {
        "full_name": "STAR",
        "collider": "RHIC",
        "type": "heavy-ion",
        "physics_program": ["QGP"],
    },
}

SKY_SURVEYS: dict[str, dict[str, Any]] = {
    "SDSS": {
        "full_name": "Sloan Digital Sky Survey",
        "coverage_type": "northern",
        "wavebands": ["u", "g", "r", "i", "z"],
        "limiting_magnitude": "22",
        "science_goals": ["galaxy evolution"],
        "data_release": "DR18",
    },
    "Pan_STARRS": {
        "full_name": "Pan-STARRS",
        "coverage_type": "northern",
        "wavebands": ["g", "r", "i", "z", "y"],
        "limiting_magnitude": "23",
        "science_goals": ["transients"],
        "data_release": "DR2",
    },
    "DES": {
        "full_name": "Dark Energy Survey",
        "coverage_type": "southern",
        "wavebands": ["g", "r", "i", "z", "Y"],
        "limiting_magnitude": "24",
        "science_goals": ["dark energy"],
        "data_release": "DR2",
    },
    "LSST": {
        "full_name": "Legacy Survey of Space and Time",
        "coverage_type": "southern",
        "wavebands": ["u", "g", "r", "i", "z", "y"],
        "limiting_magnitude": "24.5",
        "science_goals": ["transients", "dark energy"],
        "data_release": "Data Preview",
        "data_rate": "20 TB/night",
    },
    "Gaia": {
        "full_name": "Gaia",
        "coverage_type": "all_sky",
        "wavebands": ["G", "BP", "RP"],
        "limiting_magnitude": "21",
        "science_goals": ["Milky Way astrometry"],
        "data_release": "DR3",
        "astrometric_precision": "microarcsec",
    },
    "Euclid": {
        "full_name": "Euclid",
        "coverage_type": "extragalactic",
        "wavebands": ["VIS", "NISP"],
        "limiting_magnitude": "24",
        "science_goals": ["dark energy and dark matter"],
        "data_release": "2025 quick release",
    },
    "JWST": {
        "full_name": "James Webb Space Telescope",
        "coverage_type": "targeted",
        "wavebands": ["infrared"],
        "limiting_magnitude": "deep",
        "telescope": "infrared space telescope",
    },
}


def _copy(value: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(json.dumps(value)))


def get_experiment_info(name: str) -> dict[str, Any] | None:
    for table in (ACCELERATORS, DETECTORS, SKY_SURVEYS):
        if name in table:
            return _copy(table[name])
    return None


def get_detector_capabilities(name: str) -> dict[str, Any] | None:
    detector = DETECTORS.get(name)
    return _copy(detector) if detector is not None else None


def search_sky_survey(ra_deg: float, dec_deg: float) -> list[dict[str, Any]]:
    _ = ra_deg % 360.0
    results: list[dict[str, Any]] = []
    for name, survey in SKY_SURVEYS.items():
        coverage = survey["coverage_type"].lower()
        covered = (
            coverage == "all_sky"
            or (coverage == "northern" and dec_deg >= -10.0)
            or (coverage == "southern" and dec_deg <= 15.0)
            or (coverage == "extragalactic" and abs(dec_deg) <= 70.0)
        )
        if covered:
            results.append({
                "survey": name,
                "wavebands": list(survey["wavebands"]),
                "limiting_magnitude": survey["limiting_magnitude"],
                "has_coverage": True,
            })
    return results


def list_accelerators_by_type(kind: str) -> list[str]:
    needle = kind.lower()
    return [name for name, data in ACCELERATORS.items() if needle in data["type"].lower()]


def list_accelerators_by_status(status: str) -> list[str]:
    needle = status.lower()
    return [name for name, data in ACCELERATORS.items() if data["status"].lower() == needle]


def list_detectors_by_collider() -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for name, data in DETECTORS.items():
        grouped.setdefault(data["collider"], []).append(name)
    return grouped


def get_running_status(name: str) -> dict[str, Any] | None:
    accelerator = ACCELERATORS.get(name)
    if accelerator is None:
        return None
    return {"name": name, "status": accelerator["status"]}


def list_sky_surveys_by_coverage(coverage: str) -> list[str]:
    needle = coverage.lower()
    return [name for name, data in SKY_SURVEYS.items() if data["coverage_type"].lower() == needle]


def get_survey_data_release(name: str) -> str | None:
    survey = SKY_SURVEYS.get(name)
    if survey is None:
        return None
    return survey.get("data_release")


def compute_cross_section(config: ParticleConfig) -> dict[str, Any]:
    base_pb = max(config.beam_energy_GeV, 0.1) * 0.42
    if "gamma" in config.analysis_channel.lower():
        base_pb *= 0.12
    expected = base_pb * config.luminosity_inv_fb * 1000.0
    return {
        "beam": config.beam,
        "target": config.target,
        "detector": config.detector,
        "analysis_channel": config.analysis_channel,
        "cross_section_pb": round(base_pb, 6),
        "expected_events": round(expected, 3),
    }


def analyze_decay_chain(particle: str, lifetime_s: float, branching_ratios: dict[str, float]) -> dict[str, Any]:
    dominant = max(branching_ratios, key=branching_ratios.__getitem__) if branching_ratios else "unknown"
    return {
        "particle": particle,
        "lifetime_s": lifetime_s,
        "branching_ratios": branching_ratios,
        "dominant_channel": dominant,
    }


def write_particle_result(result: dict[str, Any], output_dir: str) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "particle_result.json"
    path.write_text(json.dumps(result, indent=2))
    return path
