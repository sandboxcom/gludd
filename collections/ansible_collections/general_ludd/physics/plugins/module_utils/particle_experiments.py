"""Knowledge tables for major particle experiments, detectors, and surveys."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


ACCELERATORS: dict[str, dict[str, Any]] = {
    "LHC": {
        "full_name": "Large Hadron Collider",
        "type": "proton-proton and heavy-ion collider",
        "location": "CERN, Geneva, Switzerland",
        "status": "operational",
        "beam_energies": {"pp": 6.8, "PbPb": 2.68},
        "experiments": ["ATLAS", "CMS", "LHCb", "ALICE"],
        "key_discoveries": ["Higgs boson"],
    },
    "HL_LHC": {
        "full_name": "High-Luminosity Large Hadron Collider",
        "type": "proton collider upgrade",
        "location": "CERN, Geneva, Switzerland",
        "status": "under_construction",
        "beam_energies": {"pp": 7.0},
        "experiments": ["ATLAS", "CMS"],
        "key_discoveries": [],
    },
    "Tevatron": {
        "full_name": "Tevatron",
        "type": "proton-antiproton collider",
        "location": "Fermilab, Illinois, United States",
        "status": "decommissioned",
        "beam_energies": {"ppbar": 0.98},
        "experiments": ["CDF", "D0"],
        "key_discoveries": ["top quark"],
    },
    "RHIC": {
        "full_name": "Relativistic Heavy Ion Collider",
        "type": "heavy-ion and polarized proton collider",
        "location": "Brookhaven National Laboratory, New York, United States",
        "status": "operational",
        "beam_energies": {"AuAu": 0.1, "pp": 0.255},
        "experiments": ["STAR", "sPHENIX"],
        "key_discoveries": ["quark-gluon plasma signatures"],
    },
    "SuperKEKB": {
        "full_name": "SuperKEKB",
        "type": "electron-positron collider",
        "location": "KEK, Tsukuba, Japan",
        "status": "operational",
        "beam_energies": {"e+": 4.0, "e-": 7.0},
        "experiments": ["Belle II"],
        "key_discoveries": [],
        "integrated_luminosity_target": 50.0,
    },
    "KEKB": {
        "full_name": "KEKB",
        "type": "electron-positron collider",
        "location": "KEK, Tsukuba, Japan",
        "status": "decommissioned",
        "beam_energies": {"e+": 3.5, "e-": 8.0},
        "experiments": ["Belle"],
        "key_discoveries": ["CP violation in B mesons"],
    },
    "ILC": {
        "full_name": "International Linear Collider",
        "type": "electron-positron linear collider",
        "location": "proposed",
        "status": "proposed",
        "beam_energies": {"ee": 0.25},
        "experiments": [],
        "key_discoveries": [],
    },
    "CLIC": {
        "full_name": "Compact Linear Collider",
        "type": "electron-positron linear collider",
        "location": "CERN design study",
        "status": "proposed",
        "beam_energies": {"ee": 0.38},
        "experiments": [],
        "key_discoveries": [],
    },
    "EIC": {
        "full_name": "Electron-Ion Collider",
        "type": "electron-proton and electron-ion collider",
        "location": "Brookhaven National Laboratory, New York, United States",
        "status": "under_construction",
        "beam_energies": {"ep": 0.14},
        "experiments": ["ePIC"],
        "key_discoveries": [],
    },
    "FCC": {
        "full_name": "Future Circular Collider",
        "type": "proton-proton and electron-positron collider",
        "location": "CERN feasibility study",
        "status": "feasibility_study",
        "beam_energies": {"pp": 50.0, "ee": 0.1825},
        "experiments": [],
        "key_discoveries": [],
    },
    "muon_collider": {
        "full_name": "Muon Collider",
        "type": "muon-antimuon collider",
        "location": "concept study",
        "status": "concept_study",
        "beam_energy_tev": [3.0, 10.0],
        "beam_energies": {"mumu": 5.0},
        "experiments": [],
        "key_discoveries": [],
    },
}


DETECTORS: dict[str, dict[str, Any]] = {
    "ATLAS": {
        "name": "ATLAS",
        "full_name": "ATLAS Experiment",
        "collider": "LHC",
        "type": "general purpose",
        "subdetectors": {
            "inner_tracker": "silicon pixel, strip, and transition-radiation tracking",
            "calorimeters": "liquid-argon electromagnetic and tile hadronic calorimeters",
            "muon_spectrometer": "air-core toroids and precision muon chambers",
        },
        "magnet_system": {"solenoid": "2 T", "toroid": "0.5 T"},
        "physics_program": ["Higgs", "Standard Model", "BSM searches"],
        "notable_results": ["Higgs boson discovery"],
    },
    "CMS": {
        "name": "CMS",
        "full_name": "Compact Muon Solenoid",
        "collider": "LHC",
        "type": "general purpose",
        "subdetectors": {
            "inner_tracker": "silicon pixel and strip tracking",
            "calorimeters": "crystal electromagnetic and brass-scintillator hadronic calorimeters",
            "muon_spectrometer": "drift tubes, cathode strip chambers, and RPCs",
        },
        "magnet_system": {"solenoid": "3.8 T"},
        "physics_program": ["Higgs", "top quark", "heavy ions", "BSM searches"],
        "notable_results": ["Higgs boson discovery"],
    },
    "LHCb": {
        "name": "LHCb",
        "full_name": "Large Hadron Collider beauty experiment",
        "collider": "LHC",
        "type": "forward flavor physics",
        "subdetectors": {"vertex_locator": "VELO", "ring_imaging_cherenkov": "RICH"},
        "magnet_system": {"dipole": "4 Tm"},
        "physics_program": ["CP violation", "rare decays", "heavy flavor"],
        "notable_results": ["pentaquark candidates"],
    },
    "ALICE": {
        "name": "ALICE",
        "full_name": "A Large Ion Collider Experiment",
        "collider": "LHC",
        "type": "heavy-ion dedicated",
        "subdetectors": {"tracking": "ITS and TPC", "pid": "TOF and RICH"},
        "magnet_system": {"solenoid": "0.5 T"},
        "physics_program": ["quark-gluon plasma", "heavy-ion collisions"],
        "notable_results": ["collective flow in small systems"],
    },
    "Belle_II": {
        "name": "Belle II",
        "full_name": "Belle II Experiment",
        "collider": "SuperKEKB",
        "type": "flavor factory detector",
        "subdetectors": {"vertex": "PXD and SVD", "calorimeter": "CsI(Tl)"},
        "magnet_system": {"solenoid": "1.5 T"},
        "physics_program": ["B physics", "tau physics", "dark sector"],
        "notable_results": [],
    },
    "CDF": {
        "name": "CDF",
        "full_name": "Collider Detector at Fermilab",
        "collider": "Tevatron",
        "type": "general purpose",
        "subdetectors": {"tracking": "central outer tracker"},
        "magnet_system": {"solenoid": "1.4 T"},
        "physics_program": ["top quark", "electroweak", "QCD"],
        "notable_results": ["top quark discovery"],
    },
    "D0": {
        "name": "D0",
        "full_name": "DZero Experiment",
        "collider": "Tevatron",
        "type": "general purpose",
        "subdetectors": {"calorimeter": "uranium-liquid argon calorimeter"},
        "magnet_system": {"solenoid": "2 T"},
        "physics_program": ["top quark", "electroweak", "QCD"],
        "notable_results": ["top quark discovery"],
    },
    "STAR": {
        "name": "STAR",
        "full_name": "Solenoidal Tracker at RHIC",
        "collider": "RHIC",
        "type": "heavy-ion and spin physics",
        "subdetectors": {"tracking": "time projection chamber"},
        "magnet_system": {"solenoid": "0.5 T"},
        "physics_program": ["QGP", "spin structure", "beam energy scan"],
        "notable_results": ["strongly coupled QGP"],
    },
}


SKY_SURVEYS: dict[str, dict[str, Any]] = {
    "SDSS": {
        "full_name": "Sloan Digital Sky Survey",
        "coverage_type": "northern",
        "dec_range": (-10.0, 85.0),
        "wavebands": ["u", "g", "r", "i", "z"],
        "limiting_magnitude": "r~22.5",
        "science_goals": ["galaxy evolution", "large-scale structure", "quasars"],
        "data_release": "DR18",
    },
    "Pan_STARRS": {
        "full_name": "Panoramic Survey Telescope and Rapid Response System",
        "coverage_type": "northern",
        "dec_range": (-30.0, 90.0),
        "wavebands": ["g", "r", "i", "z", "y"],
        "limiting_magnitude": "r~23.3",
        "science_goals": ["transients", "solar system", "Milky Way"],
        "data_release": "DR2",
    },
    "DES": {
        "full_name": "Dark Energy Survey",
        "coverage_type": "southern",
        "dec_range": (-65.0, 5.0),
        "wavebands": ["g", "r", "i", "z", "Y"],
        "limiting_magnitude": "i~24",
        "science_goals": ["dark energy", "weak lensing", "galaxy clusters"],
        "data_release": "DR2",
    },
    "LSST": {
        "full_name": "Legacy Survey of Space and Time",
        "coverage_type": "southern",
        "dec_range": (-90.0, 15.0),
        "wavebands": ["u", "g", "r", "i", "z", "y"],
        "limiting_magnitude": "r~24.5 single visit",
        "science_goals": ["dark energy", "time domain", "solar system", "Milky Way"],
        "data_rate": "15-20 TB/night",
        "data_release": "Data Preview / operations releases",
    },
    "Gaia": {
        "full_name": "Gaia",
        "coverage_type": "all_sky",
        "dec_range": (-90.0, 90.0),
        "wavebands": ["G", "BP", "RP"],
        "limiting_magnitude": "G~21",
        "science_goals": ["astrometry", "Milky Way structure", "stellar physics"],
        "astrometric_precision": "microarcsec precision for bright stars",
        "data_release": "DR3",
    },
    "Euclid": {
        "full_name": "Euclid",
        "coverage_type": "extragalactic",
        "dec_range": (-75.0, 75.0),
        "wavebands": ["VIS", "Y", "J", "H"],
        "limiting_magnitude": "VIS~24.5",
        "science_goals": ["dark energy and dark matter mapping", "weak lensing"],
        "data_release": "Q1 data release",
    },
    "JWST": {
        "full_name": "James Webb Space Telescope",
        "coverage_type": "pointed",
        "dec_range": (-90.0, 90.0),
        "wavebands": ["near-infrared", "mid-infrared"],
        "limiting_magnitude": "program dependent",
        "telescope": "6.5 m infrared space telescope",
    },
}


def get_experiment_info(name: str) -> dict[str, Any] | None:
    """Return accelerator, detector, or survey metadata by name."""
    for table in (ACCELERATORS, DETECTORS, SKY_SURVEYS):
        if name in table:
            return deepcopy(table[name])
    return None


def get_detector_capabilities(name: str) -> dict[str, Any] | None:
    """Return detector capabilities by detector name."""
    detector = DETECTORS.get(name)
    return deepcopy(detector) if detector is not None else None


def search_sky_survey(ra_deg: float, dec_deg: float) -> list[dict[str, Any]]:
    """List surveys with declination coverage for the supplied coordinates."""
    _ = ra_deg % 360.0
    results: list[dict[str, Any]] = []
    for name, survey in SKY_SURVEYS.items():
        dec_min, dec_max = survey.get("dec_range", (-90.0, 90.0))
        if float(dec_min) <= dec_deg <= float(dec_max):
            results.append(
                {
                    "survey": name,
                    "has_coverage": True,
                    "wavebands": list(survey.get("wavebands", [])),
                    "limiting_magnitude": survey.get("limiting_magnitude"),
                }
            )
    return results


def list_accelerators_by_type(accelerator_type: str) -> list[str]:
    """Return accelerator names whose type includes the requested text."""
    needle = accelerator_type.lower()
    return [name for name, data in ACCELERATORS.items() if needle in data["type"].lower()]


def list_accelerators_by_status(status: str) -> list[str]:
    """Return accelerator names with the exact requested status."""
    needle = status.lower()
    return [name for name, data in ACCELERATORS.items() if data["status"].lower() == needle]


def list_detectors_by_collider() -> dict[str, list[str]]:
    """Group detector names by collider."""
    grouped: dict[str, list[str]] = {}
    for name, detector in DETECTORS.items():
        grouped.setdefault(detector["collider"], []).append(name)
    return grouped


def get_running_status(name: str) -> dict[str, Any] | None:
    """Return the operating status for an accelerator by name."""
    accelerator = ACCELERATORS.get(name)
    if accelerator is None:
        return None
    return {"name": name, "status": accelerator["status"], "location": accelerator["location"]}


def list_sky_surveys_by_coverage(coverage_type: str) -> list[str]:
    """Return sky surveys with the requested coverage category."""
    needle = coverage_type.lower()
    return [
        name
        for name, survey in SKY_SURVEYS.items()
        if str(survey.get("coverage_type", "")).lower() == needle
    ]


def get_survey_data_release(name: str) -> str | None:
    """Return the latest data-release label when this table tracks one."""
    survey = SKY_SURVEYS.get(name)
    if survey is None:
        return None
    release = survey.get("data_release")
    return str(release) if release is not None else None
