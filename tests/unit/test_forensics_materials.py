"""TDD tests for the forensics materials module -- fingerprint, DNA, trace evidence.

Tests the module at:
``collections/ansible_collections/general_ludd/forensics/plugins/module_utils/materials_forensics.py``
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "forensics"
    / "plugins"
    / "module_utils"
    / "materials_forensics.py"
)


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "materials_forensics", str(MODULE_PATH)
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["materials_forensics"] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_module()
FingerprintPattern = _mod.FingerprintPattern
classify_fingerprint = _mod.classify_fingerprint
match_dna_profile = _mod.match_dna_profile
analyze_trace_evidence = _mod.analyze_trace_evidence
FINGERPRINT_PATTERNS = _mod.FINGERPRINT_PATTERNS
FINGERPRINT_MINUTIAE_TYPES = _mod.FINGERPRINT_MINUTIAE_TYPES
DNA_LOCI = _mod.DNA_LOCI
DNA_ANALYSIS_TYPES = _mod.DNA_ANALYSIS_TYPES
TRACE_EVIDENCE_TYPES = _mod.TRACE_EVIDENCE_TYPES


# ── fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def loop_fingerprint_data() -> dict[str, Any]:
    return {
        "ridge_flow_description": "friction ridge flows and recurves toward thumb",
        "core_present": True,
        "delta_count": 1,
        "ridge_count": 48,
        "quality_score": 0.85,
        "minutiae_list": [
            {"type": "RIDGE_ENDING", "x": 120, "y": 340},
            {"type": "RIDGE_ENDING", "x": 135, "y": 352},
            {"type": "BIFURCATION", "x": 225, "y": 298},
            {"type": "BIFURCATION", "x": 240, "y": 310},
            {"type": "DOT", "x": 180, "y": 400},
        ],
    }


@pytest.fixture
def whorl_fingerprint_data() -> dict[str, Any]:
    return {
        "ridge_flow_description": "ridges form concentric circles around central core with spirals",
        "core_present": True,
        "delta_count": 2,
        "ridge_count": 62,
        "quality_score": 0.9,
        "minutiae_list": [
            {"type": "RIDGE_ENDING", "x": 100, "y": 200},
            {"type": "BIFURCATION", "x": 150, "y": 250},
            {"type": "ENCLOSURE", "x": 200, "y": 300},
            {"type": "SPUR", "x": 250, "y": 350},
        ],
    }


@pytest.fixture
def arch_fingerprint_data() -> dict[str, Any]:
    return {
        "ridge_flow_description": "ridges flow horizontally across the pattern without recurve",
        "core_present": False,
        "delta_count": 0,
        "ridge_count": 22,
        "quality_score": 0.6,
        "minutiae_list": [
            {"type": "RIDGE_ENDING", "x": 50, "y": 100},
            {"type": "RIDGE_ENDING", "x": 70, "y": 110},
        ],
    }


@pytest.fixture
def matching_dna_sample() -> dict[str, Any]:
    return {
        "id": "SAMPLE-001",
        "loci": {
            "D3S1358": [15, 17],
            "vWA": [14, 16],
            "FGA": [21, 23],
            "TH01": [7, 9.3],
            "TPOX": [8, 11],
            "CSF1PO": [10, 12],
            "D5S818": [11, 12],
            "D7S820": [10, 11],
            "D8S1179": [13, 14],
            "D13S317": [11, 12],
            "D16S539": [9, 11],
            "D18S51": [14, 17],
            "D21S11": [29, 30],
        },
    }


@pytest.fixture
def matching_dna_reference() -> dict[str, Any]:
    return {
        "id": "REF-001",
        "loci": {
            "D3S1358": [15, 17],
            "vWA": [14, 16],
            "FGA": [21, 23],
            "TH01": [7, 9.3],
            "TPOX": [8, 11],
            "CSF1PO": [10, 12],
            "D5S818": [11, 12],
            "D7S820": [10, 11],
            "D8S1179": [13, 14],
            "D13S317": [11, 12],
            "D16S539": [9, 11],
            "D18S51": [14, 17],
            "D21S11": [29, 30],
        },
    }


@pytest.fixture
def mismatched_dna_sample() -> dict[str, Any]:
    return {
        "id": "SAMPLE-002",
        "loci": {
            "D3S1358": [12, 14],
            "vWA": [17, 19],
            "FGA": [20, 22],
        },
    }


@pytest.fixture
def partial_dna_reference() -> dict[str, Any]:
    return {
        "id": "REF-002",
        "loci": {
            "D3S1358": [15, 17],
            "vWA": [14, 16],
            "FGA": [21, 23],
        },
    }


@pytest.fixture
def fiber_sample() -> dict[str, Any]:
    return {
        "color": "blue",
        "diameter_um": 22.0,
        "cross_section": "round",
        "material": "nylon",
        "birefringence": 0.052,
        "melting_point_c": 258,
        "dye_composition": "disperse",
    }


@pytest.fixture
def fiber_reference() -> dict[str, Any]:
    return {
        "color": "blue",
        "diameter_um": 21.5,
        "cross_section": "round",
        "material": "nylon",
    }


# ── 1. FingerprintPattern ──────────────────────────────────────────


class TestFingerprintPattern:
    """FingerprintPattern dataclass tests."""

    def test_create_valid_arch(self) -> None:
        fp = FingerprintPattern(
            pattern_type="ARCH",
            subtype="plain_arch",
            confidence=0.8,
            minutiae_count=25,
            quality_score=0.7,
        )
        assert fp.pattern_type == "ARCH"
        assert fp.subtype == "plain_arch"
        assert fp.confidence == 0.8

    def test_create_valid_whorl(self) -> None:
        fp = FingerprintPattern(
            pattern_type="WHORL",
            subtype="plain_whorl",
            confidence=0.95,
            minutiae_count=50,
            quality_score=0.9,
            core_location=(120.0, 150.0),
            delta_locations=[(80.0, 100.0), (160.0, 200.0)],
            notes="Double delta present",
        )
        assert fp.pattern_type == "WHORL"
        assert len(fp.delta_locations) == 2

    def test_reject_invalid_pattern_type(self) -> None:
        with pytest.raises(ValueError, match="Unknown pattern_type"):
            FingerprintPattern(
                pattern_type="INVALID",
                confidence=0.5,
                quality_score=0.5,
            )

    def test_reject_confidence_out_of_range(self) -> None:
        with pytest.raises(ValueError, match=r"confidence must be 0\.0-1\.0"):
            FingerprintPattern(
                pattern_type="LOOP",
                confidence=1.5,
                quality_score=0.5,
            )

    def test_reject_quality_out_of_range(self) -> None:
        with pytest.raises(ValueError, match=r"quality_score must be 0\.0-1\.0"):
            FingerprintPattern(
                pattern_type="LOOP",
                confidence=0.5,
                quality_score=-0.1,
            )


# ── 2. classify_fingerprint ────────────────────────────────────────


class TestClassifyFingerprint:
    """classify_fingerprint function tests."""

    def test_classify_loop_pattern(self, loop_fingerprint_data: dict[str, Any]) -> None:
        result = classify_fingerprint(loop_fingerprint_data)
        assert result["pattern_type"] == "LOOP"
        assert result["confidence"] > 0.5
        assert result["core_detected"] is True
        assert result["delta_count"] == 1

    def test_classify_whorl_pattern(self, whorl_fingerprint_data: dict[str, Any]) -> None:
        result = classify_fingerprint(whorl_fingerprint_data)
        assert result["pattern_type"] == "WHORL"
        assert result["delta_count"] == 2

    def test_classify_arch_pattern(self, arch_fingerprint_data: dict[str, Any]) -> None:
        result = classify_fingerprint(arch_fingerprint_data)
        assert result["pattern_type"] == "ARCH"
        assert result["core_detected"] is False
        assert result["delta_count"] == 0

    def test_minutiae_summary_counts(self, loop_fingerprint_data: dict[str, Any]) -> None:
        result = classify_fingerprint(loop_fingerprint_data)
        summary = result["minutiae_summary"]
        assert summary.get("RIDGE_ENDING", 0) == 2
        assert summary.get("BIFURCATION", 0) == 2
        assert summary.get("DOT", 0) == 1

    def test_quality_score_reported(self, loop_fingerprint_data: dict[str, Any]) -> None:
        result = classify_fingerprint(loop_fingerprint_data)
        assert result["quality_score"] == 0.85

    def test_poor_quality_generates_notes(self, arch_fingerprint_data: dict[str, Any]) -> None:
        result = classify_fingerprint(arch_fingerprint_data)
        assert len(result["notes"]) > 0

    def test_reject_non_dict_input(self) -> None:
        with pytest.raises(TypeError, match="Expected dict"):
            classify_fingerprint("not a dict")

    def test_empty_minutiae_still_classifies(self) -> None:
        data: dict[str, Any] = {
            "ridge_flow_description": "loops and recurves",
            "core_present": True,
            "delta_count": 1,
            "ridge_count": 30,
            "quality_score": 0.6,
            "minutiae_list": [],
        }
        result = classify_fingerprint(data)
        assert result["pattern_type"] == "LOOP"
        assert result["minutiae_count"] == 0

    def test_tented_arch_detected(self) -> None:
        data: dict[str, Any] = {
            "ridge_flow_description": "upthrust and tented arch formation",
            "core_present": False,
            "delta_count": 0,
            "ridge_count": 18,
            "quality_score": 0.5,
            "minutiae_list": [],
        }
        result = classify_fingerprint(data)
        assert result["pattern_type"] == "ARCH"
        assert result["subtype"] == "tented_arch"


# ── 3. match_dna_profile ───────────────────────────────────────────


class TestMatchDnaProfile:
    """match_dna_profile function tests."""

    def test_perfect_str_match(
        self,
        matching_dna_sample: dict[str, Any],
        matching_dna_reference: dict[str, Any],
    ) -> None:
        result = match_dna_profile(matching_dna_sample, matching_dna_reference, "str")
        assert result["match_result"] == "match"
        assert result["loci_count"] == 13
        assert len(result["matched_loci"]) == 13
        assert len(result["mismatched_loci"]) == 0

    def test_complete_mismatch(
        self,
        mismatched_dna_sample: dict[str, Any],
        partial_dna_reference: dict[str, Any],
    ) -> None:
        result = match_dna_profile(mismatched_dna_sample, partial_dna_reference, "str")
        assert result["match_result"] == "exclusion"
        assert len(result["matched_loci"]) == 0

    def test_analysis_type_in_result(
        self,
        matching_dna_sample: dict[str, Any],
        matching_dna_reference: dict[str, Any],
    ) -> None:
        result = match_dna_profile(matching_dna_sample, matching_dna_reference, "codis")
        assert result["analysis_type"] == "codis"

    def test_empty_loci_returns_inconclusive(self) -> None:
        result = match_dna_profile({"loci": {}}, {"loci": {}}, "str")
        assert result["match_result"] == "inconclusive"
        assert result["loci_count"] == 0

    def test_non_dict_input_raises(self) -> None:
        with pytest.raises(TypeError):
            match_dna_profile("not dict", {"loci": {}}, "str")

    def test_probability_range(
        self,
        matching_dna_sample: dict[str, Any],
        matching_dna_reference: dict[str, Any],
    ) -> None:
        result = match_dna_profile(matching_dna_sample, matching_dna_reference, "str")
        assert 0.0 <= result["probability"] <= 1.0

    def test_sample_id_propagated(
        self,
        matching_dna_sample: dict[str, Any],
        matching_dna_reference: dict[str, Any],
    ) -> None:
        result = match_dna_profile(matching_dna_sample, matching_dna_reference, "str")
        assert result["sample_id"] == "SAMPLE-001"
        assert result["reference_id"] == "REF-001"

    def test_mtdna_type_accepted(
        self,
        matching_dna_sample: dict[str, Any],
        matching_dna_reference: dict[str, Any],
    ) -> None:
        result = match_dna_profile(matching_dna_sample, matching_dna_reference, "mtdna")
        assert result["analysis_type"] == "mtdna"
        assert "probability" in result

    def test_invalid_analysis_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown analysis_type"):
            match_dna_profile({"loci": {}}, {"loci": {}}, "invalid_type")

    def test_partial_match_detected(
        self,
        mismatched_dna_sample: dict[str, Any],
        matching_dna_reference: dict[str, Any],
    ) -> None:
        result = match_dna_profile(mismatched_dna_sample, matching_dna_reference, "str")
        assert result["match_result"] in ("probable_match", "inconclusive", "exclusion")


# ── 4. analyze_trace_evidence ──────────────────────────────────────


class TestAnalyzeTraceEvidence:
    """analyze_trace_evidence function tests."""

    def test_fiber_match(
        self, fiber_sample: dict[str, Any], fiber_reference: dict[str, Any]
    ) -> None:
        result = analyze_trace_evidence("fiber", fiber_sample, fiber_reference)
        assert result["evidence_type"] == "fiber"
        assert result["match_result"] in ("match", "probable_match")
        assert result["confidence"] > 0.5

    def test_hair_evidence_type(self) -> None:
        sample = {
            "color": "brown", "medulla_pattern": "continuous",
            "cuticle_pattern": "imbricate",
        }
        ref = {
            "color": "brown", "medulla_pattern": "continuous",
            "cuticle_pattern": "imbricate",
        }
        result = analyze_trace_evidence("hair", sample, ref)
        assert result["evidence_type"] == "hair"
        assert result["match_result"] == "match"
        assert len(result["field_comparisons"]) == 3

    def test_glass_refractive_index_match(self) -> None:
        sample = {"refractive_index": 1.5160, "color": "clear", "fluorescence": "none"}
        ref = {"refractive_index": 1.5162, "color": "clear", "fluorescence": "none"}
        result = analyze_trace_evidence("glass", sample, ref)
        assert result["match_result"] in ("match", "probable_match")

    def test_paint_exclusion(self) -> None:
        sample = {"color": "red", "binder_type": "alkyd", "layer_count": 3}
        ref = {"color": "blue", "binder_type": "acrylic", "layer_count": 5}
        result = analyze_trace_evidence("paint", sample, ref)
        assert result["match_result"] in ("exclusion", "inconclusive")

    def test_soil_partial_match(self) -> None:
        sample = {
            "color": "dark_brown", "mineral_composition": "quartz,feldspar", "ph": 6.5,
        }
        ref = {
            "color": "dark_brown", "mineral_composition": "quartz,feldspar,mica",
            "ph": 7.0,
        }
        result = analyze_trace_evidence("soil", sample, ref)
        assert result["evidence_type"] == "soil"
        assert isinstance(result["confidence"], float)

    def test_toolmark_analysis(self) -> None:
        sample = {"tool_type": "crowbar", "striation_pattern": "parallel_left"}
        ref = {"tool_type": "crowbar", "striation_pattern": "parallel_left"}
        result = analyze_trace_evidence("toolmark", sample, ref)
        assert result["match_result"] == "match"

    def test_footwear_analysis(self) -> None:
        sample = {"pattern_type": "herringbone", "manufacturing_defects": "none"}
        ref = {"pattern_type": "herringbone", "manufacturing_defects": "none"}
        result = analyze_trace_evidence("footwear", sample, ref)
        assert result["match_result"] == "match"

    def test_tire_exclusion(self) -> None:
        sample = {"tread_pattern": "directional_v", "track_width_mm": 1650}
        ref = {"tread_pattern": "asymmetric", "track_width_mm": 1800}
        result = analyze_trace_evidence("tire", sample, ref)
        assert result["match_result"] in ("exclusion", "inconclusive")

    def test_gsr_analysis(self) -> None:
        sample = {"composition": "PbBaSb", "morphology": "spheroid"}
        ref = {"composition": "PbBaSb", "morphology": "spheroid"}
        result = analyze_trace_evidence("gsr", sample, ref)
        assert result["match_result"] == "match"

    def test_invalid_evidence_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown evidence_type"):
            analyze_trace_evidence("unknown", {}, {})

    def test_non_dict_input_raises(self) -> None:
        with pytest.raises(TypeError, match="must be dicts"):
            analyze_trace_evidence("fiber", "not dict", {})

    def test_missing_fields_handled(self) -> None:
        result = analyze_trace_evidence("fiber", {"color": "red"}, {"color": "blue"})
        assert result["confidence"] == 0.0
        assert result["match_result"] == "exclusion"

    def test_findings_populated(
        self, fiber_sample: dict[str, Any], fiber_reference: dict[str, Any]
    ) -> None:
        result = analyze_trace_evidence("fiber", fiber_sample, fiber_reference)
        assert len(result["findings"]) > 0
        assert len(result["field_comparisons"]) > 0

    def test_notes_not_empty(self) -> None:
        result = analyze_trace_evidence(
            "hair", {"color": "black"}, {"color": "black"}
        )
        assert len(result["notes"]) > 0

    def test_confidence_between_zero_and_one(self) -> None:
        sample = {"color": "green", "material": "cotton", "cross_section": "flat"}
        ref = {"color": "green", "material": "cotton", "cross_section": "flat"}
        result = analyze_trace_evidence("fiber", sample, ref)
        assert 0.0 <= result["confidence"] <= 1.0


# ── 5. Data tables ─────────────────────────────────────────────────


class TestDataTables:
    """Data table consistency tests."""

    def test_fingerprint_patterns_have_subtypes(self) -> None:
        for pattern, data in FINGERPRINT_PATTERNS.items():
            assert "subtypes" in data, f"{pattern} missing subtypes"
            assert isinstance(data["subtypes"], list)
            assert len(data["subtypes"]) >= 1

    def test_all_fingerprint_patterns_valid(self) -> None:
        for pattern in FINGERPRINT_PATTERNS:
            fp = FingerprintPattern(
                pattern_type=pattern, confidence=0.5, quality_score=0.5,
            )
            assert fp.pattern_type == pattern

    def test_minutiae_types_have_descriptions(self) -> None:
        for _minutia, data in FINGERPRINT_MINUTIAE_TYPES.items():
            assert "description" in data
            assert "reliability" in data

    def test_dna_loci_have_required_keys(self) -> None:
        for locus, data in DNA_LOCI.items():
            assert "chromosome" in data, f"{locus} missing chromosome"
            assert "repeat_motif" in data, f"{locus} missing repeat_motif"

    def test_dna_analysis_types_valid_analysis_types(self) -> None:
        for atype in DNA_ANALYSIS_TYPES:
            assert isinstance(DNA_ANALYSIS_TYPES[atype], dict)
            assert "target_loci" in DNA_ANALYSIS_TYPES[atype]

    def test_trace_evidence_types_cover_all(self) -> None:
        expected = {
            "fiber", "hair", "glass", "paint", "soil",
            "gsr", "toolmark", "footwear", "tire",
        }
        assert set(TRACE_EVIDENCE_TYPES.keys()) == expected

    def test_trace_evidence_types_have_measurements(self) -> None:
        for etype, data in TRACE_EVIDENCE_TYPES.items():
            assert "measurements" in data, f"{etype} missing measurements"
            assert "match_fields" in data, f"{etype} missing match_fields"

    def test_dna_loci_allelic_ladders_are_lists(self) -> None:
        for _locus, data in DNA_LOCI.items():
            if "allelic_ladder" in data:
                assert isinstance(data["allelic_ladder"], list)
                assert len(data["allelic_ladder"]) > 0

    def test_fingerprint_frequency_pct_sums_to_100(self) -> None:
        total = sum(p["frequency_pct"] for p in FINGERPRINT_PATTERNS.values())
        assert abs(total - 100.0) < 0.1


# ── token count ─────────────────────────────────────────────────────


def test_count() -> None:
    """Ensure 35+ test functions exist."""
    import inspect
    import sys
    mod = sys.modules[__name__]
    test_funcs = [
        name for name, obj in inspect.getmembers(mod)
        if name.startswith("test_") and callable(obj)
    ]
    assert len(test_funcs) >= 35, f"Expected >=35 test functions, got {len(test_funcs)}"
