"""
materials_forensics -- Fingerprint classification, DNA profile matching,
and trace evidence analysis.

Public surface:
    FingerprintPattern          -- enum-like dataclass for fingerprint patterns
    classify_fingerprint(data)  -> dict
    match_dna_profile(sample, reference, analysis_type) -> dict
    analyze_trace_evidence(evidence_type, sample_data, reference_data) -> dict

Data tables:
    FINGERPRINT_PATTERNS        -- dict[pattern] -> pattern metadata
    FINGERPRINT_MINUTIAE_TYPES  -- dict[minutia] -> description
    DNA_LOCI                    -- dict[locus] -> chromosome + repeat info
    DNA_ANALYSIS_TYPES          -- dict[type] -> analysis parameters
    TRACE_EVIDENCE_TYPES        -- dict[type] -> measurement keys
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ═══════════════════════════════════════════════════════════════════
# Fingerprint data tables
# ═══════════════════════════════════════════════════════════════════

FINGERPRINT_PATTERNS: dict[str, dict[str, Any]] = {
    "ARCH": {
        "description": "Ridges flow from one side to the other without recurving",
        "subtypes": ["plain_arch", "tented_arch"],
        "frequency_pct": 5.0,
        "minutiae_density": "low",
        "ridge_flow": "horizontal across pattern",
    },
    "LOOP": {
        "description": "Ridges flow inward and recurve upon themselves",
        "subtypes": ["ulnar_loop", "radial_loop", "double_loop"],
        "frequency_pct": 60.0,
        "minutiae_density": "moderate",
        "ridge_flow": "recurves toward thumb or radius",
    },
    "WHORL": {
        "description": "Ridges form a circular or spiral pattern around a central point",
        "subtypes": ["plain_whorl", "central_pocket_loop", "double_loop_whorl", "accidental_whorl"],
        "frequency_pct": 35.0,
        "minutiae_density": "high",
        "ridge_flow": "concentric or spiral around core",
    },
}

FINGERPRINT_MINUTIAE_TYPES: dict[str, dict[str, Any]] = {
    "RIDGE_ENDING": {"description": "Ridge terminates abruptly", "reliability": "high",
                     "symbol": "|", "typical_count_per_pattern": {"ARCH": 25, "LOOP": 35, "WHORL": 45}},
    "BIFURCATION": {"description": "Ridge splits into two branches", "reliability": "high",
                    "symbol": "Y", "typical_count_per_pattern": {"ARCH": 30, "LOOP": 40, "WHORL": 50}},
    "DOT": {"description": "Very short ridge fragment, isolated point", "reliability": "medium",
            "symbol": "\u00b7", "typical_count_per_pattern": {"ARCH": 5, "LOOP": 8, "WHORL": 10}},
    "ENCLOSURE": {"description": "Single ridge that bifurcates and rejoins (lake/eye)", "reliability": "high",
                  "symbol": "O", "typical_count_per_pattern": {"ARCH": 3, "LOOP": 5, "WHORL": 7}},
    "SHORT_RIDGE": {"description": "Isolated ridge segment", "reliability": "medium",
                    "symbol": "-", "typical_count_per_pattern": {"ARCH": 10, "LOOP": 15, "WHORL": 18}},
    "BRIDGE": {"description": "Short ridge connecting two parallel ridges", "reliability": "high",
               "symbol": "=", "typical_count_per_pattern": {"ARCH": 2, "LOOP": 4, "WHORL": 5}},
    "SPUR": {"description": "Bifurcation with one branch significantly shorter", "reliability": "medium",
             "symbol": "\u252c", "typical_count_per_pattern": {"ARCH": 4, "LOOP": 6, "WHORL": 7}},
    "CROSSOVER": {"description": "Two ridges cross at an intersection", "reliability": "low",
                  "symbol": "X", "typical_count_per_pattern": {"ARCH": 2, "LOOP": 3, "WHORL": 5}},
    "TRIFURCATION": {"description": "Ridge splits into three branches", "reliability": "low",
                     "symbol": "\u03a8", "typical_count_per_pattern": {"ARCH": 1, "LOOP": 2, "WHORL": 3}},
}

# ═══════════════════════════════════════════════════════════════════
# DNA data tables
# ═══════════════════════════════════════════════════════════════════

DNA_LOCI: dict[str, dict[str, Any]] = {
    "D3S1358": {"chromosome": 3, "repeat_motif": "TCTA", "repeat_range": "8-20",
                "allelic_ladder": [12, 13, 14, 15, 16, 17, 18, 19]},
    "vWA": {"chromosome": 12, "repeat_motif": "TCTA", "repeat_range": "10-25",
            "allelic_ladder": [11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21]},
    "FGA": {"chromosome": 4, "repeat_motif": "CTTT", "repeat_range": "12-51",
            "allelic_ladder": [17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30]},
    "D8S1179": {"chromosome": 8, "repeat_motif": "TCTA", "repeat_range": "7-20",
                "allelic_ladder": [8, 9, 10, 11, 12, 13, 14, 15, 16]},
    "D21S11": {
        "chromosome": 21,
        "repeat_motif": "TCTA",
        "repeat_range": "24-38",
        "allelic_ladder": [
            24, 25, 26, 27, 28, 29, 30, 30.2, 31, 31.2, 32, 32.2,
            33, 33.2, 34, 34.2, 35, 35.2, 36, 37, 38,
        ],
    },
    "D18S51": {"chromosome": 18, "repeat_motif": "AGAA", "repeat_range": "7-28",
               "allelic_ladder": [7, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24]},
    "D5S818": {"chromosome": 5, "repeat_motif": "AGAT", "repeat_range": "6-18",
               "allelic_ladder": [7, 8, 9, 10, 11, 12, 13, 14, 15]},
    "D13S317": {"chromosome": 13, "repeat_motif": "TATC", "repeat_range": "5-16",
                "allelic_ladder": [7, 8, 9, 10, 11, 12, 13, 14]},
    "D7S820": {"chromosome": 7, "repeat_motif": "GATA", "repeat_range": "5-16",
               "allelic_ladder": [6, 7, 8, 9, 10, 11, 12, 13, 14]},
    "D16S539": {"chromosome": 16, "repeat_motif": "GATA", "repeat_range": "5-16",
                "allelic_ladder": [5, 8, 9, 10, 11, 12, 13, 14]},
    "CSF1PO": {"chromosome": 5, "repeat_motif": "AGAT", "repeat_range": "5-16",
               "allelic_ladder": [6, 7, 8, 9, 10, 11, 12, 13, 14]},
    "TPOX": {"chromosome": 2, "repeat_motif": "AATG", "repeat_range": "5-16",
             "allelic_ladder": [6, 7, 8, 9, 10, 11, 12, 13]},
    "TH01": {"chromosome": 11, "repeat_motif": "TCAT", "repeat_range": "3-14",
             "allelic_ladder": [4, 5, 6, 7, 8, 9, 9.3, 10, 11]},
    "D2S1338": {"chromosome": 2, "repeat_motif": "TGCC", "repeat_range": "10-30",
                "allelic_ladder": [15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27]},
    "D19S433": {"chromosome": 19, "repeat_motif": "AAGG", "repeat_range": "7-19",
                "allelic_ladder": [9, 10, 11, 12, 13, 14, 14.2, 15, 15.2, 16, 16.2, 17, 17.2, 18, 18.2, 19]},
    "AMEL": {"chromosome": "X+Y", "repeat_motif": "N/A", "repeat_range": "N/A",
             "allelic_ladder": ["X", "Y"], "notes": "Sex-determining locus"},
}

DNA_ANALYSIS_TYPES: dict[str, dict[str, Any]] = {
    "str": {
        "description": "Short Tandem Repeat analysis of autosomal STR markers",
        "target_loci": [
            "D3S1358", "vWA", "FGA", "TH01", "TPOX", "CSF1PO",
            "D5S818", "D7S820", "D8S1179", "D13S317", "D16S539",
            "D18S51", "D21S11",
        ],
        "match_threshold": 0.5,
        "inheritance": "autosomal",
    },
    "codis": {
        "description": "CODIS 20-loci expanded core set (FBI standard)",
        "target_loci": [
            "D3S1358", "vWA", "FGA", "TH01", "TPOX", "CSF1PO",
            "D5S818", "D7S820", "D8S1179", "D13S317", "D16S539",
            "D18S51", "D21S11", "D1S1656", "D2S441", "D2S1338",
            "D10S1248", "D12S391", "D19S433", "D22S1045", "AMEL",
        ],
        "match_threshold": 0.6,
        "inheritance": "autosomal",
    },
    "mtdna": {
        "description": "Mitochondrial DNA analysis (maternal lineage)",
        "target_loci": ["HV1", "HV2", "HV3"],
        "match_threshold": 0.7,
        "inheritance": "maternal",
    },
    "ychromosome": {
        "description": "Y-chromosome STR analysis (paternal lineage)",
        "target_loci": [
            "DYS19", "DYS385a", "DYS385b", "DYS389I", "DYS389II",
            "DYS390", "DYS391", "DYS392", "DYS393", "DYS437",
            "DYS438", "DYS439", "DYS448", "DYS456", "DYS458",
            "DYS635", "YGATAH4",
        ],
        "match_threshold": 0.5,
        "inheritance": "paternal",
    },
}

# ═══════════════════════════════════════════════════════════════════
# Trace evidence data tables
# ═══════════════════════════════════════════════════════════════════

TRACE_EVIDENCE_TYPES: dict[str, dict[str, Any]] = {
    "fiber": {
        "description": "Textile fiber analysis",
        "measurements": ["color", "diameter_um", "cross_section", "material",
                         "birefringence", "melting_point_c", "dye_composition"],
        "match_fields": ["color", "material", "cross_section"],
        "match_tolerance": {"diameter_um": 5.0},
    },
    "hair": {
        "description": "Hair morphology analysis (human/animal origin)",
        "measurements": ["color", "length_mm", "diameter_um", "medulla_pattern",
                         "cuticle_pattern", "pigment_distribution", "root_type"],
        "match_fields": ["color", "medulla_pattern", "cuticle_pattern"],
        "match_tolerance": {"diameter_um": 10.0, "length_mm": 5.0},
    },
    "glass": {
        "description": "Glass fragment refractive index and composition analysis",
        "measurements": ["refractive_index", "density_g_ml", "elemental_composition",
                         "thickness_mm", "color", "fluorescence"],
        "match_fields": ["color", "fluorescence"],
        "match_tolerance": {"refractive_index": 0.0005, "density_g_ml": 0.02, "thickness_mm": 0.1},
    },
    "paint": {
        "description": "Automotive and architectural paint layer analysis",
        "measurements": ["color", "layer_count", "layer_thickness_um",
                         "binder_type", "pigment_composition", "solvent_type"],
        "match_fields": ["color", "binder_type", "solvent_type"],
        "match_tolerance": {"layer_thickness_um": 5.0},
    },
    "soil": {
        "description": "Soil/sediment mineralogical and chemical analysis",
        "measurements": ["color", "ph", "particle_size_distribution",
                         "mineral_composition", "organic_content_pct", "moisture_pct"],
        "match_fields": ["color", "mineral_composition"],
        "match_tolerance": {"ph": 0.5, "organic_content_pct": 5.0, "moisture_pct": 5.0},
    },
    "gsr": {
        "description": "Gunshot residue (GSR) particle analysis",
        "measurements": ["particle_count", "composition", "particle_size_um",
                         "morphology", "lead_present", "barium_present", "antimony_present"],
        "match_fields": ["composition", "morphology"],
        "match_tolerance": {"particle_size_um": 2.0},
    },
    "toolmark": {
        "description": "Tool mark striation and impression analysis",
        "measurements": ["tool_type", "mark_width_mm", "mark_depth_mm",
                         "striation_count", "striation_pattern", "angle_deg"],
        "match_fields": ["tool_type", "striation_pattern"],
        "match_tolerance": {"mark_width_mm": 0.1, "mark_depth_mm": 0.05, "angle_deg": 2.0},
    },
    "footwear": {
        "description": "Footwear impression pattern and wear analysis",
        "measurements": ["pattern_type", "size", "wear_pattern",
                         "tread_depth_mm", "manufacturing_defects", "acquired_damage"],
        "match_fields": ["pattern_type", "manufacturing_defects", "acquired_damage"],
        "match_tolerance": {"tread_depth_mm": 0.5},
    },
    "tire": {
        "description": "Tire track impression and tread wear analysis",
        "measurements": ["tread_pattern", "track_width_mm", "wheelbase_mm",
                         "tread_depth_mm", "wear_pattern", "manufacturing_defects"],
        "match_fields": ["tread_pattern", "manufacturing_defects"],
        "match_tolerance": {"track_width_mm": 5.0, "wheelbase_mm": 10.0, "tread_depth_mm": 0.5},
    },
}

# ═══════════════════════════════════════════════════════════════════
# Dataclasses
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FingerprintPattern:
    """A classified fingerprint pattern with minutiae details."""

    pattern_type: str
    subtype: str | None = None
    confidence: float = 0.0
    minutiae_count: int = 0
    minutiae_points: list[dict[str, Any]] = field(default_factory=list)
    core_location: tuple[float, float] | None = None
    delta_locations: list[tuple[float, float]] = field(default_factory=list)
    ridge_count: int = 0
    quality_score: float = 0.0
    notes: str = ""

    def __post_init__(self) -> None:
        if self.pattern_type not in FINGERPRINT_PATTERNS:
            raise ValueError(
                f"Unknown pattern_type '{self.pattern_type}'. "
                f"Valid: {sorted(FINGERPRINT_PATTERNS.keys())}"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be 0.0-1.0, got {self.confidence}")
        if not 0.0 <= self.quality_score <= 1.0:
            raise ValueError(f"quality_score must be 0.0-1.0, got {self.quality_score}")


# ═══════════════════════════════════════════════════════════════════
# Fingerprint classification
# ═══════════════════════════════════════════════════════════════════

def classify_fingerprint(data: dict[str, Any]) -> dict[str, Any]:
    """Classify a fingerprint pattern from ridge-flow and minutiae data.

    Args:
        data: dict with keys: ridge_flow_description, minutiae_list,
              core_present, delta_count, ridge_count (all optional).

    Returns:
        dict with: pattern_type, subtype, confidence, minutiae_count,
        minutiae_summary, core_detected, delta_count, ridge_count,
        quality_score, notes.
    """
    if not isinstance(data, dict):
        raise TypeError(f"Expected dict, got {type(data).__name__}")
    ridge_flow = str(data.get("ridge_flow_description", "")).lower()
    minutiae_list = data.get("minutiae_list", [])
    core_present = bool(data.get("core_present", False))
    delta_count = int(data.get("delta_count", 0))
    ridge_count = int(data.get("ridge_count", 0))
    quality_score = float(data.get("quality_score", 0.5))

    minutiae_count = len(minutiae_list) if isinstance(minutiae_list, list) else 0

    pattern_type = _determine_pattern(ridge_flow, core_present, delta_count)
    subtype = _determine_subtype(pattern_type, ridge_flow, delta_count, minutiae_list)
    confidence = _compute_fingerprint_confidence(pattern_type, minutiae_count,
                                                  core_present, quality_score)

    minutiae_summary: dict[str, int] = {}
    if isinstance(minutiae_list, list):
        for m in minutiae_list:
            if isinstance(m, dict):
                mt = m.get("type", "UNKNOWN")
                minutiae_summary[mt] = minutiae_summary.get(mt, 0) + 1

    notes_parts: list[str] = []
    typical = FINGERPRINT_MINUTIAE_TYPES.get("RIDGE_ENDING", {}).get(
        "typical_count_per_pattern", {}).get(pattern_type, 0)
    if minutiae_count < typical * 0.5:
        notes_parts.append(f"Low minutiae count ({minutiae_count}) for {pattern_type}")
    if quality_score < 0.4:
        notes_parts.append("Poor ridge quality")

    return {
        "pattern_type": pattern_type,
        "subtype": subtype,
        "confidence": round(confidence, 4),
        "minutiae_count": minutiae_count,
        "minutiae_summary": minutiae_summary,
        "core_detected": core_present,
        "delta_count": delta_count,
        "ridge_count": ridge_count,
        "quality_score": quality_score,
        "notes": "; ".join(notes_parts) if notes_parts else "No anomalies",
    }


def _determine_pattern(ridge_flow: str, core_present: bool, delta_count: int) -> str:
    """Classify pattern based on ridge flow and delta count."""
    if delta_count < 0:
        return "ARCH"
    if not core_present and delta_count == 0:
        return "ARCH"
    if "tent" in ridge_flow or "upthrust" in ridge_flow:
        return "ARCH"
    if ("recurve" in ridge_flow or "loop" in ridge_flow) and delta_count <= 1:
        return "LOOP"
    if delta_count >= 2 or "whorl" in ridge_flow or "concentric" in ridge_flow or "spiral" in ridge_flow:
        return "WHORL"
    if core_present and delta_count == 1:
        return "LOOP"
    return "ARCH"


def _determine_subtype(
    pattern_type: str, ridge_flow: str, delta_count: int, minutiae_list: list[Any]
) -> str | None:
    """Determine subtype from ridge flow and minutiae characteristics."""
    subtypes = FINGERPRINT_PATTERNS.get(pattern_type, {}).get("subtypes", [])
    if not subtypes:
        return None
    if pattern_type == "ARCH":
        if "tent" in ridge_flow or "upthrust" in ridge_flow:
            return "tented_arch"
        return str(subtypes[0])
    if pattern_type == "LOOP":
        if "ulnar" in ridge_flow or delta_count == 1:
            return "ulnar_loop"
        if "radial" in ridge_flow:
            return "radial_loop"
        return str(subtypes[0])
    if pattern_type == "WHORL":
        if delta_count >= 3:
            return "accidental_whorl"
        if delta_count == 2:
            return "plain_whorl"
        return str(subtypes[0])
    return str(subtypes[0]) if subtypes else None


def _compute_fingerprint_confidence(
    pattern_type: str, minutiae_count: int, core_present: bool, quality_score: float
) -> float:
    """Compute confidence score from available data quality."""
    base = 0.5
    if core_present:
        base += 0.15
    typical = FINGERPRINT_MINUTIAE_TYPES.get("RIDGE_ENDING", {}).get(
        "typical_count_per_pattern", {}).get(pattern_type, 35)
    if minutiae_count > 0:
        ratio = min(minutiae_count / max(typical, 1), 1.5)
        base += ratio * 0.2
    base += quality_score * 0.15
    return max(0.0, min(base, 1.0))


# ═══════════════════════════════════════════════════════════════════
# DNA profile matching
# ═══════════════════════════════════════════════════════════════════

def match_dna_profile(
    sample: dict[str, Any],
    reference: dict[str, Any],
    analysis_type: str = "str",
) -> dict[str, Any]:
    """Compare a sample DNA profile against a reference profile.

    Args:
        sample: dict with 'loci' key mapping locus names to allele lists,
                or a dict-pattern of {'D3S1358': [15, 17], ...}.
                Also supports 'id' and 'metadata' keys.
        reference: Same shape as sample, for comparison.
        analysis_type: 'str', 'codis', 'mtdna', or 'ychromosome'.

    Returns:
        dict with: analysis_type, match_result, probability, loci_count,
        matched_loci, mismatched_loci, excluded_loci, total_loci,
        sample_id, reference_id, notes.
    """
    if not isinstance(sample, dict) or not isinstance(reference, dict):
        raise TypeError("sample and reference must be dicts")
    if analysis_type not in DNA_ANALYSIS_TYPES:
        raise ValueError(
            f"Unknown analysis_type '{analysis_type}'. "
            f"Valid: {sorted(DNA_ANALYSIS_TYPES.keys())}"
        )

    analysis_config = DNA_ANALYSIS_TYPES[analysis_type]
    target_loci = analysis_config.get("target_loci", [])
    match_threshold = float(analysis_config.get("match_threshold", 0.5))

    sample_loci = sample.get("loci", sample)
    reference_loci = reference.get("loci", reference)

    if not isinstance(sample_loci, dict) or not isinstance(reference_loci, dict):
        raise ValueError("sample and reference must contain a 'loci' dict")

    matched: list[str] = []
    mismatched: list[str] = []
    excluded: list[str] = []
    total_loci = 0

    for locus in target_loci:
        s_alleles = sample_loci.get(locus)
        r_alleles = reference_loci.get(locus)
        if s_alleles is None or r_alleles is None:
            excluded.append(locus)
            continue
        total_loci += 1
        if not isinstance(s_alleles, (list, tuple)):
            s_alleles = [s_alleles]
        if not isinstance(r_alleles, (list, tuple)):
            r_alleles = [r_alleles]
        if set(s_alleles) == set(r_alleles):
            matched.append(locus)
        else:
            mismatched.append(locus)

    if total_loci == 0:
        return {
            "analysis_type": analysis_type,
            "match_result": "inconclusive",
            "probability": 0.0,
            "loci_count": 0,
            "matched_loci": [],
            "mismatched_loci": [],
            "excluded_loci": excluded,
            "total_loci": 0,
            "sample_id": str(sample.get("id", "unknown")),
            "reference_id": str(reference.get("id", "unknown")),
            "notes": "No comparable loci found between sample and reference",
        }

    match_ratio = len(matched) / total_loci
    probability = _compute_dna_probability(match_ratio, total_loci, analysis_type)

    if match_ratio >= 0.95:
        match_result = "match"
    elif match_ratio >= match_threshold:
        match_result = "probable_match"
    elif match_ratio >= 0.3:
        match_result = "inconclusive"
    else:
        match_result = "exclusion"

    return {
        "analysis_type": analysis_type,
        "match_result": match_result,
        "probability": round(probability, 6),
        "loci_count": total_loci,
        "matched_loci": matched,
        "mismatched_loci": mismatched,
        "excluded_loci": excluded,
        "total_loci": total_loci,
        "sample_id": str(sample.get("id", "unknown")),
        "reference_id": str(reference.get("id", "unknown")),
        "notes": _dna_notes(match_result, matched, mismatched),
    }


def _compute_dna_probability(match_ratio: float, loci_count: int, analysis_type: str) -> float:
    """Compute match probability using a simplified likelihood model."""
    if loci_count == 0:
        return 0.0
    weight = 1.0
    if analysis_type in ("mtdna",):
        weight = 0.7
    freq = 0.1
    prob = 1.0
    for _ in range(loci_count):
        if match_ratio > 0.5:
            prob *= (1.0 - freq * weight * (1.0 - match_ratio))
        else:
            prob *= (freq * weight * (1.0 - match_ratio))
    return 1.0 - prob if match_ratio > 0.5 else prob


def _dna_notes(
    match_result: str, matched: list[str], mismatched: list[str]
) -> str:
    """Generate human-readable notes about the DNA comparison."""
    if match_result == "match":
        return f"All {len(matched)} comparable loci matched"
    if match_result == "probable_match":
        return (
            f"{len(matched)}/{len(matched) + len(mismatched)} loci matched; "
            f"{len(mismatched)} mismatched: {', '.join(mismatched[:5])}"
        )
    if match_result == "exclusion":
        return f"Only {len(matched)}/{len(matched) + len(mismatched)} loci matched; exclusion supported"
    return "Insufficient data for conclusive determination"


# ═══════════════════════════════════════════════════════════════════
# Trace evidence analysis
# ═══════════════════════════════════════════════════════════════════

def analyze_trace_evidence(
    evidence_type: str,
    sample_data: dict[str, Any],
    reference_data: dict[str, Any],
) -> dict[str, Any]:
    """Analyze and compare trace evidence sample against reference data.

    Args:
        evidence_type: One of 'fiber', 'hair', 'glass', 'paint', 'soil',
                       'gsr', 'toolmark', 'footwear', 'tire'.
        sample_data: Dict of sample measurements.
        reference_data: Dict of reference measurements.

    Returns:
        dict with: evidence_type, match_result, confidence, findings,
        sample_summary, reference_summary, field_comparisons, notes.
    """
    if evidence_type not in TRACE_EVIDENCE_TYPES:
        raise ValueError(
            f"Unknown evidence_type '{evidence_type}'. "
            f"Valid: {sorted(TRACE_EVIDENCE_TYPES.keys())}"
        )
    if not isinstance(sample_data, dict) or not isinstance(reference_data, dict):
        raise TypeError("sample_data and reference_data must be dicts")

    config = TRACE_EVIDENCE_TYPES[evidence_type]
    match_fields = config.get("match_fields", [])
    tolerances = config.get("match_tolerance", {})

    comparisons: list[dict[str, Any]] = []
    match_scores: list[float] = []

    for field_name in match_fields:
        s_val = sample_data.get(field_name)
        r_val = reference_data.get(field_name)
        if s_val is None or r_val is None:
            comparisons.append({
                "field": field_name, "sample_value": s_val, "reference_value": r_val,
                "match": False, "reason": "missing_data",
            })
            continue
        if isinstance(s_val, (int, float)) and isinstance(r_val, (int, float)):
            tolerance = float(tolerances.get(field_name, 0.0))
            is_match = abs(float(s_val) - float(r_val)) <= tolerance if tolerance > 0 else s_val == r_val
        else:
            is_match = str(s_val).lower() == str(r_val).lower()
        comparisons.append({
            "field": field_name, "sample_value": s_val, "reference_value": r_val,
            "match": is_match, "reason": None if is_match else "value_mismatch",
        })
        match_scores.append(1.0 if is_match else 0.0)

    total = len(match_scores)
    if total == 0:
        confidence = 0.0
        match_result = "inconclusive"
    else:
        confidence = sum(match_scores) / total
        if confidence >= 0.9:
            match_result = "match"
        elif confidence >= 0.6:
            match_result = "probable_match"
        elif confidence >= 0.3:
            match_result = "inconclusive"
        else:
            match_result = "exclusion"

    findings: list[str] = []
    if match_result in ("match", "probable_match"):
        findings.append(f"{evidence_type} evidence consistent with reference source")
        matched_count = sum(bool(comparison["match"]) for comparison in comparisons)
        findings.append(
            f"Match fields: {', '.join(match_fields)} ({matched_count}/{total} matched)"
        )
    elif match_result == "inconclusive":
        findings.append(f"Insufficient data for conclusive {evidence_type} comparison")
    else:
        findings.append(f"{evidence_type} evidence excluded from reference source")

    sample_summary: dict[str, Any] = {
        k: v for k, v in sample_data.items()
        if k in config.get("measurements", [])
    }
    reference_summary: dict[str, Any] = {
        k: v for k, v in reference_data.items()
        if k in config.get("measurements", [])
    }

    return {
        "evidence_type": evidence_type,
        "match_result": match_result,
        "confidence": round(confidence, 4),
        "findings": findings,
        "sample_summary": sample_summary or sample_data,
        "reference_summary": reference_summary or reference_data,
        "field_comparisons": comparisons,
        "notes": _trace_notes(evidence_type, match_result, confidence),
    }


def _trace_notes(evidence_type: str, match_result: str, confidence: float) -> str:
    """Generate notes about trace evidence comparison."""
    if match_result == "match":
        return f"Strong {evidence_type} evidence association (confidence: {confidence:.2%})"
    if match_result == "probable_match":
        return f"Moderate {evidence_type} evidence association (confidence: {confidence:.2%})"
    if match_result == "exclusion":
        return f"{evidence_type} evidence does NOT associate with reference (confidence: {confidence:.2%})"
    return f"Inconclusive {evidence_type} comparison"
