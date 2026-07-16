"""Analytical chemistry knowledge module.

Mass spectrometry, chromatography, and spectroscopy reference data
and computation utilities.
"""

from __future__ import annotations

from enum import StrEnum
from math import log10
from typing import TypedDict

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class IonizationMethod(StrEnum):
    EI = "ei"
    ESI = "esi"
    MALDI = "maldi"
    APCI = "apci"
    APPI = "appi"
    CI = "ci"


class MassAnalyzer(StrEnum):
    QUADRUPOLE = "quadrupole"
    TOF = "tof"
    ORBITRAP = "orbitrap"
    ION_TRAP = "ion_trap"
    FT_ICR = "ft_icr"
    SECTOR = "sector"


class ChromatographyType(StrEnum):
    GC = "gc"
    HPLC = "hplc"
    UPLC = "uplc"
    IC = "ic"
    SEC = "sec"
    AFFINITY = "affinity"


class SpectroscopyType(StrEnum):
    UV_VIS = "uv_vis"
    FLUORESCENCE = "fluorescence"
    AAS = "aas"
    ICP_OES = "icp_oes"
    ICP_MS = "icp_ms"
    IR = "ir"
    RAMAN = "raman"
    NMR = "nmr"


# ---------------------------------------------------------------------------
# TypedDicts
# ---------------------------------------------------------------------------

class MassSpecPeak(TypedDict):
    mz: float
    intensity: float
    assignment: str
    delta_ppm: float


class ChromatographyMethod(TypedDict):
    name: str
    technique: str
    stationary_phase: str
    mobile_phase: str
    detector: str
    typical_analytes: list[str]


class SpectroscopyMethod(TypedDict):
    name: str
    technique: str
    wavelength_range_nm: str
    detection_limit_ppb: float
    typical_elements: list[str]


class FragmentPattern(TypedDict):
    name: str
    mass_shift: int
    formula: str
    common_source: str


class CalibrationStandard(TypedDict):
    name: str
    certified_value: float
    uncertainty: float
    unit: str
    matrix: str


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

IONIZATION_METHODS: list[dict[str, str]] = [
    {"name": "EI", "hardness": "hard", "fragments": "extensive", "mass_range": "50-600 Da", "typical_use": "GC-MS of small organics"},
    {"name": "ESI", "hardness": "soft", "fragments": "minimal", "mass_range": "100-100000+ Da", "typical_use": "LC-MS of biomolecules"},
    {"name": "MALDI", "hardness": "soft", "fragments": "minimal", "mass_range": "500-300000+ Da", "typical_use": "proteins, polymers, imaging"},
    {"name": "APCI", "hardness": "soft", "fragments": "minimal", "mass_range": "50-2000 Da", "typical_use": "LC-MS of nonpolar compounds"},
    {"name": "APPI", "hardness": "soft", "fragments": "minimal", "mass_range": "50-2000 Da", "typical_use": "nonpolar aromatics"},
    {"name": "CI", "hardness": "medium", "fragments": "moderate", "mass_range": "50-600 Da", "typical_use": "GC-MS molecular ion confirmation"},
]

COMMON_FRAGMENTS: list[FragmentPattern] = [
    {"name": "methyl loss", "mass_shift": 15, "formula": "CH3", "common_source": "alkyl chains"},
    {"name": "water loss", "mass_shift": 18, "formula": "H2O", "common_source": "alcohols"},
    {"name": "CO loss", "mass_shift": 28, "formula": "CO", "common_source": "aldehydes, ketones"},
    {"name": "ethyl loss", "mass_shift": 29, "formula": "C2H5", "common_source": "ethyl esters"},
    {"name": "methanol loss", "mass_shift": 32, "formula": "CH3OH", "common_source": "methyl esters"},
    {"name": "Cl loss", "mass_shift": 35, "formula": "Cl", "common_source": "organochlorines"},
    {"name": "COOH loss", "mass_shift": 45, "formula": "COOH", "common_source": "carboxylic acids"},
    {"name": "tropylium", "mass_shift": -1, "formula": "C7H7+", "common_source": "benzyl compounds"},
]

CHROMATOGRAPHY_METHODS: list[ChromatographyMethod] = [
    {"name": "GC-MS", "technique": "GC", "stationary_phase": "5% phenyl-methylpolysiloxane", "mobile_phase": "He carrier gas", "detector": "MS (EI)", "typical_analytes": ["VOCs", "pesticides", "PAHs"]},
    {"name": "HPLC-UV", "technique": "HPLC", "stationary_phase": "C18 reversed-phase", "mobile_phase": "water/acetonitrile gradient", "detector": "UV-Vis DAD", "typical_analytes": ["pharmaceuticals", "flavonoids"]},
    {"name": "UPLC-MS/MS", "technique": "UPLC", "stationary_phase": "C18 sub-2um", "mobile_phase": "water/methanol + 0.1% formic acid", "detector": "triple quadrupole MS", "typical_analytes": ["drug metabolites", "pesticide residues"]},
    {"name": "IC", "technique": "IC", "stationary_phase": "anion/cation exchange", "mobile_phase": "carbonate/bicarbonate buffer", "detector": "conductivity", "typical_analytes": ["anions", "cations", "organic acids"]},
    {"name": "SEC-HPLC", "technique": "SEC", "stationary_phase": "porous silica or polymer gel", "mobile_phase": "aqueous buffer or organic solvent", "detector": "RI or UV", "typical_analytes": ["proteins", "polymers"]},
]

SPECTROSCOPY_METHODS: list[SpectroscopyMethod] = [
    {"name": "UV-Vis", "technique": "UV_VIS", "wavelength_range_nm": "190-1100", "detection_limit_ppb": 100.0, "typical_elements": ["organic chromophores", "transition metals"]},
    {"name": "Fluorescence", "technique": "FLUORESCENCE", "wavelength_range_nm": "200-900", "detection_limit_ppb": 1.0, "typical_elements": ["PAHs", "fluorescent tags", "quantum dots"]},
    {"name": "AAS (Flame)", "technique": "AAS", "wavelength_range_nm": "190-900", "detection_limit_ppb": 10.0, "typical_elements": ["Na", "K", "Ca", "Mg", "Fe", "Cu", "Zn"]},
    {"name": "AAS (Graphite Furnace)", "technique": "AAS", "wavelength_range_nm": "190-900", "detection_limit_ppb": 0.1, "typical_elements": ["Pb", "Cd", "As", "Se", "Cr"]},
    {"name": "ICP-OES", "technique": "ICP_OES", "wavelength_range_nm": "167-850", "detection_limit_ppb": 1.0, "typical_elements": ["multi-element screening", "metals"]},
    {"name": "ICP-MS", "technique": "ICP_MS", "wavelength_range_nm": "all masses 2-260 amu", "detection_limit_ppb": 0.001, "typical_elements": ["ultra-trace metals", "isotope ratios"]},
    {"name": "FTIR", "technique": "IR", "wavelength_range_nm": "2500-25000 (4000-400 cm-1)", "detection_limit_ppb": 10000.0, "typical_elements": ["functional groups", "polymers", "organics"]},
    {"name": "Raman", "technique": "RAMAN", "wavelength_range_nm": "250-1064 (excitation)", "detection_limit_ppb": 10000.0, "typical_elements": ["crystal polymorphs", "carbon materials"]},
]

CALIBRATION_STANDARDS: list[CalibrationStandard] = [
    {"name": "NIST SRM 1643f", "certified_value": 1.0, "uncertainty": 0.02, "unit": "ug/L", "matrix": "water (trace elements)"},
    {"name": "NIST SRM 3100 series", "certified_value": 1000.0, "uncertainty": 5.0, "unit": "mg/L", "matrix": "single-element standards"},
    {"name": "NIST SRM 1570a", "certified_value": 0.5, "uncertainty": 0.05, "unit": "mg/kg", "matrix": "spinach leaves"},
    {"name": "NIST SRM 2584", "certified_value": 100.0, "uncertainty": 2.0, "unit": "mg/kg", "matrix": "indoor dust (Pb)"},
    {"name": "EPA Method 8270 mix", "certified_value": 2000.0, "uncertainty": 20.0, "unit": "ug/mL", "matrix": "semivolatile organics in DCM"},
]

RETENTION_INDEX_REFERENCES: list[dict[str, object]] = [
    {"alkane": "n-hexane", "carbon_number": 6, "retention_index": 600, "boiling_point_C": 69.0},
    {"alkane": "n-heptane", "carbon_number": 7, "retention_index": 700, "boiling_point_C": 98.4},
    {"alkane": "n-octane", "carbon_number": 8, "retention_index": 800, "boiling_point_C": 125.7},
    {"alkane": "n-nonane", "carbon_number": 9, "retention_index": 900, "boiling_point_C": 150.8},
    {"alkane": "n-decane", "carbon_number": 10, "retention_index": 1000, "boiling_point_C": 174.1},
    {"alkane": "n-undecane", "carbon_number": 11, "retention_index": 1100, "boiling_point_C": 195.9},
    {"alkane": "n-dodecane", "carbon_number": 12, "retention_index": 1200, "boiling_point_C": 216.3},
    {"alkane": "n-tridecane", "carbon_number": 13, "retention_index": 1300, "boiling_point_C": 235.4},
    {"alkane": "n-tetradecane", "carbon_number": 14, "retention_index": 1400, "boiling_point_C": 253.5},
    {"alkane": "n-pentadecane", "carbon_number": 15, "retention_index": 1500, "boiling_point_C": 270.6},
]


# ---------------------------------------------------------------------------
# Computation functions
# ---------------------------------------------------------------------------

def identify_from_mass_spectrum(peaks: list[MassSpecPeak]) -> dict[str, object]:
    """Attempt to identify a compound from its mass spectrum peaks.

    Matches against known fragment patterns. Returns candidate fragments
    and a simple similarity score.

    Args:
        peaks: List of MassSpecPeak dicts with mz, intensity, assignment, delta_ppm.

    Returns:
        Dict with matched_fragments and match_count.
    """
    if not peaks:
        return {"matched_fragments": [], "match_count": 0}

    matched: list[str] = []
    for peak in peaks:
        mz = peak["mz"]
        for fragment in COMMON_FRAGMENTS:
            mass_shift = fragment["mass_shift"]
            if mass_shift > 0 and abs(mz - mass_shift) < 1.0:
                matched.append(fragment["name"])

    return {"matched_fragments": matched, "match_count": len(matched)}


def compute_retention_index(tr: float, tr_ref_low: float, tr_ref_high: float, n_low: int) -> float:
    """Compute Kovats retention index for a compound on a non-polar column.

    RI = 100 * [n + (log(tr) - log(tr_low)) / (log(tr_high) - log(tr_low))]

    For isothermal GC with n-alkane references.

    Args:
        tr: Adjusted retention time of the analyte in minutes.
        tr_ref_low: Adjusted retention time of the n-alkane with n carbons (eluting before analyte).
        tr_ref_high: Adjusted retention time of the n-alkane with n+1 carbons (eluting after analyte).
        n_low: Carbon number of the earlier-eluting n-alkane reference.

    Returns:
        Kovats retention index (dimensionless).

    Raises:
        ValueError: If tr is not between tr_ref_low and tr_ref_high (inclusive),
                    or if n_low < 1, or if any retention time <= 0.
    """
    if n_low < 1:
        raise ValueError(f"Carbon number must be >= 1, got {n_low}")
    if any(t <= 0 for t in (tr, tr_ref_low, tr_ref_high)):
        raise ValueError("All retention times must be positive")
    if not (tr_ref_low <= tr <= tr_ref_high):
        raise ValueError(
            f"Analyte retention time {tr} not between references [{tr_ref_low}, {tr_ref_high}]"
        )

    log_tr = log10(tr)
    log_low = log10(tr_ref_low)
    log_high = log10(tr_ref_high)
    return 100.0 * (n_low + (log_tr - log_low) / (log_high - log_low))


def calibrate_instrument(
    standards: list[CalibrationStandard], readings: list[float]
) -> dict[str, object]:
    """Perform a simple linear calibration from standards and instrument readings.

    Computes slope via linear regression through origin (single-standard)
    or least-squares for multiple standards. Returns calibration parameters.

    Args:
        standards: List of CalibrationStandard dicts with certified_value and unit.
        readings: Corresponding instrument responses.

    Returns:
        Dict with slope, intercept, r_squared, and calibration_valid (bool).

    Raises:
        ValueError: If standards and readings lengths differ, or either is empty.
    """
    if not standards or not readings:
        raise ValueError("Standards and readings must be non-empty")
    if len(standards) != len(readings):
        raise ValueError(
            f"Length mismatch: {len(standards)} standards vs {len(readings)} readings"
        )

    x = [s["certified_value"] for s in standards]
    y = list(readings)
    n = len(x)

    if n == 1:
        return {
            "slope": y[0] / x[0] if x[0] != 0 else 0.0,
            "intercept": 0.0,
            "r_squared": 1.0,
            "calibration_valid": x[0] != 0,
        }

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    ss_xy = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y, strict=False))
    ss_xx = sum((xi - mean_x) ** 2 for xi in x)
    ss_yy = sum((yi - mean_y) ** 2 for yi in y)

    if ss_xx == 0:
        raise ValueError("All standards have the same certified value; cannot compute slope")

    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x
    r_squared = (ss_xy ** 2) / (ss_xx * ss_yy) if ss_xx * ss_yy != 0 else 0.0
    if r_squared < 0:
        r_squared = 0.0

    return {
        "slope": slope,
        "intercept": intercept,
        "r_squared": r_squared,
        "calibration_valid": r_squared >= 0.95,
    }
